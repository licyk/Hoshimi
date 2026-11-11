"""Personal Access Tokens: the exchange that makes a ``pt-`` token usable.

A PAT cannot authenticate an API call. The official CLI exchanges it for a
short-lived job token (``jt-``) plus a job refresh token (``jrt-``), and it is
the job token that both the bearer calls and the COSY payload carry:

    POST {openApi}/api/v1/jobToken/exchange {"personal_token": "pt-..."}
      -> {token, refresh_token, expires_at, expires_in, ...}

The exchange itself needs no signature — only the CLI client type and the
openapi COSY version. Identity comes from ``/api/v1/userinfo`` with the job
token; the uid it reports is mandatory in the COSY payload (a placeholder makes
the CN gateway answer ``105 Login expired``).

Ported from ``pi-provider-qoder`` ``src/auth/pat.ts`` and the PAT half of
``src/auth/oauth.ts``.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from hoshimi.core.expiry import as_ms, iso_as_ms
from hoshimi.core.qoder.cosy import QODER_CLIENT_TYPE, QODER_OPENAPI_COSY_VERSION, get_machine_id, open_api_headers
from hoshimi.core.qoder.region import QoderRegion, exchange_url, refresh_url, user_info_url

logger = logging.getLogger(__name__)

EXCHANGE_TIMEOUT_SECONDS = 30.0
USER_INFO_TIMEOUT_SECONDS = 10.0
DEFAULT_JOB_TOKEN_TTL_MS = 24 * 60 * 60 * 1000
EXPIRY_BUFFER_MS = 5 * 60 * 1000
"""Renew this long before the station's own expiry, as the plugin does."""


class PatExchangeError(Exception):
    """The station refused the Personal Access Token."""


@dataclass(frozen=True)
class JobToken:
    """One exchanged credential: the job token and its lifetime."""

    token: str
    refresh_token: str
    expires_at_ms: int


@dataclass(frozen=True)
class QoderIdentity:
    """What ``userinfo`` reports; every field may be absent."""

    user_id: str = ""
    email: str = ""
    name: str = ""


def pat_from_env(region: QoderRegion) -> str:
    """The first PAT this region's environment variables expose."""
    for name in region.pat_env_names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def exchange_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "qodercli",
        "Cosy-Version": QODER_OPENAPI_COSY_VERSION,
        "Cosy-ClientType": QODER_CLIENT_TYPE,
    }


def _expires_at_ms(payload: dict[str, Any]) -> int:
    """The exchange's expiry: an ISO instant, or ``expires_in`` in *milliseconds*."""
    absolute = iso_as_ms(payload.get("expires_at")) or as_ms(payload.get("expires_at"))
    if absolute is not None:
        return absolute
    raw = payload.get("expires_in")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        # Observed in milliseconds (86400000 for a one-day token), not seconds.
        return int(time.time() * 1000 + raw)
    return int(time.time() * 1000 + DEFAULT_JOB_TOKEN_TTL_MS)


def exchange_job_token(client: httpx.Client, region: QoderRegion, pat: str) -> JobToken:
    """Exchange a PAT for a job token; raises :class:`PatExchangeError` on refusal."""
    try:
        response = client.post(exchange_url(region), json={"personal_token": pat}, headers=exchange_headers(), timeout=EXCHANGE_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise PatExchangeError(f"{region.provider_id}: cannot reach the token exchange ({exc})") from exc
    if response.status_code >= 400:
        raise PatExchangeError(f"{region.provider_id}: PAT exchange failed (HTTP {response.status_code}): {response.text[:200]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise PatExchangeError(f"{region.provider_id}: PAT exchange returned a non-JSON body") from exc
    if not isinstance(payload, dict) or not payload.get("token"):
        raise PatExchangeError(f"{region.provider_id}: PAT exchange returned no job token")
    refresh = payload.get("refresh_token")
    return JobToken(
        token=str(payload["token"]),
        refresh_token=str(refresh) if isinstance(refresh, str) else "",
        expires_at_ms=_expires_at_ms(payload),
    )


def fetch_user_info(client: httpx.Client, region: QoderRegion, job_token: str) -> QoderIdentity:
    """Best-effort identity read; an empty identity is not a failure here."""
    try:
        response = client.get(user_info_url(region), headers=open_api_headers(job_token), timeout=USER_INFO_TIMEOUT_SECONDS)
        if response.status_code >= 400:
            return QoderIdentity()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("qoder: userinfo unavailable (%s)", exc)
        return QoderIdentity()
    if not isinstance(payload, dict):
        return QoderIdentity()
    name = payload.get("name") or payload.get("username") or ""
    return QoderIdentity(user_id=str(payload.get("id") or ""), email=str(payload.get("email") or ""), name=str(name))


def credential_from_pat(client: httpx.Client, region: QoderRegion, pat: str, *, machine_id: str = "") -> dict[str, Any]:
    """A full account credential from a PAT: exchange, identity, machine id.

    The PAT is kept in the blob so renewal can re-exchange it; the job token is
    what requests use, and its expiry (minus the buffer) is what the refresher
    reads.
    """
    job = exchange_job_token(client, region, pat)
    identity = fetch_user_info(client, region, job.token)
    machine = machine_id or get_machine_id()
    credential: dict[str, Any] = {
        "personal_token": pat,
        "access_token": job.token,
        "security_oauth_token": job.token,
        "machine_id": machine,
        "expires_at": str(max(0, job.expires_at_ms - EXPIRY_BUFFER_MS)),
    }
    if job.refresh_token:
        credential["refresh_token"] = job.refresh_token
    if identity.user_id:
        credential["uid"] = identity.user_id
        credential["user_id"] = identity.user_id
    if identity.name:
        credential["nickname"] = identity.name
    if identity.email:
        credential["email"] = identity.email
    return credential


def refresh_device_token(client: httpx.Client, region: QoderRegion, access_token: str, refresh_token: str) -> JobToken:
    """Renew a browser-login (device) credential.

    The device flow's token is not a PAT job token: it renews against the
    center host's ``user/refresh_token`` with the current bearer, and its
    ``expires_in`` is in **seconds** (the PAT exchange's is in milliseconds).
    """
    try:
        response = client.post(
            refresh_url(region),
            json={"refreshToken": refresh_token},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": "qodercli",
            },
            timeout=EXCHANGE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise PatExchangeError(f"{region.provider_id}: cannot reach the token refresh ({exc})") from exc
    if response.status_code >= 400:
        raise PatExchangeError(f"{region.provider_id}: token refresh failed (HTTP {response.status_code}): {response.text[:200]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise PatExchangeError(f"{region.provider_id}: token refresh returned a non-JSON body") from exc
    if not isinstance(payload, dict) or not payload.get("token"):
        raise PatExchangeError(f"{region.provider_id}: token refresh returned no token")
    absolute = iso_as_ms(payload.get("expires_at")) or as_ms(payload.get("expires_at"))
    if absolute is None:
        raw = payload.get("expires_in")
        seconds = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0 else 30 * 24 * 3600
        absolute = int(time.time() * 1000 + seconds * 1000)
    new_refresh = payload.get("refresh_token")
    return JobToken(
        token=str(payload["token"]),
        refresh_token=str(new_refresh) if isinstance(new_refresh, str) and new_refresh else refresh_token,
        expires_at_ms=absolute,
    )