"""The documentation site keeps up with the code: every command, environment variable, config key and provider is on its page, in both languages.

The site (``site/content/docs``) is the documentation. A command, a setting or a provider added
without its page fails here, so the reference pages stay complete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoshimi.core.config import Settings, settings_to_dict
from hoshimi.core.provider_definitions import CANONICAL_PROVIDERS

DOCS = Path(__file__).resolve().parent.parent / "site" / "content" / "docs"
LANGUAGES = (".mdx", ".en.mdx")


def page(slug: str, language: str) -> str:
    return (DOCS / f"{slug}{language}").read_text(encoding="utf-8")


def commands() -> list[str]:
    """Every command as the reference names it: ``serve``, ``config set``…"""
    # Imported here, not at collection: the command line sets up logging and its login flows as it loads.
    import typer

    from hoshimi.cli.app import get_app

    root = typer.main.get_command(get_app())
    names: list[str] = []
    for name, command in root.commands.items():  # type: ignore[attr-defined]
        sub = getattr(command, "commands", None)
        names += [f"{name} {s}" for s in sub] if sub else [name]
    return names


def config_keys() -> list[str]:
    """Every key of config.json outside ``providers``, with each optional one set so it is written."""
    settings = Settings(
        access_token="x", model_api_key="x", default_model="m", playground_system_prompt="p", playground_title_model="t",
        playground_vision_model="off", playground_tools=["read"], playground_workspace="w", playground_approval="auto",
        playground_search_url="http://search",
    )
    data = settings_to_dict(settings)
    return [f"{section}.{key}" for section, values in data.items() if isinstance(values, dict) and section != "providers" for key in values]


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_command_has_its_section(language: str) -> None:
    text = page("cli-commands", language)
    assert [c for c in commands() if f"### `{c}`" not in text] == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_environment_variable_is_listed(language: str) -> None:
    from hoshimi.cli.commands.system import ENV_VARS

    text = page("cli-env", language)
    assert [name for name in ENV_VARS if f"`{name}`" not in text] == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_config_key_is_listed(language: str) -> None:
    keys = config_keys()
    assert "playground.approval" in keys and "server.port" in keys
    text = page("config", language)
    assert [key for key in keys if f"`{key}`" not in text] == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_provider_is_on_a_providers_page(language: str) -> None:
    files = [p for p in DOCS.glob(f"providers*{language}") if language == ".en.mdx" or not p.name.endswith(".en.mdx")]
    text = "".join(p.read_text(encoding="utf-8") for p in files)
    assert [d.id for d in CANONICAL_PROVIDERS if d.implemented and f"`{d.id}`" not in text] == []
