"""Classify upstream refusals without treating an edge rejection as a lost token."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ResponseFailure:
    kind: str
    scope: str
    action: str


def response_failure(response: httpx.Response) -> ResponseFailure | None:
    """Inspect a buffered response; successful event streams are never consumed."""
    status = response.status_code
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return ResponseFailure("challenge", "endpoint", "complete_browser_authorization")
    content_type = response.headers.get("content-type", "").lower()
    # Login authorization endpoints can legitimately return HTML, but callers
    # explicitly requesting JSON or SSE cannot use it as an API response.
    try:
        expected = response.request.headers.get("accept", "")
        stage = response.request.extensions.get("hoshimi_operation", "")
    except RuntimeError:
        expected = ""
        stage = ""
    api_html = "json" in expected or "event-stream" in expected or stage in (
        "catalog", "inference", "token_refresh", "token_exchange", "auth_poll", "balance", "checkin",
    )
    if "text/html" in content_type and (status >= 400 or api_html):
        return ResponseFailure("edge_rejected" if status >= 400 else "invalid_response", "endpoint", "check_endpoint_and_network")
    if status < 400:
        return None
    text = response.read().decode("utf-8", "replace")[:8192].strip().lower()
    if text.startswith(("<!doctype", "<html")):
        return ResponseFailure("edge_rejected", "endpoint", "check_endpoint_and_network")
    if any(marker in text for marker in ("freetiererror", "free tier can only be used from within", "only available via cline product surfaces")):
        return ResponseFailure("client_restricted", "endpoint", "check_client_compatibility")
    if any(marker in text for marker in ("regionerror", "unsupported_country", "not available in your country", "region is not supported")):
        return ResponseFailure("region_restricted", "endpoint", "check_provider_availability")
    if any(marker in text for marker in ("signature rejected", "signature mismatch")):
        return ResponseFailure("signature_rejected", "request", "check_request_integrity")
    return None


def reject_interception(response: httpx.Response, *, provider: str) -> None:
    """Stop interception before status-based account or model mutation."""
    failure = response_failure(response)
    if failure is None:
        return
    from hoshimi.core.providers.base import ProviderError

    error = ProviderError(response.status_code if response.is_error else 502, f"{provider}: {failure.kind}; {failure.action}")
    error.code = failure.kind
    error.should_retry = False
    error.detail.update({"kind": failure.kind, "scope": failure.scope, "action": failure.action})
    raise error


def refresh_token_rejected(response: httpx.Response) -> bool:
    """Only a credential verdict may permanently remove an account from rotation."""
    if response_failure(response) is not None:
        return False
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        return False
    codes: list[object] = [payload.get("error"), payload.get("code"), payload.get("type")]
    if isinstance(payload.get("error"), dict):
        codes.extend(payload["error"].get(key) for key in ("code", "type"))
    if any(code in ("invalid_client", "unauthorized_client", "access_denied") for code in codes if isinstance(code, str)):
        return False
    if any(code in ("invalid_grant", "invalid_refresh_token", "refresh_token_expired", "refresh_token_revoked") for code in codes if isinstance(code, str)):
        return True
    # A bare 403 is a permissions/edge verdict, never evidence of token expiry.
    # Retain the established 401 contract for providers which return no JSON.
    return response.status_code == 401


def failure_kind(response: httpx.Response) -> str:
    """A safe diagnostic label, never upstream prose or credentials."""
    failure = response_failure(response)
    if failure:
        return failure.kind
    return {401: "authentication", 402: "quota_exhausted", 403: "permission_denied", 429: "rate_limited"}.get(
        response.status_code, "upstream_error" if response.is_error else "ok",
    )
