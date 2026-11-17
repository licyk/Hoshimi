"""Complete catalogs and request history across ordinary provider profiles."""

import copy
import json

import httpx
import pytest

from hoshimi.core.chunks import aggregate_chat, aggregate_responses, emit_chat_chunks
from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.catalog_profiles import CATALOG_PROFILES, CatalogProfile, Field, discover_catalog
from hoshimi.core.providers.keyed import KeyedProvider, chat_row, modality_names, spec_from_row
from hoshimi.core.providers.openai_compat import consume_openai_sse
from hoshimi.core.providers.request_profiles import ChatProfile
from hoshimi.core.providers.transports import request_payload
from hoshimi.core.registry import Registry
from hoshimi.core.translate.responses import responses_to_chat


@pytest.mark.parametrize("envelope", ["models", "data", None])
def test_catalog_accepts_slug_ids_without_changing_explicit_ids_or_metadata(envelope):
    rows = [{"slug": "glm-5.3", "display_name": "GLM", "context_window": 1048576},
            {"id": "canonical", "slug": "alias"}]
    payload = {envelope: rows} if envelope else rows
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        found = discover_catalog(client, "https://example.test/api/v1/models", {}, CatalogProfile())
    assert found == [{**rows[0], "id": "glm-5.3"}, rows[1]]
    assert "id" not in rows[0]


@pytest.mark.parametrize("rows", [
    [{"slug": "same"}, {"slug": "same"}],
    [{"id": "same"}, {"slug": "same"}],
    [{}], [{"slug": ""}], [{"slug": "  "}], [{"slug": None}], [{"slug": 123}],
    [{"id": "", "slug": "valid"}], [{"id": None, "slug": "valid"}], [{"id": 123, "slug": "valid"}],
])
def test_slug_compatibility_still_rejects_missing_invalid_and_duplicate_ids(rows):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": rows}))) as client, pytest.raises(ValueError):
        discover_catalog(client, "https://example.test/models", {}, CatalogProfile())


def test_slug_does_not_override_a_provider_specific_id_path():
    profile = CatalogProfile(id_path=("model", "name"))
    row = {"model": {"name": "specific"}, "id": "generic", "slug": "alias"}
    assert profile.normalize(row)["id"] == "specific"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": [{"slug": "alias"}]}))) as client, pytest.raises(ValueError):
        discover_catalog(client, "https://example.test/models", {}, profile)


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


# -- modality fields come in two shapes, and both mean the same thing --------- #

# Transcribed from discovery-api.intern-ai.org.cn (one row of its ``/v1/models``):
# the shape that made an entire account read as "no models". Its modalities are
# descriptors carrying limits and pricing, not bare names.
_OBJECT_MODALITIES_ROW = {
    "id": "Sage-A1.5",
    "name": "Sage-A1.5",
    "input_modalities": [
        {"type": "text", "supported_inputs": {"max_context_length": {"value": 262144, "unit": "token"}}},
        {"type": "image", "supported_inputs": {"sources": {"type": "enum", "values": ["url", "base64"]}}},
    ],
    "output_modalities": [
        {
            "type": "text",
            "max_length": {"value": 262144, "unit": "token"},
            "streaming": True,
            "supported_parameters": {"tools": {"type": "boolean"}},
            "pricing": [{"type": "completion", "unit": "token", "cost_usd": "0.000064"}],
        }
    ],
}


def test_modality_names_reads_both_published_shapes():
    assert modality_names(["text", "image"]) == {"text", "image"}
    assert modality_names(_OBJECT_MODALITIES_ROW["output_modalities"]) == {"text"}
    assert modality_names(_OBJECT_MODALITIES_ROW["input_modalities"]) == {"text", "image"}
    # Absent, null, or a shape that says nothing all read as "not stated".
    assert modality_names(None) == set()
    assert modality_names("text") == {"text"}
    for unreadable in (7, {"type": "text"}, [7, None, {"no_type": True}]):
        assert modality_names(unreadable) == set()


def test_an_object_shaped_catalogue_is_not_filtered_away():
    """The regression: descriptors used to read as "output is not text"."""
    assert chat_row(_OBJECT_MODALITIES_ROW) is True
    # The string shape keeps working, unchanged.
    assert chat_row({"id": "a", "output_modalities": ["text"]}) is True
    # And the exclusion the filter exists for still excludes, in both shapes.
    assert chat_row({"id": "b", "output_modalities": [{"type": "image"}]}) is False
    assert chat_row({"id": "c", "architecture": {"output_modalities": [{"type": "audio"}]}}) is False
    assert chat_row({"id": "d", "output_modalities": [{"type": "text"}, {"type": "image"}]}) is True


def test_vision_is_read_from_descriptors_as_well_as_names():
    assert spec_from_row("Sage-A1.5", "openai", _OBJECT_MODALITIES_ROW).vision is True
    assert spec_from_row("m", "openai", {"id": "m", "input_modalities": ["image"]}).vision is True
    assert spec_from_row("m", "openai", {"id": "m", "input_modalities": [{"type": "text"}]}).vision is False


def test_an_object_shaped_account_reaches_the_lane_catalogue():
    """End to end: the descriptors survive discovery and reach the lane."""
    def handle(request):
        return httpx.Response(200, json={"object": "list", "data": [
            _OBJECT_MODALITIES_ROW,
            {"id": "glm-5.3", "output_modalities": ["text"]},
            {"id": "an-image-model", "output_modalities": [{"type": "image"}]},
        ]})

    class Lane(KeyedProvider):
        id = "test"
        base_url = "https://object.test/v1"

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = Lane(client, [Account({"id": "one", "api_key": "k"})])
        # The descriptor row is kept, the name row is still kept, and the
        # descriptor that really is not a chat target is still dropped.
        assert lane.fetch_models() == ["Sage-A1.5", "glm-5.3"]
        assert lane.specs()["Sage-A1.5"].vision is True


def test_the_catalogue_url_is_the_lane_address_not_the_account_one():
    """The premise the test above relies on: discovery reads ``self.base_url``."""
    seen = []

    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"object": "list", "data": [{"id": "m"}]})

    class Lane(KeyedProvider):
        id = "test"
        base_url = "https://lane.test/v1"

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = Lane(client, [Account({"id": "a", "api_key": "k", "base_url": "https://account.test/v1"})])
        lane.fetch_models()
    assert seen == ["https://lane.test/v1/models"]
