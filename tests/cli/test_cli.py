"""The command line: the command tree, --debug everywhere, and the config round-trip."""

from __future__ import annotations

import json
import logging

import pytest
from typer.testing import CliRunner

from hoshimi.cli.app import get_app
from hoshimi.core.paths import config_file_path
from hoshimi.logger import LOGGER_NAME

EXPECTED_TREE = {
    "check": None,
    "config": {"get": None, "path": None, "set": None, "show": None},
    "env": None,
    "login": None,
    "models": None,
    "providers": None,
    "serve": None,
    "stations": None,
    "version": None,
}


def _tree(group):
    import typer

    click_group = typer.main.get_command(group) if not hasattr(group, "commands") else group
    out = {}
    for name, cmd in getattr(click_group, "commands", {}).items():
        out[name] = _tree(cmd) if getattr(cmd, "commands", None) else None
    return out


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    # --debug sets the application logger to DEBUG for the whole process; reset it so one test's
    # log lines cannot end up in another's captured output.
    logger = logging.getLogger(LOGGER_NAME)
    level = logger.level
    yield CliRunner()
    logger.setLevel(level)


def test_command_tree_snapshot():
    assert _tree(get_app()) == EXPECTED_TREE


@pytest.mark.parametrize("args", [["--debug", "env"], ["env", "--debug"], ["config", "--debug", "show"], ["config", "show", "--debug"]])
def test_debug_option_everywhere(runner, args):
    result = runner.invoke(get_app(), args)
    assert result.exit_code == 0, result.output


def test_help_is_alphabetical(runner):
    out = runner.invoke(get_app(), ["--help"]).output
    names = [line.split()[0] for line in out.split("Commands:")[1].strip().splitlines() if line.startswith("  ") and not line.startswith("   ")]
    assert names == sorted(names)


def test_version_and_env_json(runner):
    version = json.loads(runner.invoke(get_app(), ["version", "--json"]).output)
    assert version["hoshimi"] and version["python"]
    env = json.loads(runner.invoke(get_app(), ["env", "--json"]).output)
    assert "HOSHIMI_DATA_DIR" in env["known"]
    assert "HOSHIMI_CONFIG" not in env["known"]


@pytest.mark.parametrize("command", ["serve", "check"])
def test_external_config_import_option_is_removed(runner, command):
    result = runner.invoke(get_app(), [command, "--ofm-home", "/external"])
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_env_does_not_advertise_or_read_external_config_directory(runner, monkeypatch):
    monkeypatch.setenv("OFM_HOME", "/external")
    result = runner.invoke(get_app(), ["env", "--json"])
    payload = json.loads(result.output)
    assert "OFM_HOME" not in payload["known"]
    assert "OFM_HOME" not in payload["set"]


def test_config_roundtrip_and_unknown_key(runner, tmp_path):
    assert runner.invoke(get_app(), ["config", "set", "server.port", "8001"]).exit_code == 0
    assert json.loads(runner.invoke(get_app(), ["config", "get", "server.port"]).output) == 8001
    assert config_file_path() == tmp_path / "data" / "config.json"
    assert config_file_path().is_file()
    assert not (tmp_path / "config.json").exists()
    path_result = runner.invoke(get_app(), ["config", "path"])
    assert path_result.exit_code == 0
    assert path_result.output.strip() == str(config_file_path())

    from hoshimi.core.errors import ValidationError

    result = runner.invoke(get_app(), ["config", "get", "nope"])
    assert isinstance(result.exception, ValidationError) and result.exception.exit_code == 4


def test_model_api_key_is_settable_and_masked(runner):
    assert runner.invoke(get_app(), ["config", "set", "server.model_api_key", "lan-only"]).exit_code == 0
    shown = runner.invoke(get_app(), ["config", "get", "server.model_api_key"]).output
    assert json.loads(shown) == "***"
    printed = json.loads(runner.invoke(get_app(), ["config", "show", "--json"]).output)
    assert printed["server"]["model_api_key"] == "***"


def test_check_without_accounts_exits_nonzero(runner, tmp_path):
    # The free lanes are on by default; this checks the "nothing enabled" world.
    _write_config({"providers": {"kilo": {"enabled": False}, "opencode": {"enabled": False}}})
    result = runner.invoke(get_app(), ["check"])
    assert result.exit_code == 2


def test_login_without_argument_lists_stations(runner):
    result = runner.invoke(get_app(), ["login"])
    assert result.exit_code == 0, result.output
    assert "cline" in result.output
    assert "zcode" in result.output


def test_login_unknown_station_exits_two(runner):
    result = runner.invoke(get_app(), ["login", "nope"])
    assert result.exit_code == 2, result.output
    assert "nope" in result.output


def test_models_and_providers_table(runner):
    # Only the free lanes would answer here, so disable them: this asserts that
    # a directory with no provider at all still answers both commands.
    _write_config({"providers": {"kilo": {"enabled": False}, "opencode": {"enabled": False}}})
    models = runner.invoke(get_app(), ["models", "--json"])
    assert models.exit_code == 0
    assert json.loads(models.output) == []
    providers = runner.invoke(get_app(), ["providers"])
    assert providers.exit_code == 0


def test_free_lanes_are_served_without_configuration(runner, monkeypatch):
    """The default world: keyless free lanes, no config file at all."""
    _offline_lanes(monkeypatch)
    result = runner.invoke(get_app(), ["providers", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["providers"]
    by_id = {row["id"]: row for row in payload}
    assert {"kilo", "opencode"} <= set(by_id)
    assert by_id["kilo"]["keyless"] is True
    assert by_id["kilo"]["accounts"] == []


def _offline_lanes(monkeypatch) -> None:
    # Discovery would hit the network; the roster itself is asserted in tests/core.
    from hoshimi.core.providers.kilo import KiloProvider
    from hoshimi.core.providers.opencode import OpencodeProvider

    monkeypatch.setattr(KiloProvider, "fetch_models", lambda self: [])
    monkeypatch.setattr(OpencodeProvider, "fetch_models", lambda self: [])


def _write_config(payload: dict) -> None:
    target = config_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("command", [
    ["serve"], ["check"], ["login"], ["models"], ["providers"],
    ["config", "show"], ["config", "get", "server.port"],
    ["config", "set", "server.port", "10600"], ["config", "path"],
])
def test_config_file_override_option_is_removed(runner, command):
    result = runner.invoke(get_app(), [*command, "--config", "/external/config.json"])
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_config_path_is_absolute_before_first_save(runner):
    assert not config_file_path().exists()
    result = runner.invoke(get_app(), ["config", "path"])
    assert result.exit_code == 0
    assert result.output.strip() == str(config_file_path())
    assert not config_file_path().exists()
