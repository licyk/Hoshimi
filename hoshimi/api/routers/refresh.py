"""Credential renewal: what each account's token state is, and renewing on demand."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hoshimi.api.deps import ServicesDep
from hoshimi.core.record import Record
from hoshimi.core.refresh import RefreshResult as CoreRefreshResult

router = APIRouter(prefix="/v1", tags=["refresh"])


class AccountStatus(Record):
    """One account's renewal state; the web UI reads these to flag a login."""

    provider: str
    account: str
    refreshable: bool
    needs_login: bool
    expires_at: int | None = None
    failures: int = 0
    last_error: str | None = None
    last_ok: float | None = None


class RefreshReport(Record):
    auto_refresh: bool
    accounts: list[AccountStatus] = Field(default_factory=list)


class RefreshRequest(BaseModel):
    """An on-demand round: nothing means "every provider", ``force`` means "ignore the window"."""

    provider: str | None = None
    force: bool = False


class RefreshOutcome(Record):
    provider: str
    account: str
    ok: bool
    persisted: bool = False
    terminal: bool = False
    error: str = ""


@router.get("/refresh", operation_id="get_refresh_status")
def get_refresh_status(services: ServicesDep) -> RefreshReport:
    """Per-account token state, so a lane that needs a new login is visible."""
    if services.refresher is None:
        return RefreshReport(auto_refresh=False, accounts=[])
    return RefreshReport(auto_refresh=services.settings.auto_refresh, accounts=[AccountStatus.model_validate(row) for row in services.refresher.status()])


@router.post("/refresh", operation_id="refresh_credentials")
def refresh_credentials(services: ServicesDep, body: RefreshRequest | None = None) -> list[RefreshOutcome]:
    """Renew the accounts that are due now (or every refreshable one, with ``force``).

    A station that refused the refresh token is not retried in this round; it
    comes back as ``terminal`` so the client knows the account needs a login.
    """
    if services.refresher is None:
        return []
    request = body or RefreshRequest()
    results: list[CoreRefreshResult] = services.refresher.run_once(request.provider, force=request.force)
    return [RefreshOutcome(provider=result.provider, account=result.account, ok=result.ok, persisted=result.persisted, terminal=result.terminal, error=result.error) for result in results]
