"""Availability probing: ask, because the listing will not say.

The free lane's gateway does not disclose which models this egress may use —
the listing is a flat id list, and exclusion only appears when a request is
refused with a region error. Availability is therefore established with the
smallest request that can produce a verdict.

The verdicts are deliberately asymmetric. Only a refusal that *names this model*
moves it out of the catalogue (`unavailable`); a 5xx, a quota window or a
transport failure says nothing about the model and must not remove it, because
a stale entry costs one retryable turn while a model that vanished on a hiccup
costs a re-probe the user has no visibility into.

Concurrency stays low on purpose: the lanes account quota per session and
answer 429 with growing retry-after, so a wide burst would throttle the very
egress whose availability we are establishing.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from hoshimi.core.db import Database, open_store_database, retire_legacy_file
from hoshimi.core.net.client import NetworkClient
from hoshimi.core.providers.base import Provider, QuotaExceeded

STATE_AVAILABLE = "available"
STATE_REGION_BLOCKED = "region-blocked"
STATE_UNAVAILABLE = "unavailable"
STATE_THROTTLED = "throttled"
STATE_UNKNOWN = "unknown"
STATES = (
    STATE_AVAILABLE,
    STATE_REGION_BLOCKED,
    STATE_UNAVAILABLE,
    STATE_THROTTLED,
    STATE_UNKNOWN,
)

logger = logging.getLogger(__name__)

PROBE_FILE_NAME = "probe.json"
"""Where the verdicts lived before ``hoshimi.db``; imported once, then renamed ``probe.json.migrated``."""

PROBE_AT_KEY = "probe.at"
"""The ``meta`` key holding when the last round finished."""

PING_PROMPT = "ping"
PING_MAX_TOKENS = 64
DEFAULT_TIMEOUT = 45.0
DEFAULT_CONCURRENCY = 2

# Chunk kinds that prove the model answered.
_DELTA_KINDS = frozenset({"text_delta", "reasoning_delta", "tool_call_start"})

# A refusal whose whole meaning is the identifier in the body we sent.
ROUTING_REFUSAL_STATUS = frozenset({400, 404, 422})
# `type: "RegionError"`, or the words a station uses for an egress it will not serve.
_REGION_RE = re.compile(r"RegionError|not available in your country|not available in your region|\bregion\b", re.IGNORECASE)
# A refusal that names the model rather than the gateway's own trouble.
_UNAVAILABLE_RE = re.compile(r"unavailable|not supported|no such model|unknown model|invalid model", re.IGNORECASE)
# 5xx is the gateway's own trouble; its reason phrase ("Service Unavailable") is
# never a verdict about this model, however tempting the words look.
_GW_TROUBLE_RE = re.compile(r"service unavailable|bad gateway|gateway timeout", re.IGNORECASE)


def ping_body(model: str) -> dict[str, Any]:
    """The smallest chat-shaped request that produces a verdict.

    Chat-shaped because every wire is reached from it: the providers that speak
    Responses or Messages translate this body themselves.
    """
    return {
        "model": model,
        "messages": [{"role": "user", "content": PING_PROMPT}],
        "stream": True,
        "max_tokens": PING_MAX_TOKENS,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _detail(value: Any) -> str:
    return str(value)[:200]


def _state_from_message(message: str, status: Any) -> str:
    """Map one refusal onto a verdict (see the module note on asymmetry)."""
    if _REGION_RE.search(message):
        return STATE_REGION_BLOCKED
    gateway_trouble = isinstance(status, int) and status >= 500
    if not gateway_trouble and not _GW_TROUBLE_RE.search(message) and _UNAVAILABLE_RE.search(message):
        return STATE_UNAVAILABLE
    if isinstance(status, int) and status in ROUTING_REFUSAL_STATUS:
        return STATE_UNAVAILABLE
    return STATE_UNKNOWN


def state_from_error(exc: BaseException) -> tuple[str, str]:
    """Classify a raised failure as (state, detail)."""
    message = _detail(getattr(exc, "message", None) or exc)
    if isinstance(exc, QuotaExceeded):
        return STATE_THROTTLED, message
    status = getattr(exc, "status", None)
    if not isinstance(status, int):
        status = getattr(exc, "http_status", None)
    return _state_from_message(message, status), message


def probe_model(provider: Provider, model: str) -> dict[str, Any]:
    """Ask one station for one model, once. Never raises: a failure is a verdict."""
    started = time.monotonic()
    first_delta: float | None = None
    tokens = 0
    stream: Iterator[dict[str, Any]] | None = None
    try:
        stream = provider.stream(model, ping_body(model))
        for chunk in stream:
            if not isinstance(chunk, dict):
                continue
            kind = chunk.get("type")
            if kind == "error":
                message = _detail(chunk.get("error") or "upstream error")
                return _result(_state_from_message(message, None), started, detail=message)
            if kind in _DELTA_KINDS and first_delta is None:
                first_delta = time.monotonic()
            if kind == "usage":
                # A reasoning model can spend the whole ping on thinking and
                # answer with usage alone: tokens generated are tokens answered.
                tokens = _token_count(chunk.get("usage"))
        if first_delta is None and tokens <= 0:
            return _result(STATE_UNKNOWN, started, detail="the stream carried no content")
        return _result(STATE_AVAILABLE, started, ttft=first_delta)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        state, detail = state_from_error(exc)
        return _result(state, started, detail=detail)
    finally:
        closer = getattr(stream, "close", None)
        if callable(closer):
            # Abandons an open upstream response mid-stream; httpx closes it.
            closer()


def _token_count(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    value = usage.get("completion_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _result(state: str, started: float, *, detail: str | None = None, ttft: float | None = None) -> dict[str, Any]:
    now = time.monotonic()
    out: dict[str, Any] = {
        "state": state,
        "latency_ms": int((now - started) * 1000),
        "at": _now(),
    }
    if ttft is not None:
        out["ttft_ms"] = int((ttft - started) * 1000)
    if detail:
        out["detail"] = detail
    return out


def _clone(provider: Provider, client: httpx.Client) -> Provider:
    """A probe-scoped twin: same accounts, its own client with a short timeout.

    The shared client's timeout is sized for real turns; a probe that waits
    minutes for an answer is not an answer.
    """
    clone = type(provider)(client, provider.accounts, provider.options)
    clone.max_retries = getattr(provider, "max_retries", 1)  # type: ignore[attr-defined]
    return clone


def probe_catalog(
    targets: list[tuple[Provider, str]],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
    on_result: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Probe a whole list with a bounded fan-out; returns model id → verdict."""
    results: dict[str, dict[str, Any]] = {}
    if not targets:
        return results
    lock = threading.Lock()
    client = httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0)),
        follow_redirects=True,
        http2=False,
    )
    cursor = 0

    def worker() -> None:
        nonlocal cursor
        while True:
            with lock:
                if cursor >= len(targets):
                    return
                provider, model = targets[cursor]
                cursor += 1
            try:
                if isinstance(provider.client, NetworkClient):
                    with NetworkClient(provider=provider.id, network=provider.client.policy, timeout=timeout) as probe_client:
                        result = probe_model(_clone(provider, probe_client), model)
                else:
                    result = probe_model(_clone(provider, client), model)
            except Exception as exc:  # noqa: BLE001 - cloning can fail on a custom provider
                result = _result(STATE_UNKNOWN, time.monotonic(), detail=_detail(exc))
            with lock:
                results[model] = result
            if on_result is not None:
                on_result(model, result)

    workers = max(1, min(concurrency, len(targets)))
    threads = [threading.Thread(target=worker, name=f"probe-{index}", daemon=True) for index in range(workers)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        client.close()
    return results


class ProbeStore:
    """The last verdict per model, shared by `/api/v1/probe` and the catalogue.

    Verdicts are read from the ``probe_results`` table once and then served
    from memory; a finished round writes them back. ``db`` is the gateway's
    shared database; without one, ``path`` (the old ``probe.json`` location)
    opens ``hoshimi.db`` beside it, and nothing at all keeps them in memory.
    """

    def __init__(self, path: Path | None = None, *, db: Database | None = None):
        self._lock = threading.Lock()
        self._results: dict[str, dict[str, Any]] = {}
        self._at: str | None = None
        self._thread: threading.Thread | None = None
        self._db, self._owns_db = open_store_database(path, db)
        if path is not None:
            self._import_legacy(path)
        self._load()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "at": self._at,
                "results": {model: dict(row) for model, row in self._results.items()},
            }

    def state_for(self, model: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._results.get(model)
            return dict(row) if row is not None else None

    def start(
        self,
        targets: list[tuple[Provider, str]],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> bool:
        """Start a round in the background; False when one is already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            thread = threading.Thread(
                target=self._run,
                args=(targets, concurrency, timeout),
                name="probe-round",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return True

    def close(self) -> None:
        if self._owns_db:
            self._db.close()

    def _run(self, targets: list[tuple[Provider, str]], concurrency: int, timeout: float) -> None:
        probe_catalog(targets, concurrency=concurrency, timeout=timeout, on_result=self._record)
        with self._lock:
            self._at = _now()
        self._save()

    def _record(self, model: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._results[model] = dict(result)

    # -- persistence ------------------------------------------------------ #
    def _load(self) -> None:
        results = {}
        for row in self._db.fetchall("SELECT model, result FROM probe_results"):
            verdict = _verdict(row["result"])
            if verdict is not None:
                results[str(row["model"])] = verdict
        with self._lock:
            self._results = results
            self._at = self._db.get_meta(PROBE_AT_KEY)

    def _save(self) -> None:
        with self._lock:
            rows = [(model, json.dumps(row, ensure_ascii=False)) for model, row in self._results.items()]
            at = self._at
        if self._db.closed:
            return
        try:
            self._write(rows, at)
        except sqlite3.Error as exc:
            # A verdict the disk refused is still in memory for this process.
            logger.warning("could not save probe verdicts: %s", exc)

    def _write(self, rows: list[tuple[str, str]], at: str | None) -> None:
        with self._db.transaction() as conn:
            conn.executemany("INSERT INTO probe_results (model, result) VALUES (?, ?) ON CONFLICT(model) DO UPDATE SET result = excluded.result", rows)
            if at is not None:
                conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (PROBE_AT_KEY, at))

    def _import_legacy(self, path: Path) -> None:
        """Bring an old ``probe.json`` into a database without verdicts, once, then rename it aside."""
        if not path.is_file() or self._db.fetchone("SELECT 1 FROM probe_results LIMIT 1") is not None or self._db.get_meta(PROBE_AT_KEY) is not None:
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("could not read %s: %s", path, exc)
            return
        except ValueError as exc:
            retire_legacy_file(path, "corrupt")
            logger.warning("%s is unreadable (%s); probe verdicts start over", path, exc)
            return
        results = payload.get("results") if isinstance(payload, dict) else None
        rows = [(str(model), json.dumps(row, ensure_ascii=False)) for model, row in results.items() if isinstance(row, dict) and isinstance(row.get("state"), str)] if isinstance(results, dict) else []
        at = payload.get("at") if isinstance(payload, dict) and isinstance(payload.get("at"), str) else None
        try:
            self._write(rows, at)
        except sqlite3.Error as exc:
            logger.warning("could not import %s: %s", path, exc)
            return
        retire_legacy_file(path, "migrated")
        logger.info("Imported %s into %s: %d verdicts", path.name, self._db.path, len(rows))


def _verdict(raw: Any) -> dict[str, Any] | None:
    try:
        row = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return row if isinstance(row, dict) and isinstance(row.get("state"), str) else None
