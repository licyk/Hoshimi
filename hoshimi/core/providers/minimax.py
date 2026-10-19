"""MiniMax station (Anthropic Messages protocol)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec, minimax_efforts_for, minimax_spec
from hoshimi.core.expiry import set_from_seconds
from hoshimi.core.providers.base import (
    CLAIM_ALREADY,
    CLAIM_CLAIMED,
    CLAIM_FAILED,
    Account,
    BalanceOutcome,
    ClaimOutcome,
    Provider,
    RefreshTokenRejected,
)
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.anthropic import build_anthropic_body, iter_anthropic_chunks

MINIMAX_ACCOUNT_HOST = "https://account.minimax.cn"
MINIMAX_API_HOST = "https://agent.minimax.cn"
MINIMAX_MESSAGES_PATH = "/mavis/api/v1/llm/v1/messages"
MINIMAX_SIGNIN_STATUS_PATH = "/minimax-cloud/api/v1/signin/status"
MINIMAX_SIGNIN_CLAIM_PATH = "/minimax-cloud/api/v1/signin/claim"
MINIMAX_CREDIT_DETAILS_PATH = "/minimax-cloud/api/v1/credit/details"
MINIMAX_CLIENT_ID = "mcode-public"
MINIMAX_TIMEZONE_ID = "Asia/Shanghai"

MINIMAX_MODELS = [
    "MiniMax-M3.1-Flash-Preview",
    "MiniMax-M3",
    "MiniMax-M2.7-highspeed",
    "MiniMax-M2.7",
]

_SPENT_TOKEN_MARKERS = ("invalid_grant", "expired", "revoked")


def _looks_like_spent_token(text: str) -> bool:
    """A body that says the refresh token itself is gone, not the network."""
    lowered = text.lower()
    return any(marker in lowered for marker in _SPENT_TOKEN_MARKERS)

# This family requires thinking.type="adaptive" even when no effort was requested.
_ADAPTIVE_ONLY_PREFIX = "MiniMax-M3.1"


class MinimaxProvider(Provider):
    id = "minimax"
    display_name = "MiniMax"

    def fallback_models(self) -> list[str]:
        return list(MINIMAX_MODELS)

    def _headers(self, account: Account) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {account.get('access_token', '')}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the station's own table: four ids, three shapes."""
        return {model_id: minimax_spec(model_id, self.id) for model_id in self.models()}

    def _thinking_extra(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        """Translate the chosen rung into this station's two wire fields.

        The level rides on `reasoning_effort` (see `MINIMAX_EFFORT_PATCH`), which
        `apply_effort` has already normalised to a rung the table declares. A
        level the table does *not* declare — M2.7 publishes no `effort_options`
        at all — is dropped the way upstream drops it rather than forwarded as an
        `output_config` that model never advertised.
        """
        effort = body.get("reasoning_effort")
        allowed = minimax_efforts_for(model)
        if isinstance(effort, str) and effort in allowed:
            if effort == "none":
                return {"thinking": {"type": "disabled"}}
            if effort == "on":
                return {"thinking": {"type": "adaptive"}}
            return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
        # M3.1 refuses a turn that does not ask for adaptive thinking (400, 2013).
        if model.startswith(_ADAPTIVE_ONLY_PREFIX):
            return {"thinking": {"type": "adaptive"}}
        return {}

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        payload = build_anthropic_body(body, model=model, extra=self._thinking_extra(model, body))
        url = f"{MINIMAX_API_HOST}{MINIMAX_MESSAGES_PATH}"
        with self.client.stream(
            "POST", url, json=payload, headers=self._headers(account)
        ) as response:
            observe_response(self.id, response)
            if response.status_code >= 400:
                from hoshimi.core.providers.openai_compat import raise_for_status

                raise_for_status(response, provider=self.id)
            yield from iter_anthropic_chunks(response.iter_lines())

    def refresh_account(self, account: Account) -> bool:
        refresh_token = account.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return False
        try:
            response = self.client.post(
                f"{MINIMAX_ACCOUNT_HOST}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": MINIMAX_CLIENT_ID,
                    "scope": "agent.default",
                    "audience": "agent-backend",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=20.0,
            )
        except httpx.HTTPError:
            return False
        if response.status_code >= 400:
            # The station answers a spent refresh token as `invalid_grant`.
            if _looks_like_spent_token(response.text):
                raise RefreshTokenRejected(response.status_code, "minimax: the refresh token was rejected")
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict) or payload.get("token_type") != "Bearer":
            return False
        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        if not isinstance(access, str) or not access:
            return False
        account.set("access_token", access)
        if isinstance(refresh, str) and refresh:
            account.set("refresh_token", refresh)
        set_from_seconds(account, payload.get("expires_in"))
        return True

    # -- credits ---------------------------------------------------------- #
    def _credit_headers(self, account: Account, *, with_body: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {account.get('access_token', '')}",
            "Accept": "application/json",
        }
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        # signin/* is an envelope; credit/details is flat. Accept both.
        nested = payload.get("data")
        return nested if isinstance(nested, dict) else payload

    @staticmethod
    def _business_code(payload: dict[str, Any]) -> int | None:
        base = payload.get("base_resp")
        if isinstance(base, dict):
            code = base.get("status_code")
            if isinstance(code, (int, float)):
                return int(code)
        return None

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        url = f"{MINIMAX_API_HOST}{MINIMAX_SIGNIN_CLAIM_PATH}?timezone_id={MINIMAX_TIMEZONE_ID}"
        try:
            response = self.client.post(url, json={}, headers=self._credit_headers(account, with_body=True), timeout=20.0)
        except httpx.HTTPError as exc:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=str(exc))
        if response.status_code in (401, 403):
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"credential rejected (HTTP {response.status_code})")
        try:
            payload = response.json()
        except ValueError:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"non-JSON response (HTTP {response.status_code})")
        if not isinstance(payload, dict):
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="unparseable response")
        code = self._business_code(payload)
        if code is not None and code != 0:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"business error {code}")
        data = self._unwrap(payload)
        claim_result = data.get("claim_result")
        # Idempotence lives in claim_result, not the HTTP status: a repeat is also 200.
        if claim_result == 2:
            return ClaimOutcome(self.id, account.id, CLAIM_ALREADY, message="already claimed today")
        if claim_result != 1:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="claim response missing claim_result")
        points = data.get("points")
        credits = int(points) if isinstance(points, (int, float)) else 0
        return ClaimOutcome(self.id, account.id, CLAIM_CLAIMED, credits=credits)

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        url = f"{MINIMAX_API_HOST}{MINIMAX_CREDIT_DETAILS_PATH}"
        try:
            response = self.client.get(url, headers=self._credit_headers(account), timeout=20.0)
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
        code = self._business_code(payload)
        if code is not None and code != 0:
            return BalanceOutcome(self.id, account.id, None, message=f"business error {code}")
        # The balance is the sum of each pack's remaining_amount (a string!).
        details = payload.get("details")
        total = 0
        if isinstance(details, list):
            for row in details:
                if not isinstance(row, dict):
                    continue
                raw = row.get("remaining_amount")
                try:
                    total += int(float(raw)) if raw is not None else 0
                except (TypeError, ValueError):
                    continue
        return BalanceOutcome(self.id, account.id, total)
