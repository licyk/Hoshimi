"""Refusals must act on the right model and preserve the server's waiting period."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from hoshimi.core.net.admission import AdmissionDelayed, ProviderAdmission, admission_options
from hoshimi.core.net.identity import InferenceIdentity, identified_chunks, identity
from hoshimi.core.providers.base import Account, ProviderError, QuotaExceeded, RequestThrottled
from hoshimi.core.providers.cline import ClineProvider
from hoshimi.core.providers.openai_compat import consume_openai_sse, raise_for_status, read_body
from hoshimi.core.providers.opencode import OpencodeProvider
from hoshimi.core.providers.qoder import QoderProvider
from hoshimi.core.providers.zcode import ZcodeProvider
from hoshimi.core.translate.anthropic import iter_anthropic_chunks
from hoshimi.core.translate.responses import iter_responses_chunks

FREE_ERROR = {"type": "error", "error": {"type": "FreeUsageLimitError", "message": "Free usage limit reached. Free models work in OpenCode."}}
SUCCESS = 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


@pytest.mark.parametrize("status", [401, 403, 429])
def test_free_usage_type_precedes_http_authentication(status):
    with pytest.raises(QuotaExceeded) as caught:
        raise_for_status(httpx.Response(status, json=FREE_ERROR, headers={"retry-after": "123"}), provider="opencode")
    assert caught.value.status == 429
    assert caught.value.detail["upstream_type"] == "FreeUsageLimitError"
    assert caught.value.scope == "account_model"
    assert caught.value.retry_after == 123


@pytest.mark.parametrize("consume", [consume_openai_sse, iter_responses_chunks, iter_anthropic_chunks])
def test_streaming_error_keeps_type_and_wait(consume):
    with pytest.raises(QuotaExceeded) as caught:
        list(consume(["data: " + json.dumps(FREE_ERROR), ""], provider="opencode", retry_after=15))
    assert caught.value.retry_after == 15
    assert caught.value.detail["upstream_type"] == "FreeUsageLimitError"


@pytest.mark.parametrize("streamed", [False, True])
def test_free_usage_holds_only_failed_model_and_keeps_credentials(streamed):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content)["model"])
        if requests[-1] == "limited-free":
            if streamed:
                return httpx.Response(200, text="data: " + json.dumps(FREE_ERROR) + "\n\n", headers={"retry-after": "60"})
            return httpx.Response(403, json=FREE_ERROR, headers={"retry-after": "60"})
        return httpx.Response(200, text=SUCCESS)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        account = Account({"id": "a", "api_key": "key"})
        provider = OpencodeProvider(client, [account])
        for _ in range(2):
            with pytest.raises(QuotaExceeded):
                list(provider.stream("limited-free", {}))
        chunks = list(provider.stream("healthy-free", {}))
        assert chunks[0]["text"] == "ok"
        assert requests == ["limited-free", "healthy-free"]
        assert not account.needs_login
        assert provider.cooler.snapshot()[0]["model"] == "limited-free"
        assert provider.admission.active == 0


def test_partial_stream_is_never_replayed():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                              + "data: " + json.dumps(FREE_ERROR) + "\n\n")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = OpencodeProvider(client, [Account({"api_key": "one"}), Account({"api_key": "two"}, index=1)])
        chunks = provider.stream("model-free", {})
        assert next(chunks)["text"] == "partial"
        with pytest.raises(QuotaExceeded):
            list(chunks)
        assert len(calls) == 1
        assert provider.admission.active == 0


def test_zcode_concurrency_retries_without_disabling_account():
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"code": 3009, "msg": "model concurrency limit exceeded"}, headers={"retry-after": "0"})
        return httpx.Response(200, text='event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n'
                              'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = ZcodeProvider(client, [Account({"id": "a"})])
        chunks = list(provider.stream("GLM-5.3", {}))
        assert chunks[0]["text"] == "ok"
        assert len(calls) == 2
        assert provider.cooler.snapshot() == []


def test_cline_daily_duration_and_model_scope():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, json={
        "error": {"code": "INFERENCE_CAP_ERROR", "message": "Daily free limit reached. Try again in 19h 39m"},
    }))) as client:
        provider = ClineProvider(client, [Account({"id": "a"})])
        with pytest.raises(QuotaExceeded) as caught:
            list(provider.stream("limited", {}))
        assert caught.value.retry_after == 19 * 3600 + 39 * 60
        assert provider.cooler.blocked("cline", "a", model="limited")
        assert not provider.cooler.blocked("cline", "a", model="other")


@pytest.mark.parametrize("streamed", [False, True])
def test_qoder_queue_does_not_refresh_or_retry_before_server_delay(streamed):
    calls = []
    payload = {"code": "10605", "message": json.dumps({"isQueued": True, "retryAfterSeconds": 30})}

    def handle(request):
        calls.append(request)
        return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n") if streamed else httpx.Response(403, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = QoderProvider(client, [Account({"id": "a", "refresh_token": "keep"})])
        with pytest.raises(RequestThrottled) as caught:
            list(provider.stream("model", {}))
        assert caught.value.retry_after == 30
        assert caught.value.detail["upstream_type"] == "model_queued"
        assert len(calls) == 1
        assert provider.cooler.snapshot() == []


def test_concurrency_slot_lasts_until_stream_close():
    gate = ProviderAdmission({"max_concurrency": 1, "queue_timeout_seconds": 1})
    gate.acquire(("a", "m"))
    waiting = threading.Event()
    admitted = threading.Event()

    def second():
        waiting.set()
        gate.acquire(("b", "m"))
        admitted.set()
        gate.release()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(second)
        assert waiting.wait(1)
        assert not admitted.wait(0.02)
        gate.release()
        future.result(timeout=2)
    assert admitted.is_set() and gate.active == 0


def test_long_cooldown_is_shared_and_not_clamped():
    gate = ProviderAdmission({"queue_timeout_seconds": 0})
    gate.defer(None, 60)
    with pytest.raises(AdmissionDelayed) as caught:
        gate.acquire(("another-account", "model"))
    assert 59 < caught.value.seconds <= 60


@pytest.mark.parametrize("raw", [{"max_concurrency": 0}, {"max_concurrency": True}, {"queue_timeout_seconds": -1}, {"min_interval_seconds": float("nan")}])
def test_invalid_admission_options(raw):
    with pytest.raises(ValueError):
        admission_options(raw)


def test_interleaved_identity_and_cleanup_do_not_leak():
    seen = []

    def source():
        try:
            yield {"session": identity.get().session}
            yield {"session": identity.get().session}
        finally:
            seen.append(identity.get().session)

    first = identified_chunks(source(), InferenceIdentity(session="one"))
    second = identified_chunks(source(), InferenceIdentity(session="two"))
    assert next(first)["session"] == "one"
    assert next(second)["session"] == "two"
    assert next(first)["session"] == "one"
    assert identity.get().session == ""
    first.close()
    second.close()
    assert seen == ["one", "two"]


def test_catalog_protocol_overrides_static_guess():
    with httpx.Client() as client:
        provider = OpencodeProvider(client, [Account({})])
        provider.parse_models_response({"data": [{"id": "new-free", "api": {"responses": {}}}]})
        assert provider.model_wire("new-free") == "responses"
        provider.options["model_protocols"] = {"new-free": "messages"}
        assert provider.model_wire("new-free") == "messages"
        provider.options["model_protocols"] = {"new-free": "unsupported"}
        with pytest.raises(ProviderError):
            provider.model_wire("new-free")


@pytest.mark.parametrize("prefix", [": heartbeat\n\n", "event: error\n", "id: frame-one\n"])
def test_named_sse_and_heartbeats_keep_typed_refusals(prefix):
    response = httpx.Response(200, text=prefix + "data: " + json.dumps(FREE_ERROR) + "\n\n")
    with pytest.raises(QuotaExceeded):
        list(read_body(response, provider="opencode"))
