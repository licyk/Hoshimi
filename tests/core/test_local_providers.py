"""Local endpoints are opt-in and keep each server's authentication and metadata."""

import json

import httpx
import pytest

from hoshimi.core.providers import PROVIDER_CLASSES, build_providers
from hoshimi.core.providers.base import Account, ProviderError


def test_local_backends_never_boot_or_probe_without_explicit_accounts():
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("implicit local request"))) as client:
        assert not {"lmstudio", "llamacpp", "ollama"}.intersection(build_providers(client, {}))
        for provider in ("lmstudio", "llamacpp", "ollama"):
            assert provider in build_providers(client, {provider: [{"id": "local"}]})
            assert provider not in build_providers(client, {provider: [{"id": "local"}]}, disabled={provider})


@pytest.mark.parametrize("provider", ["lmstudio", "llamacpp", "ollama"])
@pytest.mark.parametrize("key", [None, "real-key"])
def test_local_chat_can_omit_auth_or_use_a_real_key(provider, key):
    def handle(request):
        assert request.url.host == "local.example" and request.url.path == "/v1/chat/completions"
        assert request.headers.get("authorization") == ("Bearer real-key" if key else None)
        body = json.loads(request.content)
        if provider == "llamacpp":
            assert body["chat_template_kwargs"]["enable_thinking"] is True
        return httpx.Response(200, text="data: [DONE]\n\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        account = Account({"base_url": "http://local.example", **({"api_key": key} if key else {})})
        lane = PROVIDER_CLASSES[provider](client, [account])
        if provider == "lmstudio":
            lane._loaded[account.id] = (float("inf"), {})
        list(lane.stream_raw(account, "m", {"messages": [], "reasoning_effort": "high"}))


def test_ollama_native_catalog_preserves_names_and_capabilities():
    def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "model:latest"}]})
        assert request.url.path == "/api/show"
        return httpx.Response(200, json={"capabilities": ["completion", "tools", "vision", "thinking"], "model_info": {"qwen.context_length": 12345}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = PROVIDER_CLASSES["ollama"](client, [Account({})])
        assert lane.models() == ["model:latest"]
        spec = lane.specs()["model:latest"]
        assert spec.tools and spec.vision and spec.reasoning and spec.context_window == 12345
        assert spec.free is None


def test_lmstudio_loaded_context_is_per_model_and_rejects_obvious_overflow():
    def handle(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "small"}, {"id": "large"}]})
        assert request.url.path == "/api/v0/models"
        return httpx.Response(200, json={"data": [{"id": "small", "state": "loaded", "loaded_context_length": 100}, {"id": "large", "state": "loaded", "loaded_context_length": 10000}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = PROVIDER_CLASSES["lmstudio"](client, [Account({})])
        assert lane.specs()["small"].context_window == 100
        assert lane.specs()["large"].context_window == 10000
        with pytest.raises(ProviderError, match="loaded context"):
            list(lane.stream_raw(lane.accounts[0], "small", {"messages": [{"role": "user", "content": "x" * 1000}]}))
