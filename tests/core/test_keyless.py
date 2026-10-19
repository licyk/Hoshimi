"""Keyless free lanes: built without configuration, removable by disabling them."""

from __future__ import annotations

import httpx

from hoshimi.core.providers import PROVIDER_CLASSES, anonymous_account, build_providers, real_account_count
from hoshimi.core.providers.kilo import KiloProvider


def test_keyless_lanes_are_built_without_configuration() -> None:
    providers = build_providers(httpx.Client(), {}, {})
    assert {"kilo", "opencode"} <= set(providers)
    for provider_id in ("kilo", "opencode"):
        provider = providers[provider_id]
        assert provider.keyless is True
        assert provider.enabled() is True
        assert all(account.anonymous for account in provider.accounts)
        assert real_account_count(provider) == 0


def test_disabled_keyless_lane_is_not_built() -> None:
    providers = build_providers(httpx.Client(), {}, {}, disabled={"kilo"})
    assert "kilo" not in providers
    assert "opencode" in providers


def test_configured_account_wins_over_the_anonymous_placeholder() -> None:
    providers = build_providers(
        httpx.Client(),
        {"opencode": [{"id": "mine", "api_key": "secret"}]},
        {},
    )
    provider = providers["opencode"]
    assert [account.id for account in provider.accounts] == ["mine"]
    assert real_account_count(provider) == 1


def test_keyless_declaration_lives_on_the_class() -> None:
    assert PROVIDER_CLASSES["kilo"].keyless is True
    assert PROVIDER_CLASSES["opencode"].keyless is True
    assert PROVIDER_CLASSES["cline"].keyless is False


def test_kilo_listing_keeps_only_the_free_pool() -> None:
    provider = KiloProvider(httpx.Client(), [anonymous_account("kilo")])
    rows = [
        {"id": "nvidia/nemotron-3.5-lightning:free", "isFree": True},
        {"id": "paid/model", "isFree": False},
        {"id": "   ", "isFree": True},
        "not-a-row",
        {"isFree": True},
    ]
    assert provider.parse_models_response({"data": rows}) == ["nvidia/nemotron-3.5-lightning:free"]
    assert provider.listing_rows == [rows[0]]


def test_kilo_sends_no_credential() -> None:
    provider = KiloProvider(httpx.Client(), [anonymous_account("kilo")])
    headers = provider.build_headers(provider.accounts[0], "openrouter/free")
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"


def test_kilo_falls_back_to_the_last_roster_offline() -> None:
    provider = KiloProvider(httpx.Client(), [anonymous_account("kilo")])
    assert "openrouter/free" in provider.fallback_models()
