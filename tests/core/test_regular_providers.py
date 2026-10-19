"""Data registrations, protocol routing, subscription refresh and opaque history."""

import queue
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from hoshimi.core.chunks import aggregate_chat, aggregate_responses, emit_chat_chunks
from hoshimi.core.login import provider_oauth as oauth
from hoshimi.core.login.common import LoginError, LoginReporter
from hoshimi.core.provider_definitions import PROVIDER_BY_ID
from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account, RefreshTokenRejected
from hoshimi.core.providers.compat import provider_chat_body
from hoshimi.core.providers.configured import REGULAR_IDS
from hoshimi.core.providers.transports import request_payload
from hoshimi.core.translate.anthropic import build_anthropic_body
from hoshimi.core.translate.responses import responses_to_chat


def test_provider_flags_preserve_supported_fields_and_do_not_mutate_history():
    body = {"messages": [{"role": "developer", "content": "policy"}, {"role": "assistant", "content": "answer"}],
            "store": True, "reasoning_effort": "high", "max_completion_tokens": 50,
            "tools": [{"type": "function", "function": {"name": "read", "strict": True}}]}
    moonshot = provider_chat_body(body, "moonshotai", "m")
    assert moonshot["messages"][0]["role"] == "system"
    assert "store" not in moonshot and "reasoning_effort" not in moonshot
    assert "strict" not in moonshot["tools"][0]["function"]
    assert moonshot["max_tokens"] == 50
    standard = provider_chat_body(body, "openai", "m")
    assert standard["messages"][0]["role"] == "developer"
    assert standard["tools"][0]["function"]["strict"] is True
    assert standard["store"] is True and standard["reasoning_effort"] == "high"
    assert provider_chat_body(body, "deepseek", "m")["messages"][1]["reasoning_content"] == ""
    assert body["messages"][0]["role"] == "developer"
    assert "reasoning_content" not in body["messages"][1]


@pytest.mark.parametrize("provider", sorted(REGULAR_IDS))
def test_every_conventional_registration_can_send_its_declared_protocol(provider):
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, text='data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}\n\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}\n\ndata: {"type":"message_stop"}\n\n')
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"ok"}\n\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = PROVIDER_CLASSES[provider](client, [Account({"api_key": "secret", "models": [{"id": "m", "reasoning": True}]})])
        assert lane.models() == ["m"]
        assert {"type": "text_delta", "text": "ok"} in list(lane.stream_raw(lane.accounts[0], "m", {"messages": [{"role": "user", "content": "hello"}]}))
        assert str(seen[0].url).startswith(PROVIDER_BY_ID[provider].base_url)


def test_openrouter_uses_account_catalog_and_preserves_native_ids():
    def handle(request):
        assert request.url.path == "/api/v1/models/user"
        return httpx.Response(200, json={"data": [{"id": "vendor/a", "context_length": 9000}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = PROVIDER_CLASSES["openrouter"](client, [Account({"api_key": "key"})])
        assert lane.models() == ["vendor/a"]
        assert lane.specs()["vendor/a"].context_window == 9000


@pytest.mark.parametrize("provider", ["kimi-coding", "xai", "meta", "github-copilot"])
def test_device_flows_use_real_code_then_exchange_inference_credentials(provider, monkeypatch):
    monkeypatch.setattr(LoginReporter, "wait", lambda self, seconds: None)
    requests = []
    spec = oauth.DEVICE_OAUTH[provider]
    def handle(request):
        requests.append(request)
        if str(request.url) == spec.device_url:
            return httpx.Response(200, json={"device_code": "device", "user_code": "CODE", "verification_uri": "https://login.example/device", "expires_in": 60})
        if str(request.url) == spec.token_url:
            return httpx.Response(200, json={"access_token": "identity", "refresh_token": "refresh", "expires_in": 3600})
        if provider == "meta":
            assert request.headers["Authorization"] == "Bearer identity"
            return httpx.Response(200, json={"api_key": "inference"})
        return httpx.Response(200, json={"token": "inference", "expires_at": 1900000000})
    updates = []
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        credential = oauth.start_device_login(provider, client, LoginReporter(updates.append))
    assert updates[0]["user_code"] == "CODE"
    assert credential["access_token"] == ("inference" if provider in ("meta", "github-copilot") else "identity")
    assert len(requests) == (3 if provider in ("meta", "github-copilot") else 2)


def test_terminal_refresh_failure_and_unrotated_refresh_token():
    account = Account({"access_token": "old", "refresh_token": "refresh"})
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "invalid_grant"}))) as client, pytest.raises(RefreshTokenRejected):
        oauth.refresh_provider_account("xai", client, account)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "new", "expires_in": 3600}))) as client:
        assert oauth.refresh_provider_account("xai", client, account)
    assert account.get("refresh_token") == "refresh"


def test_signed_reasoning_survives_both_gateway_response_shapes():
    detail = {"type": "thinking", "thinking": "reason", "signature": "opaque"}
    chunks = [{"type": "reasoning_delta", "text": "reason"}, {"type": "reasoning_details", "details": [detail]}, {"type": "text_delta", "text": "answer"}]
    message, _, _ = aggregate_chat(emit_chat_chunks(chunks, model="m", completion_id="c", created=0))
    payload = build_anthropic_body({"messages": [message]}, model="m")
    assert payload["messages"][0]["content"][0] == detail
    response = aggregate_responses(chunks, model="m", response_id="resp_test", created=0)
    history = responses_to_chat({"input": response["output"]})
    assert history["messages"][0]["reasoning_details"] == [detail]
    native = {"type": "reasoning", "id": "rs", "encrypted_content": "opaque", "summary": []}
    assert request_payload({"messages": [{"role": "assistant", "reasoning_details": [native]}]}, "m", "responses")["input"] == [native]


def test_manual_code_wait_is_cancellable():
    inputs = queue.Queue()
    inputs.put("https://localhost/callback?code=code&state=state")
    reporter = LoginReporter(lambda values: None, inputs=inputs)
    assert reporter.authorization(None) == {"code": "code", "state": "state"}
    reporter.cancelled.set()
    with pytest.raises(LoginError, match="cancelled"):
        reporter.authorization(None)


@pytest.mark.parametrize("provider", ["openrouter", "anthropic", "openai"])
def test_browser_exchange_uses_pkce_and_openai_issued_client_id(provider, monkeypatch):
    class Callback:
        def __init__(self, path, **kwargs):
            self.path = path
        def redirect_uri(self):
            return "http://127.0.0.1:1455" + self.path
        def close(self):
            pass
    monkeypatch.setattr(oauth, "CallbackServer", Callback)
    monkeypatch.setattr(oauth, "_installation_id", lambda: "12345678-1234-1234-1234-123456789abc")
    inputs = queue.Queue()
    def update(values):
        if "login_url" in values:
            params = parse_qs(urlsplit(values["login_url"]).query)
            assert params["code_challenge_method"] == ["S256"]
            state = params.get("state", [""])[0]
            inputs.put("http://127.0.0.1:1455/auth/callback?code=code&state=" + state + "&client_id=issued")
    def handle(request):
        if provider == "openrouter":
            return httpx.Response(200, json={"key": "permanent"})
        if provider == "openai":
            assert b"client_id=issued" in request.content
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600, "scope": "chatgpt.tokens.use.direct"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        credential = oauth.start_browser_login(provider, client, LoginReporter(update, inputs=inputs))
    assert credential.get("api_key") == "permanent" if provider == "openrouter" else credential["access_token"] == "access"
