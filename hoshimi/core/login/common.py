"""Shared primitives for the login flows: PKCE, deadlines, the local callback server."""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

LOGIN_TIMEOUT_SECONDS = 600
"""Ten minutes: the stations' own flows allow 5–10, and Google has 2FA."""

POLL_MAX_NETWORK_FAILURES = 5
"""Consecutive transport failures before a polling loop gives up."""


class LoginError(Exception):
    """The flow could not produce a credential; the message is shown to a human."""


class LoginReporter:
    """Callable URL reporter with optional device authorization details."""

    def __init__(self, update: Callable[[dict[str, Any]], None], *, inputs: queue.Queue[str] | None = None, cancelled: threading.Event | None = None):
        self.update = update
        self.inputs = inputs if inputs is not None else queue.Queue()
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self._commit_actions: list[Callable[[], None]] = []

    def on_commit(self, action: Callable[[], None]) -> None:
        """Defer a flow's local writes to the manager's cancellation boundary."""
        self._commit_actions.append(action)

    def commit(self) -> None:
        self.check_cancelled()
        for action in self._commit_actions:
            action()

    def __call__(self, url: str) -> None:
        self.check_cancelled()
        self.update({"login_url": url})

    def device_code(self, url: str, code: str, expires_in: float) -> None:
        self.check_cancelled()
        self.update({"login_url": url, "user_code": code, "instructions": "Enter this code on the authorization page.",
                     "expires_at": time.time() + expires_in})

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise LoginError("Login cancelled")

    def wait(self, seconds: float) -> None:
        if self.cancelled.wait(seconds):
            raise LoginError("Login cancelled")

    def authorization(self, server: CallbackServer | None, timeout: float = LOGIN_TIMEOUT_SECONDS) -> dict[str, str]:
        """Race a loopback callback with an explicit manual submission."""
        self.update({"waiting_for": "manual_code", "instructions": "Complete browser login, or paste the final redirect URL or authorization code.",
                     "expires_at": time.time() + timeout})
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                self.check_cancelled()
                try:
                    value = self.inputs.get_nowait().strip()
                except queue.Empty:
                    value = ""
                if value:
                    split = urlsplit(value)
                    if split.scheme:
                        return {key: items[0] for key, items in parse_qs(split.query).items()}
                    if "#" in value:
                        code, state = value.split("#", 1)
                        return {"code": code, "state": state}
                    return {"code": value}
                if server is None:
                    self.wait(0.1)
                else:
                    try:
                        return server.wait(min(0.1, max(0.001, deadline - time.monotonic())))
                    except LoginError:
                        continue
            raise LoginError("Login timed out waiting for authorization")
        finally:
            self.update({"waiting_for": ""})


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def sha256_b64url(text: str) -> str:
    return b64url(hashlib.sha256(text.encode("ascii")).digest())


def pkce_pair(length: int = 64) -> tuple[str, str]:
    """A PKCE (verifier, S256 challenge) pair over the RFC 7636 alphabet."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(length))
    return verifier, sha256_b64url(verifier)


def jwt_claims(token: Any) -> dict[str, Any]:
    """A JWT's payload claims, unverified (identity display only, never trust)."""
    if not isinstance(token, str) or token.count(".") < 1:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def ms_timestamp(seconds: Any) -> str | None:
    """An upstream lifetime in seconds as an absolute ms-timestamp string."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    return str(int((time.time() + seconds) * 1000))


def append_query(url: str, **params: str) -> str:
    """Append query parameters to a URL that may already carry a fragment."""
    split = urlsplit(url)
    query = parse_qs(split.query)
    query.update({key: [value] for key, value in params.items()})
    encoded = "&".join(f"{quote(k)}={quote(v[0])}" for k, v in query.items())
    fragment = f"#{split.fragment}" if split.fragment else ""
    return f"{split.scheme}://{split.netloc}{split.path}?{encoded}{fragment}"


def remaining(deadline: float) -> float:
    """Seconds left before ``deadline``; raises when the wait is over."""
    left = deadline - time.time()
    if left <= 0:
        raise LoginError("login timed out, please start again")
    return left


def poll_pause(seconds: float) -> None:
    """Sleep between polls; the flows own their own deadlines."""
    time.sleep(max(0.0, seconds))


class CallbackServer:
    """A one-shot loopback HTTP server that captures a login redirect.

    The browser lands on ``redirect_uri`` with the station's answer in the
    query; the first well-formed hit resolves :meth:`wait` and the server is
    closed by the flow. ``dual_stack`` also binds the same port on ``::1``,
    which Google's ``localhost`` redirect needs on hosts that prefer IPv6.
    """

    def __init__(self, path: str, *, host: str = "127.0.0.1", port: int = 0, dual_stack: bool = False):
        if not path.startswith("/"):
            raise ValueError("callback path must start with /")
        self.path = path
        self._answers: queue.Queue[dict[str, str]] = queue.Queue()
        handler = _make_handler(path, self._answers)
        self._servers = [ThreadingHTTPServer((host, port), handler)]
        if dual_stack:
            try:
                self._servers.append(ThreadingHTTPServer(("::1", self.port), handler))
            except OSError:
                pass  # no IPv6 on this host; the v4 listener still answers
        self._threads = [threading.Thread(target=s.serve_forever, daemon=True, name="hoshimi-login-cb") for s in self._servers]
        for thread in self._threads:
            thread.start()

    @property
    def port(self) -> int:
        return int(self._servers[0].server_address[1])

    def redirect_uri(self, *, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{self.path}"

    def wait(self, timeout: float) -> dict[str, str]:
        """Block for the browser's redirect; the query parameters of the hit."""
        try:
            return self._answers.get(timeout=timeout)
        except queue.Empty as exc:
            raise LoginError("login timed out waiting for the browser") from exc

    def close(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()


def _make_handler(path: str, answers: queue.Queue[dict[str, str]]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # the browser's hit is not a server log line
            return

        def do_GET(self) -> None:
            split = urlsplit(self.path)
            if split.path != path:
                self.send_error(404)
                return
            params = {key: values[0] for key, values in parse_qs(split.query).items()}
            answers.put(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<!doctype html><meta charset=utf-8><h2>Login complete</h2><p>You can close this tab and return to Hoshimi.</p>")

    return Handler
