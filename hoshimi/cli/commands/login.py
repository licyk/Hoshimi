"""login: sign into a station from the command line, browser first."""

from __future__ import annotations

import logging
import time
import webbrowser
from typing import Annotated

import typer

from hoshimi.logger import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

POLL_INTERVAL_SECONDS = 2.0


def login(
    provider: Annotated[str | None, typer.Argument(help="Station to sign into (omit to list the ones that offer it)")] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open the station's login page in a browser")] = True,
    manual: Annotated[bool, typer.Option("--manual", help="Prompt for the authorization code or callback URL")] = False,
) -> None:
    """Sign into a station; the account lands in config.json when the browser comes back."""
    from hoshimi.core.context import build_services
    from hoshimi.core.login import LOGIN_TIMEOUT_SECONDS, LoginError, login_providers

    services = build_services()
    try:
        if services.login is None:  # pragma: no cover - build_services always builds one
            logger.error("The login service is not available.")
            raise typer.Exit(1)

        if provider is None:
            logger.info("Stations offering an interactive login: %s", ", ".join(login_providers()))
            return

        try:
            session = services.login.start(provider)
        except LoginError as exc:
            logger.error("%s", exc)
            raise typer.Exit(2) from None

        deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
        opened = False
        reported_code = False
        reported_url = ""
        submitted = False
        while True:
            current = services.login.poll(session.id)
            if current is None:  # pragma: no cover - the session outlives the loop
                break
            if current.user_code and not reported_code:
                logger.info("Authorization code: %s — %s", current.user_code, current.login_url)
                reported_code = True
            if current.login_url and current.login_url != reported_url:
                logger.info("Authorize at %s", current.login_url)
                reported_url = current.login_url
            if manual and current.waiting_for == "manual_code" and not submitted:
                value = typer.prompt("Authorization code or callback URL", hide_input=True)
                services.login.submit(current.id, value)
                submitted = True
            # The URL only exists once the station has issued it, so the browser
            # opens on the first poll that carries one.
            if open_browser and not opened and current.login_url:
                logger.info("Opening %s", current.login_url)
                opened = webbrowser.open(current.login_url)
                if not opened:
                    logger.warning("Could not open a browser; visit %s by hand.", current.login_url)
            if current.status != "pending":
                if current.status == "succeeded":
                    logger.info("Signed into %s as %s.", current.provider, current.account_id)
                    return
                logger.error("%s", current.message or "login failed")
                raise typer.Exit(1)
            if time.monotonic() >= deadline:
                logger.error("Login timed out before the station answered.")
                raise typer.Exit(1)
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        services.close()
