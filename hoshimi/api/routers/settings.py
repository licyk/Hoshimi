"""Effective settings (config file) as clients see them, and how to change them."""

from __future__ import annotations

import copy
from typing import Annotated, Any

from fastapi import APIRouter, Body

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import ERROR_RESPONSES
from hoshimi.core.config import Settings, save_settings, settings_from_dict, settings_to_dict, validate_settings
from hoshimi.core.context import Services
from hoshimi.core.errors import ValidationError
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["settings"])


class ServerView(Record):
    host: str
    port: int
    access_token_configured: bool
    model_api_key_configured: bool
    allow_cidrs: list[str]
    allow_hosts: list[str]
    allow_origins: list[str]
    max_body_bytes: int
    max_account_retries: int
    default_model: str | None
    auto_refresh: bool
    auto_checkin: bool


class ProviderView(Record):
    id: str
    enabled: bool
    accounts: int
    options: list[str]


class SettingsView(Record):
    """Settings as returned to clients: only key presence is disclosed."""

    data_dir: str
    config_path: str | None
    server: ServerView
    providers: list[ProviderView]


def _view(services: Services) -> SettingsView:
    settings = services.settings
    return SettingsView(
        data_dir=str(services.data_dir),
        config_path=str(services.config_path) if services.config_path else None,
        server=ServerView(
            host=settings.host,
            port=settings.port,
            access_token_configured=bool(settings.access_token),
            model_api_key_configured=bool(settings.model_api_key),
            allow_cidrs=list(settings.allow_cidrs),
            allow_hosts=list(settings.allow_hosts),
            allow_origins=list(settings.allow_origins),
            max_body_bytes=settings.max_body_bytes,
            max_account_retries=settings.max_account_retries,
            default_model=settings.default_model,
            auto_refresh=settings.auto_refresh,
            auto_checkin=settings.auto_checkin,
        ),
        providers=[
            ProviderView(id=pid, enabled=cfg.enabled, accounts=len(cfg.accounts), options=sorted(cfg.options))
            for pid, cfg in sorted(settings.providers.items())
        ],
    )


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


@router.get("/settings", operation_id="get_settings")
def get_settings(services: ServicesDep) -> SettingsView:
    return _view(services)


@router.patch("/settings", operation_id="update_settings", responses=ERROR_RESPONSES)
def update_settings(
    services: ServicesDep,
    patch: Annotated[dict[str, Any], Body(description="Partial settings, deep-merged into the config file. A server.access_token set to null is cleared.")],
) -> SettingsView:
    """Merge ``patch`` into the config file and into the running gateway alike.

    The second half is the point of this endpoint. Saving to the file alone
    would mean the account an operator just added does not exist until the
    process restarts — and a web UI that says "added" while the pool still
    refuses the id is lying to them.
    """
    data = settings_to_dict(services.settings)
    merged = _deep_merge(data, patch)
    try:
        updated = settings_from_dict(merged, Settings())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid settings: {exc}") from None
    problem = _validate(updated)
    if problem:
        raise ValidationError(problem)
    save_settings(updated, services.config_path)
    services.apply_settings(updated)
    return _view(services)


def _validate(settings: Settings) -> str | None:
    from hoshimi.core.security import validate_security_settings

    return validate_settings(settings) or validate_security_settings(settings)
