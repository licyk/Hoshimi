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

With tools turned on (``playground.tools``, less the ones a conversation
turned off), a reply is an agent loop: the model is offered the built-in
tools (:mod:`hoshimi.core.agent`); each round it streams, the tools it called
run one after another (after the user approves the ones the approval mode
asks about: see :func:`PlaygroundRunner.approval_mode`), their results go back to it, and it goes
on until a round calls nothing.
Such a reply keeps its *steps*, in order:

- ``{"type": "reasoning", "text", "ms", "round"}``;
- ``{"type": "text", "text", "round"}``;
- ``{"type": "user", "id", "text", "round"}``: a message the user sent while
  the reply ran (it *steered* it), read by the model at the start of its round;
- ``{"type": "tool", "id", "name", "arguments", "status", "output", "exit_code",
  "ms", "meta", "approval", "round"}``, where ``status`` is ``pending`` (being
  written), ``waiting`` (for approval, ``approval`` saying why), ``running``,
  ``done``, ``error``, ``denied`` or ``stopped``.

Its ``content`` is its text steps together. Sent to a model again, each round
becomes its steering messages (as user messages), an assistant message with its
``tool_calls`` and a ``tool`` message per call (or, with no tools offered, the
same as text).

A reply with tools can be steered: what the user sends while it runs
(:class:`hoshimi.core.agent.runtime.Steering`) is read at the next round,
after the calls under way. One sent as the model finishes keeps the reply
going for another round, to answer it.

The reply streams back as events (see :func:`event`) while it is kept: it is
filed as ``streaming`` before the first word, and settles as ``complete``,
``stopped`` (the page stopped it or went away) or ``error``, with the tokens
its rounds took (``usage``: the ``last`` round's and the ``total``, as the
station reported them, and the ``context`` it ended with) and how long it
worked (``duration_ms``, less the time it waited for approvals). Titles are
written by a model on request, once the first reply is in.

A conversation that nears its model's context window is compacted (see
:mod:`hoshimi.api.compaction`): as a reply begins, between its rounds of tool
calls, when the station refuses a round as too long (then the round is asked
again), or when the user asks (a ``checkpoint`` message). A compaction inside a
reply is one of its steps, ``{"type": "compaction", ...}``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from hoshimi.api import compaction
from hoshimi.api.inference import tracked_chunks
from hoshimi.core.agent.base import Tool, ToolContext, ToolError, needs_approval
from hoshimi.core.agent.context_window import Budget, budget, estimate, text_tokens
from hoshimi.core.agent.runtime import Steer
from hoshimi.core.agent.tools import GROUPS, parse_arguments, run_tool, tools_for, unsupported
from hoshimi.core.context import Services
from hoshimi.core.conversations import Attachment, ChatMessage, Conversation, ConversationStore, Status, path_to
from hoshimi.core.errors import ConflictError, HoshimiError, ValidationError
from hoshimi.core.failure import context_limit_in, is_context_overflow
from hoshimi.core.providers.base import ContextOverflow

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
_TOOLS_REFUSAL = re.compile(r"\btools?\b|tool_choice|function[ _-]?call", re.IGNORECASE)

TOO_LONG = "The conversation no longer fits in {model}'s context window, even compacted. Start a new chat, or branch it from an earlier message."
SUMMARY_SHARE = 0.75
"""The most of the summarising model's window the conversation it summarises may take."""


class Stopped(Exception):
    """The page stopped a reply (or a compaction) while a model was being asked."""


def overflowed(exc: HoshimiError) -> bool:
    """Whether a failure is the station refusing a request too long for the model's window."""
    return isinstance(exc, ContextOverflow) or is_context_overflow(None, exc.message or str(exc))


def usage_tokens(usage: dict[str, Any]) -> dict[str, int]:
    """A station's usage as the Playground keeps it: input (cached among it), output (reasoning among it)."""
    prompt = usage.get("prompt_tokens_details") or {}
    completion = usage.get("completion_tokens_details") or {}
    return {
        "input": int(usage.get("prompt_tokens") or 0),
        "cached": int((prompt.get("cached_tokens") if isinstance(prompt, dict) else 0) or usage.get("cache_read_input_tokens") or 0),
        "output": int(usage.get("completion_tokens") or 0),
        "reasoning": int((completion.get("reasoning_tokens") if isinstance(completion, dict) else 0) or 0),
    }


def event(kind: str, **fields: Any) -> dict[str, Any]:
    """One event of a streamed reply.

    - ``user``: the question as kept (``message``), sent first;
    - ``assistant``: the reply as filed, before its first word (``message``);
    - ``stage``: what is happening before the reply starts (``stage``: ``describing``, with ``model``);
    - ``notice``: something the reader should know about this reply (``notice``);
    - ``delta``: more of the reply (``text``);
    - ``reasoning``: more of what the model reasons before it answers (``text``), for a model that shows it;
    - ``tool``: a tool call as it now stands (``step``, a tool step; the page replaces the one with its ``id``);
    - ``steer``: a message the user sent while the reply ran, now read by the model (``step``, a user step with the ``id`` the send was given);
    - ``usage``: what the reply has taken so far, after each round (``usage``, as kept on the reply), against the
      model's ``window`` and the ``limit`` at which it is compacted;
    - ``compaction``: the conversation being compacted, as it now stands (``step``, a compaction step; replaced by ``id`` as a tool's is);
    - ``checkpoint``: the user's own compaction, filed before the summary is written (``message``, a checkpoint);
    - ``done``: the reply (or checkpoint) as kept, with how it ended (``message``).

    ``delta`` and ``reasoning`` add to the last step when it is of their kind,
    and start a new step otherwise.
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
class Live:
    """The messages of a reply under way, and what its next request takes of the window: the last count a station
    reported (``base``, for the messages before ``mark``) plus an estimate of what came after it, or an estimate whole."""

    messages: list[dict[str, Any]]
    definitions: list[dict[str, Any]]
    budget: Budget
    system: dict[str, Any] | None = None
    question: dict[str, Any] | None = None
    base: int = 0
    mark: int | None = None

    def used(self, messages: list[dict[str, Any]] | None = None) -> tuple[int, str]:
        messages = self.messages if messages is None else messages
        if self.mark is None or self.mark > len(messages):
            return estimate(messages, self.definitions), "estimate"
        return self.base + estimate(messages[self.mark:]), "usage"

    def compactable(self) -> bool:
        """Whether there is anything before the question to summarise."""
        return any(m is not self.system and m is not self.question for m in self.messages)


@dataclass
class Run:
    """A reply as it is made: its steps in order, the round of tool calls it is in, what it has taken, and how long it has worked."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    round: int = 0
    usage_last: dict[str, int] = field(default_factory=dict)
    usage_total: dict[str, int] = field(default_factory=dict)
    usage_rounds: int = 0
    """How many of its rounds a station reported usage for."""
    context: int = 0
    """What of the window its last request (with its answer) took."""
    context_source: str = "estimate"
    started: float = field(default_factory=time.monotonic)
    waited: float = 0.0
    """Seconds it waited for the user to approve calls."""
    _reasoning_since: float | None = None

    def _last(self, kind: str) -> dict[str, Any] | None:
        last = self.steps[-1] if self.steps else None
        return last if last is not None and last["type"] == kind and last["round"] == self.round else None

    def add_text(self, piece: str) -> None:
        self.end_reasoning()
        step = self._last("text")
        if step is None:
            self.steps.append({"type": "text", "text": piece, "round": self.round})
        else:
            step["text"] += piece

    def add_reasoning(self, piece: str) -> None:
        step = self._last("reasoning")
        if step is None:
            self._reasoning_since = time.monotonic()
            self.steps.append({"type": "reasoning", "text": piece, "ms": 0, "round": self.round})
        else:
            step["text"] += piece

    def end_reasoning(self) -> None:
        """The reasoning step going on (if any) ends: the model has moved on to answering or calling."""
        if self._reasoning_since is None:
            return
        for step in reversed(self.steps):
            if step["type"] == "reasoning":
                step["ms"] = round((time.monotonic() - self._reasoning_since) * 1000)
                break
        self._reasoning_since = None

    def add_user(self, steer_id: str, text: str) -> dict[str, Any]:
        self.end_reasoning()
        step = {"type": "user", "id": steer_id, "text": text, "round": self.round}
        self.steps.append(step)
        return step

    def add_step(self, step: dict[str, Any]) -> dict[str, Any]:
        self.end_reasoning()
        self.steps.append(step)
        return step

    def add_usage(self, usage: dict[str, Any]) -> None:
        tokens = usage_tokens(usage)
        self.usage_last = tokens
        for key, value in tokens.items():
            self.usage_total[key] = self.usage_total.get(key, 0) + value
        self.usage_rounds += 1

    def said(self, round_: int) -> bool:
        """Whether the model has said, reasoned or called anything in this round."""
        return any(s["round"] == round_ and s["type"] in ("text", "reasoning", "tool") for s in self.steps)

    @property
    def quiet(self) -> bool:
        """Nothing of the model's yet (a compaction is the gateway's own doing)."""
        return all(s["type"] == "compaction" for s in self.steps)

    def usage(self) -> dict[str, Any]:
        """What it took, as kept on the reply."""
        if not self.usage_rounds and not self.context:
            return {}
        return {"last": self.usage_last, "total": self.usage_total, "rounds": self.usage_rounds, "context": self.context, "source": self.context_source}

    @property
    def duration_ms(self) -> int:
        return max(0, round((time.monotonic() - self.started - self.waited) * 1000))

    def add_call(self, call_id: str, name: str) -> dict[str, Any]:
        self.end_reasoning()
        if not call_id or any(s.get("id") == call_id for s in self.steps):
            call_id = f"call_{uuid.uuid4().hex[:24]}"
        step = {"type": "tool", "id": call_id, "name": name, "arguments": "", "status": "pending", "output": "", "exit_code": None, "ms": 0,
                "meta": {}, "approval": "", "round": self.round}
        self.steps.append(step)
        return step

    @property
    def text(self) -> str:
        return "\n\n".join(s["text"] for s in self.steps if s["type"] == "text" and s["text"].strip())

    @property
    def reasoning(self) -> str:
        return "\n\n".join(s["text"] for s in self.steps if s["type"] == "reasoning")

    @property
    def reasoning_ms(self) -> int:
        return sum(int(s.get("ms") or 0) for s in self.steps if s["type"] == "reasoning")

    @property
    def calls(self) -> list[dict[str, Any]]:
        return [s for s in self.steps if s["type"] == "tool"]

    def kept_steps(self) -> list[dict[str, Any]]:
        """The steps to keep: a reply that called no tool, was not steered and was not compacted is its text and reasoning alone."""
        return self.steps if any(s["type"] in ("tool", "user", "compaction") for s in self.steps) else []

    def stop_calls(self) -> None:
        """Calls (and a compaction) the stop cut short before they ran."""
        for step in self.steps:
            if step["type"] == "tool" and step["status"] in ("pending", "waiting", "running"):
                step["status"] = "stopped"
                step["output"] = step["output"] or STOPPED_CALL
            elif step["type"] == "compaction" and step["status"] == "running":
                step["status"] = "stopped"


STOPPED_CALL = "The user stopped the reply before this call finished."
DENIED_CALL = "The user declined this call; it did not run. Do not try it again unless they ask."


def call_result(step: dict[str, Any]) -> str:
    """What a model is told a call gave."""
    if step["status"] == "stopped":
        return step["output"] or STOPPED_CALL
    if step["status"] == "denied":
        return DENIED_CALL
    return step["output"] or "(no output)"


def replay(message: ChatMessage, *, tools: bool, from_round: int = 0) -> list[dict[str, Any]]:
    """A kept reply as a model is sent it again: per round, what it said and called, then what each call gave.

    With no tools offered now, the calls and their results go as text, so no
    station is sent tool calls it was not told about. ``from_round`` leaves out
    the rounds before it (a compaction inside the reply summarised them).
    """
    if not message.steps:
        return [{"role": "assistant", "content": message.content}] if message.content and not from_round else []
    return replay_steps(message.steps, tools=tools, from_round=from_round)


def replay_steps(steps: list[dict[str, Any]], *, tools: bool, from_round: int = 0, only_round: int | None = None) -> list[dict[str, Any]]:
    """A reply's steps as messages, from ``from_round`` on (or ``only_round`` alone); see :func:`replay`."""
    out: list[dict[str, Any]] = []
    rounds: dict[int, list[dict[str, Any]]] = {}
    for step in steps:
        number = int(step.get("round") or 0)
        if step.get("type") in ("user", "text", "tool") and number >= from_round and (only_round is None or number == only_round):
            rounds.setdefault(number, []).append(step)
    for number in sorted(rounds):
        steps = rounds[number]
        out.extend({"role": "user", "content": s["text"]} for s in steps if s["type"] == "user")
        text = "".join(s["text"] for s in steps if s["type"] == "text")
        calls = [s for s in steps if s["type"] == "tool"]
        if not calls:
            if text.strip():
                out.append({"role": "assistant", "content": text})
            continue
        if not tools:
            done = "\n\n".join(f"[Called {c['name']}({c['arguments']}); it gave:]\n{call_result(c)}" for c in calls)
            out.append({"role": "assistant", "content": (text + "\n\n" if text.strip() else "") + done})
            continue
        out.append({
            "role": "assistant",
            "content": text or None,
            "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for c in calls],
        })
        out.extend({"role": "tool", "tool_call_id": c["id"], "content": call_result(c)} for c in calls)
    return out


def last_compaction(path: list[ChatMessage]) -> tuple[int, dict[str, Any] | None]:
    """Where on ``path`` the last finished compaction is (a checkpoint, or a reply compacted inside), and its record."""
    for at in range(len(path) - 1, -1, -1):
        if path[at].role in ("checkpoint", "assistant"):
            record = compaction.last_done(path[at].steps)
            if record is not None:
                return at, record
    return -1, None


def after_compaction(steps: list[dict[str, Any]], record: dict[str, Any], *, tools: bool) -> list[dict[str, Any]]:
    """What of a reply compacted inside follows its summary: the round just made (long outputs cut), and every round since."""
    round_ = int(record.get("round") or 0)
    before = replay_steps(steps, tools=tools, only_round=round_ - 1) if round_ > 0 else []
    return compaction.elide(before) + replay_steps(steps, tools=tools, from_round=round_)


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

    def prepare(self, conversation: Conversation, path: list[ChatMessage], model: str, *, describe: bool, tools: bool = False, ask: bool = True) -> Prepared:
        """The Chat Completions messages for a reply after ``path``.

        ``describe`` sends images as descriptions rather than images; it is
        what happens for a model that cannot read them. ``tools`` says tools
        are offered, so earlier calls go as calls (see :func:`replay`). After
        a compaction on the path, the messages start from its summary (see
        :mod:`hoshimi.api.compaction`). ``ask`` False asks no model to describe
        an image, for a count that must not wait on one.
        """
        prepared = Prepared(messages=[])
        prompt = self.services.settings.playground_system_prompt
        if prompt:
            prepared.messages.append({"role": "system", "content": prompt})
        describer: str | None = None
        asked = False

        def user(message: ChatMessage) -> dict[str, Any]:
            nonlocal describer, asked
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
                    description = self.describe(attachment, describer) if ask else attachment.description
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
            return {"role": "user", "content": content}

        at, record = last_compaction(path)
        if record is not None:
            holder = path[at]
            prepared.messages.extend(compaction.prefix(record))
            if holder.role == "assistant":
                # Compacted inside this reply: the question it answers, the round just made, and the rounds since.
                if at > 0 and path[at - 1].role == "user":
                    prepared.messages.append(user(path[at - 1]))
                prepared.messages.extend(after_compaction(holder.steps, record, tools=tools))
            path = path[at + 1:]
        for message in path:
            if message.role == "checkpoint":
                continue
            if message.role == "assistant":
                prepared.messages.extend(replay(message, tools=tools))
                continue
            prepared.messages.append(user(message))
        return prepared

    # -- tools -------------------------------------------------------------- #
    def groups(self, conversation: Conversation) -> list[str]:
        """The tools a conversation uses: those Settings turns on for new ones, as the conversation turned them on or off."""
        defaults = self.services.settings.playground_tools
        return [g for g in GROUPS if (g in defaults or g in conversation.tools_on) and g not in conversation.tools_off]

    def tools(self, conversation: Conversation, model: str) -> list[Tool]:
        """The tools offered to ``model`` in ``conversation``; none for a model without tool calls."""
        groups = self.groups(conversation)
        spec = self.services.registry.spec(model)
        if not groups or (spec is not None and spec.tools is False):
            return []
        return tools_for(groups)

    def workspace(self) -> Path:
        configured = self.services.settings.playground_workspace
        return Path(os.path.expanduser(configured)) if configured else self.services.data_dir / "workspace"

    def approval_mode(self, conversation_id: str) -> str:
        """A conversation's own approval mode (``ask``, ``auto`` or ``full``), or ``playground.approval`` for one that chose none."""
        return self.store.approval(conversation_id) or self.services.settings.playground_approval

    def context(self, conversation_id: str, cancel: threading.Event) -> ToolContext:
        return ToolContext(
            workspace=self.workspace(), conversation_id=conversation_id, cancel=cancel, terminals=self.services.agent.terminals,
            network=self.services.settings.network, search_url=self.services.settings.playground_search_url,
        )

    def request(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        """The Chat Completions request that produced a reply (or would answer a question), as sent: its first round, for one that used tools."""
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
        tools = self.tools(conversation, model)
        describe = not self.sees_images(model) or any(n.get("kind") == "image-described" for n in message.notices)
        prepared = self.prepare(conversation, path, model, describe=describe, tools=bool(tools))
        return self._body(model, effort, prepared.messages, tools)

    @staticmethod
    def _body(model: str, effort: str, messages: list[dict[str, Any]], tools: list[Tool]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = [t.definition() for t in tools]
            body["tool_choice"] = "auto"
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
    def reply(self, conversation_id: str, parent_id: str, *, model: str, effort: str, cancel: threading.Event | None = None) -> Iterator[dict[str, Any]]:
        """Stream and keep a new reply to the question ``parent_id``; ``cancel`` is set when the page stops it."""
        cancel = cancel or threading.Event()
        conversation = self.store.get(conversation_id)
        assert conversation is not None
        message = self.store.add_message(conversation_id, parent_id, "assistant", "", model=model, effort=effort, status="streaming")
        assert message is not None
        busy = self.services.agent.busy
        busy.start(conversation_id)
        path = path_to(conversation.messages, parent_id)
        run = Run()
        notices: list[dict[str, Any]] = []
        status: Status = "complete"
        error = ""

        def settle(status: Status, error: str = "") -> None:
            self.store.finish_message(
                message.id, content=run.text, status=status, error=error, notices=notices, reasoning=run.reasoning, reasoning_ms=run.reasoning_ms,
                steps=run.kept_steps(), usage=run.usage(), duration_ms=run.duration_ms, waited_ms=round(run.waited * 1000),
            )

        def notice(item: dict[str, Any]) -> Iterator[dict[str, Any]]:
            if item not in notices:
                notices.append(item)
                yield event("notice", notice=item)

        try:
            yield event("assistant", message=message)
            try:
                tools = self.tools(conversation, model)
                spec = self.services.registry.spec(model)
                if not tools and spec is not None and spec.tools is False and self.groups(conversation):
                    yield from notice({"kind": "tools-unsupported", "model": model})
                describe = not self.sees_images(model)
                for attempt in range(3):
                    if describe and self._undescribed(path):
                        describer = self.vision_model(besides=model)
                        if describer:
                            yield event("stage", stage="describing", model=describer)
                    prepared = self.prepare(conversation, path, model, describe=describe, tools=bool(tools))
                    for item in prepared.notices:
                        yield from notice(item)
                    try:
                        yield from self._agent(run, conversation_id, message.id, model, effort, prepared.messages, tools, cancel, opening=self._opening(path, model))
                        break
                    except HoshimiError as exc:
                        # A station that refuses tools, before a word of the reply: once more, without them.
                        if tools and run.quiet and _TOOLS_REFUSAL.search(exc.message or str(exc)) and not overflowed(exc):
                            tools = []
                            yield from notice({"kind": "tools-unsupported", "model": model})
                            continue
                        # A station that refuses images, before a word of the reply: once more, with descriptions.
                        if not describe and prepared.images and run.quiet and _IMAGE_REFUSAL.search(exc.message or str(exc)) and not overflowed(exc):
                            describe = True
                            yield from notice({"kind": "image-refused", "model": model})
                            continue
                        raise
            except GeneratorExit:
                # The page stopped the reply, or went away: what came is kept.
                run.end_reasoning()
                run.stop_calls()
                settle("stopped")
                raise
            except Stopped:
                status = "stopped"
            except HoshimiError as exc:
                status, error = "error", exc.message or str(exc)
            except Exception as exc:  # the reply must still be settled
                logger.exception("Playground reply failed")
                status, error = "error", str(exc) or exc.__class__.__name__
            run.end_reasoning()
            run.stop_calls()
            settle(status, error)
            yield event("done", message=self.store.message(message.id))
        finally:
            busy.end(conversation_id)

    def _opening(self, path: list[ChatMessage], model: str) -> int | None:
        """What the conversation took as its last reply ended, as the station counted it (for this model): the start of the next count."""
        prior = path[-2] if len(path) >= 2 else None
        if prior is None or prior.role != "assistant" or prior.model != model or prior.usage.get("source") != "usage":
            return None
        return int(prior.usage.get("context") or 0) or None

    def _agent(
        self, run: Run, conversation_id: str, message_id: str, model: str, effort: str, messages: list[dict[str, Any]], tools: list[Tool],
        cancel: threading.Event, *, opening: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Rounds of streaming and calling, until a round calls nothing (and no message steers it on).

        The conversation is compacted as the reply begins and between its
        rounds when it nears the model's window (see :meth:`budget`), and a
        round the station refuses as too long is compacted and asked again:
        once with a summary, once more with long tool outputs cut.
        """
        ctx = self.context(conversation_id, cancel)
        steering = self.services.agent.steering
        if tools:
            try:
                ctx.workspace.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("could not create the workspace %s: %s", ctx.workspace, exc)
            steering.open(conversation_id)
        offered = {t.name: t for t in tools}
        live = Live(
            messages=messages, definitions=[t.definition() for t in tools], budget=self.budget(model),
            system=messages[0] if messages and messages[0].get("role") == "system" else None,
            question=messages[-1] if messages and messages[-1].get("role") == "user" else None,
        )
        if opening:
            live.base, live.mark = opening, len(messages) - 1

        def compact(trigger: compaction.Trigger, round_: int) -> Generator[dict[str, Any], None, bool]:
            rebuilt = yield from self._compact_live(run, message_id, live, model, cancel, trigger=trigger, round_=round_, tools=bool(tools))
            if rebuilt is None:
                return False
            live.messages, live.base, live.mark = rebuilt, 0, None
            return True

        def follow_up(round_: int) -> Generator[dict[str, Any], None, None]:
            """Before another round: compacted, when the conversation has come near the window."""
            if self.services.settings.playground_auto_compact and live.used()[0] >= live.budget.limit and live.compactable() and not self._spent(run, live):
                yield from compact("auto", round_)

        try:
            record = compaction.last_done(run.steps)
            if record is not None:
                # Asked again (without tools, or with descriptions) after compacting as it began: from the summary still.
                live.messages, live.mark = self._assemble(live, record, run.steps, tools=bool(tools)), None
            else:
                yield from follow_up(0)
            while True:
                counted = run.usage_rounds
                retries = 0
                while True:
                    try:
                        calls = yield from self._stream(model, self._body(model, effort, live.messages, tools), run)
                        break
                    except HoshimiError as exc:
                        # Too long for the model, before a word of this round: compacted, then with long outputs cut, then given up.
                        if not overflowed(exc) or run.said(run.round) or retries >= 2:
                            raise
                        retries += 1
                        self._learn(model, exc)
                        live.budget = self.budget(model)
                        if retries == 1 and live.compactable() and (yield from compact("overflow", run.round)):
                            continue
                        shorter = compaction.elide(live.messages, limit=compaction.TRANSCRIPT_OUTPUT_LIMIT, head=compaction.TRANSCRIPT_OUTPUT_LIMIT // 2)
                        if shorter == live.messages:
                            raise HoshimiError(TOO_LONG.format(model=model)) from None
                        live.messages, live.mark = shorter, None
                said = "".join(s["text"] for s in run.steps if s["type"] == "text" and s["round"] == run.round)
                turn: dict[str, Any] | None = None
                if calls:
                    turn = {
                        "role": "assistant",
                        "content": said or None,
                        "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for c in calls],
                    }
                elif said:
                    turn = {"role": "assistant", "content": said}
                answered = live.messages + ([turn] if turn else [])
                if run.usage_rounds > counted:
                    live.base, live.mark = run.usage_last.get("input", 0) + run.usage_last.get("output", 0), len(answered)
                run.context, run.context_source = live.used(answered)
                yield event("usage", usage=run.usage(), window=live.budget.window, limit=live.budget.limit)
                if not calls:
                    # A message sent as the model finished: it goes on, to answer it.
                    waiting = steering.finish(conversation_id) if tools else []
                    if not waiting:
                        return
                    live.messages = answered
                    yield from follow_up(run.round + 1)
                    run.round += 1
                    yield from self._steer(run, message_id, waiting, live.messages)
                    continue
                live.messages = answered
                for step in calls:
                    yield from self._call(run, step, offered.get(step["name"]), ctx)
                    live.messages.append({"role": "tool", "tool_call_id": step["id"], "content": call_result(step)})
                    self.store.save_steps(message_id, run.steps, run.text)
                yield from follow_up(run.round + 1)
                run.round += 1
                yield from self._steer(run, message_id, steering.take(conversation_id), live.messages)
        finally:
            if tools:
                steering.close(conversation_id)

    @staticmethod
    def _spent(run: Run, live: Live) -> bool:
        """Whether this reply has compacted once already to no avail (it is over the limit still): it is not compacted again on its own."""
        done = [s for s in run.steps if s["type"] == "compaction" and s["status"] == "done"]
        return bool(done) and int(done[-1].get("tokens_after") or 0) >= live.budget.limit

    def _steer(self, run: Run, message_id: str, steers: list[Steer], messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """What the user sent while the reply ran, read now, at the start of a round."""
        for steer in steers:
            step = run.add_user(steer.id, steer.text)
            messages.append({"role": "user", "content": steer.text})
            yield event("steer", step=step)
        if steers:
            self.store.save_steps(message_id, run.steps, run.text)

    def _call(self, run: Run, step: dict[str, Any], tool: Tool | None, ctx: ToolContext) -> Iterator[dict[str, Any]]:
        """Run one call (asking first, when it must) and keep how it went on its step; the time spent asking is not the reply's."""
        try:
            args = parse_arguments(step["arguments"]) if tool is not None else {}
        except ToolError as exc:
            step.update(status="error", output=str(exc))
            yield event("tool", step=step)
            return
        if tool is None:
            step.update(status="error", output=unsupported(step["name"]))
            yield event("tool", step=step)
            return
        approvals = self.services.agent.approvals
        permission = tool.permission(args, ctx)
        # "Always" covers one tool, inside the workspace or outside it: leave for the one is not leave for the other.
        scope = f"{tool.group}:outside" if permission is not None and permission.outside else tool.group
        if needs_approval(permission, self.approval_mode(ctx.conversation_id)) and not approvals.allowed(ctx.conversation_id, scope):
            assert permission is not None
            step.update(status="waiting", approval=permission.code)
            yield event("tool", step=step)
            asked = time.monotonic()
            decision = approvals.ask(ctx.conversation_id, step["id"], scope, ctx.cancel)
            run.waited += time.monotonic() - asked
            if decision is None:
                step.update(status="stopped", output=STOPPED_CALL)
                yield event("tool", step=step)
                return
            if decision == "deny":
                step.update(status="denied", output="")
                yield event("tool", step=step)
                return
        step.update(status="running")
        yield event("tool", step=step)
        started = time.monotonic()
        result = run_tool(tool, step["name"], args, ctx)
        step.update(
            status="stopped" if result.meta.get("cancelled") else "done" if result.ok else "error",
            output=result.output, exit_code=result.exit_code, ms=round((time.monotonic() - started) * 1000), meta=result.meta,
        )
        yield event("tool", step=step)

    def _stream(self, model: str, body: dict[str, Any], run: Run) -> Generator[dict[str, Any], None, list[dict[str, Any]]]:
        """One round: the reply's words and reasoning as events; returns the calls it made, their arguments complete."""
        calls: list[dict[str, Any]] = []
        by_index: dict[int, dict[str, Any]] = {}
        for chunk in tracked_chunks(self.services, model, body, origin="playground", stream=True):
            kind = chunk.get("type")
            if kind == "text_delta":
                piece = str(chunk.get("text") or "")
                if piece:
                    run.add_text(piece)
                    yield event("delta", text=piece)
            elif kind == "reasoning_delta":
                piece = str(chunk.get("text") or "")
                if piece:
                    run.add_reasoning(piece)
                    yield event("reasoning", text=piece)
            elif kind == "tool_call_start":
                step = run.add_call(str(chunk.get("id") or ""), str(chunk.get("name") or ""))
                step["arguments"] = str(chunk.get("arguments") or "")
                calls.append(step)
                by_index[int(chunk.get("index") or 0)] = step
                yield event("tool", step=step)
            elif kind == "tool_call_delta":
                step = by_index.get(int(chunk.get("index") or 0))
                if step is not None:
                    step["arguments"] += str(chunk.get("arguments") or "")
            elif kind == "usage":
                usage = chunk.get("usage")
                if isinstance(usage, dict):
                    run.add_usage(usage)
            elif kind == "error":
                raise HoshimiError(str(chunk.get("message") or "The upstream stream failed."))
        run.end_reasoning()
        return calls

    # -- context and compaction ----------------------------------------------- #
    def budget(self, model: str) -> Budget:
        """``model``'s window (lowered to what a station said it is), the room kept for a reply, and where compaction starts."""
        return budget(self.services.registry.spec(model) if model else None, learned=self.services.agent.windows.get(model),
                      threshold=self.services.settings.playground_compact_threshold)

    def _learn(self, model: str, exc: HoshimiError) -> None:
        window = context_limit_in(getattr(exc, "body", "") or exc.message or str(exc))
        if window:
            self.services.agent.windows.learn(model, window)

    @staticmethod
    def _assemble(live: Live, record: dict[str, Any], steps: list[dict[str, Any]], *, tools: bool) -> list[dict[str, Any]]:
        """A reply's messages from a compaction inside it on (as :meth:`prepare` puts them together for a reply kept so)."""
        out = [live.system] if live.system is not None else []
        out.extend(compaction.prefix(record))
        if live.question is not None:
            out.append(live.question)
        return out + after_compaction(steps, record, tools=tools)

    def _compact_live(
        self, run: Run, message_id: str, live: Live, model: str, cancel: threading.Event, *, trigger: compaction.Trigger, round_: int, tools: bool,
    ) -> Generator[dict[str, Any], None, list[dict[str, Any]] | None]:
        """Compact the reply under way before ``round_``; the messages to go on with, or None when it could not be compacted."""
        record = run.add_step(compaction.new_record(trigger, "pre-turn" if round_ == 0 else "mid-turn", round_))
        record["tokens_before"] = estimate(live.messages, live.definitions)
        yield event("compaction", step=record)
        zone = [m for m in live.messages if m is not live.system and m is not live.question]
        started = time.monotonic()
        try:
            summary, writer = self._summarise(model, zone, cancel)
        except Stopped:
            record.update(status="stopped", ms=round((time.monotonic() - started) * 1000))
            yield event("compaction", step=record)
            raise
        except HoshimiError as exc:
            logger.info("could not compact %s with %s: %s", message_id, model, exc)
            record.update(status="failed", error=exc.message or str(exc), ms=round((time.monotonic() - started) * 1000))
            self.store.save_steps(message_id, run.steps, run.text)
            yield event("compaction", step=record)
            return None
        record.update(status="done", summary=summary, recent=compaction.recent_user_texts(zone, live.budget.window), model=writer,
                      ms=round((time.monotonic() - started) * 1000))
        rebuilt = self._assemble(live, record, run.steps, tools=tools)
        record["tokens_after"] = estimate(rebuilt, live.definitions)
        self.store.save_steps(message_id, run.steps, run.text)
        yield event("compaction", step=record)
        return rebuilt

    def _summarise(self, model: str, messages: list[dict[str, Any]], cancel: threading.Event, *, instructions: str = "") -> tuple[str, str]:
        """A handoff summary of ``messages``, and the model that wrote it (``playground.compact_model``, or ``model``).

        The conversation is fitted to the writer's window first, its oldest
        messages left out; a writer that still refuses it as too long is asked
        again with less, twice.
        """
        writer = self.services.settings.playground_compact_model or model
        entries = compaction.transcript_entries(messages)
        room = int(self.budget(writer).window * SUMMARY_SHARE) - text_tokens(compaction.COMPACT_PROMPT) - 500
        ask = "Summarise this conversation:\n\n{transcript}"
        if instructions.strip():
            ask += f"\n\nIn the summary, pay particular attention to: {instructions.strip()}"
        for attempt in range(3):
            body = {
                "model": writer, "stream": True, **self._quiet(writer),
                "messages": [{"role": "system", "content": compaction.COMPACT_PROMPT}, {"role": "user", "content": ask.format(transcript=compaction.fit(entries, max(2_000, room)))}],
            }
            try:
                summary = self._collect(writer, body, cancel, origin="playground-compact")
            except HoshimiError as exc:
                if attempt < 2 and overflowed(exc):
                    self._learn(writer, exc)
                    room = int(room * 0.6)
                    continue
                raise
            if not summary:
                raise HoshimiError(f"{writer} wrote no summary.")
            return summary, writer
        raise HoshimiError(TOO_LONG.format(model=writer))  # pragma: no cover - the last attempt returns or raises

    def _collect(self, model: str, body: dict[str, Any], cancel: threading.Event, *, origin: str) -> str:
        """A streamed answer's text, whole; :class:`Stopped` as soon as ``cancel`` is set."""
        chunks = tracked_chunks(self.services, model, body, origin=origin, stream=True)
        text: list[str] = []
        try:
            for chunk in chunks:
                if cancel.is_set():
                    raise Stopped
                kind = chunk.get("type")
                if kind == "text_delta":
                    text.append(str(chunk.get("text") or ""))
                elif kind == "error":
                    raise HoshimiError(str(chunk.get("message") or "The model failed."))
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
        if cancel.is_set():
            raise Stopped
        return "".join(text).strip()

    def _model_of(self, conversation: Conversation) -> str:
        return conversation.model or self.services.settings.default_model or self.services.registry.default_model or ""

    def compact(self, conversation_id: str, *, instructions: str = "", cancel: threading.Event | None = None) -> Iterator[dict[str, Any]]:
        """Compact a conversation between turns, as the user asks: a checkpoint after the path on screen, holding the summary.

        Raises at once (before any event) when something works on the
        conversation already, or there is nothing to compact; the events are
        ``checkpoint`` (filed, being written) and ``done`` (kept; or, with
        ``status`` ``error``, taken back).
        """
        conversation = self.store.get(conversation_id)
        if conversation is None:
            raise ValidationError(f"No conversation '{conversation_id}'.")
        if self.services.agent.busy.started(conversation_id) is not None:
            raise ConflictError("A reply is running in this conversation; compact it once the reply ends.")
        path = path_to(conversation.messages, conversation.current_leaf)
        at, _ = last_compaction(path)
        # A reply compacted inside has its later rounds to compact; a checkpoint has only what follows it.
        since = path[at:] if at >= 0 and path[at].role == "assistant" else path[at + 1:]
        if not any(m.role == "assistant" and m.status != "streaming" for m in since):
            raise ValidationError("Nothing to compact yet: there is no reply since the conversation was last compacted.")
        model = self._model_of(conversation)
        tools = self.tools(conversation, model) if model else []
        prepared = self.prepare(conversation, path, model, describe=not self.sees_images(model), tools=bool(tools), ask=False)
        return self._checkpoint(conversation, model, prepared.messages, [t.definition() for t in tools], instructions, cancel or threading.Event())

    def _checkpoint(
        self, conversation: Conversation, model: str, messages: list[dict[str, Any]], definitions: list[dict[str, Any]], instructions: str, cancel: threading.Event,
    ) -> Iterator[dict[str, Any]]:
        busy = self.services.agent.busy
        busy.start(conversation.id)
        system = messages[0] if messages and messages[0].get("role") == "system" else None
        zone = [m for m in messages if m is not system]
        record = compaction.new_record("manual", "manual", 0)
        record["tokens_before"] = estimate(messages, definitions)
        checkpoint = self.store.add_message(conversation.id, conversation.current_leaf or None, "checkpoint", "", model=model, status="streaming", steps=[record])
        assert checkpoint is not None
        settled = False
        started = time.monotonic()
        try:
            yield event("checkpoint", message=checkpoint)
            try:
                summary, writer = self._summarise(model, zone, cancel, instructions=instructions)
            except Stopped:
                return
            except HoshimiError as exc:
                error = exc.message or str(exc)
                record.update(status="failed", error=error, ms=round((time.monotonic() - started) * 1000))
                yield event("done", message=replace(checkpoint, status="error", error=error, steps=[record]))
                return
            record.update(status="done", summary=summary, recent=compaction.recent_user_texts(zone, self.budget(model).window), model=writer,
                          ms=round((time.monotonic() - started) * 1000))
            record["tokens_after"] = estimate(([system] if system else []) + compaction.prefix(record), definitions)
            self.store.finish_message(checkpoint.id, content=summary, status="complete", steps=[record], duration_ms=record["ms"])
            settled = True
            yield event("done", message=self.store.message(checkpoint.id))
        finally:
            if not settled:
                # Stopped, failed, or the page went away: the conversation is as it was.
                self.store.remove_leaf(checkpoint.id)
            busy.end(conversation.id)

    def context_view(self, conversation_id: str) -> dict[str, Any]:
        """How full the conversation on screen is, and what its replies took (for ``/status``)."""
        conversation = self.store.get(conversation_id)
        if conversation is None:
            raise ValidationError(f"No conversation '{conversation_id}'.")
        model = self._model_of(conversation)
        spec = self.services.registry.spec(model) if model else None
        window = self.budget(model)
        path = path_to(conversation.messages, conversation.current_leaf)
        replies = [m for m in path if m.role == "assistant"]
        leaf = path[-1] if path else None
        tools = self.tools(conversation, model) if model else []
        if leaf is not None and leaf.role == "assistant" and leaf.status != "streaming" and leaf.usage.get("context"):
            used, source = int(leaf.usage["context"]), str(leaf.usage.get("source") or "estimate")
        else:
            prepared = self.prepare(conversation, path, model, describe=not self.sees_images(model), tools=bool(tools), ask=False)
            used, source = (estimate(prepared.messages, [t.definition() for t in tools]) if path else 0), "estimate"
        totals = {key: sum(int((m.usage.get("total") or {}).get(key) or 0) for m in replies) for key in ("input", "cached", "output", "reasoning")}
        last = next((m.usage["last"] for m in reversed(replies) if isinstance(m.usage.get("last"), dict) and m.usage["last"]), {})
        records = [s for m in path if m.role in ("checkpoint", "assistant") for s in m.steps if s.get("type") == "compaction" and s.get("status") == "done"]
        durations = [m.duration_ms for m in replies if m.duration_ms]
        return {
            "model": model, "provider": spec.provider if spec is not None else "", "effort": conversation.effort,
            "window": window.window, "window_source": window.source, "limit": window.limit, "headroom": window.headroom,
            "used": used, "used_source": source, "auto_compact": self.services.settings.playground_auto_compact,
            "last": {key: int(last.get(key) or 0) for key in ("input", "cached", "output", "reasoning")}, "totals": totals,
            "rounds": sum(int(m.usage.get("rounds") or 0) for m in replies), "replies": len(replies),
            "compactions": len(records),
            "last_compaction": {key: records[-1].get(key) for key in ("tokens_before", "tokens_after", "at", "trigger")} if records else None,
            "last_duration_ms": durations[-1] if durations else 0, "total_duration_ms": sum(durations),
            "running_since": self.services.agent.busy.started(conversation_id),
            "tools": self.groups(conversation), "approval": self.approval_mode(conversation_id),
        }

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
