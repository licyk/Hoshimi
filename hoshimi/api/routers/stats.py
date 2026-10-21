"""Usage accounting: what the gateway served over a range, call by call, and live."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from hoshimi.api.deps import ServicesDep
from hoshimi.api.heartbeat import HEARTBEAT_SECONDS, PING
from hoshimi.core.context import Services
from hoshimi.core.live import CLOSED
from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.record import PYDANTIC_V2, Record

router = APIRouter(prefix="/v1", tags=["stats"])

NAMES_TTL_SECONDS = 30.0
"""How long a live stream trusts its model-name table before asking the catalogue again."""


class UsageTotals(Record):
    """Token and call counts. ``input`` is prompt tokens including cache reads and writes."""

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0
    calls: int = 0
    failed: int = 0
    no_usage: int = 0


class LatencyStats(Record):
    """Time to first token (ms) and decode speed (tokens/s); None where nothing was measurable."""

    ttft_avg_ms: int | None = None
    ttft_p50_ms: int | None = None
    ttft_p95_ms: int | None = None
    tps_avg: int | None = None
    tps_p50: int | None = None


class UsagePoint(Record):
    """One bucket of the series: ``start`` is ``YYYY-MM-DD`` for days, an ISO local datetime for hours."""

    start: str
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0
    calls: int = 0
    failed: int = 0
    ttft_avg_ms: int | None = None
    tps: int | None = None


class UsageBreakdownRow(UsageTotals):
    """One model, provider, ``provider/account`` or origin over the range."""

    key: str
    label: str
    provider: str = ""
    account: str = ""
    ttft_avg_ms: int | None = None
    ttft_p50_ms: int | None = None
    ttft_p95_ms: int | None = None
    tps: int | None = None
    last_at: str | None = None


class UsageReport(Record):
    range: str
    granularity: Literal["hour", "day"]
    since: str
    totals: UsageTotals
    previous: UsageTotals
    latency: LatencyStats
    series: list[UsagePoint]
    by_model: list[UsageBreakdownRow]
    by_provider: list[UsageBreakdownRow]
    by_account: list[UsageBreakdownRow]
    by_origin: list[UsageBreakdownRow]
    lifetime: UsageTotals
    requests: int
    failures: int


class CallRecord(Record):
    """One served call from the recent-call ring; ``seq`` is the paging cursor."""

    seq: int
    id: str
    at: str
    model: str
    name: str
    provider: str = ""
    account: str = ""
    origin: str = "chat"
    effort: str = ""
    stream: bool = False
    ok: bool = True
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0
    ttft_ms: int | None = None
    decode_ms: int = 0
    tps: int | None = None
    elapsed_ms: int | None = None
    attempts: int = 0
    finish_reason: str = ""
    error_code: str = ""
    error_message: str = ""
    no_usage: bool = False


class CallPage(Record):
    calls: list[CallRecord]
    next: int | None = None


class LiveCall(Record):
    """A call in flight. ``started_at`` is epoch seconds; the token figures are estimates (chars / 4)."""

    id: str
    model: str
    name: str
    provider: str = ""
    account: str = ""
    origin: str = "chat"
    effort: str = ""
    stream: bool = False
    started_at: float
    phase: Literal["waiting", "reasoning", "answering", "tool"] = "waiting"
    ttft_ms: int | None = None
    elapsed_ms: int = 0
    output_chars: int = 0
    reasoning_chars: int = 0
    est_output_tokens: int = 0
    tps: int | None = None
    attempts: int = 0


class LiveSnapshot(Record):
    type: Literal["snapshot"] = "snapshot"
    server_time: float
    active: list[LiveCall] = Field(default_factory=list)
    today: UsageTotals = Field(default_factory=UsageTotals)
    recent: list[CallRecord] = Field(default_factory=list)


class LiveStarted(Record):
    type: Literal["started"] = "started"
    call: LiveCall


class LiveProgress(Record):
    type: Literal["progress"] = "progress"
    call: LiveCall


class LiveFinished(Record):
    type: Literal["finished"] = "finished"
    call_id: str
    record: CallRecord
    today: UsageTotals


LiveEventUnion = Annotated[LiveSnapshot | LiveStarted | LiveProgress | LiveFinished, Field(discriminator="type")]
"""One ``data:`` frame of ``/stats/live``, discriminated by ``type``.

Heartbeat frames (``{"type": "ping"}``) are not listed: they carry nothing
and a reader skips them.
"""

if PYDANTIC_V2:
    from pydantic import RootModel

    class LiveEvent(RootModel[LiveEventUnion]):
        """One frame of ``/stats/live``: a snapshot, then started / progress / finished events."""

    LIVE_EVENT_SCHEMA: Any = LiveEvent
else:  # pragma: no cover - Pydantic v1 has no root models; the bare union documents the same frames
    LIVE_EVENT_SCHEMA = LiveEventUnion


class StatsReset(Record):
    cleared_at: str


class LiveStreamResponse(StreamingResponse):
    """Describe the events under the actual SSE media type in OpenAPI."""

    media_type = "text/event-stream"


def model_names(services: Services) -> dict[str, str]:
    return {spec.id: spec.name for spec in services.registry.catalog()}


def provider_label(services: Services, provider_id: str) -> str:
    provider = services.registry.providers.get(provider_id)
    if provider is not None and provider.display_name:
        return provider.display_name
    cls = PROVIDER_CLASSES.get(provider_id)
    return (cls.display_name if cls else "") or provider_id


def call_record(row: dict[str, Any], names: dict[str, str]) -> CallRecord:
    model = str(row.get("model") or "")
    return CallRecord.model_validate({**row, "name": names.get(model, model)})


@router.get("/stats", operation_id="get_stats")
def get_stats(services: ServicesDep, range_: Annotated[Literal["24h", "7d", "30d", "90d"], Query(alias="range")] = "7d") -> UsageReport:
    """Totals, a zero-filled series and breakdowns for one range, plus the lifetime totals.

    ``24h`` reads hourly buckets (kept 72 hours); the others read daily
    buckets (kept 120 days). ``previous`` is the equal-length span just
    before, as far as the buckets still reach.
    """
    report = services.stats.report(range_)
    names = model_names(services)

    def labelled(rows: list[dict[str, Any]], label: Any) -> list[UsageBreakdownRow]:
        return [UsageBreakdownRow.model_validate({**row, "label": label(row)}) for row in rows]

    report["by_model"] = labelled(report["by_model"], lambda row: names.get(row["key"], row["key"]))
    report["by_provider"] = labelled(report["by_provider"], lambda row: provider_label(services, row["key"]))
    report["by_account"] = labelled(report["by_account"], lambda row: row["account"] or row["key"])
    report["by_origin"] = labelled(report["by_origin"], lambda row: row["key"])
    return UsageReport.model_validate(report)


@router.get("/stats/calls", operation_id="list_stats_calls")
def list_stats_calls(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(description="Return calls older than this seq (the previous page's `next`).")] = None,
    model: str | None = None,
    provider: str | None = None,
    account: Annotated[str | None, Query(description="An account id, or `provider/account`.")] = None,
    origin: str | None = None,
    ok: bool | None = None,
) -> CallPage:
    """The recent-call ring (last 1000 calls), newest first."""
    rows, cursor = services.stats.calls(limit=limit, before=before, model=model, provider=provider, account=account, origin=origin, ok=ok)
    names = model_names(services)
    return CallPage(calls=[call_record(row, names) for row in rows], next=cursor)


@router.delete("/stats", operation_id="reset_stats")
def reset_stats(services: ServicesDep) -> StatsReset:
    """Forget every counter and recorded call. Calls in flight are still recorded when they end."""
    return StatsReset(cleared_at=services.stats.reset())


@router.get("/stats/live", operation_id="stream_stats_live", response_class=LiveStreamResponse, responses={200: {"model": LIVE_EVENT_SCHEMA}})
def stream_stats_live(services: ServicesDep, request: Request) -> StreamingResponse:
    """A snapshot of the calls in flight, today's totals and the latest calls, then every change.

    Frames are ``data: <json>\\n\\n``; a ``{"type":"ping"}`` heartbeat goes out
    every 15 seconds. The stream ends if the reader falls too far behind —
    reconnecting brings a fresh snapshot, which replaces anything missed.
    """
    return LiveStreamResponse(live_frames(services, request), headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def live_frames(services: Services, request: Request | None = None, *, heartbeat: float = HEARTBEAT_SECONDS) -> AsyncIterator[bytes]:
    """The SSE body of ``/stats/live``; ``request`` is watched for a disconnect."""
    names: dict[str, str] = {}
    names_at = -NAMES_TTL_SECONDS

    async def current_names() -> dict[str, str]:
        nonlocal names, names_at
        if time.monotonic() - names_at >= NAMES_TTL_SECONDS:
            try:
                # Catalogue refresh may perform synchronous upstream requests.
                # Keep it off the event loop serving inference and other APIs.
                names = await asyncio.to_thread(model_names, services)
            except Exception:  # noqa: BLE001 - a label is never worth ending the stream
                names = {}
            names_at = time.monotonic()
        return names

    async def frame(event: dict[str, Any]) -> bytes:
        kind = event.get("type")
        table = await current_names()
        value: Record
        if kind == "snapshot":
            value = LiveSnapshot(
                server_time=event["server_time"],
                active=[live_call(call, table) for call in event["active"]],
                today=UsageTotals.model_validate(event["today"]),
                recent=[call_record(row, table) for row in event["recent"]],
            )
        elif kind == "finished":
            value = LiveFinished(call_id=event["call_id"], record=call_record(event["record"], table), today=UsageTotals.model_validate(event["today"]))
        elif kind == "started":
            value = LiveStarted(call=live_call(event["call"], table))
        else:
            value = LiveProgress(call=live_call(event["call"], table))
        return f"data: {value.model_dump_json()}\n\n".encode()

    subscription, snapshot = services.stats.subscribe(asyncio.get_running_loop())
    disconnected = asyncio.ensure_future(_disconnect(request)) if request is not None else None
    pending: asyncio.Future[Any] | None = None
    try:
        yield await frame(snapshot)
        while True:
            if pending is None:
                pending = asyncio.ensure_future(subscription.queue.get())
            waiting = {pending} if disconnected is None else {pending, disconnected}
            done, _ = await asyncio.wait(waiting, timeout=heartbeat, return_when=asyncio.FIRST_COMPLETED)
            if disconnected is not None and disconnected in done:
                return
            if pending not in done:
                yield PING
                continue
            event = pending.result()
            pending = None
            if event is CLOSED or subscription.dropped:
                return
            yield await frame(event)
    finally:
        services.stats.live.unsubscribe(subscription)
        for task in (pending, disconnected):
            if task is not None:
                task.cancel()


def live_call(row: dict[str, Any], names: dict[str, str]) -> LiveCall:
    model = str(row.get("model") or "")
    return LiveCall.model_validate({**row, "name": names.get(model, model)})


async def _disconnect(request: Request) -> None:
    """Return when the client hangs up; the stream otherwise only notices at its next write."""
    while True:
        message = await request.receive()
        if message.get("type") == "http.disconnect":
            return
