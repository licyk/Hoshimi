"""The aggregator lane: one id, many stations, the account carries the station."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hoshimi.core.catalog import agg_spec
from hoshimi.core.errors import ValidationError
from hoshimi.core.providers import PROVIDER_CLASSES, build_providers
from hoshimi.core.providers.agg import AggProvider
from hoshimi.core.providers.base import Account, ProviderError

OR = "https://openrouter.ai/api/v1"
SF = "https://api.siliconflow.cn/v1"
SSE = (
    'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"!"}}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
    "data: [DONE]\n\n"
)


def _account(**extra: Any) -> Account:
    data = {"id": "or-1", "base_url": OR, "api_key": "sk-or-1"}
    data.update(extra)
    return Account(data, provider_id="agg")


def _provider(accounts: list[Account], handler: Any = None, options: dict[str, Any] | None = None) -> AggProvider:
    transport = httpx.MockTransport(handler) if handler else httpx.MockTransport(lambda request: httpx.Response(200, text=SSE))
    return AggProvider(httpx.Client(transport=transport), accounts, options)


def test_the_credential_is_read_from_whatever_the_station_calls_it() -> None:
    provider = _provider([_account()])
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        seen["path"] = request.url.path
        return httpx.Response(200, text=SSE)

    provider = _provider([_account()], handler)
    list(provider.stream_raw(provider.accounts[0], "openrouter/auto", {"messages": []}))
    assert seen["authorization"] == "Bearer sk-or-1"
    assert seen["path"] == "/api/v1/chat/completions"

    for key in ("access_token", "token"):
        alias = _provider([_account(api_key="", **{key: f"sk-{key}"})])
        assert alias._token(alias.accounts[0]) == f"sk-{key}"


def test_a_turn_without_a_credential_says_which_field_is_missing() -> None:
    # The classmethod reads the account directly: an incomplete account never
    # reaches a provider instance (it is dropped at build time).
    with pytest.raises(ProviderError) as missing:
        AggProvider._token(Account({"id": "or-0", "base_url": OR}, provider_id="agg"))
    assert "api_key" in missing.value.message


def test_the_base_url_is_the_station_s_root_not_the_chat_path() -> None:
    provider = _provider([_account(base_url=f"{OR}/")])
    assert provider._base(provider.accounts[0]) == OR
    # A trailing slash must not produce a doubled one on the wire.
    provider_with_base = _provider([_account(base_url="https://relay.example/v1//")])
    assert provider_with_base._base(provider_with_base.accounts[0]) == "https://relay.example/v1"


def test_an_incomplete_account_is_dropped_at_build_not_at_request_time() -> None:
    dropped = _provider([_account(id="or-2", base_url=""), _account(id="or-3", api_key="")])
    assert dropped.accounts == []

    mixed = _provider([_account(id="or-4"), _account(id="or-5", base_url="")])
    assert [account.id for account in mixed.accounts] == ["or-4"]
    assert mixed.enabled() is True


def test_declared_models_are_the_catalogue_and_need_no_listing() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text=SSE)

    provider = _provider([_account(models=["openrouter/auto", "z-ai/glm-5.2:free"])], handler)
    assert provider.models() == ["openrouter/auto", "z-ai/glm-5.2:free"]
    # Nothing was asked of the station: the account already named its roster.
    assert calls == []


def test_a_declared_station_is_the_one_a_turn_runs_on() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, text=SSE)

    openrouter = _account(id="or-1", base_url=OR, api_key="sk-or", models=["openrouter/auto"])
    siliconflow = _account(id="sf-1", base_url=SF, api_key="sk-sf", models=["Qwen/Qwen3-8B"])
    provider = _provider([openrouter, siliconflow], handler)

    # Each station only ever serves the model it declared.
    list(provider.stream_raw(siliconflow, "Qwen/Qwen3-8B", {"messages": []}))
    assert seen == ["Bearer sk-sf"]
    # A declared id goes to the station that declared it, first.
    assert [account.id for account in provider._stream_candidates("Qwen/Qwen3-8B")] == ["sf-1", "or-1"]
    assert [account.id for account in provider._stream_candidates("openrouter/auto")] == ["or-1", "sf-1"]
    # An id nobody declared falls through to the whole pool.
    assert [account.id for account in provider._stream_candidates("unknown/model")] == ["or-1", "sf-1"]

    # An undeclared account stays reachable for anything (single-station setups).
    plain = _provider([_account(id="or-9")], handler)
    assert plain._stream_candidates("any/model") == plain.accounts


def test_extra_headers_ride_along_but_cannot_replace_the_credential() -> None:
    provider = _provider([_account(headers={"HTTP-Referer": "https://example.com", "Authorization": "Bearer evil"})])
    headers = provider.build_headers(provider.accounts[0], "")
    assert headers["HTTP-Referer"] == "https://example.com"
    assert headers["Authorization"] == "Bearer sk-or-1"


def test_a_json_answer_is_not_read_as_an_empty_stream() -> None:
    provider = _provider(
        [_account()],
        lambda request: httpx.Response(200, json={"choices": [{"delta": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
    )
    chunks = list(provider.stream_raw(provider.accounts[0], "openrouter/auto", {"messages": []}))
    assert [chunk["type"] for chunk in chunks] == ["text_delta", "usage", "finish"]


def test_specs_are_inert_until_the_account_declares_them() -> None:
    # The default row knows nothing about an arbitrary id: reasoning off, so
    # the caller's body reaches the station untouched.
    inert = agg_spec("someone/brand-new-9b", "agg")
    assert (inert.vision, inert.reasoning, inert.efforts) == (False, False, ())
    assert (inert.context_window, inert.max_output) == (131072, 32768)

    provider = _provider(
        [
            _account(
                models=["openrouter/auto", "z-ai/glm-5.2:free", "qwen/qwen3-vl"],
                options={
                    "context_window": 200000,
                    "model_specs": {
                        "z-ai/glm-5.2:free": {"reasoning": True, "efforts": ("disabled", "low", "high")},
                        "qwen/qwen3-vl": {"vision": True},
                    },
                },
            )
        ]
    )
    specs = provider.specs()
    glm = specs["z-ai/glm-5.2:free"]
    assert (glm.reasoning, glm.efforts, glm.can_disable_thinking) == (True, ("disabled", "low", "high"), True)
    # The account's own default applies to ids without a row.
    assert specs["openrouter/auto"].context_window == 200000
    assert specs["qwen/qwen3-vl"].vision is True


def test_provider_options_are_the_defaults_an_account_can_override() -> None:
    provider = _provider(
        [_account(models=["openrouter/auto"], options={"model_specs": {"openrouter/auto": {"max_output": 65536}}})],
        options={"model_specs": {"openrouter/auto": {"max_output": 8192, "reasoning": True}}},
    )
    spec = provider.specs()["openrouter/auto"]
    # The account's row wins, the provider's remaining keys still apply.
    assert spec.max_output == 65536
    assert spec.reasoning is True


def _station(station: str, **extra: Any) -> Account:
    """An account at a station this gateway has an address for."""
    data: dict[str, Any] = {"id": f"{station}-1", "base_url": "", "preset": station, "api_key": f"sk-{station}"}
    data.update(extra)
    return Account(data, provider_id="agg")


def test_the_free_mark_is_what_the_station_said_about_the_price() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "deepseek-v4-flash:free"}, {"id": "mimo-v2.5"}, {"id": "claude-opus-5"}]})

    specs = _provider([_station("bai", api_key="sk-bai-1")], handler).specs()
    # The row's own answer: bai spells free in the id.
    assert specs["deepseek-v4-flash:free"].free is True
    # The table's, because the row carries no price at all and bai documents
    # this one free somewhere else.
    assert specs["mimo-v2.5"].free is True
    # Nobody said, so the spec says nothing — a paid price this gateway never
    # read is not a price it may quote.
    assert specs["claude-opus-5"].free is None


def test_a_declared_catalogue_still_gets_the_table_for_that_station() -> None:
    # No listing is asked for: the operator named the roster, and the two
    # tables in `free_marks` are keyed by the station the account named.
    provider = _provider([_station("agnes", models=["agnes-2.0-flash", "agnes-pro"])])
    specs = provider.specs()
    assert provider.models() == ["agnes-2.0-flash", "agnes-pro"]
    assert specs["agnes-2.0-flash"].free is True
    assert specs["agnes-pro"].free is None


def test_the_first_account_to_name_an_id_keeps_its_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.gmi-serving.com":
            return httpx.Response(200, json={"data": [{"id": "shared", "is_free": True}]})
        return httpx.Response(200, json={"data": [{"id": "shared", "pricing": {"prompt": "1", "completion": "1"}}]})

    # One id at two stations at two prices, and both stations have an answer:
    # GMI publishes a flag, OpenGateway prices per model. The listing's own
    # order decides, which is also the order a turn would be served in.
    first = _provider([_station("gmi"), _station("opengateway")], handler)
    assert first.models() == ["shared"]
    assert first.specs()["shared"].free is True

    second = _provider([_station("opengateway"), _station("gmi")], handler)
    assert second.specs()["shared"].free is False


def test_the_lane_is_built_only_with_a_complete_account() -> None:
    client = httpx.Client()
    assert "agg" not in build_providers(client, {})
    assert "agg" in build_providers(client, {"agg": [{"id": "or-1", "base_url": OR, "api_key": "sk-or"}]})
    assert "agg" not in build_providers(client, {"agg": [{"id": "or-1", "base_url": OR, "api_key": "sk-or"}]}, disabled={"agg"})
    # An account that cannot possibly work does not build the lane either.
    assert "agg" not in build_providers(client, {"agg": [{"id": "or-1", "base_url": ""}]})
    assert PROVIDER_CLASSES["agg"] is AggProvider
    assert AggProvider.keyless is False
    assert AggProvider.probeable is True
    assert AggProvider.requires_authorization is True


def test_the_refusal_and_the_drop_are_the_same_predicate() -> None:
    """`check_account` reuses `_base`/`_token` rather than restating them.

    So an "add account" form is refused for exactly the blobs the pool would
    discard while building — one sentence for a message shown in two very
    different places. This is what notices if the two ever drift apart.
    """
    usable = [
        {"id": "a", "base_url": OR, "api_key": "sk-or"},
        {"id": "b", "preset": "bai", "api_key": "sk-bai"},
        {"id": "c", "base_url": OR, "access_token": "tok"},
        {"id": "d", "base_url": OR, "token": "tok"},
    ]
    unusable = [
        {"id": "e", "base_url": OR},  # a station with no credential
        {"id": "f", "preset": "bai"},  # likewise, by name
        {"id": "g", "api_key": "sk"},  # a credential with no station
        {"id": "h"},  # neither
        {"id": "i", "preset": "no-such-station", "api_key": "sk"},  # a name nobody answers to
    ]

    for data in usable:
        AggProvider.check_account(data)
    for data in unusable:
        with pytest.raises(ValidationError):
            AggProvider.check_account(data)

    def kept(declared: list[dict[str, Any]]) -> set[str]:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=SSE))
        client = httpx.Client(transport=transport)
        try:
            provider = AggProvider(client, [Account(data, provider_id="agg") for data in declared])
            return {account.id for account in provider.accounts}
        finally:
            client.close()

    assert kept(usable) == {"a", "b", "c", "d"}
    assert kept(unusable) == set()
