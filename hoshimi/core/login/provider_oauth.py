"""Provider OAuth protocols ported from Pi; all storage belongs to Hoshimi.

Pi is MIT licensed, copyright (c) 2025 Mario Zechner.
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, CallbackServer, LoginError, LoginReporter, jwt_claims, ms_timestamp, pkce_pair


@dataclass(frozen=True)
class DeviceOAuth:
    client_id: str
    device_url: str
    token_url: str
    scope: str = ""


DEVICE_OAUTH = {
    "kimi-coding": DeviceOAuth("17e5f671-d194-4dfb-9706-5516cb48c098", "https://auth.kimi.com/api/oauth/device_authorization", "https://auth.kimi.com/api/oauth/token"),
    "xai": DeviceOAuth("b1a00492-073a-47ea-816f-4c329264a828", "https://auth.x.ai/oauth2/device/code", "https://auth.x.ai/oauth2/token", "openid profile email offline_access grok-cli:access api:access"),
    "meta": DeviceOAuth("1031625952748946", "https://auth.meta.com/oidc/device/authorization/", "https://auth.meta.com/oidc/device/token/"),
    "github-copilot": DeviceOAuth("Iv1.b507a08c87ecfe98", "https://github.com/login/device/code", "https://github.com/login/oauth/access_token", "read:user"),
    "radius": DeviceOAuth("pi-gateway", "https://radius.pi.dev/v1/oauth/device", "https://radius.pi.dev/v1/oauth/token", "gateway offline_access"),
}
ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OPENAI_TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
COPILOT_HEADERS = {"User-Agent": "GitHubCopilotChat/0.35.0", "Editor-Version": "vscode/1.107.0",
                   "Editor-Plugin-Version": "copilot-chat/0.35.0", "Copilot-Integration-Id": "vscode-chat"}


def json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise LoginError(f"Authorization service returned invalid JSON (HTTP {response.status_code})") from None
    if not isinstance(payload, dict):
        raise LoginError("Authorization service returned an invalid response")
    return payload


def credential_from_tokens(payload: dict[str, Any], *, refresh: str = "", client_id: str = "") -> dict[str, Any]:
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise LoginError("Token response contains no access token")
    credential: dict[str, Any] = {"access_token": access}
    renewable = payload.get("refresh_token") or refresh
    if renewable:
        credential["refresh_token"] = renewable
    expiry = ms_timestamp(payload.get("expires_in"))
    if expiry:
        credential["expires_at"] = expiry
    if client_id:
        credential["client_id"] = client_id
    if isinstance(payload.get("scope"), str):
        credential["scope"] = payload["scope"]
    claims = jwt_claims(payload.get("id_token") or access)
    if isinstance(claims.get("sub"), str):
        credential["sub"] = claims["sub"]
    if isinstance(claims.get("email"), str):
        credential["nickname"] = claims["email"]
    return credential


def _mint_meta(client: httpx.Client, identity: str) -> tuple[httpx.Response, dict[str, Any]]:
    response = client.post("https://api.meta.ai/muse-code/key", headers={"Authorization": f"Bearer {identity}", "x-api-version": "1.0.0"}, json={}, timeout=30)
    if response.is_error:
        return response, {}
    payload = json_object(response)
    key = payload.get("api_key")
    if response.is_error or not isinstance(key, str) or not key:
        return response, {}
    return response, {"access_token": key, "refresh_token": identity, "expires_at": str(int((time.time() + 86400) * 1000))}


def _mint_copilot(client: httpx.Client, identity: str) -> tuple[httpx.Response, dict[str, Any]]:
    response = client.get("https://api.github.com/copilot_internal/v2/token", headers={**COPILOT_HEADERS, "Authorization": f"Bearer {identity}"}, timeout=30)
    if response.is_error:
        return response, {}
    payload = json_object(response)
    token = payload.get("token")
    if response.is_error or not isinstance(token, str) or not token:
        return response, {}
    credential: dict[str, Any] = {"access_token": token, "refresh_token": identity}
    if isinstance(payload.get("expires_at"), (int, float)):
        credential["expires_at"] = str(int(payload["expires_at"] * 1000))
    proxy = re.search(r"proxy-ep=([^;]+)", token)
    host = proxy[1].replace("proxy.", "api.", 1) if proxy else "api.individual.githubcopilot.com"
    if not host.endswith(".githubcopilot.com") or any(c in host for c in "/:@"):
        raise LoginError("Copilot returned an invalid inference host")
    credential["base_url"] = f"https://{host}"
    return response, credential


def start_device_login(provider: str, client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    spec = DEVICE_OAUTH[provider]
    fields = {"client_id": spec.client_id}
    if spec.scope:
        fields["scope"] = spec.scope
    headers = {"Accept": "application/json"}
    if provider == "github-copilot":
        headers.update(COPILOT_HEADERS)
    response = client.post(spec.device_url, data=fields, headers=headers, timeout=30)
    device = json_object(response)
    if response.is_error:
        raise LoginError(f"{provider}: device authorization failed (HTTP {response.status_code})")
    code = device.get("device_code")
    user_code = device.get("user_code")
    url = device.get("verification_uri_complete") or device.get("verification_uri")
    if not isinstance(code, str) or not isinstance(user_code, str) or not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
        raise LoginError(f"{provider}: device response is incomplete")
    lifetime = min(float(device.get("expires_in") or LOGIN_TIMEOUT_SECONDS), LOGIN_TIMEOUT_SECONDS)
    if isinstance(report, LoginReporter):
        report.device_code(url, user_code, lifetime)
    else:
        report(url)
    deadline = time.monotonic() + lifetime
    interval = max(1.0, float(device.get("interval") or 5))
    while time.monotonic() < deadline:
        if isinstance(report, LoginReporter):
            report.wait(interval)
        else:
            time.sleep(interval)
        polled = client.post(spec.token_url, data={"client_id": spec.client_id, "device_code": code,
                             "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, headers=headers, timeout=30)
        payload = json_object(polled)
        error = payload.get("error")
        if error in ("authorization_pending", "slow_down"):
            if error == "slow_down":
                interval += 5
            continue
        if polled.is_error or error:
            raise LoginError(f"{provider}: authorization failed ({error or polled.status_code})")
        credential = credential_from_tokens(payload, client_id=spec.client_id)
        if provider in ("meta", "github-copilot"):
            response, credential = (_mint_meta if provider == "meta" else _mint_copilot)(client, credential["access_token"])
            if response.is_error or not credential:
                raise LoginError(f"{provider}: inference credential exchange failed (HTTP {response.status_code})")
        return credential
    raise LoginError(f"{provider}: authorization timed out")


def _installation_id() -> str:
    from hoshimi.core.paths import default_data_dir

    path = default_data_dir() / "oauth-installation-id"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return str(uuid.UUID(path.read_text().strip()))
    except FileNotFoundError:
        value = str(uuid.uuid4())
        try:
            with path.open("x") as stream:
                stream.write(value)
        except FileExistsError:
            return str(uuid.UUID(path.read_text().strip()))
        return value


def start_browser_login(provider: str, client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    path = "/auth/callback" if provider == "openai" else f"/oauth/callback/{secrets.token_hex(16)}"
    server = CallbackServer(path, port=1455 if provider == "openai" else 0)
    redirect = server.redirect_uri()
    params = {"response_type": "code", "redirect_uri": redirect, "code_challenge": challenge, "code_challenge_method": "S256", "state": state}
    if provider == "openrouter":
        params = {"callback_url": redirect, "code_challenge": challenge, "code_challenge_method": "S256"}
        authorize = "https://openrouter.ai/auth"
    elif provider == "anthropic":
        state = verifier
        params.update({"state": state, "client_id": ANTHROPIC_CLIENT_ID, "code": "true",
                       "scope": "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"})
        authorize = "https://claude.ai/oauth/authorize"
    else:
        params.update({"client_id": "dynamic_agent_client", "agent_name_hint": "Hoshimi", "ext_agent_host_id": f"urn:uuid:{_installation_id()}",
                       "resource": "https://api.openai.com/v1", "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct", "nonce": secrets.token_urlsafe(32)})
        authorize = "https://auth.openai.com/api/accounts/authorize"
    try:
        report(str(httpx.URL(authorize, params=params)))
        answer = report.authorization(server) if isinstance(report, LoginReporter) else server.wait(LOGIN_TIMEOUT_SECONDS)
    finally:
        server.close()
    if answer.get("error"):
        raise LoginError(f"{provider}: authorization denied")
    if provider != "openrouter" and answer.get("state") != state:
        raise LoginError("OAuth state mismatch")
    code = answer.get("code")
    if not code:
        raise LoginError("Missing authorization code")
    if provider == "openrouter":
        response = client.post("https://openrouter.ai/api/v1/auth/keys", json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"}, timeout=30)
        payload = json_object(response)
        if response.is_error or not isinstance(payload.get("key"), str) or not payload["key"]:
            raise LoginError(f"OpenRouter key exchange failed (HTTP {response.status_code})")
        return {"api_key": payload["key"]}
    if provider == "anthropic":
        response = client.post(ANTHROPIC_TOKEN_URL, json={"grant_type": "authorization_code", "client_id": ANTHROPIC_CLIENT_ID,
                               "code": code, "state": state, "redirect_uri": redirect, "code_verifier": verifier}, timeout=30)
        client_id = ANTHROPIC_CLIENT_ID
    else:
        client_id = answer.get("client_id", "")
        if not client_id:
            raise LoginError("OpenAI callback contains no issued client ID")
        response = client.post(OPENAI_TOKEN_URL, data={"grant_type": "authorization_code", "client_id": client_id, "code": code,
                               "code_verifier": verifier, "redirect_uri": redirect, "resource": "https://api.openai.com/v1"}, timeout=30)
    payload = json_object(response)
    if response.is_error:
        raise LoginError(f"{provider}: token exchange failed (HTTP {response.status_code})")
    if provider == "openai" and "chatgpt.tokens.use.direct" not in str(payload.get("scope") or "").split():
        raise LoginError("OpenAI did not grant direct API token access")
    credential = credential_from_tokens(payload, client_id=client_id)
    if not credential.get("refresh_token"):
        raise LoginError("Authorization returned no refresh token")
    return credential


def refresh_provider_account(provider: str, client: httpx.Client, account: Any) -> bool:
    """Refresh in the existing account lock, preserving unrotated refresh tokens."""
    from hoshimi.core.providers.base import RefreshTokenRejected

    refresh = account.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        return False
    try:
        if provider in ("meta", "github-copilot"):
            response, credential = (_mint_meta if provider == "meta" else _mint_copilot)(client, refresh)
            payload = {} if response.status_code in (401, 403) else json_object(response)
        else:
            if provider in DEVICE_OAUTH:
                spec = DEVICE_OAUTH[provider]
                url, client_id = spec.token_url, spec.client_id
            elif provider == "anthropic":
                url, client_id = ANTHROPIC_TOKEN_URL, ANTHROPIC_CLIENT_ID
            elif provider == "codex":
                from hoshimi.core.login.codex import CLIENT_ID, TOKEN_ENDPOINT

                url, client_id = TOKEN_ENDPOINT, str(account.get("client_id") or CLIENT_ID)
            elif provider == "openai":
                url, client_id = OPENAI_TOKEN_URL, str(account.get("client_id") or "")
                if not client_id:
                    return False
            else:
                return False
            fields = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh}
            if provider == "openai":
                fields["resource"] = "https://api.openai.com/v1"
            if provider == "anthropic":
                response = client.post(url, json=fields, timeout=30)
            else:
                response = client.post(url, data=fields, timeout=30)
            payload = {} if response.status_code in (401, 403) else json_object(response)
            credential = {} if response.is_error else credential_from_tokens(payload, refresh=refresh, client_id=client_id)
        if response.status_code in (401, 403) or payload.get("error") == "invalid_grant":
            raise RefreshTokenRejected(response.status_code, f"{provider}: authorization expired; sign in again")
        if response.is_error or not credential:
            return False
        for key, value in credential.items():
            account.set(key, value)
        return True
    except (httpx.HTTPError, LoginError):
        return False


def provider_login_flows() -> dict[str, Callable[[httpx.Client, Callable[[str], None]], dict[str, Any]]]:
    from functools import partial

    return {**{provider: partial(start_device_login, provider) for provider in DEVICE_OAUTH},
            **{provider: partial(start_browser_login, provider) for provider in ("anthropic", "openrouter", "openai")}}
