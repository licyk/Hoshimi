"""Azure, Cloudflare and Google adapters with explicit account routing fields."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.errors import ValidationError
from hoshimi.core.provider_definitions import PROVIDER_BY_ID
from hoshimi.core.providers.base import Account, AuthFailed, ProviderError
from hoshimi.core.providers.catalog_profiles import CatalogProfile, Field, discover_catalog
from hoshimi.core.providers.compat import provider_chat_body
from hoshimi.core.providers.configured import ConfiguredProvider
from hoshimi.core.providers.keyed import KeyedProvider, account_token, spec_from_row
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.providers.request_profiles import ChatProfile
from hoshimi.core.providers.transports import request_payload, stream_http
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.gemini import build_gemini_envelope, iter_gemini_chunks


class AzureProvider(ConfiguredProvider):
    id = "azure"
    display_name = "Azure OpenAI"
    definition = PROVIDER_BY_ID[id]
    base_url = ""

    def endpoint_base(self, account: Account) -> str:
        base = account.get("base_url") or self.options.get("base_url")
        if base:
            return str(base).rstrip("/")
        resource = str(account.get("resource") or self.options.get("resource") or "")
        if not re.fullmatch(r"[A-Za-z0-9-]+", resource):
            raise ProviderError(400, "azure: configure resource or base_url")
        return f"https://{resource}.openai.azure.com/openai/v1"

    def wire_for(self, model: str, account: Account | None = None) -> str:
        if not self.listing_rows.get(model, {}).get("wire") and not self.listing_rows.get(model, {}).get("api") and not self.options.get("model_protocols") and not (account and account.get("protocol")):
            return "responses"
        return super().wire_for(model, account)

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        headers["api-key"] = headers.pop("Authorization").removeprefix("Bearer ")
        return headers

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        mappings = account.get("deployments") or self.options.get("deployments") or {}
        deployment = str(mappings.get(model) or model) if isinstance(mappings, dict) else model
        # Protocol/capabilities belong to the public model ID, even when the
        # request's model is the deployment name.
        from hoshimi.core.providers.transports import request_payload, stream_http

        wire = self.wire_for(model, account)
        suffix = "/responses" if wire == "responses" else "/chat/completions"
        yield from stream_http(self.client, provider=self.id, url=self.endpoint_base(account) + suffix,
                               headers=self.build_headers(account, model), payload=request_payload(body, deployment, wire), wire=wire)


class CloudflareWorkersProvider(ConfiguredProvider):
    id = "cloudflare-workers-ai"
    display_name = "Cloudflare Workers AI"
    definition = PROVIDER_BY_ID[id]

    def endpoint_base(self, account: Account) -> str:
        if account.get("base_url") or self.options.get("base_url"):
            return super().endpoint_base(account)
        account_id = str(account.get("account_id") or self.options.get("account_id") or "")
        if not account_id:
            raise ProviderError(400, f"{self.id}: configure account_id")
        return f"https://api.cloudflare.com/client/v4/accounts/{quote(account_id, safe='')}/ai/v1"

    def fetch_models(self) -> list[str]:
        if any(isinstance(account.get("models", self.options.get("models")), list) for account in self.accounts):
            return super().fetch_models()
        rows: dict[str, dict[str, Any]] = {}
        for account in self.accounts:
            url = self.endpoint_base(account).removesuffix("/v1") + "/models/search"
            response = self.client.get(url, headers=self.build_headers(account, ""), timeout=30)
            raise_for_status(response, provider=self.id)
            for row in response.json().get("result", []):
                task = row.get("task", {})
                if isinstance(task, dict) and task.get("name") not in (None, "Text Generation"):
                    continue
                model = row.get("name")
                if isinstance(model, str):
                    rows[model] = {**row, "id": model}
        self.listing_rows.update(rows)
        return list(rows)


class CloudflareGatewayProvider(ConfiguredProvider):
    id = "cloudflare-ai-gateway"
    display_name = "Cloudflare AI Gateway"
    definition = PROVIDER_BY_ID[id]

    def endpoint_base(self, account: Account) -> str:
        if account.get("base_url") or self.options.get("base_url"):
            return super().endpoint_base(account)
        account_id = str(account.get("account_id") or self.options.get("account_id") or "")
        gateway = str(account.get("gateway_id") or self.options.get("gateway_id") or "")
        if not account_id or not gateway:
            raise ProviderError(400, "cloudflare-ai-gateway: configure account_id and gateway_id")
        return f"https://gateway.ai.cloudflare.com/v1/{quote(account_id, safe='')}/{quote(gateway, safe='')}/compat"

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        headers["cf-aig-authorization"] = headers.pop("Authorization")
        headers.pop("x-api-key", None)
        return headers

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        from hoshimi.core.providers.transports import request_payload, stream_http

        wire = self.wire_for(model, account)
        base = self.endpoint_base(account)
        if wire == "messages":
            base = base.removesuffix("/compat") + "/anthropic/v1"
        elif wire == "responses":
            base = base.removesuffix("/compat") + "/openai"
        suffix = {"messages": "/messages", "responses": "/responses"}.get(wire, "/chat/completions")
        yield from stream_http(self.client, provider=self.id, url=base + suffix, headers=self.build_headers(account, model),
                               payload=request_payload(provider_chat_body(body, self.id, model) if wire == "chat" else body, model, wire), wire=wire)


def gemini_payload(body: dict[str, Any], model: str) -> dict[str, Any]:
    """Reuse content/tool conversion, removing the Code Assist account envelope."""
    payload = build_gemini_envelope(body, model=model)["request"]
    payload.pop("sessionId", None)
    generation = payload["generationConfig"]
    if not body.get("reasoning_effort") and not body.get("thinking_config"):
        generation.pop("thinkingConfig", None)
    if isinstance(body.get("thinking_config"), dict):
        generation["thinkingConfig"] = body["thinking_config"]
    elif body.get("reasoning_effort") in ("none", "off", "disabled"):
        generation["thinkingConfig"] = {"thinkingBudget": 0, "includeThoughts": False}
    elif body.get("reasoning_effort") in ("max", "xhigh"):
        generation["thinkingConfig"] = {"thinkingBudget": 10000, "includeThoughts": True}
    for source, target in (("max_tokens", "maxOutputTokens"), ("max_completion_tokens", "maxOutputTokens"), ("top_p", "topP")):
        if source in body:
            generation[target] = body[source]
    # Each signed function call keeps its provider's opaque thought signature.
    signed = [detail["part"] for message in body.get("messages", []) for detail in message.get("reasoning_details", [])
              if isinstance(detail, dict) and detail.get("type") == "gemini_part" and isinstance(detail.get("part"), dict)]
    for content in payload["contents"]:
        if content.get("role") != "model":
            continue
        for index, part in enumerate(content["parts"]):
            for detail in signed:
                if detail.get("functionCall") == part.get("functionCall") and part.get("functionCall") is not None:
                    content["parts"][index] = detail
    return payload


class GoogleProvider(KeyedProvider):
    id = "google"
    display_name = "Google Gemini API"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        return {"x-goog-api-key": account_token(account), "Content-Type": "application/json", "Accept": "text/event-stream"}

    def model_url(self, account: Account, model: str) -> str:
        return f"{self.base_url}/models/{quote(model.removeprefix('models/'), safe='-._')}:streamGenerateContent?alt=sse"

    def wire_for(self, model: str, account: Account | None = None) -> str:
        configured = self.options.get("model_protocols", {})
        wire = configured.get(model) if isinstance(configured, dict) else None
        wire = wire or (account.get("protocol") if account else None) or self.options.get("protocol")
        return "chat" if wire in ("chat", "openai-completions") else "gemini"

    def discover_rows(self, account: Account) -> list[dict[str, Any]]:
        profile = CatalogProfile(collection=("models",), id_path=("name",), pagination="cursor", page_param="pageToken",
            fields=(Field("context_length", ("inputTokenLimit",)), Field("max_output_tokens", ("outputTokenLimit",))))
        rows = discover_catalog(self.client, f"{self.base_url}/models", self.build_headers(account, ""), profile)
        return [{**row, "id": row["id"].removeprefix("models/"), "name": row.get("displayName", row["id"])} for row in rows if "generateContent" in row.get("supportedGenerationMethods", [])]

    def compat_url(self, account: Account) -> str:
        return f"{self.base_url}/openai/chat/completions"

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if self.wire_for(model, account) == "chat":
            prepared = ChatProfile(strip=("store",)).prepare(body)
            thinking = prepared.pop("thinking_config", None)
            effort = prepared.get("reasoning_effort")
            if thinking is not None and effort is not None:
                raise ProviderError(400, "Google thinking_config and reasoning_effort are mutually exclusive")
            if self.id == "google-vertex" and effort is not None:
                prepared.pop("reasoning_effort")
                thinking = {"thinking_budget": 0, "include_thoughts": False} if effort in ("none", "off", "disabled") else {"include_thoughts": True}
            elif effort in ("max", "xhigh"):
                prepared["reasoning_effort"] = "high"
            if thinking is not None:
                if not isinstance(thinking, dict):
                    raise ProviderError(400, "thinking_config must be an object")
                prepared.setdefault("extra_body", {}).setdefault("google", {})["thinking_config"] = deepcopy(thinking)
            payload = request_payload(prepared, model, "chat")
            headers = self.build_headers(account, model)
            if self.id == "google":
                headers.pop("x-goog-api-key", None)
                headers["Authorization"] = f"Bearer {account_token(account)}"
            yield from stream_http(self.client, provider=self.id, url=self.compat_url(account), headers=headers, payload=payload, wire="chat")
            return
        with self.client.stream("POST", self.model_url(account, model), headers=self.build_headers(account, model), json=gemini_payload(body, model)) as response:
            observe_response(self.id, response)
            raise_for_status(response, provider=self.id)
            yield from iter_gemini_chunks(response.iter_lines())

    def specs(self) -> dict[str, ModelSpec]:
        rows = {model: replace(spec_from_row(model, self.id, self.listing_rows.get(model, {})), wire=self.wire_for(model, next((a for a in self.accounts if model in self._account_rows.get(a.id, {})), None))) for model in self.models()}
        profile = ChatProfile(reasoning="thinking") if self.id == "google-vertex" else ChatProfile(reasoning="effort", efforts=(("minimal", "low"), ("xhigh", "high"), ("max", "high")))
        return {model: profile.decorate(spec) for model, spec in rows.items()}


class VertexProvider(GoogleProvider):
    id = "google-vertex"
    display_name = "Google Vertex AI"
    base_url = "https://aiplatform.googleapis.com"

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._google_credentials: dict[str, Any] = {}
        self._credentials_lock = threading.Lock()

    @classmethod
    def check_account(cls, data: dict[str, Any]) -> None:
        if not account_token(Account(data)) and data.get("auth") != "adc" and not data.get("service_account_file"):
            raise ValidationError("google-vertex: supply api_key, auth=adc or service_account_file")

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        self.check_account(account.data)
        if account.get("api_key"):
            return super().build_headers(account, model)
        if account.get("access_token"):
            return {"Authorization": f"Bearer {account.get('access_token')}", "Content-Type": "application/json"}
        try:
            import google.auth
            from google.auth.exceptions import GoogleAuthError, TransportError
            from google.auth.transport.requests import Request
        except ImportError:
            raise ProviderError(400, 'Install hoshimi[google] to use Vertex ADC/service accounts') from None
        try:
            with self._credentials_lock:
                credentials = self._google_credentials.get(account.id)
                if credentials is None:
                    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
                    if account.get("service_account_file"):
                        credentials, _ = google.auth.load_credentials_from_file(str(account.get("service_account_file")), scopes=scopes)
                    else:
                        credentials, _ = google.auth.default(scopes=scopes)
                    self._google_credentials[account.id] = credentials
                if not credentials.valid:
                    credentials.refresh(Request())
                token = credentials.token
        except TransportError:
            raise ProviderError(503, "Vertex credential service is temporarily unavailable", retryable=True) from None
        except (GoogleAuthError, OSError):
            raise AuthFailed(401, "Vertex credentials are unavailable or expired; authenticate again") from None
        if not token:
            raise AuthFailed(401, "Vertex credentials returned no token")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if account.get("project"):
            headers["x-goog-user-project"] = str(account.get("project"))
        return headers

    def model_url(self, account: Account, model: str) -> str:
        location = str(account.get("location") or self.options.get("location") or "global")
        project = str(account.get("project") or self.options.get("project") or "")
        model_path = f"publishers/google/models/{quote(model.removeprefix('models/'), safe='-._')}"
        if project:
            model_path = f"projects/{quote(project, safe='')}/locations/{quote(location, safe='')}/{model_path}"
        elif not account.get("api_key"):
            raise ProviderError(400, "google-vertex: ADC requires project")
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        base = str(self.options.get("base_url") or f"https://{host}").rstrip("/")
        return f"{base}/v1/{model_path}:streamGenerateContent?alt=sse"

    def compat_url(self, account: Account) -> str:
        location = str(account.get("location") or self.options.get("location") or "global")
        project = str(account.get("project") or self.options.get("project") or "")
        if not project or not re.fullmatch(r"[a-z0-9-]+", location):
            raise ProviderError(400, "google-vertex: compatibility mode requires project and valid location")
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        return f"https://{host}/v1/projects/{quote(project, safe='')}/locations/{location}/endpoints/openapi/chat/completions"

    def discover_rows(self, account: Account) -> list[dict[str, Any]]:
        location = str(account.get("location") or self.options.get("location") or "global")
        if not re.fullmatch(r"[a-z0-9-]+", location):
            raise ProviderError(400, "google-vertex: invalid location")
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        profile = CatalogProfile(collection=("publisherModels",), id_path=("name",), pagination="cursor", page_param="pageToken")
        rows = discover_catalog(self.client, f"https://{host}/v1beta1/publishers/google/models", self.build_headers(account, ""), profile)
        result = []
        for row in rows:
            parts = row["id"].split("/")
            if len(parts) != 4 or parts[0] != "publishers" or parts[2] != "models":
                raise ValueError("invalid Vertex publisher model name")
            model = f"{parts[1]}/{parts[3]}" if self.wire_for("", account) == "chat" else parts[3]
            result.append({**row, "id": model})
        return result
