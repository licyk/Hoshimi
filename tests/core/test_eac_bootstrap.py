"""Fresh EAC installs can sign in without storing shared relay secrets."""

import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.core import eac_seal
from hoshimi.core.config import ProviderConfig, Settings
from hoshimi.core.context import build_services
from hoshimi.core.eac_relay import default_eac_account
from hoshimi.core.eac_user import read_eac_user
from hoshimi.core.errors import ValidationError
from hoshimi.core.login import LoginError, LoginManager
from hoshimi.core.login.common import LoginReporter
from hoshimi.core.login.eac import _resolve_account, start_eac_login
from hoshimi.core.paths import config_file_path
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.eac import EacProvider


@pytest.fixture
def relay(monkeypatch, tmp_path):
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("hoshimi.core.eac_relay.open_bundled_relay", lambda: {"base_url": "https://relay.example/eac/v1", "signing_secret": "test-signing-secret"})
    monkeypatch.setattr(LoginReporter, "wait", lambda self, seconds: self.check_cancelled())


def test_bundled_seal_opens_without_a_reference_checkout():
    credential = eac_seal.open_bundled_relay()
    assert credential["base_url"].startswith("https://")
    assert credential["base_url"].endswith("/v1")
    assert len(credential["signing_secret"]) >= 32


def test_tampered_seal_has_a_sanitized_error(monkeypatch):
    monkeypatch.setattr(eac_seal, "_SEAL", "A" * len(eac_seal._SEAL))
    with pytest.raises(ValidationError, match="bundled relay configuration is unavailable"):
        eac_seal.open_bundled_relay()


def test_fresh_login_saves_only_reference_and_user_identity(relay, tmp_path):
    settings = Settings()
    done = threading.Event()
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "relay.example"
        if request.url.path == "/eac/auth/github/start":
            return httpx.Response(302)
        if request.url.path == "/eac/auth/poll":
            assert request.url.params["retain"] == "1"
            return httpx.Response(200, json={"status": "ok", "token": "host-user-token", "login": "user", "ackRequired": True})
        assert request.method == "POST" and request.url.path == "/eac/auth/ack"
        assert read_eac_user()["token"] == "host-user-token"
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        manager = LoginManager(settings, client=client, on_persist=lambda _: done.set())
        session = manager.start("eac")
        assert done.wait(3), session.message
        with manager._persist_lock:
            pass
        assert session.status == "succeeded" and session.account_id == "eac-default"
        raw = json.loads(config_file_path().read_text())
        account = raw["providers"]["eac"]["accounts"][0]
        assert account == {"id": "eac-default", "relay": "bundled", "user_token": "host-user-token", "user_login": "user", "user_avatar": ""}
        assert "test-signing-secret" not in json.dumps(raw)
        assert "base_url" not in account
        assert "user_token" not in session.as_dict()
        lane = EacProvider(client, [Account(account)])
        headers = lane._headers(lane.accounts[0], method="GET", url="https://relay.example/eac/v1/models", body="")
        assert headers["x-ofm-user"] == "host-user-token"
        assert "x-ofm-signature" in headers
        manager.close()


def test_cancelled_bootstrap_cannot_create_an_account_or_ack(relay, tmp_path):
    settings = Settings()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/start"):
            return httpx.Response(302)
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json={"status": "ok", "token": "late", "ackRequired": True})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        manager = LoginManager(settings, client=client)
        run = manager._run

        def wrapped(*args):
            try:
                run(*args)
            finally:
                finished.set()

        manager._run = wrapped
        session = manager.start("eac")
        assert entered.wait(3)
        manager.cancel(session.id)
        release.set()
        assert finished.wait(3)
        assert session.status == "failed"
        assert not settings.providers
        assert not config_file_path().exists()
        assert read_eac_user() is None
        assert not any(path.endswith("/ack") for path in paths)
        manager.close()


def test_bootstrap_refuses_to_overwrite_a_relay_added_during_login(relay, tmp_path):
    settings = Settings()
    with httpx.Client() as client:
        manager = LoginManager(settings, client=client)
        settings.providers["eac"] = ProviderConfig(id="eac", accounts=[{"id": "custom", "base_url": "https://custom.example/v1", "signing_secret": "custom"}])
        with pytest.raises(LoginError, match="changed or was removed"):
            manager._eac_target({**default_eac_account(), "user_token": "new"})
        manager.close()


def test_worker_is_preferred_and_direct_relays_do_not_start_oauth(relay):
    settings = Settings(
        providers={
            "eac": ProviderConfig(
                id="eac",
                accounts=[
                    {"id": "direct", "base_url": "https://direct.example/v1", "access_token": "key"},
                    {"id": "worker", "base_url": "https://custom.example/eac/v1", "signing_secret": "secret"},
                ],
            )
        }
    )
    assert _resolve_account(settings)["id"] == "worker"
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("direct relays must not start GitHub OAuth"))) as client,
        pytest.raises(LoginError, match="requires a worker relay"),
    ):
        start_eac_login(client, lambda _: None, account=settings.providers["eac"].accounts[0])


@pytest.mark.parametrize(
    "account",
    [
        {},
        {"base_url": "https://relay.example/v1"},
        {"base_url": "https://user:pass@relay.example/v1", "signing_secret": "s"},
        {"relay": "bundled", "base_url": "https://other.example/v1"},
        {"base_url": "https://relay.example/wrong", "access_token": "key"},
    ],
)
def test_invalid_eac_accounts_are_rejected_before_saving(account):
    with pytest.raises(ValidationError):
        EacProvider.check_account(account)


def test_first_login_saves_in_the_data_directory(relay, tmp_path, monkeypatch):
    destination = config_file_path()
    other_workdir = tmp_path / "other"
    other_workdir.mkdir()
    settings = Settings(providers={p: ProviderConfig(id=p, enabled=False) for p in ("kilo", "opencode")})
    services = build_services(settings)
    try:
        assert services.config_path == destination
        assert destination == services.data_dir / "config.json"
        assert not destination.exists()
        monkeypatch.chdir(other_workdir)
        services.login._persist("test", {"access_token": "token"})
        assert json.loads(destination.read_text())["providers"]["test"]["accounts"][0]["access_token"] == "token"
        assert not (other_workdir / "config.json").exists()
    finally:
        services.close()


def test_eac_forms_offer_bundled_and_custom_relays_with_no_shared_secrets(relay, tmp_path):
    settings = Settings(providers={p: ProviderConfig(id=p, enabled=False) for p in ("kilo", "opencode")})
    services = build_services(settings)
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        forms = next(row["forms"] for row in client.get("/api/v1/provider-forms").json() if row["id"] == "eac")
        assert [form["method"] for form in forms] == ["bundled", "worker", "direct"]
        assert forms[0]["defaults"] == {"relay": "bundled"}
        assert forms[0]["fields"] == []
        assert "test-signing-secret" not in json.dumps(forms)


def test_unavailable_bundled_relay_does_not_break_provider_listing(monkeypatch):
    def unavailable():
        raise ValidationError("bundled relay unavailable")

    monkeypatch.setattr("hoshimi.core.eac_relay.open_bundled_relay", unavailable)
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("unavailable relay cannot be queried"))) as client:
        lane = EacProvider(client, [Account(default_eac_account())])
        assert lane.fetch_models() == []
