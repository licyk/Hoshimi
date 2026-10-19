"""The Zhipu free lane: the fixed base, the bearer, and the seven free ids."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hoshimi.core.catalog import zhipu_spec
from hoshimi.core.providers import PROVIDER_CLASSES, build_providers
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.zhipu import ZHIPU_BASE_URL, ZHIPU_FREE_MODELS, ZhipuProvider

SSE = (
    'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"!"}}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
    "data: [DONE]\n\n"
)


def _account(**extra: Any) -> Account:
    data = {"id": "zhipu-1", "api_key": "sk-zhipu-1"}
    data.update(extra)
    return Account(data, provider_id="zhipu")


def _provider(accounts: list[Account], handler: Any = None, options: dict[str, Any] | None = None) -> ZhipuProvider:
    transport = httpx.MockTransport(handler) if handler else httpx.MockTransport(lambda request: httpx.Response(200, text=SSE))
    return ZhipuProvider(httpx.Client(transport=transport), accounts, options)


def test_a_turn_goes_to_the_station_with_the_bearer() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, text=SSE)

    provider = _provider([_account()], handler)
    chunks = list(provider.stream_raw(provider.accounts[0], "glm-4.7-flash", {"messages": [{"role": "user", "content": "x"}]}))
    assert seen["host"] == "open.bigmodel.cn"
    assert seen["path"] == "/api/paas/v4/chat/completions"
    assert seen["authorization"] == "Bearer sk-zhipu-1"
    assert [chunk["type"] for chunk in chunks] == ["text_delta", "text_delta", "usage", "finish"]


def test_the_key_is_read_from_api_key_or_access_token() -> None:
    provider = _provider([_account()])
    assert provider._token(provider.accounts[0]) == "sk-zhipu-1"
    alias = _provider([_account(api_key="", access_token="sk-alias")])
    assert alias._token(alias.accounts[0]) == "sk-alias"
    with pytest.raises(ProviderError) as missing:
        provider._token(Account({"id": "zhipu-2"}, provider_id="zhipu"))
    assert "api_key" in missing.value.message


def test_the_international_station_is_one_option_away() -> None:
    provider = _provider([_account()], options={"base_url": "https://api.z.ai/api/paas/v4/"})
    assert provider.base_url == "https://api.z.ai/api/paas/v4"
    provider_default = _provider([_account()])
    assert provider_default.base_url == ZHIPU_BASE_URL


def test_the_listing_is_the_source_and_the_free_roster_the_floor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/paas/v4/models"
        assert request.headers.get("authorization") == "Bearer sk-zhipu-1"
        return httpx.Response(200, json={"data": [{"id": "glm-4.7-flash"}, {"id": "glm-5.3"}]})

    served = _provider([_account()], handler)
    assert served.models() == ["glm-4.7-flash", "glm-5.3"]

    # The listing unreachable: the seven free ids are what the lane still serves.
    offline = _provider([_account()], lambda request: httpx.Response(500, text=""))
    assert offline.models() == list(ZHIPU_FREE_MODELS)


def test_the_free_rows_carry_their_published_capacities() -> None:
    flash = zhipu_spec("glm-4.7-flash", "zhipu")
    assert (flash.name, flash.vision, flash.reasoning) == ("GLM-4.7-Flash", False, False)
    assert (flash.context_window, flash.max_output) == (2097152, 131072)

    z1 = zhipu_spec("glm-z1-flash", "zhipu")
    # A thinking model by construction: the budget ladder is the only control
    # that reaches the wire, and thinking cannot be switched off.
    assert (z1.reasoning, z1.can_disable_thinking, z1.efforts) == (True, False, ())
    assert z1.context_window == 131072

    vision = zhipu_spec("glm-4.6v-flash", "zhipu")
    assert (vision.name, vision.vision, vision.max_output) == ("GLM-4.6V-Flash", True, 32768)
    assert vision.reasoning is False

    thinking_vision = zhipu_spec("glm-4.1v-thinking-flash", "zhipu")
    assert (thinking_vision.vision, thinking_vision.reasoning, thinking_vision.can_disable_thinking) == (True, True, False)

    tiny = zhipu_spec("glm-4v-flash", "zhipu")
    assert (tiny.vision, tiny.context_window, tiny.max_output) == (True, 16384, 1024)

    # An id the table never declared keeps the inert default (and the generic
    # title-casing, since only the seven free rows carry a published name).
    unknown = zhipu_spec("glm-5.3", "zhipu")
    assert (unknown.name, unknown.reasoning, unknown.context_window) == ("Glm 5 3", False, 131072)


def test_the_lane_is_built_only_with_a_key() -> None:
    client = httpx.Client()
    assert "zhipu" not in build_providers(client, {})
    assert "zhipu" in build_providers(client, {"zhipu": [{"id": "zhipu-1", "api_key": "sk"}]})
    assert "zhipu" not in build_providers(client, {"zhipu": [{"id": "zhipu-1", "api_key": "sk"}]}, disabled={"zhipu"})
    assert PROVIDER_CLASSES["zhipu"] is ZhipuProvider
    assert ZhipuProvider.keyless is False
    assert ZhipuProvider.requires_authorization is True
    assert ZhipuProvider.probeable is False
