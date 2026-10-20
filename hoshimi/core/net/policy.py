"""Validated, deterministic upstream proxy selection."""

from __future__ import annotations

import ipaddress
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any
from urllib.request import getproxies

import httpx


@dataclass(frozen=True)
class NetworkPolicy:
    mode: str = "auto"
    proxy: str | None = None
    system_proxy: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_policy(raw: Any, *, inherited: NetworkPolicy | None = None) -> NetworkPolicy:
    """Resolve explicit provider overrides; invalid settings never become direct."""
    if raw is None:
        return inherited or NetworkPolicy()
    if not isinstance(raw, dict):
        raise TypeError("network must be an object")
    if set(raw) - {"mode", "proxy", "system_proxy"}:
        raise ValueError("unknown network option")
    mode = raw.get("mode", "inherit" if inherited else "auto")
    if mode == "inherit" and inherited is not None:
        return inherited
    if mode not in ("auto", "direct", "proxy"):
        raise ValueError("network.mode must be auto, direct or proxy (inherit for providers)")
    proxy = raw.get("proxy")
    if proxy is not None and not isinstance(proxy, str):
        raise ValueError("network.proxy must be a URL or null")
    proxy = proxy.strip() or None if isinstance(proxy, str) else None
    if proxy:
        try:
            url = httpx.URL(proxy)
            if url.scheme not in ("http", "https", "socks5", "socks5h") or not url.host or url.query or url.fragment or url.path not in ("", "/"):
                raise ValueError
            httpx.Proxy(proxy)
        except (httpx.InvalidURL, ValueError):
            raise ValueError("network.proxy must be a valid HTTP(S) or SOCKS5 proxy URL") from None
    if mode == "proxy" and not proxy:
        raise ValueError("network.proxy is required in proxy mode")
    system = raw.get("system_proxy", False)
    if not isinstance(system, bool):
        raise TypeError("network.system_proxy must be a boolean")
    return NetworkPolicy(mode, proxy, system)


def loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def bypassed(url: httpx.URL, patterns: str) -> bool:
    """Match NO_PROXY hosts, suffixes, optional ports and CIDR networks."""
    host = url.host.lower()
    for raw in patterns.replace(";", ",").split(","):
        pattern = raw.strip().lower()
        if not pattern:
            continue
        if pattern == "*" or (pattern == "<local>" and "." not in host and ":" not in host):
            return True
        try:
            if "/" in pattern and "://" not in pattern and ipaddress.ip_address(host) in ipaddress.ip_network(pattern, strict=False):
                return True
        except ValueError:
            pass
        if "://" in pattern:
            pattern = pattern.split("://", 1)[1]
        if pattern.count(":") == 1 or pattern.startswith("["):
            end = pattern.rsplit(":", 1)
            if len(end) == 2 and end[1].isdigit():
                if int(end[1]) != (url.port or (443 if url.scheme == "https" else 80)):
                    continue
                pattern = end[0]
        pattern = pattern.strip("[]").lstrip("*.")
        if host == pattern or host.endswith("." + pattern):
            return True
    return False


def proxy_route(url: httpx.URL, policy: NetworkPolicy) -> tuple[str | None, str]:
    """Return the proxy and its source; loopback always remains local."""
    if loopback(url.host) or policy.mode == "direct":
        return None, "direct"
    if policy.mode == "proxy":
        return policy.proxy, "configured_proxy"
    env = {key: os.environ.get(key.lower(), os.environ.get(key, "")) for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")}
    address = env["HTTPS_PROXY" if url.scheme == "https" else "HTTP_PROXY"] or env["ALL_PROXY"]
    if bypassed(url, env["NO_PROXY"]):
        return None, "direct"
    if address:
        # Environment mistakes are reported, never silently ignored.
        return parse_policy({"mode": "proxy", "proxy": address}).proxy, "environment_proxy"
    if policy.system_proxy and sys.platform == "win32":
        proxies = getproxies()
        if not bypassed(url, proxies.get("no", "")):
            address = proxies.get(url.scheme) or proxies.get("all")
            if address:
                return parse_policy({"mode": "proxy", "proxy": address}).proxy, "system_proxy"
    return None, "direct"
