"""Buddy stations: Tencent CodeBuddy (CN) and WorkBuddy (intl)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec, buddy_spec
from hoshimi.core.expiry import set_from_ms, set_from_seconds
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

CODEBUDDY_MODELS = [
    "hy4-preview",
    "hy3",
    "hy3-x",
    "deepseek-v4.1-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
    "glm-5.1",
    "glm-5v-turbo",
    "kimi-k3-1",
    "kimi-k2.8-preview",
    "kimi-k2.7",
    "kimi-k2.6",
    "minimax-m3",
]

WORKBUDDY_MODELS = [
    "default-model",
    "fast-model",
    "balanced-model",
    "primary-model",
    "deep-model",
    "hy4-preview-f",
    "hy4-preview",
    "hy3",
    "deepseek-v4.1-flash",
    "deepseek-v4.1-flash-sg",
    "gpt-6-astra",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gemini-3.5-flash",
    "glm-5.3",
    "glm-5.2",
    "kimi-k3",
    "kimi-k2.8-preview",
    "kimi-k2.6",
]

WORKBUDDY_UA_INTL = "WorkBuddy/5.5.2 WorkBuddy AI/5.5.2 CLI/5.5.2"
WORKBUDDY_UA_CN = "WorkBuddy/5.5.2 WorkBuddy/5.5.2 CLI/5.5.2"

CHECKIN_ACTIVITY_STATUS_PATH = "/v2/billing/meter/checkin-activity-status"
DAILY_CHECKIN_PATH = "/v2/billing/meter/daily-checkin"
USER_RESOURCE_PATH = "/v2/billing/meter/get-user-resource"
# The Tencent gateway answers "already claimed today" as HTTP 400 + this code;
# judging by HTTP status alone would misread a successful idempotent round.
BUDDY_CODE_ALREADY_CLAIMED = 10001

# (model-family prefix, User-Agent) rules for WorkBuddy.
_UA_BY_FAMILY = (
    ("gpt-", WORKBUDDY_UA_INTL),
    ("gemini-", WORKBUDDY_UA_INTL),
    ("claude-", WORKBUDDY_UA_INTL),
    ("glm-", WORKBUDDY_UA_CN),
    ("hy", WORKBUDDY_UA_CN),
    ("kimi-", WORKBUDDY_UA_CN),
    ("minimax-", WORKBUDDY_UA_CN),
)


class BuddyProvider(OpenAiCompatProvider):
    """Shared implementation for the Tencent buddy family."""

    endpoint = "https://copilot.tencent.com"
    api_domain = "copilot.tencent.com"
    product_code = "codebuddy"
    attribution_name = "CodeBuddy"
    client_version = "1.106.1"
    default_user_agent = "CodeBuddyIDE/1.106.1"
    model_family_ua = False

    @property
    def base_url(self) -> str:  # ty: ignore[invalid-property-type-override]
        return self.endpoint

    @property
    def chat_path(self) -> str:  # ty: ignore[invalid-property-type-override]
        return "/v2/chat/completions"

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {account.get('access_token', '')}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Domain": self.api_domain,
            "X-Product-Code": self.product_code,
            "X-Agent-Purpose": "conversation",
            "X-IDE-Name": self.attribution_name,
            "X-IDE-Type": self.attribution_name,
            "X-IDE-Version": self.client_version,
            "X-Product": self.attribution_name,
            "User-Agent": self._resolve_user_agent(model),
        }
        return headers

    def _resolve_user_agent(self, model: str) -> str:
        if self.model_family_ua:
            for prefix, ua in _UA_BY_FAMILY:
                if model.startswith(prefix):
                    return ua
        return self.default_user_agent

    def build_body(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        out = super().build_body(body, model)
        out["prompt_cache_key"] = str(body.get("prompt_cache_key") or uuid.uuid4())
        return out

    def refresh_account(self, account: Account) -> bool:
        refresh_token = account.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return False
        try:
            response = self.client.post(
                f"{self.endpoint}/v2/plugin/auth/token/refresh",
                headers={
                    "Authorization": f"Bearer {account.get('access_token', '')}",
                    "X-Refresh-Token": refresh_token,
                    "X-Auth-Refresh-Source": "ide-main",
                    "X-Domain": self.api_domain,
                    "X-Product-Code": self.product_code,
                    "X-Product": self.attribution_name,
                    "User-Agent": self.default_user_agent,
                },
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
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return False
        access = data.get("accessToken") or data.get("access_token")
        refresh = data.get("refreshToken") or data.get("refresh_token")
        if not isinstance(access, str) or not access:
            return False
        account.set("access_token", access)
        if isinstance(refresh, str) and refresh:
            account.set("refresh_token", refresh)
        # The station answers either an absolute expiry or a lifetime in
        # seconds; the refresher only ever reads `expires_at`.
        if not set_from_ms(account, data.get("expiresAt") or data.get("expires_at")):
            set_from_seconds(account, data.get("expiresIn") or data.get("expires_in"))
        return True

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the station's own product table.

        The menu is the model's own `reasoning_effort` field, so a row with no
        declared rungs is left inert rather than given a ladder the station
        does not run.
        """
        work = self.product_code == "workbuddy"
        return {model_id: buddy_spec(model_id, self.id, work=work) for model_id in self.models()}

    # -- credits ---------------------------------------------------------- #
    def _credit_headers(self, account: Account) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {account.get('access_token', '')}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            # X-Domain must match the endpoint, not the credential's snapshot.
            "X-Domain": self.api_domain,
            "X-Product": "SaaS",
            "X-Product-Code": self.product_code,
            "User-Agent": self.default_user_agent,
        }
        user_id = account.get("user_id")
        if isinstance(user_id, str) and user_id:
            headers["X-User-Id"] = user_id
        enterprise_id = account.get("enterprise_id")
        if isinstance(enterprise_id, str) and enterprise_id:
            headers["X-Enterprise-Id"] = enterprise_id
            headers["X-Tenant-Id"] = enterprise_id
        return headers

    def _post_credit(self, path: str, account: Account) -> tuple[dict[str, Any] | None, str]:
        """POST one billing endpoint; the gateway answers HTML when credentials die."""
        try:
            response = self.client.post(f"{self.endpoint}{path}", json={}, headers=self._credit_headers(account), timeout=30.0)
        except httpx.HTTPError as exc:
            return None, str(exc)
        text = response.text
        try:
            payload = response.json()
        except ValueError:
            # A non-JSON body is usually the gateway's HTML error page.
                return None, f"HTTP {response.status_code}: {text[:80]}"
        if not isinstance(payload, dict):
            return None, "unparseable response"
        return payload, ""

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        payload, error = self._post_credit(DAILY_CHECKIN_PATH, account)
        if payload is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=error)
        code = payload.get("code")
        message = str(payload.get("msg") or "")
        # Idempotence lives in the body code: a repeat is also HTTP 400.
        if code == BUDDY_CODE_ALREADY_CLAIMED or code == 1001:
            return ClaimOutcome(self.id, account.id, CLAIM_ALREADY, message=message or "already claimed today")
        if code == 1002 or code == 1003:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=message or "no claimable activity")
        if code != 0:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=message or f"claim failed (code={code})")
        data = payload.get("data")
        if not isinstance(data, dict):
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="claim response missing data")
        credit = data.get("credit")
        credits = int(credit) if isinstance(credit, (int, float)) else 0
        return ClaimOutcome(self.id, account.id, CLAIM_CLAIMED, credits=credits)

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        payload, error = self._post_credit(USER_RESOURCE_PATH, account)
        if payload is None:
            return BalanceOutcome(self.id, account.id, None, message=error)
        if payload.get("code") != 0:
            return BalanceOutcome(self.id, account.id, None, message=str(payload.get("msg") or f"business error {payload.get('code')}"))
        # data.Response.Data.Accounts —— two nested layers before the list.
        outer = payload.get("data")
        response = outer.get("Response") if isinstance(outer, dict) else None
        inner = response.get("Data") if isinstance(response, dict) else None
        accounts = inner.get("Accounts") if isinstance(inner, dict) else None
        if not isinstance(accounts, list):
            return BalanceOutcome(self.id, account.id, None, message="response missing Accounts")
        total = 0.0
        for entry in accounts:
            if not isinstance(entry, dict):
                continue
            remain = entry.get("CycleCapacityRemainPrecise", entry.get("CycleCapacityRemain"))
            try:
                total += float(remain) if remain is not None else 0.0
            except (TypeError, ValueError):
                continue
        return BalanceOutcome(self.id, account.id, int(total))


class CodeBuddyProvider(BuddyProvider):
    id = "buddy"
    display_name = "CodeBuddy (腾讯)"
    endpoint = "https://copilot.tencent.com"
    api_domain = "copilot.tencent.com"
    product_code = "codebuddy"
    attribution_name = "CodeBuddy"
    client_version = "1.106.1"
    default_user_agent = "CodeBuddyIDE/1.106.1"
    fallback_model_list = CODEBUDDY_MODELS


class WorkBuddyProvider(BuddyProvider):
    id = "workbuddy"
    display_name = "WorkBuddy (intl)"
    endpoint = "https://www.workbuddy.ai"
    api_domain = "www.workbuddy.ai"
    product_code = "workbuddy"
    attribution_name = "WorkBuddy"
    client_version = "5.5.2"
    default_user_agent = WORKBUDDY_UA_INTL
    model_family_ua = True
    fallback_model_list = WORKBUDDY_MODELS

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        # The intl build has no daily-checkin endpoint at all; balance still works.
        return ClaimOutcome(self.id, account.id, "unsupported", covers_today=False, message="no daily check-in endpoint on the international station")
