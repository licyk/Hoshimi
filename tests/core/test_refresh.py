"""Credential renewal: the verdict, the timer, the write-back, and the eight lanes."""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from hoshimi.core.config import ProviderConfig, Settings
from hoshimi.core.expiry import as_ms, iso_as_ms, jwt_exp_ms
from hoshimi.core.providers.base import Account, Provider, RefreshTokenRejected
from hoshimi.core.providers.qoder import QoderProvider
from hoshimi.core.providers.raccoon import RaccoonProvider
from hoshimi.core.refresh import (
    REFRESH_LEAD_MS,
    Refresher,
    account_expiry_ms,
    is_refreshable,
    needs_refresh,
)
from hoshimi.core.registry import Registry


def _jwt(exp: int) -> str:
    """A two-segment JWT whose only claim is ``exp`` (seconds)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}."


def _account(**extra: Any) -> Account:
    data = {"id": "acc-1", "refresh_token": "rt"}
    data.update(extra)
    return Account(data, provider_id="cline")


# -- the verdict ---------------------------------------------------------- #
def test_needs_refresh_opens_one_hour_before_expiry() -> None:
    now = time.time() * 1000
    account = _account(expires_at=str(int(now + REFRESH_LEAD_MS / 2)))
    assert needs_refresh(account, now_ms=now) is True

    far = _account(expires_at=str(int(now + REFRESH_LEAD_MS * 2)))
    assert needs_refresh(far, now_ms=now) is False

    past = _account(expires_at=str(int(now - 1000)))
    assert needs_refresh(past, now_ms=now) is True


def test_an_account_with_no_expiry_is_renewed_rather_than_gambled_on() -> None:
    assert needs_refresh(_account(), now_ms=1.0) is True
    assert account_expiry_ms(_account()) is None


def test_a_key_only_account_is_never_refreshed() -> None:
    assert is_refreshable(_account(refresh_token="")) is False
    assert needs_refresh(_account(refresh_token=""), now_ms=1.0) is False


# -- the expiry shapes ---------------------------------------------------- #
def test_expiry_reads_milliseconds_seconds_and_iso() -> None:
    assert account_expiry_ms(_account(expires_at="1800000000000")) == 1_800_000_000_000
    # A seconds-scale timestamp lands on the same field and is scaled up.
    assert account_expiry_ms(_account(expires_at="1800000000")) == 1_800_000_000_000
    # The other names stations use.
    assert account_expiry_ms(_account(expire_time="1800000000000")) == 1_800_000_000_000
    expected = int(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert account_expiry_ms(_account(expiry="2027-01-01T00:00:00+00:00")) == expected


def test_the_helpers_convert_the_shapes_stations_send() -> None:
    assert as_ms("12") == 12_000
    assert as_ms("1800000000000") == 1_800_000_000_000
    assert as_ms(True) is None
    assert as_ms("soon") is None
    assert iso_as_ms("2027-01-01T00:00:00Z") == int(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert jwt_exp_ms(_jwt(1_800_000_000)) == 1_800_000_000_000
    assert jwt_exp_ms("not-a-jwt") is None


# -- the write-back ------------------------------------------------------- #
def _refresher(tmp_path: Any, accounts: list[dict[str, Any]]) -> Refresher:
    config_path = tmp_path / "config.json"
    settings = Settings(providers={"cline": ProviderConfig(id="cline", enabled=True, accounts=accounts)}, auto_refresh=False)
    provider = ClineLike(settings.providers["cline"].accounts)
    registry = Registry({"cline": provider})
    return Refresher(settings=settings, registry=registry, config_path=config_path)


class ClineLike(Provider):
    """A provider whose accounts are the raw dicts from the config, in place."""

    id = "cline"
    display_name = "Cline"

    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        # Mirrors collect_provider_accounts: the pool owns copies, the config
        # keeps the originals, so a renewal is a visible diff.
        super().__init__(httpx.Client(), [Account(dict(raw), provider_id="cline") for raw in accounts])
        self.refresh_calls = 0
        self.refresh_result = True
        self.refresh_raises: type[Exception] | None = None

    def fallback_models(self) -> list[str]:
        return []

    def fetch_models(self) -> list[str]:
        return []

    def stream_raw(self, account, model, body):  # type: ignore[no-untyped-def]
        yield {"type": "text_delta", "text": "unused"}

    def refresh_account(self, account: Account) -> bool:
        self.refresh_calls += 1
        if self.refresh_raises is not None:
            raise self.refresh_raises(401, "the station refused the refresh token")
        if self.refresh_result:
            account.set("access_token", f"new-{account.id}")
            account.set("expires_at", str(int(time.time() * 1000) + 7_200_000))
        return self.refresh_result


def test_persist_rewrites_only_the_account_that_changed(tmp_path: Any) -> None:
    refresher = _refresher(tmp_path, [{"id": "a", "access_token": "old-a"}, {"id": "b", "access_token": "old-b"}])
    provider: ClineLike = refresher.registry.providers["cline"]  # type: ignore[assignment]
    account = next(acc for acc in provider.accounts if acc.id == "a")
    account.set("access_token", "new-a")

    assert refresher._persist("cline", "a", account.data) is True
    written = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert written["providers"]["cline"]["accounts"][0]["access_token"] == "new-a"
    # The untouched neighbour is still as it was.
    assert written["providers"]["cline"]["accounts"][1]["access_token"] == "old-b"


def test_persist_is_a_no_op_when_nothing_changed(tmp_path: Any) -> None:
    refresher = _refresher(tmp_path, [{"id": "a", "access_token": "old-a"}])
    provider: ClineLike = refresher.registry.providers["cline"]  # type: ignore[assignment]
    assert refresher._persist("cline", "a", provider.accounts[0].data) is False
    assert not (tmp_path / "config.json").exists()


def test_persist_skips_an_account_the_config_file_never_had(tmp_path: Any) -> None:
    # An account imported from an OFM data dir has nowhere to go.
    refresher = _refresher(tmp_path, [{"id": "a", "access_token": "old"}])
    provider: ClineLike = refresher.registry.providers["cline"]  # type: ignore[assignment]
    provider.accounts.append(Account({"id": "ofm", "access_token": "x"}, provider_id="cline"))
    ofm = provider.accounts[-1]
    ofm.set("access_token", "y")
    assert refresher._persist("cline", "ofm", ofm.data) is False
    assert not (tmp_path / "config.json").exists()


# -- the round ------------------------------------------------------------ #
def test_a_rejected_refresh_token_is_terminal_and_takes_the_account_out(tmp_path: Any) -> None:
    refresher = _refresher(tmp_path, [{"id": "a", "access_token": "old", "refresh_token": "rt"}])
    provider: ClineLike = refresher.registry.providers["cline"]  # type: ignore[assignment]
    provider.refresh_raises = RefreshTokenRejected

    results = refresher.run_once(force=True)
    assert [(r.ok, r.terminal, r.error != "") for r in results] == [(False, True, True)]
    assert provider.accounts[0].needs_login is True

    # The next round does not spend itself rediscovering the same fact,
    # not even with force: only a new login puts the account back.
    calls_before = provider.refresh_calls
    assert refresher.run_once(force=True) == []
    assert provider.refresh_calls == calls_before


def test_a_successful_round_writes_back_and_keeps_the_account_in_rotation(tmp_path: Any) -> None:
    refresher = _refresher(tmp_path, [{"id": "a", "access_token": "old", "refresh_token": "rt", "expires_at": "1"}])
    provider: ClineLike = refresher.registry.providers["cline"]  # type: ignore[assignment]

    results = refresher.run_once(force=True)
    assert [(r.ok, r.persisted, r.terminal) for r in results] == [(True, True, False)]
    assert provider.accounts[0].needs_login is False
    written = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert written["providers"]["cline"]["accounts"][0]["access_token"] == "new-a"


def test_the_window_decides_who_is_touched_without_force(tmp_path: Any) -> None:
    refresher = _refresher(tmp_path, [{"id": "a", "refresh_token": "rt", "expires_at": str(int(time.time() * 1000) + REFRESH_LEAD_MS * 3)}])
    assert refresher.run_once() == []
    assert refresher.run_once(force=True)[0].ok is True


def test_status_reports_one_row_per_non_anonymous_account(tmp_path: Any) -> None:
    refresher = _refresher(tmp_path, [{"id": "a", "refresh_token": "rt", "expires_at": "1800000000000"}])
    rows = refresher.status()
    assert rows[0]["provider"] == "cline"
    assert rows[0]["account"] == "a"
    assert rows[0]["refreshable"] is True
    assert rows[0]["needs_login"] is False
    assert rows[0]["expires_at"] == 1_800_000_000_000
    assert rows[0]["failures"] == 0


def test_the_timer_starts_and_stops(tmp_path: Any) -> None:
    refresher = _refresher(tmp_path, [{"id": "a", "refresh_token": "rt"}])
    refresher.start()
    assert refresher._thread is not None and refresher._thread.is_alive()
    refresher.stop()
    assert refresher._thread is None


# -- the eight lanes, by wire shape --------------------------------------- #
def test_qoder_renews_on_the_device_token_path() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"code": 0, "data": {"security_oauth_token": "new-at", "refresh_token": "new-rt", "expires_at": "1800000000000"}},
        )

    account = Account({"id": "q", "refresh_token": "rt", "machine_id": "m"}, provider_id="qoder")
    provider = QoderProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    assert provider.refresh_account(account) is True
    assert seen["path"] == "/api/v1/deviceToken/refresh"
    # The request carries the old refresh token; the new tokens land on the account.
    assert '"refresh_token":"rt"' in seen["body"] and '"machine_id":"m"' in seen["body"]
    assert account.get("access_token") == "new-at"
    assert account.get("refresh_token") == "new-rt"
    assert account.get("expires_at") == "1800000000000"


def test_raccoon_renews_on_the_auth_refresh_path_and_keeps_a_bare_access_token() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        # A station that answers a new access token only: no refresh_token.
        return httpx.Response(200, json={"code": 0, "data": {"access_token": _jwt(1_800_000_000)}})

    account = Account({"id": "r", "refresh_token": "rt"}, provider_id="raccoon")
    provider = RaccoonProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    assert provider.refresh_account(account) is True
    assert seen["path"] == "/api/web/auth/v1/refresh"
    assert account.get("refresh_token") == "rt"
    assert account.get("expires_at") == "1800000000000"


def test_raccoon_treats_a_401_as_a_spent_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "session expired"})

    account = Account({"id": "r", "refresh_token": "rt"}, provider_id="raccoon")
    provider = RaccoonProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account], {})
    try:
        provider.refresh_account(account)
    except RefreshTokenRejected:
        pass
    else:
        raise AssertionError("a 401 must be terminal, not a retryable failure")
