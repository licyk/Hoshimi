"""Per-station discovery adapters for the existing aggregator accounts."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from hoshimi.core.providers.keyed import chat_row
from hoshimi.core.providers.openai_compat import model_rows


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _merge_row(entry: dict[str, Any]) -> dict[str, Any] | None:
    routes = [route for route in _object(entry.get("vendors")).values() if isinstance(route, dict)
              and route.get("availability_status") == "available"
              and "text" in _object(route.get("capabilities")).get("input", [])
              and "text" in _object(route.get("capabilities")).get("output", [])]
    if not routes or not isinstance(entry.get("model"), str):
        return None
    row: dict[str, Any] = {"id": entry["model"], "name": entry.get("display_name") or entry["model"],
                           "reasoning": any(_object(r.get("capabilities")).get("supports_reasoning") is True for r in routes),
                           "vision": any("image" in _object(r.get("capabilities")).get("input", []) for r in routes)}
    for field in ("context_window", "max_output_tokens"):
        values = [r[field] for r in routes if isinstance(r.get(field), int) and r[field] > 0]
        if values:
            row[field] = max(values)
    # A single route must actually be free in both directions. Combining the
    # cheapest input of one route with another route's output invents a price.
    prices = [_object(r.get("pricing")) for r in routes]
    known = [p for p in prices if all(isinstance(p.get(k), (int, float)) and not isinstance(p[k], bool)
                                    and math.isfinite(p[k]) and p[k] >= 0 for k in ("input_per_million", "output_per_million"))]
    if known:
        row["isFree"] = any(p["input_per_million"] == p["output_per_million"] == 0 for p in known)
    return row


def normalize_row(station: str | None, row: dict[str, Any]) -> dict[str, Any] | None:
    if station == "merge":
        return _merge_row(row)
    if station == "venice":
        if row.get("type") != "text":
            return None
        spec = _object(row.get("model_spec"))
        caps = _object(spec.get("capabilities"))
        return {**row, "name": spec.get("name") or row.get("id"),
                "context_length": row.get("context_length") or spec.get("availableContextTokens"),
                "max_output_tokens": spec.get("maxCompletionTokens"),
                "reasoning": caps.get("supportsReasoning") is True,
                "vision": caps.get("supportsVision") is True or caps.get("supportsMultipleImages") is True}
    if station == "infron":
        if str(row.get("category_type", "")).upper() != "LLM" or row.get("is_display_only") is True:
            return None
        return {**row, "name": row.get("display_name") or row.get("id")}
    return row if chat_row(row) else None


def discover(client: httpx.Client, base: str, station: str | None, headers: dict[str, str]) -> list[dict[str, Any]]:
    """Fetch complete catalogs; a partial page sequence is never published."""
    url = f"{base}/models"
    params: dict[str, Any] = {}
    if station == "venice":
        params["type"] = "text"
    if station == "merge":
        url = f"{base.removesuffix('/openai')}/models"
        params["limit"] = 500
    rows: list[dict[str, Any]] = []
    cursors: set[str] = set()
    for _ in range(20):
        response = client.get(url, params=params, headers={**headers, "Accept": "application/json"}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        for entry in model_rows(payload):
            normalized = normalize_row(station, entry)
            if normalized is not None:
                rows.append(normalized)
        if station != "merge" or not isinstance(payload, dict) or payload.get("has_more") is not True:
            break
        cursor = payload.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError("merge: catalog pagination did not advance")
        cursors.add(cursor)
        params["cursor"] = cursor
    else:
        raise ValueError("merge: catalog exceeded 20 pages")
    if station == "ollama-cloud":
        def details(row: dict[str, Any]) -> dict[str, Any]:
            try:
                response = client.post(f"{base.removesuffix('/v1')}/api/show", json={"model": row.get("id")}, headers=headers, timeout=8)
                response.raise_for_status()
                payload = response.json()
                caps = payload.get("capabilities")
                if isinstance(caps, list):
                    if not any(c in caps for c in ("completion", "tools")):
                        return {}
                    row = {**row, "vision": "vision" in caps, "reasoning": "thinking" in caps}
                for key, value in _object(payload.get("model_info")).items():
                    if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                        row = {**row, "context_length": value}
                        break
            except (httpx.HTTPError, ValueError, AttributeError):
                pass  # Capability discovery failure does not remove a listed model.
            return row
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = [row for row in pool.map(details, rows) if row]
    return rows


def row_capabilities(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize declared metadata for agg_spec; no guessed limits or menus."""
    result: dict[str, Any] = {}
    for field in ("vision", "reasoning", "tools"):
        if isinstance(row.get(field), bool):
            result[field] = row[field]
    architecture = _object(row.get("architecture"))
    inputs = row.get("input_modalities") or architecture.get("input_modalities")
    if isinstance(inputs, list):
        result["vision"] = "image" in [str(value).lower() for value in inputs]
    parameters = row.get("supported_parameters")
    if isinstance(parameters, list):
        result["reasoning"] = "reasoning" in parameters or "reasoning_effort" in parameters
    for target, keys in {"context_window": ("context_window", "context_length"), "max_output": ("max_output_tokens", "max_completion_tokens")}.items():
        for key in keys:
            value = row.get(key) or _object(row.get("top_provider")).get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result[target] = value
                break
    return result
