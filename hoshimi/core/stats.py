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

The store keeps three shapes of the same calls, each for a different question:

- **buckets** (local hours for three days, local days for :data:`KEEP_DAYS`)
  answer "how much, over this range". A bucket row is keyed by the full
  identity of a call — ``model``, ``provider``, ``account``, ``origin`` joined
  by tabs — so any of those groupings can be recovered for any range by
  summing, without storing four copies of every bucket. Each row also carries
  fixed log-scale histograms of TTFT and tokens/s, which merge by addition, so
  a p50 or p95 over a range is computed from the range rather than averaged
  out of per-bucket percentiles (which is not a percentile of anything).
- **lifetime** rollups by model, provider, account and origin survive the
  bucket pruning, so "ever served" never shrinks.
- a **ring** of the last :data:`SAMPLES_LIMIT` calls, each with a monotonically
  increasing ``seq``: the rows a "that answer vanished" report audits, and the
  cursor a client pages them with.

Writes are debounced: a busy gateway records many calls a second, and
rewriting the whole file for each of them is the one cost here that grows with
traffic. A dirty store is written at most every :data:`WRITE_INTERVAL_SECONDS`,
and :meth:`StatsStore.flush` (called on shutdown) writes whatever is pending.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import threading
import time
import uuid
import weakref
from collections.abc import Iterable, Iterator
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshimi.core.errors import HoshimiError
from hoshimi.core.live import LiveHub, Subscription

STATS_FILE_NAME = "stats.json"
"""Where the counters survive a restart."""

STATS_VERSION = 2

MIN_DECODE_MS = 250
MAX_CREDIBLE_TPS = 250
KEEP_DAYS = 120
KEEP_HOURS = 72
SAMPLES_LIMIT = 1000
WRITE_INTERVAL_SECONDS = 2.0
PROGRESS_INTERVAL_SECONDS = 0.25
"""At most four progress events a second per call; a phase change is never held back."""

RANGES: dict[str, tuple[str, int]] = {"24h": ("hour", 24), "7d": ("day", 7), "30d": ("day", 30), "90d": ("day", 90)}
"""A report range: the bucket granularity it reads, and how many buckets."""

# Fixed log-scale histogram edges. Bucket ``i`` holds values in
# ``[base * ratio**i, base * ratio**(i + 1))``; anything below the first edge
# lands in bucket 0 and anything past the last in the last. Fixed so that rows
# written months apart still merge by plain addition.
TTFT_HIST = (10.0, 1.15, 80)  # 10 ms .. ~12 min
TPS_HIST = (1.0, 1.08, 72)  # 1 .. ~250 tok/s

KEY_SEPARATOR = "\t"
"""Joins a bucket row's identity: ids never contain a tab, and JSON keeps it readable."""

TOTAL_FIELDS = ("input", "output", "reasoning", "cache_read", "cache_write", "calls", "failed", "no_usage")
RATE_FIELDS = ("ttft_ms", "ttft_count", "decode_ms", "decode_tokens")
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
    """Hour, day and lifetime rollups plus a bounded ring of per-call samples."""

    def __init__(self, path: Path | None = None, *, write_interval: float = WRITE_INTERVAL_SECONDS):
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._path = path
        self._state = _fresh_state()
        self._dirty = False
        self._last_write = 0.0
        self._timer: threading.Timer | None = None
        self._write_interval = write_interval
        self.live = LiveHub()
        self._load()

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
        """Store one call everywhere it is counted; returns its ring sample.

        The ``finished`` live event is published while the store lock is still
        held, so a watcher subscribing concurrently sees the call either in
        its snapshot or as an event — never both and never neither.
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
        with self._lock:
            state = self._state
            key = row_key(model, provider, account, origin)
            for buckets, bucket in ((state["hours"], hour_key(now)), (state["days"], day_key(now))):
                row = buckets.setdefault(bucket, {}).setdefault(key, zero_bucket_row())
                merge_row(row, call)
                if ok and ttft_ms is not None:
                    row["ttft_ms"] += max(0, ttft_ms)
                    row["ttft_count"] += 1
                    hist_add(row["ttft_hist"], ttft_ms, TTFT_HIST)
                if measurable and rate is not None:
                    row["decode_ms"] += ms
                    row["decode_tokens"] += decode_tokens
                    hist_add(row["tps_hist"], rate, TPS_HIST)
            lifetime = state["lifetime"]
            for group, name in (("models", model), ("providers", provider), ("accounts", f"{provider}/{account}" if provider and account else ""), ("origins", origin)):
                if name:
                    merge_row(lifetime[group].setdefault(name, zero_totals()), call)
            state["requests"] += 1
            state["failed_requests"] += 0 if ok else 1
            state["seq"] += 1
            seq = state["seq"]
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
            samples = state["samples"]
            samples.append(sample)
            if len(samples) > SAMPLES_LIMIT:
                del samples[: len(samples) - SAMPLES_LIMIT]
            self._prune_locked(now)
            self._dirty = True
            if call_id is not None:
                self.live.end(call_id, {"type": "finished", "call_id": call_id, "record": dict(sample), "today": self._today_locked(now)})
        self._schedule_write()
        return dict(sample)

    def _prune_locked(self, now: float) -> None:
        state = self._state
        oldest_hour = hour_key(now - (KEEP_HOURS - 1) * 3600)
        for key in [key for key in state["hours"] if key < oldest_hour]:
            del state["hours"][key]
        oldest_day = (_local(now).date() - timedelta(days=KEEP_DAYS - 1)).isoformat()
        for key in [key for key in state["days"] if key < oldest_day]:
            del state["days"][key]

    def reset(self) -> str:
        """Forget every counter, bucket and sample; the ``seq`` cursor keeps counting.

        Keeping ``seq`` monotonic across a reset means a client holding an old
        cursor can never mistake a new call for one it has already seen.
        """
        now = time.time()
        with self._lock:
            seq = self._state["seq"]
            self._state = _fresh_state()
            self._state["seq"] = seq
            self._dirty = True
        self.flush()
        return iso(now)

    # -- reading ---------------------------------------------------------- #
    def subscribe(self, loop: asyncio.AbstractEventLoop, *, recent: int = 20) -> tuple[Subscription, dict[str, Any]]:
        """Register a live watcher and build its snapshot under one lock.

        Store lock, then hub lock — the same order :meth:`record` takes them —
        so the snapshot and the event stream that follows meet exactly.
        """
        now = time.time()
        with self._lock:
            today = self._today_locked(now)
            latest = [dict(sample) for sample in reversed(self._state["samples"][-recent:])]
            subscription, active = self.live.subscribe(loop)
        return subscription, {"type": "snapshot", "server_time": now, "active": active, "today": today, "recent": latest}

    def today(self, now: float | None = None) -> dict[str, int]:
        with self._lock:
            return self._today_locked(time.time() if now is None else now)

    def _today_locked(self, now: float) -> dict[str, int]:
        acc = zero_totals()
        for row in self._state["days"].get(day_key(now), {}).values():
            merge_row(acc, row)
        return totals_view(acc)

    def today_rollups(self, now: float | None = None) -> dict[str, dict[str, dict[str, int]]]:
        """Today's totals by provider and by ``provider/account``, for the channels page."""
        with self._lock:
            rows = dict(self._state["days"].get(day_key(time.time() if now is None else now), {}))
            providers: dict[str, dict[str, Any]] = {}
            accounts: dict[str, dict[str, Any]] = {}
            for key, row in rows.items():
                _model, provider, account, _origin = split_key(key)
                if provider:
                    merge_row(providers.setdefault(provider, zero_totals()), row)
                    if account:
                        merge_row(accounts.setdefault(f"{provider}/{account}", zero_totals()), row)
        return {"providers": {key: totals_view(row) for key, row in providers.items()}, "accounts": {key: totals_view(row) for key, row in accounts.items()}}

    def last_used(self) -> dict[str, float]:
        """When each ``provider/account`` last served a call, in epoch seconds."""
        with self._lock:
            return {key: _num(row.get("last_at")) for key, row in self._state["lifetime"]["accounts"].items() if _num(row.get("last_at"))}

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
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Ring samples newest first, filtered; and the cursor for the next older page.

        ``account`` matches either the bare account id or ``provider/account``.
        """

        def wanted(sample: dict[str, Any]) -> bool:
            if before is not None and _int(sample.get("seq")) >= before:
                return False
            if model is not None and sample.get("model") != model:
                return False
            if provider is not None and sample.get("provider") != provider:
                return False
            if account is not None and account not in (sample.get("account"), f"{sample.get('provider')}/{sample.get('account')}"):
                return False
            if origin is not None and sample.get("origin") != origin:
                return False
            return ok is None or bool(sample.get("ok")) == ok

        with self._lock:
            matched: list[dict[str, Any]] = []
            more = False
            for sample in reversed(self._state["samples"]):
                if not wanted(sample):
                    continue
                if len(matched) == limit:
                    more = True
                    break
                matched.append(dict(sample))
        return matched, (matched[-1]["seq"] if more and matched else None)

    def report(self, range_key: str = "7d", now: float | None = None) -> dict[str, Any]:
        """Everything a usage dashboard shows for one range, from the buckets.

        The previous period is the equal-length span just before, as far as
        the buckets still reach. Breakdown rows carry raw keys; display labels
        belong to the caller, which knows the catalogue.
        """
        granularity, count = RANGES[range_key]
        moment = time.time() if now is None else now
        if granularity == "hour":
            current = _hour_starts(moment, count)
            previous = _hour_starts(moment - count * 3600, count)
            keys = [(hour_key(start), iso(start)) for start in current]
            previous_keys = [hour_key(start) for start in previous]
            since = iso(current[0])
            source = "hours"
        else:
            days = _days(moment, count)
            keys = [(day.isoformat(), day.isoformat()) for day in days]
            previous_keys = [(days[0] - timedelta(days=offset)).isoformat() for offset in range(count, 0, -1)]
            since = datetime.combine(days[0], datetime.min.time()).astimezone().isoformat(timespec="seconds")
            source = "days"

        with self._lock:
            buckets = self._state[source]
            rows = {key: deepcopy(buckets.get(key, {})) for key, _label in keys}
            previous_rows = [deepcopy(row) for key in previous_keys for row in buckets.get(key, {}).values()]
            lifetime = zero_totals()
            for row in self._state["lifetime"]["models"].values():
                merge_row(lifetime, row)
            requests = self._state["requests"]
            failures = self._state["failed_requests"]

        total = zero_bucket_row()
        series: list[dict[str, Any]] = []
        groups: dict[str, dict[str, dict[str, Any]]] = {"model": {}, "provider": {}, "account": {}, "origin": {}}
        for key, start in keys:
            point = zero_bucket_row()
            for identity, row in rows[key].items():
                merge_row(point, row)
                model, provider, account, origin = split_key(identity)
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
        before = zero_totals()
        for row in previous_rows:
            merge_row(before, row)

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
            "since": since,
            "totals": totals_view(total),
            "previous": totals_view(before),
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
            "requests": requests,
            "failures": failures,
        }

    # -- persistence ------------------------------------------------------ #
    def _schedule_write(self) -> None:
        """Write now if the last write is old enough, else once the interval has passed."""
        if self._path is None:
            return
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
        """Write pending changes now (shutdown calls this); a clean store writes nothing."""
        with self._lock:
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if self._path is None:
            with self._lock:
                self._dirty = False
            return
        # One writer at a time, and the snapshot is taken inside that turn, so
        # an older snapshot can never land on top of a newer one.
        with self._write_lock:
            with self._lock:
                if not self._dirty:
                    return
                payload = json.dumps(self._state, ensure_ascii=False, separators=(",", ":"))
                self._dirty = False
            self._last_write = time.monotonic()
            self._write(payload)

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        migrated = payload.get("version") != STATS_VERSION
        state = migrate(payload)
        with self._lock:
            self._state = state
            self._dirty = migrated
        if migrated:
            # In place, so the file on disk speaks the version this code reads.
            self.flush()

    def _write(self, payload: str) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self._path)
        except OSError:
            # A counter the disk refused is still in memory for this process.
            pass


def _hour_starts(moment: float, count: int) -> list[float]:
    """Epoch starts of the ``count`` local hours ending with the one holding ``moment``."""
    current = _local(moment).replace(minute=0, second=0, microsecond=0).timestamp()
    return [current - offset * 3600 for offset in range(count - 1, -1, -1)]


def _days(moment: float, count: int) -> list[date]:
    today = _local(moment).date()
    return [today - timedelta(days=offset) for offset in range(count - 1, -1, -1)]


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
    """Read a stored file of either version into the current shape.

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
        kept = [sample for sample in samples if isinstance(sample, dict)][-SAMPLES_LIMIT:]
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
        "at": at,
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
