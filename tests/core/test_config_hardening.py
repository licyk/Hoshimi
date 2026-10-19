"""The two ways a config file can be destroyed without ever being read.

The upstream record is pi-free's `config.ts` — `restrictConfigFilePermissions`
and `backupCorruptConfigFile`. Both are transcription-adjacent rather than
literal: this project's write path is `os.replace`, so the ordering of the
checks is what the tests are really about.

That ordering is the whole design. Reading happens once, at startup; writing
happens on every settings change, every credential renewal and every login.
A file that was unreadable at startup is still sitting there at write time,
and `os.replace` is the only write path here — so a save that did not look
first is the one moment this code can destroy something it never read.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from hoshimi.core.config import Settings, load_settings, save_settings

CORRUPT = '{"server": {"port": 10600},\n  "providers": {'  # truncated mid-object


def _mode(path: Path) -> int:
    """The group-and-other bits. On Windows chmod cannot express them."""
    return stat.S_IMODE(path.stat().st_mode) & 0o077


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_a_save_restricts_the_file_it_just_wrote(tmp_path: Path) -> None:
    """The file holds refresh tokens in plaintext."""
    target = save_settings(Settings(), tmp_path / "config.json")

    assert target.exists()
    if os.name != "posix":
        pytest.skip("POSIX file modes only — see the no-modes contract test")
    assert _mode(target) == 0, "the config must not be readable by anyone else"


def test_a_save_is_not_blocked_by_a_filesystem_with_no_file_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract on every platform that is not POSIX.

    Windows and some network mounts cannot express owner-only, and a config
    save is not the place to find that out. The keys are at least inside the
    user's own directory either way, which is the whole of the guarantee a
    mode would have added.
    """
    def refuse(path: object, mode: object) -> None:
        raise OSError("no such thing here")

    monkeypatch.setattr(os, "chmod", refuse)

    target = save_settings(Settings(), tmp_path / "config.json")

    assert json.loads(target.read_text(encoding="utf-8"))["server"]["port"] == 10600


def test_the_restriction_is_applied_before_the_file_is_visible(tmp_path: Path) -> None:
    """Not after. A permission set on the final path is already too late.

    The temporary is the only copy between the write and the rename, and it
    is the one a crash would leave behind.
    """
    if os.name != "posix":
        pytest.skip("POSIX file modes only")

    seen: list[int] = []

    target = tmp_path / "config.json"
    original = save_settings(Settings(), target)
    original.unlink()

    # Watch the mode the tmp file carries for as long as it exists.
    real_write = Path.write_text

    def spy(self: Path, *args: object, **kwargs: object) -> object:
        result = real_write(self, *args, **kwargs)
        if self.name.endswith(".tmp"):
            seen.append(_mode(self))
        return result

    Path.write_text = spy  # type: ignore[method-assign]
    try:
        save_settings(Settings(), target)
    finally:
        Path.write_text = real_write  # type: ignore[method-assign]

    assert seen and all(mode == 0 for mode in seen), f"the temporary was left open: {seen}"


def test_a_save_keeps_a_copy_of_a_config_it_could_not_parse(tmp_path: Path) -> None:
    """The overwrite must not be the first time this file is looked at."""
    target = _write(tmp_path / "config.json", CORRUPT)

    save_settings(Settings(), target)

    backups = list(tmp_path.glob("config.json.corrupt-*"))
    assert len(backups) == 1, "the only copy was overwritten without a copy being kept"
    assert backups[0].read_text(encoding="utf-8") == CORRUPT
    # And the file itself is now the settings that were actually passed in.
    assert json.loads(target.read_text(encoding="utf-8"))["server"]["port"] == 10600


def test_a_save_leaves_a_readable_config_alone(tmp_path: Path) -> None:
    """A backup on every save would be a litter of identical files."""
    target = save_settings(Settings(), tmp_path / "config.json")

    save_settings(Settings(), target)

    assert list(tmp_path.glob("config.json.corrupt-*")) == []


def test_an_unreadable_config_is_backed_up_before_the_startup_refusal(tmp_path: Path) -> None:
    """The gateway still refuses to start, but says where the bytes went.

    Coming up instead would be the worse answer: an install that started
    with every provider disabled looks exactly like every account had been
    lost, and the user has no way to tell the two apart.
    """
    _write(tmp_path / "config.json", CORRUPT)

    with pytest.raises(ValueError) as excinfo:
        load_settings(str(tmp_path / "config.json"))

    backups = list(tmp_path.glob("config.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == CORRUPT
    # The refusal has to name the backup, or the user is told they are stuck
    # without being told where their config went.
    assert str(backups[0]) in str(excinfo.value)


def test_a_backup_is_not_world_readable_either(tmp_path: Path) -> None:
    """It is the same credentials, in a file nobody remembers creating."""
    _write(tmp_path / "config.json", CORRUPT)

    with pytest.raises(ValueError):
        load_settings(str(tmp_path / "config.json"))

    backup = next(tmp_path.glob("config.json.corrupt-*"))
    assert backup.exists()
    if os.name == "posix":
        assert _mode(backup) == 0


def test_a_config_that_parses_starts_normally(tmp_path: Path) -> None:
    """The refusal is about a file that cannot be read, not about a strict shape."""
    _write(tmp_path / "config.json", json.dumps({"server": {"port": 10999}}))

    settings = load_settings(str(tmp_path / "config.json"))

    assert settings.port == 10999
    assert list(tmp_path.glob("config.json.corrupt-*")) == []


def test_a_config_that_is_valid_json_but_not_an_object_is_still_refused(tmp_path: Path) -> None:
    """Parsed, and still not a config — no backup, because nothing was lost."""
    target = _write(tmp_path / "config.json", "[1, 2, 3]")

    with pytest.raises(TypeError):
        load_settings(str(tmp_path / "config.json"))

    assert list(tmp_path.glob("config.json.corrupt-*")) == []
    assert target.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_a_missing_directory_is_created_before_the_first_save(tmp_path: Path) -> None:
    target = save_settings(Settings(), tmp_path / "nested" / "deeper" / "config.json")

    assert target.exists()
    if os.name == "posix":
        assert _mode(target) == 0
