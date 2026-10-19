"""The expiry shapes stations return, converted into the one unit stored.

Stations disagree about time: some send ``expires_in`` in seconds, some an ISO
``expiresAt``, some an epoch they half the time mean in seconds and half in
milliseconds, and a JWT carries its own ``exp`` claim. Everything the gateway
decides about renewal hangs off one field, ``expires_at`` in milliseconds, so
the conversion lives here — a leaf module both the refresher and the adapters
can import without a cycle.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from typing import Any

_SECONDS_BELOW = 10**12
"""Anything at or below this is a seconds-scale timestamp; above it, milliseconds."""


def as_ms(raw: Any) -> int | None:
    """An epoch in milliseconds, accepting seconds, ms, or their string forms."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = int(raw)
    elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        value = int(raw.strip())
    else:
        return None
    if value <= 0:
        return None
    return value * 1000 if value < _SECONDS_BELOW else value


def iso_as_ms(raw: Any) -> int | None:
    """An RFC 3339 / ISO 8601 instant in milliseconds, or None when unparseable."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def jwt_exp_ms(token: Any) -> int | None:
    """The ``exp`` claim of a JWT in milliseconds; None when it is not one."""
    if not isinstance(token, str) or token.count(".") < 1:
        return None
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    return as_ms(claims.get("exp"))


def set_from_seconds(account: Any, expires_in: Any) -> bool:
    """Record an absolute expiry from an upstream ``expires_in`` (seconds)."""
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
        return False
    account.set("expires_at", str(int((time.time() + expires_in) * 1000)))
    return True


def set_from_ms(account: Any, raw: Any) -> bool:
    """Record an absolute expiry from an already-absolute value."""
    ms = as_ms(raw)
    if ms is None:
        return False
    account.set("expires_at", str(ms))
    return True


def set_from_iso(account: Any, raw: Any) -> bool:
    """Record an absolute expiry from an ISO instant."""
    ms = iso_as_ms(raw)
    if ms is None:
        return False
    account.set("expires_at", str(ms))
    return True


def set_from_jwt(account: Any, token: Any) -> bool:
    """Record an absolute expiry from a token's own ``exp`` claim."""
    ms = jwt_exp_ms(token)
    if ms is None:
        return False
    account.set("expires_at", str(ms))
    return True
