"""Availability probing: verdicts, fan-out and the store that keeps them."""

from __future__ import annotations

import time
from typing import Any

import httpx

from hoshimi.core.probe import (
    STATE_AVAILABLE,
    STATE_REGION_BLOCKED,
    STATE_THROTTLED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    ProbeStore,
    probe_catalog,
    probe_model,
    state_from_error,
)
from hoshimi.core.providers.base import Account, Provider, ProviderError, QuotaExceeded
from hoshimi.core.registry import Registry


class ProbeProvider(Provider):
    """Serves each model from its own canned outcome, without touching the network."""

    id = "probe"
    display_name = "Probe"
    probeable = True

    def __init__(self, client: httpx.Client, accounts: list[Account], options: dict[str, Any] | None = None) -> None:
        super().__init__(client, accounts, options)
        self.outcomes: dict[str, Any] = dict((options or {}).get("outcomes") or {})

    def fallback_models(self) -> list[str]:
        return list(self.outcomes)

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Any:
        outcome = self.outcomes.get(model, {})
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome.get("delay"):
            time.sleep(float(outcome["delay"]))
        if "error_chunk" in outcome:
            yield {"type": "error", "error": outcome["error_chunk"]}
            return
        if outcome.get("empty"):
            yield {"type": "finish", "reason": "stop"}
            return
        if outcome.get("usage_only"):
            # A reasoning model that spent the whole ping on thinking.
            yield {"type": "usage", "usage": {"completion_tokens": 64}}
            yield {"type": "finish", "reason": "length"}
            return
        yield {"type": "text_delta", "text": "pong"}
        yield {"type": "finish", "reason": "stop"}


def _provider(outcomes: dict[str, Any]) -> ProbeProvider:
    return ProbeProvider(httpx.Client(), [Account({"id": "a"}, provider_id="probe")], {"outcomes": outcomes})


# -- verdicts --------------------------------------------------------------- #
def test_a_delta_stream_is_available() -> None:
    provider = _provider({"m": {}})
    verdict = probe_model(provider, "m")
    assert verdict["state"] == STATE_AVAILABLE
    assert verdict["latency_ms"] >= 0
    assert verdict["ttft_ms"] is not None
    assert "detail" not in verdict


def test_tokens_generated_without_a_delta_still_answer() -> None:
    verdict = probe_model(_provider({"m": {"usage_only": True}}), "m")
    assert verdict["state"] == STATE_AVAILABLE
    assert "ttft_ms" not in verdict


def test_a_stream_that_says_nothing_is_unknown() -> None:
    verdict = probe_model(_provider({"m": {"empty": True}}), "m")
    assert verdict["state"] == STATE_UNKNOWN
    assert "no content" in verdict["detail"]


def test_an_in_stream_refusal_is_read_for_what_it_names() -> None:
    verdict = probe_model(_provider({"m": {"error_chunk": "model unavailable on this station"}}), "m")
    assert verdict["state"] == STATE_UNAVAILABLE
    assert "unavailable" in verdict["detail"]
    # …and one that names nothing still reports, without removing the model.
    verdict = probe_model(_provider({"m": {"error_chunk": "boom"}}), "m")
    assert verdict["state"] == STATE_UNKNOWN


def test_raised_failures_map_onto_verdicts() -> None:
    assert state_from_error(QuotaExceeded(429, "free tier exhausted"))[0] == STATE_THROTTLED
    assert state_from_error(ProviderError(404, "no such model"))[0] == STATE_UNAVAILABLE
    assert state_from_error(ProviderError(400, "bad request"))[0] == STATE_UNAVAILABLE
    # A gateway's own trouble must never read as a verdict about this model.
    assert state_from_error(ProviderError(503, "Service Unavailable"))[0] == STATE_UNKNOWN
    # A credential problem is not a statement about the model either.
    assert state_from_error(ProviderError(403, "FreeTierError"))[0] == STATE_UNKNOWN
    assert state_from_error(ProviderError(403, "not available in your country"))[0] == STATE_REGION_BLOCKED


def test_probe_model_never_raises() -> None:
    provider = _provider({"m": RuntimeError("connection reset")})
    verdict = probe_model(provider, "m")
    assert verdict["state"] == STATE_UNKNOWN
    assert "connection reset" in verdict["detail"]


# -- a round ---------------------------------------------------------------- #
def test_a_round_probes_every_target_with_a_bounded_fan_out() -> None:
    provider = _provider({"a": {"delay": 0.05}, "b": {}, "c": {"empty": True}})
    seen: list[str] = []
    results = probe_catalog(
        [(provider, "a"), (provider, "b"), (provider, "c")],
        concurrency=2,
        timeout=5.0,
        on_result=lambda model, _row: seen.append(model),
    )
    assert set(results) == {"a", "b", "c"}
    assert sorted(seen) == ["a", "b", "c"]
    assert results["a"]["state"] == STATE_AVAILABLE
    assert results["b"]["state"] == STATE_AVAILABLE
    assert results["c"]["state"] == STATE_UNKNOWN


def test_an_empty_round_is_a_no_op() -> None:
    assert probe_catalog([]) == {}


# -- the store --------------------------------------------------------------- #
def test_a_round_runs_in_the_background_and_keeps_its_verdicts() -> None:
    store = ProbeStore()
    provider = _provider({"m": {"delay": 0.1}})
    assert store.start([(provider, "m")], concurrency=1, timeout=5.0) is True
    # Rounds do not stack while one is alive.
    assert store.start([(provider, "m")], concurrency=1, timeout=5.0) is False
    deadline = time.time() + 5
    while store.snapshot()["running"] and time.time() < deadline:
        time.sleep(0.02)
    snapshot = store.snapshot()
    assert snapshot["running"] is False
    assert snapshot["at"] is not None
    assert snapshot["results"]["m"]["state"] == STATE_AVAILABLE
    assert store.state_for("m")["state"] == STATE_AVAILABLE
    assert store.state_for("nope") is None


def test_verdicts_survive_a_restart(tmp_path: Any) -> None:
    path = tmp_path / "probe.json"
    store = ProbeStore(path)
    assert store.start([(_provider({"m": {}}), "m")], concurrency=1, timeout=5.0) is True
    deadline = time.time() + 5
    while store.snapshot()["running"] and time.time() < deadline:
        time.sleep(0.02)

    reloaded = ProbeStore(path)
    assert reloaded.state_for("m")["state"] == STATE_AVAILABLE
    assert reloaded.snapshot()["at"] is not None
    # A corrupt file is ignored rather than fatal.
    path.write_text("{not json", encoding="utf-8")
    assert ProbeStore(path).snapshot()["results"] == {}


def test_probeable_lanes_are_the_default_round() -> None:
    probeable = _provider({"a": {}})
    quiet = ProbeProvider(httpx.Client(), [Account({"id": "b"}, provider_id="quiet")], {"outcomes": {"b": {}}})
    quiet.probeable = False
    registry = Registry({"probe": probeable, "quiet": quiet})
    assert [(provider.id, model) for provider, model in registry.probe_targets()] == [("probe", "a")]
