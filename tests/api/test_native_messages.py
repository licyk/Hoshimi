"""Claude Code extensions survive routing, account rotation and actual SDK parsing."""

import importlib
import json
import socket
import threading

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.core.native_messages import MessagesCancellation, MessagesRequest
from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account


def message(**extra):
    return {
        "id": "msg_upstream", "type": "message", "role": "assistant", "model": "claude-test",
        "content": [{"type": "text", "text": "你好"}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 7},
        **extra,
    }


def request(**extra):
    return {"model": "anthropic/claude-test", "max_tokens": 4096, "messages": [{"role": "user", "content": "hi"}], **extra}


def event_stream(*extra):
    initial = message(content=[], stop_reason=None)
    initial["usage"] = {**initial["usage"], "output_tokens": 0}
    frames = [
        {"type": "message_start", "message": initial},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}},
        *extra,
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    return "".join(f"event: {frame['type']}\r\ndata: {json.dumps(frame, ensure_ascii=False)}\r\n\r\n" for frame in frames).encode()


def install(services, handle, accounts=None, options=None):
    lane = PROVIDER_CLASSES["anthropic"](
        httpx.Client(transport=httpx.MockTransport(handle)),
        [Account(data, index=index, provider_id="anthropic") for index, data in enumerate(accounts or [
            {"api_key": "upstream-key", "models": ["claude-test"]},
        ])], options,
    )
    services.registry.providers = {"anthropic": lane}
    services.settings.model_api_key = "gateway-key"
    return lane


def test_native_extensions_and_error_metadata_are_not_rebuilt(services):
    body = request(
        system=[{"type": "text", "text": "attribution"}, {"type": "text", "text": "system", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        context_management={"edits": [{"type": "clear_tool_uses_20250919"}]},
        output_config={"effort": "xhigh", "format": {"type": "json_schema", "schema": {"type": "object"}}},
        tools=[{"name": "Read", "input_schema": {"type": "object"}, "defer_loading": True, "input_examples": [{}]},
               {"name": "tool_search_tool_regex", "type": "tool_search_tool_regex_20251119"}],
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "thought", "signature": "original-signature"},
            {"type": "text", "text": "A"}, {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}, {"type": "text", "text": "B"},
        ]}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "tool_reference", "tool_name": "Read"}]}]}],
        future_field={"preserve": True},
    )
    original = json.dumps(body)

    def handle(upstream):
        assert json.loads(upstream.content) == {**body, "model": "claude-test"}
        assert upstream.headers["x-api-key"] == "upstream-key"
        assert "authorization" not in upstream.headers
        assert upstream.headers["anthropic-beta"] == "future-capability-2026,another-beta"
        assert upstream.headers["anthropic-version"] == "2023-06-01"
        assert upstream.headers["anthropic-future"] == "yes"
        assert dict(upstream.url.params) == {"beta": "true"}
        return httpx.Response(200, json=message(), headers={"request-id": "req_native", "anthropic-ratelimit-unified-status": "allowed", "set-cookie": "secret"})

    install(services, handle)
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/messages?beta=true", json=body, headers={
            "x-api-key": "gateway-key", "anthropic-beta": "future-capability-2026,another-beta",
            "anthropic-version": "2023-06-01", "anthropic-future": "yes",
        })
    assert response.status_code == 200
    assert response.json() == message()
    assert response.headers["request-id"] == "req_native"
    assert response.headers["anthropic-ratelimit-unified-status"] == "allowed"
    assert "set-cookie" not in response.headers
    assert json.dumps(body) == original
    sample = services.stats.snapshot()["samples"][-1]
    assert sample["origin"] == "messages" and sample["effort"] == "xhigh"


@pytest.mark.parametrize("status", [400, 422, 429, 500, 529])
def test_upstream_errors_and_retry_headers_survive_exactly(services, status):
    raw = b'{"type":"error","error":{"type":"invalid_request_error","message":"output_config.effort is not supported"},"request_id":"req_error"}'
    calls = []

    def handle(upstream):
        calls.append(upstream)
        return httpx.Response(status, content=raw, headers={
            "content-type": "application/json", "retry-after": "7", "x-should-retry": "false", "request-id": "req_error",
        })

    install(services, handle, accounts=[{"id": str(i), "api_key": str(i), "models": ["claude-test"]} for i in range(2)])
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/messages", json=request(), headers={"x-api-key": "gateway-key"})
    assert response.status_code == status and response.content == raw
    assert response.headers["retry-after"] == "7" and response.headers["x-should-retry"] == "false"
    assert response.headers["request-id"] == "req_error"
    assert len(calls) == 1
    assert services.stats.snapshot()["samples"][-1]["ok"] is False


def test_native_rotation_uses_the_next_compatible_account_and_records_one_turn(services):
    calls = []

    def handle(upstream):
        key = upstream.headers["x-api-key"]
        calls.append(key)
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "expired"}}) if key == "bad" else httpx.Response(200, json=message())

    install(services, handle, accounts=[
        {"api_key": "chat-only", "protocol": "chat"}, {"api_key": "bad"}, {"api_key": "good"},
    ])
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/messages", json=request(), headers={"x-api-key": "gateway-key"})
    assert response.status_code == 200
    assert calls == ["bad", "good"]
    assert len(services.stats.snapshot()["samples"]) == 1


class Fragments(httpx.SyncByteStream):
    def __init__(self, data):
        self.data = data
        self.closed = False

    def __iter__(self):
        for byte in self.data:
            yield bytes([byte])

    def close(self):
        self.closed = True


def test_native_sse_keeps_unknown_events_utf8_and_crlf_and_accounts_usage(services):
    raw = event_stream({"type": "ping"}, {"type": "future_event", "opaque": {"data": "原样"}})
    stream = Fragments(raw)
    install(services, lambda _: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream", "request-id": "req_stream"}))
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/messages", json=request(stream=True), headers={"x-api-key": "gateway-key"})
    assert response.status_code == 200 and response.content == raw
    assert response.headers["request-id"] == "req_stream" and stream.closed
    sample = services.stats.snapshot()["samples"][-1]
    assert sample["input"] == 15 and sample["output"] == 2
    assert services.stats.snapshot()["models"][0]["cache_read"] == 5
    assert sample["ok"]


def test_truncated_native_sse_reports_error_without_a_successful_stop(services):
    raw = event_stream().split(b"event: message_delta", 1)[0] + b'event: content_block_delta\ndata: {"incomplete":'
    install(services, lambda _: httpx.Response(200, content=raw, headers={"content-type": "text/event-stream"}))
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/messages", json=request(stream=True), headers={"x-api-key": "gateway-key"})
    assert b"event: error\n" in response.content and b"event: message_stop" not in response.content
    assert b'"incomplete"' not in response.content
    assert services.stats.snapshot()["samples"][-1]["ok"] is False


@pytest.mark.parametrize("status", [200, 404])
def test_native_token_count_or_marked_fallback_accepts_full_content(services, status):
    def handle(upstream):
        assert upstream.url.path == "/v1/messages/count_tokens"
        assert "stream" not in json.loads(upstream.content)
        return httpx.Response(status, json={"input_tokens": 321} if status == 200 else {"error": "not supported"})

    install(services, handle)
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/messages/count_tokens", json={
            "model": "anthropic/claude-test", "messages": [{"role": "user", "content": [{"type": "document", "source": {"type": "base64", "data": "opaque"}}]}],
        }, headers={"x-api-key": "gateway-key"})
    assert response.status_code == 200
    assert (response.headers.get("x-hoshimi-token-count") == "estimate") == (status == 404)
    if status == 200:
        assert response.json() == {"input_tokens": 321}
    assert not services.stats.snapshot()["samples"]


def test_closing_native_iterator_closes_its_response(services):
    stream = Fragments(event_stream())
    install(services, lambda _: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))
    context = MessagesRequest(body=request(stream=True))
    upstream = services.registry.native_messages(context.body["model"], context)
    assert next(upstream)["type"] == "messages_response"
    context.cancellation.close()
    upstream.close()
    assert stream.closed


def test_native_disconnect_interrupts_a_blocked_socket_read():
    reader, writer = socket.socketpair()
    ready, finished = threading.Event(), threading.Event()

    class Network:
        def get_extra_info(self, name):
            return reader if name == "socket" else None

    class SocketStream(httpx.SyncByteStream):
        def __iter__(self):
            ready.set()
            try:
                data = reader.recv(1024)
                if data:
                    yield data
            except OSError:
                pass
            finally:
                finished.set()

        def close(self):
            reader.close()

    response = httpx.Response(200, stream=SocketStream(), extensions={"network_stream": Network()})
    cancellation = MessagesCancellation()
    cancellation.attach(response)
    thread = threading.Thread(target=lambda: list(response.iter_bytes()), daemon=True)
    try:
        thread.start()
        assert ready.wait(1)
        cancellation.close()
        assert finished.wait(1) and response.is_closed
    finally:
        writer.close()
        reader.close()
        thread.join(timeout=1)


def test_gateway_model_discovery_keeps_openai_shape_and_a_limit(services):
    install(services, lambda _: httpx.Response(200, json=message()))
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.get("/v1/models?limit=1", headers={"x-api-key": "gateway-key"})
    assert response.status_code == 200 and response.json()["object"] == "list"
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["id"] == "claude-test"
    assert response.json()["data"][0]["display_name"]


@pytest.mark.parametrize("native", [False, True])
def test_official_sdk_consumes_json_stream_tools_and_counts(services, native, monkeypatch):
    if native:
        install(services, lambda req: httpx.Response(200, json={"input_tokens": 11}) if req.url.path.endswith("count_tokens") else (
            httpx.Response(200, content=event_stream(), headers={"content-type": "text/event-stream"}) if json.loads(req.content).get("stream") else httpx.Response(200, json=message())
        ))
    else:
        services.settings.model_api_key = "gateway-key"
    model = "anthropic/claude-test" if native else "fake-1"
    # Use the HTTP module selected by the SDK version, independent of the
    # server's own httpx version. The SDK still performs all real parsing.
    sdk_http = importlib.import_module(anthropic.DefaultHttpxClient.__mro__[1].__module__.split(".")[0])
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as gateway:
        def transport(req):
            response = gateway.request(req.method, str(req.url), headers=dict(req.headers), content=req.read())
            return sdk_http.Response(response.status_code, headers=dict(response.headers), content=response.content)

        with anthropic.Anthropic(api_key="gateway-key", base_url="http://localhost", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=sdk_http.MockTransport(transport))) as sdk:
            result = sdk.messages.create(model=model, max_tokens=1024, messages=[{"role": "user", "content": "hi"}])
            assert result.type == "message" and result.stop_reason == "end_turn"
            with sdk.messages.stream(model=model, max_tokens=1024, messages=[{"role": "user", "content": "hi"}]) as stream:
                final = stream.get_final_message()
                assert final.content[0].type == "text" and final.content[0].text
            assert sdk.messages.count_tokens(model=model, messages=[{"role": "user", "content": "hi"}]).input_tokens > 0
            if not native:
                def tool_stream(*args):
                    yield {"type": "tool_call_start", "id": "toolu_read", "name": "Read", "arguments": '{"file":', "index": 0}
                    yield {"type": "tool_call_start", "id": "toolu_read2", "name": "Read", "arguments": '{"file":', "index": 1}
                    yield {"type": "tool_call_delta", "arguments": '"b"}', "index": 1}
                    yield {"type": "tool_call_delta", "arguments": '"a"}', "index": 0}
                    yield {"type": "finish", "reason": "tool_calls"}

                monkeypatch.setattr(services.registry.providers["fake"], "stream_raw", tool_stream)
                with sdk.messages.stream(model=model, max_tokens=1024, tools=[{"name": "Read", "input_schema": {"type": "object"}}], messages=[{"role": "user", "content": "read"}]) as stream:
                    final = stream.get_final_message()
                    assert final.stop_reason == "tool_use" and final.content[0].input == {"file": "a"}
                    assert final.content[1].input == {"file": "b"}
