"""Station presets: what the gateway already knows about an address.

The upstream record is pi-free's `constants.ts` (base URLs) and `config.ts`
(the key accessors — `<PREFIX>_API_KEY` for every station but one), in a clone
that is gitignored — so both halves are transcribed here rather than imported,
and the whole table is locked rather than spot-checked. A spot check would not
notice a station losing its row entirely, which is the one failure that costs a
user an hour of searching for a URL this file was supposed to save them from.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from hoshimi.core.presets import PRESET_BY_ID, PRESETS, get_preset, preset_base_url
from hoshimi.core.providers.agg import AggProvider
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.cline import CLINE_API_BASE
from hoshimi.core.providers.kilo import KILO_BASE_URL
from hoshimi.core.providers.qoder import QoderProvider

# pi-free's base URLs (constants.ts) paired with the env var its key accessor
# actually resolves (config.ts).
UPSTREAM: dict[str, tuple[str, str]] = {
    "zenmux": ("https://zenmux.ai/api/v1", "ZENMUX_API_KEY"),
    "llm7": ("https://api.llm7.io/v1", "LLM7_API_KEY"),
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "DEEPINFRA_TOKEN"),
    "sambanova": ("https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY"),
    "novita": ("https://api.novita.ai/openai/v1", "NOVITA_API_KEY"),
    "routeway": ("https://api.routeway.ai/v1", "ROUTEWAY_API_KEY"),
    "opengateway": ("https://opengateway.gitlawb.com/v1", "OPENGATEWAY_API_KEY"),
    "tokenrouter": ("https://api.tokenrouter.com/v1", "TOKENROUTER_API_KEY"),
    "anyapi": ("https://api.anyapi.ai/v1", "ANYAPI_API_KEY"),
    "bai": ("https://api.b.ai/v1", "BAI_API_KEY"),
    "orcarouter": ("https://api.orcarouter.ai/v1", "ORCAROUTER_API_KEY"),
    "xkiro": ("https://api.xkiro.com/v1", "XKIRO_API_KEY"),
    "fastrouter": ("https://api.fastrouter.ai/api/v1", "FASTROUTER_API_KEY"),
    "requesty": ("https://router.requesty.ai/v1", "REQUESTY_API_KEY"),
    "stepfun": ("https://api.stepfun.ai/step_plan/v1", "STEPFUN_API_KEY"),
    "gmi": ("https://api.gmi-serving.com/v1", "GMI_API_KEY"),
    "agnes": ("https://apihub.agnes-ai.com/v1", "AGNES_API_KEY"),
    "venice": ("https://api.venice.ai/api/v1", "VENICE_API_KEY"),
    "infron": ("https://llm.onerouter.pro/v1", "INFRON_API_KEY"),
    "merge": ("https://api-gateway.merge.dev/v1/openai", "MERGE_API_KEY"),
    "commandcode": ("https://api.commandcode.ai/provider/v1", "COMMAND_CODE_API_KEY"),
    "ollama-cloud": ("https://ollama.com/v1", "OLLAMA_API_KEY"),
}

# pi-free's own list of stations whose catalogue answers without a credential
# (AGENTS.md, "Authentication notes"). Six of these are in our table; kilo and
# cline are not — hoshimi has native lanes for those.
UPSTREAM_KEYLESS_CATALOG = {"zenmux", "deepinfra", "novita", "routeway", "sambanova", "fastrouter"}

# pi-free's *complete* provider set: every id in its PROVIDER_META except the
# four Pi built-ins (opencode, opencode-free, opencode-go, openrouter), paired
# with the address pi-free's own code talks to. This is the set the two
# mechanisms below have to cover between them; nothing else in this file asks
# that question, which is why a station could disappear from hoshimi entirely
# while every row above still looked right.
UPSTREAM_ALL_PROVIDERS: dict[str, str] = {
    "agnes": "https://apihub.agnes-ai.com/v1",
    "anyapi": "https://api.anyapi.ai/v1",
    "bai": "https://api.b.ai/v1",
    # The native three: hoshimi has its own lane for each, with OAuth and
    # credential refresh, so no `agg` preset exists for them.
    "cline": "https://api.cline.bot/api/v1",
    "commandcode": "https://api.commandcode.ai/provider/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "fastrouter": "https://api.fastrouter.ai/api/v1",
    "gmi": "https://api.gmi-serving.com/v1",
    "infron": "https://llm.onerouter.pro/v1",
    "kilo": "https://api.kilo.ai/api/gateway",
    "llm7": "https://api.llm7.io/v1",
    "merge": "https://api-gateway.merge.dev/v1/openai",
    "novita": "https://api.novita.ai/openai/v1",
    "ollama-cloud": "https://ollama.com/v1",
    "opengateway": "https://opengateway.gitlawb.com/v1",
    "orcarouter": "https://api.orcarouter.ai/v1",
    "qoder": "https://api2-v2.qoder.sh",
    "requesty": "https://router.requesty.ai/v1",
    "routeway": "https://api.routeway.ai/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "stepfun": "https://api.stepfun.ai/step_plan/v1",
    "tokenrouter": "https://api.tokenrouter.com/v1",
    "venice": "https://api.venice.ai/api/v1",
    "xkiro": "https://api.xkiro.com/v1",
    "zenmux": "https://zenmux.ai/api/v1",
}

NATIVE_LANES: frozenset[str] = frozenset({"cline", "kilo", "qoder"})

SSE = (
    'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"!"}}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
    "data: [DONE]\n\n"
)


def _provider(account: Account) -> AggProvider:
    return AggProvider(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=SSE))), [account])


def _account(**extra: Any) -> Account:
    data: dict[str, Any] = {"id": "s1", "api_key": "sk-test"}
    data.update(extra)
    return Account(data, provider_id="agg")


# -- the table --------------------------------------------------------------- #


def test_the_whole_table_is_upstreams() -> None:
    assert {preset.id: (preset.base_url, preset.key_env) for preset in PRESETS} == UPSTREAM
    assert len(PRESETS) == 22


def test_the_ids_are_unique_and_indexable() -> None:
    assert len(PRESET_BY_ID) == len(PRESETS)
    assert set(PRESET_BY_ID) == set(UPSTREAM)


def test_a_key_variable_reads_like_a_variable() -> None:
    """A name shape, not a suffix rule.

    This test used to assert ``endswith("_API_KEY")`` — which is the rule that
    put the wrong name in the table to begin with: the table was built by
    joining a prefix to ``_API_KEY``, and then a test was written that could
    only agree with the builder. Twenty-one of twenty-two rows satisfy that
    suffix and the twenty-second is the one upstream singles out as unusual, so
    the assertion and the transcription were wrong together and passed.
    """
    for preset in PRESETS:
        assert preset.key_env == preset.key_env.upper(), preset.id
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", preset.key_env), preset.id


def test_deepinfra_reads_the_variable_upstream_is_intentional_about() -> None:
    """`DEEPINFRA_TOKEN`, spelled out by pi-free as the deliberate exception.

    Every other station resolves ``<PREFIX>_API_KEY``, and the prefix for this
    one is ``DEEPINFRA`` — so transcribing the rule instead of the accessor
    produces ``DEEPINFRA_API_KEY``, which pi-free names explicitly as the
    wrong answer. A table whose own comment claims to record "the env var its
    key accessor actually resolves" cannot be checked by looking at it; it has
    to be checked against the accessor.
    """
    assert PRESET_BY_ID["deepinfra"].key_env == "DEEPINFRA_TOKEN"


def test_commandcode_reads_the_variable_the_upstream_writes() -> None:
    """Its metadata prefix is COMMANDCODE, but its accessor resolves COMMAND_CODE.

    The accessor is what a user would have to export, so it is the name that
    counts — and the one that would be wrong if the prefix were transcribed
    instead.
    """
    assert PRESET_BY_ID["commandcode"].key_env == "COMMAND_CODE_API_KEY"


def test_the_keyless_six_are_the_upstreams() -> None:
    assert {preset.id for preset in PRESETS if preset.catalog_keyless} == UPSTREAM_KEYLESS_CATALOG


def test_the_stations_hoshimi_has_a_lane_for_are_not_here() -> None:
    """An `agg` preset for them would trade a renewing credential for a token."""
    for absent in ("kilo", "cline", "qoder", "opencode", "openrouter"):
        assert absent not in PRESET_BY_ID
        assert get_preset(absent) is None
        assert preset_base_url(absent) == ""


def test_every_upstream_provider_is_accounted_for_exactly_once() -> None:
    """All 25, each through one mechanism and only one.

    The failure this exists to catch is a station that *neither* mechanism
    covers: it would make no row look wrong, it would simply be absent from
    `hoshimi stations` and from the lane list, and the operator would go
    hunting for a URL this project says it knows.
    """
    assert len(UPSTREAM_ALL_PROVIDERS) == 25
    covered = set(PRESET_BY_ID) | NATIVE_LANES
    assert covered == set(UPSTREAM_ALL_PROVIDERS)
    assert not set(PRESET_BY_ID) & NATIVE_LANES


def test_the_two_upstream_tables_agree_about_who_lives_where() -> None:
    """`UPSTREAM` and `UPSTREAM_ALL_PROVIDERS` are one fact written twice."""
    presets = {preset.id: preset.base_url for preset in PRESETS}
    assert presets == {k: v for k, v in UPSTREAM_ALL_PROVIDERS.items() if k not in NATIVE_LANES}
    assert set(UPSTREAM) == set(presets)


def test_the_native_lanes_reach_the_hosts_upstream_reaches() -> None:
    """By host rather than by path: each lane assembles its own paths.

    cline is an OAuth lane with its own endpoints, and kilo's gateway path is
    part of hoshimi's own address — but all three must be the same machine
    pi-free talks to, or the lanes would be quietly pointed at the wrong
    station while every URL in this file still looked plausible.
    """
    for lane, address in (("cline", CLINE_API_BASE), ("kilo", KILO_BASE_URL), ("qoder", QoderProvider.base_url)):
        assert urlsplit(address).netloc == urlsplit(UPSTREAM_ALL_PROVIDERS[lane]).netloc, lane


def test_a_blank_name_is_not_a_station() -> None:
    assert get_preset("") is None
    assert get_preset("   ") is None
    assert preset_base_url("") == ""


def test_a_name_is_matched_exactly() -> None:
    """`Ollama Cloud` is not a preset id; the id is what a config line carries."""
    assert get_preset("ollama-cloud") is not None
    assert get_preset("ollama_cloud") is None
    assert get_preset("Ollama Cloud") is None


# -- the lane using it ------------------------------------------------------- #


def test_a_preset_is_enough_to_reach_a_station() -> None:
    """The whole point: the operator supplies a key, not an address."""
    account = _account(preset="sambanova", api_key="sk-test")
    assert AggProvider._base(account) == "https://api.sambanova.ai/v1"


def test_an_explicit_url_still_wins_over_the_table() -> None:
    """The table is a convenience, never an override."""
    account = _account(preset="sambanova", base_url="https://my.own.station/v1", api_key="sk-test")
    assert AggProvider._base(account) == "https://my.own.station/v1"


def test_an_account_with_a_url_and_no_preset_is_unaffected() -> None:
    account = _account(base_url="https://openrouter.ai/api/v1", api_key="sk-test")
    assert AggProvider._base(account) == "https://openrouter.ai/api/v1"


def test_an_unknown_preset_is_a_mistake_that_says_which_name() -> None:
    """A typo'd name must not read as "no base_url"; the error names it."""
    with pytest.raises(ProviderError) as excinfo:
        AggProvider._base(_account(preset="sambonova", api_key="sk-test"))
    assert "sambonova" in excinfo.value.message
    assert "stations" in excinfo.value.message


def test_a_preset_account_survives_construction() -> None:
    """A preset-only account is usable, not dropped as half-filled."""
    provider = _provider(_account(preset="novita", api_key="sk-test"))
    assert [account.id for account in provider.accounts] == ["s1"]


def test_an_account_with_neither_url_nor_preset_is_still_dropped() -> None:
    provider = _provider(_account(api_key="sk-test"))
    assert provider.accounts == []


def test_an_account_named_after_an_unknown_station_is_dropped() -> None:
    """The mistake is caught where every other half-filled account is caught."""
    provider = _provider(_account(preset="sambonova", api_key="sk-test"))
    assert provider.accounts == []


def test_the_preset_carries_the_credential_the_account_brought() -> None:
    """The table knows addresses; the account still brings the key."""
    provider = _provider(_account(preset="llm7", api_key="sk-mine"))
    headers = provider.build_headers(provider.accounts[0], "some-model")
    assert headers["Authorization"] == "Bearer sk-mine"
