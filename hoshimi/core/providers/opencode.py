"""OpenCode inference: API-key accounts and the anonymous v2 free lane.

The anonymous path aligns with OpenCode v2.2.1's captured requests: TLS ciphers,
timestamp-based sessions, context envelopes, and read/shell declarations.
Caller tools keep their own definitions through aliases and historical replay.

The read/shell declarations are there for the lane's gate, not for the caller:
a caller that declared no such tools (a chat with no tools at all, the
Playground) cannot run them. A model that calls one anyway is answered here,
the way a coding agent answers a tool it does not have (Codex's
``unsupported call: <name>``), and asked again; the caller sees only the text,
and its own tool calls. A model that keeps calling them is given up on after
:data:`MAX_UNSUPPORTED_ROUNDS` such answers.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import threading
import time
from collections.abc import Iterator
from functools import lru_cache
from typing import Any, ClassVar

from hoshimi.core.catalog import ModelSpec, opencode_spec
from hoshimi.core.failure import parse_retry_after
from hoshimi.core.net.identity import identity
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.openai_compat import (
    OpenAiCompatProvider,
    model_rows,
    raise_for_status,
    read_body,
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

CLIENT_UA = "opencode/latest/2.2.1/cli"

# Models served by /inference/openai/v1/responses instead of /chat/completions.
RESPONSES_MODELS = frozenset({"muse-spark-1.2-contributor-free", "muse-spark-1.3-contributor-free"})
# Models served by the Anthropic-shaped /inference/anthropic/v1/messages endpoint.
MESSAGES_MODELS = frozenset({"union-alpha"})
# Ids that are free-tier without carrying a `free` token.
ALWAYS_FREE = frozenset({"union-alpha", "space-bunny-free", "big-pickle"})

SESSION_RE = re.compile(r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
SESSION_DESCENDING_RE = re.compile(r"^[0-9a-f]{9}f[c-f][0-9a-f]$")
REQUEST_RE = re.compile(r"^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$")
_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SESSION_LOCK = threading.Lock()
_MUSE_SPARK_RE = re.compile(r"^muse[-_]?spark(?:$|[-_:.\s])", re.IGNORECASE)

DEFAULT_MAX_TOKENS = 8192

MAX_UNSUPPORTED_ROUNDS = 2
"""How many times a model calling only tools the caller never offered is answered and asked again."""


def _format_id(prefix: str, raw: bytes) -> str:
    """A gateway-shaped canonical id: 6 random bytes as hex + 14 bytes base62."""
    head = raw[:6].hex()
    tail = "".join(_BASE62[byte % 62] for byte in raw[6:20])
    return f"{prefix}_{head}{tail}"


def mint_request_id() -> str:
    return _format_id("msg", os.urandom(20))


def session_for_conversation(seed: str | None, *, namespace: str = "") -> str:
    """Keep a real timestamp-based v2 session for each account/conversation.

    Hashing the timestamp creates a syntactically valid but unusable session.
    Cache only the seed digest, never credentials, and do not rotate on refusals.
    """
    value = seed.strip() if isinstance(seed, str) else ""
    if not namespace and SESSION_RE.fullmatch(value) and SESSION_DESCENDING_RE.fullmatch(value[4:16]):
        return value
    signal = f"{namespace}\0{value or 'global'}" if namespace else value or "global"
    digest = hashlib.sha256(f"ses\0{signal}".encode()).digest()
    with _SESSION_LOCK:
        return _session_for_digest(digest)


@lru_cache(maxsize=8192)
def _session_for_digest(digest: bytes) -> str:
    descending = (~((int(time.time() * 1000) << 12) | 1)) & 0xFFFFFFFFFFFF
    tail = "".join(_BASE62[byte % 62] for byte in digest[6:20])
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


def unsupported_result(name: str) -> str:
    """What a model is told for a tool it called that this request does not offer."""
    return f"unsupported call: {name}. This tool is not available here; answer without it."


def _add_usage(total: dict[str, Any] | None, more: dict[str, Any]) -> dict[str, Any]:
    """Two rounds' usage as one: every count added, field by field."""
    if total is None:
        return copy.deepcopy(more)
    out = copy.deepcopy(total)
    for key, value in more.items():
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = (out.get(key) or 0) + value
        elif isinstance(value, dict):
            out[key] = _add_usage(out.get(key) if isinstance(out.get(key), dict) else None, value)
        else:
            out.setdefault(key, value)
    return out


def _with_unsupported_answers(body: dict[str, Any], text: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """The conversation carried one round on: the model's turn with its calls, and an answer to each."""
    messages = list(body.get("messages") or [])
    messages.append({
        "role": "assistant",
        "content": text or None,
        "tool_calls": [{"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"] or "{}"}} for call in calls],
    })
    messages += [{"role": "tool", "tool_call_id": call["id"], "content": unsupported_result(call["name"])} for call in calls]
    return {**body, "messages": messages}


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

    def model_wire(self, model: str) -> str:
        """Prefer explicit model configuration and catalog protocol declarations."""
        configured = self.options.get("model_protocols", {})
        selected = configured.get(model) if isinstance(configured, dict) else None
        selected = selected or getattr(self, "_model_wires", {}).get(model) or wire_for(model)
        if selected not in ("chat", "responses", "messages"):
            raise ProviderError(400, f"{self.id}: unsupported model protocol {selected!r}")
        return selected

    def parse_models_response(self, data: Any) -> list[str]:
        aliases = {"chat-completions": "chat", "chat": "chat", "responses": "responses", "messages": "messages"}
        wires = {}
        for row in model_rows(data):
            api = row.get("api")
            raw = row.get("wire") or row.get("protocol") or api
            if isinstance(raw, dict):
                raw = next((key for key in raw if key in aliases), None)
            if isinstance(row.get("id"), str) and isinstance(raw, str) and raw in aliases:
                wires[row["id"]] = aliases[raw]
        self._model_wires = wires
        return super().parse_models_response(data)

    def endpoint_url(self, wire: str) -> str:
        base = self.base_url.rstrip("/")
        if wire == "responses":
            return f"{base}/openai/v1/responses"
        if wire == "messages":
            return f"{base}/anthropic/v1/messages"
        return f"{base}/openai/v1/chat/completions"

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
            "User-Agent": identity.get().user_agent or self.user_agent or CLIENT_UA,
            "x-opencode-client": "cli",
            "x-opencode-project": identity.get().project or str(account.get("project_id") or "global"),
        }
        if self._api_key(account) == "public" and not headers["User-Agent"].startswith("opencode/"):
            headers["User-Agent"] = self.user_agent or CLIENT_UA
        if session:
            for name in ("x-opencode-session", "x-opencode-session-id", "x-session-affinity", "x-session-id"):
                headers[name] = session
        return headers

    def build_body(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        out = super().build_body(body, model)
        out.pop("session_id", None)
        out.pop("conversation_id", None)
        if "max_tokens" in out:
            out.setdefault("max_completion_tokens", out.pop("max_tokens"))
        out["stream_options"] = {**(out.get("stream_options") or {}), "include_usage": True}
        return out

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Any:
        """One reply, over as many upstream rounds as it takes to answer calls the caller cannot run.

        A round whose only tool calls are to tools the caller never declared
        (the lane's read/shell gate, or a name the model made up) is not the
        caller's: its text is passed on, the calls are answered here as
        unsupported, and the model is asked again in the same session. Calls
        to the caller's own tools always go to the caller; next to them,
        undeclared ones are left out. Usage is added up over the rounds.
        """
        anonymous = self._api_key(account) == "public"
        current = body
        usage: dict[str, Any] | None = None
        for round_number in range(MAX_UNSUPPORTED_ROUNDS + 1):
            text: list[str] = []
            held: dict[int, dict[str, Any]] = {}
            held_ids: set[str] = set()
            passed_calls = 0
            finish: dict[str, Any] | None = None
            for chunk, own in self._stream_once(account, model, current, anonymous=anonymous):
                kind = chunk.get("type")
                if kind == "tool_call_start" and not own:
                    held[int(chunk.get("index") or 0)] = {"id": str(chunk.get("id") or f"call_{len(held)}"), "name": str(chunk.get("name") or ""), "arguments": str(chunk.get("arguments") or "")}
                    held_ids.add(str(chunk.get("id") or ""))
                    continue
                if kind == "tool_call_delta" and int(chunk.get("index") or 0) in held:
                    held[int(chunk.get("index") or 0)]["arguments"] += str(chunk.get("arguments") or "")
                    continue
                if kind == "reasoning_details" and any(isinstance(d, dict) and d.get("call_id") in held_ids for d in chunk.get("details") or []):
                    continue
                if kind == "usage" and isinstance(chunk.get("usage"), dict):
                    usage = _add_usage(usage, chunk["usage"])
                    continue
                if kind == "finish":
                    finish = chunk
                    continue
                if kind == "tool_call_start":
                    passed_calls += 1
                elif kind == "text_delta":
                    text.append(str(chunk.get("text") or ""))
                yield chunk
            if held and not passed_calls:
                if round_number == MAX_UNSUPPORTED_ROUNDS:
                    names = ", ".join(sorted({call["name"] for call in held.values()}))
                    raise ProviderError(502, f"opencode: the model kept calling tools this request does not offer ({names})", retryable=False)
                current = _with_unsupported_answers(current, "".join(text), [held[i] for i in sorted(held)])
                continue
            if usage is not None:
                yield {"type": "usage", "usage": usage}
            if finish is not None:
                # Only undeclared calls were left out: what remains is the caller's to act on.
                yield finish if passed_calls or finish.get("reason") != "tool_calls" else {**finish, "reason": "stop"}
            return

    def _stream_once(self, account: Account, model: str, body: dict[str, Any], *, anonymous: bool) -> Iterator[tuple[dict[str, Any], bool]]:
        """One upstream request: each chunk, and for a tool call whether it is to one of the caller's tools.

        Calls are told apart by the name the model was sent, before aliases
        are undone: a caller's own ``read`` travels as ``read_client`` beside
        the gate's ``read``. On a keyed account every tool is the caller's.
        """
        wire = self.model_wire(model)
        context = identity.get()
        seed = context.session or _session_seed(body)
        passthrough = context.forwarded and context.session and (not anonymous or SESSION_RE.fullmatch(context.session))
        session = context.session if passthrough else session_for_conversation(seed, namespace=f"{account.id}\0{self._api_key(account)}")

        if wire == "responses":
            payload = chat_to_responses(body, model=model)
            style = "flat"
        elif wire == "messages":
            payload = build_anthropic_body(body, model=model, default_max_tokens=DEFAULT_MAX_TOKENS)
            style = "claude"
        else:
            payload = self.build_body(body, model)
            style = "chat"
        if wire == "responses":
            payload.setdefault("prompt_cache_key", session)

        # The fingerprint gate belongs to the anonymous lane; a keyed account
        # sees the caller's tools exactly as declared.
        caller_tools = {tool_name(tool) for tool in payload.get("tools") or []}
        choice = payload.get("tool_choice")
        tools_disabled = choice == "none" or (isinstance(choice, dict) and choice.get("type") == "none")
        rename = apply_fingerprint(payload, style) if anonymous else {}
        sent_aliases = {original: sent for sent, original in rename.items()}
        # A caller that turned tools off (the gate drops ``none`` upstream) takes no call at all.
        callable_names = set() if tools_disabled else {sent_aliases.get(name, name) for name in caller_tools}

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
            extensions={"hoshimi_transport": "opencode"} if anonymous else {},
        ) as response:
            observe_response(self.id, response)
            if response.status_code >= 400:
                raise_for_status(response, provider=self.id)
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            if wire == "responses":
                chunks = iter_responses_chunks(response.iter_lines(), provider=self.id, retry_after=retry_after)
            elif wire == "messages":
                chunks = iter_anthropic_chunks(response.iter_lines(), provider=self.id, retry_after=retry_after)
            else:
                chunks = read_body(response, provider=self.id)
            for chunk in chunks:
                if isinstance(chunk, dict) and chunk.get("type") == "tool_call_start":
                    sent = chunk.get("name")
                    own = not anonymous or sent in callable_names
                    yield ({**chunk, "name": restore_tool_name(sent, rename)} if own else chunk), own
                else:
                    yield chunk, True

    def fetch_models(self) -> list[str]:
        try:
            ids = super().fetch_models()
        except (TypeError, ValueError):
            self.catalog_failed = True
            ids = []
        if ids and self._anonymous():
            ids = [model_id for model_id in ids if is_free_lane(model_id)]
        if not ids:
            self.catalog_authoritative = False
        # A transient discovery failure must not replace a verified live roster
        # with historical fallback ids. The fallback is for a first cold start.
        return ids or list(self._models_cache or [])

    def specs(self) -> dict[str, ModelSpec]:
        """The local capability table plus this model's endpoint."""
        return {model_id: opencode_spec(model_id, self.id, wire=self.model_wire(model_id)) for model_id in self.models()}
