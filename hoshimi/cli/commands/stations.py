"""stations: the OpenAI-compatible stations an ``agg`` account can name."""

from __future__ import annotations

from typing import Annotated

import typer

from hoshimi.cli.output import print_json, print_table


def stations(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """List the stations an `agg` account can name as its `preset`.

    The table is what the gateway already knows about a station's address; the
    key is still the operator's to bring.
    """
    from hoshimi.core.presets import PRESETS

    if json_output:
        print_json(
            {
                "stations": [
                    {
                        "id": preset.id,
                        "display_name": preset.display_name,
                        "base_url": preset.base_url,
                        "key_env": preset.key_env,
                        "catalog_keyless": preset.catalog_keyless,
                    }
                    for preset in PRESETS
                ]
            }
        )
        return

    print_table(
        "Stations",
        ["Id", "Name", "Base URL", "Key env"],
        [(preset.id, preset.display_name, preset.base_url, preset.key_env) for preset in PRESETS],
    )
    print()
    print('Use one in config.json:  "providers" → "agg" → "accounts" → {"preset": "<id>", "api_key": "..."}')
    print("The key env var is the name the upstream reads its key from; Hoshimi reads config.json only.")
