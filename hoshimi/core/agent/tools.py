"""The built-in tools, by name and by the Settings switch that turns them on, and how one call is run."""

from __future__ import annotations

import json
import logging
from typing import Any

from hoshimi.core.agent.base import Cancelled, Tool, ToolContext, ToolError, ToolResult, clip
from hoshimi.core.agent.files import APPLY_PATCH, READ
from hoshimi.core.agent.terminals import EXEC_COMMAND, KILL_SESSION, SHELL, WRITE_STDIN
from hoshimi.core.agent.web import WEB_FETCH, WEB_SEARCH

logger = logging.getLogger(__name__)

TOOLS: dict[str, Tool] = {t.name: t for t in (READ, APPLY_PATCH, SHELL, EXEC_COMMAND, WRITE_STDIN, KILL_SESSION, WEB_SEARCH, WEB_FETCH)}
GROUPS: tuple[str, ...] = ("read", "apply_patch", "shell", "terminal", "web_search", "web_fetch")
"""The switches, in the order Settings shows them: ``terminal`` is exec_command, write_stdin and kill_session together."""


def tools_for(groups: list[str] | tuple[str, ...] | set[str]) -> list[Tool]:
    return [t for t in TOOLS.values() if t.group in groups]


def unsupported(name: str) -> str:
    """What a model is told for a tool it was not given (Codex's words)."""
    return f"unsupported call: {name}"


def parse_arguments(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError(f"The arguments are not valid JSON ({exc.msg} at character {exc.pos}); send them as one JSON object.") from None
    if not isinstance(value, dict):
        raise ToolError("The arguments must be one JSON object.")
    return value


def run_tool(tool: Tool | None, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """One call, with whatever went wrong as the result the model reads."""
    if tool is None:
        return ToolResult(unsupported(name), ok=False)
    try:
        ctx.check()
        result = tool.run(args, ctx)
    except Cancelled as exc:
        return ToolResult(str(exc), ok=False, meta={"cancelled": True})
    except ToolError as exc:
        return ToolResult(clip(str(exc)), ok=False)
    except Exception as exc:  # A tool's own failure is the model's to read, not the reply's end.
        logger.exception("tool %s failed", name)
        return ToolResult(f"{name} failed: {exc.__class__.__name__}: {exc}", ok=False)
    return result
