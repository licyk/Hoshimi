"""Port selection: the port that is tested must be the port that serves."""

import errno
import json
import socket

import pytest

from hoshimi.core.net import ports
from hoshimi.core.net.ports import PortUnavailableError, bind_first_free_port
from hoshimi.core.net.runtime_file import read_runtime_file, remove_runtime_file, write_runtime_file


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hold(port: int) -> socket.socket:
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen()
    return s


def test_free_port_is_used():
    port = _free_port()
    sock = bind_first_free_port("127.0.0.1", port)
    try:
        assert sock.getsockname()[1] == port
    finally:
        sock.close()


def test_held_port_moves_to_next():
    port = _free_port()
    holder = _hold(port)
    try:
        sock = bind_first_free_port("127.0.0.1", port)
        assert sock.getsockname()[1] > port
        sock.close()
    finally:
        holder.close()


def test_strict_mode_does_not_move():
    port = _free_port()
    holder = _hold(port)
    try:
        with pytest.raises(PortUnavailableError, match="strict"):
            bind_first_free_port("127.0.0.1", port, strict=True)
    finally:
        holder.close()


def _taken(port: int) -> OSError:
    return OSError(errno.EADDRINUSE, f"port {port} is taken")


def test_search_has_no_attempt_limit(monkeypatch):
    # Far more taken ports than the old cap of 20.
    tried = []

    def fake_bind(host, port):
        tried.append(port)
        if port < 8000 + 500:
            raise _taken(port)
        return port

    monkeypatch.setattr(ports, "_bind", fake_bind)
    assert bind_first_free_port("127.0.0.1", 8000) == 8500
    assert tried == list(range(8000, 8501))


def test_search_wraps_around_below_the_start(monkeypatch):
    def fake_bind(host, port):
        if port >= 65000:
            raise _taken(port)
        return port

    monkeypatch.setattr(ports, "_bind", fake_bind)
    assert bind_first_free_port("127.0.0.1", 65000) == ports.LOWEST_FALLBACK_PORT


def test_every_port_taken_fails_after_trying_each_once(monkeypatch):
    tried = []

    def fake_bind(host, port):
        tried.append(port)
        raise _taken(port)

    monkeypatch.setattr(ports, "_bind", fake_bind)
    with pytest.raises(PortUnavailableError, match="No free port"):
        bind_first_free_port("127.0.0.1", 8000)
    assert sorted(tried) == list(range(ports.LOWEST_FALLBACK_PORT, ports.MAX_PORT + 1))


def test_an_error_another_port_would_not_fix_stops_the_search(monkeypatch):
    tried = []

    def fake_bind(host, port):
        tried.append(port)
        raise OSError(errno.EADDRNOTAVAIL, "address not available")

    monkeypatch.setattr(ports, "_bind", fake_bind)
    with pytest.raises(PortUnavailableError, match="Cannot bind"):
        bind_first_free_port("127.0.0.1", 8000)
    assert tried == [8000]


def test_runtime_file_matches_socket(tmp_path):
    sock = bind_first_free_port("127.0.0.1", _free_port())
    try:
        port = sock.getsockname()[1]
        write_runtime_file(tmp_path, "127.0.0.1", port, f"http://127.0.0.1:{port}")
        data = read_runtime_file(tmp_path)
        assert data is not None and data["port"] == port
        assert json.loads((tmp_path / "server.json").read_text())["url"].endswith(str(port))
        remove_runtime_file(tmp_path)
        assert read_runtime_file(tmp_path) is None
    finally:
        sock.close()


def test_runtime_file_of_a_dead_process_is_stale(tmp_path):
    import subprocess
    import sys

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    for pid in (child.pid, 0, -1):
        (tmp_path / "server.json").write_text(json.dumps({"host": "127.0.0.1", "port": 1, "url": "x", "pid": pid}))
        assert read_runtime_file(tmp_path) is None


def test_port_zero_lets_the_system_choose():
    sock = bind_first_free_port("127.0.0.1", 0, strict=True)
    try:
        assert sock.getsockname()[1] > 0
    finally:
        sock.close()


def test_out_of_range_ports_are_refused():
    with pytest.raises(PortUnavailableError, match="out of range"):
        bind_first_free_port("127.0.0.1", 70000)


def test_windows_never_probes_a_pid_with_kill(monkeypatch):
    """On Windows ``os.kill(pid, 0)`` is TerminateProcess: reading server.json must not end the server."""
    from hoshimi.core.net import runtime_file

    def killer(*_args):
        raise AssertionError("os.kill must not be called on Windows")

    monkeypatch.setattr(runtime_file.sys, "platform", "win32")
    monkeypatch.setattr(runtime_file.os, "kill", killer)
    monkeypatch.setattr(runtime_file, "_windows_pid_alive", lambda pid: pid == 4242)
    assert runtime_file._pid_alive(4242) is True
    assert runtime_file._pid_alive(4243) is False
    assert runtime_file._pid_alive(0) is False
