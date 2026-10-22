"""Whether a newer Hoshimi is published, asked of PyPI in the background.

``pip install -U hoshimi`` is how an install moves forward, so PyPI's own
listing is the one that answers. The check never sits in front of anything:
it runs on a daemon thread once the server is up and again once a day, and a
request only ever reads the last answer. No network, a proxy that refuses,
an answer that cannot be read: each leaves the last good answer standing
(or says the check failed, when there never was one) and is retried at the
next round. Nothing is downloaded or installed.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from hoshimi.core.net.client import NetworkClient, operation
from hoshimi.core.net.policy import NetworkPolicy
from hoshimi.version import VERSION

logger = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/hoshimi/json"
FETCH_TIMEOUT = 10.0
CHECK_INTERVAL_SECONDS = 24 * 3600
"""A server left running for weeks still hears about a release within a day."""

UpdateStatus = Literal["checking", "available", "current", "failed", "disabled"]

_RELEASE = re.compile(r"v?(\d+(?:\.\d+)*)")


def release_version(text: object) -> tuple[int, ...] | None:
    """``"0.3.4"`` as ``(0, 3, 4)``; None for anything but a final release (``1.0rc1``, ``0.4.dev2``)."""
    match = _RELEASE.fullmatch(str(text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a later final release than ``current`` (``0.3`` equals ``0.3.0``)."""
    new, old = release_version(candidate), release_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def latest_release(payload: object) -> str | None:
    """The newest final release in a PyPI project document.

    ``info.version`` is the newest release PyPI would install, which is a
    pre-release only when nothing else exists; the release list is read too,
    so a yanked newest release (every file of it yanked) is passed over.
    """
    if not isinstance(payload, dict):
        return None
    releases = payload.get("releases")
    if isinstance(releases, dict):
        live = [
            version for version, files in releases.items()
            if release_version(version) is not None
            and not (isinstance(files, list) and files and all(isinstance(f, dict) and f.get("yanked") for f in files))
        ]
        if live:
            return max(live, key=lambda version: release_version(version) or ())
    info = payload.get("info")
    version = info.get("version") if isinstance(info, dict) else None
    return str(version) if release_version(version) is not None else None


@dataclass(frozen=True)
class UpdateState:
    """The last answer: ``latest`` is None until a check has succeeded."""

    status: UpdateStatus
    current: str
    latest: str | None = None
    checked_at: float | None = None


class UpdateChecker:
    """Asks PyPI for the newest release on a timer; ``state`` is the last answer."""

    def __init__(
        self, *, current: str = VERSION, url: str = PYPI_URL, timeout: float = FETCH_TIMEOUT,
        interval: float = CHECK_INTERVAL_SECONDS, network: NetworkPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.current = current
        self.url = url
        self.timeout = float(timeout)
        self.interval = float(interval)
        self.network = network or NetworkPolicy()
        self.transport = transport
        self._lock = threading.Lock()
        self._state = UpdateState(status="checking", current=current)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> UpdateState:
        with self._lock:
            return self._state

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hoshimi-updates", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.check_now()
            if self._stop.wait(self.interval):
                return

    def check_now(self) -> UpdateState:
        """One round; a failure keeps the last good answer, if there was one."""
        try:
            latest = latest_release(self._fetch())
            if latest is None:
                raise ValueError("no final release listed")
        except Exception as exc:
            logger.debug("update check failed: %s", exc, exc_info=True)
            with self._lock:
                if self._state.latest is None:
                    self._state = UpdateState(status="failed", current=self.current, checked_at=time.time())
                return self._state
        status: UpdateStatus = "available" if is_newer(latest, self.current) else "current"
        with self._lock:
            self._state = UpdateState(status=status, current=self.current, latest=latest, checked_at=time.time())
        if status == "available":
            logger.info("Hoshimi %s is available (running %s)", latest, self.current)
        return self.state

    def _fetch(self) -> Any:
        with (
            NetworkClient(provider="pypi", network=self.network, timeout=self.timeout, transport=self.transport) as client,
            operation("update-check"),
        ):
            response = client.get(self.url, headers={"Accept": "application/json", "User-Agent": f"hoshimi/{self.current}"}, follow_redirects=True)
            response.raise_for_status()
            return response.json()
