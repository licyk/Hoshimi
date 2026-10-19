"""Normalized stream chunks and emitters for Chat Completions / Responses formats.

Every provider adapter translates its upstream protocol into a stream of
normalized chunks; the emitters below turn those chunks into the two public
wire formats (OpenAI Chat Completions and OpenAI Responses).

Normalized chunk shapes (dicts with a "type" key):

- {"type": "text_delta", "text": str}
- {"type": "reasoning_delta", "text": str}
- {"type": "block_stop"} (native text/thinking boundary; preserves signed blocks)
- {"type": "tool_call_start", "id": str, "name": str, "arguments": str, "index": int}
- {"type": "tool_call_delta", "index": int, "arguments": str}
- {"type": "usage", "usage": {prompt_tokens, completion_tokens, total_tokens, ...}}
- {"type": "finish", "reason": "stop" | "tool_calls" | "length" | "error"}
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any
from uuid import uuid4


class UpstreamStreamFailure(Exception):
    """Raised when the upstream stream reported an error mid-flight."""


def new_completion_id() -> str:
    return f"chatcmpl_{uuid4().hex}"


def new_response_id() -> str:
    return f"resp_{uuid4().hex}"


def empty_usage() -> dict[str, Any]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _normalize_finish_reason(reason: Any) -> str:
    if reason in ("stop", "tool_calls", "length"):
        return str(reason)
    return "stop"


# --------------------------------------------------------------------------- #
# Chat Completions emitter / aggregator
# --------------------------------------------------------------------------- #


def _chat_chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None,
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def emit_chat_chunks(
    chunks: Iterable[dict[str, Any]],
    *,
    model: str,
    completion_id: str,
    created: int,
) -> Iterator[dict[str, Any]]:
    """Translate normalized chunks into OpenAI chat.completion.chunk dicts."""
    yield _chat_chunk(completion_id, created, model, {"role": "assistant"}, None)

    finish_reason = "stop"
    saw_finish = False

    for chunk in chunks:
        kind = chunk.get("type")

        if kind == "text_delta":
            text = chunk.get("text")
            if isinstance(text, str) and text:
                yield _chat_chunk(completion_id, created, model, {"content": text}, None)

        elif kind == "reasoning_delta":
            text = chunk.get("text")
            if isinstance(text, str) and text:
                yield _chat_chunk(
                    completion_id, created, model, {"reasoning_content": text}, None
                )

        elif kind == "reasoning_details":
            yield _chat_chunk(completion_id, created, model, {"reasoning_details": chunk.get("details", [])}, None)

        elif kind == "tool_call_start":
            entry: dict[str, Any] = {
                "index": chunk.get("index", 0),
                "id": chunk.get("id"),
                "type": "function",
                "function": {
                    "name": chunk.get("name"),
                    "arguments": chunk.get("arguments") or "",
                },
            }
            yield _chat_chunk(completion_id, created, model, {"tool_calls": [entry]}, None)

        elif kind == "tool_call_delta":
            arguments = chunk.get("arguments")
            if isinstance(arguments, str) and arguments:
                entry = {"index": chunk.get("index", 0), "function": {"arguments": arguments}}
                yield _chat_chunk(completion_id, created, model, {"tool_calls": [entry]}, None)

        elif kind == "usage":
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                yield {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [],
                    "usage": usage,
                }

        elif kind == "finish":
            finish_reason = _normalize_finish_reason(chunk.get("reason"))
            saw_finish = True
            yield _chat_chunk(completion_id, created, model, {}, finish_reason)

        elif kind == "error":
            message = chunk.get("error") or "Upstream stream failed."
            yield {"error": {"message": str(message), "type": "upstream_error"}}

    if not saw_finish:
        yield _chat_chunk(completion_id, created, model, {}, "stop")


def aggregate_chat(
    chunks: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """Fold streaming chat chunks into a single message + finish reason + usage."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_details: list[dict[str, Any]] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason = "stop"
    usage: dict[str, Any] | None = None
    error_message: str | None = None

    for chunk in chunks:
        if isinstance(chunk.get("error"), dict):
            error_message = chunk["error"].get("message")
            continue
        if not chunk.get("choices"):
            if chunk.get("usage") is not None:
                usage = chunk["usage"]
            continue
        choice = chunk["choices"][0]
        delta = choice.get("delta") or {}
        if isinstance(delta.get("content"), str):
            content_parts.append(delta["content"])
        if isinstance(delta.get("reasoning_content"), str):
            reasoning_parts.append(delta["reasoning_content"])
        if isinstance(delta.get("reasoning_details"), list):
            reasoning_details.extend(delta["reasoning_details"])
        for entry in delta.get("tool_calls") or []:
            index = entry.get("index", 0)
            slot = tool_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
            if entry.get("id"):
                slot["id"] = entry["id"]
            fn = entry.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if isinstance(fn.get("arguments"), str):
                slot["arguments"] += fn["arguments"]
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

    if error_message:
        raise UpstreamStreamFailure(str(error_message))

    message: dict[str, Any] = {"role": "assistant"}
    text = "".join(content_parts)
    message["content"] = text if text else None
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if reasoning_details:
        message["reasoning_details"] = reasoning_details
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_calls[index]["id"] or f"call_{index}",
                "type": "function",
                "function": {
                    "name": tool_calls[index]["name"],
                    "arguments": tool_calls[index]["arguments"],
                },
            }
            for index in sorted(tool_calls)
        ]
    return message, finish_reason, usage


# --------------------------------------------------------------------------- #
# Responses emitter / aggregator
# --------------------------------------------------------------------------- #


def _responses_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    prompt = int((usage or {}).get("prompt_tokens") or 0)
    completion = int((usage or {}).get("completion_tokens") or 0)
    cached = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
    reasoning = ((usage or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": prompt + completion,
        "input_tokens_details": {"cached_tokens": int(cached or 0)},
        "output_tokens_details": {"reasoning_tokens": int(reasoning or 0)},
    }


def _base_response(response_id: str, created: int, model: str, status: str) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": status,
        "model": model,
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "metadata": {},
    }


def _fold_responses(
    chunks: Iterable[dict[str, Any]],
) -> tuple[list[str], list[str], dict[int, dict[str, Any]], dict[str, Any] | None, str, str | None, list[dict[str, Any]]]:
    """Fold normalized chunks into raw parts shared by both Responses paths."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_details: list[dict[str, Any]] = []
    tools: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None
    finish_reason = "stop"
    error_message: str | None = None

    for chunk in chunks:
        kind = chunk.get("type")
        if kind == "text_delta":
            text = chunk.get("text")
            if isinstance(text, str):
                content_parts.append(text)
        elif kind == "reasoning_delta":
            text = chunk.get("text")
            if isinstance(text, str):
                reasoning_parts.append(text)
        elif kind == "reasoning_details":
            reasoning_details.extend(chunk.get("details") or [])
        elif kind == "tool_call_start":
            slot = chunk.get("index", len(tools))
            tools[slot] = {
                "call_id": chunk.get("id") or f"call_{slot}",
                "name": chunk.get("name") or "",
                "arguments": chunk.get("arguments") or "",
            }
        elif kind == "tool_call_delta":
            slot = chunk.get("index", 0)
            entry = tools.get(slot)
            arguments = chunk.get("arguments") or ""
            if entry is not None and arguments:
                entry["arguments"] += arguments
        elif kind == "usage":
            value = chunk.get("usage")
            if isinstance(value, dict):
                usage = value
        elif kind == "finish":
            finish_reason = _normalize_finish_reason(chunk.get("reason"))
        elif kind == "error":
            error_message = str(chunk.get("error") or "Upstream stream failed.")

    return content_parts, reasoning_parts, tools, usage, finish_reason, error_message, reasoning_details


def _response_output(
    response_id: str,
    content_parts: list[str],
    reasoning_parts: list[str],
    tools: dict[int, dict[str, Any]],
    reasoning_details: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    native = [item for item in reasoning_details or [] if item.get("type") == "reasoning"]
    if native:
        output.extend(native)
    elif reasoning_parts or reasoning_details:
        output.append(
            {
                "type": "reasoning",
                "id": f"rs_{response_id[5:]}",
                "summary": [{"type": "summary_text", "text": "".join(reasoning_parts)}],
                **({"provider_details": reasoning_details} if reasoning_details else {}),
            }
        )
    if content_parts or not tools:
        output.append(
            {
                "type": "message",
                "id": f"msg_{response_id[5:]}",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "".join(content_parts), "annotations": []}
                ],
            }
        )
    for slot in sorted(tools):
        entry = tools[slot]
        output.append(
            {
                "type": "function_call",
                "id": entry["call_id"],
                "call_id": entry["call_id"],
                "name": entry["name"],
                "arguments": entry["arguments"],
                "status": "completed",
            }
        )
    return output


def emit_responses_events(
    chunks: Iterable[dict[str, Any]],
    *,
    model: str,
    response_id: str,
    created: int,
) -> Iterator[dict[str, Any]]:
    """Translate normalized chunks into OpenAI Responses SSE event dicts."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_details: list[dict[str, Any]] = []
    tools: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None

    sequence = 0

    def event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal sequence
        value = {"type": event_type, "sequence_number": sequence, **payload}
        sequence += 1
        return value

    msg_id = f"msg_{response_id[5:]}"
    rs_id = f"rs_{response_id[5:]}"

    yield event("response.created", {"response": _base_response(response_id, created, model, "in_progress")})
    yield event("response.in_progress", {"response": _base_response(response_id, created, model, "in_progress")})

    reasoning_index: int | None = None
    message_index: int | None = None
    next_index = 0
    tool_index_by_slot: dict[int, int] = {}

    for chunk in chunks:
        kind = chunk.get("type")

        if kind == "reasoning_details":
            reasoning_details.extend(chunk.get("details") or [])
        elif kind == "reasoning_delta":
            text = chunk.get("text")
            if not isinstance(text, str) or not text:
                continue
            if reasoning_index is None:
                reasoning_index = next_index
                next_index += 1
                yield event(
                    "response.output_item.added",
                    {
                        "output_index": reasoning_index,
                        "item": {"type": "reasoning", "id": rs_id, "summary": []},
                    },
                )
            reasoning_parts.append(text)
            yield event(
                "response.reasoning_summary_text.delta",
                {"item_id": rs_id, "output_index": reasoning_index, "summary_index": 0, "delta": text},
            )

        elif kind == "text_delta":
            text = chunk.get("text")
            if not isinstance(text, str) or not text:
                continue
            if message_index is None:
                message_index = next_index
                next_index += 1
                yield event(
                    "response.output_item.added",
                    {
                        "output_index": message_index,
                        "item": {
                            "type": "message",
                            "id": msg_id,
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [],
                        },
                    },
                )
            content_parts.append(text)
            yield event(
                "response.output_text.delta",
                {"item_id": msg_id, "output_index": message_index, "content_index": 0, "delta": text},
            )

        elif kind == "tool_call_start":
            slot = chunk.get("index", len(tool_index_by_slot))
            call_id = chunk.get("id") or f"call_{slot}"
            name = chunk.get("name") or ""
            arguments = chunk.get("arguments") or ""
            tools[slot] = {"call_id": call_id, "name": name, "arguments": arguments}
            tool_index_by_slot[slot] = next_index
            yield event(
                "response.output_item.added",
                {
                    "output_index": next_index,
                    "item": {
                        "type": "function_call",
                        "id": call_id,
                        "call_id": call_id,
                        "name": name,
                        "arguments": "",
                        "status": "in_progress",
                    },
                },
            )
            next_index += 1
            if arguments:
                yield event(
                    "response.function_call_arguments.delta",
                    {"item_id": call_id, "output_index": tool_index_by_slot[slot], "delta": arguments},
                )

        elif kind == "tool_call_delta":
            slot = chunk.get("index", 0)
            entry = tools.get(slot)
            arguments = chunk.get("arguments") or ""
            if entry is None or not arguments:
                continue
            entry["arguments"] += arguments
            yield event(
                "response.function_call_arguments.delta",
                {
                    "item_id": entry["call_id"],
                    "output_index": tool_index_by_slot.get(slot, 0),
                    "delta": arguments,
                },
            )

        elif kind == "usage":
            value = chunk.get("usage")
            if isinstance(value, dict):
                usage = value

        elif kind == "finish":
            pass

        elif kind == "error":
            message = chunk.get("error") or "Upstream stream failed."
            yield event(
                "response.failed",
                {
                    "response": {
                        **_base_response(response_id, created, model, "failed"),
                        "error": {"code": "upstream_error", "message": str(message)},
                    }
                },
            )
            return

    if reasoning_index is not None:
        summary = "".join(reasoning_parts)
        yield event(
            "response.reasoning_summary_text.done",
            {"item_id": rs_id, "output_index": reasoning_index, "summary_index": 0, "text": summary},
        )
        yield event(
            "response.output_item.done",
            {
                "output_index": reasoning_index,
                "item": {
                    "type": "reasoning",
                    "id": rs_id,
                    "summary": [{"type": "summary_text", "text": summary}],
                },
            },
        )

    if message_index is not None:
        text = "".join(content_parts)
        yield event(
            "response.output_text.done",
            {"item_id": msg_id, "output_index": message_index, "content_index": 0, "text": text},
        )
        yield event(
            "response.output_item.done",
            {
                "output_index": message_index,
                "item": {
                    "type": "message",
                    "id": msg_id,
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                },
            },
        )

    for slot in sorted(tools):
        entry = tools[slot]
        index = tool_index_by_slot.get(slot, 0)
        yield event(
            "response.function_call_arguments.done",
            {"item_id": entry["call_id"], "output_index": index, "arguments": entry["arguments"]},
        )
        yield event(
            "response.output_item.done",
            {
                "output_index": index,
                "item": {
                    "type": "function_call",
                    "id": entry["call_id"],
                    "call_id": entry["call_id"],
                    "name": entry["name"],
                    "arguments": entry["arguments"],
                    "status": "completed",
                },
            },
        )

    output = _response_output(response_id, content_parts, reasoning_parts, tools, reasoning_details)
    final = {
        **_base_response(response_id, created, model, "completed"),
        "output": output,
        "usage": _responses_usage(usage),
    }
    yield event("response.completed", {"response": final})


def aggregate_responses(
    chunks: Iterable[dict[str, Any]],
    *,
    model: str,
    response_id: str,
    created: int,
) -> dict[str, Any]:
    """Fold normalized chunks into a single Responses object."""
    content_parts, reasoning_parts, tools, usage, _finish, error_message, reasoning_details = _fold_responses(chunks)
    if error_message:
        raise UpstreamStreamFailure(error_message)
    return {
        **_base_response(response_id, created, model, "completed"),
        "output": _response_output(response_id, content_parts, reasoning_parts, tools, reasoning_details),
        "usage": _responses_usage(usage),
    }
