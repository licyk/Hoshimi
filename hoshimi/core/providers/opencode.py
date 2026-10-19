"""OpenCode inference: API-key accounts and the anonymous v2 free lane.

The anonymous path follows dsh-our-free-model's src/upstream.js at f0b979f:
v2 client headers, descending session encoding, and exact read/shell declarations.
Caller tools keep their own definitions through aliases and historical replay.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, ClassVar

from hoshimi.core.catalog import ModelSpec, opencode_spec
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.openai_compat import (
    OpenAiCompatProvider,
    consume_openai_sse,
    raise_for_status,
)
from hoshimi.core.providers.opencode_fingerprint import (
    apply_fingerprint,
    restore_tool_name,
    tool_name,
)
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.anthropic import build_anthropic_body, iter_anthropic_chunks
from hoshimi.core.translate.chat import text_from_content
from hoshimi.core.translate.responses import chat_to_responses, iter_responses_chunks

OPENCODE_BASE_URL = "https://opencode.ai/inference"

CLIENT_UA = "opencode/latest/2.0.26/cli"

# Models served by /inference/v1/responses instead of /chat/completions.
RESPONSES_MODELS = frozenset({"muse-spark-1.2-contributor-free", "muse-spark-1.3-contributor-free"})
# Models served by the Anthropic-shaped /inference/v1/messages endpoint.
MESSAGES_MODELS = frozenset({"union-alpha"})
# Ids that are free-tier without carrying a `free` token.
ALWAYS_FREE = frozenset({"union-alpha", "space-bunny-free", "big-pickle"})

SESSION_RE = re.compile(r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
SESSION_DESCENDING_RE = re.compile(r"^[0-9a-f]{9}f[c-f][0-9a-f]$")
REQUEST_RE = re.compile(r"^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$")
_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_MUSE_SPARK_RE = re.compile(r"^muse[-_]?spark(?:$|[-_:.\s])", re.IGNORECASE)

DEFAULT_MAX_TOKENS = 8192


def _format_id(prefix: str, raw: bytes) -> str:
    """A gateway-shaped canonical id: 6 random bytes as hex + 14 bytes base62."""
    head = raw[:6].hex()
    tail = "".join(_BASE62[byte % 62] for byte in raw[6:20])
    return f"{prefix}_{head}{tail}"


def mint_request_id() -> str:
    return _format_id("msg", os.urandom(20))


def session_for_conversation(seed: str | None, *, namespace: str = "") -> str:
    """Encode a stable, descending v2 session without persisting upstream identities.

    Account namespaces keep concurrent accounts isolated. Canonical incoming ids
    can pass through only when the caller has not requested a separate namespace.
    """
    value = seed.strip() if isinstance(seed, str) else ""
    if not namespace and SESSION_RE.fullmatch(value) and SESSION_DESCENDING_RE.fullmatch(value[4:16]):
        return value
    signal = f"{namespace}\0{value or 'global'}" if namespace else value or "global"
    digest = hashlib.sha256(f"ses\0{signal}".encode()).digest()
    timestamp = int.from_bytes(digest[:5], "big") >> 4
    descending = (~((timestamp << 12) | 1)) & 0xFFFFFFFFFFFF
    number = int.from_bytes(digest[6:16], "big")
    tail = ""
    for _ in range(14):
        number, digit = divmod(number, 62)
        tail = _BASE62[digit] + tail
    return f"ses_{descending:012x}{tail}"


def base_model_id(model: str) -> str:
    """Strip a trailing "(level)" thinking suffix so lookups hit the base id."""
    return re.sub(r"\([^()]+\)\s*$", "", str(model or "")).strip()


def is_responses_model(model_id: str) -> bool:
    base = base_model_id(model_id)
    if base in RESPONSES_MODELS:
        return True
    bare = base.rsplit("/", 1)[-1]
    return bool(_MUSE_SPARK_RE.match(bare))


def is_messages_model(model_id: str) -> bool:
    return base_model_id(model_id) in MESSAGES_MODELS


def wire_for(model_id: str) -> str:
    """Which upstream endpoint serves this model: `chat`, `responses`, `messages`."""
    if is_responses_model(model_id):
        return "responses"
    if is_messages_model(model_id):
        return "messages"
    return "chat"


def is_free_lane(model_id: str) -> bool:
    """Is this id on the anonymous lane? The listing mixes paid and free ids."""
    base = base_model_id(model_id)
    if base in ALWAYS_FREE:
        return True
    return bool(re.search(r"(?:^|[-_])free(?:$|[-_.])", base))


def _session_seed(body: dict[str, Any]) -> str:
    """The stable conversation seed a session id is derived from."""
    for key in ("session_id", "conversation_id", "user"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for message in body.get("messages") or []:
        if isinstance(message, dict) and message.get("role") in ("user", "human"):
            text = text_from_content(message.get("content"))
            if text:
                return text[:512]
    return "global"


def _renamed(chunks: Any, rename: dict[str, str], allowed_tools: set[str] | None = None) -> Any:
    for chunk in chunks:
        if isinstance(chunk, dict) and chunk.get("type") == "tool_call_start":
            if allowed_tools is not None and chunk.get("name") not in allowed_tools:
                raise ProviderError(502, "opencode: upstream requested a tool that the caller did not authorize", retryable=False)
            chunk = {**chunk, "name": restore_tool_name(chunk.get("name"), rename)}
        yield chunk


class OpencodeProvider(OpenAiCompatProvider):
    id = "opencode"
    display_name = "OpenCode Zen"
    keyless = True
    # The listing names ids flatly: which of them this egress may use only a
    # request can tell (see hoshimi.core.probe).
    probeable = True
    base_url = OPENCODE_BASE_URL
    chat_path = "/v1/chat/completions"
    models_path = "/v1/models"
    user_agent = CLIENT_UA
    fallback_model_list: ClassVar[list[str]] = [
        "mimo-v2.6-flash-free",
        "deepseek-v4.1-flash-free",
        "glm-5.3-flash-free",
        "nemotron-3.5-super-vl-free",
        "mimo-v2.5-free",
        "muse-spark-1.3-contributor-free",
        "muse-spark-1.2-contributor-free",
        "nemotron-3.5-lightning-free",
        "nemotron-3-ultra-free",
        "ling-3.1-flash-free",
        "step-5-preview-free",
        "longcat-2.5-preview-free",
        "space-bunny-free",
        "big-pickle",
        "jev-1.13-free",
        "exo-free",
    ]

    def _api_key(self, account: Account) -> str:
        key = account.get("api_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
        return "public"

    def _anonymous(self) -> bool:
        return not self.accounts or self._api_key(self.accounts[0]) == "public"

    def endpoint_url(self, wire: str) -> str:
        base = self.base_url.rstrip("/")
        if wire == "responses":
            return f"{base}/v1/responses"
        if wire == "messages":
            return f"{base}/v1/messages"
        return f"{base}/v1/chat/completions"

    def build_headers(
        self,
        account: Account,
        model: str,
        *,
        session: str | None = None,
        accept: str = "*/*",
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": accept,
            "Authorization": f"Bearer {self._api_key(account)}",
            "User-Agent": self.user_agent or CLIENT_UA,
            "x-opencode-client": "cli",
            "x-opencode-project": str(account.get("project_id") or "global"),
        }
        if session:
            for name in ("x-opencode-session", "x-opencode-session-id", "x-session-affinity", "x-session-id"):
                headers[name] = session
        return headers

    def build_body(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        out = super().build_body(body, model)
        out.pop("session_id", None)
        out.pop("conversation_id", None)
        return out

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Any:
        wire = wire_for(model)
        anonymous = self._api_key(account) == "public"
        session = session_for_conversation(_session_seed(body), namespace=f"{account.id}\0{self._api_key(account)}")

        if wire == "responses":
            payload = chat_to_responses(body, model=model)
            style = "flat"
        elif wire == "messages":
            payload = build_anthropic_body(body, model=model, default_max_tokens=DEFAULT_MAX_TOKENS)
            style = "claude"
        else:
            payload = self.build_body(body, model)
            style = "chat"

        # The fingerprint gate belongs to the anonymous lane; a keyed account
        # sees the caller's tools exactly as declared.
        caller_tools = {tool_name(tool) for tool in payload.get("tools") or []}
        choice = payload.get("tool_choice")
        tools_disabled = choice == "none" or (isinstance(choice, dict) and choice.get("type") == "none")
        rename = apply_fingerprint(payload, style) if anonymous else {}
        sent_aliases = {original: sent for sent, original in rename.items()}
        allowed_tools = {sent_aliases.get(name, name) for name in caller_tools} if anonymous else None
        if anonymous and tools_disabled:
            allowed_tools = set()

        headers = self.build_headers(
            account,
            model,
            session=session,
            accept="text/event-stream",
        )
        with self.client.stream(
            "POST",
            self.endpoint_url(wire),
            json=payload,
            headers=headers,
        ) as response:
            observe_response(self.id, response)
            if response.status_code >= 400:
                raise_for_status(response, provider=self.id)
            if wire == "responses":
                chunks = iter_responses_chunks(response.iter_lines(), provider=self.id)
            elif wire == "messages":
                chunks = iter_anthropic_chunks(response.iter_lines())
            else:
                chunks = consume_openai_sse(response.iter_lines(), provider=self.id)
            yield from _renamed(chunks, rename, allowed_tools)

    def fetch_models(self) -> list[str]:
        try:
            ids = super().fetch_models()
        except (TypeError, ValueError):
            self.catalog_failed = True
            ids = []
        if ids and self._anonymous():
            ids = [model_id for model_id in ids if is_free_lane(model_id)]
        # A transient discovery failure must not replace a verified live roster
        # with historical fallback ids. The fallback is for a first cold start.
        return ids or list(self._models_cache or [])

    def specs(self) -> dict[str, ModelSpec]:
        """The local capability table plus this model's endpoint."""
        return {model_id: opencode_spec(model_id, self.id, wire=wire_for(model_id)) for model_id in self.models()}
