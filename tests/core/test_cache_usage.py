"""Prompt-cache reads and writes survive every usage dialect on the way to the stats."""

from __future__ import annotations

import json
from typing import Any

import pytest

from hoshimi.core.message_chunks import messages_usage
from hoshimi.core.native_messages import MessagesObserver
from hoshimi.core.providers.openai_compat import consume_openai_sse
from hoshimi.core.stats import StatsStore
from hoshimi.core.translate.responses import iter_responses_chunks


def _sse(*frames: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for frame in frames:
        lines += ["data: " + json.dumps(frame), ""]
    return lines + ["data: [DONE]", ""]


def _chat_usage(usage: dict[str, Any]) -> dict[str, Any]:
    chunks = list(consume_openai_sse(_sse({"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]}, {"choices": [], "usage": usage}), provider="test"))
    return next(chunk for chunk in chunks if chunk["type"] == "usage")["usage"]


@pytest.mark.parametrize(
    ("usage", "details"),
    [
        # OpenAI itself: reads only.
        ({"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 60}}, {"cached_tokens": 60}),
        # OpenRouter: reads and writes inside the details object.
        ({"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 70}}, {"cached_tokens": 10, "cache_write_tokens": 70}),
        # Other relays: the write as `cache_creation_tokens`.
        (
            {"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 0, "cache_creation_tokens": 30}},
            {"cached_tokens": 0, "cache_write_tokens": 30},
        ),
        # Anthropic-flavoured relays: top-level buckets.
        ({"prompt_tokens": 100, "completion_tokens": 5, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 40}, {"cached_tokens": 20, "cache_write_tokens": 40}),
        # DeepSeek: hits at the top level.
        ({"prompt_tokens": 100, "completion_tokens": 5, "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36}, {"cached_tokens": 64}),
    ],
)
def test_chat_dialects_map_into_one_vocabulary(usage: dict[str, Any], details: dict[str, Any]) -> None:
    mapped = _chat_usage(usage)
    assert mapped["prompt_tokens"] == 100 and mapped["completion_tokens"] == 5
    assert mapped["prompt_tokens_details"] == details


def test_a_station_without_cache_fields_gets_no_details() -> None:
    assert "prompt_tokens_details" not in _chat_usage({"prompt_tokens": 3, "completion_tokens": 1})


def test_explicit_cached_tokens_win_over_the_deepseek_spelling() -> None:
    mapped = _chat_usage({"prompt_tokens": 9, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 4}, "prompt_cache_hit_tokens": 8})
    assert mapped["prompt_tokens_details"] == {"cached_tokens": 4}


def test_responses_usage_keeps_cache_writes() -> None:
    lines = _sse(
        {"type": "response.output_text.delta", "output_index": 0, "delta": "ok"},
        {
            "type": "response.completed",
            "response": {"status": "completed", "usage": {"input_tokens": 50, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 30}}},
        },
    )
    usage = next(chunk for chunk in iter_responses_chunks(lines) if chunk["type"] == "usage")["usage"]
    assert usage["prompt_tokens_details"] == {"cached_tokens": 5, "cache_write_tokens": 30}


def test_writes_reach_the_stats_and_the_messages_split() -> None:
    usage = _chat_usage({"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 70}})
    # Anthropic clients see the split they expect: uncached input is what is left.
    assert messages_usage(usage) == {"input_tokens": 20, "output_tokens": 5, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 70}

    store = StatsStore()
    turn = store.turn("m")
    list(turn.wrap([{"type": "text_delta", "text": "hi"}, {"type": "usage", "usage": usage}, {"type": "finish", "reason": "stop"}]))
    totals = store.report("24h")["totals"]
    assert (totals["input"], totals["cache_read"], totals["cache_write"]) == (100, 10, 70)


def test_native_observer_counts_what_streamed() -> None:
    observer = MessagesObserver()
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3, "cache_creation_input_tokens": 12}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "hello"}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"a":1}'}},
    ]
    chunks = [chunk for event in events for chunk in observer.observe(event)]
    assert chunks[0]["usage"]["prompt_tokens_details"]["cache_write_tokens"] == 12
    assert [(chunk["type"], chunk.get("text", chunk.get("arguments"))) for chunk in chunks[1:]] == [
        ("reasoning_delta", "hmm"),
        ("text_delta", "hello"),
        ("tool_call_delta", '{"a":1}'),
    ]
