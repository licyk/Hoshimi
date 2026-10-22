"""What the Playground's agent keeps between calls: background commands, and approvals asked and given."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal

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


class AgentRuntime:
    def __init__(self) -> None:
        self.terminals = TerminalManager()
        self.approvals = Approvals()

    def end(self, conversation_id: str) -> None:
        """A conversation is gone: its background commands stop, its approvals are forgotten."""
        self.terminals.end(conversation_id)
        self.approvals.forget(conversation_id)

    def close(self) -> None:
        self.terminals.close()
