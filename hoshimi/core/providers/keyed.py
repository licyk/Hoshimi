"""Shared key-based account and live catalog handling for fixed services."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec, default_spec
from hoshimi.core.errors import ValidationError
from hoshimi.core.modelsdev import enrich_spec
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.catalog_profiles import CATALOG_PROFILES, CatalogProfile, discover_catalog
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider


def account_token(account: Account) -> str:
    """Normalize the key field without introducing ambient credential sources."""
    return next((value for key in ("api_key", "access_token", "token") if (value := str(account.get(key, "")).strip())), "")


def chat_row(row: dict[str, Any]) -> bool:
    kind = row.get("model_type") or row.get("type")
    if kind in ("embedding", "embeddings", "image", "video", "audio", "rerank", "classifier"):
        return False
    architecture = row.get("architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    modalities = row.get("output_modalities") or architecture.get("output_modalities")
    return not isinstance(modalities, list) or not modalities or "text" in modalities


def spec_from_row(model: str, provider: str, row: dict[str, Any], *, wire: str = "chat") -> ModelSpec:
    """Read only declared capabilities; limits are never borrowed across stations."""
    spec = default_spec(model, provider)
    values: dict[str, Any] = {"wire": wire}
    if isinstance(row.get("name"), str):
        values["name"] = row["name"]
    for target, keys in {"context_window": ("context_length", "context_window", "contextWindow"),
                         "max_output": ("max_output_tokens", "max_completion_tokens", "maxTokens")}.items():
        value = next((row[k] for k in keys if isinstance(row.get(k), int) and not isinstance(row[k], bool) and row[k] > 0), None)
        if value is not None:
            values[target] = value
    capabilities = row.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    for key in ("reasoning", "vision", "tools"):
        value = row.get(key, capabilities.get(key))
        if isinstance(value, bool):
            values[key] = value
    architecture = row.get("architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    inputs = row.get("input_modalities") or row.get("input") or architecture.get("input_modalities")
    supports = capabilities.get("supports")
    if isinstance(supports, dict):
        for target, key in (("reasoning", "reasoning_effort"), ("vision", "vision")):
            if isinstance(supports.get(key), bool):
                values[target] = supports[key]
    parameters = row.get("supported_parameters")
    if isinstance(parameters, list):
        values["reasoning"] = any(key in parameters for key in ("reasoning", "reasoning_effort"))
    top = row.get("top_provider")
    if isinstance(top, dict) and isinstance(top.get("max_completion_tokens"), int) and top["max_completion_tokens"] > 0:
        values["max_output"] = top["max_completion_tokens"]
    if isinstance(inputs, list):
        values["vision"] = "image" in inputs
    if isinstance(row.get("isFree"), bool):
        values["free"] = row["isFree"]
    return enrich_spec(replace(spec, **values), declared="reasoning" in values or "vision" in values)


class KeyedProvider(OpenAiCompatProvider):
    """A fixed upstream with configured keys, declared models or a live catalog."""

    chat_path = "/chat/completions"
    models_path = "/models"
    requires_authorization = True

    def __init__(self, client: httpx.Client, accounts: list[Account], options: dict[str, Any] | None = None):
        super().__init__(client, accounts, options)
        self.base_url = str(self.options.get("base_url") or self.base_url).rstrip("/")
        self.listing_rows: dict[str, dict[str, Any]] = {}
        self._account_rows: dict[str, dict[str, dict[str, Any]]] = {}

    @classmethod
    def check_account(cls, data: dict[str, Any]) -> None:
        if not account_token(Account(data)):
            raise ValidationError(f"{cls.id}: the account needs api_key or access_token")

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        token = account_token(account)
        if not token:
            raise ProviderError(400, f"{self.id}: the account needs api_key or access_token")
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream"}

    def fetch_models(self) -> list[str]:
        for account in self.accounts:
            declared = account.get("models", self.options.get("models"))
            try:
                if isinstance(declared, list):
                    rows = [item if isinstance(item, dict) else {"id": item} for item in declared]
                elif self.models_path:
                    rows = self.discover_rows(account)
                else:
                    continue
                found = {row["id"]: row for row in rows if isinstance(row.get("id"), str) and row["id"] and self.accepts_row(row)}
            except (httpx.HTTPError, ValueError, TypeError, ProviderError):
                self.catalog_failed = True
                # Keep this account's last complete catalog; another account's
                # success must not discard its previously learned models.
                continue
            self._account_rows[account.id] = found
        self.catalog_authoritative = not self.catalog_failed
        combined: dict[str, dict[str, Any]] = {}
        for account in self.accounts:
            for model, row in self._account_rows.get(account.id, {}).items():
                combined.setdefault(model, row)
        self.listing_rows = combined
        return list(combined)

    def discover_rows(self, account: Account) -> list[dict[str, Any]]:
        profile = CATALOG_PROFILES.get(self.id, CatalogProfile())
        try:
            return discover_catalog(self.client, self.catalog_url(account), self.build_headers(account, ""), profile)
        except httpx.HTTPStatusError as exc:
            if self.id != "openrouter" or exc.response.status_code not in (401, 403, 404):
                raise
            return discover_catalog(self.client, self.catalog_url(account).removesuffix("/user"), self.build_headers(account, ""), profile)

    def _stream_candidates(self, model: str) -> list[Account]:
        if not self._account_rows:
            return self.accounts
        return [account for account in self.accounts if model in self._account_rows.get(account.id, {})]

    def accepts_row(self, row: dict[str, Any]) -> bool:
        return chat_row(row)

    def catalog_url(self, account: Account) -> str:
        return f"{self.base_url}{self.models_path}"

    def fallback_models(self) -> list[str]:
        return list(self.listing_rows)

    def specs(self) -> dict[str, ModelSpec]:
        models = self.models()
        return {model: self._restored_specs[model] if self.catalog_failed and model in self._restored_specs
                else spec_from_row(model, self.id, self.listing_rows.get(model, {})) for model in models}
