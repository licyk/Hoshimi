"""Daily check-in and balance: what each station still has, and taking today's free credits."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hoshimi.api.deps import ServicesDep
from hoshimi.core.quota import QUOTA
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["credits"])


class BalanceRow(Record):
    provider: str
    account: str
    available: int | None = None
    unit: str = "credits"
    message: str = ""


class CheckinState(Record):
    """The daily sweep's own memory: whether today has already run, and how it went."""

    last_date: str = ""
    last_result: str = ""
    last_at: float = 0.0
    auto_checkin: bool = True


class CooledAccount(Record):
    """One account held out of rotation, with the refusal that held it.

    `reason` is one of `rate_limited` / `policy_blocked` — a name the gateway
    chose, never the station's body, so this record stays free of anything a
    credential could leak.
    """

    provider: str
    account: str
    reason: str = ""
    seconds_left: float = 0.0


class BalanceReport(Record):
    rows: list[BalanceRow] = Field(default_factory=list)
    checkin: CheckinState = Field(default_factory=CheckinState)
    # Quota read off the responses the gateway was sending anyway — no extra
    # round trip, and available even on a station with no balance endpoint.
    passive: list[PassiveQuota] = Field(default_factory=list)
    # Accounts a station has told us to stop offering, and for how much
    # longer. Without this the table is invisible: a caller sees a lane that
    # is suddenly down to half its accounts and has no way to ask why.
    cooled: list[CooledAccount] = Field(default_factory=list)


class PassiveCounters(Record):
    """What the station's answers say about the connection: counts, never bodies."""

    responses: int = 0
    auth_failures: int = 0
    rate_limited: int = 0
    server_errors: int = 0
    quota_header_drift: int = 0


class PassiveQuota(Record):
    """One provider's remaining allowance, and the headers it was kind enough to send.

    `source` is a header *name*, never a value — this record is safe to return
    from a diagnostics endpoint precisely because it holds no credential.
    `stale` marks a reading past the five minutes it is worth, so a client can
    show the number without implying it is still true.
    """

    provider: str
    remaining: float | None = None
    limit: float | None = None
    percent: int | None = None
    source: str = ""
    updated_at: float = 0.0
    stale: bool = False
    counters: PassiveCounters = Field(default_factory=PassiveCounters)


class ClaimRequest(BaseModel):
    """An on-demand claim round: nothing means "every provider"."""

    provider: str | None = None


class ClaimRow(Record):
    provider: str
    account: str
    status: str
    credits: int = 0
    covers_today: bool = False
    message: str = ""


class ClaimReport(Record):
    last_date: str = ""
    last_result: str = ""
    last_at: float = 0.0
    rows: list[ClaimRow] = Field(default_factory=list)


@router.get("/credits", operation_id="get_credits")
def get_credits(services: ServicesDep, provider: str | None = None) -> BalanceReport:
    """Read-only balance for every account that can answer."""
    # The switch is settings, not the credits service, so it travels even on an install
    # with no check-in thread at all.
    report = BalanceReport(checkin=CheckinState(auto_checkin=services.settings.auto_checkin))
    report.passive = [
        PassiveQuota(
            provider=row["provider"],
            remaining=row["remaining"],
            limit=row["limit"],
            percent=row["percent"],
            source=row["source"],
            updated_at=row["updated_at"],
            stale=row["stale"],
            counters=PassiveCounters(**row["counters"]),
        )
        for row in QUOTA.snapshot()
        if provider is None or row["provider"] == provider
    ]
    report.cooled = [
        CooledAccount(provider=row["provider"], account=row["account"], reason=row["reason"], seconds_left=row["seconds_left"])
        for lane in services.registry.providers.values()
        for row in lane.cooler.snapshot()
        if provider is None or row["provider"] == provider
    ]
    if services.credits is None:
        return report
    doc = services.credits.store.doc
    report.rows = [
        BalanceRow(provider=row.provider, account=row.account, available=row.available, unit=row.unit, message=row.message)
        for row in services.credits.balance(provider)
    ]
    report.checkin = CheckinState(
        last_date=doc.last_date,
        last_result=doc.last_result,
        last_at=doc.last_at,
        auto_checkin=services.settings.auto_checkin,
    )
    return report


@router.post("/credits/claim", operation_id="claim_credits")
def claim_credits(services: ServicesDep, body: ClaimRequest | None = None) -> ClaimReport:
    """Renew today's free credits (stations answer "already" on a second try)."""
    if services.credits is None:
        return ClaimReport()
    request = body or ClaimRequest()
    outcomes = services.credits.claim(request.provider)
    doc = services.credits.store.doc
    return ClaimReport(
        last_date=doc.last_date,
        last_result=doc.last_result,
        last_at=doc.last_at,
        rows=[
            ClaimRow(provider=o.provider, account=o.account, status=o.status, credits=o.credits, covers_today=o.covers_today, message=o.message)
            for o in outcomes
        ],
    )
