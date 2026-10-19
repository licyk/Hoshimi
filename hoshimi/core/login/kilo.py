"""Kilo device authorization; credentials use the existing account store."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, LoginError, LoginReporter, poll_pause

DEVICE_URL = "https://api.kilo.ai/api/device-auth/codes"


def start_kilo_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    response = client.post(DEVICE_URL, timeout=30)
    if response.is_error:
        raise LoginError(f"kilo: device authorization failed (HTTP {response.status_code})")
    data = response.json()
    code, url = data.get("code"), data.get("verificationUrl")
    if not isinstance(code, str) or not code or not isinstance(url, str) or not url:
        raise LoginError("kilo: device authorization response is incomplete")
    lifetime = min(float(data.get("expiresIn") or LOGIN_TIMEOUT_SECONDS), LOGIN_TIMEOUT_SECONDS)
    if isinstance(report, LoginReporter):
        report.device_code(url, code, lifetime)
    else:
        report(url)
    deadline = time.monotonic() + lifetime
    while time.monotonic() < deadline:
        if isinstance(report, LoginReporter):
            report.wait(3)
        else:
            poll_pause(3)
        polled = client.get(f"{DEVICE_URL}/{code}", timeout=30)
        if polled.status_code == 202:
            continue
        if polled.status_code in (403, 410):
            raise LoginError("kilo: authorization denied or expired")
        if polled.is_error:
            raise LoginError(f"kilo: device polling failed (HTTP {polled.status_code})")
        payload = polled.json()
        if payload.get("status") == "approved" and isinstance(payload.get("token"), str) and payload["token"]:
            # Kilo returns a long-lived key, not a renewable OAuth token.
            # Do not invent refresh_token/expires_at and schedule fake refreshes.
            return {"access_token": payload["token"]}
        if payload.get("status") in ("denied", "expired"):
            raise LoginError("kilo: authorization denied or expired")
    raise LoginError("kilo: authorization timed out")
