"""Bounded, shared catalogue work, isolated from request-handler worker threads."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.providers.base import Provider


@dataclass
class DiscoveryResult:
    specs: dict[str, ModelSpec]
    failed: bool = False


class CatalogDiscovery:
    """Share in-flight work per provider object, never across rebuilt account pools."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="catalog")
        self._lock = threading.Lock()
        self._pending: WeakKeyDictionary[Provider, Future[DiscoveryResult]] = WeakKeyDictionary()
        self._cache: WeakKeyDictionary[Provider, DiscoveryResult] = WeakKeyDictionary()

    def cached(self, provider: Provider) -> DiscoveryResult:
        with self._lock:
            return self._cache.get(provider, DiscoveryResult({}))

    def submit(self, provider: Provider) -> Future[DiscoveryResult]:
        with self._lock:
            pending = self._pending.get(provider)
            if pending is None or pending.done():
                pending = self._pool.submit(self._load, provider)
                self._pending[provider] = pending
            return pending

    def _load(self, provider: Provider) -> DiscoveryResult:
        try:
            if provider.catalog_failed:
                provider._models_expires_at = 0.0
            specs = provider.specs()
            failed = provider.catalog_failed
        except Exception:  # noqa: BLE001 - one adapter must not abort catalogue discovery
            specs, failed = {}, True
        with self._lock:
            previous = self._cache.get(provider)
            if failed and previous is not None:
                specs = previous.specs
            result = DiscoveryResult(specs, failed)
            self._cache[provider] = result
        return result

    def close(self) -> None:
        # A timed-out synchronous request cannot be killed safely. Do not wait for
        # it on disconnect/shutdown, and do not create another worker on retries.
        self._pool.shutdown(wait=False, cancel_futures=True)
