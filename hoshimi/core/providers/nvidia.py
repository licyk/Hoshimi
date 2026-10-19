"""NVIDIA hosted NIM: live IDs and explicit Chat Completions compatibility."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from hoshimi.core.providers.base import Account
from hoshimi.core.providers.keyed import KeyedProvider
from hoshimi.core.providers.nim_tools import clean_schema, native_tools


class NvidiaProvider(KeyedProvider):
    id = "nvidia"
    display_name = "NVIDIA NIM"
    base_url = "https://integrate.api.nvidia.com/v1"
    probeable = True

    def accepts_row(self, row: dict[str, Any]) -> bool:
        # The hosted catalog is ID-only and also includes embeddings, reward
        # scorers and document parsers. This is a task filter, not an outage list.
        model = str(row.get("id") or "").lower()
        return super().accepts_row(row) and not any(part in model for part in ("embed", "rerank", "nvclip", "-reward", "-detector", "nemotron-parse", "gliner"))

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        return {**super().build_headers(account, model), "NVCF-POLL-SECONDS": "3600"}

    def build_body(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        out = super().build_body(deepcopy(body), model)
        for key in ("store", "reasoning_effort", "reasoning", "prompt_cache_retention"):
            out.pop(key, None)
        ceiling = out.pop("max_completion_tokens", None)
        if ceiling is not None:
            out.setdefault("max_tokens", ceiling)
        for message in out.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "developer":
                message["role"] = "system"
        for tool in out.get("tools") or []:
            if isinstance(tool, dict):
                tool.pop("strict", None)
                function = tool.get("function")
                if isinstance(function, dict):
                    function.pop("strict", None)
                    if isinstance(function.get("parameters"), dict):
                        function["parameters"] = clean_schema(function["parameters"])
        return out

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        chunks = super().stream_raw(account, model, body)
        if "minimax" in model.lower() and body.get("tools"):
            yield from native_tools(chunks, body["tools"])
        else:
            yield from chunks
