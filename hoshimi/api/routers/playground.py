"""Tool-authenticated Playground routes sharing the existing inference implementation."""

from typing import Annotated, Any

from fastapi import APIRouter, Body

from hoshimi.api.deps import ServicesDep
from hoshimi.api.routers.chat import chat_completion
from hoshimi.api.routers.models import ModelList, list_models

router = APIRouter(prefix="/v1/playground", tags=["playground"])
router.add_api_route("/models", list_models, methods=["GET"], response_model=ModelList, operation_id="list_playground_models")


@router.post("/chat/completions", operation_id="create_playground_chat_completion")
def create_playground_chat_completion(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="OpenAI Chat Completions request")],
) -> Any:
    """The Chat Completions route, filed under its own origin so a test from the web UI is not a client's call."""
    return chat_completion(services, body, origin="playground")
