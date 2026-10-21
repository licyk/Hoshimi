"""The stats surface: range reports, the call ring, a reset, and the live stream."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.api.heartbeat import PING
from hoshimi.api.routers.stats import live_frames
from hoshimi.core.live import LiveHub
from tests.conftest import make_services


def _chat(client: Any, model: str = "fake-1", **extra: Any) -> Any:
    return client.post("/v1/chat/completions", json={"model": model, "stream": False, "messages": [{"role": "user", "content": "hi"}], **extra})


def _frames(raw: bytes) -> list[dict[str, Any]]:
    return [json.loads(block[len("data: ") :]) for block in raw.decode().split("\n\n") if block.startswith("data: ")]


def test_report_ranges_and_validation(client: Any) -> None:
    assert _chat(client).status_code == 200
    for name, points, granularity in (("24h", 24, "hour"), ("7d", 7, "day"), ("30d", 30, "day"), ("90d", 90, "day")):
        report = client.get("/api/v1/stats", params={"range": name}).json()
        assert (report["range"], report["granularity"], len(report["series"])) == (name, granularity, points)
        assert report["totals"]["calls"] == 1
        assert report["series"][-1]["calls"] == 1
        assert report["latency"]["ttft_avg_ms"] is not None
        model = report["by_model"][0]
        assert (model["key"], model["label"], model["provider"], model["account"]) == ("fake-1", "Fake 1", "", "")
        account = report["by_account"][0]
        assert (account["key"], account["label"], account["provider"], account["account"]) == ("fake/fake-0", "fake-0", "fake", "fake-0")
        assert account["last_at"] is not None
        assert report["by_origin"][0]["key"] == "chat"
    assert client.get("/api/v1/stats", params={"range": "1y"}).status_code == 422
    assert client.get("/api/v1/stats").json()["range"] == "7d"


def test_calls_page_with_cursor_and_filters(client: Any) -> None:
    for _ in range(3):
        assert _chat(client).status_code == 200
    assert _chat(client, model="nope-1").status_code in (200, 404)
    first = client.get("/api/v1/stats/calls", params={"limit": 2}).json()
    assert [row["seq"] for row in first["calls"]] == [4, 3]
    assert first["next"] == 3
    rest = client.get("/api/v1/stats/calls", params={"limit": 2, "before": first["next"]}).json()
    assert [row["seq"] for row in rest["calls"]] == [2, 1] and rest["next"] is None
    # The display name comes from the catalogue, the id stays the id.
    assert [row["name"] for row in rest["calls"]] == ["Fake 1", "Fake 1"]

    assert [row["seq"] for row in client.get("/api/v1/stats/calls", params={"model": "fake-1", "ok": True}).json()["calls"]] == [3, 2, 1]
    assert client.get("/api/v1/stats/calls", params={"account": "fake/fake-0"}).json()["calls"][0]["account"] == "fake-0"
    assert client.get("/api/v1/stats/calls", params={"origin": "messages"}).json()["calls"] == []
    assert client.get("/api/v1/stats/calls", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/stats/calls", params={"limit": 201}).status_code == 422


def test_reset_clears_everything(client: Any) -> None:
    assert _chat(client).status_code == 200
    cleared = client.delete("/api/v1/stats")
    assert cleared.status_code == 200 and cleared.json()["cleared_at"]
    report = client.get("/api/v1/stats").json()
    assert (report["requests"], report["lifetime"]["calls"], report["by_model"]) == (0, 0, [])
    assert client.get("/api/v1/stats/calls").json() == {"calls": [], "next": None}


def test_playground_calls_have_their_own_origin(client: Any) -> None:
    played = client.post("/api/v1/playground/chat/completions", json={"model": "fake-1", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
    assert played.status_code == 200
    assert _chat(client).status_code == 200
    origins = [row["origin"] for row in client.get("/api/v1/stats/calls").json()["calls"]]
    assert origins == ["chat", "playground"]
    stream_flags = [row["stream"] for row in client.get("/api/v1/stats/calls").json()["calls"]]
    assert stream_flags == [False, True]


def test_live_stream_sends_a_snapshot_then_events_through_the_route() -> None:
    services = make_services()
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        assert _chat(client).status_code == 200  # one call already in the ring
        received: dict[str, Any] = {}

        def watch() -> None:
            received["response"] = client.get("/api/v1/stats/live")

        watcher = threading.Thread(target=watch)
        watcher.start()
        deadline = time.monotonic() + 3
        while services.stats.live.subscribers == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert services.stats.live.subscribers == 1
        assert _chat(client).status_code == 200
        # Ends every open live stream, as a shutdown does; queued after the
        # call's events, so the response holds all of them.
        services.stats.live.close()
        watcher.join(3)
        assert not watcher.is_alive()

    response = received["response"]
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = _frames(response.content)
    kinds = [event["type"] for event in events]
    assert kinds[0] == "snapshot" and kinds[1] == "started" and kinds[-1] == "finished"
    snapshot = events[0]
    assert snapshot["active"] == [] and snapshot["today"]["calls"] == 1
    assert [row["seq"] for row in snapshot["recent"]] == [1]
    started, finished = events[1], events[-1]
    assert started["call"]["model"] == "fake-1" and started["call"]["phase"] == "waiting"
    assert started["call"]["name"] == "Fake 1"
    assert finished["call_id"] == started["call"]["id"] == finished["record"]["id"]
    assert (finished["record"]["seq"], finished["record"]["account"]) == (2, "fake-0")
    assert finished["today"]["calls"] == 2
    progress = [event["call"] for event in events if event["type"] == "progress"]
    # Routed, then answering: the account is known while the call runs.
    assert progress and progress[-1]["account"] == "fake-0" and progress[-1]["phase"] == "answering"


def test_live_stream_heartbeats_and_ends_on_disconnect() -> None:
    services = make_services()

    class Request:
        def __init__(self) -> None:
            self.gone = asyncio.Event()

        async def receive(self) -> dict[str, Any]:
            await self.gone.wait()
            return {"type": "http.disconnect"}

    async def run() -> None:
        request = Request()
        stream = live_frames(services, request, heartbeat=0.01)  # type: ignore[arg-type]
        assert json.loads((await anext(stream))[len("data: ") :])["type"] == "snapshot"
        assert await anext(stream) == PING
        assert services.stats.live.subscribers == 1
        request.gone.set()
        with pytest.raises(StopAsyncIteration):
            while True:
                frame = await asyncio.wait_for(anext(stream), 1)
                assert frame == PING
        assert services.stats.live.subscribers == 0

    asyncio.run(run())


def test_a_slow_live_reader_is_cut_loose() -> None:
    services = make_services()
    services.stats.live = LiveHub(queue_limit=2)

    async def run() -> None:
        stream = live_frames(services, None, heartbeat=5)
        await anext(stream)  # the snapshot; the subscriber now exists

        def burst() -> None:
            for _ in range(5):
                services.stats.turn("fake-1").finish()

        await asyncio.to_thread(burst)
        await asyncio.sleep(0.05)
        frames = [frame async for frame in stream]
        # What fit in the queue may arrive; then the stream ends instead of
        # replaying a backlog — the client reconnects for a fresh snapshot.
        assert len(frames) <= 2
        assert services.stats.live.subscribers == 0

    asyncio.run(asyncio.wait_for(run(), 3))


def test_live_event_schema_is_published() -> None:
    schema = create_app(make_services(), serve_ui=False).openapi()
    response = schema["paths"]["/api/v1/stats/live"]["get"]["responses"]["200"]["content"]["text/event-stream"]["schema"]
    assert response["$ref"] == "#/components/schemas/LiveEvent"
    event = schema["components"]["schemas"]["LiveEvent"]
    assert {ref["$ref"].rsplit("/", 1)[-1] for ref in event["oneOf"]} == {"LiveSnapshot", "LiveStarted", "LiveProgress", "LiveFinished"}
    assert schema["paths"]["/api/v1/stats"]["delete"]["operationId"] == "reset_stats"
    assert schema["paths"]["/api/v1/stats/calls"]["get"]["operationId"] == "list_stats_calls"


def test_live_catalogue_lookup_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    services = make_services()
    services.stats.record(model="fake-1", ok=True)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        released = threading.Event()

        def slow_names(_services):
            loop.call_soon_threadsafe(released.set)
            if not released.wait(1):
                raise RuntimeError("The catalogue lookup blocked the event loop")
            return {"fake-1": "Cached display name"}

        monkeypatch.setattr("hoshimi.api.routers.stats.model_names", slow_names)
        stream = live_frames(services)
        try:
            snapshot = _frames(await anext(stream))[0]
            assert snapshot["recent"][0]["name"] == "Cached display name"
        finally:
            await stream.aclose()
        assert services.stats.live.subscribers == 0

    asyncio.run(run())
