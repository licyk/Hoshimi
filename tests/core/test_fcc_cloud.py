"""FCC service identities and HTTP behavior, without importing the reference app."""

import json

import httpx
import pytest

from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account

NEW_CLOUD = ("alibaba-cloud", "qwen-coding-plan-intl", "siliconflow", "nebius", "chutes", "featherless", "wandb", "mistral-codestral", "cohere", "wafer", "zai-api", "nararoute", "poolside", "scaleway", "lightning", "experiential", "cheaperinference", "atlascloud", "opper")


@pytest.mark.parametrize("provider", NEW_CLOUD)
def test_new_cloud_service_sends_its_own_key_and_keeps_upstream_model_id(provider):
    def handle(request):
        assert request.headers["authorization"] == "Bearer account-key"
        payload = json.loads(request.content)
        assert payload["model"] == "upstream/vendor/model"
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = PROVIDER_CLASSES[provider](client, [Account({"api_key": "account-key", "models": ["upstream/vendor/model"]})])
        assert lane.models() == ["upstream/vendor/model"]
        assert lane.specs()["upstream/vendor/model"].free is None
        assert {"type": "text_delta", "text": "ok"} in list(lane.stream_raw(lane.accounts[0], "upstream/vendor/model", {"messages": [{"role": "user", "content": "hello"}]}))


def test_dashscope_directory_uses_native_pagination_and_capability_fields():
    def handle(request):
        assert request.url.path == "/api/v1/models"
        page = int(request.url.params["page_no"])
        row = {"model": f"qwen-{page}", "capabilities": ["TG", "Reasoning"], "features": ["function-calling"], "model_info": {"context_window": 12345}}
        return httpx.Response(200, json={"success": True, "output": {"total": 2, "page_no": page, "models": [row]}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = PROVIDER_CLASSES["alibaba-cloud"](client, [Account({"api_key": "key"})])
        assert lane.models() == ["qwen-1", "qwen-2"]
        assert lane.specs()["qwen-1"].tools is True
        assert lane.specs()["qwen-1"].context_window == 12345


def test_featherless_keeps_chat_models_without_tools_and_rejects_wrong_plan():
    payload = {"data": [{"id": "chat-only", "available_on_current_plan": True, "is_gated": False, "features": {"tool_use": False}}, {"id": "other-plan", "available_on_current_plan": False, "is_gated": False}], "pagination": {"current_page": 1, "total_pages": 1}}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        lane = PROVIDER_CLASSES["featherless"](client, [Account({"api_key": "key"})])
        assert lane.models() == ["chat-only"]
        assert lane.specs()["chat-only"].tools is False
