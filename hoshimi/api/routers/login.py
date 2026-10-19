"""Interactive logins: starting one, and watching it come back."""

from __future__ import annotations

from fastapi import APIRouter

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import ERROR_RESPONSES
from hoshimi.core.errors import NotFoundError, ValidationError
from hoshimi.core.login import LoginError, login_providers
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1/login", tags=["login"])


class LoginSessionView(Record):
    """One login attempt as the web UI polls it: where the browser is, and what became of it."""

    id: str
    provider: str
    status: str
    login_url: str = ""
    message: str = ""
    account_id: str = ""
    started_at: float = 0.0
    user_code: str = ""
    instructions: str = ""
    expires_at: float | None = None
    waiting_for: str = ""


class StartRequest(Record):
    """Which station to sign into; there is nothing else to a login start."""

    provider: str


class LoginInput(Record):
    session_id: str
    value: str


@router.post("/input", operation_id="submit_login_input", responses=ERROR_RESPONSES)
def submit_login_input(body: LoginInput, services: ServicesDep) -> dict[str, bool]:
    if services.login is None:
        raise ValidationError("The login service is not available.")
    try:
        services.login.submit(body.session_id, body.value)
    except LoginError as exc:
        raise ValidationError(str(exc)) from None
    return {"accepted": True}


@router.post("/cancel", operation_id="cancel_login", responses=ERROR_RESPONSES)
def cancel_login(session_id: str, services: ServicesDep) -> dict[str, bool]:
    if services.login is None:
        raise ValidationError("The login service is not available.")
    try:
        services.login.cancel(session_id)
    except LoginError as exc:
        raise ValidationError(str(exc)) from None
    return {"cancelled": True}


@router.get("/providers", operation_id="list_login_providers", responses=ERROR_RESPONSES)
def list_login_providers() -> list[str]:
    """Stations that offer an interactive login."""
    return login_providers()


@router.post("/start", operation_id="start_login", response_model=LoginSessionView, responses=ERROR_RESPONSES)
def start_login(body: StartRequest, services: ServicesDep) -> LoginSessionView:
    """Begin a login; the session's ``login_url`` appears as soon as the station issues one."""
    if services.login is None:
        raise ValidationError("The login service is not available.")
    try:
        session = services.login.start(body.provider)
    except LoginError as exc:
        # An unknown station name is a client mistake; the message names what exists.
        raise ValidationError(str(exc)) from None
    return LoginSessionView(**session.as_dict())


@router.get("/poll", operation_id="poll_login", response_model=LoginSessionView, responses=ERROR_RESPONSES)
def poll_login(session_id: str, services: ServicesDep) -> LoginSessionView:
    """Read a session: its browser URL once known, and its verdict once reached."""
    if services.login is None:
        raise ValidationError("The login service is not available.")
    session = services.login.poll(session_id)
    if session is None:
        raise NotFoundError("No such login session.")
    return LoginSessionView(**session.as_dict())
