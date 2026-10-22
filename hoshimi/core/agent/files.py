"""``read`` and ``apply_patch``: looking at files and changing them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hoshimi.core.agent.base import Permission, Tool, ToolContext, ToolError, ToolResult, integer, string
from hoshimi.core.agent.patch import PatchError, UpdateFile, commit, parse_patch, plan_patch

READ_LIMIT = 2000
"""Lines a read returns when it names no limit."""
LINE_LIMIT = 500
"""Characters of one line a read returns; the rest of a longer line is cut."""
READ_CHARS = 60_000
"""Characters one read returns at most; a longer slice stops early and says where to go on."""
LIST_LIMIT = 500
"""Entries a directory listing shows."""


def _read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.resolve(string(args, "path"))
    offset = integer(args, "offset", 1, low=1, high=10**9)
    limit = integer(args, "limit", READ_LIMIT, low=1, high=10_000)
    if path.is_dir():
        return _list(path)
    if not path.exists():
        raise ToolError(f"No such file: {path}")
    try:
        with path.open("rb") as f:
            if b"\0" in f.read(8192):
                raise ToolError(f"{path} is a binary file; read text files only.")
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"Could not read {path}: {exc.strerror or exc}") from None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [line.removesuffix("\r") for line in lines]
    if not lines:
        return ToolResult(f"{path} is empty.", meta={"path": str(path), "lines": 0})
    if offset > len(lines):
        raise ToolError(f"offset {offset} is past the end of the file ({len(lines)} lines).")
    out: list[str] = []
    size = 0
    last = offset - 1
    for number in range(offset, min(len(lines), offset + limit - 1) + 1):
        line = lines[number - 1]
        if len(line) > LINE_LIMIT:
            line = line[:LINE_LIMIT] + f"… [{len(line) - LINE_LIMIT} more characters]"
        entry = f"L{number}: {line}"
        if out and size + len(entry) > READ_CHARS:
            break
        out.append(entry)
        size += len(entry) + 1
        last = number
    if last < len(lines):
        out.append(f"[Lines {offset}–{last} of {len(lines)}. Read on with offset={last + 1}.]")
    return ToolResult("\n".join(out), meta={"path": str(path), "lines": len(lines), "from": offset, "to": last})


def _list(path: Path) -> ToolResult:
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        raise ToolError(f"Could not list {path}: {exc.strerror or exc}") from None
    shown = [f"{p.name}/" if p.is_dir() else p.name for p in entries[:LIST_LIMIT]]
    more = f"\n[{len(entries) - LIST_LIMIT} more entries not shown.]" if len(entries) > LIST_LIMIT else ""
    body = "\n".join(shown) if shown else "(empty)"
    return ToolResult(f"Directory {path}:\n{body}{more}", meta={"path": str(path), "entries": len(entries)})


def _read_permission(args: dict[str, Any], ctx: ToolContext) -> Permission | None:
    try:
        path = ctx.resolve(string(args, "path"))
    except ToolError:
        return None
    return None if ctx.inside(path) else Permission("read", outside=True)


def _patch_permission(args: dict[str, Any], ctx: ToolContext) -> Permission:
    """A patch changes files; one that names a file outside the workspace (or moves one there) reaches outside it."""
    try:
        hunks = parse_patch(str(args.get("input") or ""))
    except PatchError:
        return Permission("edit")
    names = [h.path for h in hunks] + [h.move_path for h in hunks if isinstance(h, UpdateFile) and h.move_path]
    try:
        outside = any(not ctx.inside(ctx.resolve(name)) for name in names)
    except ToolError:
        outside = False
    return Permission("edit", outside=outside)


def _apply_patch(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    patch = string(args, "input")
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    try:
        changes = plan_patch(patch, ctx.resolve)
        ctx.check()
        applied = commit(changes)
    except PatchError as exc:
        raise ToolError(f"apply_patch verification failed: {exc}") from None
    files = [{"path": c.move_to or c.path, "kind": c.kind, **({"from": c.path} if c.move_to else {})} for c in changes]
    return ToolResult(applied.summary(), meta={"files": files})


READ = Tool(
    name="read",
    group="read",
    description=(
        "Read a text file, or list a directory. Lines come back numbered as `L<number>: <text>`; the numbers are not part of "
        "the file. Reads 2000 lines from the start unless told otherwise: use `offset` (the first line, from 1) and `limit` "
        "to read a long file in parts. A relative path is taken from the workspace."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The file or directory, absolute or relative to the workspace."},
            "offset": {"type": "integer", "description": "The first line to read, from 1. Default 1."},
            "limit": {"type": "integer", "description": "How many lines to read. Default 2000."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    run=_read,
    permission=_read_permission,
)

APPLY_PATCH_DESCRIPTION = """Use the `apply_patch` tool to edit files.
Your patch language is a stripped‑down, file‑oriented diff format designed to be easy to parse and safe to apply. You can think of it as a high‑level envelope:

*** Begin Patch
[ one or more file sections ]
*** End Patch

Within that envelope, you get a sequence of file operations.
You MUST include a header to specify the action you are taking.
Each operation starts with one of three headers:

*** Add File: <path> - create a new file. Every following line is a + line (the initial contents).
*** Delete File: <path> - remove an existing file. Nothing follows.
*** Update File: <path> - patch an existing file in place (optionally with a rename).

May be immediately followed by *** Move to: <new path> if you want to rename the file.
Then one or more “hunks”, each introduced by @@ (optionally followed by a hunk header).
Within a hunk each line starts with:

For instructions on [context_before] and [context_after]:
- By default, show 3 lines of code immediately above and 3 lines immediately below each change. If a change is within 3 lines of a previous change, do NOT duplicate the first change’s [context_after] lines in the second change’s [context_before] lines.
- If 3 lines of context is insufficient to uniquely identify the snippet of code within the file, use the @@ operator to indicate the class or function to which the snippet belongs. For instance, we might have:
@@ class BaseClass
[3 lines of pre-context]
- [old_code]
+ [new_code]
[3 lines of post-context]

- If a code block is repeated so many times in a class or function such that even a single `@@` statement and 3 lines of context cannot uniquely identify the snippet of code, you can use multiple `@@` statements to jump to the right context. For instance:

@@ class BaseClass
@@ 	 def method():
[3 lines of pre-context]
- [old_code]
+ [new_code]
[3 lines of post-context]

The full grammar definition is below:
Patch := Begin { FileOp } End
Begin := "*** Begin Patch" NEWLINE
End := "*** End Patch" NEWLINE
FileOp := AddFile | DeleteFile | UpdateFile
AddFile := "*** Add File: " path NEWLINE { "+" line NEWLINE }
DeleteFile := "*** Delete File: " path NEWLINE
UpdateFile := "*** Update File: " path NEWLINE [ MoveTo ] { Hunk }
MoveTo := "*** Move to: " newPath NEWLINE
Hunk := "@@" [ header ] NEWLINE { HunkLine } [ "*** End of File" NEWLINE ]
HunkLine := (" " | "-" | "+") text NEWLINE

A full patch can combine several operations:

*** Begin Patch
*** Add File: hello.txt
+Hello world
*** Update File: src/app.py
*** Move to: src/main.py
@@ def greet():
-print("Hi")
+print("Hello, world!")
*** Delete File: obsolete.txt
*** End Patch

It is important to remember:

- You must include a header with your intended action (Add/Delete/Update)
- You must prefix new lines with `+` even when creating a new file
- File references should be relative to the workspace; changing files outside it needs the user's approval.
"""
"""Codex's description of its JSON apply_patch tool (codex-rs, Apache-2.0), with the last rule fitted to the workspace."""

APPLY_PATCH = Tool(
    name="apply_patch",
    group="apply_patch",
    description=APPLY_PATCH_DESCRIPTION,
    parameters={
        "type": "object",
        "properties": {"input": {"type": "string", "description": "The entire contents of the apply_patch command"}},
        "required": ["input"],
        "additionalProperties": False,
    },
    run=_apply_patch,
    permission=_patch_permission,
)
