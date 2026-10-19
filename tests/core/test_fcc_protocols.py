"""Distinct subscriptions, alternate cloud wires, and bounded native tools."""

import json

import httpx
import pytest

from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.cloud import GoogleProvider, VertexProvider
from hoshimi.core.providers.nim_tools import MARK, clean_schema, native_tools


@pytest.mark.parametrize("provider", ["cline-pass", "minimax-chat", "bedrock-mantle"])
def test_separate_lane_never_reuses_agent_or_cloud_sdk_credentials(provider):
    def handle(request):
        assert request.headers["authorization"] == "Bearer separate"
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert ("max_completion_tokens" in body) == (provider == "minimax-chat")
        return httpx.Response(200, text="data: [DONE]\n\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = PROVIDER_CLASSES[provider](client, [Account({"api_key": "separate"})])
        list(lane.stream_raw(lane.accounts[0], "m", {"messages": [], "max_tokens": 10}))


def test_clinepass_selects_subscription_catalog_instead_of_free_catalog():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"clinePass": [{"id": "paid"}], "free": [{"id": "free"}]}))) as client:
        lane = PROVIDER_CLASSES["cline-pass"](client, [Account({"api_key": "key"})])
        assert lane.models() == ["paid"]


def test_google_chat_mode_uses_bearer_and_preserves_tool_signatures():
    def handle(request):
        assert request.url.path == "/v1beta/openai/chat/completions"
        assert request.headers["authorization"] == "Bearer key"
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "high"
        assert body["messages"][0]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "opaque"
        return httpx.Response(200, text="data: [DONE]\n\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = GoogleProvider(client, [Account({"api_key": "key", "protocol": "chat"})])
        list(lane.stream_raw(lane.accounts[0], "gemini", {"reasoning_effort": "max", "messages": [{"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "read", "arguments": "{}"}, "extra_content": {"google": {"thought_signature": "opaque"}}}]}]}))


def test_vertex_publisher_pagination_and_project_scoped_chat():
    def handle(request):
        assert request.headers["authorization"] == "Bearer access"
        if request.method == "GET":
            page = 2 if request.url.params.get("pageToken") else 1
            return httpx.Response(200, json={"publisherModels": [{"name": f"publishers/google/models/model-{page}"}], **({"nextPageToken": "next"} if page == 1 else {})})
        assert request.url.path == "/v1/projects/project/locations/global/endpoints/openapi/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "google/model-1"
        assert payload["extra_body"]["google"]["thinking_config"]["include_thoughts"] is True
        return httpx.Response(200, text="data: [DONE]\n\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = VertexProvider(client, [Account({"access_token": "access", "project": "project", "protocol": "chat"})])
        assert lane.models() == ["google/model-1", "google/model-2"]
        list(lane.stream_raw(lane.accounts[0], "google/model-1", {"messages": [], "reasoning_effort": "high"}))


@pytest.mark.parametrize("provider,path", [("vercel-ai-gateway", "/v1/chat/completions"), ("xai", "/v1/chat/completions")])
def test_existing_services_can_select_chat_without_replacing_default_protocol(provider, path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="data: [DONE]\n\n") if request.url.path == path else httpx.Response(404))) as client:
        lane = PROVIDER_CLASSES[provider](client, [Account({"api_key": "key"})], {"model_protocols": {"m": "chat"}})
        assert lane.wire_for("m") == "chat"
        list(lane.stream_raw(lane.accounts[0], "m", {"messages": []}))


def test_native_tool_markup_can_split_at_every_boundary_without_leaking_into_text():
    raw = "hello" + MARK + '<tool_call>' + MARK + '<invoke name="read">' + MARK + '<path>a<b.txt' + MARK + '</path>' + MARK + '</invoke>' + MARK + '</tool_call>'
    tools = [{"function": {"name": "read", "parameters": {"properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
    chunks = ({"type": "text_delta", "text": c} for c in raw)
    output = list(native_tools(iter([*chunks, {"type": "finish", "reason": "stop"}]), tools))
    assert "".join(c["text"] for c in output if c["type"] == "text_delta") == "hello"
    call = next(c for c in output if c["type"] == "tool_call_start")
    assert json.loads(call["arguments"]) == {"path": "a<b.txt"}
    assert output[-1]["reason"] == "tool_calls"
    with pytest.raises(ProviderError):
        list(native_tools(iter([{"type": "text_delta", "text": MARK + "<tool_call>"}]), tools))


def test_nim_schema_cleanup_preserves_boolean_defaults():
    assert clean_schema({"properties": {"flag": {"type": "boolean", "default": False}}, "additionalProperties": False}) == {"properties": {"flag": {"type": "boolean", "default": False}}}
