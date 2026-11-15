"""Full call payloads stay separate from summaries and follow inference/retention boundaries."""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.api.call_trace import CallTraceMiddleware
from hoshimi.api.inference import tracked_chunks
from hoshimi.core.call_trace import current_trace
from hoshimi.core.db import Database
from hoshimi.core.errors import ModelNotFoundError
from hoshimi.core.net.client import NetworkClient, operation
from hoshimi.core.stats import StatsStore
from tests.conftest import make_services


def detail(client: Any) -> dict[str, Any]:
    row = client.get("/api/v1/stats/calls").json()["calls"][0]
    assert "trace" not in row
    response = client.get(f'/api/v1/stats/calls/{row["seq"]}')
    assert response.status_code == 200
    return response.json()["trace"]


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/responses", "/v1/messages", "/api/v1/playground/chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
def test_client_bodies_match_actual_protocol_reply(client: Any, path: str, stream: bool) -> None:
    body: dict[str, Any] = {"model": "fake-1", "stream": stream}
    if path == "/v1/responses":
        body["input"] = "hello 世界"
    else:
        body["messages"] = [{"role": "user", "content": "hello 世界"}]
        body["max_tokens"] = 20
    response = client.post(path, json=body, headers={"Authorization": "Bearer private-client-key"})
    assert response.status_code == 200
    trace = detail(client)
    assert json.loads(trace["client_request"]["body"]) == body
    assert trace["client_request"]["headers"]["authorization"] == "***"
    assert trace["client_response"]["body"] == response.text
    assert trace["client_response"]["status"] == 200
    assert trace["client_response"]["complete"] is True
    assert trace["response"]["complete"] is True
    assert trace["response"]["chunks"]
    assert "private-client-key" not in json.dumps(trace)


def test_failure_old_record_missing_record_and_reset(client: Any, services: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args, **kwargs):
        raise ModelNotFoundError("Unknown model")
    monkeypatch.setattr(services.registry, "stream", missing)
    response = client.post("/v1/chat/completions", json={"model": "unknown/model", "messages": []})
    assert response.status_code == 404
    trace = detail(client)
    assert trace["client_response"]["body"] == response.text
    assert trace["client_response"]["status"] == 404
    assert trace["upstream"] == []
    row = services.stats.record(model="historical", ok=True)
    assert client.get(f'/api/v1/stats/calls/{row["seq"]}').json()["trace"] is None
    assert client.get("/api/v1/stats/calls/999999").status_code == 404
    client.delete("/api/v1/stats")
    assert services.stats.db.fetchall("SELECT * FROM call_traces") == []
    assert client.get(f'/api/v1/stats/calls/{row["seq"]}').status_code == 404


def test_detail_requires_management_key_and_supports_mount_prefix() -> None:
    services = make_services(access_token="management-key", model_api_key="inference-key")
    with TestClient(create_app(services, serve_ui=False, api_prefix="/gateway"), base_url="http://localhost") as client:
        response = client.post("/gateway/v1/chat/completions", headers={"Authorization": "Bearer inference-key"}, json={"model": "fake-1", "messages": []})
        assert response.status_code == 200
        url = "/gateway/api/v1/stats/calls/1"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer inference-key"}).status_code == 401
        result = client.get(url, headers={"Authorization": "Bearer management-key"})
        assert result.status_code == 200
        assert result.json()["trace"]["client_request"]["url"] == "/gateway/v1/chat/completions"


class Chunks(httpx.SyncByteStream):
    def __init__(self, parts: list[bytes]):
        self.parts, self.closed = parts, False

    def __iter__(self):
        yield from self.parts

    def close(self):
        self.closed = True


def test_upstream_attempts_sse_compression_and_credentials() -> None:
    store = StatsStore()
    turn = store.turn("m")
    sse = 'data: {"text":"你好"}\n\ndata: [DONE]\n\n'.encode()
    compressed = gzip.compress(sse)
    streams = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fail":
            return httpx.Response(503, text="refused upstream-secret", headers={"set-cookie": "session=cookie-secret"})
        stream = Chunks([compressed[:12], compressed[12:]])
        streams.append(stream)
        return httpx.Response(200, headers={"content-encoding": "gzip", "content-type": "text/event-stream"}, stream=stream)

    with NetworkClient(provider="example", transport=httpx.MockTransport(handle)) as client:
        def generate():
            turn.trace.route = {"account": "a1", "attempt": 1}
            client.post("https://example.test/fail?key=query-secret", json={"messages": ["hi"]}, headers={"Authorization": "Bearer upstream-secret"})
            # Token maintenance inside an inference call must never be captured.
            with operation("token_refresh"):
                client.post("https://example.test/fail", json={"refresh_token": "refresh-secret"})
            turn.trace.route = {"account": "a2", "attempt": 2}
            with client.stream("POST", "https://example.test/good", json={"model": "actual", "stream": True}) as response:
                assert response.read() == sse
                yield {"type": "text_delta", "text": "你好"}
        list(turn.wrap(generate()))
    trace = store.call_trace(1)["trace"]
    first, second = trace["upstream"]
    assert first["response"]["status"] == 503
    assert first["response_body"]["body"] == "refused ***"
    assert first["request"]["headers"]["authorization"] == "***"
    assert first["response"]["headers"]["set-cookie"] == "***"
    assert first["account"] == "a1" and second["account"] == "a2"
    assert json.loads(second["request"]["body"]) == {"model": "actual", "stream": True}
    assert second["response_body"]["body"] == sse.decode()
    assert second["response_body"]["complete"] is True
    assert streams[0].closed
    assert current_trace.get() is None
    assert not any(secret in json.dumps(trace) for secret in ("upstream-secret", "query-secret", "cookie-secret", "refresh-secret"))
    store.close()


def test_partial_stream_and_concurrent_calls_are_isolated() -> None:
    store = StatsStore()

    def run(name: str):
        turn = store.turn(name)
        stream = Chunks([name.encode(), b"unread"])
        with NetworkClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
            def generate():
                with client.stream("POST", "https://example.test/infer", json={"model": name}) as response:
                    for chunk in response.iter_bytes():
                        yield {"type": "text_delta", "text": chunk.decode()}
            chunks = turn.wrap(generate())
            assert next(chunks)["text"] == name
            assert current_trace.get() is None
            chunks.close()
        assert stream.closed
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ["one", "two"]))
    rows, _ = store.calls()
    for row in rows:
        trace = store.call_trace(row["seq"])["trace"]
        assert trace["response"]["complete"] is False
        assert trace["upstream"][0]["response_body"]["body"] == row["model"]
        assert trace["upstream"][0]["response_body"]["complete"] is False
    store.close()


def test_internal_playground_call_and_persistence_retention(tmp_path: Any) -> None:
    services = make_services()
    db = Database(tmp_path / "trace.db")
    services.stats = StatsStore(db=db)
    body = {"messages": [{"role": "user", "content": "internal call"}]}
    list(tracked_chunks(services, "fake-1", body, origin="playground", stream=True))
    services.stats.close()
    db.close()
    db = Database(tmp_path / "trace.db")
    store = StatsStore(db=db)
    trace = store.call_trace(1)["trace"]
    assert trace["client_request"] is None
    assert trace["request"] == body
    assert trace["response"]["complete"] is True
    with db.transaction() as conn:
        conn.execute("UPDATE calls SET ts = ?", (time.time() - 100 * 86400,))
        store._prune(conn, time.time())
    assert store.call_trace(1) is None
    assert db.fetchall("SELECT * FROM call_traces") == []
    store.save_trace("missing", zlib.compress(json.dumps(trace).encode()))
    store.flush()
    assert db.fetchall("SELECT * FROM call_traces") == []
    store.close()
    db.close()
    services.registry.close()


def test_middleware_marks_failed_delivery_incomplete() -> None:
    store = StatsStore()

    async def app(scope, receive, send):
        await receive()
        turn = store.turn("m")
        turn.finish()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await send({"type": "http.response.body", "body": b"lost"})

    async def receive():
        return {"type": "http.request", "body": b"{}"}

    async def send(message):
        if message.get("body") == b"lost":
            raise OSError("disconnected")

    with pytest.raises(OSError):
        asyncio.run(CallTraceMiddleware(app)({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []}, receive, send))
    trace = store.call_trace(1)["trace"]
    assert trace["client_response"]["body"] == "first"
    assert trace["client_response"]["complete"] is False
    assert trace["client_response"]["finished"] is True
    store.close()


def test_network_failure_retains_request_without_inventing_response() -> None:
    store = StatsStore()
    turn = store.turn("m")

    def fail(request):
        raise httpx.ConnectError("connection refused", request=request)

    with NetworkClient(transport=httpx.MockTransport(fail)) as client:
        def generate():
            client.post("https://example.test/infer", json={"model": "m"})
            yield {}
        with pytest.raises(httpx.RequestError):
            list(turn.wrap(generate()))
    attempt = store.call_trace(1)["trace"]["upstream"][0]
    assert attempt["error"] == "ConnectError"
    assert json.loads(attempt["request"]["body"]) == {"model": "m"}
    assert "response" not in attempt
    assert attempt["response_body"]["complete"] is False
    assert current_trace.get() is None
    store.close()


def test_bedrock_sdk_records_parameters_and_events(monkeypatch: pytest.MonkeyPatch) -> None:
    from hoshimi.core.providers import bedrock
    from hoshimi.core.providers.base import Account

    events = [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "hello"}}}, {"messageStop": {"stopReason": "end_turn"}}]
    class EventStream:
        closed = False

        def __iter__(self):
            yield from events

        def close(self):
            self.closed = True

    stream = EventStream()
    class SDK:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def converse_stream(self, **payload):
            assert payload["modelId"] == "model-a"
            return {"stream": stream, "ResponseMetadata": {"HTTPStatusCode": 200}}

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "botocore.eventstream", SimpleNamespace(EventStreamBuffer=object))
    monkeypatch.setitem(sys.modules, "botocore.exceptions", SimpleNamespace(ClientError=type("ClientError", (Exception,), {})))
    monkeypatch.setattr(bedrock, "bedrock_config", lambda *args: None)
    monkeypatch.setattr(bedrock.BedrockProvider, "_session", staticmethod(lambda *args: SimpleNamespace(client=lambda *args, **kwargs: SDK())))
    store = StatsStore()
    turn = store.turn("model-a")
    with httpx.Client() as client:
        provider = bedrock.BedrockProvider(client, [Account({"aws_access_key_id": "id", "aws_secret_access_key": "secret"})])
        list(turn.wrap(provider.stream_raw(provider.accounts[0], "model-a", {"messages": [{"role": "user", "content": "hi"}]})))
    attempt = store.call_trace(1)["trace"]["upstream"][0]
    assert attempt["protocol"] == "aws-sdk"
    assert attempt["request"]["modelId"] == "model-a"
    assert attempt["response"]["events"] == events
    assert attempt["response"]["complete"] is True
    assert stream.closed
    store.close()
