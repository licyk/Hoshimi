"""Reasoning-effort policy: levels that are enforced, not hinted.

A live probe of the free lane settled the design: the gateway accepts
`reasoning_effort`, `thinking.budget_tokens`, `enable_thinking` and
`thinking_budget` and then ignores all of them — the unknown fields also
occasionally surfaced as an upstream 503. The single control the pooled
account actually enforces is `max_tokens`, and reasoning volume tracks it
directly.

So an effort level here is a real, enforced generation budget rather than a
hint: every declared level changes measured behaviour. Because the ceiling is
shared by thinking and the visible answer, a lower level shortens both — and a
model whose thinking cannot be switched off pays for it out of the same ceiling
before the answer starts, so every rung of its ladder is widened by
:data:`ALWAYS_THINKING_FACTOR`.

A model that declares its own thinking menu (Kilo's free pool, the EAC lane)
is different: the gateway takes the level itself as the control, a JSON merge
patch on the request body, so there the menu — not a token ladder — is what
reaches the wire.

Unless the caller expresses a level, nothing here touches the body: an API
caller who set their own `max_tokens` keeps it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hoshimi.core.catalog import ModelSpec

DEFAULT_LEVEL = "balanced"
"""The rung a caller inherits when they name a level that does not exist."""

ALWAYS_THINKING_FACTOR = 2
"""Rungs of a model that must think are widened by this factor."""

MIN_BUDGET = 512
"""Below this the answer itself cannot land, so no level is allowed to go."""

MAX_TOKEN_KEYS = ("max_tokens", "max_completion_tokens")


@dataclass(frozen=True)
class Level:
    """One rung of the free lane's token ladder."""

    id: str
    name: str
    ceiling: int | None
    hint: str


# Ordered for display: the tuple order is the picker's order.
LEVELS: tuple[Level, ...] = (
    Level("light", "Light", 2048, "terse deliberation, the fastest answer here."),
    Level("balanced", "Balanced", 8192, "enough to reason through a normal turn."),
    Level("deep", "Deep", None, "the model's full output capacity, extended deliberation."),
)

# What a harness's vocabulary means on this lane: OpenAI sends
# minimal/low/medium/high, the ladder speaks light/balanced/deep.
_LADDER_ALIASES = {"minimal": "light", "low": "light", "medium": "balanced", "high": "deep", "xhigh": "deep", "max": "deep"}
# …and a declared menu speaks its model's own levels.
_MENU_ALIASES = {"minimal": "low", "light": "low", "balanced": "medium", "deep": "high", "xhigh": "max"}

# Upstream stations name their rungs themselves: `none` (Cline, Loomy, Qoder,
# MiniMax), `off` (LobsterAI, CodeArts, Raccoon), `on` where a switch is all the
# model offers, `xhigh`/`default` where a vendor table ships them, and Gemini's
# `tiered`, which is not a level of effort but "let the model pick".
_MENU_NAMES = {
    "disabled": "Off",
    "none": "Off",
    "off": "Off",
    "on": "On",
    "default": "Default",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "X-High",
    "max": "Max",
    "tiered": "Adaptive",
}


def supports_effort(spec: ModelSpec | None) -> bool:
    """Does this model carry a reasoning budget at all?"""
    return spec is not None and spec.reasoning


def has_declared_effort_menu(spec: ModelSpec | None) -> bool:
    """Does this entry declare its own thinking menu (its effort field is the
    control on the wire)? The free lane's ladder entries never do."""
    return spec is not None and len(spec.efforts) > 0


def requested_level(body: dict[str, Any]) -> str | None:
    """The effort level the caller asked for, if any."""
    value = body.get("reasoning_effort")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if isinstance(effort, str) and effort.strip():
            return effort.strip().lower()
    return None


def resolve_level(level: str | None, spec: ModelSpec | None) -> str | None:
    """The rung in force for one call.

    A model with no effort menu has no rung: a level inherited from elsewhere
    must not shrink a window that belongs to the answer. A menu model whose
    caller answered with a level outside the menu still gets the menu's
    default — the recorded effort is what the wire actually carries.
    """
    if spec is None:
        return None
    if has_declared_effort_menu(spec):
        if level is not None and level in spec.efforts:
            return level
        alias = _MENU_ALIASES.get(level or "", "")
        if alias and alias in spec.efforts:
            return alias
        return default_effort_for(spec)
    if not spec.reasoning:
        return None
    rungs = {run.id for run in LEVELS}
    if level in rungs:
        return level
    alias = _LADDER_ALIASES.get(level or "", "")
    if alias in rungs:
        return alias
    return DEFAULT_LEVEL


def default_effort_for(spec: ModelSpec | None) -> str | None:
    """The effort id a caller should treat as this model's default."""
    if spec is None:
        return None
    if has_declared_effort_menu(spec):
        if spec.effort_default and spec.effort_default in spec.efforts:
            return spec.effort_default
        return spec.efforts[0]
    return DEFAULT_LEVEL if spec.reasoning else None


def _usable(value: Any) -> float:
    """A caller-supplied token ceiling, or "none" when it is not a positive number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float("inf")
    return float(value) if value > 0 else float("inf")


def _ceiling_of(level_id: str | None, spec: ModelSpec | None) -> int | None:
    for rung in LEVELS:
        if rung.id == level_id:
            if rung.ceiling is None:
                return None
            if spec is not None and spec.can_disable_thinking is False:
                return rung.ceiling * ALWAYS_THINKING_FACTOR
            return rung.ceiling
    return None


def budget_for(
    level: str | None,
    spec: ModelSpec | None,
    requested: Any = None,
    fallback: Any = None,
) -> int:
    """Resolve the generation ceiling for one level against one model.

    The level ceiling is the controlling term; the model's own capacity and the
    caller's request can only lower it further, never raise it. A model with a
    declared thinking menu has its level as the real control on the wire, so
    its ceiling is the model capacity regardless of level — a ladder there would
    cut answers for a menu that never promised one.
    """
    if spec is None:
        return MIN_BUDGET
    capacity = min(_usable(spec.max_output), _usable(requested), _usable(fallback))
    if has_declared_effort_menu(spec):
        # A native effort field is independent of the caller's output ceiling.
        return max(1, int(capacity))
    ceiling = _ceiling_of(resolve_level(level, spec), spec)
    if ceiling is None:
        return max(MIN_BUDGET, int(capacity))
    return max(MIN_BUDGET, int(min(ceiling, capacity)))


def _instantiate(patch: Any, effort: str) -> Any:
    """Deep-clone a declared patch and substitute the level for ``$effort``."""
    if isinstance(patch, str):
        return effort if patch == "$effort" else patch
    if isinstance(patch, list):
        return [_instantiate(item, effort) for item in patch]
    if isinstance(patch, dict):
        return {key: _instantiate(value, effort) for key, value in patch.items()}
    return patch


def off_rung_id(spec: ModelSpec | None) -> str | None:
    """The rung this model switches thinking off with, if it has one.

    A station names that rung itself — `disabled`, `none` or `off` — and a
    table that declares an off patch without naming the rung keeps the
    historical `disabled`, so the older hand-written tables still answer.
    """
    if spec is None:
        return None
    if spec.effort_off_id:
        return spec.effort_off_id
    return "disabled" if spec.effort_off_patch is not None else None


def is_off_rung(level_id: str | None, spec: ModelSpec | None) -> bool:
    off = off_rung_id(spec)
    return bool(off) and level_id == off


def effort_patch_for(level: str | None, spec: ModelSpec | None) -> dict[str, Any] | None:
    """The request-body patch a menu level turns into, or None when the model
    declares no thinking menu at all."""
    if not has_declared_effort_menu(spec) or spec is None:
        return None
    chosen = resolve_level(level, spec)
    if chosen is None:
        return None
    if spec.effort_off_patch is not None and is_off_rung(chosen, spec):
        return _instantiate(spec.effort_off_patch, chosen)
    if spec.effort_patch is None:
        return None
    return _instantiate(spec.effort_patch, chosen)


def _menu_row(spec: ModelSpec, level_id: str) -> dict[str, Any]:
    off = is_off_rung(level_id, spec)
    return {
        "id": level_id,
        "name": _MENU_NAMES.get(level_id, level_id),
        "description": (
            "thinking switched off for this turn; the answer alone"
            if off
            else f'thinking level "{level_id}", sent to the gateway as this model\'s own effort field'
        ),
        "is_default": level_id == default_effort_for(spec),
        "tokens": None,
    }


def _ladder_row(spec: ModelSpec, rung: Level, tokens: int) -> dict[str, Any]:
    suffix = " (thinking cannot be switched off on this model)" if spec.can_disable_thinking is False else ""
    return {
        "id": rung.id,
        "name": rung.name,
        "description": f"{_kilos(tokens)} output ceiling, shared by thinking and the answer{suffix}: {rung.hint}",
        "is_default": rung.id == DEFAULT_LEVEL,
        "tokens": tokens,
    }


def efforts_for(spec: ModelSpec | None, requested: Any = None, fallback: Any = None) -> list[dict[str, Any]] | None:
    """The declared effort list for one model, in picker order.

    The description is generated from the same `budget_for`/patch call that
    will decide the request, not written next to it — a rung that advertises 8K
    while the wire sends 16384 is a number the user reads and the wire does not
    honour.
    """
    if spec is None:
        return None
    if has_declared_effort_menu(spec):
        return [_menu_row(spec, level_id) for level_id in spec.efforts]
    if not spec.reasoning:
        return None
    return [_ladder_row(spec, rung, budget_for(rung.id, spec, requested, fallback)) for rung in LEVELS]


def _kilos(tokens: int) -> str:
    return f"{round(tokens / 1024)} K"


def _max_tokens_of(body: dict[str, Any]) -> int | None:
    for key in MAX_TOKEN_KEYS:
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def apply_effort(spec: ModelSpec | None, body: dict[str, Any]) -> dict[str, Any]:
    """Return the body as it will go out: level ceilings and patches applied.

    Nothing happens when the caller named no level — an API caller who set
    their own `max_tokens` keeps it, and a model that neither thinks nor
    declares a menu is never touched.
    """
    if spec is None:
        return body
    menu = has_declared_effort_menu(spec)
    if not menu and not spec.reasoning:
        return body
    level = requested_level(body)
    if level is None:
        return body

    out = dict(body)
    chosen = resolve_level(level, spec)
    budget = budget_for(chosen, spec, _max_tokens_of(out))

    if menu:
        patch = effort_patch_for(chosen, spec) or {}
        for key in ("reasoning_effort", "reasoning"):
            if key not in patch:
                out.pop(key, None)
        out.update(patch)
    else:
        # The gateway ignores these fields and occasionally 503s on them: the
        # budget is the only control this lane enforces.
        out.pop("reasoning_effort", None)
        out.pop("reasoning", None)

    out["max_tokens"] = budget
    out.pop("max_completion_tokens", None)
    return out
