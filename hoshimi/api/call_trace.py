"""Capture inference HTTP bodies before compression, including serialized SSE and error replies."""

from __future__ import annotations

import asyncio

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hoshimi.core.call_trace import ClientExchange, current_exchange, safe_headers


class CallTraceMiddleware:
    def __init__(self, app: ASGIApp, prefix: str = ""):
        self.app = app
        self.paths = {f"{prefix}{path}" for path in (
            "/v1/chat/completions", "/v1/responses", "/v1/messages", "/v1/classify",
            "/api/v1/playground/chat/completions",
        )}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in self.paths:
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope["headers"]}
        query = scope.get("query_string", b"").decode("latin-1")
        exchange = ClientExchange(scope["method"], scope["path"] + (f"?{query}" if query else ""), headers)

        async def capture_receive() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                exchange.request_body.data.extend(message.get("body", b""))
                exchange.request_body.complete = not message.get("more_body", False)
            return message

        async def capture_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                exchange.response = {"status": message["status"], "headers": safe_headers(
                    {key.decode("latin-1"): value.decode("latin-1") for key, value in message.get("headers", [])})}
            await send(message)
            if message["type"] == "http.response.body":
                exchange.response_body.data.extend(message.get("body", b""))
                exchange.response_body.complete = not message.get("more_body", False)

        token = current_exchange.set(exchange)
        try:
            await self.app(scope, capture_receive, capture_send)
        finally:
            current_exchange.reset(token)
            exchange.finished = True
            # The statistics may finish before the serializer emits its final SSE frames.
            for trace in exchange.traces:
                await asyncio.to_thread(trace.persist)
