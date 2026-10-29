"""Waiting, and the header that says how long.

The upstream record is pi-free's `lib/fetch.ts` (`fetchWithRetry`,
`computeRetryBackoffMs`) and `retry-after.ts`. The second is transcribed
almost literally because it documents two traps that are silent in both
directions, and a copy that missed either would still look correct:

* **zero is a legal answer** — "released, go now" — and a falsy check would
  turn it into "the server said nothing", so a freshly released account
  waits out a fallback it no longer owes;
* **a bare number is seconds, not a date** — parsed as a date, ``"900"``
  becomes some instant in 1970, the difference is a large negative, and a
  clamp turns that into zero. The same silent wrong answer, from a header
  the station sent correctly.

What this project adds is the gate around the wait. Rotating to another
account is instant and costs nothing; waiting is neither. So a wait happens
only when the pool has nothing left to ask, only for a refusal that time
could change, and never once content has gone out — a second answer then
arrives as a duplicate.
"""

from __future__ import annotations

import httpx
import pytest

from hoshimi.core.failure import parse_retry_after
from hoshimi.core.providers.base import (
    Account,
    AuthFailed,
    ModelGone,
    PolicyBlocked,
    Provider,
    ProviderError,
    QuotaExceeded,
)
from hoshimi.core.providers.openai_compat import raise_for_status

# -- the header -------------------------------------------------------------- #


def test_a_bare_number_is_seconds_not_a_date() -> None:
    """The trap: a date parse makes `900` into 1970 and clamps it to zero."""
    assert parse_retry_after("900", now=0.0) == 900.0
    assert parse_retry_after("0", now=0.0) == 0.0
    assert parse_retry_after("1.5", now=0.0) == 1.5


def test_zero_is_an_answer_and_not_an_absence() -> None:
    """Both are the same number to a falsy check, and opposite instructions."""
    assert parse_retry_after("0", now=0.0) == 0.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("   ") is None


def test_a_negative_number_is_a_malformed_header_not_permission_to_go() -> None:
    """None means "the server declared nothing", so the caller guesses."""
    assert parse_retry_after("-5", now=0.0) is None


def test_nonsense_and_infinities_are_not_wait_times() -> None:
    assert parse_retry_after("soon") is None
    assert parse_retry_after("in a bit") is None
    assert parse_retry_after("inf") is None
    assert parse_retry_after("-inf") is None
    assert parse_retry_after("nan") is None


def test_an_http_date_is_read_as_a_moment_not_a_duration() -> None:
    header = "Wed, 21 Oct 2026 07:28:00 GMT"
    now = parse_retry_after(header, now=0.0)
    assert now is not None and now > 0

    # From a moment beside it, the answer is the gap rather than the epoch.
    from datetime import datetime, timezone

    moment = datetime(2026, 10, 21, 7, 28, 0, tzinfo=timezone.utc).timestamp()
    assert parse_retry_after(header, now=moment - 30.0) == pytest.approx(30.0, abs=0.5)
    # Once the moment has passed the answer is zero — "it has already
    # released" — which is not the same as undeclared.
    assert parse_retry_after(header, now=moment + 60.0) == 0.0


def test_a_date_without_a_zone_is_utc_by_definition() -> None:
    """RFC 9110. Parsed as local time it would be off by the host's offset."""
    from datetime import datetime, timezone

    moment = datetime(2026, 10, 21, 7, 28, 0, tzinfo=timezone.utc).timestamp()
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00", now=moment) == 0.0


def test_the_header_reaches_the_error_that_needs_it() -> None:
    """A wait time that stops at the parser is decoration."""

    def classify(headers: dict[str, str]) -> ProviderError | None:
        try:
            raise_for_status(httpx.Response(429, content=b"rate limit exceeded", headers=headers), provider="lane")
        except ProviderError as exc:
            return exc
        return None

    assert classify({}) .retry_after is None
    assert classify({"retry-after": "120"}).retry_after == 120.0
    # And it rides the classification the body earned, not a new one.
    assert isinstance(classify({"retry-after": "120"}), QuotaExceeded)


def test_the_header_is_read_off_a_refusal_that_needs_no_body() -> None:
    """An empty 429 is the common case from a gateway in front of the station."""

    with pytest.raises(ProviderError) as excinfo:
        raise_for_status(httpx.Response(429, content=b"", headers={"Retry-After": "45"}), provider="lane")

    assert excinfo.value.retry_after == 45.0


# -- the wait ---------------------------------------------------------------- #


@pytest.fixture()
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every delay the pool asked for, in order, without spending it."""
    record: list[float] = []
    monkeypatch.setattr("time.sleep", record.append)
    return record


def test_a_transient_failure_reasks_the_pool_instead_of_failing_at_once() -> None:
    """A single-account lane is the common configuration, and it had no answer."""
    lane = _Lane([_Failing(503)], transient=ProviderError(503, "lane: upstream unavailable", retryable=True))

    with pytest.raises(ProviderError):
        list(lane.stream("m", {}))

    assert lane.calls == ["a", "a", "a"], "one account, one pass, then the retries it is owed"


def test_long_server_wait_is_returned_without_an_early_retry(slept: list[float]) -> None:
    """`Retry-After` is a statement about the station's limiter, not a licence.

    A request path that parks for an hour is a worse answer than a failure
    the caller can route elsewhere.
    """
    lane = _Lane([_Failing(503)], transient=ProviderError(503, "lane: slow down", retryable=True, retry_after=3600.0))

    with pytest.raises(ProviderError) as failure:
        list(lane.stream("m", {}))

    assert failure.value.retry_after == 3600.0
    assert slept == []


def test_a_named_short_wait_is_honoured_exactly(slept: list[float]) -> None:
    """Not jittered: the station said how long, and it is not a guess."""
    lane = _Lane([_Failing(503)], transient=ProviderError(503, "lane: slow down", retryable=True, retry_after=2.0))

    with pytest.raises(ProviderError):
        list(lane.stream("m", {}))

    assert slept == [2.0, 2.0]


def test_zero_means_go_at_once_and_is_not_read_as_undeclared(slept: list[float]) -> None:
    """The trap, seen from the pool: a released account is asked again now.

    An undeclared wait jitters up to the base; a declared zero does not
    sleep at all. To a falsy check they are the same value.
    """
    released = _Lane([_Failing(503)], transient=ProviderError(503, "lane: released", retryable=True, retry_after=0.0))
    undeclared = _Lane([_Failing(503)], transient=ProviderError(503, "lane: unavailable", retryable=True))

    with pytest.raises(ProviderError):
        list(released.stream("m", {}))
    assert slept == [], "a station that said `0` was made to wait anyway"

    with pytest.raises(ProviderError):
        list(undeclared.stream("m", {}))
    assert slept, "an undeclared wait was read as an immediate one"


def test_without_a_declared_wait_the_delay_is_jittered(slept: list[float]) -> None:
    """Full jitter, because a fleet backing off in step is the herd itself."""
    lane = _Lane([_Failing(503)], transient=ProviderError(503, "lane: unavailable", retryable=True))

    with pytest.raises(ProviderError):
        list(lane.stream("m", {}))

    assert len(slept) == lane.backoff_retries
    assert all(0.0 <= delay <= lane.backoff_max_seconds for delay in slept), slept


def test_the_later_wait_is_not_shorter_than_the_earlier_one(slept: list[float]) -> None:
    """A retry index that counts down doubles the wrong way."""
    lane = _Lane([_Failing(503)], transient=ProviderError(503, "lane: unavailable", retryable=True))

    with pytest.raises(ProviderError):
        list(lane.stream("m", {}))

    assert len(slept) == 2
    # Both are bounded by their own ceiling: base * 2**attempt.
    assert slept[0] <= lane.backoff_base_seconds
    assert slept[1] <= lane.backoff_base_seconds * 2


def test_an_account_scoped_refusal_is_never_waited_on(slept: list[float]) -> None:
    """The answer is another account, and the pool is already looking."""
    for refusal in (
        QuotaExceeded(429, "lane: allowance gone", retryable=True),
        AuthFailed(401, "lane: bad token", retryable=True),
        PolicyBlocked(403, "lane: blocked", retryable=True),
        ModelGone(404, "lane: no such model"),
    ):
        lane = _Lane([_Failing(400)], transient=refusal)
        with pytest.raises(ProviderError):
            list(lane.stream("m", {}))
        assert slept == [], f"{type(refusal).__name__} was waited on"
        assert lane.calls == ["a"], f"{type(refusal).__name__} re-asked the same account"
        slept.clear()


def test_nothing_is_retried_once_content_has_gone_out(slept: list[float]) -> None:
    """A second answer arrives as a duplicate, which is worse than a failure.

    The turn is already answered as far as the caller is concerned, so the
    break is reported rather than papered over — and no second request is
    spent on it.
    """
    lane = _Lane([_Failing(503, after=1)], transient=ProviderError(503, "lane: unavailable", retryable=True))

    chunks: list[dict] = []
    with pytest.raises(ProviderError):
        for chunk in lane.stream("m", {}):
            chunks.append(chunk)  # noqa: PERF402 - `list()` would discard these on the raise

    assert [chunk["type"] for chunk in chunks] == ["text_delta"]
    assert lane.calls == ["a"], "the turn was answered before it broke; retrying would repeat it"
    assert slept == []


def test_an_unrecoverable_status_is_not_retried(slept: list[float]) -> None:
    """Burning the pool on the caller's own request only makes it slower."""
    lane = _Lane([_Failing(400)], transient=ProviderError(400, "lane: bad request"))

    with pytest.raises(ProviderError):
        list(lane.stream("m", {}))

    assert lane.calls == ["a"]
    assert slept == []


# -- fixtures ---------------------------------------------------------------- #


class _Failing:
    """One account's answer: fail, optionally after some content."""

    def __init__(self, status: int, *, after: int = 0):
        self.status = status
        self.after = after

    def raised(self) -> ProviderError:
        return ProviderError(self.status, f"lane: upstream {self.status}")


class _Lane(Provider):
    """A lane whose single account answers on demand."""

    id = "lane"

    def __init__(self, answers: list[_Failing], *, transient: ProviderError | None = None):
        super().__init__(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="{}"))), [Account({"id": "a"}, provider_id="lane")])
        self._answers = list(answers)
        self._transient = transient
        self.calls: list[str] = []

    def stream_raw(self, account: Account, model: str, body: dict) -> object:
        self.calls.append(account.id)
        answer = self._answers.pop(0) if self._answers else _Failing(503)

        def run() -> object:
            for index in range(answer.after):
                yield {"type": "text_delta", "text": f"chunk-{index}"}
            if self._transient is not None:
                raise self._transient
            raise answer.raised()

        return run()
