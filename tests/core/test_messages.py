"""Agent histories, opaque thinking and parallel tools survive Messages round trips."""

import json

import pytest

from hoshimi.core.chunks import UpstreamStreamFailure
from hoshimi.core.errors import ValidationError
from hoshimi.core.message_chunks import aggregate_messages, emit_messages_events, messages_usage
from hoshimi.core.providers.transports import request_payload
from hoshimi.core.translate.anthropic import iter_anthropic_chunks
from hoshimi.core.translate.messages import messages_to_chat


def request(**fields):
    return {"model": "provider/original-model", "max_tokens": 4096, "messages": [{"role": "user", "content": "hi"}], **fields}


def events(chunks):
    return list(emit_messages_events(chunks, model="provider/original-model", message_id="msg_test"))


def test_tools_results_media_and_system_map_without_mutating_the_request():
    body = request(
        system=[{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}],
        tools=[{"name": "read", "description": "Read a file", "input_schema": {"type": "object"}, "strict": True}],
        tool_choice={"type": "tool", "name": "read", "disable_parallel_tool_use": True},
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "look", "signature": "native-signature"},
                    {"type": "tool_use", "id": "toolu_exact", "name": "read", "input": {"file": "a"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_exact",
                        "is_error": True,
                        "content": [{"type": "text", "text": "bad"}, {"type": "image", "source": {"type": "url", "url": "https://image.example/a.png"}}],
                    },
                    {"type": "text", "text": "try again"},
                ],
            },
        ],
    )
    snapshot = json.dumps(body)
    chat = messages_to_chat(body, native=True)
    assert chat["model"] == body["model"]
    assert [item["role"] for item in chat["messages"]] == ["system", "assistant", "tool", "user"]
    assert chat["messages"][2]["is_error"] is True
    native = request_payload(chat, "original-model", "messages")
    assert native["system"] == body["system"]
    assert native["messages"][0]["content"] == body["messages"][0]["content"]
    assert native["messages"][1]["content"][0] == body["messages"][1]["content"][0]
    assert native["tool_choice"] == {"type": "tool", "name": "read", "disable_parallel_tool_use": True}
    assert native["tools"][0]["strict"] is True
    assert json.dumps(body) == snapshot


def test_user_text_image_order_survives_native_replay():
    parts = [
        {"type": "text", "text": "before"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}},
        {"type": "text", "text": "after", "cache_control": {"type": "ephemeral"}},
    ]
    chat = messages_to_chat(request(messages=[{"role": "user", "content": parts}]), native=True)
    assert request_payload(chat, "m", "messages")["messages"][0]["content"] == parts


@pytest.mark.parametrize(("thinking", "effort"), [({"type": "disabled"}, "none"), ({"type": "adaptive"}, "xhigh"), ({"type": "enabled", "budget_tokens": 1024}, "low")])
def test_thinking_maps_for_chat_and_stays_native_for_messages(thinking, effort):
    body = request(thinking=thinking)
    assert messages_to_chat(body)["reasoning_effort"] == effort
    native = messages_to_chat(body, native=True)
    assert "reasoning_effort" not in native
    assert request_payload(native, "m", "messages")["thinking"] == thinking


def test_output_effort_has_priority_over_budget():
    chat = messages_to_chat(request(thinking={"type": "enabled", "budget_tokens": 1024}, output_config={"effort": "max"}))
    assert chat["reasoning_effort"] == "xhigh"


def test_parallel_tool_indices_are_unique_and_finish_waits_for_usage():
    frames = events(
        [
            {"type": "text_delta", "text": "reading"},
            {"type": "tool_call_start", "index": 0, "id": "c0", "name": "read", "arguments": '{"x":'},
            {"type": "tool_call_start", "index": 1, "id": "c1", "name": "read", "arguments": '{"x":'},
            {"type": "tool_call_delta", "index": 1, "arguments": "2}"},
            {"type": "tool_call_delta", "index": 0, "arguments": "1}"},
            {"type": "finish", "reason": "tool_calls"},
            {"type": "finish", "reason": "stop"},
            {"type": "usage", "usage": {"prompt_tokens": 100, "completion_tokens": 12, "prompt_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 5}}},
        ]
    )
    starts = [f for f in frames if f["type"] == "content_block_start"]
    assert [f["index"] for f in starts] == [0, 1, 2]
    assert frames[-1] == {"type": "message_stop"}
    assert sum(f["type"] == "message_delta" for f in frames) == 1
    final = aggregate_messages(frames)
    assert [block["input"] for block in final["content"][1:]] == [{"x": 1}, {"x": 2}]
    assert final["stop_reason"] == "tool_use"
    assert final["usage"] == {"input_tokens": 75, "output_tokens": 12, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5}


def test_native_signed_thinking_redaction_and_cache_buckets_survive():
    native = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "first"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig1"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "thinking", "thinking": "second", "signature": "sig2"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "redacted_thinking", "data": "redacted"}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]
    lines = [line for frame in native for line in ("data: " + json.dumps(frame), "")]
    out = aggregate_messages(emit_messages_events(iter_anthropic_chunks(lines), model="m", message_id="msg_x"))
    assert out["content"] == [
        {"type": "thinking", "thinking": "first", "signature": "sig1"},
        {"type": "thinking", "thinking": "second", "signature": "sig2"},
        {"type": "redacted_thinking", "data": "redacted"},
    ]
    assert out["usage"]["input_tokens"] == 10
    assert out["usage"]["cache_read_input_tokens"] == 20
    assert out["usage"]["cache_creation_input_tokens"] == 5
    replay = messages_to_chat(request(messages=[{"role": "assistant", "content": out["content"]}]), native=True)
    assert request_payload(replay, "m", "messages")["messages"][0]["content"] == out["content"]


@pytest.mark.parametrize(
    "detail",
    [
        {"type": "reasoning", "id": "rs_x", "encrypted_content": "opaque", "summary": []},
        {"type": "hoshimi_tool_extra", "call_id": "c", "extra_content": {"google": {"thought_signature": "sig"}}},
    ],
)
def test_other_protocol_signatures_round_trip_through_agent_history(detail):
    out = aggregate_messages(
        events(
            [
                {"type": "reasoning_delta", "text": "reason"},
                {"type": "reasoning_details", "details": [detail]},
                {"type": "text_delta", "text": "answer"},
                {"type": "finish", "reason": "stop"},
            ]
        )
    )
    chat = messages_to_chat(request(messages=[{"role": "assistant", "content": out["content"]}]))
    assert chat["messages"][0]["reasoning_details"] == [detail]


@pytest.mark.parametrize(
    "chunks",
    [
        [],
        [{"type": "text_delta", "text": "partial"}],
        [{"type": "text_delta", "text": "partial"}, {"type": "error", "error": "broken"}],
        [{"type": "tool_call_start", "index": 0, "id": "c", "name": "read", "arguments": '{"x":'}, {"type": "finish", "reason": "tool_calls"}],
        [{"type": "tool_call_delta", "index": 2, "arguments": "{}"}, {"type": "finish", "reason": "stop"}],
    ],
)
def test_truncated_or_invalid_streams_cannot_report_success(chunks):
    with pytest.raises(UpstreamStreamFailure):
        events(chunks)


@pytest.mark.parametrize(
    "fields",
    [
        {"max_tokens": True},
        {"max_tokens": 0},
        {"stream": "true"},
        {"messages": []},
        {"thinking": {"type": "enabled", "budget_tokens": "bad"}},
        {"thinking": {"type": "enabled", "budget_tokens": 4096}},
        {"tools": [{"type": "web_search_20250305", "name": "search"}]},
        {"mcp_servers": [{}]},
        {"messages": [{"role": "user", "content": [{"type": "document", "source": {}}]}]},
        {"messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "r", "signature": "hoshimi:v1:not-base64"}]}]},
    ],
)
def test_invalid_agent_requests_fail_explicitly(fields):
    with pytest.raises(ValidationError):
        messages_to_chat(request(**fields))


def test_cache_buckets_never_make_input_negative():
    assert messages_usage({"prompt_tokens": 3, "prompt_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 200}})["input_tokens"] == 0


def test_multiple_opaque_signature_packets_remain_independently_replayable():
    details = [{"type": "reasoning", "encrypted_content": "one"}, {"type": "reasoning", "encrypted_content": "two"}]
    chunks = [{"type": "reasoning_details", "details": [detail]} for detail in details] + [{"type": "finish", "reason": "stop"}]
    out = aggregate_messages(events(chunks))
    replay = messages_to_chat(request(messages=[{"role": "assistant", "content": out["content"]}]))
    assert replay["messages"][0]["reasoning_details"] == details


def test_native_stream_without_stop_cannot_be_mistaken_for_a_complete_turn():
    lines = ['data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":"partial"}}', ""]
    with pytest.raises(UpstreamStreamFailure):
        events(iter_anthropic_chunks(lines))


def test_tool_error_metadata_is_portable_for_chat():
    chat = messages_to_chat(request(messages=[{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c", "is_error": True, "content": "failed"}]}]))
    result = chat["messages"][0]
    assert "is_error" not in result
    assert result["content"] == [{"type": "text", "text": "Tool execution failed."}, {"type": "text", "text": "failed"}]
