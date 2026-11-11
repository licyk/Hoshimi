"""Per-account node discovery, and the rotation that survives a daily limit.

Qoder publishes a node map at ``{center}/algo/api/v3/service/region/endpoints``,
encoded with the same custom Base64 as request bodies. It exposes several node
classes; the two that matter here are ``inferNodes`` (where the catalogue lives)
and ``dataNodeMap.remote_agent`` (where the official CLI drives agent chat).

Sending agent chat to the inference node gets ``403 code 110 "Billing daily
count exceeded"`` for accounts whose daily chat allowance is tracked per node,
even while the same account succeeds through the CLI. So: keep the inference
node for the catalogue, prefer the remote-agent node for chat, and walk the
remaining candidates when a node refuses. The node that served a request is
persisted for an hour so a restart does not repeat the failing route.

Ported from ``pi-provider-qoder`` ``src/endpoints.ts``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from hoshimi.core.paths import default_data_dir
from hoshimi.core.qoder.cosy import CosyCredentials, build_auth_headers
from hoshimi.core.qoder.encoding import decode_body
from hoshimi.core.qoder.region import ChatEndpointOverride, QoderRegion, normalize_base_url, region_endpoints_url

logger = logging.getLogger(__name__)

CACHE_FILE = "qoder-endpoints.json"
CACHE_VERSION = 1
CACHE_TTL_SECONDS = 60 * 60
FETCH_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class RegionEndpoints:
    """The node classes the map publishes, each as normalized base URLs."""

    infer_nodes: tuple[str, ...] = ()
    remote_agent_nodes: tuple[str, ...] = ()
    security_nodes: tuple[str, ...] = ()
    center_nodes: tuple[str, ...] = ()


def _string_array(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = normalize_base_url(item)
        if normalized:
            out.append(normalized)
    return tuple(out)


def parse_region_endpoints(payload: Any) -> RegionEndpoints | None:
    """Read the map; None when it names no node at all."""
    if not isinstance(payload, dict):
        return None
    data_nodes = payload.get("dataNodeMap")
    data_nodes = data_nodes if isinstance(data_nodes, dict) else {}
    endpoints = RegionEndpoints(
        infer_nodes=_string_array(payload.get("inferNodes")),
        remote_agent_nodes=_string_array(data_nodes.get("remote_agent")),
        security_nodes=_string_array(payload.get("security")),
        center_nodes=_string_array(payload.get("centerNodes")),
    )
    if not (endpoints.infer_nodes or endpoints.remote_agent_nodes or endpoints.security_nodes):
        return None
    return endpoints


def chat_endpoint_candidates(endpoints: RegionEndpoints | None, fallback: str) -> list[str]:
    """Ordered, de-duplicated chat nodes: current first, then remote-agent, infer, security."""
    seen: set[str] = set()
    out: list[str] = []

    def push(url: str | None) -> None:
        normalized = normalize_base_url(url or "")
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        out.append(normalized)

    push(fallback)
    for url in (endpoints.remote_agent_nodes if endpoints else ()):
        push(url)
    for url in (endpoints.infer_nodes if endpoints else ()):
        push(url)
    for url in (endpoints.security_nodes if endpoints else ()):
        push(url)
    return out


def cache_path() -> Path:
    return default_data_dir() / CACHE_FILE


@dataclass
class _CacheEntry:
    chat_base_url: str
    updated_at: float


class EndpointResolver:
    """Resolves, remembers and rotates the chat node for one region.

    The in-memory map and the on-disk cache are both best-effort: a failure
    leaves the lane on its configured base URL, which is what the plugin does
    when discovery is unavailable.
    """

    def __init__(
        self,
        region: QoderRegion,
        client: httpx.Client,
        *,
        override: ChatEndpointOverride | None = None,
        path: Path | None = None,
        version_provider: Callable[[], str] | None = None,
    ) -> None:
        self.region = region
        self.client = client
        self.override = override if override is not None else ChatEndpointOverride()
        self.path = path or cache_path()
        self._version_provider = version_provider
        self._lock = threading.Lock()
        self._discovered: RegionEndpoints | None = None
        self._discovered_at = 0.0
        self._file_cache: dict[str, _CacheEntry] | None = None
        self._credentials: CosyCredentials | None = None
        self._resolved = False

    def cosy_version(self) -> str:
        from hoshimi.core.qoder.cosy_version import BUNDLED_GATEWAY_COSY_VERSION

        return self._version_provider() if self._version_provider is not None else BUNDLED_GATEWAY_COSY_VERSION

    # -- the persisted node ------------------------------------------------ #
    def _read_file(self) -> dict[str, _CacheEntry]:
        with self._lock:
            if self._file_cache is not None:
                return self._file_cache
            entries: dict[str, _CacheEntry] = {}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = None
            modes = raw.get("modes") if isinstance(raw, dict) else None
            if isinstance(modes, dict):
                for key, value in modes.items():
                    if not isinstance(value, dict):
                        continue
                    url = normalize_base_url(str(value.get("chatBaseUrl") or ""))
                    updated = value.get("updatedAt")
                    if url and isinstance(updated, (int, float)):
                        entries[key] = _CacheEntry(url, float(updated))
            self._file_cache = entries
            return entries

    def _write_file(self, entries: dict[str, _CacheEntry]) -> None:
        with self._lock:
            self._file_cache = entries
            payload = {
                "version": CACHE_VERSION,
                "modes": {key: {"chatBaseUrl": row.chat_base_url, "updatedAt": row.updated_at} for key, row in entries.items()},
            }
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except OSError:
                pass  # a read-only data directory must not break chat

    def cached_chat_base_url(self, now: float | None = None) -> str | None:
        stamp = time.time() if now is None else now
        entry = self._read_file().get(self.region.mode)
        if entry is None or stamp - entry.updated_at > CACHE_TTL_SECONDS:
            return None
        return entry.chat_base_url

    def remember(self, chat_base_url: str, now: float | None = None) -> None:
        """Persist the node that just served a request."""
        normalized = normalize_base_url(chat_base_url)
        if not normalized:
            return
        entries = dict(self._read_file())
        entries[self.region.mode] = _CacheEntry(normalized, time.time() if now is None else now)
        self._write_file(entries)

    def apply_cached(self) -> str | None:
        cached = self.cached_chat_base_url()
        if cached:
            self.override.set(self.region, cached)
        return cached

    def clear(self) -> None:
        with self._lock:
            self._discovered = None
            self._discovered_at = 0.0
        self._write_file({})
        self.override.clear()

    # -- discovery --------------------------------------------------------- #
    def fetch(self, credentials: CosyCredentials) -> RegionEndpoints | None:
        url = region_endpoints_url(self.region)
        try:
            headers = build_auth_headers(None, url, credentials, cosy_version=self.cosy_version())
            headers["Accept"] = "application/json"
            response = self.client.get(url, headers=headers, timeout=FETCH_TIMEOUT_SECONDS)
            if response.status_code >= 400:
                return None
            text = response.text
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("qoder: region/endpoints unavailable (%s)", exc)
            return None
        try:
            payload = json.loads(text)
        except ValueError:
            try:
                payload = json.loads(decode_body(text))
            except ValueError:
                return None
        return parse_region_endpoints(payload)

    def endpoints(self, credentials: CosyCredentials | None = None) -> RegionEndpoints | None:
        with self._lock:
            if self._discovered is not None and time.time() - self._discovered_at <= CACHE_TTL_SECONDS:
                return self._discovered
            if credentials is not None:
                self._credentials = credentials
            creds = self._credentials
        if creds is None:
            return None
        fetched = self.fetch(creds)
        with self._lock:
            if fetched is None:
                return self._discovered
            self._discovered = fetched
            self._discovered_at = time.time()
            return fetched

    def refresh(self, credentials: CosyCredentials) -> str | None:
        """Prefer the remote-agent node, persist it, keep the current one on failure."""
        if not credentials.auth_token:
            return None
        endpoints = self.endpoints(credentials)
        if endpoints is None:
            return None
        candidates = chat_endpoint_candidates(endpoints, self.region.base_url)
        preferred = next((url for url in candidates if url in endpoints.remote_agent_nodes), candidates[0] if candidates else None)
        if not preferred:
            return None
        self.override.set(self.region, preferred)
        self.remember(preferred)
        return preferred

    def ensure(self, credentials: CosyCredentials) -> None:
        """Resolve the chat node once per instance; later turns reuse it.

        A rotation that served a request stays in force for the rest of this
        process: re-preferring the remote-agent node on every turn would undo
        the one route that worked. A failed discovery is retried next turn.
        """
        with self._lock:
            if self._resolved:
                return
        preferred = self.refresh(credentials)
        with self._lock:
            self._resolved = preferred is not None or self._discovered is not None

    def rotate(self, attempted: set[str], credentials: CosyCredentials | None = None) -> str | None:
        """Switch to the next untried node; ``attempted`` is owned by the caller."""
        current = normalize_base_url(self.override.get(self.region))
        if current:
            attempted.add(current)
        endpoints = self.endpoints(credentials)
        candidates = chat_endpoint_candidates(endpoints, self.region.base_url)
        nxt = next((url for url in candidates if url not in attempted), None)
        if nxt is None:
            return None
        attempted.add(nxt)
        self.override.set(self.region, nxt)
        return nxt

    def chat_base_url(self) -> str:
        return self.override.get(self.region)
