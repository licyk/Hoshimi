"""Exercise routing, fallback and real subprocess stream lifetime locally."""

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from hoshimi.core.net import bun
from hoshimi.core.net import client as networking
from hoshimi.core.net.bun import BunTransport
from hoshimi.core.net.client import NetworkClient
from hoshimi.core.net.policy import NetworkPolicy


def test_missing_runtime_falls_back_before_sending_and_records_it(monkeypatch):
    calls = []
    monkeypatch.setattr(networking, "runtime_path", lambda: None)
    monkeypatch.setattr(networking.httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(
        lambda request: (calls.append(request), httpx.Response(200, text="ok"))[1],
    ))
    monkeypatch.setattr(networking, "BunTransport", lambda **kwargs: pytest.fail("Bun must not start"))
    with NetworkClient(provider="opencode") as client:
        assert client.post("https://upstream.test/chat", extensions={"hoshimi_transport": "bun"}).text == "ok"
        observation = client.diagnostics()[0]
        assert observation["transport"] == "httpx"
        assert observation["transport_fallback"] == "bun_unavailable"
    assert len(calls) == 1


def test_upstream_quota_does_not_replay_on_httpx(monkeypatch):
    calls = []
    monkeypatch.setattr(networking, "runtime_path", lambda: "/installed/bun")
    monkeypatch.setattr(networking, "BunTransport", lambda **kwargs: httpx.MockTransport(
        lambda request: (calls.append(request), httpx.Response(429, headers={"retry-after": "60"}, text="quota"))[1],
    ))
    monkeypatch.setattr(networking.httpx, "HTTPTransport", lambda **kwargs: pytest.fail("Do not replay on HTTPX"))
    with NetworkClient(provider="opencode") as client:
        response = client.post("https://upstream.test/chat", extensions={"hoshimi_transport": "bun"})
        assert response.status_code == 429 and response.headers["retry-after"] == "60"
        assert client.diagnostics()[0]["transport"] == "bun"
    assert len(calls) == 1


def test_runtime_discovery_can_use_opencodes_embedded_bun(monkeypatch, tmp_path):
    monkeypatch.setattr(bun.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(bun.shutil, "which", lambda name: "/installed/opencode" if name == "opencode" else None)
    assert bun.runtime_path() == "/installed/opencode"


@pytest.fixture
def upstream():
    if bun.runtime_path() is None:
        pytest.skip("Bun/OpenCode not installed; routing regressions still run")
    release = threading.Event()
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("content-length", 0)))
            received.append((self.path, data, dict(self.headers)))
            self.send_response(200)
            if self.path == "/stream":
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"data: first\n\n")
                self.wfile.flush()
                release.wait(5)
            else:
                compressed = gzip.compress(data)
                self.send_header("content-encoding", "gzip")
                self.send_header("content-length", str(len(compressed)))
                self.end_headers()
                self.wfile.write(compressed)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def test_bun_direct_policy_ignores_environment_proxy_and_decodes_gzip(upstream, monkeypatch):
    url, received, _release = upstream
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    with NetworkClient(provider="opencode", network=NetworkPolicy("direct"), timeout=5) as client:
        response = client.post(url, json={"text": "你好"}, extensions={"hoshimi_transport": "bun"})
        assert response.json() == {"text": "你好"}
        assert "content-encoding" not in response.headers
        assert client.diagnostics()[0]["transport"] == "bun"
        assert client.diagnostics()[0]["route"] == "direct"
    assert len(received) == 1


def test_bun_uses_configured_proxy_without_direct_retry(upstream):
    url, received, _release = upstream
    with NetworkClient(provider="opencode", network=NetworkPolicy("proxy", url), timeout=5) as client:
        response = client.post("http://upstream.invalid/chat", json={"hello": "world"}, extensions={"hoshimi_transport": "bun"})
        assert response.json() == {"hello": "world"}
        assert client.diagnostics()[0]["route"] == "configured_proxy"
    assert received[0][0] == "http://upstream.invalid/chat"


@pytest.mark.parametrize("timeout", [False, True])
def test_bun_stream_close_and_read_timeout_reap_process(upstream, monkeypatch, timeout):
    url, received, release = upstream
    processes = []
    popen = bun.subprocess.Popen

    def start(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(bun.subprocess, "Popen", start)
    with httpx.Client(transport=BunTransport(), timeout=0.5 if timeout else 5) as client:
        with client.stream("POST", url + "/stream", json={"stream": True}) as response:
            chunks = response.iter_raw()
            assert next(chunks) == b"data: first\n\n"
            assert not release.is_set()  # Headers and first bytes precede completion.
            if timeout:
                with pytest.raises(httpx.ReadTimeout):
                    next(chunks)
        assert processes[0].poll() is not None
    assert json.loads(received[0][1]) == {"stream": True}
