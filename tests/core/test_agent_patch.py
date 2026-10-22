"""apply_patch: Codex's own scenarios (``tests/fixtures/apply_patch``, from codex-rs/apply-patch, Apache-2.0), and the parser's edges."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hoshimi.core.agent.patch import PatchError, apply_patch, parse_patch, seek_sequence

SCENARIOS = Path(__file__).resolve().parent.parent / "fixtures" / "apply_patch"
# Codex's library writes hunk by hunk, so its scenario keeps the first file; the tool here (like Codex's
# own tool) works everything out first, and a patch that fails anywhere writes nothing.
ALL_OR_NOTHING = {"015_failure_after_partial_success_leaves_changes"}


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


@pytest.mark.parametrize("scenario", sorted(p.name for p in SCENARIOS.iterdir() if p.is_dir()))
def test_codex_scenarios(scenario: str, tmp_path: Path) -> None:
    source = SCENARIOS / scenario
    if (source / "input").is_dir():
        shutil.copytree(source / "input", tmp_path, dirs_exist_ok=True)
    before = _snapshot(tmp_path)
    try:
        apply_patch((source / "patch.txt").read_text(encoding="utf-8"), tmp_path)
    except PatchError:
        pass
    if scenario in ALL_OR_NOTHING:
        assert _snapshot(tmp_path) == before
    else:
        assert _snapshot(tmp_path) == _snapshot(source / "expected")


def test_the_summary_lists_added_modified_and_deleted(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("bye\n")
    (tmp_path / "m.txt").write_text("a\nb\nc\n")
    patch = "*** Begin Patch\n*** Delete File: old.txt\n*** Update File: m.txt\n@@\n a\n-b\n+B\n*** Add File: new/n.txt\n+hi\n*** End Patch\n"
    assert apply_patch(patch, tmp_path).summary() == "Success. Updated the following files:\nA new/n.txt\nM m.txt\nD old.txt\n"
    assert (tmp_path / "m.txt").read_text() == "a\nB\nc\n"


def test_a_heredoc_is_unwrapped() -> None:
    hunks = parse_patch("<<'EOF'\n*** Begin Patch\n*** Add File: a\n+x\n*** End Patch\nEOF\n")
    assert len(hunks) == 1


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ("bad", "invalid patch: The first line of the patch must be '*** Begin Patch'"),
        ("*** Begin Patch\nbad", "invalid patch: The last line of the patch must be '*** End Patch'"),
        ("*** Begin Patch\n*** Frobnicate File: foo\n*** End Patch", "invalid hunk at line 2, '*** Frobnicate File: foo' is not a valid hunk header."),
        ("*** Begin Patch\n*** Update File: test.py\n*** End Patch", "invalid hunk at line 2, Update file hunk for path 'test.py' is empty"),
        ("*** Begin Patch\n*** Update File: a\n@@\nbad line\n*** End Patch", "invalid hunk at line 4, Unexpected line found in update hunk: 'bad line'"),
    ],
)
def test_parse_errors_say_what_is_wrong(patch: str, message: str) -> None:
    with pytest.raises(PatchError, match="^" + message.replace("*", r"\*").replace("(", r"\(").replace(")", r"\)").replace("{", r"\{").replace("}", r"\}")):
        parse_patch(patch)


def test_seek_sequence_gets_less_strict_in_steps() -> None:
    lines = ["foo   ", "  bar\t", "“quoted” — dash x"]
    assert seek_sequence(lines, ["foo"], 0, False) == 0
    assert seek_sequence(lines, ["bar"], 0, False) == 1
    assert seek_sequence(lines, ['"quoted" - dash x'], 0, False) == 2
    assert seek_sequence(lines, ["missing"], 0, False) is None
    assert seek_sequence(["a"], ["a", "b"], 0, False) is None
    assert seek_sequence(["x", "y", "x"], ["x"], 0, True) == 2


def test_a_context_line_jumps_to_the_right_place(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("def a():\n    return 1\n\ndef b():\n    return 1\n")
    patch = "*** Begin Patch\n*** Update File: f.py\n@@ def b():\n-    return 1\n+    return 2\n*** End Patch"
    apply_patch(patch, tmp_path)
    assert (tmp_path / "f.py").read_text() == "def a():\n    return 1\n\ndef b():\n    return 2\n"


def test_a_missing_line_fails_and_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\n")
    patch = "*** Begin Patch\n*** Add File: b.txt\n+new\n*** Update File: a.txt\n@@\n-two\n+three\n*** End Patch"
    with pytest.raises(PatchError, match="Failed to find expected lines in a.txt:\ntwo"):
        apply_patch(patch, tmp_path)
    assert not (tmp_path / "b.txt").exists()


def test_two_operations_on_one_file_are_refused(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\n")
    patch = "*** Begin Patch\n*** Update File: a.txt\n@@\n-one\n+two\n*** Delete File: a.txt\n*** End Patch"
    with pytest.raises(PatchError, match="multiple operations target a.txt"):
        apply_patch(patch, tmp_path)
