"""EAC: a gateway-mediated GitHub device flow, not a station OAuth dance.

The relay that answers ``/v1/models`` and ``/v1/chat/completions`` is also the
endpoint that hands out per-install authorizations: it owns the GitHub OAuth
flow, watches the user star the repo, and persists the result. The flow on
this side is a polling loop over a short link token.

``prepare`` asks the gateway to register the link and pre-arms a GitHub
authorization. The answer is a 302 (a redirect the local browser opens); a
non-2xx here means the gateway refused the link. ``poll`` fetches
``/auth/poll?link=<id>&retain=1`` every couple of seconds: ``pending`` keeps
the loop going, ``ok`` writes the per-install authorization to
``eac-user.json`` and fires an optional ACK, ``expired`` and ``unstarred``
are terminal.

The credential is a single per-install record, not a station account:
every worker/direct relay key this install holds shares the same GitHub
token, so the file lives beside Hoshimi state under the platform data
directory, not on any one account.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx

from hoshimi.core.eac_relay import default_eac_account, relay_credential, validate_relay
from hoshimi.core.eac_user import read_eac_user, write_eac_user
from hoshimi.core.errors import ValidationError
from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, LoginError, LoginReporter, poll_pause

POLL_INTERVAL_SECONDS = 1.5
START_TIMEOUT_SECONDS = 15.0
POLL_TIMEOUT_SECONDS = 15.0
ACK_TIMEOUT_SECONDS = 3.0

LINK_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
LINK_LENGTH = 32

# Compatibility seam for standalone callers. LoginManager passes an explicit
# account snapshot; managers must never bind their own settings globally.
_settings_getter: Callable[[], Any] | None = None


def bind_settings(getter: Callable[[], Any] | None) -> None:
    """Install a callable returning the live ``Settings`` for EAC flow setup.

    Standalone callers may bind a getter; managed sessions pass their account
    explicitly so another gateway cannot replace an in-flight session's target.
    """
    global _settings_getter
    _settings_getter = getter


def _new_link() -> str:
    return "".join(secrets.choice(LINK_CHARSET) for _ in range(LINK_LENGTH))


def _gateway_root(account: dict[str, Any]) -> str:
    """The gateway host the lane talks to, with the ``/v1`` suffix stripped.

    The relay is reachable on the ``/v1``-rooted base URL the provider also
    uses; auth lives on the same origin, just one path segment higher.
    """
    raw = relay_credential(account).get("base_url") if isinstance(account, dict) else None
    base = raw if isinstance(raw, str) else ""
    return base.strip().rstrip("/").removesuffix("/v1")


def _resolve_account(settings: Any = None) -> dict[str, Any] | None:
    """Prefer configured worker relays; a fresh install uses the bundled relay."""
    if settings is None and _settings_getter is not None:
        settings = _settings_getter()
    if settings is None:
        return default_eac_account()
    providers = getattr(settings, "providers", None)
    if not isinstance(providers, dict):
        return default_eac_account()
    cfg = providers.get("eac")
    if cfg is None:
        return default_eac_account()
    accounts = getattr(cfg, "accounts", None)
    if not isinstance(accounts, list) or not accounts:
        return default_eac_account()
    candidates = []
    for index, entry in enumerate(accounts):
        if isinstance(entry, dict) and (entry.get("base_url") or entry.get("relay") == "bundled"):
            candidates.append({**entry, "id": str(entry.get("id") or f"eac-{index}")})
    return next((entry for entry in candidates if entry.get("relay") == "bundled" or entry.get("signing_secret")), candidates[0] if candidates else None)


def _poll_once(client: httpx.Client, base_root: str, link: str) -> tuple[str, dict[str, Any]]:
    """One poll of the gateway. ``status`` is whatever the gateway named; a non-JSON
    answer or HTTP error is mapped to a synthetic terminal status so the loop
    can keep its simple state machine.
    """
    try:
        response = client.get(
            f"{base_root}/auth/poll",
            params={"link": link, "retain": "1"},
            headers={"accept": "application/json"},
            timeout=POLL_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return "transport", {}
    if response.status_code >= 400:
        try:
            payload = json.loads(response.text)
        except ValueError:
            payload = {}
        return f"http-{response.status_code}", payload if isinstance(payload, dict) else {}
    try:
        payload = response.json()
    except ValueError:
        return "malformed", {}
    if not isinstance(payload, dict):
        return "malformed", {}
    status = payload.get("status")
    return status if isinstance(status, str) else "malformed", payload


def _persist_authorization(client: httpx.Client, record: dict[str, Any], *, base_root: str, link: str, ack_required: bool) -> None:
    if not write_eac_user(record):
        raise LoginError("eac: failed to persist the per-install authorization; check data-dir permissions")
    persisted = read_eac_user()
    if persisted is None or persisted.get("token") != record["token"]:
        raise LoginError("eac: the persisted token did not survive a write-then-read round trip")
    if ack_required:
        try:
            client.post(f"{base_root}/auth/ack", params={"link": link},
                        headers={"x-ofm-user": record["token"], "accept": "application/json"},
                        timeout=ACK_TIMEOUT_SECONDS, follow_redirects=False)
        except httpx.HTTPError:
            pass


def start_eac_login(client: httpx.Client, report: Callable[[str], None], *, account: dict[str, Any] | None = None) -> dict[str, Any]:
    """Drive the GitHub device flow through the EAC gateway.

    Returns ``{user_token, user_login, user_avatar, account_id}``. The
    ``user_token`` field rides the wire as ``x-ofm-user``; the LoginManager
    binds it onto the existing EAC account so a worker/direct relay answer
    one credential together.
    """
    account = dict(account) if account is not None else _resolve_account()
    if account is None:
        raise LoginError("eac: configured accounts have no usable relay; select the bundled relay or configure a worker relay")

    try:
        validate_relay(account, login=True)
    except ValidationError as exc:
        raise LoginError(exc.message) from None

    base_root = _gateway_root(account)
    if not base_root:
        raise LoginError("eac: the account needs base_url (the relay's /v1 root)")

    link = _new_link()
    try:
        start_response = client.get(
            f"{base_root}/auth/github/start",
            params={"link": link},
            timeout=START_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise LoginError(f"eac: cannot reach the authorization service ({exc})") from exc

    if start_response.status_code != 302:
        raise LoginError(f"eac: authorization start refused (HTTP {start_response.status_code})")

    report(f"{base_root}/auth/github/start?link={link}")

    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    last_status = "pending"
    while time.monotonic() < deadline:
        if isinstance(report, LoginReporter):
            report.wait(POLL_INTERVAL_SECONDS)
        else:
            poll_pause(POLL_INTERVAL_SECONDS)
        last_status, payload = _poll_once(client, base_root, link)
        if isinstance(report, LoginReporter):
            report.check_cancelled()
        if last_status == "pending":
            continue
        if last_status == "expired":
            raise LoginError("eac: authorization expired, please start again")
        if last_status == "unstarred":
            raise LoginError("eac: the GitHub account has not starred the upstream repository")
        if last_status != "ok":
            continue
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise LoginError("eac: the gateway returned no token for this install")
        record = {
            "token": token,
            "login": payload.get("login", "") if isinstance(payload.get("login"), str) else "",
            "avatar": payload.get("avatar", "") if isinstance(payload.get("avatar"), str) else "",
        }
        persist = partial(_persist_authorization, client, record, base_root=base_root, link=link, ack_required=payload.get("ackRequired") is True)
        if isinstance(report, LoginReporter):
            report.on_commit(persist)
        else:
            persist()
        return {
            **account,
            "user_token": token,
            "user_login": record["login"],
            "user_avatar": record["avatar"],
            "account_id": str(account.get("id") or "eac-1"),
        }

    raise LoginError(f"eac: login timed out (last status: {last_status})")
