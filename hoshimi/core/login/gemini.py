"""Google (Code Assist): a loopback redirect plus the standard token endpoint.

Unlike the device-code stations, Google wants a real redirect back to this
machine: a throwaway server on ``localhost`` (any port — RFC 8252 allows it for
native apps) catches the ``code`` and hands it to
``oauth2.googleapis.com/token``.

The client is Google's public desktop client for Cloud Code. Its id and secret
travel with the credential so the station's refresh path can renew without the
user configuring anything.
"""

from __future__ import annotations

import datetime as _dt
import secrets
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, CallbackServer, LoginError, LoginReporter

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
CALLBACK_PATH = "/oauth-callback"

CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "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"

# The six scopes the Code Assist channel actually needs; `cloud-platform` is
# mandatory and the last two are Cloud Code's own channels.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]


def _iso_to_ms(raw: Any) -> str | None:
    """An ISO instant as an absolute ms-timestamp string; None when unparseable."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return str(int(moment.timestamp() * 1000))


def start_gemini_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    state = secrets.token_hex(16)
    # Google allows any port for a loopback redirect, and wants `localhost`
    # spelled out in the URI — some hosts answer only on ::1 for that name.
    server = CallbackServer(CALLBACK_PATH, dual_stack=True)
    try:
        redirect_uri = server.redirect_uri(host="localhost")
        login_url = httpx.URL(
            AUTH_ENDPOINT,
            params={
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": " ".join(SCOPES),
                "state": state,
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            },
        ).__str__()
        report(login_url)
        params = report.authorization(server) if isinstance(report, LoginReporter) else server.wait(LOGIN_TIMEOUT_SECONDS)
    finally:
        server.close()

    if params.get("error"):
        raise LoginError(f"gemini: authorization was refused ({params['error']})")
    if params.get("state") != state:
        raise LoginError("gemini: the callback did not match this login (state mismatch)")
    code = params.get("code") or ""
    if not code:
        raise LoginError("gemini: the callback carried no authorization code")

    try:
        response = client.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"gemini: token exchange failed: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or not isinstance(payload, dict):
        detail = str(payload.get("error_description") or payload.get("error") or "")
        raise LoginError(f"gemini: token exchange failed (HTTP {response.status_code}{': ' + detail if detail else ''})")
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise LoginError("gemini: token exchange returned no access token")

    credential: dict[str, Any] = {
        "access_token": access,
        "token_type": str(payload.get("token_type") or "Bearer"),
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    if isinstance(refresh, str) and refresh:
        credential["refresh_token"] = refresh
    scope = payload.get("scope")
    if isinstance(scope, str) and scope:
        credential["scope"] = scope
    # `expires_in` is relative; the config's refresher wants an absolute value.
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and expires_in > 0:
        import time as _time

        credential["expiry"] = str(int((_time.time() + expires_in) * 1000))
    id_token = payload.get("id_token")
    if isinstance(id_token, str) and id_token:
        from hoshimi.core.login.common import jwt_claims

        claims = jwt_claims(id_token)
        sub = claims.get("sub")
        if isinstance(sub, str) and sub:
            credential["sub"] = sub
        email = claims.get("email")
        if isinstance(email, str) and email:
            credential["email"] = email
            credential["nickname"] = email
    return credential
