"""Anthropic Messages events over the provider-neutral chunk stream."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any
from uuid import uuid4

from hoshimi.core.chunks import UpstreamStreamFailure
from hoshimi.core.translate.messages import encode_details


def new_message_id() -> str:
    return f"msg_{uuid4().hex}"


def messages_usage(usage: dict[str, Any]) -> dict[str, int]:
    """OpenAI includes cache hits in prompt tokens; Anthropic uses separate buckets."""

    def count(value: Any) -> int:
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0

    prompt = count(usage.get("prompt_tokens"))
    details = usage.get("prompt_tokens_details") or {}
    cached = min(prompt, count(usage.get("cache_read_input_tokens", details.get("cached_tokens"))))
    written = min(prompt - cached, count(usage.get("cache_creation_input_tokens", details.get("cache_write_tokens"))))
    return {
        "input_tokens": prompt - cached - written,
        "output_tokens": count(usage.get("completion_tokens")),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": written,
    }


def _message(model: str, message_id: str) -> dict[str, Any]:
    return {"id": message_id, "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None, "stop_sequence": None, "usage": messages_usage({})}


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, **fields}


def emit_messages_events(chunks: Iterable[dict[str, Any]], *, model: str, message_id: str) -> Iterator[dict[str, Any]]:
    """Emit one terminal message delta, after the final usage, and never an OpenAI DONE."""
    yield _event("message_start", message=_message(model, message_id))
    blocks: list[dict[str, Any]] = []
    opened: set[int] = set()
    tools: dict[int, int] = {}
    current: int | None = None
    usage: dict[str, Any] = {}
    finish: str | None = None
    stop_sequence: str | None = None
    saw_tool = False

    def start(block: dict[str, Any]) -> dict[str, Any]:
        nonlocal current
        index = len(blocks)
        blocks.append(dict(block))
        opened.add(index)
        current = index if block["type"] in ("text", "thinking") else None
        return _event("content_block_start", index=index, content_block=block)

    def close(index: int) -> dict[str, Any]:
        if blocks[index]["type"] == "tool_use":
            raw = blocks[index].get("arguments", "")
            try:
                value = json.loads(raw) if raw else {}
            except ValueError:
                raise UpstreamStreamFailure("Upstream returned incomplete tool arguments.") from None
            if not isinstance(value, dict):
                raise UpstreamStreamFailure("Upstream tool arguments must be an object.")
        opened.remove(index)
        return _event("content_block_stop", index=index)

    def end_current() -> Iterator[dict[str, Any]]:
        nonlocal current
        if current is not None:
            if current in opened:
                yield close(current)
            current = None

    for chunk in chunks:
        kind = chunk.get("type")
        if kind == "error" or (kind == "finish" and chunk.get("reason") == "error"):
            raise UpstreamStreamFailure(str(chunk.get("error") or "Upstream stream failed."))
        if kind in ("text_delta", "reasoning_delta", "text_start", "reasoning_start"):
            target = "thinking" if kind in ("reasoning_delta", "reasoning_start") else "text"
            field = "thinking" if target == "thinking" else "text"
            if current is None or blocks[current]["type"] != target:
                yield from end_current()
                yield start({"type": target, field: "", **({"signature": ""} if target == "thinking" else {})})
            text = chunk.get("text", "")
            if text:
                assert current is not None
                blocks[current][field] += text
                yield _event("content_block_delta", index=current, delta={"type": "thinking_delta" if target == "thinking" else "text_delta", field: text})
        elif kind == "reasoning_details":
            opaque: list[dict[str, Any]] = []
            for detail in chunk.get("details") or []:
                if not isinstance(detail, dict):
                    continue
                if detail.get("type") == "redacted_thinking":
                    yield from end_current()
                    yield start(dict(detail))
                    yield close(len(blocks) - 1)
                elif detail.get("type") == "thinking":
                    text = str(detail.get("thinking") or "")
                    if current is None or blocks[current]["type"] != "thinking" or blocks[current]["thinking"] != text or blocks[current].get("signature"):
                        yield from end_current()
                        yield start({"type": "thinking", "thinking": "", "signature": ""})
                        assert current is not None
                        blocks[current]["thinking"] = text
                        if text:
                            yield _event("content_block_delta", index=current, delta={"type": "thinking_delta", "thinking": text})
                    signature = detail.get("signature", "")
                    assert current is not None
                    blocks[current]["signature"] += signature
                    yield _event("content_block_delta", index=current, delta={"type": "signature_delta", "signature": signature})
                else:
                    opaque.append(detail)
            if opaque:
                if current is None or blocks[current]["type"] != "thinking" or blocks[current].get("signature"):
                    yield from end_current()
                    yield start({"type": "thinking", "thinking": "", "signature": ""})
                signature = encode_details(opaque)
                assert current is not None
                blocks[current]["signature"] = signature
                yield _event("content_block_delta", index=current, delta={"type": "signature_delta", "signature": signature})
        elif kind == "block_stop":
            # Native parsers report a boundary after signatures, before the next block.
            yield from end_current()
        elif kind == "tool_call_start":
            yield from end_current()
            slot = chunk.get("index", 0)
            if slot in tools or not chunk.get("id") or not chunk.get("name"):
                raise UpstreamStreamFailure("Upstream returned an invalid tool call identity.")
            index = len(blocks)
            tools[slot] = index
            saw_tool = True
            yield start({"type": "tool_use", "id": chunk["id"], "name": chunk["name"], "input": {}})
            raw = chunk.get("arguments") or ""
            blocks[index]["arguments"] = raw
            if raw:
                yield _event("content_block_delta", index=index, delta={"type": "input_json_delta", "partial_json": raw})
        elif kind == "tool_call_delta":
            slot = chunk.get("index", 0)
            if slot not in tools:
                raise UpstreamStreamFailure("Upstream returned arguments for an unknown tool call.")
            index = tools[slot]
            raw = chunk.get("arguments") or ""
            blocks[index]["arguments"] += raw
            if raw:
                yield _event("content_block_delta", index=index, delta={"type": "input_json_delta", "partial_json": raw})
        elif kind == "usage":
            usage.update(chunk.get("usage") or {})
        elif kind == "finish":
            if finish is None:
                finish = {"length": "max_tokens", "tool_calls": "tool_use", "stop": "end_turn", "stop_sequence": "stop_sequence", "refusal": "refusal"}.get(
                    chunk.get("reason"), "end_turn"
                )
                stop_sequence = chunk.get("stop_sequence")
    if finish is None:
        raise UpstreamStreamFailure("Upstream stream ended without a completion marker.")
    yield from end_current()
    for index in sorted(opened):
        yield close(index)
    if saw_tool and finish == "end_turn":
        finish = "tool_use"
    yield _event("message_delta", delta={"stop_reason": finish, "stop_sequence": stop_sequence}, usage=messages_usage(usage))
    yield _event("message_stop")


def aggregate_messages(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Fold the same event stream the SDK consumes into a non-streaming Message."""
    message: dict[str, Any] = {}
    content: list[dict[str, Any]] = []
    arguments: dict[int, str] = {}
    for event in events:
        kind = event["type"]
        if kind == "message_start":
            message = event["message"]
        elif kind == "content_block_start":
            content.append(dict(event["content_block"]))
        elif kind == "content_block_delta":
            index = event["index"]
            delta = event["delta"]
            field = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature"}.get(delta["type"])
            if field:
                content[index][field] = content[index].get(field, "") + delta[field]
            elif delta["type"] == "input_json_delta":
                arguments[index] = arguments.get(index, "") + delta["partial_json"]
        elif kind == "content_block_stop" and event["index"] in arguments:
            content[event["index"]]["input"] = json.loads(arguments[event["index"]])
        elif kind == "message_delta":
            message.update(event["delta"])
            message["usage"].update(event["usage"])
    message["content"] = content
    return message
