"""Raccoon Work: a WeChat QR login page polled by the gateway itself."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, POLL_MAX_NETWORK_FAILURES, LoginError, jwt_claims, poll_pause

API_BASE = "https://xiaohuanxiong.com"
AUTH_PREFIX = "/api/web/auth/v1"
POLL_SECONDS = 2.0


def _qr_login_url(code: str) -> str:
    return str(httpx.URL(f"{API_BASE}/login/mp", params={"code": code, "appname": "商汤小浣熊官网"}))


def _credential(data: dict[str, Any]) -> dict[str, Any]:
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise LoginError("raccoon: login response missing access_token")
    refresh = data.get("refresh_token")
    result: dict[str, Any] = {
        "access_token": access,
        "device_id": secrets.token_hex(16),
    }
    if isinstance(refresh, str) and refresh:
        result["refresh_token"] = refresh
    for key in ("refresh_token", "office_identity", "user_id", "nickname", "phone"):
        value = data.get(key)
        if isinstance(value, str) and value:
            result[key] = value
    exp = jwt_claims(access).get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool) and exp > 0:
        result["expires_at"] = str(int(exp * 1000))
    return result


def _enrich(client: httpx.Client, credential: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {credential['access_token']}",
        "X-Org-Code": "",
        "X-Raccoon-Language": "zh",
    }
    try:
        response = client.get(f"{API_BASE}{AUTH_PREFIX}/user_info", headers=headers, timeout=30.0)
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return credential
    if response.status_code >= 400 or not isinstance(payload, dict) or payload.get("code") != 0:
        return credential
    data = payload.get("data")
    if not isinstance(data, dict):
        return credential
    for source, target in (("id", "user_id"), ("name", "nickname"), ("office_identity", "office_identity"), ("phone", "phone")):
        value = data.get(source)
        if isinstance(value, str) and value:
            credential[target] = value
    return credential


def start_raccoon_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    """Open Raccoon's WeChat page and poll the matching server-side QR code."""
    code = secrets.token_hex(16)
    report(_qr_login_url(code))
    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    failures = 0
    while time.monotonic() < deadline:
        poll_pause(POLL_SECONDS)
        try:
            response = client.post(
                f"{API_BASE}{AUTH_PREFIX}/login_with_qrcode_code",
                json={"qrcode_code": code},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=30.0,
            )
            payload = response.json()
            failures = 0
        except (httpx.HTTPError, ValueError):
            failures += 1
            if failures >= POLL_MAX_NETWORK_FAILURES:
                raise LoginError("raccoon: cannot reach the QR login service")
            continue
        if not isinstance(payload, dict) or payload.get("code") != 0:
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        status = str(data.get("status") or "")
        if status == "canceled":
            raise LoginError("raccoon: authorization was canceled")
        if status != "success" or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            continue
        return _enrich(client, _credential(data))
    raise LoginError("raccoon: login timed out")
