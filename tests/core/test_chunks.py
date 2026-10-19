from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from hoshimi.core.chunks import (
    aggregate_chat,
    aggregate_responses,
    emit_chat_chunks,
    emit_responses_events,
)


def chunks(*items: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield from items


def test_emit_chat_chunks_roundtrip() -> None:
    source = chunks(
        {"type": "text_delta", "text": "hello "},
        {"type": "reasoning_delta", "text": "thinking"},
        {"type": "text_delta", "text": "world"},
        {
            "type": "tool_call_start",
            "id": "call_1",
            "name": "get_weather",
            "arguments": '{"city":',
            "index": 0,
        },
        {"type": "tool_call_delta", "index": 0, "arguments": '"Paris"}'},
        {"type": "usage", "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}},
        {"type": "finish", "reason": "tool_calls"},
    )
    emitted = list(
        emit_chat_chunks(source, model="m", completion_id="chatcmpl_x", created=1)
    )
    # First chunk announces the role.
    assert emitted[0]["choices"][0]["delta"] == {"role": "assistant"}
    # Finish chunk carries the reason.
    assert emitted[-1]["choices"][0]["finish_reason"] == "tool_calls"

    message, finish_reason, usage = aggregate_chat(emitted)
    assert message["content"] == "hello world"
    assert message["reasoning_content"] == "thinking"
    assert message["tool_calls"][0]["function"]["name"] == "get_weather"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"city": "Paris"}
    assert finish_reason == "tool_calls"
    assert usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}


def test_emit_chat_chunks_emits_stop_when_upstream_omits_finish() -> None:
    emitted = list(
        emit_chat_chunks(
            chunks({"type": "text_delta", "text": "hi"}),
            model="m",
            completion_id="c",
            created=1,
        )
    )
    assert emitted[-1]["choices"][0]["finish_reason"] == "stop"


def test_responses_event_sequence() -> None:
    source = chunks(
        {"type": "reasoning_delta", "text": "r1"},
        {"type": "text_delta", "text": "a"},
        {"type": "text_delta", "text": "b"},
        {"type": "finish", "reason": "stop"},
    )
    events = list(
        emit_responses_events(source, model="m", response_id="resp_1", created=7)
    )
    types = [event["type"] for event in events]
    assert types[0] == "response.created"
    assert types[1] == "response.in_progress"
    assert "response.reasoning_summary_text.delta" in types
    assert "response.output_text.delta" in types
    assert types[-1] == "response.completed"
    # sequence numbers are dense and ordered
    assert [e["sequence_number"] for e in events] == list(range(len(events)))

    final = events[-1]["response"]
    assert final["status"] == "completed"
    kinds = [item["type"] for item in final["output"]]
    assert kinds == ["reasoning", "message"]
    text = final["output"][1]["content"][0]["text"]
    assert text == "ab"


def test_responses_aggregate_with_tool_calls() -> None:
    source = chunks(
        {"type": "tool_call_start", "id": "call_9", "name": "fn", "arguments": "{}", "index": 0},
        {"type": "finish", "reason": "tool_calls"},
    )
    payload = aggregate_responses(source, model="m", response_id="resp_2", created=1)
    assert payload["output"][0]["type"] == "function_call"
    assert payload["output"][0]["name"] == "fn"
    assert payload["usage"]["total_tokens"] == 0


def test_aggregate_chat_propagates_upstream_error() -> None:
    from hoshimi.core.chunks import UpstreamStreamFailure

    with pytest.raises(UpstreamStreamFailure):
        aggregate_chat(
            emit_chat_chunks(
                chunks({"type": "error", "error": "boom"}),
                model="m",
                completion_id="c",
                created=1,
            )
        )
