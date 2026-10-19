"""CodeArts station (Huawei Cloud ModelArts, OpenAI body + SDK-HMAC-SHA256).

Signing follows Huawei's SDK-HMAC-SHA256 scheme as implemented in
dsh-our-free-model's sign.ts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from hoshimi.core.catalog import ModelSpec, codearts_spec
from hoshimi.core.providers.base import (
    CLAIM_ALREADY,
    CLAIM_CLAIMED,
    CLAIM_FAILED,
    Account,
    BalanceOutcome,
    ClaimOutcome,
    ProviderError,
)
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider, consume_openai_sse, raise_for_status
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.chat import convert_tools

CODEARTS_CHAT_BASE = "https://snap-access.cn-north-4.myhuaweicloud.com/api/v2"
CODEARTS_SNAP_ENGINE_URL = "https://snap-access.cn-north-4.myhuaweicloud.com"
CODEARTS_PACKAGE_INFO_PATH = "/snap-manager/v1/statistics/plugin"
CODEARTS_OPS_DELIVERY_PATH = "/v1/ops/delivery"
CODEARTS_OPS_CLAIM_PATH = "/v1/ops/claim"
CODEARTS_OPS_CONFIRM_PATH = "/v1/ops/confirm"
CODEARTS_OPS_CHANNEL = "IDE"
CODEARTS_DAILY_LOGIN_TYPE = "USER_LOGIN"
CODEARTS_CLAIMED_STATUSES = frozenset({"CLAIMED", "CONFIRMED", "CONSUMED"})


class _BenefitHeaderRejected(Exception):
    """Internal: the `maas_type: benefit` header was refused mid-stream."""


def _normalize_model_id(model_id: str) -> str:
    if len(model_id) > 5 and model_id[-5] == "-" and model_id[-4:].isdigit():
        return model_id[:-5]
    return model_id


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256_hex(key: bytes, data: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def sign_request_huawei(
    ak: str,
    sk: str,
    security_token: str,
    method: str,
    url: str,
    body: bytes,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return headers (incl. Authorization) for a signed Huawei request."""
    parts = urlsplit(url)
    uri = parts.path or "/"
    if not uri.endswith("/"):
        uri += "/"
    query = parts.query
    date_stamp = (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("-", "")
        .replace(":", "")
        .split(".")[0]
        + "Z"
    )
    payload_hash = _sha256_hex(body)

    headers: dict[str, str] = {
        "host": parts.netloc,
        "x-sdk-date": date_stamp,
        "x-sdk-content-sha256": payload_hash,
        "x-security-token": security_token,
    }
    if extra_headers:
        headers.update({key.lower(): value for key, value in extra_headers.items()})
    if method.upper() != "GET":
        headers["content-type"] = "application/json"

    signed_keys = sorted(headers)
    canonical_headers = "\n".join(f"{key}:{headers[key]}" for key in signed_keys)
    signed_headers = ";".join(signed_keys)
    canonical_request = (f"{method.upper()}\n{uri}\n{query}\n{canonical_headers}\n\n{signed_headers}\n{payload_hash}")
    canonical_hash = _sha256_hex(canonical_request.encode("utf-8"))
    string_to_sign = f"SDK-HMAC-SHA256\n{date_stamp}\n{canonical_hash}"
    signature = _hmac_sha256_hex(sk.encode("utf-8"), string_to_sign.encode("utf-8"))

    headers["Authorization"] = (
        f"SDK-HMAC-SHA256 Access={ak},SignedHeaders={signed_headers},Signature={signature}"
    )
    return headers


class CodeArtsProvider(OpenAiCompatProvider):
    id = "codearts"
    display_name = "CodeArts (华为云)"
    base_url = CODEARTS_CHAT_BASE
    chat_path = "/chat/completions"
    model_list_url = "https://snap-access.cn-north-4.myhuaweicloud.com/v1/model/builtin"
    # Models billed through the benefit (权益) package: only they accept the
    # `maas_type: benefit` signed header.
    benefit_fallback_models: ClassVar[list[str]] = ["glm-5.3-flash", "deepseek-v4.1-flash"]
    fallback_model_list: ClassVar[list[str]] = [
        "GLM-5.2",
        "GLM-5.1",
        "GLM-5",
        "glm-5.3-flash",
        "openpangu-2.0-flash",
        "openpangu-2.0-pro",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-v4.1-flash",
    ]

    def _credentials(self, account: Account) -> tuple[str, str, str]:
        ak = str(account.get("access_key_id") or "")
        sk = str(account.get("secret_access_key") or "")
        token = str(account.get("security_token") or "")
        return ak, sk, token

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        # Real headers are computed in stream_raw (signature needs the body).
        return {"Content-Type": "application/json", "Accept": "text/event-stream"}

    def build_body(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "model": model,
            "messages": body.get("messages") or [],
            "stream": True,
            "prompt_cache_key": str(body.get("prompt_cache_key") or "fma-default"),
            "max_tokens": int(
                body.get("max_tokens") or body.get("max_completion_tokens") or 65536
            ),
        }
        if body.get("temperature") is not None:
            out["temperature"] = body["temperature"]
        if body.get("top_p") is not None:
            out["top_p"] = body["top_p"]
        stop = body.get("stop")
        if stop:
            out["stop"] = stop
        tools = convert_tools(body.get("tools"))
        if tools:
            out["tools"] = tools
        tool_choice = body.get("tool_choice")
        if tool_choice in ("auto", "none", "required"):
            out["tool_choice"] = tool_choice
        elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            fn = tool_choice.get("function")
            name = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(name, str) and name:
                out["tool_choice"] = {"type": "function", "function": {"name": name}}
        # The lane's off rung arrives as `thinking` (see `CODEARTS_EFFORT_OFF_PATCH`).
        # This builder picks fields out of the body instead of passing it through, so
        # the switch has to be carried across explicitly — and only the off rung has
        # one: `on` writes nothing, which is upstream's measured server default.
        thinking = body.get("thinking")
        if isinstance(thinking, dict):
            out["thinking"] = thinking
        return out

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the lane's own switch: two rungs on every id.

        The remote catalogue names ids and nothing else, so the rungs belong to
        the lane while the context window stays bound to the table — an id
        nobody measured keeps the gateway's default rather than an invented one.
        """
        return {model_id: codearts_spec(model_id, self.id) for model_id in self.models()}

    def _benefit_models(self) -> set[str]:
        configured = self.options.get("benefit_models")
        if isinstance(configured, list) and configured:
            return {str(item) for item in configured}
        return set(self.benefit_fallback_models)

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        payload = self.build_body(body, model)
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ak, sk, token = self._credentials(account)
        url = self.chat_url()

        # The benefit header is only meaningful (and only accepted) for models
        # billed through the 权益 package; drop it after the first refusal.
        extras = [{"maas_type": "benefit"}, None] if model in self._benefit_models() else [None]
        last_error: ProviderError | None = None

        for extra in extras:
            headers = sign_request_huawei(ak, sk, token, "POST", url, raw, extra)
            with self.client.stream("POST", url, content=raw, headers=headers) as response:
                observe_response(self.id, response)
                if response.status_code >= 400:
                    response.read()
                    if response.status_code >= 500 and extra is None:
                        raise_for_status(response, provider=self.id)
                    last_error = ProviderError(
                        response.status_code, f"{self.id}: HTTP {response.status_code}"
                    )
                    continue
                try:
                    yield from self._consume_with_benefit_fallback(
                        response.iter_lines(), drop_benefit=extra is not None
                    )
                except _BenefitHeaderRejected:
                    last_error = None
                    continue
            return
        raise last_error or ProviderError(502, f"{self.id}: request failed")

    def _consume_with_benefit_fallback(
        self, lines: Iterable[str], *, drop_benefit: bool
    ) -> Iterator[dict[str, Any]]:
        for chunk in consume_openai_sse(lines, provider=self.id):
            if (
                drop_benefit
                and chunk.get("type") == "error"
                and "InferHub.4004.200" in str(chunk.get("error") or "")
            ):
                raise _BenefitHeaderRejected()
            yield chunk

    def fetch_models(self) -> list[str]:
        if not self.accounts:
            return []
        account = self.accounts[0]
        ak, sk, token = self._credentials(account)
        headers = sign_request_huawei(ak, sk, token, "GET", self.model_list_url, b"")
        try:
            response = self.client.get(self.model_list_url, headers=headers, timeout=30.0)
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        try:
            data = response.json()
        except ValueError:
            return []
        entries: list[Any] = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            for key in ("models", "data", "model_list"):
                value = data.get(key)
                if isinstance(value, list):
                    entries = value
                    break
        models: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("model_id") or item.get("id") or item.get("name")
            if not isinstance(raw_id, str) or not raw_id:
                continue
            normalized = _normalize_model_id(raw_id)
            if "-VL" in normalized:
                continue
            if normalized not in models:
                models.append(normalized)
        return models

    # -- credits ---------------------------------------------------------- #
    def _snap_request(
        self,
        account: Account,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        ak, sk, token = self._credentials(account)
        url = f"{CODEARTS_SNAP_ENGINE_URL}{path}"
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
        headers = sign_request_huawei(ak, sk, token, method, url, raw)
        # These are routing headers; they must NOT enter the signature.
        headers["Agent-Type"] = "PromptCenter"
        headers["X-Language"] = "zh-cn"
        try:
            response = self.client.request(method, url, content=raw or None, headers=headers, timeout=30.0)
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
        data = payload.get("data")
        if isinstance(data, dict):
            return data, ""
        # Some responses put the payload at the top level; keep it either way.
        return payload, ""

    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        data, error = self._snap_request(account, CODEARTS_PACKAGE_INFO_PATH)
        if data is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=error)
        package = data.get("package")
        package = package if isinstance(package, dict) else {}
        if package.get("is_token_package") is True:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="token-billing account is outside the credit campaign")
        if package.get("is_credit_package") is False and package.get("is_token_package") is not True:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="non-credit billing account is outside the credit campaign")

        activities_data, error = self._snap_request(account, f"{CODEARTS_OPS_DELIVERY_PATH}?channel={CODEARTS_OPS_CHANNEL}")
        if activities_data is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"activity list query failed: {error}")
        items = activities_data.get("items")
        activity: dict[str, Any] | None = None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and str(item.get("type") or "") == CODEARTS_DAILY_LOGIN_TYPE:
                activity = item
                break
        if activity is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="no daily-login activity found")
        if activity.get("claimable") is not True:
            status = str(activity.get("status") or "")
            if status in CODEARTS_CLAIMED_STATUSES:
                return ClaimOutcome(self.id, account.id, CLAIM_ALREADY, message="already claimed today")
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=f"not claimable right now (status={status})")
        campaign_id = activity.get("campaignId")
        if campaign_id is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message="activity missing campaignId")

        claim_data, error = self._snap_request(
            account,
            CODEARTS_OPS_CLAIM_PATH,
            method="POST",
            body={"campaignId": campaign_id, "channel": CODEARTS_OPS_CHANNEL},
        )
        if claim_data is None:
            return ClaimOutcome(self.id, account.id, CLAIM_FAILED, covers_today=False, message=error)
        benefit_id = claim_data.get("id")
        if benefit_id is not None:
            # Confirm is required by the server; failure here is non-fatal —
            # the credit is already in a pending state.
            self._snap_request(account, CODEARTS_OPS_CONFIRM_PATH, method="POST", body={"campaignId": campaign_id})
        credits = 0
        for key in ("benefitAmount", "credit", "credits", "creditAmount", "amount"):
            value = claim_data.get(key)
            if isinstance(value, (int, float)) and value > 0:
                credits = int(value)
                break
        if credits == 0:
            amount = activity.get("benefitAmount") or activity.get("amount")
            if isinstance(amount, (int, float)):
                credits = int(amount)
        return ClaimOutcome(self.id, account.id, CLAIM_CLAIMED, credits=credits)

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        data, error = self._snap_request(account, CODEARTS_PACKAGE_INFO_PATH)
        if data is None:
            return BalanceOutcome(self.id, account.id, None, message=error)
        metrics = data.get("metrics")
        if not isinstance(metrics, list):
            return BalanceOutcome(self.id, account.id, None, message="response missing metrics")
        total = 0
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            if str(metric.get("name") or "") != "usageTotalPackageCredit":
                continue
            value = metric.get("value")
            try:
                total = int(float(value)) if value is not None else 0
            except (TypeError, ValueError):
                total = 0
            break
        return BalanceOutcome(self.id, account.id, total)
