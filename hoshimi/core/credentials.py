"""Credentials from Hoshimi's provider configuration and EAC login store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hoshimi.core.config import ProviderConfig, Settings


def load_eac_user_token(path: Path | None = None) -> str:
    """The per-install GitHub authorization token, or an empty string.

    The token lives independently of any one provider account — every EAC
    account this install holds shares the same GitHub identity, and the
    install can also hold it before the user has configured any account —
    so the credential layer owns the file lookup.
    """
    from hoshimi.core.eac_user import eac_user_path, read_eac_user

    target = path if path is not None else eac_user_path()
    record = read_eac_user(target)
    if record is None:
        return ""
    return str(record.get("token") or "")


def collect_provider_accounts(
    settings: Settings,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Return configured accounts and options for enabled providers."""
    accounts: dict[str, list[dict[str, Any]]] = {}
    options: dict[str, dict[str, Any]] = {}

    for provider_id, cfg in settings.providers.items():
        if not cfg.enabled:
            continue
        if cfg.accounts:
            accounts[provider_id] = [dict(a) for a in cfg.accounts]
        if cfg.options:
            options[provider_id] = dict(cfg.options)

    return accounts, options


def provider_config_summary(settings: Settings) -> dict[str, ProviderConfig]:
    return dict(settings.providers)
