"""MiniMax: a device code that also proves a PKCE verifier.

``POST account.minimax.cn/oauth2/device/code`` takes an S256 challenge and
answers with a device code plus a user-facing link; the token then only comes
back from ``/oauth2/token`` once the browser side is done, and the verifier must
match the challenge that was sent up front.

The station answers in two shapes: HTTP 200 with a ``status`` field while the
user is still thinking, and the OAuth-standard non-200 with an ``error`` field.
Both are polled, and both must end in a Bearer grant whose scope still includes
``agent.default`` — a token without it cannot call the model endpoint.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, POLL_MAX_NETWORK_FAILURES, LoginError, pkce_pair, poll_pause

MINIMAX_ACCOUNT_HOST = "https://account.minimax.cn"
DEVICE_CODE_PATH = "/oauth2/device/code"
TOKEN_PATH = "/oauth2/token"
CLIENT_ID = "mcode-public"
SCOPE = "agent.default"
AUDIENCE = "agent-backend"

_FORM = {"Content-Type": "application/x-www-form-urlencoded"}
SLOW_DOWN_EXTRA_SECONDS = 5.0


def _text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def start_minimax_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    verifier, challenge = pkce_pair()

    # 1. request the device code with the challenge --------------------------------
    try:
        response = client.post(
            f"{MINIMAX_ACCOUNT_HOST}{DEVICE_CODE_PATH}",
            data={
                "client_id": CLIENT_ID,
                "scope": SCOPE,
                "audience": AUDIENCE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            headers=_FORM,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"minimax: device code request failed: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    device_code = _text(payload, "device_code")
    if response.status_code >= 400 or not device_code:
        raise LoginError(f"minimax: device code request failed (HTTP {response.status_code})")
    # The complete URI carries the user code; the bare one asks for it by hand.
    login_url = _text(payload, "verification_uri_complete") or _text(payload, "verification_uri", "verification_url")
    if not login_url:
        raise LoginError("minimax: device code response had no verification link")
    report(login_url)

    # 2. poll the token endpoint ---------------------------------------------
    interval = max(1.0, float(payload.get("interval") or 5))
    expires_in = float(payload.get("expires_in") or 900)
    deadline = time.monotonic() + min(expires_in, LOGIN_TIMEOUT_SECONDS)
    failures = 0
    grant: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        poll_pause(interval)
        try:
            polled = client.post(
                f"{MINIMAX_ACCOUNT_HOST}{TOKEN_PATH}",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": CLIENT_ID,
                    "code_verifier": verifier,
                },
                headers=_FORM,
                timeout=30.0,
            )
            failures = 0
        except httpx.HTTPError as exc:
            failures += 1
            if failures >= POLL_MAX_NETWORK_FAILURES:
                raise LoginError(f"minimax: cannot reach the login service ({exc})") from exc
            continue
        try:
            body = polled.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        status = _text(body, "status")
        error = _text(body, "error")

        # Shape one: HTTP 200 with a status field.
        if 200 <= polled.status_code < 300:
            if status in ("pending", "") and not body.get("access_token"):
                continue
            if status == "slow_down":
                interval += SLOW_DOWN_EXTRA_SECONDS
                continue
            if status in ("denied", "access_denied"):
                raise LoginError("minimax: authorization was denied")
            if status in ("expired", "expired_token"):
                raise LoginError("minimax: the device code expired before authorization")
            if body.get("access_token"):
                grant = body
                break
            continue

        # Shape two: the OAuth-standard error body.
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += SLOW_DOWN_EXTRA_SECONDS
            continue
        if error in ("access_denied", "denied"):
            raise LoginError("minimax: authorization was denied")
        if error in ("expired_token", "expired"):
            raise LoginError("minimax: the device code expired before authorization")
        raise LoginError(f"minimax: token polling failed (HTTP {polled.status_code}, {error or 'unknown error'})")
    if grant is None:
        raise LoginError("minimax: login timed out before authorization completed")

    access = _text(grant, "access_token")
    refresh = _text(grant, "refresh_token")
    token_type = _text(grant, "token_type")
    if not access or not refresh:
        raise LoginError("minimax: token response was missing access or refresh token")
    if token_type.lower() != "bearer":
        raise LoginError(f"minimax: token response was not a Bearer grant (got {token_type or 'nothing'})")
    scope = str(grant.get("scope") or "")
    if SCOPE not in scope:
        # Without the agent scope the token authenticates but cannot serve models.
        raise LoginError(f"minimax: token scope does not include {SCOPE} (got {scope or 'nothing'})")

    credential: dict[str, Any] = {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
    }
    try:
        expires_in_seconds = float(grant.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in_seconds = 0.0
    if expires_in_seconds > 0:
        credential["expires_at"] = str(int((time.time() + expires_in_seconds) * 1000))
    if scope:
        credential["scope"] = scope
    return credential
