"""ZCode: a server-mediated device flow — no local callback, no ``zcode://``.

``POST /api/v1/oauth/cli/init`` takes a self-generated 32-byte hex secret as its
Bearer and answers with a flow id plus an ``authorize_url`` to open in the
browser. The user's approval lands on the station's own callback; polling
``/api/v1/oauth/cli/poll/{flow_id}`` with the same secret eventually reports
``status: ready`` and hands back the JWT.

The ``device_mid`` is a self-generated UUID that must be stable per account —
inference endpoints require the header but do not bind its value, so the login
pins one and stores it with the credential.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, LoginError, poll_pause

ZCODE_ORIGIN = "https://zcode.z.ai"
INIT_PATH = "/api/v1/oauth/cli/init"
APP_VERSION = "3.14.3"

POLL_INTERVAL_FALLBACK_SECONDS = 2.0


def _headers(flow_secret: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {flow_secret}",
        "Content-Type": "application/json",
        "User-Agent": f"ZCode/{APP_VERSION}",
        "HTTP-Referer": ZCODE_ORIGIN,
        "X-ZCode-App-Version": APP_VERSION,
        "X-Platform": "win32",
    }


def start_zcode_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    flow_secret = secrets.token_hex(32)

    # 1. init -----------------------------------------------------------------
    try:
        response = client.post(
            f"{ZCODE_ORIGIN}{INIT_PATH}",
            json={"provider": "bigmodel"},
            headers=_headers(flow_secret),
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"zcode: cannot reach the authorization service: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    raw_data = payload.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    flow_id = str(data.get("flow_id") or "")
    authorize_url = str(data.get("authorize_url") or "")
    if response.status_code >= 400 or not flow_id:
        raise LoginError(f"zcode: authorization init failed (HTTP {response.status_code})")
    if not authorize_url.startswith("https://"):
        raise LoginError("zcode: authorization init returned no usable authorize_url")
    report(authorize_url)

    interval = data.get("poll_interval_sec")
    interval = float(interval) if isinstance(interval, (int, float)) and not isinstance(interval, bool) and interval >= 1 else POLL_INTERVAL_FALLBACK_SECONDS
    expires_at = data.get("expires_at")
    server_deadline = float(expires_at) - time.time() - 1 if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool) and expires_at > 0 else LOGIN_TIMEOUT_SECONDS
    deadline = time.monotonic() + max(1.0, min(LOGIN_TIMEOUT_SECONDS, server_deadline))

    # 2. poll -----------------------------------------------------------------
    while time.monotonic() < deadline:
        poll_pause(interval)
        try:
            polled = client.get(
                f"{ZCODE_ORIGIN}/api/v1/oauth/cli/poll/{flow_id}",
                headers=_headers(flow_secret),
                timeout=20.0,
            )
        except httpx.HTTPError:
            continue  # a blip is not a verdict; the official client retries too
        # Only a genuine 4xx (minus 408/429) is terminal.
        if 400 <= polled.status_code < 500 and polled.status_code not in (408, 429):
            raise LoginError(f"zcode: polling was refused (HTTP {polled.status_code})")
        if polled.status_code >= 500:
            continue
        try:
            body = polled.json()
        except ValueError:
            continue
        if not isinstance(body, dict) or body.get("code") != 0:
            continue
        inner = body.get("data")
        if not isinstance(inner, dict):
            continue
        status = str(inner.get("status") or "")
        if status == "pending":
            continue
        if status == "failed":
            raise LoginError("zcode: authorization was refused")
        if status != "ready":
            continue

        token = inner.get("token")
        user = inner.get("user") if isinstance(inner.get("user"), dict) else {}
        third_party = inner.get("bigmodel") if isinstance(inner.get("bigmodel"), dict) else inner.get("zai") if isinstance(inner.get("zai"), dict) else {}
        user_id = str(user.get("user_id") or user.get("id") or "")
        if not isinstance(token, str) or not token or not user_id:
            raise LoginError("zcode: ready response was missing the token or the user id")

        credential: dict[str, Any] = {
            "zcode_jwt": token,
            "device_mid": uuid.uuid4().hex,
            "user_id": user_id,
            "app_version": APP_VERSION,
            "source": "plugin",
        }
        third_party_access = third_party.get("access_token") or third_party.get("accessToken")
        if isinstance(third_party_access, str) and third_party_access:
            credential["bigmodel_access_token"] = third_party_access
        third_party_refresh = third_party.get("refresh_token") or third_party.get("refreshToken")
        if isinstance(third_party_refresh, str) and third_party_refresh:
            credential["refresh_token"] = third_party_refresh
        nickname = str(user.get("name") or user.get("email") or "")
        if nickname:
            credential["nickname"] = nickname
        return credential

    raise LoginError("zcode: login timed out before authorization completed")
