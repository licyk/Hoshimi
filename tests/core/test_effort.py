"""The effort policy and the capability catalog it reads."""

from __future__ import annotations

from typing import Any

from hoshimi.core.catalog import ModelSpec, kilo_spec, opencode_spec
from hoshimi.core.effort import (
    DEFAULT_LEVEL,
    LEVELS,
    MIN_BUDGET,
    apply_effort,
    budget_for,
    default_effort_for,
    effort_patch_for,
    efforts_for,
    requested_level,
    resolve_level,
)


def ladder(**kwargs: Any) -> ModelSpec:
    return ModelSpec(id="m", provider="opencode", name="M", reasoning=True, **kwargs)


def menu(**kwargs: Any) -> ModelSpec:
    base: dict[str, Any] = {
        "id": "k",
        "provider": "kilo",
        "name": "K",
        "reasoning": True,
        "efforts": ("disabled", "low", "medium", "high"),
        "effort_default": "high",
        "effort_patch": {"reasoning": {"effort": "$effort"}},
        "effort_off_patch": {"reasoning": {"enabled": False}},
    }
    base.update(kwargs)
    return ModelSpec(**base)


# -- levels ---------------------------------------------------------------- #
def test_ladder_levels_read_as_budgets() -> None:
    spec = ladder(max_output=65536)
    assert budget_for("light", spec) == 2048
    assert budget_for("balanced", spec) == 8192
    # Deep is the model's own capacity: a level can only lower it.
    assert budget_for("deep", spec) == 65536


def test_thinking_always_on_widens_every_rung() -> None:
    spec = ladder(max_output=131072, can_disable_thinking=False)
    assert budget_for("light", spec) == 4096
    assert budget_for("balanced", spec) == 16384


def test_the_caller_can_only_lower_the_ceiling() -> None:
    spec = ladder(max_output=131072)
    assert budget_for("light", spec, requested=1000) == 1000
    assert budget_for("deep", spec, requested=4096) == 4096
    # …and never below the point where the answer itself cannot land.
    assert budget_for("light", spec, requested=100) == MIN_BUDGET
    assert budget_for("light", spec, requested=0) == 2048


def test_harness_vocabularies_resolve_to_this_lane_s_rungs() -> None:
    spec = ladder()
    assert resolve_level("low", spec) == "light"
    assert resolve_level("medium", spec) == DEFAULT_LEVEL
    assert resolve_level("high", spec) == "deep"
    assert resolve_level("xhigh", spec) == "deep"
    # An unnameable level falls back to the default rung rather than failing.
    assert resolve_level("silly", spec) == DEFAULT_LEVEL
    assert resolve_level(None, spec) == DEFAULT_LEVEL
    # A model that never thinks has no rung at all.
    assert resolve_level("high", ModelSpec(id="x", provider="p", name="X")) is None


def test_a_menu_speaks_its_model_s_own_levels() -> None:
    spec = menu()
    assert resolve_level("high", spec) == "high"
    assert resolve_level("light", spec) == "low"
    assert resolve_level("deep", spec) == "high"
    # `xhigh` would be `max`, but this menu tops out at `high`.
    assert resolve_level("xhigh", spec) == "high"
    # Outside the menu: the menu's own default, which is what the wire carries.
    assert resolve_level("silly", spec) == "high"
    assert default_effort_for(spec) == "high"


def test_requested_level_reads_both_spellings() -> None:
    assert requested_level({"reasoning_effort": " high "}) == "high"
    assert requested_level({"reasoning": {"effort": "low"}}) == "low"
    assert requested_level({"messages": []}) is None
    assert requested_level({"reasoning_effort": 3}) is None


# -- patches --------------------------------------------------------------- #
def test_menu_levels_become_body_patches() -> None:
    spec = menu()
    assert effort_patch_for("medium", spec) == {"reasoning": {"effort": "medium"}}
    # The off rung uses the off patch, which names no level at all.
    assert effort_patch_for("disabled", spec) == {"reasoning": {"enabled": False}}
    # No placeholder to substitute: the patch is emitted as a copy, not shared.
    patch = effort_patch_for("disabled", spec)
    assert patch is not spec.effort_off_patch
    # A model that declares no menu has nothing to patch.
    assert effort_patch_for("low", ladder()) is None


# -- the body -------------------------------------------------------------- #
def test_a_body_that_named_no_level_passes_through() -> None:
    spec = ladder(max_output=131072)
    untouched = {"model": "m", "messages": [], "max_tokens": 40000}
    assert apply_effort(spec, untouched) is untouched
    assert "max_tokens" not in apply_effort(spec, {"model": "m"})

    # A level that names no rung still lands on the default one.
    out = apply_effort(spec, {"model": "m", "max_tokens": 40000, "reasoning_effort": "high"})
    assert out["max_tokens"] == 40000  # Deep, bounded by the caller's own ceiling.


def test_a_level_becomes_an_enforced_budget() -> None:
    spec = ladder(max_output=131072, can_disable_thinking=False)
    out = apply_effort(spec, {"model": "m", "messages": [], "reasoning_effort": "low"})
    # The gateway ignores these fields (and 503s on them sometimes).
    assert "reasoning_effort" not in out
    assert "reasoning" not in out
    assert out["max_tokens"] == 4096
    # The caller's own ceiling competes with the rung; it can only win downward.
    out = apply_effort(spec, {"model": "m", "messages": [], "max_tokens": 1000, "reasoning_effort": "low"})
    assert out["max_tokens"] == 1000
    # …and max_completion_tokens is folded into the one field this lane reads.
    out = apply_effort(spec, {"model": "m", "messages": [], "max_completion_tokens": 2000, "reasoning_effort": "low"})
    assert out["max_tokens"] == 2000
    assert "max_completion_tokens" not in out


def test_a_menu_level_reaches_the_wire_as_its_own_effort_field() -> None:
    spec = menu(max_output=65536)
    out = apply_effort(spec, {"model": "k", "messages": [], "reasoning_effort": "medium"})
    assert out["reasoning"] == {"effort": "medium"}
    assert "reasoning_effort" not in out
    # A menu's ceiling is the model capacity — the level is the real control.
    assert out["max_tokens"] == 65536

    out = apply_effort(spec, {"model": "k", "messages": [], "reasoning_effort": "disabled"})
    assert out["reasoning"] == {"enabled": False}


def test_models_without_reasoning_are_never_touched() -> None:
    spec = ModelSpec(id="x", provider="p", name="X", reasoning=False)
    body = {"model": "x", "messages": [], "reasoning_effort": "high", "max_tokens": 5000}
    assert apply_effort(spec, body) is body
    assert apply_effort(None, body) is body


# -- the picker ------------------------------------------------------------- #
def test_the_ladder_advertises_the_number_the_wire_will_send() -> None:
    rows = efforts_for(ladder(max_output=131072, can_disable_thinking=False)) or []
    assert [row["id"] for row in rows] == [level.id for level in LEVELS]
    tokens = {row["id"]: row["tokens"] for row in rows}
    assert tokens == {"light": 4096, "balanced": 16384, "deep": 131072}
    assert rows[0]["description"].startswith("4 K output ceiling")
    assert "(thinking cannot be switched off on this model)" in rows[0]["description"]
    assert [row["is_default"] for row in rows] == [False, True, False]
    assert efforts_for(ModelSpec(id="x", provider="p", name="X")) is None


def test_a_menu_advertises_its_own_levels() -> None:
    rows = efforts_for(menu()) or []
    assert [row["id"] for row in rows] == ["disabled", "low", "medium", "high"]
    assert [row["name"] for row in rows] == ["Off", "Low", "Medium", "High"]
    assert [row["is_default"] for row in rows] == [False, False, False, True]
    assert rows[0]["tokens"] is None


# -- capability tables ------------------------------------------------------ #
def test_opencode_specs_carry_the_local_capability_table() -> None:
    spec = opencode_spec("mimo-v2.6-flash-free", "opencode", wire="chat")
    assert (spec.name, spec.vision, spec.reasoning) == ("MiMo V2.6 Flash", True, True)
    assert (spec.context_window, spec.max_output) == (1048576, 131072)
    # This one cannot think less, so its ladder rungs are widened.
    assert spec.can_disable_thinking is False
    assert spec.efforts == ()

    muse = opencode_spec("muse-spark-1.2-contributor-free", "opencode", wire="responses")
    assert muse.region_sensitive is True
    assert muse.wire == "responses"

    jev = opencode_spec("jev-1.13-free", "opencode")
    assert jev.reasoning is False
    assert jev.context_window == 32768

    # Unknown ids keep the lane's baseline rather than guessing.
    unknown = opencode_spec("some-new-free", "opencode")
    assert (unknown.reasoning, unknown.max_output) == (True, 32768)


def test_opencode_v2_effort_menus_reach_the_wire_without_replacing_the_token_ceiling() -> None:
    deepseek = opencode_spec("deepseek-v4.1-flash-free", "opencode")
    assert deepseek.vision and deepseek.context_window == 1048576
    assert deepseek.efforts == ("disabled", "low", "high", "max")
    body = {"max_tokens": 128, "reasoning_effort": "disabled"}
    out = apply_effort(deepseek, body)
    assert out["reasoning_effort"] == "none" and out["max_tokens"] == 128
    glm = opencode_spec("glm-5.3-flash-free", "opencode")
    assert glm.vision and glm.efforts == ("low", "high", "max")
    assert not glm.can_disable_thinking and glm.effort_off_id is None
    assert all(row["id"] != "disabled" for row in efforts_for(glm) or [])
    vl = opencode_spec("nemotron-3.5-super-vl-free", "opencode")
    assert vl.vision and "xhigh" in vl.efforts
    assert opencode_spec("longcat-2.5-preview-free", "opencode").vision is False


def test_kilo_specs_read_the_listing_rows() -> None:
    row = {
        "id": "nvidia/nemotron-3.5-lightning:free",
        "name": "NVIDIA: Nemotron 3.5 Lightning (free)",
        "context_length": 128000,
        "top_provider": {"max_completion_tokens": 32768},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["reasoning", "tools"],
    }
    spec = kilo_spec(str(row["id"]), row, "kilo")
    assert spec.name == "Kilo Nemotron 3.5 Lightning"
    assert (spec.context_window, spec.max_output) == (128000, 32768)
    assert spec.vision is True
    assert spec.efforts == ("disabled", "low", "medium", "high")
    assert spec.effort_default == "high"
    assert spec.effort_patch == {"reasoning": {"effort": "$effort"}}

    # Families that answer 400 on the off rung omit it instead of promising it.
    stepfun = kilo_spec("stepfun/step-5-preview-free", {"id": "stepfun/step-5-preview-free", "supported_parameters": ["reasoning"]}, "kilo")
    assert stepfun.efforts == ("low", "medium", "high")
    assert stepfun.effort_off_patch is None
    assert stepfun.can_disable_thinking is False

    # A row that does not declare reasoning falls back to the token ladder.
    plain = kilo_spec("poolside/laguna-s-2.1:free", {"id": "poolside/laguna-s-2.1:free"}, "kilo")
    assert plain.reasoning is True
    assert plain.efforts == ()

    # A row lost to a network failure still yields a usable (ladder) spec.
    orphan = kilo_spec("openrouter/free", None, "kilo")
    assert (orphan.id, orphan.name, orphan.reasoning) == ("openrouter/free", "Openrouter Free", True)
