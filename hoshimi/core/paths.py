"""Default locations on disk."""

import os
import sys
from pathlib import Path

ENV_PREFIX = "HOSHIMI_"
DATA_DIR_ENV = f"{ENV_PREFIX}DATA_DIR"


def default_data_dir() -> Path:
    """Return the data directory: ``HOSHIMI_DATA_DIR``, or the platform's user data folder."""
    env = os.environ.get(DATA_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return (base / "hoshimi").resolve()


def config_file_path() -> Path:
    """Return the configuration file in Hoshimi's data directory, even before it exists."""
    return default_data_dir() / "config.json"
