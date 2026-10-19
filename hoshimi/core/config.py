"""Settings and config-file loading."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 10600
DEFAULT_MAX_BODY_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ACCOUNT_RETRIES = 3
DEFAULT_CONFIG_PATHS = ("config.json",)
CONFIG_PATH_ENV = "HOSHIMI_CONFIG"


@dataclass
class ProviderConfig:
    """Per-provider configuration from the config file."""

    id: str
    enabled: bool = True
    accounts: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    access_token: str | None = None
    # Separate credentials for tool management and external model inference.
    model_api_key: str | None = None
    allow_cidrs: list[str] = field(default_factory=list)
    allow_hosts: list[str] = field(default_factory=list)
    allow_origins: list[str] = field(default_factory=list)
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    max_account_retries: int = DEFAULT_MAX_ACCOUNT_RETRIES
    # Renew credentials on a timer (see hoshimi.core.refresh). Off for an
    # install that prefers to rotate tokens by hand.
    auto_refresh: bool = True
    # Daily check-in timer (see hoshimi.core.credits). Off for pure gateways.
    auto_checkin: bool = True
    # Provider accounts managed by this gateway.
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    # Optional default model when a request omits "model".
    default_model: str | None = None


def validate_settings(settings: Settings) -> str | None:
    """Reject values the server could not run with; None when the settings are usable."""
    if not 0 <= settings.port <= 65535:
        return f"server.port must be between 0 and 65535 (got {settings.port})."
    if settings.max_body_bytes <= 0:
        return "server.max_body_bytes must be positive."
    if settings.max_account_retries < 1:
        return "server.max_account_retries must be at least 1."
    return None


def find_config_file(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None
    env = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if env:
        path = Path(env).expanduser()
        if path.exists():
            return path
    for candidate in DEFAULT_CONFIG_PATHS:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _split_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def settings_from_dict(data: dict[str, Any], settings: Settings | None = None) -> Settings:
    settings = settings or Settings()
    server_raw = data.get("server")
    security_raw = data.get("security")
    server: dict[str, Any] = server_raw if isinstance(server_raw, dict) else {}
    security: dict[str, Any] = security_raw if isinstance(security_raw, dict) else {}

    if isinstance(server.get("host"), str):
        settings.host = server["host"]
    if isinstance(server.get("port"), int):
        settings.port = server["port"]
    for key in ("access_token", "model_api_key"):
        if key not in server:
            continue
        value = server[key]
        if value is not None and not isinstance(value, str):
            raise ValueError(f"server.{key} must be a string or null")
        setattr(settings, key, value.strip() or None if isinstance(value, str) else None)
    if server.get("default_model") is not None:
        settings.default_model = str(server["default_model"])
    if isinstance(server.get("max_body_bytes"), int):
        settings.max_body_bytes = server["max_body_bytes"]
    if isinstance(server.get("max_account_retries"), int):
        settings.max_account_retries = max(1, server["max_account_retries"])
    if isinstance(server.get("auto_refresh"), bool):
        settings.auto_refresh = server["auto_refresh"]
    if isinstance(server.get("auto_checkin"), bool):
        settings.auto_checkin = server["auto_checkin"]

    settings.allow_cidrs = _split_list(security.get("allow_cidr") or security.get("allow_cidrs"))
    settings.allow_hosts = _split_list(security.get("allow_host") or security.get("allow_hosts"))
    settings.allow_origins = _split_list(
        security.get("allow_origin") or security.get("allow_origins")
    )

    providers = data.get("providers")
    if isinstance(providers, dict):
        for pid, raw in providers.items():
            if not isinstance(raw, dict):
                continue
            cfg = settings.providers.get(pid) or ProviderConfig(id=pid)
            if isinstance(raw.get("enabled"), bool):
                cfg.enabled = raw["enabled"]
            if isinstance(raw.get("accounts"), list):
                cfg.accounts = [a for a in raw["accounts"] if isinstance(a, dict)]
            if isinstance(raw.get("options"), dict):
                cfg.options.update(raw["options"])
            settings.providers[pid] = cfg
    return settings


def load_settings(config_path: str | None = None, settings: Settings | None = None) -> Settings:
    settings = settings or Settings()
    path = find_config_file(config_path)
    if path is None:
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # The gateway still refuses to start — a config it could not read is
        # not a config it should quietly replace with nothing, and an install
        # that came up with every provider disabled would look like every
        # account had vanished. But the bytes are preserved first, and the
        # message says where they went: without that, the next write to this
        # path is an `os.replace` over the only copy.
        backup = _backup_unreadable(path)
        hint = f" The original bytes were kept at {backup}." if backup else ""
        raise ValueError(f"Failed to read config file {path}: {exc}.{hint}") from exc
    if not isinstance(data, dict):
        raise TypeError(f"Config file {path} must contain a JSON object.")
    return settings_from_dict(data, settings)


def _backup_unreadable(path: Path) -> Path | None:
    """Keep a copy of a config file that could not be parsed. None = it failed.

    Best effort and silent on success: a failure here must not turn a clear
    "your config is broken" into a second, less useful error.
    """
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        target.write_bytes(path.read_bytes())
    except OSError as exc:
        logger.warning("Could not keep a copy of the unreadable config %s: %s", path, exc)
        return None
    _restrict_permissions(target)
    return target


def _restrict_permissions(path: Path) -> None:
    """Owner read/write only, best effort.

    The file holds refresh tokens in plaintext, so it must not be
    world-readable — but a filesystem that cannot say so (Windows, some
    network mounts) is not a reason to fail a save. The keys are at least
    inside the user's own directory either way.
    """
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.debug("Could not restrict %s to 0600: %s", path, exc)


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    """Serialize settings back to the config-file shape, so a save round-trips."""
    data: dict[str, Any] = {
        "server": {
            "host": settings.host,
            "port": settings.port,
            "max_body_bytes": settings.max_body_bytes,
            "max_account_retries": settings.max_account_retries,
            "auto_refresh": settings.auto_refresh,
            "auto_checkin": settings.auto_checkin,
        },
        "security": {
            "allow_cidrs": list(settings.allow_cidrs),
            "allow_hosts": list(settings.allow_hosts),
            "allow_origins": list(settings.allow_origins),
        },
    }
    if settings.access_token:
        data["server"]["access_token"] = settings.access_token
    if settings.model_api_key:
        data["server"]["model_api_key"] = settings.model_api_key
    if settings.default_model:
        data["server"]["default_model"] = settings.default_model
    if settings.providers:
        data["providers"] = {
            pid: {
                "enabled": cfg.enabled,
                **({"accounts": cfg.accounts} if cfg.accounts else {}),
                **({"options": cfg.options} if cfg.options else {}),
            }
            for pid, cfg in settings.providers.items()
        }
    return data


def save_settings(settings: Settings, path: str | Path | None = None) -> Path:
    """Write ``settings`` back to the config file (created when missing).

    Two things happen around the write that are not the write itself, and
    both are about the file's other lives. The result is restricted to the
    owner, because it holds refresh tokens in plaintext. And an existing
    file that cannot be parsed is copied aside first: ``os.replace`` is the
    only write path here, so an unexamined overwrite of a hand-edited or
    half-written config is the one moment this routine can destroy
    something it never read.
    """
    target = Path(path).expanduser() if path else find_config_file()
    if target is None:
        target = Path("config.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _backup_unreadable(target)
    descriptor, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    os.close(descriptor)  # mkstemp creates the file owner-only before any token is written.
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(settings_to_dict(settings), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    _restrict_permissions(target)
    return target
