"""Opt-in local services; credentials and loaded model metadata stay per account."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import httpx

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.errors import ValidationError
from hoshimi.core.provider_definitions import PROVIDER_BY_ID
from hoshimi.core.providers.base import Account, ContextOverflow
from hoshimi.core.providers.catalog_profiles import CatalogProfile, discover_catalog
from hoshimi.core.providers.configured import ConfiguredProvider
from hoshimi.core.providers.keyed import account_token


class LocalProvider(ConfiguredProvider):
    # keyless=False prevents the registry's automatic anonymous boot path.
    # An explicit account (possibly {}) is the opt-in for this local endpoint.
    keyless = False
    requires_authorization = False

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._loaded: dict[str, tuple[float, dict[str, int]]] = {}

    @classmethod
    def check_account(cls, data: dict[str, Any]) -> None:
        base = data.get("base_url")
        if base is not None:
            url = urlsplit(str(base))
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValidationError("Local base_url must be an HTTP(S) API address without credentials, query or fragment")

    def endpoint_base(self, account: Account) -> str:
        self.check_account(account.data)
        base = str(account.get("base_url") or self.base_url).rstrip("/")
        return base if base.endswith("/v1") else base + "/v1"

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if account_token(account):
            headers["Authorization"] = f"Bearer {account_token(account)}"
        return headers

    def loaded_contexts(self, account: Account) -> dict[str, int]:
        cached = self._loaded.get(account.id)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        contexts: dict[str, int] = {}
        try:
            response = self.client.get(self.endpoint_base(account).removesuffix("/v1") + "/api/v0/models", headers=self.build_headers(account, ""), timeout=2, follow_redirects=False)
            response.raise_for_status()
            for row in response.json().get("data", []):
                model, limit = row.get("id"), row.get("loaded_context_length")
                if row.get("state") == "loaded" and isinstance(model, str) and type(limit) is int and limit > 0:
                    contexts[model] = limit
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            contexts = cached[1] if cached else {}
        self._loaded[account.id] = (time.monotonic(), contexts)
        return contexts

    def discover_rows(self, account: Account) -> list[dict[str, Any]]:
        base = self.endpoint_base(account)
        headers = self.build_headers(account, "")
        if self.id != "ollama":
            rows = discover_catalog(self.client, base + "/models", headers, CatalogProfile())
            if self.id == "lmstudio":
                loaded = self.loaded_contexts(account)
                rows = [{**row, **({"context_length": loaded[row["id"]]} if row["id"] in loaded else {})} for row in rows]
            return rows
        response = self.client.get(base.removesuffix("/v1") + "/api/tags", headers=headers, timeout=15, follow_redirects=False)
        response.raise_for_status()
        source = response.json().get("models")
        if not isinstance(source, list):
            raise TypeError("Ollama directory has no models")
        rows = []
        for entry in source:
            if not isinstance(entry, dict):
                raise TypeError("Ollama model entry is not an object")
            model = entry.get("name") or entry.get("model")
            if not isinstance(model, str) or not model:
                raise ValueError("Ollama model has no name")
            row = {**entry, "id": model}
            try:
                details = self.client.post(base.removesuffix("/v1") + "/api/show", json={"model": model}, headers=headers, timeout=5, follow_redirects=False)
                details.raise_for_status()
                payload = details.json()
                caps = payload.get("capabilities")
                if isinstance(caps, list):
                    if "completion" not in caps and "tools" not in caps:
                        continue
                    row.update(vision="vision" in caps, reasoning="thinking" in caps, tools="tools" in caps)
                info = payload.get("model_info", {})
                limits = [v for k, v in info.items() if k.endswith(".context_length") and type(v) is int and v > 0] if isinstance(info, dict) else []
                if limits:
                    row["context_length"] = max(limits)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                pass  # An optional metadata failure does not erase a listed model.
            rows.append(row)
        return rows

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if self.id == "lmstudio":
            context = self.loaded_contexts(account).get(model)
            # A rough preflight protects against obvious overload; the server's
            # tokenizer remains authoritative for requests close to the limit.
            size = len(json.dumps(body.get("messages", []), ensure_ascii=False).encode("utf-8"))
            output = body.get("max_tokens", body.get("max_completion_tokens", 0))
            output = output if type(output) is int and output > 0 else 0
            if context and size // 4 + output > context:
                raise ContextOverflow(400, "LM Studio request exceeds this model's loaded context window")
        yield from super().stream_raw(account, model, body)

    def specs(self) -> dict[str, ModelSpec]:
        return {model: replace(spec, free=None) for model, spec in super().specs().items()}


def local_provider_classes() -> dict[str, type[LocalProvider]]:
    return {name: type(name.title() + "Provider", (LocalProvider,), {"id": name, "display_name": PROVIDER_BY_ID[name].name,
                 "base_url": PROVIDER_BY_ID[name].base_url, "definition": PROVIDER_BY_ID[name]}) for name in ("lmstudio", "llamacpp", "ollama")}
