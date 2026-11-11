"""Interactive logins: the session registry the CLI and the API both drive.

A flow is one function per station. It receives the shared HTTP client and a
``report`` callback it must call with the browser URL the moment the station
gives one — that is the whole contract that makes both consumers work: the
CLI prints the URL and opens a browser, the API hands it to the web UI, and
neither needs to know whether the station answered with a device code, an
external login link or a local callback port.

The flow then blocks until the credential is ready and returns it as the raw
account dict the providers already speak. Failures are :class:`LoginError`
with a human-readable message; anything else is a bug and is logged as one.
"""

from __future__ import annotations

import copy
import logging
import queue
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

from hoshimi.core.eac_relay import DEFAULT_EAC_ACCOUNT_ID
from hoshimi.core.login.common import LOGIN_TIMEOUT_SECONDS, LoginError, LoginReporter
from hoshimi.core.net.client import NetworkClient, operation
from hoshimi.core.providers.base import ProviderError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hoshimi.core.config import Settings

logger = logging.getLogger(__name__)

ReportUrl = Callable[[str], None]
LoginFlow = Callable[[httpx.Client, ReportUrl], dict[str, Any]]

LOGIN_FLOW_TIMEOUT_SECONDS = LOGIN_TIMEOUT_SECONDS + 60
"""One minute of headroom over the flows' own waits, so they fail first."""

SESSION_TTL_SECONDS = 3600
"""Finished sessions are forgotten after an hour; the UI only needs minutes."""

STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

INPUT_STATES = frozenset({"manual_code", "personal_token"})
"""The waits a pasted answer resolves: a browser code, or a station token."""


@dataclass
class LoginSession:
    """One login attempt: where the browser is, and what became of it."""

    id: str
    provider: str
    status: str = STATUS_PENDING
    login_url: str = ""
    message: str = ""
    account_id: str = ""
    started_at: float = field(default_factory=time.time)
    user_code: str = ""
    instructions: str = ""
    expires_at: float | None = None
    waiting_for: str = ""
    inputs: queue.Queue[str] = field(default_factory=queue.Queue, repr=False)
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    replace_account_id: str | None = None
    original_account: dict[str, Any] | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "status": self.status,
            "login_url": self.login_url,
            "message": self.message,
            "account_id": self.account_id,
            "started_at": self.started_at,
            "user_code": self.user_code,
            "instructions": self.instructions,
            "expires_at": self.expires_at,
            "waiting_for": self.waiting_for,
        }


def _flows() -> dict[str, LoginFlow]:
    """The station flows, imported lazily so one broken station cannot break startup."""
    from hoshimi.core.login.buddy import start_buddy_login
    from hoshimi.core.login.cline import start_cline_login
    from hoshimi.core.login.codearts import start_codearts_login
    from hoshimi.core.login.codex import start_codex_login
    from hoshimi.core.login.eac import start_eac_login
    from hoshimi.core.login.gemini import start_gemini_login
    from hoshimi.core.login.kilo import start_kilo_login
    from hoshimi.core.login.lobsterai import start_lobsterai_login
    from hoshimi.core.login.loomy import start_loomy_login
    from hoshimi.core.login.minimax import start_minimax_login
    from hoshimi.core.login.provider_oauth import provider_login_flows
    from hoshimi.core.login.qoder import start_qoder_login
    from hoshimi.core.login.qodercli import start_qodercli_login
    from hoshimi.core.login.raccoon import start_raccoon_login
    from hoshimi.core.login.trae import start_trae_login
    from hoshimi.core.login.zcode import start_zcode_login

    return {
        **provider_login_flows(),
        "cline": start_cline_login,
        "kilo": start_kilo_login,
        "minimax": start_minimax_login,
        "buddy": lambda client, report: start_buddy_login(client, report, "buddy"),
        "workbuddy": lambda client, report: start_buddy_login(client, report, "workbuddy"),
        "zcode": start_zcode_login,
        "qoder": lambda client, report: start_qoder_login(client, report, "qoder"),
        "qodercn": lambda client, report: start_qoder_login(client, report, "qodercn"),
        "qodercli": lambda client, report: start_qodercli_login(client, report, "qodercli"),
        "qodercli-cn": lambda client, report: start_qodercli_login(client, report, "qodercli-cn"),
        "gemini": start_gemini_login,
        "trae": start_trae_login,
        "lobsterai": start_lobsterai_login,
        "loomy": start_loomy_login,
        "codearts": start_codearts_login,
        "codex": start_codex_login,
        "raccoon": start_raccoon_login,
        "eac": start_eac_login,
    }


def login_providers() -> list[str]:
    """Station ids that offer an interactive login."""
    return sorted(_flows())


class LoginManager:
    """Starts flows, keeps their sessions, and files the credential it gets back."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        on_persist: Callable[[LoginManager], None] | None = None,
        on_account_persist: Callable[[str, str], None] | None = None,
    ):
        self.settings = settings
        self._client = client
        self._on_persist = on_persist
        self._on_account_persist = on_account_persist
        self._sessions: dict[str, LoginSession] = {}
        self._lock = threading.Lock()
        self._persist_lock = threading.RLock()
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}

    def diagnostics(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return [(pid, dict(row)) for pid, rows in self._diagnostics.items() for row in rows]

    # -- the sessions ------------------------------------------------------ #
    def start(self, provider: str, *, account_id: str | None = None) -> LoginSession:
        flows = _flows()
        flow = flows.get(provider)
        if flow is None:
            known = ", ".join(sorted(flows))
            raise LoginError(f"no interactive login for '{provider}' (known: {known})")
        original = copy.deepcopy(self._account(provider, account_id)) if account_id is not None else None
        if provider == "eac":
            from hoshimi.core.login.eac import _resolve_account, start_eac_login

            account = {**original, "id": account_id} if original is not None else _resolve_account(self.settings)
            if account is None:
                raise LoginError("eac: configured accounts have no usable relay; select the bundled relay or configure a worker relay")
            flow = partial(start_eac_login, account=account)
        if provider == "cline":
            cfg = self.settings.providers.get(provider)
            if cfg is not None and cfg.options.get("client_profile") == "extension":
                from hoshimi.core.login.cline_browser import start_cline_browser_login

                flow = start_cline_browser_login
        self._prune()
        session = LoginSession(id=secrets.token_hex(8), provider=provider, replace_account_id=account_id, original_account=original)
        with self._lock:
            self._sessions[session.id] = session
        thread = threading.Thread(target=self._run, args=(session, flow), daemon=True, name=f"hoshimi-login-{provider}")
        thread.start()
        return session

    def poll(self, session_id: str) -> LoginSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def close(self) -> None:
        with self._persist_lock, self._lock:
            for session in self._sessions.values():
                if session.status == STATUS_PENDING:
                    session.cancelled.set()
                    session.status = STATUS_FAILED
                    session.message = "Login cancelled"
        if self._client is not None:
            self._client.close()

    def submit(self, session_id: str, value: str) -> None:
        session = self.poll(session_id)
        if session is None or session.status != STATUS_PENDING or session.waiting_for not in INPUT_STATES:
            raise LoginError("This login is not waiting for an authorization code or token.")
        if not value.strip() or len(value) > 16384:
            raise LoginError("Supply a valid authorization code or callback URL.")
        session.inputs.put(value)

    def cancel(self, session_id: str) -> None:
        session = self.poll(session_id)
        if session is None:
            raise LoginError("No such login session.")
        with self._persist_lock:
            if session.status == STATUS_PENDING:
                session.cancelled.set()
                session.status = STATUS_FAILED
                session.message = "Login cancelled"

    # -- the worker -------------------------------------------------------- #
    def _run(self, session: LoginSession, flow: LoginFlow) -> None:
        def update(values: dict[str, Any]) -> None:
            for key, value in values.items():
                setattr(session, key, value)

        report = LoginReporter(update, inputs=session.inputs, cancelled=session.cancelled)
        cfg = self.settings.providers.get(session.provider)
        client = self._client or NetworkClient(
            provider=session.provider, network=self.settings.network,
            options=cfg.options if cfg else {}, timeout=30.0, login=True,
        )
        try:
            with operation("login"):
                credential = flow(client, report)
            if session.login_url == "":
                raise LoginError("the station returned no login URL")
            with self._persist_lock:
                report.check_cancelled()
                if session.replace_account_id is not None and self._account(session.provider, session.replace_account_id) != session.original_account:
                    raise LoginError("The account changed during login; start again.")
                if session.provider == "eac":
                    self._eac_target(credential)
                # EAC also owns a per-install token file. If the account write
                # fails after that commit, put its previous authorization back.
                from hoshimi.core.eac_user import clear_eac_user, read_eac_user, write_eac_user

                previous_eac = read_eac_user() if session.provider == "eac" else None
                try:
                    report.commit()
                    account_id = self._persist(session.provider, credential, account_id=session.replace_account_id)
                except Exception:
                    if session.provider == "eac":
                        if previous_eac is None:
                            clear_eac_user()
                        else:
                            write_eac_user(previous_eac)
                    raise
                session.status = STATUS_SUCCEEDED
                session.account_id = account_id
        except (LoginError, ProviderError) as exc:
            session.status = STATUS_FAILED
            session.message = str(exc)
            return
        except Exception as exc:
            logger.exception("login flow for %s failed", session.provider)
            session.status = STATUS_FAILED
            session.message = f"login failed unexpectedly: {exc}"
            return
        finally:
            if isinstance(client, NetworkClient):
                with self._lock:
                    self._diagnostics[session.provider] = client.diagnostics()
            if self._client is None:
                client.close()

    # -- the write-back ---------------------------------------------------- #
    def _persist(self, provider: str, credential: dict[str, Any], *, account_id: str | None = None) -> str:
        """File the new account under ``provider``, replacing the same identity."""
        from hoshimi.core.config import ProviderConfig, save_settings

        previous = self.settings.providers.get(provider)
        cfg = copy.deepcopy(previous)
        if cfg is None:
            cfg = ProviderConfig(id=provider, enabled=True)
        if account_id is None:
            cfg.enabled = True
        identity = _identity_of(credential)
        replacement = account_id
        account_id = account_id or ""
        target = None
        if provider == "eac":
            account_id = self._eac_target(credential)
            target = next((raw for index, raw in enumerate(cfg.accounts) if str(raw.get("id") or f"eac-{index}") == account_id), None)
            credential = {key: credential[key] for key in ("user_token", "user_login", "user_avatar", "relay") if key in credential}
        elif replacement is not None:
            target = next((raw for index, raw in enumerate(cfg.accounts) if str(raw.get("id") or f"{provider}-{index}") == replacement), None)
            if target is None:
                raise LoginError("The account was removed during login; start again.")
        elif identity is not None:
            key, value = identity
            target = next((raw for raw in cfg.accounts if str(raw.get(key) or "") == value), None)
        if target is None:
            account_id = account_id or f"{provider}-{secrets.token_hex(3)}"
            cfg.accounts.append({"id": account_id, **credential})
        else:
            account_id = str(target.get("id") or account_id or f"{provider}-{secrets.token_hex(3)}")
            # A targeted re-login replaces old tokens/identity completely, while
            # retaining user configuration such as model and endpoint overrides.
            from hoshimi.core.provider_forms import credential_forms

            configuration = {f.key for form in credential_forms().get(provider, ()) for f in form.fields if f.kind != "secret"}
            updated = {key: value for key, value in target.items() if key in configuration} if replacement is not None and provider != "eac" else dict(target)
            updated.update(credential)
            updated["id"] = account_id
            target.clear()
            target.update(updated)
        saved = next(raw for raw in cfg.accounts if raw.get("id") == account_id)
        saved["_hoshimi_login"] = "browser"
        candidate = copy.copy(self.settings)
        candidate.providers = {**self.settings.providers, provider: cfg}
        save_settings(candidate)
        self.settings.providers[provider] = cfg
        if self._on_persist is not None:
            try:
                self._on_persist(self)
            except Exception:
                if previous is None:
                    self.settings.providers.pop(provider, None)
                else:
                    self.settings.providers[provider] = previous
                save_settings(self.settings)
                try:
                    self._on_persist(self)
                except Exception:
                    logger.exception("could not restore the running pool after a failed login update")
                raise
        if self._on_account_persist is not None:
            self._on_account_persist(provider, account_id)
        return account_id

    def _account(self, provider: str, account_id: str) -> dict[str, Any]:
        cfg = self.settings.providers.get(provider)
        for index, raw in enumerate(cfg.accounts if cfg else []):
            if str(raw.get("id") or f"{provider}-{index}") == account_id:
                return raw
        raise LoginError("The account no longer exists; start again.")

    def _eac_target(self, credential: dict[str, Any]) -> str:
        cfg = self.settings.providers.get("eac")
        if (cfg is None or not cfg.accounts) and credential.get("relay") == "bundled" and credential.get("id") == DEFAULT_EAC_ACCOUNT_ID:
            return DEFAULT_EAC_ACCOUNT_ID
        for index, raw in enumerate(cfg.accounts if cfg else []):
            account_id = str(raw.get("id") or f"eac-{index}")
            if account_id == credential.get("id", credential.get("account_id")):
                if all(raw.get(key) == credential.get(key) for key in ("relay", "base_url", "signing_secret", "access_token")):
                    return account_id
                break
        raise LoginError("eac: the relay account changed or was removed during login; start again")

    def _prune(self) -> None:
        cutoff = time.time() - SESSION_TTL_SECONDS
        with self._persist_lock, self._lock:
            for key in [k for k, s in self._sessions.items() if s.started_at < cutoff]:
                self._sessions[key].cancelled.set()
                del self._sessions[key]


def _identity_of(credential: dict[str, Any]) -> tuple[str, str] | None:
    """The stable identity a station reports, so a re-login replaces, not stacks."""
    for key in ("user_id", "account_id", "userid", "uid", "sub", "access_key_id", "api_key", "access_token"):
        value = credential.get(key)
        if isinstance(value, str) and value:
            return key, value
    return None
