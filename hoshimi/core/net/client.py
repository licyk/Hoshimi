"""HTTPX clients with shared policy, isolated sessions and safe diagnostics."""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

import httpx

from hoshimi.core.net.policy import NetworkPolicy, parse_policy, proxy_route
from hoshimi.core.net.profiles import request_operation
from hoshimi.core.net.responses import failure_kind, reject_interception

_operation: ContextVar[str] = ContextVar("upstream_operation", default="")


@contextmanager
def operation(name: str) -> Generator[None]:
    """Carry an operation label through synchronous adapter calls."""
    token = _operation.set(name)
    try:
        yield
    finally:
        _operation.reset(token)


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request) -> bool:
        return False


class _Stream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, release) -> None:
        self.stream, self.release = stream, release
        self.closed = False

    def __iter__(self):
        yield from self.stream

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.stream.close()
            finally:
                self.release()


class _Pool:
    def __init__(self, proxy: str | None):
        self.transport = httpx.HTTPTransport(proxy=proxy, retries=0, trust_env=True)
        self.active = 0
        self.retired = False


class RoutingTransport(httpx.BaseTransport):
    """Snapshot routing per request; retire old pools after their streams close."""

    def __init__(self, client: NetworkClient):
        self.client = client
        self.lock = threading.RLock()
        self.pools: dict[str | None, _Pool] = {}
        self.generation: NetworkPolicy | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        policy = self.client.policy
        proxy, route = proxy_route(request.url, policy)
        request.extensions["hoshimi_route"] = route
        with self.lock:
            if policy != self.generation:
                for pool in self.pools.values():
                    pool.retired = True
                    if not pool.active:
                        pool.transport.close()
                self.pools.clear()
                self.generation = policy
            pool = self.pools.get(proxy)
            if pool is None:
                pool = self.pools[proxy] = _Pool(proxy)
            pool.active += 1

        def release() -> None:
            with self.lock:
                pool.active -= 1
                if pool.retired and not pool.active:
                    pool.transport.close()

        try:
            response = pool.transport.handle_request(request)
            assert isinstance(response.stream, httpx.SyncByteStream)
            response.stream = _Stream(response.stream, release)
            return response
        except BaseException:
            release()
            raise

    def close(self) -> None:
        with self.lock:
            for pool in self.pools.values():
                pool.retired = True
                if not pool.active:
                    pool.transport.close()
            self.pools.clear()


class NetworkClient(httpx.Client):
    """A registry owns the root; providers borrow clients and login owns a session."""

    def __init__(
        self, *, provider: str = "", root: NetworkClient | None = None,
        network: NetworkPolicy | None = None, options: dict[str, Any] | None = None,
        timeout: float = 600.0, login: bool = False, transport: httpx.BaseTransport | None = None,
    ):
        self.provider = provider
        self.root = root or self
        self.network = network or NetworkPolicy()
        self.provider_options = options or {}
        self.children: dict[str, NetworkClient] = {}
        self.observations: dict[str, dict[str, Any]] = {}
        self._network_lock = threading.RLock()
        self.login_session = login
        super().__init__(
            transport=transport or RoutingTransport(self),
            timeout=httpx.Timeout(timeout, connect=15.0),
            follow_redirects=False,
            trust_env=False,
            cookies=None if login else CookieJar(policy=_NoCookies()),
            event_hooks={"response": [self._observe]},
        )

    @property
    def policy(self) -> NetworkPolicy:
        return parse_policy(self.provider_options.get("network"), inherited=self.root.network)

    def for_provider(self, provider: str, options: dict[str, Any]) -> NetworkClient:
        with self._network_lock:
            client = self.children.get(provider)
            if client is None:
                client = self.children[provider] = NetworkClient(provider=provider, root=self, options=options, timeout=self.timeout.read or 600.0)
            client.provider_options = options
            return client

    def _observe(self, response: httpx.Response) -> None:
        request = response.request
        stage = request.extensions.get("hoshimi_operation", "request")
        kind = failure_kind(response)
        self._record(stage, kind, request, status=response.status_code)
        reject_interception(response, provider=self.provider or "upstream")

    def _record(self, stage: str, kind: str, request: httpx.Request, *, status: int | None = None) -> None:
        with self._network_lock:
            self.observations[stage] = {
                "operation": stage, "kind": kind, "status": status, "at": time.time(),
                "host": request.url.host, "route": request.extensions.get("hoshimi_route", "custom_transport"),
            }

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        # Exceptions never include a credential-bearing URL in public diagnostics.
        stage = request_operation(request, _operation.get(), self.login_session)
        request.extensions["hoshimi_operation"] = stage
        if stage in ("catalog", "token_refresh", "token_exchange", "auth_poll"):
            request.headers["Accept"] = "application/json"
        try:
            return super().send(request, **kwargs)
        except httpx.HTTPError as exc:
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "proxy_error" if isinstance(exc, httpx.ProxyError) else "connection_error"
            self._record(stage, kind, request)
            raise httpx.RequestError(f"{self.provider}: {kind}; check network configuration", request=request) from None

    def diagnostics(self) -> list[dict[str, Any]]:
        with self._network_lock:
            return [dict(row) for row in self.observations.values()]

    def close(self) -> None:
        for child in self.children.values():
            child.close()
        self.children.clear()
        super().close()
