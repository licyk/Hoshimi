"""Data-defined conventional providers and account-specific protocol routing."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec, default_spec
from hoshimi.core.net.identity import identity
from hoshimi.core.provider_definitions import CANONICAL_PROVIDERS, REGULAR_PROVIDER_IDS, ProviderDefinition
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.catalog_profiles import CATALOG_PROFILES, CatalogProfile, Field, catalog_url, discover_catalog
from hoshimi.core.providers.compat import gateway_chat_body, provider_chat_body
from hoshimi.core.providers.copilot import AUTO_MODEL_ID, AutoRoute, AutoSessionCache, auto_prompt, free_usable, is_free_account, model_kind, resolve_auto
from hoshimi.core.providers.keyed import KeyedProvider, account_token, spec_from_row
from hoshimi.core.providers.request_profiles import CHAT_PROFILES
from hoshimi.core.providers.transports import request_payload, stream_http
from hoshimi.core.translate.chat import image_data_urls

REGULAR_IDS = REGULAR_PROVIDER_IDS


class ConfiguredProvider(KeyedProvider):
    """One canonical service per lane; credentials remain in Hoshimi accounts."""

    definition: ProviderDefinition

    def endpoint_base(self, account: Account) -> str:
        return str(account.get("base_url") or self.base_url).rstrip("/")

    def wire_for(self, model: str, account: Account | None = None) -> str:
        row = self._account_rows.get(account.id, {}).get(model, self.listing_rows.get(model, {})) if account else self.listing_rows.get(model, {})
        configured = self.options.get("model_protocols", {})
        wire = configured.get(model) if isinstance(configured, dict) else None
        wire = wire or (account.get("protocol") if account else None) or row.get("wire") or row.get("api")
        aliases = {"anthropic-messages": "messages", "openai-completions": "chat", "openai-responses": "responses", "openai-codex-responses": "responses", "google-generative-ai": "gemini"}
        if isinstance(wire, str) and wire in (*aliases, "messages", "responses", "chat"):
            return aliases.get(wire, wire)
        if self.id == "github-copilot":
            endpoints = row.get("supported_endpoints", [])
            if "/responses" in endpoints:
                return "responses"
            if "/v1/messages" in endpoints:
                return "messages"
        if self.definition.protocol == "multi":
            if self.id == "opencode-go" and model.startswith("gemini-"):
                return "gemini"
            if self.id in ("fireworks", "opencode-go") and model.rsplit("/", 1)[-1].startswith("claude"):
                return "messages"
            return "chat"
        return self.definition.protocol if self.definition.protocol in ("messages", "responses") else "chat"

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        if self.wire_for(model, account) == "messages":
            headers["anthropic-version"] = "2023-06-01"
            if self.id == "anthropic":
                if account.get("api_key"):
                    headers["x-api-key"] = account_token(account)
                    headers.pop("Authorization", None)
                else:
                    headers["anthropic-beta"] = "claude-code-20250219,oauth-2025-04-20"
        if self.id == "codex" and account.get("account_id"):
            headers["ChatGPT-Account-Id"] = str(account.get("account_id"))
            headers["originator"] = "hoshimi"
        if self.id == "github-copilot":
            headers.update({"Editor-Version": "vscode/1.107.0", "Editor-Plugin-Version": "copilot-chat/0.35.0",
                            "Copilot-Integration-Id": "vscode-chat", "X-GitHub-Api-Version": "2026-06-01"})
        return headers

    def catalog_url(self, account: Account) -> str:
        if self.id in CATALOG_PROFILES:
            return catalog_url(self.endpoint_base(account), CATALOG_PROFILES[self.id])
        if self.id == "openrouter":
            return f"{self.endpoint_base(account)}/models/user"
        return f"{self.endpoint_base(account)}{self.models_path}"

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        wire = self.wire_for(model, account)
        if wire == "gemini":
            from hoshimi.core.providers.cloud import gemini_payload
            from hoshimi.core.providers.openai_compat import raise_for_status
            from hoshimi.core.providers.opencode import session_for_conversation
            from hoshimi.core.translate.gemini import iter_gemini_chunks

            headers = self.build_headers(account, model)
            headers["x-opencode-session"] = identity.get().session or session_for_conversation(str(body.get("session_id") or body.get("user") or "global"))
            if identity.get().user_agent:
                headers["User-Agent"] = identity.get().user_agent
            url = f"{self.endpoint_base(account)}/models/{model}:streamGenerateContent?alt=sse"
            with self.client.stream("POST", url, headers=headers, json=gemini_payload(body, model)) as response:
                raise_for_status(response, provider=self.id)
                yield from iter_gemini_chunks(response.iter_lines())
            return
        prepared = provider_chat_body(body, self.id, model) if wire == "chat" else body
        profile = CHAT_PROFILES.get(self.id)
        if wire == "chat" and profile is not None:
            prepared = profile.prepare(body)
        if self.id == "mistral" or (profile is not None and profile.tool_ids == "mistral"):
            # Keep the profile request, then normalize both sides of tool IDs.
            prepared = profile.prepare(body) if profile is not None else gateway_chat_body(body)
            prepared.pop("store", None)
            # Native Mistral requires nine alphanumeric characters for every
            # call/result reference. A stable mapping keeps both sides paired.
            for message in prepared.get("messages") or []:
                if message.get("tool_call_id"):
                    message["tool_call_id"] = hashlib.sha256(message["tool_call_id"].encode()).hexdigest()[:9]
                for call in message.get("tool_calls") or []:
                    if call.get("id"):
                        call["id"] = hashlib.sha256(call["id"].encode()).hexdigest()[:9]
        payload = request_payload(prepared, model, wire)
        if self.id == "codex":
            payload["instructions"] = "\n\n".join(str(message.get("content") or "") for message in body.get("messages", []) if message.get("role") in ("system", "developer"))
            payload["input"] = [item for item in payload["input"] if item.get("role") not in ("system", "developer")]
            for key in ("max_output_tokens", "temperature", "top_p"):
                payload.pop(key, None)
        headers = self.build_headers(account, model)
        if self.id == "github-copilot":
            messages = body.get("messages") or []
            headers["X-Initiator"] = "agent" if messages and messages[-1].get("role") != "user" else "user"
            headers["Openai-Intent"] = "conversation-edits"
            if any(image_data_urls(message.get("content")) for message in messages):
                headers["Copilot-Vision-Request"] = "true"
        suffix = {"messages": "/messages", "responses": "/responses"}.get(wire, "/chat/completions")
        if self.id == "github-copilot" and wire == "messages":
            suffix = "/v1/messages"
        if self.id == "opencode-go":
            from hoshimi.core.providers.opencode import session_for_conversation

            headers["x-opencode-session"] = identity.get().session or session_for_conversation(str(body.get("session_id") or body.get("user") or "global"))
            if identity.get().user_agent:
                headers["User-Agent"] = identity.get().user_agent
            if wire != "messages":
                payload["prompt_cache_key"] = headers["x-opencode-session"]
        yield from stream_http(self.client, provider=self.id, url=f"{self.endpoint_base(account)}{suffix}", headers=headers, payload=payload, wire=wire)

    def messages_endpoint(self, account: Account, model: str, *, count_only: bool = False) -> str | None:
        # Dedicated subclasses own different envelopes/URLs. A Messages model
        # spec alone is not authority to bypass their protocol adapters.
        if self.id not in REGULAR_IDS or self.wire_for(model, account) != "messages":
            return None
        if self.options.get("messages_passthrough") is False:
            return None
        if count_only and not self.options.get("messages_count_tokens", self.id == "anthropic"):
            return None
        suffix = "/v1/messages" if self.id == "github-copilot" else "/messages"
        return f"{self.endpoint_base(account)}{suffix}{'/count_tokens' if count_only else ''}"

    def messages_headers(self, account: Account, model: str) -> dict[str, str]:
        return self.build_headers(account, model)

    def specs(self) -> dict[str, ModelSpec]:
        rows = {model: replace(spec_from_row(model, self.id, self.listing_rows.get(model, {})), wire=self.wire_for(model, next((a for a in self.accounts if model in self._account_rows.get(a.id, {})), None))) for model in self.models()}
        profile = CHAT_PROFILES.get(self.id)
        return {model: profile.decorate(spec) if profile is not None and spec.wire == "chat" else spec for model, spec in rows.items()}

    def refresh_account(self, account: Account) -> bool:
        from hoshimi.core.login.provider_oauth import refresh_provider_account

        return refresh_provider_account(self.id, self.client, account)


class OpenAIProvider(ConfiguredProvider):
    """A verified ChatGPT grant can use the direct Responses route."""

    @staticmethod
    def _can_infer(account: Account) -> bool:
        return account.get("plan_authorized") is not False and not account.get("pending_refresh") and not account.needs_login and not account.get("reauth_required")

    def inference_accounts(self) -> list[Account]:
        return [account for account in self.accounts if self._can_infer(account)]

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        if not self._can_infer(account):
            raise ProviderError(403, "OpenAI plan authorization or identity verification is required.")
        return super().build_headers(account, model)

    def wire_for(self, model: str, account: Account | None = None) -> str:
        if account is not None and account.get("plan_authorized") is True:
            return "responses"
        return super().wire_for(model, account)

    def endpoint_base(self, account: Account) -> str:
        if account.get("plan_authorized") is True:
            return self.definition.base_url
        return super().endpoint_base(account)

    def discover_rows(self, account: Account) -> list[dict[str, Any]]:
        if account.get("plan_authorized") is not True:
            return super().discover_rows(account)
        profile = CatalogProfile(collection=("models",), id_path=("slug",), fields=(Field("name", ("display_name",)),),
                                 required=((("visibility",), ("list",)),))
        headers = {**self.build_headers(account, ""), "Accept": "application/json"}
        return discover_catalog(self.client, self.catalog_url(account), headers, profile)

    def refresh_account(self, account: Account) -> bool:
        from hoshimi.core.login.openai import refresh_openai
        from hoshimi.core.providers.base import RefreshTokenRejected

        try:
            return refresh_openai(self.client, account)
        except RefreshTokenRejected as exc:
            try:
                account.commit_credentials({"reauth_required": True, "access_token": None, "refresh_token": None, "expires_at": None, "pending_refresh": None})
            except OSError:
                raise RefreshTokenRejected(exc.status, f"{exc} Could not save the reauthorization status.") from None
            raise


class CopilotProvider(ConfiguredProvider):
    """A Copilot account whose plan decides what it may run.

    Copilot hands every account the same listing — the union of every model
    it serves — and each row states the plans it is restricted to and what it
    costs. A free account is therefore offered models it cannot run: on a
    real free account 35 of the 48 listed models answer
    ``model_not_supported``, and the ones that work are exactly the rows
    priced at zero for the account's tier.

    So the lane filters the listing by what the account may actually use, and
    offers the Auto route — which asks Copilot to pick a model the account
    can run — beside them.
    """

    id = "github-copilot"
    display_name = "GitHub Copilot"
    base_url = "https://api.individual.githubcopilot.com"
    # The canonical Copilot lane. The generated registration overrides this
    # with the same definition; naming it here keeps the class usable on its
    # own, which the tests and the CLI's direct lane lookups rely on.
    definition: ProviderDefinition = next(d for d in CANONICAL_PROVIDERS if d.id == "github-copilot")
    models_path = "/models"

    def __init__(self, client: httpx.Client, accounts: list[Account], options: dict[str, Any] | None = None):
        super().__init__(client, accounts, options)
        self.auto_routes = AutoSessionCache()

    def accepts_row(self, row: dict[str, Any]) -> bool:
        """Whether this lane may run the row on any of its accounts."""
        return self.accepts_row_for(self.inference_accounts()[0], row) if self.inference_accounts() else False

    def accepts_row_for(self, account: Account, row: dict[str, Any]) -> bool:
        """Whether this account may run the row.

        A free account is held to the free rows; a paid account is held to
        the chat rows it can see, because a paid plan may run what it is
        billed for and the gate no longer decides for it. The answer is per
        account, so a free account in the pool does not remove a paid
        account's models from the lane.
        """
        if not super().accepts_row(row):
            return False
        # Copilot states the model's kind in its capabilities; the embedding
        # and decision rows answer 400 on a chat route on any plan.
        if model_kind(row) not in (None, "chat"):
            return False
        if is_free_account(account):
            return free_usable(row)
        return True

    def fetch_models(self) -> list[str]:
        models = super().fetch_models()
        if self._auto_available():
            # The Auto route is a model the account can run, so it belongs in
            # the listing beside the fixed ones.
            return [AUTO_MODEL_ID, *models]
        return models

    def _stream_candidates(self, model: str) -> list[Account]:
        """The accounts that may run this model.

        The Auto route is not a row of the catalogue — it is Copilot's own
        picker, and every account that can reach it may run it. So it is
        offered to the accounts that can, rather than looked up among the
        rows the catalogue returned.
        """
        if model == AUTO_MODEL_ID:
            return [account for account in self.inference_accounts() if is_free_account(account)]
        return super()._stream_candidates(model)

    def _auto_available(self) -> bool:
        """Whether any account can reach Copilot's Auto route.

        The route answers for a free account, which is the account that needs
        it: a paid account can already name the model it wants.
        """
        return any(is_free_account(account) for account in self.inference_accounts())

    def wire_for(self, model: str, account: Account | None = None) -> str:
        if model == AUTO_MODEL_ID:
            # The Auto session token is only accepted on /responses.
            return "responses"
        return super().wire_for(model, account)

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if model != AUTO_MODEL_ID:
            yield from super().stream_raw(account, model, body)
            return
        route = self.auto_route(account, body)
        payload = request_payload(body, route.model_id, "responses")
        headers = self.build_headers(account, route.model_id)
        headers["Copilot-Session-Token"] = route.session_token
        headers["Openai-Intent"] = "conversation-agent"
        yield from stream_http(self.client, provider=self.id, url=f"{self.endpoint_base(account)}/responses",
                               headers=headers, payload=payload, wire="responses")

    def auto_route(self, account: Account, body: dict[str, Any]) -> AutoRoute:
        """The Auto resolution for this turn, reused while it stays valid."""
        key = self._auto_key(account, body)
        cached = self.auto_routes.get(key)
        if cached is not None:
            return cached
        prompt, has_image = auto_prompt(body)
        route = resolve_auto(self.client, account, self.endpoint_base(account), prompt, has_image)
        self.auto_routes.put(key, route)
        return route

    def _auto_key(self, account: Account, body: dict[str, Any]) -> str:
        """The identity of one Auto session: an account and a conversation.

        The session token belongs to a single conversation — it names the
        model Copilot picked for it — so it is not carried from one talk into
        the next. A conversation with no id of its own gets one of its own,
        so an unidentified turn is not mixed into another's session.
        """
        conversation = str(body.get("session_id") or body.get("conversation_id") or "").strip()
        if not conversation:
            conversation = identity.get().session
        return f"{account.id}:{conversation}"

    def specs(self) -> dict[str, ModelSpec]:
        specs = super().specs()
        if AUTO_MODEL_ID in specs:
            return specs
        specs[AUTO_MODEL_ID] = replace(default_spec(AUTO_MODEL_ID, self.id), wire="responses",
                                       name="GitHub Copilot Auto")
        return specs


def regular_provider_classes() -> dict[str, type[KeyedProvider]]:
    """Generate thin classes from definitions; protocol logic remains shared."""
    return {definition.id: type(definition.id.replace("-", "_").title() + "Provider", (_CLASS_FOR.get(definition.id, ConfiguredProvider),),
                                {"id": definition.id, "display_name": definition.name, "base_url": definition.base_url,
                                 "definition": definition, "models_path": definition.catalog if definition.catalog.startswith("/") and " " not in definition.catalog else "/models"})
            for definition in CANONICAL_PROVIDERS if definition.id in REGULAR_IDS}


_CLASS_FOR: dict[str, type[ConfiguredProvider]] = {
    "openai": OpenAIProvider,
    "github-copilot": CopilotProvider,
}
