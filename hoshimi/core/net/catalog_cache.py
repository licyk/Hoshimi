"""Account-scoped last-good model metadata; never persists credentials."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.paths import default_data_dir

if TYPE_CHECKING:
    from hoshimi.core.providers.base import Provider

MAX_AGE = 7 * 24 * 3600


def cache_path(provider: Provider) -> Path:
    identity = {
        "version": 1, "provider": provider.id, "options": provider.options,
        "accounts": [account.data for account in provider.accounts],
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
    return default_data_dir() / "catalog-cache" / f"{digest}.json"


def restore(provider: Provider) -> tuple[dict[str, ModelSpec], float | None]:
    """Restore only this exact endpoint/account configuration, marking it stale."""
    from hoshimi.core.providers.keyed import KeyedProvider

    try:
        path = cache_path(provider)
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}, None
        raw = json.loads(path.read_text(encoding="utf-8"))
        timestamp = float(raw["at"])
        if not 0 <= time.time() - timestamp <= MAX_AGE:
            return {}, None
        specs: dict[str, ModelSpec] = {}
        for row in raw["models"]:
            row["efforts"] = tuple(row.get("efforts", ()))
            spec = ModelSpec(**row)
            if spec.provider != provider.id or not isinstance(spec.id, str):
                return {}, None
            specs[spec.id] = spec
        account_rows = raw.get("accounts", {})
        if not isinstance(account_rows, dict):
            return {}, None
        if isinstance(provider, KeyedProvider):
            provider._account_rows = {account.id: {model: {"id": model} for model in account_rows.get(account.id, []) if model in specs} for account in provider.accounts}
            provider.listing_rows = {model: {"id": model} for model in specs}
        provider._models_cache = list(specs)
        provider._restored_specs = specs
        # Restore before network refresh, but never postpone that refresh.
        provider._models_expires_at = 0.0
        provider.catalog_last_success = timestamp
        return specs, timestamp
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}, None


def persist(provider: Provider, specs: dict[str, ModelSpec], timestamp: float) -> None:
    """Publish a complete snapshot atomically; failures do not affect inference."""
    path = cache_path(provider)
    temp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "at": timestamp, "models": [asdict(spec) for spec in specs.values()],
            "accounts": {key: list(rows) for key, rows in getattr(provider, "_account_rows", {}).items()},
        }
        descriptor, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        temp = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False)
        os.replace(temp, path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temp is not None:
            with suppress(OSError):
                temp.unlink(missing_ok=True)
