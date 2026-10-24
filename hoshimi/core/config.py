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

from hoshimi.core.agent.approval import APPROVAL_MODES, DEFAULT_APPROVAL, LEGACY_MODES
from hoshimi.core.agent.terminals import SHELL_KINDS, shell_kind
from hoshimi.core.agent.tools import GROUPS
from hoshimi.core.net.policy import NetworkPolicy, parse_policy
from hoshimi.core.paths import config_file_path

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 10600
DEFAULT_MAX_BODY_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ACCOUNT_RETRIES = 3
DEFAULT_STATS_KEEP_DAYS = 90


@dataclass
class ProviderConfig:
    """Per-provider configuration from the config file."""

    id: str
    enabled: bool = True
    accounts: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    network: NetworkPolicy = field(default_factory=NetworkPolicy)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # Fail at start when `port` is taken, instead of searching for a free one.
    strict_port: bool = False
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
    # Ask PyPI whether a newer release exists (see hoshimi.core.updates). Off
    # for an install that should make no requests of its own.
    check_updates: bool = True
    # Days each recorded call is kept in hoshimi.db (daily totals are kept forever).
    stats_keep_days: int = DEFAULT_STATS_KEEP_DAYS
    # Provider accounts managed by this gateway.
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    # Optional default model when a request omits "model".
    default_model: str | None = None
    # Sent first in every Playground conversation (playground.system_prompt); empty sends none.
    playground_system_prompt: str = ""
    # The model that names Playground conversations (playground.title_model); empty: the conversation's own.
    playground_title_model: str = ""
    # The model that describes images for one that cannot read them (playground.vision_model):
    # "auto" picks one from the catalogue, "off" never asks, anything else is that model.
    playground_vision_model: str = "auto"
    # The Playground's built-in tools new conversations start with (playground.tools): read, apply_patch,
    # shell, terminal, web_search, web_fetch; each conversation turns them on or off for itself. All of
    # them by default; the reviewer (playground.approval "review") judges what they do first.
    playground_tools: list[str] = field(default_factory=lambda: list(GROUPS))
    # Where the tools work (playground.workspace); empty: <data dir>/workspace.
    playground_workspace: str = ""
    # When tools ask first, and whom (playground.approval): "review" has a model judge commands, file
    # changes and anything outside the workspace (and asks the user when it cannot); "ask" asks the user
    # for those; "workspace" ("auto" before) asks the user only before reaching outside the workspace;
    # "full" never asks.
    playground_approval: str = DEFAULT_APPROVAL
    # The model that reviews calls in "review" mode (playground.review_model); empty: the conversation's own.
    playground_review_model: str = ""
    # The shell commands run in when a call names none (playground.shell): "auto" (bash, sh without it,
    # PowerShell on Windows), or bash, zsh, sh, powershell or cmd.
    playground_shell: str = "auto"
    # A SearXNG instance for web_search (playground.search_url); empty searches DuckDuckGo.
    playground_search_url: str = ""
    # Compact a conversation by itself as it nears its model's context window (playground.auto_compact).
    playground_auto_compact: bool = True
    # The share of the window at which it is compacted (playground.compact_threshold), 0.5 to 0.95.
    playground_compact_threshold: float = 0.85
    # The model that writes the summary (playground.compact_model); empty: the conversation's own.
    playground_compact_model: str = ""


def validate_settings(settings: Settings) -> str | None:
    """Reject values the server could not run with; None when the settings are usable."""
    try:
        parse_policy(settings.network.as_dict())
        for cfg in settings.providers.values():
            parse_policy(cfg.options.get("network"), inherited=settings.network)
            if cfg.id == "cline" and cfg.options.get("client_profile", "sdk") not in ("sdk", "extension"):
                return "cline client_profile must be sdk or extension"
    except (TypeError, ValueError) as exc:
        return str(exc)
    if not 0 <= settings.port <= 65535:
        return f"server.port must be between 0 and 65535 (got {settings.port})."
    if settings.max_body_bytes <= 0:
        return "server.max_body_bytes must be positive."
    if settings.max_account_retries < 1:
        return "server.max_account_retries must be at least 1."
    if settings.stats_keep_days < 1:
        return "server.stats_keep_days must be at least 1."
    unknown = [t for t in settings.playground_tools if t not in GROUPS]
    if unknown:
        return f"playground.tools: unknown tool {unknown[0]!r} (known: {', '.join(GROUPS)})."
    if settings.playground_approval not in APPROVAL_MODES:
        return f"playground.approval must be {', '.join(APPROVAL_MODES[:-1])} or {APPROVAL_MODES[-1]}."
    if settings.playground_shell != "auto" and settings.playground_shell not in SHELL_KINDS:
        return f"playground.shell must be auto, {', '.join(SHELL_KINDS[:-1])} or {SHELL_KINDS[-1]}."
    if settings.playground_search_url and not settings.playground_search_url.startswith(("http://", "https://")):
        return "playground.search_url must be an http(s) address."
    if not 0.5 <= settings.playground_compact_threshold <= 0.95:
        return "playground.compact_threshold must be between 0.5 and 0.95."
    return None


def _split_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def settings_from_dict(data: dict[str, Any], settings: Settings | None = None) -> Settings:
    settings = settings or Settings()
    if "network" in data:
        settings.network = parse_policy(data["network"])
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
    if "check_updates" in server:
        if not isinstance(server["check_updates"], bool):
            raise ValueError("server.check_updates must be true or false")
        settings.check_updates = server["check_updates"]
    if "strict_port" in server:
        if not isinstance(server["strict_port"], bool):
            raise ValueError("server.strict_port must be true or false")
        settings.strict_port = server["strict_port"]
    if "stats_keep_days" in server:
        value = server["stats_keep_days"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("server.stats_keep_days must be a whole number of days")
        settings.stats_keep_days = value

    settings.allow_cidrs = _split_list(security.get("allow_cidr") or security.get("allow_cidrs"))
    settings.allow_hosts = _split_list(security.get("allow_host") or security.get("allow_hosts"))
    settings.allow_origins = _split_list(
        security.get("allow_origin") or security.get("allow_origins")
    )

    playground = data.get("playground")
    if isinstance(playground, dict):
        for key, attribute, default in (
            ("system_prompt", "playground_system_prompt", ""),
            ("title_model", "playground_title_model", ""),
            ("vision_model", "playground_vision_model", "auto"),
            ("compact_model", "playground_compact_model", ""),
            ("review_model", "playground_review_model", ""),
        ):
            if key not in playground:
                continue
            value = playground[key]
            if value is not None and not isinstance(value, str):
                raise ValueError(f"playground.{key} must be a string")
            setattr(settings, attribute, (value or "").strip() or default)
        for key, attribute, default in (
            ("workspace", "playground_workspace", ""),
            ("approval", "playground_approval", DEFAULT_APPROVAL),
            ("search_url", "playground_search_url", ""),
            ("shell", "playground_shell", "auto"),
        ):
            if key not in playground:
                continue
            value = playground[key]
            if value is not None and not isinstance(value, str):
                raise ValueError(f"playground.{key} must be a string")
            setattr(settings, attribute, (value or "").strip() or default)
        # Old names: approval "auto" is "workspace"; shell "pwsh" is "powershell".
        settings.playground_approval = LEGACY_MODES.get(settings.playground_approval, settings.playground_approval)
        if settings.playground_shell != "auto":
            settings.playground_shell = shell_kind(settings.playground_shell) or settings.playground_shell
        if "tools" in playground:
            tools = playground["tools"]
            if tools is None:
                tools = []
            if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
                raise ValueError("playground.tools must be a list of tool names")
            settings.playground_tools = list(dict.fromkeys(t.strip() for t in tools if t.strip()))
        if "auto_compact" in playground:
            value = playground["auto_compact"]
            if not isinstance(value, bool):
                raise ValueError("playground.auto_compact must be true or false")
            settings.playground_auto_compact = value
        if "compact_threshold" in playground:
            value = playground["compact_threshold"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("playground.compact_threshold must be a number")
            settings.playground_compact_threshold = float(value)

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


def load_settings() -> Settings:
    """Load the data directory's config; a missing file uses the defaults."""
    settings = Settings()
    path = config_file_path()
    if not path.exists():
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
        "network": settings.network.as_dict(),
        "server": {
            "host": settings.host,
            "port": settings.port,
            "strict_port": settings.strict_port,
            "max_body_bytes": settings.max_body_bytes,
            "max_account_retries": settings.max_account_retries,
            "auto_refresh": settings.auto_refresh,
            "auto_checkin": settings.auto_checkin,
            "check_updates": settings.check_updates,
            "stats_keep_days": settings.stats_keep_days,
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
    playground = {
        key: value
        for key, value, default in (
            ("system_prompt", settings.playground_system_prompt, ""),
            ("title_model", settings.playground_title_model, ""),
            ("vision_model", settings.playground_vision_model, "auto"),
            ("tools", settings.playground_tools, list(GROUPS)),
            ("workspace", settings.playground_workspace, ""),
            ("approval", settings.playground_approval, DEFAULT_APPROVAL),
            ("review_model", settings.playground_review_model, ""),
            ("shell", settings.playground_shell, "auto"),
            ("search_url", settings.playground_search_url, ""),
            ("auto_compact", settings.playground_auto_compact, True),
            ("compact_threshold", settings.playground_compact_threshold, 0.85),
            ("compact_model", settings.playground_compact_model, ""),
        )
        if value != default
    }
    if playground:
        data["playground"] = playground
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


def save_settings(settings: Settings) -> Path:
    """Write ``settings`` back to the config file (created when missing).

    Two things happen around the write that are not the write itself, and
    both are about the file's other lives. The result is restricted to the
    owner, because it holds refresh tokens in plaintext. And an existing
    file that cannot be parsed is copied aside first: ``os.replace`` is the
    only write path here, so an unexamined overwrite of a hand-edited or
    half-written config is the one moment this routine can destroy
    something it never read.
    """
    target = config_file_path()
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
