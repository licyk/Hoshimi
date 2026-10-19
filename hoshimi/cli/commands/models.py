"""models: the model catalogue every enabled provider advertises."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hoshimi.cli.output import print_json, print_table


def models(
    config: Annotated[Path | None, typer.Option(help="Path to the config file", resolve_path=True)] = None,
    provider: Annotated[str | None, typer.Option(help="Only show this provider")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """List the available models (``provider/model`` routes them)."""
    from hoshimi.cli.output import open_services

    with open_services(str(config) if config else None) as services:
        rows = [(name, owner) for name, owner in services.registry.model_ids() if provider is None or owner == provider]
        if json_output:
            print_json([{"id": name, "provider": owner} for name, owner in rows])
            return
        if not rows:
            print("no models available")
            return
        print_table("Models", ["Model", "Provider"], rows)
