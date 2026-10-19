"""OpenAI Chat <-> Gemini (daily-cloudcode) translation.

Ported from dsh-our-free-model's gemini.ts / gemini-messages.ts:
- request envelope {"request": {...}} with alphabetically re-serialized JSON keys
- model `gemini-3.8-flash-<tier>` (tier from reasoning_effort or model suffix)
- session id derived via FNV-1a-64 over (project, lane, first user text)
- SSE frames may be wrapped in {"response": {...}} or bare; `data: [DONE]` ends
- thought parts map to reasoning; functionCall parts arrive complete (not deltas)
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from typing import Any
from uuid import uuid4

from hoshimi.core.sse import iter_sse
from hoshimi.core.translate.chat import parse_tool_arguments, split_system_messages, text_from_content

GEMINI_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
GEMINI_STREAM_PATH = "/v1internal:streamGenerateContent?alt=sse"
GEMINI_DEFAULT_PROJECT = "aicode-consumers"
GEMINI_SESSION_LANE = "infer"
GEMINI_UPSTREAM_FLASH = "gemini-3.8-flash"

GEMINI_IDENTITY_HEADERS = {
    "User-Agent": "antigravity/4.3.0 (cmdc-pak)",
    "x-client-name": "antigravity",
    "x-client-version": "4.3.0",
    "x-machine-id": "cmdc-pak",
    "x-vscode-sessionid": "proxy",
}

_TIER_BUDGETS = {"low": 1000, "medium": 4000, "high": 10000, "tiered": -1}

GEMINI_TIER_IDS = ("low", "medium", "high", "tiered")
"""The four tiers, in picker order. `catalog.GEMINI_EFFORTS` must agree with this."""
GEMINI_DEFAULT_TIER = "medium"
"""What an unset tier resolves to. `catalog.GEMINI_DEFAULT_EFFORT` must agree."""

_SCHEMA_KEYS = {
    "type",
    "format",
    "description",
    "nullable",
    "enum",
    "items",
    "minItems",
    "maxItems",
    "properties",
    "required",
    "minProperties",
    "maxProperties",
    "minLength",
    "maxLength",
}


def gemini_model_ids() -> list[str]:
    """The station's single catalogue row.

    Upstream publishes one id and puts the tier in the ``efforts`` picker
    instead: four suffixed rows would advertise four models where the station
    ships one ("不暴露 4 个带后缀的模型名 —— 档位走 efforts 下拉框").

    The suffix still goes out on the wire — the bare id is answered 404 — and
    :func:`canonical_model_id` keeps stripping it, so a client that cached a
    suffixed name reaches the same place through an explicit ``provider/model``.
    """
    return [GEMINI_UPSTREAM_FLASH]


def canonical_model_id(model: str) -> str:
    for tier in GEMINI_TIER_IDS:
        suffix = f"-{tier}"
        if model.endswith(suffix):
            return model[: -len(suffix)]
    return model


def effort_to_tier(effort: str | None, model: str | None = None) -> str:
    # An explicit `reasoning_effort` wins; otherwise a `-low`/`-high` model
    # suffix selects the tier; default is medium.
    if effort in GEMINI_TIER_IDS:
        return str(effort)
    if model:
        for tier in GEMINI_TIER_IDS:
            if model.endswith(f"-{tier}"):
                return tier
    return GEMINI_DEFAULT_TIER


def derive_session_id(project: str, first_user_text: str, lane: str = GEMINI_SESSION_LANE) -> str:
    """FNV-1a 64-bit hash rendered as a signed 64-bit decimal string."""
    mask = (1 << 64) - 1
    value = 0xCBF29CE484222325
    prime = 0x100000001B3
    data = f"{project}\x00{lane}\x00{first_user_text}".encode()
    for byte in data:
        value = ((value ^ byte) * prime) & mask
    if value >= (1 << 63):
        value -= 1 << 64
    return str(value)


def _sanitize_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_schema(item)
            for key, item in value.items()
            if key in _SCHEMA_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_schema(item) for item in value]
    return value


def _alphabetical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _alphabetical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_alphabetical(item) for item in value]
    return value


def marshal_envelope(envelope: dict[str, Any]) -> str:
    return json.dumps(_alphabetical(envelope), ensure_ascii=False, separators=(",", ":"))


def _parse_data_url(url: str) -> tuple[str, str] | None:
    if not url.startswith("data:"):
        return None
    try:
        header, data = url.split(",", 1)
    except ValueError:
        return None
    media = header[len("data:"):].split(";")[0] or "image/png"
    return media, data


def build_gemini_envelope(
    body: dict[str, Any],
    *,
    model: str,
    effort: str | None = None,
    project: str = GEMINI_DEFAULT_PROJECT,
) -> dict[str, Any]:
    """Build the {"request": ...} envelope for streamGenerateContent."""
    tier = effort_to_tier(effort, model)
    messages = body.get("messages") or []
    system, rest = split_system_messages(messages)

    tool_names: dict[str, str] = {}
    contents: list[dict[str, Any]] = []
    for message in rest:
        role = message.get("role")
        content = message.get("content")
        parts: list[dict[str, Any]] = []

        if role == "tool":
            call_id = message.get("tool_call_id")
            name = tool_names.get(str(call_id), "")
            if not name:
                continue
            response: dict[str, Any] = {"content": text_from_content(content)}
            if message.get("is_error") is True:
                response["error"] = True
            parts.append({"functionResponse": {"name": name, "response": response}})
        elif role == "assistant":
            text = text_from_content(content)
            if text:
                parts.append({"text": text})
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                fn = tool_call.get("function") or {}
                name = fn.get("name")
                call_id = tool_call.get("id")
                if not name:
                    continue
                if isinstance(call_id, str) and call_id:
                    tool_names[call_id] = name
                parts.append(
                    {"functionCall": {"name": name, "args": parse_tool_arguments(fn.get("arguments"))}}
                )
        elif role == "user":
            text = text_from_content(content)
            if text:
                parts.append({"text": text})
            for image_url in _image_urls(content):
                parsed = _parse_data_url(image_url)
                if parsed:
                    media, data = parsed
                    parts.append({"inlineData": {"mimeType": media, "data": data}})

        if parts:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": parts})

    if not contents:
        raise ValueError("gemini: request has no usable message content")

    first_user_text = ""
    for message in rest:
        if message.get("role") == "user":
            first_user_text = text_from_content(message.get("content"))
            break

    thinking_config: dict[str, Any] = {"includeThoughts": True}
    budget = _TIER_BUDGETS.get(tier, 4000)
    if budget >= 0:
        thinking_config["thinkingBudget"] = budget

    generation_config: dict[str, Any] = {
        "maxOutputTokens": int(body.get("max_tokens") or body.get("max_completion_tokens") or 64000),
        "thinkingConfig": thinking_config,
    }
    if body.get("temperature") is not None:
        generation_config["temperature"] = body["temperature"]

    request: dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
        "sessionId": derive_session_id(project, first_user_text),
    }
    if system:
        request["systemInstruction"] = {"role": "system", "parts": [{"text": system}]}

    tools_in = body.get("tools") or []
    declarations: list[dict[str, Any]] = []
    for tool in tools_in:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function") or {}
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        declarations.append(
            {
                "name": name,
                "description": fn.get("description") or "",
                "parameters": _sanitize_schema(
                    fn.get("parameters") or {"type": "object", "properties": {}}
                ),
            }
        )
    if declarations:
        request["tools"] = [{"functionDeclarations": declarations}]
        tool_config = _tool_config(body.get("tool_choice"))
        if tool_config is not None:
            request["toolConfig"] = tool_config

    return {
        # The upstream model always carries the resolved tier suffix.
        "model": f"{GEMINI_UPSTREAM_FLASH}-{tier}",
        "project": project,
        "request": request,
        "requestId": f"agent/{int(time.time() * 1000)}/{uuid4().hex[:8]}",
        "userAgent": "antigravity",
    }


def _image_urls(content: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(content, list):
        return out
    for item in content:
        if isinstance(item, dict) and item.get("type") == "image_url":
            image_url = item.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if isinstance(url, str) and url:
                out.append(url)
    return out


def _tool_config(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice in (None, "auto"):
        return None
    if tool_choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    if tool_choice in ("required", "any"):
        return {"functionCallingConfig": {"mode": "ANY"}}
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        fn = tool_choice.get("function")
        name = fn.get("name") if isinstance(fn, dict) else None
        if isinstance(name, str) and name:
            return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
    return None


def iter_gemini_chunks(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Consume Gemini SSE and yield normalized chunks."""
    usage_total = 0
    usage_prompt = 0
    usage_completion = 0
    finish_reason: str | None = None
    tool_count = 0
    saw_any = False

    for _event, data in iter_sse(lines):
        if not data or data == "[DONE]":
            continue
        try:
            frame = json.loads(data)
        except ValueError:
            continue
        if not isinstance(frame, dict):
            continue
        wrapped = frame.get("response")
        inner: dict[str, Any] = wrapped if isinstance(wrapped, dict) else frame

        usage = inner.get("usageMetadata")
        if isinstance(usage, dict):
            total = usage.get("totalTokenCount")
            if isinstance(total, (int, float)) and total >= usage_total:
                usage_total = int(total)
                usage_prompt = _nonneg(usage.get("promptTokenCount"))
                usage_completion = _nonneg(usage.get("candidatesTokenCount")) or max(
                    0, usage_total - usage_prompt
                )

        candidates = inner.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            continue
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            continue
        if isinstance(candidate.get("finishReason"), str) and candidate["finishReason"]:
            finish_reason = candidate["finishReason"]

        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue

        for part in parts:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("thoughtSignature"), str):
                yield {"type": "reasoning_details", "details": [{"type": "gemini_part", "part": part}]}
            if part.get("functionCall") is not None:
                call = part["functionCall"]
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                args_json = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
                tool_id = f"call_{tool_count}"
                tool_count += 1
                saw_any = True
                yield {
                    "type": "tool_call_start",
                    "id": tool_id,
                    "name": name,
                    "arguments": args_json,
                    "index": tool_count - 1,
                }
                continue

            text = part.get("text")
            if not isinstance(text, str) or not text:
                continue
            saw_any = True
            if part.get("thought") is True:
                yield {"type": "reasoning_delta", "text": text}
            else:
                yield {"type": "text_delta", "text": text}

    if not saw_any:
        yield {"type": "error", "error": "Upstream returned no content."}
        return

    yield {
        "type": "usage",
        "usage": {
            "prompt_tokens": usage_prompt,
            "completion_tokens": usage_completion,
            "total_tokens": usage_total or (usage_prompt + usage_completion),
        },
    }
    if tool_count > 0:
        yield {"type": "finish", "reason": "tool_calls"}
    elif finish_reason in ("MAX_TOKENS", "length"):
        yield {"type": "finish", "reason": "length"}
    else:
        yield {"type": "finish", "reason": "stop"}


def _nonneg(value: Any) -> int:
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return 0
