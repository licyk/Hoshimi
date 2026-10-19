"""Cline station (api.cline.bot, OpenAI-compatible, WorkOS token)."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from hoshimi.core.catalog import ModelSpec, cline_spec
from hoshimi.core.expiry import set_from_iso, set_from_ms
from hoshimi.core.providers.base import Account, BalanceOutcome, RefreshTokenRejected
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider

CLINE_API_BASE = "https://api.cline.bot"


class ClineProvider(OpenAiCompatProvider):
    id = "cline"
    display_name = "Cline"
    base_url = CLINE_API_BASE
    chat_path = "/api/v1/chat/completions"
    models_path = "/api/v1/ai/cline/recommended-models"
    fallback_model_list: ClassVar[list[str]] = [
        "stealth/space-bunny-alpha",
        "cline-free/mimo-v2.6-flash",
        "cline-free/muse-spark-1.3-contributor",
    ]

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        headers.update(
            {
                "HTTP-Referer": "https://cline.bot",
                "X-Title": "Cline",
                "X-IS-MULTIROOT": "false",
                "X-CLIENT-TYPE": "cline-sdk",
            }
        )
        return headers

    def parse_models_response(self, data: Any) -> list[str]:
        if isinstance(data, dict):
            inner = data.get("data") if isinstance(data.get("data"), dict) else data
            free = inner.get("free") if isinstance(inner, dict) else None
            if isinstance(free, list):
                return [
                    item["id"]
                    for item in free
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]
        return super().parse_models_response(data)

    def refresh_account(self, account: Account) -> bool:
        refresh_token = account.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return False
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/auth/refresh",
                json={"refreshToken": refresh_token, "grantType": "refresh_token"},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "HTTP-Referer": "https://cline.bot",
                    "X-Title": "Cline",
                    "X-IS-MULTIROOT": "false",
                    "X-CLIENT-TYPE": "cline-sdk",
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
        # Cline reports the expiry as an ISO instant, not a lifetime.
        if not set_from_iso(account, inner.get("expiresAt") or inner.get("expires_at")):
            set_from_ms(account, inner.get("expire_time"))
        return True

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the free lane's table: three ids, one shared menu."""
        return {model_id: cline_spec(model_id, self.id) for model_id in self.models()}

    # -- credits ---------------------------------------------------------- #
    CLINE_BALANCE_SCALE = 100_000  # raw micro-USD to dollars-ish magnitude

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        # The WorkOS JWT carries the auth; the path needs the station's
        # `account_id` (usr-...), NOT the JWT sub (user_...).
        user_id = str(account.get("account_id") or "").strip()
        if not user_id:
            return BalanceOutcome(self.id, account.id, None, message="credential is missing account_id")
        # The stored token carries the `workos:` prefix (chat sends it verbatim);
        # only add it when a bare JWT slipped through, never twice.
        token = str(account.get("access_token") or "").removeprefix("workos:")
        headers = {
            "Authorization": f"Bearer workos:{token}",
            "Accept": "application/json",
            "HTTP-Referer": "https://cline.bot",
            "X-Title": "Cline",
            "X-IS-MULTIROOT": "false",
            "X-CLIENT-TYPE": "cline-sdk",
        }
        url = f"{CLINE_API_BASE}/api/v1/users/{user_id}/balance"
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
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return BalanceOutcome(self.id, account.id, None, message="unexpected response shape")
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        raw = data.get("balance")
        try:
            credits = int(float(raw) // self.CLINE_BALANCE_SCALE) if raw is not None else None
        except (TypeError, ValueError):
            credits = None
        return BalanceOutcome(self.id, account.id, credits, unit="credits")
