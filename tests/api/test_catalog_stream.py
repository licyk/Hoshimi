"""A slow or failed upstream must not hold healthy providers behind it."""

import asyncio
import json
import threading

from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.api.routers import catalog
from tests.conftest import FakeProvider


def decode(frame):
    return json.loads(frame.removeprefix("data: "))


def test_provider_metadata_does_not_query_models(client, services, monkeypatch):
    def forbidden():
        raise AssertionError("metadata must not query the upstream")

    monkeypatch.setattr(services.registry.providers["fake"], "models", forbidden)
    assert client.get("/api/v1/providers").status_code == 200


def test_fast_provider_arrives_before_blocked_provider(services, monkeypatch):
    slow, fast = FakeProvider(), services.registry.providers["fake"]
    gate, started = threading.Event(), threading.Event()

    def blocked():
        started.set()
        assert gate.wait(3)
        return fast.specs()

    monkeypatch.setattr(slow, "specs", blocked)
    services.registry.providers = {"slow": slow, "fast": fast}

    async def run():
        stream = catalog.catalog_events(services)
        snapshot = decode(await anext(stream))
        assert [p["id"] for p in snapshot["providers"]] == ["slow", "fast"]
        update = decode(await asyncio.wait_for(anext(stream), timeout=1))
        assert update["provider"]["id"] == "fast"
        assert update["provider"]["data"][0]["id"] == "fake-1"
        assert started.is_set() and not gate.is_set()
        gate.set()
        events = [decode(frame) async for frame in stream if frame.startswith("data:")]
        assert events[-1]["type"] == "done"

    try:
        asyncio.run(run())
    finally:
        gate.set()


def test_timeout_retries_and_disconnect_share_work(services, monkeypatch):
    slow = services.registry.providers["fake"]
    gate, started = threading.Event(), threading.Event()
    calls = []

    def blocked():
        calls.append(1)
        started.set()
        assert gate.wait(3)
        return {}

    monkeypatch.setattr(slow, "specs", blocked)
    monkeypatch.setattr(catalog, "CATALOG_TIMEOUT_SECONDS", 0.03)

    async def run():
        stream = catalog.catalog_events(services)
        await anext(stream)
        # Start work, then close this subscriber without cancelling the worker.
        first = decode(await anext(stream))
        assert first["provider"]["status"] == "timeout"
        await stream.aclose()
        assert started.is_set()
        second = [decode(frame) async for frame in catalog.catalog_events(services) if frame.startswith("data:")]
        assert second[-2]["provider"]["status"] == "timeout"
        assert second[-1]["type"] == "done"
        assert len(calls) == 1

    try:
        asyncio.run(run())
    finally:
        gate.set()


def test_failed_refresh_keeps_cache_and_rebuilt_account_pool_drops_it(services, monkeypatch):
    provider = services.registry.providers["fake"]

    async def read():
        return [decode(frame) async for frame in catalog.catalog_events(services) if frame.startswith("data:")]

    before = asyncio.run(read())
    assert before[-2]["provider"]["status"] == "ready"

    def fail():
        raise RuntimeError("upstream secret must not reach the event")

    monkeypatch.setattr(provider, "specs", fail)
    failed = asyncio.run(read())
    assert failed[-2]["provider"]["status"] == "failed"
    assert failed[-2]["provider"]["data"] == before[-2]["provider"]["data"]
    assert "upstream secret" not in json.dumps(failed)
    replacement = FakeProvider()
    monkeypatch.setattr(replacement, "specs", fail)
    services.registry.providers["fake"] = replacement
    rebuilt = asyncio.run(read())
    assert rebuilt[0]["providers"][0]["data"] == []
    assert rebuilt[-2]["provider"]["data"] == []


def test_stream_uses_management_auth_and_is_not_compressed(services):
    services.settings.access_token = "tool"
    services.settings.model_api_key = "model"
    with TestClient(create_app(services, api_prefix="/gateway", serve_ui=False), base_url="http://localhost") as client:
        path = "/gateway/api/v1/catalog/stream"
        assert client.get(path, headers={"Authorization": "Bearer model"}).status_code == 401
        response = client.get(path, headers={"Authorization": "Bearer tool", "Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in response.headers
        assert response.headers["x-accel-buffering"] == "no"
        assert '"type":"done"' in response.text


def test_malformed_capabilities_do_not_break_other_provider_chunks(services, monkeypatch):
    malformed = FakeProvider()
    monkeypatch.setattr(malformed, "specs", lambda: {"invalid": None})
    services.registry.providers["malformed"] = malformed

    async def read():
        return [decode(frame) async for frame in catalog.catalog_events(services) if frame.startswith("data:")]

    events = asyncio.run(read())
    updates = {event["provider"]["id"]: event["provider"] for event in events if event["type"] == "provider"}
    assert updates["malformed"]["status"] == "failed"
    assert updates["fake"]["status"] == "ready"
    assert updates["fake"]["data"]
    assert events[-1]["type"] == "done"
