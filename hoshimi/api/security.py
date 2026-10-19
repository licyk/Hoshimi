"""Access control for a gateway that holds upstream account credentials.

- The ``Host`` header must name the bound host or a loopback name while bound to loopback,
  which blocks DNS rebinding.
- A state-changing request from a browser must come from the page's own origin, or from an
  origin listed in ``security.allow_origins``.
- The client address must fall inside ``security.allow_cidrs`` (loopback is always allowed).
- ``server.access_token`` protects management APIs and API documentation.
- ``server.model_api_key`` independently protects external ``/v1/`` model APIs.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from hoshimi.api.errors import anthropic_error, is_messages_path
from hoshimi.api.paths import route_path
from hoshimi.core.security import SecurityPolicy, is_allowed_origin, is_allowed_remote, is_authorized

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
# Paths that work without a key: the UI shell and the liveness probes.
PUBLIC_PATHS = ("/health", "/api/v1/app/health")
PROTECTED_PREFIXES = ("/v1/", "/api/", "/openapi.json", "/docs")


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _hostname(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header[1:].split("]")[0]
    return host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header


class SecurityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        policy: Callable[[], SecurityPolicy],
        bound_host: Callable[[], str],
        extra_hosts: set[str] | None = None,
        prefix: str = "",
    ) -> None:
        self.app = app
        self.policy = policy
        self.bound_host = bound_host
        self.extra_hosts = extra_hosts or set()
        # Everything this package serves may sit under a prefix chosen by a host application.
        self.prefix = prefix
        self.public_paths = tuple(f"{prefix}{p}" for p in PUBLIC_PATHS)
        self.protected_prefixes = tuple(f"{prefix}{p}" for p in PROTECTED_PREFIXES)
        self.inference_prefixes = (f"{prefix}/v1/",)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        problem = self._check(scope, headers)
        if problem is not None:
            status, code, message = problem
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            payload = anthropic_error(message, status) if is_messages_path(route_path(scope)) else {"code": code, "message": message, "detail": {}}
            body = json.dumps(payload).encode()
            await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

    def _check(self, scope: Scope, headers: dict[str, str]) -> tuple[int, str, str] | None:
        policy = self.policy()
        client = scope.get("client") or ("", 0)
        # A test transport reports a name, not an address; only a real IP can be matched.
        if client[0] and _is_ip(client[0]) and not is_allowed_remote(client[0], policy):
            return 403, "remote_not_allowed", "Remote address not allowed"

        bound = self.bound_host()
        if bound in LOOPBACK_NAMES or bound.startswith("127."):
            host = _hostname(headers.get("host", ""))
            if host not in LOOPBACK_NAMES | {bound} | self.extra_hosts:
                return 400, "bad_host", "Host not allowed"

        method = scope.get("method", "GET")
        origin = headers.get("origin")
        preflight = method == "OPTIONS" and bool(origin) and bool(headers.get("access-control-request-method"))
        if method not in SAFE_METHODS or scope["type"] == "websocket" or preflight:
            if origin:
                own = f"{'https' if scope.get('scheme') in ('https', 'wss') else 'http'}://{headers.get('host', '')}"
                if origin != own and not is_allowed_origin(origin, policy):
                    return 403, "bad_origin", "Cross-origin request refused"
            elif headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
                return 403, "bad_origin", "Cross-site request refused"

        path = route_path(scope)
        # Only a real CORS preflight skips the key; its subsequent request is gated.
        if preflight:
            return None
        needs_key = path.startswith(self.protected_prefixes) and path not in self.public_paths
        inference = path.startswith(self.inference_prefixes)
        authorization = headers.get("authorization")
        # SDK-style x-api-key is accepted on inference routes only; it cannot
        # grant access to management routes or override an explicit bearer.
        if inference and not authorization and headers.get("x-api-key"):
            authorization = f"Bearer {headers['x-api-key']}"
        if needs_key and not is_authorized(authorization, policy, inference=inference):
            return 401, "model_api_key_required" if inference else "auth_required", "A model API key is required" if inference else "A tool access key is required"
        return None
