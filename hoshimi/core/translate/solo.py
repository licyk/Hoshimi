"""OpenAI Chat <-> TRAE SOLO translation.

Ported from dsh-our-free-model's trae.ts (transformToSOLOBody / parseTraeSSELine):
- request keeps the OpenAI shape but: stream=true, `function` channel field,
  model becomes both `config_name` and `model`, assistant tool_calls use
  `function_call` (not `function`), content strings become [{type,text}],
  tools[].parameters must be a JSON string, tool_choice is normalized.
- SSE uses named events: metadata / timing_cost / output / extra_info /
  token_usage / done / error. `output` events carry incremental
  {response, reasoning_content, tool_calls}. There is no data:[DONE].
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

from hoshimi.core.sse import iter_sse
from hoshimi.core.translate.chat import convert_tools

TRAE_FUNCTION = "solo_work_lite"
TRAE_DEFAULT_MODEL = "glm-5.2"


def build_solo_body(body: dict[str, Any], *, model: str, channel: str = TRAE_FUNCTION) -> dict[str, Any]:
    """Transform an OpenAI chat body into a TRAE SOLO request body."""
    out: dict[str, Any] = {key: value for key, value in body.items() if key != "messages"}
    out["stream"] = True
    out["function"] = channel

    messages = body.get("messages") or []
    out["messages"] = [_transform_message(m) for m in messages if isinstance(m, dict)]

    base_model = model.split("__")[0] if "__" in model else model
    config_name = base_model or TRAE_DEFAULT_MODEL
    out["config_name"] = config_name
    out["model"] = config_name

    _normalize_tool_choice(out)
    _normalize_tools(out)

    if body.get("max_tokens") is not None and body.get("max_completion_tokens") is None:
        pass  # already copied via spread of body keys
    if body.get("max_completion_tokens") is not None:
        out["max_tokens"] = body["max_completion_tokens"]

    return out


def _transform_message(message: dict[str, Any]) -> dict[str, Any]:
    result = dict(message)
    if result.get("role") == "assistant":
        kept: list[dict[str, Any]] = []
        for tool_call in result.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            call = dict(tool_call)
            fn = call.get("function")
            if isinstance(fn, dict):
                call["function_call"] = fn
                call.pop("function", None)
            function_call = call.get("function_call")
            name = function_call.get("name") if isinstance(function_call, dict) else None
            if not isinstance(name, str) or not name.strip():
                continue
            kept.append(call)
        if kept:
            result["tool_calls"] = kept
        else:
            result.pop("tool_calls", None)

    content = result.get("content")
    if isinstance(content, str):
        result["content"] = [{"type": "text", "text": content}]
    return result


def _normalize_tool_choice(body: dict[str, Any]) -> None:
    tool_choice = body.get("tool_choice")
    if tool_choice is None:
        return
    if isinstance(tool_choice, str):
        if tool_choice.lower().strip() == "none":
            body.pop("tool_choice", None)
            body.pop("tools", None)
        return
    if isinstance(tool_choice, dict):
        kind = str(tool_choice.get("type") or "").lower().strip()
        if kind == "none":
            body.pop("tool_choice", None)
            body.pop("tools", None)
        elif kind in ("auto", "required"):
            body["tool_choice"] = kind
        elif kind == "function":
            fn = tool_choice.get("function")
            name = fn.get("name") if isinstance(fn, dict) else None
            if not isinstance(name, str) or not name.strip():
                name = tool_choice.get("name")
            body["tool_choice"] = name.strip() if isinstance(name, str) and name.strip() else "auto"
        else:
            body.pop("tool_choice", None)
    else:
        body.pop("tool_choice", None)


def _normalize_tools(body: dict[str, Any]) -> None:
    tools = convert_tools(body.get("tools"))
    if not tools:
        body.pop("tools", None)
        return
    out: list[dict[str, Any]] = []
    for tool in tools:
        fn = dict(tool["function"])
        if isinstance(fn.get("parameters"), dict):
            fn["parameters"] = json.dumps(fn["parameters"], ensure_ascii=False)
        out.append({"type": "function", "function": fn})
    body["tools"] = out


def iter_solo_chunks(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Consume TRAE SOLO SSE and yield normalized chunks."""
    tool_count = 0
    usage_input = 0
    usage_output = 0
    finish_reason = "stop"
    saw_any = False

    for event_name, data in iter_sse(lines):
        if not data or data == "[DONE]":
            continue
        event = (event_name or "").strip()
        try:
            raw = json.loads(data)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue

        if event == "output":
            response = raw.get("response")
            if isinstance(response, str) and response:
                saw_any = True
                yield {"type": "text_delta", "text": response}
            reasoning = raw.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                saw_any = True
                yield {"type": "reasoning_delta", "text": reasoning}
            tool_calls = raw.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function")
                    if not isinstance(function, dict):
                        continue
                    name = str(function.get("name") or "")
                    arguments = function.get("arguments")
                    args_text = arguments if isinstance(arguments, str) else json.dumps(
                        arguments or {}, ensure_ascii=False
                    )
                    call_id = call.get("id") or f"call_{tool_count}"
                    index = tool_count
                    tool_count += 1
                    saw_any = True
                    yield {
                        "type": "tool_call_start",
                        "id": str(call_id),
                        "name": name,
                        "arguments": args_text,
                        "index": index,
                    }

        elif event == "token_usage":
            usage_input = _nonneg(raw.get("prompt_tokens"))
            usage_output = _nonneg(raw.get("completion_tokens"))

        elif event == "done":
            reason = raw.get("finish_reason")
            if isinstance(reason, str):
                if reason == "tool_calls" or reason == "function_call":
                    finish_reason = "tool_calls"
                elif reason == "length":
                    finish_reason = "length"
                else:
                    finish_reason = "stop"

        elif event == "error":
            message = raw.get("message") or f"TRAE error {raw.get('code')}"
            yield {"type": "error", "error": str(message)}
            return

    if not saw_any:
        yield {"type": "error", "error": "Upstream returned no content."}
        return

    yield {
        "type": "usage",
        "usage": {
            "prompt_tokens": usage_input,
            "completion_tokens": usage_output,
            "total_tokens": usage_input + usage_output,
        },
    }
    if tool_count > 0 and finish_reason == "stop":
        yield {"type": "finish", "reason": "tool_calls"}
    else:
        yield {"type": "finish", "reason": finish_reason}


def _nonneg(value: Any) -> int:
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return 0
