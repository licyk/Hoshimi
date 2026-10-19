"""OpenAI Codex: browser PKCE login through a fixed loopback redirect.

The protocol constants and request shapes mirror the Codex CLI client used by
KohakuTerrarium. The surrounding lifecycle is Hoshimi's: this flow reports the
authorization URL, waits for the existing callback server to receive the code,
and returns a plain account dict for ``LoginManager`` to persist. It never
opens a browser itself; the CLI and web UI own that user-facing action.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, CallbackServer, LoginError, LoginReporter, jwt_claims, ms_timestamp, pkce_pair

ISSUER = "https://auth.openai.com"
AUTH_ENDPOINT = f"{ISSUER}/oauth/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CALLBACK_PATH = "/auth/callback"
REDIRECT_PORT = 1455
SCOPE = "openid email profile offline_access"
AUDIENCE = "https://api.openai.com/v1"
ID_TOKEN_AUTH_CLAIM = "https://api.openai.com/auth"

_FORM = {"Content-Type": "application/x-www-form-urlencoded"}
_LOGIN_LOCK = threading.Lock()


def _auth_url(code_challenge: str, state: str, redirect_uri: str) -> str:
    return httpx.URL(
        AUTH_ENDPOINT,
        params={
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "audience": AUDIENCE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
    ).__str__()


def _token_detail(payload: dict[str, Any]) -> str:
    for key in ("error_description", "error", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _exchange_code(client: httpx.Client, code: str, code_verifier: str, redirect_uri: str) -> dict[str, Any]:
    try:
        response = client.post(
            TOKEN_ENDPOINT,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers=_FORM,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"codex: token exchange failed: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or not isinstance(payload, dict):
        detail = _token_detail(payload) if isinstance(payload, dict) else ""
        suffix = f": {detail}" if detail else ""
        raise LoginError(f"codex: token exchange failed (HTTP {response.status_code}{suffix})")

    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise LoginError("codex: token exchange returned no access token")
    if not isinstance(refresh, str) or not refresh:
        raise LoginError("codex: token exchange returned no refresh token")

    id_token = payload.get("id_token")
    claims = jwt_claims(id_token) if isinstance(id_token, str) else {}
    auth = claims.get(ID_TOKEN_AUTH_CLAIM)
    auth = auth if isinstance(auth, dict) else {}
    account_id = auth.get("chatgpt_account_id")
    sub = claims.get("sub")
    email = claims.get("email")

    credential: dict[str, Any] = {
        "access_token": access,
        "refresh_token": refresh,
        "client_id": CLIENT_ID,
    }
    expires_at = ms_timestamp(payload.get("expires_in"))
    if expires_at is None:
        raw_exp = claims.get("exp")
        if isinstance(raw_exp, (int, float)) and not isinstance(raw_exp, bool) and raw_exp > 0:
            expires_at = str(int(raw_exp * 1000))
    if expires_at is not None:
        credential["expires_at"] = expires_at
    if isinstance(account_id, str) and account_id:
        credential["account_id"] = account_id
    elif isinstance(sub, str) and sub:
        credential["sub"] = sub
    if isinstance(email, str) and email:
        credential["email"] = email
        credential["nickname"] = email
    scope = payload.get("scope")
    if isinstance(scope, str) and scope:
        credential["scope"] = scope
    return credential


def start_codex_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    """Sign into a ChatGPT/Codex account with OAuth PKCE and a local redirect."""
    if not _LOGIN_LOCK.acquire(blocking=False):
        raise LoginError("codex: another login is already in progress")

    try:
        code_verifier, code_challenge = pkce_pair()
        state = secrets.token_hex(32)
        try:
            server = CallbackServer(CALLBACK_PATH, port=REDIRECT_PORT, dual_stack=True)
        except OSError as exc:
            raise LoginError(f"codex: cannot bind the local callback port {REDIRECT_PORT}: {exc}") from exc

        try:
            redirect_uri = server.redirect_uri(host="localhost")
            report(_auth_url(code_challenge, state, redirect_uri))
            params = report.authorization(server) if isinstance(report, LoginReporter) else server.wait(LOGIN_TIMEOUT_SECONDS)
        finally:
            server.close()

        if params.get("error"):
            raise LoginError(f"codex: authorization was refused ({params['error']})")
        if params.get("state") != state:
            raise LoginError("codex: the callback did not match this login (state mismatch)")
        code = params.get("code") or ""
        if not code:
            raise LoginError("codex: the callback carried no authorization code")
        return _exchange_code(client, code, code_verifier, redirect_uri)
    finally:
        _LOGIN_LOCK.release()
