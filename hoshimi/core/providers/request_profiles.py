"""Immutable request policy for ordinary Chat Completions services."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Literal

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.providers.base import ProviderError


@dataclass(frozen=True)
class ChatProfile:
    max_tokens_field: str = "max_tokens"
    developer_role: str = "system"
    strict_tools: bool = True
    strip: tuple[str, ...] = ("store",)
    reasoning: Literal["passthrough", "none", "effort", "object", "thinking", "template", "llamacpp", "split"] = "passthrough"
    efforts: tuple[tuple[str, str], ...] = ()
    reasoning_delta: str = "reasoning_content"
    strip_names: bool = False
    tool_ids: Literal["preserve", "mistral"] = "preserve"
    off_effort: str | None = "none"

    def decorate(self, spec: ModelSpec) -> ModelSpec:
        """Declare a real wire control so the free-lane budget policy keeps it."""
        if not spec.reasoning or self.reasoning in ("none", "passthrough"):
            return spec
        if self.reasoning in ("template", "llamacpp", "thinking", "split"):
            levels = ("none", "high")
        else:
            mapped = dict(self.efforts)
            values = tuple(dict.fromkeys(mapped.get(level, level) for level in ("minimal", "low", "medium", "high", "xhigh", "max")))
            levels = (("none",) if self.off_effort is not None else ()) + values
        return replace(spec, efforts=levels, effort_default="high", effort_patch={"reasoning_effort": "$effort"},
                       effort_off_id="none" if "none" in levels else None,
                       effort_off_patch={"reasoning_effort": "none"} if "none" in levels else None)

    def prepare(self, body: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(body)
        ceiling = result.pop("max_completion_tokens", None)
        ceiling = result.pop("max_tokens", ceiling)
        if ceiling is not None:
            result[self.max_tokens_field] = ceiling
        for field in self.strip:
            result.pop(field, None)
        for message in result.get("messages") or []:
            if message.get("role") == "developer":
                message["role"] = self.developer_role
            if self.strip_names:
                message.pop("name", None)
        if not self.strict_tools:
            for tool in result.get("tools") or []:
                tool.pop("strict", None)
                if isinstance(tool.get("function"), dict):
                    tool["function"].pop("strict", None)
        effort = result.pop("reasoning_effort", None) if self.reasoning != "passthrough" else None
        if effort is not None:
            if self.reasoning == "thinking" and "thinking" in result:
                raise ProviderError(400, "thinking and reasoning_effort are mutually exclusive")
            mapped = dict(self.efforts).get(str(effort), effort)
            enabled = effort not in ("off", "none", "disabled")
            if self.reasoning == "effort":
                if enabled or self.off_effort is not None:
                    result["reasoning_effort"] = mapped if enabled else self.off_effort
            elif self.reasoning == "object":
                result["reasoning"] = {"effort": mapped} if enabled else {"enabled": False}
            elif self.reasoning == "thinking":
                result["thinking"] = {"type": "enabled" if enabled else "disabled"}
            elif self.reasoning in ("template", "llamacpp"):
                template = result.setdefault("chat_template_kwargs", {})
                if not isinstance(template, dict):
                    raise ProviderError(400, "chat_template_kwargs must be an object")
                if "enable_thinking" in template:
                    raise ProviderError(400, "template thinking and reasoning_effort are mutually exclusive")
                template["enable_thinking"] = enabled
                if self.reasoning == "llamacpp":
                    template["thinking"] = enabled
            elif self.reasoning == "split":
                result["reasoning_split"] = True
        return result


LIMITED_EFFORTS = (("minimal", "low"), ("low", "low"), ("medium", "medium"), ("high", "high"), ("xhigh", "high"), ("max", "high"))
CHAT_PROFILES: dict[str, ChatProfile] = {
    "lmstudio": ChatProfile(reasoning="effort", efforts=LIMITED_EFFORTS),
    "llamacpp": ChatProfile(reasoning="llamacpp"),
    "ollama": ChatProfile(reasoning="effort", efforts=(("minimal", "low"), ("xhigh", "max"))),
    "cline-pass": ChatProfile(reasoning="none"),
    "minimax-chat": ChatProfile(max_tokens_field="max_completion_tokens", reasoning="split"),
    "bedrock-mantle": ChatProfile(reasoning="none"),
    "vercel-ai-gateway": ChatProfile(reasoning="object", efforts=(("max", "xhigh"),)),
    "xai": ChatProfile(reasoning="none", developer_role="developer"),
    "alibaba-cloud": ChatProfile(reasoning="none"),
    "qwen-coding-plan-intl": ChatProfile(reasoning="none"),
    "siliconflow": ChatProfile(reasoning="none"),
    "nebius": ChatProfile(reasoning="effort", efforts=(("max", "xhigh"),)),
    "chutes": ChatProfile(reasoning="none"),
    "featherless": ChatProfile(reasoning="template"),
    "wandb": ChatProfile(max_tokens_field="max_completion_tokens", reasoning="template", reasoning_delta="reasoning"),
    "mistral-codestral": ChatProfile(reasoning="none", tool_ids="mistral"),
    "cohere": ChatProfile(reasoning="effort", efforts=tuple((e, "high") for e in ("minimal", "low", "medium", "high", "xhigh", "max")), strip_names=True,
        strip=("audio", "logit_bias", "metadata", "modalities", "n", "parallel_tool_calls", "prediction", "service_tier", "store", "top_logprobs")),
    "wafer": ChatProfile(reasoning="effort", efforts=(("minimal", "low"), ("xhigh", "max"))),
    "zai-api": ChatProfile(reasoning="thinking"),
    "nararoute": ChatProfile(reasoning="effort", efforts=LIMITED_EFFORTS),
    "poolside": ChatProfile(reasoning="template"),
    "scaleway": ChatProfile(reasoning="none"),
    "lightning": ChatProfile(reasoning="effort", efforts=LIMITED_EFFORTS),
    "experiential": ChatProfile(reasoning="effort", efforts=LIMITED_EFFORTS),
    "cheaperinference": ChatProfile(reasoning="effort", efforts=LIMITED_EFFORTS),
    "atlascloud": ChatProfile(reasoning="effort", efforts=(("minimal", "high"), ("low", "high"), ("medium", "high"), ("max", "xhigh")), off_effort=None),
    "opper": ChatProfile(reasoning="effort", efforts=LIMITED_EFFORTS),
}
