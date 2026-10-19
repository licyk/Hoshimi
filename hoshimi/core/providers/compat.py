"""Small request-shaping rules shared by OpenAI-compatible gateways."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def gateway_chat_body(body: dict[str, Any], *, strict_tools: bool = True) -> dict[str, Any]:
    """Gateways use system roles; preserve history, tools and reasoning data."""
    out = deepcopy(body)
    ceiling = out.pop("max_completion_tokens", None)
    if ceiling is not None:
        out.setdefault("max_tokens", ceiling)
    for message in out.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "developer":
            message["role"] = "system"
    if not strict_tools:
        for tool in out.get("tools") or []:
            if isinstance(tool, dict):
                tool.pop("strict", None)
                if isinstance(tool.get("function"), dict):
                    tool["function"].pop("strict", None)
    return out


def provider_chat_body(body: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    """Pi's explicit compatibility flags for conventional Chat Completions."""
    nonstandard = {"ant-ling", "cerebras", "deepseek", "moonshotai", "moonshotai-cn", "together", "zai", "zai-coding-cn",
                   "cloudflare-workers-ai", "cloudflare-ai-gateway", "opencode-go"}
    no_effort = {"ant-ling", "moonshotai", "moonshotai-cn", "together", "zai", "zai-coding-cn", "cloudflare-ai-gateway"}
    no_strict = {"cerebras", "moonshotai", "moonshotai-cn", "together", "cloudflare-ai-gateway"}
    out = gateway_chat_body(body, strict_tools=provider not in no_strict)
    if provider not in nonstandard and (provider != "openrouter" or model.startswith(("anthropic/", "openai/"))):
        out["messages"] = deepcopy(body.get("messages") or [])
    if provider in nonstandard:
        out.pop("store", None)
    if provider in no_effort:
        out.pop("reasoning_effort", None)
    if provider == "deepseek":
        for message in out.get("messages") or []:
            if message.get("role") == "assistant":
                message.setdefault("reasoning_content", "")
    return out
