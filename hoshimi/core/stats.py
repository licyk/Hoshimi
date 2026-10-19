"""Usage accounting: what this gateway actually served, by day and model.

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
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

STATS_FILE_NAME = "stats.json"
"""Where the counters survive a restart."""

MIN_DECODE_MS = 250
MAX_CREDIBLE_TPS = 250
KEEP_DAYS = 120
SAMPLES_LIMIT = 400
SAMPLES_EXPOSED = 200

# Chunks that prove tokens are streaming right now.
_CONTENT_KINDS = frozenset({"text_delta", "reasoning_delta", "tool_call_start", "tool_call_delta"})
_REASONING_KINDS = frozenset({"reasoning_delta"})

_INITIAL: dict[str, Any] = {
    "version": 1,
    "days": {},
    "models": {},
    "requests": 0,
    "failed_requests": 0,
    "samples": [],
}


def day_key(at: float | None = None) -> str:
    """The local calendar day a call belongs to on the dashboard."""
    return datetime.fromtimestamp(at).astimezone().date().isoformat() if at is not None else datetime.now().astimezone().date().isoformat()


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


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _zero_row() -> dict[str, Any]:
    return {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "calls": 0, "failed": 0}


def _rate_row() -> dict[str, Any]:
    return {"ttft_ms": 0, "ttft_samples": 0, "decode_ms": 0, "decode_tokens": 0}


class Turn:
    """One request's contribution to the stats, recorded exactly once."""

    def __init__(self, store: StatsStore, model: str, *, effort: str = "", origin: str = "chat"):
        self._store = store
        self._model = model
        self._effort = effort
        self._origin = origin
        self._started = time.monotonic()
        self._first: float | None = None
        self._ended: float | None = None
        self._usage: dict[str, Any] | None = None
        self._saw_reasoning = False
        self._ok = True
        self._done = False

    def wrap(self, chunks: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """Pass every chunk through, watching the stream for timing and usage.

        A client that hangs up mid-answer is a call the gateway *did* serve, so
        it lands as a success; `failed` is reserved for the gateway or the
        upstream failing the call.
        """
        try:
            for chunk in chunks:
                if isinstance(chunk, dict):
                    self._observe(chunk)
                yield chunk
        except GeneratorExit:
            self.finish()
            raise
        except BaseException:
            self._ok = False
            self.finish()
            raise
        self.finish()

    def fail(self) -> None:
        """Record a request that never produced a stream (routing errors too)."""
        self._ok = False
        self.finish()

    def _observe(self, chunk: dict[str, Any]) -> None:
        kind = chunk.get("type")
        if kind in _CONTENT_KINDS:
            if self._first is None:
                self._first = time.monotonic()
            if kind in _REASONING_KINDS:
                self._saw_reasoning = True
        elif kind == "usage":
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                self._usage = usage
        elif kind == "finish":
            self._ended = time.monotonic()
        elif kind == "error" or chunk.get("error") is not None:
            self._ok = False

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
        ttft_ms = int((self._first - self._started) * 1000) if self._first is not None else None
        decode_ms = int((ended - self._first) * 1000) if self._first is not None else None
        self._store.record(
            model=self._model,
            ok=self._ok,
            input=_int(usage.get("prompt_tokens")),
            output=output,
            reasoning=reasoning,
            cache_read=_int(prompt_details.get("cached_tokens")),
            decode_tokens=window_tokens(output, reasoning, self._saw_reasoning) if self._ok else 0,
            ttft_ms=ttft_ms,
            decode_ms=decode_ms,
            elapsed_ms=int((time.monotonic() - self._started) * 1000),
            effort=self._effort,
            origin=self._origin,
            no_usage=not usage,
        )


class StatsStore:
    """Day and model buckets plus a bounded ring of per-call samples."""

    def __init__(self, path: Path | None = None):
        self._lock = threading.Lock()
        self._path = path
        self._state = _fresh_state()
        self._load()

    # -- recording -------------------------------------------------------- #
    def turn(self, model: str, *, effort: str = "", origin: str = "chat") -> Turn:
        return Turn(self, model, effort=effort, origin=origin)

    def record(
        self,
        *,
        model: str,
        ok: bool,
        input: int = 0,
        output: int = 0,
        reasoning: int = 0,
        cache_read: int = 0,
        decode_tokens: int = 0,
        ttft_ms: int | None = None,
        decode_ms: int | None = None,
        elapsed_ms: int | None = None,
        effort: str = "",
        origin: str = "chat",
        no_usage: bool = False,
    ) -> None:
        now = time.time()
        with self._lock:
            days = self._state["days"]
            day = day_key(now)
            bucket = days.setdefault(day, {"total": 0, "models": {}})
            per_model = bucket["models"]
            row = per_model.setdefault(model, _zero_row() | _rate_row())
            row["input"] += input
            row["output"] += output
            row["reasoning"] += reasoning
            row["cache_read"] += cache_read
            row["calls"] += 1
            row["failed"] += 0 if ok else 1
            if ok and ttft_ms is not None:
                row["ttft_ms"] += ttft_ms
                row["ttft_samples"] += 1
            measurable, ms, rate = decode_window(decode_ms, decode_tokens, ok)
            if measurable:
                row["decode_ms"] += ms
                row["decode_tokens"] += decode_tokens
            bucket["total"] += input + output

            lifetime = self._state["models"].setdefault(model, _zero_row())
            lifetime["input"] += input
            lifetime["output"] += output
            lifetime["reasoning"] += reasoning
            lifetime["cache_read"] += cache_read
            lifetime["calls"] += 1
            lifetime["failed"] += 0 if ok else 1

            self._state["requests"] += 1
            self._state["failed_requests"] += 0 if ok else 1
            samples = self._state["samples"]
            samples.append(
                {
                    "at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
                    "model": model,
                    "ok": ok,
                    "input": input,
                    "output": output,
                    "reasoning": reasoning,
                    "ttft_ms": ttft_ms,
                    "tps": rate,
                    "decode_ms": ms if measurable else 0,
                    "decode_tokens": decode_tokens if measurable else 0,
                    "elapsed_ms": elapsed_ms,
                    "effort": effort,
                    "origin": origin,
                    **({} if not no_usage else {"no_usage": True}),
                }
            )
            if len(samples) > SAMPLES_LIMIT:
                del samples[: len(samples) - SAMPLES_LIMIT]
            self._prune_days_locked()
            snapshot = json.dumps(self._state, ensure_ascii=False)
        self._write(snapshot)

    def _prune_days_locked(self) -> None:
        days = self._state["days"]
        if len(days) <= KEEP_DAYS:
            return
        for key in sorted(days)[: len(days) - KEEP_DAYS]:
            del days[key]

    # -- reading ---------------------------------------------------------- #
    def snapshot(self, names: dict[str, str] | None = None) -> dict[str, Any]:
        names = names or {}
        with self._lock:
            days_raw = {day: {"total": row.get("total", 0), "models": {m: dict(r) for m, r in row.get("models", {}).items()}} for day, row in self._state["days"].items()}
            lifetime = {model: dict(row) for model, row in self._state["models"].items()}
            requests = self._state["requests"]
            failures = self._state["failed_requests"]
            samples = [dict(row) for row in self._state["samples"][-SAMPLES_EXPOSED:]]

        series: list[dict[str, Any]] = []
        totals: dict[str, dict[str, Any]] = {}
        for day in sorted(days_raw):
            bucket = days_raw[day]
            rows = []
            for model, row in bucket["models"].items():
                rows.append(
                    {
                        "model": model,
                        "name": names.get(model, model),
                        "input": row.get("input", 0),
                        "output": row.get("output", 0),
                        "reasoning": row.get("reasoning", 0),
                        "calls": row.get("calls", 0),
                        "failed": row.get("failed", 0),
                    }
                )
                combined = totals.setdefault(model, _zero_row() | _rate_row())
                for key in ("input", "output", "reasoning", "cache_read", "calls", "failed"):
                    combined[key] += row.get(key, 0)
                for key in ("ttft_ms", "ttft_samples", "decode_ms", "decode_tokens"):
                    combined[key] += row.get(key, 0)
            series.append({"day": day, "total": bucket.get("total", 0), "models": rows})

        models: list[dict[str, Any]] = []
        for model in sorted(set(totals) | set(lifetime)):
            day_row = totals.get(model, _zero_row() | _rate_row())
            life = lifetime.get(model, _zero_row())
            samples_ok = day_row["ttft_samples"]
            models.append(
                {
                    "model": model,
                    "name": names.get(model, model),
                    "input": life["input"],
                    "output": life["output"],
                    "reasoning": life["reasoning"],
                    "cache_read": life["cache_read"],
                    "calls": life["calls"],
                    "failed": life["failed"],
                    "avg_ttft_ms": round(day_row["ttft_ms"] / samples_ok) if samples_ok else None,
                    "tps": _tps(day_row),
                }
            )

        grand = {
            "input": sum(row["input"] for row in models),
            "output": sum(row["output"] for row in models),
            "reasoning": sum(row["reasoning"] for row in models),
            "calls": sum(row["calls"] for row in models),
            "failed": sum(row["failed"] for row in models),
        }
        return {
            "requests": requests,
            "failures": failures,
            "days": series,
            "models": models,
            "grand": grand,
            "samples": samples,
        }

    # -- persistence ------------------------------------------------------ #
    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            if isinstance(payload.get("days"), dict):
                self._state["days"] = payload["days"]
            if isinstance(payload.get("models"), dict):
                self._state["models"] = payload["models"]
            if isinstance(payload.get("samples"), list):
                self._state["samples"] = payload["samples"]
            if isinstance(payload.get("requests"), int):
                self._state["requests"] = payload["requests"]
            if isinstance(payload.get("failed_requests"), int):
                self._state["failed_requests"] = payload["failed_requests"]

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


def _tps(day_row: dict[str, Any]) -> int | None:
    measurable, _ms, rate = decode_window(day_row.get("decode_ms"), day_row.get("decode_tokens", 0), True)
    return rate if measurable else None


def _fresh_state() -> dict[str, Any]:
    return {"version": 1, "days": {}, "models": {}, "requests": 0, "failed_requests": 0, "samples": []}
