"""Who decides whether a tool call runs: the approval modes, and the router that sends a call that needs leave to whoever gives it.

Shaped after Codex's approvals, where one policy says which actions need
approval and a reviewer setting says who answers. Here one choice, the
approval mode (``playground.approval``, or a conversation's own), says both:

- ``ask``: every call that needs leave (a command, a file change, anything
  outside the workspace: see :class:`~hoshimi.core.agent.base.Permission`)
  asks the user;
- ``workspace``: only a call that reaches outside the workspace asks the user;
- ``review``: every call that needs leave is judged by a model, the reviewer
  (:mod:`hoshimi.core.agent.review`); a call it could not judge asks the user;
- ``full``: nothing asks.

``auto`` is the old name of ``workspace`` and is read as it. An "always" the
user gave for a tool in a conversation lets its calls run without asking
anyone, whatever the mode.

An approver is a generator: it yields what changes on the call's step as it
goes (waiting for the user, being reviewed, what the reviewer said), for the
page to show, and returns its :class:`Verdict`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from hoshimi.core.agent.base import Cancelled, Permission, Tool, ToolContext, ToolError
from hoshimi.core.agent.review import Review, ReviewFailed, failed_review
from hoshimi.core.agent.terminals import pick_shell

if TYPE_CHECKING:
    from hoshimi.core.agent.runtime import Approvals

Mode = Literal["ask", "workspace", "review", "full"]
APPROVAL_MODES: tuple[Mode, ...] = ("ask", "workspace", "review", "full")
DEFAULT_APPROVAL: Mode = "review"
LEGACY_MODES = {"auto": "workspace"}
"""Old names of modes, read as the mode they became."""

MAX_DENIALS_IN_A_ROW = 3
MAX_DENIALS = 10
"""Reviewer denials after which a reply is stopped: so many one after another, or so many in all."""

Update = dict[str, Any]
"""Fields of a call's step that change as it is decided."""


def approval_mode(value: str | None) -> Mode | Literal[""]:
    """``value`` as a mode, an old name read as the new one; empty (or unknown) stays empty."""
    value = (value or "").strip()
    value = LEGACY_MODES.get(value, value)
    return next((mode for mode in APPROVAL_MODES if mode == value), "")


def needs_approval(permission: Permission | None, mode: str) -> bool:
    """Whether a call that needs ``permission`` waits for someone's leave in ``mode``."""
    if permission is None or mode == "full":
        return False
    return mode != "workspace" or permission.outside


@dataclass
class ApprovalRequest:
    """One call that needs leave: what it is, and what has been said before it (for the reviewer)."""

    call_id: str
    tool: Tool
    args: dict[str, Any]
    permission: Permission
    ctx: ToolContext
    transcript: list[dict[str, Any]] = field(default_factory=list)
    """The Chat Completions messages so far, the call's own assistant message last."""

    @property
    def conversation_id(self) -> str:
        return self.ctx.conversation_id

    @property
    def scope(self) -> str:
        """What an "always" covers: one tool, inside the workspace or outside it (leave for the one is not leave for the other)."""
        return f"{self.tool.group}:outside" if self.permission.outside else self.tool.group

    def action(self) -> dict[str, Any]:
        """The call as the reviewer reads it."""
        action: dict[str, Any] = {
            "tool": self.tool.name,
            "arguments": self.args,
            "needs": self.permission.code,
            "outside_workspace": self.permission.outside,
            "workspace": str(self.ctx.workspace),
        }
        if "shell" in self.tool.parameters.get("properties", {}):
            try:
                action["shell"] = pick_shell(self.args, self.ctx).kind
            except ToolError:
                action["shell"] = self.args.get("shell")
        return action

    def action_json(self) -> str:
        return json.dumps(self.action(), ensure_ascii=False, indent=2, default=str)


@dataclass
class Verdict:
    decision: Literal["allow", "deny", "stopped"]
    by: Literal["user", "reviewer"]
    message: str = ""
    """What the model is told of a denial; empty for the usual words."""
    waited: float = 0.0
    """Seconds spent waiting for the user (not the reply's own time)."""


class Approver(Protocol):
    def decide(self, request: ApprovalRequest) -> Generator[Update, None, Verdict]: ...


class UserApprover:
    """Asks the user, on the page, and waits for the answer (see :class:`~hoshimi.core.agent.runtime.Approvals`)."""

    def __init__(self, approvals: Approvals) -> None:
        self.approvals = approvals

    def decide(self, request: ApprovalRequest) -> Generator[Update, None, Verdict]:
        yield {"status": "waiting", "approval": request.permission.code}
        asked = time.monotonic()
        decision = self.approvals.ask(request.conversation_id, request.call_id, request.scope, request.ctx.cancel)
        waited = time.monotonic() - asked
        if decision is None:
            return Verdict("stopped", "user", waited=waited)
        return Verdict("deny" if decision == "deny" else "allow", "user", waited=waited)


class ReviewApprover:
    """Asks the reviewer; a call it could not judge (it failed, timed out, or answered nonsense) goes to the user."""

    def __init__(self, review: Callable[[ApprovalRequest], Review], fallback: Approver) -> None:
        self.review = review
        self.fallback = fallback

    def decide(self, request: ApprovalRequest) -> Generator[Update, None, Verdict]:
        yield {"status": "reviewing", "approval": request.permission.code}
        started = time.monotonic()
        try:
            review = self.review(request)
        except Cancelled:
            return Verdict("stopped", "reviewer")
        except ReviewFailed as exc:
            yield {"review": failed_review(str(exc), model=exc.model, ms=round((time.monotonic() - started) * 1000)).view()}
            return (yield from self.fallback.decide(request))
        yield {"review": review.view()}
        if review.allowed:
            return Verdict("allow", "reviewer")
        return Verdict("deny", "reviewer", message=review.denial())


class ApprovalRouter:
    """Sends each call that needs leave to whoever gives it in the conversation's mode; None runs it at once."""

    def __init__(self, approvals: Approvals, review: Callable[[ApprovalRequest], Review] | None = None) -> None:
        self.approvals = approvals
        self.review = review

    def route(self, mode: str, request: ApprovalRequest) -> Approver | None:
        if not needs_approval(request.permission, mode) or self.approvals.allowed(request.conversation_id, request.scope):
            return None
        user = UserApprover(self.approvals)
        if mode == "review" and self.review is not None:
            return ReviewApprover(self.review, user)
        return user


@dataclass
class DenialBreaker:
    """Counts a reply's reviewer denials: past :data:`MAX_DENIALS_IN_A_ROW` one after another, or :data:`MAX_DENIALS` in all, the reply stops.

    A model that keeps calling what the reviewer refuses is stopped rather than left to try every way around it.
    """

    in_a_row: int = 0
    total: int = 0

    def record(self, verdict: Verdict | None) -> bool:
        """Count a call's verdict (None: it ran without asking); True when the reply should stop."""
        if verdict is not None and verdict.by == "reviewer" and verdict.decision == "deny":
            self.in_a_row += 1
            self.total += 1
        elif verdict is None or verdict.decision == "allow":
            self.in_a_row = 0
        return self.in_a_row >= MAX_DENIALS_IN_A_ROW or self.total >= MAX_DENIALS
