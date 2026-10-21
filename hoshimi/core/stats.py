"""Usage accounting: what this gateway actually served, by hour, day and model.

Two numbers on this lane are easy to get wrong, so they are measured rather
than assumed:

**Decode speed.** A window shorter than :data:`MIN_DECODE_MS` is noise with a
unit on it (a real sample published 63 000 tok/s: 63 tokens inside a 1 ms
window), and a rate above :data:`MAX_CREDIBLE_TPS` is a clock, not a model. A
call tripping either is not a slow measurement — it is *no* measurement, and it
is dropped instead of averaged in.

**What the window contained.** Reasoning that streamed past counts as decode
time; reasoning the station spent before its first frame does not, so it is
subtracted from the token count rather than credited to the answer's speed.

The store keeps three shapes of the same calls in ``hoshimi.db`` (see
:mod:`hoshimi.core.db`), each for a different question:

- **buckets** (``usage_hourly`` keyed by the epoch start of a *local* hour,
  kept :data:`KEEP_HOURLY_DAYS`; ``usage_daily`` keyed by the local date, kept
  forever) answer "how much, over this range". A bucket row is keyed by the
  full identity of a call — ``model``, ``provider``, ``account``, ``origin`` —
  so any of those groupings can be recovered for any range by summing, without
  storing four copies of every bucket. Each bucket also has fixed log-scale
  histograms of TTFT and tokens/s (``usage_hist_*``), which merge by addition,
  so a p50 or p95 over a range is computed from the range rather than averaged
  out of per-bucket percentiles (which is not a percentile of anything). The
  daily table is not redundant with the hourly one: days imported from an old
  ``stats.json`` have no hourly split, and a 90-day report reads 90 rows per
  identity instead of 2160.
- **lifetime** rollups by model, provider, account and origin are never
  pruned, so "ever served" never shrinks.
- **calls**, one row per call for ``stats_keep_days`` days, each with a
  ``seq`` that never repeats (``AUTOINCREMENT`` keeps counting through a
  reset): the rows a "that answer vanished" report audits, and the cursor a
  client pages them with.

Writes are batched: a busy gateway records many calls a second, and a
transaction per call is the one cost here that grows with traffic. A call is
added to small in-memory caches at once — today's totals and the latest calls,
which is everything a live event needs — and to a pending batch that is
written in one transaction at most every :data:`WRITE_INTERVAL_SECONDS`.
Every read writes the pending batch first, so nothing recorded is ever
missing from a report; :meth:`StatsStore.flush` (called on shutdown) writes
whatever is left.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import threading
import time
import uuid
import weakref
from collections import deque
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshimi.core.db import Database, open_store_database, retire_legacy_file
from hoshimi.core.errors import HoshimiError, ValidationError
from hoshimi.core.live import LiveHub, Subscription

logger = logging.getLogger(__name__)

STATS_FILE_NAME = "stats.json"
"""Where the counters lived before ``hoshimi.db``; imported once, then renamed ``stats.json.migrated``."""

STATS_VERSION = 2
"""The newest ``stats.json`` layout :func:`migrate` reads."""

MIN_DECODE_MS = 250
MAX_CREDIBLE_TPS = 250
KEEP_HOURLY_DAYS = 120
"""Hourly buckets older than this are pruned; daily buckets and lifetime rollups are kept forever."""
DEFAULT_KEEP_CALL_DAYS = 90
"""How long a recorded call is kept by default (``server.stats_keep_days``)."""
RECENT_LIMIT = 100
"""Latest calls held in memory for live snapshots, so a watcher connecting costs no query."""
WRITE_INTERVAL_SECONDS = 1.0
PRUNE_INTERVAL_SECONDS = 60.0
"""Retention is enforced on a write, at most this often: a delete per batch would be waste."""
PROGRESS_INTERVAL_SECONDS = 0.25
"""At most four progress events a second per call; a phase change is never held back."""

RANGES: dict[str, tuple[str, int]] = {"24h": ("hour", 24), "7d": ("day", 7), "30d": ("day", 30), "90d": ("day", 90)}
"""A rolling report range: the bucket granularity it reads, and how many buckets. ``day`` (one calendar day) is separate."""

# Fixed log-scale histogram edges. Bucket ``i`` holds values in
# ``[base * ratio**i, base * ratio**(i + 1))``; anything below the first edge
# lands in bucket 0 and anything past the last in the last. Fixed so that rows
# written months apart still merge by plain addition.
TTFT_HIST = (10.0, 1.15, 80)  # 10 ms .. ~12 min
TPS_HIST = (1.0, 1.08, 72)  # 1 .. ~250 tok/s

KEY_SEPARATOR = "\t"
"""Joined a bucket row's identity in ``stats.json``; kept to read those files."""

TOTAL_FIELDS = ("input", "output", "reasoning", "cache_read", "cache_write", "calls", "failed", "no_usage")
RATE_FIELDS = ("ttft_ms", "ttft_count", "decode_ms", "decode_tokens")
BUCKET_FIELDS = TOTAL_FIELDS + RATE_FIELDS
IDENTITY_FIELDS = ("model", "provider", "account", "origin")
CALL_FIELDS = (
    "seq",
    "id",
    "ts",
    "at",
    "model",
    "provider",
    "account",
    "origin",
    "effort",
    "stream",
    "ok",
    "input",
    "output",
    "reasoning",
    "cache_read",
    "cache_write",
    "ttft_ms",
    "decode_ms",
    "decode_tokens",
    "tps",
    "elapsed_ms",
    "attempts",
    "finish_reason",
    "error_code",
    "error_message",
    "no_usage",
)
CALL_SEARCH_FIELDS = ("model", "provider", "account", "error_code", "error_message", "finish_reason")
"""Columns the free-text ``q`` of :meth:`StatsStore.calls` looks in."""
LIFETIME_GROUPS = ("models", "providers", "accounts", "origins")
TOTAL_GROUP = "total"
"""The lifetime row every call adds to, whatever its identity: ``requests`` and ``failures``."""
ERROR_MESSAGE_LIMIT = 160

# Chunks that prove tokens are streaming right now.
_CONTENT_KINDS = frozenset({"text_delta", "reasoning_delta", "tool_call_start", "tool_call_delta"})
_REASONING_KINDS = frozenset({"reasoning_delta"})
_TOOL_KINDS = frozenset({"tool_call_start", "tool_call_delta"})
_PHASES = {"text_delta": "answering", "reasoning_delta": "reasoning", "tool_call_start": "tool", "tool_call_delta": "tool"}

# Credentials look like this in an upstream's prose; none of it belongs in a stored row.
_SECRETS = (
    re.compile(r"(?i)\b(bearer|token|key|authorization)\b([\s:=\"']+)[^\s\"',;]{8,}"),
    re.compile(r"\b(?:sk|ak|pk|rk)-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"[A-Za-z0-9_\-]{32,}"),
)

Identity = tuple[str, str, str, str]
"""``(model, provider, account, origin)``: what a bucket row is keyed by."""


def day_key(at: float | None = None) -> str:
    """The local calendar day a call belongs to on the dashboard."""
    return _local(at).date().isoformat()


def hour_key(at: float | None = None) -> str:
    """The local clock hour a call belongs to: ``YYYY-MM-DDTHH``."""
    return _local(at).strftime("%Y-%m-%dT%H")


def iso(at: float) -> str:
    return datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds")


def _local(at: float | None) -> datetime:
    return datetime.fromtimestamp(time.time() if at is None else at).astimezone()


def decode_window(decode_ms: int | None, tokens: int, ok: bool) -> tuple[bool, int, int | None]:
    """Classify one completed call's decode window: (measurable, ms, tokens/s)."""
    ms = decode_ms if isinstance(decode_ms, int) and decode_ms > 0 else 0
    if not ok or tokens <= 0 or ms < MIN_DECODE_MS:
        return False, 0, None
    rate = tokens / (ms / 1000)
    if rate > MAX_CREDIBLE_TPS:
        return False, 0, None
    return True, ms, round(rate)


def window_tokens(output: int, reasoning: int, saw_reasoning: bool) -> int:
    """Tokens attributable to the window between the first frame and the finish."""
    if saw_reasoning:
        return output
    return max(0, output - reasoning)


def row_key(model: str, provider: str = "", account: str = "", origin: str = "") -> str:
    return KEY_SEPARATOR.join((model, provider, account, origin))


def split_key(key: str) -> tuple[str, str, str, str]:
    parts = key.split(KEY_SEPARATOR)
    parts += [""] * (4 - len(parts))
    return parts[0], parts[1], parts[2], parts[3]


# -- histograms ------------------------------------------------------------ #
def hist_index(value: float, spec: tuple[float, float, int]) -> int:
    base, ratio, size = spec
    if value <= base:
        return 0
    return min(size - 1, int(math.log(value / base) / math.log(ratio)))


def hist_add(hist: dict[str, int], value: float, spec: tuple[float, float, int]) -> None:
    key = str(hist_index(value, spec))
    hist[key] = hist.get(key, 0) + 1


def hist_merge(into: dict[str, int], other: Any) -> None:
    if not isinstance(other, dict):
        return
    for key, count in other.items():
        if isinstance(count, int) and count > 0:
            into[key] = into.get(key, 0) + count


def percentile(hist: dict[str, int], q: float, spec: tuple[float, float, int]) -> int | None:
    """The ``q`` quantile of a histogram, interpolated geometrically inside its bucket.

    Accurate to the bucket's width (15% for TTFT, 8% for speed), which is
    finer than the jitter of either measurement.
    """
    counts = sorted((int(key), count) for key, count in hist.items() if count > 0)
    total = sum(count for _index, count in counts)
    if not total:
        return None
    base, ratio, _size = spec
    rank = q * total
    seen = 0
    for index, count in counts:
        if seen + count >= rank:
            fraction = (rank - seen) / count
            return round(base * ratio ** (index + fraction))
        seen += count
    index = counts[-1][0]
    return round(base * ratio ** (index + 1))


# -- rows ------------------------------------------------------------------ #
def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0.0


def zero_totals() -> dict[str, Any]:
    return {**dict.fromkeys(TOTAL_FIELDS, 0), "last_at": 0.0}


def zero_bucket_row() -> dict[str, Any]:
    return {**zero_totals(), **dict.fromkeys(RATE_FIELDS, 0), "ttft_hist": {}, "tps_hist": {}}


def merge_row(into: dict[str, Any], row: dict[str, Any]) -> None:
    """Add one stored row into an accumulator; missing fields count as zero."""
    for name in TOTAL_FIELDS:
        into[name] = into.get(name, 0) + _int(row.get(name))
    into["last_at"] = max(_num(into.get("last_at")), _num(row.get("last_at")))
    if "ttft_hist" in into:
        for name in RATE_FIELDS:
            into[name] = into.get(name, 0) + _int(row.get(name))
        hist_merge(into["ttft_hist"], row.get("ttft_hist"))
        hist_merge(into["tps_hist"], row.get("tps_hist"))


def totals_view(row: dict[str, Any]) -> dict[str, int]:
    return {name: _int(row.get(name)) for name in TOTAL_FIELDS}


def rates_view(row: dict[str, Any]) -> dict[str, int | None]:
    """Average/percentile latency and speed of an accumulated bucket row."""
    count = _int(row.get("ttft_count"))
    measurable, _ms, rate = decode_window(_int(row.get("decode_ms")), _int(row.get("decode_tokens")), True)
    ttft_hist = row.get("ttft_hist") or {}
    tps_hist = row.get("tps_hist") or {}
    return {
        "ttft_avg_ms": round(_int(row.get("ttft_ms")) / count) if count else None,
        "ttft_p50_ms": percentile(ttft_hist, 0.5, TTFT_HIST),
        "ttft_p95_ms": percentile(ttft_hist, 0.95, TTFT_HIST),
        "tps": rate if measurable else None,
        "tps_p50": percentile(tps_hist, 0.5, TPS_HIST),
    }


def short_error(text: Any) -> str:
    """An error as a stored row may carry it: one line, short, with credentials masked.

    An upstream's error prose is the station's body, and bodies have been seen
    to echo the key that was refused. The ring is readable by anyone with the
    management key, so it keeps the gist and drops anything shaped like a
    credential.
    """
    if not isinstance(text, str):
        return ""
    line = " ".join(text.split())
    line = _SECRETS[0].sub(lambda match: f"{match.group(1)}{match.group(2)}***", line)
    for pattern in _SECRETS[1:]:
        line = pattern.sub("***", line)
    return line if len(line) <= ERROR_MESSAGE_LIMIT else line[: ERROR_MESSAGE_LIMIT - 1] + "…"


def describe_error(exc: BaseException) -> tuple[str, str]:
    """(code, message) for an exception that ended a call; never the upstream body.

    A provider error's message *is* the station's body, so a status-carrying
    error is described by its status alone.
    """
    status = getattr(exc, "status", None)
    if isinstance(exc, HoshimiError) and isinstance(status, int):
        return exc.code, f"upstream returned HTTP {status}"
    if isinstance(exc, HoshimiError):
        return exc.code, short_error(exc.message)
    return "internal_error", type(exc).__name__


def describe_error_chunk(error: Any) -> tuple[str, str]:
    """(code, message) for an ``error`` chunk, which carries a string or an OpenAI-style object."""
    if isinstance(error, dict):
        code = error.get("code") or error.get("type") or "upstream_error"
        return short_error(str(code)) or "upstream_error", short_error(str(error.get("message") or ""))
    return "upstream_error", short_error(error if isinstance(error, str) else "")


class Turn:
    """One request's contribution to the stats, recorded exactly once.

    Besides the final row, a turn is a live call: it registers with the
    store's :class:`~hoshimi.core.live.LiveHub` when created, reports its phase
    and how much it has streamed while it runs (throttled to
    :data:`PROGRESS_INTERVAL_SECONDS`, except for a phase change), and leaves
    with the row it stored.
    """

    def __init__(self, store: StatsStore, model: str, *, effort: str = "", origin: str = "chat", stream: bool = False, provider: str = ""):
        self._store = store
        self.id = uuid.uuid4().hex[:16]
        self._model = model
        self._effort = effort
        self._origin = origin
        self._stream = stream
        self._provider = provider
        self._account = ""
        self._attempts = 0
        self._started_at = time.time()
        self._started = time.monotonic()
        self._first: float | None = None
        self._ended: float | None = None
        self._usage: dict[str, Any] | None = None
        self._saw_reasoning = False
        self._ok = True
        self._done = False
        self._phase = "waiting"
        self._text_chars = 0
        self._reasoning_chars = 0
        self._last_progress = 0.0
        self._finish_reason = ""
        self._error_code = ""
        self._error_message = ""
        store.live.begin(self.id, self.live_view)

    # -- routing ---------------------------------------------------------- #
    def routed(self, provider: str, account: str, attempt: int) -> None:
        """The account that actually answered (``on_route`` of the registry)."""
        self._provider = provider
        self._account = account
        self._attempts = attempt
        self._publish(force=True)

    # -- the stream ------------------------------------------------------- #
    def wrap(self, chunks: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """Pass every chunk through, watching the stream for timing and usage.

        A client that hangs up mid-answer is a call the gateway *did* serve, so
        it lands as a success; `failed` is reserved for the gateway or the
        upstream failing the call.

        A stream that is dropped without ever being started (a response whose
        headers never went out) is still a call that must leave the live list,
        so the generator carries a finalizer for that case. It records from a
        short-lived thread because a collector may run it anywhere — including
        inside this module's own locks.
        """
        generator = self._wrap(chunks)
        # Not at interpreter exit: a process going down records nothing more.
        weakref.finalize(generator, self._abandoned).atexit = False
        return generator

    def _abandoned(self) -> None:
        if not self._done:
            threading.Thread(target=self.finish, name="hoshimi-stats-abandoned", daemon=True).start()

    def _wrap(self, chunks: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        try:
            for chunk in chunks:
                if isinstance(chunk, dict):
                    self._observe(chunk)
                yield chunk
        except GeneratorExit:
            self.finish()
            raise
        except BaseException as exc:
            self.fail(exc)
            raise
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
        self.finish()

    def fail(self, exc: BaseException | None = None) -> None:
        """Record a request that never produced a stream (routing errors too)."""
        self._ok = False
        if exc is not None and not self._error_code:
            self._error_code, self._error_message = describe_error(exc)
        self.finish()

    def _observe(self, chunk: dict[str, Any]) -> None:
        kind = chunk.get("type")
        if kind in _CONTENT_KINDS:
            now = time.monotonic()
            if self._first is None:
                self._first = now
            if kind in _REASONING_KINDS:
                self._saw_reasoning = True
                self._reasoning_chars += _text_length(chunk.get("text"))
            elif kind in _TOOL_KINDS:
                self._text_chars += _text_length(chunk.get("arguments")) + _text_length(chunk.get("name"))
            else:
                self._text_chars += _text_length(chunk.get("text"))
            phase = _PHASES[kind]
            if phase != self._phase:
                self._phase = phase
                self._publish(force=True)
            else:
                self._publish()
        elif kind == "usage":
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                self._usage = usage
        elif kind == "finish":
            self._ended = time.monotonic()
            reason = chunk.get("reason")
            if isinstance(reason, str):
                self._finish_reason = reason
        elif kind == "error" or chunk.get("error") is not None:
            self._ok = False
            if not self._error_code:
                self._error_code, self._error_message = describe_error_chunk(chunk.get("error"))

    # -- live ------------------------------------------------------------- #
    def _publish(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_progress < PROGRESS_INTERVAL_SECONDS:
            return
        self._last_progress = now
        self._store.live.progress(self.id)

    def live_view(self) -> dict[str, Any]:
        """This call as a watcher sees it right now (``LiveCall`` without its display name)."""
        now = time.monotonic()
        estimate = (self._text_chars + self._reasoning_chars + 3) // 4
        tps = None
        if self._first is not None:
            decode_ms = int((now - self._first) * 1000)
            measurable, _ms, rate = decode_window(decode_ms, estimate, True)
            tps = rate if measurable else None
        return {
            "id": self.id,
            "model": self._model,
            "provider": self._provider,
            "account": self._account,
            "origin": self._origin,
            "effort": self._effort,
            "stream": self._stream,
            "started_at": self._started_at,
            "phase": self._phase,
            "ttft_ms": int((self._first - self._started) * 1000) if self._first is not None else None,
            "elapsed_ms": int((now - self._started) * 1000),
            "output_chars": self._text_chars,
            "reasoning_chars": self._reasoning_chars,
            "est_output_tokens": estimate,
            "tps": tps,
            "attempts": self._attempts,
        }

    # -- the row ---------------------------------------------------------- #
    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        ended = self._ended if self._ended is not None else time.monotonic()
        usage = self._usage if isinstance(self._usage, dict) else {}
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, dict) else {}
        prompt_details = usage.get("prompt_tokens_details")
        prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
        output = _int(usage.get("completion_tokens"))
        reasoning = _int(details.get("reasoning_tokens"))
        cache_write = _int(prompt_details.get("cache_write_tokens")) or _int(prompt_details.get("cache_creation_tokens"))
        ttft_ms = int((self._first - self._started) * 1000) if self._first is not None else None
        decode_ms = int((ended - self._first) * 1000) if self._first is not None else None
        self._store.record(
            model=self._model,
            ok=self._ok,
            input=_int(usage.get("prompt_tokens")),
            output=output,
            reasoning=reasoning,
            cache_read=_int(prompt_details.get("cached_tokens")),
            cache_write=cache_write,
            decode_tokens=window_tokens(output, reasoning, self._saw_reasoning) if self._ok else 0,
            ttft_ms=ttft_ms,
            decode_ms=decode_ms,
            elapsed_ms=int((time.monotonic() - self._started) * 1000),
            effort=self._effort,
            origin=self._origin,
            no_usage=not usage,
            provider=self._provider,
            account=self._account,
            stream=self._stream,
            attempts=self._attempts,
            finish_reason=self._finish_reason,
            error_code="" if self._ok else (self._error_code or "upstream_error"),
            error_message="" if self._ok else self._error_message,
            call_id=self.id,
        )


def _text_length(value: Any) -> int:
    return len(value) if isinstance(value, str) else 0


class StatsStore:
    """Hour, day and lifetime rollups plus every recorded call, in ``hoshimi.db``.

    ``db`` is the gateway's shared database. Without one, a ``path`` (the old
    ``stats.json`` location) opens ``hoshimi.db`` beside it, and nothing at
    all keeps the rows in memory — for tests and embedders, as a store
    without a path always did. A ``stats.json`` at ``path`` is imported once
    into a database that has no usage yet, then renamed aside.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        db: Database | None = None,
        write_interval: float = WRITE_INTERVAL_SECONDS,
        keep_days: int = DEFAULT_KEEP_CALL_DAYS,
    ):
        self._lock = threading.Lock()
        self._db, self._owns_db = open_store_database(path, db)
        self._timer: threading.Timer | None = None
        self._write_interval = write_interval
        self._last_write = float("-inf")
        self._last_prune = float("-inf")
        self.keep_days = keep_days
        """Days a recorded call is kept (``server.stats_keep_days``); re-read on every prune."""
        self.live = LiveHub()
        self._pending_calls: list[dict[str, Any]] = []
        self._pending_buckets: dict[tuple[str, Any, Identity], dict[str, Any]] = {}
        self._pending_hist: dict[tuple[str, Any, Identity, str, int], int] = {}
        self._pending_lifetime: dict[tuple[str, str], dict[str, Any]] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=RECENT_LIMIT)
        self._today_day = ""
        self._today_rows: dict[Identity, dict[str, Any]] = {}
        self._seq = 0
        if path is not None:
            self._import_legacy(path)
        self._load()

    @property
    def db(self) -> Database:
        return self._db

    # -- recording -------------------------------------------------------- #
    def turn(self, model: str, *, effort: str = "", origin: str = "chat", stream: bool = False, provider: str = "") -> Turn:
        return Turn(self, model, effort=effort, origin=origin, stream=stream, provider=provider)

    def record(
        self,
        *,
        model: str,
        ok: bool,
        input: int = 0,
        output: int = 0,
        reasoning: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        decode_tokens: int = 0,
        ttft_ms: int | None = None,
        decode_ms: int | None = None,
        elapsed_ms: int | None = None,
        effort: str = "",
        origin: str = "chat",
        no_usage: bool = False,
        provider: str = "",
        account: str = "",
        stream: bool = False,
        attempts: int = 0,
        finish_reason: str = "",
        error_code: str = "",
        error_message: str = "",
        call_id: str | None = None,
        at: float | None = None,
    ) -> dict[str, Any]:
        """Count one call everywhere it is counted; returns its call row.

        The ``finished`` live event is published while the store lock is still
        held, so a watcher subscribing concurrently sees the call either in
        its snapshot or as an event — never both and never neither. Both read
        the in-memory caches, so neither waits for the database.
        """
        now = time.time() if at is None else at
        measurable, ms, rate = decode_window(decode_ms, decode_tokens, ok)
        call = {
            "input": input,
            "output": output,
            "reasoning": reasoning,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "calls": 1,
            "failed": 0 if ok else 1,
            "no_usage": 1 if no_usage else 0,
            "last_at": now,
        }
        bucket = {**call, **dict.fromkeys(RATE_FIELDS, 0)}
        hist: list[tuple[str, int]] = []
        if ok and ttft_ms is not None:
            bucket["ttft_ms"] = max(0, ttft_ms)
            bucket["ttft_count"] = 1
            hist.append(("ttft", hist_index(ttft_ms, TTFT_HIST)))
        if measurable and rate is not None:
            bucket["decode_ms"] = ms
            bucket["decode_tokens"] = decode_tokens
            hist.append(("tps", hist_index(rate, TPS_HIST)))
        identity: Identity = (model, provider, account, origin)
        with self._lock:
            # First, so a day that just turned over is loaded before this call joins it.
            self._today_rows_locked(time.time())
            self._seq += 1
            seq = self._seq
            sample = {
                "seq": seq,
                "id": call_id or f"c{seq}",
                "at": iso(now),
                "ts": now,
                "model": model,
                "provider": provider,
                "account": account,
                "origin": origin,
                "effort": effort,
                "stream": stream,
                "ok": ok,
                "input": input,
                "output": output,
                "reasoning": reasoning,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "ttft_ms": ttft_ms,
                "decode_ms": ms if measurable else 0,
                "decode_tokens": decode_tokens if measurable else 0,
                "tps": rate,
                "elapsed_ms": elapsed_ms,
                "attempts": attempts,
                "finish_reason": finish_reason,
                "error_code": error_code,
                "error_message": short_error(error_message),
                "no_usage": no_usage,
            }
            self._pending_calls.append(sample)
            for scope, key in (("h", hour_start(now)), ("d", day_key(now))):
                _accumulate(self._pending_buckets.setdefault((scope, key, identity), {}), bucket, BUCKET_FIELDS)
                for kind, index in hist:
                    slot = (scope, key, identity, kind, index)
                    self._pending_hist[slot] = self._pending_hist.get(slot, 0) + 1
            for group, name in _lifetime_names(identity):
                _accumulate(self._pending_lifetime.setdefault((group, name), {}), call, TOTAL_FIELDS)
            if day_key(now) == self._today_day:
                merge_row(self._today_rows.setdefault(identity, zero_totals()), call)
            self._recent.append(sample)
            if call_id is not None:
                self.live.end(call_id, {"type": "finished", "call_id": call_id, "record": dict(sample), "today": _sum_totals(self._today_rows_locked(now))})
        self._schedule_write()
        return dict(sample)

    def reset(self) -> str:
        """Forget every counter, bucket and call; the ``seq`` cursor keeps counting.

        Keeping ``seq`` monotonic across a reset means a client holding an old
        cursor can never mistake a new call for one it has already seen. The
        rows go; ``sqlite_sequence`` stays, so the database agrees.
        """
        now = time.time()
        with self._lock:
            timer, self._timer = self._timer, None
            if timer is not None:
                timer.cancel()
            with self._db.transaction() as conn:
                # Live clients have already seen pending cursors. Keep their
                # high-water mark even when reset discards the unwritten batch.
                conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'calls' AND seq < ?", (self._seq, self._seq))
                conn.execute("INSERT INTO sqlite_sequence (name, seq) SELECT 'calls', ? WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'calls')", (self._seq,))
                for table in ("calls", "usage_hourly", "usage_daily", "usage_hist_hourly", "usage_hist_daily", "lifetime"):
                    conn.execute(f"DELETE FROM {table}")
            self._clear_pending_locked()
            self._recent.clear()
            self._today_rows = {}
        return iso(now)

    # -- reading ---------------------------------------------------------- #
    def subscribe(self, loop: asyncio.AbstractEventLoop, *, recent: int = 20) -> tuple[Subscription, dict[str, Any]]:
        """Register a live watcher and build its snapshot under one lock.

        Store lock, then hub lock — the same order :meth:`record` takes them —
        so the snapshot and the event stream that follows meet exactly.
        """
        now = time.time()
        with self._lock:
            today = _sum_totals(self._today_rows_locked(now))
            latest = [dict(sample) for sample in list(self._recent)[-recent:][::-1]] if recent > 0 else []
            subscription, active = self.live.subscribe(loop)
        return subscription, {"type": "snapshot", "server_time": now, "active": active, "today": today, "recent": latest}

    def today(self, now: float | None = None) -> dict[str, int]:
        with self._lock:
            return _sum_totals(self._today_rows_locked(time.time() if now is None else now))

    def today_rollups(self, now: float | None = None) -> dict[str, dict[str, dict[str, int]]]:
        """Today's totals by provider and by ``provider/account``, for the channels page."""
        with self._lock:
            rows = {identity: dict(row) for identity, row in self._today_rows_locked(time.time() if now is None else now).items()}
        providers: dict[str, dict[str, Any]] = {}
        accounts: dict[str, dict[str, Any]] = {}
        for (_model, provider, account, _origin), row in rows.items():
            if provider:
                merge_row(providers.setdefault(provider, zero_totals()), row)
                if account:
                    merge_row(accounts.setdefault(f"{provider}/{account}", zero_totals()), row)
        return {"providers": {key: totals_view(row) for key, row in providers.items()}, "accounts": {key: totals_view(row) for key, row in accounts.items()}}

    def last_used(self) -> dict[str, float]:
        """When each ``provider/account`` last served a call, in epoch seconds."""
        with self._reading() as conn:
            rows = conn.execute("SELECT name, last_at FROM lifetime WHERE grp = 'accounts' AND last_at > 0").fetchall()
        return {str(row["name"]): float(row["last_at"]) for row in rows}

    def calls(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        model: str | None = None,
        provider: str | None = None,
        account: str | None = None,
        origin: str | None = None,
        ok: bool | None = None,
        since: float | None = None,
        until: float | None = None,
        q: str | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Recorded calls newest first, filtered; and the cursor for the next older page.

        ``account`` matches either the bare account id or ``provider/account``.
        ``since`` (inclusive) and ``until`` (exclusive) are epoch seconds. ``q``
        is a case-insensitive substring of the model, provider, account,
        error code, error message or finish reason.
        """
        where: list[str] = []
        params: list[Any] = []
        for clause, value in (("seq < ?", before), ("model = ?", model), ("provider = ?", provider), ("origin = ?", origin), ("ts >= ?", since), ("ts < ?", until)):
            if value is not None:
                where.append(clause)
                params.append(value)
        if account is not None:
            where.append("(account = ? OR provider || '/' || account = ?)")
            params += [account, account]
        if ok is not None:
            where.append("ok = ?")
            params.append(1 if ok else 0)
        needle = (q or "").strip()
        if needle:
            pattern = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(" + " OR ".join(f"{name} LIKE ? ESCAPE '\\'" for name in CALL_SEARCH_FIELDS) + ")")
            params += [pattern] * len(CALL_SEARCH_FIELDS)
        sql = "SELECT * FROM calls" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY seq DESC LIMIT ?"
        params.append(max(0, limit) + 1)
        with self._reading() as conn:
            rows = conn.execute(sql, params).fetchall()
        matched = [_call_row(row) for row in rows[:limit]]
        return matched, (matched[-1]["seq"] if len(rows) > limit and matched else None)

    def days(self, now: float | None = None) -> dict[str, Any]:
        """Every local day with any usage, oldest first — for a calendar to mark."""
        moment = time.time() if now is None else now
        with self._reading() as conn:
            rows = conn.execute(
                "SELECT day, SUM(calls) AS calls, SUM(input + output) AS tokens, SUM(failed) AS failed FROM usage_daily GROUP BY day HAVING SUM(calls) > 0 OR SUM(input + output) > 0 ORDER BY day"
            ).fetchall()
        days = [{"date": str(row["day"]), "calls": int(row["calls"] or 0), "tokens": int(row["tokens"] or 0), "failed": int(row["failed"] or 0)} for row in rows]
        return {"days": days, "first": days[0]["date"] if days else None, "today": day_key(moment)}

    def report(self, range_key: str = "7d", now: float | None = None, date: str | None = None) -> dict[str, Any]:
        """Everything a usage dashboard shows for one range, from the buckets.

        Rolling ranges (``24h``, ``7d``, ``30d``, ``90d``) compare with the
        equal-length span just before. ``day`` is one local calendar day
        (``date``, default today) in hours — 23 or 25 of them across a DST
        change — compared with the day before; when the day is today, only
        with the day before *up to the same time of day*, since half a day
        against a whole one says nothing. Breakdown rows carry raw keys;
        display labels belong to the caller, which knows the catalogue.
        """
        moment = time.time() if now is None else now
        if range_key == "day":
            return self._day_report(moment, date)
        if range_key not in RANGES:
            raise ValidationError(f"Unknown range {range_key!r}.")
        granularity, count = RANGES[range_key]
        with self._reading() as conn:
            if granularity == "hour":
                current = _hour_starts(moment, count)
                keys: list[tuple[Any, str]] = [(start, iso(start)) for start in current]
                end = current[-1] + 3600
                rows = _load_buckets(conn, "h", current[0] - count * 3600, end)
                since, until = iso(current[0]), iso(end)
            else:
                days = _days(moment, count)
                keys = [(day.isoformat(), day.isoformat()) for day in days]
                end_day = days[-1] + timedelta(days=1)
                rows = _load_buckets(conn, "d", (days[0] - timedelta(days=count)).isoformat(), end_day.isoformat())
                since, until = iso(_midnight(days[0])), iso(_midnight(end_day))
            lifetime = _lifetime_total(conn)
        series, total, groups = _accumulate_range(keys, rows)
        first = keys[0][0]
        before = zero_totals()
        for key, identities in rows.items():
            if key < first:
                for row in identities.values():
                    merge_row(before, row)
        return _report_body(
            range_key=range_key,
            granularity=granularity,
            since=since,
            until=until,
            day=None,
            hourly=granularity == "hour",
            moment=moment,
            series=series,
            total=total,
            previous=before,
            groups=groups,
            lifetime=lifetime,
        )

    def _day_report(self, moment: float, raw: str | None) -> dict[str, Any]:
        today = _local(moment).date()
        day = _parse_day(raw, today)
        next_day = day + timedelta(days=1)
        previous_day = day - timedelta(days=1)
        start, end = _midnight(day), _midnight(next_day)
        previous_start = _midnight(previous_day)
        keys: list[tuple[Any, str]] = [(hour, iso(hour)) for hour in range(int(start), int(end), 3600)]
        keep_seconds = max(1, int(self.keep_days)) * 86400
        with self._reading() as conn:
            hourly_rows = _load_buckets(conn, "h", start, end)
            daily_rows = _load_buckets(conn, "d", previous_day.isoformat(), next_day.isoformat())
            if day == today:
                # The same wall-clock time yesterday: a fair "so far today" comparison.
                cut = datetime.combine(previous_day, _local(moment).time()).astimezone().timestamp()
                if previous_start >= moment - keep_seconds:
                    before = _calls_totals(conn, previous_start, cut)
                else:
                    before = zero_totals()
                    for identities in _load_buckets(conn, "h", previous_start, cut).values():
                        for row in identities.values():
                            merge_row(before, row)
            else:
                before = zero_totals()
                for row in daily_rows.get(previous_day.isoformat(), {}).values():
                    merge_row(before, row)
            lifetime = _lifetime_total(conn)
        series, _hourly_total, _hourly_groups = _accumulate_range(keys, hourly_rows)
        # Totals, breakdowns and latency come from the day row: identical to the
        # hours summed while those exist, and still there once they are pruned.
        _points, total, groups = _accumulate_range([(day.isoformat(), day.isoformat())], daily_rows)
        has_hourly = any(hourly_rows.values())
        has_daily = bool(daily_rows.get(day.isoformat()))
        before_retention = start < moment - KEEP_HOURLY_DAYS * 86400
        return _report_body(
            range_key="day",
            granularity="hour",
            since=iso(start),
            until=iso(end),
            day=day.isoformat(),
            hourly=has_hourly or (not has_daily and not before_retention),
            moment=moment,
            series=series,
            total=total,
            previous=before,
            groups=groups,
            lifetime=lifetime,
        )

    # -- persistence ------------------------------------------------------ #
    @contextmanager
    def _reading(self) -> Generator[sqlite3.Connection, None, None]:
        """Write what is pending, then hold one read transaction for a consistent snapshot."""
        with self._lock:
            self._write_locked()
        with self._db.transaction() as conn:
            yield conn

    def _today_rows_locked(self, moment: float) -> dict[Identity, dict[str, Any]]:
        """The day's totals by identity: from the cache for today, else from the database.

        A day later than the cached one becomes the cache — that is midnight
        passing — so the live view never queries more than once a day.
        """
        day = day_key(moment)
        if day == self._today_day:
            return self._today_rows
        self._write_locked()
        rows: dict[Identity, dict[str, Any]] = {}
        if not self._db.closed:
            for row in self._db.fetchall("SELECT * FROM usage_daily WHERE day = ?", (day,)):
                merge_row(rows.setdefault(_identity(row), zero_totals()), dict(row))
        if day > self._today_day:
            self._today_day, self._today_rows = day, rows
        return rows

    def _schedule_write(self) -> None:
        """Write now if the last write is old enough, else once the interval has passed."""
        wait = self._write_interval - (time.monotonic() - self._last_write)
        if wait <= 0:
            self.flush()
            return
        with self._lock:
            if self._timer is not None:
                return
            timer = threading.Timer(wait, self._timed_flush)
            timer.daemon = True
            self._timer = timer
        timer.start()

    def _timed_flush(self) -> None:
        with self._lock:
            self._timer = None
        self.flush()

    def flush(self) -> None:
        """Write the pending batch now (shutdown calls this); nothing pending writes nothing."""
        with self._lock:
            timer, self._timer = self._timer, None
            if timer is not None:
                timer.cancel()
            self._write_locked()

    def close(self) -> None:
        """Flush, and close the database if this store opened it."""
        self.flush()
        if self._owns_db:
            self._db.close()

    def _write_locked(self) -> None:
        """Write the pending batch in one transaction, pruning when it is due. Caller holds the lock."""
        if self._db.closed:
            return
        if not (self._pending_calls or self._pending_buckets or self._pending_hist or self._pending_lifetime):
            return
        # Only alongside a write: rows age out no faster than new ones arrive.
        prune = time.monotonic() - self._last_prune >= PRUNE_INTERVAL_SECONDS
        try:
            with self._db.transaction() as conn:
                if self._pending_calls:
                    _insert_calls(conn, self._pending_calls)
                _upsert_buckets(conn, [(scope, key, identity, row) for (scope, key, identity), row in self._pending_buckets.items()])
                _upsert_hist(conn, [(scope, key, identity, kind, index, count) for (scope, key, identity, kind, index), count in self._pending_hist.items()])
                _upsert_lifetime(conn, [(group, name, row) for (group, name), row in self._pending_lifetime.items()])
                if prune:
                    self._prune(conn, time.time())
        except sqlite3.Error as exc:
            # Kept pending: the next write tries again, and this process still counts it.
            logger.warning("could not write usage statistics: %s", exc)
            return
        self._clear_pending_locked()
        self._last_write = time.monotonic()
        if prune:
            self._last_prune = self._last_write

    def _prune(self, conn: sqlite3.Connection, now: float) -> None:
        conn.execute("DELETE FROM calls WHERE ts < ?", (now - max(1, int(self.keep_days)) * 86400,))
        cutoff = now - KEEP_HOURLY_DAYS * 86400
        conn.execute("DELETE FROM usage_hourly WHERE bucket_start < ?", (cutoff,))
        conn.execute("DELETE FROM usage_hist_hourly WHERE bucket_start < ?", (cutoff,))

    def _clear_pending_locked(self) -> None:
        self._pending_calls = []
        self._pending_buckets = {}
        self._pending_hist = {}
        self._pending_lifetime = {}

    def _load(self) -> None:
        sequence = self._db.fetchone("SELECT seq FROM sqlite_sequence WHERE name = 'calls'")
        top = self._db.fetchone("SELECT MAX(seq) AS seq FROM calls")
        self._seq = max(int(sequence["seq"]) if sequence is not None else 0, int(top["seq"] or 0) if top is not None else 0)
        latest = self._db.fetchall("SELECT * FROM calls ORDER BY seq DESC LIMIT ?", (RECENT_LIMIT,))
        self._recent.extend(_call_row(row) for row in reversed(latest))
        with self._lock:
            self._today_rows_locked(time.time())

    def _has_data(self) -> bool:
        row = self._db.fetchone("SELECT EXISTS(SELECT 1 FROM lifetime) OR EXISTS(SELECT 1 FROM calls) OR EXISTS(SELECT 1 FROM usage_daily) AS present")
        return bool(row is not None and row["present"])

    def _import_legacy(self, path: Path) -> None:
        """Bring an old ``stats.json`` into an empty database, once, then rename it aside.

        Never into a database that already counts something: that file is
        then older than the database, and adding it again would double it.
        """
        if not path.is_file() or self._has_data():
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("could not read %s: %s", path, exc)
            return
        try:
            payload: Any = json.loads(text)
        except ValueError as exc:
            payload, problem = None, str(exc)
        else:
            problem = "" if isinstance(payload, dict) else "not a JSON object"
        if problem or not isinstance(payload, dict):
            kept = retire_legacy_file(path, "corrupt")
            logger.warning("%s is unreadable (%s); usage statistics start over%s", path, problem, f", the file is kept as {kept.name}" if kept else "")
            return
        state = migrate(payload)
        try:
            with self._db.transaction() as conn:
                _write_state(conn, state)
        except sqlite3.Error as exc:
            logger.warning("could not import %s: %s", path, exc)
            return
        retire_legacy_file(path, "migrated")
        logger.info("Imported %s into %s: %d calls, %d days and %d hours of usage", path.name, self._db.path, len(state["samples"]), len(state["days"]), len(state["hours"]))


# -- report helpers ---------------------------------------------------------- #
def _report_body(
    *,
    range_key: str,
    granularity: str,
    since: str,
    until: str,
    day: str | None,
    hourly: bool,
    moment: float,
    series: list[dict[str, Any]],
    total: dict[str, Any],
    previous: dict[str, Any],
    groups: dict[str, dict[str, dict[str, Any]]],
    lifetime: dict[str, Any],
) -> dict[str, Any]:
    def breakdown(group: str) -> list[dict[str, Any]]:
        out = []
        for name, row in groups[group].items():
            rates = rates_view(row)
            provider, _, account = name.partition("/") if group == "account" else (name if group == "provider" else "", "", "")
            out.append(
                {
                    "key": name,
                    "provider": provider,
                    "account": account,
                    **totals_view(row),
                    "ttft_avg_ms": rates["ttft_avg_ms"],
                    "ttft_p50_ms": rates["ttft_p50_ms"],
                    "ttft_p95_ms": rates["ttft_p95_ms"],
                    "tps": rates["tps"],
                    "last_at": iso(row["last_at"]) if _num(row.get("last_at")) else None,
                }
            )
        return sorted(out, key=lambda row: (-(row["input"] + row["output"]), -row["calls"], row["key"]))

    rates = rates_view(total)
    return {
        "range": range_key,
        "granularity": granularity,
        "date": day,
        "since": since,
        "until": until,
        "hourly": hourly,
        "server_time": iso(moment),
        "totals": totals_view(total),
        "previous": totals_view(previous),
        "latency": {
            "ttft_avg_ms": rates["ttft_avg_ms"],
            "ttft_p50_ms": rates["ttft_p50_ms"],
            "ttft_p95_ms": rates["ttft_p95_ms"],
            "tps_avg": rates["tps"],
            "tps_p50": rates["tps_p50"],
        },
        "series": series,
        "by_model": breakdown("model"),
        "by_provider": breakdown("provider"),
        "by_account": breakdown("account"),
        "by_origin": breakdown("origin"),
        "lifetime": totals_view(lifetime),
        "requests": _int(lifetime.get("calls")),
        "failures": _int(lifetime.get("failed")),
    }


def _accumulate_range(
    keys: list[tuple[Any, str]], rows: dict[Any, dict[Identity, dict[str, Any]]]
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, dict[str, Any]]]]:
    """A zero-filled series over ``keys``, the range total, and the four breakdowns."""
    total = zero_bucket_row()
    series: list[dict[str, Any]] = []
    groups: dict[str, dict[str, dict[str, Any]]] = {"model": {}, "provider": {}, "account": {}, "origin": {}}
    for key, start in keys:
        point = zero_bucket_row()
        for identity, row in rows.get(key, {}).items():
            merge_row(point, row)
            model, provider, account, origin = identity
            for group, name in (("model", model), ("provider", provider), ("account", f"{provider}/{account}" if provider and account else ""), ("origin", origin)):
                if name:
                    merge_row(groups[group].setdefault(name, zero_bucket_row()), row)
        merge_row(total, point)
        rates = rates_view(point)
        series.append(
            {
                "start": start,
                **{name: point[name] for name in ("input", "output", "reasoning", "cache_read", "cache_write", "calls", "failed")},
                "ttft_avg_ms": rates["ttft_avg_ms"],
                "tps": rates["tps"],
            }
        )
    return series, total, groups


def _parse_day(raw: str | None, today: date) -> date:
    if raw is None or raw == "":
        return today
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        raise ValidationError(f"date must be YYYY-MM-DD (got {raw!r}).") from None
    if day > today:
        raise ValidationError(f"date {raw} is in the future.")
    return day


def _midnight(day: date) -> float:
    """Epoch seconds of local midnight starting ``day`` (DST-aware: the platform resolves the offset)."""
    return datetime.combine(day, datetime.min.time()).astimezone().timestamp()


def _hour_starts(moment: float, count: int) -> list[int]:
    """Epoch starts of the ``count`` local hours ending with the one holding ``moment``."""
    current = hour_start(moment)
    return [current - offset * 3600 for offset in range(count - 1, -1, -1)]


def _days(moment: float, count: int) -> list[date]:
    today = _local(moment).date()
    return [today - timedelta(days=offset) for offset in range(count - 1, -1, -1)]


def hour_start(at: float | None = None) -> int:
    """Epoch seconds at which the local clock hour holding ``at`` began: the hourly bucket key."""
    return int(_local(at).replace(minute=0, second=0, microsecond=0).timestamp())


def _lifetime_names(identity: Identity) -> list[tuple[str, str]]:
    model, provider, account, origin = identity
    names = [("models", model), ("providers", provider), ("accounts", f"{provider}/{account}" if provider and account else ""), ("origins", origin)]
    return [(group, name) for group, name in names if name] + [(TOTAL_GROUP, "")]


def _accumulate(into: dict[str, Any], delta: dict[str, Any], fields: tuple[str, ...]) -> None:
    for name in fields:
        into[name] = into.get(name, 0) + delta.get(name, 0)
    into["last_at"] = max(_num(into.get("last_at")), _num(delta.get("last_at")))


def _sum_totals(rows: dict[Identity, dict[str, Any]]) -> dict[str, int]:
    acc = zero_totals()
    for row in rows.values():
        merge_row(acc, row)
    return totals_view(acc)


def _identity(row: sqlite3.Row) -> Identity:
    return (str(row["model"]), str(row["provider"]), str(row["account"]), str(row["origin"]))


def _call_row(row: sqlite3.Row) -> dict[str, Any]:
    out = {name: row[name] for name in CALL_FIELDS}
    for name in ("ok", "stream", "no_usage"):
        out[name] = bool(out[name])
    return out


def _load_buckets(conn: sqlite3.Connection, scope: str, lo: Any, hi: Any) -> dict[Any, dict[Identity, dict[str, Any]]]:
    """Bucket rows with ``lo <= key < hi``, histograms attached, as ``{key: {identity: row}}``."""
    table, hist_table, key = _BUCKET_TABLES[scope]
    out: dict[Any, dict[Identity, dict[str, Any]]] = {}
    for record in conn.execute(f"SELECT * FROM {table} WHERE {key} >= ? AND {key} < ?", (lo, hi)):
        row = zero_bucket_row()
        for name in BUCKET_FIELDS:
            row[name] = int(record[name])
        row["last_at"] = float(record["last_at"])
        out.setdefault(record[key], {})[_identity(record)] = row
    for record in conn.execute(f"SELECT * FROM {hist_table} WHERE {key} >= ? AND {key} < ?", (lo, hi)):
        row = out.setdefault(record[key], {}).setdefault(_identity(record), zero_bucket_row())
        hist = row["ttft_hist"] if record["kind"] == "ttft" else row["tps_hist"]
        index = str(record["bin"])
        hist[index] = hist.get(index, 0) + int(record["count"])
    return out


def _lifetime_total(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM lifetime WHERE grp = ? AND name = ''", (TOTAL_GROUP,)).fetchone()
    out = zero_totals()
    if row is not None:
        merge_row(out, dict(row))
    return out


def _calls_totals(conn: sqlite3.Connection, since: float, until: float) -> dict[str, Any]:
    """Totals of the recorded calls in ``[since, until)``: exact to the second, unlike a bucket."""
    row = conn.execute(
        "SELECT COALESCE(SUM(input), 0) AS input, COALESCE(SUM(output), 0) AS output, COALESCE(SUM(reasoning), 0) AS reasoning,"
        " COALESCE(SUM(cache_read), 0) AS cache_read, COALESCE(SUM(cache_write), 0) AS cache_write, COUNT(*) AS calls,"
        " COALESCE(SUM(1 - ok), 0) AS failed, COALESCE(SUM(no_usage), 0) AS no_usage FROM calls WHERE ts >= ? AND ts < ?",
        (since, until),
    ).fetchone()
    out = zero_totals()
    if row is not None:
        merge_row(out, dict(row))
    return out


# -- writing ------------------------------------------------------------------ #
_BUCKET_TABLES = {"h": ("usage_hourly", "usage_hist_hourly", "bucket_start"), "d": ("usage_daily", "usage_hist_daily", "day")}


def _upsert_sql(table: str, keys: tuple[str, ...], fields: tuple[str, ...]) -> str:
    columns = (*keys, *fields, "last_at")
    updates = ", ".join(f"{name} = {name} + excluded.{name}" for name in fields)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))}) "
        f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET {updates}, last_at = max(last_at, excluded.last_at)"
    )


_BUCKET_SQL = {scope: _upsert_sql(table, (key, *IDENTITY_FIELDS), BUCKET_FIELDS) for scope, (table, _hist, key) in _BUCKET_TABLES.items()}
_HIST_SQL = {
    scope: f"INSERT INTO {hist} ({key}, model, provider, account, origin, kind, bin, count) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
    f"ON CONFLICT({key}, model, provider, account, origin, kind, bin) DO UPDATE SET count = count + excluded.count"
    for scope, (_table, hist, key) in _BUCKET_TABLES.items()
}
_LIFETIME_SQL = _upsert_sql("lifetime", ("grp", "name"), TOTAL_FIELDS)
_CALL_SQL = f"INSERT OR IGNORE INTO calls ({', '.join(CALL_FIELDS)}) VALUES ({', '.join('?' * len(CALL_FIELDS))})"


def _insert_calls(conn: sqlite3.Connection, samples: list[dict[str, Any]]) -> None:
    conn.executemany(_CALL_SQL, [tuple(sample[name] for name in CALL_FIELDS) for sample in samples])


def _upsert_buckets(conn: sqlite3.Connection, items: list[tuple[str, Any, Identity, dict[str, Any]]]) -> None:
    for scope in _BUCKET_TABLES:
        rows = [(key, *identity, *(_int(row.get(name)) for name in BUCKET_FIELDS), _num(row.get("last_at"))) for kind, key, identity, row in items if kind == scope]
        if rows:
            conn.executemany(_BUCKET_SQL[scope], rows)


def _upsert_hist(conn: sqlite3.Connection, items: list[tuple[str, Any, Identity, str, int, int]]) -> None:
    for scope in _BUCKET_TABLES:
        rows = [(key, *identity, kind, index, count) for which, key, identity, kind, index, count in items if which == scope and count > 0]
        if rows:
            conn.executemany(_HIST_SQL[scope], rows)


def _upsert_lifetime(conn: sqlite3.Connection, items: list[tuple[str, str, dict[str, Any]]]) -> None:
    rows = [(group, name, *(_int(row.get(field)) for field in TOTAL_FIELDS), _num(row.get("last_at"))) for group, name, row in items]
    if rows:
        conn.executemany(_LIFETIME_SQL, rows)


def _hour_key_start(key: str) -> int | None:
    """``YYYY-MM-DDTHH`` (a local hour, as ``stats.json`` keyed it) as the epoch start of that hour."""
    try:
        return int(datetime.strptime(key, "%Y-%m-%dT%H").astimezone().timestamp())
    except ValueError:
        return None


def _write_state(conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    """Write a :func:`migrate`-shaped ``stats.json`` state into the tables (the one-time import)."""
    _insert_calls(conn, state["samples"])
    buckets: list[tuple[str, Any, Identity, dict[str, Any]]] = []
    hist: list[tuple[str, Any, Identity, str, int, int]] = []
    for scope, source in (("h", "hours"), ("d", "days")):
        for raw_key, rows in state[source].items():
            key: Any = _hour_key_start(raw_key) if scope == "h" else raw_key
            if key is None:
                continue
            for joined, row in rows.items():
                identity = split_key(joined)
                buckets.append((scope, key, identity, row))
                for kind in ("ttft", "tps"):
                    for index, count in (row.get(f"{kind}_hist") or {}).items():
                        if str(index).isdigit() and _int(count):
                            hist.append((scope, key, identity, kind, int(index), _int(count)))
    _upsert_buckets(conn, buckets)
    _upsert_hist(conn, hist)
    lifetime: list[tuple[str, str, dict[str, Any]]] = []
    total = zero_totals()
    for group in LIFETIME_GROUPS:
        for name, row in state["lifetime"][group].items():
            lifetime.append((group, name, row))
            if group == "models":
                merge_row(total, row)
    if state["requests"]:
        # The file's own request counters are the authority: a call with no
        # model id was counted there but under no model.
        total["calls"] = state["requests"]
        total["failed"] = state["failed_requests"]
    lifetime.append((TOTAL_GROUP, "", total))
    _upsert_lifetime(conn, lifetime)
    seq = _int(state.get("seq"))
    if seq:
        # AUTOINCREMENT continues after the file's last cursor, not after its last kept row.
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'calls' AND seq < ?", (seq, seq))
        conn.execute("INSERT INTO sqlite_sequence (name, seq) SELECT 'calls', ? WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'calls')", (seq,))


# -- stats.json --------------------------------------------------------------- #
def _fresh_state() -> dict[str, Any]:
    return {
        "version": STATS_VERSION,
        "seq": 0,
        "requests": 0,
        "failed_requests": 0,
        "lifetime": {"models": {}, "providers": {}, "accounts": {}, "origins": {}},
        "days": {},
        "hours": {},
        "samples": [],
    }


def migrate(payload: dict[str, Any]) -> dict[str, Any]:
    """Read a ``stats.json`` of either version into the version 2 shape (before its import).

    Version 1 kept day buckets keyed by model alone and lifetime rows by model
    alone. Those rows keep every total they had; the identity they never
    recorded (provider, account, origin) is left empty rather than guessed,
    so a migrated day still counts in every total and in ``by_model``, and
    simply does not appear under a provider it cannot be attributed to.
    """
    state = _fresh_state()
    for name in ("requests", "failed_requests", "seq"):
        state[name] = _int(payload.get(name))
    samples = payload.get("samples")
    if payload.get("version") == STATS_VERSION:
        for name in ("days", "hours"):
            raw = payload.get(name)
            if isinstance(raw, dict):
                state[name] = {key: {row: _bucket_row(value) for row, value in rows.items() if isinstance(value, dict)} for key, rows in raw.items() if isinstance(rows, dict)}
        lifetime = payload.get("lifetime")
        if isinstance(lifetime, dict):
            for group in state["lifetime"]:
                rows = lifetime.get(group)
                if isinstance(rows, dict):
                    state["lifetime"][group] = {key: _totals_row(value) for key, value in rows.items() if isinstance(value, dict)}
    else:
        days = payload.get("days")
        if isinstance(days, dict):
            for day, bucket in days.items():
                models = bucket.get("models") if isinstance(bucket, dict) else None
                if isinstance(models, dict):
                    state["days"][day] = {row_key(model): _bucket_row(row) for model, row in models.items() if isinstance(row, dict)}
        models = payload.get("models")
        if isinstance(models, dict):
            state["lifetime"]["models"] = {model: _totals_row(row) for model, row in models.items() if isinstance(row, dict)}
    if isinstance(samples, list):
        kept = [sample for sample in samples if isinstance(sample, dict)]
        seq = state["seq"]
        for sample in kept:
            if not _int(sample.get("seq")):
                seq += 1
                sample["seq"] = seq
            seq = max(seq, _int(sample.get("seq")))
        state["seq"] = max(seq, state["seq"])
        state["samples"] = [_sample(sample) for sample in kept]
    return state


def _totals_row(raw: dict[str, Any]) -> dict[str, Any]:
    row = zero_totals()
    merge_row(row, raw)
    return row


def _bucket_row(raw: dict[str, Any]) -> dict[str, Any]:
    row = zero_bucket_row()
    merge_row(row, raw)
    if "ttft_samples" in raw:  # version 1's name for the count
        row["ttft_count"] += _int(raw.get("ttft_samples"))
    return row


def _sample(raw: dict[str, Any]) -> dict[str, Any]:
    seq = _int(raw.get("seq"))
    at = raw.get("at") if isinstance(raw.get("at"), str) else ""
    ts = _num(raw.get("ts"))
    if not ts and at:
        try:
            ts = datetime.fromisoformat(at).timestamp()
        except ValueError:
            ts = 0.0
    out: dict[str, Any] = {
        "seq": seq,
        "id": raw.get("id") if isinstance(raw.get("id"), str) and raw.get("id") else f"c{seq}",
        "at": at or (iso(ts) if ts else ""),
        "ts": ts,
    }
    for name in ("model", "provider", "account", "origin", "effort", "finish_reason", "error_code", "error_message"):
        value = raw.get(name)
        out[name] = value if isinstance(value, str) else ""
    out["origin"] = out["origin"] or "chat"
    for name in ("input", "output", "reasoning", "cache_read", "cache_write", "decode_ms", "decode_tokens", "attempts"):
        out[name] = _int(raw.get(name))
    for name in ("ttft_ms", "tps", "elapsed_ms"):
        value = raw.get(name)
        out[name] = value if isinstance(value, int) and not isinstance(value, bool) else None
    out["ok"] = bool(raw.get("ok", True))
    out["stream"] = bool(raw.get("stream", False))
    out["no_usage"] = bool(raw.get("no_usage", False))
    return out
