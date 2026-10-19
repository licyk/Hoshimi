"""Anthropic-compatible Messages endpoint for stateless agent conversations."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from itertools import chain
from typing import Annotated, Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, StreamingResponse

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import ANTHROPIC_ERROR_RESPONSES, anthropic_error
from hoshimi.api.inference import prepare_request, tracked_chunks
from hoshimi.core.chunks import UpstreamStreamFailure
from hoshimi.core.errors import HoshimiError, UpstreamError
from hoshimi.core.message_chunks import aggregate_messages, emit_messages_events, new_message_id
from hoshimi.core.record import Record
from hoshimi.core.translate.messages import estimate_input_tokens, messages_to_chat

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/messages", tags=["anthropic"])


class MessageUsage(Record):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


class MessageResponse(Record):
    id: str
    type: str
    role: str
    model: str
    content: list[dict[str, Any]]
    stop_reason: str | None
    stop_sequence: str | None
    usage: MessageUsage


class CountTokensResponse(Record):
    input_tokens: int


def _frame(event: dict[str, Any]) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _close(stream: Iterator[dict[str, Any]]) -> None:
    close = getattr(stream, "close", None)
    if close is not None:
        close()


def _sse(events: Iterator[dict[str, Any]], upstream: Iterator[dict[str, Any]]) -> Iterator[bytes]:
    try:
        for event in events:
            yield _frame(event)
    except HoshimiError as exc:
        yield _frame(anthropic_error(exc.message, exc.http_status))
    except UpstreamStreamFailure as exc:
        yield _frame(anthropic_error(str(exc), 502))
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception:
        logger.exception("Messages stream failed")
        yield _frame(anthropic_error("The upstream stream failed unexpectedly.", 502))
    finally:
        _close(events)
        _close(upstream)


@router.post(
    "",
    operation_id="create_message",
    response_model=MessageResponse,
    responses={**ANTHROPIC_ERROR_RESPONSES, 200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def create_message(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="Anthropic Messages request; send the full conversation. stream defaults to false.")],
) -> Any:
    requested = body.get("model")
    spec = services.registry.spec(requested) if isinstance(requested, str) else None
    chat = messages_to_chat(body, native=spec is not None and spec.wire == "messages")
    model, chat = prepare_request(services, chat)
    upstream = tracked_chunks(services, model, chat, origin="messages")
    message_id = new_message_id()
    headers = {"request-id": message_id}
    if not body.get("stream", False):
        try:
            message = aggregate_messages(emit_messages_events(upstream, model=model, message_id=message_id))
        except UpstreamStreamFailure as exc:
            raise UpstreamError(str(exc)) from None
        finally:
            _close(upstream)
        return JSONResponse(content=message, headers=headers)
    # Open the upstream before sending headers, so authentication/rate-limit
    # failures can still carry their HTTP status instead of an HTTP 200.
    try:
        first = next(upstream)
    except StopIteration:
        raise UpstreamError("Upstream returned an empty stream.") from None
    if first.get("type") == "error":
        _close(upstream)
        raise UpstreamError(str(first.get("error") or "Upstream stream failed."))
    events = emit_messages_events(chain([first], upstream), model=model, message_id=message_id)
    return StreamingResponse(_sse(events, upstream), media_type="text/event-stream", headers={**headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/count_tokens", operation_id="count_message_tokens", response_model=CountTokensResponse, responses=ANTHROPIC_ERROR_RESPONSES)
def count_message_tokens(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="Local input-token estimate, not an upstream tokenizer count; max_tokens is optional.")],
) -> Any:
    messages_to_chat(body, count_only=True)
    services.registry.resolve(body["model"])
    return JSONResponse(content={"input_tokens": estimate_input_tokens(body)}, headers={"x-hoshimi-token-count": "estimate"})
