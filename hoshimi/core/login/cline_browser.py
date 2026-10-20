"""Cline extension browser flow, selectable alongside WorkOS device authorization."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from hoshimi.core.login.cline import CLINE_API_BASE, TOKEN_PREFIX, _timestamp_ms
from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, CallbackServer, LoginError, LoginReporter
from hoshimi.core.net.profiles import cline_headers
from hoshimi.core.net.responses import reject_interception


def start_cline_browser_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    """Exchange one browser authorization result once, preserving token rotation."""
    server = None
    for port in range(48801, 48812):
        try:
            server = CallbackServer("/auth", port=port)
            break
        except OSError:
            continue
    if server is None:
        raise LoginError("cline: no available callback port (48801–48811)")
    redirect = server.redirect_uri()
    headers = cline_headers(profile="extension")
    try:
        response = client.get(
            f"{CLINE_API_BASE}/api/v1/auth/authorize",
            params={"client_type": "extension", "callback_url": redirect, "redirect_uri": redirect},
            headers=headers, follow_redirects=False, timeout=30.0,
        )
        reject_interception(response, provider="cline")
        if response.is_redirect:
            url = response.headers.get("location", "")
        else:
            try:
                data = response.json()
                url = data.get("redirect_url", "") if isinstance(data, dict) else ""
            except ValueError:
                url = ""
        if response.is_error or not isinstance(url, str) or urlsplit(url).scheme != "https":
            raise LoginError(f"cline: invalid authorization response (HTTP {response.status_code})")
        report(url)
        answer = report.authorization(server) if isinstance(report, LoginReporter) else server.wait(LOGIN_TIMEOUT_SECONDS)
    finally:
        server.close()
    if answer.get("error"):
        raise LoginError("cline: authorization denied")
    code = answer.get("refreshToken") or answer.get("idToken") or answer.get("code")
    if not code:
        raise LoginError("cline: missing authorization code")
    payload = {"grant_type": "authorization_code", "code": code, "client_type": "extension", "redirect_uri": redirect}
    if answer.get("provider"):
        payload["provider"] = answer["provider"]
    response = client.post(f"{CLINE_API_BASE}/api/v1/auth/token", json=payload, headers=headers, timeout=30.0)
    reject_interception(response, provider="cline")
    try:
        raw = response.json()
        data = raw.get("data") if isinstance(raw, dict) else None
    except ValueError:
        raw = None
        data = None
    if response.is_error or not isinstance(raw, dict) or raw.get("success") is not True or not isinstance(data, dict) or not isinstance(data.get("accessToken"), str) or not data["accessToken"]:
        raise LoginError(f"cline: token exchange failed (HTTP {response.status_code}); start a new login")
    access = data["accessToken"]
    result: dict[str, Any] = {"access_token": access if access.startswith(TOKEN_PREFIX) else TOKEN_PREFIX + access, "client_profile": "extension"}
    if isinstance(data.get("refreshToken"), str):
        result["refresh_token"] = data["refreshToken"]
    expires = _timestamp_ms(data.get("expiresAt"))
    if expires is not None:
        result["expire_time"] = expires
    return result
