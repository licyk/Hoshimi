"""The status table, and what it decides about a pool and a catalogue.

The upstream record is pi-free's `lib/auto-fallback/classifier.ts` and
`blacklist.ts`, in a clone that is gitignored — so the status sets are
transcribed here rather than imported, and a machine without the clone runs
this test all the same.

Both sets are locked as whole sets rather than spot-checked: the reason a table
exists is that what a free station answers is not obvious (Cloudflare's
521-527, Anthropic's 529, "402 means the allowance is gone"), and a spot check
would not notice one status quietly being dropped from the middle of it.

The decision these tests pin is that a table entry has to *do* something. A
`retryable` flag nothing reads would be decoration; here the same table decides
whether the pool asks another account and whether the catalogue remembers an id.
"""

from __future__ import annotations

import httpx
import pytest

from hoshimi.core.errors import ModelUnavailableError
from hoshimi.core.failure import (
    MODEL_GONE_STATUSES,
    RECOVERABLE,
    RECOVERABLE_STATUSES,
    UNKNOWN,
    UNRECOVERABLE,
    UNRECOVERABLE_STATUSES,
    DeadModelStore,
    classify_status,
    is_model_gone,
    is_recoverable,
)
from hoshimi.core.providers.base import (
    Account,
    AuthFailed,
    ModelGone,
    Provider,
    ProviderError,
    QuotaExceeded,
)
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.registry import Registry

# -- upstream tables --------------------------------------------------------- #

# pi-free `classifier.ts` `RECOVERABLE_HTTP_STATUSES`.
UPSTREAM_RECOVERABLE = {402, 408, 409, 425, 429, 500, 502, 503, 504, 507, 521, 522, 523, 524, 525, 526, 527, 529}
# pi-free `classifier.ts` `UNRECOVERABLE_HTTP_STATUSES`.
UPSTREAM_UNRECOVERABLE = {400, 401, 403, 404, 405, 406, 410, 415, 418, 422, 451}


def _error(status: int, body: str = "upstream answer") -> ProviderError | None:
    """What `raise_for_status` makes of one upstream answer, or None."""
    try:
        raise_for_status(httpx.Response(status, content=body.encode()), provider="lane")
    except ProviderError as exc:
        return exc
    return None


def _messages(count: int = 1) -> list[dict[str, str]]:
    return [{"role": "user", "content": "hi"}] * count


# -- the table --------------------------------------------------------------- #


def test_the_status_sets_are_upstreams_not_ours() -> None:
    assert set(RECOVERABLE_STATUSES) == UPSTREAM_RECOVERABLE
    assert set(UNRECOVERABLE_STATUSES) == UPSTREAM_UNRECOVERABLE
    # The id statuses are a subset on purpose: "no such model" is both about
    # the id and not worth retrying on the same account.
    assert set(MODEL_GONE_STATUSES) <= set(UNRECOVERABLE_STATUSES)


def test_402_is_quota_rather_than_an_opaque_failure() -> None:
    """The free tier's own sentence: the allowance is gone, so rotate accounts.

    This status used to fall through as a plain upstream error, which the pool
    reads as "nothing to do here" — exactly wrong for a lane where 402 means a
    different account is the fix.
    """
    assert classify_status(402) == RECOVERABLE
    exc = _error(402, "Payment Required")
    assert isinstance(exc, QuotaExceeded) and exc.retryable is True
    # 402 is decided by the status alone; there is nothing useful in the body.
    assert isinstance(_error(402, "some other prose entirely"), QuotaExceeded)


def test_404_is_about_the_id_and_never_about_the_body() -> None:
    """The status is read first: prose is guesswork, "no such model" is not."""
    assert is_model_gone(404) and is_model_gone(410)
    assert not is_model_gone(400) and not is_model_gone(422)
    exc = _error(404, "quota exceeded for this endpoint")
    assert isinstance(exc, ModelGone), "a body mentioning quota does not rename a 404"
    assert exc.http_status == 404


def test_retryable_is_decided_by_the_table_not_by_the_caller() -> None:
    assert _error(503).retryable is True
    assert _error(429).retryable is True
    assert _error(400).retryable is False
    assert _error(422).retryable is False
    # A status nobody named is recoverable: hiding a real outage behind "we
    # don't recognize this code" is the worse mistake on a free lane.
    assert classify_status(599) == UNKNOWN
    assert _error(599).retryable is True and is_recoverable(599)


def test_a_credential_failure_is_never_read_as_anything_else() -> None:
    """The pool's first move is renewal, so 401/403 must not become a 5xx."""
    assert classify_status(401) == UNRECOVERABLE
    assert isinstance(_error(401), AuthFailed)
    assert isinstance(_error(403), AuthFailed)
    # A body that talks about quota does not rename the credential statuses.
    assert isinstance(_error(401, "rate limit exceeded"), AuthFailed)


# -- what the table decides about the pool ----------------------------------- #


class StatusProvider(Provider):
    """Answers each account with one canned status, so the pool's rule shows.

    ``calls`` is the number of accounts that were actually asked — the whole
    point of the rule is how many of them get spent.
    """

    id = "status"
    display_name = "Status"

    def __init__(self, statuses: list[int]) -> None:
        super().__init__(
            httpx.Client(),
            [Account({"access_token": "x"}, provider_id=f"status-{index}") for index in range(len(statuses))],
        )
        self.statuses = list(statuses)
        self.calls = 0

    def fallback_models(self) -> list[str]:
        return ["m"]

    def fetch_models(self) -> list[str]:
        return []

    def stream_raw(self, account: Account, model: str, body: dict):  # type: ignore[override]
        index = min(self.calls, len(self.statuses) - 1)
        self.calls += 1
        status = self.statuses[index]
        if status < 400:
            yield {"type": "text_delta", "text": "ok"}
            return
        raise_for_status(httpx.Response(status, content=b"answer"), provider=self.id)


def test_a_4xx_about_the_caller_does_not_burn_the_pool() -> None:
    """Switching accounts would fail identically, and only makes it slower."""
    provider = StatusProvider([400, 200])
    with pytest.raises(ProviderError) as excinfo:
        list(provider.stream("m", {"messages": _messages()}))
    assert provider.calls == 1
    assert excinfo.value.status == 400


def test_a_5xx_asks_the_next_account() -> None:
    provider = StatusProvider([503, 200])
    chunks = list(provider.stream("m", {"messages": _messages()}))
    assert provider.calls == 2
    assert chunks and chunks[0]["text"] == "ok"


def test_quota_and_credentials_still_rotate() -> None:
    """The two account-scoped failures, pinned so the new rule cannot eat them."""
    quota = StatusProvider([429, 200])
    assert list(quota.stream("m", {"messages": _messages()}))
    assert quota.calls == 2

    auth = StatusProvider([401, 200])
    assert list(auth.stream("m", {"messages": _messages()}))
    assert auth.calls == 2


def test_every_account_is_asked_before_a_model_is_declared_gone() -> None:
    """A per-account catalogue may know an id another account does not."""
    provider = StatusProvider([404, 404])
    with pytest.raises(ModelGone):
        list(provider.stream("m", {"messages": _messages()}))
    assert provider.calls == 2


# -- what the table decides about the catalogue ------------------------------ #


class GoneProvider(Provider):
    """A station that has stopped answering for one id."""

    id = "gone"
    display_name = "Gone"

    def __init__(self, model: str, *, status: int = 404) -> None:
        super().__init__(httpx.Client(), [Account({"access_token": "x"}, provider_id="gone-0")])
        self.model = model
        self.status = status
        self.calls = 0

    def fallback_models(self) -> list[str]:
        return [self.model]

    def fetch_models(self) -> list[str]:
        return []

    def stream_raw(self, account: Account, model: str, body: dict):  # type: ignore[override]
        self.calls += 1
        if self.status < 400:
            yield {"type": "text_delta", "text": "ok"}
            return
        raise_for_status(httpx.Response(self.status, content=b"answer"), provider=self.id)


def test_a_404_from_the_pool_bans_the_id() -> None:
    provider = GoneProvider("m")
    registry = Registry({"gone": provider})
    with pytest.raises(ModelGone):
        list(registry.stream("m", {"messages": _messages()}))
    assert registry.dead_models.is_dead("gone", "m")
    # The catalogue still lists it: a client that cached the id must not be
    # told it never existed, only that it cannot be answered right now.
    assert "m" in dict(registry.model_ids())


def test_a_banned_id_is_refused_without_asking_upstream() -> None:
    """Asking again spends a round trip to learn the same thing."""
    provider = GoneProvider("m")
    registry = Registry({"gone": provider})
    registry.dead_models.strike("gone", "m", reason="HTTP 404")

    with pytest.raises(ModelUnavailableError) as excinfo:
        registry.stream("m", {"messages": _messages()})
    assert provider.calls == 0
    # Refused at call time, not on the first read, so the turn is counted as
    # failed by whoever wrapped the stream.
    assert excinfo.value.http_status == 503
    assert excinfo.value.detail["model"] == "m"


def test_an_id_that_answers_has_its_streak_pruned() -> None:
    """The clear is for entries nothing else will touch.

    A strike inside its window blocks the id outright, so no turn can be the
    one that clears it; what can arrive is an *expired* entry, still sitting in
    the store, and the first answer after it should not leave it there.
    """
    provider = GoneProvider("m", status=200)
    registry = Registry({"gone": provider})
    registry.dead_models.strike("gone", "m", now=0.0)
    assert not registry.dead_models.is_dead("gone", "m")

    chunks = list(registry.stream("m", {"messages": _messages()}))
    assert chunks and provider.calls == 1
    assert not registry.dead_models.is_dead("gone", "m")
    assert len(registry.dead_models) == 0


# -- the store's own rules --------------------------------------------------- #


def test_one_strike_bans_for_the_window_and_forgets_after_it() -> None:
    store = DeadModelStore(ttl=600.0, max_strikes=3)
    store.strike("p", "m", reason="HTTP 404", now=1000.0)
    assert store.is_dead("p", "m", now=1000.0)
    assert store.is_dead("p", "m", now=1599.0)
    # The signal decays: one bad afternoon must not ban an id forever.
    assert not store.is_dead("p", "m", now=1601.0)


def test_three_strikes_promote_to_a_ban_the_window_cannot_lift() -> None:
    store = DeadModelStore(ttl=600.0, max_strikes=3)
    store.strike("p", "m", now=1000.0)
    store.strike("p", "m", now=1010.0)
    entry = store.strike("p", "m", now=1020.0)
    assert entry.hard and entry.strikes == 3
    # An id that failed three times in ten minutes is gone, not flaky.
    assert store.is_dead("p", "m", now=100_000.0)


def test_a_streak_expires_between_windows_so_a_count_cannot_accumulate() -> None:
    store = DeadModelStore(ttl=600.0, max_strikes=3)
    store.strike("p", "m", now=1000.0)
    store.strike("p", "m", now=1010.0)
    rows = store.snapshot(now=1020.0)
    assert rows[0]["strikes"] == 2 and rows[0]["dead"] is True
    # Two failures a fortnight apart are two separate bad days, not one long one.
    store.strike("p", "m", now=2000.0)
    assert store.snapshot(now=2001.0)[0]["strikes"] == 1


def test_the_store_is_scoped_per_pair_and_can_be_reset() -> None:
    store = DeadModelStore()
    store.strike("p", "m")
    store.strike("p", "other")
    store.strike("q", "m")
    assert store.is_dead("p", "m") and store.is_dead("q", "m")

    store.clear("p", "m")
    assert not store.is_dead("p", "m")
    assert store.is_dead("p", "other")

    assert store.clear_all() == 2
    assert len(store) == 0 and store.snapshot() == []
