"""TRAE: a loopback callback that hands back tokens, not an authorization code.

The station's ``/authorization`` page redirects to ``http://127.0.0.1:<port>/authorize``
carrying ``refreshToken`` / ``userJwt`` / ``userInfo`` directly — there is no
code to exchange afterwards, so no PKCE, no state. What is exchanged is the
refresh token: ``ExchangeToken`` mints the access token (and rotates the
refresh one), then ``GetUserInfo`` fills in the profile.

The port prefers 18080 (the Go client's default) but falls back to whatever the
OS hands out — the redirect URI travels in the login URL, so a different port
is harmless, while a hard failure on a busy machine would be fatal.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, CallbackServer, LoginError, LoginReporter

CONSOLE_HOST = "https://www.trae.cn"
OAUTH_HOST = "https://api.trae.com.cn"
EXCHANGE_PATH = "/cloudide/api/v3/trae/oauth/ExchangeToken"
USER_INFO_PATH = "/cloudide/api/v3/trae/GetUserInfo"
CALLBACK_PATH = "/authorize"
CLIENT_ID = "en1oxy7wnw8j9n"
IDE_VERSION = "0.1.52"
PREFERRED_CALLBACK_PORT = 18080


def _machine_trace_id(machine_id: str, device_id: str) -> str:
    joined = machine_id + device_id
    return joined[-16:] if len(joined) >= 16 else joined.zfill(16)


def _parse_json_param(raw: Any) -> dict[str, Any]:
    """The callback's JSON params arrive double-encoded often enough to matter."""
    if not isinstance(raw, str) or not raw:
        return {}
    from urllib.parse import unquote

    candidates = [raw]
    once = unquote(raw)
    if once != raw:
        candidates.append(once)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _fix_nickname(raw: str, uid: str) -> str:
    """TRAE's callback mangles CJK nicknames; only trust a name that looks intact."""
    if raw and any("一" <= ch <= "鿿" for ch in raw):
        return raw
    if raw:
        return raw
    return f"user-{uid[-4:]}" if uid else ""


def start_trae_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    machine_id = secrets.token_hex(16)
    device_id = secrets.token_hex(16)
    # Port 18080 is the reference default, but a busy machine must not make
    # login impossible — fall back to an ephemeral port.
    try:
        server = CallbackServer(CALLBACK_PATH, port=PREFERRED_CALLBACK_PORT)
    except OSError:
        server = CallbackServer(CALLBACK_PATH)
    try:
        callback_url = server.redirect_uri()
        params = {
            "login_version": "1",
            "auth_from": "solo",
            "login_channel": "native_ide",
            "plugin_version": "2.3.62834",
            "auth_type": "local",
            "client_id": CLIENT_ID,
            "redirect": "0",
            "login_trace_id": _machine_trace_id(machine_id, device_id),
            # The parameter name is `auth_callback_url`, not `callback_url`.
            "auth_callback_url": callback_url,
            "machine_id": machine_id,
            "device_id": device_id,
            "x_device_id": device_id,
            "x_machine_id": machine_id,
            "x_device_brand": "PC",
            "x_device_type": "PC",
            "x_os_version": "1.0",
            "x_app_version": IDE_VERSION,
            "x_app_type": "stable",
        }
        login_url = httpx.URL(f"{CONSOLE_HOST}/authorization", params=params).__str__()
        report(login_url)
        query = report.authorization(server) if isinstance(report, LoginReporter) else server.wait(LOGIN_TIMEOUT_SECONDS)
    finally:
        server.close()

    refresh_token = str(query.get("refreshToken") or "")
    user_jwt = _parse_json_param(query.get("userJwt"))
    if not refresh_token:
        refresh_token = str(user_jwt.get("RefreshToken") or "")
    jwt_access = str(user_jwt.get("Token") or "")
    user_info = _parse_json_param(query.get("userInfo"))
    uid = str(user_info.get("UserID") or "")
    nickname_raw = str(user_info.get("ScreenName") or "")
    enterprise_id = str(user_info.get("TenantID") or "")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"Trae/{IDE_VERSION}",
    }
    access_token = jwt_access
    expires_at = ""
    if refresh_token:
        try:
            exchanged = client.post(
                f"{OAUTH_HOST}{EXCHANGE_PATH}",
                json={"ClientID": CLIENT_ID, "RefreshToken": refresh_token, "ClientSecret": "-", "UserID": ""},
                headers=headers,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise LoginError(f"trae: ExchangeToken failed: {exc}") from exc
        if exchanged.status_code >= 400:
            raise LoginError(f"trae: ExchangeToken failed (HTTP {exchanged.status_code})")
        try:
            payload = exchanged.json()
        except ValueError:
            payload = {}
        raw_result = payload.get("Result")
        result = raw_result if isinstance(raw_result, dict) else {}
        access_token = str(result.get("Token") or result.get("token") or result.get("accessToken") or "")
        rotated = str(result.get("RefreshToken") or result.get("refresh_token") or "")
        if rotated:
            refresh_token = rotated
        expire_at = result.get("TokenExpireAt") or result.get("tokenExpireAt")
        expire_duration = result.get("TokenExpireDuration") or result.get("tokenExpireDuration")
        import time as _time

        if isinstance(expire_at, (int, float)) and not isinstance(expire_at, bool) and expire_at > 0:
            expires_at = str(int(expire_at if expire_at > 1e12 else expire_at * 1000))
        elif isinstance(expire_duration, (int, float)) and not isinstance(expire_duration, bool) and expire_duration > 0:
            expires_at = str(int((_time.time() + expire_duration) * 1000))
    if not access_token:
        raise LoginError("trae: no access token after the exchange")

    # GetUserInfo enriches the profile; a failure is not fatal.
    try:
        info_response = client.post(
            f"{OAUTH_HOST}{USER_INFO_PATH}",
            json={"ReqSource": "IDE", "IDEVersion": IDE_VERSION},
            headers={**headers, "X-Cloudide-Token": access_token},
            timeout=30.0,
        )
        if info_response.status_code < 400:
            info_payload = info_response.json()
            info_result = info_payload.get("Result") if isinstance(info_payload.get("Result"), dict) else {}
            uid = str(info_result.get("UserID") or info_result.get("userId") or uid)
            nickname_raw = str(info_result.get("ScreenName") or info_result.get("screenName") or nickname_raw)
            enterprise_id = str(info_result.get("EnterpriseID") or info_result.get("enterpriseId") or enterprise_id)
            phone = str(info_result.get("NonPlainTextMobile") or "")
            email = str(info_result.get("NonPlainTextEmail") or "")
    except (httpx.HTTPError, ValueError):
        pass  # the callback's own values still stand
    if not uid:
        raise LoginError("trae: neither the callback nor GetUserInfo named the user")

    credential: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "uid": uid,
        "machine_id": machine_id,
        "device_id": device_id,
    }
    if expires_at:
        credential["expires_at"] = expires_at
    nickname = _fix_nickname(nickname_raw, uid)
    if nickname:
        credential["nickname"] = nickname
    if enterprise_id:
        credential["enterprise_id"] = enterprise_id
    if phone:
        credential["phone"] = phone
    if email:
        credential["email"] = email
    return credential
