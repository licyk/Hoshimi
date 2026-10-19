"""version, env."""

from __future__ import annotations

import os
import platform
from importlib import metadata
from typing import Annotated

import typer

from hoshimi.cli.output import print_json, print_table
from hoshimi.version import VERSION

COMPONENTS = ("fastapi", "uvicorn", "pydantic", "typer", "httpx", "rich")

ENV_VARS = {
    "HOSHIMI_CONFIG": "Path to the config file (default: ./config.json)",
    "HOSHIMI_DATA_DIR": "Data directory holding settings and the runtime file",
    "HOSHIMI_LOG_LEVEL": "Log level: DEBUG, INFO, WARNING or ERROR",
}


def version(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """Show the version of Hoshimi and its main components."""
    components: dict[str, str | None] = {}
    for name in COMPONENTS:
        try:
            components[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            components[name] = None
    if json_output:
        print_json({"hoshimi": VERSION, "python": platform.python_version(), "components": components})
        return
    rows = [
        ("hoshimi", VERSION),
        ("python", platform.python_version()),
        *((k, v or "not installed") for k, v in components.items()),
    ]
    print_table(None, ["Component", "Version"], rows)


def env(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """List the environment variables Hoshimi reads, and those currently set."""
    in_use = {k: ("***" if "TOKEN" in k or "KEY" in k else v) for k, v in sorted(os.environ.items()) if k.startswith("HOSHIMI_")}
    if json_output:
        print_json({"known": ENV_VARS, "set": in_use})
        return
    print_table("Environment variables", ["Name", "Meaning"], ENV_VARS.items())
    if in_use:
        print_table("Set now", ["Name", "Value"], in_use.items())
