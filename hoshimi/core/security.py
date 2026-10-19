"""Independent tool/model credentials, CIDR, Host and CORS checks."""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from hoshimi.core.config import Settings

_LOOPBACK_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


@dataclass
class SecurityPolicy:
    access_token: str | None
    model_api_key: str | None = None
    networks: list[ipaddress._BaseNetwork] = field(default_factory=list)
    hosts: set[str] = field(default_factory=set)
    origins: set[str] = field(default_factory=set)
    allow_all_origins: bool = False
    max_body_bytes: int = 0


def parse_networks(values: Iterable[str]) -> list[ipaddress._BaseNetwork]:
    out: list[ipaddress._BaseNetwork] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        try:
            if "/" not in text:
                out.append(ipaddress.ip_network(f"{text}/32" if ":" not in text else f"{text}/128"))
            else:
                out.append(ipaddress.ip_network(text, strict=False))
        except ValueError as exc:
            raise ValueError(f"Invalid CIDR or IP: {value!r} ({exc})") from exc
    return out


def parse_origins(values: Iterable[str]) -> tuple[set[str], bool]:
    origins: set[str] = set()
    allow_all = False
    for value in values:
        text = value.strip()
        if not text:
            continue
        if text == "*":
            allow_all = True
            continue
        parts = urlsplit(text)
        if parts.scheme and parts.netloc:
            origins.add(f"{parts.scheme}://{parts.netloc}".rstrip("/"))
        else:
            origins.add(text.rstrip("/"))
    return origins, allow_all


def build_policy(settings: Settings) -> SecurityPolicy:
    networks = parse_networks(settings.allow_cidrs)
    origins, allow_all = parse_origins(settings.allow_origins)
    hosts = {h.strip().lower() for h in settings.allow_hosts if h.strip()}
    return SecurityPolicy(
        access_token=settings.access_token,
        model_api_key=settings.model_api_key,
        networks=networks,
        hosts=hosts,
        origins=origins,
        allow_all_origins=allow_all,
        max_body_bytes=settings.max_body_bytes,
    )


def is_loopback_bind(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback


def validate_security_settings(settings: Settings) -> str | None:
    if settings.allow_origins and not settings.access_token:
        return "security.allow_origins requires server.access_token (browser CORS must not be open to anonymous callers)."
    if not is_loopback_bind(settings.host) and not (settings.allow_cidrs and settings.access_token) and settings.host not in ("localhost",):
        return (
                "Refusing to bind a non-loopback host without both security.allow_cidrs and server.access_token. "
            "The gateway can forward requests using upstream account credentials."
        )
    return None


def is_allowed_remote(remote_ip: str, policy: SecurityPolicy) -> bool:
    try:
        addr = ipaddress.ip_address(remote_ip)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if not policy.networks:
        return is_loopback_bind_only(policy)
    return any(addr in net for net in policy.networks)


def is_loopback_bind_only(policy: SecurityPolicy) -> bool:
    # No allowlist configured: only loopback clients may connect (checked by caller
    # binding; remote non-loopback is rejected unless a network allowlist exists).
    return False


def is_allowed_host(host_header: str | None, policy: SecurityPolicy) -> bool:
    if not host_header:
        return True
    value = host_header.strip().lower()
    if not policy.hosts and not policy.networks:
        return True
    name = value.rsplit(":", 1)[0].strip("[]")
    if name in policy.hosts:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def is_allowed_origin(origin: str | None, policy: SecurityPolicy) -> bool:
    if not origin:
        return True
    if policy.allow_all_origins:
        return True
    if not policy.origins:
        # An empty allowlist grants no cross-origin write authority.
        return False
    return origin.rstrip("/") in policy.origins


def cors_response_origin(origin: str | None, policy: SecurityPolicy) -> str | None:
    if not origin:
        return None
    if policy.allow_all_origins:
        return "*"
    if origin.rstrip("/") in policy.origins:
        return origin
    return None


def _bearer_matches(authorization: str | None, key: str) -> bool:
    """Constant-time comparison of a presented bearer token against one key."""
    if not authorization:
        return False
    parts = authorization.split(None, 1)
    if len(parts) != 2:
        return False
    scheme, token = parts
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(token.strip().encode("utf-8"), key.encode("utf-8"))


def is_authorized(authorization: str | None, policy: SecurityPolicy, *, inference: bool = False) -> bool:
    """Check only the credential belonging to the requested surface."""
    key = policy.model_api_key if inference else policy.access_token
    return not key or _bearer_matches(authorization, key)
