"""Daily check-in and balance: what each station owes the account, and taking it.

Two questions live here, deliberately not mixed:

- *what does this account still have?* — a read-only balance/quota query per
  station; a lane without such an endpoint says so rather than guessing;
- *take today's free credits* — the daily check-in. Most stations answer
  "already claimed" on a second try, but the gateway still records the date
  only when something actually moved (`covers_today`), because a station that
  refreshes its campaign on a delay (Qoder, 10:00 UTC+8) would otherwise mark
  a day as done while yesterday's leftover still shows.

The scheduler is a single serial thread: stations rate-limit aggressively when
asked in parallel, so one account after another, with a short startup delay,
is the whole anti-risk-control strategy. State lands in ``checkin.json`` next
to the probe and stats stores.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hoshimi.core.providers.base import (
    CLAIM_CLAIMED,
    CLAIM_UNSUPPORTED,
    Account,
    BalanceOutcome,
    ClaimOutcome,
    Provider,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hoshimi.core.config import Settings
    from hoshimi.core.registry import Registry

logger = logging.getLogger(__name__)

CHECKIN_FILE_NAME = "checkin.json"
"""Where the "we already ran today" marker survives a restart."""

CHECKIN_INTERVAL_SECONDS = 24 * 60 * 60
"""One sweep a day is enough; the window is a calendar day, not a rolling 24h."""

FIRST_CHECKIN_DELAY_SECONDS = 30
"""A fresh boot should not stampede every station at once."""

UTC8 = timezone(timedelta(hours=8))
"""The stations agree on Beijing wall-clock, not the host's timezone."""


def utc8_today(now: float | None = None) -> str:
    """Today's date in UTC+8, the day every station's check-in is scoped to."""
    moment = datetime.fromtimestamp(now if now is not None else time.time(), tz=UTC8)
    return moment.strftime("%Y-%m-%d")


# -- the state file ------------------------------------------------------- #
@dataclass
class CheckinDoc:
    """The one fact the scheduler needs across restarts: did today already run."""

    last_date: str = ""
    last_result: str = ""
    last_at: float = 0.0
    channels: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Any) -> CheckinDoc:
        if not isinstance(raw, dict):
            return cls()
        channels = raw.get("channels")
        return cls(
            last_date=str(raw.get("last_date") or ""),
            last_result=str(raw.get("last_result") or ""),
            last_at=float(raw.get("last_at") or 0.0),
            channels=[{"provider": str(c.get("provider") or ""), "text": str(c.get("text") or "")} for c in channels if isinstance(c, dict)] if isinstance(channels, list) else [],
        )

    def as_dict(self) -> dict[str, Any]:
        return {"last_date": self.last_date, "last_result": self.last_result, "last_at": self.last_at, "channels": self.channels}


class CheckinStore:
    """Loads once, saves atomically; a corrupt file is simply a fresh start."""

    def __init__(self, path: Path | None = None):
        self._lock = threading.Lock()
        self._path = path
        self.doc = CheckinDoc()
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            self.doc = CheckinDoc.from_dict(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            self.doc = CheckinDoc()

    def save(self) -> None:
        if self._path is None:
            return
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.doc.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self._path)


# -- the service ---------------------------------------------------------- #
@dataclass
class CreditsService:
    """Claims and balances one station after another, and remembers the day."""

    settings: Settings
    registry: Registry
    store: CheckinStore
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    # -- read-only -------------------------------------------------------- #
    def balance(self, provider_id: str | None = None) -> list[BalanceOutcome]:
        """One row per (provider, account) that can answer; unsupported lanes are skipped."""
        out: list[BalanceOutcome] = []
        for pid, provider in self._ordered(provider_id):
            for account in self._real_accounts(provider):
                try:
                    outcome = provider.balance_of(account)
                except Exception as exc:  # noqa: BLE001 - one station must not kill the sweep
                    logger.warning("balance of %s/%s failed: %s", pid, account.id, exc)
                    outcome = BalanceOutcome(pid, account.id, None, message=str(exc))
                if outcome is not None:
                    out.append(outcome)
        return out

    # -- the claim round --------------------------------------------------- #
    def claim(self, provider_id: str | None = None) -> list[ClaimOutcome]:
        """Renew every account's daily credits, one station after another.

        Serial on purpose: the stations throttle a parallel burst far harder
        than they throttle a slow, honest sweep.
        """
        out: list[ClaimOutcome] = []
        for pid, provider in self._ordered(provider_id):
            for account in self._real_accounts(provider):
                try:
                    outcome = provider.claim_daily(account)
                except Exception as exc:  # noqa: BLE001 - one station must not kill the sweep
                    logger.warning("claim on %s/%s failed: %s", pid, account.id, exc)
                    out.append(ClaimOutcome(pid, account.id, "failed", covers_today=False, message=str(exc)))
                    continue
                if outcome is None:
                    continue
                out.append(outcome)
        return out

    # -- the daily timer --------------------------------------------------- #
    def run_daily(self, *, force: bool = False) -> list[ClaimOutcome]:
        """Claim only when today (UTC+8) has not been covered yet."""
        today = utc8_today()
        if not force and self.store.doc.last_date == today:
            return []
        outcomes = self.claim()
        # `covers_today` (not claimed+already) is the gate: a station mid-refresh
        # still answers "already" about yesterday and must not burn the day.
        covers = [o for o in outcomes if o.covers_today and o.status in (CLAIM_CLAIMED, "already")]
        self.store.doc.last_date = today if covers else self.store.doc.last_date
        self.store.doc.last_result = _summarize(outcomes)
        self.store.doc.last_at = time.time()
        self.store.doc.channels = [{"provider": o.provider, "text": o.message or o.status} for o in outcomes[:24]]
        self.store.save()
        return outcomes

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hoshimi-checkin", daemon=True)
        self._thread.start()
        logger.info("daily check-in started (every %ds)", CHECKIN_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        if self._stop.wait(FIRST_CHECKIN_DELAY_SECONDS):
            return
        while not self._stop.is_set():
            try:
                self.run_daily()
            except Exception:
                logger.exception("daily check-in round failed")
            if self._stop.wait(CHECKIN_INTERVAL_SECONDS):
                return

    # -- helpers ----------------------------------------------------------- #
    def _ordered(self, provider_id: str | None) -> list[tuple[str, Provider]]:
        known = [pid for pid in self.registry.providers if provider_id is None or pid == provider_id]
        return [(pid, self.registry.providers[pid]) for pid in known]

    @staticmethod
    def _real_accounts(provider: Provider) -> list[Account]:
        return [account for account in provider.accounts if not account.anonymous]


def _summarize(outcomes: list[ClaimOutcome]) -> str:
    if not outcomes:
        return "no lanes to claim"
    claimed = sum(1 for o in outcomes if o.status == CLAIM_CLAIMED)
    already = sum(1 for o in outcomes if o.status == "already")
    failed = sum(1 for o in outcomes if o.status == "failed")
    unsupported = sum(1 for o in outcomes if o.status == CLAIM_UNSUPPORTED)
    parts = []
    if claimed:
        parts.append(f"{claimed} claimed")
    if already:
        parts.append(f"{already} already")
    if failed:
        parts.append(f"{failed} failed")
    if unsupported:
        parts.append(f"{unsupported} unsupported")
    return ", ".join(parts) or "nothing to do"
