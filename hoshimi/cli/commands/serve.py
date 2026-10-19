"""serve: bind the port, build the services, and serve the API and the web UI."""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from hoshimi.logger import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def serve(
    host: Annotated[str | None, typer.Option(help="Address to bind (default from config: 127.0.0.1)")] = None,
    port: Annotated[int | None, typer.Option(min=0, max=65535, help="First port to try (default from config; 0 = any free port)")] = None,
    strict_port: Annotated[bool | None, typer.Option("--strict-port/--no-strict-port", help="Fail instead of trying the next port")] = None,
    open_browser: Annotated[bool | None, typer.Option("--open/--no-open", help="Open the web UI in a browser")] = True,
    config: Annotated[Path | None, typer.Option(help="Path to the config file", resolve_path=True)] = None,
    access_token: Annotated[str | None, typer.Option(help="Access key for the tool UI and management API")] = None,
    model_api_key: Annotated[str | None, typer.Option(help="Independent API key for external /v1/ model requests")] = None,
    default_model: Annotated[str | None, typer.Option(help="Model used when a request omits 'model'")] = None,
    auto_refresh: Annotated[bool, typer.Option("--refresh/--no-refresh", help="Renew credentials on a timer (off = rotate tokens by hand)")] = True,
    auto_checkin: Annotated[bool, typer.Option("--checkin/--no-checkin", help="Daily check-in timer for free credits (off = claim by hand)")] = True,
    serve_ui: Annotated[bool, typer.Option("--ui/--no-ui", help="Serve the web UI next to the API")] = True,
) -> None:
    """Start the server: OpenAI-compatible API plus the Hoshimi web UI."""
    import uvicorn

    from hoshimi.api.app import create_app, normalize_prefix
    from hoshimi.core.config import load_settings
    from hoshimi.core.context import build_services
    from hoshimi.core.net.ports import PortUnavailableError, bind_first_free_port, is_loopback
    from hoshimi.core.net.runtime_file import remove_runtime_file, write_runtime_file

    settings = load_settings(str(config) if config else None)
    if access_token:
        settings.access_token = access_token
    if model_api_key:
        settings.model_api_key = model_api_key
    if default_model:
        settings.default_model = default_model
    settings.auto_refresh = auto_refresh
    settings.auto_checkin = auto_checkin
    services = build_services(settings, config_path=str(config) if config else None)

    host = host or settings.host
    port = settings.port if port is None else port
    strict = bool(strict_port)

    if not is_loopback(host):
        logger.warning("Binding to %s makes the server reachable from other machines.", host)
        if not settings.access_token:
            services.close()
            logger.error("Set server.access_token first (hoshimi config set server.access_token <secret>).")
            raise typer.Exit(4)
        if not settings.model_api_key:
            logger.warning(
                "No server.model_api_key is set: external model APIs accept requests without a key."
            )

    try:
        sock = bind_first_free_port(host, port, strict=strict)
    except PortUnavailableError as e:
        services.close()
        logger.error("%s", e)
        raise typer.Exit(3) from None
    actual_port = sock.getsockname()[1]
    if actual_port != port and port != 0:
        logger.warning("Port %s is unavailable. Using port %s.", port, actual_port)
    url_host = f"[{host}]" if ":" in host else host
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else url_host
    url = f"http://{url_host}:{actual_port}{normalize_prefix(None)}"

    app = create_app(services, bound_host=host, bound_port=actual_port, serve_ui=serve_ui)
    config_ = uvicorn.Config(app=app, log_level="warning", lifespan="on")
    server = uvicorn.Server(config_)
    write_runtime_file(services.data_dir, host, actual_port, url)
    print(f"SERVER_READY url={url}", flush=True)
    logger.info("Hoshimi running on %s", url)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.run(sockets=[sock])
    finally:
        remove_runtime_file(services.data_dir)
        services.close()
        sock.close()
