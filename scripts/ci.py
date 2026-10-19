"""Decide whether CI publishes the distributions built by this push.

Use the checked-out Git history rather than the compare API: the first push
to a branch has an all-zero ``before`` SHA, which the API cannot compare.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = "hoshimi/version.py"
RELEASE_REFS = {"refs/heads/main", "refs/heads/hoshimi_main"}


def git(root: Path, *args: str) -> str:
    """Read Git history, failing the plan if it cannot be inspected."""
    return subprocess.check_output(["git", *args], cwd=root, stdin=subprocess.DEVNULL, text=True).strip()


def release_requested(event_name: str, ref: str, event: dict[str, Any], root: Path = ROOT) -> bool:
    """Release version changes on release branches, or a pushed version tag."""
    if event_name != "push" or event.get("deleted"):
        return False
    if ref.startswith("refs/tags/v"):
        return True
    if ref not in RELEASE_REFS:
        return False

    head = event["after"]
    base = event.get("before", "")
    if not base.strip("0"):
        # New branches compare against the parent of the oldest pushed commit.
        # If the payload has no commits, compare the entire tree to include the
        # initial version file rather than silently missing the first release.
        commits = event.get("commits") or []
        parents = git(root, "rev-list", "--parents", "-n", "1", commits[0]["id"]).split()[1:] if commits else []
        base = parents[0] if parents else git(root, "hash-object", "-t", "tree", "--stdin")

    changed = git(root, "diff", "--name-only", "--no-renames", base, head, "--", VERSION_FILE)
    return VERSION_FILE in changed.splitlines()


def main() -> None:
    event_name = os.environ["GITHUB_EVENT_NAME"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    values = {"release": release_requested(event_name, os.environ["GITHUB_REF"], event)}
    print(json.dumps(values))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"release={str(values['release']).lower()}\n")


if __name__ == "__main__":
    main()
