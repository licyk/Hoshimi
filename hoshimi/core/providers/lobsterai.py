"""LobsterAI station (Youdao, OpenAI-compatible with capability headers)."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import httpx

from hoshimi.core.expiry import set_from_seconds
from hoshimi.core.providers.base import (
    CLAIM_ALREADY,
    CLAIM_CLAIMED,
    CLAIM_FAILED,
    Account,
    BalanceOutcome,
    ClaimOutcome,
    RefreshTokenRejected,
)
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider

LOBSTERAI_API_BASE = "https://lobsterai-server.youdao.com"
LOBSTERAI_CAPABILITIES = "kimi-k3-agentic-v1,thinking-level-control-v1"
LOBSTERAI_CLIENT_VERSION = "2026.9.4"
LOBSTERAI_ACTIVITY_SLOT_PATH = "/api/client-activities/slot"
LOBSTERAI_ACTIVITY_CONTEXT_PATH = "/api/client-activities"
LOBSTERAI_PROFILE_SUMMARY_PATH = "/api/user/profile-summary"
LOBSTERAI_SLOT_PLACEMENT = "desktop_sidebar"
LOBSTERAI_SLOT_CONTAINER_API_VERSION = "2"
LOBSTERAI_SLOT_PLATFORM = "win32"


class LobsteraiProvider(OpenAiCompatProvider):
    id = "lobsterai"
    display_name = "LobsterAI (有道)"
    base_url = LOBSTERAI_API_BASE
    chat_path = "/api/proxy/v1/chat/completions"
    models_path = "/api/models/available"
    user_agent = "LobsterAI/0.1.0"
    # Upstream fallback table (19 ids); the remote catalogue wins when it answers.
    fallback_model_list: ClassVar[list[str]] = [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "MiniMax-M3",
        "MiniMax-M2.7",
        "qwen3.7-max",
        "qwen3.7-plus",
        "qwen3.6-plus",
        "qwen3.5-plus-2026-04-20",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
        "kimi-k2.6",
        "kimi-k2.5",
        "doubao-seed-2-1-pro-260628",
        "doubao-seed-2-1-turbo-260628",
        "doubao-seed-2-0-code-preview-260215",
        "glm-5.2",
        "glm-5.1",
        "glm-5v-turbo",
        "glm-5",
    ]

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        headers["X-LobsterAI-Client-Capabilities"] = LOBSTERAI_CAPABILITIES
        headers["X-LobsterAI-Client-Version"] = LOBSTERAI_CLIENT_VERSION
        return headers

    def build_body(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        out = super().build_body(body, model)
        out["stream"] = True
        return out

    def parse_models_response(self, data: Any) -> list[str]:
        models: list[str] = []
        entries: Any = data
        if isinstance(data, dict):
            entries = data.get("data") or data.get("models") or data
        if isinstance(entries, dict):
            entries = entries.get("models") or entries.get("list") or []
        if isinstance(entries, list):
            for item in entries:
                if isinstance(item, str):
                    models.append(item)
                elif isinstance(item, dict) and isinstance(item.get("id"), str):
                    models.append(item["id"])
                elif isinstance(item, dict) and isinstance(item.get("name"), str):
                    models.append(item["name"])
        return models

    def refresh_account(self, account: Account) -> bool:
        refresh_token = account.get("refresh_token")
        uuid_value = account.get("uuid")
        if not isinstance(refresh_token, str) or not refresh_token:
            return False
        body: dict[str, Any] = {"refreshToken": refresh_token}
        if isinstance(uuid_value, str) and uuid_value:
            body["uuid"] = uuid_value
        created = account.get("created_at")
        if created is not None:
            body["createdAt"] = created
        try:
            response = self.client.post(
                f"{self.base_url}/api/auth/refresh",
                json=body,
                headers={"Content-Type": "application/json", "User-Agent": "LobsterAI/0.1.0"},
                timeout=30.0,
            )
        except httpx.HTTPError:
            return False
        if response.status_code in (401, 403):
            raise RefreshTokenRejected(response.status_code, f"{self.id}: the station refused the refresh token")
        if response.status_code >= 400:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        inner = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(inner, dict):
            return False
        access = inner.get("accessToken") or inner.get("access_token")
        refresh = inner.get("refreshToken") or inner.get("refresh_token")
        if not isinstance(access, str) or not access:
            return False
        account.set("access_token", access)
        if isinstance(refresh, str) and refresh:
            account.set("refresh_token", refresh)
        set_from_seconds(account, inner.get("expiresIn") or inner.get("expires_in"))
        return True

    # -- credits ---------------------------------------------------------- #
    def _credit_headers(self, account: Account) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {account.get('access_token', '')}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent or "LobsterAI/0.1.0",
        }

    def _credit_request(self, path: str, account: Account, *, method: str = "GET", body: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        try:
            kwargs: dict[str, Any] = {"headers": self._credit_headers(account), "timeout": 30.0}
            if body is not None:
                kwargs["json"] = body
            response = self.client.request(method, f"{LOBSTERAI_API_BASE}{path}", **kwargs)
        except httpx.HTTPError as exc:
            return None, str(exc)
        text = response.text
        try:
            payload = response.json()
        except ValueError:
            if response.status_code in (401, 403):
                return None, f"credential rejected (HTTP {response.status_code})"
            return None, f"non-JSON response (HTTP {response.status_code}): {text[:80]}"
        if not isinstance(payload, dict):
            return None, "unparseable response"
        code = payload.get("code")
        if code != 0:
            message = str(payload.get("msg") or payload.get("message") or f"code={code}")
            return None, message
        data = payload.get("data")
        if not isinstance(data, dict):
            return None, "response missing data"
        return data, ""

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        # Step 1: the activity slot. A pinned client version is what the
        # station expects; the upstream resolves it live and caches, we ship it.
        query = f"?placement={LOBSTERAI_SLOT_PLACEMENT}&clientVersion={LOBSTERAI_CLIENT_VERSION}&containerApiVersion={LOBSTERAI_SLOT_CONTAINER_API_VERSION}&platform={LOBSTERAI_SLOT_PLATFORM}"
        slot, error = self._credit_request(f"{LOBSTERAI_ACTIVITY_SLOT_PATH}{query}", account)
        if slot is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"activity slot query failed: {error}")
        activity = slot.get("activity")
        activity = activity if isinstance(activity, dict) else {}
        activity_code = str(activity.get("activityCode") or "")
        slot_state = str(slot.get("slotState") or "")
        if slot_state != "available" or not activity_code:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"no available activity (slotState={slot_state})")

        # Step 2: the activity context — claimed today? is check_in offered?
        revision = activity.get("configRevision")
        context, error = self._credit_request(f"{LOBSTERAI_ACTIVITY_CONTEXT_PATH}/{activity_code}/context?configRevision={revision}", account)
        if context is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"activity context query failed: {error}")
        state = context.get("state")
        state = state if isinstance(state, dict) else {}
        if state.get("claimedToday") is True:
            return ClaimOutcome(self.id, account.id, CLAIM_ALREADY, message="already claimed today")
        actions = context.get("actions")
        if not isinstance(actions, list) or "check_in" not in actions:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="check_in is not offered right now")

        # Step 3: the claim itself, with a client idempotency key.
        body = {"configRevision": revision, "idempotencyKey": str(uuid.uuid4()), "payload": {}}
        data, error = self._credit_request(f"{LOBSTERAI_ACTIVITY_CONTEXT_PATH}/{activity_code}/actions/check_in", account, method="POST", body=body)
        if data is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=error)
        result = data.get("result")
        result = result if isinstance(result, dict) else {}
        # The field name varies by campaign version; take the first that is a number.
        credits = 0
        for key in ("creditsGranted", "rewardCredits", "credits"):
            value = result.get(key)
            if isinstance(value, (int, float)):
                credits = int(value)
                break
        return ClaimOutcome(self.id, account.id, CLAIM_CLAIMED, credits=credits)

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        data, error = self._credit_request(LOBSTERAI_PROFILE_SUMMARY_PATH, account)
        if data is None:
            return BalanceOutcome(self.id, account.id, None, message=error)
        items = data.get("creditItems")
        if not isinstance(items, list):
            return BalanceOutcome(self.id, account.id, None, message="response missing creditItems")
        total = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            remaining = item.get("creditsRemaining")
            try:
                total += int(float(remaining)) if remaining is not None else 0
            except (TypeError, ValueError):
                continue
        return BalanceOutcome(self.id, account.id, total)
