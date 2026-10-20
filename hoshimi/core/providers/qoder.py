"""Qoder stations (Alibaba, public OpenAI-compatible inference endpoint).

The official encrypted WASM path is intentionally not reimplemented here; the
public `inferBase/model/v1/chat/completions` endpoint accepts the same
`security_oauth_token` bearer and speaks plain OpenAI SSE.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec, qoder_spec
from hoshimi.core.expiry import set_from_ms
from hoshimi.core.net.responses import refresh_token_rejected
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

QODER_SASH_USAGE_PATH = "/sash/api/v2/me/usage"
QODER_SASH_CAMPAIGNS_PATH = "/sash/api/v1/me/campaigns"
QODER_SASH_CLIENT_TYPE = "10"  # desktop-app identity; CLI identity (5) hides campaigns


# International catalogue (17 ids), matching the upstream product table: the ids
# `ultimate` / `performance` / `efficient` / `smodel` / `cmodel` exist only here.
QODER_INTL_MODELS = [
    "auto",
    "ultimate",
    "performance",
    "efficient",
    "smodel",
    "cmodel",
    "qmodel_38max",
    "qfmodel",
    "qmodel_latest",
    "qmodel",
    "kmodel_latest",
    "kmodel",
    "gmodel",
    "gfmodel",
    "dmodel",
    "dfmodel",
    "mmodel",
]

# The Chinese catalogue is its own list (14 ids), not a copy of the international
# one: `q37fmodel` / `gm51model` are CN-only, and the five ids the international
# lane answers to are rejected here — sending one is a guaranteed error.
QODER_CN_MODELS = [
    "auto",
    "qmodel_38max",
    "qfmodel",
    "qmodel_latest",
    "qmodel",
    "q37fmodel",
    "dmodel",
    "dfmodel",
    "gmodel",
    "gfmodel",
    "gm51model",
    "kmodel_latest",
    "kmodel",
    "mmodel",
]


class QoderProvider(OpenAiCompatProvider):
    id = "qoder"
    display_name = "Qoder"
    base_url = "https://api2-v2.qoder.sh"
    open_api_base = "https://openapi.qoder.sh"
    chat_path = "/model/v1/chat/completions"
    user_agent = "qoder/1.0.0"
    fallback_model_list = QODER_INTL_MODELS

    def _bearer(self, account: Account) -> str:
        token = account.get("security_oauth_token")
        if isinstance(token, str) and token:
            return token
        return str(account.get("access_token") or "")

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        headers["Authorization"] = f"Bearer {self._bearer(account)}"
        headers["X-Request-ID"] = str(uuid.uuid4())
        headers["X-Session-ID"] = str(uuid.uuid4())
        return headers

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from this site's own catalogue.

        The international and Chinese tables differ id for id (`mmodel` is a
        different MiniMax generation on each), so which one applies is decided
        by the station rather than by the row.
        """
        cn = self.id == "qodercn"
        return {model_id: qoder_spec(model_id, self.id, cn=cn) for model_id in self.models()}

    def refresh_account(self, account: Account) -> bool:
        refresh_token = account.get("refresh_token")
        machine_id = account.get("machine_id")
        if not isinstance(refresh_token, str) or not refresh_token:
            return False
        body: dict[str, Any] = {"refresh_token": refresh_token}
        if isinstance(machine_id, str) and machine_id:
            body["machine_id"] = machine_id
        try:
            response = self.client.post(
                f"{self.open_api_base}/api/v1/deviceToken/refresh",
                json=body,
                headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "qoder/1.0.0"},
                timeout=30.0,
            )
        except httpx.HTTPError:
            return False
        if refresh_token_rejected(response):
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
        access = data.get("security_oauth_token") or data.get("access_token") or data.get("device_token")
        refresh = data.get("refresh_token") or data.get("refreshToken")
        if not isinstance(access, str) or not access:
            return False
        account.set("security_oauth_token", access)
        account.set("access_token", access)
        if isinstance(refresh, str) and refresh:
            account.set("refresh_token", refresh)
        # The station reports the new expiry either as an absolute timestamp or
        # in seconds; both land on the one field the refresher reads.
        if not set_from_ms(account, data.get("expires_at") or data.get("expiresAt")):
            set_from_ms(account, data.get("refresh_token_expires_at"))
        return True


    # -- credits ---------------------------------------------------------- #
    def _sash_headers(self, account: Account) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bearer(account)}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cosy-ClientType": QODER_SASH_CLIENT_TYPE,
            "User-Agent": "Qoder",
        }

    def _load_machine_identity(self) -> dict[str, str] | None:
        # The IDE caches Cosy-MachineToken/Type on disk; without them the
        # campaigns endpoint hides the claimable benefit (see upstream notes).
        import json
        import os
        from pathlib import Path

        candidates: list[Path] = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            for dirname in ("Qoder", "Qoder CN"):
                candidates.append(Path(appdata) / dirname / "SharedClientCache" / "cache" / "machine_token.json")
        home = Path.home()
        for base in (home / "Library" / "Application Support", home / ".config"):
            for dirname in ("Qoder", "Qoder CN"):
                candidates.append(base / dirname / "SharedClientCache" / "cache" / "machine_token.json")
        for path in candidates:
            try:
                raw = path.read_text(encoding="utf-8")
                payload = json.loads(raw)
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            token = str(payload.get("token") or payload.get("machineToken") or "")
            typ = str(payload.get("type") or payload.get("machineType") or "")
            if token and typ:
                return {"token": token, "type": typ}
        return None

    def _sash_request(self, account: Account, path: str, *, method: str = "GET", machine: bool = False) -> tuple[dict[str, Any] | None, str]:
        headers = self._sash_headers(account)
        if machine:
            identity = self._load_machine_identity()
            if identity is None:
                return None, "machine identity unavailable (install Qoder desktop or provide Cosy-MachineToken)"
            headers["Cosy-MachineToken"] = identity["token"]
            headers["Cosy-MachineType"] = identity["type"]
        try:
            kwargs: dict[str, Any] = {"headers": headers, "timeout": 30.0}
            response = self.client.request(method, f"{self.open_api_base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            return None, str(exc)
        if response.status_code in (401, 403):
            return None, f"credential rejected (HTTP {response.status_code})"
        if response.status_code >= 400:
            return None, f"HTTP {response.status_code}"
        try:
            payload = response.json()
        except ValueError:
            return None, "non-JSON response"
        if not isinstance(payload, dict):
            return None, "unparseable response"
        return payload, ""

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        campaigns, error = self._sash_request(account, QODER_SASH_CAMPAIGNS_PATH, machine=True)
        if campaigns is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=error)
        raw = campaigns.get("campaigns")
        items = raw if isinstance(raw, list) else []
        claimable = None
        claimed_benefit = False
        for item in items:
            if not isinstance(item, dict):
                continue
            action = str(item.get("actionType") or "")
            status = str(item.get("claimStatus") or "")
            if action == "CLAIM_BENEFIT" and status == "CLAIMED":
                claimed_benefit = True
            if action == "CLAIM_BENEFIT" and status == "CLAIMABLE":
                claimable = item
        if claimable is None:
            # An already-claimed benefit this cycle is the idempotent case; an
            # empty list is ambiguous (before the 10:00 UTC+8 refresh) so we
            # do not claim already for that.
            if claimed_benefit:
                return ClaimOutcome(self.id, account.id, CLAIM_ALREADY, message="already claimed this cycle")
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="no claimable campaign right now")
        campaign_id = str(claimable.get("campaignId") or "")
        if not campaign_id:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="claimable campaign missing campaignId")
        body, error = self._sash_request(account, f"{QODER_SASH_CAMPAIGNS_PATH}/{campaign_id}/claim", method="POST", machine=True)
        if body is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=error)
        # Replayed is the idempotency marker (HTTP 200 either way).
        if body.get("replayed") is True:
            return ClaimOutcome(self.id, account.id, CLAIM_ALREADY, message="already claimed this cycle")
        benefit = body.get("benefit")
        benefit = benefit if isinstance(benefit, dict) else {}
        amount = benefit.get("amount")
        credits = int(amount) if isinstance(amount, (int, float)) else 0
        return ClaimOutcome(self.id, account.id, CLAIM_CLAIMED, credits=credits)

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        payload, error = self._sash_request(account, QODER_SASH_USAGE_PATH)
        if payload is None:
            return BalanceOutcome(self.id, account.id, None, message=error)
        usage = payload.get("qoderUsage")
        usage = usage if isinstance(usage, dict) else {}
        total = 0
        for key in ("userQuota", "addOnQuota"):
            quota = usage.get(key)
            quota = quota if isinstance(quota, dict) else {}
            remaining = quota.get("remaining")
            try:
                total += int(float(remaining)) if remaining is not None else 0
            except (TypeError, ValueError):
                continue
        return BalanceOutcome(self.id, account.id, total)



class QoderCnProvider(QoderProvider):
    id = "qodercn"
    display_name = "Qoder (CN)"
    base_url = "https://gateway.qoder.com.cn"
    open_api_base = "https://openapi.qoder.com.cn"
    fallback_model_list = QODER_CN_MODELS
