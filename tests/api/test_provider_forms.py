"""Portable form metadata and login cancellation across the management API."""

import threading

import httpx

from hoshimi.core.config import Settings
from hoshimi.core.login import LoginManager
from hoshimi.core.provider_forms import credential_forms
from hoshimi.core.providers import PROVIDER_CLASSES


def test_credential_forms_match_registered_account_validation(client):
    response = client.get("/api/v1/provider-forms")
    assert response.status_code == 200
    for provider in response.json():
        assert provider["id"] in PROVIDER_CLASSES
        for form in provider["forms"]:
            data = dict(form["defaults"])
            for field in form["fields"]:
                if field["required"]:
                    data[field["key"]] = ["model"] if field["kind"] == "list" else "https://relay.example/v1" if field["key"] == "base_url" else "value"
            PROVIDER_CLASSES[provider["id"]].check_account(data)
    assert "api_key" in {field.key for form in credential_forms()["nvidia"] for field in form.fields}


def test_local_forms_and_protocol_choices_are_available_before_connecting(client):
    rows = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    for provider in ("lmstudio", "llamacpp", "ollama"):
        assert rows[provider]["enabled"] is False
        assert rows[provider]["requires_authorization"] is False
        form = credential_forms()[provider][0]
        assert form.method == "local"
        assert all(not field.required for field in form.fields)
    google = credential_forms()["google"][0]
    assert next(field for field in google.fields if field.key == "protocol").choices == ("gemini", "chat")


def test_cancelled_login_cannot_persist_late_credentials(tmp_path, monkeypatch):
    released = threading.Event()
    finished = threading.Event()
    def flow(client, report):
        report("https://login.example")
        released.wait(5)
        return {"api_key": "late"}
    monkeypatch.setattr("hoshimi.core.login.manager._flows", lambda: {"test": flow})
    manager = LoginManager(Settings(), client=httpx.Client())
    original = manager._run
    def run(*args):
        try:
            original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(manager, "_run", run)
    session = manager.start("test")
    manager.cancel(session.id)
    released.set()
    assert finished.wait(5)
    assert not manager.settings.providers
    assert not (tmp_path / "config.json").exists()
    assert session.status == "failed"
    manager.close()
