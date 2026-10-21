"""Print this package's OpenAPI document, for the web UI's schema.d.ts.

Run from the repository root:

    python scripts/generate_openapi.py > hoshimi/webui/openapi.json
    cd hoshimi/webui && bun run typegen
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# The repository root, so the script runs whether or not the package is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hoshimi.api.app import create_app
from hoshimi.core.config import Settings
from hoshimi.core.context import build_services


def main() -> None:
    # Empty settings and a throwaway data directory: the schema describes
    # shapes, not this machine's configuration — and building the services
    # opens hoshimi.db, which would otherwise import this machine's old JSON
    # stores just to print a schema.
    with tempfile.TemporaryDirectory(prefix="hoshimi-openapi-") as data_dir:
        os.environ["HOSHIMI_DATA_DIR"] = data_dir
        services = build_services(Settings())
        try:
            app = create_app(services, serve_ui=False)
            json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        finally:
            services.close()


if __name__ == "__main__":
    main()
