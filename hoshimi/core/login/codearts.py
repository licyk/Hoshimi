"""CodeArts (华为云): PKCE plus a DPoP proof on the token exchange.

The portal authorizes a loopback redirect (port 10000 or above — lower ones are
refused) with an S256 challenge; the callback hands back a ``code`` that only
the STS endpoint will accept, and only alongside a ``DPoP`` proof: an ES256 JWS
over the request's method and URL.

The proof key is generated here and kept with the credential — renewal reuses
it, and the code verifier must match the challenge that went up front. A
``secret`` callback instead of a ``code`` means the station fell back to the
legacy ticket exchange, which needs neither.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, CallbackServer, LoginError, LoginReporter, b64url, sha256_b64url

CLIENT_ID = "codearts-agent"
STS_TOKEN_ENDPOINT = "https://sts.cn-north-4.myhuaweicloud.com/v1/oauth2/tokens"
TICKET_ENDPOINT = "https://snap-access.cn-north-4.myhuaweicloud.com/snap-manager/v1/login/ticket"
PORTAL_AUTHORIZE_BASE = "https://codearts.huaweicloud.com/portal/authorize"
CALLBACK_PATH = "/oauth/callback"
MIN_CALLBACK_PORT = 10_000
PLUGIN_NAME = "snap_AIIDE"
PLUGIN_VERSION = "5.2.0"


def _require_cryptography() -> Any:
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:  # pragma: no cover - an install without the wheel
        raise LoginError("codearts: the 'cryptography' package is required for DPoP, install it with 'pip install cryptography'") from exc
    return ec


def _b64url_uint(value: int) -> str:
    return b64url(value.to_bytes(32, "big"))


def _public_jwk(public_key: Any) -> dict[str, str]:
    numbers = public_key.public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": _b64url_uint(numbers.x), "y": _b64url_uint(numbers.y)}


def _sign_dpop(private_key: Any, public_jwk: dict[str, str], htm: str, htu: str) -> str:
    """A DPoP proof: an ES256 JWS over the method and URL of the request."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    header = {"alg": "ES256", "typ": "dpop+jwt", "jwk": public_jwk}
    payload = {"htm": htm, "htu": htu, "iat": int(time.time()), "jti": secrets.token_hex(32)}
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}.{b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    der = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    # JWS wants the raw r||s pair; cryptography hands back DER.
    r, s = utils.decode_dss_signature(der)
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"{signing_input}.{signature}"


def _credential_from_ticket(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The legacy ticket answer carries the same AK/SK/ST the provider signs with."""
    raw_credential = payload.get("credential")
    credential = raw_credential if isinstance(raw_credential, dict) else {}
    access = str(credential.get("access") or "")
    security_token = str(credential.get("securitytoken") or credential.get("securityToken") or "")
    if access and security_token:
        return {
            "access_key_id": access,
            "secret_access_key": str(credential.get("secret") or ""),
            "security_token": security_token,
            "expires_at": str(credential.get("expires_at") or credential.get("expiresAt") or ""),
            "user_id": str(payload.get("user_id") or ""),
            "nickname": str(payload.get("user_name") or ""),
        }
    raw_result = payload.get("result")
    result = raw_result if isinstance(raw_result, dict) else {}
    access_key_id = str(result.get("accessKeyId") or "")
    security_token = str(result.get("securityToken") or "")
    if access_key_id and security_token:
        return {
            "access_key_id": access_key_id,
            "secret_access_key": str(result.get("secretAccessKey") or ""),
            "security_token": security_token,
            "expires_at": str(result.get("expiration") or result.get("expiresAt") or ""),
        }
    return None


def _open_callback_server() -> Any:
    """A loopback server on a port the portal considers privileged (>=10000)."""
    for _ in range(8):
        try:
            return CallbackServer(CALLBACK_PATH, port=secrets.randbelow(65536 - MIN_CALLBACK_PORT) + MIN_CALLBACK_PORT)
        except OSError:
            continue  # busy or refused; try another high port
    raise LoginError("codearts: could not bind a local callback port")


def start_codearts_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    ec = _require_cryptography()
    ticket_id = secrets.token_hex(32)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")
    challenge = sha256_b64url(verifier)
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = _public_jwk(private_key.public_key())

    # The portal's redirect must land on a port it considers privileged.
    server = _open_callback_server()
    try:
        login_url = (
            f"{PORTAL_AUTHORIZE_BASE}?theme=2&locale=zh-cn"
            f"&uri_scheme={CLIENT_ID}&client_id={CLIENT_ID}&port={server.port}"
            f"&code_challenge={challenge}&code_challenge_method=S256"
            f"&ticket_id={ticket_id}&plugin-name={PLUGIN_NAME}&plugin-version={PLUGIN_VERSION}"
        )
        report(login_url)
        params = report.authorization(server, min(LOGIN_TIMEOUT_SECONDS, 300.0)) if isinstance(report, LoginReporter) else server.wait(min(LOGIN_TIMEOUT_SECONDS, 300.0))
    finally:
        server.close()

    secret = params.get("secret") or ""
    code = params.get("code") or ""
    if params.get("error") and not code and not secret:
        raise LoginError(f"codearts: authorization was refused ({params['error']})")
    if secret:
        return _poll_ticket(client, ticket_id, secret)
    if not code:
        raise LoginError("codearts: the callback carried neither a code nor a ticket secret")

    try:
        response = client.post(
            STS_TOKEN_ENDPOINT,
            data={
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": f"http://127.0.0.1:{server.port}{CALLBACK_PATH}",
            },
            headers={
                "DPoP": _sign_dpop(private_key, public_jwk, "POST", STS_TOKEN_ENDPOINT),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"codearts: the token exchange failed: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or not isinstance(payload, dict):
        detail = str(payload.get("error_msg") or payload.get("error") or "")
        raise LoginError(f"codearts: the token exchange failed (HTTP {response.status_code}{': ' + detail if detail else ''})")
    raw_credentials = payload.get("credentials")
    credentials = raw_credentials if isinstance(raw_credentials, dict) else {}
    access_key_id = str(credentials.get("access_key_id") or "")
    security_token = str(credentials.get("security_token") or "")
    if not access_key_id or not security_token:
        raise LoginError("codearts: the token exchange returned no temporary credentials")

    credential: dict[str, Any] = {
        "access_key_id": access_key_id,
        "secret_access_key": str(credentials.get("secret_access_key") or ""),
        "security_token": security_token,
        "expires_at": str(credentials.get("expiration") or ""),
        # Kept for renewal: the same verifier and proof key must accompany the
        # refresh grant, and the portal binds the pair to the original challenge.
        "code_verifier": verifier,
        "dpop_private_key_jwk": {
            "kty": "EC",
            "crv": "P-256",
            "x": public_jwk["x"],
            "y": public_jwk["y"],
            "d": b64url(private_key.private_numbers().private_value.to_bytes(32, "big")),
        },
    }
    refresh = payload.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        credential["refresh_token"] = refresh
    return credential


def _poll_ticket(client: httpx.Client, ticket_id: str, secret: str) -> dict[str, Any]:
    """The portal's fallback: poll the ticket endpoint until credentials land."""
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            response = client.get(
                TICKET_ENDPOINT,
                params={"ticket_id": ticket_id, "secret": secret},
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "plugin-name": PLUGIN_NAME,
                    "plugin-version": PLUGIN_VERSION,
                },
                timeout=30.0,
            )
        except httpx.HTTPError:
            continue
        if response.status_code >= 400:
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        credential = _credential_from_ticket(payload)
        if credential is not None:
            return credential
    raise LoginError("codearts: the ticket exchange never produced credentials")
