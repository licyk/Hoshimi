"""Usage accounting: what the gateway served, by day and by model."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import Field

from hoshimi.api.deps import ServicesDep
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["stats"])


class UsageRow(Record):
    """One model's lifetime totals; the rates come from the retained days."""

    model: str
    name: str
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    calls: int = 0
    failed: int = 0
    avg_ttft_ms: int | None = None
    tps: int | None = None


class DayRow(Record):
    day: str
    total: int
    models: list[UsageRow] = Field(default_factory=list)


class Grand(Record):
    input: int = 0
    output: int = 0
    reasoning: int = 0
    calls: int = 0
    failed: int = 0


class Sample(Record):
    """The last calls, kept so a "that answer vanished" report has rows to audit."""

    at: str
    model: str
    ok: bool
    input: int = 0
    output: int = 0
    reasoning: int = 0
    ttft_ms: int | None = None
    tps: int | None = None
    decode_ms: int = 0
    decode_tokens: int = 0
    elapsed_ms: int | None = None
    effort: str = ""
    origin: str = "chat"
    no_usage: bool = False


class StatsSnapshot(Record):
    requests: int
    failures: int
    days: list[DayRow]
    models: list[UsageRow]
    grand: Grand
    samples: list[Sample]


@router.get("/stats", operation_id="get_stats")
def get_stats(services: ServicesDep) -> StatsSnapshot:
    """Totals for the dashboard: per-day and per-model usage, plus the recent calls."""
    names = {spec.id: spec.name for spec in services.registry.catalog()}
    return StatsSnapshot.model_validate(services.stats.snapshot(names))
