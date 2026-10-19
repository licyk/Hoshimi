"""OpenCode v2 tool declarations and collision-safe request aliases.

Ported from dsh-our-free-model src/upstream.js (f0b979f, MIT). The upstream
compares tool descriptions and schemas, so preserve the declaration strings.
"""

from __future__ import annotations

import copy
from typing import Any

FINGERPRINT_TOOLS = ("read", "shell")
FINGERPRINT_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "read": {
        "description": "Read the contents of a file or directory. Supports text files, images, and PDFs. Images and PDFs are presented directly to the model. Each text "
        "line is prefixed by its 1-based line number as <line>: <content>. The prefix is for reference and is not part of the file content. Directory "
        "entries are returned one per line. Use offset and limit to read large files or directories in sections. Prefer one larger read over many small "
        "slices, and use grep to find specific content in large files.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory to read"},
                "offset": {"type": "integer", "minimum": 0, "description": "The line or directory entry to start reading from (1-based)"},
                "limit": {"type": "integer", "minimum": 0, "description": "The maximum number of lines or directory entries to read (defaults to and capped at 2000)"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "shell": {
        "description": "Execute a shell command and return its output. Commands run on Linux using bash. Quote file paths containing spaces or special characters. Prefer "
        "dedicated tools over shell commands when possible. When output is large, the full result is saved to a file and a truncated preview is returned. "
        "Rely on automatic truncation unless filtering the output is more useful. Commands accept an optional timeout, background commands have no timeout "
        "by default. Background commands return immediately, and you will be notified when they complete.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command string to execute"},
                "workdir": {
                    "type": "string",
                    "description": "Working directory to execute the command in. Defaults to the current working directory. When possible, "
                    "avoid changing directories in the command and set the working directory here instead.",
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Timeout in milliseconds. Set to 0 to disable the timeout. Defaults to 120000 for foreground commands. "
                    "Background commands have no timeout by default.",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run the command in the background and return immediately (useful for dev servers and long-running "
                    "builds). You do not need to use '&' at the end of the command when using this parameter. You will be "
                    "notified when it completes. DO NOT poll for completion.",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}


def tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    name = tool.get("name")
    if not isinstance(name, str):
        name = tool.get("function", {}).get("name") if isinstance(tool.get("function"), dict) else None
    return name.strip() if isinstance(name, str) else ""


def _rename_tool(tool: dict[str, Any], name: str) -> dict[str, Any]:
    fn = tool.get("function")
    return {**tool, "function": {**fn, "name": name}} if isinstance(fn, dict) else {**tool, "name": name}


def fingerprint_tool(name: str, style: str) -> dict[str, Any]:
    definition = copy.deepcopy(FINGERPRINT_TOOL_DEFINITIONS[name])
    if style == "claude":
        return {"name": name, "description": definition["description"], "input_schema": definition["parameters"]}
    fn = {"name": name, **definition}
    return {"type": "function", **fn} if style == "flat" else {"type": "function", "function": fn}


def _carries_definition(tool: dict[str, Any], name: str, style: str) -> bool:
    definition = tool if style in ("flat", "claude") else tool.get("function")
    if not isinstance(definition, dict):
        return False
    expected = FINGERPRINT_TOOL_DEFINITIONS[name]
    return definition.get("description") == expected["description"] and definition.get("input_schema" if style == "claude" else "parameters") == expected["parameters"]


def _alias_history(body: dict[str, Any], aliases: dict[str, str]) -> None:
    if not aliases:
        return
    if isinstance(body.get("input"), list):
        body["input"] = [
            {**item, "name": aliases.get(item.get("name"), item.get("name"))} if isinstance(item, dict) and item.get("type") == "function_call" else item for item in body["input"]
        ]
    if not isinstance(body.get("messages"), list):
        return
    messages = copy.deepcopy(body["messages"])
    for message in messages:
        if not isinstance(message, dict):
            continue
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in aliases:
                block["name"] = aliases[block["name"]]
        for call in message.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if isinstance(fn, dict) and fn.get("name") in aliases:
                fn["name"] = aliases[fn["name"]]
    body["messages"] = messages


def _note_injected(body: dict[str, Any], style: str, names: list[str]) -> None:
    label = " or ".join(f"`{name}`" for name in names)
    noun = "tools; they are placeholders" if len(names) > 1 else "tool; it is a placeholder"
    action = "do" if len(names) > 1 else "does"
    note = f"Do not call the {label} {noun} that {action} nothing. Use the other tools you have been given instead."
    if style == "flat":
        instructions = body.get("instructions")
        body["instructions"] = f"{instructions}\n\n{note}" if isinstance(instructions, str) and instructions else note
    elif style == "claude":
        system = body.get("system")
        if isinstance(system, list):
            body["system"] = [*system, {"type": "text", "text": note}]
        else:
            body["system"] = f"{system}\n\n{note}" if isinstance(system, str) and system else note
    else:
        messages = list(body.get("messages") or [])
        if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
            messages[0] = {**messages[0], "content": f"{messages[0]['content']}\n\n{note}"}
        else:
            messages.insert(0, {"role": "system", "content": note})
        body["messages"] = messages


def apply_fingerprint(body: dict[str, Any], style: str) -> dict[str, str]:
    """Add v2 declarations and return sent-name to caller-name aliases, in place."""
    raw_tools = body.get("tools")
    tools = raw_tools if isinstance(raw_tools, list) else []
    taken = {tool_name(tool) for tool in tools}
    seen: set[str] = set()
    present: set[str] = set()
    rename: dict[str, str] = {}
    out: list[Any] = []
    for tool in tools:
        name = tool_name(tool)
        if name not in FINGERPRINT_TOOLS:
            out.append(tool)
            continue
        if name in seen:
            continue
        seen.add(name)
        if _carries_definition(tool, name, style):
            present.add(name)
            out.append(tool)
            continue
        alias = f"{name}_client"
        index = 2
        while alias in taken:
            alias = f"{name}_client{index}"
            index += 1
        taken.add(alias)
        rename[alias] = name
        out.append(_rename_tool(tool, alias))

    injected = [name for name in FINGERPRINT_TOOLS if name not in present]
    body["tools"] = [*out, *(fingerprint_tool(name, style) for name in injected)]
    aliases = {original: sent for sent, original in rename.items()}
    choice = body.get("tool_choice")
    if isinstance(choice, dict):
        name = tool_name(choice)
        if name in aliases:
            body["tool_choice"] = _rename_tool(choice, aliases[name])
    _alias_history(body, aliases)
    if injected:
        _note_injected(body, style, injected)
    if choice == "none" or (isinstance(choice, dict) and choice.get("type") == "none"):
        body.pop("tool_choice", None)
    if style == "flat" and not body.get("tool_choice"):
        body["tool_choice"] = "auto"
    return rename


def restore_tool_name(name: Any, rename: dict[str, str]) -> Any:
    return rename.get(name, name) if isinstance(name, str) else name
