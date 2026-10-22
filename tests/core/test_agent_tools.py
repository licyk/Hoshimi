"""The Playground agent's built-in tools: read, apply_patch, shell, background commands, web search and fetch."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from hoshimi.core.agent.base import ToolContext, clip
from hoshimi.core.agent.runtime import Approvals
from hoshimi.core.agent.terminals import TerminalManager
from hoshimi.core.agent.tools import TOOLS, parse_arguments, run_tool
from hoshimi.core.agent.web import check_url, html_to_markdown

posix = pytest.mark.skipif(os.name == "nt", reason="POSIX shell")


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return ToolContext(workspace=workspace, conversation_id="c1", terminals=TerminalManager())


def call(name: str, ctx: ToolContext, **args: Any):
    return run_tool(TOOLS[name], name, args, ctx)


# -- read ------------------------------------------------------------------------- #
def test_read_numbers_lines_and_reads_on_from_an_offset(ctx: ToolContext) -> None:
    (ctx.workspace / "a.txt").write_text("".join(f"line {i}\r\n" for i in range(1, 11)))
    result = call("read", ctx, path="a.txt", offset=3, limit=2)
    assert result.ok
    assert result.output == "L3: line 3\nL4: line 4\n[Lines 3–4 of 10. Read on with offset=5.]"
    assert call("read", ctx, path="a.txt", offset=10).output == "L10: line 10"
    assert "past the end" in call("read", ctx, path="a.txt", offset=11).output


def test_read_lists_a_directory_and_refuses_binary_and_missing_files(ctx: ToolContext) -> None:
    (ctx.workspace / "sub").mkdir()
    (ctx.workspace / "b.bin").write_bytes(b"\x00\x01")
    assert call("read", ctx, path=".").output.endswith("sub/\nb.bin")
    assert "binary" in call("read", ctx, path="b.bin").output
    missing = call("read", ctx, path="nope.txt")
    assert not missing.ok and "No such file" in missing.output


def test_read_outside_the_workspace_asks_first(ctx: ToolContext, tmp_path: Path) -> None:
    read = TOOLS["read"]
    assert read.approval({"path": "inside.txt"}, ctx) is None
    assert read.approval({"path": str(tmp_path / "elsewhere.txt")}, ctx) == "reads outside the workspace"
    assert read.approval({"path": "../escape.txt"}, ctx) == "reads outside the workspace"


def test_a_long_line_is_cut(ctx: ToolContext) -> None:
    (ctx.workspace / "long.txt").write_text("x" * 600 + "\n")
    assert call("read", ctx, path="long.txt").output.endswith("… [100 more characters]")


# -- apply_patch ---------------------------------------------------------------------- #
def test_apply_patch_changes_files_in_the_workspace(ctx: ToolContext) -> None:
    result = call("apply_patch", ctx, input="*** Begin Patch\n*** Add File: src/new.py\n+print('hi')\n*** End Patch")
    assert result.ok and result.output == "Success. Updated the following files:\nA src/new.py\n"
    assert result.meta["files"] == [{"path": "src/new.py", "kind": "add"}]
    assert (ctx.workspace / "src/new.py").read_text() == "print('hi')\n"


def test_apply_patch_refuses_paths_outside_the_workspace(ctx: ToolContext, tmp_path: Path) -> None:
    for path in ("../outside.txt", str(tmp_path / "outside.txt")):
        result = call("apply_patch", ctx, input=f"*** Begin Patch\n*** Add File: {path}\n+x\n*** End Patch")
        assert not result.ok and "outside the workspace" in result.output
    assert not (tmp_path / "outside.txt").exists()


def test_apply_patch_failures_are_told_to_the_model(ctx: ToolContext) -> None:
    result = call("apply_patch", ctx, input="*** Begin Patch\n*** Update File: missing.txt\n@@\n-a\n+b\n*** End Patch")
    assert not result.ok
    assert result.output == "apply_patch verification failed: Failed to read file to update missing.txt: no such file"


# -- arguments ---------------------------------------------------------------------- #
def test_arguments_must_be_one_json_object() -> None:
    assert parse_arguments("") == {}
    assert parse_arguments('{"path": "a"}') == {"path": "a"}
    with pytest.raises(Exception, match="not valid JSON"):
        parse_arguments("{path")
    with pytest.raises(Exception, match="one JSON object"):
        parse_arguments("[1]")


def test_an_unknown_tool_is_unsupported(ctx: ToolContext) -> None:
    result = run_tool(None, "teleport", {}, ctx)
    assert (result.ok, result.output) == (False, "unsupported call: teleport")


def test_clip_keeps_the_start_and_the_end() -> None:
    text = "a" * 50 + "b" * 50
    assert clip(text, 100) == text
    cut = clip(text, 20)
    assert cut.startswith("a" * 10) and cut.endswith("b" * 10) and "80 characters cut" in cut


# -- shell ----------------------------------------------------------------------------- #
@posix
def test_shell_runs_in_the_workspace_and_reports_the_exit_code(ctx: ToolContext) -> None:
    result = call("shell", ctx, command="pwd; echo oops >&2; exit 3")
    assert not result.ok and result.exit_code == 3
    assert result.output.startswith("Exit code: 3\nWall time: ")
    assert f"Output:\n{ctx.workspace}\noops\n" in result.output


@posix
def test_shell_stops_a_command_that_runs_too_long_with_what_it_started(ctx: ToolContext, tmp_path: Path) -> None:
    marker = tmp_path / "child-ran"
    started = time.monotonic()
    result = call("shell", ctx, command=f"(sleep 3; touch {marker}) & echo started; sleep 30", timeout_ms=1000)
    assert time.monotonic() - started < 6
    assert result.exit_code == -1 and "command timed out after 1000 milliseconds" in result.output and "started" in result.output
    time.sleep(3.5)
    assert not marker.exists()


@posix
def test_a_stop_ends_a_running_command(ctx: ToolContext) -> None:
    threading.Timer(0.5, ctx.cancel.set).start()
    started = time.monotonic()
    result = call("shell", ctx, command="sleep 30")
    assert time.monotonic() - started < 5
    assert not result.ok and result.meta.get("cancelled")


@posix
def test_a_background_command_is_read_written_to_and_killed(ctx: ToolContext) -> None:
    script = "echo ready; while read line; do echo got:$line; done"
    started = call("exec_command", ctx, cmd=script, yield_time_ms=500)
    assert started.ok and "Process running with session ID 1" in started.output and "ready" in started.output
    sid = started.meta["session_id"]
    assert ctx.terminals is not None and [t["id"] for t in ctx.terminals.of("c1")] == [sid]
    answered = call("write_stdin", ctx, session_id=sid, chars="hello\n", yield_time_ms=500)
    assert "got:hello" in answered.output and "ready" not in answered.output
    # Another conversation cannot touch it.
    other = ToolContext(workspace=ctx.workspace, conversation_id="c2", terminals=ctx.terminals)
    assert "Unknown session ID" in call("write_stdin", other, session_id=sid).output
    killed = call("kill_session", ctx, session_id=sid)
    assert killed.ok and f"Session {sid} stopped" in killed.output
    assert ctx.terminals.of("c1") == []


@posix
def test_a_background_command_that_finishes_reports_its_exit_code(ctx: ToolContext) -> None:
    done = call("exec_command", ctx, cmd="echo hi; exit 4", yield_time_ms=5000)
    assert "Process exited with code 4" in done.output and "hi" in done.output and done.exit_code == 4
    assert ctx.terminals is not None and ctx.terminals.of("c1") == []


@posix
def test_too_many_background_commands_are_refused(ctx: ToolContext) -> None:
    ctx.terminals = TerminalManager(limit=1)
    assert call("exec_command", ctx, cmd="sleep 30", yield_time_ms=250).meta["running"]
    assert "running already" in call("exec_command", ctx, cmd="sleep 30", yield_time_ms=250).output
    ctx.terminals.close()


# -- approvals ------------------------------------------------------------------------- #
def test_an_approval_waits_for_the_answer_and_always_is_remembered() -> None:
    approvals = Approvals()
    cancel = threading.Event()
    threading.Timer(0.2, lambda: approvals.answer("c1", "call-1", "always")).start()
    assert approvals.ask("c1", "call-1", "shell", cancel) == "always"
    assert approvals.allowed("c1", "shell") and not approvals.allowed("c2", "shell")
    assert not approvals.answer("c1", "call-1", "allow")  # nothing waits any more
    assert approvals.ask("c1", "call-2", "shell", cancel, timeout=0.2) == "deny"
    threading.Timer(0.2, cancel.set).start()
    assert approvals.ask("c1", "call-3", "shell", cancel) is None


# -- web ---------------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", ["http://127.0.0.1:10600/api", "http://localhost/", "http://10.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest", "file:///etc/passwd", "http://user:pw@example.com/"])
def test_web_fetch_reads_public_pages_only(url: str) -> None:
    with pytest.raises(Exception, match="local or private|Only http|user name"):
        check_url(url)


def test_web_fetch_follows_redirects_only_to_public_addresses(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from hoshimi.core.agent import web

    monkeypatch.setattr(web, "public_address", lambda host: host != "internal.example")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://internal.example/secret"})
        return httpx.Response(200, text="secret")

    ctx.transport = httpx.MockTransport(handler)
    result = call("web_fetch", ctx, url="https://public.example/")
    assert not result.ok and "internal.example is a local or private address" in result.output


def test_web_fetch_returns_a_page_as_markdown(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from hoshimi.core.agent import web

    monkeypatch.setattr(web, "public_address", lambda host: True)
    page = "<html><head><title>Doc</title></head><body><nav>menu</nav><h1>Hello</h1><p>See <a href='/x'>this</a>.</p></body></html>"
    ctx.transport = httpx.MockTransport(lambda request: httpx.Response(200, html=page))
    result = call("web_fetch", ctx, url="example.com/page")
    assert result.ok
    assert result.output == "URL: https://example.com/page\nTitle: Doc\n\n# Hello\n\nSee [this](https://example.com/x)."
    ctx.transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"}))
    assert "only text can be read" in call("web_fetch", ctx, url="https://example.com/a.png").output


def test_html_to_markdown_prefers_the_main_content() -> None:
    body = "<p>" + "Real content. " * 20 + "</p>"
    title, text = html_to_markdown(f"<title>T</title><div>Sidebar junk</div><main>{body}<pre>code  kept\n  indented</pre><ol><li>one</li><li>two</li></ol></main>")
    assert title == "T" and "Sidebar" not in text
    assert "```\ncode  kept\n  indented\n```" in text and "1. one\n2. two" in text


DUCK = """<table><tr><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python-httpx.org%2F&amp;rut=x" class='result-link'>HTTPX</a></td></tr>
<tr><td class='result-snippet'>
  A next-generation HTTP client for <b>Python</b>.
</td></tr></table>"""


def test_web_search_reads_duckduckgo_results(ctx: ToolContext) -> None:
    ctx.transport = httpx.MockTransport(lambda request: httpx.Response(200, html=DUCK))
    result = call("web_search", ctx, query="httpx")
    assert result.ok
    assert result.output == "Results for “httpx”:\n\n1. HTTPX\n   https://www.python-httpx.org/\n   A next-generation HTTP client for Python."


def test_web_search_can_ask_searxng(ctx: ToolContext) -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t.example/", "content": "snip"}]})

    ctx.search_url, ctx.transport = "https://searx.example/", httpx.MockTransport(handler)
    assert "1. T\n   https://t.example/\n   snip" in call("web_search", ctx, query="q").output
    assert str(seen[0]) == "https://searx.example/search?q=q&format=json"


def test_web_search_says_when_duckduckgo_refuses(ctx: ToolContext) -> None:
    ctx.transport = httpx.MockTransport(lambda request: httpx.Response(202, html="<html></html>"))
    result = call("web_search", ctx, query="q")
    assert not result.ok and "SearXNG" in result.output

