"""Safe catalogue failure summaries, including failures after HTTP succeeds."""

from __future__ import annotations

import time
from typing import Any

import httpx

from hoshimi.core.net.responses import failure_kind

_KINDS = frozenset({
    "challenge", "edge_rejected", "invalid_response", "client_restricted", "region_restricted",
    "signature_rejected", "authentication", "permission_denied", "rate_limited", "quota_exhausted",
    "upstream_error", "timeout", "proxy_error", "connection_error", "catalog_failed", "catalog_endpoint_missing",
})


def catalog_failure(exc: Exception | None, observed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep status and routing metadata, never exception text, URLs or response bodies."""
    row = dict(observed or {})
    status = row.get("status")
    kind = row.get("kind") if row.get("kind") in _KINDS else None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        kind = failure_kind(exc.response)
    elif isinstance(exc, httpx.TimeoutException):
        kind = "timeout"
    elif isinstance(exc, httpx.ProxyError):
        kind = "proxy_error"
    elif isinstance(exc, httpx.RequestError):
        kind = kind or "connection_error"
    elif isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
        kind = kind or "invalid_response"
    elif exc is not None:
        status = status if status is not None else getattr(exc, "status", None)
        code = getattr(exc, "code", None)
        kind = kind or (code if isinstance(code, str) and code in _KINDS else None)
    # A missing catalogue endpoint says nothing about a model's inference availability.
    if kind in (None, "ok", "upstream_error"):
        kind = {401: "authentication", 402: "quota_exhausted", 403: "permission_denied", 404: "catalog_endpoint_missing",
                405: "catalog_endpoint_missing", 429: "rate_limited"}.get(status, "upstream_error" if status and status >= 400 else "catalog_failed")
    request = None
    if isinstance(exc, httpx.HTTPError):
        try:
            request = exc.request
        except RuntimeError:
            pass
    result = {
        "operation": "catalog", "kind": kind, "status": status, "at": time.time(),
        "host": request.url.host if request is not None else row.get("host", ""),
        "route": row.get("route", "unknown"),
    }
    if row.get("transport"):
        result["transport"] = row["transport"]
    return result
