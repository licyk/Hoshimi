"""Provider codes keep their meaning across HTTP and streaming error envelopes."""

import json

import httpx
import pytest

from hoshimi.core.providers.base import Account, AuthFailed, ContextOverflow, ModelGone, ProviderError, QuotaExceeded, RequestThrottled
from hoshimi.core.providers.minimax import MinimaxProvider
from hoshimi.core.providers.openai_compat import raise_for_status, read_body
from hoshimi.core.providers.refusals import provider_refusal
from hoshimi.core.providers.trae import TraeProvider
from hoshimi.core.translate.anthropic import iter_anthropic_chunks


@pytest.mark.parametrize(("provider", "code", "expected"), [
    ("minimax-chat", 1002, RequestThrottled), ("minimax-chat", 1039, RequestThrottled),
    ("minimax-chat", 1041, RequestThrottled), ("minimax-chat", 2056, QuotaExceeded),
    ("minimax-chat", 1008, QuotaExceeded), ("minimax-chat", 2049, AuthFailed),
    ("minimax-chat", 2069, ModelGone), ("minimax-chat", 2013, ProviderError),
    ("zhipu", "1302", RequestThrottled), ("zai-api", "1305", RequestThrottled),
    ("zai-coding-cn", "1308", QuotaExceeded), ("zhipu", "1113", QuotaExceeded),
    ("zhipu", "1003", AuthFailed), ("zhipu", "1211", ModelGone),
    ("zhipu", "1261", ContextOverflow), ("zhipu", "1313", ProviderError),
    ("trae", 4011, RequestThrottled), ("trae", 4008, QuotaExceeded),
    ("qoder", "105", AuthFailed), ("qoder", "110", QuotaExceeded),
    ("zcode", 1002, AuthFailed), ("zcode", 3012, ProviderError),
    ("zcode", 3007, ProviderError), ("loomy", "402", QuotaExceeded),
    ("raccoon", 402, QuotaExceeded), ("lobsterai", 40101, AuthFailed),
])
@pytest.mark.parametrize("streamed", [False, True])
def test_business_code_precedes_http_status(provider, code, expected, streamed):
    payload = {"base_resp": {"status_code": code, "status_msg": "refused"}} if provider.startswith("minimax") else {"error": {"code": code, "message": "refused"}}
    with pytest.raises(expected) as caught:
        if streamed:
            list(read_body(httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n", headers={"retry-after": "60"}), provider=provider))
        else:
            raise_for_status(httpx.Response(403, json=payload, headers={"retry-after": "60"}), provider=provider)
    assert type(caught.value) is expected
    assert caught.value.retry_after == 60
    assert caught.value.detail["upstream_type"]
    if expected in (QuotaExceeded, RequestThrottled):
        assert caught.value.status == 429


def test_same_numeric_code_has_different_provider_meanings():
    payload = {"error": {"code": 1002, "message": "refused"}}
    assert isinstance(provider_refusal("zcode", payload), AuthFailed)
    assert isinstance(provider_refusal("minimax", payload), RequestThrottled)
    assert provider_refusal("unrelated", payload) is None
    assert provider_refusal("zcode", {"error": {"code": 13012, "message": "refused"}}) is None


def test_qoder_nested_queue_respects_longest_server_wait():
    error = provider_refusal("qoder", {"code": 10605, "message": json.dumps({"retryAfterMs": 30000})}, retry_after=2)
    assert isinstance(error, RequestThrottled)
    assert error.retry_after == 30


@pytest.mark.parametrize("provider", ["lobsterai", "raccoon", "loomy"])
def test_credit_message_is_not_an_authentication_failure(provider):
    error = provider_refusal(provider, {"error": {"message": "余额不足"}}, status=403)
    assert isinstance(error, QuotaExceeded)
    assert error.scope == "account"


def test_minimax_success_status_and_unknown_error():
    success = {"base_resp": {"status_code": 0, "status_msg": "success"}, "choices": [{"delta": {"content": "rate limit"}}]}
    assert provider_refusal("minimax-chat", success) is None
    chunks = list(read_body(httpx.Response(200, json=success), provider="minimax-chat"))
    assert chunks[0]["text"] == "rate limit"
    error = provider_refusal("minimax-chat", {"base_resp": {"status_code": 9999, "status_msg": "unknown failure"}})
    assert error is not None and error.should_retry is False


@pytest.mark.parametrize(("cls", "frame"), [
    (MinimaxProvider, 'data: {"base_resp":{"status_code":2056,"status_msg":"usage limit exceeded"}}\n\n'),
    (TraeProvider, 'event: error\ndata: {"code":4008,"message":"credit exhausted"}\n\n'),
])
def test_native_station_streams_reach_shared_cooldown(cls, frame):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text=frame, headers={"retry-after": "60"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = cls(client, [Account({"id": "a", "access_token": "keep"})])
        with pytest.raises(QuotaExceeded):
            list(provider.stream("model", {}))
        assert len(calls) == 1
        assert provider.cooler.blocked(provider.id, "a", model="other")
        assert not provider.accounts[0].needs_login
        assert provider.admission.active == 0


def test_named_anthropic_rate_error_reaches_shared_parser():
    lines = ['data: {"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}', ""]
    with pytest.raises(RequestThrottled) as caught:
        list(iter_anthropic_chunks(lines, provider="anthropic", retry_after=30))
    assert caught.value.scope == "account"
    assert caught.value.retry_after == 30
