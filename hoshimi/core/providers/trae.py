"""TRAE station (ByteDance SOLO protocol)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec, trae_spec
from hoshimi.core.expiry import set_from_ms
from hoshimi.core.providers.base import (
    CLAIM_CLAIMED,
    CLAIM_FAILED,
    Account,
    BalanceOutcome,
    ClaimOutcome,
    Provider,
    ProviderError,
    RefreshTokenRejected,
)
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.solo import TRAE_FUNCTION, build_solo_body, iter_solo_chunks

TRAE_AGENT_HOST = "https://trae-api-cn.mchost.guru"
TRAE_OAUTH_HOST = "https://api.trae.com.cn"
TRAE_UG_HOST = "https://api.trae.cn"
TRAE_CHAT_PATH = "/api/agent/v3/llm_utils_chat"
TRAE_CHECKIN_STATUS_PATH = "/trae/api/v2/ug/checkin_credits/status"
TRAE_CHECKIN_CLAIM_PATH = "/trae/api/v2/ug/checkin_credits/claim"
TRAE_ENT_USAGE_PATH = "/trae/api/v2/pay/ide_user_ent_usage"
TRAE_CLIENT_ID = "en1oxy7wnw8j9n"

TRAE_MODELS = [
    "DeepSeek-V4-Flash-Official",
    "Doubao-Seed-2.1-Pro",
    "seed-code-pro-0430",
    "Doubao-Seed-2.1-Turbo",
    "Doubao-Seed-2.0-Code",
    "glm-5.2",
    "glm-5-turbo",
    "glm-5",
    "DeepSeek-V4-Pro",
    "DeepSeek-V4-Flash",
    "kimi-k3",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "minimax-m3",
    "qwen-3.7-plus",
    "sagitta",
    "aquila",
]


class TraeProvider(Provider):
    id = "trae"
    display_name = "TRAE"

    def fallback_models(self) -> list[str]:
        return list(TRAE_MODELS)

    def _headers(self, account: Account, stream: bool = True) -> dict[str, str]:
        token = str(account.get("access_token", ""))
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": str(self.options.get("user_agent") or "Trae/0.1.52"),
            "Authorization": f"Cloud-IDE-JWT {token}",
            "X-Cloudide-Token": token,
            "X-Ide-Token": token,
            "X-Uid": str(account.get("uid") or ""),
            "X-App-Id": str(
                self.options.get("app_id") or "6eefa01c-1036-4c7e-9ca5-d891f63bfcd8"
            ),
            "X-App-Version": "default",
            "X-Ide-Version": str(self.options.get("ide_version") or "0.1.52"),
            "X-Ide-Version-Code": str(self.options.get("ide_version_code") or "20260811"),
            "X-Ide-Version-Type": "stable",
            "X-Device-Type": "macos",
            "X-OS-Version": str(self.options.get("os_version") or "macOS 15.7.4"),
            "X-Device-Brand": str(self.options.get("device_brand") or "Apple"),
            "Request-Traffic-Type": "prod",
        }
        machine_id = account.get("machine_id")
        if isinstance(machine_id, str) and machine_id:
            headers["X-Machine-Id"] = machine_id
        device_id = account.get("device_id")
        if isinstance(device_id, str) and device_id:
            headers["X-Device-Id"] = device_id
        return headers

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        channel = str(self.options.get("channel") or TRAE_FUNCTION)
        payload = build_solo_body(body, model=model, channel=channel)
        url = f"{TRAE_AGENT_HOST}{TRAE_CHAT_PATH}"
        with self.client.stream(
            "POST", url, json=payload, headers=self._headers(account)
        ) as response:
            observe_response(self.id, response)
            if response.status_code >= 400:
                raise_for_status(response, provider=self.id)
            yield from iter_solo_chunks(response.iter_lines())

    def refresh_account(self, account: Account) -> bool:
        refresh_token = account.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return False
        try:
            response = self.client.post(
                f"{TRAE_OAUTH_HOST}/cloudide/api/v3/trae/oauth/ExchangeToken",
                json={
                    "ClientID": TRAE_CLIENT_ID,
                    "RefreshToken": refresh_token,
                    "ClientSecret": "-",
                    "UserID": "",
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": str(self.options.get("user_agent") or "Trae/0.1.52"),
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
        result = None
        if isinstance(payload, dict):
            candidate = payload.get("Result") or payload.get("result")
            if isinstance(candidate, dict):
                result = candidate
        if result is None:
            return False
        access = result.get("Token") or result.get("token") or result.get("accessToken")
        refresh = result.get("RefreshToken") or result.get("refreshToken")
        if not isinstance(access, str) or not access:
            return False
        account.set("access_token", access)
        # ExchangeToken always answers a new refresh token; keeping the old one
        # would end the account on the next round.
        if isinstance(refresh, str) and refresh:
            account.set("refresh_token", refresh)
        set_from_ms(account, result.get("TokenExpireAt") or result.get("tokenExpireAt"))
        return True

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the station's table: a context window, nothing more.

        The thinking rungs arrive per account from the remote catalogue, which
        this gateway does not fetch, so no menu is advertised and a caller's own
        `reasoning_effort` is forwarded untouched.
        """
        return {model_id: trae_spec(model_id, self.id) for model_id in self.models()}

    # -- credits ---------------------------------------------------------- #
    def _checkin_headers(self, account: Account) -> dict[str, str]:
        """The full client header set the check-in endpoints expect.

        The device identity is derived deterministically from `uid` so each
        account keeps a stable, distinct device — the stations rate-limit per
        device, not per token.
        """
        uid = str(account.get("uid") or "")
        if not uid:
            raise ProviderError(400, "trae: account is missing uid, cannot derive check-in device identity")
        token = str(account.get("access_token", ""))
        device_id = _derive_device_id15(uid)
        market_user_id = _derive_market_user_id(uid)
        session_id = _derive_session_id(uid)
        return {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN",
            "User-Agent": "VSCode 1.107.1 (TRAE SOLO CN)",
            "Authorization": f"Cloud-IDE-JWT {token}",
            "X-Market-Client-Id": "VSCode 1.107.1",
            "X-Market-User-Id": market_user_id,
            "X-User-Region": "CN",
            "X-Device-Id": device_id,
            "X-Lgw-Req-Sdk-Type": "3",
            "Package-Type": "stable_cn",
            "X-Lscbd-Aid": "787976",
            "X-Lscbd-Platform": "windows",
            "App-Version": "0.1.52",
            "X-Tt-Trace-Id": f"00-{uuid.uuid4().hex[:32]}-01",
            "Vscode-Sessionid": session_id,
            "X-Request-Id": str(uuid.uuid4()),
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "none",
        }

    def _post_ug(self, path: str, account: Account) -> dict[str, Any] | None:
        """POST one UG endpoint; None when the response is not a usable object."""
        try:
            response = self.client.post(f"{TRAE_UG_HOST}{path}", json={}, headers=self._checkin_headers(account), timeout=20.0)
        except (httpx.HTTPError, ProviderError):
            return None
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        # Claim carries no credits field; the real number lives on the status
        # endpoint, so a successful claim is followed by one status read.
        body = self._post_ug(TRAE_CHECKIN_CLAIM_PATH, account)
        if body is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="claim endpoint unreachable")
        code = _read_claim_code(body)
        if code == 0:
            status = self._post_ug(TRAE_CHECKIN_STATUS_PATH, account)
            credits = 0
            if status is not None:
                raw = status.get("credits")
                credits = int(raw) if isinstance(raw, (int, float)) else 0
            return ClaimOutcome(self.id, account.id, CLAIM_CLAIMED, credits=credits)
        message = str(body.get("message") or body.get("msg") or f"claim failed (code={code})")
        return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=message)

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        try:
            response = self.client.post(
                f"{TRAE_UG_HOST}{TRAE_ENT_USAGE_PATH}",
                json={"require_usage": True, "req_source": 2},
                headers=self._checkin_headers(account),
                timeout=20.0,
            )
        except (httpx.HTTPError, ProviderError) as exc:
            return BalanceOutcome(self.id, account.id, None, message=str(exc))
        if response.status_code >= 400:
            return BalanceOutcome(self.id, account.id, None, message=f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return BalanceOutcome(self.id, account.id, None, message="non-JSON response")
        if not isinstance(payload, dict):
            return BalanceOutcome(self.id, account.id, None, message="unparseable response")
        packs = payload.get("user_entitlement_pack_list")
        if not isinstance(packs, list):
            return BalanceOutcome(self.id, account.id, None, message="response missing entitlement packs")
        total = 0
        for entry in packs:
            if not isinstance(entry, dict):
                continue
            base = entry.get("entitlement_base_info")
            quota = base.get("quota") if isinstance(base, dict) else None
            limit = quota.get("credits_limit") if isinstance(quota, dict) else None
            usage = entry.get("usage")
            used = usage.get("credits_amount") if isinstance(usage, dict) else 0
            try:
                limit_i = int(limit) if limit is not None else 0
                used_i = int(used) if isinstance(used, (int, float)) else 0
            except (TypeError, ValueError):
                continue
            if limit_i <= 0:
                continue
            total += limit_i - used_i
        return BalanceOutcome(self.id, account.id, total)


def _read_claim_code(body: dict[str, Any]) -> int:
    raw = body.get("code")
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    # Absent code means success on this endpoint; anything else is a failure.
    return 0 if raw is None else -1


def _seeded_stream(seed: str, salt: str, nbytes: int) -> bytes:
    """SHA256(salt:seed ++ counterBE32) chained until `nbytes` bytes."""
    prefix = f"{salt}:{seed}".encode()
    out = bytearray()
    counter = 0
    while len(out) < nbytes:
        out.extend(hashlib.sha256(prefix + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(out[:nbytes])


def _derive_device_id15(uid: str) -> str:
    """15 digits, stable per account — each account owns its own device."""
    stream = _seeded_stream(uid, "devid", 15)
    return "".join(str(b % 10) for b in stream)


def _derive_market_user_id(uid: str) -> str:
    """A UUIDv4-shaped id, deterministic from the account's user id."""
    stream = bytearray(_seeded_stream(uid, "market", 16))
    stream[6] = (stream[6] & 0x0F) | 0x40
    stream[8] = (stream[8] & 0x3F) | 0x80
    hexed = stream.hex()
    return f"{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"


def _derive_session_id(uid: str) -> str:
    """64 hex characters, deterministic from the account's user id."""
    return _seeded_stream(uid, "sess", 32).hex()
