"""Bounded normalization of NIM schema quirks and model-native tool blocks."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from hoshimi.core.providers.base import ProviderError

MARK = "]<]minimax[>["
START = MARK + "<tool_call>"
END = MARK + "</tool_call>"


def clean_schema(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    for key in ("additionalProperties", "items", "contains", "not", "if", "then", "else"):
        if isinstance(result.get(key), bool):
            result.pop(key)
        elif isinstance(result.get(key), dict):
            result[key] = clean_schema(result[key])
    for key in ("properties", "$defs", "definitions", "patternProperties"):
        if isinstance(result.get(key), dict):
            result[key] = {name: clean_schema(item) for name, item in result[key].items() if not isinstance(item, bool)}
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if isinstance(result.get(key), list):
            result[key] = [clean_schema(item) for item in result[key] if not isinstance(item, bool)]
    return result


def _arguments(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    result: dict[str, Any] = {}
    pattern = re.compile(re.escape(MARK) + r"<([A-Za-z_][\w.-]*)>(.*?)" + re.escape(MARK) + r"</\1>", re.DOTALL)
    position = 0
    for match in pattern.finditer(text):
        if text[position:match.start()].strip():
            raise ValueError("unframed tool argument")
        key, raw = match.groups()
        if key not in properties or key in result:
            raise ValueError("unknown or repeated tool argument")
        kind = properties[key].get("type") if isinstance(properties[key], dict) else None
        if kind == "string":
            result[key] = raw
        else:
            result[key] = json.loads(raw)
            expected = {"integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list, "null": type(None)}.get(kind)
            if expected is not None and (not isinstance(result[key], expected) or kind in ("integer", "number") and isinstance(result[key], bool)):
                raise ValueError("tool argument has incorrect type")
        position = match.end()
    if text[position:].strip() or any(key not in result for key in schema.get("required", [])):
        raise ValueError("incomplete tool arguments")
    return result


def native_tools(chunks: Iterator[dict[str, Any]], tools: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    allowed = {tool.get("function", {}).get("name"): tool.get("function", {}).get("parameters", {}) for tool in tools}
    buffer = ""
    in_block = False
    calls = 0
    finish: dict[str, Any] | None = None
    for chunk in chunks:
        if chunk.get("type") == "finish":
            finish = chunk
            continue
        if chunk.get("type") != "text_delta":
            yield chunk
            continue
        buffer += chunk.get("text", "")
        if len(buffer) > 1024 * 1024:
            raise ProviderError(502, "NIM native tool block exceeded its limit")
        while buffer:
            if not in_block:
                position = buffer.find(START)
                if position < 0:
                    keep = next((n for n in range(min(len(buffer), len(START) - 1), 0, -1) if START.startswith(buffer[-n:])), 0)
                    text = buffer[:-keep] if keep else buffer
                    if text:
                        yield {"type": "text_delta", "text": text}
                    buffer = buffer[-keep:] if keep else ""
                    break
                if position:
                    yield {"type": "text_delta", "text": buffer[:position]}
                buffer = buffer[position + len(START):]
                in_block = True
            position = buffer.find(END)
            if position < 0:
                break
            block, buffer = buffer[:position], buffer[position + len(END):]
            pattern = re.compile(re.escape(MARK) + r'<invoke name="([^"]+)">(.*?)' + re.escape(MARK) + r"</invoke>", re.DOTALL)
            consumed = 0
            try:
                matches = list(pattern.finditer(block))
                if not matches:
                    raise ValueError("no tool invocation")
                for match in matches:
                    name, raw = match.groups()
                    if block[consumed:match.start()].strip() or name not in allowed:
                        raise ValueError("undeclared tool")
                    arguments = _arguments(raw, allowed[name])
                    yield {"type": "tool_call_start", "index": calls, "id": "call_" + uuid.uuid4().hex, "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}
                    calls += 1
                    consumed = match.end()
                if block[consumed:].strip():
                    raise ValueError("trailing tool markup")
            except (ValueError, TypeError):
                raise ProviderError(502, "NIM returned malformed native tool arguments") from None
            in_block = False
    if in_block:
        raise ProviderError(502, "NIM stream ended inside a native tool block")
    if buffer:
        yield {"type": "text_delta", "text": buffer}
    if finish is not None:
        yield {**finish, "reason": "tool_calls"} if calls else finish
