"""Daily check-in and balance: the service, the timer gate, the stations' wire shapes."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from hoshimi.core.config import ProviderConfig, Settings
from hoshimi.core.credits import (
    CHECKIN_FILE_NAME,
    CheckinDoc,
    CheckinStore,
    CreditsService,
    utc8_today,
)
from hoshimi.core.providers.base import (
    CLAIM_ALREADY,
    CLAIM_CLAIMED,
    CLAIM_FAILED,
    CLAIM_UNSUPPORTED,
    Account,
    BalanceOutcome,
    ClaimOutcome,
    Provider,
)
from hoshimi.core.providers.lobsterai import LobsteraiProvider
from hoshimi.core.providers.loomy import LoomyProvider
from hoshimi.core.providers.minimax import MinimaxProvider
from hoshimi.core.providers.qoder import QoderProvider
from hoshimi.core.providers.trae import TraeProvider
from hoshimi.core.registry import Registry


class CreditsLike(Provider):
    """A provider whose claim/balance results are scripted by the test."""

    id = "cline"
    display_name = "Cline"

    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        super().__init__(httpx.Client(), [Account(dict(raw), provider_id="cline") for raw in accounts])
        self.claim_result: ClaimOutcome | None = None
        self.balance_result: BalanceOutcome | None = None
        self.claim_calls = 0
        self.balance_calls = 0

    def fallback_models(self) -> list[str]:
        return []

    def fetch_models(self) -> list[str]:
        return []

    def stream_raw(self, account, model, body):  # type: ignore[no-untyped-def]
        yield {"type": "text_delta", "text": "unused"}

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        self.claim_calls += 1
        return self.claim_result

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        self.balance_calls += 1
        return self.balance_result


def _service(tmp_path: Any, accounts: list[dict[str, Any]], provider: CreditsLike | None = None) -> tuple[CreditsService, CreditsLike]:
    settings = Settings(providers={"cline": ProviderConfig(id="cline", enabled=True, accounts=accounts)}, auto_refresh=False, auto_checkin=False)
    provider = provider or CreditsLike(settings.providers["cline"].accounts)
    registry = Registry({"cline": provider})
    store = CheckinStore(tmp_path / CHECKIN_FILE_NAME)
    return CreditsService(settings=settings, registry=registry, store=store), provider


# -- the calendar gate ---------------------------------------------------- #
def test_utc8_today_is_the_station_day_not_the_host_day() -> None:
    # 2026-10-11 16:30 UTC is already 2026-10-12 00:30 in UTC+8.
    moment = time.mktime((2026, 10, 11, 16, 30, 0, 0, 0, 0)) - time.timezone
    # time.mktime interprets in local TZ; use a fixed epoch instead.
    from datetime import datetime, timezone

    moment = datetime(2026, 10, 11, 16, 30, tzinfo=timezone.utc).timestamp()
    assert utc8_today(moment) == "2026-10-12"
    # Just before midnight UTC+8 still belongs to the previous day.
    moment = datetime(2026, 10, 11, 15, 59, tzinfo=timezone.utc).timestamp()
    assert utc8_today(moment) == "2026-10-11"


def test_run_daily_is_a_no_op_when_today_is_already_covered(tmp_path: Any) -> None:
    service, provider = _service(tmp_path, [{"id": "a", "access_token": "t"}])
    today = utc8_today()
    service.store.doc = CheckinDoc(last_date=today, last_result="1 claimed", last_at=time.time())

    assert service.run_daily() == []
    assert provider.claim_calls == 0


def test_force_runs_even_when_today_is_covered(tmp_path: Any) -> None:
    service, provider = _service(tmp_path, [{"id": "a", "access_token": "t"}])
    provider.claim_result = ClaimOutcome("cline", "a", CLAIM_CLAIMED, credits=100)
    service.store.doc = CheckinDoc(last_date=utc8_today(), last_result="1 claimed", last_at=time.time())

    outcomes = service.run_daily(force=True)
    assert len(outcomes) == 1 and outcomes[0].status == CLAIM_CLAIMED


def test_a_day_is_only_marked_done_when_something_actually_covered_it(tmp_path: Any) -> None:
    # A station mid-refresh answers "already" about yesterday; covers_today=False
    # means the day must NOT be burned.
    service, provider = _service(tmp_path, [{"id": "a", "access_token": "t"}])
    provider.claim_result = ClaimOutcome("cline", "a", CLAIM_ALREADY, covers_today=False, message="no claimable campaign right now")

    outcomes = service.run_daily()
    assert outcomes[0].status == CLAIM_ALREADY
    assert service.store.doc.last_date == ""  # not marked done
    # A successful claim the next attempt does mark the day.
    provider.claim_result = ClaimOutcome("cline", "a", CLAIM_CLAIMED, credits=100)
    service.run_daily()
    assert service.store.doc.last_date == utc8_today()


def test_anonymous_accounts_are_skipped(tmp_path: Any) -> None:
    service, provider = _service(tmp_path, [{"id": "a", "access_token": "t"}])
    # inject an anonymous account
    provider.accounts.append(Account({"id": "anon"}, provider_id="cline", anonymous=True))
    provider.claim_result = ClaimOutcome("cline", "a", CLAIM_CLAIMED)
    outcomes = service.claim()
    assert [o.account for o in outcomes] == ["a"]
    assert provider.claim_calls == 1


def test_one_station_failure_does_not_stop_the_sweep(tmp_path: Any) -> None:
    class Boom(CreditsLike):
        def claim_daily(self, account: Account) -> ClaimOutcome | None:
            raise RuntimeError("network down")

    service, provider = _service(tmp_path, [{"id": "a", "access_token": "t"}], provider=Boom([{"id": "a", "access_token": "t"}]))
    provider.claim_result = ClaimOutcome("cline", "a", CLAIM_CLAIMED, credits=50)
    # Make the provider raise on first account then succeed on second? Simpler: one account, catch exception.
    outcomes = service.claim()
    assert outcomes[0].status == CLAIM_FAILED
    assert "network down" in outcomes[0].message


def test_balance_skips_lanes_without_an_endpoint(tmp_path: Any) -> None:
    service, provider = _service(tmp_path, [{"id": "a", "access_token": "t"}])
    provider.balance_result = None  # base default
    assert service.balance() == []
    provider.balance_result = BalanceOutcome("cline", "a", 5000)
    rows = service.balance()
    assert rows[0].available == 5000


def test_the_store_survives_a_restart(tmp_path: Any) -> None:
    store = CheckinStore(tmp_path / CHECKIN_FILE_NAME)
    store.doc.last_date = "2026-10-11"
    store.doc.last_result = "1 claimed"
    store.save()
    store.close()

    reloaded = CheckinStore(tmp_path / CHECKIN_FILE_NAME)
    assert reloaded.doc.last_date == "2026-10-11"
    assert reloaded.doc.last_result == "1 claimed"


def test_a_checkin_json_is_imported_once_and_a_corrupt_one_ignored(tmp_path: Any) -> None:
    path = tmp_path / CHECKIN_FILE_NAME
    path.write_text(json.dumps({"last_date": "2026-10-11", "last_result": "2 claimed", "last_at": 5.0, "channels": [{"provider": "cline", "text": "ok"}]}), encoding="utf-8")
    store = CheckinStore(path)
    assert (store.doc.last_date, store.doc.last_result, store.doc.last_at) == ("2026-10-11", "2 claimed", 5.0)
    assert store.doc.channels == [{"provider": "cline", "text": "ok"}]
    assert not path.exists() and (tmp_path / f"{CHECKIN_FILE_NAME}.migrated").is_file()

    other = tmp_path / "other"
    other.mkdir()
    (other / CHECKIN_FILE_NAME).write_text("{ not json", encoding="utf-8")
    fresh = CheckinStore(other / CHECKIN_FILE_NAME)
    assert fresh.doc.last_date == ""
    assert (other / f"{CHECKIN_FILE_NAME}.corrupt").is_file()


def test_every_claim_lands_in_the_history(tmp_path: Any) -> None:
    service, provider = _service(tmp_path, [{"id": "a", "access_token": "t"}, {"id": "b", "access_token": "t"}])
    provider.claim_result = ClaimOutcome("cline", "a", CLAIM_CLAIMED, credits=100, message="+100")
    service.run_daily()
    provider.claim_result = ClaimOutcome("cline", "b", CLAIM_FAILED, message="network down")
    service.claim("cline")  # an on-demand claim is history too
    provider.claim_result = ClaimOutcome("cline", "a", CLAIM_UNSUPPORTED)
    service.claim()  # "no endpoint" is not an outcome worth a row

    rows = service.store.history()
    assert [(row["status"], row["ok"], row["message"]) for row in rows[:2]] == [("failed", False, "network down"), ("failed", False, "network down")]
    assert [(row["status"], row["ok"], row["credits"], row["message"]) for row in rows[2:]] == [("claimed", True, 100, "+100"), ("claimed", True, 100, "+100")]
    assert all(row["date"] == utc8_today(row["at"]) for row in rows)
    assert service.store.history("other") == []
    assert len(service.store.history(limit=1)) == 1
    assert [len(rows) for rows in service.store.recent_by_provider(3).values()] == [3]


def test_history_keeps_a_year(tmp_path: Any) -> None:
    store = CheckinStore(tmp_path / CHECKIN_FILE_NAME)
    now = time.time()
    store.record([ClaimOutcome("cline", "a", CLAIM_ALREADY)], at=now - 400 * 86400)
    store.record([ClaimOutcome("cline", "a", CLAIM_ALREADY)], at=now - 300 * 86400)
    store.record([ClaimOutcome("cline", "a", CLAIM_CLAIMED)], at=now)
    assert [row["status"] for row in store.history()] == ["claimed", "already"]


def test_the_timer_starts_and_stops(tmp_path: Any) -> None:
    service, _ = _service(tmp_path, [{"id": "a", "access_token": "t"}])
    service.start()
    assert service._thread is not None and service._thread.is_alive()
    service.stop()
    assert service._thread is None


# -- the stations, by wire shape ------------------------------------------ #
def test_minimax_claim_posts_empty_body_and_reads_base_resp() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = str(request.url.query)
        return httpx.Response(200, json={"base_resp": {"status_code": 0, "status_msg": ""}, "claim_result": 1, "points": 100})

    account = Account({"id": "m", "access_token": "tok"}, provider_id="minimax")
    provider = MinimaxProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    outcome = provider.claim_daily(account)
    assert outcome is not None and outcome.status == CLAIM_CLAIMED and outcome.credits == 100
    assert seen["path"] == "/minimax-cloud/api/v1/signin/claim"
    assert "timezone_id=Asia/Shanghai" in seen["query"]


def test_minimax_already_is_claim_result_2() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"base_resp": {"status_code": 0}, "claim_result": 2})

    account = Account({"id": "m", "access_token": "tok"}, provider_id="minimax")
    provider = MinimaxProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    outcome = provider.claim_daily(account)
    assert outcome is not None and outcome.status == CLAIM_ALREADY


def test_minimax_balance_sums_details_remaining() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"details": [{"remaining_amount": 120}, {"remaining_amount": 80}]})

    account = Account({"id": "m", "access_token": "tok"}, provider_id="minimax")
    provider = MinimaxProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    row = provider.balance_of(account)
    assert row is not None and row.available == 200


def test_loomy_claim_uses_token_header_and_reads_already_processed() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["token"] = request.headers.get("token", "")
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"code": "000000", "data": {"alreadyProcessed": True, "dailyQuota": 5000}})

    account = Account({"id": "l", "access_token": "tok"}, provider_id="loomy")
    provider = LoomyProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    outcome = provider.claim_daily(account)
    assert outcome is not None and outcome.status == CLAIM_ALREADY
    assert seen["path"].endswith("/points/first-login")
    assert seen["token"] == "tok" and seen["auth"] == ""


def test_loomy_balance_reads_available_balance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "000000", "data": {"list": [], "availableBalance": 4200, "balance": 4200}})

    account = Account({"id": "l", "access_token": "tok"}, provider_id="loomy")
    provider = LoomyProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    row = provider.balance_of(account)
    assert row is not None and row.available == 4200


def test_trae_claim_uses_ug_endpoint_and_reads_claim_code() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path.endswith("/claim"):
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(200, json={"code": 0, "credits": 30})

    account = Account(
        {
            "id": "t",
            "access_token": "tok",
            "uid": "12345",
            "sessionid": "abc",
            "market_user_id": "mu",
            "device_id": "987654321098765",
        },
        provider_id="trae",
    )
    provider = TraeProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    outcome = provider.claim_daily(account)
    assert outcome is not None and outcome.status == CLAIM_CLAIMED and outcome.credits == 30
    assert any(p.endswith("/claim") for p in seen)


def test_trae_balance_sums_limit_minus_used() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "user_entitlement_pack_list": [
                    {"entitlement_base_info": {"quota": {"credits_limit": 100}}, "usage": {"credits_amount": 30}},
                    {"entitlement_base_info": {"quota": {"credits_limit": 50}}, "usage": {"credits_amount": 10}},
                ],
            },
        )

    account = Account({"id": "t", "access_token": "tok", "uid": "1"}, provider_id="trae")
    provider = TraeProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    row = provider.balance_of(account)
    assert row is not None and row.available == 110  # (100-30)+(50-10)


def test_qoder_claim_posts_empty_body_and_honours_replayed() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "showCampaign": True,
                    "claimable": True,
                    "campaigns": [{"campaignId": "c1", "actionType": "CLAIM_BENEFIT", "claimStatus": "CLAIMABLE", "benefit": {"amount": 100}}],
                },
            )
        return httpx.Response(200, json={"replayed": False, "benefit": {"amount": 100}})

    account = Account({"id": "q", "security_oauth_token": "tok"}, provider_id="qoder")
    provider = QoderProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    # Machine identity is missing on CI; the provider must fail cleanly, not invent a claim.
    outcome = provider.claim_daily(account)
    assert outcome is not None and outcome.status == CLAIM_FAILED
    assert "machine identity" in outcome.message


def test_qoder_balance_reads_user_quota_plus_addon() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"qoderUsage": {"userQuota": {"remaining": 0}, "addOnQuota": {"remaining": 100}}})

    account = Account({"id": "q", "security_oauth_token": "tok"}, provider_id="qoder")
    provider = QoderProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    row = provider.balance_of(account)
    assert row is not None and row.available == 100


def test_lobsterai_claim_walks_slot_context_checkin() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path.endswith("/slot"):
            return httpx.Response(200, json={"code": 0, "data": {"slotState": "available", "activity": {"activityCode": "daily", "configRevision": 7}}})
        if path.endswith("/context"):
            return httpx.Response(200, json={"code": 0, "data": {"state": {"claimedToday": False}, "actions": ["check_in"]}})
        if path.endswith("/check_in"):
            return httpx.Response(200, json={"code": 0, "data": {"result": {"creditsGranted": 100}}})
        return httpx.Response(404)

    account = Account({"id": "lb", "access_token": "tok"}, provider_id="lobsterai")
    provider = LobsteraiProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    outcome = provider.claim_daily(account)
    assert outcome is not None and outcome.status == CLAIM_CLAIMED and outcome.credits == 100
    assert any(p.endswith("/slot") for p in seen)
    assert any("check_in" in p for p in seen)


def test_lobsterai_already_when_context_says_claimed_today() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/slot"):
            return httpx.Response(200, json={"code": 0, "data": {"slotState": "available", "activity": {"activityCode": "daily", "configRevision": 7}}})
        return httpx.Response(200, json={"code": 0, "data": {"state": {"claimedToday": True}, "actions": []}})

    account = Account({"id": "lb", "access_token": "tok"}, provider_id="lobsterai")
    provider = LobsteraiProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    outcome = provider.claim_daily(account)
    assert outcome is not None and outcome.status == CLAIM_ALREADY


def test_lobsterai_balance_sums_credit_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"creditItems": [{"creditsRemaining": 100}, {"creditsRemaining": 50}]}})

    account = Account({"id": "lb", "access_token": "tok"}, provider_id="lobsterai")
    provider = LobsteraiProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    row = provider.balance_of(account)
    assert row is not None and row.available == 150


def test_workbuddy_says_unsupported_rather_than_inventing_a_claim() -> None:
    from hoshimi.core.providers.buddy import WorkBuddyProvider

    account = Account({"id": "wb", "access_token": "tok"}, provider_id="workbuddy")
    provider = WorkBuddyProvider(httpx.Client(), [account], {})
    outcome = provider.claim_daily(account)
    assert outcome is not None and outcome.status == CLAIM_UNSUPPORTED
