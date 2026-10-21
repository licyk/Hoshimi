"""Run with an installed wheel's interpreter: python -I scripts/smoke_install.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    import hoshimi

    checkout = Path(__file__).resolve().parent.parent
    installed = Path(hoshimi.__file__).resolve()
    if checkout in installed.parents:
        raise RuntimeError("Smoke check imported the source checkout, not the installed distribution")
    subprocess.run([sys.executable, "-I", "-m", "hoshimi", "version"], check=True)

    with tempfile.TemporaryDirectory(prefix="hoshimi-smoke-") as data:
        os.environ["XDG_DATA_HOME"] = data
        from fastapi.testclient import TestClient

        from hoshimi.api.app import create_app
        from hoshimi.api.static import web_dist_dir
        from hoshimi.core.config import Settings
        from hoshimi.core.context import build_services

        services = build_services(Settings(auto_refresh=False, auto_checkin=False))
        with TestClient(create_app(services), base_url="http://localhost") as client:
            assert client.get("/health").status_code == 200
            index = client.get("/")
            assert index.status_code == 200 and "Hoshimi" in index.text
            assert any(a.name.startswith("favicon") for a in (web_dist_dir() / "assets").glob("*.svg"))
            for asset in (web_dist_dir() / "assets").glob("*"):
                assert client.get(f"/assets/{asset.name}").status_code == 200, asset.name
            assert client.get("/api/v1/providers").status_code == 200
    print(f"Installed package, CLI, API and bundled Web UI verified: {installed}")


if __name__ == "__main__":
    main()
