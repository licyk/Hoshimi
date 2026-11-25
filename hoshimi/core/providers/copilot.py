"""GitHub Copilot: the plan-aware catalogue, and the Auto route that saves a free account.

Copilot's ``/models`` listing is not a catalogue of what an account may run.
It is the union of every model Copilot serves, each row carrying the plans it
is restricted to, and a free account is handed all of it: a real Copilot Free
account answers 200 with 48 models, of which 35 are Claude Opus, GPT-6 and
Gemini tiers the account cannot touch. Only ``model_picker_enabled`` filters
that list, and a free account has it false on every row — which is why the
listing looked unusable while still naming models that do work.

So the filter is read from the billing block instead. A row is usable by this
account when three things hold, and they were checked against every model of
a live free account rather than assumed:

1. it is priced at zero — ``billing.token_prices.default.input_price`` and
   ``output_price`` are both ``0``. A paid row is one the account would be
   billed for, which a free account cannot do at all;
2. the plan gate admits the account — ``billing.restricted_to`` is absent, or
   it names ``free``. A row restricted to ``pro_plus`` is refused at the
   wire even when the account could afford it;
3. it is a chat model — ``capabilities.type`` is ``chat``. The same listing
   carries embeddings and decision models, which answer 400 on a chat route.

That rule reproduced the live verdict for all 48 models of a real free
account: the 13 it names are exactly the 13 that answer 200, and the 35 it
excludes are exactly the 35 that answer ``model_not_supported``.

**The Auto route is the other half.** ``POST /auto`` asks Copilot which
model fits a prompt and answers with that model plus a session token. On a
free account the answer is a model the account may run — the listing says so
in its own billing block, and ``/auto`` picks from the same table — but the
inference request must carry the session token, and it must use the
``/responses`` endpoint. Without the token the model is refused as
``model_not_supported``; with it on ``/chat/completions`` the account is
refused with ``no_available_model_endpoints``.

The plan is read from the token mint itself. ``/copilot_internal/v2/token``
answers with ``sku`` (``free_limited_copilot``) and the same value is
embedded in the minted token, so the free tier is known before the first
inference and without a second round trip.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from hoshimi.core.providers.base import Account, ProviderError

logger = logging.getLogger(__name__)

# The Copilot CLI fingerprint. Copilot serves a different slice of its
# catalogue to the CLI than to a VS Code chat session, and the Auto route
# only answers the CLI fingerprint.
CLI_EDITOR_VERSION = "copilot/1.0.107"
CLI_USER_AGENT = "copilot/1.0.107 (linux v24.22.1) term/unknown client/github/cli"
CLI_INTEGRATION_ID = "copilot-developer-cli"
CLI_HARNESS_ID = "copilot-sdk"
CLI_API_VERSION = "2026-08-01"

# The plan SKU a free account carries. Copilot also reports the plan as
# ``individual``, which is what a paid individual plan reports too, so the
# SKU is the only reliable spelling of the tier.
FREE_SKU = "free_limited_copilot"

# The virtual model id that routes a turn through Copilot's own picker.
AUTO_MODEL_ID = "copilot-auto"

_SKU_PATTERN = re.compile(r"(?:^|;)sku=([^;]+)")


def plan_sku(token: str) -> str:
    """The plan SKU inside a minted Copilot token, or "" when it carries none."""
    match = _SKU_PATTERN.search(token or "")
    return match.group(1) if match else ""


def is_free_account(account: Account) -> bool:
    """Whether this account is on a plan whose models are the free ones.

    The minted token names the SKU, so an account that has logged in knows
    its tier without another round trip, and the login records the same SKU
    so the tier survives the token rotation a refresh performs. An account
    that never minted one (a plain API key) is not judged here: the gate is
    the catalogue's own pricing, and a paid key pays for what it asks for.
    """
    for source in (account.get("copilot_sku"), plan_sku(str(account.get("access_token") or ""))):
        if isinstance(source, str) and source:
            return source == FREE_SKU
    return False


def _price(row: dict[str, Any], name: str) -> float | None:
    billing = row.get("billing")
    if not isinstance(billing, dict):
        return None
    table = billing.get("token_prices")
    if not isinstance(table, dict):
        return None
    default = table.get("default")
    if not isinstance(default, dict):
        return None
    value = default.get(name)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def free_usable(row: dict[str, Any]) -> bool:
    """Whether a catalogue row is one this account may actually run.

    A free account is billed for a priced row and refused by the plan gate on
    a restricted one, so neither can be served. What is left is priced for
    the account's tier — free — and speaks a chat protocol.
    """
    if _price(row, "input_price") != 0 or _price(row, "output_price") != 0:
        return False
    billing = row.get("billing")
    restricted = billing.get("restricted_to") if isinstance(billing, dict) else None
    if isinstance(restricted, list) and "free" not in restricted:
        return False
    return model_kind(row) in (None, "chat")


def model_kind(row: dict[str, Any]) -> str | None:
    """What kind of model a catalogue row describes.

    Copilot states it in ``capabilities.type`` — ``chat``, ``embeddings`` or
    ``decisions`` — and states no kind at all for the chat models that
    predate the field.
    """
    capabilities = row.get("capabilities")
    kind = capabilities.get("type") if isinstance(capabilities, dict) else None
    if isinstance(kind, str) and kind:
        return kind
    kind = row.get("model_type") or row.get("type")
    return kind if isinstance(kind, str) and kind else None


@dataclass(frozen=True)
class AutoRoute:
    """One Auto resolution: the model Copilot picked and its session token."""

    model_id: str
    session_token: str
    expires_at: float


class AutoSessionCache:
    """Auto resolutions, keyed by account, reused until they expire.

    The session token is what lets the account run the model Copilot picked,
    so it is worth keeping: a new one per turn would spend a round trip on
    every message of a conversation. Copilot mints the token with a lifetime
    of its own, which the token's own ``exp`` claim states; that claim is
    honoured rather than a duration guessed here.

    The key names the account and the conversation, because the session token
    belongs to one of each: reusing it across conversations would carry a
    model's choice from one talk into the next.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: dict[str, AutoRoute] = {}

    def get(self, key: str) -> AutoRoute | None:
        with self._lock:
            route = self._routes.get(key)
            if route is None:
                return None
            if route.expires_at <= time.time():
                self._routes.pop(key, None)
                return None
            return route

    def put(self, key: str, route: AutoRoute) -> None:
        with self._lock:
            self._routes[key] = route

    def clear(self, key: str) -> None:
        with self._lock:
            self._routes.pop(key, None)


def auto_prompt(body: dict[str, Any]) -> tuple[str, bool]:
    """A summary of the request for the Auto picker, and whether it has an image.

    Copilot's picker only needs to know what kind of turn this is, so the
    last few messages are enough. The whole history is not sent: it is the
    caller's prompt, and the picker is not the model that will answer it.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        messages = []
    parts: list[str] = []
    has_image = False
    for message in messages[-4:]:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in ("image_url", "image", "input_image"):
                has_image = True
            elif isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "\n".join(parts)[:4000], has_image


def resolve_auto(client: httpx.Client, account: Account, base_url: str, prompt: str, has_image: bool) -> AutoRoute:
    """Ask Copilot which model fits this turn, and keep the session token."""
    token = str(account.get("access_token") or "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": CLI_USER_AGENT,
        "Editor-Version": CLI_EDITOR_VERSION,
        "Openai-Intent": "conversation-agent",
        "Copilot-Integration-Id": CLI_INTEGRATION_ID,
        "Copilot-Harness-Id": CLI_HARNESS_ID,
        "X-GitHub-Api-Version": CLI_API_VERSION,
        "X-Initiator": "user",
    }
    url = f"{base_url.rstrip('/')}/auto"
    try:
        response = client.post(url, headers=headers, json={"prompt": prompt, "has_image": has_image}, timeout=30)
    except httpx.HTTPError as exc:
        raise ProviderError(502, f"github-copilot: Auto routing request failed: {exc}") from None
    if response.status_code >= 400:
        detail = response.text[:300]
        raise ProviderError(response.status_code, f"github-copilot: Auto routing was refused: {detail}")
    try:
        payload = response.json()
    except ValueError:
        raise ProviderError(502, "github-copilot: Auto routing returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise ProviderError(502, "github-copilot: Auto routing returned an invalid response")
    selected = payload.get("selected_model")
    model_id = selected.get("id") if isinstance(selected, dict) else selected
    session_token = payload.get("session_token")
    if not isinstance(model_id, str) or not model_id or not isinstance(session_token, str) or not session_token:
        raise ProviderError(502, "github-copilot: Auto routing returned no model")
    expires_at = _session_expiry(payload.get("expires_at"), session_token)
    return AutoRoute(model_id=model_id, session_token=session_token, expires_at=expires_at)


def _session_expiry(raw: Any, session_token: str) -> float:
    """When the session token stops being usable, in unix seconds.

    Copilot states the lifetime in the response and again in the token's own
    claims. The response is read first; the token is the fallback, because a
    token that expires is a token that cannot be used, and a route that is
    kept past its expiry fails at the wire instead of being resolved again.
    """
    value = raw
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 99_999_999_999:
            seconds /= 1000
        return seconds
    if isinstance(value, str) and value.strip():
        try:
            seconds = float(value)
        except ValueError:
            seconds = 0.0
        if seconds > 99_999_999_999:
            seconds /= 1000
        if seconds > 0:
            return seconds
    claim = _token_expiry(session_token)
    return claim if claim else time.time() + 600


def _token_expiry(session_token: str) -> float:
    """The ``exp`` claim of a session token, read without verifying it.

    The token is Copilot's own opaque bearer; only its lifetime is read, and
    a token that cannot be decoded simply has no stated expiry.
    """
    import base64

    parts = session_token.split(".")
    if len(parts) < 2:
        return 0.0
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        import json as _json

        claims = _json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return 0.0
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    return float(expiry) if isinstance(expiry, (int, float)) and not isinstance(expiry, bool) else 0.0
