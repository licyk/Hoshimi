"""The Anthropic-compatible surface uses existing routing, statistics and security."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.core.providers.base import ProviderError
from hoshimi.core.sse import iter_sse
from tests.conftest import FakeProvider


def body(**fields):
    return {"model": "fake-1", "max_tokens": 1024, "messages": [{"role": "user", "content": "hi"}], **fields}


def frames(response):
    return [json.loads(data) for name, data in iter_sse(response.text.splitlines()) if name]


def test_messages_defaults_to_json_and_counts_one_turn(client, services):
    response = client.post("/v1/messages", json=body(), headers={"anthropic-version": "2023-06-01"})
    assert response.status_code == 200
    message = response.json()
    assert message["id"].startswith("msg_")
    assert message["type"] == "message" and message["role"] == "assistant"
    assert message["content"] == [{"type": "text", "text": "echo:fake-1:1"}]
    assert message["model"] == "fake-1"
    assert message["stop_reason"] == "end_turn"
    assert message["usage"]["input_tokens"] == 1
    assert response.headers["request-id"] == message["id"]
    sample = services.stats.calls(limit=1)[0][0]
    assert sample["origin"] == "messages" and sample["stream"] is False
    assert (sample["provider"], sample["account"]) == ("fake", "fake-0")


def test_messages_stream_has_named_events_and_no_done(client):
    response = client.post("/v1/messages?beta=true", json=body(stream=True))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "[DONE]" not in response.text
    events = frames(response)
    assert events[0]["type"] == "message_start"
    assert events[-1]["type"] == "message_stop"
    assert [event["type"] for event in events].count("message_delta") == 1
    assert "".join(e["delta"].get("text", "") for e in events if e["type"] == "content_block_delta") == "echo:fake-1:1"
    assert events[-2]["usage"]["output_tokens"] == 2


def test_tool_loop_can_be_replayed_through_the_same_account(client, services, monkeypatch):
    lane = services.registry.providers["fake"]
    seen = []

    def stream(account, model, request):
        seen.append((account.id, request))
        if request["messages"][-1]["role"] == "tool":
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "finish", "reason": "stop"}
        else:
            yield {"type": "tool_call_start", "index": 0, "id": "toolu_unchanged", "name": "read", "arguments": '{"path":"a"}'}
            yield {"type": "finish", "reason": "tool_calls"}

    monkeypatch.setattr(lane, "stream_raw", stream)
    tools = [{"name": "read", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}]
    response = client.post("/v1/messages", json=body(tools=tools))
    assert response.json()["stop_reason"] == "tool_use"
    content = response.json()["content"]
    response = client.post(
        "/v1/messages",
        json=body(
            tools=tools,
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": content},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_unchanged", "content": "file"}]},
            ],
        ),
    )
    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "done"
    assert seen[0][0] == seen[1][0]
    assert seen[1][1]["messages"][-1]["tool_call_id"] == "toolu_unchanged"


@pytest.mark.parametrize("stream", [False, True])
def test_early_upstream_errors_keep_the_http_status(client, services, monkeypatch, stream):
    def fail(*args, **kwargs):
        raise ProviderError(429, "busy")

    monkeypatch.setattr(services.registry, "stream", fail)
    response = client.post("/v1/messages", json=body(stream=stream))
    assert response.status_code == 429
    assert response.json() == {"type": "error", "error": {"type": "rate_limit_error", "message": "busy"}}


@pytest.mark.parametrize("stream", [False, True])
def test_late_errors_never_send_a_successful_message_stop(client, services, monkeypatch, stream):
    def fail(account, model, request):
        yield {"type": "text_delta", "text": "partial"}
        yield {"type": "error", "error": "broken"}

    monkeypatch.setattr(services.registry.providers["fake"], "stream_raw", fail)
    response = client.post("/v1/messages", json=body(stream=stream))
    if stream:
        assert frames(response)[-1] == {"type": "error", "error": {"type": "api_error", "message": "broken"}}
        assert "event: message_stop" not in response.text
    else:
        assert response.status_code == 502
        assert response.json()["error"]["type"] == "api_error"


def test_a_quota_error_after_content_never_rotates_and_duplicates_a_turn(client, services, monkeypatch):
    from hoshimi.core.providers.base import Account, QuotaExceeded

    lane = services.registry.providers["fake"]
    lane.accounts.append(Account({"id": "second", "access_token": "second"}, provider_id="fake"))
    calls = []

    def fail(account, model, request):
        calls.append(account.id)
        yield {"type": "text_delta", "text": "partial"}
        raise QuotaExceeded(429, "limit after partial output")

    monkeypatch.setattr(lane, "stream_raw", fail)
    response = client.post("/v1/messages", json=body(stream=True))
    assert frames(response)[-1]["type"] == "error"
    assert len(calls) == 1
    assert lane.cooler.blocked(lane.id, calls[0])


@pytest.mark.parametrize("headers", [{"x-api-key": "relay"}, {"x-api-key": "admin"}, {"Authorization": "Bearer relay"}])
def test_sdk_credentials_do_not_open_management_routes(services, headers):
    services.settings.access_token = "admin"
    services.settings.model_api_key = "relay"
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        assert client.post("/v1/messages", json=body(), headers=headers).status_code == (401 if headers == {"x-api-key": "admin"} else 200)
        assert client.get("/v1/models", headers=headers).status_code == (401 if headers == {"x-api-key": "admin"} else 200)
        assert client.get("/api/v1/settings", headers=headers).status_code == 401
        denied = client.post("/v1/messages", json=body(), headers={"x-api-key": "bad"})
        assert denied.status_code == 401
        assert denied.json()["error"]["type"] == "authentication_error"
        assert client.post("/v1/messages", json=body(), headers={"Authorization": "Bearer bad", "x-api-key": "relay"}).status_code == 401


@pytest.mark.parametrize("content", ["not json", "[]", "null", '{"model":"fake-1"}'])
def test_malformed_requests_have_anthropic_error_envelopes(client, content):
    response = client.post("/v1/messages", content=content, headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_unknown_model_is_anthropic_shaped_404(client, services):
    services.registry.providers["other"] = FakeProvider()
    response = client.post("/v1/messages", json=body(model="missing"))
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found_error"


def test_count_tokens_is_explicitly_an_estimate_and_does_not_generate(client, services):
    response = client.post("/v1/messages/count_tokens", json={"model": "fake-1", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 200
    assert response.json()["input_tokens"] > 0
    assert response.headers["x-hoshimi-token-count"] == "estimate"
    assert not services.registry.providers["fake"].seen


def test_prefix_and_asgi_mount_preserve_sdk_auth_and_error_shapes(services):
    services.settings.access_token = "admin"
    services.settings.model_api_key = "relay"
    services.registry.providers["other"] = FakeProvider()
    host = FastAPI()
    host.mount("/mounted", create_app(services, api_prefix="/gateway", serve_ui=False))
    with TestClient(host, base_url="http://localhost") as client:
        path = "/mounted/gateway/v1/messages"
        assert client.post(path, json=body()).json()["error"]["type"] == "authentication_error"
        response = client.post(path, json=body(stream=True), headers={"x-api-key": "relay"})
        assert response.status_code == 200
        assert frames(response)[-1]["type"] == "message_stop"
        response = client.post(path, json=body(model="missing"), headers={"x-api-key": "relay"})
        assert response.status_code == 404 and response.json()["error"]["type"] == "not_found_error"


def test_openapi_exposes_both_response_formats_and_token_estimation(client):
    schema = client.get("/openapi.json").json()
    endpoint = schema["paths"]["/v1/messages"]["post"]
    assert endpoint["operationId"] == "create_message"
    assert set(endpoint["responses"]["200"]["content"]) == {"application/json", "text/event-stream"}
    assert schema["paths"]["/v1/messages/count_tokens"]["post"]["operationId"] == "count_message_tokens"


def test_native_provider_preserves_configuration_and_uses_only_its_own_key(services):
    import httpx

    from hoshimi.core.providers import PROVIDER_CLASSES
    from hoshimi.core.providers.base import Account

    seen = []

    def handle(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "upstream-secret"
        assert "authorization" not in request.headers
        sent = json.loads(request.content)
        seen.append(sent)
        assert sent["model"] == "claude-test"
        assert sent["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
        native = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 2, "cache_read_input_tokens": 9}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "reason", "signature": "native-signature"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "answer"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
            {"type": "message_stop"},
        ]
        if not sent.get("stream"):
            return httpx.Response(200, json={
                "id": "msg_native", "type": "message", "role": "assistant", "model": sent["model"],
                "content": [{"type": "thinking", "thinking": "reason", "signature": "native-signature"}, {"type": "text", "text": "answer"}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 2, "cache_read_input_tokens": 9, "output_tokens": 3},
            })
        return httpx.Response(200, text="".join("data: " + json.dumps(frame) + "\n\n" for frame in native), headers={"content-type": "text/event-stream"})

    upstream = httpx.Client(transport=httpx.MockTransport(handle))
    lane = PROVIDER_CLASSES["anthropic"](upstream, [Account({"api_key": "upstream-secret", "models": ["claude-test"]}, provider_id="anthropic")])
    services.registry.providers = {"anthropic": lane}
    services.settings.access_token = "admin"
    services.settings.model_api_key = "relay"
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        request = body(
            model="anthropic/claude-test",
            max_tokens=4096,
            thinking={"type": "enabled", "budget_tokens": 1024},
            system=[{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}],
        )
        response = client.post("/v1/messages", json=request, headers={"x-api-key": "relay"})
        assert response.status_code == 200
        assert response.json()["usage"]["input_tokens"] == 2
        assert response.json()["usage"]["cache_read_input_tokens"] == 9
        request["messages"].append({"role": "assistant", "content": response.json()["content"]})
        request["messages"].append({"role": "user", "content": "continue"})
        assert client.post("/v1/messages", json=request, headers={"x-api-key": "relay"}).status_code == 200
        assert seen[1]["messages"][1]["content"][0] == {"type": "thinking", "thinking": "reason", "signature": "native-signature"}
