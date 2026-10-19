"""check: validate the configuration and report what would be served."""

from __future__ import annotations

from typing import Annotated

import typer

from hoshimi.cli.output import print_json


def check(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """Validate the configuration and exit without serving."""
    from hoshimi.core.config import load_settings, validate_settings
    from hoshimi.core.context import build_services
    from hoshimi.core.security import validate_security_settings

    settings = load_settings()
    problem = validate_settings(settings) or validate_security_settings(settings)
    if problem:
        raise typer.BadParameter(problem)

    services = build_services(settings)
    try:
        from hoshimi.core.providers import real_account_count

        accounts = {
            pid: real_account_count(provider) for pid, provider in services.registry.providers.items()
        }
        total = sum(accounts.values())
        payload = {
            "config_path": str(services.config_path),
            "providers": accounts,
            "accounts": total,
            "keyless": sorted(pid for pid, p in services.registry.providers.items() if p.keyless),
            "models": len(services.registry.model_ids()),
        }
        if json_output:
            print_json(payload)
            return
        if not accounts:
            print("error: no provider enabled. Enable a free lane (`providers.<id>.enabled`) or add accounts to config.json (`providers.<id>.accounts`).", flush=True)
            raise typer.Exit(2)
        parts = []
        for pid, count in sorted(accounts.items()):
            suffix = ""
            if count == 0 and services.registry.providers[pid].keyless:
                suffix = " (keyless)"
            parts.append(f"{pid}:{count}{suffix}")
        print(f"ok: {len(accounts)} provider(s), {total} account(s) — {', '.join(parts)}")
        print(f"config: {services.config_path}")
    finally:
        services.close()
