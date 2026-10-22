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
on until a round calls nothing, or ``playground.max_steps`` rounds are spent.
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
``stopped`` (the page stopped it or went away) or ``error``. Titles are
written by a model on request, once the first reply is in.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoshimi.api.inference import tracked_chunks
from hoshimi.core.agent.base import Tool, ToolContext, ToolError, needs_approval
from hoshimi.core.agent.runtime import Steer
from hoshimi.core.agent.tools import GROUPS, parse_arguments, run_tool, tools_for, unsupported
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
_TOOLS_REFUSAL = re.compile(r"\btools?\b|tool_choice|function[ _-]?call", re.IGNORECASE)


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
    - ``done``: the reply as kept, with how it ended (``message``).

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
class Run:
    """A reply as it is made: its steps in order, and the round of tool calls it is in."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    round: int = 0
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
        """The steps to keep: a reply that called no tool and was not steered is its text and reasoning alone."""
        return self.steps if any(s["type"] in ("tool", "user") for s in self.steps) else []

    def stop_calls(self) -> None:
        """Calls the stop cut short before they ran."""
        for step in self.calls:
            if step["status"] in ("pending", "waiting", "running"):
                step["status"] = "stopped"
                step["output"] = step["output"] or STOPPED_CALL


STOPPED_CALL = "The user stopped the reply before this call finished."
DENIED_CALL = "The user declined this call; it did not run. Do not try it again unless they ask."


def call_result(step: dict[str, Any]) -> str:
    """What a model is told a call gave."""
    if step["status"] == "stopped":
        return step["output"] or STOPPED_CALL
    if step["status"] == "denied":
        return DENIED_CALL
    return step["output"] or "(no output)"


def replay(message: ChatMessage, *, tools: bool) -> list[dict[str, Any]]:
    """A kept reply as a model is sent it again: per round, what it said and called, then what each call gave.

    With no tools offered now, the calls and their results go as text, so no
    station is sent tool calls it was not told about.
    """
    if not message.steps:
        return [{"role": "assistant", "content": message.content}] if message.content else []
    out: list[dict[str, Any]] = []
    rounds: dict[int, list[dict[str, Any]]] = {}
    for step in message.steps:
        rounds.setdefault(int(step.get("round") or 0), []).append(step)
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

    def prepare(self, conversation: Conversation, path: list[ChatMessage], model: str, *, describe: bool, tools: bool = False) -> Prepared:
        """The Chat Completions messages for a reply after ``path``.

        ``describe`` sends images as descriptions rather than images; it is
        what happens for a model that cannot read them. ``tools`` says tools
        are offered, so earlier calls go as calls (see :func:`replay`).
        """
        prepared = Prepared(messages=[])
        prompt = self.services.settings.playground_system_prompt
        if prompt:
            prepared.messages.append({"role": "system", "content": prompt})
        describer: str | None = None
        asked = False
        for message in path:
            if message.role == "assistant":
                prepared.messages.extend(replay(message, tools=tools))
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

    # -- tools -------------------------------------------------------------- #
    def tools(self, conversation: Conversation, model: str) -> list[Tool]:
        """The tools offered to ``model`` in ``conversation``: on in Settings, not turned off here, and none for a model without tool calls."""
        groups = [g for g in GROUPS if g in self.services.settings.playground_tools and g not in conversation.tools_off]
        spec = self.services.registry.spec(model)
        if not groups or (spec is not None and spec.tools is False):
            return []
        return tools_for(groups)

    def workspace(self) -> Path:
        configured = self.services.settings.playground_workspace
        return Path(os.path.expanduser(configured)) if configured else self.services.data_dir / "workspace"

    def approval_mode(self, conversation_id: str) -> str:
        """``full`` for a conversation given full access; ``playground.approval`` (``ask``, ``auto`` or ``full``) otherwise."""
        return "full" if self.store.full_access(conversation_id) else self.services.settings.playground_approval

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
    def _body(model: str, effort: str, messages: list[dict[str, Any]], tools: list[Tool], *, last_round: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = [t.definition() for t in tools]
            # The last round may not call: it answers with what it has.
            body["tool_choice"] = "none" if last_round else "auto"
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
        yield event("assistant", message=message)
        path = path_to(conversation.messages, parent_id)
        run = Run()
        notices: list[dict[str, Any]] = []
        status: Status = "complete"
        error = ""

        def settle(status: Status, error: str = "") -> None:
            self.store.finish_message(
                message.id, content=run.text, status=status, error=error, notices=notices, reasoning=run.reasoning, reasoning_ms=run.reasoning_ms,
                steps=run.kept_steps(),
            )

        def notice(item: dict[str, Any]) -> Iterator[dict[str, Any]]:
            if item not in notices:
                notices.append(item)
                yield event("notice", notice=item)

        try:
            tools = self.tools(conversation, model)
            spec = self.services.registry.spec(model)
            if not tools and spec is not None and spec.tools is False and any(g not in conversation.tools_off for g in self.services.settings.playground_tools):
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
                    yield from self._agent(run, conversation_id, message.id, model, effort, prepared.messages, tools, cancel, notice)
                    break
                except HoshimiError as exc:
                    # A station that refuses tools, before a word of the reply: once more, without them.
                    if tools and not run.steps and _TOOLS_REFUSAL.search(exc.message or str(exc)):
                        tools = []
                        yield from notice({"kind": "tools-unsupported", "model": model})
                        continue
                    # A station that refuses images, before a word of the reply: once more, with descriptions.
                    if not describe and prepared.images and not run.steps and _IMAGE_REFUSAL.search(exc.message or str(exc)):
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
        except HoshimiError as exc:
            status, error = "error", exc.message or str(exc)
        except Exception as exc:  # the reply must still be settled
            logger.exception("Playground reply failed")
            status, error = "error", str(exc) or exc.__class__.__name__
        run.end_reasoning()
        run.stop_calls()
        settle(status, error)
        yield event("done", message=self.store.message(message.id))

    def _agent(
        self, run: Run, conversation_id: str, message_id: str, model: str, effort: str, messages: list[dict[str, Any]], tools: list[Tool],
        cancel: threading.Event, notice: Any,
    ) -> Iterator[dict[str, Any]]:
        """Rounds of streaming and calling, until a round calls nothing (and no message steers it on)."""
        limit = self.services.settings.playground_max_steps
        ctx = self.context(conversation_id, cancel)
        steering = self.services.agent.steering
        if tools:
            try:
                ctx.workspace.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("could not create the workspace %s: %s", ctx.workspace, exc)
            steering.open(conversation_id)
        offered = {t.name: t for t in tools}
        try:
            while True:
                last_round = bool(tools) and run.round >= limit
                calls = yield from self._stream(model, self._body(model, effort, messages, tools, last_round=last_round), run)
                said = "".join(s["text"] for s in run.steps if s["type"] == "text" and s["round"] == run.round)
                if not calls:
                    # A message sent as the model finished: it goes on, to answer it.
                    waiting = steering.finish(conversation_id) if tools else []
                    if not waiting:
                        return
                    if said:
                        messages.append({"role": "assistant", "content": said})
                    run.round += 1
                    yield from self._steer(run, message_id, waiting, messages)
                    continue
                if last_round:
                    for step in calls:
                        step.update(status="stopped", output=f"Not run: this reply reached its limit of {limit} rounds of tool calls.")
                        yield event("tool", step=step)
                    yield from notice({"kind": "step-limit", "name": str(limit)})
                    return
                messages.append({
                    "role": "assistant",
                    "content": said or None,
                    "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for c in calls],
                })
                for step in calls:
                    yield from self._call(step, offered.get(step["name"]), ctx)
                    messages.append({"role": "tool", "tool_call_id": step["id"], "content": call_result(step)})
                    self.store.save_steps(message_id, run.steps, run.text)
                run.round += 1
                yield from self._steer(run, message_id, steering.take(conversation_id), messages)
        finally:
            if tools:
                steering.close(conversation_id)

    def _steer(self, run: Run, message_id: str, steers: list[Steer], messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """What the user sent while the reply ran, read now, at the start of a round."""
        for steer in steers:
            step = run.add_user(steer.id, steer.text)
            messages.append({"role": "user", "content": steer.text})
            yield event("steer", step=step)
        if steers:
            self.store.save_steps(message_id, run.steps, run.text)

    def _call(self, step: dict[str, Any], tool: Tool | None, ctx: ToolContext) -> Iterator[dict[str, Any]]:
        """Run one call (asking first, when it must) and keep how it went on its step."""
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
            decision = approvals.ask(ctx.conversation_id, step["id"], scope, ctx.cancel)
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
            elif kind == "error":
                raise HoshimiError(str(chunk.get("message") or "The upstream stream failed."))
        run.end_reasoning()
        return calls

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
