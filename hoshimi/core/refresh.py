"""Credential renewal: the verdict, the timer, and writing survivors back.

Two separate questions live here, deliberately not mixed:

- *should this account be renewed now?* — a lead window before the recorded
  expiry, so a turn never discovers a dead token mid-stream. An account with
  no recorded expiry is renewed rather than gambled on;
- *what happens when renewal fails?* — a station that refused the refresh
  token (401/403, or a body that says so) is terminal for that account and
  takes it out of rotation; anything else is retried on the next round.

The write-back is the delicate part. ``config.json`` is a file the operator
owns, so a renewal only ever rewrites the one account it changed, through the
same atomic path a settings save uses — and only when the file actually has
that account. An account imported from an OFM data dir has nowhere to go and
is simply renewed in memory for as long as the process lives.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hoshimi.core.config import Settings, save_settings
from hoshimi.core.expiry import as_ms, iso_as_ms
from hoshimi.core.providers.base import Account, Provider, RefreshTokenRejected

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hoshimi.core.registry import Registry

logger = logging.getLogger(__name__)

REFRESH_LEAD_MS = 3_600_000
"""Renew this long before expiry, so a turn never meets a dead token."""

REFRESH_INTERVAL_SECONDS = 30 * 60
"""One sweep of every account on this cadence."""

FIRST_SWEEP_DELAY_SECONDS = 30
"""A fresh boot should not stampede every station at once."""


def account_expiry_ms(account: Account) -> int | None:
    """The account's absolute expiry in ms, across the field names stations use."""
    for key in ("expires_at", "expire_time"):
        ms = as_ms(account.get(key))
        if ms is not None:
            return ms
    return iso_as_ms(account.get("expiry"))


def is_refreshable(account: Account) -> bool:
    token = account.get("refresh_token")
    return isinstance(token, str) and bool(token.strip())


def needs_refresh(account: Account, now_ms: float | None = None) -> bool:
    """Renew when the lead window has opened, or when nothing records an expiry."""
    if not is_refreshable(account):
        return False
    expiry = account_expiry_ms(account)
    if expiry is None:
        return True
    now = time.time() * 1000 if now_ms is None else now_ms
    return expiry - now <= REFRESH_LEAD_MS


@dataclass
class RefreshResult:
    provider: str
    account: str
    ok: bool
    persisted: bool = False
    terminal: bool = False
    error: str = ""


@dataclass
class Refresher:
    """Renews credentials on a timer and writes the survivors back to the config."""

    settings: Settings
    registry: Registry
    config_path: Any = None
    # Per-account bookkeeping, keyed (provider, account).
    failures: dict[tuple[str, str], int] = field(default_factory=dict)
    last_ok: dict[tuple[str, str], float] = field(default_factory=dict)
    last_error: dict[tuple[str, str], str] = field(default_factory=dict)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    # -- the round --------------------------------------------------------- #
    def run_once(self, provider_id: str | None = None, *, force: bool = False) -> list[RefreshResult]:
        """Renew every account that is due (or, with ``force``, every refreshable one)."""
        results: list[RefreshResult] = []
        for pid, provider in self.registry.providers.items():
            if provider_id is not None and pid != provider_id:
                continue
            for account in list(provider.accounts):
                # A terminal account (needs_login) is out of rotation until a
                # human logs in again; force still skips it on purpose.
                if account.anonymous or account.needs_login or not is_refreshable(account):
                    continue
                if not force and not needs_refresh(account):
                    continue
                results.append(self._refresh_one(provider, account))
        return results

    def _refresh_one(self, provider: Provider, account: Account) -> RefreshResult:
        key = (provider.id, account.id)
        terminal = False
        error = ""
        try:
            ok = provider.renew_account(account, expected_revision=account.auth_revision)
        except RefreshTokenRejected as exc:
            ok, terminal, error = False, True, str(exc)
        except Exception as exc:  # noqa: BLE001 - one station must not kill the round
            ok, error = False, str(exc)
        else:
            if not ok:
                error = "refresh was refused"

        result = RefreshResult(provider=provider.id, account=account.id, ok=ok, terminal=terminal, error=error)
        if ok:
            self.failures.pop(key, None)
            self.last_ok[key] = time.time()
            self.last_error.pop(key, None)
            account.needs_login = False
            result.persisted = self._persist(provider.id, account.id, account.data)
            logger.info("refreshed %s/%s%s", provider.id, account.id, " (written to config)" if result.persisted else "")
            return result

        self.failures[key] = self.failures.get(key, 0) + 1
        self.last_error[key] = error or "refresh failed"
        if terminal:
            account.needs_login = True
            logger.warning("%s/%s: refresh token rejected, account needs a new login", provider.id, account.id)
        else:
            logger.warning("refresh of %s/%s failed (attempt %d): %s", provider.id, account.id, self.failures[key], error)
        return result

    # -- persistence ------------------------------------------------------- #
    def _persist(self, provider_id: str, account_id: str, data: dict[str, Any]) -> bool:
        """Write one account's renewed fields back to the config file."""
        cfg = self.settings.providers.get(provider_id)
        if cfg is None:
            return False
        target = next((raw for raw in cfg.accounts if str(raw.get("id") or "") == account_id), None)
        if target is None:
            # Imported from an OFM data dir: the config file has no such account.
            return False
        changed = False
        for key, value in data.items():
            if key == "id":
                continue
            if target.get(key) != value:
                target[key] = value
                changed = True
        if not changed:
            return False
        save_settings(self.settings, self.config_path)
        return True

    # -- status ------------------------------------------------------------ #
    def status(self) -> list[dict[str, Any]]:
        """One row per configured (non-anonymous) account, renewed or not."""
        rows: list[dict[str, Any]] = []
        for pid, provider in self.registry.providers.items():
            for account in provider.accounts:
                if account.anonymous:
                    continue
                key = (pid, account.id)
                expiry = account_expiry_ms(account)
                rows.append(
                    {
                        "provider": pid,
                        "account": account.id,
                        "refreshable": is_refreshable(account),
                        "needs_login": account.needs_login,
                        "expires_at": expiry,
                        "failures": self.failures.get(key, 0),
                        "last_error": self.last_error.get(key),
                        "last_ok": self.last_ok.get(key),
                    }
                )
        return rows

    # -- the timer --------------------------------------------------------- #
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hoshimi-refresh", daemon=True)
        self._thread.start()
        logger.info("credential renewal started (every %ds)", REFRESH_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        if self._stop.wait(FIRST_SWEEP_DELAY_SECONDS):
            return
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("credential renewal round failed")
            if self._stop.wait(REFRESH_INTERVAL_SECONDS):
                return
