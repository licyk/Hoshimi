"""Data-defined conventional providers and account-specific protocol routing."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.provider_definitions import CANONICAL_PROVIDERS, REGULAR_PROVIDER_IDS, ProviderDefinition
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.catalog_profiles import CATALOG_PROFILES, catalog_url
from hoshimi.core.providers.compat import gateway_chat_body, provider_chat_body
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
            headers["x-opencode-session"] = session_for_conversation(str(body.get("session_id") or body.get("user") or "global"))
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

            headers["x-opencode-session"] = session_for_conversation(str(body.get("session_id") or body.get("user") or "global"))
        yield from stream_http(self.client, provider=self.id, url=f"{self.endpoint_base(account)}{suffix}", headers=headers, payload=payload, wire=wire)

    def specs(self) -> dict[str, ModelSpec]:
        rows = {model: replace(spec_from_row(model, self.id, self.listing_rows.get(model, {})), wire=self.wire_for(model, next((a for a in self.accounts if model in self._account_rows.get(a.id, {})), None))) for model in self.models()}
        profile = CHAT_PROFILES.get(self.id)
        return {model: profile.decorate(spec) if profile is not None and spec.wire == "chat" else spec for model, spec in rows.items()}

    def refresh_account(self, account: Account) -> bool:
        from hoshimi.core.login.provider_oauth import refresh_provider_account

        return refresh_provider_account(self.id, self.client, account)


def regular_provider_classes() -> dict[str, type[KeyedProvider]]:
    """Generate thin classes from definitions; protocol logic remains shared."""
    return {definition.id: type(definition.id.replace("-", "_").title() + "Provider", (ConfiguredProvider,),
                                {"id": definition.id, "display_name": definition.name, "base_url": definition.base_url,
                                 "definition": definition, "models_path": definition.catalog if definition.catalog.startswith("/") and " " not in definition.catalog else "/models"})
            for definition in CANONICAL_PROVIDERS if definition.id in REGULAR_IDS}
