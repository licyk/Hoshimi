"""What a tool is, what it is given when it runs, and what it gives back."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hoshimi.core.net.policy import NetworkPolicy

if TYPE_CHECKING:
    from hoshimi.core.agent.terminals import TerminalManager

OUTPUT_LIMIT = 32_000
"""The most of one tool's output a model is sent, in characters; the middle of a longer one is cut."""


class ToolError(Exception):
    """A tool that could not do what it was asked; the message is what the model is told."""


class Cancelled(ToolError):
    def __init__(self) -> None:
        super().__init__("Stopped by the user.")


@dataclass
class ToolContext:
    """Where and for whom a tool runs."""

    workspace: Path
    """Relative paths resolve here; apply_patch writes nowhere else."""
    conversation_id: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    """Set when the reply is stopped: a tool that waits checks it and gives up."""
    terminals: TerminalManager | None = None
    network: NetworkPolicy = field(default_factory=NetworkPolicy)
    search_url: str = ""
    """A SearXNG instance to search with; empty searches DuckDuckGo."""
    transport: Any = None
    """An httpx transport for the web tools (tests)."""

    def resolve(self, path: str) -> Path:
        """``path`` as a file: ``~`` expanded, a relative one under the workspace."""
        if not path or not path.strip():
            raise ToolError("A path is required.")
        candidate = Path(os.path.expanduser(path.strip()))
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        return Path(os.path.normpath(candidate))

    def inside(self, path: Path) -> bool:
        """Whether ``path`` is in the workspace, symbolic links followed."""
        try:
            path.resolve().relative_to(self.workspace.resolve())
        except (ValueError, OSError):
            return False
        return True

    def check(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()


@dataclass
class ToolResult:
    output: str
    ok: bool = True
    exit_code: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    """What the page shows beside the output (files changed, a terminal's session, the address fetched)."""


@dataclass(frozen=True)
class Permission:
    """What a call needs the user's leave for: ``reason`` (``command``, ``edit`` or ``read``), and whether it reaches outside the workspace.

    ``ask`` mode asks for every permission, ``auto`` only for one that reaches
    outside the workspace, ``full`` for none.
    """

    reason: str
    outside: bool = False

    @property
    def code(self) -> str:
        """The permission as the page reads it: ``command``, ``edit``, ``edit-outside``, ``read-outside``…"""
        return f"{self.reason}-outside" if self.outside else self.reason


APPROVAL_MODES = ("ask", "auto", "full")


def needs_approval(permission: Permission | None, mode: str) -> bool:
    if permission is None or mode == "full":
        return False
    return mode != "auto" or permission.outside


@dataclass(frozen=True)
class Tool:
    name: str
    group: str
    """The switch in Settings that turns this tool on (several terminal tools share one)."""
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any], ToolContext], ToolResult]
    permission: Callable[[dict[str, Any], ToolContext], Permission | None] = lambda args, ctx: None
    """What this call needs the user's leave for (whether it is asked depends on the approval mode), or None."""

    def definition(self) -> dict[str, Any]:
        """The tool as a Chat Completions ``tools`` entry."""
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def clip(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """``text`` with its middle cut when it is longer than ``limit``: the start and the end are what tell."""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n…{len(text) - limit} characters cut…\n{text[-tail:]}"


def string(args: dict[str, Any], key: str, *, required: bool = True, default: str = "") -> str:
    value = args.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a string.")
    if required and not value.strip():
        raise ToolError(f"'{key}' is required.")
    return value


def integer(args: dict[str, Any], key: str, default: int, *, low: int, high: int) -> int:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ToolError(f"'{key}' must be a whole number.")
    try:
        number = int(value)
    except ValueError:
        raise ToolError(f"'{key}' must be a whole number.") from None
    return max(low, min(high, number))
