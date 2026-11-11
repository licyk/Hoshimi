"""The agent-chat request: OpenAI chat body -> Qoder's ``agent_chat_generation``.

The gateway's agent endpoint is not OpenAI-shaped. It takes an envelope that
declares a CLI session (``session_type: "qodercli"``, ``agent_id:
"agent_common"``, ``chat_task: "FREE_INPUT"``), carries the conversation as
OpenAI-style ``messages``, and puts the generation controls in a nested
``parameters`` object. Three details are load-bearing and were settled against
the live service by the plugin this is ported from:

* the top-level ``system`` field is ignored — the system prompt has to be a
  leading ``role: system`` message;
* an assistant turn with tool calls but no visible text must carry a placeholder
  content, or the gateway drops the message and the following ``tool`` reply
  orphans ("tool must follow a message with tool_calls");
* tool call ids must match ``^[a-zA-Z0-9_-]{1,64}$``; anything else is a 400.

Thinking is driven by ``parameters.enable_thinking`` plus, for effort-based
models, ``parameters.reasoning_effort``. Disabling takes both: ``enable_thinking:
false`` alone still routes a reasoning model as reasoning-enabled.

Ported from ``pi-provider-qoder`` ``src/protocol/transform.ts`` and the request
half of ``src/protocol/stream.ts``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from hoshimi.core.qoder.catalog import MAX_OUTPUT_TOKENS, ModelEntry
from hoshimi.core.qoder.thinking import strip_dsml_residue

VALID_WIRE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
RESERVED_WIRE_ID = re.compile(r"^qtc_[0-9a-f]{60}$")
OFF_RUNGS = frozenset({"none", "off", "disabled", "minimal_off"})


class ToolCallIdMapper:
    """Rewrite tool call ids for the wire only, deterministically.

    The assistant ``tool_calls`` entry and its ``tool`` result map through the
    same instance, so they stay paired; history is never mutated. An id that
    already fits the wire pattern passes through unless it looks generated, in
    which case it is re-hashed so a pass-through can never collide with a
    generated one.
    """

    def __init__(self) -> None:
        self._by_original: dict[str, str] = {}
        self._by_wire: dict[str, str] = {}

    def __call__(self, original: str) -> str:
        if not isinstance(original, str) or not original:
            raise ValueError("Qoder tool call id must be a non-empty string")
        cached = self._by_original.get(original)
        if cached is not None:
            return cached
        if VALID_WIRE_ID.match(original) and not RESERVED_WIRE_ID.match(original):
            wire = original
        else:
            digest = hashlib.sha256(b"qoder-tool-call-id:v1\0" + original.encode("utf-8")).hexdigest()[:60]
            wire = f"qtc_{digest}"
        owner = self._by_wire.get(wire)
        if owner is not None and owner != original:
            # Never repair a collision by remapping an earlier id or by leaking
            # the raw internal id onto the wire.
            raise ValueError("Qoder tool call id mapping collision")
        self._by_original[original] = wire
        self._by_wire[wire] = original
        return wire


def _text_of(content: Any) -> str:
    """The text of a message content, whether it is a string or a parts array."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                out.append(part["text"])
        return "".join(out)
    return ""


def _image_parts(content: Any) -> list[dict[str, Any]]:
    """Image blocks of a content array, in OpenAI ``image_url`` shape."""
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image_url" and isinstance(part.get("image_url"), dict):
            out.append({"type": "image_url", "image_url": part["image_url"]})
        elif part.get("type") == "image" and isinstance(part.get("data"), str):
            mime = str(part.get("mimeType") or part.get("mime_type") or "image/png")
            out.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{part['data']}"}})
    return out


def _user_content(message: dict[str, Any]) -> Any:
    content = message.get("content")
    if isinstance(content, str):
        return content
    images = _image_parts(content)
    if images:
        parts: list[dict[str, Any]] = []
        text = _text_of(content)
        if text:
            parts.append({"type": "text", "text": text})
        parts.extend(images)
        return parts
    return _text_of(content)


def transform_messages(messages: Any) -> tuple[list[dict[str, Any]], str]:
    """OpenAI messages -> Qoder messages, plus the system prompt text.

    System messages are folded into one leading prompt (the envelope's own
    ``system`` field is ignored upstream). Tool results keep their run together:
    a ``tool`` message carries text only, so any images they returned ride on a
    single ``user`` message after the whole run — splitting the replies makes
    upstreams reject the assistant ``tool_calls`` turn.
    """
    mapper = ToolCallIdMapper()
    out: list[dict[str, Any]] = []
    system_parts: list[str] = []
    rows = [row for row in (messages or []) if isinstance(row, dict)]

    index = 0
    while index < len(rows):
        message = rows[index]
        role = str(message.get("role") or "")
        if role == "system" or role == "developer":
            text = _text_of(message.get("content"))
            if text:
                system_parts.append(text)
            index += 1
            continue
        if role == "user":
            out.append({"role": "user", "content": _user_content(message)})
            index += 1
            continue
        if role == "assistant":
            content = _text_of(message.get("content"))
            reasoning = message.get("reasoning_content")
            # Replayed reasoning is cleaned the same way the live channel is:
            # stored text can carry leaked DSML markup, and the next turn would
            # send it straight back into the prompt.
            reasoning_text = strip_dsml_residue(reasoning) if isinstance(reasoning, str) else ""
            tool_calls: list[dict[str, Any]] = []
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                function = function if isinstance(function, dict) else {}
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
                call_id = call.get("id")
                tool_calls.append({
                    "id": mapper(str(call_id)) if call_id else "",
                    "type": "function",
                    "function": {"name": str(function.get("name") or ""), "arguments": arguments},
                })
            mapped: dict[str, Any] = {
                "role": "assistant",
                # A placeholder keeps the gateway from dropping a turn whose
                # only payload is tool calls or reasoning.
                "content": content or (" " if tool_calls or reasoning_text else None),
            }
            if reasoning_text:
                mapped["reasoning_content"] = reasoning_text
            if tool_calls:
                mapped["tool_calls"] = tool_calls
            out.append(mapped)
            index += 1
            continue
        if role == "tool":
            images: list[dict[str, Any]] = []
            while index < len(rows) and str(rows[index].get("role") or "") == "tool":
                result = rows[index]
                call_id = result.get("tool_call_id")
                out.append({
                    "role": "tool",
                    "tool_call_id": mapper(str(call_id)) if call_id else "",
                    "content": _text_of(result.get("content")),
                })
                images.extend(_image_parts(result.get("content")))
                index += 1
            if images:
                label = f"[{len(images)} image{'' if len(images) == 1 else 's'} returned by the previous tool call]"
                out.append({"role": "user", "content": [{"type": "text", "text": label}, *images]})
            continue
        # An unknown role is passed through as user text rather than dropped.
        out.append({"role": "user", "content": _text_of(message.get("content"))})
        index += 1

    return out, "\n\n".join(system_parts)


def transform_tools(tools: Any) -> list[dict[str, Any]]:
    """OpenAI tool declarations in the shape the agent endpoint accepts."""
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        declaration: dict[str, Any] = {"name": name}
        if isinstance(function.get("description"), str):
            declaration["description"] = function["description"]
        parameters = function.get("parameters")
        if parameters is not None:
            declaration["parameters"] = parameters
        out.append({"type": "function", "function": declaration})
    return out


def stable_hash(prefix: str, *inputs: str) -> str:
    digest = hashlib.sha256()
    digest.update(prefix.encode("utf-8"))
    for value in inputs:
        digest.update(b"\0")
        digest.update(value.encode("utf-8"))
    return digest.hexdigest()[:16]


def chat_record_id(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, max_tokens: int) -> str:
    """A stable id for one logical request, so a retry correlates with its set."""
    digest = hashlib.sha256()
    digest.update(b"qoder-record\0")
    digest.update(model.encode("utf-8"))
    for message in messages:
        role = message.get("role")
        if role:
            digest.update(b"\0")
            digest.update(str(role).encode("utf-8"))
        content = message.get("content")
        if content:
            digest.update(b"\0")
            digest.update((content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)).encode("utf-8"))
    if tools:
        digest.update(b"\0")
        digest.update(json.dumps(tools, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    digest.update(b"\0")
    digest.update(f"mt={max_tokens}".encode())
    return digest.hexdigest()[:16]


def thinking_parameters(
    effort: Any,
    entry: ModelEntry | None,
    *,
    max_tokens: int,
) -> tuple[dict[str, Any], bool]:
    """``parameters`` and whether thinking is on, from the caller's level.

    An absent level means thinking off, exactly as the plugin resolves it: the
    envelope must state the switch either way. ``reasoning_effort`` is only sent
    when the model advertises effort rungs; a model with no disabled branch
    rejects ``"none"`` outright, so there the switch alone is the disable.
    """
    level = str(effort).strip().lower() if isinstance(effort, str) and effort.strip() else ""
    thinking_on = bool(level) and level not in OFF_RUNGS
    parameters: dict[str, Any] = {"max_tokens": max_tokens}
    if thinking_on:
        parameters["enable_thinking"] = True
        if entry is not None and entry.efforts and not entry.toggle_only and level in entry.efforts:
            parameters["reasoning_effort"] = level
        return parameters, True
    parameters["enable_thinking"] = False
    lacks_disabled_branch = entry is not None and bool(entry.efforts) and not entry.toggle_only and not entry.can_disable
    if not lacks_disabled_branch:
        parameters["reasoning_effort"] = "none"
    return parameters, False


def requested_effort(body: dict[str, Any]) -> Any:
    """The level the caller (or the effort policy) named, if any."""
    if isinstance(body.get("reasoning_effort"), str):
        return body["reasoning_effort"]
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        return reasoning.get("effort")
    return None


def output_ceiling(body: dict[str, Any]) -> int:
    """The generation ceiling: the caller's when lower, the upstream cap otherwise."""
    for key in ("max_tokens", "max_completion_tokens"):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return min(value, MAX_OUTPUT_TOKENS)
    return MAX_OUTPUT_TOKENS


def build_chat_body(
    body: dict[str, Any],
    *,
    model_key: str,
    model_config: dict[str, Any],
    entry: ModelEntry | None,
    user_id: str,
    session_marker: str = "",
) -> dict[str, Any]:
    """The full ``agent_chat_generation`` envelope for one turn."""
    messages, system_text = transform_messages(body.get("messages"))
    tools = transform_tools(body.get("tools"))
    max_tokens = output_ceiling(body)
    parameters, thinking_on = thinking_parameters(requested_effort(body), entry, max_tokens=max_tokens)

    last_user_text = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            last_user_text = content if isinstance(content, str) else _text_of(content)
            break

    stable = stable_hash("qoder-session", user_id, model_key)
    session_id = f"{stable}-{session_marker}" if session_marker else f"{stable}-{uuid.uuid4()}"
    record_id = chat_record_id(model_key, messages, tools or None, max_tokens)
    is_reasoning = bool(entry.reasoning) if entry is not None else bool(model_config.get("is_reasoning"))

    config = dict(model_config)
    config["key"] = model_key
    config["is_reasoning"] = is_reasoning if thinking_on else False

    return {
        "request_id": str(uuid.uuid4()),
        "request_set_id": record_id,
        "chat_record_id": record_id,
        "session_id": session_id,
        "stream": True,
        "chat_task": "FREE_INPUT",
        "is_reply": True,
        "is_retry": False,
        "source": 1,
        "version": "3",
        "session_type": "qodercli",
        "agent_id": "agent_common",
        "task_id": "common",
        "code_language": "",
        "chat_prompt": "",
        "image_urls": None,
        "aliyun_user_type": "",
        # Ignored upstream; the prompt rides as a leading system message.
        "system": "",
        "messages": [{"role": "system", "content": system_text}, *messages] if system_text else messages,
        "tools": tools,
        "parameters": parameters,
        "chat_context": {
            "chatPrompt": "",
            "imageUrls": None,
            "extra": {
                "context": [],
                "modelConfig": {"key": model_key, "is_reasoning": is_reasoning if thinking_on else False},
                "originalContent": last_user_text,
            },
            "features": [],
            "text": last_user_text,
        },
        "model_config": config,
        "business": {
            "product": "cli",
            "version": "1.0.0",
            "type": "agent",
            "stage": "start",
            "id": str(uuid.uuid4()),
            "name": last_user_text[:30],
            "begin_at": int(time.time() * 1000),
        },
    }