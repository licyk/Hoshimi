"""Map domain exceptions onto the endpoint's JSON error shape.

``/v1/*`` speaks OpenAI's error envelope (``{"error": {"message", "type", "code"}}``) because
that is what OpenAI clients parse. Messages uses Anthropic's envelope;
management routes answer Hoshimi's ``{code, message, detail}``.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from hoshimi.api.paths import route_path
from hoshimi.core.errors import HoshimiError, ModelNotFoundError, UpstreamError, ValidationError
from hoshimi.core.providers.base import ProviderError
from hoshimi.core.record import Record

# Domain code -> the ``type`` OpenAI clients expect.
OPENAI_ERROR_TYPES = {
    "not_found": "invalid_request_error",
    "model_not_found": "invalid_request_error",
    "invalid_input": "invalid_request_error",
    "conflict": "invalid_request_error",
    "upstream_error": "upstream_error",
    "quota_exceeded": "upstream_error",
    "context_length_exceeded": "invalid_request_error",
    "auth_failed": "upstream_error",
    "internal_error": "server_error",
}


class ErrorResponse(Record):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class AnthropicErrorDetail(Record):
    type: str
    message: str


class AnthropicErrorResponse(Record):
    type: str
    error: AnthropicErrorDetail


ANTHROPIC_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {status: {"model": AnthropicErrorResponse} for status in (400, 401, 403, 404, 429, 500, 502, 503, "default")}


def openai_error(message: str, error_type: str, code: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "code": code}}


def is_messages_path(path: str) -> bool:
    return path.rstrip("/").endswith(("/v1/messages", "/v1/messages/count_tokens"))


def anthropic_error(message: str, status: int) -> dict[str, Any]:
    kind = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        413: "request_too_large",
        429: "rate_limit_error",
        529: "overloaded_error",
    }.get(status, "api_error")
    return {"type": "error", "error": {"type": kind, "message": message}}


def error_type_for(exc: HoshimiError) -> str:
    if isinstance(exc, UpstreamError):
        return "upstream_error"
    if isinstance(exc, (ModelNotFoundError, ValidationError)):
        return "invalid_request_error"
    return OPENAI_ERROR_TYPES.get(exc.code, "server_error")


def error_response(status: int, code: str, message: str, detail: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message, "detail": detail or {}}, headers=headers)


def upstream_error_metadata(exc: HoshimiError) -> dict[str, Any]:
    """Carry typed refusals and wait times through protocol-shaped errors."""
    metadata = {key: exc.detail[key] for key in ("upstream_type", "upstream_code", "scope") if key in exc.detail}
    if isinstance(exc, ProviderError) and exc.retry_after is not None and math.isfinite(exc.retry_after):
        metadata["retry_after"] = max(0, math.ceil(exc.retry_after))
    return metadata


async def _domain_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HoshimiError)
    metadata = upstream_error_metadata(exc)
    headers = {"retry-after": str(metadata["retry_after"])} if "retry_after" in metadata else {}
    if is_messages_path(route_path(request.scope)):
        if isinstance(exc, ProviderError) and exc.response_body is not None:
            return Response(content=exc.response_body, status_code=exc.http_status, headers={**exc.response_headers, **headers})
        payload = anthropic_error(exc.message, exc.http_status)
        payload["error"].update(metadata)
        return JSONResponse(status_code=exc.http_status, content=payload, headers=headers)
    if route_path(request.scope).startswith("/v1/"):
        payload = openai_error(exc.message, error_type_for(exc), code=exc.code if exc.code != "internal_error" else None)
        payload["error"].update(metadata)
        return JSONResponse(status_code=exc.http_status, content=payload, headers=headers)
    return error_response(exc.http_status, exc.code, exc.message, exc.detail, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HoshimiError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    if is_messages_path(route_path(request.scope)):
        return JSONResponse(status_code=400, content=anthropic_error("Invalid Messages request body.", 400))
    return await request_validation_exception_handler(request, exc)


# Declared on routes so the generated client knows the error shape.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}
