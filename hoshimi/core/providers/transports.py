"""Shared HTTP transports; provider adapters own endpoints and authentication."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

import httpx

from hoshimi.core.providers.openai_compat import raise_for_status, read_body
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.anthropic import build_anthropic_body, iter_anthropic_chunks
from hoshimi.core.translate.responses import chat_to_responses, iter_responses_chunks


def request_payload(body: dict[str, Any], model: str, wire: str) -> dict[str, Any]:
    if wire == "messages":
        extra = {key: body[key] for key in ("thinking", "output_config", "metadata") if key in body}
        return build_anthropic_body(body, model=model, extra=extra)
    if wire == "responses":
        payload = chat_to_responses(body, model=model)
        for key in ("reasoning", "include", "text", "parallel_tool_calls", "prompt_cache_key", "service_tier"):
            if key in body:
                payload[key] = body[key]
        if isinstance(body.get("reasoning_effort"), str):
            payload.setdefault("reasoning", {"effort": body["reasoning_effort"]})
        return payload
    payload: dict[str, Any] = deepcopy({**{key: value for key, value in body.items() if key not in ("session_id", "conversation_id")}, "model": model, "stream": True})
    signed = {detail.get("call_id"): detail.get("extra_content") for message in payload.get("messages", [])
              for detail in message.get("reasoning_details", []) if isinstance(detail, dict) and detail.get("type") == "hoshimi_tool_extra"}
    for message in payload.get("messages", []):
        if isinstance(message.get("reasoning_details"), list):
            message["reasoning_details"] = [d for d in message["reasoning_details"] if not isinstance(d, dict) or d.get("type") != "hoshimi_tool_extra"]
            if not message["reasoning_details"]:
                message.pop("reasoning_details")
        for call in message.get("tool_calls") or []:
            if isinstance(signed.get(call.get("id")), dict):
                call["extra_content"] = signed[call["id"]]
    return payload


def stream_http(client: httpx.Client, *, provider: str, url: str, headers: dict[str, str], payload: dict[str, Any], wire: str) -> Iterator[dict[str, Any]]:
    with client.stream("POST", url, headers=headers, json=payload) as response:
        observe_response(provider, response)
        raise_for_status(response, provider=provider)
        if wire == "messages":
            yield from iter_anthropic_chunks(response.iter_lines())
        elif wire == "responses":
            yield from iter_responses_chunks(response.iter_lines(), provider=provider)
        else:
            yield from read_body(response, provider=provider)
