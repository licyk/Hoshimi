"""Shared model capabilities for the OpenAI and management catalogues."""

from __future__ import annotations

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.effort import efforts_for
from hoshimi.core.record import Record


class EffortOption(Record):
    id: str
    name: str
    description: str = ""
    is_default: bool = False
    tokens: int | None = None


class ModelCapabilities(Record):
    wire: str = "chat"
    vision: bool = False
    reasoning: bool = False
    tools: bool | None = None
    context_window: int = 0
    max_output: int = 0
    can_disable_thinking: bool = True
    region_sensitive: bool = False
    # None means the station did not declare whether calls cost anything.
    free: bool | None = None
    efforts: list[EffortOption]
    effort_default: str | None = None


def model_capabilities(spec: ModelSpec) -> ModelCapabilities:
    """Serialize the same capabilities and effort menu on every catalogue."""
    options = efforts_for(spec) or []
    return ModelCapabilities(
        wire=spec.wire,
        vision=spec.vision,
        reasoning=spec.reasoning,
        tools=spec.tools,
        context_window=spec.context_window,
        max_output=spec.max_output,
        can_disable_thinking=spec.can_disable_thinking,
        region_sensitive=spec.region_sensitive,
        free=spec.free,
        efforts=[EffortOption(**option) for option in options],
        effort_default=next((option["id"] for option in options if option.get("is_default")), None),
    )
