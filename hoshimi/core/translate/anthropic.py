"""OpenAI Chat <-> Anthropic Messages translation (for MiniMax / ZCode).

Ported from dsh-our-free-model's minimax-messages.ts / zcode-anthropic.ts:
- system goes to the top-level `system` field
- tools use the flat `input_schema` shape
- assistant tool_calls become `tool_use` content blocks (input is an object)
- role:"tool" messages become user messages with `tool_result` blocks
- SSE is Anthropic-style (message_start / content_block_* / message_delta /
  message_stop, no data:[DONE]); thinking deltas map to reasoning,
  signatures and cache usage survive normalization and history replay.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from copy import deepcopy
from typing import Any

from hoshimi.core.sse import iter_sse
from hoshimi.core.translate.chat import (
    parse_tool_arguments,
    split_system_messages,
    tool_output_text,
)


def _parse_data_url(url: str) -> tuple[str, str] | None:
    if not url.startswith("data:"):
        return None
    try:
        header, data = url.split(",", 1)
    except ValueError:
        return None
    media = header[len("data:"):].split(";")[0] or "image/png"
    return media, data


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    out = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") in ("text", "input_text") and isinstance(part.get("text"), str):
            block = {"type": "text", "text": part["text"]}
        elif part.get("type") == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str):
                continue
            parsed = _parse_data_url(url)
            if parsed:
                media, data = parsed
                block = {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
            elif url.startswith(("https://", "http://")):
                block = {"type": "image", "source": {"type": "url", "url": url}}
            else:
                continue
        else:
            continue
        if isinstance(part.get("cache_control"), dict):
            block["cache_control"] = deepcopy(part["cache_control"])
        out.append(block)
    return out


def build_anthropic_body(
    body: dict[str, Any],
    *,
    model: str,
    default_max_tokens: int = 8192,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an Anthropic Messages request body from an OpenAI chat body."""
    messages = body.get("messages") or []
    system, rest = split_system_messages(messages)

    out_messages: list[dict[str, Any]] = []
    pending_assistant: dict[str, Any] | None = None
    pending_results: list[dict[str, Any]] = []

    def commit_pending() -> None:
        nonlocal pending_assistant, pending_results
        if pending_assistant is not None:
            out_messages.append(pending_assistant)
            pending_assistant = None
        if pending_results:
            out_messages.append({"role": "user", "content": pending_results})
            pending_results = []

    for message in rest:
        role = message.get("role")
        content = message.get("content")

        if role == "tool":
            call_id = message.get("tool_call_id")
            if not call_id:
                continue
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": _tool_result_content(content),
            }
            if message.get("is_error") is True:
                block["is_error"] = True
            pending_results.append(block)
            continue

        blocks: list[dict[str, Any]] = []
        if role == "assistant":
            signed = message.get("reasoning_details")
            if isinstance(signed, list):
                blocks.extend(block for block in signed if isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"))
            blocks.extend(_content_blocks(content))
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                fn = tool_call.get("function") or {}
                call_id = tool_call.get("id")
                name = fn.get("name")
                if not call_id or not name:
                    continue
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": parse_tool_arguments(fn.get("arguments")),
                    }
                )
            commit_pending()
            if blocks:
                pending_assistant = {"role": "assistant", "content": blocks}
        elif role == "user":
            blocks = _content_blocks(content)
            commit_pending()
            if blocks:
                out_messages.append({"role": "user", "content": blocks})

    commit_pending()

    payload: dict[str, Any] = {
        "model": model,
        "stream": True,
        "messages": out_messages,
        "max_tokens": int(body.get("max_tokens") or body.get("max_completion_tokens") or default_max_tokens),
    }
    if system:
        payload["system"] = system
        system_blocks = [part for message in messages if message.get("role") in ("system", "developer") for part in _content_blocks(message.get("content"))]
        if any("cache_control" in part for part in system_blocks):
            payload["system"] = system_blocks
    for key in ("thinking", "output_config", "metadata", "top_k"):
        if key in body:
            payload[key] = deepcopy(body[key])
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        payload["top_p"] = body["top_p"]
    stop = body.get("stop")
    if isinstance(stop, str) and stop:
        payload["stop_sequences"] = [stop]
    elif isinstance(stop, list) and stop:
        payload["stop_sequences"] = stop

    tools_in = body.get("tools") or []
    tools: list[dict[str, Any]] = []
    for tool in tools_in:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function") or {}
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        tools.append(
            {
                "name": name,
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
        if isinstance(fn.get("strict"), bool):
            tools[-1]["strict"] = fn["strict"]
        if isinstance(tool.get("cache_control"), dict):
            tools[-1]["cache_control"] = deepcopy(tool["cache_control"])
    if tools:
        payload["tools"] = tools

    tool_choice = body.get("tool_choice")
    if tool_choice == "auto":
        payload["tool_choice"] = {"type": "auto"}
    elif tool_choice == "none":
        payload["tool_choice"] = {"type": "none"}
    elif tool_choice == "required":
        payload["tool_choice"] = {"type": "any"}
    elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        name = (tool_choice.get("function") or {}).get("name")
        if isinstance(name, str) and name:
            payload["tool_choice"] = {"type": "tool", "name": name}
    if isinstance(body.get("parallel_tool_calls"), bool):
        payload.setdefault("tool_choice", {"type": "auto"})["disable_parallel_tool_use"] = not body["parallel_tool_calls"]

    if extra:
        payload.update(extra)
    return payload


def _tool_result_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        converted = _content_blocks(content)
        if converted:
            return converted
        out: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image_url":
                url = (block.get("image_url") or {}).get("url")
                parsed = _parse_data_url(url) if isinstance(url, str) else None
                if parsed:
                    media, data = parsed
                    out.append(
                        {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
                    )
            elif isinstance(block.get("text"), str):
                out.append({"type": "text", "text": block["text"]})
        if out:
            return out
    return tool_output_text(content)


def iter_anthropic_chunks(
    lines: Iterable[str],
    *,
    supports_thinking: bool = True,
    provider: str = "upstream",
    retry_after: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Consume Anthropic Messages SSE and yield normalized chunks."""
    blocks: dict[int, dict[str, Any]] = {}
    usage_input = 0
    usage_output = 0
    cache_read = 0
    cache_creation = 0
    stop_reason: Any = None
    stop_sequence: Any = None
    saw_any = False
    saw_stop = False

    for event_name, data in iter_sse(lines):
        if not data or data == "[DONE]":
            continue
        try:
            frame = json.loads(data)
        except ValueError:
            continue
        if not isinstance(frame, dict):
            continue
        kind = frame.get("type") or event_name

        if kind == "error":
            from hoshimi.core.providers.refusals import raise_provider_refusal

            raise_provider_refusal(provider, frame, retry_after=retry_after)
            error = frame.get("error")
            message = (
                error.get("message")
                if isinstance(error, dict) and isinstance(error.get("message"), str)
                else "Upstream stream failed."
            )
            yield {"type": "error", "error": message}
            return

        if kind == "message_start":
            message = frame.get("message")
            if isinstance(message, dict):
                usage = message.get("usage") or {}
                usage_input = _nonneg(usage.get("input_tokens"))
                usage_output = _nonneg(usage.get("output_tokens"))
                cache_read = _nonneg(usage.get("cache_read_input_tokens"))
                cache_creation = _nonneg(usage.get("cache_creation_input_tokens"))
            continue

        if kind == "content_block_start":
            index = int(frame.get("index") or 0)
            block = frame.get("content_block")
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                blocks[index] = {"kind": "reasoning", "text": ""}
                saw_any = True
                if block.get("thinking"):
                    blocks[index]["text"] = block["thinking"]
                    yield {"type": "reasoning_delta", "text": block["thinking"]}
                if block.get("signature"):
                    blocks[index]["signature"] = block["signature"]
            elif btype == "redacted_thinking":
                saw_any = True
                yield {"type": "reasoning_details", "details": [deepcopy(block)]}
            elif btype == "text":
                blocks[index] = {"kind": "text", "text": ""}
                saw_any = True
                if block.get("text"):
                    blocks[index]["text"] = block["text"]
                    yield {"type": "text_delta", "text": block["text"]}
            elif btype == "tool_use":
                blocks[index] = {
                    "kind": "tool",
                    "id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "arguments": "",
                }
                saw_any = True
                yield {
                    "type": "tool_call_start",
                    "id": blocks[index]["id"],
                    "name": blocks[index]["name"],
                    "arguments": "",
                    "index": index,
                }
                if isinstance(block.get("input"), dict) and block["input"]:
                    initial = json.dumps(block["input"], ensure_ascii=False)
                    blocks[index]["arguments"] = initial
                    yield {"type": "tool_call_delta", "index": index, "arguments": initial}
            continue

        if kind == "content_block_delta":
            index = int(frame.get("index") or 0)
            delta = frame.get("delta")
            if not isinstance(delta, dict):
                continue
            state = blocks.get(index)
            dtype = delta.get("type")
            if dtype == "text_delta" and isinstance(delta.get("text"), str):
                if state is not None:
                    state["text"] += delta["text"]
                yield {"type": "text_delta", "text": delta["text"]}
            elif dtype == "thinking_delta" and isinstance(delta.get("thinking"), str):
                if supports_thinking:
                    if state is not None:
                        state["text"] += delta["thinking"]
                    yield {"type": "reasoning_delta", "text": delta["thinking"]}
            elif dtype == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                if state is not None:
                    state["arguments"] += delta["partial_json"]
                yield {"type": "tool_call_delta", "index": index, "arguments": delta["partial_json"]}
            elif dtype == "signature_delta" and isinstance(delta.get("signature"), str) and state is not None:
                state["signature"] = state.get("signature", "") + delta["signature"]
            continue

        if kind == "content_block_stop":
            index = int(frame.get("index") or 0)
            state = blocks.pop(index, None)
            if state is not None and state["kind"] == "reasoning" and state.get("signature"):
                yield {"type": "reasoning_details", "details": [{"type": "thinking", "thinking": state["text"], "signature": state["signature"]}]}
            if state is not None and state["kind"] == "tool":
                yield {"type": "tool_call_delta", "index": index, "arguments": ""}
            if state is not None and state["kind"] in ("reasoning", "text"):
                yield {"type": "block_stop"}
            continue

        if kind == "message_delta":
            delta = frame.get("delta")
            if isinstance(delta, dict) and delta.get("stop_reason") is not None:
                stop_reason = delta.get("stop_reason")
                stop_sequence = delta.get("stop_sequence")
            usage = frame.get("usage")
            if isinstance(usage, dict):
                usage_output = _nonneg(usage.get("output_tokens", usage_output))
                usage_input = _nonneg(usage.get("input_tokens", usage_input))
                cache_read = _nonneg(usage.get("cache_read_input_tokens", cache_read))
                cache_creation = _nonneg(usage.get("cache_creation_input_tokens", cache_creation))
            continue

        if kind == "message_stop":
            saw_stop = True
            break

    if not saw_any:
        yield {"type": "error", "error": "Upstream returned no content blocks."}
        return
    if not saw_stop and stop_reason is None:
        yield {"type": "error", "error": "Upstream Messages stream ended without a completion marker."}
        return

    yield {
        "type": "usage",
        "usage": {
            "prompt_tokens": usage_input + cache_read + cache_creation,
            "completion_tokens": usage_output,
            "total_tokens": usage_input + cache_read + cache_creation + usage_output,
            **({"prompt_tokens_details": {"cached_tokens": cache_read, "cache_write_tokens": cache_creation}} if cache_read or cache_creation else {}),
        },
    }
    if stop_reason == "tool_use":
        yield {"type": "finish", "reason": "tool_calls"}
    elif stop_reason == "max_tokens":
        yield {"type": "finish", "reason": "length"}
    elif stop_reason == "stop_sequence":
        yield {"type": "finish", "reason": "stop_sequence", "stop_sequence": stop_sequence}
    elif stop_reason == "refusal":
        yield {"type": "finish", "reason": "refusal"}
    else:
        yield {"type": "finish", "reason": "stop"}


def _nonneg(value: Any) -> int:
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return 0
