"""Model capability specs: what one id can do before any request goes out.

Two sources, deliberately layered so no single one can break a request:

1. the upstream listing — the authoritative set of ids a station will name;
2. a vetted local capability table (context window / vision / reasoning),
   because a listing discloses an id and nothing else.

A spec is what the effort policy reads (see :mod:`hoshimi.core.effort`) and
what ``GET /api/v1/models`` reports to the web UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

FALLBACK_CONTEXT_WINDOW = 131072
FALLBACK_MAX_OUTPUT = 32768


@dataclass(frozen=True)
class ModelSpec:
    """Everything one model id is known to support, per provider."""

    id: str
    provider: str
    name: str
    wire: str = "chat"
    vision: bool = False
    reasoning: bool = False
    tools: bool | None = None
    context_window: int = FALLBACK_CONTEXT_WINDOW
    max_output: int = FALLBACK_MAX_OUTPUT
    can_disable_thinking: bool = True
    region_sensitive: bool = False
    # Whether calling this id costs the account anything. Three-way on purpose:
    # a station that publishes no price is not saying its models are free (see
    # `free_marks.py`), and guessing would be a promise about somebody else's
    # money. None is "the station did not say", which is the answer for every
    # model the gateway has no evidence about — most of them, always.
    free: bool | None = None
    # The model's own thinking menu (its effort field is the control on the
    # wire). Empty means the free lane's token ladder applies instead.
    efforts: tuple[str, ...] = ()
    effort_default: str | None = None
    # The rung that switches thinking off. Upstream names it per station —
    # `disabled`, `none` or `off` — and a station without one has no way to
    # ask for a quiet turn at all, so this stays None until a table says
    # otherwise.
    effort_off_id: str | None = None
    effort_patch: dict[str, Any] | None = None
    effort_off_patch: dict[str, Any] | None = None


# -- the OpenCode free lane ------------------------------------------------- #
#
# Local capability baseline: `context_window`/`max_output` are the published
# capacities; `vision` is what the lane accepted under a direct image probe,
# not what a model card claims.

_CAPABILITIES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (re.compile(r"^mimo.*v2\.6"), {"vision": True, "reasoning": True, "context_window": 1048576, "max_output": 131072, "can_disable_thinking": False}),
    (re.compile(r"^mimo.*v2\.5"), {"vision": True, "reasoning": True, "context_window": 1048576, "max_output": 131072, "can_disable_thinking": False}),
    (re.compile(r"^mimo"), {"vision": True, "reasoning": True, "context_window": 262144, "max_output": 131072}),
    (re.compile(r"^muse.?spark"), {"vision": True, "reasoning": True, "context_window": 1048576, "max_output": 131072}),
    (re.compile(r"^nemotron"), {"vision": False, "reasoning": True, "context_window": 128000, "max_output": 32768}),
    (re.compile(r"^ling"), {"vision": False, "reasoning": True, "context_window": 128000, "max_output": 32768}),
    (re.compile(r"^space.?bunny"), {"vision": True, "reasoning": True, "context_window": 262144, "max_output": 65536}),
    (re.compile(r"^union"), {"vision": True, "reasoning": False, "context_window": 262144, "max_output": 131072}),
    (re.compile(r"^deepseek"), {"vision": False, "reasoning": True, "context_window": 128000, "max_output": 64000}),
    (re.compile(r"^jev"), {"vision": False, "reasoning": False, "context_window": 32768, "max_output": 4096}),
)

# Exact free-lane overrides from dsh-our-free-model src/catalog.js (f0b979f).
# A model's declared effort menu takes precedence over the generic token ladder.
_OPENCODE_OVERRIDES: dict[str, dict[str, Any]] = {
    "deepseek-v4.1-flash-free": {
        "vision": True, "context_window": 1048576, "max_output": 384000,
        "efforts": ("disabled", "low", "high", "max"), "effort_default": "high",
        "effort_patch": {"reasoning_effort": "$effort"}, "effort_off_patch": {"reasoning_effort": "none"},
    },
    "glm-5.3-flash-free": {
        "vision": True, "context_window": 1048576, "max_output": 131072,
        "efforts": ("low", "high", "max"), "effort_default": "high", "can_disable_thinking": False,
        "effort_patch": {"reasoning_effort": "$effort"},
    },
    "nemotron-3.5-super-vl-free": {
        "vision": True, "context_window": 1048576, "max_output": 131072,
        "efforts": ("disabled", "minimal", "low", "medium", "high", "xhigh"), "effort_default": "high",
        "effort_patch": {"reasoning_effort": "$effort"}, "effort_off_patch": {"reasoning_effort": "none"},
    },
    "space-bunny-free": {
        "vision": True, "context_window": 1048576, "max_output": 524288,
        "efforts": ("low", "medium", "high", "xhigh", "max"), "effort_default": "high", "can_disable_thinking": False,
        "effort_patch": {"reasoning_effort": "$effort"},
    },
    "longcat-2.5-preview-free": {"vision": False, "context_window": 1048576, "max_output": 131072},
    "big-pickle": {"vision": True, "context_window": 262144, "max_output": 131072},
}

# Ids whose regional availability is known to be egress-dependent.
_REGION_SENSITIVE: tuple[re.Pattern[str], ...] = (re.compile(r"^muse.?spark"),)

# Human-facing display names, so a raw upstream id never reaches the picker.
DISPLAY_NAMES: dict[str, str] = {
    "mimo-v2.6-flash-free": "MiMo V2.6 Flash",
    "mimo-v2.5-free": "MiMo V2.5",
    "muse-spark-1.3-contributor-free": "Muse Spark 1.3",
    "muse-spark-1.2-contributor-free": "Muse Spark 1.2",
    "nemotron-3-ultra-free": "Nemotron 3 Ultra",
    "nemotron-3.5-lightning-free": "Nemotron 3.5 Lightning",
    "ling-3.0-flash-fin-free": "Ling 3.0 Flash Fin",
    "space-bunny-free": "Space Bunny",
    "union-alpha": "Union Alpha",
    "deepseek-v4-flash-free": "DeepSeek V4 Flash",
    "jev-1.13-free": "Jev 1.13",
}

_WORD_SPLIT = re.compile(r"[-_./]+")


def strip_level_suffix(model_id: str) -> str:
    """Strip a trailing ``(level)`` thinking suffix so lookups hit the base id."""
    return re.sub(r"\([^()]+\)\s*$", "", str(model_id or "")).strip()


def base_id(model_id: str) -> str:
    """The bare id a station's listing or a picker shows (no vendor prefix)."""
    return strip_level_suffix(model_id)


def capabilities_for(model_id: str) -> dict[str, Any]:
    """The baseline capacities for one model id (defaults when nothing matches)."""
    base = base_id(model_id)
    for pattern, caps in _CAPABILITIES:
        if pattern.search(base):
            return dict(caps)
    return {"vision": False, "reasoning": True, "context_window": FALLBACK_CONTEXT_WINDOW, "max_output": FALLBACK_MAX_OUTPUT}


def is_region_sensitive(model_id: str) -> bool:
    base = base_id(model_id)
    return any(pattern.search(base) for pattern in _REGION_SENSITIVE)


def display_name(model_id: str) -> str:
    """Title-case a bare upstream id into something a picker can show."""
    base = base_id(model_id)
    known = DISPLAY_NAMES.get(base)
    if known is not None:
        return known
    words = [word for word in _WORD_SPLIT.split(base) if word]
    return " ".join(word if word[:1].isdigit() else word[:1].upper() + word[1:] for word in words)


def default_spec(model_id: str, provider: str, *, name: str | None = None, reasoning: bool = False) -> ModelSpec:
    """A spec for a station whose listing names ids and nothing else.

    ``reasoning`` defaults to False: an account channel with no capability
    table keeps whatever the caller sent (the body passes through untouched).
    """
    return ModelSpec(
        id=model_id,
        provider=provider,
        name=name or display_name(model_id),
        reasoning=reasoning,
    )


def opencode_spec(model_id: str, provider: str, *, wire: str = "chat") -> ModelSpec:
    """The OpenCode lane's spec: local capability table + per-model endpoint."""
    caps = {**capabilities_for(model_id), **_OPENCODE_OVERRIDES.get(base_id(model_id), {})}
    return ModelSpec(
        id=model_id,
        provider=provider,
        name=display_name(model_id),
        wire=wire,
        vision=bool(caps.get("vision")),
        reasoning=bool(caps.get("reasoning")),
        context_window=_positive_int(caps.get("context_window")) or FALLBACK_CONTEXT_WINDOW,
        max_output=_positive_int(caps.get("max_output")) or FALLBACK_MAX_OUTPUT,
        can_disable_thinking=caps.get("can_disable_thinking") is not False,
        region_sensitive=is_region_sensitive(model_id),
        efforts=tuple(caps.get("efforts") or ()),
        effort_default=caps.get("effort_default"),
        effort_off_id="disabled" if "disabled" in caps.get("efforts", ()) else None,
        effort_patch=caps.get("effort_patch"),
        effort_off_patch=caps.get("effort_off_patch"),
    )


# -- the Kilo free pool ----------------------------------------------------- #
#
# The gateway speaks OpenRouter's unified `reasoning` object: `effort` picks a
# level, `enabled: false` switches thinking off. Two families refuse the off
# rung outright (stepfun, liquid, thinkingmachines answer 400 "Reasoning is
# mandatory"), and the auto-routers accept the parameter but keep thinking, so
# their menus omit `disabled` rather than promise a rung that fails the turn.

KILO_EFFORT_PATCH: dict[str, Any] = {"reasoning": {"effort": "$effort"}}
KILO_EFFORT_OFF_PATCH: dict[str, Any] = {"reasoning": {"enabled": False}}
_KILO_REASONING_MANDATORY = (re.compile(r"^stepfun/"), re.compile(r"^liquid/"), re.compile(r"^thinkingmachines/"))
_KILO_NO_DISABLE_IDS = frozenset({"kilo-auto/free", "openrouter/free"})


def kilo_display_name(row: dict[str, Any]) -> str:
    """The listing's own name with the vendor prefix and `(free)` stripped."""
    raw = str(row.get("name") or "").strip()
    bare = (raw or str(row.get("id") or "")).strip()
    bare = re.sub(r"^[^:]{1,40}:\s+", "", bare)
    bare = re.sub(r"\s*\(free\)\s*$", "", bare, flags=re.IGNORECASE).strip()
    # The listing's own name survives untouched (its `3.5` is a version, not a
    # separator); only an unnamed row falls back to a pretty id.
    return f"Kilo {bare or _pretty(str(row.get('id') or ''))}"


def _pretty(text: str) -> str:
    words = [word for word in _WORD_SPLIT.split(text) if word]
    if not words:
        return text
    return " ".join(word.upper() if word.isupper() else word[:1].upper() + word[1:] for word in words)


def kilo_spec(model_id: str, row: dict[str, Any] | None, provider: str) -> ModelSpec:
    """One free-pool row's spec, or the generic reasoning spec when the row is
    missing (the id list survived a network failure but the rows did not)."""
    model_id = str(model_id or "").strip()
    if not row:
        # No row, no capabilities: the free pool always thinks, so the token
        # ladder (not a menu nobody can name) is what applies.
        return ModelSpec(id=model_id, provider=provider, name=display_name(model_id), reasoning=True)

    top = row.get("top_provider")
    top = top if isinstance(top, dict) else {}
    context = _positive_int(row.get("context_length")) or _positive_int(top.get("context_length")) or FALLBACK_CONTEXT_WINDOW
    output = _positive_int(top.get("max_completion_tokens")) or FALLBACK_MAX_OUTPUT
    architecture = row.get("architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    raw_modalities = architecture.get("input_modalities")
    input_modalities = [str(item) for item in raw_modalities] if isinstance(raw_modalities, list) else []

    params = row.get("supported_parameters")
    parameters = [str(item) for item in params] if isinstance(params, list) else []
    if "reasoning" in parameters:
        can_disable = not any(pattern.search(model_id) for pattern in _KILO_REASONING_MANDATORY)
        can_disable = can_disable and model_id not in _KILO_NO_DISABLE_IDS
        efforts = ("disabled", "low", "medium", "high") if can_disable else ("low", "medium", "high")
    else:
        can_disable = True
        efforts = ()

    return ModelSpec(
        id=model_id,
        provider=provider,
        name=kilo_display_name(row),
        wire="chat",
        vision="image" in input_modalities,
        reasoning=True,
        context_window=context,
        max_output=output,
        can_disable_thinking=can_disable,
        region_sensitive=False,
        efforts=efforts,
        effort_default="high" if efforts else None,
        effort_patch=dict(KILO_EFFORT_PATCH) if efforts else None,
        effort_off_patch=dict(KILO_EFFORT_OFF_PATCH) if efforts and can_disable else None,
    )


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number > 0 else None


# -- the EAC co-paid lane --------------------------------------------------- #
#
# A second producer whose ids are namespaced upstream ids (`org/model`):
# the org prefix is routing detail, never a name the picker shows, so every
# display name is the model's own name under the channel tag. Every row
# thinks — the relay streams reasoning whether or not the caller asks — and
# the thinking menu is copied from ZCode's built-in provider config: a list
# of levels plus a JSON merge patch the level maps onto the request body.
#
# The capacities are published specs cross-checked against this lane where
# the relay allowed a probe; `vision` records what the relay actually
# accepted under a direct image-input probe, not what a model card claims.

EAC_TAG = "EAC"

_EAC_DISPLAY_NAMES: dict[str, str] = {
    "deepseek-ai/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "moonshotai/kimi-k2.6": "Kimi K2.6",
    "moonshotai/kimi-k3": "Kimi K3",
    "openai/gpt-oss-20b": "GPT-OSS 20B",
    "z-ai/glm-5.3": "GLM 5.3",
    "z-ai/glm-5.3-flash": "GLM 5.3 Flash",
    # The two opencode lanes are relayed through the EAC gateway server-side:
    # the upstream turns around and answers on the free pool, so a single
    # install-level rate limit no longer caps them. The picker shows the
    # model under the EAC tag with a Free mark to set the model apart from
    # the deeper org-prefixed listings underneath.
    "opencode/deepseek-v4.1-flash-free": "DeepSeek V4.1 Flash Free",
    "opencode/glm-5.3-flash-free": "GLM 5.3 Flash Free",
}

_EAC_CAPABILITIES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    # The relayed free-lane ids: same reasoning_options as the canonical DeepSeek /
    # GLM rows, but the level rides the wire as `reasoning_effort` because the
    # gateway is answering on the OpenCode anonymous lane rather than the
    # full EAC relay (which would take the JSON merge patch instead).
    (
        re.compile(r"^opencode/deepseek-v4"),
        {
            "vision": True,
            "reasoning": True,
            "context_window": 1048576,
            "max_output": 384000,
            "efforts": ("disabled", "low", "high", "max"),
            "effort_default": "high",
            "effort_patch": {"reasoning_effort": "$effort"},
            "effort_off_patch": {"reasoning_effort": "none"},
        },
    ),
    (
        re.compile(r"^opencode/glm-5\.3-flash"),
        {
            "vision": True,
            "reasoning": True,
            "context_window": 1048576,
            "max_output": 131072,
            "efforts": ("low", "high", "max"),
            "effort_default": "high",
            "effort_patch": {"reasoning_effort": "$effort"},
        },
    ),
    (
        re.compile(r"^deepseek-ai/deepseek-v4"),
        {
            "vision": True,
            "reasoning": True,
            "context_window": 1048576,
            "max_output": 384000,
            "efforts": ("disabled", "low", "high", "max"),
            "effort_default": "high",
            "effort_patch": {"reasoning_effort": "$effort"},
            "effort_off_patch": {"reasoning": {"enabled": False}},
        },
    ),
    (
        re.compile(r"^moonshotai/kimi-k3"),
        {
            "vision": True,
            "reasoning": True,
            "context_window": 1048576,
            "max_output": 131072,
            "efforts": ("low", "high", "max"),
            "effort_default": "high",
            "effort_patch": {"reasoning_effort": "$effort"},
        },
    ),
    (
        re.compile(r"^moonshotai/kimi"),
        {
            "vision": True,
            "reasoning": True,
            "context_window": 262144,
            "max_output": 98304,
            "efforts": ("low", "high", "max"),
            "effort_default": "high",
            "effort_patch": {"reasoning_effort": "$effort"},
        },
    ),
    (
        re.compile(r"^z-ai/glm-5\.3-flash"),
        {
            "vision": True,
            "reasoning": True,
            "context_window": 1048576,
            "max_output": 131072,
            "efforts": ("low", "high", "max"),
            "effort_default": "high",
            "effort_patch": {"thinking": {"type": "enabled"}, "output_config": {"effort": "$effort"}},
        },
    ),
    (
        re.compile(r"^z-ai/glm-5"),
        {
            "vision": False,
            "reasoning": True,
            "context_window": 1048576,
            "max_output": 131072,
            "efforts": ("low", "high", "max"),
            "effort_default": "high",
            "effort_patch": {"thinking": {"type": "enabled"}, "output_config": {"effort": "$effort"}},
        },
    ),
    (
        re.compile(r"^openai/gpt-oss"),
        {
            "vision": False,
            "reasoning": True,
            "context_window": 131072,
            "max_output": 32768,
            "efforts": ("low", "medium", "high"),
            "effort_default": "medium",
            "effort_patch": {"reasoning_effort": "$effort"},
        },
    ),
)

_EAC_DEFAULT_CAPS: dict[str, Any] = {"vision": False, "reasoning": True}


def eac_display_name(model_id: str) -> str:
    """The model's own name under the channel tag; the org prefix never shows."""
    base = str(model_id or "").strip()
    bare = base[base.index("/") + 1 :] if "/" in base else base
    known = _EAC_DISPLAY_NAMES.get(base)
    return f"{EAC_TAG} {known or _pretty(bare)}"


def eac_spec(model_id: str, provider: str) -> ModelSpec:
    """One EAC row: the vetted capacity table, or the lane's own default.

    The menu (and only the menu) is what the wire carries: a row ZCode never
    declared has no menu, so the free lane's ladder is what applies to it.
    """
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = dict(_EAC_DEFAULT_CAPS)
    for pattern, entry in _EAC_CAPABILITIES:
        if pattern.search(model_id):
            caps = dict(entry)
            break
    efforts = caps.get("efforts")
    return ModelSpec(
        id=model_id,
        provider=provider,
        name=eac_display_name(model_id),
        wire="chat",
        vision=bool(caps.get("vision")),
        reasoning=bool(caps.get("reasoning", True)),
        context_window=_positive_int(caps.get("context_window")) or FALLBACK_CONTEXT_WINDOW,
        max_output=_positive_int(caps.get("max_output")) or FALLBACK_MAX_OUTPUT,
        can_disable_thinking=isinstance(efforts, tuple) and "disabled" in efforts,
        region_sensitive=False,
        efforts=tuple(str(item) for item in efforts) if isinstance(efforts, (tuple, list)) else (),
        effort_default=caps.get("effort_default"),
        effort_patch=caps.get("effort_patch"),
        effort_off_patch=caps.get("effort_off_patch"),
    )


# -- one row of a station's own capability table ------------------------------ #


def _menu_spec(
    model_id: str,
    provider: str,
    caps: dict[str, Any],
    *,
    effort_patch: dict[str, Any] | None = None,
    effort_off_patch: dict[str, Any] | None = None,
) -> ModelSpec:
    """Build one row from a station's own capability table.

    A row that declares ``efforts`` carries its own thinking menu, so the level
    is the real control on the wire; a row without one has no menu at all and
    its body passes through untouched. ``effort_off_id`` names the rung that
    switches thinking off — upstream spells that ``none``, ``off`` or
    ``disabled``, per station — and a table that declares none leaves the model
    with no way to ask for a quiet turn.

    ``effort_off_patch`` is for a station whose off rung is *not* the level
    written on the wire: CodeArts answers `thinking.type`, not `reasoning_effort`,
    and only the off rung has anything to say.
    """
    efforts = caps.get("efforts")
    menu = tuple(str(item) for item in efforts) if isinstance(efforts, (tuple, list)) else ()
    off_id = caps.get("effort_off_id")
    patch = caps.get("effort_patch") or effort_patch
    off_patch = caps.get("effort_off_patch") or effort_off_patch
    return ModelSpec(
        id=model_id,
        provider=provider,
        name=str(caps.get("name") or display_name(model_id)),
        wire="chat",
        vision=bool(caps.get("vision")),
        reasoning=bool(menu),
        context_window=_positive_int(caps.get("context_window")) or FALLBACK_CONTEXT_WINDOW,
        max_output=_positive_int(caps.get("max_output")) or FALLBACK_MAX_OUTPUT,
        can_disable_thinking=bool(off_id),
        efforts=menu,
        effort_default=caps.get("effort_default"),
        effort_off_id=off_id,
        effort_patch=patch if menu else None,
        effort_off_patch=off_patch if menu else None,
    )


# -- the Buddy lanes (CodeBuddy / WorkBuddy) ---------------------------------- #
#
# Upstream ships a per-model table for both stations: the context window, the
# output ceiling, whether the lane takes an image, and the model's own thinking
# menu. The menu reaches the wire as a top-level `reasoning_effort`, and neither
# station offers a rung that switches thinking off — a row with no
# `reasoningEfforts` has no menu at all, so it is inert here too.
#
# Transcribed from `vendor/channel-pack/src/product.ts`
# (`CODEBUDDY_FALLBACK_MODELS` / `WORKBUDDY_FALLBACK_MODELS`), 2026-10-12.

BUDDY_EFFORT_PATCH: dict[str, Any] = {"reasoning_effort": "$effort"}

# The DeepSeek rows carry a sibling thinking switch next to the level.
_BUDDY_DEEPSEEK_PATCH: dict[str, Any] = {
    "reasoning_effort": "$effort",
    "thinking": {"type": "enabled"},
}

_CODEBUDDY_CAPABILITIES: dict[str, dict[str, Any]] = {
    "hy4-preview": {"name": "Hy4 preview", "context_window": 1000000, "max_output": 64000, "vision": True, "efforts": ("high",), "effort_default": "high"},
    "hy3": {"name": "Hy3", "context_window": 192000, "max_output": 64000, "vision": True, "efforts": ("low", "high"), "effort_default": "high"},
    "hy3-x": {"name": "Hy3", "context_window": 192000, "max_output": 64000, "vision": True, "efforts": ("low", "high"), "effort_default": "high"},
    "deepseek-v4.1-flash": {
        "name": "Deepseek-V4.1-Flash",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("low", "high", "max"),
        "effort_default": "high",
        "effort_patch": _BUDDY_DEEPSEEK_PATCH,
    },
    "deepseek-v4-pro": {
        "name": "Deepseek-V4-Pro",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("low", "high", "xhigh"),
        "effort_default": "high",
        "effort_patch": _BUDDY_DEEPSEEK_PATCH,
    },
    "deepseek-v4-flash": {
        "name": "Deepseek-V4-Flash",
        "context_window": 1000000,
        "max_output": 50000,
        "vision": True,
        "efforts": ("low", "high", "max"),
        "effort_default": "high",
        "effort_patch": _BUDDY_DEEPSEEK_PATCH,
    },
    "glm-5.3": {"name": "GLM-5.3", "context_window": 1000000, "max_output": 64000, "vision": True, "efforts": ("low", "high", "max"), "effort_default": "high"},
    "glm-5.3-flash": {"name": "GLM-5.3-Flash", "context_window": 1000000, "max_output": 32000, "vision": True, "efforts": ("low", "high", "max"), "effort_default": "high"},
    "glm-5.2": {"name": "GLM-5.2", "context_window": 1000000, "max_output": 64000, "vision": True, "efforts": ("high", "xhigh"), "effort_default": "high"},
    "glm-5.1": {"name": "GLM-5.1", "context_window": 200000, "max_output": 48000, "vision": True, "efforts": ("medium",)},
    "glm-5v-turbo": {"name": "GLM-5V-Turbo", "context_window": 200000, "max_output": 64000, "vision": True, "efforts": ("medium",)},
    "kimi-k3-1": {"name": "Kimi-K3-1", "context_window": 1000000, "max_output": 32000, "vision": True, "efforts": ("medium",)},
    "kimi-k2.8-preview": {"name": "Kimi-K2.8-Preview", "context_window": 1000000, "max_output": 64000, "vision": True, "efforts": ("low", "high", "max"), "effort_default": "high"},
    "kimi-k2.7": {"name": "Kimi-K2.7", "context_window": 256000, "max_output": 32000, "vision": True, "efforts": ("medium",)},
    "kimi-k2.6": {"name": "Kimi-K2.6", "context_window": 256000, "max_output": 32000, "vision": True, "efforts": ("medium",)},
    "minimax-m3": {"name": "MiniMax-M3", "context_window": 512000, "max_output": 64000, "vision": True, "efforts": ("medium",)},
}

_WORKBUDDY_CAPABILITIES: dict[str, dict[str, Any]] = {
    "default-model": {"name": "Auto", "context_window": 176000, "max_output": 24000, "vision": True},
    "fast-model": {"name": "Fast", "context_window": 200000, "max_output": 32000, "vision": True, "efforts": ("medium",)},
    "balanced-model": {"name": "Balanced", "context_window": 256000, "max_output": 32000, "vision": True, "efforts": ("medium",)},
    "primary-model": {"name": "Primary", "context_window": 272000, "max_output": 72000, "vision": True, "efforts": ("high",)},
    "deep-model": {"name": "Deep", "context_window": 176000, "max_output": 24000, "vision": True},
    "hy4-preview-f": {"name": "Hy4 preview", "context_window": 1000000, "max_output": 64000, "vision": True, "efforts": ("high",), "effort_default": "high"},
    "hy4-preview": {"name": "Hy4 preview", "context_window": 1000000, "max_output": 64000, "vision": True, "efforts": ("high",), "effort_default": "high"},
    "hy3": {"name": "Hy3", "context_window": 192000, "max_output": 64000, "vision": True, "efforts": ("low", "high"), "effort_default": "high"},
    "deepseek-v4.1-flash": {
        "name": "Deepseek-V4.1-Flash",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("high",),
        "effort_default": "high",
        "effort_patch": _BUDDY_DEEPSEEK_PATCH,
    },
    "deepseek-v4.1-flash-sg": {
        "name": "Deepseek-V4.1-Flash",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("high",),
        "effort_default": "high",
        "effort_patch": _BUDDY_DEEPSEEK_PATCH,
    },
    "gpt-6-astra": {
        "name": "GPT-6-Astra",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("low", "medium", "high", "xhigh", "max"),
        "effort_default": "high",
    },
    "gpt-5.6-sol": {
        "name": "GPT-5.6-Sol",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("low", "medium", "high", "xhigh", "max"),
        "effort_default": "high",
    },
    "gpt-5.6-terra": {
        "name": "GPT-5.6-Terra",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("low", "medium", "high", "xhigh", "max"),
        "effort_default": "high",
    },
    "gpt-5.6-luna": {
        "name": "GPT-5.6-Luna",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("low", "medium", "high", "xhigh", "max"),
        "effort_default": "high",
    },
    "gpt-5.5": {"name": "GPT-5.5", "context_window": 1000000, "max_output": 128000, "vision": True, "efforts": ("low", "medium", "high", "xhigh"), "effort_default": "high"},
    "gpt-5.4": {"name": "GPT-5.4", "context_window": 272000, "max_output": 72000, "vision": True, "efforts": ("low", "medium", "high", "xhigh"), "effort_default": "high"},
    "gpt-5.3-codex": {"name": "GPT-5.3-Codex", "context_window": 272000, "vision": True, "efforts": ("medium",)},
    "gemini-3.5-flash": {"name": "Gemini-3.5-Flash", "context_window": 1000000, "max_output": 65536, "vision": True, "efforts": ("medium",)},
    "glm-5.3": {"name": "GLM-5.3", "context_window": 1000000, "max_output": 48000, "vision": True, "efforts": ("low", "high", "max"), "effort_default": "high"},
    "glm-5.2": {"name": "GLM-5.2", "context_window": 1000000, "max_output": 48000, "vision": True, "efforts": ("high", "xhigh"), "effort_default": "high"},
    "kimi-k3": {"name": "Kimi-K3", "context_window": 1000000, "max_output": 32000, "vision": True, "efforts": ("medium",)},
    "kimi-k2.8-preview": {"name": "Kimi-K2.8-Preview", "context_window": 1000000, "max_output": 32000, "vision": True, "efforts": ("low", "high", "max"), "effort_default": "high"},
    "kimi-k2.6": {"name": "Kimi-K2.6", "context_window": 256000, "max_output": 32000, "vision": True, "efforts": ("medium",)},
}


def buddy_spec(model_id: str, provider: str, *, work: bool = False) -> ModelSpec:
    """One Buddy row: the station's own table, or its inert default."""
    model_id = str(model_id or "").strip()
    table = _WORKBUDDY_CAPABILITIES if work else _CODEBUDDY_CAPABILITIES
    caps: dict[str, Any] = {"vision": False}
    caps.update(table.get(model_id, {}))
    return _menu_spec(model_id, provider, caps, effort_patch=BUDDY_EFFORT_PATCH)


# -- the Cline free lane ------------------------------------------------------ #
#
# Three fixed ids, and one thinking menu that belongs to the lane rather than to
# any row: five rungs with `none` among them, written verbatim as a top-level
# `reasoning_effort`. Upstream labels the last rung "Extra"; the id is `max`.
# The default is `high`, which upstream notes changes Cline from "never thinks"
# to "thinks unless told otherwise" — the point of declaring it.
#
# Transcribed from `vendor/channel-pack/src/cline-product.ts`
# (`CLINE_FALLBACK_MODELS` + `CLINE_REASONING_EFFORTS`), 2026-10-12.

CLINE_EFFORTS: tuple[str, ...] = ("none", "low", "medium", "high", "max")
CLINE_EFFORT_PATCH: dict[str, Any] = {"reasoning_effort": "$effort"}
# Upstream's `CLINE_DEFAULT_REASONING_EFFORT`: declaring it moves Cline from
# "never thinks unless asked" to "thinks unless told otherwise", which is the
# point of declaring a menu at all.
CLINE_DEFAULT = "high"

_CLINE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "stealth/space-bunny-alpha": {"name": "Space Bunny Alpha", "context_window": 1000000, "max_output": 524288, "vision": True},
    "cline-free/mimo-v2.6-flash": {"name": "MiMo-V2.6-Flash", "context_window": 1048576, "max_output": 131072, "vision": True},
    "cline-free/muse-spark-1.3-contributor": {"name": "Muse Spark 1.3 Contributor", "context_window": 1048576, "max_output": 943718, "vision": True},
}


def cline_spec(model_id: str, provider: str) -> ModelSpec:
    """One Cline row: the lane's menu on every id, capacities where known.

    The menu is a property of the *lane*, not of a row: upstream declares these
    five rungs for every Cline model because the remote catalogue carries no
    rungs of its own, and an id the table has never seen would otherwise show
    "this model offers no reasoning levels" in the picker. Only the capacities
    stay row-bound — inventing a context window for an id nobody measured is
    the one thing an unknown row must not do.
    """
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = {
        "vision": False,
        "efforts": CLINE_EFFORTS,
        "effort_default": CLINE_DEFAULT,
        "effort_off_id": "none",
    }
    caps.update(_CLINE_CAPABILITIES.get(model_id, {}))
    return _menu_spec(model_id, provider, caps, effort_patch=CLINE_EFFORT_PATCH)


# -- the Loomy lane ----------------------------------------------------------- #
#
# Eight chat ids, one lane-wide menu of five rungs with `none` among them. The
# names upstream ships carry the billing multiplier (` · x3.0`); that is a price
# tag, not a label, so only the bare model name is shown here. Upstream's own
# default is `high` rather than the remote `low`, deliberately — the two paths
# must agree on what an unchosen turn sends.
#
# No `supportsImage` in the upstream table, so no row claims vision.
#
# Transcribed from `vendor/channel-pack/src/loomy-product.ts`
# (`LOOMY_FALLBACK_MODELS` + `LOOMY_EFFORTS` + `LOOMY_DEFAULT_EFFORT`),
# 2026-10-12.

LOOMY_EFFORTS: tuple[str, ...] = ("none", "low", "medium", "high", "xhigh")
LOOMY_EFFORT_PATCH: dict[str, Any] = {"reasoning_effort": "$effort"}
LOOMY_DEFAULT_EFFORT = "high"

_LOOMY_CAPABILITIES: dict[str, dict[str, Any]] = {
    "deepseek-v4-flash-0731": {"name": "DeepSeek V4 Flash 0731", "context_window": 1048576},
    "MiniMax-M3": {"name": "MiniMax M3", "context_window": 1048576},
    "Kimi-k2.6": {"name": "Kimi k2.6", "context_window": 262144},
    "qwen-3.8-max": {"name": "Qwen 3.8 Max", "context_window": 1000000},
    "GLM-5.3-Flash": {"name": "GLM 5.3 Flash", "context_window": 1048576},
    "qwen3.8-flash": {"name": "qwen 3.8 flash", "context_window": 1000000},
    "spark-x": {"name": "Spark X2.5", "context_window": 1048576},
    "mimo-v2.5": {"name": "MiMo V2.5", "context_window": 1048576},
}


def loomy_spec(model_id: str, provider: str) -> ModelSpec:
    """One Loomy row: capacities from the table, the lane's menu on top."""
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = {"vision": False}
    caps.update(dict(_LOOMY_CAPABILITIES.get(model_id, {})))
    caps.setdefault("efforts", LOOMY_EFFORTS)
    caps.setdefault("effort_default", LOOMY_DEFAULT_EFFORT)
    caps.setdefault("effort_off_id", "none")
    return _menu_spec(model_id, provider, caps, effort_patch=LOOMY_EFFORT_PATCH)


# -- the Trae lane ------------------------------------------------------------ #
#
# Upstream's table carries a context window and nothing else: the thinking menu
# arrives per account from the remote catalogue, which this gateway does not
# fetch. So the row stays inert — `reasoning=False` means a caller's own
# `reasoning_effort` reaches the station untouched instead of being rewritten
# into a ladder this lane does not run, and no menu is advertised until the
# rungs are known.
#
# Transcribed from `vendor/channel-pack/src/trae-product.ts`
# (`TRAE_FALLBACK_MODELS`), 2026-10-12.

TRAE_CONTEXT_WINDOW = 200000


def trae_spec(model_id: str, provider: str) -> ModelSpec:
    """One Trae row: the context window is all the table declares."""
    model_id = str(model_id or "").strip()
    return ModelSpec(
        id=model_id,
        provider=provider,
        name=display_name(model_id),
        wire="chat",
        vision=False,
        reasoning=False,
        context_window=TRAE_CONTEXT_WINDOW,
        max_output=FALLBACK_MAX_OUTPUT,
        can_disable_thinking=False,
    )


# -- the Qoder lanes (international / China) --------------------------------- #
#
# Two catalogues, two tables — never one copied into the other: `mmodel` is
# MiniMax-M3 on the international site and MiniMax-M2.7 in China, and the five
# international-only ids are rejected outright by the Chinese gateway. Both
# tables carry a context window, image support and the model's own thinking
# menu; neither declares an output ceiling, so every row keeps the gateway's
# default capacity rather than an invented one.
#
# `qoderEffortsFor` (`qoder-adapter.ts`) appends `none` to whatever rungs the
# row already declares when the catalogue carries a `thinking_config.disabled`
# branch. That is what `supports_disable` below means, and it is also why `none`
# is the off rung: `qmodel` / `qmodel_latest` declare no levels at all and end
# up with a one-rung menu whose only rung is "off".
#
# Upstream writes the level into the encrypted envelope's nested `parameters`
# object (`qoder-wasm.ts`), a path this gateway deliberately does not speak.
# It drives the public OpenAI endpoint instead, where `reasoning_effort` is the
# standard field and `none` is the very value upstream spells — so the level
# rides top-level, exactly as the Buddy lanes send theirs, and the sibling
# `enable_thinking` stays behind with the WASM payload.
#
# Transcribed from `vendor/channel-pack/src/qoder-product.ts`
# (`QODER_FALLBACK_MODELS` / `QODER_CN_FALLBACK_MODELS`), 2026-10-12.

QODER_EFFORT_PATCH: dict[str, Any] = {"reasoning_effort": "$effort"}

_QODER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "auto": {"name": "Auto", "context_window": 200000, "vision": True},
    "ultimate": {
        "name": "Ultimate",
        "context_window": 1000000,
        "vision": True,
        "efforts": ("xhigh", "high", "low", "max", "medium"),
        "effort_default": "high",
        "supports_disable": True,
    },
    "performance": {
        "name": "Performance",
        "context_window": 1000000,
        "vision": True,
        "efforts": ("xhigh", "high", "low", "max", "medium"),
        "effort_default": "medium",
        "supports_disable": True,
    },
    "efficient": {"name": "Efficient", "context_window": 1000000, "vision": True},
    "smodel": {"name": "Sonus", "context_window": 1000000, "vision": True, "efforts": ("xhigh", "high", "low", "max", "medium"), "effort_default": "high"},
    "cmodel": {"name": "Cantus", "context_window": 1000000, "vision": True, "efforts": ("xhigh", "high", "low", "max", "medium"), "effort_default": "high"},
    "qmodel_38max": {"name": "Qwen3.8-Max", "context_window": 1000000, "vision": True, "efforts": ("xhigh", "low", "medium"), "effort_default": "xhigh", "supports_disable": True},
    "qfmodel": {"name": "Qwen3.8-Flash", "context_window": 1000000, "vision": True, "efforts": ("xhigh", "low", "medium"), "effort_default": "medium", "supports_disable": True},
    "qmodel_latest": {"name": "Qwen3.7-Max", "context_window": 1000000, "vision": True, "supports_disable": True},
    "qmodel": {"name": "Qwen3.7-Plus", "context_window": 1000000, "vision": True, "supports_disable": True},
    "kmodel_latest": {"name": "Kimi-K3", "context_window": 1000000, "vision": True, "efforts": ("high", "low", "max"), "effort_default": "max"},
    "kmodel": {"name": "Kimi-K2.8-Preview", "context_window": 1000000, "vision": True, "efforts": ("high", "low", "max"), "effort_default": "max"},
    "gmodel": {"name": "GLM-5.3", "context_window": 1000000, "vision": True, "efforts": ("high", "low", "max"), "effort_default": "max"},
    "gfmodel": {"name": "GLM-5.3-Flash", "context_window": 1000000, "vision": True, "efforts": ("high", "max"), "effort_default": "max"},
    "dmodel": {"name": "DeepSeek-V4-Pro", "context_window": 1000000, "vision": True, "efforts": ("high", "max"), "effort_default": "max", "supports_disable": True},
    "dfmodel": {"name": "DeepSeek-Flash", "context_window": 1000000, "vision": True, "efforts": ("high", "max", "low"), "effort_default": "max", "supports_disable": True},
    "mmodel": {"name": "MiniMax-M3", "context_window": 1000000, "vision": True},
}

# CN-only ids: `q37fmodel` / `gm51model`. The Chinese rows carry no `vision`
# key at all where the catalogue has no `is_vl` (`mmodel`), so vision stays off.
_QODER_CN_CAPABILITIES: dict[str, dict[str, Any]] = {
    "auto": {"name": "Auto", "context_window": 200000, "vision": True},
    "qmodel_38max": {"name": "Qwen3.8-Max", "context_window": 1000000, "vision": True, "efforts": ("xhigh", "low", "medium"), "effort_default": "medium", "supports_disable": True},
    "qfmodel": {"name": "Qwen3.8-Flash", "context_window": 1000000, "vision": True, "efforts": ("xhigh", "low", "medium"), "effort_default": "medium", "supports_disable": True},
    "qmodel_latest": {"name": "Qwen3.7-Max", "context_window": 1000000, "vision": True, "supports_disable": True},
    "qmodel": {"name": "Qwen3.7-Plus", "context_window": 1000000, "vision": True, "supports_disable": True},
    "q37fmodel": {"name": "Qwen3.7-Flash", "context_window": 1000000, "vision": True},
    "dmodel": {"name": "DeepSeek-V4-Pro", "context_window": 1000000, "vision": True, "efforts": ("high", "max"), "effort_default": "max", "supports_disable": True},
    "dfmodel": {"name": "DeepSeek-Flash", "context_window": 1000000, "vision": True, "efforts": ("high", "max", "low"), "effort_default": "max", "supports_disable": True},
    "gmodel": {"name": "GLM-5.3", "context_window": 1000000, "vision": True, "efforts": ("high", "low", "max"), "effort_default": "max"},
    "gfmodel": {"name": "GLM-5.3-Flash", "context_window": 1000000, "vision": True, "efforts": ("high", "max"), "effort_default": "max"},
    "gm51model": {"name": "GLM-5.2", "context_window": 1000000, "vision": True, "efforts": ("high", "max"), "effort_default": "max", "supports_disable": True},
    "kmodel_latest": {"name": "Kimi-K3", "context_window": 1000000, "vision": True, "efforts": ("high", "low", "max"), "effort_default": "max"},
    "kmodel": {"name": "Kimi-K2.8-Preview", "context_window": 1000000, "vision": True, "efforts": ("high", "low", "max"), "effort_default": "max"},
    "mmodel": {"name": "MiniMax-M2.7", "context_window": 200000},
}


def qoder_spec(model_id: str, provider: str, *, cn: bool = False) -> ModelSpec:
    """One Qoder row: this site's catalogue, or an inert default.

    The off rung is not written into the table — it is derived the way the
    client derives it, by appending `none` when the catalogue has a `disabled`
    branch. A row with neither levels nor a `disabled` branch has no menu at
    all, which is upstream's "this model offers no reasoning levels".
    """
    model_id = str(model_id or "").strip()
    table = _QODER_CN_CAPABILITIES if cn else _QODER_CAPABILITIES
    caps: dict[str, Any] = dict(table.get(model_id, {}))
    rungs: list[str] = list(caps.get("efforts") or ())
    if caps.get("supports_disable") and "none" not in rungs:
        rungs.append("none")
    caps["efforts"] = tuple(rungs)
    caps["effort_off_id"] = "none" if caps.get("supports_disable") else None
    return _menu_spec(model_id, provider, caps, effort_patch=QODER_EFFORT_PATCH)


# -- the MiniMax lane -------------------------------------------------------- #
#
# Four ids, three distinct thinking shapes, all of them decided by one field:
#
# * `forced_on` (`M3.1-Flash-Preview`, `M2.7`, `M2.7-highspeed`): switching
#   thinking off is a hard 400 on M3.1 and a silent no-op on M2.7, so those rows
#   declare no off rung. Only M3.1 carries level options;
# * `switchable` (`M3`): no options at all, just the two states — `on` and `none`.
#   Upstream appends both and refuses to name a default (measured: M3 does not
#   think when `thinking` is absent at all), which is why this table pins `none`
#   rather than leaving the picker to fall on `on`.
#
# The level reaches the provider as `reasoning_effort`; `_thinking_extra` is what
# translates it into `thinking` / `output_config`, and it only forwards a rung the
# table above actually declares.
#
# Transcribed from `vendor/channel-pack/src/minimax-product.ts`
# (`MINIMAX_FALLBACK_MODELS`), 2026-10-12.

MINIMAX_EFFORT_PATCH: dict[str, Any] = {"reasoning_effort": "$effort"}

_MINIMAX_CAPABILITIES: dict[str, dict[str, Any]] = {
    "MiniMax-M3.1-Flash-Preview": {
        "name": "M3.1-Flash-Preview",
        "context_window": 1000000,
        "max_output": 128000,
        "vision": True,
        "efforts": ("default", "low", "medium", "high", "xhigh", "max"),
        "effort_default": "default",
    },
    "MiniMax-M3": {"name": "M3", "context_window": 1000000, "max_output": 128000, "vision": True, "efforts": ("on", "none"), "effort_default": "none", "effort_off_id": "none"},
    "MiniMax-M2.7-highspeed": {"name": "M2.7-highspeed", "context_window": 200000, "max_output": 128000},
    "MiniMax-M2.7": {"name": "M2.7", "context_window": 200000, "max_output": 128000},
}


def minimax_spec(model_id: str, provider: str) -> ModelSpec:
    """One MiniMax row: its own thinking shape, or an inert default."""
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = {"vision": False}
    caps.update(dict(_MINIMAX_CAPABILITIES.get(model_id, {})))
    return _menu_spec(model_id, provider, caps, effort_patch=MINIMAX_EFFORT_PATCH)


def minimax_efforts_for(model_id: str) -> tuple[str, ...]:
    """The rungs one id declares, for the adapter that has to drop the rest.

    `M2.7` has no `effort_options` upstream, so a level aimed at it is dropped
    rather than forwarded — sending `output_config` to a model that never
    offered one is the same invention a table this size exists to prevent.
    """
    rungs = _MINIMAX_CAPABILITIES.get(str(model_id or "").strip(), {}).get("efforts")
    return tuple(rungs) if isinstance(rungs, tuple) else ()


# -- the ZCode lane ---------------------------------------------------------- #
#
# Two ids, one three-rung menu, and a wire that is not OpenAI's: the level is
# written as `output_config.effort` inside the Anthropic payload. The provider
# reads it back off `reasoning_effort` *after* `build_anthropic_body` has run,
# because the Anthropic builder drops keys it does not know.
#
# Transcribed from `vendor/channel-pack/src/zcode-product.ts`
# (`ZCODE_FALLBACK_MODELS`), 2026-10-12.

ZCODE_EFFORT_PATCH: dict[str, Any] = {"reasoning_effort": "$effort"}

_ZCODE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "GLM-5.3-Flash": {"name": "GLM-5.3-Flash", "context_window": 1000000, "max_output": 128000, "vision": True, "efforts": ("low", "high", "max"), "effort_default": "max"},
    "GLM-5.3": {"name": "GLM-5.3", "context_window": 1000000, "max_output": 128000, "vision": False, "efforts": ("low", "high", "max"), "effort_default": "max"},
}


def zcode_spec(model_id: str, provider: str) -> ModelSpec:
    """One ZCode row: capacities from the table, its three rungs where known."""
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = {"vision": False}
    caps.update(dict(_ZCODE_CAPABILITIES.get(model_id, {})))
    return _menu_spec(model_id, provider, caps, effort_patch=ZCODE_EFFORT_PATCH)


# -- the CodeArts lane ------------------------------------------------------- #
#
# A switch, not a ladder: upstream measured `reasoning_effort` (every value
# included) as accepted-but-ineffective on this gateway and declares exactly two
# rungs for **every** model, because the control is the top-level `thinking.type`
# and it is a property of the gateway rather than of any row. `on` therefore
# writes nothing at all — the server's default is thinking — and `off` is the
# only rung with a patch.
#
# No output ceiling is published either; 65536 is the ceiling the station's own
# body builder already sends when a caller names none, so declaring it keeps the
# wire where it is instead of dropping it to the gateway's global default.
#
# Transcribed from `vendor/channel-pack/src/llm-adapter.ts`
# (`CONTEXT_WINDOWS` + `resolveModel`), 2026-10-12.

CODEARTS_EFFORTS: tuple[str, ...] = ("on", "off")
CODEARTS_EFFORT_PATCH: dict[str, Any] = {}
CODEARTS_EFFORT_OFF_PATCH: dict[str, Any] = {"thinking": {"type": "disabled"}}
CODEARTS_MAX_OUTPUT = 65536

_CODEARTS_CONTEXT_WINDOWS: dict[str, int] = {
    "GLM-5.2": 202752,
    "glm-5.3-flash": 1048576,
    "deepseek-v4-flash": 1048576,
    "deepseek-v4-pro": 1048576,
    "deepseek-v4.1-flash": 1000000,
}


def codearts_spec(model_id: str, provider: str) -> ModelSpec:
    """One CodeArts row: the lane's switch on every id, capacities where known."""
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = {
        "vision": False,
        "efforts": CODEARTS_EFFORTS,
        "effort_default": "on",
        "effort_off_id": "off",
        "max_output": CODEARTS_MAX_OUTPUT,
    }
    context = _CODEARTS_CONTEXT_WINDOWS.get(model_id)
    if context is not None:
        caps["context_window"] = context
    return _menu_spec(
        model_id,
        provider,
        caps,
        effort_patch=CODEARTS_EFFORT_PATCH,
        effort_off_patch=CODEARTS_EFFORT_OFF_PATCH,
    )


# -- the Raccoon lane -------------------------------------------------------- #
#
# Six visible ids and, for all of them, the same two rungs: `extra_body.thinking`
# is a provider-level dialect here, not a per-model one, and upstream measured
# the server default to be thinking already — so `on` states that rather than
# changing it. Off is the only rung that differs from what would go out anyway.
#
# Upstream's labels carry the billing multiplier (` · x0.75`, ` · 免费`); that is
# a price tag, not part of the model's name, and it is dropped exactly as Loomy's
# ` · x3.0` was. The output ceiling is the one the catalogue publishes
# (`params.max_tokens`), unlike CodeArts'.
#
# Transcribed from `vendor/channel-pack/src/raccoon-product.ts`
# (`RACCOON_FALLBACK_MODELS` + `RACCOON_REASONING_EFFORTS`), 2026-10-12.

RACCOON_EFFORTS: tuple[str, ...] = ("on", "off")
RACCOON_EFFORT_PATCH: dict[str, Any] = {"extra_body": {"thinking": {"type": "enabled"}}}
RACCOON_EFFORT_OFF_PATCH: dict[str, Any] = {"extra_body": {"thinking": {"type": "disabled"}}}

_RACCOON_CAPABILITIES: dict[str, dict[str, Any]] = {
    "sn-sensenova-6-8-flash": {"name": "SenseNova-6.8-Flash", "context_window": 256000, "max_output": 63999, "vision": True},
    "sn-sensenova-6-8-flash-lite": {"name": "SenseNova-6.8-Flash-Lite", "context_window": 256000, "max_output": 63999, "vision": True},
    "sn-glm-5-3": {"name": "GLM-5-3", "context_window": 1000000, "max_output": 100000, "vision": True},
    "sn-kimi-k3": {"name": "Kimi-K3", "context_window": 1000000, "max_output": 100000, "vision": True},
    "sn-glm-5-3-flash": {"name": "GLM-5-3-Flash", "context_window": 1000000, "max_output": 100000, "vision": True},
    "sn-deepseek-v4-1-flash": {"name": "DeepSeek-V4.1-Flash", "context_window": 1000000, "max_output": 100000, "vision": True},
}


def raccoon_spec(model_id: str, provider: str) -> ModelSpec:
    """One Raccoon row: capacities from the table, the lane's switch on top."""
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = {"vision": False}
    caps.update(dict(_RACCOON_CAPABILITIES.get(model_id, {})))
    caps.setdefault("efforts", RACCOON_EFFORTS)
    caps.setdefault("effort_default", "on")
    caps.setdefault("effort_off_id", "off")
    return _menu_spec(
        model_id,
        provider,
        caps,
        effort_patch=RACCOON_EFFORT_PATCH,
        effort_off_patch=RACCOON_EFFORT_OFF_PATCH,
    )


# -- the Gemini lane ---------------------------------------------------------- #
#
# One id, four rungs, and no rung that switches thinking off. Upstream measured
# that flipping `includeThoughts` off still thinks and still bills, so the off is
# fake — the station publishes no such rung and this table offers none either.
#
# The level is not a label: it becomes a thinking budget *and* a suffix on the
# model name, because the bare `gemini-3.8-flash` is answered 404 — the suffix is
# the admission key. The catalogue therefore shows a single row, exactly as
# upstream decided ("不暴露 4 个带后缀的模型名 —— 档位走 efforts 下拉框"), and
# `GeminiProvider.stream_raw` appends the suffix on the way out.
#
# Transcribed from `vendor/channel-pack/src/gemini.ts` (`GEMINI_FALLBACK_MODELS`
# + `GEMINI_EFFORT_IDS` + `GEMINI_DEFAULT_EFFORT`), 2026-10-12.

GEMINI_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "tiered")
GEMINI_DEFAULT_EFFORT = "medium"
# The level rides the standard field; `translate.gemini` turns it into the
# thinking budget and the model-name suffix.
GEMINI_EFFORT_PATCH: dict[str, Any] = {"reasoning_effort": "$effort"}

_GEMINI_CAPABILITIES: dict[str, dict[str, Any]] = {
    "gemini-3.8-flash": {
        "name": "Gemini 3.8 Flash",
        "context_window": 1000000,
        "max_output": 64000,
        "vision": True,
        "efforts": GEMINI_EFFORTS,
        "effort_default": GEMINI_DEFAULT_EFFORT,
    },
}


def gemini_spec(model_id: str, provider: str) -> ModelSpec:
    """One Gemini row: the station's single id, with its four-tier menu."""
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = {"vision": False}
    caps.update(dict(_GEMINI_CAPABILITIES.get(model_id, {})))
    return _menu_spec(model_id, provider, caps, effort_patch=GEMINI_EFFORT_PATCH)


# -- the aggregator lane ---------------------------------------------------- #
#
# An arbitrary OpenAI-compatible station the install brings its own key for.
# The gateway cannot know what an arbitrary upstream id can do, so the default
# row is deliberately inert: `reasoning=False` means the effort policy leaves
# the body alone and the caller's own fields reach the station unchanged.
# Declarations come from the account's `options`, which is also where a model
# that does carry a menu states it.

_AGG_DEFAULT_CAPS: dict[str, Any] = {"vision": False, "reasoning": False}

# CommandCode's own catalogue, transcribed from pi-free's
# `providers/commandcode/commandcode-pricing.ts` (itself a port of
# patlux/pi-commandcode-provider, MIT), last checked against the station's
# pricing page 2026-08-25. It is the one aggregator that publishes what its
# models can do, so its two lists are the one place a spec in this lane can
# rest on the station instead of on models.dev — which describes somebody
# else's station, and never this one's.
#
# Two lists of what a model *is*, and no numbers, deliberately. The same
# upstream file carries a cost table as well (56 of its 58 entries are priced,
# and those are not migrating: a paid model's price is not this gateway's
# business — only the two $0 entries were, as `free_marks`) and a three-row
# output-ceiling table whose substance is really pi-free's own default rather
# than CommandCode's. "This model reasons" and "this model takes images" are
# facts the station vouches for; "this model emits 16384 tokens" is a number
# that goes stale, and C23 already refused one of those from a source further
# away than this.
COMMANDCODE_VISION: frozenset[str] = frozenset(
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

COMMANDCODE_REASONING: frozenset[str] = frozenset(
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

_AGG_STATION_CAPABILITIES: dict[str, dict[str, frozenset[str]]] = {
    "commandcode": {"vision": COMMANDCODE_VISION, "reasoning": COMMANDCODE_REASONING},
}
"""What one station published about its own ids, by station then by field.

The station is the key and not the provider, because `agg` is one provider
carrying twenty-two stations: an id's capabilities belong to whoever is
selling it, and the same string at two stations can be two different things.
"""


def agg_capability(station: str | None, model_id: str) -> dict[str, Any]:
    """The two facts CommandCode's catalogue says one id has, and nothing else.

    An id the station does not name gets nothing at all — no field, not even
    a False — because a table that covered "the rest" would be this gateway
    deciding what an unlisted model does. The empty dict is what makes that
    distinguishable downstream: a named id and an unnamed one can no longer
    look alike.
    """
    if station is None:
        return {}
    fields = _AGG_STATION_CAPABILITIES.get(station)
    if fields is None:
        return {}
    return {field: True for field, ids in fields.items() if model_id in ids}


def agg_spec(
    model_id: str,
    provider: str,
    overrides: dict[str, Any] | None = None,
    *,
    station_caps: dict[str, Any] | None = None,
) -> ModelSpec:
    """One aggregator row: the account's declarations, else the station's, else the inert default.

    Three sources with a fixed order, because they have different standing. The
    inert default is a refusal to guess. What the station published about its
    own id outranks it (and outranks models.dev, which describes somebody
    else's station). What the operator declared outranks both: an account that
    states a capacity knows its own contract, and a table transcribed from a
    pricing page does not.
    """
    caps: dict[str, Any] = dict(_AGG_DEFAULT_CAPS)
    caps.update(station_caps or {})
    caps.update(overrides or {})
    efforts = caps.get("efforts")
    return ModelSpec(
        id=model_id,
        provider=provider,
        name=display_name(model_id),
        wire="chat",
        vision=bool(caps.get("vision")),
        reasoning=bool(caps.get("reasoning")),
        context_window=_positive_int(caps.get("context_window")) or FALLBACK_CONTEXT_WINDOW,
        max_output=_positive_int(caps.get("max_output")) or FALLBACK_MAX_OUTPUT,
        can_disable_thinking=isinstance(efforts, tuple) and "disabled" in efforts,
        region_sensitive=bool(caps.get("region_sensitive")),
        efforts=tuple(str(item) for item in efforts) if isinstance(efforts, (tuple, list)) else (),
        effort_default=caps.get("effort_default"),
        effort_patch=caps.get("effort_patch") if isinstance(caps.get("effort_patch"), dict) else None,
        effort_off_patch=caps.get("effort_off_patch") if isinstance(caps.get("effort_off_patch"), dict) else None,
    )


# -- the Zhipu (BigModel) free lane ----------------------------------------- #
#
# The permanently free slice of open.bigmodel.cn, as published in the
# platform's own pricing table (免费 / 免费 rows). Thinking on the GLM family
# is a `thinking: {type}` switch rather than a token ladder, and the free
# variants do not publish a menu, so a row stays inert (`reasoning=False`)
# unless the model is a thinking model by construction (Z1, V-Thinking):
# there the budget the lane enforces is the only control that reaches the
# wire, so the free lane's ladder is what applies.

_ZHIPU_CAPABILITIES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (re.compile(r"^glm-4\.7-flash$"), {"vision": False, "reasoning": False, "context_window": 2097152, "max_output": 131072}),
    (re.compile(r"^glm-4\.5-flash$"), {"vision": False, "reasoning": False, "context_window": 131072, "max_output": 98304}),
    (re.compile(r"^glm-4-flash-250414$"), {"vision": False, "reasoning": False, "context_window": 131072, "max_output": 16384}),
    (re.compile(r"^glm-z1-flash$"), {"vision": False, "reasoning": True, "context_window": 131072, "max_output": 131072, "can_disable_thinking": False}),
    (re.compile(r"^glm-4\.6v-flash$"), {"vision": True, "reasoning": False, "context_window": 131072, "max_output": 32768}),
    (re.compile(r"^glm-4\.1v-thinking-flash$"), {"vision": True, "reasoning": True, "context_window": 65536, "max_output": 16384, "can_disable_thinking": False}),
    (re.compile(r"^glm-4v-flash$"), {"vision": True, "reasoning": False, "context_window": 16384, "max_output": 1024}),
)

_ZHIPU_DEFAULT_CAPS: dict[str, Any] = {"vision": False, "reasoning": False}

_ZHIPU_DISPLAY_NAMES: dict[str, str] = {
    "glm-4.7-flash": "GLM-4.7-Flash",
    "glm-4.5-flash": "GLM-4.5-Flash",
    "glm-4-flash-250414": "GLM-4-Flash-250414",
    "glm-z1-flash": "GLM-Z1-Flash",
    "glm-4.6v-flash": "GLM-4.6V-Flash",
    "glm-4.1v-thinking-flash": "GLM-4.1V-Thinking-Flash",
    "glm-4v-flash": "GLM-4V-Flash",
}


def zhipu_display_name(model_id: str) -> str:
    base = str(model_id or "").strip()
    return _ZHIPU_DISPLAY_NAMES.get(base) or display_name(base)


def zhipu_spec(model_id: str, provider: str) -> ModelSpec:
    """One Zhipu row: the vetted free table, or the lane's inert default."""
    model_id = str(model_id or "").strip()
    caps: dict[str, Any] = dict(_ZHIPU_DEFAULT_CAPS)
    for pattern, entry in _ZHIPU_CAPABILITIES:
        if pattern.search(model_id):
            caps = dict(entry)
            break
    return ModelSpec(
        id=model_id,
        provider=provider,
        name=zhipu_display_name(model_id),
        wire="chat",
        vision=bool(caps.get("vision")),
        reasoning=bool(caps.get("reasoning")),
        context_window=_positive_int(caps.get("context_window")) or FALLBACK_CONTEXT_WINDOW,
        max_output=_positive_int(caps.get("max_output")) or FALLBACK_MAX_OUTPUT,
        can_disable_thinking=caps.get("can_disable_thinking") is not False,
        region_sensitive=False,
    )
