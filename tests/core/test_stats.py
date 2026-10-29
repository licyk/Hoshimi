"""Usage accounting: one request becomes exactly one row, and a rate is only a rate when it is credible."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Generator, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from hoshimi.core.db import DB_FILE_NAME, Database
from hoshimi.core.errors import ModelNotFoundError, ValidationError
from hoshimi.core.live import CLOSED, LiveHub
from hoshimi.core.providers.base import Account, Provider, ProviderError
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.registry import Registry
from hoshimi.core.stats import (
    KEEP_HOURLY_DAYS,
    MIN_DECODE_MS,
    RECENT_LIMIT,
    TPS_HIST,
    TTFT_HIST,
    StatsStore,
    day_key,
    decode_window,
    hist_add,
    hour_key,
    hour_start,
    percentile,
    short_error,
    window_tokens,
)


def test_decode_window_rejects_noise_and_phantom_speed() -> None:
    # 63 tokens inside a millisecond is a unit conversion, not a measurement.
    assert decode_window(1, 63, True) == (False, 0, None)
    assert decode_window(MIN_DECODE_MS - 1, 50, True) == (False, 0, None)
    assert decode_window(None, 50, True) == (False, 0, None)
    assert decode_window(0, 50, True) == (False, 0, None)
    # 300 tokens in a second is above what any station on this lane publishes.
    assert decode_window(1000, 300, True) == (False, 0, None)
    assert decode_window(0, 0, True) == (False, 0, None)
    assert decode_window(1000, 0, True) == (False, 0, None)
    # A failed call carries no speed at all.
    assert decode_window(1000, 100, False) == (False, 0, None)
    assert decode_window(1000, 100, True) == (True, 1000, 100)


def test_window_tokens_splits_the_seen_from_the_spent() -> None:
    # Reasoning that streamed past belongs to the window; reasoning spent
    # before the first frame does not, so it is subtracted rather than
    # credited to the answer's speed.
    assert window_tokens(100, 40, saw_reasoning=True) == 100
    assert window_tokens(100, 40, saw_reasoning=False) == 60
    assert window_tokens(10, 40, saw_reasoning=False) == 0


def test_report_histograms_stay_with_their_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows and histograms are read in one transaction: a call landing mid-report is in the next one."""
    from hoshimi.core import stats

    store = StatsStore()
    now = time.time()
    store.record(model="m", ok=True, ttft_ms=100, at=now)
    expected = store.report(now=now)
    accumulate = stats._accumulate_range
    added = False

    def record_during_report(keys, rows):
        nonlocal added
        if not added:
            added = True
            store.record(model="m", ok=True, ttft_ms=9000, at=now)
            store.flush()
        return accumulate(keys, rows)

    monkeypatch.setattr(stats, "_accumulate_range", record_during_report)
    assert store.report(now=now) == expected
    assert added and store.report(now=now)["totals"]["calls"] == 2


def _chunks(ok: bool = True) -> list[dict[str, Any]]:
    if not ok:
        return [{"type": "text_delta", "text": "half"}, {"type": "error", "error": "boom"}]
    return [
        {"type": "text_delta", "text": "answer"},
        {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 3}}},
        {"type": "finish", "reason": "stop"},
    ]


def _latest(store: StatsStore) -> dict[str, Any]:
    rows, _cursor = store.calls(limit=1)
    return rows[0]


def test_turn_records_tokens_outcome_and_identity_once() -> None:
    store = StatsStore()
    turn = store.turn("m-1", effort="high", origin="responses", stream=True)
    turn.routed("fake", "acct-2", 2)
    list(turn.wrap(_chunks()))
    # A second finish is a second look at the same turn, not a second turn.
    turn.finish()

    report = store.report("24h")
    assert (report["requests"], report["failures"]) == (1, 0)
    assert report["totals"] == {"input": 10, "output": 4, "reasoning": 3, "cache_read": 0, "cache_write": 0, "calls": 1, "failed": 0, "no_usage": 0}
    sample = _latest(store)
    assert sample["ok"] is True
    assert (sample["effort"], sample["origin"], sample["stream"]) == ("high", "responses", True)
    assert (sample["provider"], sample["account"], sample["attempts"]) == ("fake", "acct-2", 2)
    assert sample["finish_reason"] == "stop"
    assert sample["id"] == turn.id and sample["seq"] == 1
    assert [row["key"] for row in report["by_account"]] == ["fake/acct-2"]
    assert [row["key"] for row in report["by_provider"]] == ["fake"]
    assert [row["key"] for row in report["by_origin"]] == ["responses"]


def test_cache_reads_and_writes_are_counted() -> None:
    store = StatsStore()
    for details in ({"cached_tokens": 40, "cache_write_tokens": 25}, {"cached_tokens": 5, "cache_creation_tokens": 7}):
        turn = store.turn("m-1")
        list(turn.wrap([{"type": "text_delta", "text": "x"}, {"type": "usage", "usage": {"prompt_tokens": 100, "completion_tokens": 1, "prompt_tokens_details": details}}]))
    totals = store.report("7d")["totals"]
    assert (totals["input"], totals["cache_read"], totals["cache_write"]) == (200, 45, 32)
    assert _latest(store)["cache_write"] == 7


def test_error_chunk_and_exception_both_count_as_failed() -> None:
    failing = StatsStore()
    turn = failing.turn("m-1")
    list(turn.wrap(_chunks(ok=False)))
    report = failing.report("24h")
    assert (report["requests"], report["failures"]) == (1, 1)
    assert report["by_model"][0]["failed"] == 1
    sample = _latest(failing)
    assert (sample["error_code"], sample["error_message"]) == ("upstream_error", "boom")

    raising = StatsStore()

    def boom() -> Any:
        raise ProviderError(429, "the key sk-abcdefghijklmnop was refused: full body")
        yield

    turn = raising.turn("m-1")
    with pytest.raises(ProviderError):
        list(turn.wrap(boom()))
    report = raising.report("24h")
    assert (report["requests"], report["failures"]) == (1, 1)
    sample = _latest(raising)
    # The status, never the station's body.
    assert sample["error_code"] == "upstream_error"
    assert sample["error_message"] == "upstream returned HTTP 429"


def test_error_messages_are_short_and_masked() -> None:
    assert short_error("Bearer abcdef0123 rejected") == "Bearer *** rejected"
    assert short_error("token limit exceeded") == "token limit exceeded"
    assert "sk-" not in short_error("bad key sk-1234567890abcdef")
    assert "x" * 40 not in short_error("token " + "x" * 40)
    assert len(short_error("word " * 200)) <= 160
    assert short_error({"not": "text"}) == ""


def test_client_disconnect_is_a_served_call() -> None:
    store = StatsStore()
    turn = store.turn("m-1")
    stream: Generator[dict[str, Any], None, None] = turn.wrap(_chunks())  # type: ignore[assignment]
    next(stream)
    stream.close()

    report = store.report("24h")
    assert (report["requests"], report["failures"]) == (1, 0)
    assert store.live.active() == []


def test_routing_failure_is_recorded_by_fail() -> None:
    store = StatsStore()
    store.turn("missing-1").fail(ModelNotFoundError("Model 'missing-1' not found."))
    report = store.report("24h")
    assert (report["requests"], report["failures"]) == (1, 1)
    sample = _latest(store)
    assert sample["model"] == "missing-1"
    assert sample["error_code"] == "model_not_found"
    assert store.live.active() == []


def test_an_abandoned_stream_leaves_the_live_view() -> None:
    """A stream nobody ever started still ends its call, so the live list cannot hold ghosts."""
    store = StatsStore()
    turn = store.turn("m-1")
    stream = turn.wrap(iter(_chunks()))
    assert len(store.live.active()) == 1
    del stream
    deadline = time.monotonic() + 2
    while store.live.active() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert store.live.active() == []
    assert store.report("24h")["requests"] == 1


def test_a_slow_window_survives_to_the_rate() -> None:
    store = StatsStore()
    turn = store.turn("m-1")
    stream: Iterator[dict[str, Any]] = turn.wrap([{"type": "text_delta", "text": "answer"}])
    # One observed frame, then a real decode window: the rate must come out of
    # it rather than out of a millisecond of scheduling noise.
    next(stream)
    time.sleep(MIN_DECODE_MS / 1000 + 0.05)
    turn._observe({"type": "usage", "usage": {"prompt_tokens": 0, "completion_tokens": 50}})
    turn._observe({"type": "finish", "reason": "stop"})
    turn.finish()

    row = store.report("24h")["by_model"][0]
    assert isinstance(row["tps"], int)
    assert 50 < row["tps"] <= 250
    assert isinstance(row["ttft_avg_ms"], int)


def test_rates_need_measurable_windows_not_just_calls() -> None:
    store = StatsStore()
    store.record(model="m-1", ok=True, input=1, output=9, decode_tokens=9, decode_ms=10, ttft_ms=4)
    row = store.report("24h")["by_model"][0]
    # A 10 ms window is noise: the call still counts, the speed does not.
    assert row["calls"] == 1
    assert row["tps"] is None
    assert row["ttft_avg_ms"] == 4
    assert row["failed"] == 0


def test_percentiles_come_from_merged_histograms() -> None:
    hist: dict[str, int] = {}
    for value in range(100, 1100, 10):  # 100 values, 100 .. 1090 ms
        hist_add(hist, value, TTFT_HIST)
    p50 = percentile(hist, 0.5, TTFT_HIST)
    p95 = percentile(hist, 0.95, TTFT_HIST)
    assert p50 is not None and p95 is not None
    # Accurate to a bucket's width (15%).
    assert 595 * 0.85 <= p50 <= 595 * 1.15
    assert 1040 * 0.85 <= p95 <= 1040 * 1.15
    assert percentile({}, 0.5, TPS_HIST) is None

    # Over a range, the percentile is of the range — two hours merge.
    store = StatsStore()
    now = time.time()
    for index in range(20):
        store.record(model="m", ok=True, ttft_ms=100, at=now - 3600)
        store.record(model="m", ok=True, ttft_ms=2000 if index < 4 else 100, at=now)
    latency = store.report("24h", now=now)["latency"]
    assert latency["ttft_p50_ms"] is not None and 85 <= latency["ttft_p50_ms"] <= 115
    assert latency["ttft_p95_ms"] is not None and latency["ttft_p95_ms"] > 115


def test_series_are_contiguous_and_zero_filled() -> None:
    store = StatsStore()
    now = time.time()
    store.record(model="m", ok=True, input=5, output=1, at=now - 5 * 3600)
    store.record(model="m", ok=True, input=3, output=2, at=now)

    hourly = store.report("24h", now=now)
    assert hourly["granularity"] == "hour"
    assert len(hourly["series"]) == 24
    assert hourly["series"][-1]["input"] == 3
    assert hourly["series"][-6]["input"] == 5
    assert sum(point["calls"] for point in hourly["series"]) == 2
    assert hourly["series"][0]["start"] == hourly["since"]
    expected = datetime.fromtimestamp(now).astimezone().replace(minute=0, second=0, microsecond=0)
    assert datetime.fromisoformat(hourly["series"][-1]["start"]) == expected
    assert all(point["ttft_avg_ms"] is None for point in hourly["series"])

    store.record(model="m", ok=True, input=7, at=now - 3 * 86400)
    daily = store.report("7d", now=now)
    assert daily["granularity"] == "day"
    assert [point["start"] for point in daily["series"]] == [(datetime.fromtimestamp(now).astimezone().date() - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
    assert daily["series"][-4]["input"] == 7
    assert daily["totals"]["input"] == 15
    assert len(store.report("30d", now=now)["series"]) == 30
    assert len(store.report("90d", now=now)["series"]) == 90


def test_previous_period_is_the_span_just_before() -> None:
    store = StatsStore()
    now = time.time()
    store.record(model="m", ok=True, input=10, at=now - 10 * 86400)
    store.record(model="m", ok=True, input=1, at=now)
    report = store.report("7d", now=now)
    assert report["totals"]["input"] == 1
    assert report["previous"]["input"] == 10
    assert report["lifetime"]["input"] == 11


def test_hours_and_calls_are_pruned_but_days_and_lifetime_totals_are_not() -> None:
    store = StatsStore()
    store.keep_days = 2
    now = time.time()
    store.record(model="m-1", ok=True, input=1, at=now - (KEEP_HOURLY_DAYS + 5) * 86400)
    store.record(model="m-1", ok=True, input=1, at=now - 3 * 86400)
    store.record(model="m-1", ok=True, input=1, output=1, at=now)
    store._last_prune = float("-inf")  # retention runs at most once a minute; make it due now
    store.flush()

    oldest_hour = store.db.fetchone("SELECT MIN(bucket_start) AS start FROM usage_hourly")["start"]
    assert oldest_hour >= now - KEEP_HOURLY_DAYS * 86400
    assert store.db.fetchone("SELECT COUNT(*) AS n FROM usage_hist_hourly WHERE bucket_start < ?", (now - KEEP_HOURLY_DAYS * 86400,))["n"] == 0
    # Days are kept forever: the calendar still knows the oldest one.
    assert store.db.fetchone("SELECT COUNT(DISTINCT day) AS n FROM usage_daily")["n"] == 3
    # Calls are kept `keep_days` days.
    assert [row["input"] for row in store.calls()[0]] == [1]
    report = store.report("90d", now=now)
    assert report["requests"] == 3
    assert report["lifetime"]["input"] == 3


def test_day_and_hour_keys_are_local() -> None:
    assert len(day_key(time.time())) == 10
    assert day_key(time.time()).count("-") == 2
    assert hour_key(time.time())[10] == "T" and len(hour_key(time.time())) == 13
    now = time.time()
    assert datetime.fromtimestamp(hour_start(now)).astimezone() == datetime.fromtimestamp(now).astimezone().replace(minute=0, second=0, microsecond=0)


def test_calls_page_newest_first_with_filters() -> None:
    store = StatsStore()
    for index in range(7):
        store.record(model=f"m-{index % 2}", ok=index != 3, provider="p", account=f"a{index % 3}", origin="chat" if index < 5 else "playground")
    page, cursor = store.calls(limit=3)
    assert [row["seq"] for row in page] == [7, 6, 5]
    assert cursor == 5
    older, cursor = store.calls(limit=3, before=cursor)
    assert [row["seq"] for row in older] == [4, 3, 2]
    last, cursor = store.calls(limit=3, before=cursor)
    assert [row["seq"] for row in last] == [1] and cursor is None

    assert [row["seq"] for row in store.calls(model="m-1")[0]] == [6, 4, 2]
    assert [row["seq"] for row in store.calls(ok=False)[0]] == [4]
    assert [row["seq"] for row in store.calls(origin="playground")[0]] == [7, 6]
    assert [row["seq"] for row in store.calls(account="a0")[0]] == [7, 4, 1]
    assert [row["seq"] for row in store.calls(account="p/a0")[0]] == [7, 4, 1]
    assert store.calls(provider="other")[0] == []


def test_calls_span_the_whole_retention_not_a_ring() -> None:
    store = StatsStore()
    for _ in range(RECENT_LIMIT * 11):
        store.record(model="m", ok=True)
    seen: list[int] = []
    cursor = None
    while True:
        rows, cursor = store.calls(limit=200, before=cursor)
        seen += [row["seq"] for row in rows]
        if cursor is None:
            break
    assert seen == list(range(RECENT_LIMIT * 11, 0, -1))
    # The live snapshot holds only the newest few, from memory.
    assert len(store._recent) == RECENT_LIMIT


def test_calls_filter_by_time_and_text() -> None:
    store = StatsStore()
    base = time.time() - 3600
    store.record(model="glm-4.6", ok=True, provider="zhipu", account="a1", finish_reason="stop", at=base)
    store.record(model="kimi-k2", ok=False, provider="moonshot", account="b", error_code="rate_limited", error_message="Too many requests", at=base + 60)
    store.record(model="qwen3-coder", ok=True, provider="qwen", account="c", finish_reason="length", at=base + 120)
    assert [row["model"] for row in store.calls(since=base + 60)[0]] == ["qwen3-coder", "kimi-k2"]
    assert [row["model"] for row in store.calls(until=base + 60)[0]] == ["glm-4.6"]
    assert [row["model"] for row in store.calls(since=base + 60, until=base + 120)[0]] == ["kimi-k2"]
    assert [row["model"] for row in store.calls(q="TOO MANY")[0]] == ["kimi-k2"]
    assert [row["model"] for row in store.calls(q="Zhipu")[0]] == ["glm-4.6"]
    assert [row["model"] for row in store.calls(q="length")[0]] == ["qwen3-coder"]
    assert [row["model"] for row in store.calls(q="  RATE_ ")[0]] == ["kimi-k2"]
    # LIKE wildcards in the needle are literal characters.
    assert store.calls(q="%")[0] == []
    assert [row["model"] for row in store.calls(q="_")[0]] == ["kimi-k2"]
    page, cursor = store.calls(limit=1, q="o")
    assert len(page) == 1 and cursor == page[0]["seq"]


def test_concurrent_records_all_land_with_unique_seqs() -> None:
    store = StatsStore(write_interval=0.01)

    def burst() -> None:
        for _ in range(200):
            store.record(model="m", ok=True, input=1, provider="p", account="a")

    threads = [threading.Thread(target=burst) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    report = store.report("24h")
    assert report["totals"]["calls"] == report["requests"] == 1600
    assert report["totals"]["input"] == 1600
    assert store.today()["calls"] == 1600
    rows = store.db.fetchall("SELECT seq FROM calls ORDER BY seq")
    assert [row["seq"] for row in rows] == list(range(1, 1601))


def test_counters_survive_a_restart(tmp_path: Path) -> None:
    db = Database(tmp_path / DB_FILE_NAME)
    first = StatsStore(db=db)
    first.record(model="m-1", ok=True, input=7, output=5, reasoning=2)
    first.record(model="m-2", ok=False, input=1)
    first.close()
    db.close()

    second = StatsStore(db=Database(tmp_path / DB_FILE_NAME))
    report = second.report("7d")
    assert (report["requests"], report["failures"]) == (2, 1)
    assert report["lifetime"] == {"input": 8, "output": 5, "reasoning": 2, "cache_read": 0, "cache_write": 0, "calls": 2, "failed": 1, "no_usage": 0}
    assert [row["key"] for row in report["by_model"]] == ["m-1", "m-2"]
    assert second.today()["calls"] == 2
    second.record(model="m-3", ok=True)
    assert _latest(second)["seq"] == 3


def _stored_calls(store: StatsStore) -> int:
    """Rows actually in the database, read past the pending batch."""
    return store.db.fetchone("SELECT COUNT(*) AS n FROM calls")["n"]


def test_writes_are_batched_and_flushed(tmp_path: Path) -> None:
    store = StatsStore(tmp_path / "stats.json", write_interval=60)
    store.record(model="m", ok=True, input=1)  # the first write is immediate
    assert _stored_calls(store) == 1
    store.record(model="m", ok=True, input=1)
    store.record(model="m", ok=True, input=1)
    # Within the interval: pending, one timer waiting.
    assert _stored_calls(store) == 1
    assert store._timer is not None
    assert store.today()["calls"] == 3  # from memory
    # A read writes the batch first, so it never misses a recorded call.
    assert store.report("24h")["totals"]["calls"] == 3
    assert _stored_calls(store) == 3
    store.record(model="m", ok=True, input=1)
    store.flush()
    assert _stored_calls(store) == 4
    assert store._timer is None

    quick = StatsStore(write_interval=0.05)
    quick.record(model="m", ok=True)
    quick.record(model="m", ok=True)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _stored_calls(quick) != 2:
        time.sleep(0.02)
    assert _stored_calls(quick) == 2


def test_services_close_flushes(tmp_path: Path) -> None:
    from tests.conftest import make_services

    services = make_services()
    services.stats = StatsStore(tmp_path / "stats.json", write_interval=60)
    services.stats.record(model="m", ok=True)
    services.stats.record(model="m", ok=True)
    services.close()
    services.close()  # the lifespan and the serve command both close
    assert Database(tmp_path / DB_FILE_NAME).fetchone("SELECT COUNT(*) AS n FROM calls")["n"] == 2


def test_reset_clears_and_persists(tmp_path: Path) -> None:
    store = StatsStore(tmp_path / "stats.json", write_interval=3600)
    store.record(model="m", ok=True, input=4, provider="p", account="a")
    cleared = store.reset()
    assert datetime.fromisoformat(cleared)
    report = store.report("24h")
    assert report["requests"] == 0 and report["lifetime"]["calls"] == 0 and report["by_model"] == []
    assert store.calls()[0] == [] and store.today()["calls"] == 0 and store.days()["days"] == []
    for table in ("calls", "usage_hourly", "usage_daily", "usage_hist_hourly", "usage_hist_daily", "lifetime"):
        assert store.db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table
    # The cursor is never reused — not by this store, and not after a restart.
    store.record(model="m", ok=True)
    assert _latest(store)["seq"] == 2
    # Reset can discard a batch that live clients saw before it reached disk.
    assert store.record(model="m", ok=True)["seq"] == 3
    assert store.db.fetchone("SELECT seq FROM sqlite_sequence WHERE name = 'calls'")["seq"] == 2
    store.reset()
    store.close()
    assert StatsStore(tmp_path / "stats.json").record(model="m", ok=True)["seq"] == 4


def test_version_1_files_are_imported_once(tmp_path: Path) -> None:
    path = tmp_path / "stats.json"
    today = datetime.now().astimezone().date().isoformat()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "days": {
                    today: {
                        "total": 15,
                        "models": {
                            "m-1": {
                                "input": 10,
                                "output": 5,
                                "reasoning": 1,
                                "cache_read": 2,
                                "calls": 3,
                                "failed": 1,
                                "ttft_ms": 900,
                                "ttft_samples": 3,
                                "decode_ms": 2000,
                                "decode_tokens": 100,
                            }
                        },
                    }
                },
                "models": {"m-1": {"input": 40, "output": 20, "reasoning": 1, "cache_read": 2, "calls": 9, "failed": 1}},
                "requests": 9,
                "failed_requests": 1,
                "samples": [
                    {"at": "2026-10-20T10:00:00+08:00", "model": "m-1", "ok": True, "input": 10, "ttft_ms": 300, "tps": 50, "origin": "chat"},
                    {"at": "2026-10-20T11:00:00+08:00", "model": "m-1", "ok": False},
                ],
            }
        ),
        encoding="utf-8",
    )

    store = StatsStore(path)
    assert not path.exists() and (tmp_path / "stats.json.migrated").is_file()
    report = store.report("7d")
    assert (report["requests"], report["failures"]) == (9, 1)
    assert report["lifetime"]["input"] == 40 and report["lifetime"]["cache_write"] == 0
    assert report["totals"]["input"] == 10 and report["totals"]["calls"] == 3
    assert report["by_model"][0]["ttft_avg_ms"] == 300
    assert report["by_model"][0]["tps"] == 50
    # Provider, account and origin were never recorded: not guessed.
    assert report["by_provider"] == [] and report["by_account"] == [] and report["by_origin"] == []
    # A version 1 day has no hours: the day report says so instead of drawing zeros as data.
    day = store.report("day")
    assert day["hourly"] is False and day["totals"]["calls"] == 3 and sum(point["calls"] for point in day["series"]) == 0
    assert store.today()["calls"] == 3
    rows, _ = store.calls()
    assert [row["seq"] for row in rows] == [2, 1]
    assert rows[1]["provider"] == "" and rows[1]["cache_write"] == 0 and rows[1]["id"] == "c1"
    assert rows[1]["ts"] == datetime.fromisoformat("2026-10-20T10:00:00+08:00").timestamp()
    store.record(model="m-1", ok=True)
    assert _latest(store)["seq"] == 3
    store.close()

    # Once: a file reappearing beside a database that already counts is not added again.
    (tmp_path / "stats.json.migrated").rename(path)
    again = StatsStore(path)
    assert again.report("7d")["requests"] == 10 and path.is_file()


def test_version_2_files_keep_hours_histograms_and_the_cursor(tmp_path: Path) -> None:
    now = time.time()
    identity = "m-2\tp\ta\tchat"
    row = {
        "input": 5,
        "output": 3,
        "calls": 2,
        "failed": 0,
        "ttft_ms": 400,
        "ttft_count": 2,
        "decode_ms": 2000,
        "decode_tokens": 100,
        "last_at": now,
        "ttft_hist": {"20": 2},
        "tps_hist": {"40": 1},
    }
    path = tmp_path / "stats.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "seq": 41,
                "requests": 2,
                "failed_requests": 0,
                "lifetime": {
                    "models": {"m-2": {"input": 5, "output": 3, "calls": 2, "last_at": now}},
                    "providers": {"p": {"input": 5, "calls": 2}},
                    "accounts": {"p/a": {"input": 5, "calls": 2, "last_at": now}},
                    "origins": {"chat": {"calls": 2}},
                },
                "days": {day_key(now): {identity: row}},
                "hours": {hour_key(now): {identity: row}},
                "samples": [{"seq": 40, "id": "abc", "at": "x", "ts": now - 10, "model": "m-2", "provider": "p", "account": "a", "ok": True, "input": 5}],
            }
        ),
        encoding="utf-8",
    )
    store = StatsStore(path)
    assert (tmp_path / "stats.json.migrated").is_file()
    report = store.report("24h", now=now)
    assert report["totals"]["calls"] == 2 and report["series"][-1]["calls"] == 2
    assert report["latency"]["ttft_avg_ms"] == 200
    assert report["latency"]["ttft_p50_ms"] is not None and report["latency"]["tps_p50"] is not None
    assert [row["key"] for row in report["by_account"]] == ["p/a"]
    assert store.report("day", now=now)["hourly"] is True
    assert store.last_used() == {"p/a": now}
    assert [row["id"] for row in store.calls()[0]] == ["abc"]
    # AUTOINCREMENT continues after the file's cursor, not after its last kept row.
    assert store.record(model="m", ok=True)["seq"] == 42


def test_corrupt_counters_start_over(tmp_path: Path) -> None:
    path = tmp_path / "stats.json"
    path.write_text("{not json", encoding="utf-8")
    store = StatsStore(path)
    report = store.report("7d")
    assert report["requests"] == 0
    assert report["by_model"] == []
    assert not path.exists()
    assert (tmp_path / "stats.json.corrupt").read_text(encoding="utf-8") == "{not json"


# -- the calendar day -------------------------------------------------------- #
def _midnight(day: date) -> float:
    return datetime.combine(day, datetime.min.time()).astimezone().timestamp()


def test_a_day_report_covers_every_local_hour_of_that_day() -> None:
    store = StatsStore()
    now = time.time()
    today = datetime.fromtimestamp(now).astimezone().date()
    yesterday = today - timedelta(days=1)
    noon = _midnight(yesterday) + 12 * 3600
    store.record(model="m", ok=True, input=4, provider="p", account="a", ttft_ms=300, at=noon)
    store.record(model="m", ok=True, input=6, at=_midnight(yesterday - timedelta(days=1)) + 12 * 3600)

    report = store.report("day", now=now, date=yesterday.isoformat())
    assert (report["range"], report["granularity"], report["date"], report["hourly"]) == ("day", "hour", yesterday.isoformat(), True)
    assert datetime.fromisoformat(report["since"]).timestamp() == _midnight(yesterday)
    assert datetime.fromisoformat(report["until"]).timestamp() == _midnight(today)
    assert report["series"][0]["start"] == report["since"]
    assert len(report["series"]) in (23, 24, 25)
    assert [datetime.fromisoformat(point["start"]).timestamp() for point in report["series"] if point["calls"]] == [hour_start(noon)]
    assert report["totals"]["input"] == 4 and report["previous"]["input"] == 6
    assert report["latency"]["ttft_avg_ms"] == 300
    assert [row["key"] for row in report["by_account"]] == ["p/a"]
    assert datetime.fromisoformat(report["server_time"])

    for bad in ((today + timedelta(days=1)).isoformat(), "yesterday", "2026-13-01"):
        with pytest.raises(ValidationError):
            store.report("day", now=now, date=bad)
    assert store.report("day", now=now)["date"] == today.isoformat()


def test_today_compares_with_yesterday_up_to_the_same_time() -> None:
    store = StatsStore()
    today = datetime.now().astimezone().date()
    yesterday = _midnight(today - timedelta(days=1))
    now = _midnight(today) + 2 * 3600 + 30 * 60  # 02:30 today
    store.record(model="m", ok=True, input=1, at=_midnight(today) + 600)
    store.record(model="m", ok=True, input=10, at=yesterday + 600)  # 00:10 yesterday: before the same time
    store.record(model="m", ok=True, input=100, at=yesterday + 2 * 3600 + 45 * 60)  # 02:45 yesterday: after it
    store.record(model="m", ok=True, input=1000, at=yesterday + 20 * 3600)
    report = store.report("day", now=now)
    assert report["totals"]["input"] == 1
    assert report["previous"]["input"] == 10
    # Past the calls' retention, the hours stand in (to the hour holding the cut).
    store.keep_days = 1
    assert store.report("day", now=now)["previous"]["input"] == 110
    # A past day compares with the whole day before it.
    full = store.report("day", now=now, date=(today - timedelta(days=1)).isoformat())
    assert full["totals"]["input"] == 1110


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset to switch the local zone")
# Future days on purpose: hours older than KEEP_HOURLY_DAYS would be pruned on the first write.
@pytest.mark.parametrize(("zone", "day", "hours"), [("America/New_York", "2027-03-14", 23), ("America/New_York", "2026-11-01", 25), ("Asia/Shanghai", "2027-03-14", 24)])
def test_a_day_has_as_many_hours_as_the_clock_gave_it(monkeypatch: pytest.MonkeyPatch, zone: str, day: str, hours: int) -> None:
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        store = StatsStore()
        start = _midnight(date.fromisoformat(day))
        for offset in range(hours):
            store.record(model="m", ok=True, input=1, at=start + offset * 3600 + 60)
        report = store.report("day", now=start + 5 * 86400, date=day)
        assert len(report["series"]) == hours
        assert [point["calls"] for point in report["series"]] == [1] * hours
        assert report["totals"]["calls"] == hours
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_days_lists_every_day_with_usage() -> None:
    store = StatsStore()
    now = time.time()
    assert store.days(now=now) == {"days": [], "first": None, "today": day_key(now)}
    store.record(model="m", ok=True, input=3, output=2, at=now - 3 * 86400)
    store.record(model="m", ok=False, input=1, at=now)
    store.record(model="m", ok=True, input=1, at=now)
    listed = store.days(now=now)
    assert [row["date"] for row in listed["days"]] == [day_key(now - 3 * 86400), day_key(now)]
    assert listed["days"][0] == {"date": day_key(now - 3 * 86400), "calls": 1, "tokens": 5, "failed": 0}
    assert listed["days"][1]["calls"] == 2 and listed["days"][1]["failed"] == 1
    assert listed["first"] == day_key(now - 3 * 86400)


def test_today_rollups_and_last_used() -> None:
    store = StatsStore()
    store.record(model="m", ok=True, input=3, output=1, provider="p", account="a")
    store.record(model="m", ok=False, input=2, provider="p", account="b")
    store.record(model="m", ok=False, input=9, provider="", account="")
    rollups = store.today_rollups()
    assert rollups["providers"]["p"]["input"] == 5
    assert rollups["accounts"]["p/a"]["calls"] == 1 and rollups["accounts"]["p/b"]["failed"] == 1
    assert set(store.last_used()) == {"p/a", "p/b"}
    assert store.today()["calls"] == 3


# -- routing ----------------------------------------------------------------- #


class RotatingProvider(Provider):
    """The first account is out of allowance; the second answers."""

    id = "rot"

    def __init__(self) -> None:
        super().__init__(httpx.Client(), [Account({"id": "first", "api_key": "a"}, provider_id="rot"), Account({"id": "second", "api_key": "b"}, provider_id="rot")])

    def fallback_models(self) -> list[str]:
        return ["m"]

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if account.id == "first":
            raise_for_status(httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "quota exhausted"}}), provider=self.id)
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "finish", "reason": "stop"}


def test_on_route_names_the_account_that_answered_after_a_rotation() -> None:
    routes: list[tuple[str, str, int]] = []
    registry = Registry({"rot": RotatingProvider()})
    chunks = list(registry.stream("rot/m", {"messages": []}, on_route=lambda *route: routes.append(route)))
    assert routes == [("rot", "second", 2)]
    # Observing the route changes nothing about the stream.
    assert chunks == list(Registry({"rot": RotatingProvider()}).stream("rot/m", {"messages": []}))

    store = StatsStore()
    turn = store.turn("rot/m", provider=registry.provider_hint("rot/m"))
    list(turn.wrap(Registry({"rot": RotatingProvider()}).stream("rot/m", {"messages": []}, on_route=turn.routed)))
    sample = _latest(store)
    assert (sample["provider"], sample["account"], sample["attempts"]) == ("rot", "second", 2)


# -- live -------------------------------------------------------------------- #


def test_turn_progress_is_throttled_and_phase_changes_are_not() -> None:
    store = StatsStore()

    async def run() -> list[dict[str, Any]]:
        subscription, snapshot = store.subscribe(asyncio.get_running_loop())
        assert snapshot["type"] == "snapshot" and snapshot["active"] == []

        def work() -> None:
            turn = store.turn("m", origin="chat", stream=True)
            chunks = [{"type": "reasoning_delta", "text": "think" * 4}] + [{"type": "text_delta", "text": "abcd"}] * 50
            list(turn.wrap(chunks + [{"type": "usage", "usage": {"prompt_tokens": 1, "completion_tokens": 50}}, {"type": "finish", "reason": "stop"}]))

        await asyncio.to_thread(work)
        events = []
        while True:
            event = await asyncio.wait_for(subscription.queue.get(), 1)
            events.append(event)
            if event["type"] == "finished":
                return events

    events = asyncio.run(run())
    kinds = [event["type"] for event in events]
    assert kinds[0] == "started" and kinds[-1] == "finished"
    phases = [event["call"]["phase"] for event in events if event["type"] == "progress"]
    assert phases[:2] == ["reasoning", "answering"]
    # 51 content chunks in a few milliseconds: two phase changes, not 51 frames.
    assert len(phases) <= 4
    finished = events[-1]
    assert finished["record"]["id"] == finished["call_id"] == events[0]["call"]["id"]
    assert finished["today"]["calls"] == 1


def test_live_view_estimates_tokens_from_characters() -> None:
    store = StatsStore()
    turn = store.turn("m")
    turn._observe({"type": "reasoning_delta", "text": "x" * 40})
    turn._observe({"type": "text_delta", "text": "y" * 80})
    view = turn.live_view()
    assert (view["phase"], view["reasoning_chars"], view["output_chars"], view["est_output_tokens"]) == ("answering", 40, 80, 30)
    assert view["ttft_ms"] is not None and view["tps"] is None  # not yet MIN_DECODE_MS of decoding
    turn.finish()


def test_publishing_crosses_threads_and_a_slow_subscriber_is_dropped() -> None:
    hub = LiveHub(queue_limit=3)

    async def run() -> None:
        subscription, active = hub.subscribe(asyncio.get_running_loop())
        assert active == []
        views = {"id": "c1"}

        def publish() -> None:
            hub.begin("c1", lambda: dict(views))
            for _ in range(10):
                hub.progress("c1")

        thread = threading.Thread(target=publish)
        thread.start()
        await asyncio.to_thread(thread.join)
        await asyncio.sleep(0.05)
        assert subscription.dropped
        assert subscription.queue.qsize() == 3
        # The next publish removes it from the hub entirely.
        hub.progress("c1")
        assert hub.subscribers == 0

        second, active = hub.subscribe(asyncio.get_running_loop())
        assert [row["id"] for row in active] == ["c1"]
        hub.close()
        assert await asyncio.wait_for(second.queue.get(), 1) is CLOSED
        assert hub.subscribers == 0

    asyncio.run(run())


def test_snapshot_and_finished_events_never_overlap() -> None:
    """A call recorded concurrently with a subscribe is in the snapshot or in the stream, never both."""
    store = StatsStore()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        stop = threading.Event()

        def churn() -> None:
            while not stop.is_set():
                store.turn("m").finish()
                time.sleep(0.0005)

        worker = threading.Thread(target=churn)
        worker.start()
        try:
            for _ in range(10):
                subscription, snapshot = store.subscribe(loop)
                seen = {row["id"] for row in snapshot["recent"]}
                await asyncio.sleep(0.005)
                store.live.unsubscribe(subscription)
                while not subscription.queue.empty():
                    event = subscription.queue.get_nowait()
                    if event["type"] == "finished":
                        assert event["call_id"] not in seen
        finally:
            stop.set()
            worker.join()

    asyncio.run(run())
