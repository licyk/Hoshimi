"""providers: which stations are configured and how many accounts each has."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hoshimi.cli.output import print_json, print_table


def providers(
    config: Annotated[Path | None, typer.Option(help="Path to the config file", resolve_path=True)] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """List the configured providers with their accounts and model counts."""
    from hoshimi.cli.output import open_services

    with open_services(str(config) if config else None) as services:
        from hoshimi.core.providers import real_account_count

        rows = []
        payload = []
        for pid, provider in services.registry.providers.items():
            rows.append((pid, provider.display_name or pid, real_account_count(provider), len(provider.models())))
            payload.append(
                {
                    "id": pid,
                    "display_name": provider.display_name or pid,
                    "accounts": [account.id for account in provider.accounts if not account.anonymous],
                    "keyless": provider.keyless,
                    "models": len(provider.models()),
                }
            )
        disabled = [(pid, cfg.enabled) for pid, cfg in sorted(services.settings.providers.items()) if not cfg.enabled]
        if json_output:
            print_json({"providers": payload, "disabled": [pid for pid, _ in disabled]})
            return
        if not rows:
            print("no providers configured")
            return
        print_table("Providers", ["Id", "Name", "Accounts", "Models"], rows)
        if disabled:
            print("disabled: " + ", ".join(pid for pid, _ in disabled))
