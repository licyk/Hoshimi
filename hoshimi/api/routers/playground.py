"""Tool-authenticated Playground routes: chat on the shared inference path, and kept conversations."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Query, Response

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import ERROR_RESPONSES
from hoshimi.api.routers.chat import chat_completion
from hoshimi.api.routers.models import ModelList, list_models
from hoshimi.core.conversations import Conversation, Message
from hoshimi.core.errors import NotFoundError, ValidationError
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1/playground", tags=["playground"])
router.add_api_route("/models", list_models, methods=["GET"], response_model=ModelList, operation_id="list_playground_models")


@router.post("/chat/completions", operation_id="create_playground_chat_completion")
def create_playground_chat_completion(
    services: ServicesDep,
    body: Annotated[dict[str, Any], Body(description="OpenAI Chat Completions request")],
) -> Any:
    """The Chat Completions route, filed under its own origin so a test from the web UI is not a client's call.

    The system prompt from Settings (``playground.system_prompt``) goes first,
    unless the request brings a system message of its own. It is added here,
    per request, rather than kept in each conversation: a change in Settings
    applies to every conversation from its next message on.
    """
    prompt = services.settings.playground_system_prompt
    messages = body.get("messages")
    if prompt and isinstance(messages, list) and not any(isinstance(m, dict) and m.get("role") in ("system", "developer") for m in messages):
        body = {**body, "messages": [{"role": "system", "content": prompt}, *messages]}
    return chat_completion(services, body, origin="playground")


# -- conversations ------------------------------------------------------------ #
class MessageView(Record):
    role: Literal["user", "assistant"]
    content: str
    model: str = ""
    """The model that wrote an assistant message; empty for the user's."""
    created_at: float


class ConversationSummary(Record):
    id: str
    title: str
    """The first thing asked, cut to one line; empty until something is."""
    model: str
    effort: str
    archived: bool
    created_at: float
    updated_at: float
    """Unix seconds of the last message, which orders the list."""


class ConversationView(ConversationSummary):
    messages: list[MessageView]


class ConversationList(Record):
    conversations: list[ConversationSummary]


class ConversationCreate(Record):
    model: str = ""
    effort: str = ""
    title: str = ""


class ConversationPatch(Record):
    """What to change; a field left out stays as it is."""

    title: str | None = None
    model: str | None = None
    effort: str | None = None
    archived: bool | None = None


class MessageCreate(Record):
    role: Literal["user", "assistant"]
    content: str
    model: str = ""


class Session(Record):
    """The conversation open last, so the Playground can return to it; None for a new one."""

    conversation_id: str | None = None


def _summary(c: Conversation) -> ConversationSummary:
    return ConversationSummary(id=c.id, title=c.title, model=c.model, effort=c.effort, archived=c.archived, created_at=c.created_at, updated_at=c.updated_at)


def _view(c: Conversation) -> ConversationView:
    return ConversationView(**_summary(c).model_dump(), messages=[_message(m) for m in c.messages])


def _message(m: Message) -> MessageView:
    return MessageView(role=m.role, content=m.content, model=m.model, created_at=m.created_at)


def _missing(conversation_id: str) -> NotFoundError:
    return NotFoundError(f"No conversation '{conversation_id}'.")


@router.get("/conversations", operation_id="list_conversations")
def list_conversations(services: ServicesDep, archived: Annotated[bool, Query(description="The archive instead of the recent ones")] = False) -> ConversationList:
    return ConversationList(conversations=[_summary(c) for c in services.conversations.conversations(archived=archived)])


@router.post("/conversations", operation_id="create_conversation", status_code=201)
def create_conversation(services: ServicesDep, body: ConversationCreate) -> ConversationView:
    return _view(services.conversations.create(model=body.model, effort=body.effort, title=body.title))


@router.get("/conversations/{conversation_id}", operation_id="get_conversation", responses=ERROR_RESPONSES)
def get_conversation(services: ServicesDep, conversation_id: str) -> ConversationView:
    conversation = services.conversations.get(conversation_id)
    if conversation is None:
        raise _missing(conversation_id)
    return _view(conversation)


@router.patch("/conversations/{conversation_id}", operation_id="update_conversation", responses=ERROR_RESPONSES)
def update_conversation(services: ServicesDep, conversation_id: str, body: ConversationPatch) -> ConversationSummary:
    conversation = services.conversations.update(conversation_id, title=body.title, model=body.model, effort=body.effort, archived=body.archived)
    if conversation is None:
        raise _missing(conversation_id)
    return _summary(conversation)


@router.delete("/conversations/{conversation_id}", operation_id="delete_conversation", status_code=204, responses=ERROR_RESPONSES)
def delete_conversation(services: ServicesDep, conversation_id: str) -> Response:
    if not services.conversations.delete(conversation_id):
        raise _missing(conversation_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages", operation_id="append_conversation_message", status_code=201, responses=ERROR_RESPONSES)
def append_message(services: ServicesDep, conversation_id: str, body: MessageCreate) -> MessageView:
    """Add a message at the end. The first user message titles an untitled conversation; any message unarchives it."""
    message = services.conversations.append(conversation_id, body.role, body.content, model=body.model)
    if message is None:
        raise _missing(conversation_id)
    return _message(message)


@router.get("/session", operation_id="get_playground_session")
def get_session(services: ServicesDep) -> Session:
    return Session(conversation_id=services.conversations.session())


@router.put("/session", operation_id="set_playground_session", responses=ERROR_RESPONSES)
def set_session(services: ServicesDep, body: Session) -> Session:
    if body.conversation_id is not None and services.conversations.get(body.conversation_id) is None:
        raise ValidationError(f"No conversation '{body.conversation_id}' to open.")
    services.conversations.set_session(body.conversation_id)
    return Session(conversation_id=body.conversation_id)
