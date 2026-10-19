"""Release planning must handle the first push without a compare API request."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import RELEASE_REFS, VERSION_FILE, release_requested

ZERO_SHA = "0" * 40


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def commit(root: Path, path: str, contents: str) -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    git(root, "add", "--", path)
    git(root, "-c", "user.name=CI Test", "-c", "user.email=ci@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", "change")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path):
    git(tmp_path, "init", "--quiet")
    first = commit(tmp_path, VERSION_FILE, 'VERSION = "0.1.0"\n')
    feature = commit(tmp_path, "feature.txt", "feature\n")
    version = commit(tmp_path, VERSION_FILE, 'VERSION = "0.2.0"\n')
    return tmp_path, first, feature, version


@pytest.mark.parametrize("ref", sorted(RELEASE_REFS))
def test_version_change_releases_on_each_release_branch(history, ref):
    root, first, feature, version = history
    assert release_requested("push", ref, {"before": feature, "after": version}, root)
    assert not release_requested("push", ref, {"before": first, "after": feature}, root)


def test_first_push_includes_version_changes_across_all_pushed_commits(history):
    root, first, feature, _ = history
    event = {"before": ZERO_SHA, "after": feature, "commits": [{"id": first}, {"id": feature}]}
    assert release_requested("push", "refs/heads/main", event, root)


def test_new_branch_does_not_release_when_only_a_feature_was_pushed(history):
    root, _, feature, _ = history
    event = {"before": ZERO_SHA, "after": feature, "commits": [{"id": feature}]}
    assert not release_requested("push", "refs/heads/hoshimi_main", event, root)


def test_first_push_without_commit_details_checks_the_initial_tree(history):
    root, _, feature, _ = history
    assert release_requested("push", "refs/heads/main", {"before": ZERO_SHA, "after": feature}, root)


def test_reverted_version_is_not_a_release(history):
    root, _, feature, _ = history
    reverted = commit(root, VERSION_FILE, 'VERSION = "0.1.0"\n')
    assert not release_requested("push", "refs/heads/main", {"before": feature, "after": reverted}, root)


@pytest.mark.parametrize("event_name,ref,event,expected", [
    ("push", "refs/tags/v0.2.0", {}, True),
    ("push", "refs/tags/other", {}, False),
    ("push", "refs/heads/feature", {}, False),
    ("pull_request", "refs/pull/1/merge", {}, False),
    ("workflow_dispatch", "refs/heads/main", {}, False),
    ("push", "refs/heads/main", {"deleted": True}, False),
])
def test_only_release_pushes_publish(event_name, ref, event, expected):
    assert release_requested(event_name, ref, event) is expected


def test_plan_command_writes_boolean_output(tmp_path, monkeypatch):
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps({}), encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v0.2.0")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    result = subprocess.run([sys.executable, "scripts/ci.py"], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == {"release": True}
    assert output.read_text() == "release=true\n"
