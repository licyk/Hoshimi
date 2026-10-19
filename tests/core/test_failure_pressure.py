"""Two refusals that arrive wearing somebody else's status.

The upstream record is dsh's `vendor/channel-pack/src/llm-adapter.ts` — the
constants and the measured evidence behind them are transcribed here, in a
clone that is gitignored, so a machine without it runs these tests all the
same.

The defect both of these fix is the same shape. A saturated model answers
HTTP 429 and a policy-blocked account answers HTTP 403, which are exactly
the numbers a gateway reads as "rate limit, rotate" and "credentials,
renew". Reading the number alone made four unrelated accounts look like one
pool-wide outage: each was locked for a full hour by a fallback duration,
over a model that was merely busy for twenty seconds.

So the tests below are about *classification taking the right action*, not
about matching strings: an unrecognised body that merely looks similar must
still be the old answer, and a recognised one must change what the pool
does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from hoshimi.core.failure import (
    MODEL_SATURATION_BUSINESS_CODE,
    POLICY_BLOCK_BUSINESS_CODE,
    AccountCooler,
    is_model_saturation,
    is_policy_block,
    parse_reset_after,
)
from hoshimi.core.providers.base import (
    Account,
    AuthFailed,
    ModelSaturated,
    PolicyBlocked,
    Provider,
    ProviderError,
    QuotaExceeded,
)
from hoshimi.core.providers.openai_compat import raise_for_status

# Measured on 2026-10-05: four unrelated uids all answered with this, and the
# payload's own `actions` list offers SWITCH_MODEL with no account option.
SATURATED_BODY = (
    '{"code":14003,"msg":"too many requests",'
    '"displayMsg":{"zh":"模型繁忙，请换模型或稍后重试"},'
    '"actions":["SWITCH_MODEL","SUBMIT_FEEDBACK","RETRY"]}'
)

# Measured as HTTP 403 across four accounts, and unrelated to what was asked.
POLICY_BLOCK_BODY = '{"code":11140,"msg":"request illegal","requestId":"a-uuid"}'


def _classified(status: int, body: str) -> ProviderError:
    try:
        raise_for_status(httpx.Response(status, content=body.encode()), provider="lane")
    except ProviderError as exc:
        return exc
    raise AssertionError(f"HTTP {status} was not refused at all")


# -- saturation: the model, not the account --------------------------------- #


def test_saturation_arrives_as_a_429_and_is_still_not_a_rate_limit() -> None:
    exc = _classified(429, SATURATED_BODY)
    assert isinstance(exc, ModelSaturated)
    assert not isinstance(exc, QuotaExceeded)


def test_saturation_prose_is_narrow_enough_to_survive_a_reply_about_busy_servers() -> None:
    assert is_model_saturation(429, "the model busy right now, try later")
    assert is_model_saturation(429, "请求量饱和")
    assert is_model_saturation(429, "currently saturated")

    # A body that merely mentions being busy is not a refusal. This is the
    # whole reason the prose set is narrow rather than `busy|saturated`.
    assert not is_model_saturation(429, "I was busy all afternoon and could not answer")
    assert not is_model_saturation(429, "The saturated fat debate continues")


def test_saturation_never_reads_a_healthy_body_as_a_refusal() -> None:
    assert not is_model_saturation(200, SATURATED_BODY)
    assert not is_model_saturation(200, "模型繁忙")


def test_a_lucky_digit_in_a_request_id_is_not_the_business_code() -> None:
    # `requestId` is a UUID, so any digits occur somewhere by chance. Only the
    # structured field counts — a substring search would answer here.
    assert not is_model_saturation(429, '{"code":0,"requestId":"x-14003-y","message":"boom"}')


def test_saturation_is_recognised_from_a_frame_riding_a_200_stream() -> None:
    """No HTTP status to read: the failure is the frame itself.

    `None` is a different answer from 200 — a healthy 200 that happens to
    contain the phrase is a reply, not a refusal.
    """
    assert is_model_saturation(None, SATURATED_BODY)
    assert not is_model_saturation(200, SATURATED_BODY)


def test_saturation_stands_the_pool_down_instead_of_burning_it() -> None:
    """One busy model must not be reported as "every account is limited"."""
    accounts = [Account({"id": "a"}, provider_id="lane"), Account({"id": "b"}, provider_id="lane"), Account({"id": "c"}, provider_id="lane")]
    lane = _Lane(accounts, {"a": ModelSaturated(429, "busy")})

    with pytest.raises(ModelSaturated):
        list(lane.stream("m", {}))

    assert lane.calls == ["a"], f"the pool was burned on a wall every account meets: {lane.calls}"


# -- policy block: this account, and not its credential ---------------------- #


def test_the_two_codes_are_locked_as_numbers_not_as_a_grep() -> None:
    """A station that changes a code has to fail a test, not a search."""
    assert MODEL_SATURATION_BUSINESS_CODE == 14003
    assert POLICY_BLOCK_BUSINESS_CODE == 11140


def test_a_policy_block_is_an_auth_shaped_refusal_with_its_own_name() -> None:
    exc = _classified(403, POLICY_BLOCK_BODY)
    assert isinstance(exc, PolicyBlocked)
    # Rotation is still the answer, so it stays under the auth family...
    assert isinstance(exc, AuthFailed)
    # ...but renewal is not, and the pool has to be able to tell them apart.
    assert exc.code == "policy_blocked"


def test_policy_block_prose_covers_both_languages_and_both_stations() -> None:
    assert is_policy_block(403, "request illegal")
    assert is_policy_block(403, "安全审核未通过")
    assert is_policy_block(403, "failed the safety review")
    assert not is_policy_block(403, "the request was malformed")


def test_a_policy_block_rotates_away_and_holds_the_account_back() -> None:
    accounts = [Account({"id": "blocked"}, provider_id="lane"), Account({"id": "healthy"}, provider_id="lane")]
    lane = _Lane(accounts, {"blocked": PolicyBlocked(403, POLICY_BLOCK_BODY)})

    chunks = list(lane.stream("m", {}))

    assert any(chunk["type"] == "text_delta" for chunk in chunks)
    assert lane.calls == ["blocked", "healthy"], "rotation is the answer for a blocked account"
    # And the next turn must not start by asking the account that just said no.
    assert lane.cooler.blocked("lane", "blocked")

    list(lane.stream("m", {}))
    assert lane.calls == ["blocked", "healthy", "healthy"]


def test_a_policy_block_does_not_spend_a_round_on_the_refresh_endpoint() -> None:
    """The credential is fine; renewing it would only burn a turn."""

    class _RenewingLane(_Lane):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            self.refreshed: list[str] = []

        def refresh_account(self, account: Account) -> bool:
            self.refreshed.append(account.id)
            return True

    accounts = [Account({"id": "blocked"}, provider_id="lane"), Account({"id": "healthy"}, provider_id="lane")]
    lane = _RenewingLane(accounts, {"blocked": PolicyBlocked(403, POLICY_BLOCK_BODY)})

    list(lane.stream("m", {}))

    assert lane.refreshed == []


# -- the hold: a number for a refusal that named no time --------------------- #


def test_a_limit_holds_for_the_time_the_station_actually_named() -> None:
    """Both spellings, and the offset is captured rather than assumed."""
    chinese = "您的使用量已超出频率限制，将在 2026-09-11 18:08:17 UTC+8 重置"
    english = "your usage will reset at 2026-09-17 09:09:36 UTC+8, alternatively, contact us"

    held = parse_reset_after(chinese, now=_utc(2026, 9, 11, 10, 8, 17, 8))
    assert held == 8 * 3600
    assert parse_reset_after(english, now=_utc(2026, 9, 17, 9, 9, 36, 8)) == 0.0

    # A station that is not in UTC+8 must not be read as though it were.
    assert parse_reset_after("将在 2026-09-11 18:08:17 UTC+0 reset", now=_utc(2026, 9, 11, 18, 8, 17, 0)) == 0.0
    assert parse_reset_after("将在 2026-09-11 18:08:17 UTC-5 重置", now=_utc(2026, 9, 11, 18, 8, 17, 0)) == 5 * 3600


def test_no_declared_time_is_not_the_same_answer_as_an_immediate_one() -> None:
    """Zero means "use it now"; None means "the server declared nothing"."""
    assert parse_reset_after("rate limited") is None
    assert parse_reset_after("") is None
    assert parse_reset_after("reset at") is None
    assert parse_reset_after("将在 2026-09-11 18:08:17 UTC+8 重置", now=_utc(2026, 9, 11, 18, 8, 17, 8)) == 0.0


def test_a_hold_never_shortens_one_already_running() -> None:
    cooler = AccountCooler()
    cooler.hold("lane", "a", 100.0, now=0.0)
    assert cooler.hold("lane", "a", 10.0, now=0.0) == 100.0
    # Two refusals in the same direction must not cancel each other out.
    assert cooler.hold("lane", "a", 200.0, now=0.0) == 200.0
    assert not cooler.blocked("lane", "a", now=200.0)


def test_a_hold_expires_on_its_own() -> None:
    cooler = AccountCooler(policy_block_seconds=30.0)
    cooler.hold("lane", "a", 30.0, now=0.0)
    assert cooler.blocked("lane", "a", now=29.0)
    assert not cooler.blocked("lane", "a", now=30.0)
    # Once expired it is forgotten, not merely ignored.
    assert cooler.snapshot(now=31.0) == []


def test_a_snapshot_names_the_reason_and_the_time_left() -> None:
    cooler = AccountCooler()
    cooler.hold("lane", "a", 120.0, reason="rate_limited", now=0.0)
    cooler.hold("other", "b", 1800.0, reason="policy_blocked", now=0.0)

    rows = cooler.snapshot(now=10.0)

    assert [(row["provider"], row["account"], row["reason"]) for row in rows] == [("lane", "a", "rate_limited"), ("other", "b", "policy_blocked")]
    assert rows[0]["seconds_left"] == 110.0


def test_accounts_are_let_go_individually_or_all_at_once() -> None:
    cooler = AccountCooler()
    cooler.hold("lane", "a", 10.0)
    cooler.hold("lane", "b", 10.0)
    cooler.hold("other", "c", 10.0)

    assert cooler.clear("lane", "a") == 1
    assert not cooler.blocked("lane", "a")
    assert cooler.clear("lane", "a") == 0
    assert cooler.clear("lane") == 1
    assert len(cooler) == 1, "one lane's sweep must not release another lane's hold"


def test_a_quota_refusal_holds_the_account_for_the_fallback_when_unnamed() -> None:
    accounts = [Account({"id": "full"}, provider_id="lane"), Account({"id": "spare"}, provider_id="lane")]
    lane = _Lane(accounts, {"full": QuotaExceeded(429, "rate limit exceeded")}, quota_hold=3600.0)

    list(lane.stream("m", {}))

    assert lane.calls == ["full", "spare"]
    assert lane.cooler.snapshot()[0]["seconds_left"] == 3600.0


# -- fixtures ---------------------------------------------------------------- #


def _utc(year: int, month: int, day: int, hour: int, minute: int, second: int, offset_hours: int) -> float:
    moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone(timedelta(hours=offset_hours)))
    return moment.timestamp()


class _Lane(Provider):
    """A lane whose accounts fail on demand, so the pool's rules can be watched."""

    id = "lane"

    def __init__(self, accounts: list[Account], answers: dict[str, ProviderError], *, quota_hold: float = 60.0):
        super().__init__(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="{}"))), accounts)
        self._answers = answers
        self.calls: list[str] = []
        self.quota_hold_seconds = quota_hold

    def stream_raw(self, account: Account, model: str, body: dict) -> object:
        self.calls.append(account.id)
        answer = self._answers.get(account.id)
        if answer is not None:
            raise answer

        def run() -> object:
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "finish", "reason": "stop"}

        return run()
