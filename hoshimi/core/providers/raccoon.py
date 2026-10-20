"""Raccoon station (SenseTime xiaohuanxiong, OpenAI-compatible)."""

from __future__ import annotations

from typing import ClassVar

import httpx

from hoshimi.core.catalog import ModelSpec, raccoon_spec
from hoshimi.core.expiry import set_from_jwt
from hoshimi.core.net.responses import refresh_token_rejected
from hoshimi.core.providers.base import Account, BalanceOutcome, RefreshTokenRejected
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider

RACCOON_API_BASE = "https://xiaohuanxiong.com"
RACCOON_LLM_PREFIX = "/api/web/llm/v2"
RACCOON_AUTH_PREFIX = "/api/web/auth/v1"


class RaccoonProvider(OpenAiCompatProvider):
    id = "raccoon"
    display_name = "Raccoon (商汤)"
    base_url = RACCOON_API_BASE
    chat_path = f"{RACCOON_LLM_PREFIX}/chat/completions"
    fallback_model_list: ClassVar[list[str]] = [
        "sn-sensenova-6-8-flash",
        "sn-sensenova-6-8-flash-lite",
        "sn-glm-5-3",
        "sn-glm-5-3-flash",
        "sn-kimi-k3",
        "sn-deepseek-v4-1-flash",
    ]

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        headers["X-Org-Code"] = str(account.get("office_identity") or "")
        headers["X-Raccoon-Language"] = "zh"
        device_id = account.get("device_id")
        if isinstance(device_id, str) and device_id:
            headers["X-Client-Device-ID"] = device_id
        return headers

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the station's own table: six ids, one shared switch."""
        return {model_id: raccoon_spec(model_id, self.id) for model_id in self.models()}

    def refresh_account(self, account: Account) -> bool:
        refresh_token = account.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return False
        try:
            response = self.client.post(
                f"{self.base_url}{RACCOON_AUTH_PREFIX}/refresh",
                json={"refresh_token": refresh_token},
                headers={"Content-Type": "application/json"},
                timeout=60.0,
            )
        except httpx.HTTPError:
            return False
        # 401 (or the station's own "session expired" code) means the refresh
        # token itself is spent: renewing again cannot help.
        if refresh_token_rejected(response):
            raise RefreshTokenRejected(401, "raccoon: the refresh token was rejected")
        if response.status_code >= 400:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        if isinstance(payload, dict) and payload.get("code") == 200003:
            raise RefreshTokenRejected(401, "raccoon: the session expired, please log in again")
        inner = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(inner, dict):
            return False
        access = inner.get("access_token") or inner.get("accessToken")
        refresh = inner.get("refresh_token") or inner.get("refreshToken")
        if not isinstance(access, str) or not access:
            return False
        account.set("access_token", access)
        # The station may answer with a new access_token only; keeping the old
        # refresh token is what stops one renewal from ending the account.
        if isinstance(refresh, str) and refresh:
            account.set("refresh_token", refresh)
        # The access token is a JWT: its own exp claim is the expiry.
        set_from_jwt(account, access)
        return True

    # -- credits ---------------------------------------------------------- #
    RACCOON_POINTS_PREFIX = "/api/web/points/v1"

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        headers = {
            "Accept": "application/json",
            "X-Org-Code": str(account.get("office_identity") or ""),
            "X-Raccoon-Language": "zh",
        }
        device_id = account.get("device_id")
        if isinstance(device_id, str) and device_id:
            headers["X-Client-Device-ID"] = device_id
        url = f"{self.base_url}{self.RACCOON_POINTS_PREFIX}/balance"
        try:
            response = self.client.get(url, headers=headers, timeout=30.0)
        except httpx.HTTPError as exc:
            return BalanceOutcome(self.id, account.id, None, message=str(exc))
        if response.status_code >= 400:
            return BalanceOutcome(self.id, account.id, None, message=f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return BalanceOutcome(self.id, account.id, None, message="non-JSON response")
        if not isinstance(payload, dict) or payload.get("code") != 0:
            message = str(payload.get("message") if isinstance(payload, dict) else "") or "unexpected response"
            return BalanceOutcome(self.id, account.id, None, message=message)
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        available = data.get("available_points")
        try:
            credits = int(float(available)) if available is not None else None
        except (TypeError, ValueError):
            credits = None
        if credits is None:
            return BalanceOutcome(self.id, account.id, None, message="response missing available_points")
        return BalanceOutcome(self.id, account.id, credits)
