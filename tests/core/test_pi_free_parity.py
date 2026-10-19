"""Protocol differences that cannot be covered by a URL preset alone."""

import json

import httpx

from hoshimi.core.login.common import LoginReporter
from hoshimi.core.login.kilo import start_kilo_login
from hoshimi.core.providers.agg import AggProvider
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.kilo import KiloProvider


def test_kilo_authentication_keeps_paid_entries_and_anonymous_stays_free():
    with httpx.Client() as client:
        lane = KiloProvider(client, [Account({"api_key": "key"})])
        assert lane.build_headers(lane.accounts[0], "x")["Authorization"] == "Bearer key"
        assert lane.parse_models_response({"data": [{"id": "paid", "isFree": False}, {"id": "free", "isFree": True}]}) == ["paid", "free"]
        lane._models_cache = ["paid", "free"]
        lane._models_expires_at = float("inf")
        assert lane.specs()["paid"].free is False


def test_commandcode_claude_reaches_messages():
    def handle(request):
        assert request.url.path == "/provider/v1/messages"
        body = json.loads(request.content)
        assert body["system"] == "rules"
        return httpx.Response(200, text='event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\nevent: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = AggProvider(client, [Account({"preset": "commandcode", "api_key": "key"})])
        chunks = list(lane.stream_raw(lane.accounts[0], "anthropic/claude-sonnet", {"messages": [{"role": "system", "content": "rules"}]}))
        assert {"type": "text_delta", "text": "hello"} in chunks


def test_merge_pagination_and_free_price_require_one_real_free_route():
    requests = []
    route = {"availability_status": "available", "context_window": 64000, "capabilities": {"input": ["text", "image"], "output": ["text"]}}

    def handle(request):
        requests.append(request)
        assert request.url.path == "/v1/models"
        if request.url.params.get("cursor"):
            return httpx.Response(200, json={"data": [{"model": "two", "vendors": {"free": {**route, "pricing": {"input_per_million": 0, "output_per_million": 0}}}}]})
        return httpx.Response(200, json={"data": [{"model": "one", "vendors": {
            "a": {**route, "pricing": {"input_per_million": 0, "output_per_million": 2}},
            "b": {**route, "pricing": {"input_per_million": 2, "output_per_million": 0}}}}], "has_more": True, "next_cursor": "next"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = AggProvider(client, [Account({"preset": "merge", "api_key": "key"})])
        assert lane.models() == ["one", "two"]
        assert lane.specs()["one"].free is False
        assert lane.specs()["two"].free is True
        assert lane.specs()["one"].vision is True
        assert lane.specs()["one"].context_window == 64000
        assert len(requests) == 2


def test_ollama_capabilities_do_not_invent_free_prices():
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "cloud-model"}]})
        assert request.url.path == "/api/show"
        return httpx.Response(200, json={"capabilities": ["completion", "vision", "thinking", "tools"], "model_info": {"test.context_length": 8000}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = AggProvider(client, [Account({"preset": "ollama-cloud", "api_key": "key"})])
        spec = lane.specs()["cloud-model"]
        assert (spec.reasoning, spec.vision, spec.context_window, spec.free) == (True, True, 8000, None)


def test_kilo_device_login_reports_code_and_returns_a_key(monkeypatch):
    monkeypatch.setattr("hoshimi.core.login.kilo.poll_pause", lambda seconds: None)
    events = []
    def handle(request):
        if request.method == "POST":
            return httpx.Response(200, json={"code": "CODE", "verificationUrl": "https://app.kilo.ai/device", "expiresIn": 60})
        return httpx.Response(200, json={"status": "approved", "token": "secret"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        credential = start_kilo_login(client, LoginReporter(events.append))
    assert credential == {"access_token": "secret"}
    assert events[0]["user_code"] == "CODE"
