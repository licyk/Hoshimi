"""Reauthorization replaces the selected entry only after a successful flow."""

import copy
import json

import httpx
import pytest

from hoshimi.core.config import ProviderConfig, Settings, save_settings
from hoshimi.core.login import LoginError, LoginManager
from hoshimi.core.login.manager import LoginSession
from hoshimi.core.paths import config_file_path
from hoshimi.core.provider_forms import account_edit_mode


def manager_and_session():
    raw = {"id": "selected", "access_token": "old", "refresh_token": "old-refresh", "account_id": "old-user", "models": ["model"]}
    settings = Settings(providers={"openai": ProviderConfig(id="openai", accounts=[raw, {"id": "other", "access_token": "keep"}])})
    save_settings(settings)
    manager = LoginManager(settings, client=httpx.Client())
    session = LoginSession(id="attempt", provider="openai", replace_account_id="selected", original_account=copy.deepcopy(raw))
    return manager, session


def successful_flow(_client, report):
    report("https://login.example")
    return {"access_token": "new", "account_id": "different-user"}


def test_success_replaces_selected_id_instead_of_appending_a_new_identity():
    manager, session = manager_and_session()
    try:
        manager._run(session, successful_flow)
        assert session.status == "succeeded" and session.account_id == "selected"
        accounts = json.loads(config_file_path().read_text())["providers"]["openai"]["accounts"]
        assert accounts == [
            {"id": "selected", "access_token": "new", "account_id": "different-user", "models": ["model"], "_hoshimi_login": "browser"},
            {"id": "other", "access_token": "keep"},
        ]
    finally:
        manager.close()


@pytest.mark.parametrize("failure", ["denied", "cancelled", "removed", "edited", "disk", "rebuild"])
def test_unsuccessful_relogin_preserves_existing_state(failure, monkeypatch):
    manager, session = manager_and_session()
    if failure == "removed":
        manager.settings.providers["openai"].accounts.pop(0)
    elif failure == "edited":
        manager.settings.providers["openai"].accounts[0]["access_token"] = "edited"
    elif failure == "cancelled":
        session.cancelled.set()
    before = copy.deepcopy(manager.settings.providers)
    saved = config_file_path().read_bytes()
    def flow(client, report):
        if failure == "denied":
            raise LoginError("access denied")
        return successful_flow(client, report)
    def fail_save(_settings):
        raise OSError("disk full")
    if failure == "disk":
        monkeypatch.setattr("hoshimi.core.config.save_settings", fail_save)
    elif failure == "rebuild":
        manager._on_persist = fail_save
    try:
        manager._run(session, flow)
        assert session.status == "failed"
        assert manager.settings.providers == before
        assert config_file_path().read_bytes() == saved
    finally:
        manager.close()


def test_start_rejects_a_missing_target_before_launching_browser():
    manager, _session = manager_and_session()
    try:
        with pytest.raises(LoginError, match="no longer exists"):
            manager.start("openai", account_id="missing")
    finally:
        manager.close()


def test_relogin_updates_the_running_pool_and_clears_old_renewal_failures():
    from hoshimi.core.context import build_services

    manager, session = manager_and_session()
    services = build_services(manager.settings)
    manager.close()
    try:
        services.refresher.failures["openai", "selected"] = 3
        services.refresher.last_error["openai", "selected"] = "old refusal"
        services.registry.providers["openai"].accounts[0].needs_login = True
        services.login._run(session, successful_flow)
        assert session.status == "succeeded"
        account = services.registry.providers["openai"].accounts[0]
        assert account.id == "selected" and account.get("access_token") == "new"
        assert not account.needs_login
        assert not services.refresher.failures and not services.refresher.last_error
    finally:
        services.close()


def test_relogin_preserves_a_disabled_channel():
    manager, session = manager_and_session()
    manager.settings.providers["openai"].enabled = False
    try:
        manager._run(session, successful_flow)
        assert session.status == "succeeded"
        assert not manager.settings.providers["openai"].enabled
    finally:
        manager.close()


@pytest.mark.parametrize(("provider", "data", "mode"), [
    ("openai", {"api_key": "key"}, "config"),
    ("openai", {"access_token": "token", "refresh_token": "refresh"}, "login"),
    ("kilo", {"access_token": "token"}, "login"),
    ("kilo", {"api_key": "key"}, "config"),
    ("qodercli", {"personal_token": "pat"}, "config"),
    ("openrouter", {"api_key": "key", "_hoshimi_login": "browser"}, "login"),
    ("openai", {"access_token": "token", "_hoshimi_login": "config"}, "config"),
])
def test_edit_mode_distinguishes_accounts_on_the_same_provider(provider, data, mode):
    assert account_edit_mode(provider, data, loginable=True) == mode
