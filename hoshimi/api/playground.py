"""Running a Playground conversation on the server.

The page says what to add and where (``parent_id``); everything a model is
sent is put together here, from the conversation's path:

- the system prompt from Settings first (``playground.system_prompt``);
- a file's text inline, under its name, cut at :data:`FILE_TEXT_LIMIT`;
- an image as an image for a model that reads them, and otherwise as what a
  model that does read them saw in it (``playground.vision_model``: ``auto``
  picks one from the catalogue, ``off`` never asks). The description is kept
  with the image, so it is asked for once. With no such model, the image is
  left out and the reply says so. A model said to read images whose station
  refuses them is asked once more, with descriptions.

The reply streams back as events (see :func:`event`) while it is kept: it is
filed as ``streaming`` before the first word, and settles as ``complete``,
``stopped`` (the page stopped it or went away) or ``error``. Titles are
written by a model on request, once the first reply is in.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from typing import Any

from hoshimi.api.inference import tracked_chunks
from hoshimi.core.context import Services
from hoshimi.core.conversations import Attachment, ChatMessage, Conversation, ConversationStore, Status, path_to
from hoshimi.core.errors import HoshimiError, ValidationError

logger = logging.getLogger(__name__)

FILE_TEXT_LIMIT = 200_000
"""The most of one file's text a model is sent; the rest is cut, and the model told so."""

TITLE_LIMIT = 80

VISION_PROMPT = (
    "Describe this image for someone who will answer questions about it without seeing it. "
    "Say what it shows and how it is laid out, transcribe any text in it exactly, and give the values of any "
    "chart or table. Reply with the description only."
)
TITLE_PROMPT = (
    "You name conversations. Reply with a title for the conversation below: at most six words, in the language "
    "the user wrote in, with no quotation marks and no full stop. Reply with the title only."
)

UNREADABLE_STATES = {"unavailable", "region-blocked"}
"""Probe verdicts that rule a model out as the one to describe images."""

_IMAGE_REFUSAL = re.compile(r"image|vision|multimodal|multi-modal|image_url|visual", re.IGNORECASE)


def event(kind: str, **fields: Any) -> dict[str, Any]:
    """One event of a streamed reply.

    - ``user``: the question as kept (``message``), sent first;
    - ``assistant``: the reply as filed, before its first word (``message``);
    - ``stage``: what is happening before the reply starts (``stage``: ``describing``, with ``model``);
    - ``notice``: something the reader should know about this reply (``notice``);
    - ``delta``: more of the reply (``text``);
    - ``reasoning``: more of what the model reasons before it answers (``text``), for a model that shows it;
    - ``done``: the reply as kept, with how it ended (``message``).
    """
    return {"type": kind, **fields}


@dataclass
class Prepared:
    """What a model is sent for one reply, and what the reader is told about how it was made."""

    messages: list[dict[str, Any]]
    notices: list[dict[str, Any]] = field(default_factory=list)
    images: bool = False
    """Whether images go as images (and a refusal could be about them)."""


@dataclass
class Thought:
    """What a reply reasoned, and when: from its first reasoning to its first word (or to the end)."""

    parts: list[str] = field(default_factory=list)
    started: float | None = None
    ended: float | None = None

    @property
    def text(self) -> str:
        return "".join(self.parts)

    @property
    def ms(self) -> int:
        if self.started is None:
            return 0
        return round(((self.ended or time.monotonic()) - self.started) * 1000)


class PlaygroundRunner:
    def __init__(self, services: Services) -> None:
        self.services = services

    @property
    def store(self) -> ConversationStore:
        return self.services.conversations

    # -- models ------------------------------------------------------------- #
    def sees_images(self, model: str) -> bool:
        spec = self.services.registry.spec(model)
        return bool(spec and spec.vision)

    def vision_model(self, *, besides: str = "") -> str | None:
        """The model that describes images for one that cannot read them; None when there is none to ask."""
        setting = self.services.settings.playground_vision_model
        if setting == "off":
            return None
        if setting and setting != "auto":
            return setting
        for spec in self.services.registry.catalog():
            if not spec.vision or spec.id == besides:
                continue
            verdict = self.services.probe.state_for(spec.id)
            if verdict and verdict.get("state") in UNREADABLE_STATES:
                continue
            return spec.id
        return None

    def _quiet(self, model: str) -> dict[str, Any]:
        """The least thinking a model allows, for the small asks (titles, descriptions)."""
        spec = self.services.registry.spec(model)
        if spec is None:
            return {}
        if spec.effort_off_id:
            return {"reasoning_effort": spec.effort_off_id}
        if spec.efforts:
            return {"reasoning_effort": spec.efforts[0]}
        return {}

    def _ask(self, model: str, messages: list[dict[str, Any]], *, origin: str) -> str:
        """One whole answer from ``model``, not streamed."""
        body = {"model": model, "messages": messages, "stream": False, **self._quiet(model)}
        text: list[str] = []
        for chunk in tracked_chunks(self.services, model, body, origin=origin, stream=False):
            kind = chunk.get("type")
            if kind == "text_delta":
                text.append(str(chunk.get("text") or ""))
            elif kind == "error":
                raise HoshimiError(str(chunk.get("message") or "The model failed."))
        return "".join(text).strip()

    # -- images and files ---------------------------------------------------- #
    def describe(self, attachment: Attachment, model: str) -> str:
        """What ``model`` sees in an image; kept with it, so it is asked once."""
        if attachment.description:
            return attachment.description
        url = f"data:{attachment.mime};base64,{base64.b64encode(attachment.data).decode()}"
        text = self._ask(model, [{"role": "user", "content": [{"type": "text", "text": VISION_PROMPT}, {"type": "image_url", "image_url": {"url": url}}]}], origin="playground-vision")
        if text:
            self.store.describe(attachment.id, text)
        return text

    def prepare(self, conversation: Conversation, path: list[ChatMessage], model: str, *, describe: bool) -> Prepared:
        """The Chat Completions messages for a reply after ``path``.

        ``describe`` sends images as descriptions rather than images; it is
        what happens for a model that cannot read them.
        """
        prepared = Prepared(messages=[])
        prompt = self.services.settings.playground_system_prompt
        if prompt:
            prepared.messages.append({"role": "system", "content": prompt})
        describer: str | None = None
        asked = False
        for message in path:
            if message.role == "assistant":
                if message.content:
                    prepared.messages.append({"role": "assistant", "content": message.content})
                continue
            parts: list[dict[str, Any]] = []
            text = message.content
            for info in message.attachments:
                attachment = self.store.attachment(info.id)
                if attachment is None:
                    continue
                if attachment.kind == "file":
                    body = attachment.text
                    cut = len(body) > FILE_TEXT_LIMIT
                    body = body[:FILE_TEXT_LIMIT] + ("\n[… the rest of the file was cut]" if cut else "")
                    text += f'\n\n<file name="{attachment.name}">\n{body}\n</file>'
                    continue
                if not describe:
                    prepared.images = True
                    url = f"data:{attachment.mime};base64,{base64.b64encode(attachment.data).decode()}"
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                    continue
                if not asked:
                    describer, asked = self.vision_model(besides=model), True
                if describer is None:
                    text += f"\n\n[Image “{attachment.name}” left out: this model cannot read images.]"
                    prepared.notices.append({"kind": "image-omitted", "name": attachment.name})
                    continue
                try:
                    description = self.describe(attachment, describer)
                except HoshimiError as exc:
                    logger.info("could not describe %s with %s: %s", attachment.name, describer, exc)
                    description = ""
                if description:
                    text += f"\n\n[Image “{attachment.name}”, as described by {describer}:]\n{description}"
                    prepared.notices.append({"kind": "image-described", "name": attachment.name, "model": describer})
                else:
                    text += f"\n\n[Image “{attachment.name}” left out: it could not be described.]"
                    prepared.notices.append({"kind": "image-omitted", "name": attachment.name})
            content: Any = [{"type": "text", "text": text}, *parts] if parts else text
            prepared.messages.append({"role": "user", "content": content})
        return prepared

    def request(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        """The Chat Completions request that produced a reply (or would answer a question), as sent."""
        conversation = self.store.get(conversation_id)
        if conversation is None:
            raise HoshimiError(f"No conversation '{conversation_id}'.")
        message = next((m for m in conversation.messages if m.id == message_id), None)
        if message is None:
            raise ValidationError(f"No message '{message_id}' in this conversation.")
        leaf = message.parent_id if message.role == "assistant" else message.id
        path = path_to(conversation.messages, leaf or "")
        model = message.model if message.role == "assistant" and message.model else conversation.model
        effort = message.effort if message.role == "assistant" else conversation.effort
        prepared = self.prepare(conversation, path, model, describe=not self.sees_images(model) or any(n.get("kind") == "image-described" for n in message.notices))
        body: dict[str, Any] = {"model": model, "messages": prepared.messages, "stream": True}
        if effort:
            body["reasoning_effort"] = effort
        return body

    def _undescribed(self, path: list[ChatMessage]) -> bool:
        """Whether an image on ``path`` has yet to be described (so a model will be asked first)."""
        for message in path:
            for info in message.attachments:
                if info.kind == "image":
                    attachment = self.store.attachment(info.id)
                    if attachment is not None and not attachment.description:
                        return True
        return False

    # -- replies ------------------------------------------------------------- #
    def reply(self, conversation_id: str, parent_id: str, *, model: str, effort: str) -> Iterator[dict[str, Any]]:
        """Stream and keep a new reply to the question ``parent_id``."""
        conversation = self.store.get(conversation_id)
        assert conversation is not None
        message = self.store.add_message(conversation_id, parent_id, "assistant", "", model=model, effort=effort, status="streaming")
        assert message is not None
        yield event("assistant", message=message)
        path = path_to(conversation.messages, parent_id)
        text: list[str] = []
        thought = Thought()
        notices: list[dict[str, Any]] = []
        status: Status = "complete"
        error = ""
        try:
            describe = not self.sees_images(model)
            for attempt in range(2):
                if describe and self._undescribed(path):
                    describer = self.vision_model(besides=model)
                    if describer:
                        yield event("stage", stage="describing", model=describer)
                prepared = self.prepare(conversation, path, model, describe=describe)
                for notice in prepared.notices:
                    if notice not in notices:
                        notices.append(notice)
                        yield event("notice", notice=notice)
                body: dict[str, Any] = {"model": model, "messages": prepared.messages, "stream": True}
                if effort:
                    body["reasoning_effort"] = effort
                try:
                    yield from self._stream(model, body, text, thought)
                    break
                except HoshimiError as exc:
                    # A station that refuses images, before a word of the reply: once more, with descriptions.
                    if attempt == 0 and prepared.images and not text and _IMAGE_REFUSAL.search(exc.message or str(exc)):
                        describe = True
                        notice = {"kind": "image-refused", "model": model}
                        notices.append(notice)
                        yield event("notice", notice=notice)
                        continue
                    raise
        except GeneratorExit:
            # The page stopped the reply, or went away: what came is kept.
            self.store.finish_message(message.id, content="".join(text), status="stopped", notices=notices, reasoning=thought.text, reasoning_ms=thought.ms)
            raise
        except HoshimiError as exc:
            status, error = "error", exc.message or str(exc)
        except Exception as exc:  # the reply must still be settled
            logger.exception("Playground reply failed")
            status, error = "error", str(exc) or exc.__class__.__name__
        self.store.finish_message(message.id, content="".join(text), status=status, error=error, notices=notices, reasoning=thought.text, reasoning_ms=thought.ms)
        yield event("done", message=self.store.message(message.id))

    def _stream(self, model: str, body: dict[str, Any], text: list[str], thought: Thought) -> Iterator[dict[str, Any]]:
        for chunk in tracked_chunks(self.services, model, body, origin="playground", stream=True):
            kind = chunk.get("type")
            if kind == "text_delta":
                piece = str(chunk.get("text") or "")
                if piece:
                    if thought.started is not None and thought.ended is None:
                        thought.ended = time.monotonic()
                    text.append(piece)
                    yield event("delta", text=piece)
            elif kind == "reasoning_delta":
                piece = str(chunk.get("text") or "")
                if piece:
                    if thought.started is None:
                        thought.started = time.monotonic()
                    thought.parts.append(piece)
                    yield event("reasoning", text=piece)
            elif kind == "error":
                raise HoshimiError(str(chunk.get("message") or "The upstream stream failed."))

    # -- titles -------------------------------------------------------------- #
    def title(self, conversation_id: str) -> Conversation | None:
        """Name a conversation after its first exchange; a user's own title, or a temporary conversation, is left alone."""
        conversation = self.store.get(conversation_id)
        if conversation is None or conversation.temporary or conversation.title_state in ("user", "auto"):
            return conversation
        path = path_to(conversation.messages, conversation.current_leaf)
        question = next((m for m in path if m.role == "user"), None)
        answer = next((m for m in path if m.role == "assistant" and m.content), None)
        if question is None:
            return conversation
        model = self.services.settings.playground_title_model or conversation.model or (answer.model if answer else "")
        if not model:
            return conversation
        exchange = f"User: {question.content[:2000]}" + (f"\n\nAssistant: {answer.content[:2000]}" if answer else "")
        try:
            raw = self._ask(model, [{"role": "system", "content": TITLE_PROMPT}, {"role": "user", "content": exchange}], origin="playground-title")
        except HoshimiError as exc:
            logger.info("could not title %s with %s: %s", conversation_id, model, exc)
            return conversation
        title = clean_title(raw)
        if not title:
            return conversation
        return self.store.update(conversation_id, title=title, title_state="auto")


def clean_title(raw: str) -> str:
    """A model's title as a title: its first line, without quotes, labels or a closing full stop."""
    line = next((part.strip() for part in raw.splitlines() if part.strip()), "")
    line = re.sub(r"^(title|标题|タイトル)\s*[:：]\s*", "", line, flags=re.IGNORECASE)
    # Quotes, emphasis and a closing full stop, in whichever order the model put them.
    previous = None
    while previous != line:
        previous = line
        line = line.strip().strip("\"'“”‘’「」『』*#`").rstrip(".。").strip()
    return line[:TITLE_LIMIT]


def sse(events: Iterator[dict[str, Any]]) -> Generator[str, None, None]:
    """Events as Server-Sent Events, one JSON object per ``data:`` line; closing this closes ``events``."""
    try:
        for item in events:
            yield f"data: {json.dumps(item, default=_jsonable, ensure_ascii=False)}\n\n"
    finally:
        close = getattr(events, "close", None)
        if close is not None:
            close()


def _jsonable(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")
