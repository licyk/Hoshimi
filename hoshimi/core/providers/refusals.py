"""Provider business errors shared by HTTP and streaming protocol adapters."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from hoshimi.core.failure import is_context_overflow, is_model_saturation, is_policy_block
from hoshimi.core.providers.base import AuthFailed, ContextOverflow, ModelGone, ModelSaturated, ProviderError, QuotaExceeded, RequestThrottled


def provider_refusal(provider: str, payload: Any, *, status: int | None = None, retry_after: float | None = None) -> ProviderError | None:
    """Read error envelopes, including JSON nested inside Qoder's message field."""
    rows: list[dict[str, Any]] = []
    messages: list[str] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                messages.append(value)
            else:
                if isinstance(decoded, (dict, str)):
                    visit(decoded, depth + 1)
        elif isinstance(value, dict):
            rows.append(value)
            for key in ("error", "message", "msg", "status_msg", "error_msg", "data", "result", "body", "response", "base_resp"):
                if key in value:
                    visit(value[key], depth + 1)

    visit(payload)
    codes = {str(row[key]) for row in rows for key in ("code", "type", "status_code", "error_code") if isinstance(row.get(key), (str, int))}
    message = "; ".join(dict.fromkeys(messages)) or str(payload)
    kind = ""
    lower = message.lower()

    def failure(cls: type[ProviderError], http_status: int, code: str, *, scope: str = "account", retry: bool | None = None) -> ProviderError:
        error = cls(http_status, f"{provider}: {message}", retryable=retry is not False and http_status in (401, 429, 503, 529), retry_after=retry_after)
        error.scope = scope
        error.should_retry = retry
        error.detail.update(upstream_type=code, upstream_code=code, upstream_status=status, scope=scope)
        return error

    # Numeric business codes are provider-specific: the same number can mean
    # expired credentials at one service and exhausted credit at another.
    if provider in ("minimax", "minimax-api", "minimax-cn", "minimax-chat"):
        groups = (
            ({"1008", "2053", "2056", "2067"}, QuotaExceeded, 429, None),
            ({"1002", "1039", "1041", "1045", "2045", "2046", "2047", "2062"}, RequestThrottled, 429, None),
            ({"1004", "2049"}, AuthFailed, 401, None),
            ({"2022", "2069"}, ModelGone, 404, None),
            ({"2057", "2061", "2068"}, ProviderError, 403, False),
            ({"1026", "1027", "1046", "2013", "2014"}, ProviderError, 400, False),
            ({"1000", "1001", "1024", "1033", "2064", "2066"}, ProviderError, 503, None),
        )
        for group, cls, http_status, retry in groups:
            if matched := sorted(codes & group):
                return failure(cls, http_status, matched[0], retry=retry)
        base = next((row["base_resp"] for row in rows if isinstance(row.get("base_resp"), dict)), {})
        if base.get("status_code") not in (None, 0, "0"):
            return failure(ProviderError, status if status and status >= 400 else 502, str(base["status_code"]), retry=False)
    elif provider in ("zhipu", "zai", "zai-api", "zai-coding-cn"):
        groups = (
            ({"1113", "1304", "1308", "1309", "1310", "1314", "1316", "1317", "1318", "1319", "1320", "1321"}, QuotaExceeded, 429, None),
            ({"1302", "1303", "1305"}, RequestThrottled, 429, None),
            ({"1312"}, ModelSaturated, 429, None),
            ({"1000", "1001", "1003"}, AuthFailed, 401, None),
            ({"1211"}, ModelGone, 404, None),
            ({"1261"}, ContextOverflow, 400, False),
            ({"1005", "1220", "1311", "1313", "1315"}, ProviderError, 403, False),
            ({"1210", "1212", "1213", "1214", "1215", "1301"}, ProviderError, 400, False),
        )
        for group, cls, http_status, retry in groups:
            if matched := sorted(codes & group):
                return failure(cls, http_status, matched[0], retry=retry)
    elif provider == "trae":
        if "4008" in codes:
            return failure(QuotaExceeded, 429, "4008")
        if "1005" in codes and "plan" in lower:
            return failure(QuotaExceeded, 429, "1005", scope="account_model")
        if "4011" in codes:
            return failure(RequestThrottled, 429, "4011")
    elif provider in ("lobsterai", "loomy", "raccoon"):
        markers = ("积分不足", "余额不足", "额度已用完", "insufficient credit", "insufficient balance", "insufficient points", "credit exhausted", "out of credit", "freecreditsused", "free credits used")
        if "402" in codes or any(marker in lower.replace("_", " ") for marker in markers):
            return failure(QuotaExceeded, 429, "quota_exhausted")
        if provider == "lobsterai" and (matched := sorted(codes & {"40100", "40101"})):
            return failure(AuthFailed, 401, matched[0])
    quota = False
    scope = "account_model"
    if provider in ("opencode", "opencode-go"):
        kind = next((name for name in ("FreeUsageLimitError", "GoUsageLimitError", "FreeTierError") if name in codes), "")
        quota = kind in ("FreeUsageLimitError", "GoUsageLimitError")
        if kind == "FreeTierError":
            error = ProviderError(403, f"{provider}: {message}")
            error.code = "client_restricted"
            error.should_retry = False
            error.detail["upstream_type"] = kind
            return error
    elif provider == "zcode":
        if "3012" in codes or "3007" in codes:
            error = failure(ProviderError, 403, "3012" if "3012" in codes else "3007", retry=False)
            error.code = "risk_rejected" if "3012" in codes else "challenge"
            return error
        if "1002" in codes:
            return failure(AuthFailed, 401, "1002")
        if "3009" in codes:
            kind = "concurrency_limited"
        elif codes.intersection({"1005", "1113"}):
            kind, quota, scope = "quota_exhausted", True, "account"
    elif provider in ("qoder", "qoder-cn"):
        if "105" in codes:
            return failure(AuthFailed, 401, "105")
        if "10605" in codes:
            kind = "model_queued"
            waits = [retry_after] if retry_after is not None else []
            for key, scale in (("retry_after_ms", 0.001), ("retryAfterMs", 0.001), ("retryAfterSeconds", 1.0), ("retry_after_seconds", 1.0)):
                for row in rows:
                    value = row.get(key)
                    if not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value) and value >= 0:
                        waits.append(value * scale)
            retry_after = max(waits) if waits else None
        elif "110" in codes:
            kind, quota, scope = "quota_exhausted", True, "account"
    elif provider == "cline" and any(marker in message.lower() for marker in ("daily free limit", "free daily limit", "daily limit reached")):
        kind, quota = "daily_free_limit", True
        if retry_after is None:
            match = re.search(r"try again in\s+([^.;\n]*)", message, re.IGNORECASE)
            if match:
                tokens = re.findall(r"(\d+)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-z])", match[1], re.IGNORECASE)
                if tokens:
                    retry_after = sum(int(value) * {"h": 3600, "m": 60, "s": 1}[unit[0].lower()] for value, unit in tokens)
    if not kind:
        common = (
            ({"insufficient_quota", "quota_exceeded", "billing_hard_limit_reached"}, QuotaExceeded, 429, None),
            ({"rate_limit_error", "rate_limit_exceeded", "too_many_requests"}, RequestThrottled, 429, None),
            ({"overloaded_error"}, ModelSaturated, 529, None),
            ({"authentication_error", "invalid_api_key"}, AuthFailed, 401, None),
            ({"permission_error", "permission_denied"}, ProviderError, 403, False),
            ({"context_length_exceeded"}, ContextOverflow, 400, False),
        )
        for group, cls, http_status, retry in common:
            if matched := sorted(codes & group):
                return failure(cls, http_status, matched[0], retry=retry)
    if not kind and status == 429:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        if is_model_saturation(status, text) or is_policy_block(status, text) or is_context_overflow(status, text):
            return None
        kind = "rate_limited"
        scope = "account"
        quota = any(marker in message.lower() for marker in ("quota", "daily", "insufficient", "额度", "余额"))
    if not kind:
        return
    error_type = QuotaExceeded if quota else RequestThrottled
    error = error_type(429, f"{provider}: {message}", retryable=True, retry_after=retry_after)
    error.scope = scope
    error.detail.update({"upstream_type": kind, "upstream_status": status, "scope": scope})
    return error


def raise_provider_refusal(provider: str, payload: Any, *, status: int | None = None, retry_after: float | None = None) -> None:
    error = provider_refusal(provider, payload, status=status, retry_after=retry_after)
    if error is not None:
        raise error
