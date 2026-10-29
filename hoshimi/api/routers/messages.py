"""Anthropic-compatible Messages endpoint for stateless agent conversations."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from itertools import chain
from typing import Annotated, Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import ANTHROPIC_ERROR_RESPONSES, anthropic_error, upstream_error_metadata
from hoshimi.api.heartbeat import with_heartbeats
from hoshimi.api.inference import prepare_request, tracked_chunks
from hoshimi.core.chunks import UpstreamStreamFailure
from hoshimi.core.context import Services
from hoshimi.core.errors import HoshimiError, UpstreamError
from hoshimi.core.message_chunks import aggregate_messages, emit_messages_events, new_message_id
from hoshimi.core.native_messages import MessagesRequest
from hoshimi.core.net.identity import InferenceIdentity
from hoshimi.core.record import Record
from hoshimi.core.stats import Turn
from hoshimi.core.translate.messages import estimate_input_tokens, messages_to_chat, validate_messages_request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/messages", tags=["anthropic"])


class MessageUsage(Record):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


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
        payload = anthropic_error(exc.message, exc.http_status)
        payload["error"].update(upstream_error_metadata(exc))
        yield _frame(payload)
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


def _native_sse(upstream: Iterator[dict[str, Any]]) -> Iterator[bytes]:
    """Preserve complete upstream SSE frames, so heartbeats cannot split JSON."""
    pending = b""
    try:
        for chunk in upstream:
            if chunk.get("type") != "messages_bytes":
                continue
            pending += chunk["data"]
            while boundary := re.search(rb"\r?\n\r?\n", pending):
                yield pending[:boundary.end()]
                pending = pending[boundary.end():]
        if pending.strip():
            raise UpstreamError("Upstream Messages stream ended inside an SSE frame.")
    except HoshimiError as exc:
        payload = anthropic_error(exc.message, exc.http_status)
        payload["error"].update(upstream_error_metadata(exc))
        yield _frame(payload)
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception:
        logger.exception("Native Messages stream failed")
        yield _frame(anthropic_error("The upstream stream failed unexpectedly.", 502))
    finally:
        _close(upstream)


def _native_response(services: Services, body: dict[str, Any], request: Request, *, count_only: bool = False) -> Response | None:
    context = MessagesRequest(body=body, headers=dict(request.headers), query=list(request.query_params.multi_items()), count_only=count_only)
    effort = body.get("output_config", {})
    effort = str(effort.get("effort") or "") if isinstance(effort, dict) else ""
    streaming = bool(body.get("stream", False))
    # The turn is opened only once the native route is known to exist: when it
    # is not, the translated path counts this request instead, and a turn
    # opened here would linger in the live view as a call nobody finishes.
    # The registry's stream is lazy, so ``routed`` can only fire after then.
    opened: list[Turn] = []

    def routed(provider: str, account: str, attempt: int) -> None:
        if opened:
            opened[0].routed(provider, account, attempt)

    try:
        upstream = services.registry.native_messages(body["model"], context, on_route=None if count_only else routed)
    except BaseException as exc:
        if not count_only:
            services.stats.turn(body["model"], effort=effort, origin="messages", stream=streaming,
                                provider=services.registry.provider_hint(body["model"])).fail(exc)
        raise
    if upstream is None:
        return None
    if not count_only:
        turn = services.stats.turn(body["model"], effort=effort, origin="messages", stream=streaming, provider=services.registry.provider_hint(body["model"]))
        opened.append(turn)
        upstream = turn.wrap(upstream)
    try:
        first = next(upstream)
        if first.get("type") != "messages_response":
            raise UpstreamError("Upstream Messages response has no headers.")
        headers = first["headers"]
        if body.get("stream", False) and not count_only:
            headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            return StreamingResponse(with_heartbeats(_native_sse(upstream), cancel=context.cancellation.close),
                                     status_code=first["status"], headers=headers, media_type="text/event-stream")
        try:
            data = b"".join(chunk["data"] for chunk in upstream if chunk.get("type") == "messages_bytes")
            return Response(content=data, status_code=first["status"], headers=headers, media_type="application/json")
        finally:
            _close(upstream)
    except BaseException:
        _close(upstream)
        raise


@router.post(
    "",
    operation_id="create_message",
    response_model=MessageResponse,
    responses={**ANTHROPIC_ERROR_RESPONSES, 200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def create_message(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="Anthropic Messages request; send the full conversation. stream defaults to false.")],
    request: Request,
) -> Any:
    validate_messages_request(body)
    response = _native_response(services, body, request)
    if response is not None:
        return response
    requested = body.get("model")
    spec = services.registry.spec(requested) if isinstance(requested, str) else None
    provider, _model = services.registry.resolve(body["model"])
    try:
        chat = messages_to_chat(body, native=spec is not None and spec.wire == "messages",
                                structured_output=bool(provider.options.get("structured_outputs")) and (spec is None or spec.wire in ("chat", "responses")))
    except HoshimiError as exc:
        exc.message = f"{provider.id}: {exc.message}"
        raise
    model, chat = prepare_request(services, chat)
    upstream = tracked_chunks(services, model, chat, origin="messages", stream=bool(body.get("stream", False)), identity=InferenceIdentity.from_headers(request.headers))
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
    return StreamingResponse(with_heartbeats(_sse(events, upstream)), media_type="text/event-stream", headers={**headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/count_tokens", operation_id="count_message_tokens", response_model=CountTokensResponse, responses=ANTHROPIC_ERROR_RESPONSES)
def count_message_tokens(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="Native upstream token counting when supported, otherwise a marked local estimate; max_tokens is optional.")],
    request: Request,
) -> Any:
    validate_messages_request(body, count_only=True)
    response = _native_response(services, body, request, count_only=True)
    if response is not None:
        return response
    return JSONResponse(content={"input_tokens": estimate_input_tokens(body)}, headers={"x-hoshimi-token-count": "estimate"})
