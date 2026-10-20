"""Network adapters for optional cloud SDKs, without modifying process globals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from hoshimi.core.net.client import NetworkClient, operation
from hoshimi.core.net.policy import proxy_route


@dataclass
class GoogleResponse:
    status: int
    headers: dict[str, str]
    data: bytes


def google_request(client: httpx.Client):
    """Supply google-auth's callable transport contract using the provider client."""
    def request(url: str, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None,
                timeout: float = 30.0, **kwargs: Any) -> GoogleResponse:
        with operation("token_refresh"):
            try:
                response = client.request(method, url, content=body, headers=headers, timeout=timeout)
            except httpx.HTTPError:
                from google.auth.exceptions import TransportError

                raise TransportError("Credential service connection failed") from None
        return GoogleResponse(response.status_code, dict(response.headers), response.content)
    return request


def bedrock_config(client: httpx.Client, service: str, region: str):
    """Pass the selected route into botocore's documented client configuration."""
    from botocore.config import Config

    options: dict[str, Any] = {
        "connect_timeout": 15.0, "read_timeout": client.timeout.read or 600.0,
        "retries": {"total_max_attempts": 1},
    }
    if isinstance(client, NetworkClient):
        proxy, _route = proxy_route(httpx.URL(f"https://{service}.{region}.amazonaws.com"), client.policy)
        options["proxies"] = {"http": proxy, "https": proxy} if proxy else {}
    return Config(**options)
