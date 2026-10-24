"""Commands: ``shell`` waits for one; ``exec_command``, ``write_stdin`` and ``kill_session`` keep one running.

Shaped after Codex's tools of the same names. A command runs in the shell
its call names (``shell``: bash, zsh, sh, PowerShell or cmd, as installed),
or else the one Settings choose (``playground.shell``; ``auto`` is bash, sh
without it, and PowerShell on Windows), in a process group of its own, so
stopping it stops whatever it started. Output
(standard error merged in) is read as it comes; what a model is sent of it is
cut in the middle past :data:`~hoshimi.core.agent.base.OUTPUT_LIMIT`.

A background command (``exec_command``) is waited for a little while; if it is
still running then, it keeps running as a *session*, which belongs to the
conversation that started it. The model reads its new output and writes to
its input with ``write_stdin``, and stops it with ``kill_session``; the page
lists and stops them too. Sessions end with their conversation and with the
server.

There is no sandbox: a command can do whatever the user running Hoshimi can.
"""

from __future__ import annotations

import codecs
import functools
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any

from hoshimi.core.agent.base import Cancelled, Permission, Tool, ToolContext, ToolError, ToolResult, clip, integer, string

SHELL_TIMEOUT_MS = 60_000
SHELL_TIMEOUT_MAX_MS = 600_000
YIELD_MS = 10_000
YIELD_MAX_MS = 30_000
WRITE_YIELD_MS = 250
POLL_YIELD_MS = 5_000
POLL_YIELD_MAX_MS = 120_000
MAX_SESSIONS = 16
CAPTURE_LIMIT = 1_000_000
"""Characters of unread output a command keeps; the middle of more is dropped."""

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_ENV = {"PAGER": "cat", "GIT_PAGER": "cat", "NO_COLOR": "1", "CLICOLOR": "0", "TERM": "dumb", "GIT_TERMINAL_PROMPT": "0"}


# -- shells -------------------------------------------------------------------------- #
SHELL_KINDS = ("bash", "zsh", "sh", "powershell", "cmd")
"""The shells a command can run in, by the name a call and Settings give them (``pwsh`` is ``powershell``)."""

_BINARIES = {"bash": ("bash",), "zsh": ("zsh",), "sh": ("sh",), "powershell": ("pwsh", "powershell"), "cmd": ("cmd",)}
"""What each is called on the ``PATH``, in the order looked for: PowerShell 7 before Windows PowerShell."""
_FALLBACKS = {
    "bash": ("/bin/bash", "/usr/bin/bash"),
    "zsh": ("/bin/zsh", "/usr/bin/zsh"),
    "sh": ("/bin/sh",),
    "powershell": (r"C:\Program Files\PowerShell\7\pwsh.exe",) if os.name == "nt" else ("/usr/local/bin/pwsh", "/usr/bin/pwsh"),
    "cmd": (),
}
"""Where each usually is, for a ``PATH`` that does not have it."""


@dataclass(frozen=True)
class Shell:
    kind: str
    """One of :data:`SHELL_KINDS`."""
    path: str

    def argv(self, command: str) -> list[str]:
        """What runs ``command`` in this shell: never as a login shell, never reading a profile."""
        if self.kind == "powershell":
            return [self.path, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
        if self.kind == "cmd":
            return [self.path, "/d", "/s", "/c", command]
        return [self.path, "-c", command]


def shell_kind(name: str) -> str | None:
    """The kind of shell a name or a path is (``/usr/bin/pwsh`` and ``powershell.exe`` are ``powershell``); None for another."""
    stem = re.split(r"[\\/]", name.strip())[-1].lower()
    stem = stem.removesuffix(".exe")
    if stem == "pwsh":
        return "powershell"
    return stem if stem in SHELL_KINDS else None


@functools.cache
def find_shell(kind: str) -> Shell | None:
    """Where ``kind`` is installed here; None when it is not (looked for once)."""
    for binary in _BINARIES.get(kind, ()):
        path = shutil.which(binary)
        if path:
            return Shell(kind, path)
    if kind == "cmd" and os.name == "nt":
        return Shell("cmd", os.environ.get("COMSPEC") or "cmd.exe")
    for path in _FALLBACKS.get(kind, ()):
        if os.path.isfile(path):
            return Shell(kind, path)
    return None


def available_shells() -> list[str]:
    """The shells installed here, in the order of :data:`SHELL_KINDS`."""
    return [kind for kind in SHELL_KINDS if find_shell(kind) is not None]


def default_shell(setting: str = "auto") -> Shell:
    """The shell a call that names none runs in: ``playground.shell`` when it is installed, otherwise bash (sh without it), PowerShell on Windows."""
    if setting and setting != "auto":
        chosen = find_shell(setting)
        if chosen is not None:
            return chosen
    for kind in ("powershell", "cmd") if os.name == "nt" else ("bash", "sh", "zsh"):
        found = find_shell(kind)
        if found is not None:
            return found
    return Shell("cmd", os.environ.get("COMSPEC") or "cmd.exe") if os.name == "nt" else Shell("sh", "/bin/sh")


def pick_shell(args: dict[str, Any], ctx: ToolContext) -> Shell:
    """The shell a call runs in: the one it names (by name or by path), or the default."""
    raw = string(args, "shell", required=False).strip()
    if not raw:
        return default_shell(ctx.shell)
    kind = shell_kind(raw)
    if kind is None:
        raise ToolError(f"Unknown shell {raw!r}; use one of: {', '.join(available_shells())}.")
    if "/" in raw or "\\" in raw:
        path = os.path.expanduser(raw)
        if not os.path.isfile(path):
            raise ToolError(f"There is no shell at {raw}; use one of: {', '.join(available_shells())}.")
        return Shell(kind, path)
    found = find_shell(kind)
    if found is None:
        raise ToolError(f"{kind} is not installed here; use one of: {', '.join(available_shells())}.")
    return found


def with_shells(tool: Tool, setting: str = "auto") -> Tool:
    """A command tool as a model is offered it: told which shells are here, and which one it gets when it names none."""
    shell = tool.parameters.get("properties", {}).get("shell")
    if shell is None:
        return tool
    default = default_shell(setting).kind
    shells = ", ".join(available_shells()) or default
    properties = {**tool.parameters["properties"], "shell": {**shell, "description": shell["description"].format(default=default, shells=shells)}}
    return replace(tool, description=tool.description.format(default=default), parameters={**tool.parameters, "properties": properties})


def spawn(argv: list[str], cwd: str, *, stdin: bool) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, **_ENV},
            start_new_session=os.name != "nt", creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        raise ToolError(f"Could not start the command: {exc.strerror or exc}") from None


def kill_tree(proc: subprocess.Popen[bytes], *, grace: float = 2.0) -> None:
    """Stop ``proc`` and everything it started: politely, then not."""
    if os.name == "nt":
        if proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(grace)
            if sig == signal.SIGTERM:
                # The shell is gone; what it left behind in its group goes too.
                os.killpg(proc.pid, signal.SIGKILL)
            return
        except subprocess.TimeoutExpired:
            continue
        except (ProcessLookupError, PermissionError):
            return


class Capture:
    """A command's output as it comes, read on a thread of its own; unread output past the limit loses its middle."""

    def __init__(self, stream: Any, limit: int = CAPTURE_LIMIT) -> None:
        self.limit = limit
        self.lock = threading.Lock()
        self.head = ""
        self.tail = ""
        self.dropped = 0
        self.thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self.thread.start()

    def _pump(self, stream: Any) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while chunk := stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536):
                self._add(decoder.decode(chunk))
        except (OSError, ValueError):
            pass
        self._add(decoder.decode(b"", final=True))

    def _add(self, text: str) -> None:
        if not text:
            return
        with self.lock:
            if len(self.head) < self.limit // 2:
                room = self.limit // 2 - len(self.head)
                self.head += text[:room]
                text = text[room:]
            self.tail += text
            over = len(self.tail) - self.limit // 2
            if over > 0:
                self.dropped += over
                self.tail = self.tail[over:]

    def take(self) -> str:
        """The output not read yet."""
        with self.lock:
            text = self.head + (f"\n…{self.dropped} characters dropped…\n" if self.dropped else "") + self.tail
            self.head, self.tail, self.dropped = "", "", 0
        return _ANSI.sub("", text)

    def finish(self, timeout: float = 1.0) -> None:
        self.thread.join(timeout)


def _seconds(start: float) -> str:
    return f"{time.monotonic() - start:.1f}"


def _wait(proc: subprocess.Popen[bytes], deadline: float, cancel: threading.Event) -> None:
    while proc.poll() is None and time.monotonic() < deadline:
        if cancel.wait(0.05):
            return


def workdir(args: dict[str, Any], ctx: ToolContext) -> str:
    raw = string(args, "workdir", required=False)
    path = ctx.resolve(raw) if raw.strip() else ctx.workspace
    if not raw.strip():
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ToolError(f"The working directory {path} does not exist.")
    return str(path)


# -- shell: run and wait ---------------------------------------------------------- #
def run_shell(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = string(args, "command")
    cwd = workdir(args, ctx)
    shell = pick_shell(args, ctx)
    timeout_ms = integer(args, "timeout_ms", SHELL_TIMEOUT_MS, low=1000, high=SHELL_TIMEOUT_MAX_MS)
    start = time.monotonic()
    proc = spawn(shell.argv(command), cwd, stdin=False)
    assert proc.stdout is not None
    capture = Capture(proc.stdout)
    try:
        _wait(proc, start + timeout_ms / 1000, ctx.cancel)
    finally:
        timed_out = proc.poll() is None
        kill_tree(proc, grace=1.0)
        capture.finish()
    output = capture.take()
    if ctx.cancel.is_set() and timed_out:
        raise Cancelled()
    code = proc.returncode if not timed_out else -1
    note = f"command timed out after {timeout_ms} milliseconds\n" if timed_out else ""
    text = f"Exit code: {code}\nWall time: {_seconds(start)} seconds\nOutput:\n{note}{clip(output)}"
    return ToolResult(text, ok=code == 0, exit_code=code, meta={"cwd": cwd, "shell": shell.kind, "timed_out": timed_out})


# -- background sessions ------------------------------------------------------------ #
@dataclass
class Session:
    id: int
    owner: str
    command: str
    cwd: str
    proc: subprocess.Popen[bytes]
    capture: Capture
    shell: str = "sh"
    started_at: float = field(default_factory=time.time)

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id, "command": self.command, "cwd": self.cwd, "shell": self.shell, "started_at": self.started_at, "running": self.running,
            "exit_code": self.proc.returncode,
        }


class TerminalManager:
    """The background commands of every conversation, by session id."""

    def __init__(self, limit: int = MAX_SESSIONS) -> None:
        self.limit = limit
        self.lock = threading.Lock()
        self.sessions: dict[int, Session] = {}
        self.next_id = 1

    def _get(self, owner: str, session_id: int) -> Session:
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None or session.owner != owner:
            raise ToolError(f"Unknown session ID {session_id}; it may have exited and been read already.")
        return session

    def _report(self, session: Session, start: float) -> ToolResult:
        """What happened since the last look: the new output, and whether it still runs."""
        running = session.running
        if not running:
            session.capture.finish()
        output = session.capture.take()
        if running:
            state = f"Process running with session ID {session.id}"
        else:
            state = f"Process exited with code {session.proc.returncode}"
            with self.lock:
                self.sessions.pop(session.id, None)
        text = f"Wall time: {_seconds(start)} seconds\n{state}\nOutput:\n{clip(output)}"
        meta = {"session_id": session.id, "running": running, "cwd": session.cwd, "shell": session.shell}
        return ToolResult(text, ok=running or session.proc.returncode == 0, exit_code=None if running else session.proc.returncode, meta=meta)

    def start(self, owner: str, command: str, cwd: str, yield_ms: int, cancel: threading.Event, shell: Shell | None = None) -> ToolResult:
        with self.lock:
            live = [s for s in self.sessions.values() if s.running]
            if len(live) >= self.limit:
                raise ToolError(f"{self.limit} background commands are running already; stop one with kill_session first.")
            session_id = self.next_id
            self.next_id += 1
        shell = shell or default_shell()
        start = time.monotonic()
        proc = spawn(shell.argv(command), cwd, stdin=True)
        assert proc.stdout is not None
        session = Session(session_id, owner, command, cwd, proc, Capture(proc.stdout), shell.kind)
        with self.lock:
            self.sessions[session_id] = session
        _wait(proc, start + yield_ms / 1000, cancel)
        return self._report(session, start)

    def write(self, owner: str, session_id: int, chars: str, yield_ms: int, cancel: threading.Event) -> ToolResult:
        session = self._get(owner, session_id)
        start = time.monotonic()
        if chars:
            if not session.running:
                raise ToolError(f"Session {session_id} has exited; its input is closed.")
            try:
                assert session.proc.stdin is not None
                session.proc.stdin.write(chars.encode("utf-8"))
                session.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                raise ToolError(f"Session {session_id} no longer reads its input.") from None
        _wait(session.proc, start + yield_ms / 1000, cancel)
        return self._report(session, start)

    def kill(self, owner: str, session_id: int) -> ToolResult:
        session = self._get(owner, session_id)
        kill_tree(session.proc)
        session.capture.finish()
        with self.lock:
            self.sessions.pop(session_id, None)
        output = session.capture.take()
        return ToolResult(f"Session {session_id} stopped (exit code {session.proc.returncode}).\nOutput:\n{clip(output)}", meta={"session_id": session_id})

    def of(self, owner: str) -> list[dict[str, Any]]:
        with self.lock:
            return [s.view() for s in self.sessions.values() if s.owner == owner]

    def stop(self, owner: str, session_id: int) -> bool:
        """Stop a session from the page; False for one that is not this conversation's."""
        try:
            self.kill(owner, session_id)
        except ToolError:
            return False
        return True

    def end(self, owner: str) -> None:
        """Stop every existing session of a conversation, leaving other conversations alone."""
        with self.lock:
            ids = [s.id for s in self.sessions.values() if s.owner == owner]
        for session_id in ids:
            self.stop(owner, session_id)

    def close(self) -> None:
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            kill_tree(session.proc, grace=0.5)


def _terminals(ctx: ToolContext) -> TerminalManager:
    if ctx.terminals is None:
        raise ToolError("Background commands are not available here.")
    return ctx.terminals


def _session_id(args: dict[str, Any]) -> int:
    value = args.get("session_id")
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().isdigit():
        raise ToolError("'session_id' must be the number exec_command returned.")
    return int(value)


def run_exec_command(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = string(args, "cmd")
    cwd = workdir(args, ctx)
    shell = pick_shell(args, ctx)
    yield_ms = integer(args, "yield_time_ms", YIELD_MS, low=250, high=YIELD_MAX_MS)
    result = _terminals(ctx).start(ctx.conversation_id, command, cwd, yield_ms, ctx.cancel, shell)
    result.meta["command"] = command
    return result


def run_write_stdin(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    chars = string(args, "chars", required=False)
    default, high = (WRITE_YIELD_MS, YIELD_MAX_MS) if chars else (POLL_YIELD_MS, POLL_YIELD_MAX_MS)
    yield_ms = integer(args, "yield_time_ms", default, low=250 if chars else 1000, high=high)
    return _terminals(ctx).write(ctx.conversation_id, _session_id(args), chars, yield_ms, ctx.cancel)


def run_kill_session(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return _terminals(ctx).kill(ctx.conversation_id, _session_id(args))


_WORKDIR = {"type": "string", "description": "The directory to run in, absolute or relative to the workspace. Default: the workspace."}
_SHELL = {
    "type": "string",
    "description": (
        "The shell to run it in, by name or by path: one of {shells} (installed here). Default: {default}. "
        "Write the command in that shell's own syntax (PowerShell cmdlets for powershell, cmd builtins for cmd)."
    ),
}

SHELL = Tool(
    name="shell",
    group="shell",
    description=(
        "Run a shell command and wait for it to finish, then return its exit code and output (standard error included). "
        "Runs in {default} in the workspace unless `shell` or `workdir` say otherwise. For something that keeps "
        "running (a server, a watcher) or asks for input, use exec_command instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command line to run."},
            "shell": _SHELL,
            "workdir": _WORKDIR,
            "timeout_ms": {"type": "integer", "description": f"Stop it after this long. Default {SHELL_TIMEOUT_MS}, at most {SHELL_TIMEOUT_MAX_MS}."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    run=run_shell,
    permission=lambda args, ctx: Permission("command"),
)

EXEC_COMMAND = Tool(
    name="exec_command",
    group="terminal",
    description=(
        "Start a command in the background, in {default} unless `shell` says otherwise. Waits up to `yield_time_ms` for it: "
        "if it finishes, returns its exit code and output; if it is still running, returns what it printed so far and a session ID. "
        "Read its new output or type into it with write_stdin, and stop it with kill_session."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "The command line to run."},
            "shell": _SHELL,
            "workdir": _WORKDIR,
            "yield_time_ms": {"type": "integer", "description": f"How long to wait before returning. Default {YIELD_MS}, from 250 to {YIELD_MAX_MS}."},
        },
        "required": ["cmd"],
        "additionalProperties": False,
    },
    run=run_exec_command,
    permission=lambda args, ctx: Permission("command"),
)

WRITE_STDIN = Tool(
    name="write_stdin",
    group="terminal",
    description=(
        "Write to a background command's input and return its new output; with empty `chars`, just wait and return its new "
        "output. Says whether it is still running, or its exit code."
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {"type": "integer", "description": "The session ID exec_command returned."},
            "chars": {"type": "string", "description": "What to type, with \\n for Enter. Empty to only read new output."},
            "yield_time_ms": {"type": "integer", "description": f"How long to wait for output. Default {WRITE_YIELD_MS} after writing, {POLL_YIELD_MS} when only reading (at most {POLL_YIELD_MAX_MS})."},
        },
        "required": ["session_id"],
        "additionalProperties": False,
    },
    run=run_write_stdin,
    # Typing into a shell runs commands; only reading its output does not.
    permission=lambda args, ctx: Permission("command") if isinstance(args.get("chars"), str) and args["chars"] else None,
)

KILL_SESSION = Tool(
    name="kill_session",
    group="terminal",
    description="Stop a background command, and everything it started, and return its last output.",
    parameters={
        "type": "object",
        "properties": {"session_id": {"type": "integer", "description": "The session ID exec_command returned."}},
        "required": ["session_id"],
        "additionalProperties": False,
    },
    run=run_kill_session,
)
