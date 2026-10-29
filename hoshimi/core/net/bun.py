"""Bun fetch behind HTTPX's transport interface for OpenCode's anonymous lane.

The official client uses Bun's TLS stack. Replaying the same request through
OpenSSL can receive FreeUsageLimitError even when Bun succeeds. Credentials and
bodies travel over stdin, never command arguments or temporary files.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import httpx

_FETCH = r"""
const input = JSON.parse(await Bun.stdin.text());
const emit = async (value) => {
  const line = JSON.stringify(value) + '\n';
  if (!process.stdout.write(line)) await new Promise(resolve => process.stdout.once('drain', resolve));
};
try {
  const response = await fetch(input.url, {
    method: input.method,
    headers: input.headers,
    body: input.body === null ? undefined : Buffer.from(input.body, 'base64'),
    proxy: input.proxy || undefined,
    redirect: 'manual',
    tls: process.env.SSL_CERT_FILE ? {ca: await Bun.file(process.env.SSL_CERT_FILE).text()} : undefined,
  });
  const headers = [...response.headers].filter(([key]) =>
    !['content-encoding', 'content-length', 'transfer-encoding'].includes(key));
  await emit({status: response.status, headers});
  if (response.body) for await (const chunk of response.body)
    await emit({data: Buffer.from(chunk).toString('base64')});
  await emit({end: true});
} catch {
  // Do not expose credential-bearing URLs or proxy addresses in errors.
  await emit({error: true});
}
"""


def runtime_path() -> str | None:
    """An installed OpenCode executable also exposes its embedded Bun runtime."""
    for name, directory in (("bun", ".bun"), ("opencode", ".opencode")):
        path = shutil.which(name)
        if path:
            return path
        candidate = Path.home() / directory / "bin" / (name + (".exe" if os.name == "nt" else ""))
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class BunUnavailable(httpx.RequestError):
    """A local runtime requirement, not an upstream quota failure."""


class _BunStream(httpx.SyncByteStream):
    def __init__(self, request: httpx.Request, proxy: str | None):
        runtime = runtime_path()
        if runtime is None:
            raise BunUnavailable("OpenCode anonymous inference requires Bun or OpenCode on PATH", request=request)
        if proxy and httpx.URL(proxy).scheme not in ("http", "https"):
            raise BunUnavailable("OpenCode Bun transport requires an HTTP or HTTPS proxy", request=request)
        self.request = request
        self.timeout = request.extensions.get("timeout", {}).get("read", 600.0)
        self.closed = threading.Event()
        self.lines: queue.Queue[bytes] = queue.Queue(maxsize=2)
        payload = {
            "url": str(request.url), "method": request.method,
            "headers": dict(request.headers), "proxy": proxy,
            "body": base64.b64encode(request.read()).decode() if request.method not in ("GET", "HEAD") else None,
        }
        env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
        env["BUN_BE_BUN"] = "1"
        try:
            self.process = subprocess.Popen(
                [runtime, "--eval", _FETCH], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env,
            )
        except OSError:
            raise BunUnavailable("Unable to start the OpenCode Bun transport", request=request) from None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(payload).encode())
            self.process.stdin.close()
        except (OSError, ValueError):
            self.close()
            raise httpx.ConnectError("OpenCode Bun transport could not accept the request", request=request) from None

    def _read(self) -> None:
        assert self.process.stdout is not None
        while not self.closed.is_set():
            line = self.process.stdout.readline()
            while not self.closed.is_set():
                try:
                    self.lines.put(line, timeout=0.1)
                    break
                except queue.Full:
                    continue
            if not line:
                return

    def frame(self) -> dict[str, Any]:
        if self.closed.is_set():
            raise httpx.ReadError("OpenCode Bun stream is closed", request=self.request)
        try:
            line = self.lines.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            raise httpx.ReadTimeout("OpenCode Bun transport timed out", request=self.request) from None
        try:
            frame = json.loads(line)
            if not isinstance(frame, dict) or frame.get("error"):
                raise ValueError
            return frame
        except (ValueError, TypeError):
            self.close()
            raise httpx.RemoteProtocolError("OpenCode Bun transport ended before completing the response", request=self.request) from None

    def __iter__(self):
        try:
            while True:
                frame = self.frame()
                if frame.get("end"):
                    return
                if not isinstance(frame.get("data"), str):
                    raise httpx.RemoteProtocolError("Invalid OpenCode Bun response frame", request=self.request)
                yield base64.b64decode(frame["data"])
        finally:
            self.close()

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.lines.put_nowait(b"")
        except queue.Full:
            pass
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.reader.join(timeout=1)
        for pipe in (self.process.stdin, self.process.stdout):
            if pipe is not None:
                pipe.close()


class BunTransport(httpx.BaseTransport):
    def __init__(self, *, proxy: str | None = None):
        self.proxy = proxy

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        stream = _BunStream(request, self.proxy)
        try:
            frame = stream.frame()
            return httpx.Response(int(frame["status"]), headers=frame["headers"], stream=stream)
        except BaseException:
            stream.close()
            raise
