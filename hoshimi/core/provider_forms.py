"""Credential form descriptions shared by API consumers; no credentials in them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

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
        # The Qoder CLI lanes take a Personal Access Token, not an API key: the
        # adapter exchanges it for a job token on first use and on renewal.
        "qodercli": (CredentialForm("token", (CredentialField("personal_token", "secret", True),)),),
        "qodercli-cn": (CredentialForm("token", (CredentialField("personal_token", "secret", True),)),),
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


def account_form(provider: str, data: dict[str, Any]) -> CredentialForm:
    """Recover the existing method, including legacy accounts without UI metadata."""
    forms = credential_forms().get(provider, ())
    if provider == "agg":
        forms = (CredentialForm("api_key", (KEY, CredentialField("preset"), BASE, MODELS)),)
    matches = [form for form in forms
               if all(data.get(f.key) for f in form.fields if f.required)
               and all(data.get(key, value) == value for key, value in form.defaults if key in {"auth", "relay"})]
    selected = max(matches, key=lambda form: sum(bool(data.get(f.key)) for f in form.fields), default=CredentialForm("config", ()))
    fields = list(selected.fields)
    known = {f.key for f in fields} | {key for key, _ in selected.defaults} | {"id", "_hoshimi_login"}
    # Keep custom/imported configuration editable too; secret values are never
    # embedded in the channel list, only in the explicitly opened editor.
    for key, value in data.items():
        if key in known:
            continue
        secret = any(part in key.lower() for part in ("token", "secret", "password", "key", "cookie", "credential"))
        kind = "json" if isinstance(value, dict) else "list" if isinstance(value, list) else "secret" if secret else "text"
        fields.append(CredentialField(key, kind))
    return CredentialForm(selected.method, tuple(fields), selected.defaults)


def account_edit_mode(provider: str, data: dict[str, Any], *, loginable: bool) -> Literal["config", "login"]:
    """Use recorded provenance first, then the credential shape for older files."""
    if not loginable or data.get("_hoshimi_login") == "config":
        return "config"
    if data.get("_hoshimi_login") == "browser":
        return "login"
    if provider == "eac":
        return "login" if data.get("user_token") else "config"
    if data.get("api_key") or data.get("personal_token"):
        return "config"
    return "login"
