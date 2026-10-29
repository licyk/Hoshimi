"""NVIDIA wire contract and account integration, with no paid requests."""

import copy
import json

import httpx
import pytest

from hoshimi.core.errors import ValidationError
from hoshimi.core.providers import build_providers
from hoshimi.core.providers.base import Account, AuthFailed, RequestThrottled
from hoshimi.core.providers.nvidia import NvidiaProvider


def test_nim_preserves_ids_capabilities_and_normalizes_requests():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer secret"
        if request.method == "GET":
            return httpx.Response(200, json={"data": [
                {"id": "Vendor/Model_With.Case", "context_length": 32000, "max_output_tokens": 4096, "vision": True, "reasoning": True},
                {"id": "embedding", "type": "embedding"},
                {"id": "nvidia/nv-embedqa-mistral-7b-v2"},
            ]})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"reasoning_content":"think","tool_calls":[{"index":0,"id":"t","function":{"name":"read","arguments":"{}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\ndata: [DONE]\n\n')

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = build_providers(client, {"nvidia": [{"api_key": "secret"}]})["nvidia"]
        assert lane.models() == ["Vendor/Model_With.Case"]
        spec = lane.specs()[lane.models()[0]]
        assert (spec.context_window, spec.max_output, spec.vision, spec.reasoning, spec.free) == (32000, 4096, True, True, None)
        body = {"messages": [{"role": "developer", "content": "rules"}], "max_completion_tokens": 8, "store": True,
                "reasoning_effort": "high", "tools": [{"type": "function", "function": {"name": "read", "strict": True}}]}
        original = copy.deepcopy(body)
        chunks = list(lane.stream_raw(lane.accounts[0], lane.models()[0], body))
        assert body == original
        sent = json.loads(seen[-1].content)
        assert sent["model"] == "Vendor/Model_With.Case"
        assert sent["messages"][0]["role"] == "system"
        assert sent["max_tokens"] == 8
        assert not {"store", "reasoning_effort", "max_completion_tokens"} & sent.keys()
        assert "strict" not in sent["tools"][0]["function"]
        assert {chunk["type"] for chunk in chunks} == {"reasoning_delta", "tool_call_start", "usage", "finish"}


@pytest.mark.parametrize("status,error", [(401, AuthFailed), (429, RequestThrottled)])
def test_nim_refusals_use_the_existing_error_taxonomy(status, error):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text="refused"))) as client:
        lane = NvidiaProvider(client, [Account({"api_key": "key"})])
        with pytest.raises(error):
            list(lane.stream_raw(lane.accounts[0], "model", {"messages": []}))


def test_nim_rejects_empty_credentials_before_save():
    with pytest.raises(ValidationError):
        NvidiaProvider.check_account({"api_key": " "})
