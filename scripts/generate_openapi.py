"""Print this package's OpenAPI document, for the web UI's schema.d.ts.

Run from the repository root:

    python scripts/generate_openapi.py > hoshimi/webui/openapi.json
    cd hoshimi/webui && bun run typegen
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The repository root, so the script runs whether or not the package is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hoshimi.api.app import create_app
from hoshimi.core.config import Settings
from hoshimi.core.context import build_services


def main() -> None:
    # Empty settings: the schema describes shapes, not this machine's configuration.
    services = build_services(Settings())
    app = create_app(services, serve_ui=False)
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
