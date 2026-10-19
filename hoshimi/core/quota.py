"""Quota a station reports without being asked.

Every free station answers rate-limit headers on the way past, and reading them
costs nothing: the response that carries the tokens also carries how many
requests are left. The alternative is a balance query — an extra round trip on
a lane that rate-limits exactly that, and an endpoint several stations do not
even expose. This is pi-free's *passive quota monitoring*
(`lib/quota-monitor.ts`), and what it keeps is the useful half: the header
pairs in their order, the clamped percentage, the five-minute stale window, and
four response counters. Only the status-bar renderer is dropped — a gateway
reports through its own API.

Two rules from that module are worth keeping verbatim:

- the snapshot is keyed by **provider id**, because a provider is what a client
  picks and what an operator reasons about (for the aggregator lane that means
  "every station under it shares one reading", which is a coarser truth than
  per-station would be, and the right one anyway);
- **nothing but numbers is stored**. The one string that survives is `source`,
  and it is a header *name*, never a value — the same discipline pi-free keeps
  for its wire logs, and the reason this store can be dumped into a
  diagnostics endpoint at all.

Parsing is strict where pi-free is lenient: `Number.parseFloat("10 requests")`
would read 10, `float()` refuses. The drift counter below is what makes that
the better choice — a dialect the table does not match is counted rather than
misread, which is the whole reason the counter exists.

Where the observation happens is one rule, repeated: **the first line of a
station's `stream_raw` `with ... as response` block**, before the status check.
One turn is one observation, successes and failures alike, and a station added
later does the same in the same place — `grep observe_response` is the audit.
Non-serving calls (model listings, refresh, balance) are deliberately left
alone; a `/models` response says nothing about what a generation is worth.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# pi-free's `HEADER_PAIRS`, in order. The order is the priority: SambaNova's
# scoped pair wins over the generic one it also sends, and the daily pair is
# last because a daily window only means something when nothing else did.
HEADER_PAIRS: tuple[tuple[str, str], ...] = (
    ("x-ratelimit-remaining-requests", "x-ratelimit-limit-requests"),
    ("x-ratelimit-remaining", "x-ratelimit-limit"),
    ("ratelimit-remaining-requests", "ratelimit-limit-requests"),
    ("ratelimit-remaining", "ratelimit-limit"),
    ("x-ratelimit-remaining-requests-day", "x-ratelimit-limit-requests-day"),
    ("x-ratelimit-remaining-day", "x-ratelimit-limit-day"),
)

STALE_AFTER_SECONDS = 300.0
"""pi-free hides a reading older than five minutes rather than show a lie."""

_REMAINING_HINT = re.compile(r"remaining")
_LIMIT_HINT = re.compile(r"(^|-)limit")


@dataclass(frozen=True)
class QuotaReading:
    """One station's answer about how much is left, and how it was asked."""

    remaining: float
    limit: float
    percent: int
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {"remaining": self.remaining, "limit": self.limit, "percent": self.percent, "source": self.source}


def extract_quota(headers: Mapping[str, Any]) -> QuotaReading | None:
    """The first pair that parses, in upstream's priority order.

    Both halves have to be present and parseable, and the limit has to be above
    zero: a limit of zero is a station saying "none", not a fraction to divide
    by.
    """
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    for remaining_key, limit_key in HEADER_PAIRS:
        remaining_raw = normalized.get(remaining_key)
        limit_raw = normalized.get(limit_key)
        if remaining_raw is None or limit_raw is None:
            continue
        try:
            remaining = float(remaining_raw)
            limit = float(limit_raw)
        except ValueError:
            continue
        if limit <= 0:
            continue
        percent = round((remaining / limit) * 100)
        return QuotaReading(remaining, limit, max(0, min(100, percent)), remaining_key)
    return None


def looks_like_drift(keys: list[str]) -> bool:
    """Both halves of a quota pair are present but no known format matched.

    Only then. A remaining-only or a limit-only header is a legitimate
    half-signal — a station that simply does not publish one of them — and
    counting those as drift would bury the one that matters: a station that
    changed its dialect, which is exactly what this gateway cannot read any
    more and would otherwise never notice.
    """
    return any(_REMAINING_HINT.search(key) for key in keys) and any(_LIMIT_HINT.search(key) for key in keys)


@dataclass
class ResponseCounters:
    """What the station's answers say about the *connection*, counted.

    pi-free's four names and thresholds, kept because the counts are the only
    thing that turns "the lane feels slow" into "23 rate limits and one format
    drift in the last hour".
    """

    responses: int = 0
    auth_failures: int = 0
    rate_limited: int = 0
    server_errors: int = 0
    quota_header_drift: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "responses": self.responses,
            "auth_failures": self.auth_failures,
            "rate_limited": self.rate_limited,
            "server_errors": self.server_errors,
            "quota_header_drift": self.quota_header_drift,
        }


@dataclass
class _Entry:
    reading: QuotaReading | None
    counters: ResponseCounters
    updated_at: float


class QuotaStore:
    """Per-provider quota, built from responses the gateway was sending anyway.

    Thread-safe: the pool answers from more than one thread and an observation
    lands wherever the turn happened to run.
    """

    def __init__(self, *, stale_after: float = STALE_AFTER_SECONDS) -> None:
        self.stale_after = float(stale_after)
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def observe(self, provider: str, status: int, headers: Mapping[str, Any], *, now: float | None = None) -> None:
        """Fold one response into the record for its provider."""
        key = str(provider or "").strip()
        if not key:
            return
        moment = time.time() if now is None else now
        keys = [str(name).lower() for name in headers]

        with self._lock:
            entry = self._entries.get(key)
            counters = entry.counters if entry is not None else ResponseCounters()
            reading = entry.reading if entry is not None else None

            counters.responses += 1
            if status in (401, 403):
                counters.auth_failures += 1
            elif status == 429:
                counters.rate_limited += 1
            elif 500 <= status < 600:
                counters.server_errors += 1

            found = extract_quota(headers)
            if found is not None:
                reading = found
            elif looks_like_drift(keys):
                counters.quota_header_drift += 1
                logger.debug("quota headers present but none matched: %s", sorted(keys))

            self._entries[key] = _Entry(reading=reading, counters=counters, updated_at=moment)

    def is_stale(self, provider: str, *, now: float | None = None) -> bool:
        """Has the reading outlived the five minutes it is worth?"""
        moment = time.time() if now is None else now
        with self._lock:
            entry = self._entries.get(str(provider))
            reading = entry.reading if entry else None
            updated_at = entry.updated_at if entry else 0.0
        if reading is None:
            return True
        return moment - updated_at > self.stale_after

    def snapshot(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Every provider observed so far, with its reading and its counters."""
        moment = time.time() if now is None else now
        with self._lock:
            items = [(key, entry.reading, dict(entry.counters.as_dict()), entry.updated_at) for key, entry in self._entries.items()]
        out = []
        for provider, reading, counters, updated_at in items:
            # A provider that answered and said nothing about quota has no row
            # worth showing: `responses` on its own is just the call count this
            # gateway already keeps in stats. What stays is a reading, or a
            # counter that says something went wrong — including a dialect the
            # table can no longer read, which is the one a human wants to see.
            if reading is None and not any(counters[name] for name in ("auth_failures", "rate_limited", "server_errors", "quota_header_drift")):
                continue
            out.append(
                {
                    "provider": provider,
                    "remaining": reading.remaining if reading else None,
                    "limit": reading.limit if reading else None,
                    "percent": reading.percent if reading else None,
                    "source": reading.source if reading else "",
                    "updated_at": updated_at,
                    "stale": reading is None or moment - updated_at > self.stale_after,
                    "counters": counters,
                }
            )
        return sorted(out, key=lambda row: row["provider"])

    def reset(self) -> int:
        with self._lock:
            dropped = len(self._entries)
            self._entries.clear()
        return dropped

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


QUOTA = QuotaStore()
"""The process-wide record the API reads. Deliberately not persisted: a quota
figure older than a restart belongs to a station that has since re-armed it."""


def observe_response(provider: str, response: httpx.Response) -> None:
    """Wire-facing entry point: record the answer, and never fail on it.

    This runs on the path that carries a user's turn. A header store is not
    worth a failed generation, so anything at all goes to debug and back — the
    same "observability is best-effort and never throws" rule pi-free keeps for
    its status helpers.
    """
    try:
        QUOTA.observe(provider, response.status_code, response.headers)
    except Exception:
        logger.debug("quota observation failed for %s", provider, exc_info=True)
