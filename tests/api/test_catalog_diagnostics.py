"""Catalogue errors must reach both UI endpoints without exposing upstream secrets."""

import asyncio
import json

import httpx
import pytest

from hoshimi.api.routers.catalog import catalog_events
from hoshimi.api.routers.channels import build_report
from hoshimi.core.net.client import NetworkClient
from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account


def install_provider(services, handler, accounts=None):
    client = NetworkClient(provider="openai", transport=httpx.MockTransport(handler))
    provider = PROVIDER_CLASSES["openai"](client, accounts or [Account({"id": "one", "api_key": "secret-key", "base_url": "https://relay.example/v1"})])
    services.registry.providers["openai"] = provider
    return provider


def read_catalog(services):
    async def read():
        return [json.loads(frame.removeprefix("data: ")) async for frame in catalog_events(services) if frame.startswith("data:")]

    events = asyncio.run(read())
    return next(event["provider"] for event in events if event["type"] == "provider" and event["provider"]["id"] == "openai")


def test_slug_catalog_is_ready_and_invalid_refresh_keeps_the_complete_catalog(services):
    rows = [{"slug": "glm-5.3", "context_window": 1048576}, {"slug": "glm-5.3-flash"}, {"slug": "glm-5-turbo"}]
    provider = install_provider(services, lambda request: httpx.Response(200, json={"models": rows}))
    try:
        initial = read_catalog(services)
        assert initial["status"] == "ready"
        assert initial["error"] is None and initial["diagnostic"] is None
        assert list(provider.listing_rows) == [row["slug"] for row in rows]
        assert provider.specs()["glm-5.3"].context_window == 1048576
        # Normalized IDs are also the duplicate-check keys. A broken refresh cannot
        # publish its prefix or erase the previous complete per-account catalogue.
        rows.append({"id": "glm-5.3"})
        provider._models_expires_at = 0
        failed = read_catalog(services)
        assert failed["status"] == "failed" and failed["error"] == "invalid_response"
        assert failed["data"] == initial["data"]
    finally:
        services.close()


@pytest.mark.parametrize("status,body,kind", [
    (200, '{"error":"secret-body"}', "invalid_response"),
    (200, 'not JSON secret-body', "invalid_response"),
    (401, '{"error":"secret-body"}', "authentication"),
    (403, '{"error":"secret-body"}', "permission_denied"),
    (404, '{"error":"secret-body"}', "catalog_endpoint_missing"),
    (405, '{"error":"secret-body"}', "catalog_endpoint_missing"),
    (429, '{"error":"secret-body"}', "rate_limited"),
    (503, '{"error":"secret-body"}', "upstream_error"),
])
def test_catalog_and_channel_report_safe_http_failures(services, status, body, kind, caplog):
    install_provider(services, lambda request: httpx.Response(status, text=body))
    try:
        result = read_catalog(services)
        assert result["status"] == "failed" and result["error"] == kind
        diagnostic = result["diagnostic"]
        assert (diagnostic["kind"], diagnostic["status"], diagnostic["host"]) == (kind, status, "relay.example")
        channel = next(row for row in build_report(services).channels if row.id == "openai")
        assert channel.diagnostics[0].model_dump() == diagnostic
        public = json.dumps(result) + channel.model_dump_json() + caplog.text
        assert "secret-body" not in public and "secret-key" not in public
    finally:
        services.close()


@pytest.mark.parametrize("error,kind", [(httpx.ConnectTimeout, "timeout"), (httpx.ProxyError, "proxy_error"), (httpx.ConnectError, "connection_error")])
def test_catalog_retains_transport_failure_kind(services, error, kind):
    def fail(request):
        raise error("secret from transport", request=request)

    install_provider(services, fail)
    try:
        result = read_catalog(services)
        assert result["error"] == kind and result["diagnostic"]["status"] is None
        assert "secret" not in json.dumps(result)
    finally:
        services.close()


def test_later_account_success_keeps_failure_until_complete_recovery(services):
    failing = False

    def respond(request):
        if failing and request.headers["authorization"] == "Bearer bad":
            return httpx.Response(404, json={"error": "secret upstream text"})
        return httpx.Response(200, json={"data": [{"id": "working-model"}]})

    accounts = [Account({"id": key, "api_key": key, "base_url": f"https://{key}.example/v1"}) for key in ("bad", "good")]
    provider = install_provider(services, respond, accounts)
    try:
        initial = read_catalog(services)
        assert initial["status"] == "ready"
        failing = True
        provider._models_expires_at = 0
        failed = read_catalog(services)
        assert failed["data"] == initial["data"]
        assert failed["error"] == "catalog_endpoint_missing"
        assert failed["diagnostic"]["host"] == "bad.example"
        assert provider.client.diagnostics()[0]["kind"] == "ok"
        channel = next(row for row in build_report(services).channels if row.id == "openai")
        assert channel.diagnostics[0].status == 404
        assert channel.diagnostics[0].host == "bad.example"
        # The cached retry interval retains the same failure, without another request.
        assert read_catalog(services)["diagnostic"] == failed["diagnostic"]
        failing = False
        provider.catalog_retry_at = provider._models_expires_at = 0
        recovered = read_catalog(services)
        assert recovered["error"] is None and recovered["diagnostic"] is None
        channel = next(row for row in build_report(services).channels if row.id == "openai")
        assert all(row.kind == "ok" for row in channel.diagnostics)
    finally:
        services.close()


def test_exception_code_and_url_credentials_are_not_exposed(services, monkeypatch, caplog):
    provider = install_provider(services, lambda request: httpx.Response(200))

    class BadAdapterError(Exception):
        code = "secret-exception-code"

    def fail():
        raise BadAdapterError("https://user:secret-pass@relay.example/v1?token=secret-query")

    monkeypatch.setattr(provider, "fetch_models", fail)
    try:
        result = read_catalog(services)
        assert result["error"] == "catalog_failed"
        assert "secret" not in json.dumps(result) + caplog.text
        assert result["diagnostic"]["status"] is None
    finally:
        services.close()


def test_non_http_adapter_failure_does_not_reuse_previous_http_status(services, monkeypatch):
    provider = install_provider(services, lambda request: httpx.Response(404, json={"error": "missing"}))
    try:
        assert read_catalog(services)["diagnostic"]["status"] == 404

        def fail():
            raise ValueError("secret invalid capabilities")

        monkeypatch.setattr(provider, "specs", fail)
        provider.catalog_retry_at = 0
        result = read_catalog(services)
        assert result["error"] == "invalid_response"
        assert result["diagnostic"]["status"] is None
        assert result["diagnostic"]["host"] == ""
    finally:
        services.close()
