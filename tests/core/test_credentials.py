"""Only Hoshimi's own enabled provider accounts enter the pool."""

from __future__ import annotations

import json
from pathlib import Path

from hoshimi.core.config import DEFAULT_PORT, ProviderConfig, Settings, load_settings, save_settings, settings_from_dict, settings_to_dict
from hoshimi.core.credentials import collect_provider_accounts
from hoshimi.core.paths import config_file_path


def test_collect_uses_enabled_configured_accounts_and_options():
    settings = Settings(
        providers={
            "cline": ProviderConfig(id="cline", accounts=[{"id": "own", "access_token": "config"}], options={"setting": "value"}),
            "trae": ProviderConfig(id="trae", enabled=False, accounts=[{"access_token": "disabled"}], options={"ignored": True}),
        }
    )
    accounts, options = collect_provider_accounts(settings)
    assert accounts == {"cline": [{"id": "own", "access_token": "config"}]}
    assert options == {"cline": {"setting": "value"}}
    accounts["cline"][0]["access_token"] = "changed"
    options["cline"]["setting"] = "changed"
    assert settings.providers["cline"].accounts[0]["access_token"] == "config"
    assert settings.providers["cline"].options["setting"] == "value"


def test_old_import_settings_and_environment_cannot_add_external_accounts(tmp_path, monkeypatch):
    home = tmp_path / "external"
    (home / "channel-pack").mkdir(parents=True)
    (home / "channel-credentials.json").write_text(json.dumps({"CLINE_ACCOUNT_AAA111": json.dumps({"access_token": "external"})}))
    (home / "channel-pack" / "state.json").write_text(json.dumps({"accounts": [{"provider": "cline", "credentialRef": "CLINE_ACCOUNT_AAA111", "enabled": True}]}))
    monkeypatch.setenv("OFM_HOME", str(home))
    settings = settings_from_dict({"import": {"ofm_home": str(home)}, "providers": {"cline": {"accounts": [{"access_token": "own"}]}}})

    def refuse_external_read(path, *args, **kwargs):
        raise AssertionError(f"Account collection must not read external files: {path}")

    monkeypatch.setattr(Path, "read_text", refuse_external_read)
    assert collect_provider_accounts(settings)[0] == {"cline": [{"access_token": "own"}]}
    assert "import" not in settings_to_dict(settings)


def test_old_filename_is_not_discovered_or_overwritten(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOSHIMI_CONFIG", raising=False)
    legacy = tmp_path / "free-model-api.json"
    original = '{"server":{"port":12345}}'
    legacy.write_text(original)
    assert not config_file_path().exists()
    assert load_settings().port == DEFAULT_PORT
    saved = save_settings(Settings())
    assert saved.name == "config.json"
    assert legacy.read_text() == original
    assert saved == config_file_path()


def test_config_ignores_working_directory_and_legacy_environment(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(data_dir))
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    original = '{"server":{"port":12345}}'
    local = workdir / "config.json"
    legacy = tmp_path / "legacy.json"
    local.write_text(original)
    legacy.write_text(original)
    monkeypatch.setenv("HOSHIMI_CONFIG", str(legacy))

    assert load_settings().port == DEFAULT_PORT
    assert not data_dir.exists()
    saved = save_settings(Settings(port=10999))
    assert saved == data_dir / "config.json"
    assert load_settings().port == 10999

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert load_settings().port == 10999
    assert local.read_text() == legacy.read_text() == original
    assert not (elsewhere / "config.json").exists()
