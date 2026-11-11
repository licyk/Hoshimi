"""Account editors expose real values on demand and update exactly one entry."""

import copy
import json

import pytest

from hoshimi.core.config import ProviderConfig, save_settings
from hoshimi.core.paths import config_file_path


def install(services, provider="openai", **account):
    candidate = copy.deepcopy(services.settings)
    candidate.providers[provider] = ProviderConfig(id=provider, accounts=[account, {"id": "other", "api_key": "untouched"}])
    save_settings(candidate)
    services.apply_settings(candidate)


def test_editor_returns_real_secret_without_leaking_it_into_channel_report(client, services):
    install(services, id="mine", api_key="original-secret", models=["model-a"], base_url="https://example.test/v1")
    response = client.get("/api/v1/providers/openai/accounts/mine")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["account"]["api_key"] == "original-secret"
    report = client.get("/api/v1/channels")
    assert "original-secret" not in report.text
    channel = next(row for row in report.json()["channels"] if row["id"] == "openai")
    assert channel["accounts"][0]["edit_mode"] == "config"


def test_update_keeps_id_other_accounts_and_clears_optional_fields(client, services):
    install(services, api_key="old", models=["old-model"], base_url="https://old.test/v1")
    response = client.put("/api/v1/providers/openai/accounts/openai-0", json={"account": {"api_key": "new", "protocol": "responses"}})
    assert response.status_code == 200, response.text
    saved = json.loads(config_file_path().read_text())["providers"]["openai"]["accounts"]
    assert saved[0] == {"id": "openai-0", "api_key": "new", "protocol": "responses", "_hoshimi_login": "config"}
    assert saved[1] == {"id": "other", "api_key": "untouched"}
    assert services.registry.providers["openai"].accounts[0].get("api_key") == "new"


@pytest.mark.parametrize("data", [{}, {"api_key": ""}, {"api_key": "new", "id": "renamed"}, {"api_key": "new", "protocol": "invalid"}, {"api_key": "new", "models": "bad"}])
def test_invalid_edit_preserves_file_and_pool(client, services, data):
    install(services, id="mine", api_key="old")
    before = config_file_path().read_bytes()
    account = services.registry.providers["openai"].accounts[0]
    response = client.put("/api/v1/providers/openai/accounts/mine", json={"account": data})
    assert response.status_code == 400
    assert config_file_path().read_bytes() == before
    assert services.registry.providers["openai"].accounts[0] is account


def test_write_failure_leaves_live_account_untouched(client, services, monkeypatch):
    install(services, id="mine", api_key="old")
    before = copy.deepcopy(services.settings.providers["openai"].accounts)
    def fail(_settings):
        raise OSError("disk full")
    monkeypatch.setattr("hoshimi.api.routers.providers.save_settings", fail)
    with pytest.raises(OSError, match="disk full"):
        client.put("/api/v1/providers/openai/accounts/mine", json={"account": {"api_key": "new"}})
    assert services.settings.providers["openai"].accounts == before


def test_browser_account_uses_relogin_and_cannot_be_overwritten_by_form(client, services):
    install(services, id="mine", access_token="old", refresh_token="refresh", _hoshimi_login="browser")
    report = client.get("/api/v1/channels").json()
    account = next(row for row in report["channels"] if row["id"] == "openai")["accounts"][0]
    assert account["edit_mode"] == "login"
    response = client.put("/api/v1/providers/openai/accounts/mine", json={"account": {"api_key": "new"}})
    assert response.status_code == 400
    assert services.settings.providers["openai"].accounts[0]["access_token"] == "old"


def test_disabled_legacy_account_has_an_editable_derived_id(client, services):
    install(services, api_key="old")
    services.settings.providers["openai"].enabled = False
    report = client.get("/api/v1/channels").json()
    account = next(row for row in report["channels"] if row["id"] == "openai")["accounts"][0]
    assert account["id"] == "openai-0" and account["edit_mode"] == "config"
    assert client.put("/api/v1/providers/openai/accounts/openai-0", json={"account": {"api_key": "new"}}).status_code == 200
    assert not services.settings.providers["openai"].enabled


def test_custom_secret_and_json_fields_survive_editing(client, services):
    install(services, provider="amazon-bedrock", id="mine", aws_access_key_id="AKID", aws_secret_access_key="secret", aws_session_token="token", models=["m"])
    detail = client.get("/api/v1/providers/amazon-bedrock/accounts/mine").json()
    assert detail["form"]["method"] == "aws"
    assert detail["account"]["aws_session_token"] == "token"
    assert next(field for field in detail["form"]["fields"] if field["key"] == "aws_session_token")["kind"] == "secret"


def test_missing_account_cannot_be_created_by_edit(client):
    path = "/api/v1/providers/openai/accounts/missing"
    assert client.get(path).status_code == 404
    assert client.put(path, json={"account": {"api_key": "new"}}).status_code == 404


def test_changing_pat_discards_old_job_token_and_renewal_errors(client, services):
    from hoshimi.core.refresh import Refresher

    install(services, provider="qodercli", id="mine", personal_token="old-pat", access_token="old-job", expires_at="9999999999999")
    services.refresher = Refresher(services.settings, services.registry)
    services.refresher.failures["qodercli", "mine"] = 2
    services.refresher.last_error["qodercli", "mine"] = "old error"
    response = client.put("/api/v1/providers/qodercli/accounts/mine", json={"account": {"personal_token": "new-pat", "access_token": "old-job", "expires_at": "9999999999999"}})
    assert response.status_code == 200, response.text
    account = services.registry.providers["qodercli"].accounts[0]
    assert account.get("personal_token") == "new-pat"
    assert account.get("access_token") is None
    assert account.get("expires_at") is None
    assert not services.refresher.failures and not services.refresher.last_error
