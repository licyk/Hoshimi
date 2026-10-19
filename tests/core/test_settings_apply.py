"""A settings save lands in the running gateway, not only in the file.

`PATCH /api/v1/settings` used to write the config and then leave the provider
pool untouched, so an account added through the web UI did not exist until the
process restarted — the one thing a web UI cannot ask of an operator who is
already using it. `Services.apply_settings` is the fix; these tests are about
the two properties that made it more than a one-line swap:

- a save that does not move what the pool is built from must not rebuild it,
  because a rebuild makes fresh accounts and a fresh account has forgotten
  that its refresh token was refused;
- every service holding the settings object must be re-pointed, because the
  renewal timer and the login manager each persist *through* that reference —
  leaving one behind would have it write the previous configuration over the
  file the operator just edited.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from hoshimi.core.config import ProviderConfig, Settings, save_settings, settings_from_dict, settings_to_dict
from hoshimi.core.context import Services, build_services
from hoshimi.core.credits import CHECKIN_FILE_NAME, CheckinStore, CreditsService
from hoshimi.core.login import LoginManager
from hoshimi.core.refresh import Refresher
from hoshimi.core.registry import build_registry
from tests.conftest import FakeProvider, make_services


def _patched(services: Services, patch: dict[str, Any]) -> Settings:
    """The settings a PATCH would hand to apply_settings: the file, deep-merged."""
    merged = copy.deepcopy(settings_to_dict(services.settings))
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return settings_from_dict(merged, Settings())


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


# -- the pool follows the config ------------------------------------------ #
def test_an_account_added_by_a_save_is_live_without_a_restart() -> None:
    services = make_services()
    assert [a.id for a in services.registry.providers["fake"].accounts] == ["fake-0"]

    services.apply_settings(_patched(services, {"providers": {"fake": {"accounts": [{"id": "brand-new", "access_token": "t"}]}}}))

    assert [a.id for a in services.registry.providers["fake"].accounts] == ["brand-new"]


def test_a_lane_the_save_enables_appears_in_the_running_pool() -> None:
    """A provider the config did not name is not in the pool; naming it is enough."""
    services = make_services()
    services.settings.providers["agg"] = ProviderConfig(id="agg", enabled=False)
    assert "agg" not in services.registry.providers

    services.apply_settings(
        _patched(
            services,
            {"providers": {"agg": {"enabled": True, "accounts": [{"id": "bai", "preset": "bai", "api_key": "k"}]}}},
        )
    )

    assert "agg" in services.registry.providers


def test_disabling_a_lane_takes_it_out_of_the_running_pool() -> None:
    services = make_services()
    assert "fake" in services.registry.providers

    services.apply_settings(_patched(services, {"providers": {"fake": {"enabled": False}}}))

    assert "fake" not in services.registry.providers


# -- what must survive a save --------------------------------------------- #
def test_a_save_that_moves_nothing_rebuilds_nothing() -> None:
    services = make_services()
    before = services.registry.providers["fake"]

    rebuilt = services.apply_settings(_patched(services, {"server": {"port": 1234, "default_model": "fake-1"}}))

    assert rebuilt is False
    assert services.registry.providers["fake"] is before
    assert services.settings.port == 1234


def test_an_unchanged_lane_keeps_its_memory_when_another_lane_changes() -> None:
    """Editing one station's key must not clear another station's rate-limit hold."""
    registry = build_registry(
        {"cline": [{"id": "c1", "access_token": "t"}], "agg": [{"id": "bai", "preset": "bai", "api_key": "k"}]}
    )
    cline = registry.providers["cline"]
    cline.cooler.hold("cline", "c1", 3_600_000.0, reason="rate_limited")
    assert cline.cooler.blocked("cline", "c1") is True

    registry.rebuild(
        {"cline": [{"id": "c1", "access_token": "t"}], "agg": [{"id": "bai", "preset": "bai", "api_key": "rotated"}]},
        disabled=set(),
    )

    assert registry.providers["cline"] is cline
    assert registry.providers["cline"].cooler.blocked("cline", "c1") is True
    assert registry.providers["agg"] is not None


def test_a_lane_whose_own_config_changed_is_rebuilt() -> None:
    """The other half of the same rule: the lane that *did* move gets a fresh one."""
    registry = build_registry({"cline": [{"id": "c1", "access_token": "t"}]})
    original = registry.providers["cline"]
    original.cooler.hold("cline", "c1", 3_600_000.0, reason="rate_limited")

    registry.rebuild({"cline": [{"id": "c1", "access_token": "u"}]}, disabled=set())

    assert registry.providers["cline"] is not original
    assert registry.providers["cline"].cooler.blocked("cline", "c1") is False


def test_the_ban_store_survives_a_rebuild() -> None:
    """`404 for this id` is a fact about the station, not about the accounts."""
    services = make_services()
    services.registry.dead_models.strike("fake", "gone-model", reason="HTTP 404")

    services.apply_settings(
        _patched(services, {"providers": {"fake": {"accounts": [{"id": "brand-new", "access_token": "t"}]}}})
    )

    assert services.registry.dead_models.is_dead("fake", "gone-model") is True


def test_a_rebuild_keeps_the_shared_http_client() -> None:
    """A turn already streaming holds a provider object; its client must stay open."""
    registry = build_registry({"cline": [{"id": "c1", "access_token": "t"}]})
    client = registry.client
    assert client is not None

    registry.rebuild({"cline": [{"id": "c1", "access_token": "t"}, {"id": "c2", "access_token": "u"}]})

    assert registry.client is client
    assert registry.providers["cline"].client is client
    assert [a.id for a in registry.providers["cline"].accounts] == ["c1", "c2"]


def test_a_rebuilt_lane_is_swapped_not_mutated() -> None:
    """The pool is replaced wholesale: nothing holds a half-updated lane."""
    registry = build_registry({"cline": [{"id": "c1", "access_token": "t"}]})
    old = registry.providers["cline"]

    registry.rebuild({"cline": [{"id": "c9", "access_token": "t"}]})

    assert registry.providers["cline"] is not old
    assert [a.id for a in registry.providers["cline"].accounts] == ["c9"]


# -- the services holding the settings ------------------------------------ #
def _services_with_timers(tmp_path: Path) -> Services:
    services = make_services()
    services.refresher = Refresher(settings=services.settings, registry=services.registry)
    services.credits = CreditsService(
        settings=services.settings,
        registry=services.registry,
        store=CheckinStore(tmp_path / CHECKIN_FILE_NAME),
    )
    services.login = LoginManager(settings=services.settings)
    return services


def test_a_browser_login_rebuilds_the_running_pool_after_a_settings_save(tmp_path: Path) -> None:
    """The login callback must read the manager's current settings, not boot-time state."""
    initial = Settings(port=0, providers={"fake": ProviderConfig(id="fake", accounts=[{"id": "fake-0", "access_token": "t"}])})
    save_settings(initial)
    services = build_services()

    services.apply_settings(_patched(services, {"server": {"port": 1234}}))
    assert services.login is not None
    account_id = services.login._persist("fake", {"access_token": "u", "account_id": "user-1"})

    assert [a.id for a in services.registry.providers["fake"].accounts] == ["fake-0", account_id]


def test_every_service_holding_the_settings_is_repointed(tmp_path: Path) -> None:
    """The renewal timer and the login manager persist through their own reference."""
    services = _services_with_timers(tmp_path)
    updated = _patched(services, {"server": {"port": 1234}})

    services.apply_settings(updated)

    assert services.refresher is not None and services.refresher.settings is updated
    assert services.credits is not None and services.credits.settings is updated
    assert services.login is not None and services.login.settings is updated


def test_a_renewal_after_a_save_writes_the_new_config_not_the_old_one(tmp_path: Path) -> None:
    """The failure the re-pointing exists to prevent: a stale reference clobbering the file."""
    services = _services_with_timers(tmp_path)
    services.apply_settings(_patched(services, {"server": {"port": 1234}}))
    provider = services.registry.providers["fake"]
    provider.accounts[0].data["access_token"] = "rotated"

    assert services.refresher is not None
    assert services.refresher._persist("fake", provider.accounts[0].id, provider.accounts[0].data) is True

    import json

    assert json.loads(services.config_path.read_text(encoding="utf-8"))["server"]["port"] == 1234


def test_apply_settings_reports_whether_it_rebuilt(tmp_path: Path) -> None:
    services = _services_with_timers(tmp_path)

    assert services.apply_settings(_patched(services, {"server": {"auto_checkin": False}})) is False
    assert services.apply_settings(_patched(services, {"providers": {"fake": {"accounts": [{"id": "x", "access_token": "t"}]}}})) is True


def test_the_declared_lane_is_what_a_rebuild_reproduces() -> None:
    """`make_services` declares `fake` in the config, so a rebuild keeps it.

    A pool that exists only in memory would make every rebuild look like it
    deleted everything — which is the bug these tests are here to catch, not a
    fixture detail worth rediscovering.
    """
    services = make_services()
    rebuilt = services.apply_settings(_patched(services, {"server": {"port": 9999}}))

    assert rebuilt is False
    assert isinstance(services.registry.providers["fake"], FakeProvider)
