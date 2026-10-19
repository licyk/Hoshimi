"""EAC relay references: a bundled default or an explicitly configured relay."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from hoshimi.core.eac_seal import open_bundled_relay
from hoshimi.core.errors import ValidationError

DEFAULT_EAC_ACCOUNT_ID = "eac-default"


def default_eac_account() -> dict[str, Any]:
    """A reference, not the shared relay signing material."""
    return {"id": DEFAULT_EAC_ACCOUNT_ID, "relay": "bundled"}


def relay_credential(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve transient wire credentials without changing the persisted account."""
    if data.get("relay") == "bundled":
        if any(data.get(key) for key in ("base_url", "signing_secret", "access_token")):
            raise ValidationError("eac: a bundled relay reference cannot also specify a custom relay")
        return {**data, **open_bundled_relay()}
    return dict(data)


def validate_relay(data: dict[str, Any], *, login: bool = False) -> None:
    resolved = relay_credential(data)
    base = resolved.get("base_url")
    if not isinstance(base, str) or not base.strip():
        raise ValidationError("eac: configure a worker relay with base_url and signing_secret, or use the bundled relay")
    try:
        url = urlsplit(base.strip())
        valid = url.scheme in ("http", "https") and bool(url.hostname) and url.port != 0 and url.username is None and url.password is None and not url.query and not url.fragment
    except ValueError:
        valid = False
    if not valid or not url.path.rstrip("/").endswith("/v1"):
        raise ValidationError("eac: base_url must be an HTTP(S) relay URL ending in /v1, without credentials, query or fragment")
    worker = isinstance(resolved.get("signing_secret"), str) and bool(resolved["signing_secret"].strip())
    direct = isinstance(resolved.get("access_token"), str) and bool(resolved["access_token"].strip())
    if login and not worker:
        raise ValidationError("eac: GitHub authorization requires a worker relay; direct relays use their existing Bearer token")
    if not worker and not direct:
        raise ValidationError("eac: a custom relay needs signing_secret (worker) or access_token (direct)")
