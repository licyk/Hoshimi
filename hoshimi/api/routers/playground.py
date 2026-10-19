"""Tool-authenticated Playground routes sharing the existing inference implementation."""

from fastapi import APIRouter

from hoshimi.api.routers.chat import create_chat_completion
from hoshimi.api.routers.models import ModelList, list_models

router = APIRouter(prefix="/v1/playground", tags=["playground"])
router.add_api_route("/models", list_models, methods=["GET"], response_model=ModelList, operation_id="list_playground_models")
router.add_api_route("/chat/completions", create_chat_completion, methods=["POST"], operation_id="create_playground_chat_completion")
