"""Gemini station (Google Code Assist daily-cloudcode channel)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec, gemini_spec
from hoshimi.core.expiry import set_from_iso, set_from_seconds
from hoshimi.core.providers.base import Account, BalanceOutcome, Provider, RefreshTokenRejected
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.gemini import (
    GEMINI_ENDPOINT,
    GEMINI_IDENTITY_HEADERS,
    GEMINI_STREAM_PATH,
    GEMINI_UPSTREAM_FLASH,
    build_gemini_envelope,
    canonical_model_id,
    gemini_model_ids,
    iter_gemini_chunks,
    marshal_envelope,
)

_SPENT_TOKEN_MARKERS = ("invalid_grant", "invalid_client", "expired", "revoked", "unauthorized")


def _looks_like_spent_token(text: str) -> bool:
    """A body that says the refresh token itself is gone, not the network."""
    lowered = text.lower()
    return any(marker in lowered for marker in _SPENT_TOKEN_MARKERS)


class GeminiProvider(Provider):
    id = "gemini"
    display_name = "Gemini (Code Assist)"

    def fallback_models(self) -> list[str]:
        # One row, four rungs — not four suffixed rows. See `gemini_model_ids`.
        return gemini_model_ids()

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the station's own table: one id, a four-tier menu.

        The level reaches the wire as `reasoning_effort`; `build_gemini_envelope`
        turns it into the thinking budget *and* the `-tier` suffix the station
        requires on the model name.
        """
        return {model_id: gemini_spec(model_id, self.id) for model_id in self.models()}

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        # The catalogue names the bare id; the station only answers the
        # suffixed one. Any leftover suffix in `model` is stripped here and
        # re-derived from the effort, so the two never disagree.
        canonical = canonical_model_id(model)
        if canonical != GEMINI_UPSTREAM_FLASH:
            model = GEMINI_UPSTREAM_FLASH
        effort = body.get("reasoning_effort")
        effort_str = effort if isinstance(effort, str) else None

        envelope = build_gemini_envelope(body, model=model, effort=effort_str)
        wire = marshal_envelope(envelope)

        headers = {
            "Authorization": f"Bearer {account.get('access_token', '')}",
            "Content-Type": "application/json",
            **GEMINI_IDENTITY_HEADERS,
        }
        url = f"{GEMINI_ENDPOINT}{GEMINI_STREAM_PATH}"
        with self.client.stream("POST", url, content=wire.encode("utf-8"), headers=headers) as response:
            observe_response(self.id, response)
            if response.status_code >= 400:
                raise_for_status(response, provider=self.id)
            yield from iter_gemini_chunks(response.iter_lines())

    def refresh_account(self, account: Account) -> bool:
        refresh_token = account.get("refresh_token")
        # The login flow files the client next to the token; provider options
        # still win so a self-built client keeps working.
        client_id = self.options.get("client_id") or account.get("client_id")
        if not isinstance(refresh_token, str) or not refresh_token or not client_id:
            return False
        import httpx

        try:
            response = self.client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": str(client_id),
                    "client_secret": str(self.options.get("client_secret") or account.get("client_secret") or ""),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
        except httpx.HTTPError:
            return False
        if response.status_code >= 400:
            # Google answers a spent refresh token as `invalid_grant`.
            if _looks_like_spent_token(response.text):
                raise RefreshTokenRejected(response.status_code, "gemini: the refresh token was rejected")
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        access = payload.get("access_token")
        if not isinstance(access, str) or not access:
            return False
        account.set("access_token", access)
        # Google rotates the refresh token occasionally; a new one must be kept.
        refresh = payload.get("refresh_token")
        if isinstance(refresh, str) and refresh:
            account.set("refresh_token", refresh)
        # The grant reports a lifetime in seconds; the lane also accepts an
        # absolute `expiry`, which is what the login flow wrote.
        if not set_from_seconds(account, payload.get("expires_in")):
            set_from_iso(account, payload.get("expiry"))
        return True

    # -- credits ---------------------------------------------------------- #
    GEMINI_SANDBOX_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
    GEMINI_QUOTA_PATH = "/v1internal:retrieveUserQuotaSummary"

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        token = str(account.get("access_token") or "")
        if not token:
            return BalanceOutcome(self.id, account.id, None, message="no Google account authorized yet")
        project = str(account.get("cloudaicompanionProject") or "aicode-consumers")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/4.3.0 (cmdc-pak)",
            "x-client-name": "antigravity",
        }
        try:
            response = self.client.post(
                f"{self.GEMINI_SANDBOX_ENDPOINT}{self.GEMINI_QUOTA_PATH}",
                json={"project": project},
                headers=headers,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            return BalanceOutcome(self.id, account.id, None, message=str(exc))
        if response.status_code in (401, 403):
            return BalanceOutcome(self.id, account.id, None, message=f"quota query rejected (HTTP {response.status_code})")
        if response.status_code >= 400:
            return BalanceOutcome(self.id, account.id, None, message=f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return BalanceOutcome(self.id, account.id, None, message="non-JSON response")
        # Gemini free tier is quota-window based, not credits. Surface the
        # five-hour and weekly remaining fractions as the unit so the UI does
        # not invent a credit total.
        buckets: Any = None
        if isinstance(payload, list):
            buckets = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("buckets"), list):
                buckets = payload["buckets"]
            elif isinstance(payload.get("summary"), dict) and isinstance(payload["summary"].get("buckets"), list):
                buckets = payload["summary"]["buckets"]
        five_hour = None
        weekly = None
        for item in buckets if isinstance(buckets, list) else []:
            if not isinstance(item, dict):
                continue
            bucket_id = str(item.get("bucketId") or "")
            fraction = item.get("remainingFraction")
            try:
                frac = float(fraction) if fraction is not None else None
            except (TypeError, ValueError):
                frac = None
            if bucket_id == "gemini-5h" and five_hour is None and frac is not None:
                five_hour = frac
            if bucket_id == "gemini-weekly" and weekly is None and frac is not None:
                weekly = frac
        if five_hour is None and weekly is None:
            return BalanceOutcome(self.id, account.id, None, message="quota response has no Gemini buckets")
        parts = []
        if five_hour is not None:
            parts.append(f"5h={five_hour:.0%}")
        if weekly is not None:
            parts.append(f"weekly={weekly:.0%}")
        return BalanceOutcome(self.id, account.id, None, unit="quota", message=", ".join(parts))
