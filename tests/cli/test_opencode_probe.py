"""Diagnostic probes preserve service headers and reject Windows shell shims."""

import json

import httpx
import pytest

from scripts import opencode_transport_probe as probe


def test_windows_shell_launcher_requires_native_binary(tmp_path):
    launcher = tmp_path / "opencode"
    launcher.write_text("#!/bin/sh\nexec node opencode.cjs\n")
    with pytest.raises(ValueError, match="launcher"):
        probe.native_executable(str(launcher), windows=True)
    executable = tmp_path / "opencode.exe"
    executable.write_bytes(b"MZtest")
    assert probe.native_executable(str(launcher), windows=True) == str(executable.resolve())


def test_python_only_preserves_service_headers_without_resolving_opencode(monkeypatch, capsys):
    captured = []

    def respond(request):
        captured.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')

    monkeypatch.setattr(probe.sys, "argv", ["probe", "--python-only", "--headers", "service", "--prompt", "Reply with only OK."])
    monkeypatch.setattr(probe.shutil, "which", lambda _: pytest.fail("Python-only must not look for OpenCode"))
    monkeypatch.setattr(probe, "OpenCodeTransport", lambda **_: httpx.MockTransport(respond))
    probe.main()
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 2 and rows[1]["answer"] == "OK" and rows[1]["done"]
    assert rows[0]["header_profile"] == "service"
    assert len(captured) == 1
    assert captured[0].headers["connection"] == "keep-alive"
    assert captured[0].headers["accept-encoding"] == rows[0]["accept_encoding"]
    assert "gzip" in rows[0]["accept_encoding"]
    assert json.loads(captured[0].content)["messages"][-1]["content"] == "Reply with only OK."
