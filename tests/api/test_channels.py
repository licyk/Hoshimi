"""The channels aggregate: one read, from memory only, with a status word per lane."""

from __future__ import annotations

import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.api.routers.channels import build_report
from hoshimi.api.routers.providers import list_providers
from hoshimi.core.config import ProviderConfig
from hoshimi.core.quota import QUOTA
from hoshimi.core.refresh import Refresher
from tests.conftest import FakeProvider, make_services


@pytest.fixture(autouse=True)
def clean_quota() -> Iterator[None]:
    QUOTA.reset()
    yield
    QUOTA.reset()


def _no_network(services: Any, *, catalogue: bool = True) -> None:
    """Balances are an upstream round trip each: the channels page must never ask."""

    def balance(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the channels report must not query balances")

    def forbidden() -> Any:
        raise AssertionError("the channels report must not query the catalogue")

    services.credits = SimpleNamespace(
        balance=balance,
        start=lambda: None,
        stop=lambda: None,
        store=SimpleNamespace(doc=SimpleNamespace(last_date="2026-10-21", last_result="ok", last_at=12.5), recent_by_provider=lambda limit: {}),
    )
    if catalogue:
        services.registry.providers["fake"].models = forbidden


def _channel(report: Any, provider_id: str) -> Any:
    return next(channel for channel in report.channels if channel.id == provider_id)


def test_channels_list_the_same_lanes_as_providers_without_network() -> None:
    services = make_services()
    _no_network(services, catalogue=False)
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        assert client.post("/v1/chat/completions", json={"model": "fake/fake-1", "stream": False, "messages": [{"role": "user", "content": "hi"}]}).status_code == 200
        _no_network(services)
        response = client.get("/api/v1/channels")
        providers = client.get("/api/v1/providers").json()
    assert response.status_code == 200
    payload = response.json()
    assert [channel["id"] for channel in payload["channels"]] == [row["id"] for row in providers]
    assert payload["checkin"] == {"last_date": "2026-10-21", "last_result": "ok", "last_at": 12.5, "auto_checkin": services.settings.auto_checkin}
    assert (payload["auto_refresh"], payload["auto_checkin"]) == (services.settings.auto_refresh, services.settings.auto_checkin)

    fake = next(channel for channel in payload["channels"] if channel["id"] == "fake")
    assert (fake["display_name"], fake["enabled"], fake["configured"], fake["status"]) == ("Fake", True, True, "ok")
    assert fake["network"] == {"mode": "inherit", "proxy_configured": False, "system_proxy": False}
    assert fake["usage_today"]["calls"] == 1 and fake["usage_today"]["input"] == 1
    account = fake["accounts"][0]
    assert account["id"] == "fake-0"
    assert account["usage_today"]["calls"] == 1
    assert account["last_used_at"] is not None
    assert (account["needs_login"], account["cooled_reason"], account["expires_at"]) == (False, "", None)


def test_status_words() -> None:
    services = make_services()
    _no_network(services)
    lane = services.registry.providers["fake"]
    assert _channel(build_report(services), "fake").status == "ok"

    lane.cooler.hold("fake", "fake-0", 60, reason="rate_limited")
    held = _channel(build_report(services), "fake")
    assert held.status == "attention"
    assert held.accounts[0].cooled_reason == "rate_limited" and 0 < held.accounts[0].cooled_seconds <= 60
    lane.cooler.clear("fake", "fake-0")

    lane.accounts[0].needs_login = True
    assert _channel(build_report(services), "fake").status == "attention"
    lane.accounts[0].needs_login = False

    lane.accounts[0].data["expires_at"] = int((time.time() - 60) * 1000)
    expired = _channel(build_report(services), "fake")
    assert expired.status == "attention" and expired.accounts[0].expires_at is not None
    lane.accounts[0].data.pop("expires_at")

    QUOTA.observe("fake", 200, {"x-ratelimit-remaining": "5", "x-ratelimit-limit": "100"})
    low = _channel(build_report(services), "fake")
    assert low.status == "attention" and low.quota is not None and low.quota.percent == 5
    QUOTA.reset()
    QUOTA.observe("fake", 429, {})
    assert _channel(build_report(services), "fake").status == "attention"
    QUOTA.reset()
    assert _channel(build_report(services), "fake").status == "ok"

    lane.accounts = []
    assert _channel(build_report(services), "fake").status == "empty"
    services.settings.providers["fake"].enabled = False
    assert _channel(build_report(services), "fake").status == "disabled"


def test_disabled_and_unconfigured_lanes_are_listed() -> None:
    services = make_services()
    _no_network(services)
    services.settings.providers["nvidia"] = ProviderConfig(id="nvidia", enabled=False, accounts=[{"id": "n-1", "api_key": "k"}])
    report = build_report(services)
    nvidia = _channel(report, "nvidia")
    assert (nvidia.enabled, nvidia.configured, nvidia.status) == (False, True, "disabled")
    assert [account.id for account in nvidia.accounts] == ["n-1"]
    # A lane offered only because it can be added: listed, not configured, and
    # "not set up" rather than "switched off".
    pending = [channel for channel in report.channels if not channel.configured]
    assert pending and all(channel.status == "empty" for channel in pending)
    assert {channel.id for channel in report.channels} == {row.id for row in list_providers(services)}
    agg = _channel(report, "agg")
    assert agg.addable


def test_renewal_state_comes_from_the_refresher() -> None:
    services = make_services()
    _no_network(services)
    services.refresher = Refresher(settings=services.settings, registry=services.registry)
    services.refresher.failures[("fake", "fake-0")] = 2
    services.refresher.last_error[("fake", "fake-0")] = "refresh refused"
    services.refresher.last_ok[("fake", "fake-0")] = 100.0
    account = _channel(build_report(services), "fake").accounts[0]
    assert (account.failures, account.last_error, account.last_ok) == (2, "refresh refused", 100.0)


def test_cooled_accounts_on_a_second_lane_stay_on_it() -> None:
    services = make_services(FakeProvider())
    _no_network(services)
    services.registry.providers["fake0"].cooler.hold("fake0", "fake-0", 30, reason="policy_blocked")
    report = build_report(services)
    assert _channel(report, "fake").accounts[0].cooled_reason == ""
    assert _channel(report, "fake0").accounts[0].cooled_reason == "policy_blocked"


def test_restart_required_tracks_a_saved_listen_address() -> None:
    services = make_services()
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        assert client.get("/api/v1/settings").json()["restart_required"] is False
        # Not bound by this process (embedded, tests): never reported.
        assert client.patch("/api/v1/settings", json={"server": {"port": 8123}}).json()["restart_required"] is False

    bound = make_services()
    with TestClient(create_app(bound, bound_host="127.0.0.1", bound_port=40123, serve_ui=False), base_url="http://localhost") as client:
        assert client.get("/api/v1/settings").json()["restart_required"] is False
        moved = client.patch("/api/v1/settings", json={"server": {"port": 8124}})
        assert moved.status_code == 200 and moved.json()["restart_required"] is True
        back = client.patch("/api/v1/settings", json={"server": {"port": 0}})
        assert back.json()["restart_required"] is False


def test_a_port_that_fell_back_is_reported_but_needs_no_restart() -> None:
    services = make_services()
    services.settings.port = 40123
    app = create_app(services, bound_host="127.0.0.1", bound_port=40125, serve_ui=False)
    with TestClient(app, base_url="http://localhost") as client:
        view = client.get("/api/v1/settings").json()
        assert view["listening"] == {"host": "127.0.0.1", "port": 40125, "requested_port": 40123, "fallback": True}
        assert view["server"]["port"] == 40123 and view["restart_required"] is False

    # `serve --port` names the first port tried; the config may say another.
    overridden = make_services()
    with TestClient(create_app(overridden, bound_host="127.0.0.1", bound_port=9000, requested_port=9000, serve_ui=False), base_url="http://localhost") as client:
        view = client.get("/api/v1/settings").json()
        assert view["listening"]["fallback"] is False and view["restart_required"] is False

    # Port 0 asks the system for any port: whatever it picked is not a fallback.
    with TestClient(create_app(make_services(), bound_host="127.0.0.1", bound_port=40126, serve_ui=False), base_url="http://localhost") as client:
        assert client.get("/api/v1/settings").json()["listening"] == {"host": "127.0.0.1", "port": 40126, "requested_port": 0, "fallback": False}

    with TestClient(create_app(make_services(), serve_ui=False), base_url="http://localhost") as client:
        assert client.get("/api/v1/settings").json()["listening"] is None


def test_strict_port_and_stats_retention_are_settings(client: Any, services: Any) -> None:
    import json

    from hoshimi.core.paths import config_file_path

    view = client.get("/api/v1/settings").json()["server"]
    assert (view["strict_port"], view["stats_keep_days"]) == (False, 90)
    saved = client.patch("/api/v1/settings", json={"server": {"strict_port": True, "stats_keep_days": 30}})
    assert saved.status_code == 200
    assert (saved.json()["server"]["strict_port"], saved.json()["server"]["stats_keep_days"]) == (True, 30)
    assert services.stats.keep_days == 30
    stored = json.loads(config_file_path().read_text(encoding="utf-8"))["server"]
    assert (stored["strict_port"], stored["stats_keep_days"]) == (True, 30)
    for bad in ({"stats_keep_days": 0}, {"stats_keep_days": "30"}, {"strict_port": "yes"}):
        assert client.patch("/api/v1/settings", json={"server": bad}).status_code == 400, bad
    assert services.stats.keep_days == 30


def test_checkin_history_is_listed_and_shown_per_lane() -> None:
    from hoshimi.core.credits import CheckinStore, CreditsService
    from hoshimi.core.providers.base import CLAIM_ALREADY, CLAIM_CLAIMED, CLAIM_FAILED, ClaimOutcome

    services = make_services()
    services.credits = CreditsService(settings=services.settings, registry=services.registry, store=CheckinStore(db=services.db))
    now = time.time()
    for index in range(7):
        services.credits.store.record(
            [ClaimOutcome("fake", "fake-0", CLAIM_CLAIMED if index % 2 else CLAIM_ALREADY, credits=index, message=f"run {index}")], at=now - (7 - index) * 86400
        )
    services.credits.store.record([ClaimOutcome("other", "x", CLAIM_FAILED, message="refused")], at=now)
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        history = client.get("/api/v1/credits/checkins", params={"provider": "fake", "limit": 3}).json()["items"]
        assert [row["message"] for row in history] == ["run 6", "run 5", "run 4"]
        assert history[1] == {
            "at": history[1]["at"],
            "date": history[1]["date"],
            "provider": "fake",
            "account": "fake-0",
            "ok": True,
            "message": "run 5",
            "status": "claimed",
            "credits": 5,
        }
        everything = client.get("/api/v1/credits/checkins").json()["items"]
        assert everything[0]["provider"] == "other" and everything[0]["ok"] is False and len(everything) == 8
        assert client.get("/api/v1/credits/checkins", params={"limit": 0}).status_code == 422
        lane = next(row for row in client.get("/api/v1/channels").json()["channels"] if row["id"] == "fake")
        assert [row["message"] for row in lane["recent_checkins"]] == ["run 6", "run 5", "run 4", "run 3", "run 2"]

    bare = make_services()
    with TestClient(create_app(bare, serve_ui=False), base_url="http://localhost") as client:
        assert client.get("/api/v1/credits/checkins").json() == {"items": []}
