"""Classification endpoints; classifier models are never offered as chat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from hoshimi.api.deps import ServicesDep
from hoshimi.core.errors import ModelNotFoundError, UpstreamError
from hoshimi.core.providers.typesafe import TypeSafeProvider
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["classification"])


class ClassificationRequest(Record):
    model: str
    state: dict[str, Any]
    questions: dict[str, dict[str, Any]]


@router.get("/classifiers", operation_id="list_classifiers")
def list_classifiers(services: ServicesDep) -> dict[str, Any]:
    return {"data": [{"id": f"{provider.id}/{model}", "owned_by": provider.id} for provider in services.registry.providers.values()
                     if isinstance(provider, TypeSafeProvider) for model in provider.classifier_models()]}


@router.post("/classify", operation_id="create_classification")
def create_classification(body: ClassificationRequest, services: ServicesDep) -> dict[str, Any]:
    provider_id, _, model = body.model.partition("/")
    provider = services.registry.providers.get(provider_id)
    if not isinstance(provider, TypeSafeProvider) or model not in provider.classifier_models():
        raise ModelNotFoundError("Select a classifier from /v1/classifiers.")
    turn = services.stats.turn(body.model, origin="classify", provider=provider.id)
    result = None
    for chunk in turn.wrap(provider.stream(model, {"state": body.state, "questions": body.questions, "_classification": True}, on_route=turn.routed)):
        if chunk.get("type") == "classification":
            result = chunk["result"]
    if result is None:
        raise UpstreamError("Classifier returned no result.")
    return result
