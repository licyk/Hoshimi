"""Radius native catalog and Pi's serialized message/event protocol."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.keyed import KeyedProvider, spec_from_row
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.quota import observe_response
from hoshimi.core.sse import iter_sse
from hoshimi.core.translate.chat import parse_tool_arguments, text_from_content


def _content(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    out: list[dict[str, Any]] = []
    for part in value or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") in ("text", "input_text"):
            out.append({"type": "text", "text": part.get("text", "")})
        elif part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(",", 1)
                base64.b64decode(data, validate=True)
                out.append({"type": "image", "mimeType": header[5:].split(";")[0], "data": data})
    return out


def pi_context(body: dict[str, Any], model: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for original in body.get("messages", []):
        role = original.get("role")
        content = _content(original.get("content"))
        message: dict[str, Any] = {"role": role, "content": content, "timestamp": original.get("timestamp", 0)}
        if role in ("system", "developer"):
            message.update(role="system", content=text_from_content(original.get("content")))
        elif role == "assistant":
            for detail in original.get("reasoning_details", []):
                if isinstance(detail, dict) and detail.get("type") == "pi_thinking":
                    content.append({"type": "thinking", "thinking": detail.get("thinking", ""), "thinkingSignature": detail.get("signature"), "redacted": detail.get("redacted", False)})
            for call in original.get("tool_calls", []):
                fn = call.get("function", {})
                names[call["id"]] = fn.get("name", "")
                content.append({"type": "toolCall", "id": call["id"], "name": fn.get("name"), "arguments": parse_tool_arguments(fn.get("arguments"))})
            message.update(api="pi-messages", provider="radius", model=model, stopReason="toolUse" if original.get("tool_calls") else "stop",
                           usage={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0,
                                  "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}})
        elif role == "tool":
            message.update(role="toolResult", toolCallId=original.get("tool_call_id"), toolName=names.get(original.get("tool_call_id"), ""), isError=original.get("is_error") is True)
        messages.append(message)
    tools = [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""), "parameters": tool["function"].get("parameters", {"type": "object", "properties": {}})} for tool in body.get("tools", []) if tool.get("type") == "function"]
    if tools:
        if not messages or messages[0]["role"] != "system":
            messages.insert(0, {"role": "system", "content": "", "timestamp": 0})
        messages[0]["toolsAdded"] = tools
    return {"messages": messages}


class RadiusProvider(KeyedProvider):
    id = "radius"
    display_name = "Radius"
    base_url = "https://radius.pi.dev"

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._inference_bases: dict[str, str] = {}

    def fetch_models(self) -> list[str]:
        rows: dict[str, dict[str, Any]] = {}
        for account in self.accounts:
            gateway = str(account.get("gateway") or self.base_url).rstrip("/")
            response = self.client.get(f"{gateway}/v1/config", headers=self.build_headers(account, ""), timeout=30)
            raise_for_status(response, provider=self.id)
            config = response.json()
            base = config.get("baseUrl")
            if not isinstance(base, str) or urlsplit(base).scheme not in ("http", "https"):
                raise ProviderError(502, "radius: gateway returned an invalid inference endpoint")
            self._inference_bases[account.id] = base.rstrip("/")
            for row in config.get("models", []):
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    rows.setdefault(row["id"], row)
        self.listing_rows.update(rows)
        return list(rows)

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if account.id not in self._inference_bases:
            self.fetch_models()
        base = self._inference_bases.get(account.id)
        if not base:
            raise ProviderError(502, "radius: no inference endpoint is configured")
        options = {target: body[source] for source, target in (("temperature", "temperature"), ("max_tokens", "maxTokens"),
                   ("reasoning_effort", "reasoning"), ("session_id", "sessionId"), ("tool_choice", "toolChoice")) if source in body}
        with self.client.stream("POST", f"{base}/messages", headers=self.build_headers(account, model), json={"model": model, "context": pi_context(body, model), "options": options}) as response:
            observe_response(self.id, response)
            raise_for_status(response, provider=self.id)
            for _, data in iter_sse(response.iter_lines()):
                if not data or data == "[DONE]":
                    continue
                event = json.loads(data)
                kind = event.get("type")
                index = event.get("contentIndex", 0)
                if kind in ("text_delta", "thinking_delta"):
                    yield {"type": "text_delta" if kind == "text_delta" else "reasoning_delta", "text": event.get("delta", "")}
                elif kind == "thinking_end" and event.get("contentSignature"):
                    yield {"type": "reasoning_details", "details": [{"type": "pi_thinking", "thinking": event.get("content", ""), "signature": event["contentSignature"], "redacted": event.get("redacted", False)}]}
                elif kind == "toolcall_start":
                    yield {"type": "tool_call_start", "index": index, "id": event["id"], "name": event["toolName"], "arguments": ""}
                elif kind == "toolcall_delta":
                    yield {"type": "tool_call_delta", "index": index, "arguments": event.get("delta", "")}
                elif kind == "error":
                    raise ProviderError(502, str(event.get("errorMessage") or "Radius stream failed"))
                elif kind == "done":
                    usage = event.get("usage", {})
                    prompt = sum(usage.get(key, 0) for key in ("input", "cacheRead", "cacheWrite"))
                    output = usage.get("output", 0)
                    yield {"type": "usage", "usage": {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output}}
                    yield {"type": "finish", "reason": {"toolUse": "tool_calls", "length": "length"}.get(event.get("reason"), "stop")}
                    return
            raise ProviderError(502, "radius: stream ended before done")

    def specs(self) -> dict[str, ModelSpec]:
        return {model: replace(spec_from_row(model, self.id, self.listing_rows.get(model, {})), wire="pi-messages") for model in self.models()}

    def refresh_account(self, account: Account) -> bool:
        from hoshimi.core.login.provider_oauth import refresh_provider_account

        return refresh_provider_account(self.id, self.client, account)
