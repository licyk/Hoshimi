"""The OpenAI model catalogue."""

from __future__ import annotations

from fastapi import APIRouter

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


class ModelList(Record):
    object: str = "list"
    data: list[ModelObject]


@router.get("/models", operation_id="list_models")
def list_models(services: ServicesDep) -> ModelList:
    """Every advertised id, with the one fact a model id cannot carry.

    The roster is what each station published; ``free`` is what the station
    said about the price of calling an id there, and it is absent for almost
    everything because almost nothing publishes a price list. A client that
    has no idea what to do with the extra field is the expected case — every
    OpenAI client ignores keys it does not know.
    """
    free = {spec.id: spec.free for spec in services.registry.catalog()}
    return ModelList(
        data=[ModelObject(id=name, owned_by=owner, free=free.get(name)) for name, owner in services.registry.model_ids()]
    )
