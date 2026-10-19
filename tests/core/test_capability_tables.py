"""The capability tables, and the effort menus they carry.

The upstream record lives in `dsh-our-free-model/vendor/channel-pack/src/*-product.ts`,
an external clone that is gitignored — so the tables below are transcribed rather than
imported. They are checked against the port itself: a machine without the clone runs this
test all the same.

Two things are locked for each station:

* the row itself — context window, output ceiling, image support, the display label;
* how a declared menu reaches the wire — the exact JSON merge patch, the id of the rung
  that switches thinking off (upstream spells that `none` here, `off` there,
  `disabled` elsewhere), and which rung an out-of-menu level is allowed to land on.

A station whose rungs arrive from the remote catalogue instead of a table must stay
inert: a menu invented here would send levels the station rejects.

Upstream record: `product.ts` (Buddy), `cline-product.ts`, `loomy-product.ts`,
`trae-product.ts`, `qoder-product.ts`, `minimax-product.ts`, `zcode-product.ts`,
`raccoon-product.ts`, `gemini.ts`, `llm-adapter.ts` (CodeArts), transcribed 2026-10-12;
and `providers/commandcode/commandcode-pricing.ts` for the aggregator lane,
transcribed from the station's pricing page as last verified 2026-08-25.
"""

from __future__ import annotations

from typing import Any

import httpx

from hoshimi.core.catalog import (
    COMMANDCODE_REASONING,
    COMMANDCODE_VISION,
    FALLBACK_CONTEXT_WINDOW,
    FALLBACK_MAX_OUTPUT,
    ModelSpec,
    agg_capability,
    agg_spec,
    buddy_spec,
    cline_spec,
    codearts_spec,
    gemini_spec,
    loomy_spec,
    minimax_spec,
    qoder_spec,
    raccoon_spec,
    trae_spec,
    zcode_spec,
)
from hoshimi.core.effort import apply_effort, default_effort_for, effort_patch_for, efforts_for
from hoshimi.core.free_marks import STATION_FREE_IDS

# -- upstream tables --------------------------------------------------------- #

# `cline-product.ts` `CLINE_FALLBACK_MODELS`.
CLINE_UPSTREAM: dict[str, dict[str, Any]] = {
    "stealth/space-bunny-alpha": {"name": "Space Bunny Alpha", "contextWindow": 1_000_000, "maxTokens": 524_288, "supportsImage": True},
    "cline-free/mimo-v2.6-flash": {"name": "MiMo-V2.6-Flash", "contextWindow": 1_048_576, "maxTokens": 131_072, "supportsImage": True},
    "cline-free/muse-spark-1.3-contributor": {"name": "Muse Spark 1.3 Contributor", "contextWindow": 1_048_576, "maxTokens": 943_718, "supportsImage": True},
}

# `cline-product.ts` `CLINE_REASONING_EFFORTS` — the order is the picker's order, and the
# last rung is labelled "Extra" in the IDE while its id stays `max`.
CLINE_RUNGS = ("none", "low", "medium", "high", "max")
CLINE_DEFAULT = "high"

# `loomy-product.ts` `LOOMY_FALLBACK_MODELS` (the eight `type==='chat'` rows).
# Names ship with the billing multiplier (` · x3.0`) which upstream strips before showing.
LOOMY_UPSTREAM: dict[str, dict[str, Any]] = {
    "deepseek-v4-flash-0731": {"name": "DeepSeek V4 Flash 0731", "contextWindow": 1_048_576},
    "MiniMax-M3": {"name": "MiniMax M3", "contextWindow": 1_048_576},
    "Kimi-k2.6": {"name": "Kimi k2.6", "contextWindow": 262_144},
    "qwen-3.8-max": {"name": "Qwen 3.8 Max", "contextWindow": 1_000_000},
    "GLM-5.3-Flash": {"name": "GLM 5.3 Flash", "contextWindow": 1_048_576},
    "qwen3.8-flash": {"name": "qwen 3.8 flash", "contextWindow": 1_000_000},
    "spark-x": {"name": "Spark X2.5", "contextWindow": 1_048_576},
    "mimo-v2.5": {"name": "MiMo V2.5", "contextWindow": 1_048_576},
}

# `loomy-product.ts` `LOOMY_EFFORTS` + `LOOMY_DEFAULT_EFFORT`. The default is `high` even
# though the remote catalogue declares `low`: upstream deliberately does not follow it so
# that "the remote answered" and "the remote failed" send the same level.
LOOMY_RUNGS = ("none", "low", "medium", "high", "xhigh")
LOOMY_DEFAULT = "high"

# `product.ts` `CODEBUDDY_FALLBACK_MODELS` — (id, contextWindow, maxOutputTokens, rungs,
# declared default). Upstream writes `defaultReasoningEffort` on some rows only, and
# always as `high`; the rest leave it off and the menu's own rung answers instead.
CODEBUDDY_UPSTREAM: dict[str, tuple[int, int, tuple[str, ...], str | None]] = {
    "hy4-preview": (1_000_000, 64_000, ("high",), "high"),
    "hy3": (192_000, 64_000, ("low", "high"), "high"),
    "hy3-x": (192_000, 64_000, ("low", "high"), "high"),
    "deepseek-v4.1-flash": (1_000_000, 128_000, ("low", "high", "max"), "high"),
    "deepseek-v4-pro": (1_000_000, 128_000, ("low", "high", "xhigh"), "high"),
    "deepseek-v4-flash": (1_000_000, 50_000, ("low", "high", "max"), "high"),
    "glm-5.3": (1_000_000, 64_000, ("low", "high", "max"), "high"),
    "glm-5.3-flash": (1_000_000, 32_000, ("low", "high", "max"), "high"),
    "glm-5.2": (1_000_000, 64_000, ("high", "xhigh"), "high"),
    "glm-5.1": (200_000, 48_000, ("medium",), None),
    "glm-5v-turbo": (200_000, 64_000, ("medium",), None),
    "kimi-k3-1": (1_000_000, 32_000, ("medium",), None),
    "kimi-k2.8-preview": (1_000_000, 64_000, ("low", "high", "max"), "high"),
    "kimi-k2.7": (256_000, 32_000, ("medium",), None),
    "kimi-k2.6": (256_000, 32_000, ("medium",), None),
    "minimax-m3": (512_000, 64_000, ("medium",), None),
}

# `product.ts` `WORKBUDDY_FALLBACK_MODELS` (id -> (contextWindow, rungs, declared default));
# the five alias ids resolve to a label of their own rather than to the id title-cased.
WORKBUDDY_UPSTREAM: dict[str, tuple[int, tuple[str, ...], str | None]] = {
    "default-model": (176_000, (), None),
    "fast-model": (200_000, ("medium",), None),
    "balanced-model": (256_000, ("medium",), None),
    "primary-model": (272_000, ("high",), None),
    "deep-model": (176_000, (), None),
    "hy4-preview-f": (1_000_000, ("high",), "high"),
    "hy4-preview": (1_000_000, ("high",), "high"),
    "hy3": (192_000, ("low", "high"), "high"),
    "deepseek-v4.1-flash": (1_000_000, ("high",), "high"),
    "deepseek-v4.1-flash-sg": (1_000_000, ("high",), "high"),
    "gpt-6-astra": (1_000_000, ("low", "medium", "high", "xhigh", "max"), "high"),
    "gpt-5.6-sol": (1_000_000, ("low", "medium", "high", "xhigh", "max"), "high"),
    "gpt-5.6-terra": (1_000_000, ("low", "medium", "high", "xhigh", "max"), "high"),
    "gpt-5.6-luna": (1_000_000, ("low", "medium", "high", "xhigh", "max"), "high"),
    "gpt-5.5": (1_000_000, ("low", "medium", "high", "xhigh"), "high"),
    "gpt-5.4": (272_000, ("low", "medium", "high", "xhigh"), "high"),
    "gpt-5.3-codex": (272_000, ("medium",), None),
    "gemini-3.5-flash": (1_000_000, ("medium",), None),
    "glm-5.3": (1_000_000, ("low", "high", "max"), "high"),
    "glm-5.2": (1_000_000, ("high", "xhigh"), "high"),
    "kimi-k3": (1_000_000, ("medium",), None),
    "kimi-k2.8-preview": (1_000_000, ("low", "high", "max"), "high"),
    "kimi-k2.6": (256_000, ("medium",), None),
}

WORKBUDDY_LABELS = {
    "default-model": "Auto",
    "fast-model": "Fast",
    "balanced-model": "Balanced",
    "primary-model": "Primary",
    "deep-model": "Deep",
}

# `trae-product.ts` `TRAE_FALLBACK_MODELS`: every row carries `contextWindow` and nothing
# else — no image flag, no output ceiling, no rungs (those come from the remote catalogue).
TRAE_CONTEXT_WINDOW = 200_000

# The Buddy lanes offer no rung that switches thinking off, so no level a caller sends may
# be treated as one; Cline and Loomy both call it `none`.
NO_OFF_RUNG = None

# `qoder-product.ts` `QODER_FALLBACK_MODELS` — the row as the picker sees it:
# (display label, context window, image support, rungs *after* the client appends `none`
# for a `disabled` branch, the declared default, whether that branch exists).
QODER_INTL_UPSTREAM: dict[str, tuple[str, int, bool, tuple[str, ...], str | None, bool]] = {
    "auto": ("Auto", 200_000, True, (), None, False),
    "ultimate": ("Ultimate", 1_000_000, True, ("xhigh", "high", "low", "max", "medium", "none"), "high", True),
    "performance": ("Performance", 1_000_000, True, ("xhigh", "high", "low", "max", "medium", "none"), "medium", True),
    "efficient": ("Efficient", 1_000_000, True, (), None, False),
    "smodel": ("Sonus", 1_000_000, True, ("xhigh", "high", "low", "max", "medium"), "high", False),
    "cmodel": ("Cantus", 1_000_000, True, ("xhigh", "high", "low", "max", "medium"), "high", False),
    "qmodel_38max": ("Qwen3.8-Max", 1_000_000, True, ("xhigh", "low", "medium", "none"), "xhigh", True),
    "qfmodel": ("Qwen3.8-Flash", 1_000_000, True, ("xhigh", "low", "medium", "none"), "medium", True),
    "qmodel_latest": ("Qwen3.7-Max", 1_000_000, True, ("none",), None, True),
    "qmodel": ("Qwen3.7-Plus", 1_000_000, True, ("none",), None, True),
    "kmodel_latest": ("Kimi-K3", 1_000_000, True, ("high", "low", "max"), "max", False),
    "kmodel": ("Kimi-K2.8-Preview", 1_000_000, True, ("high", "low", "max"), "max", False),
    "gmodel": ("GLM-5.3", 1_000_000, True, ("high", "low", "max"), "max", False),
    "gfmodel": ("GLM-5.3-Flash", 1_000_000, True, ("high", "max"), "max", False),
    "dmodel": ("DeepSeek-V4-Pro", 1_000_000, True, ("high", "max", "none"), "max", True),
    "dfmodel": ("DeepSeek-Flash", 1_000_000, True, ("high", "max", "low", "none"), "max", True),
    "mmodel": ("MiniMax-M3", 1_000_000, True, (), None, False),
}

# `qoder-product.ts` `QODER_CN_FALLBACK_MODELS` — its own capture, not a copy: two ids are
# CN-only, and `mmodel` is a different MiniMax generation than on the international site.
QODER_CN_UPSTREAM: dict[str, tuple[str, int, bool, tuple[str, ...], str | None, bool]] = {
    "auto": ("Auto", 200_000, True, (), None, False),
    "qmodel_38max": ("Qwen3.8-Max", 1_000_000, True, ("xhigh", "low", "medium", "none"), "medium", True),
    "qfmodel": ("Qwen3.8-Flash", 1_000_000, True, ("xhigh", "low", "medium", "none"), "medium", True),
    "qmodel_latest": ("Qwen3.7-Max", 1_000_000, True, ("none",), None, True),
    "qmodel": ("Qwen3.7-Plus", 1_000_000, True, ("none",), None, True),
    "q37fmodel": ("Qwen3.7-Flash", 1_000_000, True, (), None, False),
    "dmodel": ("DeepSeek-V4-Pro", 1_000_000, True, ("high", "max", "none"), "max", True),
    "dfmodel": ("DeepSeek-Flash", 1_000_000, True, ("high", "max", "low", "none"), "max", True),
    "gmodel": ("GLM-5.3", 1_000_000, True, ("high", "low", "max"), "max", False),
    "gfmodel": ("GLM-5.3-Flash", 1_000_000, True, ("high", "max"), "max", False),
    "gm51model": ("GLM-5.2", 1_000_000, True, ("high", "max", "none"), "max", True),
    "kmodel_latest": ("Kimi-K3", 1_000_000, True, ("high", "low", "max"), "max", False),
    "kmodel": ("Kimi-K2.8-Preview", 1_000_000, True, ("high", "low", "max"), "max", False),
    "mmodel": ("MiniMax-M2.7", 200_000, False, (), None, False),
}

# `minimax-product.ts` `MINIMAX_FALLBACK_MODELS` — (display label, context window,
# maxTokens, image support, rungs, declared default, thinkingMode). Upstream declares no
# default for `switchable` M3 (measured: it does not think when `thinking` is absent), so
# the port pins `none` — the same outcome, said in a vocabulary the picker has to read.
MINIMAX_UPSTREAM: dict[str, tuple[str, int, int, bool, tuple[str, ...], str | None, str]] = {
    "MiniMax-M3.1-Flash-Preview": ("M3.1-Flash-Preview", 1_000_000, 128_000, True, ("default", "low", "medium", "high", "xhigh", "max"), "default", "forced_on"),
    "MiniMax-M3": ("M3", 1_000_000, 128_000, True, ("on", "none"), "none", "switchable"),
    "MiniMax-M2.7-highspeed": ("M2.7-highspeed", 200_000, 128_000, False, (), None, "forced_on"),
    "MiniMax-M2.7": ("M2.7", 200_000, 128_000, False, (), None, "forced_on"),
}

# `zcode-product.ts` `ZCODE_FALLBACK_MODELS` — (context window, maxTokens, image support,
# `reasoning.levels`, `reasoning.defaultLevel`). Upstream warns that GLM-5.3 has an empty
# `capabilities` object and therefore *no* vision, unlike its Flash sibling.
ZCODE_UPSTREAM: dict[str, tuple[int, int, bool, tuple[str, ...], str]] = {
    "GLM-5.3-Flash": (1_000_000, 128_000, True, ("low", "high", "max"), "max"),
    "GLM-5.3": (1_000_000, 128_000, False, ("low", "high", "max"), "max"),
}

# `llm-adapter.ts` CodeArts — `CONTEXT_WINDOWS` (the rest publish none) and the two rungs
# `resolveModel` declares for *every* id, because the control is gateway-wide.
CODEARTS_CONTEXT_WINDOWS: dict[str, int] = {
    "GLM-5.2": 202_752,
    "glm-5.3-flash": 1_048_576,
    "deepseek-v4-flash": 1_048_576,
    "deepseek-v4-pro": 1_048_576,
    "deepseek-v4.1-flash": 1_000_000,
}
CODEARTS_RUNGS = ("on", "off")

# `raccoon-product.ts` `RACCOON_FALLBACK_MODELS` — (display label without the billing
# multiplier, context window, `params.maxTokens`). Every row takes images, including the
# two whose remote `tags` omit `vision` and that upstream therefore measured by hand.
RACCOON_UPSTREAM: dict[str, tuple[str, int, int]] = {
    "sn-sensenova-6-8-flash": ("SenseNova-6.8-Flash", 256_000, 63_999),
    "sn-sensenova-6-8-flash-lite": ("SenseNova-6.8-Flash-Lite", 256_000, 63_999),
    "sn-glm-5-3": ("GLM-5-3", 1_000_000, 100_000),
    "sn-kimi-k3": ("Kimi-K3", 1_000_000, 100_000),
    "sn-glm-5-3-flash": ("GLM-5-3-Flash", 1_000_000, 100_000),
    "sn-deepseek-v4-1-flash": ("DeepSeek-V4.1-Flash", 1_000_000, 100_000),
}


def _patched(spec: ModelSpec, level: str) -> dict[str, Any]:
    """The body this station would send for one level, against an empty call."""
    return apply_effort(spec, {"messages": [], "reasoning_effort": level})


def _turn(spec: ModelSpec, level: str) -> dict[str, Any]:
    """The same, with the one message the Gemini envelope insists on."""
    return apply_effort(spec, {"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": level})


# -- the Cline free lane ----------------------------------------------------- #


def test_cline_rows_match_the_upstream_table() -> None:
    for model_id, expected in CLINE_UPSTREAM.items():
        spec = cline_spec(model_id, "cline")
        assert spec.name == expected["name"], f"{model_id}: {spec.name!r}"
        assert spec.context_window == expected["contextWindow"], f"{model_id}: {spec.context_window}"
        assert spec.max_output == expected["maxTokens"], f"{model_id}: {spec.max_output}"
        assert spec.vision is expected["supportsImage"], f"{model_id}: vision {spec.vision}"


def test_cline_shares_one_five_rung_menu_across_its_three_ids() -> None:
    for model_id in CLINE_UPSTREAM:
        spec = cline_spec(model_id, "cline")
        assert spec.efforts == CLINE_RUNGS, f"{model_id}: {spec.efforts}"
        assert spec.effort_default == CLINE_DEFAULT
        assert spec.effort_off_id == "none"


def test_cline_menu_reaches_the_wire_as_a_verbatim_reasoning_effort() -> None:
    spec = cline_spec("stealth/space-bunny-alpha", "cline")
    assert effort_patch_for("high", spec) == {"reasoning_effort": "high"}
    # `none` is a plain rung here: the off patch does not exist, so the level itself
    # is what switches thinking off.
    assert spec.effort_off_patch is None
    assert effort_patch_for("none", spec) == {"reasoning_effort": "none"}
    assert _patched(spec, "none")["reasoning_effort"] == "none"
    assert _patched(spec, "max")["reasoning_effort"] == "max"


def test_cline_capacity_bounds_the_generation_ceiling() -> None:
    spec = cline_spec("stealth/space-bunny-alpha", "cline")
    assert _patched(spec, "high")["max_tokens"] == 524_288
    # A caller's own ceiling only ever lowers it.
    assert apply_effort(spec, {"messages": [], "reasoning_effort": "high", "max_tokens": 1024})["max_tokens"] == 1024


def test_cline_level_outside_the_menu_falls_to_the_default() -> None:
    """A rung that is not there must never be invented — `off` is not a Cline level."""
    spec = cline_spec("stealth/space-bunny-alpha", "cline")
    assert default_effort_for(spec) == "high"
    assert _patched(spec, "off")["reasoning_effort"] == "high"
    # …while a level the alias table knows resolves to its own rung.
    assert _patched(spec, "minimal")["reasoning_effort"] == "low"


def test_cline_body_is_untouched_when_the_caller_names_no_level() -> None:
    spec = cline_spec("stealth/space-bunny-alpha", "cline")
    raw = {"messages": [], "max_tokens": 42}
    assert apply_effort(spec, dict(raw)) == raw


def test_cline_unknown_id_keeps_the_lane_menu_but_invents_no_capacity() -> None:
    """A remote id the table never saw still needs the picker's five rungs.

    The menu belongs to the lane (the remote catalogue carries no rungs), but
    the context window belongs to a measurement — an id nobody measured must
    not walk away with a made-up one.
    """
    spec = cline_spec("cline-free/solar-mini4", "cline")
    assert spec.efforts == CLINE_RUNGS, spec.efforts
    assert spec.effort_default == CLINE_DEFAULT
    assert spec.effort_off_id == "none"
    assert spec.reasoning is True
    assert spec.context_window == FALLBACK_CONTEXT_WINDOW
    assert spec.vision is False
    assert spec.name != ""


# -- the Loomy lane ---------------------------------------------------------- #


def test_loomy_rows_match_the_upstream_table() -> None:
    for model_id, expected in LOOMY_UPSTREAM.items():
        spec = loomy_spec(model_id, "loomy")
        assert spec.name == expected["name"], f"{model_id}: {spec.name!r}"
        assert spec.context_window == expected["contextWindow"], f"{model_id}: {spec.context_window}"
        # The upstream table carries no image flag, so no row claims vision.
        assert spec.vision is False, f"{model_id}: vision {spec.vision}"


def test_loomy_labels_drop_the_billing_multiplier() -> None:
    """` · x3.0` is a price tag, not part of the model's name."""
    for model_id in LOOMY_UPSTREAM:
        assert "·" not in loomy_spec(model_id, "loomy").name, model_id


def test_loomy_menu_defaults_to_high_even_though_the_remote_says_low() -> None:
    """Upstream pins the default so the live and fallback paths send the same level."""
    for model_id in LOOMY_UPSTREAM:
        spec = loomy_spec(model_id, "loomy")
        assert spec.efforts == LOOMY_RUNGS, f"{model_id}: {spec.efforts}"
        assert spec.effort_default == LOOMY_DEFAULT
        assert spec.effort_off_id == "none"


def test_loomy_menu_reaches_the_wire_verbatim() -> None:
    spec = loomy_spec("qwen3.8-flash", "loomy")
    assert effort_patch_for("none", spec) == {"reasoning_effort": "none"}
    assert effort_patch_for("xhigh", spec) == {"reasoning_effort": "xhigh"}
    assert _patched(spec, "xhigh")["reasoning_effort"] == "xhigh"


# -- the Buddy lanes --------------------------------------------------------- #


def test_codebuddy_rows_match_the_upstream_table() -> None:
    for model_id, (context, ceiling, rungs, declared) in CODEBUDDY_UPSTREAM.items():
        spec = buddy_spec(model_id, "buddy")
        assert spec.context_window == context, f"{model_id}: {spec.context_window}"
        assert spec.max_output == ceiling, f"{model_id}: {spec.max_output}"
        assert spec.efforts == rungs, f"{model_id}: {spec.efforts}"
        assert spec.vision is True, f"{model_id}: vision {spec.vision}"
        assert spec.effort_default == declared, f"{model_id}: {spec.effort_default}"
        # Whatever the table says, the rung a caller lands on must be in the menu.
        assert default_effort_for(spec) in rungs, model_id


def test_workbuddy_rows_match_the_upstream_table() -> None:
    for model_id, (context, rungs, declared) in WORKBUDDY_UPSTREAM.items():
        spec = buddy_spec(model_id, "workbuddy", work=True)
        assert spec.context_window == context, f"{model_id}: {spec.context_window}"
        assert spec.efforts == rungs, f"{model_id}: {spec.efforts}"
        assert spec.effort_default == declared, f"{model_id}: {spec.effort_default}"
        # Whatever the table says, the rung a caller lands on must be in the menu —
        # and a row with no menu lands on nothing at all.
        assert default_effort_for(spec) in (rungs or (None,)), model_id


def test_workbuddy_aliases_carry_their_own_labels() -> None:
    for model_id, label in WORKBUDDY_LABELS.items():
        spec = buddy_spec(model_id, "workbuddy", work=True)
        assert spec.name == label, f"{model_id}: {spec.name!r}"


def test_workbuddy_rows_without_rungs_are_inert() -> None:
    """`default-model` and `deep-model` declare no menu upstream, so none is shown."""
    for model_id in ("default-model", "deep-model"):
        spec = buddy_spec(model_id, "workbuddy", work=True)
        assert spec.efforts == () and spec.reasoning is False, model_id
        assert efforts_for(spec) is None


def test_buddy_offers_no_rung_that_switches_thinking_off() -> None:
    """Neither Buddy lane declares an off rung; asking for one lands on the default."""
    for table, work in ((CODEBUDDY_UPSTREAM, False), (WORKBUDDY_UPSTREAM, True)):
        for model_id in table:
            spec = buddy_spec(model_id, "buddy", work=work)
            assert spec.effort_off_id == NO_OFF_RUNG, f"{model_id}: {spec.effort_off_id}"
            assert spec.can_disable_thinking is False, model_id
            assert "none" not in spec.efforts and "off" not in spec.efforts, model_id


def test_buddy_menu_reaches_the_wire_as_a_top_level_reasoning_effort() -> None:
    spec = buddy_spec("hy3", "buddy")
    assert effort_patch_for("high", spec) == {"reasoning_effort": "high"}
    assert _patched(spec, "low")["reasoning_effort"] == "low"
    # No sibling thinking switch on a non-DeepSeek row.
    assert "thinking" not in _patched(spec, "high")


def test_buddy_deepseek_rows_add_the_sibling_thinking_switch() -> None:
    """The DeepSeek rows need `thinking.type` alongside the level; the rest do not."""
    for model_id in ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4.1-flash"):
        body = _patched(buddy_spec(model_id, "buddy"), "high")
        assert body["thinking"] == {"type": "enabled"}, f"{model_id}: {body.get('thinking')}"
        assert body["reasoning_effort"] == "high"
    assert "thinking" not in _patched(buddy_spec("glm-5.3", "buddy"), "high")


def test_buddy_single_rung_menu_defaults_to_that_rung() -> None:
    """A row with one rung has no `defaultReasoningEffort` upstream; the rung is it."""
    spec = buddy_spec("glm-5.1", "buddy")
    assert spec.efforts == ("medium",)
    assert spec.effort_default is None
    assert default_effort_for(spec) == "medium"
    assert [row["id"] for row in (efforts_for(spec) or []) if row["is_default"]] == ["medium"]


# -- the Trae lane ----------------------------------------------------------- #


def test_trae_rows_carry_the_context_window_and_nothing_else() -> None:
    from hoshimi.core.providers.trae import TRAE_MODELS

    for model_id in TRAE_MODELS:
        spec = trae_spec(model_id, "trae")
        assert spec.context_window == TRAE_CONTEXT_WINDOW, f"{model_id}: {spec.context_window}"
        assert spec.reasoning is False and spec.efforts == (), model_id
        assert spec.vision is False, model_id


def test_trae_forwards_the_callers_own_level_untouched() -> None:
    """No rungs are known, so the caller's `reasoning_effort` must survive as sent.

    A ladder here would strip the field and rewrite the ceiling for a station whose
    adapter reads the level directly — and inventing a menu would advertise rungs
    this gateway never fetched.
    """
    spec = trae_spec("glm-5.2", "trae")
    raw = {"messages": [], "reasoning_effort": "high", "max_tokens": 7}
    assert apply_effort(spec, dict(raw)) == raw
    assert efforts_for(spec) is None


# -- every advertised id has a row ------------------------------------------- #


def test_every_advertised_id_resolves_to_a_row() -> None:
    """A station's whole fallback list must be covered, so no id silently goes inert."""
    from hoshimi.core.providers.buddy import CODEBUDDY_MODELS, WORKBUDDY_MODELS
    from hoshimi.core.providers.cline import ClineProvider
    from hoshimi.core.providers.loomy import LoomyProvider

    for model_id in CODEBUDDY_MODELS:
        assert buddy_spec(model_id, "buddy").context_window != FALLBACK_CONTEXT_WINDOW, model_id
    for model_id in WORKBUDDY_MODELS:
        spec = buddy_spec(model_id, "workbuddy", work=True)
        assert spec.context_window != FALLBACK_CONTEXT_WINDOW, model_id
        assert spec.name, model_id
    for model_id in ClineProvider.fallback_model_list:
        assert cline_spec(model_id, "cline").context_window != FALLBACK_CONTEXT_WINDOW, model_id
    for model_id in LoomyProvider.fallback_model_list:
        assert loomy_spec(model_id, "loomy").context_window != FALLBACK_CONTEXT_WINDOW, model_id


# -- the Qoder lanes --------------------------------------------------------- #


def test_qoder_intl_rows_match_the_upstream_table() -> None:
    for model_id, (name, context, image, rungs, default, disable) in QODER_INTL_UPSTREAM.items():
        spec = qoder_spec(model_id, "qoder")
        assert spec.name == name, f"{model_id}: {spec.name!r}"
        assert spec.context_window == context, f"{model_id}: {spec.context_window}"
        assert spec.vision is image, f"{model_id}: vision {spec.vision}"
        assert spec.efforts == rungs, f"{model_id}: {spec.efforts}"
        assert spec.effort_default == default, f"{model_id}: {spec.effort_default}"
        assert spec.reasoning is bool(rungs), model_id
        assert spec.can_disable_thinking is disable, model_id
        assert (spec.effort_off_id == "none") is disable, model_id


def test_qodercn_rows_match_the_upstream_table() -> None:
    for model_id, (name, context, image, rungs, default, disable) in QODER_CN_UPSTREAM.items():
        spec = qoder_spec(model_id, "qodercn", cn=True)
        assert spec.name == name, f"{model_id}: {spec.name!r}"
        assert spec.context_window == context, f"{model_id}: {spec.context_window}"
        assert spec.vision is image, f"{model_id}: vision {spec.vision}"
        assert spec.efforts == rungs, f"{model_id}: {spec.efforts}"
        assert spec.effort_default == default, f"{model_id}: {spec.effort_default}"
        assert (spec.effort_off_id == "none") is disable, model_id


def test_the_two_qoder_catalogues_diverge_id_for_id() -> None:
    """`mmodel` is a different MiniMax generation on each site, not one shared row."""
    intl = qoder_spec("mmodel", "qoder")
    cn = qoder_spec("mmodel", "qodercn", cn=True)
    assert (intl.name, intl.context_window) == ("MiniMax-M3", 1_000_000)
    assert (cn.name, cn.context_window) == ("MiniMax-M2.7", 200_000)
    # The five ids China refuses must not be handed a Chinese row either.
    for model_id in ("ultimate", "performance", "efficient", "smodel", "cmodel"):
        assert qoder_spec(model_id, "qodercn", cn=True).efforts == (), model_id


def test_the_station_not_the_row_decides_which_qoder_catalogue_applies() -> None:
    """`qodercn/mmodel` is China's row; the bare id falls to the first lane.

    Both sites publish `mmodel`, and they do not mean the same thing — so the
    table cannot be picked from the id, only from the provider that answered.
    """
    from hoshimi.core.providers.qoder import QoderCnProvider, QoderProvider
    from hoshimi.core.registry import Registry

    client = httpx.Client()
    registry = Registry({"qoder": QoderProvider(client, []), "qodercn": QoderCnProvider(client, [])})
    bare = registry.spec("mmodel")
    cn = registry.spec("qodercn/mmodel")
    assert bare is not None and cn is not None
    assert (bare.provider, bare.name, bare.context_window) == ("qoder", "MiniMax-M3", 1_000_000)
    assert (cn.provider, cn.name, cn.context_window) == ("qodercn", "MiniMax-M2.7", 200_000)


def test_qoder_appends_the_off_rung_last_and_only_where_a_branch_exists() -> None:
    """`gU()` pushes `none` onto the catalogue's rungs; a row with none has no menu."""
    ultimate = qoder_spec("ultimate", "qoder")
    assert ultimate.efforts[-1] == "none", ultimate.efforts
    # `qmodel` / `qmodel_latest` publish no levels at all — the only option is "off".
    assert qoder_spec("qmodel", "qoder").efforts == ("none",)
    assert default_effort_for(qoder_spec("qmodel", "qoder")) == "none"
    # Five rungs and no `disabled` branch: nothing may switch thinking off.
    sonus = qoder_spec("smodel", "qoder")
    assert "none" not in sonus.efforts and sonus.can_disable_thinking is False
    assert sonus.effort_off_id is None


def test_qoder_menu_reaches_the_wire_as_a_top_level_reasoning_effort() -> None:
    spec = qoder_spec("ultimate", "qoder")
    assert effort_patch_for("xhigh", spec) == {"reasoning_effort": "xhigh"}
    assert _patched(spec, "xhigh")["reasoning_effort"] == "xhigh"
    # `none` is a plain rung here, exactly as Cline and Loomy send it: the nested
    # `parameters` envelope belongs to the encrypted WASM path this gateway skips.
    assert spec.effort_off_patch is None
    off = _patched(spec, "none")
    assert off["reasoning_effort"] == "none"
    assert "parameters" not in off and "enable_thinking" not in off


def test_qoder_row_without_a_menu_stays_inert() -> None:
    for model_id in ("auto", "efficient", "mmodel"):
        spec = qoder_spec(model_id, "qoder")
        assert spec.reasoning is False and spec.efforts == (), model_id
        raw = {"messages": [], "reasoning_effort": "high", "max_tokens": 5}
        assert apply_effort(spec, dict(raw)) == raw


def test_qoder_declares_no_output_ceiling_and_invents_none() -> None:
    """Upstream publishes no output limit for Qoder, so the gateway's default stands.

    A menu model's ceiling is its capacity: choosing a level pins it, which is the
    one number here that comes from the port rather than from upstream.
    """
    for model_id in QODER_INTL_UPSTREAM:
        spec = qoder_spec(model_id, "qoder")
        assert spec.max_output == FALLBACK_MAX_OUTPUT, model_id
    assert _patched(qoder_spec("ultimate", "qoder"), "high")["max_tokens"] == FALLBACK_MAX_OUTPUT


# -- the MiniMax lane --------------------------------------------------------- #


def test_minimax_rows_match_the_upstream_table() -> None:
    for model_id, (name, context, ceiling, image, rungs, default, _mode) in MINIMAX_UPSTREAM.items():
        spec = minimax_spec(model_id, "minimax")
        assert spec.name == name, f"{model_id}: {spec.name!r}"
        assert spec.context_window == context, f"{model_id}: {spec.context_window}"
        assert spec.max_output == ceiling, f"{model_id}: {spec.max_output}"
        assert spec.vision is image, f"{model_id}: vision {spec.vision}"
        assert spec.efforts == rungs, f"{model_id}: {spec.efforts}"
        assert spec.effort_default == default, f"{model_id}: {spec.effort_default}"
        assert spec.reasoning is bool(rungs), model_id


def test_minimax_only_the_switchable_id_offers_an_off_rung() -> None:
    """A `forced_on` id answers 400 (M3.1) or silently keeps thinking (M2.7)."""
    for model_id, (_n, _c, _x, _i, _r, _d, mode) in MINIMAX_UPSTREAM.items():
        spec = minimax_spec(model_id, "minimax")
        if mode == "switchable":
            assert spec.effort_off_id == "none" and spec.can_disable_thinking is True, model_id
        else:
            assert spec.effort_off_id is None and spec.can_disable_thinking is False, model_id
            assert "none" not in spec.efforts and "on" not in spec.efforts, model_id


def test_minimax_level_rides_on_reasoning_effort_for_the_adapter_to_translate() -> None:
    spec = minimax_spec("MiniMax-M3", "minimax")
    assert effort_patch_for("on", spec) == {"reasoning_effort": "on"}
    assert effort_patch_for("none", spec) == {"reasoning_effort": "none"}
    # `apply_effort` must not strip the field the provider reads: the patch is what
    # keeps it alive long enough to become `thinking` / `output_config`.
    assert _patched(spec, "none")["reasoning_effort"] == "none"


def test_minimax_adapter_writes_thinking_and_output_config() -> None:
    from hoshimi.core.providers.minimax import MinimaxProvider

    provider = MinimaxProvider(httpx.Client(), [])
    assert provider._thinking_extra("MiniMax-M3.1-Flash-Preview", {"reasoning_effort": "high"}) == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }
    assert provider._thinking_extra("MiniMax-M3", {"reasoning_effort": "none"}) == {"thinking": {"type": "disabled"}}
    assert provider._thinking_extra("MiniMax-M3", {"reasoning_effort": "on"}) == {"thinking": {"type": "adaptive"}}
    # M2.7 publishes no levels, so nothing at all goes out — upstream's "整个不发".
    assert provider._thinking_extra("MiniMax-M2.7", {"reasoning_effort": "high"}) == {}
    assert provider._thinking_extra("MiniMax-M2.7", {}) == {}
    # M3.1 must be asked for adaptive thinking or the station answers 400 (2013).
    assert provider._thinking_extra("MiniMax-M3.1-Flash-Preview", {}) == {"thinking": {"type": "adaptive"}}


def test_minimax_a_level_outside_the_menu_lands_on_a_rung_the_station_offers() -> None:
    from hoshimi.core.providers.minimax import MinimaxProvider

    spec = minimax_spec("MiniMax-M3.1-Flash-Preview", "minimax")
    provider = MinimaxProvider(httpx.Client(), [])
    for asked in ("banana", "minimal", "off"):
        body = _patched(spec, asked)
        assert body["reasoning_effort"] in spec.efforts, f"{asked} -> {body['reasoning_effort']}"
        assert provider._thinking_extra("MiniMax-M3.1-Flash-Preview", body)["output_config"] == {"effort": body["reasoning_effort"]}


# -- the ZCode lane ----------------------------------------------------------- #


def test_zcode_rows_match_the_upstream_table() -> None:
    for model_id, (context, ceiling, image, rungs, default) in ZCODE_UPSTREAM.items():
        spec = zcode_spec(model_id, "zcode")
        assert spec.name == model_id, f"{model_id}: {spec.name!r}"
        assert spec.context_window == context, f"{model_id}: {spec.context_window}"
        assert spec.max_output == ceiling, f"{model_id}: {spec.max_output}"
        assert spec.vision is image, f"{model_id}: vision {spec.vision}"
        assert spec.efforts == rungs, f"{model_id}: {spec.efforts}"
        assert spec.effort_default == default, f"{model_id}: {spec.effort_default}"
        assert spec.effort_off_id is None and spec.can_disable_thinking is False, model_id


def test_zcode_level_rides_on_reasoning_effort_until_the_adapter_lifts_it() -> None:
    """The Anthropic body builder drops what it does not know, so `output_config`
    cannot be written here — `stream_raw` adds it after the payload exists."""
    from hoshimi.core.providers.zcode import VALID_EFFORTS

    spec = zcode_spec("GLM-5.3-Flash", "zcode")
    assert effort_patch_for("low", spec) == {"reasoning_effort": "low"}
    body = _patched(spec, "low")
    assert body["reasoning_effort"] == "low"
    assert "output_config" not in body
    # The provider's wire-side guard and the table are one rung list, not two.
    assert VALID_EFFORTS == spec.efforts


# -- the CodeArts lane -------------------------------------------------------- #


def test_codearts_declares_one_switch_for_every_id() -> None:
    from hoshimi.core.providers.codearts import CodeArtsProvider

    for model_id in CodeArtsProvider.fallback_model_list:
        spec = codearts_spec(model_id, "codearts")
        assert spec.efforts == CODEARTS_RUNGS, f"{model_id}: {spec.efforts}"
        assert spec.effort_default == "on" and spec.effort_off_id == "off", model_id
        assert spec.can_disable_thinking is True, model_id
    # An id the remote catalogue hands out later still gets the lane's switch.
    assert codearts_spec("some-new-model", "codearts").efforts == CODEARTS_RUNGS


def test_codearts_context_window_is_bound_to_the_measured_rows() -> None:
    """Upstream leaves the rest `undefined`; the gateway's default is not a measurement."""
    from hoshimi.core.providers.codearts import CodeArtsProvider

    for model_id in CodeArtsProvider.fallback_model_list:
        expected = CODEARTS_CONTEXT_WINDOWS.get(model_id, FALLBACK_CONTEXT_WINDOW)
        assert codearts_spec(model_id, "codearts").context_window == expected, model_id


def test_codearts_on_writes_nothing_and_off_writes_the_switch() -> None:
    """`reasoning_effort` is measured as accepted-but-ineffective here; `thinking.type` is the control."""
    spec = codearts_spec("glm-5.3-flash", "codearts")
    on = _patched(spec, "on")
    assert "thinking" not in on and "reasoning_effort" not in on, on
    off = _patched(spec, "off")
    assert off["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in off


def test_codearts_body_builder_carries_the_switch_across() -> None:
    from hoshimi.core.providers.codearts import CodeArtsProvider

    provider = CodeArtsProvider(httpx.Client(), [])
    spec = codearts_spec("glm-5.3-flash", "codearts")
    off = provider.build_body(_patched(spec, "off"), "glm-5.3-flash")
    assert off["thinking"] == {"type": "disabled"}
    assert "thinking" not in provider.build_body(_patched(spec, "on"), "glm-5.3-flash")
    # The declared ceiling is the one this builder already sent when none was named.
    assert off["max_tokens"] == 65_536
    assert spec.max_output == 65_536


# -- the Raccoon lane --------------------------------------------------------- #


def test_raccoon_rows_match_the_upstream_table() -> None:
    for model_id, (name, context, ceiling) in RACCOON_UPSTREAM.items():
        spec = raccoon_spec(model_id, "raccoon")
        assert spec.name == name, f"{model_id}: {spec.name!r}"
        assert spec.context_window == context, f"{model_id}: {spec.context_window}"
        assert spec.max_output == ceiling, f"{model_id}: {spec.max_output}"
        assert spec.vision is True, f"{model_id}: vision {spec.vision}"


def test_raccoon_labels_drop_the_billing_multiplier() -> None:
    """` · x0.75` and ` · 免费` are price tags, exactly as Loomy's ` · x3.0` was."""
    for model_id in RACCOON_UPSTREAM:
        assert "·" not in raccoon_spec(model_id, "raccoon").name, model_id


def test_raccoon_switch_lands_inside_extra_body() -> None:
    """`extra_body.thinking` is the only form this gateway answers; top level is ignored."""
    spec = raccoon_spec("sn-kimi-k3", "raccoon")
    assert spec.efforts == ("on", "off") and spec.effort_default == "on"
    assert spec.effort_off_id == "off"
    on = _patched(spec, "on")
    off = _patched(spec, "off")
    assert on["extra_body"] == {"thinking": {"type": "enabled"}}
    assert off["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in on and "reasoning_effort" not in off
    # A menu model's ceiling is the catalogue's `params.max_tokens`, not the default.
    assert off["max_tokens"] == 100_000
    assert _patched(raccoon_spec("sn-sensenova-6-8-flash", "raccoon"), "on")["max_tokens"] == 63_999


# -- the Gemini lane ------------------------------------------------------------ #

# `gemini.ts` `GEMINI_FALLBACK_MODELS`: one row, four rungs, and no rung that
# switches thinking off.
GEMINI_UPSTREAM: dict[str, dict[str, Any]] = {
    "gemini-3.8-flash": {"name": "Gemini 3.8 Flash", "contextWindow": 1_000_000, "maxTokens": 64_000, "supportsImage": True},
}
# `gemini.ts` `GEMINI_EFFORT_IDS` / `GEMINI_DEFAULT_EFFORT`.
GEMINI_RUNGS = ("low", "medium", "high", "tiered")
GEMINI_DEFAULT = "medium"


def test_gemini_publishes_one_id_and_not_the_four_suffixed_ones() -> None:
    """Upstream hides the `-tier` names on purpose: the picker is what carries the tier."""
    from hoshimi.core.providers.gemini import GeminiProvider
    from hoshimi.core.translate.gemini import canonical_model_id

    assert GeminiProvider(httpx.Client(), []).models() == ["gemini-3.8-flash"]
    # A client that cached a suffixed name still routes: `stream_raw` strips the
    # suffix and re-derives the tier from the level instead of the id. The row
    # itself is the bare one — a suffix is not a model.
    for tier in GEMINI_RUNGS:
        assert canonical_model_id(f"gemini-3.8-flash-{tier}") == "gemini-3.8-flash", tier


def test_gemini_rows_match_the_upstream_table() -> None:
    for model_id, expected in GEMINI_UPSTREAM.items():
        spec = gemini_spec(model_id, "gemini")
        assert spec.name == expected["name"], f"{model_id}: {spec.name!r}"
        assert spec.context_window == expected["contextWindow"], f"{model_id}: {spec.context_window}"
        assert spec.max_output == expected["maxTokens"], f"{model_id}: {spec.max_output}"
        assert spec.vision is expected["supportsImage"], f"{model_id}: vision {spec.vision}"
        assert spec.efforts == GEMINI_RUNGS and spec.effort_default == GEMINI_DEFAULT, model_id
    # An id the station names later gets no menu invented for it here.
    assert gemini_spec("gemini-something-new", "gemini").efforts == ()


def test_gemini_level_reaches_the_wire_as_the_tier_suffix() -> None:
    """The level rides the standard field; only the adapter knows about suffixes."""
    from hoshimi.core.translate.gemini import build_gemini_envelope

    spec = gemini_spec("gemini-3.8-flash", "gemini")
    body = _patched(spec, "high")
    assert body["reasoning_effort"] == "high"
    assert "model" not in body, body
    envelope = build_gemini_envelope(_turn(spec, "high"), model="gemini-3.8-flash", effort="high")
    assert envelope["model"] == "gemini-3.8-flash-high"
    assert envelope["request"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 10_000


def test_gemini_offers_no_rung_that_switches_thinking_off() -> None:
    """Upstream measured `includeThoughts: false` as still thinking and still billing.

    A fake off is no off, so the station publishes no such rung — and neither does
    this table. `tiered` withholds the budget instead, which is how the model picks.
    """
    from hoshimi.core.translate.gemini import GEMINI_DEFAULT_TIER, GEMINI_TIER_IDS, build_gemini_envelope

    spec = gemini_spec("gemini-3.8-flash", "gemini")
    assert spec.effort_off_id is None and spec.effort_off_patch is None
    assert spec.can_disable_thinking is False
    # The table and the wire are one rung list, not two.
    assert GEMINI_TIER_IDS == spec.efforts and GEMINI_DEFAULT_TIER == spec.effort_default
    envelope = build_gemini_envelope(_turn(spec, "tiered"), model="gemini-3.8-flash", effort="tiered")
    config = envelope["request"]["generationConfig"]
    assert "thinkingBudget" not in config["thinkingConfig"]
    assert envelope["model"] == "gemini-3.8-flash-tiered"


# -- every advertised id of the second batch has a row ------------------------ #


def test_every_second_batch_id_resolves_to_a_row() -> None:
    from hoshimi.core.providers.codearts import CodeArtsProvider
    from hoshimi.core.providers.qoder import QODER_CN_MODELS, QODER_INTL_MODELS
    from hoshimi.core.providers.raccoon import RaccoonProvider
    from hoshimi.core.providers.zcode import ZCODE_MODELS

    for model_id in QODER_INTL_MODELS:
        spec = qoder_spec(model_id, "qoder")
        assert spec.context_window != FALLBACK_CONTEXT_WINDOW and spec.name, model_id
    for model_id in QODER_CN_MODELS:
        spec = qoder_spec(model_id, "qodercn", cn=True)
        assert spec.context_window != FALLBACK_CONTEXT_WINDOW and spec.name, model_id
    for model_id in ZCODE_MODELS:
        assert zcode_spec(model_id, "zcode").efforts == ZCODE_UPSTREAM[model_id][3], model_id
    for model_id in CodeArtsProvider.fallback_model_list:
        spec = codearts_spec(model_id, "codearts")
        assert spec.name and spec.efforts == CODEARTS_RUNGS, model_id
    for model_id in RaccoonProvider.fallback_model_list:
        spec = raccoon_spec(model_id, "raccoon")
        assert spec.context_window != FALLBACK_CONTEXT_WINDOW and spec.name, model_id
    for model_id in MINIMAX_UPSTREAM:
        spec = minimax_spec(model_id, "minimax")
        assert spec.context_window != FALLBACK_CONTEXT_WINDOW and spec.name, model_id
    for model_id in GEMINI_UPSTREAM:
        spec = gemini_spec(model_id, "gemini")
        assert spec.name == GEMINI_UPSTREAM[model_id]["name"] and spec.efforts == GEMINI_RUNGS


# -- the aggregator lane: CommandCode's own catalogue ------------------------ #

# Transcribed from pi-free's `providers/commandcode/commandcode-pricing.ts` (a
# port of patlux/pi-commandcode-provider, MIT), last checked against the
# station's pricing page 2026-08-25. Locked in full rather than spot-checked
# for the reason the native tables are: an id quietly dropped from a list
# loses its capability and nothing else notices — the model stays listed,
# stays callable, and is simply believed to be text-only again.
# `MODEL_INPUT_MODALITIES` — all 41 entries, every one of them `["text", "image"]`.
COMMANDCODE_VISION_IDS = frozenset(
    {
        "MiniMaxAI/MiniMax-M3",
        "Qwen/Qwen3.6-Plus",
        "Qwen/Qwen3.7-Flash",
        "Qwen/Qwen3.7-Plus",
        "Qwen/Qwen3.8-27B",
        "Qwen/Qwen3.8-Max",
        "claude-fable-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "deepseek/deepseek-v4-flash-vision-exp",
        "google/gemini-3.1-flash-lite",
        "google/gemini-3.5-flash",
        "google/gemini-3.5-flash-lite",
        "google/gemini-3.6-flash",
        "google/gemini-3.7-flash",
        "gpt-5.3-codex",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.5",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "meta/muse-spark-1.1",
        "meta/muse-spark-1.2",
        "meta/muse-spark-1.2-contributor",
        "moonshotai/Kimi-K2.5",
        "moonshotai/Kimi-K2.6",
        "moonshotai/Kimi-K2.7-Code",
        "moonshotai/Kimi-K2.7-Code-Highspeed",
        "moonshotai/Kimi-K3",
        "sakana/fugu-ultra",
        "stealth/ox-alpha",
        "stepfun/Step-3.7-Flash",
        "thinkingmachines/inkling",
        "thinkingmachines/inkling-small",
        "xai/grok-4.5",
        "xiaomi/mimo-v2.5",
    }
)

# `MODEL_REASONING` — every entry is `true`, so membership is the whole value.
COMMANDCODE_REASONING_IDS = frozenset(
    {
        "MiniMaxAI/MiniMax-M3",
        "Qwen/Qwen3.6-Max-Preview",
        "Qwen/Qwen3.6-Plus",
        "Qwen/Qwen3.7-Flash",
        "Qwen/Qwen3.7-Max",
        "Qwen/Qwen3.7-Plus",
        "Qwen/Qwen3.8-27B",
        "Qwen/Qwen3.8-Max",
        "claude-fable-5",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-flash-vision-exp",
        "deepseek/deepseek-v4-pro",
        "google/gemini-3.1-flash-lite",
        "google/gemini-3.5-flash",
        "google/gemini-3.5-flash-lite",
        "google/gemini-3.6-flash",
        "google/gemini-3.7-flash",
        "gpt-5.3-codex",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.5",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "meta/muse-spark-1.1",
        "meta/muse-spark-1.2",
        "meta/muse-spark-1.2-contributor",
        "moonshotai/Kimi-K2.7-Code",
        "moonshotai/Kimi-K2.7-Code-Highspeed",
        "moonshotai/Kimi-K3",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "poolside/laguna-s-2.1-free",
        "sakana/fugu-ultra",
        "stealth/ox-alpha",
        "stepfun/Step-3.5-Flash",
        "stepfun/Step-3.7-Flash",
        "tencent/hy3-paid",
        "thinkingmachines/inkling",
        "thinkingmachines/inkling-small",
        "xai/grok-4.5",
        "xai/grok-4.6",
        "zai-org/GLM-5.2",
        "zai-org/GLM-5.3",
    }
)


def test_commandcode_rows_match_the_upstream_table() -> None:
    assert COMMANDCODE_VISION == COMMANDCODE_VISION_IDS
    assert COMMANDCODE_REASONING == COMMANDCODE_REASONING_IDS


def test_the_two_lists_disagree_about_exactly_those_ids() -> None:
    """Vision without reasoning, and reasoning without vision, are both real.

    Four ids take images and do not reason, and eleven reason and do not take
    images — so no single "capability" set could have carried both tables.
    These are two facts about a model, not one fact wearing a second name.
    """
    assert COMMANDCODE_VISION - COMMANDCODE_REASONING == frozenset(("claude-haiku-4-5-20251001", "moonshotai/Kimi-K2.5", "moonshotai/Kimi-K2.6", "xiaomi/mimo-v2.5"))
    assert COMMANDCODE_REASONING - COMMANDCODE_VISION == frozenset(
        (
            "Qwen/Qwen3.6-Max-Preview",
            "Qwen/Qwen3.7-Max",
            "deepseek/deepseek-v4-flash",
            "deepseek/deepseek-v4-pro",
            "nvidia/nemotron-3-ultra-550b-a55b",
            "poolside/laguna-s-2.1-free",
            "stepfun/Step-3.5-Flash",
            "tencent/hy3-paid",
            "xai/grok-4.6",
            "zai-org/GLM-5.2",
            "zai-org/GLM-5.3",
        )
    )


def test_an_id_the_station_does_not_name_gets_nothing_at_all() -> None:
    """Not even a False — a table that covered "the rest" would be a guess.

    The empty dict is what makes a named id and an unnamed one distinguishable
    downstream, which is why no field is ever filled with a default here.
    """
    assert agg_capability("commandcode", "someone/brand-new-9b") == {}
    assert agg_capability(None, "claude-opus-5") == {}


def test_the_station_is_the_key_and_not_the_provider() -> None:
    """`agg` is one provider id carrying twenty-two stations.

    The same string can be two different models at two of them, so a
    capability belongs to whoever is selling the id and not to whoever happens
    to share a provider name.
    """
    assert agg_capability("commandcode", "claude-opus-5") == {"vision": True, "reasoning": True}
    assert agg_capability("bai", "claude-opus-5") == {}


def test_the_station_table_beats_the_default_and_the_operator_beats_both() -> None:
    """Three sources with three standings, in one row."""
    caps = agg_capability("commandcode", "claude-opus-5")
    spec = agg_spec("claude-opus-5", "agg", station_caps=caps)
    assert (spec.vision, spec.reasoning) == (True, True)
    # An account knows its own contract better than a pricing page does.
    overridden = agg_spec("claude-opus-5", "agg", {"vision": False}, station_caps=caps)
    assert (overridden.vision, overridden.reasoning) == (False, True)
    # And with neither, the row stays inert rather than guessing.
    inert = agg_spec("claude-opus-5", "agg")
    assert (inert.vision, inert.reasoning) == (False, False)


def test_the_free_table_and_the_capability_table_agree_on_the_two_free_ids() -> None:
    """Two transcriptions taken from one upstream file, checked against each other.

    The cost table prices exactly two CommandCode models at $0, and both are
    in the reasoning list — which is what a free model handed to a coding
    agent has to be. Nothing else here would notice one table being updated
    without the other.
    """
    free = STATION_FREE_IDS["commandcode"]
    assert len(free) == 2
    assert free <= COMMANDCODE_REASONING
