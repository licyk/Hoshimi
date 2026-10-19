"""Translation between the OpenAI Responses API and internal Chat Completions."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any, TypeGuard

from hoshimi.core.sse import iter_sse
from hoshimi.core.translate.chat import convert_tools, text_from_content


class ResponsesRequestError(ValueError):
    """Raised for Responses parameters this stateless gateway cannot honor."""


def _is_object(value: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict)


def _reject(condition: bool, message: str) -> None:
    if condition:
        raise ResponsesRequestError(message)


def responses_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI Responses request body into a Chat Completions body."""
    _reject(
        isinstance(body.get("previous_response_id"), str)
        or bool(body.get("previous_response_id")),
        "This stateless endpoint does not support `previous_response_id`. "
        "Send the full conversation in `input` each time.",
    )
    text = body.get("text")
    if _is_object(text):
        fmt = text.get("format")
        if _is_object(fmt) and fmt.get("type") not in (None, "text"):
            raise ResponsesRequestError(
                "Structured output (`text.format`) is not supported by this stateless gateway."
            )

    instructions = body.get("instructions")
    messages: list[dict[str, Any]] = []
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})

    items = body.get("input")
    if isinstance(items, str):
        messages.append({"role": "user", "content": items})
    elif isinstance(items, list):
        messages.extend(_convert_input_items(items))

    chat: dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
        "stream": body.get("stream") is True,
    }

    if body.get("temperature") is not None:
        chat["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        chat["top_p"] = body["top_p"]
    max_output_tokens = body.get("max_output_tokens")
    if max_output_tokens is not None:
        chat["max_tokens"] = max_output_tokens

    tools = _convert_responses_tools(body.get("tools"))
    if tools:
        chat["tools"] = tools
    tool_choice = body.get("tool_choice")
    if tool_choice in ("auto", "none", "required"):
        chat["tool_choice"] = tool_choice
    elif _is_object(tool_choice) and tool_choice.get("type") == "function":
        nested = tool_choice.get("function")
        name = tool_choice.get("name") or (nested.get("name") if _is_object(nested) else None)
        if isinstance(name, str) and name:
            chat["tool_choice"] = {"type": "function", "function": {"name": name}}

    reasoning = body.get("reasoning")
    if _is_object(reasoning) and isinstance(reasoning.get("effort"), str):
        chat["reasoning_effort"] = reasoning["effort"]

    return chat


def _convert_input_items(items: list[Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in items:
        if not _is_object(item):
            continue
        kind = item.get("type")
        if kind == "reasoning":
            details = item.get("provider_details")
            messages.append({"role": "assistant", "content": "", "reasoning_details": details if isinstance(details, list) else [item]})
        elif kind in (None, "message") or (kind is None and item.get("role")):
            role = item.get("role")
            if role not in ("user", "assistant", "system", "developer"):
                continue
            content = item.get("content")
            if role in ("system", "developer"):
                messages.append({"role": "system", "content": _message_text(content)})
            elif role == "assistant":
                messages.append(
                    {
                        "role": "assistant",
                        "content": _message_text(content),
                        **(
                            {"tool_calls": item["tool_calls"]}
                            if isinstance(item.get("tool_calls"), list)
                            else {}
                        ),
                    }
                )
            else:
                messages.append({"role": "user", "content": _user_content(content)})
        elif kind == "function_call":
            call_id = item.get("call_id") or item.get("id")
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": item.get("name"),
                                "arguments": item.get("arguments") or "{}",
                            },
                        }
                    ],
                }
            )
        elif kind == "function_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id"),
                    "content": item.get("output") if isinstance(item.get("output"), str)
                    else text_from_content(item.get("output")),
                }
            )
    return messages


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if _is_object(block) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _user_content(content: Any) -> Any:
    """Map Responses input content blocks back to chat content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for block in content:
            if not _is_object(block):
                continue
            btype = block.get("type")
            if btype in ("input_text", "output_text", "text") and isinstance(block.get("text"), str):
                parts.append({"type": "text", "text": block["text"]})
            elif btype == "input_image":
                url = block.get("image_url")
                if isinstance(url, str):
                    parts.append({"type": "image_url", "image_url": {"url": url}})
        return parts or ""
    return content


def _convert_responses_tools(tools: Any) -> list[dict[str, Any]]:
    """Accept function tools (incl. flattened namespace tools); drop the rest."""
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        if not _is_object(tool):
            continue
        if tool.get("type") != "function":
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        entry = {
            "type": "function",
            "function": {
                "name": name,
                "parameters": tool.get("parameters")
                or {"type": "object", "properties": {}},
            },
        }
        if isinstance(tool.get("description"), str):
            entry["function"]["description"] = tool["description"]
        out.append(entry)
    # Normalize via the shared converter (drops malformed entries).
    return convert_tools(out)


# --------------------------------------------------------------------------- #
# Chat -> Responses (for stations that serve the Responses wire)
# --------------------------------------------------------------------------- #


def chat_to_responses(body: dict[str, Any], *, model: str) -> dict[str, Any]:
    """Convert an internal Chat Completions body into a Responses request.

    The upstream this gateway speaks to is stateless: every turn carries the
    whole conversation (`input`), the request never stores anything, and the
    upstream always streams (the caller's `stream` flag is served by folding
    that stream locally).
    """
    items = _input_items(body.get("messages") or [])
    if not items:
        items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "..."}]}]

    out: dict[str, Any] = {
        "model": model,
        "input": items,
        "stream": True,
        "store": False,
    }

    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        out["max_output_tokens"] = max_tokens
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]

    tools = _flat_tools(body.get("tools"))
    if tools:
        out["tools"] = tools
    tool_choice = body.get("tool_choice")
    if tool_choice in ("auto", "none", "required"):
        out["tool_choice"] = tool_choice
    elif _is_object(tool_choice) and tool_choice.get("type") == "function":
        nested = tool_choice.get("function")
        name = tool_choice.get("name") or (nested.get("name") if _is_object(nested) else None)
        if isinstance(name, str) and name:
            out["tool_choice"] = {"type": "function", "name": name}
    return out


def _input_items(messages: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if not _is_object(message):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in ("system", "developer"):
            text = text_from_content(content)
            if text:
                items.append({"type": "message", "role": "system", "content": [{"type": "input_text", "text": text}]})
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                items.append(
                    {"type": "function_call_output", "call_id": call_id, "output": text_from_content(content)}
                )
            continue
        if role == "assistant":
            details = message.get("reasoning_details")
            if isinstance(details, list):
                items.extend(item for item in details if _is_object(item) and item.get("type") == "reasoning")
            text = text_from_content(content)
            if text:
                items.append(
                    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
                )
            for call in message.get("tool_calls") or []:
                if not _is_object(call):
                    continue
                raw_function = call.get("function")
                fn = raw_function if _is_object(raw_function) else {}
                name = fn.get("name") if _is_object(fn) else None
                if not isinstance(name, str) or not name:
                    continue
                call_id = call.get("id")
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id if isinstance(call_id, str) and call_id else f"call_{len(items)}",
                        "name": name,
                        "arguments": fn.get("arguments") or "{}",
                    }
                )
            continue
        parts: list[dict[str, Any]] = []
        for part in _content_parts(content):
            if _is_object(part) and part.get("type") in ("text", "input_text"):
                if isinstance(part.get("text"), str):
                    parts.append({"type": "input_text", "text": part["text"]})
            elif _is_object(part) and part.get("type") in ("image_url", "input_image"):
                url = part.get("image_url")
                if _is_object(url):
                    url = url.get("url")
                if isinstance(url, str) and url:
                    parts.append({"type": "input_image", "image_url": url})
        if parts:
            items.append({"type": "message", "role": "user", "content": parts})
    return items


def _content_parts(content: Any) -> list[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return content
    return []


def _flat_tools(tools: Any) -> list[dict[str, Any]]:
    """Function tools in the flat Responses shape (`{type, name, parameters}`)."""
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        if not _is_object(tool):
            continue
        if tool.get("type") != "function":
            continue
        nested = tool.get("function") if _is_object(tool.get("function")) else None
        name = tool.get("name") or (nested.get("name") if _is_object(nested) else None)
        if not isinstance(name, str) or not name:
            continue
        parameters = tool.get("parameters") or (nested.get("parameters") if _is_object(nested) else None)
        entry: dict[str, Any] = {
            "type": "function",
            "name": name,
            "parameters": parameters or {"type": "object", "properties": {}},
        }
        description = tool.get("description") or (nested.get("description") if _is_object(nested) else None)
        if isinstance(description, str) and description:
            entry["description"] = description
        out.append(entry)
    return out

# --------------------------------------------------------------------------- #
# Responses SSE -> normalized chunks
# --------------------------------------------------------------------------- #


def iter_responses_chunks(
    lines: Iterable[str],
    *,
    provider: str = "upstream",
) -> Iterator[dict[str, Any]]:
    """Consume an OpenAI Responses SSE stream into normalized chunks."""
    tool_calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None
    status: str | None = None
    incomplete_reason: str | None = None
    error_message: str | None = None
    saw_content = False
    saw_terminal = False

    for _event_name, data in iter_sse(lines):
        if not data or data == "[DONE]":
            continue
        try:
            frame = json.loads(data)
        except ValueError:
            continue
        if not isinstance(frame, dict):
            continue
        kind = frame.get("type")

        if kind == "error" or (frame.get("error") is not None and kind != "response.failed"):
            error = frame.get("error") or frame
            message = (
                error.get("message")
                if _is_object(error) and isinstance(error.get("message"), str)
                else str(error)
            )
            error_message = message or "upstream error"
            continue

        if kind == "response.output_text.delta":
            text = frame.get("delta")
            if isinstance(text, str) and text:
                saw_content = True
                yield {"type": "text_delta", "text": text}
        elif kind in ("response.reasoning_summary_text.delta", "response.output_reasoning.text.delta"):
            text = frame.get("delta")
            if isinstance(text, str) and text:
                saw_content = True
                yield {"type": "reasoning_delta", "text": text}
        elif kind == "response.output_item.added":
            item = frame.get("item")
            if _is_object(item) and item.get("type") == "function_call":
                index = _nonneg(frame.get("output_index"))
                slot = tool_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
                call_id = item.get("call_id") or item.get("id")
                if isinstance(call_id, str) and call_id:
                    slot["id"] = call_id
                if isinstance(item.get("name"), str) and item["name"]:
                    slot["name"] = item["name"]
        elif kind == "response.function_call_arguments.delta":
            arguments = frame.get("delta")
            if isinstance(arguments, str) and arguments:
                index = _nonneg(frame.get("output_index"))
                slot = tool_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
                slot["arguments"] += arguments
        elif kind == "response.function_call_arguments.done":
            index = _nonneg(frame.get("output_index"))
            slot = tool_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
            arguments = frame.get("arguments")
            if isinstance(arguments, str) and not slot["arguments"]:
                slot["arguments"] = arguments
            if isinstance(frame.get("name"), str) and frame["name"] and not slot["name"]:
                slot["name"] = frame["name"]
        elif kind == "response.output_item.done":
            item = frame.get("item")
            if _is_object(item) and item.get("type") == "reasoning":
                yield {"type": "reasoning_details", "details": [item]}
        elif kind in ("response.completed", "response.incomplete", "response.failed", "response.done"):
            saw_terminal = True
            response = frame.get("response")
            if _is_object(response):
                raw_usage = response.get("usage")
                if _is_object(raw_usage):
                    usage = _responses_usage(raw_usage)
                if isinstance(response.get("status"), str):
                    status = response["status"]
                details = response.get("incomplete_details")
                if _is_object(details) and isinstance(details.get("reason"), str):
                    incomplete_reason = details["reason"]
                failure = response.get("error")
                if _is_object(failure) and isinstance(failure.get("message"), str):
                    error_message = failure["message"]
            if kind == "response.failed" and not error_message:
                error_message = "upstream reported a failed response"

    if error_message and not saw_content and not tool_calls:
        yield {"type": "error", "error": error_message}
        return

    for index in sorted(tool_calls):
        slot = tool_calls[index]
        if not slot["name"] and not slot["arguments"]:
            continue
        yield {
            "type": "tool_call_start",
            "id": slot["id"] or f"call_{index}",
            "name": slot["name"] or "",
            "arguments": slot["arguments"],
            "index": index,
        }

    if usage is not None:
        yield {"type": "usage", "usage": usage}

    if not saw_content and not tool_calls and not saw_terminal:
        yield {"type": "error", "error": "Upstream returned no content."}
        return

    if status == "failed":
        yield {"type": "error", "error": error_message or "upstream reported a failed response"}
        yield {"type": "finish", "reason": "stop"}
    elif incomplete_reason == "max_output_tokens":
        yield {"type": "finish", "reason": "length"}
    elif tool_calls:
        yield {"type": "finish", "reason": "tool_calls"}
    else:
        yield {"type": "finish", "reason": "stop"}


def _responses_usage(raw: dict[str, Any]) -> dict[str, Any]:
    prompt = _nonneg(raw.get("input_tokens"))
    completion = _nonneg(raw.get("output_tokens"))
    total = _nonneg(raw.get("total_tokens")) or (prompt + completion)
    out: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    details = raw.get("input_tokens_details")
    if _is_object(details) and details.get("cached_tokens") is not None:
        out["prompt_tokens_details"] = {"cached_tokens": _nonneg(details.get("cached_tokens"))}
    details = raw.get("output_tokens_details")
    if _is_object(details) and details.get("reasoning_tokens") is not None:
        out["completion_tokens_details"] = {"reasoning_tokens": _nonneg(details.get("reasoning_tokens"))}
    return out


def _nonneg(value: Any) -> int:
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return 0
