"""Authentication state transitions must survive cancellation and rejected tokens."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from hoshimi.core.config import ProviderConfig, Settings
from hoshimi.core.eac_user import read_eac_user
from hoshimi.core.login import LoginManager, provider_oauth
from hoshimi.core.login.common import LoginReporter
from hoshimi.core.providers.base import Account, AuthFailed, Provider, RefreshTokenRejected
from hoshimi.core.providers.eac import EacProvider


def test_eac_login_updates_the_existing_relay_and_acks_with_post(tmp_path, monkeypatch):
    monkeypatch.setattr("hoshimi.core.eac_user.default_data_dir", lambda: tmp_path)
    monkeypatch.setattr(LoginReporter, "wait", lambda self, seconds: self.check_cancelled())
    settings = Settings(providers={"eac": ProviderConfig(id="eac", accounts=[
        {"id": "relay", "base_url": "https://relay.example/v1", "signing_secret": "secret", "user_token": "old"}
    ])})
    acks = []
    def handle(request):
        if request.url.path == "/auth/github/start":
            return httpx.Response(302, headers={"location": "https://github.example/authorize"})
        if request.url.path == "/auth/poll":
            return httpx.Response(200, json={"status": "ok", "token": "fresh", "login": "user", "ackRequired": True})
        acks.append(request)
        assert read_eac_user()["token"] == "fresh"
        return httpx.Response(200, json={"ok": True})
    done = threading.Event()
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        manager = LoginManager(settings, client=client, on_persist=lambda _: done.set())
        session = manager.start("eac")
        assert done.wait(3), session.message
        with manager._persist_lock:
            pass  # the callback fires just before the terminal session state is set
        assert session.account_id == "relay"
        assert len(settings.providers["eac"].accounts) == 1
        account = settings.providers["eac"].accounts[0]
        assert account["signing_secret"] == "secret" and account["user_token"] == "fresh"
        assert json.loads((tmp_path / "config.json").read_text())["providers"]["eac"]["accounts"] == [account]
        assert len(acks) == 1 and acks[0].method == "POST"
        assert acks[0].headers["x-ofm-user"] == "fresh"
        manager.close()


def test_cancel_during_eac_poll_cannot_write_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("hoshimi.core.eac_user.default_data_dir", lambda: tmp_path)
    monkeypatch.setattr(LoginReporter, "wait", lambda self, seconds: self.check_cancelled())
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    settings = Settings(providers={"eac": ProviderConfig(id="eac", accounts=[
        {"id": "relay", "base_url": "https://relay.example/v1", "signing_secret": "secret"}
    ])})
    def handle(request):
        if request.url.path == "/auth/github/start":
            return httpx.Response(302)
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json={"status": "ok", "token": "late"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        manager = LoginManager(settings, client=client)
        run = manager._run
        def wrapped(*args):
            try:
                run(*args)
            finally:
                finished.set()
        monkeypatch.setattr(manager, "_run", wrapped)
        session = manager.start("eac")
        assert entered.wait(3)
        manager.cancel(session.id)
        release.set()
        assert finished.wait(3)
        assert read_eac_user() is None
        assert not (tmp_path / "config.json").exists()
        assert session.status == "failed"
        manager.close()


def test_eac_explicit_null_isolates_install_authorization(monkeypatch):
    monkeypatch.setattr("hoshimi.core.providers.eac.load_eac_user_token", lambda: "install-secret")
    account = Account({"base_url": "https://relay.example/v1", "signing_secret": "secret", "user_token": None})
    with httpx.Client() as client:
        provider = EacProvider(client, [account])
        assert "x-ofm-user" not in provider._headers(account, method="GET", url="https://relay.example/v1/models", body="")


@pytest.mark.parametrize("provider", ["codex", "meta", "github-copilot"])
def test_non_json_refresh_rejection_is_terminal(provider):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401, text="Unauthorized"))) as client, pytest.raises(RefreshTokenRejected):
        provider_oauth.refresh_provider_account(provider, client, Account({"refresh_token": "revoked"}))


def test_failed_login_save_leaves_live_credentials_unchanged(tmp_path, monkeypatch):
    settings = Settings(providers={"test": ProviderConfig(id="test", enabled=False, accounts=[{"id": "existing", "sub": "user", "access_token": "original"}])})
    def fail(*args, **kwargs):
        raise OSError("not writable")
    monkeypatch.setattr("hoshimi.core.config.save_settings", fail)
    with httpx.Client() as client:
        manager = LoginManager(settings, client=client)
        with pytest.raises(OSError):
            manager._persist("test", {"sub": "user", "access_token": "new"})
        assert settings.providers["test"].accounts[0]["access_token"] == "original"
        assert not settings.providers["test"].enabled
        manager.close()


class RejectedProvider(Provider):
    id = "rejected"

    def __init__(self):
        super().__init__(httpx.Client(), [Account({"refresh_token": "refresh"})])
        self.refreshes = 0

    def stream_raw(self, account, model, body):
        raise AuthFailed(401, "rejected")
        yield  # make the rejection lazy like an HTTP stream

    def refresh_account(self, account):
        self.refreshes += 1
        if self.refreshes > 2:
            pytest.fail("an unchanged auth rejection must not refresh indefinitely")
        account.set("access_token", f"token-{self.refreshes}")
        return True


def test_repeated_auth_rejection_only_refreshes_once_per_turn():
    provider = RejectedProvider()
    try:
        with pytest.raises(AuthFailed):
            list(provider.stream("m", {}))
        assert provider.refreshes == 1
    finally:
        provider.client.close()


def test_auth_failure_after_output_never_starts_a_second_account():
    provider = RejectedProvider()
    provider.accounts = [Account({"id": "first"}), Account({"id": "second"})]
    calls = []
    def stream(account, model, body):
        calls.append(account.id)
        yield {"type": "text_delta", "text": "partial"}
        raise AuthFailed(401, "interrupted")
    provider.stream_raw = stream
    try:
        result = provider.stream("m", {})
        assert next(result)["text"] == "partial"
        with pytest.raises(AuthFailed):
            next(result)
        assert calls == ["first"]
        assert provider.refreshes == 0
    finally:
        provider.client.close()


def test_concurrent_auth_refresh_does_not_reuse_a_rotating_token():
    provider = RejectedProvider()
    account = provider.accounts[0]
    entered, release = threading.Event(), threading.Event()
    def refresh(account):
        provider.refreshes += 1
        entered.set()
        assert release.wait(3)
        account.set("access_token", "fresh")
        return True
    provider.refresh_account = refresh
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(provider.renew_account, account, expected_revision=0)
            assert entered.wait(3)
            second = pool.submit(provider.renew_account, account, expected_revision=0)
            release.set()
            assert first.result(3) and second.result(3)
        assert provider.refreshes == 1
    finally:
        provider.client.close()
