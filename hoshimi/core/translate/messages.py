"""Validate Anthropic Messages input and translate it into the shared chat format."""

from __future__ import annotations

import base64
import binascii
import json
from copy import deepcopy
from typing import Any

from hoshimi.core.errors import ValidationError

SIGNATURE_PREFIX = "hoshimi:v1:"


def encode_details(details: list[dict[str, Any]]) -> str:
    """Carry opaque non-Anthropic reasoning through an agent's signature replay."""
    return SIGNATURE_PREFIX + base64.urlsafe_b64encode(json.dumps(details, ensure_ascii=False).encode()).decode()


def decode_details(signature: str) -> list[dict[str, Any]] | None:
    if not signature.startswith(SIGNATURE_PREFIX):
        return None
    if len(signature) > 1_048_576:
        raise ValidationError("Thinking signature is too large.")
    try:
        value = json.loads(base64.b64decode(signature[len(SIGNATURE_PREFIX) :], altchars=b"-_", validate=True))
    except (ValueError, binascii.Error):
        raise ValidationError("Invalid Hoshimi thinking signature.") from None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValidationError("Invalid Hoshimi thinking signature.")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _text(value: Any, field: str) -> str:
    _require(isinstance(value, str), f"{field} must be a string.")
    return value


def _parts(content: Any, *, native: bool = False) -> list[dict[str, Any]]:
    """Convert text/images without fetching user-supplied URLs."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    _require(isinstance(content, list), "Message content must be a string or an array.")
    result = []
    for block in content:
        _require(isinstance(block, dict), "Content blocks must be objects.")
        kind = block.get("type")
        if kind == "text":
            part = {"type": "text", "text": _text(block.get("text"), "text")}
            if native and isinstance(block.get("cache_control"), dict):
                part["cache_control"] = deepcopy(block["cache_control"])
            result.append(part)
        elif kind == "image":
            source = block.get("source")
            _require(isinstance(source, dict), "Image source must be an object.")
            if source.get("type") == "base64":
                media = _text(source.get("media_type"), "media_type")
                data = _text(source.get("data"), "image data")
                _require(media in ("image/png", "image/jpeg", "image/gif", "image/webp"), "Unsupported image media type.")
                url = f"data:{media};base64,{data}"
            elif source.get("type") == "url":
                url = _text(source.get("url"), "image URL")
                _require(url.startswith(("https://", "http://")), "Image URLs must use HTTP(S).")
            else:
                raise ValidationError("Unsupported image source.")
            result.append({"type": "image_url", "image_url": {"url": url}})
        else:
            raise ValidationError(f"Unsupported content block: {kind!r}.")
    return result


def _message(message: Any, *, native: bool) -> list[dict[str, Any]]:
    _require(isinstance(message, dict), "Messages must be objects.")
    role = message.get("role")
    _require(role in ("user", "assistant"), "Messages roles must be user or assistant; use top-level system.")
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    _require(isinstance(content, list), "Message content must be a string or an array.")
    parts: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    thinking: list[str] = []
    for block in content:
        _require(isinstance(block, dict), "Content blocks must be objects.")
        kind = block.get("type")
        if kind == "tool_use":
            _require(role == "assistant", "tool_use belongs in assistant messages.")
            call_id = _text(block.get("id"), "tool_use.id")
            name = _text(block.get("name"), "tool_use.name")
            _require(bool(call_id and name) and isinstance(block.get("input"), dict), "tool_use requires id, name and object input.")
            tools.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(block["input"], ensure_ascii=False)}})
        elif kind == "tool_result":
            _require(role == "user", "tool_result belongs in user messages.")
            _require(not parts, "tool_result blocks must precede user text and images.")
            call_id = _text(block.get("tool_use_id"), "tool_result.tool_use_id")
            _require(bool(call_id), "tool_result requires tool_use_id.")
            value = block.get("content", "")
            result: dict[str, Any] = {"role": "tool", "tool_call_id": call_id, "content": value if isinstance(value, str) else _parts(value, native=native)}
            if "is_error" in block:
                _require(isinstance(block["is_error"], bool), "is_error must be a boolean.")
                if native:
                    result["is_error"] = block["is_error"]
                elif block["is_error"]:
                    result["content"] = [{"type": "text", "text": "Tool execution failed."}, *_parts(value)]
            results.append(result)
        elif kind in ("thinking", "redacted_thinking"):
            _require(role == "assistant", "Thinking belongs in assistant messages.")
            if kind == "thinking":
                text = _text(block.get("thinking"), "thinking")
                signature = _text(block.get("signature", ""), "signature")
                thinking.append(text)
                decoded = decode_details(signature)
                if decoded is not None:
                    details.extend(decoded)
                elif signature:
                    details.append(deepcopy(block))
            else:
                _text(block.get("data"), "redacted_thinking.data")
                details.append(deepcopy(block))
        else:
            parts.extend(_parts([block], native=native))
    out: dict[str, Any] = {"role": role, "content": parts or None}
    if tools:
        out["tool_calls"] = tools
    if details:
        out["reasoning_details"] = details
    if thinking:
        out["reasoning_content"] = "".join(thinking)
    if parts or tools or details or thinking:
        results.append(out)
    return results


def messages_to_chat(body: dict[str, Any], *, native: bool = False, count_only: bool = False) -> dict[str, Any]:
    """Keep upstream model IDs and tool identities; reject unsupported agent features."""
    _require(isinstance(body.get("model"), str) and bool(body["model"].strip()), "model is required.")
    maximum = body.get("max_tokens")
    if not count_only:
        _require(isinstance(maximum, int) and not isinstance(maximum, bool) and maximum > 0, "max_tokens must be a positive integer.")
    _require(isinstance(body.get("messages"), list) and bool(body["messages"]), "messages must be a non-empty array.")
    _require(isinstance(body.get("stream", False), bool), "stream must be a boolean.")
    for unsupported in ("mcp_servers", "container", "context_management"):
        _require(not body.get(unsupported), f"{unsupported} is not supported by this stateless endpoint.")
    messages: list[dict[str, Any]] = []
    if body.get("system") is not None:
        system = _parts(body["system"], native=native)
        _require(all(part["type"] == "text" for part in system), "system supports text only.")
        messages.append({"role": "system", "content": system})
    for message in body["messages"]:
        messages.extend(_message(message, native=native))
    result: dict[str, Any] = {"model": body["model"], "messages": messages, "stream": body.get("stream", False)}
    if maximum is not None:
        result["max_tokens"] = maximum
    for field in ("temperature", "top_p", "top_k"):
        if body.get(field) is not None:
            value = body[field]
            _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{field} must be numeric.")
            if field != "top_k" or native:
                result[field] = value
    if body.get("stop_sequences") is not None:
        stops = body["stop_sequences"]
        _require(isinstance(stops, list) and all(isinstance(s, str) for s in stops), "stop_sequences must be an array of strings.")
        result["stop"] = deepcopy(stops)
    tools = body.get("tools", [])
    _require(isinstance(tools, list), "tools must be an array.")
    mapped: list[dict[str, Any]] = []
    for tool in tools:
        _require(isinstance(tool, dict) and tool.get("type") in (None, "custom"), "Only client function tools are supported.")
        _require(isinstance(tool.get("name"), str) and bool(tool["name"]) and isinstance(tool.get("input_schema"), dict), "Tools require name and input_schema.")
        fn = {"name": tool["name"], "parameters": deepcopy(tool["input_schema"])}
        for field in ("description", "strict"):
            if field in tool:
                fn[field] = tool[field]
        entry = {"type": "function", "function": fn}
        if native and "cache_control" in tool:
            entry["cache_control"] = deepcopy(tool["cache_control"])
        mapped.append(entry)
    if mapped:
        result["tools"] = mapped
    choice = body.get("tool_choice")
    if choice is not None:
        _require(isinstance(choice, dict), "tool_choice must be an object.")
        kind = choice.get("type")
        _require(kind in ("auto", "any", "none", "tool"), "Unsupported tool_choice.")
        _require(kind not in ("any", "tool") or bool(mapped), "This tool_choice requires declared tools.")
        if kind == "tool":
            _require(choice.get("name") in {tool["function"]["name"] for tool in mapped}, "tool_choice names an undeclared tool.")
            result["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
        else:
            result["tool_choice"] = {"auto": "auto", "any": "required", "none": "none"}[kind]
        if "disable_parallel_tool_use" in choice:
            _require(isinstance(choice["disable_parallel_tool_use"], bool), "disable_parallel_tool_use must be a boolean.")
            result["parallel_tool_calls"] = not choice["disable_parallel_tool_use"]
    thinking = body.get("thinking")
    output = body.get("output_config")
    _require(body.get("metadata") is None or isinstance(body["metadata"], dict), "metadata must be an object.")
    if thinking is not None:
        _require(isinstance(thinking, dict) and thinking.get("type") in ("enabled", "disabled", "adaptive"), "Invalid thinking configuration.")
        if thinking["type"] == "enabled":
            budget = thinking.get("budget_tokens")
            _require(isinstance(budget, int) and not isinstance(budget, bool) and budget >= 1024, "thinking.budget_tokens must be at least 1024.")
            _require(count_only or budget < maximum, "thinking.budget_tokens must be less than max_tokens.")
    if output is not None:
        _require(isinstance(output, dict), "output_config must be an object.")
        _require(not output.get("format"), "Structured output is not supported by this endpoint.")
        _require(output.get("effort") in (None, "low", "medium", "high", "max"), "Unsupported output_config.effort.")
    if native:
        for field in ("thinking", "output_config", "metadata"):
            if body.get(field) is not None:
                result[field] = deepcopy(body[field])
    else:
        effort = (output or {}).get("effort")
        if effort:
            result["reasoning_effort"] = "xhigh" if effort == "max" else effort
        elif thinking:
            budget = thinking.get("budget_tokens", 16384)
            result["reasoning_effort"] = {"disabled": "none", "adaptive": "xhigh", "enabled": "low" if budget < 4000 else "medium" if budget < 16000 else "high"}[thinking["type"]]
        metadata = body.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("user_id"), str):
            result["user"] = metadata["user_id"]
    return result


def estimate_input_tokens(body: dict[str, Any]) -> int:
    """Local approximation; never presented as the upstream tokenizer's count."""
    payload = {key: body[key] for key in ("system", "messages", "tools", "tool_choice") if key in body}
    return max(1, (len(json.dumps(payload, ensure_ascii=False).encode()) + 2) // 3)
