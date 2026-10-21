"""``create_app(services)``: the FastAPI app with routers, the security layer and the web UI."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from hoshimi.api.errors import install_error_handlers
from hoshimi.api.routers import app_info, catalog, channels, chat, classification, credits, health, login, messages, models, playground, probe, providers, refresh, settings, stats
from hoshimi.api.security import SecurityMiddleware
from hoshimi.api.static import SPAStaticFiles, web_dist_dir
from hoshimi.core.context import Services
from hoshimi.core.errors import ValidationError
from hoshimi.core.security import build_policy, validate_security_settings
from hoshimi.version import VERSION

logger = logging.getLogger(__name__)

INFERENCE_ROUTES = (chat, models, classification, messages)


def normalize_prefix(prefix: str | None) -> str:
    """``/gateway/`` and ``gateway`` both become ``/gateway``; nothing becomes ``""``."""
    cleaned = (prefix or "").strip().strip("/")
    return f"/{cleaned}" if cleaned else ""


def create_app(
    services: Services,
    bound_host: str | None = None,
    bound_port: int | None = None,
    extra_hosts: set[str] | None = None,
    serve_ui: bool = True,
    api_prefix: str | None = None,
) -> FastAPI:
    """Build the application.

    ``api_prefix`` moves everything this package serves — the API and the web UI — under one
    path, so a host application can mount it beside its own routes without a clash. The web UI
    needs no change: it derives its base URL from the URL its own script was loaded from.
    """
    problem = validate_security_settings(services.settings)
    if problem:
        raise ValidationError(problem)

    prefix = normalize_prefix(api_prefix)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        # The renewal timer is off when the operator set server.auto_refresh=false
        # or built Services without a refresher (tests, embedders).
        if services.refresher is not None and services.settings.auto_refresh:
            services.refresher.start()
        if services.credits is not None and services.settings.auto_checkin:
            services.credits.start()
        try:
            yield
        finally:
            services.close()

    app = FastAPI(title="Hoshimi", version=VERSION, lifespan=lifespan, docs_url=f"{prefix}/docs", redoc_url=None, openapi_url=f"{prefix}/openapi.json")
    app.state.services = services
    app.state.bound_port = bound_port
    app.state.bound_host = bound_host
    # What the socket was opened from, so the settings view can tell a saved
    # host/port that still needs a restart; unknown when nothing was bound.
    app.state.started_listen = (services.settings.host, services.settings.port) if bound_host is not None or bound_port is not None else None
    app.state.api_prefix = prefix
    install_error_handlers(app)

    # Management API under /api; inference protocols share /v1.
    app.include_router(health.router, prefix=prefix)
    for module in (app_info, settings, providers, channels, catalog, probe, stats, refresh, credits, login, playground):
        app.include_router(module.router, prefix=f"{prefix}/api")
    for module in INFERENCE_ROUTES:
        app.include_router(module.router, prefix=prefix)

    origins = services.settings.allow_origins
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)
    # Chat deltas are small; long JSON payloads compress well.
    app.add_middleware(GZipMiddleware, minimum_size=4096)
    app.add_middleware(
        SecurityMiddleware,
        policy=lambda: build_policy(services.settings),
        bound_host=lambda: bound_host or services.settings.host,
        extra_hosts=extra_hosts,
        prefix=prefix,
    )

    # Registered last, so it only receives paths nothing else matched.
    dist = web_dist_dir()
    if serve_ui and (dist / "index.html").is_file():
        app.mount(f"{prefix}/", SPAStaticFiles(directory=dist, html=True), name="ui")
    elif serve_ui:
        logger.warning("No web UI build found at %s; serving the API only", dist)
    return app
