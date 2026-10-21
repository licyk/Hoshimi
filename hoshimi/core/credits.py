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
is the whole anti-risk-control strategy. The day marker lives in the
database's ``meta`` table and every outcome in its ``checkins`` table (see
:mod:`hoshimi.core.db`), kept :data:`KEEP_HISTORY_DAYS` days, so an operator
can see which station stopped paying out and since when.
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

from hoshimi.core.db import Database, open_store_database, retire_legacy_file
from hoshimi.core.providers.base import (
    CLAIM_ALREADY,
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
"""Where the state lived before ``hoshimi.db``; imported once, then renamed ``checkin.json.migrated``."""

CHECKIN_STATE_KEY = "checkin.state"
"""The ``meta`` key holding :class:`CheckinDoc` as JSON."""

KEEP_HISTORY_DAYS = 365
"""How long one check-in outcome stays in the history."""

MESSAGE_LIMIT = 300

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
    """The day marker (``meta``) and the history of outcomes (``checkins``).

    ``db`` is the gateway's shared database; without one, ``path`` (the old
    ``checkin.json`` location) opens ``hoshimi.db`` beside it, and nothing at
    all keeps everything in memory. A ``checkin.json`` at ``path`` is imported
    once, then renamed aside; an unreadable one is simply a fresh start.
    """

    def __init__(self, path: Path | None = None, *, db: Database | None = None):
        self._lock = threading.Lock()
        self._db, self._owns_db = open_store_database(path, db)
        self.doc = CheckinDoc()
        if path is not None:
            self._import_legacy(path)
        self.doc = CheckinDoc.from_dict(self._db.get_json(CHECKIN_STATE_KEY))

    def save(self) -> None:
        with self._lock:
            if not self._db.closed:
                self._db.set_json(CHECKIN_STATE_KEY, self.doc.as_dict())

    def record(self, outcomes: list[ClaimOutcome], *, at: float | None = None) -> None:
        """Append one row per outcome to the history, and drop what is past :data:`KEEP_HISTORY_DAYS`.

        ``unsupported`` is not an outcome worth remembering: it is a lane
        without a check-in endpoint saying so, every day, forever.
        """
        moment = time.time() if at is None else at
        rows = [
            (moment, utc8_today(moment), o.provider, o.account, o.status, 1 if o.status in (CLAIM_CLAIMED, CLAIM_ALREADY) else 0, int(o.credits or 0), _message(o))
            for o in outcomes
            if o.status != CLAIM_UNSUPPORTED
        ]
        with self._lock:
            if self._db.closed:
                return
            with self._db.transaction() as conn:
                if rows:
                    conn.executemany("INSERT INTO checkins (at, date, provider, account, status, ok, credits, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
                conn.execute("DELETE FROM checkins WHERE at < ?", (moment - KEEP_HISTORY_DAYS * 86400,))

    def history(self, provider: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Recorded outcomes, newest first."""
        sql = "SELECT * FROM checkins" + (" WHERE provider = ?" if provider is not None else "") + " ORDER BY at DESC, id DESC LIMIT ?"
        params: tuple[Any, ...] = (provider, limit) if provider is not None else (limit,)
        return [_history_row(row) for row in self._db.fetchall(sql, params)]

    def recent_by_provider(self, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
        """The latest ``limit`` outcomes of every provider, newest first, in one query."""
        rows = self._db.fetchall(
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY provider ORDER BY at DESC, id DESC) AS n FROM checkins) WHERE n <= ? ORDER BY provider, at DESC, id DESC",
            (limit,),
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            out.setdefault(str(row["provider"]), []).append(_history_row(row))
        return out

    def close(self) -> None:
        if self._owns_db:
            self._db.close()

    def _import_legacy(self, path: Path) -> None:
        if not path.is_file() or self._db.get_meta(CHECKIN_STATE_KEY) is not None:
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("could not read %s: %s", path, exc)
            return
        except ValueError as exc:
            retire_legacy_file(path, "corrupt")
            logger.warning("%s is unreadable (%s); the check-in state starts over", path, exc)
            return
        self._db.set_json(CHECKIN_STATE_KEY, CheckinDoc.from_dict(raw).as_dict())
        retire_legacy_file(path, "migrated")
        logger.info("Imported %s into %s", path.name, self._db.path)


def _message(outcome: ClaimOutcome) -> str:
    text = " ".join(str(outcome.message or outcome.status).split())
    return text if len(text) <= MESSAGE_LIMIT else text[: MESSAGE_LIMIT - 1] + "…"


def _history_row(row: Any) -> dict[str, Any]:
    return {
        "at": float(row["at"]),
        "date": str(row["date"]),
        "provider": str(row["provider"]),
        "account": str(row["account"]),
        "status": str(row["status"]),
        "ok": bool(row["ok"]),
        "credits": int(row["credits"]),
        "message": str(row["message"]),
    }


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
        try:
            self.store.record(out)
        except Exception:  # history is a record of the claim, never a reason it failed
            logger.exception("could not record the check-in outcomes")
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
