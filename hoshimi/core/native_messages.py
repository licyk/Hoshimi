"""Lossless Messages transport and observation, independent of server frameworks."""

from __future__ import annotations

import codecs
import json
import math
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from hoshimi.core.errors import UpstreamError
from hoshimi.core.failure import parse_retry_after
from hoshimi.core.net.identity import InferenceIdentity
from hoshimi.core.providers.base import Account, Provider, ProviderError
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.quota import observe_response


class MessagesCancellation:
    """Close the active upstream when the downstream reader disappears."""

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._response: httpx.Response | None = None

    def attach(self, response: httpx.Response | None) -> None:
        with self._lock:
            self._response = response
            cancelled = self.cancelled.is_set()
        if response is not None and cancelled:
            response.close()

    def close(self) -> None:
        self.cancelled.set()
        with self._lock:
            response = self._response
        if response is not None:
            # close() alone need not interrupt recv() in another thread. Each
            # HTTP/1 response owns its connection; leave HTTP/2 multiplexing alone.
            network = response.extensions.get("network_stream")
            if network is not None and response.http_version != "HTTP/2":
                connection = network.get_extra_info("socket")
                if connection is not None:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            response.close()


@dataclass
class MessagesRequest:
    """Client protocol metadata kept separate from the inference JSON."""

    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    query: list[tuple[str, str]] = field(default_factory=list)
    count_only: bool = False
    cancellation: MessagesCancellation = field(default_factory=MessagesCancellation)


def response_headers(headers: httpx.Headers) -> dict[str, str]:
    """Forward protocol/retry metadata, excluding hop-by-hop and secret headers."""
    names = {"content-type", "request-id", "x-request-id", "retry-after", "x-should-retry"}
    result = {key: value for key, value in headers.items() if key in names or key.startswith("anthropic-")}
    if "retry-after" in result:
        seconds = parse_retry_after(result["retry-after"])
        if seconds is not None and math.isfinite(seconds):
            result["retry-after"] = str(max(0, math.ceil(seconds)))
    return result


def usage_chunk(usage: dict[str, Any]) -> dict[str, Any]:
    """Observe native cache buckets using the shared statistics vocabulary."""
    def count(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    cached, written = count("cache_read_input_tokens"), count("cache_creation_input_tokens")
    prompt, output = count("input_tokens") + cached + written, count("output_tokens")
    return {"type": "usage", "usage": {
        "prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output,
        "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
    }}


class MessagesObserver:
    """Watch a side copy of SSE for stats without reconstructing client bytes."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._tail = ""
        self._data: list[str] = []
        self._event = ""
        self._usage: dict[str, Any] = {}
        self.terminal = False

    def feed(self, data: bytes) -> Iterator[dict[str, Any]]:
        lines = (self._tail + self._decoder.decode(data)).split("\n")
        self._tail = lines.pop()
        for line in lines:
            line = line.rstrip("\r")
            if line.startswith("event:"):
                self._event = line[6:].strip()
            elif line.startswith("data:"):
                self._data.append(line[5:].lstrip())
            elif not line:
                if self._data:
                    try:
                        event = json.loads("\n".join(self._data))
                    except ValueError:
                        event = None
                    if isinstance(event, dict):
                        yield from self.observe(event, self._event)
                self._event, self._data = "", []

    def observe(self, event: dict[str, Any], name: str = "") -> Iterator[dict[str, Any]]:
        kind = event.get("type") or name
        if kind == "message_start":
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("usage"), dict):
                self._usage.update(message["usage"])
                yield usage_chunk(self._usage)
        elif kind == "message_delta":
            if isinstance(event.get("usage"), dict):
                self._usage.update(event["usage"])
                yield usage_chunk(self._usage)
        elif kind == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict):
                if block.get("type") in ("tool_use", "server_tool_use"):
                    yield {"type": "tool_call_start"}
                elif block.get("text") or block.get("thinking"):
                    thinking = block.get("type") == "thinking"
                    yield {"type": "reasoning_delta" if thinking else "text_delta", "text": _text(block.get("thinking" if thinking else "text"))}
        elif kind == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict):
                kind = delta.get("type")
                # Lengths ride along so the live view can count what streamed;
                # nothing downstream of the observer forwards these chunks.
                if kind == "text_delta":
                    yield {"type": "text_delta", "text": _text(delta.get("text"))}
                elif kind == "thinking_delta":
                    yield {"type": "reasoning_delta", "text": _text(delta.get("thinking"))}
                elif kind == "input_json_delta":
                    yield {"type": "tool_call_delta", "arguments": _text(delta.get("partial_json"))}
        elif kind == "error":
            self.terminal = True
            yield {"type": "error", "error": event.get("error") or "Upstream Messages error"}
        elif kind == "message_stop":
            self.terminal = True
            yield {"type": "finish"}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def stream_native_messages(provider: Provider, account: Account, model: str, context: MessagesRequest) -> Iterator[dict[str, Any]]:
    """Use one account; rotation remains in Provider.stream_using."""
    endpoint = provider.messages_endpoint(account, model, count_only=context.count_only)
    if endpoint is None:
        raise UpstreamError(f"{provider.id}: this account does not support native Messages.")
    # Adapter credentials are authoritative. Never forward incoming gateway keys.
    headers = provider.messages_headers(account, model)
    if provider.id == "opencode-go":
        from hoshimi.core.providers.opencode import session_for_conversation

        caller = InferenceIdentity.from_headers(context.headers)
        headers["x-opencode-session"] = caller.session or session_for_conversation(str(context.body.get("session_id") or context.body.get("user") or "global"))
        if caller.user_agent:
            headers["User-Agent"] = caller.user_agent
    required = headers.get("anthropic-beta", "")
    headers.update({key: value for key, value in context.headers.items() if key.startswith("anthropic-")})
    supplied = headers.get("anthropic-beta", "")
    if required and required != supplied:
        headers["anthropic-beta"] = ",".join(value for value in dict.fromkeys([*supplied.split(","), *required.split(",")]) if value)
    payload = {**context.body, "model": model}
    if context.count_only:
        payload.pop("stream", None)
    with provider.client.stream("POST", endpoint, headers=headers, params=tuple(context.query), json=payload, follow_redirects=False) as response:
        context.cancellation.attach(response)
        try:
            observe_response(provider.id, response)
            if context.count_only and response.status_code in (404, 405, 501):
                from hoshimi.core.translate.messages import estimate_input_tokens

                yield {"type": "messages_response", "status": 200, "headers": {"content-type": "application/json", "x-hoshimi-token-count": "estimate"}}
                yield {"type": "messages_bytes", "data": json.dumps({"input_tokens": estimate_input_tokens(context.body)}).encode()}
                return
            try:
                raise_for_status(response, provider=provider.id)
            except ProviderError as exc:
                exc.response_body = response.content
                exc.response_headers = response_headers(response.headers)
                exc.should_retry = {"true": True, "false": False}.get(response.headers.get("x-should-retry", ""), exc.should_retry)
                raise
            stream = context.body.get("stream", False) and not context.count_only
            if stream:
                if "text/event-stream" not in response.headers.get("content-type", "").lower():
                    raise UpstreamError(f"{provider.id}: expected an upstream Messages event stream.")
                yield {"type": "messages_response", "status": response.status_code, "headers": response_headers(response.headers)}
                observer = MessagesObserver()
                for data in response.iter_bytes():
                    if context.cancellation.cancelled.is_set():
                        return
                    yield from observer.feed(data)
                    yield {"type": "messages_bytes", "data": data}
                if not observer.terminal and not context.cancellation.cancelled.is_set():
                    raise UpstreamError("Upstream Messages stream ended without message_stop or error.")
            else:
                data = response.read()
                try:
                    message = json.loads(data)
                except ValueError:
                    raise UpstreamError(f"{provider.id}: expected an upstream Messages JSON response.") from None
                yield {"type": "messages_response", "status": response.status_code, "headers": response_headers(response.headers)}
                if not context.count_only and isinstance(message, dict):
                    if message.get("content"):
                        yield {"type": "text_delta"}
                    if isinstance(message.get("usage"), dict):
                        yield usage_chunk(message["usage"])
                    yield {"type": "error", "error": message["error"]} if message.get("type") == "error" else {"type": "finish"}
                yield {"type": "messages_bytes", "data": data}
        except httpx.HTTPError:
            if context.cancellation.cancelled.is_set():
                return
            raise UpstreamError(f"{provider.id}: upstream Messages connection failed.") from None
        finally:
            context.cancellation.attach(None)
