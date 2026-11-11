"""Qoder CLI login: a Personal Access Token, or the browser device flow.

The CLI lanes authenticate with a PAT (``pt-…``) minted on the account's
integrations page. A PAT is not an API credential: the flow exchanges it for a
short-lived job token, resolves the account identity, and files the whole blob
(the PAT included, so renewal can re-exchange it).

The international edition also offers Qoder's browser login, exactly as the
plugin does: the device page is reported (the UI opens it) while the same session
accepts a pasted PAT, and whichever answers first wins. The China edition has no
device flow, so there the token is the only way in.

A PAT already exported in the environment is used without asking.
"""

from __future__ import annotations

import queue
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from hoshimi.core.expiry import as_ms, iso_as_ms
from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, LoginError, LoginReporter, pkce_pair, poll_pause
from hoshimi.core.qoder.auth import PatExchangeError, credential_from_pat, fetch_user_info, pat_from_env
from hoshimi.core.qoder.cosy import get_machine_id
from hoshimi.core.qoder.region import QoderRegion, device_login_url, device_poll_url, region_for

PAT_PROMPT_SECONDS = min(300.0, LOGIN_TIMEOUT_SECONDS)
DEVICE_POLL_INTERVAL_SECONDS = 2.0
DEVICE_POLL_BUDGET_SECONDS = 180.0
DEFAULT_DEVICE_TTL_MS = 30 * 24 * 60 * 60 * 1000


def _credential_from_token(
    client: httpx.Client,
    region: QoderRegion,
    token: str,
    *,
    refresh_token: str,
    expires_at_ms: int,
    user_id: str,
    machine_id: str,
) -> dict[str, Any]:
    """The account blob a device approval produces (no PAT to re-exchange)."""
    identity = fetch_user_info(client, region, token)
    credential: dict[str, Any] = {
        "access_token": token,
        "security_oauth_token": token,
        "machine_id": machine_id,
        "expires_at": str(max(0, expires_at_ms - 5 * 60 * 1000)),
    }
    if refresh_token:
        credential["refresh_token"] = refresh_token
    uid = user_id or identity.user_id
    if uid:
        credential["uid"] = uid
        credential["user_id"] = uid
    name = identity.name
    if name:
        credential["nickname"] = name
    if identity.email:
        credential["email"] = identity.email
    return credential


def _device_expiry(payload: dict[str, Any]) -> int:
    absolute = iso_as_ms(payload.get("expires_at")) or as_ms(payload.get("expires_at"))
    if absolute is not None:
        return absolute
    raw = payload.get("expires_in")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return int(time.time() * 1000 + raw * 1000)
    return int(time.time() * 1000 + DEFAULT_DEVICE_TTL_MS)


def _pasted(reporter: LoginReporter) -> str:
    try:
        return reporter.inputs.get_nowait().strip()
    except queue.Empty:
        return ""


def _exchange_pasted(client: httpx.Client, reporter: LoginReporter, region: QoderRegion, value: str) -> dict[str, Any]:
    if not value.startswith("pt-"):
        raise LoginError(f"{region.provider_id}: a Personal Access Token starts with 'pt-' (create one at {region.pat_manage_url})")
    try:
        credential = credential_from_pat(client, region, value)
    except PatExchangeError as exc:
        raise LoginError(str(exc)) from exc
    if not credential.get("uid"):
        raise LoginError(f"{region.provider_id}: the station accepted the token but reported no account id")
    reporter.update({"waiting_for": ""})
    return credential


def _wait_for_pat(client: httpx.Client, reporter: LoginReporter, region: QoderRegion, timeout: float) -> dict[str, Any]:
    """China: the token is the only way in, so the session just waits for it."""
    reporter.update({
        "waiting_for": "personal_token",
        "instructions": f"Paste the Personal Access Token (pt-…) from {region.pat_manage_url}",
        "expires_at": time.time() + timeout,
    })
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            reporter.check_cancelled()
            value = _pasted(reporter)
            if value:
                return _exchange_pasted(client, reporter, region, value)
            reporter.wait(0.2)
        raise LoginError(f"{region.provider_id}: login timed out waiting for the Personal Access Token")
    finally:
        reporter.update({"waiting_for": ""})


def _device_or_pat(client: httpx.Client, reporter: LoginReporter, region: QoderRegion) -> dict[str, Any]:
    """International: race the browser approval against a pasted token.

    The device page is a pure URL build (challenge, nonce, machine id on the
    query); the station then watches ``deviceToken/poll`` until the user
    approves. 202/404 there means "not yet", not "gone".
    """
    verifier, challenge = pkce_pair()
    nonce = str(uuid.uuid4())
    machine_id = get_machine_id()
    reporter(device_login_url(region, challenge, machine_id, nonce))
    reporter.update({
        "waiting_for": "personal_token",
        "instructions": f"Finish sign-in in the browser, or paste a Personal Access Token (pt-…) from {region.pat_manage_url}",
        "expires_at": time.time() + PAT_PROMPT_SECONDS,
    })
    poll = device_poll_url(region, nonce, verifier)
    deadline = time.monotonic() + min(DEVICE_POLL_BUDGET_SECONDS, PAT_PROMPT_SECONDS)
    failures = 0
    try:
        while time.monotonic() < deadline:
            reporter.check_cancelled()
            value = _pasted(reporter)
            if value:
                return _exchange_pasted(client, reporter, region, value)
            poll_pause(DEVICE_POLL_INTERVAL_SECONDS)
            try:
                response = client.get(poll, headers={"Accept": "application/json", "User-Agent": "qodercli"}, timeout=15.0)
                failures = 0
            except httpx.HTTPError as exc:
                failures += 1
                if failures >= 5:
                    raise LoginError(f"{region.provider_id}: cannot reach the login service ({exc})") from exc
                continue
            if response.status_code in (202, 404):
                continue
            if response.status_code >= 400:
                raise LoginError(f"{region.provider_id}: device token polling failed (HTTP {response.status_code})")
            try:
                payload = response.json()
            except ValueError:
                continue
            if not isinstance(payload, dict) or not payload.get("token"):
                continue
            return _credential_from_token(
                client,
                region,
                str(payload["token"]),
                refresh_token=str(payload.get("refresh_token") or ""),
                expires_at_ms=_device_expiry(payload),
                user_id=str(payload.get("user_id") or ""),
                machine_id=machine_id,
            )
        raise LoginError(f"{region.provider_id}: login timed out before authorization completed")
    finally:
        reporter.update({"waiting_for": ""})


def start_qodercli_login(client: httpx.Client, report: Callable[[str], None], variant: str) -> dict[str, Any]:
    """One CLI-lane login. ``variant`` is the lane id (``qodercli`` / ``qodercli-cn``)."""
    region = region_for(variant)
    if region.provider_id != variant:
        raise LoginError(f"{variant}: unknown Qoder CLI lane")

    pat = pat_from_env(region)
    if pat:
        try:
            credential = credential_from_pat(client, region, pat)
        except PatExchangeError as exc:
            raise LoginError(str(exc)) from exc
        # The manager requires a reported URL; the integrations page is where
        # this credential came from.
        report(region.pat_manage_url)
        return credential

    if not isinstance(report, LoginReporter):
        raise LoginError(f"{variant}: this login needs an interactive session (paste a Personal Access Token from {region.pat_manage_url})")

    if region.supports_browser_login:
        return _device_or_pat(client, report, region)
    report(region.pat_manage_url)
    return _wait_for_pat(client, report, region, PAT_PROMPT_SECONDS)


def start_qodercli_global_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    return start_qodercli_login(client, report, "qodercli")


def start_qodercli_cn_login(client: httpx.Client, report: Callable[[str], None]) -> dict[str, Any]:
    return start_qodercli_login(client, report, "qodercli-cn")