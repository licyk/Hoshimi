"""Network refusals must preserve credentials, streams and last-good catalogues."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from hoshimi.core.config import Settings, settings_from_dict, settings_to_dict, validate_settings
from hoshimi.core.discovery import CatalogDiscovery
from hoshimi.core.net.client import NetworkClient, operation
from hoshimi.core.net.policy import NetworkPolicy, parse_policy, proxy_route
from hoshimi.core.net.responses import refresh_token_rejected
from hoshimi.core.providers.base import Account, AuthFailed, ProviderError, RefreshTokenRejected
from hoshimi.core.providers.cline import ClineProvider
from hoshimi.core.providers.openai_compat import raise_for_status


@pytest.mark.parametrize("status,headers,text,kind", [
    (403, {}, "<!doctype html><html>blocked</html>", "edge_rejected"),
    (401, {"content-type": "text/html"}, "expired", "edge_rejected"),
    (200, {"content-type": "text/html"}, "<html>browser required</html>", "invalid_response"),
    (200, {"cf-mitigated": "challenge"}, "challenge", "challenge"),
    (403, {}, '{"error":{"type":"FreeTierError"}}', "client_restricted"),
    (403, {}, '{"error":{"type":"RegionError"}}', "region_restricted"),
    (403, {}, '{"error":{"message":"signature rejected"}}', "signature_rejected"),
])
def test_interceptions_do_not_refresh_or_disable_accounts(status, headers, text, kind):
    account = Account({"id": "one", "access_token": "good", "refresh_token": "still-good"})
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers=headers, text=text)

    with NetworkClient(provider="cline", transport=httpx.MockTransport(handle)) as client:
        provider = ClineProvider(client, [account])
        with pytest.raises(ProviderError) as exc:
            list(provider.stream("model", {"messages": []}))
        assert exc.value.code == kind
        assert not isinstance(exc.value, AuthFailed)
        assert not account.needs_login
        assert account.get("refresh_token") == "still-good"
        assert len(calls) == 1
        assert client.diagnostics()[0]["operation"] == "inference"


@pytest.mark.parametrize("status,payload,rejected", [
    (403, {"error": "forbidden"}, False),
    (400, {"error": "invalid_client"}, False),
    (400, {"error": "invalid_grant"}, True),
    (403, {"error": {"code": "invalid_refresh_token"}}, True),
    (401, {}, True),
])
def test_refresh_requires_a_token_verdict(status, payload, rejected):
    assert refresh_token_rejected(httpx.Response(status, json=payload)) is rejected


def test_plain_httpx_refresh_also_preserves_html_rejections():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403, text="<html>blocked</html>"))) as client:
        account = Account({"refresh_token": "good"})
        provider = ClineProvider(client, [account])
        assert not provider.renew_account(account, expected_revision=0)
        assert not account.needs_login


def test_explicit_invalid_grant_is_terminal():
    with NetworkClient(provider="cline", transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"error": "invalid_grant"}))) as client:
        account = Account({"refresh_token": "revoked"})
        provider = ClineProvider(client, [account])
        with pytest.raises(RefreshTokenRejected):
            provider.renew_account(account, expected_revision=0)
        assert account.needs_login


def test_permission_denial_is_not_authentication():
    with pytest.raises(ProviderError) as exc:
        raise_for_status(httpx.Response(403, json={"error": "forbidden"}), provider="test")
    assert not isinstance(exc.value, AuthFailed)
    assert exc.value.code == "permission_denied"


def test_proxy_precedence_and_loopback(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://environment:8080")
    monkeypatch.setenv("no_proxy", ".internal,example.com:8443")
    assert proxy_route(httpx.URL("https://api.example.com"), NetworkPolicy()) == ("http://environment:8080", "environment_proxy")
    assert proxy_route(httpx.URL("https://api.internal"), NetworkPolicy()) == (None, "direct")
    assert proxy_route(httpx.URL("https://example.com:8443"), NetworkPolicy()) == (None, "direct")
    explicit = NetworkPolicy("proxy", "http://configured:8080")
    assert proxy_route(httpx.URL("https://api.internal"), explicit) == ("http://configured:8080", "configured_proxy")
    for url in ("http://localhost", "http://127.0.0.2", "http://[::1]"):
        assert proxy_route(httpx.URL(url), explicit) == (None, "direct")
    assert proxy_route(httpx.URL("https://example.com"), NetworkPolicy("direct")) == (None, "direct")
    assert parse_policy({"mode": "inherit"}, inherited=explicit) == explicit


@pytest.mark.parametrize("raw", [{"mode": "proxy"}, {"mode": "typo"}, {"mode": "proxy", "proxy": "file:///tmp/x"}, {"proxy": "http://host/path"}])
def test_invalid_proxy_configuration_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_policy(raw)


def test_network_configuration_roundtrip():
    raw = {"network": {"mode": "proxy", "proxy": "http://user:secret@host:8000"},
           "providers": {"cline": {"options": {"network": {"mode": "direct"}}}}}
    settings = settings_from_dict(raw)
    assert validate_settings(settings) is None
    assert settings_from_dict(settings_to_dict(settings)) == settings
    assert Settings().network.mode == "auto"


def test_provider_cookies_are_not_shared_but_login_session_keeps_its_cookie():
    cookies = []

    def handle(request):
        cookies.append(request.headers.get("cookie"))
        return httpx.Response(200, json={}, headers={"set-cookie": "session=private; Path=/"})

    with NetworkClient(transport=httpx.MockTransport(handle)) as client:
        client.get("https://upstream.test/one")
        client.get("https://upstream.test/two")
    assert cookies == [None, None]
    cookies.clear()
    with NetworkClient(login=True, transport=httpx.MockTransport(handle)) as client:
        client.get("https://upstream.test/one")
        client.get("https://upstream.test/two")
    assert cookies == [None, "session=private"]
    with NetworkClient(login=True, transport=httpx.MockTransport(handle)) as client:
        client.get("https://upstream.test/three")
    assert cookies[-1] is None


def test_diagnostics_never_include_secrets():
    with NetworkClient(provider="test", transport=httpx.MockTransport(lambda _: httpx.Response(403, text="<html>secret-token</html>"))) as client:
        with operation("catalog"), pytest.raises(ProviderError):
            client.get("https://user:password@upstream.test/token?code=secret", headers={"Authorization": "Bearer private"})
        text = json.dumps(client.diagnostics())
    assert all(secret not in text for secret in ("password", "secret", "private", "token?"))
    assert "edge_rejected" in text
    assert "catalog" in text


def test_catalogue_survives_restart_then_failure_and_is_account_scoped():
    calls = []
    blocked = False

    def handle(request):
        calls.append(request)
        assert "authorization" not in request.headers  # Cline catalogue is public.
        if blocked:
            return httpx.Response(403, text="<html>blocked</html>")
        return httpx.Response(200, json={"data": {"free": [{"id": "learned-model"}]}})

    with NetworkClient(provider="cline", transport=httpx.MockTransport(handle)) as client:
        first = ClineProvider(client, [Account({"id": "account", "access_token": "secret"})])
        discovery = CatalogDiscovery()
        try:
            result = discovery.submit(first).result()
            assert list(result.specs) == ["learned-model"]
            assert result.last_success is not None
        finally:
            discovery.close()
        blocked = True
        second = ClineProvider(client, [Account({"id": "account", "access_token": "secret"})])
        discovery = CatalogDiscovery()
        try:
            assert list(discovery.cached(second).specs) == ["learned-model"]
            assert discovery.cached(second).source == "disk"
            failure = discovery.submit(second).result()
            assert failure.failed and failure.error == "edge_rejected"
            assert list(failure.specs) == ["learned-model"]
            assert failure.retry_at > time.time()
            discovery.submit(second).result()
            second.models()
            assert len(calls) == 2  # Both callers respect the failure cooldown.
            changed = ClineProvider(client, [Account({"id": "different", "access_token": "other"})])
            assert not discovery.cached(changed).specs
        finally:
            discovery.close()


def test_authoritative_empty_catalogue_eventually_removes_old_models():
    payload = {"data": {"free": [{"id": "old-model"}]}}
    with NetworkClient(provider="cline", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        provider = ClineProvider(client, [Account({"access_token": "t"})])
        assert provider.models() == ["old-model"]
        payload["data"]["free"] = []
        provider._models_expires_at = 0
        assert provider.models() == ["old-model"]  # Existing shrink guard holds once.
        provider._models_expires_at = 0
        assert provider.models() == []


def test_proxy_change_does_not_close_an_active_stream(monkeypatch):
    from hoshimi.core.net import client as networking

    transports = []

    class Transport(httpx.BaseTransport):
        def __init__(self, **kwargs):
            self.proxy = kwargs["proxy"]
            self.closed = False
            transports.append(self)

        def handle_request(self, request):
            return httpx.Response(200, stream=httpx.ByteStream(b"data: hello\n\n"))

        def close(self):
            self.closed = True

    monkeypatch.setattr(networking.httpx, "HTTPTransport", Transport)
    with NetworkClient(network=NetworkPolicy("proxy", "http://first:8080")) as client:
        with client.stream("POST", "https://upstream.test/chat") as stream:
            client.network = NetworkPolicy("proxy", "http://second:8080")
            client.get("https://upstream.test/models")
            assert [t.proxy for t in transports] == ["http://first:8080", "http://second:8080"]
            assert not transports[0].closed
            assert stream.read() == b"data: hello\n\n"
        assert transports[0].closed
    assert transports[1].closed


def test_malformed_catalogue_does_not_remove_previous_models():
    payload = {"data": {"free": [{"id": "old-model"}]}}
    with NetworkClient(provider="cline", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        provider = ClineProvider(client, [Account({"access_token": "t"})])
        assert provider.models() == ["old-model"]
        payload.clear()
        payload["error"] = "maintenance"
        provider._models_expires_at = 0
        assert provider.models() == ["old-model"]
        assert provider.catalog_failed and provider.catalog_error == "invalid_response"


def test_client_configuration_error_does_not_invalidate_refresh_token():
    response = httpx.Response(401, json={"error": "invalid_client"})
    assert not refresh_token_rejected(response)


def test_failed_proxy_does_not_replay_a_post_or_try_direct(monkeypatch):
    from hoshimi.core.net import client as networking

    attempts = []

    class BrokenProxy(httpx.BaseTransport):
        def __init__(self, **kwargs):
            assert kwargs["proxy"] == "http://proxy.test:8080"

        def handle_request(self, request):
            attempts.append(request)
            raise httpx.ProxyError("private proxy details", request=request)

    monkeypatch.setattr(networking.httpx, "HTTPTransport", BrokenProxy)
    with NetworkClient(provider="cline", network=NetworkPolicy("proxy", "http://proxy.test:8080")) as client:
        with pytest.raises(httpx.RequestError, match="proxy_error") as exc:
            client.post("https://upstream.test/auth/token", json={"code": "single-use"})
        assert "private" not in str(exc.value)
        assert client.diagnostics()[0]["kind"] == "proxy_error"
    assert len(attempts) == 1


def test_google_auth_transport_shares_provider_client_and_diagnostics():
    from hoshimi.core.net.sdk import google_request

    def handle(request):
        assert request.method == "POST" and request.content == b"grant_type=refresh_token"
        return httpx.Response(200, json={"access_token": "fresh"})

    with NetworkClient(provider="google-vertex", transport=httpx.MockTransport(handle)) as client:
        response = google_request(client)("https://oauth2.googleapis.com/token", method="POST", body=b"grant_type=refresh_token")
        assert response.status == 200 and json.loads(response.data)["access_token"] == "fresh"
        assert client.diagnostics()[0]["operation"] == "token_refresh"


def test_bedrock_sdk_uses_configured_route_without_environment_fallback(monkeypatch):
    pytest.importorskip("botocore")
    from hoshimi.core.net.sdk import bedrock_config

    monkeypatch.setenv("HTTPS_PROXY", "http://environment.test:8080")
    with NetworkClient(network=NetworkPolicy("direct")) as client:
        assert bedrock_config(client, "bedrock-runtime", "us-east-1").proxies == {}
        client.network = NetworkPolicy("proxy", "http://explicit.test:8080")
        config = bedrock_config(client, "bedrock", "us-east-1")
        assert config.proxies["https"] == "http://explicit.test:8080"
        assert config.retries["total_max_attempts"] == 1
