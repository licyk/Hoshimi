"""Effective settings (config file) as clients see them, and how to change them."""

from __future__ import annotations

import copy
from typing import Annotated, Any

from fastapi import APIRouter, Body, Request
from pydantic import Field

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import ERROR_RESPONSES
from hoshimi.core.config import Settings, save_settings, settings_from_dict, settings_to_dict, validate_settings
from hoshimi.core.context import Services
from hoshimi.core.errors import ValidationError
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["settings"])


class ServerView(Record):
    """``server.*`` as configured. ``port`` is the configured first port; the bound one is ``SettingsView.listening``."""

    host: str
    port: int
    strict_port: bool = False
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
    check_updates: bool = True
    stats_keep_days: int = 90


class ListeningView(Record):
    """The socket this process actually serves on.

    ``port`` is the bound port and ``requested_port`` the one the search
    started from (``server.port``, or ``serve --port``). ``fallback`` says the
    requested port was taken and the search moved on — a fact about this run,
    not a pending change, so it never sets ``restart_required``.
    """

    host: str
    port: int
    requested_port: int
    fallback: bool = False


class NetworkView(Record):
    mode: str = "auto"
    proxy_configured: bool = False
    system_proxy: bool = False


class ProviderView(Record):
    id: str
    enabled: bool
    accounts: int
    options: list[str]
    network: NetworkView = Field(default_factory=lambda: NetworkView(mode="inherit"))
    client_profile: str | None = None


def network_view(raw: dict[str, Any], default: str = "auto") -> NetworkView:
    return NetworkView(mode=raw.get("mode", default), proxy_configured=bool(raw.get("proxy")), system_proxy=bool(raw.get("system_proxy")))


class PlaygroundSettingsView(Record):
    """``playground.*``.

    ``system_prompt`` goes first in every Playground conversation (empty sends
    none); ``title_model`` names conversations (empty: the conversation's own
    model); ``vision_model`` describes images for a model that cannot read
    them (``auto`` picks one, ``off`` never asks).
    """

    system_prompt: str = ""
    title_model: str = ""
    vision_model: str = "auto"


class SettingsView(Record):
    """Settings as returned to clients: only key presence is disclosed.

    ``restart_required`` says a saved ``server.host``/``server.port`` has not
    taken effect: the socket is bound once, at start, and a settings save
    cannot move it. Compared against the values this process was *started
    with*, not the socket itself, so a port that fell back to the next free
    one (or a ``--port`` override) is not reported as a pending change no
    restart would resolve. False when the process did not bind a socket of
    its own (an embedder, a test, the schema generator).
    """

    data_dir: str
    config_path: str
    server: ServerView
    providers: list[ProviderView]
    network: NetworkView = Field(default_factory=NetworkView)
    playground: PlaygroundSettingsView = Field(default_factory=PlaygroundSettingsView)
    restart_required: bool = False
    listening: ListeningView | None = None


def listening_view(state: Any) -> ListeningView | None:
    """Where this process listens, from the app state; None when it bound no socket of its own."""
    port = getattr(state, "bound_port", None)
    if port is None:
        return None
    requested = getattr(state, "requested_port", None)
    requested = port if requested is None else requested
    return ListeningView(host=getattr(state, "bound_host", None) or "", port=port, requested_port=requested, fallback=requested != 0 and port != requested)


def _view(services: Services, request: Request | None = None) -> SettingsView:
    settings = services.settings
    started = getattr(request.app.state, "started_listen", None) if request is not None else None
    return SettingsView(
        data_dir=str(services.data_dir),
        restart_required=started is not None and tuple(started) != (settings.host, settings.port),
        listening=listening_view(request.app.state) if request is not None else None,
        config_path=str(services.config_path),
        network=network_view(settings.network.as_dict()),
        playground=PlaygroundSettingsView(
            system_prompt=settings.playground_system_prompt, title_model=settings.playground_title_model, vision_model=settings.playground_vision_model
        ),
        server=ServerView(
            host=settings.host,
            port=settings.port,
            strict_port=settings.strict_port,
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
            check_updates=settings.check_updates,
            stats_keep_days=settings.stats_keep_days,
        ),
        providers=[
            ProviderView(id=pid, enabled=cfg.enabled, accounts=len(cfg.accounts), options=sorted(cfg.options),
                         network=network_view(cfg.options.get("network") or {}, "inherit"),
                         client_profile=cfg.options.get("client_profile", "sdk") if pid == "cline" else None)
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
def get_settings(services: ServicesDep, request: Request) -> SettingsView:
    return _view(services, request)


@router.patch("/settings", operation_id="update_settings", responses=ERROR_RESPONSES)
def update_settings(
    services: ServicesDep,
    request: Request,
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
    save_settings(updated)
    services.apply_settings(updated)
    return _view(services, request)


def _validate(settings: Settings) -> str | None:
    from hoshimi.core.security import validate_security_settings

    return validate_settings(settings) or validate_security_settings(settings)
