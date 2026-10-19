"""Availability probing: start a round and read the verdicts."""

from __future__ import annotations

from fastapi import APIRouter

from hoshimi.api.deps import ServicesDep
from hoshimi.core.probe import DEFAULT_CONCURRENCY, DEFAULT_TIMEOUT
from hoshimi.core.providers.base import Provider
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["probe"])


class ProbeState(Record):
    """One model's verdict from the last round that asked about it."""

    state: str
    detail: str | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    at: str | None = None


class ProbeSnapshot(Record):
    running: bool
    started: bool = False
    at: str | None = None
    results: dict[str, ProbeState]
    unresolved: list[str]


class ProbeRequest(Record):
    """`models` omitted probes every default lane; an empty list does the same."""

    models: list[str] | None = None


def _snapshot(services, *, started: bool = False, unresolved: list[str] | None = None) -> ProbeSnapshot:
    raw = services.probe.snapshot()
    return ProbeSnapshot(
        running=bool(raw["running"]),
        started=started,
        at=raw["at"],
        results={model: ProbeState(**row) for model, row in raw["results"].items()},
        unresolved=list(unresolved or []),
    )


def _targets(services, models: list[str] | None) -> tuple[list[tuple[Provider, str]], list[str]]:
    """The round's work: an explicit roster, or every lane whose listing cannot answer."""
    if not models:
        return services.registry.probe_targets(), []
    targets: list[tuple[Provider, str]] = []
    unresolved: list[str] = []
    for model in models:
        try:
            provider, upstream = services.registry.resolve(model)
        except Exception:  # noqa: BLE001 - an unknown id is a report, not a crash
            unresolved.append(model)
            continue
        targets.append((provider, upstream))
    return targets, unresolved


@router.get("/probe", operation_id="get_probe")
def get_probe(services: ServicesDep) -> ProbeSnapshot:
    """The last verdict per model, and whether a round is running now."""
    return _snapshot(services)


@router.post("/probe", operation_id="start_probe")
def start_probe(services: ServicesDep, request: ProbeRequest | None = None) -> ProbeSnapshot:
    """Start a round (background, low fan-out) and report what it will ask.

    Rounds do not stack: a second call while one is running returns the running
    snapshot with `started: false`.
    """
    targets, unresolved = _targets(services, request.models if request else None)
    started = services.probe.start(targets, concurrency=DEFAULT_CONCURRENCY, timeout=DEFAULT_TIMEOUT)
    return _snapshot(services, started=started, unresolved=unresolved)
