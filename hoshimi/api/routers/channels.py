"""Channels: every lane with its accounts, health and today's usage, in one read.

An aggregate of what the separate management routes already know — the
provider list, credential renewal state, passive quota, cooled accounts,
network diagnostics, the configured options, and the stats rollups — so the
channels page asks once instead of six times and never shows one lane from
two different moments.

It reads memory only. Balances (``/credits``) ask each station over the
network and are deliberately not part of this report: a page that opens on
every visit must not cost every station a round trip.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from hoshimi.api.deps import ServicesDep
from hoshimi.api.routers.credits import CheckinState, PassiveQuota, passive_quota
from hoshimi.api.routers.providers import CredentialFormView, NetworkDiagnostic, ProviderSummary, list_provider_forms, list_providers, network_diagnostics
from hoshimi.api.routers.settings import NetworkView, network_view
from hoshimi.api.routers.stats import UsageTotals
from hoshimi.core.context import Services
from hoshimi.core.login import login_providers
from hoshimi.core.provider_forms import credential_forms
from hoshimi.core.quota import QUOTA
from hoshimi.core.record import Record
from hoshimi.core.refresh import account_expiry_ms, is_refreshable

router = APIRouter(prefix="/v1", tags=["channels"])

LOW_QUOTA_PERCENT = 10
"""At or below this share of its allowance, a lane asks for attention."""


class ChannelAccount(Record):
    """One account's health: renewal state, refusal hold and today's usage.

    ``expires_at`` is epoch **milliseconds** (as ``/refresh`` reports it);
    ``last_ok`` is epoch seconds; ``cooled_seconds`` is how long the account
    stays out of rotation.
    """

    id: str
    needs_login: bool = False
    refreshable: bool = False
    expires_at: int | None = None
    failures: int = 0
    last_error: str | None = None
    last_ok: float | None = None
    cooled_reason: str = ""
    cooled_seconds: float = 0.0
    usage_today: UsageTotals = Field(default_factory=UsageTotals)
    last_used_at: str | None = None


class ChannelView(Record):
    id: str
    display_name: str
    enabled: bool
    keyless: bool
    configured: bool
    requires_authorization: bool = False
    loginable: bool = False
    addable: bool = False
    forms: list[CredentialFormView] = Field(default_factory=list)
    models: int | None = None
    options: list[str] = Field(default_factory=list)
    network: NetworkView = Field(default_factory=lambda: NetworkView(mode="inherit"))
    client_profile: str | None = None
    diagnostics: list[NetworkDiagnostic] = Field(default_factory=list)
    quota: PassiveQuota | None = None
    accounts: list[ChannelAccount] = Field(default_factory=list)
    usage_today: UsageTotals = Field(default_factory=UsageTotals)
    status: Literal["ok", "attention", "disabled", "empty"] = "ok"


class ChannelsReport(Record):
    channels: list[ChannelView]
    checkin: CheckinState
    auto_refresh: bool
    auto_checkin: bool


@router.get("/channels", operation_id="list_channels")
def list_channels(services: ServicesDep) -> ChannelsReport:
    """Every lane ``/providers`` lists, with everything known about it in memory."""
    return build_report(services)


def build_report(services: Services) -> ChannelsReport:
    now = time.time()
    settings = services.settings
    forms = {row.id: row.forms for row in list_provider_forms()}
    form_ids = set(credential_forms())
    loginable = set(login_providers())
    diagnostics = network_diagnostics(services)
    quota = {row["provider"]: row for row in QUOTA.snapshot(now=now)}
    renewal = {(row["provider"], row["account"]): row for row in services.refresher.status()} if services.refresher is not None else {}
    usage = services.stats.today_rollups(now)
    last_used = services.stats.last_used()

    channels: list[ChannelView] = []
    for summary in list_providers(services):
        pid = summary.id
        cfg = settings.providers.get(pid)
        options = cfg.options if cfg is not None else {}
        lane = services.registry.providers.get(pid)
        cooled = {row["account"]: row for row in lane.cooler.snapshot(now=now)} if lane is not None else {}
        accounts = [_account(services, pid, account_id, renewal.get((pid, account_id)), cooled.get(account_id), usage["accounts"], last_used) for account_id in summary.account_ids]
        reading = quota.get(pid)
        view = ChannelView(
            id=pid,
            display_name=summary.display_name,
            enabled=summary.enabled,
            keyless=summary.keyless,
            configured=cfg is not None,
            requires_authorization=summary.requires_authorization,
            loginable=pid in loginable,
            addable=pid == "agg" or pid in form_ids,
            forms=forms.get(pid, []),
            models=summary.models,
            options=sorted(options),
            network=network_view(options.get("network") or {}, "inherit"),
            client_profile=options.get("client_profile", "sdk") if pid == "cline" else None,
            diagnostics=[row for row in diagnostics if row.provider == pid],
            quota=passive_quota(reading) if reading is not None else None,
            accounts=accounts,
            usage_today=UsageTotals.model_validate(usage["providers"].get(pid, {})),
        )
        view.status = channel_status(view, summary, now)
        channels.append(view)

    checkin = CheckinState(auto_checkin=settings.auto_checkin)
    if services.credits is not None:
        doc = services.credits.store.doc
        checkin = CheckinState(last_date=doc.last_date, last_result=doc.last_result, last_at=doc.last_at, auto_checkin=settings.auto_checkin)
    return ChannelsReport(channels=channels, checkin=checkin, auto_refresh=settings.auto_refresh, auto_checkin=settings.auto_checkin)


def channel_status(view: ChannelView, summary: ProviderSummary, now: float) -> Literal["ok", "attention", "disabled", "empty"]:
    """One word for the lane, in the order an operator would want to hear it.

    ``empty`` is a lane with nothing to serve from: no account of its own and
    no keyless pool, or one only offered (listed because it can be signed into
    or given a key, never configured). ``disabled`` is kept for a lane the
    operator switched off, so the list can tell "not set up" from "turned
    off". ``attention`` is a lane that still serves but has something a human
    could fix — a login, an expired token, a held account, or an allowance
    about to run out.
    """
    if not view.configured and not view.enabled and not view.accounts:
        return "empty"
    if not view.enabled:
        return "disabled"
    if summary.accounts == 0 and not view.keyless:
        return "empty"
    now_ms = now * 1000
    for account in view.accounts:
        if account.needs_login or account.cooled_reason or (account.expires_at is not None and account.expires_at <= now_ms):
            return "attention"
    if view.quota is not None and (view.quota.counters.rate_limited > 0 or (view.quota.percent is not None and view.quota.percent <= LOW_QUOTA_PERCENT)):
        return "attention"
    return "ok"


def _account(
    services: Services,
    provider_id: str,
    account_id: str,
    renewal: dict | None,
    cooled: dict | None,
    usage: dict[str, dict[str, int]],
    last_used: dict[str, float],
) -> ChannelAccount:
    key = f"{provider_id}/{account_id}"
    row = ChannelAccount(id=account_id, usage_today=UsageTotals.model_validate(usage.get(key, {})))
    if renewal is not None:
        row.needs_login = bool(renewal.get("needs_login"))
        row.refreshable = bool(renewal.get("refreshable"))
        row.expires_at = renewal.get("expires_at")
        row.failures = int(renewal.get("failures") or 0)
        row.last_error = renewal.get("last_error")
        row.last_ok = renewal.get("last_ok")
    else:
        # No renewal service (an embedder, a test): the pool's own account
        # still knows whether it needs a login and when its token expires.
        lane = services.registry.providers.get(provider_id)
        account = next((item for item in lane.accounts if item.id == account_id), None) if lane is not None else None
        if account is not None:
            row.needs_login = account.needs_login
            row.refreshable = is_refreshable(account)
            row.expires_at = account_expiry_ms(account)
    if cooled is not None:
        row.cooled_reason = str(cooled.get("reason") or "held")
        row.cooled_seconds = float(cooled.get("seconds_left") or 0.0)
    if key in last_used:
        row.last_used_at = datetime.fromtimestamp(last_used[key]).astimezone().isoformat(timespec="seconds")
    return row
