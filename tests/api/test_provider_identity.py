"""Public protocol entries preserve identity through lazy provider iteration."""

import json
import time

import httpx
import pytest

from hoshimi.api.playground import PlaygroundRunner, Run
from hoshimi.core.net.identity import identity
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.opencode import OpencodeProvider


def install_provider(services):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    provider = OpencodeProvider(httpx.Client(transport=httpx.MockTransport(handle)), [Account({"api_key": "upstream-key"})])
    provider._models_cache = ["deepseek-v4.1-flash-free"]
    provider._models_expires_at = time.time() + 60
    services.registry.providers["opencode"] = provider
    return provider, requests


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses", "messages"])
@pytest.mark.parametrize("stream", [False, True])
def test_client_session_survives_all_protocol_entries(client, services, endpoint, stream):
    provider, requests = install_provider(services)
    body = {"model": "opencode/deepseek-v4.1-flash-free", "stream": stream, "max_tokens": 32}
    body.update({"input": "hello"} if endpoint == "responses" else {"messages": [{"role": "user", "content": "hello"}]})
    try:
        response = client.post("/v1/" + endpoint, json=body, headers={
            "x-opencode-session": "ses_original_client_session", "user-agent": "opencode/test/cli", "x-opencode-project": "project-one",
        })
        assert response.status_code == 200, response.text
        assert requests[0].url.path == "/inference/openai/v1/chat/completions"
        assert requests[0].headers["x-opencode-session"] == "ses_original_client_session"
        assert requests[0].headers["user-agent"] == "opencode/test/cli"
        assert requests[0].headers["x-opencode-project"] == "project-one"
        assert requests[0].headers["authorization"] == "Bearer upstream-key"
        assert "prompt_cache_key" not in json.loads(requests[0].content)
        assert identity.get().session == ""
    finally:
        provider.client.close()


def test_playground_sessions_stay_distinct_and_survive_compaction(services):
    provider, requests = install_provider(services)
    runner = PlaygroundRunner(services)
    model = "opencode/deepseek-v4.1-flash-free"
    try:
        for conversation, text in [("one", "hello"), ("two", "hello"), ("one", "compressed history")]:
            list(runner._stream(model, {"messages": [{"role": "user", "content": text}]}, Run(conversation_id=conversation)))
        sessions = [request.headers["x-opencode-session"] for request in requests]
        assert sessions[0] != sessions[1]
        assert sessions[0] == sessions[2]
        assert all("prompt_cache_key" not in json.loads(request.content) for request in requests)
    finally:
        provider.client.close()


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses", "messages"])
def test_free_usage_wait_reaches_api_caller(client, services, endpoint):
    provider, _requests = install_provider(services)
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403, json={
        "type": "error", "error": {"type": "FreeUsageLimitError", "message": "Free usage limit reached"},
    }, headers={"retry-after": "60"})))
    body = {"model": "opencode/deepseek-v4.1-flash-free", "stream": False, "max_tokens": 32}
    body.update({"input": "hello"} if endpoint == "responses" else {"messages": [{"role": "user", "content": "hello"}]})
    try:
        response = client.post("/v1/" + endpoint, json=body)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        assert response.json()["error"]["upstream_type"] == "FreeUsageLimitError"
        assert response.json()["error"]["scope"] == "account_model"
    finally:
        provider.client.close()
