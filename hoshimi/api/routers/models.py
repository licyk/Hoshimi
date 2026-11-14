"""The OpenAI model catalogue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from hoshimi.api.deps import ServicesDep
from hoshimi.api.model_metadata import ModelCapabilities, model_capabilities
from hoshimi.core.catalog import ModelSpec
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["openai"])


class ModelObject(ModelCapabilities):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = ""
    display_name: str = ""


class ModelList(Record):
    object: str = "list"
    data: list[ModelObject]


@router.get("/models", operation_id="list_models")
def list_models(services: ServicesDep, limit: Annotated[int | None, Query(ge=1, le=1000)] = None) -> ModelList:
    """Every advertised id, with the management catalogue's model capabilities."""
    if limit is not None:
        return ModelList(data=[_model_object(model, owner, spec)
                               for model, owner, spec in services.registry.quick_models()[:limit]])
    specs = {(spec.provider, spec.id): spec for spec in services.registry.catalog()}
    return ModelList(
        data=[_model_object(name, owner, specs.get((owner, name))) for name, owner in services.registry.model_ids()]
    )


def _model_object(model: str, owner: str, spec: ModelSpec | None) -> ModelObject:
    spec = spec or ModelSpec(id=model, provider=owner, name=model)
    return ModelObject(id=model, owned_by=owner, display_name=spec.name, **model_capabilities(spec).model_dump())
