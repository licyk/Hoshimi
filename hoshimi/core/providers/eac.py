"""The EAC co-paid lane: a relay that speaks OpenAI chat and gates turns on
this install's own authorization.

Two lane modes, decided by the account's credential:

- ``direct`` — ``access_token`` is one bearer credential, sent as-is.
- ``worker`` — a signing gateway in front of the relay carries the real
  credential in its own environment; the request here carries no secret at
  all, only an HMAC-SHA256 signature over ``timestamp \\n METHOD \\n path \\
  sha256(body)`` under the account's shared signing secret, plus the timestamp
  for the gateway's replay window. A relay key leaked from such a request is
  therefore an entry the gateway can revoke, not the credential itself.

The signature covers ``sha256(body)``, so the body must reach the gateway
byte-for-byte as signed: it is serialized once, signed over those bytes and
sent as those bytes, never re-encoded in between.

Worker mode also carries ``user_token`` — the per-user GitHub authorization —
in ``x-ofm-user``. Absent, the gateway answers ``401 AuthorizationRequired``,
which is exactly what an install that has not authorized this channel should
see.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from typing import Any, ClassVar

import httpx

from hoshimi.core.catalog import ModelSpec, eac_spec
from hoshimi.core.credentials import load_eac_user_token
from hoshimi.core.eac_relay import relay_credential, validate_relay
from hoshimi.core.errors import ValidationError
from hoshimi.core.providers.base import Account, AuthFailed, ProviderError
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider, raise_for_status, read_body
from hoshimi.core.quota import observe_response
from hoshimi.version import VERSION

EAC_MODELS = (
    # The relayed free-lane ids (OpenCode anonymous gateway in front, EAC tag
    # out front) are part of the lane now too; the upstream also names them
    # on /v1/models, but a stale or unreachable listing should still expose
    # them so a fresh install can see what the lane serves.
    "opencode/deepseek-v4.1-flash-free",
    "opencode/glm-5.3-flash-free",
    "deepseek-ai/deepseek-v4.1-flash",
    "moonshotai/kimi-k2.6",
    "moonshotai/kimi-k3",
    "openai/gpt-oss-20b",
    "z-ai/glm-5.3",
    "z-ai/glm-5.3-flash",
)
"""The lane ids when the listing cannot be reached (published roster)."""

USER_AGENT = f"hoshimi/{VERSION}"


def sign_request(
    signing_secret: str,
    *,
    method: str,
    path: str,
    body: str = "",
    now: float | None = None,
) -> dict[str, str]:
    """The signing headers for one gateway request.

    The layout is the gateway's: a millisecond timestamp, then
    ``METHOD``, path and the body's SHA-256, each separated by newlines.
    """
    timestamp = str(int(time.time() * 1000 if now is None else now))
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    payload = f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}".encode()
    signature = hmac.new(signing_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return {"x-ofm-timestamp": timestamp, "x-ofm-signature": signature}


def _needs_authorization(text: str) -> bool:
    lowered = text.lower()
    return "authorizationrequired" in lowered or "authorization required" in lowered


class EacProvider(OpenAiCompatProvider):
    """The relay's own wire: chat completions, signed or bearer, never anonymous."""

    id = "eac"
    display_name = "EAC (co-paid)"
    # Nothing here is served without this install's authorization, so the
    # provider is only built when an account is configured — and the API says
    # so, so the settings page can label the lane as needing one.
    keyless = False
    probeable = False
    requires_authorization = True
    fallback_model_list: ClassVar[list[str]] = list(EAC_MODELS)

    @classmethod
    def check_account(cls, data: dict[str, Any]) -> None:
        validate_relay(data)

    # -- credential -------------------------------------------------------- #
    @staticmethod
    def mode(account: Account) -> str:
        """``worker`` when the account signs, ``direct`` when it holds a bearer."""
        return "worker" if str(relay_credential(account.data).get("signing_secret") or "").strip() else "direct"

    @staticmethod
    def _base(account: Account) -> str:
        base = str(relay_credential(account.data).get("base_url") or "").strip().rstrip("/")
        if not base:
            raise ProviderError(400, "eac: the account needs base_url (the relay's /v1 root)")
        return base

    @staticmethod
    def _install_user_token() -> str:
        """The per-install GitHub token, or an empty string when absent.

        Mirrors the dsh-our-free-model split: the account carries whatever the
        operator typed into ``config.json``; ``eac-user.json`` holds what the
        gateway itself returned for this install. The lane prefers the
        account field when the operator set one. An omitted field falls back
        to the file; explicit null/blank isolates this account from the install
        identity. A later login is read at request time, not frozen at startup.
        """
        # Top-level import: the credentials module owns the lookup, and a
        # module-level binding is what lets test seams monkeypatch the
        # loader without an in-function import shadowing the patch.
        return load_eac_user_token()

    def _headers(self, account: Account, *, method: str, url: str, body: str) -> dict[str, str]:
        credential = relay_credential(account.data)
        worker = bool(str(credential.get("signing_secret") or "").strip())
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if method == "POST" else "application/json",
            "User-Agent": USER_AGENT,
        }
        if worker:
            secret = str(credential.get("signing_secret") or "").strip()
            if not secret:
                raise ProviderError(400, "eac: the account's signing_secret is empty")
            headers.update(sign_request(secret, method=method, path=httpx.URL(url).raw_path.split(b"?", 1)[0].decode("ascii"), body=body))
        else:
            token = str(credential.get("access_token") or "").strip()
            if not token:
                raise ProviderError(400, "eac: the account needs access_token (direct) or signing_secret (worker)")
            headers["Authorization"] = f"Bearer {token}"
        if worker:
            # Omission opts into the install identity; explicit null/blank isolates it.
            user_token = account.data.get("user_token") if "user_token" in account.data else self._install_user_token()
            if isinstance(user_token, str) and user_token.strip():
                headers["x-ofm-user"] = user_token.strip()
        return headers

    def specs(self) -> dict[str, ModelSpec]:
        return {model_id: eac_spec(model_id, self.id) for model_id in self.models()}

    # -- chat -------------------------------------------------------------- #
    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        payload = self.build_body(body, model)
        url = f"{self._base(account)}/chat/completions"
        # One serialization, signed, then sent as those exact bytes: a
        # re-encoded body would fail the gateway's HMAC check.
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self._headers(account, method="POST", url=url, body=content.decode("utf-8"))
        with self.client.stream("POST", url, content=content, headers=headers, follow_redirects=False) as response:
            observe_response(self.id, response)
            if response.status_code >= 400:
                self._raise(response)
            yield from read_body(response, provider=self.id)

    def _raise(self, response: httpx.Response) -> None:
        text = response.read().decode("utf-8", "replace")
        if response.status_code in (401, 403) and _needs_authorization(text):
            raise AuthFailed(
                response.status_code,
                "eac: the relay demands this install's GitHub authorization (set the account's user_token)",
                retryable=False,
            )
        raise_for_status(response, provider=self.id)

    # -- model discovery --------------------------------------------------- #
    def fetch_models(self) -> list[str]:
        if not self.accounts:
            return []
        account = self.accounts[0]
        try:
            url = f"{self._base(account)}/models"
            headers = self._headers(account, method="GET", url=url, body="")
        except (ProviderError, ValidationError):
            self.catalog_failed = True
            return []
        try:
            response = self.client.get(url, headers=headers, timeout=30.0, follow_redirects=False)
        except httpx.HTTPError:
            self.catalog_failed = True
            return []
        if response.status_code >= 400:
            self.catalog_failed = True
            return []
        return self.parse_models_response(response.json())
