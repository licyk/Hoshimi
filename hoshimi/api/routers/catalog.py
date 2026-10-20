"""Model capability catalogue for the web UI: what each id can do."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterable
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import Field

from hoshimi.api.deps import ServicesDep
from hoshimi.api.routers.probe import ProbeState
from hoshimi.core.catalog import ModelSpec
from hoshimi.core.effort import LEVELS, efforts_for
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["models"])
CATALOG_TIMEOUT_SECONDS = 20.0


class EffortOption(Record):
    id: str
    name: str
    description: str = ""
    is_default: bool = False
    tokens: int | None = None


class ModelSpecObject(Record):
    id: str
    provider: str
    name: str
    wire: str = "chat"
    vision: bool = False
    reasoning: bool = False
    tools: bool | None = None
    context_window: int = 0
    max_output: int = 0
    can_disable_thinking: bool = True
    region_sensitive: bool = False
    # Whether calling this id costs the account anything, per the station that
    # serves it. None is "the station did not say", which is most ids at most
    # stations — a price list is not something every gateway publishes.
    free: bool | None = None
    efforts: list[EffortOption]
    effort_default: str | None = None
    # The last availability verdict for this id, when a round has asked about it.
    probe: ProbeState | None = None
    # The gateway's own verdict: the station answered 404/410 for every
    # account, so the id is banned for a while. Unlike `probe` this was not
    # asked for — it is what serving this model taught the gateway.
    unavailable: bool = False


class ModelSpecList(Record):
    levels: list[EffortOption]
    data: list[ModelSpecObject]


class CatalogProvider(Record):
    id: str
    status: Literal["loading", "ready", "failed", "timeout"]
    data: list[ModelSpecObject]
    error: str | None = None
    last_success: float | None = None
    retry_at: float | None = None
    source: str = "live"


class CatalogEvent(Record):
    type: Literal["snapshot", "provider", "done"]
    levels: list[EffortOption] = Field(default_factory=list)
    providers: list[CatalogProvider] = Field(default_factory=list)
    provider: CatalogProvider | None = None


class CatalogStreamResponse(StreamingResponse):
    """Describe an individual event under the actual SSE media type in OpenAPI."""

    media_type = "text/event-stream"


def _probe_state(services, model: str) -> ProbeState | None:
    row = services.probe.state_for(model)
    return ProbeState(**row) if row else None


@router.get("/models", operation_id="list_model_specs")
def list_model_specs(services: ServicesDep) -> ModelSpecList:
    """Every advertised model with its capability spec and effort menu.

    `levels` is the free lane's token ladder (what a budget-driven model
    offers); each model carries its own `efforts` when it has a menu.
    """
    return ModelSpecList(levels=_levels(), data=_model_objects(services, services.registry.catalog()))


def _levels() -> list[EffortOption]:
    return [
        EffortOption(
            id=level.id,
            name=level.name,
            description=level.hint,
            is_default=level.id == "balanced",
            tokens=None,
        )
        for level in LEVELS
    ]


def _model_objects(services, specs: Iterable[ModelSpec]) -> list[ModelSpecObject]:
    data: list[ModelSpecObject] = []
    for spec in specs:
        options = efforts_for(spec) or []
        data.append(
            ModelSpecObject(
                id=spec.id,
                provider=spec.provider,
                name=spec.name,
                wire=spec.wire,
                vision=spec.vision,
                reasoning=spec.reasoning,
                tools=spec.tools,
                context_window=spec.context_window,
                max_output=spec.max_output,
                can_disable_thinking=spec.can_disable_thinking,
                region_sensitive=spec.region_sensitive,
                free=spec.free,
                efforts=[EffortOption(**option) for option in options],
                effort_default=next((option["id"] for option in options if option.get("is_default")), None),
                probe=_probe_state(services, spec.id),
                unavailable=services.registry.dead_models.is_dead(spec.provider, spec.id),
            )
        )
    return data


@router.get("/catalog/stream", operation_id="stream_catalog", response_class=CatalogStreamResponse,
            responses={200: {"model": CatalogEvent}})
def stream_catalog(services: ServicesDep) -> StreamingResponse:
    """Send cached rows first, then each provider independently in completion order."""
    return CatalogStreamResponse(catalog_events(services), headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def catalog_events(services) -> AsyncIterator[str]:
    discovery = services.registry.discovery
    # Snapshot the actual objects: account edits must not mix old and new pools.
    providers = services.registry.ordered_providers()

    def row(pid, provider, status, specs=None):
        try:
            data = _model_objects(services, (specs if specs is not None else discovery.cached(provider).specs).values())
        except Exception:  # noqa: BLE001 - malformed capabilities are isolated to their provider
            return CatalogProvider(id=pid, status="failed", data=[])
        state = discovery.cached(provider)
        return CatalogProvider(id=pid, status=status, data=data, error=state.error,
                               last_success=state.last_success, retry_at=state.retry_at, source=state.source)

    def event(value: CatalogEvent) -> str:
        return f"data: {value.model_dump_json()}\n\n"

    yield event(CatalogEvent(type="snapshot", levels=_levels(), providers=[row(pid, provider, "loading") for pid, provider in providers]))
    pending = {asyncio.shield(asyncio.wrap_future(discovery.submit(provider))): (pid, provider) for pid, provider in providers}
    deadline = time.monotonic() + CATALOG_TIMEOUT_SECONDS
    try:
        while pending:
            done, _ = await asyncio.wait(pending, timeout=min(1.0, max(0, deadline - time.monotonic())), return_when=asyncio.FIRST_COMPLETED)
            for future in done:
                pid, provider = pending.pop(future)
                result = future.result()
                yield event(CatalogEvent(type="provider", provider=row(pid, provider, "failed" if result.failed else "ready", result.specs)))
            if pending and time.monotonic() >= deadline:
                for pid, provider in pending.values():
                    yield event(CatalogEvent(type="provider", provider=row(pid, provider, "timeout")))
                break
            if pending and not done:
                yield ": keep-alive\n\n"
        yield event(CatalogEvent(type="done"))
    finally:
        # Shielded futures detach this subscriber without cancelling another
        # browser's request or releasing a still-running synchronous worker.
        for future in pending:
            future.cancel()


class UnavailableReset(Record):
    cleared: int


@router.post("/models/unavailable/reset", operation_id="reset_unavailable_models")
def reset_unavailable_models(services: ServicesDep) -> UnavailableReset:
    """Let every banned id back in.

    The ban is the gateway remembering a refusal; this is the operator saying
    the station has been fixed. A turn that gets an answer clears its own entry
    anyway, so what this is really for is the entries nothing will retry: an id
    no client asks for any more, or one that will 404 again the moment it is
    tried and so can never be the request that clears it.
    """
    return UnavailableReset(cleared=services.registry.dead_models.clear_all())
