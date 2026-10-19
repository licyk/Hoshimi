"""OpenAI Chat Completions request helpers."""

from __future__ import annotations

import json
from typing import Any


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
            and item.get("type") in ("text", "input_text", "output_text")
            and isinstance(item.get("text"), str)
        ]
        return "".join(parts)
    return ""


def image_data_urls(content: Any) -> list[str]:
    """Collect image URLs (or data URLs) from a chat message content value."""
    out: list[str] = []
    if not isinstance(content, list):
        return out
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image_url":
            continue
        image_url = item.get("image_url")
        url = image_url.get("url") if isinstance(image_url, dict) else None
        if isinstance(url, str) and url:
            out.append(url)
    return out


def split_system_messages(messages: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    """Split system/developer messages into an instructions string."""
    parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") in ("system", "developer"):
            text = text_from_content(message.get("content"))
            if text:
                parts.append(text)
        else:
            rest.append(message)
    return "\n\n".join(parts), rest


def tool_output_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    text = text_from_content(content)
    if text:
        return text
    return json.dumps(content, ensure_ascii=False)


def convert_tools(tools: Any) -> list[dict[str, Any]]:
    """Keep OpenAI function tools as-is (normalized)."""
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function") or {}
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        entry: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": name,
                "parameters": fn.get("parameters")
                or {"type": "object", "properties": {}},
            },
        }
        if isinstance(fn.get("description"), str):
            entry["function"]["description"] = fn["description"]
        out.append(entry)
    return out


def tool_names(tools: Any) -> set[str]:
    names: set[str] = set()
    for tool in tools or []:
        if isinstance(tool, dict) and tool.get("type") == "function":
            fn = tool.get("function") or {}
            name = fn.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def finish_reason_from_stop(reason: Any) -> str:
    mapping = {
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "stop": "stop",
        "end_turn": "stop",
        "stop_sequence": "stop",
    }
    if isinstance(reason, str):
        return mapping.get(reason, "stop")
    return "stop"
