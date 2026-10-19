"""Complete catalogs and request history across ordinary provider profiles."""

import copy
import json

import httpx
import pytest

from hoshimi.core.chunks import aggregate_chat, aggregate_responses, emit_chat_chunks
from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.catalog_profiles import CATALOG_PROFILES, CatalogProfile, Field, discover_catalog
from hoshimi.core.providers.keyed import KeyedProvider
from hoshimi.core.providers.openai_compat import consume_openai_sse
from hoshimi.core.providers.request_profiles import ChatProfile
from hoshimi.core.providers.transports import request_payload
from hoshimi.core.registry import Registry
from hoshimi.core.translate.responses import responses_to_chat


def test_numbered_catalog_is_atomic_and_keeps_account_model_ownership(monkeypatch):
    profile = CatalogProfile(collection=("data",), pagination="numbered", fields=(Field("context_length", ("limits", "context")),))
    monkeypatch.setitem(CATALOG_PROFILES, "test", profile)
    broken = False
    def handle(request):
        key = request.headers["authorization"].split()[-1]
        page = int(request.url.params["page"])
        if broken and key == "a" and page == 2:
            return httpx.Response(500)
        return httpx.Response(200, json={"data": [{"id": f"{key}-{page}", "limits": {"context": 9000}}], "pagination": {"current_page": page, "total_pages": 2}})
    class Lane(KeyedProvider):
        id = "test"
        base_url = "https://example.test/v1"
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = Lane(client, [Account({"id": "a", "api_key": "a"}), Account({"id": "b", "api_key": "b"})])
        assert lane.fetch_models() == ["a-1", "a-2", "b-1", "b-2"]
        assert lane.specs()["a-1"].context_window == 9000
        broken = True
        assert lane.fetch_models() == ["a-1", "a-2", "b-1", "b-2"]
        assert [a.id for a in lane._stream_candidates("b-2")] == ["b"]


@pytest.mark.parametrize("payload", [
    {"data": [{"id": "a"}], "pagination": {"current_page": 1, "total_pages": 2}},
    {"data": [{"id": "a"}], "pagination": {"current_page": 0, "total_pages": 1}},
])
def test_repeated_or_invalid_page_fails_instead_of_publishing_prefix(payload):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client, pytest.raises(ValueError):
        discover_catalog(client, "https://example.test/models", {}, CatalogProfile(pagination="numbered", collection=("data",), max_pages=3))


def test_request_profile_preserves_opaque_history_and_input():
    body = {"max_tokens": 20, "reasoning_effort": "high", "store": True,
            "messages": [{"role": "developer", "content": "system"}, {"role": "assistant", "reasoning_details": [{"signature": "opaque"}], "tool_calls": [{"id": "call", "extra_content": {"google": {"thought_signature": "signed"}}, "function": {"name": "f", "arguments": "{}"}}]}],
            "tools": [{"type": "function", "function": {"name": "f", "strict": True}}]}
    before = copy.deepcopy(body)
    result = ChatProfile(max_tokens_field="max_completion_tokens", strict_tools=False, reasoning="thinking").prepare(body)
    assert result["max_completion_tokens"] == 20 and "store" not in result
    assert result["thinking"] == {"type": "enabled"}
    assert result["messages"][1] == body["messages"][1]
    assert "strict" not in result["tools"][0]["function"]
    assert body == before


def test_tool_signature_survives_chat_and_responses_round_trips():
    extra = {"google": {"thought_signature": "opaque"}}
    frame = {"choices": [{"delta": {"tool_calls": [{"id": "call", "index": 0, "extra_content": extra, "function": {"name": "f", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
    chunks = list(consume_openai_sse(["data: " + json.dumps(frame), "", "data: [DONE]", ""], provider="test"))
    message, _, _ = aggregate_chat(emit_chat_chunks(chunks, model="m", completion_id="c", created=0))
    chat = request_payload({"messages": [message]}, "m", "chat")
    assert chat["messages"][0]["tool_calls"][0]["extra_content"] == extra
    response = aggregate_responses(chunks, model="m", response_id="r", created=0)
    restored = request_payload(responses_to_chat({"input": response["output"]}), "m", "chat")
    assert next(call for msg in restored["messages"] for call in msg.get("tool_calls", []))["extra_content"] == extra


@pytest.mark.parametrize("pagination,payload", [("none", {"error": "temporarily unavailable"}), ("cursor", {"data": [{"id": "a"}], "nextPageToken": "same"})])
def test_malformed_success_and_stalled_cursor_are_failures(pagination, payload):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client, pytest.raises(ValueError):
        discover_catalog(client, "https://example.test/models", {}, CatalogProfile(pagination=pagination, max_pages=3))


def test_registry_keeps_real_vendor_effort_instead_of_applying_free_lane_budget():
    seen = []
    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text="data: [DONE]\n\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = PROVIDER_CLASSES["atlascloud"](client, [Account({"api_key": "key", "models": [{"id": "m", "reasoning": True}]})])
        list(Registry({"atlascloud": lane}).stream("atlascloud/m", {"messages": [], "reasoning_effort": "xhigh", "max_tokens": 20}))
        assert seen[0]["reasoning_effort"] == "xhigh"
        assert seen[0]["max_tokens"] == 20
        assert lane.specs()["m"].efforts == ("high", "xhigh")
