"""Per-call payload capture, scoped to iterator operations rather than worker threads."""

from __future__ import annotations

import base64
import json
import logging
import re
import zlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)
current_trace: ContextVar[CallTrace | None] = ContextVar("call_trace", default=None)
current_exchange: ContextVar[ClientExchange | None] = ContextVar("call_exchange", default=None)
_SECRET = re.compile(r"authorization|cookie|api[-_]?key|token|secret|password|credential|signature|^key$|^dpop$", re.IGNORECASE)


def safe_headers(headers: Any) -> dict[str, str]:
    return {key: "***" if _SECRET.search(key) else value for key, value in dict(headers).items()}


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    query = urlencode([(key, "***" if _SECRET.search(key) else value) for key, value in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, host, parts.path, query, ""))


def json_value(value: Any) -> Any:
    """Snapshot mutable chunks; retain binary protocol data explicitly as base64."""
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


class BodyCapture:
    def __init__(self) -> None:
        self.data = bytearray()
        self.complete = False
        self.decoded = False

    def view(self, headers: dict[str, str] | None = None) -> dict[str, Any]:
        raw = bytes(self.data)
        try:
            # HTTPX decodes compression here; transport capture itself never consumes ahead.
            decoded = httpx.Response(200, headers=headers, content=raw).content if headers and not self.decoded else raw
            text = decoded.decode("utf-8")
            encoding = "utf-8"
        except (UnicodeError, httpx.DecodingError):
            text, encoding = base64.b64encode(raw).decode("ascii"), "base64"
        return {"body": text, "encoding": encoding, "complete": self.complete, "bytes": len(raw)}


class ClientExchange:
    def __init__(self, method: str, url: str, headers: dict[str, str]):
        self.request = {"method": method, "url": safe_url(url), "headers": safe_headers(headers)}
        self.secrets = secret_values(headers, url)
        self.request_body = BodyCapture()
        self.response: dict[str, Any] = {}
        self.response_body = BodyCapture()
        self.finished = False
        self.traces: list[CallTrace] = []


class CallTrace:
    def __init__(self, call_id: str, save: Callable[[str, bytes], None]):
        self.id, self.save = call_id, save
        self.exchange = current_exchange.get()
        if self.exchange is not None:
            self.exchange.traces.append(self)
        self.request: Any = None
        self.chunks: list[Any] = []
        self.complete = False
        self.upstream: list[dict[str, Any]] = []
        self.route: dict[str, Any] = {}
        self.secrets: set[str] = set(self.exchange.secrets) if self.exchange else set()

    def view(self) -> dict[str, Any]:
        exchange = self.exchange
        result = {
            "request": json_value(self.request),
            "response": {"chunks": self.chunks, "complete": self.complete},
            "client_request": {**exchange.request, **exchange.request_body.view()} if exchange else None,
            "client_response": {**exchange.response, **exchange.response_body.view(), "finished": exchange.finished} if exchange else None,
            "upstream": [{key: (value.view(row.get("response", {}).get("headers")) if key == "response_body" else value)
                          for key, value in row.items()} for row in self.upstream],
        }
        return redact(result, self.secrets)

    def pack(self) -> bytes | None:
        try:
            return zlib.compress(json.dumps(self.view(), ensure_ascii=False).encode("utf-8"))
        except Exception:
            # Observability must never replace the model's answer with a storage error.
            logger.warning("Could not encode call payloads", exc_info=True)
            return None

    def persist(self) -> None:
        data = self.pack()
        if data is not None:
            try:
                self.save(self.id, data)
            except Exception:
                logger.warning("Could not save call payloads", exc_info=True)


class CapturedStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, body: BodyCapture):
        self.stream, self.body = stream, body

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.stream:
            self.body.data.extend(chunk)
            yield chunk
        self.body.complete = True

    def close(self) -> None:
        self.stream.close()


class TraceTransport(httpx.BaseTransport):
    """Capture each HTTP attempt, including redirects and failed attempts, without buffering delivery."""

    def __init__(self, transport: httpx.BaseTransport, provider: str):
        self.transport, self.provider = transport, provider

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        trace = current_trace.get()
        if trace is None or request.extensions.get("hoshimi_operation") not in ("inference", "request"):
            return self.transport.handle_request(request)
        body = BodyCapture()
        trace.secrets.update(secret_values(request.headers, str(request.url)))
        try:
            body.data.extend(request.content)
            body.complete = True
        except httpx.RequestNotRead:
            # Unusual streaming uploads are captured only as the transport reads them.
            assert isinstance(request.stream, httpx.SyncByteStream)
            request.stream = CapturedStream(request.stream, body)
        row: dict[str, Any] = {
            "provider": self.provider, **trace.route,
            "request": {"method": request.method, "url": safe_url(str(request.url)), "headers": safe_headers(request.headers)},
            "response_body": BodyCapture(),
        }
        trace.upstream.append(row)
        try:
            response = self.transport.handle_request(request)
            row["response"] = {"status": response.status_code, "headers": safe_headers(response.headers)}
            trace.secrets.update(secret_values(response.headers, ""))
            assert isinstance(response.stream, httpx.SyncByteStream)
            if response.is_stream_consumed:
                row["response_body"].data.extend(response.content)
                row["response_body"].complete = True
                # Already decoded by HTTPX (notably mock/embedded transports).
                row["response_body"].decoded = True
            else:
                response.stream = CapturedStream(response.stream, row["response_body"])
            return response
        except Exception as exc:
            row["error"] = type(exc).__name__
            raise
        finally:
            row["request"].update(body.view())

    def close(self) -> None:
        self.transport.close()


def secret_values(headers: Any, url: str) -> set[str]:
    values = [value for key, value in dict(headers).items() if _SECRET.search(key)]
    values.extend(value for key, value in parse_qsl(urlsplit(url).query) if _SECRET.search(key))
    result = set(values)
    for value in values:
        if value.lower().startswith(("bearer ", "basic ")):
            result.add(value.split(" ", 1)[1])
        if "=" in value:
            result.update(part.strip().split("=", 1)[1] for part in value.split(";") if "=" in part)
    return {value for value in result if len(value) >= 4}


def redact(value: Any, secrets: set[str]) -> Any:
    """Mask known transport credentials even if an upstream echoes one in its error body."""
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    return value
