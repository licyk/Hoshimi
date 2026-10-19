"""OpenAI-compatible Chat Completions and Responses endpoints (thin: parse, call, shape)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, StreamingResponse

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import error_type_for, openai_error
from hoshimi.api.inference import prepare_request, tracked_chunks
from hoshimi.core.chunks import (
    UpstreamStreamFailure,
    aggregate_chat,
    aggregate_responses,
    emit_chat_chunks,
    emit_responses_events,
    new_completion_id,
    new_response_id,
)
from hoshimi.core.errors import HoshimiError, UpstreamError, ValidationError
from hoshimi.core.translate.responses import ResponsesRequestError, responses_to_chat

router = APIRouter(prefix="/v1", tags=["openai"])

SSE_MEDIA_TYPE = "text/event-stream; charset=utf-8"


def _sse(events: Iterable[dict[str, Any]], *, kind: str) -> Iterator[bytes]:
    """Serialize event dicts as SSE frames; a failure after the headers is a frame, not a 500."""
    try:
        for event in events:
            if isinstance(event, dict) and isinstance(event.get("error"), dict):
                yield _frame(event)
                break
            yield _frame(event)
    except HoshimiError as exc:
        if kind == "responses":
            yield _frame({"error": {"message": exc.message, "type": error_type_for(exc), "code": exc.code}})
        else:
            yield _frame(openai_error(exc.message, error_type_for(exc), exc.code))
        return
    except (BrokenPipeError, ConnectionResetError):
        return
    yield b"data: [DONE]\n\n"


def _frame(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _finish_non_stream(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content=payload)


@router.post("/chat/completions", operation_id="create_chat_completion")
def create_chat_completion(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="OpenAI Chat Completions request")],
) -> Any:
    model, body = prepare_request(services, body)
    stream = bool(body.get("stream", True))

    completion_id = new_completion_id()
    created = int(time.time())
    chunks = emit_chat_chunks(
        tracked_chunks(services, model, body, origin="chat"),
        model=model,
        completion_id=completion_id,
        created=created,
    )

    if not stream:
        try:
            message, finish_reason, usage = aggregate_chat(chunks)
        except UpstreamStreamFailure as exc:
            raise UpstreamError(str(exc)) from None
        payload = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                    "logprobs": None,
                }
            ],
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        return _finish_non_stream(payload)

    return StreamingResponse(_sse(chunks, kind="chat"), media_type=SSE_MEDIA_TYPE)


@router.post("/responses", operation_id="create_response")
def create_response(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="OpenAI Responses request")],
) -> Any:
    try:
        chat_body = responses_to_chat(body)
    except ResponsesRequestError as exc:
        raise ValidationError(str(exc)) from None
    model, chat_body = prepare_request(services, chat_body)
    stream = bool(body.get("stream", True))

    response_id = new_response_id()
    created = int(time.time())
    chunks = tracked_chunks(services, model, chat_body, origin="responses")

    if not stream:
        try:
            payload = aggregate_responses(chunks, model=model, response_id=response_id, created=created)
        except UpstreamStreamFailure as exc:
            raise UpstreamError(str(exc)) from None
        return _finish_non_stream(payload)

    events = emit_responses_events(chunks, model=model, response_id=response_id, created=created)
    return StreamingResponse(_sse(events, kind="responses"), media_type=SSE_MEDIA_TYPE)
