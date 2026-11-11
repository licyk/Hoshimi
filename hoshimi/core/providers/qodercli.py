"""Qoder CLI lanes: the agent-chat protocol, with COSY signing and PAT login.

Two lanes, one per region, ported from the ``pi-provider-qoder`` extension. They
sit beside the existing ``qoder`` / ``qodercn`` lanes rather than replacing them:
those speak the public OpenAI-compatible inference endpoint with a device token,
these speak the official CLI's ``agent_chat_generation`` endpoint with a Personal
Access Token, which is the path that carries the full catalogue, the reasoning
menus and the per-node chat allowance.

What the port keeps, in full:

- the COSY signature and the CLI client identity (see :mod:`hoshimi.core.qoder.cosy`);
- the custom Base64 body envelope (``Encode=1``);
- PAT -> job token exchange, identity resolution and re-exchange on renewal;
- per-account node discovery, remote-agent preference, and rotation on the
  per-node daily chat limit (code 110);
- the scheduler's busy/queue recovery (code 10605);
- the live catalogue with its thinking menus, and the static fallbacks;
- thinking-tag routing, DSML residue cleanup and the degenerate-turn report.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from typing import Any, ClassVar

import httpx

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.errors import ValidationError
from hoshimi.core.expiry import as_ms
from hoshimi.core.providers.base import (
    Account,
    AuthFailed,
    BalanceOutcome,
    ModelGone,
    Provider,
    ProviderError,
    RefreshTokenRejected,
)
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.providers.refusals import provider_refusal
from hoshimi.core.qoder.auth import PatExchangeError, exchange_job_token, fetch_user_info, pat_from_env, refresh_device_token
from hoshimi.core.qoder.catalog import ModelCatalog, ModelEntry, spec_for, static_entries
from hoshimi.core.qoder.cosy import CosyCredentials, build_auth_headers, get_machine_id, open_api_headers
from hoshimi.core.qoder.cosy_version import CosyVersion
from hoshimi.core.qoder.encoding import decode_body, encode_body
from hoshimi.core.qoder.endpoints import EndpointResolver
from hoshimi.core.qoder.protocol import OFF_RUNGS, build_chat_body, requested_effort
from hoshimi.core.qoder.region import CN, GLOBAL, QoderRegion, chat_url, usage_url
from hoshimi.core.qoder.stream import DailyChatLimitRejection, ModelBusyRejection, is_daily_chat_limit, iter_agent_chunks
from hoshimi.core.quota import observe_response

logger = logging.getLogger(__name__)

MAX_CHAT_ENDPOINT_SWITCHES = 3
DEFAULT_MODEL_BUSY_RETRIES = 2
MODEL_BUSY_MAX_WAIT_SECONDS = 5.0
TOKEN_REFRESH_LEAD_MS = 5 * 60 * 1000
"""Re-exchange this long before the job token's recorded expiry."""


class QoderCliProvider(Provider):
    """The international Qoder CLI lane."""

    id = "qodercli"
    display_name = "Qoder CLI"
    region: ClassVar[QoderRegion] = GLOBAL
    # The catalogue is gated on the account's own credential and is already the
    # verdict: a probe round would spend a turn per model to learn nothing.
    probeable = False
    max_concurrency = 2

    def __init__(self, client: httpx.Client, accounts: list[Account], options: dict[str, Any] | None = None) -> None:
        super().__init__(client, accounts, options)
        self.cosy_version = CosyVersion()
        self.catalog = ModelCatalog(self.region)
        self.endpoints = EndpointResolver(self.region, client, version_provider=self.cosy_version.get)
        # The node that served the last successful chat, so the first turn after
        # a restart does not repeat a route that already refused.
        self.endpoints.apply_cached()

    # -- credentials ------------------------------------------------------- #
    @classmethod
    def check_account(cls, data: dict[str, Any]) -> None:
        if not _personal_token(data) and not _job_token(data):
            raise ValidationError(f"{cls.id}: the account needs a Personal Access Token (personal_token) or a job token (access_token)")

    def fallback_models(self) -> list[str]:
        return [entry.id for entry in static_entries(self.region)]

    def _personal_token(self, account: Account) -> str:
        token = _personal_token(account.data)
        if token:
            return token
        # An environment PAT is authoritative for this lane, as it is for the
        # plugin: it logs the lane in without a config entry.
        return pat_from_env(self.region)

    def _job_token(self, account: Account) -> str:
        return _job_token(account.data)

    def _ensure_token(self, account: Account) -> str:
        """The job token to sign with, exchanging the PAT when it is due."""
        token = self._job_token(account)
        expiry = as_ms(account.get("expires_at"))
        due = expiry is None or expiry - time.time() * 1000 <= TOKEN_REFRESH_LEAD_MS
        if token and not due:
            return token
        pat = self._personal_token(account)
        if not pat:
            if token:
                return token
            raise AuthFailed(401, f"{self.id}: the account has no Personal Access Token; sign in again")
        try:
            job = exchange_job_token(self.client, self.region, pat)
        except PatExchangeError as exc:
            if token:
                # The station refused the exchange but the current token may
                # still be good; a later round retries the renewal.
                logger.warning("%s/%s: PAT re-exchange failed (%s)", self.id, account.id, exc)
                return token
            raise AuthFailed(401, str(exc)) from exc
        account.set("access_token", job.token)
        account.set("security_oauth_token", job.token)
        if job.refresh_token:
            account.set("refresh_token", job.refresh_token)
        account.set("expires_at", str(max(0, job.expires_at_ms - TOKEN_REFRESH_LEAD_MS)))
        if not account.get("machine_id"):
            account.set("machine_id", get_machine_id())
        account.auth_revision += 1
        self._resolve_identity(account, job.token)
        return job.token

    def _resolve_identity(self, account: Account, job_token: str) -> None:
        """Fill uid/name/email from the job token; the uid is mandatory in COSY."""
        if account.get("uid"):
            return
        identity = fetch_user_info(self.client, self.region, job_token)
        if identity.user_id:
            account.set("uid", identity.user_id)
            account.set("user_id", identity.user_id)
        if identity.name:
            account.set("nickname", identity.name)
        if identity.email:
            account.set("email", identity.email)

    def _credentials(self, account: Account) -> CosyCredentials:
        token = self._ensure_token(account)
        self._resolve_identity(account, token)
        user_id = str(account.get("uid") or account.get("user_id") or "")
        if not user_id:
            # A placeholder uid makes the CN gateway answer "105 Login expired";
            # failing here says which credential is unusable instead.
            raise AuthFailed(401, f"{self.id}: the station reported no user id for this token; sign in again")
        return CosyCredentials(
            user_id=user_id,
            auth_token=token,
            name=str(account.get("nickname") or self.region.user_name_fallback),
            email=str(account.get("email") or self.region.user_email_fallback),
            machine_id=str(account.get("machine_id") or "") or get_machine_id(),
        )

    def refresh_account(self, account: Account) -> bool:
        """Renew the credential: re-exchange a PAT, or refresh a device token."""
        pat = self._personal_token(account)
        before = self._job_token(account)
        if pat:
            try:
                job = exchange_job_token(self.client, self.region, pat)
            except PatExchangeError as exc:
                message = str(exc)
                if "HTTP 400" in message or "HTTP 401" in message or "HTTP 403" in message:
                    raise RefreshTokenRejected(401, f"{self.id}: the station refused the Personal Access Token") from exc
                return False
        else:
            refresh = account.get("refresh_token")
            if not isinstance(refresh, str) or not refresh or not before:
                return False
            try:
                job = refresh_device_token(self.client, self.region, before, refresh)
            except PatExchangeError as exc:
                message = str(exc)
                if "HTTP 400" in message or "HTTP 401" in message or "HTTP 403" in message:
                    raise RefreshTokenRejected(401, f"{self.id}: the station refused the refresh token") from exc
                return False
        account.set("access_token", job.token)
        account.set("security_oauth_token", job.token)
        if job.refresh_token:
            account.set("refresh_token", job.refresh_token)
        account.set("expires_at", str(max(0, job.expires_at_ms - TOKEN_REFRESH_LEAD_MS)))
        if not account.get("machine_id"):
            account.set("machine_id", get_machine_id())
        self._resolve_identity(account, job.token)
        return job.token != before

    # -- catalogue --------------------------------------------------------- #
    def fetch_models(self) -> list[str]:
        if not self.accounts:
            return []
        account = self.accounts[0]
        try:
            credentials = self._credentials(account)
        except ProviderError as exc:
            self.record_catalog_failure(exc)
            return []
        # The gateway gates the catalogue on the client version, so the identity
        # is refreshed before signing this request, not after.
        self.cosy_version.refresh(self.client)
        if not self.catalog.refresh(self.client, credentials, cosy_version=self.cosy_version.get()):
            return []
        self.catalog_authoritative = True
        return self.catalog.ids()

    def specs(self) -> dict[str, ModelSpec]:
        entries = self.catalog.entries()
        return {model_id: spec_for(entry, self.id) for model_id, entry in entries.items()}

    def _entry(self, model: str) -> ModelEntry:
        entry = self.catalog.entry_for(model)
        if entry is None and self.catalog.stale():
            # A turn can arrive before any catalogue discovery (a direct stream
            # call, a cold cache): ask the station once rather than answering
            # "no such model" from the static fallback.
            self.models()
            entry = self.catalog.entry_for(model)
        if entry is None:
            raise ModelGone(404, f"{self.id}: the catalogue does not list '{model}'")
        return entry

    # -- inference --------------------------------------------------------- #
    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        entry = self._entry(model)
        credentials = self._credentials(account)
        version = self.cosy_version.get()
        config = self.catalog.config_for(entry.id) or {"key": entry.key, "is_reasoning": entry.reasoning, "source": entry.source}
        payload = build_chat_body(
            body,
            model_key=entry.key,
            model_config=config,
            entry=entry,
            user_id=credentials.user_id,
            session_marker=str(body.get("session_id") or body.get("user") or ""),
        )
        encoded = encode_body(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        # Discovery is best-effort and cached; a failure leaves the configured node.
        self.endpoints.ensure(credentials)

        attempted: set[str] = set()
        switches = 0
        switched = False
        busy_retries = 0
        model_source = str(config.get("source") or entry.source or "system")
        # The in-band thinking parser runs unless the caller explicitly asked
        # for a quiet turn, exactly as the reference client resolves it.
        effort = requested_effort(body)
        thinking_enabled = not (isinstance(effort, str) and effort.strip().lower() in OFF_RUNGS)

        while True:
            url = chat_url(self.region, self.endpoints.chat_base_url())
            headers = build_auth_headers(encoded, url, credentials, cosy_version=version)
            headers.update({
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "Accept-Encoding": "identity",
                "X-Model-Key": entry.key,
                "X-Model-Source": model_source,
            })
            try:
                with self.client.stream("POST", url, content=encoded, headers=headers) as response:
                    observe_response(self.id, response)
                    if response.status_code >= 400:
                        text = response.read().decode("utf-8", "replace")
                        if is_daily_chat_limit(_error_payload(text)):
                            raise DailyChatLimitRejection(f"Upstream status {response.status_code}: {text}")
                        raise_for_status(response, provider=self.id)
                    yield from iter_agent_chunks(response.iter_lines(), provider=self.id, thinking_enabled=thinking_enabled)
                if switched:
                    # Only a node that actually served a request is persisted.
                    self.endpoints.remember(self.endpoints.chat_base_url())
                return
            except DailyChatLimitRejection as exc:
                if exc.saw_output:
                    # Content already went out: a retry would duplicate it, so
                    # the turn ends as an error the caller can see beside it.
                    yield {"type": "error", "error": f"{self.id}: {exc}"}
                    return
                if switches < MAX_CHAT_ENDPOINT_SWITCHES:
                    nxt = self.endpoints.rotate(attempted, credentials)
                    if nxt:
                        switches += 1
                        switched = True
                        logger.info("%s: daily chat limit on one node, switching to %s", self.id, nxt)
                        continue
                refusal = provider_refusal(self.id, str(exc), status=403)
                raise refusal if refusal is not None else ProviderError(403, f"{self.id}: {exc}") from exc
            except ModelBusyRejection as exc:
                if exc.saw_output:
                    yield {"type": "error", "error": f"{self.id}: model rejected the request after partial output (10605 RESOURCE_LIMIT)"}
                    return
                wait = max(0.0, exc.retry_after_ms / 1000)
                if busy_retries >= DEFAULT_MODEL_BUSY_RETRIES:
                    raise ProviderError(503, f"{self.id}: server error, model is busy (10605 RESOURCE_LIMIT, attempts={busy_retries + 1})", retryable=True) from exc
                if wait > MODEL_BUSY_MAX_WAIT_SECONDS:
                    raise ProviderError(503, f"{self.id}: server error, model is busy (10605 RESOURCE_LIMIT, requested delay {wait}s exceeds local cap)", retryable=True, retry_after=wait) from exc
                busy_retries += 1
                if wait > 0:
                    time.sleep(wait)
                # qodercli resets request_id and chat_record_id to ONE new value
                # per recovery attempt and keeps request_set_id.
                next_id = str(uuid.uuid4())
                payload["request_id"] = next_id
                payload["chat_record_id"] = next_id
                encoded = encode_body(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    # -- credits ----------------------------------------------------------- #
    def balance_of(self, account: Account) -> BalanceOutcome | None:
        """The account-wide credit balance (personal, add-on and organization)."""
        try:
            token = self._ensure_token(account)
        except ProviderError as exc:
            return BalanceOutcome(self.id, account.id, None, message=str(exc))
        try:
            response = self.client.get(usage_url(self.region), headers=open_api_headers(token), timeout=30.0)
        except httpx.HTTPError as exc:
            return BalanceOutcome(self.id, account.id, None, message=str(exc))
        if response.status_code in (401, 403):
            return BalanceOutcome(self.id, account.id, None, message=f"credential rejected (HTTP {response.status_code})")
        if response.status_code >= 400:
            return BalanceOutcome(self.id, account.id, None, message=f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return BalanceOutcome(self.id, account.id, None, message="non-JSON response")
        if not isinstance(payload, dict):
            return BalanceOutcome(self.id, account.id, None, message="unparseable response")
        total = 0.0
        seen = False
        for key in ("userQuota", "addOnQuota", "orgResourcePackage"):
            quota = payload.get(key)
            if not isinstance(quota, dict):
                continue
            remaining = quota.get("remaining")
            if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
                total += float(remaining)
                seen = True
        if not seen:
            return BalanceOutcome(self.id, account.id, None, message="response carried no quota buckets")
        return BalanceOutcome(self.id, account.id, int(total))


class QoderCliCnProvider(QoderCliProvider):
    """The China Qoder CLI lane: its own hosts, catalogue and tokens."""

    id = "qodercli-cn"
    display_name = "Qoder CLI (CN)"
    region = CN


def _personal_token(data: dict[str, Any]) -> str:
    """The PAT this blob carries.

    Presence is what the lane checks before filing an account; whether the token
    is one the station accepts is the exchange's verdict, not a guess made here.
    An ``api_key`` only counts when it is spelled like a PAT, so a relay key
    pasted into the wrong lane is not silently treated as one.
    """
    for key in ("personal_token", "pat"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = data.get("api_key")
    if isinstance(value, str) and value.strip().startswith("pt-"):
        return value.strip()
    return ""


def _job_token(data: dict[str, Any]) -> str:
    for key in ("access_token", "security_oauth_token", "job_token"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _error_payload(text: str) -> Any:
    """An error body as JSON, decoding the custom envelope when it is one."""
    try:
        return json.loads(text)
    except ValueError:
        try:
            return json.loads(decode_body(text))
        except ValueError:
            return text