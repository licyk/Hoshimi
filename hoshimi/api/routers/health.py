"""Liveness probe. Public: no API key is needed."""

from fastapi import APIRouter

router = APIRouter(tags=["app"])


@router.get("/health", operation_id="get_root_health")
def get_root_health() -> dict[str, str]:
    return {"status": "ok"}
