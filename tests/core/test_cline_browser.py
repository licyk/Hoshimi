"""Browser authorization stays separate from the WorkOS SDK profile."""

import json

import httpx
import pytest

from hoshimi.core.login import cline_browser
from hoshimi.core.login.cline import start_cline_login
from hoshimi.core.login.common import LoginError
from hoshimi.core.net.client import NetworkClient
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.cline import ClineProvider


class Callback:
    closed = False

    def __init__(self, path, *, port):
        assert path == "/auth" and port == 48801

    def redirect_uri(self):
        return "http://127.0.0.1:48801/auth"

    def wait(self, timeout):
        return {"code": "single-use-code", "provider": "google"}

    def close(self):
        Callback.closed = True


def test_browser_login_and_refresh_keep_extension_identity(monkeypatch):
    monkeypatch.setattr(cline_browser, "CallbackServer", Callback)
    requests = []
    urls = []

    def handle(request):
        requests.append(request)
        assert request.headers["x-client-type"] == "VSCode Extension"
        assert request.headers["user-agent"].startswith("Cline/")
        if request.url.path.endswith("/authorize"):
            return httpx.Response(302, headers={"location": "https://identity.test/authorize"})
        if request.url.path.endswith("/token"):
            body = json.loads(request.content)
            assert body["code"] == "single-use-code" and body["provider"] == "google"
            return httpx.Response(200, json={"success": True, "data": {"accessToken": "first", "refreshToken": "rotate-1"}})
        assert request.url.path.endswith("/refresh")
        assert json.loads(request.content)["refreshToken"] == "rotate-1"
        return httpx.Response(200, json={"success": True, "data": {"accessToken": "second", "refreshToken": "rotate-2"}})

    with NetworkClient(provider="cline", login=True, transport=httpx.MockTransport(handle)) as client:
        credential = cline_browser.start_cline_browser_login(client, urls.append)
        assert credential["access_token"] == "workos:first"
        account = Account(credential)
        provider = ClineProvider(client, [account])
        assert provider.renew_account(account, expected_revision=0)
        assert account.get("refresh_token") == "rotate-2"
        assert provider.build_headers(account, "model")["Authorization"] == "Bearer workos:second"
    assert urls == ["https://identity.test/authorize"] and Callback.closed
    assert len(requests) == 3


def test_browser_code_is_never_replayed_after_an_exchange_failure(monkeypatch):
    monkeypatch.setattr(cline_browser, "CallbackServer", Callback)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://identity.test/"}) if request.method == "GET" else httpx.Response(503, json={"error": "busy"})

    with NetworkClient(provider="cline", login=True, transport=httpx.MockTransport(handle)) as client, pytest.raises(LoginError):
        cline_browser.start_cline_browser_login(client, lambda _: None)
    assert len(calls) == 2 and Callback.closed


def test_device_poll_html_is_not_reported_as_waiting_for_authorization(monkeypatch):
    monkeypatch.setattr("hoshimi.core.login.cline.poll_pause", lambda _: None)

    def handle(request):
        if request.url.path.endswith("/device"):
            return httpx.Response(200, json={"device_code": "d", "user_code": "u", "verification_uri": "https://identity.test", "interval": 1})
        return httpx.Response(403, text="<html>blocked</html>")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client, pytest.raises(ProviderError) as exc:
        start_cline_login(client, lambda _: None)
    assert exc.value.code == "edge_rejected"
