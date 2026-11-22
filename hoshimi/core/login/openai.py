"""Sign in with ChatGPT for the OpenAI API provider."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, CallbackServer, LoginError, LoginReporter, pkce_pair
from hoshimi.core.providers.base import Account

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {DIRECT_SCOPE}"
DYNAMIC_CLIENT = "dynamic_agent_client"

_installation_lock = threading.Lock()


def _installation_id() -> str:
    from hoshimi.core.paths import default_data_dir

    path = default_data_dir() / "oauth-installation-id"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _installation_lock:
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


def _metadata(client: httpx.Client, url: str, stage: str) -> dict[str, Any]:
    try:
        response = client.get(url, timeout=15, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data
    except (httpx.HTTPError, ValueError):
        pass
    raise LoginError(f"OpenAI {stage} is temporarily unavailable. Try again shortly.")


def discovery(client: httpx.Client) -> dict[str, Any]:
    data = _metadata(client, f"{ISSUER}/.well-known/openid-configuration", "sign-in configuration")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = data.get(field)
        if not isinstance(value, str) or not value.startswith(ISSUER + "/") or urlsplit(value).fragment:
            raise LoginError("Invalid OpenAI sign-in configuration.")
    if data.get("issuer") != ISSUER:
        raise LoginError("Invalid OpenAI sign-in issuer.")
    return data


class InvalidIdentity(LoginError):
    """A definitive signature or identity-claim failure."""


def _identity(client: httpx.Client, jwks_uri: str, token: Any, client_id: str, *, nonce: str | None, received_at: float) -> dict[str, str]:
    invalid = "OpenAI identity verification failed. Sign in again."
    if not isinstance(token, str) or not token:
        raise InvalidIdentity(invalid)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise InvalidIdentity(invalid) from None
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise InvalidIdentity(invalid)
    keys = _metadata(client, jwks_uri, "signing-key service").get("keys")
    if not isinstance(keys, list):
        raise LoginError("OpenAI signing-key service returned an invalid key set. Try again shortly.")
    matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"] and key.get("kty") == "RSA"
               and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256"]
    if len(matches) != 1:
        raise LoginError("OpenAI signing key is temporarily unavailable. Try again shortly.")
    try:
        public = matches[0]
        if "key_ops" in public and (not isinstance(public["key_ops"], list) or "verify" not in public["key_ops"]):
            raise ValueError
        key = jwt.PyJWK.from_dict(public, algorithm="RS256").key
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise LoginError("OpenAI signing key is temporarily unavailable. Try again shortly.") from None
    try:
        # A rotated token may await signing-key recovery beyond its lifetime.
        # Check its validity at receipt, while still verifying signature and all identity bindings.
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER, audience=client_id,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"], "verify_exp": False, "verify_iat": False, "verify_nbf": False})
        dates = [claims["iat"], claims["exp"], claims.get("nbf", claims["iat"])]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in dates):
            raise ValueError
        issued, expires, valid_from = dates
        if expires <= received_at - 5 or max(issued, valid_from) > received_at + 5:
            raise ValueError
        if not isinstance(claims["sub"], str) or not claims["sub"] or (nonce is not None and claims.get("nonce") != nonce):
            raise ValueError
        multiple_audiences = isinstance(claims["aud"], list) and len(claims["aud"]) > 1
        if ("azp" in claims or multiple_audiences) and claims.get("azp") != client_id:
            raise ValueError
    except (jwt.PyJWTError, TypeError, ValueError, OverflowError):
        raise InvalidIdentity(invalid) from None
    identity = {"sub": claims["sub"]}
    if isinstance(claims.get("email"), str):
        identity["nickname"] = claims["email"]
    return identity


class TokenError(LoginError):
    def __init__(self, response: httpx.Response, body: dict[str, Any]):
        field = body.get("error")
        code = field.get("code") if isinstance(field, dict) else field
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code) else "unknown_error"
        self.status = response.status_code
        request_id = response.headers.get("x-request-id", "")
        suffix = f", request ID {request_id}" if re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", request_id) else ""
        super().__init__(f"OpenAI token request failed (HTTP {self.status}, {self.code}{suffix}).")


def _tokens(client: httpx.Client, endpoint: str, fields: dict[str, str]) -> dict[str, Any]:
    try:
        response = client.post(endpoint, data={**fields, "resource": RESOURCE}, headers={"Accept": "application/json"}, timeout=30, follow_redirects=False)
    except httpx.HTTPError:
        raise LoginError("Could not reach the OpenAI token endpoint.") from None
    try:
        body = response.json()
    except ValueError:
        body = None
    if not response.is_success:
        raise TokenError(response, body if isinstance(body, dict) else {})
    if not isinstance(body, dict):
        raise LoginError("Invalid OpenAI token response.")
    return body


class InvalidTokenResponse(LoginError):
    """A successful exchange returned unusable credentials."""


def _credentials(body: dict[str, Any], received_at: float) -> dict[str, Any]:
    scope = body.get("scope")
    if not isinstance(scope, str):
        raise InvalidTokenResponse("OpenAI did not confirm the granted permissions.")
    direct = DIRECT_SCOPE in scope.split()
    fields: dict[str, Any] = {"scope": scope, "plan_authorized": direct}
    if direct or "offline_access" in scope.split() or body.get("access_token"):
        expiry = body.get("expires_in")
        if (str(body.get("token_type")).lower() != "bearer" or not isinstance(body.get("access_token"), str) or not body["access_token"]
                or not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry) or expiry <= 0):
            raise InvalidTokenResponse("OpenAI returned incomplete access credentials.")
        fields.update(access_token=body["access_token"], expires_at=str(int((received_at + expiry) * 1000)))
        if isinstance(body.get("refresh_token"), str) and body["refresh_token"]:
            fields["refresh_token"] = body["refresh_token"]
        elif "offline_access" in scope.split():
            raise InvalidTokenResponse("OpenAI omitted the granted offline refresh token.")
    return fields


def start_openai_login(client: httpx.Client, report: Callable[[str], None], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    config = discovery(client)
    client_id = previous.get("client_id")
    host_id = f"urn:uuid:{_installation_id()}"
    for attempt in range(2):
        verifier, challenge = pkce_pair()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        server = CallbackServer("/auth/callback", state=state)
        redirect = server.redirect_uri()
        params = {"response_type": "code", "redirect_uri": redirect, "code_challenge": challenge, "code_challenge_method": "S256",
                  "state": state, "nonce": nonce, "scope": SCOPES, "resource": RESOURCE, "ext_agent_host_id": host_id, "client_id": client_id or DYNAMIC_CLIENT}
        if not client_id:
            params["agent_name_hint"] = "Hoshimi"
        if previous.get("plan_authorized") is False:
            params["prompt"] = "consent"
        try:
            report(str(httpx.URL(config["authorization_endpoint"], params=params)))
            answer = report.authorization(server, callback_state=state) if isinstance(report, LoginReporter) else server.wait(LOGIN_TIMEOUT_SECONDS)
        finally:
            server.close()
        if answer.get("state") != state:
            raise LoginError("OpenAI callback state mismatch.")
        if "error" in answer:
            if answer["error"] == "access_denied":
                raise LoginError("OpenAI authorization was declined.")
            code = answer["error"]
            raise LoginError("OpenAI authorization failed" + (f" ({code})." if re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", code) else "."))
        issued = answer.get("client_id", client_id)
        if not isinstance(issued, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", issued) or issued == DYNAMIC_CLIENT or (client_id and issued != client_id):
            raise LoginError("OpenAI registration is incomplete or does not match the selected account.")
        if not answer.get("code"):
            raise LoginError("OpenAI callback contains no authorization code.")
        client_id = issued
        try:
            body = _tokens(client, config["token_endpoint"], {"grant_type": "authorization_code", "client_id": issued, "code": answer["code"],
                           "code_verifier": verifier, "redirect_uri": redirect})
        except TokenError as exc:
            if attempt == 0 and exc.code == "invalid_grant":
                continue
            raise
        received_at = time.time()
        identity = _identity(client, config["jwks_uri"], body.get("id_token"), issued, nonce=nonce, received_at=received_at)
        if previous.get("sub") and identity["sub"] != previous["sub"]:
            raise LoginError("OpenAI returned a different account. Sign in with the original account.")
        return {"client_id": issued, **identity, **_credentials(body, received_at)}
    raise LoginError("OpenAI sign-in could not be completed.")


def refresh_openai(client: httpx.Client, account: Account) -> bool:
    """Checkpoint each rotated grant before its identity is allowed into the pool."""
    from hoshimi.core.providers.base import ProviderError, RefreshTokenRejected

    refresh = account.get("refresh_token")
    client_id = account.get("client_id")
    if not isinstance(refresh, str) or not isinstance(client_id, str) or not refresh or not client_id:
        return False
    try:
        config = discovery(client)
        pending = account.get("pending_refresh")
        # The checkpoint is encrypted with key material from the previous high-entropy
        # grant. Both the old grant and checkpoint live in the owner-only config.
        cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"hoshimi-openai-rotation\x00" + refresh.encode()).digest()))
        if pending:
            try:
                rotation = json.loads(cipher.decrypt(pending.encode()))
                fields, token, received_at = rotation["fields"], rotation["id_token"], rotation["received_at"]
            except (InvalidToken, KeyError, ValueError, TypeError, AttributeError):
                raise RefreshTokenRejected(401, "OpenAI refresh checkpoint is invalid. Sign in again.") from None
        else:
            body = _tokens(client, config["token_endpoint"], {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh})
            received_at = time.time()
            body.setdefault("scope", account.get("scope"))
            fields = _credentials(body, received_at)
            if not fields.get("refresh_token") or not fields.get("access_token"):
                raise InvalidTokenResponse("OpenAI returned incomplete rotated credentials.")
            token = body.get("id_token")
            if "id_token" in body and (not isinstance(token, str) or not token):
                raise InvalidIdentity("Invalid refreshed OpenAI identity.")
            payload = json.dumps({"fields": fields, "id_token": token, "received_at": received_at}).encode()
            checkpoint = cipher.encrypt(payload).decode()
            try:
                account.commit_credentials({"pending_refresh": checkpoint})
            except OSError:
                # The old grant has already been consumed. Keep the checkpoint
                # in memory too so a storage outage cannot trigger a replay.
                account.set("pending_refresh", checkpoint)
                raise
        if token is not None:
            identity = _identity(client, config["jwks_uri"], token, client_id, received_at=received_at, nonce=None)
            if account.get("sub") and identity["sub"] != account.get("sub"):
                raise RefreshTokenRejected(401, "Refreshed OpenAI identity differs from the selected account.")
            fields.update(identity)
        account.commit_credentials({**fields, "pending_refresh": None})
        return True
    except OSError:
        raise ProviderError(503, "Could not save the OpenAI token rotation. Check the data directory and retry.") from None
    except (InvalidIdentity, InvalidTokenResponse) as exc:
        raise RefreshTokenRejected(401, str(exc)) from None
    except TokenError as exc:
        if exc.code in {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused", "refresh_token_revoked"}:
            raise RefreshTokenRejected(exc.status, str(exc)) from None
        raise ProviderError(exc.status, str(exc)) from None
    except LoginError as exc:
        raise ProviderError(503, str(exc)) from None
