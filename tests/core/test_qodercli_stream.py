"""The Qoder agent-chat response: envelopes, thinking artefacts, and recovery.

The DSML markers are assembled from fragments so the test file itself carries no
literal markup that a reader (or a tool) could mistake for its own instructions.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hoshimi.core.providers.base import Account, ProviderError, RequestThrottled
from hoshimi.core.providers.qodercli import QoderCliCnProvider
from hoshimi.core.qoder import stream
from hoshimi.core.qoder.region import CN

CLOSE_INVOKE = "<" + "/invoke>"
CLOSE_PARAMETER = "<" + "/parameter>"
CLOSE_DSML = "<" + "/DSML:function_calls>"


def envelope(body: Any, status: int = 200) -> str:
    payload = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return "data:" + json.dumps({"headers": {}, "body": payload, "statusCodeValue": status, "statusCode": "OK"})


def chunk(delta: dict[str, Any], **extra: Any) -> str:
    return envelope({"id": "chatcmpl-1", "model": "lite", "choices": [{"index": 0, "delta": delta, **extra}]})


def lines(*frames: str) -> list[str]:
    out: list[str] = []
    for frame in frames:
        out.extend([frame, ""])
    return out


def text_of(chunks: list[dict[str, Any]], kind: str = "text_delta") -> str:
    return "".join(c["text"] for c in chunks if c["type"] == kind)


# -- the happy path ---------------------------------------------------------- #
def test_envelopes_become_normalized_chunks_with_credits() -> None:
    usage = {
        "prompt_tokens": 19, "completion_tokens": 2, "total_tokens": 21,
        "credits": 0.000613272, "original_credits": 0.001, "billable": True,
        "prompt_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 1},
    }
    frames = lines(
        chunk({"role": "assistant", "content": "Hello"}),
        chunk({"content": "!"}),
        chunk({"content": ""}, finish_reason="stop"),
        envelope({"id": "chatcmpl-1", "choices": [], "usage": usage}),
        envelope("[DONE]"),
        "event:finish",
        'data:{"firstTokenDuration":487}',
    )
    chunks = list(stream.iter_agent_chunks(frames, provider="qodercli"))
    assert [c["text"] for c in chunks if c["type"] == "text_delta"] == ["Hello", "!"]
    reported = next(c for c in chunks if c["type"] == "usage")["usage"]
    assert reported["prompt_tokens"] == 19
    assert reported["prompt_tokens_details"] == {"cached_tokens": 4, "cache_write_tokens": 1}
    assert reported["credits"] == 0.000613272 and reported["charged_credits"] == 0.000613272
    assert chunks[-1] == {"type": "finish", "reason": "stop"}


def test_non_billable_requests_charge_zero() -> None:
    frames = lines(chunk({"content": "hi"}), envelope({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "credits": 0.005, "billable": False}}), envelope("[DONE]"))
    reported = next(c for c in stream.iter_agent_chunks(frames, provider="qodercli") if c["type"] == "usage")["usage"]
    assert reported["credits"] == 0.005 and reported["charged_credits"] == 0


def test_the_wrapped_done_sentinel_ends_the_stream() -> None:
    frames = lines(chunk({"content": "hi"}), envelope("[DONE]"), chunk({"content": "never read"}))
    assert text_of(list(stream.iter_agent_chunks(frames, provider="qodercli"))) == "hi"


def test_tool_call_fragments_accumulate_into_one_call() -> None:
    frames = lines(
        chunk({"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "get_weather", "arguments": '{"city":'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "Paris"}'}}]}),
        chunk({"content": ""}, finish_reason="tool_calls"),
        envelope("[DONE]"),
    )
    chunks = list(stream.iter_agent_chunks(frames, provider="qodercli"))
    call = next(c for c in chunks if c["type"] == "tool_call_start")
    assert call["id"] == "call_1" and call["name"] == "get_weather"
    assert json.loads(call["arguments"]) == {"city": "Paris"}
    assert chunks[-1] == {"type": "finish", "reason": "tool_calls"}


def test_length_finish_reason_survives() -> None:
    frames = lines(chunk({"content": "partial"}, finish_reason="length"), envelope("[DONE]"))
    assert list(stream.iter_agent_chunks(frames, provider="qodercli"))[-1] == {"type": "finish", "reason": "length"}


# -- thinking artefacts ------------------------------------------------------ #
def test_reasoning_channel_is_cleaned_of_tags_and_dsml() -> None:
    frames = lines(
        chunk({"reasoning_content": "<thinking>let me think"}),
        chunk({"reasoning_content": CLOSE_INVOKE + CLOSE_PARAMETER}),
        chunk({"content": "answer"}),
        envelope("[DONE]"),
    )
    chunks = list(stream.iter_agent_chunks(frames, provider="qodercli"))
    reasoning = text_of(chunks, "reasoning_delta")
    assert "let me think" in reasoning
    assert "invoke" not in reasoning and "parameter" not in reasoning
    assert text_of(chunks) == "answer"


def test_thinking_tags_split_across_deltas_are_not_leaked_as_text() -> None:
    frames = lines(
        chunk({"content": "<think"}),
        chunk({"content": ">secret reasoning</think"}),
        chunk({"content": ">"}),
        chunk({"content": "\n\nvisible"}),
        envelope("[DONE]"),
    )
    chunks = list(stream.iter_agent_chunks(frames, provider="qodercli"))
    assert text_of(chunks, "reasoning_delta") == "secret reasoning"
    # The separator the model emits after the closer arrives in its own delta,
    # so it stays in the visible text (the plugin's parser behaves the same).
    assert text_of(chunks) == "\n\nvisible"


def test_orphan_closer_from_the_reasoning_channel_is_dropped() -> None:
    frames = lines(chunk({"reasoning_content": "thoughts"}), chunk({"content": "</thinking>\n\nanswer"}), envelope("[DONE]"))
    assert text_of(list(stream.iter_agent_chunks(frames, provider="qodercli"))) == "answer"


def test_degenerate_dsml_turn_is_reported_as_an_error() -> None:
    frames = lines(chunk({"reasoning_content": CLOSE_INVOKE + CLOSE_PARAMETER}), envelope("[DONE]"))
    chunks = list(stream.iter_agent_chunks(frames, provider="qodercli"))
    assert chunks[-1]["type"] == "error"
    assert "degenerate model output" in chunks[-1]["error"]


def test_a_single_literal_tag_in_prose_is_not_degenerate() -> None:
    frames = lines(chunk({"reasoning_content": "use " + CLOSE_INVOKE + " to close"}), chunk({"content": "answer"}), envelope("[DONE]"))
    chunks = list(stream.iter_agent_chunks(frames, provider="qodercli"))
    assert chunks[-1]["type"] == "finish"


def test_malformed_envelopes_are_skipped_but_a_lost_payload_is_reported() -> None:
    frames = lines("data:not json", chunk({"content": "ok"}), envelope("[DONE]"))
    assert text_of(list(stream.iter_agent_chunks(frames, provider="qodercli"))) == "ok"

    broken = "data:" + json.dumps({"body": '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":" \"Paris\"}', "statusCodeValue": 200})
    frames = lines(
        chunk({"tool_calls": [{"index": 0, "id": "c", "function": {"name": "f", "arguments": '{"city":'}}]}),
        broken,
        envelope("[DONE]"),
    )
    with pytest.raises(ProviderError) as excinfo:
        list(stream.iter_agent_chunks(frames, provider="qodercli"))
    assert excinfo.value.retryable and "cut off" in excinfo.value.body


# -- business codes ---------------------------------------------------------- #
def test_daily_limit_is_recognized_through_the_nested_json() -> None:
    body = json.dumps({"code": "110", "message": "Billing daily count exceeded"})
    assert stream.is_daily_chat_limit(body)
    assert stream.is_daily_chat_limit(json.dumps({"code": 110}))
    assert not stream.is_daily_chat_limit(json.dumps({"code": "105"}))
    frames = lines(envelope(body, status=403))
    with pytest.raises(stream.DailyChatLimitRejection):
        list(stream.iter_agent_chunks(frames, provider="qodercli"))


def test_busy_ready_carries_the_retry_hint() -> None:
    queue_state = json.dumps({"isQueued": False, "serviceAvailable": True, "retryAfterSeconds": 1})
    body = json.dumps({"code": "10605", "message": queue_state, "subCode": "RESOURCE_LIMIT"})
    snapshot = stream.parse_model_busy(json.dumps({"code": "403", "message": body}))
    assert snapshot == {"kind": "ready", "retry_after_ms": 1000}
    frames = lines(envelope(body, status=403))
    with pytest.raises(stream.ModelBusyRejection) as excinfo:
        list(stream.iter_agent_chunks(frames, provider="qodercli"))
    assert excinfo.value.retry_after_ms == 1000 and not excinfo.value.queued


def test_busy_queued_prefers_wait_time_and_is_schedulable() -> None:
    queue_state = json.dumps({"isQueued": True, "serviceAvailable": True, "waitTime": 30, "retryAfterSeconds": 2, "queueCount": 4, "queueType": "CALL_COUNT_LIMIT"})
    body = json.dumps({"code": "10605", "message": queue_state})
    snapshot = stream.parse_model_busy(body)
    assert snapshot is not None and snapshot["kind"] == "queued" and snapshot["retry_after_ms"] == 30000
    frames = lines(envelope(body, status=403))
    with pytest.raises(RequestThrottled) as excinfo:
        list(stream.iter_agent_chunks(frames, provider="qodercli"))
    assert excinfo.value.retry_after == 30.0


def test_a_busy_signal_without_service_available_is_not_a_recovery() -> None:
    body = json.dumps({"code": "10605", "message": json.dumps({"isQueued": False, "serviceAvailable": False})})
    assert stream.parse_model_busy(body) is None


def test_login_expired_is_an_auth_failure() -> None:
    from hoshimi.core.providers.base import AuthFailed

    frames = lines(envelope(json.dumps({"code": "105", "message": "Login expired"}), status=401))
    with pytest.raises(AuthFailed):
        list(stream.iter_agent_chunks(frames, provider="qodercli"))


# -- the provider's recovery loop -------------------------------------------- #
def catalog_handler(models: list[dict[str, Any]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobToken/exchange"):
            return httpx.Response(200, json={"token": "jt-1", "refresh_token": "jrt-1", "expires_at": "2030-01-01T00:00:00Z"})
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json={"id": "uid-1", "name": "Neo", "email": "neo@example.com"})
        if request.url.path.endswith("/model/list"):
            return httpx.Response(200, json={"chat": models})
        if request.url.path.endswith("/region/endpoints"):
            payload = {"inferNodes": ["https://infer.example"], "dataNodeMap": {"remote_agent": ["https://agent.example"]}}
            from hoshimi.core.qoder.encoding import encode_body

            return httpx.Response(200, text=encode_body(json.dumps(payload).encode()).decode())
        return httpx.Response(404, json={"code": "404", "message": "unexpected path"})

    return handler


MODELS = [{"key": "lite", "display_name": "Lite", "enable": True, "source": "system"}]


def test_daily_limit_rotates_to_the_next_node_and_persists_it(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))
    hosts: list[str] = []
    base = catalog_handler(MODELS)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent_chat_generation"):
            hosts.append(request.url.host)
            if len(hosts) == 1:
                return httpx.Response(403, json={"code": "110", "message": "Billing daily count exceeded"})
            return httpx.Response(200, text="\n".join(lines(chunk({"content": "served"}), envelope("[DONE]"))))
        return base(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lane = QoderCliCnProvider(client, [Account({"id": "a", "personal_token": "pt-x"}, provider_id="qodercli-cn")])
        chunks = list(lane.stream("Lite", {"messages": [{"role": "user", "content": "hi"}]}))
    assert text_of(chunks) == "served"
    # The configured base is the first rotation candidate, then the other nodes.
    assert hosts == ["agent.example", "gateway.qoder.com.cn"]
    assert lane.endpoints.cached_chat_base_url() == "https://gateway.qoder.com.cn/"


def test_busy_ready_is_retried_with_a_fresh_request_id(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("hoshimi.core.providers.qodercli.time.sleep", lambda _seconds: None)
    bodies: list[dict[str, Any]] = []
    base = catalog_handler(MODELS)
    queue_state = json.dumps({"isQueued": False, "serviceAvailable": True, "retryAfterSeconds": 0})
    busy = json.dumps({"code": "10605", "message": queue_state})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent_chat_generation"):
            from hoshimi.core.qoder.encoding import decode_body

            bodies.append(json.loads(decode_body(request.content.decode())))
            if len(bodies) == 1:
                return httpx.Response(200, text="\n".join(lines(envelope(busy, status=403))))
            return httpx.Response(200, text="\n".join(lines(chunk({"content": "recovered"}), envelope("[DONE]"))))
        return base(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lane = QoderCliCnProvider(client, [Account({"id": "a", "personal_token": "pt-x"}, provider_id="qodercli-cn")])
        chunks = list(lane.stream("Lite", {"messages": [{"role": "user", "content": "hi"}]}))
    assert text_of(chunks) == "recovered"
    assert len(bodies) == 2
    assert bodies[0]["request_set_id"] == bodies[1]["request_set_id"]
    assert bodies[0]["request_id"] != bodies[1]["request_id"]
    assert bodies[0]["chat_record_id"] != bodies[1]["chat_record_id"]


def test_a_refusal_after_partial_output_is_not_retried(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))
    calls: list[str] = []
    base = catalog_handler(MODELS)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent_chat_generation"):
            calls.append(request.url.host)
            frames = lines(chunk({"content": "partial"}), envelope(json.dumps({"code": "110", "message": "Billing daily count exceeded"}), status=403))
            return httpx.Response(200, text="\n".join(frames))
        return base(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lane = QoderCliCnProvider(client, [Account({"id": "a", "personal_token": "pt-x"}, provider_id="qodercli-cn")])
        chunks = list(lane.stream("Lite", {"messages": [{"role": "user", "content": "hi"}]}))
    assert text_of(chunks) == "partial"
    assert len(calls) == 1  # a retry would duplicate the content already emitted
    assert chunks[-1]["type"] == "error"


def test_the_request_body_is_encoded_and_signed(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))
    seen: dict[str, Any] = {}
    base = catalog_handler(MODELS)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("agent_chat_generation"):
            from hoshimi.core.qoder.encoding import decode_body

            seen["headers"] = dict(request.headers)
            seen["raw"] = request.content
            seen["body"] = json.loads(decode_body(request.content.decode()))
            seen["url"] = str(request.url)
            return httpx.Response(200, text="\n".join(lines(chunk({"content": "ok"}), envelope("[DONE]"))))
        return base(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lane = QoderCliCnProvider(client, [Account({"id": "a", "personal_token": "pt-x"}, provider_id="qodercli-cn")])
        list(lane.stream("Lite", {"messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}], "reasoning_effort": "none"}))
    headers = seen["headers"]
    assert headers["authorization"].startswith("Bearer COSY.")
    assert headers["x-model-key"] == "lite"
    assert headers["cosy-clienttype"] == "5"
    assert headers["cosy-sigpath"] == "/api/v2/service/pro/sse/agent_chat_generation"
    assert int(headers["cosy-bodylength"]) == len(seen["raw"])
    import hashlib

    assert headers["cosy-bodyhash"] == hashlib.md5(seen["raw"]).hexdigest()
    assert seen["url"].startswith("https://agent.example/algo/api/v2/service/pro/sse/agent_chat_generation?")
    assert "FetchKeys=llm_model_result" in seen["url"] and "AgentId=agent_common" in seen["url"] and "Encode=1" in seen["url"]
    body = seen["body"]
    assert body["messages"][0] == {"role": "system", "content": "rules"}
    assert body["parameters"]["enable_thinking"] is False
    assert body["model_config"]["key"] == "lite" and body["model_config"]["is_reasoning"] is False


def test_an_unknown_model_is_model_gone(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from hoshimi.core.providers.base import ModelGone

    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))
    with httpx.Client(transport=httpx.MockTransport(catalog_handler(MODELS))) as client:
        lane = QoderCliCnProvider(client, [Account({"id": "a", "personal_token": "pt-x"}, provider_id="qodercli-cn")])
        lane.models()
        with pytest.raises(ModelGone):
            list(lane.stream("NoSuchModel", {"messages": []}))


def test_a_pat_only_account_exchanges_on_first_use_and_renews(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))
    exchanges: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobToken/exchange"):
            exchanges.append(json.loads(request.content)["personal_token"])
            return httpx.Response(200, json={"token": f"jt-{len(exchanges)}", "refresh_token": "jrt", "expires_at": "2030-01-01T00:00:00Z"})
        return catalog_handler(MODELS)(request)

    account = Account({"id": "a", "personal_token": "pt-x"}, provider_id="qodercli-cn")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lane = QoderCliCnProvider(client, [account])
        lane.models()
        assert account.get("access_token") == "jt-1"
        assert account.get("uid") == "uid-1"
        assert account.get("machine_id")
        assert lane.refresh_account(account) is True
        assert account.get("access_token") == "jt-2"
    assert exchanges == ["pt-x", "pt-x"]


def test_a_refused_pat_is_terminal_for_the_account(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from hoshimi.core.providers.base import RefreshTokenRejected

    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobToken/exchange"):
            return httpx.Response(401, json={"errorCode": "Unauthorized"})
        return catalog_handler(MODELS)(request)

    account = Account({"id": "a", "personal_token": "pt-x"}, provider_id="qodercli-cn")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lane = QoderCliCnProvider(client, [account])
        with pytest.raises(RefreshTokenRejected):
            lane.refresh_account(account)


def test_balance_reads_every_quota_bucket(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/quota/usage"):
            return httpx.Response(200, json={"userQuota": {"remaining": 2963.0}, "addOnQuota": {"remaining": 10}, "orgResourcePackage": {"remaining": 5}})
        return catalog_handler(MODELS)(request)

    account = Account({"id": "a", "personal_token": "pt-x"}, provider_id="qodercli-cn")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lane = QoderCliCnProvider(client, [account])
        outcome = lane.balance_of(account)
    assert outcome is not None and outcome.available == 2978


def test_the_cn_lane_uses_the_cn_hosts() -> None:
    assert CN.open_api_url == "https://openapi.qoder.com.cn"
    assert CN.base_url == "https://gateway.qoder.com.cn/"
    assert QoderCliCnProvider.region is CN
    assert CN.provider_id == "qodercli-cn"