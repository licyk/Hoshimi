"""Tool-authenticated Playground routes: chat on the shared inference path, and kept conversations.

A conversation is a tree of messages (see :mod:`hoshimi.core.conversations`).
Sending, editing and retrying all add messages and stream the reply as
Server-Sent Events, one JSON object per event (see
:func:`hoshimi.api.playground.event`); everything a model is sent is put
together on the server (:class:`hoshimi.api.playground.PlaygroundRunner`).
"""

import threading
from collections.abc import AsyncIterator, Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import ERROR_RESPONSES
from hoshimi.api.playground import PlaygroundRunner, event, sse
from hoshimi.api.routers.chat import chat_completion
from hoshimi.api.routers.models import ModelList, list_models
from hoshimi.core.context import Services
from hoshimi.core.conversations import AttachmentInfo, ChatMessage, Conversation
from hoshimi.core.errors import NotFoundError, ValidationError
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1/playground", tags=["playground"])
router.add_api_route("/models", list_models, methods=["GET"], response_model=ModelList, operation_id="list_playground_models")

SSE_RESPONSES: dict[int | str, dict[str, Any]] = {200: {"description": "The reply's events, as Server-Sent Events", "content": {"text/event-stream": {}}}, **ERROR_RESPONSES}

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_TYPES = {"application/json", "application/xml", "application/yaml", "application/x-yaml", "application/javascript", "application/x-sh", "application/sql", "application/toml"}


@router.post("/chat/completions", operation_id="create_playground_chat_completion")
def create_playground_chat_completion(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="OpenAI Chat Completions request")],
) -> Any:
    """The Chat Completions route, filed under its own origin so a test from the web UI is not a client's call.

    The system prompt from Settings (``playground.system_prompt``) goes first,
    unless the request brings a system message of its own.
    """
    prompt = services.settings.playground_system_prompt
    messages = body.get("messages")
    if prompt and isinstance(messages, list) and not any(isinstance(m, dict) and m.get("role") in ("system", "developer") for m in messages):
        body = {**body, "messages": [{"role": "system", "content": prompt}, *messages]}
    return chat_completion(services, body, origin="playground")


# -- views ---------------------------------------------------------------------- #
class Notice(Record):
    """Something about how a reply was made: ``image-described`` (by ``model``), ``image-omitted``, or ``image-refused`` (by ``model``)."""

    kind: str
    name: str = ""
    model: str = ""


class AttachmentView(Record):
    id: str
    kind: Literal["image", "file"]
    name: str
    mime: str
    """The file's own type; a file is kept, and served, as its text."""
    size: int
    width: int | None = None
    height: int | None = None


class MessageView(Record):
    id: str
    parent_id: str | None
    """The message this one follows; None for the first. Siblings are the versions of one turn."""
    role: Literal["user", "assistant"]
    content: str
    model: str = ""
    effort: str = ""
    status: Literal["complete", "streaming", "stopped", "error"] = "complete"
    error: str = ""
    notices: list[Notice] = Field(default_factory=list)
    created_at: float
    attachments: list[AttachmentView] = Field(default_factory=list)
    reasoning: str = ""
    """What the model reasoned before it answered, for a model that shows it; empty otherwise."""
    reasoning_ms: int = 0
    """How long it reasoned, in milliseconds."""


class ConversationSummary(Record):
    id: str
    title: str
    """The first question until a model names it; then the generated or the user's own title."""
    title_state: Literal["", "provisional", "auto", "user"]
    model: str
    effort: str
    archived: bool
    temporary: bool
    branched_from: str
    """The conversation this one was branched from, if any."""
    created_at: float
    updated_at: float
    """Unix seconds of the last message, which orders the list."""


class ConversationView(ConversationSummary):
    current_leaf: str
    """The last message of the path on screen."""
    messages: list[MessageView]
    """Every message of the tree, oldest first."""


class ConversationList(Record):
    conversations: list[ConversationSummary]


class ConversationCreate(Record):
    model: str = ""
    effort: str = ""
    title: str = ""
    temporary: bool = False


class ConversationPatch(Record):
    """What to change; a field left out stays as it is. A title set here is the user's own, never replaced."""

    title: str | None = None
    model: str | None = None
    effort: str | None = None
    archived: bool | None = None
    current_leaf: str | None = None


class MessageSend(Record):
    """A question after ``parent_id`` (None: the first). An edit is a question after the edited one's parent."""

    parent_id: str | None = None
    content: str = ""
    attachment_ids: list[str] = Field(default_factory=list)
    model: str = ""
    effort: str = ""


class Retry(Record):
    """Another reply to the same question; ``model`` and ``effort`` default to the ones it was answered with."""

    model: str = ""
    effort: str | None = None


class Branch(Record):
    message_id: str


class Session(Record):
    """The conversation open last, so the Playground can return to it; None for a new one."""

    conversation_id: str | None = None


def _attachment(a: AttachmentInfo) -> AttachmentView:
    return AttachmentView(id=a.id, kind=a.kind, name=a.name, mime=a.mime, size=a.size, width=a.width, height=a.height)


def _message(m: ChatMessage) -> MessageView:
    return MessageView(
        id=m.id, parent_id=m.parent_id, role=m.role, content=m.content, model=m.model, effort=m.effort, status=m.status, error=m.error,
        notices=[Notice(**{k: v for k, v in n.items() if k in ("kind", "name", "model")}) for n in m.notices if isinstance(n, dict) and n.get("kind")],
        created_at=m.created_at, attachments=[_attachment(a) for a in m.attachments], reasoning=m.reasoning, reasoning_ms=m.reasoning_ms,
    )


def _summary(c: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=c.id, title=c.title, title_state=c.title_state, model=c.model, effort=c.effort, archived=c.archived, temporary=c.temporary,
        branched_from=c.branched_from, created_at=c.created_at, updated_at=c.updated_at,
    )


def _view(c: Conversation) -> ConversationView:
    return ConversationView(**_summary(c).model_dump(), current_leaf=c.current_leaf, messages=[_message(m) for m in c.messages])


def _conversation(services: Services, conversation_id: str) -> Conversation:
    conversation = services.conversations.get(conversation_id)
    if conversation is None:
        raise NotFoundError(f"No conversation '{conversation_id}'.")
    return conversation


def _model(services: Services, *candidates: str) -> str:
    model = next((c for c in candidates if c), "") or services.settings.default_model or services.registry.default_model or ""
    if not model:
        raise ValidationError("Choose a model first.")
    return model


def _stream(events: Iterator[dict[str, Any]]) -> StreamingResponse:
    """Events as an SSE response that no proxy buffers.

    The events are read on a worker thread, one at a time, and the reader is
    closed as soon as the response ends: when the page stops a reply or goes
    away, the reply settles as stopped at once, not whenever the abandoned
    generator happens to be collected.
    """
    lines = sse(_wired(events))
    lock = threading.Lock()
    done = object()

    def pull() -> Any:
        with lock:
            return next(lines, done)

    def close() -> None:
        with lock:
            lines.close()

    async def body() -> AsyncIterator[str]:
        try:
            while (line := await run_in_threadpool(pull)) is not done:
                yield line
        finally:
            await run_in_threadpool(close)

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _wired(events: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Events with their messages in the shape the REST routes use; closing this closes the reply."""
    try:
        for item in events:
            yield _wire(item)
    finally:
        close = getattr(events, "close", None)
        if close is not None:
            close()


def _wire(item: dict[str, Any]) -> dict[str, Any]:
    """An event with its message in the shape the REST routes use."""
    message = item.get("message")
    if isinstance(message, ChatMessage):
        return {**item, "message": _message(message).model_dump()}
    return item


# -- conversations ---------------------------------------------------------------- #
@router.get("/conversations", operation_id="list_conversations")
def list_conversations(services: ServicesDep, archived: Annotated[bool, Query(description="The archive instead of the recent ones")] = False) -> ConversationList:
    """The kept conversations, the most recently used first; temporary ones are never listed."""
    return ConversationList(conversations=[_summary(c) for c in services.conversations.conversations(archived=archived)])


@router.post("/conversations", operation_id="create_conversation", status_code=201)
def create_conversation(services: ServicesDep, body: ConversationCreate) -> ConversationView:
    return _view(services.conversations.create(model=body.model, effort=body.effort, title=body.title, temporary=body.temporary))


@router.get("/conversations/{conversation_id}", operation_id="get_conversation", responses=ERROR_RESPONSES)
def get_conversation(services: ServicesDep, conversation_id: str) -> ConversationView:
    return _view(_conversation(services, conversation_id))


@router.patch("/conversations/{conversation_id}", operation_id="update_conversation", responses=ERROR_RESPONSES)
def update_conversation(services: ServicesDep, conversation_id: str, body: ConversationPatch) -> ConversationSummary:
    _conversation(services, conversation_id)
    try:
        conversation = services.conversations.update(
            conversation_id, title=body.title, model=body.model, effort=body.effort, archived=body.archived, current_leaf=body.current_leaf
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    assert conversation is not None
    return _summary(conversation)


@router.delete("/conversations/{conversation_id}", operation_id="delete_conversation", status_code=204, responses=ERROR_RESPONSES)
def delete_conversation(services: ServicesDep, conversation_id: str) -> Response:
    if not services.conversations.delete(conversation_id):
        raise NotFoundError(f"No conversation '{conversation_id}'.")
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages", operation_id="send_conversation_message", responses=SSE_RESPONSES)
def send_message(services: ServicesDep, conversation_id: str, body: MessageSend) -> StreamingResponse:
    """Add a question after ``parent_id`` and stream the reply.

    Events, in order: ``user`` (the question as kept), ``assistant`` (the
    reply as filed), then any of ``stage``, ``notice``, ``reasoning`` and
    ``delta``, and last ``done`` (the reply as kept, with how it ended).
    """
    conversation = _conversation(services, conversation_id)
    if not body.content.strip() and not body.attachment_ids:
        raise ValidationError("Write a message or attach a file.")
    model = _model(services, body.model, conversation.model)
    try:
        question = services.conversations.add_message(conversation_id, body.parent_id, "user", body.content, attachment_ids=body.attachment_ids)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    assert question is not None
    services.conversations.update(conversation_id, model=model, effort=body.effort)
    runner = PlaygroundRunner(services)

    def events() -> Any:
        yield event("user", message=question)
        yield from runner.reply(conversation_id, question.id, model=model, effort=body.effort)

    return _stream(events())


@router.post("/conversations/{conversation_id}/messages/{message_id}/retry", operation_id="retry_conversation_message", responses=SSE_RESPONSES)
def retry_message(services: ServicesDep, conversation_id: str, message_id: str, body: Retry) -> StreamingResponse:
    """Another reply beside ``message_id`` (a reply), or a first one to it (a question left unanswered); events as for a send."""
    conversation = _conversation(services, conversation_id)
    message = services.conversations.message(message_id)
    if message is None or message.conversation_id != conversation_id:
        raise NotFoundError(f"No message '{message_id}' in this conversation.")
    question = message.id if message.role == "user" else message.parent_id
    if question is None:
        raise ValidationError("This reply answers nothing to retry.")
    replied = message.role == "assistant"
    model = _model(services, body.model, message.model if replied else "", conversation.model)
    effort = body.effort if body.effort is not None else (message.effort if replied else conversation.effort)
    runner = PlaygroundRunner(services)
    return _stream(runner.reply(conversation_id, question, model=model, effort=effort))


@router.get("/conversations/{conversation_id}/messages/{message_id}/request", operation_id="get_conversation_message_request", responses=ERROR_RESPONSES)
def get_message_request(services: ServicesDep, conversation_id: str, message_id: str) -> dict[str, Any]:
    """The Chat Completions request behind a reply, as the gateway sent it (for "Copy as API request")."""
    _conversation(services, conversation_id)
    return PlaygroundRunner(services).request(conversation_id, message_id)


@router.post("/conversations/{conversation_id}/branch", operation_id="branch_conversation", status_code=201, responses=ERROR_RESPONSES)
def branch_conversation(services: ServicesDep, conversation_id: str, body: Branch) -> ConversationView:
    """A new conversation holding this one's path up to ``message_id``."""
    _conversation(services, conversation_id)
    try:
        branch = services.conversations.branch(conversation_id, body.message_id)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    assert branch is not None
    return _view(branch)


@router.post("/conversations/{conversation_id}/title", operation_id="title_conversation", responses=ERROR_RESPONSES)
def title_conversation(services: ServicesDep, conversation_id: str) -> ConversationSummary:
    """Have a model name the conversation after its first exchange; a user's own title is kept. Waits for the model."""
    _conversation(services, conversation_id)
    conversation = PlaygroundRunner(services).title(conversation_id)
    assert conversation is not None
    return _summary(conversation)


# -- attachments -------------------------------------------------------------------- #
@router.post("/attachments", operation_id="upload_attachment", status_code=201, responses=ERROR_RESPONSES)
async def upload_attachment(
    services: ServicesDep,
    request: Request,
    name: Annotated[str, Query(description="The file's name")],
    type: Annotated[str, Query(description="The file's own type, when the body is its extracted text (a PDF)")] = "",
    width: int | None = None,
    height: int | None = None,
) -> AttachmentView:
    """The request body is the file: an image (PNG, JPEG, WebP or GIF) as itself, any other file as its UTF-8 text."""
    data = await request.body()
    if not data:
        raise ValidationError("The file is empty.")
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in IMAGE_TYPES:
        info = services.conversations.add_attachment(kind="image", name=name, mime=content_type, data=data, width=width, height=height)
    elif content_type.startswith("text/") or content_type in TEXT_TYPES:
        text = data.decode("utf-8", errors="replace")
        info = services.conversations.add_attachment(kind="file", name=name, mime=type or content_type, data=data, text=text)
    else:
        raise ValidationError(f"Files of type '{content_type or 'unknown'}' cannot be read; send an image, or a file as text.")
    return _attachment(info)


@router.get("/attachments/{attachment_id}", operation_id="get_attachment", responses=ERROR_RESPONSES)
def get_attachment(services: ServicesDep, attachment_id: str) -> Response:
    """An image as itself, a file as its text."""
    attachment = services.conversations.attachment(attachment_id)
    if attachment is None:
        raise NotFoundError(f"No attachment '{attachment_id}'.")
    media_type = attachment.mime if attachment.kind == "image" else "text/plain; charset=utf-8"
    return Response(content=attachment.data, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/attachments/{attachment_id}", operation_id="delete_attachment", status_code=204, responses=ERROR_RESPONSES)
def delete_attachment(services: ServicesDep, attachment_id: str) -> Response:
    """Take back an attachment before it is sent; one a message carries stays."""
    if services.conversations.attachment(attachment_id) is None:
        raise NotFoundError(f"No attachment '{attachment_id}'.")
    if not services.conversations.delete_attachment(attachment_id):
        raise ValidationError("This attachment belongs to a sent message.")
    return Response(status_code=204)


# -- session ------------------------------------------------------------------------ #
@router.get("/session", operation_id="get_playground_session")
def get_session(services: ServicesDep) -> Session:
    return Session(conversation_id=services.conversations.session())


@router.put("/session", operation_id="set_playground_session", responses=ERROR_RESPONSES)
def set_session(services: ServicesDep, body: Session) -> Session:
    if body.conversation_id is not None and services.conversations.get(body.conversation_id) is None:
        raise ValidationError(f"No conversation '{body.conversation_id}' to open.")
    services.conversations.set_session(body.conversation_id)
    return Session(conversation_id=services.conversations.session())
