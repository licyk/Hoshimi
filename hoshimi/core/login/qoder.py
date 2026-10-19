"""Qoder (intl + cn): a device code the station resolves by polling.

``{authBase}/device/selectAccounts`` is a pure URL build — challenge, nonce,
machine id and the public client id go on the query, nothing is fetched. The
station then watches ``{openApiBase}/api/v1/deviceToken/poll?nonce&verifier``
until the user approves; 404 there means "not yet", not "gone".

The ``machine_id`` is a self-generated UUID that must survive relogins: the
refresh body carries it and the station binds the device to it.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, POLL_MAX_NETWORK_FAILURES, LoginError, b64url, poll_pause

DEVICE_SELECT_PATH = "/device/selectAccounts"
POLL_PATH = "/api/v1/deviceToken/poll"

POLL_INTERVAL_SECONDS = 1.0
POLL_BUDGET_SECONDS = 300.0

_PRODUCTS: dict[str, tuple[str, str, str]] = {
    # auth base, open api base, public client id
    "qoder": ("https://qoder.com", "https://openapi.qoder.sh", "e883ade2-e6e3-4d6d-adf7-f92ceff5fdcb"),
    "qodercn": ("https://qoder.cn", "https://openapi.qoder.com.cn", "732aef47-9cf2-46a2-95fe-4cebb5d0d1fa"),
}


def _pkce() -> tuple[str, str]:
    """RFC 7636: a 43–128 char verifier from the unreserved set, S256 challenge."""
    import hashlib

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    length = 43 + secrets.randbelow(86)
    verifier = "".join(alphabet[secrets.randbelow(len(alphabet))] for _ in range(length))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, b64url(digest)


def _timestamp_ms(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and raw > 0:
        value = float(raw)
        return int(value if value > 1_000_000_000_000 else value * 1000)
    if isinstance(raw, str) and raw:
        import datetime as _dt

        try:
            return int(_dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    return None


def start_qoder_login(client: httpx.Client, report: Callable[[str], None], variant: str) -> dict[str, Any]:
    product = _PRODUCTS.get(variant)
    if product is None:
        raise LoginError(f"qoder: unknown product '{variant}'")
    auth_base, open_api_base, client_id = product

    machine_id = str(uuid.uuid4())
    nonce = str(uuid.uuid4())
    verifier, challenge = _pkce()
    login_url = (
        f"{auth_base}{DEVICE_SELECT_PATH}"
        f"?challenge={challenge}&challenge_method=S256&nonce={nonce}"
        f"&machine_id={machine_id}&client_id={client_id}"
    )
    report(login_url)

    poll_url = f"{open_api_base}{POLL_PATH}?nonce={nonce}&verifier={verifier}&challenge_method=S256"
    deadline = time.monotonic() + min(POLL_BUDGET_SECONDS, LOGIN_TIMEOUT_SECONDS)
    failures = 0
    payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        poll_pause(POLL_INTERVAL_SECONDS)
        try:
            polled = client.get(poll_url, headers={"Accept": "application/json"}, timeout=15.0)
            failures = 0
        except httpx.HTTPError as exc:
            failures += 1
            if failures >= POLL_MAX_NETWORK_FAILURES:
                raise LoginError(f"{variant}: cannot reach the login service ({exc}") from exc
            continue
        if polled.status_code == 404:
            continue  # the session simply has no token yet
        if polled.status_code >= 400:
            raise LoginError(f"{variant}: token polling failed (HTTP {polled.status_code})")
        try:
            body = polled.json()
        except ValueError:
            continue
        if not isinstance(body, dict):
            continue
        access = str(body.get("token") or body.get("device_token") or body.get("access_token") or "")
        if not access:
            continue  # 2xx but the approval has not landed yet
        payload = body
        break
    if payload is None:
        raise LoginError(f"{variant}: login timed out before authorization completed")

    credential: dict[str, Any] = {
        # The provider reads `security_oauth_token ?? access_token`; write both.
        "security_oauth_token": access,
        "access_token": access,
        "machine_id": machine_id,
    }
    refresh = payload.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        credential["refresh_token"] = refresh
    expires_at = _timestamp_ms(payload.get("expires_at") or payload.get("expiresAt"))
    if expires_at is not None:
        credential["expire_time"] = expires_at
    refresh_expires = _timestamp_ms(payload.get("refresh_token_expires_at") or payload.get("refreshTokenExpiresAt"))
    if refresh_expires is not None:
        credential["refresh_token_expire_time"] = refresh_expires
    uid = payload.get("user_id")
    if isinstance(uid, str) and uid:
        credential["uid"] = uid
    name = payload.get("user_name")
    if isinstance(name, str) and name:
        credential["nickname"] = name
    return credential
