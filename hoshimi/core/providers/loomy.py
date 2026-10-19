"""Loomy station (iFlytek, OpenAI-compatible, dual auth headers)."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from hoshimi.core.catalog import ModelSpec, loomy_spec
from hoshimi.core.providers.base import (
    CLAIM_ALREADY,
    CLAIM_CLAIMED,
    CLAIM_FAILED,
    Account,
    BalanceOutcome,
    ClaimOutcome,
)
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider

LOOMY_API_BASE = "https://loomyad.xunfei.cn/api/v1"
LOOMY_POINTS_RECORDS_PATH = "/points/records"
LOOMY_FIRST_LOGIN_PATH = "/points/first-login"
LOOMY_OK_CODE = "000000"


class LoomyProvider(OpenAiCompatProvider):
    id = "loomy"
    display_name = "Loomy (讯飞)"
    base_url = LOOMY_API_BASE
    chat_path = "/chat/completions"
    models_path = "/models"
    fallback_model_list: ClassVar[list[str]] = [
        "deepseek-v4-flash-0731",
        "MiniMax-M3",
        "Kimi-k2.6",
        "qwen-3.8-max",
        "GLM-5.3-Flash",
        "qwen3.8-flash",
        "spark-x",
        "mimo-v2.5",
    ]

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        token = str(account.get("access_token", ""))
        headers = super().build_headers(account, model)
        # Chat accepts only Bearer; the control plane accepts only the bare
        # `token` header — official clients send both, so do the same.
        headers["token"] = token
        return headers

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the station's own table: eight ids, one shared menu."""
        return {model_id: loomy_spec(model_id, self.id) for model_id in self.models()}

    # -- credits ---------------------------------------------------------- #
    def _credit_headers(self, account: Account, *, with_body: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            # The control plane wants the bare `token`, never Authorization.
            "token": str(account.get("access_token", "")),
        }
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _envelope(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        code = str(payload.get("code") or "")
        if code != LOOMY_OK_CODE:
            message = str(payload.get("desc") or payload.get("message") or f"business error {code or '(missing)'}")
            return None, message
        data = payload.get("data")
        return (data if isinstance(data, dict) else {}), ""

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        url = f"{LOOMY_API_BASE}{LOOMY_FIRST_LOGIN_PATH}"
        try:
            response = self.client.post(url, json={}, headers=self._credit_headers(account, with_body=True), timeout=30.0)
        except httpx.HTTPError as exc:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=str(exc))
        if response.status_code >= 400:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="non-JSON response")
        if not isinstance(payload, dict):
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="unparseable response")
        data, error = self._envelope(payload)
        if data is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=error)
        # Idempotence lives in the body: a repeat call is also HTTP 200.
        if data.get("alreadyProcessed") is True:
            quota = data.get("dailyQuota")
            return ClaimOutcome(self.id, account.id, CLAIM_ALREADY, message=f"daily quota already initialised (quota={quota})" if quota is not None else "daily quota already initialised")
        # The station grants dailyQuota - dailyConsumed; never invent a number.
        quota = _as_int(data.get("dailyQuota"))
        consumed = _as_int(data.get("dailyConsumed"))
        if quota is not None and consumed is not None:
            granted = max(0, quota - consumed)
        elif quota is not None:
            granted = quota
        else:
            granted = 0
        return ClaimOutcome(self.id, account.id, CLAIM_CLAIMED, credits=granted)

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        url = f"{LOOMY_API_BASE}{LOOMY_POINTS_RECORDS_PATH}?pageNo=1&pageSize=1&recordType=all"
        try:
            response = self.client.get(url, headers=self._credit_headers(account), timeout=30.0)
        except httpx.HTTPError as exc:
            return BalanceOutcome(self.id, account.id, None, message=str(exc))
        if response.status_code >= 400:
            return BalanceOutcome(self.id, account.id, None, message=f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return BalanceOutcome(self.id, account.id, None, message="non-JSON response")
        if not isinstance(payload, dict):
            return BalanceOutcome(self.id, account.id, None, message="unparseable response")
        data, error = self._envelope(payload)
        if data is None:
            return BalanceOutcome(self.id, account.id, None, message=error)
        available = _as_int(data.get("availableBalance"))
        permanent = _as_int(data.get("balance"))
        # `balance` is the core field: without it the shape is wrong, don't invent.
        if permanent is None and available is None:
            return BalanceOutcome(self.id, account.id, None, message="response missing balance")
        total = available if available is not None else permanent
        return BalanceOutcome(self.id, account.id, total)


def _as_int(value: Any) -> int | None:
    """Read a finite number the station may have sent as int or string."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
