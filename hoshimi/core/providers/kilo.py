"""Kilo AI free pool (OpenAI-compatible gateway that answers with no credential)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar

from hoshimi.core.catalog import ModelSpec, kilo_spec
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.compat import gateway_chat_body
from hoshimi.core.providers.keyed import account_token, chat_row
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider

KILO_BASE_URL = "https://api.kilo.ai/api/gateway"


class KiloProvider(OpenAiCompatProvider):
    """The free slice of Kilo's gateway: `isFree: true` rows only.

    Everything else on this gateway is a paid id that answers 401 without a key,
    so the listing is filtered down to the free pool before it reaches the
    catalogue. There is no secret on this lane — free-pool prompts may be logged
    by the upstream provider (the README carries the same warning).
    """

    id = "kilo"
    display_name = "Kilo AI"
    keyless = True
    base_url = KILO_BASE_URL
    chat_path = "/chat/completions"
    models_path = "/models"
    user_agent = "hoshimi"
    fallback_model_list: ClassVar[list[str]] = [
        "stepfun/step-5-preview-free",
        "kilo-auto/free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "dots-studio/dots-3-note-preview:free",
        "inclusionai/ling-3.1-flash",
        "poolside/laguna-s-2.1:free",
        "stealth/glyph-cluster",
        "liquid/lfm-2.5-2.6b:free",
        "nvidia/nemotron-3.5-lightning:free",
        "thinkingmachines/inkling-small:free",
        "poolside/laguna-xs-2.1:free",
        "cohere/north-mini-code:free",
        "nvidia/nemotron-3.5-content-safety:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/free",
    ]

    def __init__(self, client: Any, accounts: list[Account], options: dict[str, Any] | None = None) -> None:
        super().__init__(client, accounts, options)
        # The last successful listing, kept so capability lookups (effort menus,
        # context windows) follow the same refresh as the id list.
        self.listing_rows: list[dict[str, Any]] = []

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        token = account_token(account)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers.pop("Authorization", None)
        return headers

    def parse_models_response(self, data: Any) -> list[str]:
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        ids: list[str] = []
        free_rows: list[dict[str, Any]] = []
        for row in rows:
            authenticated = any(account_token(account) for account in self.accounts)
            if not isinstance(row, dict) or not chat_row(row):
                continue
            if (not authenticated or self.options.get("free_only") is True) and row.get("isFree") is not True:
                continue
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            free_rows.append(row)
            ids.append(model_id.strip())
        self.listing_rows = free_rows
        return ids

    def build_body(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        out = super().build_body(gateway_chat_body(body, strict_tools=False), model)
        out.pop("store", None)
        out.pop("reasoning_effort", None)
        return out

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the last listing rows; the id list can outlive them.

        With a declared thinking menu the level itself is the control on the
        wire; without one (a row that does not name `reasoning`, or a row lost
        to a network failure) the token ladder applies and the model's own
        streaming thinking cannot be switched off.
        """
        rows = {}
        for row in self.listing_rows:
            model_id = str(row.get("id") or "").strip()
            if model_id:
                rows[model_id] = row
        return {model_id: replace(kilo_spec(model_id, rows.get(model_id), self.id), free=rows.get(model_id, {}).get("isFree")) for model_id in self.models()}
