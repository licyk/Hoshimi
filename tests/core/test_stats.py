"""Usage accounting: one request becomes exactly one row, and a rate is only a rate when it is credible."""

from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

from hoshimi.core.stats import KEEP_DAYS, MIN_DECODE_MS, StatsStore, day_key, decode_window, window_tokens


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


def _chunks(ok: bool = True) -> list[dict[str, Any]]:
    if not ok:
        return [{"type": "text_delta", "text": "half"}, {"type": "error", "error": "boom"}]
    return [
        {"type": "text_delta", "text": "answer"},
        {"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 3}}},
        {"type": "finish", "reason": "stop"},
    ]


def test_turn_records_tokens_outcome_and_identity_once() -> None:
    store = StatsStore()
    turn = store.turn("m-1", effort="high", origin="responses")
    list(turn.wrap(_chunks()))
    # A second finish is a second look at the same turn, not a second turn.
    turn.finish()

    snap = store.snapshot()
    assert snap["requests"] == 1
    assert snap["failures"] == 0
    row = snap["models"][0]
    assert row["model"] == "m-1"
    assert (row["input"], row["output"], row["reasoning"]) == (10, 4, 3)
    assert row["calls"] == 1
    assert row["failed"] == 0
    sample = snap["samples"][0]
    assert sample["ok"] is True
    assert sample["effort"] == "high"
    assert sample["origin"] == "responses"
    assert snap["grand"] == {"input": 10, "output": 4, "reasoning": 3, "calls": 1, "failed": 0}


def test_error_chunk_and_exception_both_count_as_failed() -> None:
    failing = StatsStore()
    turn = failing.turn("m-1")
    list(turn.wrap(_chunks(ok=False)))
    snap = failing.snapshot()
    assert (snap["requests"], snap["failures"]) == (1, 1)
    assert snap["models"][0]["failed"] == 1

    raising = StatsStore()

    def boom() -> Any:
        raise RuntimeError("upstream went away")
        yield

    turn = raising.turn("m-1")
    try:
        list(turn.wrap(boom()))
    except RuntimeError:
        pass
    snap = raising.snapshot()
    assert (snap["requests"], snap["failures"]) == (1, 1)
    assert snap["models"][0]["failed"] == 1


def test_client_disconnect_is_a_served_call() -> None:
    store = StatsStore()
    turn = store.turn("m-1")
    stream: Generator[dict[str, Any], None, None] = turn.wrap(_chunks())
    next(stream)
    stream.close()

    snap = store.snapshot()
    assert (snap["requests"], snap["failures"]) == (1, 0)
    assert snap["models"][0]["failed"] == 0


def test_routing_failure_is_recorded_by_fail() -> None:
    store = StatsStore()
    store.turn("missing-1").fail()
    snap = store.snapshot()
    assert (snap["requests"], snap["failures"]) == (1, 1)
    assert snap["samples"][0]["model"] == "missing-1"


def test_a_slow_window_survives_to_the_rate() -> None:
    store = StatsStore()
    turn = store.turn("m-1")
    stream: Generator[dict[str, Any], None, None] = turn.wrap([{"type": "text_delta", "text": "answer"}])
    # One observed frame, then a real decode window: the rate must come out of
    # it rather than out of a millisecond of scheduling noise.
    next(stream)
    time.sleep(MIN_DECODE_MS / 1000 + 0.05)
    turn._observe({"type": "usage", "usage": {"prompt_tokens": 0, "completion_tokens": 50}})
    turn._observe({"type": "finish", "reason": "stop"})
    turn.finish()

    snap = store.snapshot()
    row = snap["models"][0]
    assert isinstance(row["tps"], int)
    assert 50 < row["tps"] <= 250
    assert isinstance(row["avg_ttft_ms"], int)


def test_days_are_pruned_but_lifetime_totals_are_not() -> None:
    store = StatsStore()
    for day in range(KEEP_DAYS + 10):
        state = store._state
        state["days"][f"2026-01-{day:02d}"] = {"total": 0, "models": {}}
    store.record(model="m-1", ok=True, input=1, output=1)

    with store._lock:
        assert len(store._state["days"]) == KEEP_DAYS
    assert store.snapshot()["requests"] == 1


def test_day_key_is_a_local_calendar_date() -> None:
    assert len(day_key(time.time())) == 10
    assert day_key(time.time()).count("-") == 2


def test_counters_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "stats.json"
    first = StatsStore(path)
    first.record(model="m-1", ok=True, input=7, output=5, reasoning=2)
    first.record(model="m-2", ok=False, input=1)

    second = StatsStore(path)
    snap = second.snapshot()
    assert (snap["requests"], snap["failures"]) == (2, 1)
    assert snap["grand"] == {"input": 8, "output": 5, "reasoning": 2, "calls": 2, "failed": 1}
    assert [row["model"] for row in snap["models"]] == ["m-1", "m-2"]


def test_corrupt_counters_start_over(tmp_path: Path) -> None:
    path = tmp_path / "stats.json"
    path.write_text("{not json", encoding="utf-8")
    store = StatsStore(path)
    snap = store.snapshot()
    assert snap["requests"] == 0
    assert snap["models"] == []


def test_rates_need_measurable_windows_not_just_calls() -> None:
    store = StatsStore()
    store.record(model="m-1", ok=True, input=1, output=9, decode_tokens=9, decode_ms=10, ttft_ms=4)
    row = store.snapshot()["models"][0]
    # A 10 ms window is noise: the call still counts, the speed does not.
    assert row["calls"] == 1
    assert row["tps"] is None
    assert row["avg_ttft_ms"] == 4
    assert row["failed"] == 0
