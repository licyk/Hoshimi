"""config show | get | set | path — the config file (JSON) behind the gateway."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Annotated, Any

import typer

from hoshimi.cli.output import console, print_json


def _load() -> tuple[dict[str, Any], Path]:
    """Return the raw config-file dict and the file it came from."""
    from hoshimi.core.config import load_settings, settings_to_dict
    from hoshimi.core.paths import config_file_path

    path = config_file_path()
    settings = load_settings()
    data = _raw_or_settings(path) if path.exists() else settings_to_dict(settings)
    return data, path


def _raw_or_settings(path: Path) -> dict[str, Any]:
    """Read the file as written, so ``set`` does not drop keys we do not model."""
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return raw


def _mask(data: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(data)
    server = out.get("server")
    if isinstance(server, dict):
        for key in ("access_token", "model_api_key"):
            if server.get(key):
                server[key] = "***"
    sensitive = {"api_key", "access_token", "refresh_token", "token", "security_oauth_token", "secret_access_key", "security_token", "user_token",
                 "aws_access_key_id", "aws_secret_access_key", "aws_session_token", "client_secret", "code_verifier", "dpop_private_key_jwk", "proxy"}
    def redact(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in sensitive and item:
                    value[key] = "***"
                else:
                    redact(item)
        elif isinstance(value, list):
            for item in value:
                redact(item)
    redact(out.get("providers"))
    redact(out.get("network"))
    return out


def _get_dotted(data: Any, dotted: str) -> Any:
    node = data
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise KeyError(dotted)
    return node


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def _parse_scalar(raw: str) -> Any:
    """Turn a command-line string into a value: JSON when it parses, else the string."""
    import json

    text = raw.strip()
    if text.lower() in ("null", "none"):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return raw


def config_show(
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """Show the effective configuration (the API key as configured or not)."""
    import json

    data, path = _load()
    masked = _mask(data)
    if json_output:
        print_json(masked)
        return
    console.print_json(json.dumps(masked, ensure_ascii=False))
    console.print(f"[dim]{path}[/dim]")


def config_get(
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. server.port")],
) -> None:
    """Print one setting."""
    data, _ = _load()
    try:
        value = _get_dotted(_mask(data), key)
    except KeyError:
        from hoshimi.core.errors import ValidationError

        raise ValidationError(f"Unknown setting: {key}") from None
    print_json(value)


def config_set(
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. server.port or security.allow_cidrs")],
    value: Annotated[str, typer.Argument(help="New value. JSON is parsed (numbers, true/false, lists); 'null' clears")],
) -> None:
    """Change one setting and save it."""
    from hoshimi.core.config import Settings, save_settings, settings_from_dict, validate_settings
    from hoshimi.core.security import validate_security_settings

    data, _ = _load()
    _set_dotted(data, key, _parse_scalar(value))
    try:
        settings = settings_from_dict(data, Settings())
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(f"Invalid config: {exc}") from None
    problem = validate_settings(settings) or validate_security_settings(settings)
    if problem:
        raise typer.BadParameter(problem)
    save_settings(settings)
    shown = _get_dotted(_mask(data), key)
    console.print(f"{key} = {shown if shown is not None else '(cleared)'}")


def config_path() -> None:
    """Print the path of the config file (where it would be written)."""
    from hoshimi.core.paths import config_file_path

    print(config_file_path())
