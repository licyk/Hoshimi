"""Cline: WorkOS device code, then a register call that mints the station token.

Three steps: ``POST api.workos.com/user_management/authorize/device`` with the
public client id, poll ``.../authenticate`` with the device grant, then hand the
WorkOS pair to ``POST api.cline.bot/api/v1/auth/register`` — the station's own
``accessToken`` (a JWT prefixed ``workos:``) only comes back from that last call.

The prefix is part of the value: every request the station accepts carries
``Authorization: Bearer workos:<jwt>`` and the bare JWT answers 401.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, POLL_MAX_NETWORK_FAILURES, LoginError, poll_pause

WORK_OS_BASE = "https://api.workos.com"
WORK_OS_CLIENT_ID = "client_01K3A541FN8TA3EPPHTD2325AR"
DEVICE_PATH = "/user_management/authorize/device"
AUTHENTICATE_PATH = "/user_management/authenticate"
CLINE_API_BASE = "https://api.cline.bot"
REGISTER_PATH = "/api/v1/auth/register"
TOKEN_PREFIX = "workos:"

CLIENT_HEADERS = {
    "HTTP-Referer": "https://cline.bot",
    "X-Title": "Cline",
    "X-IS-MULTIROOT": "false",
    "X-CLIENT-TYPE": "cline-sdk",
}

_FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def _str(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _timestamp_ms(raw: Any) -> int | None:
    """Cline sends ``expiresAt`` as an ISO instant; accept seconds/millis too."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw * 1000) if raw < 1e12 else int(raw)
    if isinstance(raw, str) and raw:
        parsed = _iso_ms(raw)
        return parsed
    return None


def _iso_ms(text: str) -> int | None:
    import datetime as _dt

    try:
        moment = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(moment.timestamp() * 1000)


def start_cline_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    # 1. device authorization ------------------------------------------------
    try:
        response = client.post(
            f"{WORK_OS_BASE}{DEVICE_PATH}",
            data={"client_id": WORK_OS_CLIENT_ID},
            headers=_FORM,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"cline: device authorization request failed: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    device_code = _str(payload, "device_code")
    user_code = _str(payload, "user_code")
    verification_uri = _str(payload, "verification_uri", "verification_url")
    if response.status_code >= 400 or not device_code or not user_code or not verification_uri:
        raise LoginError(f"cline: device authorization failed (HTTP {response.status_code})")
    login_url = _str(payload, "verification_uri_complete") or verification_uri
    report(login_url)

    # 2. poll WorkOS ---------------------------------------------------------
    interval = max(1.0, float(payload.get("interval") or 5))
    deadline = time.monotonic() + min(float(payload.get("expires_in") or 900), LOGIN_TIMEOUT_SECONDS)
    failures = 0
    tokens: dict[str, str] | None = None
    while time.monotonic() < deadline:
        poll_pause(interval)
        try:
            polled = client.post(
                f"{WORK_OS_BASE}{AUTHENTICATE_PATH}",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": WORK_OS_CLIENT_ID,
                },
                headers=_FORM,
                timeout=30.0,
            )
            failures = 0
        except httpx.HTTPError as exc:
            failures += 1
            if failures >= POLL_MAX_NETWORK_FAILURES:
                raise LoginError(f"cline: cannot reach the login service ({exc})") from exc
            continue
        try:
            body = polled.json()
        except ValueError:
            body = {}
        if 200 <= polled.status_code < 300:
            access = _str(body, "access_token")
            refresh = _str(body, "refresh_token")
            if not access or not refresh:
                raise LoginError("cline: WorkOS answered without the token pair")
            tokens = {"access_token": access, "refresh_token": refresh}
            break
        error = _str(body, "error")
        if error in ("authorization_pending", ""):
            continue
        if error == "slow_down":
            interval += 1.0
            continue
        if error in ("access_denied", "expired_token", "invalid_grant"):
            raise LoginError(f"cline: authorization refused ({error})")
        raise LoginError(f"cline: token polling failed (HTTP {polled.status_code}, {error or 'unknown error'})")
    if tokens is None:
        raise LoginError("cline: login timed out before authorization completed")

    # 3. register with the station -------------------------------------------
    try:
        registered = client.post(
            f"{CLINE_API_BASE}{REGISTER_PATH}",
            json={"accessToken": tokens["access_token"], "refreshToken": tokens["refresh_token"]},
            headers={**CLIENT_HEADERS, "Content-Type": "application/json"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"cline: token registration failed: {exc}") from exc
    try:
        raw = registered.json()
    except ValueError:
        raw = {}
    if registered.status_code >= 400:
        raise LoginError(f"cline: token registration rejected (HTTP {registered.status_code})")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        raise LoginError("cline: registration response was not an object")

    access = _str(data, "accessToken", "access_token")
    if not access:
        raise LoginError("cline: registration returned no access token")
    credential: dict[str, Any] = {
        # The station wants the prefix on every request; store it as sent.
        "access_token": access if access.startswith(TOKEN_PREFIX) else TOKEN_PREFIX + access,
        "refresh_token": _str(data, "refreshToken", "refresh_token") or tokens["refresh_token"],
    }
    expires_at = _timestamp_ms(data.get("expiresAt") or data.get("expires_at") or data.get("expire_time"))
    if expires_at is not None:
        credential["expire_time"] = expires_at
    raw_info = data.get("userInfo")
    info = raw_info if isinstance(raw_info, dict) else {}
    account_id = _str(info, "clineUserId", "accountId") or _str(data, "accountId", "account_id")
    if account_id:
        # Balance needs the `usr-...` id; the JWT's own `sub` is a different one.
        credential["account_id"] = account_id
    email = _str(info, "email") or _str(data, "email")
    if email:
        credential["email"] = email
        credential["nickname"] = email
    else:
        nickname = " ".join(part for part in (_str(info, "firstName"), _str(info, "lastName")) if part)
        if nickname:
            credential["nickname"] = nickname
        elif account_id:
            credential["nickname"] = account_id
    return credential
