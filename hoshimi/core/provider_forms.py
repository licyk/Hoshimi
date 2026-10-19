"""Credential form descriptions shared by API consumers; no credentials in them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hoshimi.core.provider_definitions import CANONICAL_PROVIDERS


@dataclass(frozen=True)
class CredentialField:
    key: str
    kind: Literal["text", "secret", "list", "json", "select"] = "text"
    required: bool = False
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class CredentialForm:
    method: str
    fields: tuple[CredentialField, ...]
    defaults: tuple[tuple[str, str], ...] = ()


KEY = CredentialField("api_key", "secret", True)
MODELS = CredentialField("models", "list")
REGION = CredentialField("region")
BASE = CredentialField("base_url")
GOOGLE_WIRE = CredentialField("protocol", "select", choices=("gemini", "chat"))


def credential_forms() -> dict[str, tuple[CredentialForm, ...]]:
    """Describe only forms whose fields the adapter actually accepts."""
    forms: dict[str, tuple[CredentialForm, ...]] = {d.id: (CredentialForm("api_key", (KEY, BASE, MODELS)),) for d in CANONICAL_PROVIDERS if d.implemented and "api_key" in d.auth}
    forms.update({
        "eac": (
            CredentialForm("bundled", (), (("relay", "bundled"),)),
            CredentialForm("worker", (CredentialField("base_url", required=True), CredentialField("signing_secret", "secret", True))),
            CredentialForm("direct", (CredentialField("base_url", required=True), CredentialField("access_token", "secret", True))),
        ),
        "kilo": (CredentialForm("api_key", (KEY,)),),
        "zhipu": (CredentialForm("api_key", (KEY,)),),
        "azure": (CredentialForm("api_key", (KEY, CredentialField("resource", required=True), CredentialField("models", "list", True), CredentialField("deployments", "json"))),),
        "cloudflare-workers-ai": (CredentialForm("api_key", (KEY, CredentialField("account_id", required=True), MODELS)),),
        "cloudflare-ai-gateway": (CredentialForm("api_key", (KEY, CredentialField("account_id", required=True), CredentialField("gateway_id", required=True), MODELS)),),
        "google": (CredentialForm("api_key", (KEY, MODELS, GOOGLE_WIRE), (("protocol", "gemini"),)),),
        "google-vertex": (
            CredentialForm("api_key", (KEY, CredentialField("project"), CredentialField("location"), MODELS, GOOGLE_WIRE), (("protocol", "gemini"),)),
            CredentialForm("adc", (CredentialField("project", required=True), CredentialField("location"), MODELS, GOOGLE_WIRE), (("auth", "adc"), ("protocol", "gemini"))),
            CredentialForm("service_account", (CredentialField("service_account_file", required=True), CredentialField("project", required=True), CredentialField("location"), MODELS, GOOGLE_WIRE), (("protocol", "gemini"),)),
        ),
        "amazon-bedrock": (
            CredentialForm("api_key", (KEY, REGION, CredentialField("models", "list", True))),
            CredentialForm("aws", (CredentialField("aws_access_key_id", "secret", True), CredentialField("aws_secret_access_key", "secret", True), CredentialField("aws_session_token", "secret"), REGION, MODELS)),
            CredentialForm("profile", (CredentialField("profile", required=True), REGION, MODELS)),
            CredentialForm("aws_chain", (REGION, MODELS), (("auth", "aws"),)),
        ),
        "typesafe": (CredentialForm("api_key", (KEY, CredentialField("classifiers", "list"))),),
    })
    for provider, default, choices in (("vercel-ai-gateway", "messages", ("messages", "chat")), ("xai", "responses", ("responses", "chat")), ("openai", "responses", ("responses", "chat"))):
        forms[provider] = (CredentialForm("api_key", (KEY, BASE, MODELS, CredentialField("protocol", "select", choices=choices)), (("protocol", default),)),)
    for provider in ("lmstudio", "llamacpp", "ollama"):
        definition = next(d for d in CANONICAL_PROVIDERS if d.id == provider)
        forms[provider] = (CredentialForm("local", (BASE, CredentialField("api_key", "secret"), MODELS), (("base_url", definition.base_url),)),)
    return forms
