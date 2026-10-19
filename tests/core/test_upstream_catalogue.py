"""Every free id the upstream channel-pack advertises must exist in the port.

The upstream tables live in `dsh-our-free-model/vendor/channel-pack/src/*-product.ts`, which
is an external clone and therefore gitignored — so the transcribed tables below are the
record. They are checked against the port, not against the clone: a machine without the
clone runs this test all the same.

Two directions are asserted:

* ids the upstream fallback table carries, the port must carry too (nothing free is lost);
* ids upstream deliberately dropped, the port must not resurrect (a model that only ever
  fails belongs in no picker).

Upstream record: `*-product.ts`, transcribed 2026-10-12.
"""

from __future__ import annotations

import httpx

from hoshimi.core.providers import lobsterai as lobsterai_mod
from hoshimi.core.providers import loomy as loomy_mod
from hoshimi.core.providers import opencode as opencode_mod
from hoshimi.core.providers import qoder as qoder_mod
from hoshimi.core.providers import trae as trae_mod
from hoshimi.core.providers.base import Provider

# -- upstream tables --------------------------------------------------------- #

# `qoder-product.ts` `QODER_FALLBACK_MODELS` (17).
QODER_INTL_UPSTREAM = [
    "auto", "ultimate", "performance", "efficient", "smodel", "cmodel",
    "qmodel_38max", "qfmodel", "qmodel_latest", "qmodel",
    "kmodel_latest", "kmodel", "gmodel", "gfmodel", "dmodel", "dfmodel", "mmodel",
]

# `qoder-product.ts` `QODER_CN_FALLBACK_MODELS` (14). The upstream comment calls out that the
# CN catalogue is its own capture (2026-09-27) and must not reuse the international table:
# five of those ids are rejected here, and two are CN-only.
QODER_CN_UPSTREAM = [
    "auto", "qmodel_38max", "qfmodel", "qmodel_latest", "qmodel", "q37fmodel",
    "dmodel", "dfmodel", "gmodel", "gfmodel", "gm51model",
    "kmodel_latest", "kmodel", "mmodel",
]
QODER_CN_REJECTED = ["ultimate", "performance", "efficient", "smodel", "cmodel"]

# `trae-product.ts` `TRAE_FALLBACK_MODELS` (32) minus the four `isHidden` internal agents
# (`browser_use_subagent`, `explore_sub_agent_v13`, `explore_sub_agent_v2`, `summary`) and the
# eleven `custom_model_*` placeholders — upstream's own `isTraeModelCallable` drops both kinds.
TRAE_UPSTREAM = [
    "DeepSeek-V4-Flash-Official", "Doubao-Seed-2.1-Pro", "seed-code-pro-0430",
    "Doubao-Seed-2.1-Turbo", "Doubao-Seed-2.0-Code",
    "glm-5.2", "glm-5-turbo", "glm-5",
    "DeepSeek-V4-Pro", "DeepSeek-V4-Flash",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6",
    "minimax-m3", "qwen-3.7-plus", "sagitta", "aquila",
]

# `lobsterai-product.ts` `LOBSTERAI_FALLBACK_MODELS` (19).
LOBSTERAI_UPSTREAM = [
    "deepseek-v4-flash", "deepseek-v4-pro", "MiniMax-M3", "MiniMax-M2.7",
    "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus-2026-04-20",
    "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6", "kimi-k2.5",
    "doubao-seed-2-1-pro-260628", "doubao-seed-2-1-turbo-260628",
    "doubao-seed-2-0-code-preview-260215",
    "glm-5.2", "glm-5.1", "glm-5v-turbo", "glm-5",
]

# `loomy-product.ts` `LOOMY_FALLBACK_MODELS` (8).
LOOMY_UPSTREAM = [
    "deepseek-v4-flash-0731", "MiniMax-M3", "Kimi-k2.6", "qwen-3.8-max",
    "GLM-5.3-Flash", "qwen3.8-flash", "spark-x", "mimo-v2.5",
]

# `opencode-product.ts` `OPENCODE_FALLBACK_MODELS` (16), split by `isFree` — upstream reads
# that flag as "the anonymous lane (`Bearer public`) can use it", which is the only dimension
# a keyless pool cares about.
OPENCODE_UPSTREAM_FREE = [
    "big-pickle", "space-bunny-free", "longcat-2.5-preview-free",
    "mimo-v2.6-flash-free", "mimo-v2.5-free", "deepseek-v4.1-flash-free", "glm-5.3-flash-free",
    "nemotron-3-ultra-free", "nemotron-3.5-lightning-free",
]
# `isFree: false` — these answer 401 without a paid key, so a keyless lane never lists them.
OPENCODE_UPSTREAM_PAID = [
    "deepseek-v4-flash", "deepseek-v4.1-flash", "glm-5.2", "kimi-k2.5",
    "minimax-m2.7", "minimax-m3", "qwen3.8-max",
]
# Removed upstream with a note: `/v1/messages` 500s for both anonymous and paid keys while the
# chat endpoint 404s, so it fails on every path and only clutters the picker.
OPENCODE_UPSTREAM_DROPPED = ["ling-3.0-flash-fin-free"]


def _fallback(provider: type[Provider]) -> list[str]:
    return list(provider.fallback_model_list)  # type: ignore[attr-defined]


# -- qoder ------------------------------------------------------------------- #

def test_qoder_intl_carries_every_upstream_id() -> None:
    missing = set(QODER_INTL_UPSTREAM) - set(qoder_mod.QODER_INTL_MODELS)
    assert not missing, f"international lane lost upstream ids: {sorted(missing)}"
    assert len(qoder_mod.QODER_INTL_MODELS) == len(QODER_INTL_UPSTREAM)


def test_qodercn_uses_its_own_catalogue_not_the_international_copy() -> None:
    """The CN lane used to be `list(QODER_INTL_MODELS)` — five ids the CN endpoint rejects."""
    cn = qoder_mod.QODER_CN_MODELS
    missing = set(QODER_CN_UPSTREAM) - set(cn)
    assert not missing, f"CN lane lost upstream ids: {sorted(missing)}"
    assert len(cn) == len(QODER_CN_UPSTREAM), "CN catalogue is its own 14, not the intl 17"

    leaked = set(QODER_CN_REJECTED) & set(cn)
    assert not leaked, f"CN lane advertises ids it rejects: {sorted(leaked)}"


def test_the_two_catalogues_diverge_where_upstream_says_they_do() -> None:
    intl = set(qoder_mod.QODER_INTL_MODELS)
    cn = set(qoder_mod.QODER_CN_MODELS)
    assert cn - intl == {"q37fmodel", "gm51model"}, "CN-only ids belong on CN alone"
    assert intl - cn == set(QODER_CN_REJECTED), "the five rejected ids belong on intl alone"


# -- trae -------------------------------------------------------------------- #

def test_trae_carries_the_callable_upstream_ids() -> None:
    missing = set(TRAE_UPSTREAM) - set(trae_mod.TRAE_MODELS)
    assert not missing, f"trae lost upstream ids: {sorted(missing)}"
    assert len(trae_mod.TRAE_MODELS) == len(TRAE_UPSTREAM)


def test_trae_lists_no_internal_or_placeholder_id() -> None:
    """Upstream's `isTraeModelCallable` drops `custom_model_*`; `isHidden` drops the agents."""
    internal = [m for m in trae_mod.TRAE_MODELS if m.startswith("custom_model_")]
    assert not internal, f"trae exposes placeholder slots: {internal}"
    for hidden in ("browser_use_subagent", "explore_sub_agent_v13", "explore_sub_agent_v2", "summary"):
        assert hidden not in trae_mod.TRAE_MODELS


# -- lobsterai / loomy ------------------------------------------------------- #

def test_lobsterai_fallback_is_the_whole_upstream_table() -> None:
    have = set(lobsterai_mod.LobsteraiProvider.fallback_model_list)
    missing = set(LOBSTERAI_UPSTREAM) - have
    assert not missing, f"lobsterai fallback lost upstream ids: {sorted(missing)}"
    # The port once spelled this `qwen-3.7-max`; upstream's table (and the station) use no hyphen.
    assert "qwen3.7-max" in have
    assert "qwen-3.7-max" not in have


def test_loomy_fallback_is_the_whole_upstream_table() -> None:
    have = set(loomy_mod.LoomyProvider.fallback_model_list)
    missing = set(LOOMY_UPSTREAM) - have
    assert not missing, f"loomy fallback lost upstream ids: {sorted(missing)}"


# -- opencode ---------------------------------------------------------------- #

def test_opencode_fallback_carries_the_free_upstream_ids() -> None:
    have = set(opencode_mod.OpencodeProvider.fallback_model_list)
    missing = set(OPENCODE_UPSTREAM_FREE) - have
    assert not missing, f"opencode lost free upstream ids: {sorted(missing)}"


def test_opencode_leaves_the_paid_upstream_ids_unlisted() -> None:
    """A keyless lane that lists a paid id hands the caller a 401 it cannot fix."""
    have = set(opencode_mod.OpencodeProvider.fallback_model_list)
    listed = set(OPENCODE_UPSTREAM_PAID) & have
    assert not listed, f"keyless lane lists paid ids: {sorted(listed)}"


def test_opencode_does_not_resurrect_the_id_upstream_removed() -> None:
    have = set(opencode_mod.OpencodeProvider.fallback_model_list)
    listed = set(OPENCODE_UPSTREAM_DROPPED) & have
    assert not listed, f"an id upstream proved unusable is back in the picker: {sorted(listed)}"


# -- the live lane still wins ------------------------------------------------ #

def test_the_remote_catalogue_still_outranks_the_fallback() -> None:
    """Every station that can fetch a catalogue keeps `fetch_models` ahead of its fallback.

    The fallback tables above only speak when the upstream is unreachable; nothing here may
    turn them into the primary source.
    """

    class _Station(Provider):
        id = "station"
        fetched = 0

        def __init__(self) -> None:
            super().__init__(httpx.Client(), [])

        def fallback_models(self) -> list[str]:
            return ["fallback-only"]

        def fetch_models(self) -> list[str]:
            type(self).fetched += 1
            return ["live-1", "live-2"]

        def stream_raw(self, account, model, body):  # type: ignore[no-untyped-def]
            yield {"type": "text_delta", "text": ""}

    station = _Station()
    for _ in range(3):
        assert station.models() == ["live-1", "live-2"]
    assert _Station.fetched == 1, "the catalogue is cached, not refetched per call"
