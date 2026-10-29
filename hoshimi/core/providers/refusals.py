"""Provider business errors shared by HTTP and streaming protocol adapters."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from hoshimi.core.providers.base import ProviderError, QuotaExceeded, RequestThrottled


def raise_provider_refusal(provider: str, payload: Any, *, status: int | None = None, retry_after: float | None = None) -> None:
    """Read error envelopes, including JSON nested inside Qoder's message field."""
    if provider not in ("opencode", "opencode-go", "zcode", "cline", "qoder", "qoder-cn"):
        return
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
            for key in ("error", "message", "msg", "data", "result", "body", "response"):
                if key in value:
                    visit(value[key], depth + 1)

    visit(payload)
    codes = {str(row[key]) for row in rows for key in ("code", "type") if isinstance(row.get(key), (str, int))}
    message = "; ".join(dict.fromkeys(messages)) or str(payload)
    kind = ""
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
            raise error
    elif provider == "zcode":
        if "3009" in codes:
            kind = "concurrency_limited"
        elif codes.intersection({"1005", "1113"}):
            kind, quota, scope = "quota_exhausted", True, "account"
    elif provider.startswith("qoder"):
        if "10605" in codes:
            kind = "model_queued"
            if retry_after is None:
                for key, scale in (("retry_after_ms", 0.001), ("retryAfterMs", 0.001), ("retryAfterSeconds", 1.0)):
                    for row in rows:
                        value = row.get(key)
                        if not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value) and value >= 0:
                            retry_after = value * scale
                            break
                    if retry_after is not None:
                        break
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
    if not kind and status == 429:
        kind = "rate_limited"
        quota = any(marker in message.lower() for marker in ("quota", "daily", "insufficient", "额度", "余额"))
    if not kind:
        return
    error_type = QuotaExceeded if quota else RequestThrottled
    error = error_type(429, f"{provider}: {message}", retryable=True, retry_after=retry_after)
    error.scope = scope
    error.detail.update({"upstream_type": kind, "upstream_status": status, "scope": scope})
    raise error
