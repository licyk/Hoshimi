"""CodeBuddy / WorkBuddy: an external login page whose state the station polls.

No redirect comes back to us: ``POST /v2/plugin/auth/state`` mints a ``state``
and an ``authUrl``, the user finishes in the browser, and the plugin polls
``/v2/plugin/auth/token?state=`` until the token appears (11217 = not yet), then
``/v2/plugin/login/account?state=`` for the profile (12151 = not yet).

The two products are the same protocol on two hosts; the variant picks which,
and the request headers (``X-Domain``, ``User-Agent``) follow it.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, LoginError, poll_pause
from hoshimi.core.providers.buddy import BuddyProvider, WorkBuddyProvider

AUTH_STATE_PATH = "/v2/plugin/auth/state"
AUTH_TOKEN_PATH = "/v2/plugin/auth/token"
LOGIN_ACCOUNT_PATH = "/v2/plugin/login/account"

CODE_TOKEN_NOT_READY = 11217
CODE_ACCOUNT_NOT_READY = 12151

POLL_INTERVAL_SECONDS = 1.0
LOGIN_BUDGET_SECONDS = 300.0

_PRODUCTS: dict[str, tuple[type[BuddyProvider], str]] = {
    # The ``platform`` query the state endpoint keys on, per product.
    "buddy": (BuddyProvider, "ide"),
    "workbuddy": (WorkBuddyProvider, "workbuddy-ai"),
}


def _product(variant: str) -> tuple[type[BuddyProvider], str]:
    entry = _PRODUCTS.get(variant)
    if entry is None:
        raise LoginError(f"buddy: unknown product '{variant}'")
    return entry


def _envelope(payload: Any) -> tuple[int, Any, str]:
    """Every answer is ``{code, message, data}``; pull the three apart."""
    if not isinstance(payload, dict):
        return -1, None, "response was not an object"
    code = payload.get("code")
    code = int(code) if isinstance(code, (int, float)) and not isinstance(code, bool) else 0
    data = payload.get("data")
    message = str(payload.get("message") or payload.get("msg") or "")
    return code, data, message


def _decorate_login_url(auth_url: str, variant: str) -> str:
    """WorkBuddy wants a plugin version and a fresh session id on the link."""
    if variant != "workbuddy":
        return auth_url
    try:
        parts = httpx.URL(auth_url)
    except ValueError:
        return auth_url
    params = [(key, value) for key, value in parts.params.multi_items() if key not in ("version", "loginSessionId")]
    params += [("version", "5.5.2"), ("loginSessionId", uuid.uuid4().hex)]
    return str(parts.copy_with(params=params))


def _jwt_claim(token: str, claim: str) -> str:
    """The profile endpoint often returns an empty nickname; the JWT has it."""
    import base64
    import json

    pieces = token.split(".")
    if len(pieces) < 2:
        return ""
    padded = pieces[1] + "=" * (-len(pieces[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return ""
    value = payload.get(claim) if isinstance(payload, dict) else None
    return value if isinstance(value, str) else ""


def _absolute_ms(record: dict[str, Any], absolute_key: str, relative_key: str) -> str:
    raw = record.get(absolute_key)
    if isinstance(raw, str) and raw:
        try:
            parsed = float(raw)
        except ValueError:
            import datetime as _dt

            try:
                parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                return ""
        return str(int(parsed if parsed > 1_000_000_000_000 else parsed * 1000))
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        value = float(raw)
        return str(int(value if value > 1_000_000_000_000 else value * 1000))
    relative = record.get(relative_key)
    if isinstance(relative, (int, float)) and not isinstance(relative, bool) and relative > 0:
        return str(int((time.time() + float(relative)) * 1000))
    return ""


def start_buddy_login(client: httpx.Client, report: Callable[[str], None], variant: str) -> dict[str, Any]:
    cls, platform = _product(variant)
    endpoint = cls.endpoint
    ua = cls.default_user_agent
    anonymous = {
        "X-Domain": cls.api_domain,
        "X-No-Authorization": "true",
        "X-No-User-Id": "true",
        "X-No-Enterprise-Id": "true",
        "X-No-Department-Info": "true",
        "User-Agent": ua,
    }

    # 1. mint the state -------------------------------------------------------
    try:
        response = client.post(
            f"{endpoint}{AUTH_STATE_PATH}",
            params={"platform": platform},
            headers=anonymous,
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"{variant}: auth state request failed: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    code, data, message = _envelope(payload)
    if response.status_code != 200 or not isinstance(data, dict):
        raise LoginError(f"{variant}: auth state request failed (HTTP {response.status_code}, {message or f'code={code}'})")
    state = str(data.get("state") or "")
    auth_url = str(data.get("authUrl") or "")
    if not state or not auth_url:
        raise LoginError(f"{variant}: auth state response had no state or authUrl")
    login_url = _decorate_login_url(auth_url, variant)
    report(login_url)

    # 2. poll for the token ---------------------------------------------------
    deadline = time.monotonic() + min(LOGIN_BUDGET_SECONDS, LOGIN_TIMEOUT_SECONDS)
    token: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        poll_pause(POLL_INTERVAL_SECONDS)
        try:
            polled = client.get(
                f"{endpoint}{AUTH_TOKEN_PATH}",
                params={"state": state},
                headers={"X-No-Authorization": "true", "User-Agent": ua},
                timeout=15.0,
            )
        except httpx.HTTPError:
            continue  # a blip must not abort a login the user is mid-way through
        try:
            body = polled.json()
        except ValueError:
            body = {}
        polled_code, data, message = _envelope(body)
        if polled.status_code == 200 and isinstance(data, dict):
            token = data
            break
        if polled_code == CODE_TOKEN_NOT_READY:
            continue
        raise LoginError(f"{variant}: token polling failed (HTTP {polled.status_code}, code={polled_code} {message})")
    if token is None:
        raise LoginError(f"{variant}: login timed out waiting for the token")

    access = str(token.get("accessToken") or "")
    refresh = str(token.get("refreshToken") or "")
    if not access:
        raise LoginError(f"{variant}: token response had no accessToken")

    # 3. poll for the account -------------------------------------------------
    account_data: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        poll_pause(POLL_INTERVAL_SECONDS)
        try:
            polled = client.get(
                f"{endpoint}{LOGIN_ACCOUNT_PATH}",
                params={"state": state},
                headers={
                    "X-Domain": str(token.get("domain") or cls.api_domain),
                    "Authorization": f"Bearer {access}",
                    "X-No-User-Id": "true",
                    "X-No-Enterprise-Id": "true",
                    "User-Agent": ua,
                },
                timeout=15.0,
            )
        except httpx.HTTPError:
            continue
        try:
            body = polled.json()
        except ValueError:
            body = {}
        polled_code, data, message = _envelope(body)
        if polled.status_code == 200 and isinstance(data, dict):
            account_data = data
            break
        if polled_code == CODE_ACCOUNT_NOT_READY:
            continue
        raise LoginError(f"{variant}: account polling failed (HTTP {polled.status_code}, code={polled_code} {message})")
    if account_data is None:
        raise LoginError(f"{variant}: login timed out waiting for the account")

    credential: dict[str, Any] = {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": str(token.get("tokenType") or "Bearer"),
        "expires_at": _absolute_ms(token, "expiresAt", "expiresIn"),
        "refresh_expires_at": _absolute_ms(token, "refreshExpiresAt", "refreshExpiresIn"),
        "domain": str(token.get("domain") or ""),
    }
    uid = str(account_data.get("uid") or "")
    credential["user_id"] = uid or _jwt_claim(access, "sub")
    nickname = str(account_data.get("nickname") or "") or _jwt_claim(access, "nickname") or _jwt_claim(access, "preferred_username")
    if nickname:
        credential["nickname"] = nickname
    enterprise_id = str(account_data.get("enterpriseId") or "")
    if enterprise_id:
        credential["enterprise_id"] = enterprise_id
    credential["account_type"] = str(account_data.get("type") or "personal")
    return credential
