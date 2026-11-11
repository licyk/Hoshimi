"""The COSY client version, tracked against the latest Qoder CLI release.

The gateway hands a reduced model catalogue to clients whose COSY version lags
the current CLI, so the identity has to follow the releases feed the public
changelog reads (a GitHub-shaped API over ``QoderAI/changelog-CLI``, served by
``repo.qoder.com``). The resolved version is cached on disk and refreshed at
most once a day; every failure falls back to the bundled value, so signing never
depends on this fetch succeeding.

Ported from ``pi-provider-qoder`` ``src/cosy-version.ts``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from hoshimi.core.paths import default_data_dir

logger = logging.getLogger(__name__)

BUNDLED_GATEWAY_COSY_VERSION = "1.1.72"
QODER_CLI_RELEASES_URL = "https://repo.qoder.com/repos/QoderAI/changelog-CLI/releases?per_page=5&page=1"
CACHE_FILE = "qoder-cosy-version.json"
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
FETCH_TIMEOUT_SECONDS = 5.0

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def is_cli_version(value: Any) -> bool:
    return isinstance(value, str) and bool(_VERSION_RE.match(value))


def compare_cli_versions(a: str, b: str) -> int:
    """Numeric x.y.z comparison; equal versions return 0."""
    left = [int(part) for part in a.split(".")]
    right = [int(part) for part in b.split(".")]
    for index in range(3):
        if left[index] != right[index]:
            return left[index] - right[index]
    return 0


def cache_path() -> Path:
    return default_data_dir() / CACHE_FILE


class CosyVersion:
    """The version to sign with now, plus the throttled refresh behind it.

    ``get`` is synchronous and network-free so a request never waits on the
    releases feed; ``refresh`` is called before a catalogue fetch, which is the
    one request whose answer the version actually gates.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_path()
        self._lock = threading.Lock()
        self._memo: dict[str, Any] | None = None
        self._loaded = False

    def _read(self) -> dict[str, Any] | None:
        with self._lock:
            if self._loaded:
                return self._memo
            self._loaded = True
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._memo = None
                return None
            self._memo = raw if isinstance(raw, dict) else None
            return self._memo

    def _write(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._memo = data
            self._loaded = True
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except OSError:
                # A read-only data directory must not break requests; the
                # in-memory copy still serves this process.
                pass

    def get(self) -> str:
        cached = (self._read() or {}).get("version")
        if is_cli_version(cached) and compare_cli_versions(str(cached), BUNDLED_GATEWAY_COSY_VERSION) >= 0:
            return str(cached)
        return BUNDLED_GATEWAY_COSY_VERSION

    def refresh(self, client: httpx.Client, *, force: bool = False, now: float | None = None) -> str:
        """Fetch the newest release tag when the daily window has opened.

        Never raises and never downgrades the identity: offline, a bad payload
        or an older tag all keep the version currently in use.
        """
        stamp = time.time() if now is None else now
        data = self._read() or {}
        checked_at = data.get("checkedAt")
        if not force and isinstance(checked_at, (int, float)) and stamp - checked_at < REFRESH_INTERVAL_SECONDS:
            return self.get()
        try:
            response = client.get(QODER_CLI_RELEASES_URL, headers={"Accept": "application/vnd.github.v3+json"}, timeout=FETCH_TIMEOUT_SECONDS)
            if response.status_code >= 400:
                return self.get()
            releases = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("qoder: CLI release feed unavailable (%s)", exc)
            return self.get()
        if not isinstance(releases, list):
            return self.get()
        latest = next(
            (row.get("tag_name") for row in releases if isinstance(row, dict) and not row.get("draft") and not row.get("prerelease") and is_cli_version(row.get("tag_name"))),
            None,
        )
        current = self.get()
        if isinstance(latest, str) and compare_cli_versions(latest, current) > 0:
            self._write({"version": latest, "checkedAt": stamp})
            return latest
        self._write({"version": data.get("version"), "checkedAt": stamp})
        return current