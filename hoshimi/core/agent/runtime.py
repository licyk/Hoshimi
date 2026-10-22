"""What the Playground's agent keeps between calls: background commands, approvals asked and given, messages that steer a reply under way,
the conversations working now, and the context windows stations have turned out to have."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from hoshimi.core.agent.context_window import LearnedWindows
from hoshimi.core.agent.terminals import TerminalManager

Decision = Literal["allow", "always", "deny"]

APPROVAL_TIMEOUT = 600.0
"""Seconds a call waits for the user's answer; no answer is a no."""


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    decision: Decision = "deny"


class Approvals:
    """Calls that wait for the user's yes or no, and the tools a conversation was told to run without asking.

    Kept in memory: an "always" lasts as long as the process does.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: dict[tuple[str, str], _Pending] = {}
        self.always: dict[str, set[str]] = {}

    def allowed(self, conversation_id: str, group: str) -> bool:
        with self.lock:
            return group in self.always.get(conversation_id, set())

    def ask(self, conversation_id: str, call_id: str, group: str, cancel: threading.Event, timeout: float = APPROVAL_TIMEOUT) -> Decision | None:
        """Wait for the answer to one call; None when the reply was stopped first. Timing out is a no."""
        pending = _Pending()
        key = (conversation_id, call_id)
        with self.lock:
            self.pending[key] = pending
        deadline = time.monotonic() + timeout
        try:
            while not pending.event.wait(0.1):
                if cancel.is_set():
                    return None
                if time.monotonic() >= deadline:
                    return "deny"
        finally:
            with self.lock:
                self.pending.pop(key, None)
        if pending.decision == "always":
            with self.lock:
                self.always.setdefault(conversation_id, set()).add(group)
        return pending.decision

    def answer(self, conversation_id: str, call_id: str, decision: Decision) -> bool:
        """Give the answer to a waiting call; False when nothing waits for it."""
        with self.lock:
            pending = self.pending.get((conversation_id, call_id))
            if pending is None:
                return False
            pending.decision = decision
            pending.event.set()
        return True

    def forget(self, conversation_id: str) -> None:
        with self.lock:
            self.always.pop(conversation_id, None)


@dataclass(frozen=True)
class Steer:
    id: str
    text: str


class Steering:
    """Messages the user sends while a reply runs, for it to read at its next round.

    A reply that can be steered opens its conversation's queue as it starts
    and takes what is in it between rounds. It closes the queue only when the
    queue is empty, so a message sent as it ends is never lost: the reply
    goes on to answer it, or the send is refused and the page sends it as a
    message of its own.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queues: dict[str, list[Steer]] = {}

    def open(self, conversation_id: str) -> None:
        with self.lock:
            self.queues.setdefault(conversation_id, [])

    def push(self, conversation_id: str, text: str) -> Steer | None:
        """Queue a message for the reply under way; None when no reply takes one."""
        with self.lock:
            queue = self.queues.get(conversation_id)
            if queue is None:
                return None
            steer = Steer(f"steer_{uuid.uuid4().hex[:16]}", text)
            queue.append(steer)
            return steer

    def take(self, conversation_id: str) -> list[Steer]:
        with self.lock:
            queue = self.queues.get(conversation_id)
            if not queue:
                return []
            taken = list(queue)
            queue.clear()
            return taken

    def finish(self, conversation_id: str) -> list[Steer]:
        """Close the queue if nothing waits in it; otherwise what waits (and it stays open)."""
        with self.lock:
            queue = self.queues.get(conversation_id)
            if queue:
                taken = list(queue)
                queue.clear()
                return taken
            self.queues.pop(conversation_id, None)
            return []

    def close(self, conversation_id: str) -> list[Steer]:
        """Close the queue whatever waits in it (the reply was stopped or failed); returns what was never read."""
        with self.lock:
            return self.queues.pop(conversation_id, None) or []


class Busy:
    """The conversations a reply (or a compaction) is working on now, and since when (Unix seconds)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.since: dict[str, float] = {}
        self.count: dict[str, int] = {}

    def start(self, conversation_id: str) -> bool:
        """Mark it working; False when something already was (it is marked all the same, and must be ended)."""
        with self.lock:
            free = conversation_id not in self.count
            self.count[conversation_id] = self.count.get(conversation_id, 0) + 1
            self.since.setdefault(conversation_id, time.time())
            return free

    def end(self, conversation_id: str) -> None:
        with self.lock:
            left = self.count.get(conversation_id, 1) - 1
            if left > 0:
                self.count[conversation_id] = left
            else:
                self.count.pop(conversation_id, None)
                self.since.pop(conversation_id, None)

    def started(self, conversation_id: str) -> float | None:
        with self.lock:
            return self.since.get(conversation_id)


class AgentRuntime:
    def __init__(self) -> None:
        self.terminals = TerminalManager()
        self.approvals = Approvals()
        self.steering = Steering()
        self.busy = Busy()
        self.windows = LearnedWindows()

    def end(self, conversation_id: str) -> None:
        """A conversation is gone: its background commands stop, its approvals are forgotten."""
        self.terminals.end(conversation_id)
        self.approvals.forget(conversation_id)

    def close(self) -> None:
        self.terminals.close()
