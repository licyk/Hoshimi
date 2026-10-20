"""The OpenAI model catalogue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from hoshimi.api.deps import ServicesDep
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["openai"])


class ModelObject(Record):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = ""
    # Not part of OpenAI's shape, and a client may ignore it. It is the one
    # fact a free-model gateway has that a model id cannot carry: whether
    # calling this costs the account anything. None means the station never
    # said — see `hoshimi.core.free_marks`, where that is the honest answer
    # for most ids at most stations.
    free: bool | None = None
    display_name: str = ""


class ModelList(Record):
    object: str = "list"
    data: list[ModelObject]


@router.get("/models", operation_id="list_models")
def list_models(services: ServicesDep, limit: Annotated[int | None, Query(ge=1, le=1000)] = None) -> ModelList:
    """Every advertised id, with the one fact a model id cannot carry.

    The roster is what each station published; ``free`` is what the station
    said about the price of calling an id there, and it is absent for almost
    everything because almost nothing publishes a price list. A client that
    has no idea what to do with the extra field is the expected case — every
    OpenAI client ignores keys it does not know.
    """
    if limit is not None:
        return ModelList(data=[ModelObject(id=model, owned_by=owner, free=spec.free if spec else None, display_name=spec.name if spec else model)
                               for model, owner, spec in services.registry.quick_models()[:limit]])
    specs = {spec.id: spec for spec in services.registry.catalog()}
    return ModelList(
        data=[ModelObject(id=name, owned_by=owner, free=specs[name].free if name in specs else None,
                          display_name=specs[name].name if name in specs else name) for name, owner in services.registry.model_ids()]
    )
