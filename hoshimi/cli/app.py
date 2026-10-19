"""Entry point: ``get_app()`` registers every command; ``main()`` runs it with uniform error handling."""

import sys
import traceback

import typer
from typer import Abort, Exit

from hoshimi.cli.commands.check import check
from hoshimi.cli.commands.config import config_get, config_path, config_set, config_show
from hoshimi.cli.commands.login import login
from hoshimi.cli.commands.models import models
from hoshimi.cli.commands.providers import providers
from hoshimi.cli.commands.serve import serve
from hoshimi.cli.commands.stations import stations
from hoshimi.cli.commands.system import env, version
from hoshimi.cli.factory import ClickException, typer_factory
from hoshimi.logger import setup_logging

logger = setup_logging()


def get_app() -> typer.Typer:
    """Build the Hoshimi command line. Every command is registered here, and nowhere else."""
    app = typer_factory("An OpenAI-compatible gateway in front of the free model stations")

    app.command(help="Start the server: API and web UI", name="serve")(serve)
    app.command(help="Validate the configuration and report providers and accounts", name="check")(check)
    app.command(help="List the available models", name="models")(models)
    app.command(help="List the configured providers and their accounts", name="providers")(providers)
    app.command(help="List the stations an `agg` account can name as its preset", name="stations")(stations)
    app.command(help="Sign into a station; the account lands in config.json", name="login")(login)
    app.command(help="Show the version of Hoshimi and its main components", name="version")(version)
    app.command(help="List the environment variables Hoshimi reads", name="env")(env)

    config_cli = typer_factory(help="Show and change settings in config.json")
    config_cli.command(help="Show the effective configuration", name="show")(config_show)
    config_cli.command(help="Print one setting", name="get")(config_get)
    config_cli.command(help="Change one setting and save it", name="set")(config_set)
    config_cli.command(help="Print the path of the config file", name="path")(config_path)
    app.add_typer(config_cli, name="config")

    return app


def main() -> None:
    """Run the command line."""
    from hoshimi.core.errors import HoshimiError

    try:
        get_app()()
    except Exit as e:
        sys.exit(e.exit_code)
    except Abort:
        logger.error("Cancelled")
        sys.exit(1)
    except ClickException as e:
        e.show()
        sys.exit(e.exit_code)
    except HoshimiError as e:
        logger.error("%s", e.message)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        logger.error("Interrupted")
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        logger.error("Command failed: %s", e)
        sys.exit(1)
