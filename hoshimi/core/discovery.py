"""Bounded, shared catalogue work, isolated from request-handler worker threads."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.net.catalog_cache import persist, restore
from hoshimi.core.net.client import operation
from hoshimi.core.providers.base import Provider


@dataclass
class DiscoveryResult:
    specs: dict[str, ModelSpec]
    failed: bool = False
    error: str | None = None
    last_success: float | None = None
    retry_at: float | None = None
    source: str = "live"
    diagnostic: dict[str, Any] | None = None


class CatalogDiscovery:
    """Share in-flight work per provider object, never across rebuilt account pools."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="catalog")
        self._lock = threading.Lock()
        self._pending: WeakKeyDictionary[Provider, Future[DiscoveryResult]] = WeakKeyDictionary()
        self._cache: WeakKeyDictionary[Provider, DiscoveryResult] = WeakKeyDictionary()
        self._restored: WeakKeyDictionary[Provider, bool] = WeakKeyDictionary()

    def _restore(self, provider: Provider) -> None:
        if provider not in self._restored:
            self._restored[provider] = True
            if provider._models_cache is None:
                specs, timestamp = restore(provider)
                if timestamp is not None:
                    self._cache[provider] = DiscoveryResult(specs, last_success=timestamp, source="disk")

    def cached(self, provider: Provider) -> DiscoveryResult:
        with self._lock:
            self._restore(provider)
            return self._cache.get(provider, DiscoveryResult({}))

    def submit(self, provider: Provider) -> Future[DiscoveryResult]:
        with self._lock:
            self._restore(provider)
            pending = self._pending.get(provider)
            if pending is not None and pending.done() and provider.catalog_retry_at > time.time():
                return pending
            if pending is None or pending.done():
                pending = self._pool.submit(self._load, provider)
                self._pending[provider] = pending
            return pending

    def _load(self, provider: Provider) -> DiscoveryResult:
        provider._catalog_started_at = time.time()
        try:
            if provider.catalog_failed and provider.catalog_retry_at <= time.time():
                provider._models_expires_at = 0.0
            with operation("catalog"):
                specs = provider.specs()
            failed = provider.catalog_failed
        except Exception as exc:  # noqa: BLE001 - one adapter must not abort catalogue discovery
            specs, failed = {}, True
            provider.record_catalog_failure(exc)
            provider.catalog_retry_at = time.time() + 30.0
        error = provider.catalog_error if failed else None
        with self._lock:
            previous = self._cache.get(provider)
            if failed and previous is not None:
                specs = previous.specs
            result = DiscoveryResult(
                specs, failed, error or ("catalog_failed" if failed else None),
                provider.catalog_last_success or (previous.last_success if previous else None),
                provider.catalog_retry_at or None, "cache" if failed and specs else provider.catalog_source,
                dict(provider.catalog_diagnostic) if failed and provider.catalog_diagnostic else None,
            )
            self._cache[provider] = result
        if not failed and not provider._models_shrunk and result.last_success is not None and (previous is None or previous.last_success != result.last_success):
            persist(provider, specs, result.last_success)
        return result

    def close(self) -> None:
        # A timed-out synchronous request cannot be killed safely. Do not wait for
        # it on disconnect/shutdown, and do not create another worker on retries.
        self._pool.shutdown(wait=False, cancel_futures=True)
