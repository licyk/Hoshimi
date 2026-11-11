"""The Qoder model catalogue: live rows, static fallback, and the specs they make.

``{base}algo/api/v2/model/list?Encode=1`` is COSY-signed and returns
``{"chat": [...]}``. Each row carries the upstream ``key`` (``auto``, ``qmodel``,
``dmodel``…), a ``display_name``, whether it is enabled, vision and reasoning
flags, selectable context sizes and a ``thinking_config``.

The public model id is the display name with whitespace stripped (``Qwen3.8-Max``);
the upstream key never leaves this module except inside the request's
``model_config``. Two catalogues, two static fallbacks: the CN list is its own
table, not a copy of the international one.

Context uses the largest advertised ``context_config`` option (often 1M) rather
than ``max_input_tokens``, which the gateway ships as a stale 180K floor. Output
is the documented upstream ceiling, 131072 — the catalogue declares no per-model
output cap.

Ported from ``pi-provider-qoder`` ``src/catalog.ts``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.paths import default_data_dir
from hoshimi.core.qoder.cosy import CosyCredentials, build_auth_headers
from hoshimi.core.qoder.region import QoderRegion, model_list_url

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 131072
DEFAULT_CONTEXT_WINDOW = 1_000_000
CACHE_TTL_SECONDS = 3600.0
FETCH_TIMEOUT_SECONDS = 30.0

# The rungs Qoder's effort-based models name, in picker order.
THINKING_LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")
EFFORT_PATCH: dict[str, Any] = {"reasoning_effort": "$effort"}
OFF_RUNG = "none"


def to_model_id(display_name: str | None) -> str:
    """The public id: the display name with every whitespace run removed."""
    return re.sub(r"\s+", "", display_name or "QoderModel")


@dataclass(frozen=True)
class ModelEntry:
    """One catalogue row, normalized; ``raw`` is what ``model_config`` sends."""

    key: str
    display_name: str
    enable: bool = True
    vision: bool = False
    reasoning: bool = False
    source: str = "system"
    context_window: int = DEFAULT_CONTEXT_WINDOW
    efforts: tuple[str, ...] = ()
    effort_default: str | None = None
    can_disable: bool = False
    toggle_only: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return to_model_id(self.display_name)


def context_window_from_catalog(row: dict[str, Any]) -> int:
    """The largest selectable context option, or the 1M default."""
    config = row.get("context_config")
    advertised = 0
    if isinstance(config, dict):
        for value in config.values():
            if isinstance(value, dict):
                count = value.get("token_count")
                if isinstance(count, (int, float)) and not isinstance(count, bool) and count > advertised:
                    advertised = int(count)
    return advertised if advertised > 0 else DEFAULT_CONTEXT_WINDOW


def with_max_context_as_default(row: dict[str, Any]) -> dict[str, Any]:
    """Pin ``is_default`` on the largest context option, as the CLI does."""
    config = row.get("context_config")
    if not isinstance(config, dict) or not config:
        return row
    largest = 0
    for value in config.values():
        if isinstance(value, dict):
            count = value.get("token_count")
            if isinstance(count, (int, float)) and not isinstance(count, bool) and count > largest:
                largest = int(count)
    if largest <= 0:
        return row
    updated = dict(row)
    updated["context_config"] = {
        name: {**(value if isinstance(value, dict) else {}), "is_default": isinstance(value, dict) and value.get("token_count") == largest}
        for name, value in config.items()
    }
    return updated


def _truthy(value: Any) -> bool:
    """JavaScript truthiness, which the catalogue's own client applies.

    Qoder writes ``"disabled": {}`` and ``"enabled": {}`` — empty objects that
    are *present* branches upstream. Python's ``bool({})`` is False, so a plain
    truth test would silently drop every model's off rung and never send
    ``reasoning_effort: "none"``. Only null/absent, false, zero and "" are false.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return bool(value)
    return True


def thinking_menu(row: dict[str, Any]) -> tuple[tuple[str, ...], str | None, bool, bool]:
    """ ``(efforts, default, can_disable, toggle_only)`` from ``thinking_config``.

    Two shapes: effort-based (``enabled.efforts`` names the rungs) and
    toggle-only (``enabled`` with no efforts — any level just turns thinking on,
    so the menu is a single ``on`` rung). ``disabled`` is what makes an off rung
    exist; without it reasoning is mandatory upstream.
    """
    config = row.get("thinking_config")
    if not isinstance(config, dict):
        return (), None, False, False
    enabled = config.get("enabled")
    can_disable = _truthy(config.get("disabled"))
    efforts_map = enabled.get("efforts") if isinstance(enabled, dict) else None
    if isinstance(efforts_map, dict) and efforts_map:
        supported = set(efforts_map)
        rungs = tuple(level for level in THINKING_LEVELS if level in supported)
        default = next((level for level in THINKING_LEVELS if isinstance(efforts_map.get(level), dict) and efforts_map[level].get("is_default")), None)
        if not rungs:
            return (), None, can_disable, False
        if can_disable:
            rungs = (*rungs, OFF_RUNG)
        return rungs, default, can_disable, False
    if _truthy(enabled):
        rungs = ("on", OFF_RUNG) if can_disable else ("on",)
        return rungs, "on", can_disable, True
    return (), None, can_disable, False


def parse_entry(row: dict[str, Any]) -> ModelEntry | None:
    """One live catalogue row; None when it is disabled or unnamed."""
    key = row.get("key")
    display = row.get("display_name")
    if not isinstance(key, str) or not key or not row.get("enable") or not isinstance(display, str) or not display:
        return None
    efforts, default, can_disable, toggle_only = thinking_menu(row)
    reasoning = bool(row.get("is_reasoning")) or _truthy(row.get("thinking_config"))
    return ModelEntry(
        key=key,
        display_name=display,
        enable=True,
        vision=bool(row.get("is_vl")),
        reasoning=reasoning,
        source=str(row.get("source") or "system"),
        context_window=context_window_from_catalog(row),
        efforts=efforts,
        effort_default=default,
        can_disable=can_disable,
        toggle_only=toggle_only,
        raw=with_max_context_as_default(row),
    )


def _static(
    key: str,
    display: str,
    *,
    reasoning: bool = False,
    vision: bool = True,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    efforts: tuple[str, ...] = (),
    effort_default: str | None = None,
    can_disable: bool = False,
    toggle_only: bool = False,
) -> ModelEntry:
    raw: dict[str, Any] = {"key": key, "is_reasoning": reasoning, "source": "system"}
    return ModelEntry(
        key=key,
        display_name=display,
        vision=vision,
        reasoning=reasoning,
        context_window=context_window,
        efforts=efforts,
        effort_default=effort_default,
        can_disable=can_disable,
        toggle_only=toggle_only,
        raw=raw,
    )


# The international fallback table (the plugin's `staticModels`).
STATIC_GLOBAL: tuple[ModelEntry, ...] = (
    _static("auto", "Auto", reasoning=True, toggle_only=True, efforts=("on",), can_disable=False),
    _static("ultimate", "Ultimate", reasoning=True, efforts=("low", "medium", "high", "xhigh", "max", OFF_RUNG), effort_default="high", can_disable=True),
    _static("performance", "Performance", reasoning=True, efforts=("low", "medium", "high", "xhigh", "max", OFF_RUNG), effort_default="medium", can_disable=True),
    _static("efficient", "Efficient"),
    _static("lite", "Lite", vision=False),
    _static("qmodel", "Qwen3.7 Plus"),
    _static("cmodel", "Cantus", reasoning=True, efforts=("low", "medium", "high", "xhigh", "max", OFF_RUNG), effort_default="high", can_disable=True),
    _static("qmodel_preview", "Qwen3.8-Max", reasoning=True, efforts=("low", "medium", "xhigh", OFF_RUNG), effort_default="xhigh", can_disable=True),
    _static("qmodel_latest", "Qwen3.7-Max"),
    _static("dmodel", "DeepSeek-V4-Pro", reasoning=True, efforts=("low", "high", "max", OFF_RUNG), effort_default="max", can_disable=True),
    _static("dfmodel", "DeepSeek-V4-Flash", reasoning=True, efforts=("low", "high", "max", OFF_RUNG), effort_default="max", can_disable=True),
    _static("gm51model", "GLM-5.2", reasoning=True, efforts=("low", "high", "max", OFF_RUNG), effort_default="max", can_disable=True),
    # The catalogue advertises 256K; not part of the 1M live test.
    _static("kmodel", "Kimi-K2.7-Code", context_window=256000),
    _static("kmodel_latest", "Kimi-K3"),
    _static("mmodel", "MiniMax-M3"),
)

# The CN fallback table (the plugin's `staticCnModels`) — its own ids and sizes.
STATIC_CN: tuple[ModelEntry, ...] = (
    _static("auto", "Auto", reasoning=True, context_window=200000, toggle_only=True, efforts=("on",)),
    _static("qmodel_latest", "Qwen3.7-Max", reasoning=True, toggle_only=True, efforts=("on",)),
    _static("qmodel", "Qwen3.7-Plus", reasoning=True, vision=False, toggle_only=True, efforts=("on",)),
    _static("q36fmodel", "Qwen3.6-Flash", reasoning=True, vision=False, toggle_only=True, efforts=("on",)),
    _static("dmodel", "DeepSeek-V4-Pro", reasoning=True, vision=False, toggle_only=True, efforts=("on",)),
    _static("dfmodel", "DeepSeek-V4-Flash", vision=False),
    _static("gm51model", "GLM-5.2", reasoning=True, context_window=200000, toggle_only=True, efforts=("on",)),
    _static("kmodel", "Kimi-K2.7-Code", context_window=256000),
    _static("mmodel", "MiniMax-M2.7", vision=False, context_window=200000),
)


def static_entries(region: QoderRegion) -> tuple[ModelEntry, ...]:
    return STATIC_CN if region.mode == "cn" else STATIC_GLOBAL


def spec_for(entry: ModelEntry, provider_id: str) -> ModelSpec:
    """The gateway's capability row for one catalogue entry.

    A declared menu makes the level the control on the wire (``reasoning_effort``
    top-level, which the provider moves into the envelope's ``parameters``); a
    row without one keeps ``reasoning`` from the catalogue and forwards whatever
    the caller sent.
    """
    menu = entry.efforts
    off_id = OFF_RUNG if entry.can_disable and OFF_RUNG in menu else None
    return ModelSpec(
        id=entry.id,
        provider=provider_id,
        name=entry.display_name,
        wire="chat",
        vision=entry.vision,
        reasoning=bool(menu) or entry.reasoning,
        context_window=entry.context_window,
        max_output=MAX_OUTPUT_TOKENS,
        can_disable_thinking=off_id is not None,
        efforts=menu,
        effort_default=entry.effort_default if entry.effort_default in menu else (menu[0] if menu else None),
        effort_off_id=off_id,
        effort_patch=EFFORT_PATCH if menu else None,
    )


def cache_path(region: QoderRegion) -> Path:
    return default_data_dir() / region.model_cache_file


class ModelCatalog:
    """The entries a lane advertises, with a one-hour disk cache.

    ``entries`` never blocks on the network: a refresh is explicit (login,
    catalogue discovery), and a failed one keeps whatever was last good.
    """

    def __init__(self, region: QoderRegion, *, path: Path | None = None) -> None:
        self.region = region
        self.path = path or cache_path(region)
        self._lock = threading.Lock()
        self._entries: dict[str, ModelEntry] = {}
        self._updated_at = 0.0
        self._loaded = False

    # -- persistence ------------------------------------------------------- #
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        configs = raw.get("configs")
        updated = raw.get("updatedAt")
        if not isinstance(configs, dict) or not isinstance(updated, (int, float)):
            return
        entries: dict[str, ModelEntry] = {}
        for model_id, row in configs.items():
            if not isinstance(row, dict):
                continue
            entry = parse_entry({**row, "enable": True})
            if entry is not None and entry.id == model_id:
                entries[model_id] = entry
        if entries:
            self._entries = entries
            self._updated_at = float(updated)

    def _save(self) -> None:
        payload = {
            "updatedAt": self._updated_at,
            "configs": {model_id: entry.raw for model_id, entry in self._entries.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass  # the in-memory catalogue still serves this process

    # -- reads ------------------------------------------------------------- #
    def stale(self, now: float | None = None) -> bool:
        with self._lock:
            self._load()
            stamp = time.time() if now is None else now
            return not self._entries or stamp - self._updated_at > CACHE_TTL_SECONDS

    def entries(self) -> dict[str, ModelEntry]:
        with self._lock:
            self._load()
            if self._entries:
                return dict(self._entries)
        return {entry.id: entry for entry in static_entries(self.region)}

    def ids(self) -> list[str]:
        return list(self.entries())

    def entry_for(self, model_id: str) -> ModelEntry | None:
        entries = self.entries()
        direct = entries.get(model_id)
        if direct is not None:
            return direct
        # An id the caller spelled with whitespace, or a raw upstream key: both
        # resolve to the same row rather than failing the turn.
        stripped = to_model_id(model_id)
        for entry in entries.values():
            if entry.id == stripped or entry.key == model_id:
                return entry
        return None

    def config_for(self, model_id: str) -> dict[str, Any] | None:
        """The ``model_config`` object the request carries for this id."""
        entry = self.entry_for(model_id)
        if entry is None:
            return None
        return dict(entry.raw) if entry.raw else {"key": entry.key, "is_reasoning": entry.reasoning, "source": entry.source}

    # -- the fetch --------------------------------------------------------- #
    def refresh(self, client: httpx.Client, credentials: CosyCredentials, *, cosy_version: str) -> bool:
        """Fetch the live catalogue. True when it replaced what we had.

        The COSY identity is refreshed by the caller *before* this: the gateway
        gates the catalogue on the client version, so a stale identity returns a
        reduced list that would then be cached for its whole TTL.
        """
        url = model_list_url(self.region)
        try:
            headers = build_auth_headers(None, url, credentials, cosy_version=cosy_version)
            headers["Accept"] = "application/json"
            response = client.get(url, headers=headers, timeout=FETCH_TIMEOUT_SECONDS)
            if response.status_code >= 400:
                logger.debug("qoder: model/list returned HTTP %s", response.status_code)
                return False
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("qoder: model/list unavailable (%s)", exc)
            return False
        rows = payload.get("chat") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            return False
        entries: dict[str, ModelEntry] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry = parse_entry(row)
            if entry is not None:
                entries[entry.id] = entry
        if not entries:
            return False
        with self._lock:
            self._loaded = True
            self._entries = entries
            self._updated_at = time.time()
            self._save()
        return True