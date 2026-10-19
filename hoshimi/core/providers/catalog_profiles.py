"""Declarative model discovery; incomplete refreshes never replace a catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin

import httpx

Path = tuple[str, ...]


def at(value: Any, path: Path) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


@dataclass(frozen=True)
class Field:
    target: str
    path: Path
    contains: str | None = None


@dataclass(frozen=True)
class CatalogProfile:
    path: str = "/models"
    root_relative: bool = False
    query: tuple[tuple[str, str], ...] = ()
    collection: Path | None = None
    id_path: Path = ("id",)
    fields: tuple[Field, ...] = ()
    required: tuple[tuple[Path, tuple[Any, ...]], ...] = ()
    contains: tuple[tuple[Path, str], ...] = ()
    pagination: Literal["none", "numbered", "dashscope", "cursor"] = "none"
    page_param: str = "page"
    current_page: Path = ("pagination", "current_page")
    total_pages: Path = ("pagination", "total_pages")
    next_cursor: Path = ("nextPageToken",)
    max_pages: int = 100

    def normalize(self, row: dict[str, Any]) -> dict[str, Any] | None:
        if self.pagination == "dashscope":
            capabilities = row.get("capabilities")
            if not isinstance(capabilities, list) or not {"TG", "Reasoning", "VU", "Multimodal-Omni"}.intersection(capabilities) or any(str(c).startswith("Realtime-") for c in capabilities):
                return None
        if any(at(row, path) not in allowed for path, allowed in self.required):
            return None
        if any(not isinstance(at(row, path), list) or expected not in at(row, path) for path, expected in self.contains):
            return None
        model = at(row, self.id_path)
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model directory entry has no model ID")
        result = {**row, "id": model}
        for field in self.fields:
            value = at(row, field.path)
            if field.contains is not None:
                value = field.contains in value if isinstance(value, list) else None
            if value is not None:
                result[field.target] = value
        return result


def discover_catalog(client: httpx.Client, url: str, headers: dict[str, str], profile: CatalogProfile) -> list[dict[str, Any]]:
    query: dict[str, Any] = dict(profile.query)
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    cursors: set[str] = set()
    expected_total: int | None = None
    raw_count = 0
    for page in range(1, profile.max_pages + 1):
        if profile.pagination in ("numbered", "dashscope"):
            query[profile.page_param] = page
        response = client.get(url, params=query, headers=headers, timeout=30, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if profile.collection is None and not isinstance(payload, list) and not (isinstance(payload, dict) and any(isinstance(payload.get(key), list) for key in ("data", "models"))):
            raise ValueError("model directory response has no collection")
        raw = at(payload, profile.collection) if profile.collection is not None else (payload if isinstance(payload, list) else payload.get("data", payload.get("models")))
        if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
            raise ValueError("invalid model directory collection")
        raw_count += len(raw)
        for row in raw:
            model = at(row, profile.id_path)
            if not isinstance(model, str) or not model.strip() or model in ids:
                raise ValueError("missing or duplicate model directory ID")
            ids.add(model)
            normalized = profile.normalize(row)
            if normalized is not None:
                result.append(normalized)
        if profile.pagination == "none":
            return result
        if profile.pagination == "numbered":
            current, total = at(payload, profile.current_page), at(payload, profile.total_pages)
            if type(current) is not int or current != page or type(total) is not int or total < page:
                raise ValueError("invalid model directory pagination")
            if expected_total is not None and total != expected_total:
                raise ValueError("model directory page count changed")
            expected_total = total
            if current == total:
                return result
        elif profile.pagination == "dashscope":
            total = at(payload, ("output", "total"))
            if at(payload, ("success",)) is not True or type(total) is not int or total < 0 or at(payload, ("output", "page_no")) != page:
                raise ValueError("invalid DashScope directory page")
            if expected_total is not None and total != expected_total:
                raise ValueError("DashScope directory size changed")
            expected_total = total
            if raw_count > total:
                raise ValueError("DashScope directory exceeds declared size")
            if raw_count == total:
                return result
        else:
            cursor = at(payload, profile.next_cursor)
            if cursor in (None, ""):
                return result
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError("model directory cursor did not advance")
            cursors.add(cursor)
            query[profile.page_param] = cursor
        if not raw:
            raise ValueError("model directory ended before its final page")
    raise ValueError("model directory page limit exceeded")


CATALOG_PROFILES: dict[str, CatalogProfile] = {
    "cline-pass": CatalogProfile(path="/ai/cline/recommended-models", collection=("clinePass",)),
    "xai": CatalogProfile(path="/language-models", collection=("models",)),
    "vercel-ai-gateway": CatalogProfile(fields=(Field("context_length", ("context_window",)), Field("max_output_tokens", ("max_tokens",)), Field("input_modalities", ("modalities", "input")), Field("reasoning", ("supported_parameters",), "reasoning"))),
    "alibaba-cloud": CatalogProfile(path="/api/v1/models", root_relative=True, collection=("output", "models"), id_path=("model",),
        query=(("page_size", "100"), ("supports", "inference")), pagination="dashscope", page_param="page_no",
        fields=(Field("context_length", ("model_info", "context_window")), Field("max_output_tokens", ("model_info", "max_output_tokens")),
                Field("reasoning", ("capabilities",), "Reasoning"), Field("input_modalities", ("inference_metadata", "request_modality")), Field("tools", ("features",), "function-calling"))),
    "siliconflow": CatalogProfile(query=(("sub_type", "chat"),)),
    "nebius": CatalogProfile(query=(("verbose", "true"),), fields=(Field("context_length", ("context_length",)), Field("input_modalities", ("architecture", "input_modalities")))),
    "chutes": CatalogProfile(contains=((("input_modalities",), "text"), (("output_modalities",), "text")),
        fields=(Field("tools", ("supported_features",), "tools"), Field("reasoning", ("supported_features",), "reasoning"), Field("max_output_tokens", ("max_output_length",)))),
    "featherless": CatalogProfile(collection=("data",), query=(("available_on_current_plan", "true"), ("status", "active"), ("per_page", "1000")),
        pagination="numbered", required=((("available_on_current_plan",), (True,)), (("is_gated",), (False,))),
        fields=(Field("tools", ("features", "tool_use")), Field("vision", ("features", "image_input")), Field("max_output_tokens", ("max_completion_tokens",)))),
    "mistral-codestral": CatalogProfile(fields=(Field("context_length", ("max_context_length",)), Field("vision", ("capabilities", "vision")), Field("tools", ("capabilities", "function_calling")))),
    "nararoute": CatalogProfile(fields=(Field("reasoning", ("reasoning",)),)),
    "lightning": CatalogProfile(fields=(Field("max_output_tokens", ("max_tokens",)), Field("input_modalities", ("architecture", "input_modalities")))),
    "cheaperinference": CatalogProfile(query=(("type", "text"), ("streaming", "true")), required=((("type",), ("text",)),)),
    "atlascloud": CatalogProfile(fields=(Field("max_output_tokens", ("max_output_length",)),)),
}


def catalog_url(base: str, profile: CatalogProfile) -> str:
    if profile.root_relative:
        return urljoin(base, profile.path)
    return base.rstrip("/") + profile.path
