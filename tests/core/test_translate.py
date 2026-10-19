from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlsplit

from hoshimi.core.providers.codearts import sign_request_huawei
from hoshimi.core.sse import iter_sse, parse_sse_events
from hoshimi.core.translate.anthropic import build_anthropic_body, iter_anthropic_chunks
from hoshimi.core.translate.gemini import (
    build_gemini_envelope,
    derive_session_id,
    marshal_envelope,
)
from hoshimi.core.translate.solo import build_solo_body, iter_solo_chunks


def test_iter_sse_events_and_multiline_data() -> None:
    lines = [
        "event: output",
        'data: {"a":',
        "data: 1}",
        "",
        "data: [DONE]",
        "",
        "event: done\r",
        'data: {"finish_reason":"stop"}\r',
        "\r",
    ]
    pairs = list(iter_sse(lines))
    assert pairs[0] == ("output", '{"a":\n1}')
    assert pairs[1] == (None, "[DONE]")
    assert pairs[2] == ("done", '{"finish_reason":"stop"}')

    parsed = list(parse_sse_events(iter_sse(lines)))
    assert parsed[0] == ("output", {"a": 1})
    assert parsed[1] == ("done", {"__done__": True})
    assert parsed[2] == ("done", {"finish_reason": "stop"})


def test_iter_sse_flushes_trailing_data_without_blank_line() -> None:
    assert list(iter_sse(['data: {"x":1}'])) == [(None, '{"x":1}')]


def test_build_anthropic_body_shapes() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "fn", "arguments": '{"a": 1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "fn",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}},
                },
            }
        ],
        "tool_choice": "auto",
        "stop": "STOP",
    }
    payload = build_anthropic_body(body, model="m")
    assert payload["model"] == "m"
    assert payload["stream"] is True
    assert payload["system"] == "be brief"
    assert payload["max_tokens"] == 8192
    assert payload["stop_sequences"] == ["STOP"]

    assert payload["tools"][0] == {
        "name": "fn",
        "description": "d",
        "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}}},
    }

    roles = [message["role"] for message in payload["messages"]]
    assert roles == ["user", "assistant", "user"]
    assistant = payload["messages"][1]["content"]
    assert {"type": "text", "text": "calling"} in assistant
    tool_use = next(block for block in assistant if block["type"] == "tool_use")
    assert tool_use == {"type": "tool_use", "id": "call_1", "name": "fn", "input": {"a": 1}}
    tool_result = payload["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "call_1"


def test_build_anthropic_body_tool_choice_and_extra() -> None:
    body = {"messages": [{"role": "user", "content": "hi"}], "tool_choice": "required"}
    payload = build_anthropic_body(body, model="m", extra={"temperature": 0.2})
    assert payload["tool_choice"] == {"type": "any"}
    assert payload["temperature"] == 0.2
    assert "tools" not in payload

    body["tool_choice"] = {"type": "function", "function": {"name": "fn"}}
    payload = build_anthropic_body(body, model="m")
    assert payload["tool_choice"] == {"type": "tool", "name": "fn"}

    body["tool_choice"] = "none"
    payload = build_anthropic_body(body, model="m")
    assert payload["tool_choice"] == {"type": "none"}


def _anthropic_lines() -> list[str]:
    return [
        "event: message_start",
        'data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}',
        "",
        "event: content_block_start",
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking"}}',
        "",
        "event: content_block_delta",
        (
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":"hmm"}}'
        ),
        "",
        "event: content_block_delta",
        (
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"signature_delta","signature":"x"}}'
        ),
        "",
        "event: content_block_stop",
        'data: {"type":"content_block_stop","index":0}',
        "",
        "event: content_block_start",
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"text"}}',
        "",
        "event: content_block_delta",
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Hi"}}',
        "",
        "event: content_block_stop",
        'data: {"type":"content_block_stop","index":1}',
        "",
        "event: message_delta",
        (
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
            '"usage":{"output_tokens":5}}'
        ),
        "",
        "event: message_stop",
        'data: {"type":"message_stop"}',
        "",
    ]


def test_iter_anthropic_chunks_normalizes_stream() -> None:
    chunks = list(iter_anthropic_chunks(_anthropic_lines()))
    kinds = [chunk["type"] for chunk in chunks]
    assert kinds == [
        "reasoning_delta",
        "reasoning_details",
        "block_stop",
        "text_delta",
        "block_stop",
        "usage",
        "finish",
    ]
    reasoning = next(chunk for chunk in chunks if chunk["type"] == "reasoning_delta")
    assert reasoning["text"] == "hmm"
    usage = next(chunk for chunk in chunks if chunk["type"] == "usage")
    assert usage["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    finish = next(chunk for chunk in chunks if chunk["type"] == "finish")
    assert finish["reason"] == "tool_calls"


def test_iter_anthropic_chunks_tool_use_and_errors() -> None:
    lines = [
        "event: content_block_start",
        (
            'data: {"type":"content_block_start","index":0,'
            '"content_block":{"type":"tool_use","id":"call_1","name":"fn"}}'
        ),
        "",
        "event: content_block_delta",
        (
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"input_json_delta","partial_json":"{\\"a\\""}}'
        ),
        "",
        "event: message_delta",
        'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}',
        "",
    ]
    chunks = list(iter_anthropic_chunks(lines))
    start = chunks[0]
    assert start == {"type": "tool_call_start", "id": "call_1", "name": "fn", "arguments": "", "index": 0}
    assert chunks[1] == {"type": "tool_call_delta", "index": 0, "arguments": '{"a"'}
    assert chunks[-1] == {"type": "finish", "reason": "length"}

    error = list(iter_anthropic_chunks(["event: error", 'data: {"type":"error","error":{"message":"boom"}}', ""]))
    assert error == [{"type": "error", "error": "boom"}]

    empty = list(iter_anthropic_chunks([]))
    assert empty == [{"type": "error", "error": "Upstream returned no content blocks."}]


def test_gemini_envelope_shape_and_tiers() -> None:
    body = {"messages": [{"role": "user", "content": "hello"}]}
    envelope = build_gemini_envelope(body, model="gemini-3.8-flash", effort="high")
    assert envelope["model"] == "gemini-3.8-flash-high"
    assert envelope["project"] == "aicode-consumers"
    assert envelope["userAgent"] == "antigravity"
    assert envelope["requestId"].startswith("agent/")
    assert len(envelope["requestId"].split("/")[-1]) == 8

    config = envelope["request"]["generationConfig"]
    assert config["thinkingConfig"] == {"includeThoughts": True, "thinkingBudget": 10000}
    assert envelope["request"]["sessionId"] == derive_session_id("aicode-consumers", "hello")

    # The tier can also come from the model id; an explicit effort wins.
    low = build_gemini_envelope(body, model="gemini-3.8-flash-low", effort=None)
    assert low["model"] == "gemini-3.8-flash-low"
    assert low["request"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 1000

    default = build_gemini_envelope(body, model="gemini-3.8-flash", effort=None)
    assert default["model"] == "gemini-3.8-flash-medium"
    assert default["request"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 4000

    tiered = build_gemini_envelope(body, model="gemini-3.8-flash", effort="tiered")
    assert tiered["model"] == "gemini-3.8-flash-tiered"
    assert tiered["request"]["generationConfig"]["thinkingConfig"] == {"includeThoughts": True}


def test_gemini_envelope_marshals_keys_alphabetically() -> None:
    body = {"messages": [{"role": "user", "content": "hello"}]}
    envelope = build_gemini_envelope(body, model="gemini-3.8-flash", effort="low")
    wire = marshal_envelope(envelope)
    assert list(json.loads(wire).keys()) == ["model", "project", "request", "requestId", "userAgent"]
    request_keys = list(json.loads(wire)["request"].keys())
    assert request_keys == sorted(request_keys)


def test_gemini_envelope_maps_tools_and_roles() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "fn", "arguments": '{"a":1}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "fn",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}, "unknown": 1},
                },
            }
        ],
        "tool_choice": "none",
    }
    request = build_gemini_envelope(body, model="gemini-3.8-flash")["request"]
    assert request["systemInstruction"] == {"role": "system", "parts": [{"text": "sys"}]}
    roles = [content["role"] for content in request["contents"]]
    assert roles == ["user", "model", "user"]
    assert request["contents"][1]["parts"][0]["functionCall"] == {"name": "fn", "args": {"a": 1}}
    assert request["contents"][2]["parts"][0]["functionResponse"] == {
        "name": "fn",
        "response": {"content": "ok"},
    }
    declaration = request["tools"][0]["functionDeclarations"][0]
    # Schema keys outside the allowlist are stripped before sending.
    assert "unknown" not in declaration["parameters"]
    assert request["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}


def test_build_solo_body_shape() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "using tool",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "fn", "arguments": '{"x":1}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {"name": "fn", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            }
        ],
        "tool_choice": "auto",
        "max_completion_tokens": 123,
    }
    out = build_solo_body(body, model="glm-5.2")
    assert out["stream"] is True
    assert out["function"] == "solo_work_lite"
    assert out["config_name"] == "glm-5.2"
    assert out["model"] == "glm-5.2"
    assert out["max_tokens"] == 123

    assert out["messages"][1]["content"] == [{"type": "text", "text": "hi"}]
    tool_call = out["messages"][2]["tool_calls"][0]
    assert "function" not in tool_call
    assert tool_call["function_call"]["name"] == "fn"
    assert isinstance(out["tools"][0]["function"]["parameters"], str)
    assert json.loads(out["tools"][0]["function"]["parameters"])["type"] == "object"


def test_build_solo_body_suffix_and_tool_choice_none() -> None:
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "fn"}}],
        "tool_choice": "none",
    }
    out = build_solo_body(body, model="glm-5.2__solo")
    assert out["config_name"] == "glm-5.2"
    assert "tools" not in out
    assert "tool_choice" not in out


def test_iter_solo_chunks_normalizes_stream() -> None:
    lines = [
        "event: metadata",
        'data: {"trace_id":"t"}',
        "",
        "event: output",
        'data: {"response":"Hi","reasoning_content":"hmm"}',
        "",
        "event: output",
        'data: {"tool_calls":[{"id":"call_1","function":{"name":"fn","arguments":"{}"}}]}',
        "",
        "event: token_usage",
        'data: {"prompt_tokens":7,"completion_tokens":3}',
        "",
        "event: done",
        'data: {"finish_reason":"tool_calls"}',
        "",
    ]
    chunks = list(iter_solo_chunks(lines))
    assert [chunk["type"] for chunk in chunks] == [
        "text_delta",
        "reasoning_delta",
        "tool_call_start",
        "usage",
        "finish",
    ]
    assert chunks[2] == {"type": "tool_call_start", "id": "call_1", "name": "fn", "arguments": "{}", "index": 0}
    assert chunks[3]["usage"]["total_tokens"] == 10

    error = list(iter_solo_chunks(["event: error", 'data: {"code":42,"message":"nope"}', ""]))
    assert error == [{"type": "error", "error": "nope"}]


def test_huawei_signature_is_reproducible() -> None:
    url = "https://snap-access.cn-north-4.myhuaweicloud.com/api/v2/chat/completions"
    body = json.dumps({"model": "m", "stream": True}, separators=(",", ":")).encode()
    headers = sign_request_huawei("ak", "sk", "token", "POST", url, body, {"maas_type": "benefit"})

    assert headers["host"] == "snap-access.cn-north-4.myhuaweicloud.com"
    assert headers["x-security-token"] == "token"
    assert headers["content-type"] == "application/json"
    assert headers["x-sdk-content-sha256"] == hashlib.sha256(body).hexdigest()

    authorization = headers["Authorization"]
    assert authorization.startswith("SDK-HMAC-SHA256 Access=ak,")
    signed_headers = authorization.split("SignedHeaders=")[1].split(",")[0]
    keys = signed_headers.split(";")
    assert keys == sorted(keys)
    assert "maas_type" in keys

    parts = urlsplit(url)
    uri = parts.path or "/"
    if not uri.endswith("/"):
        uri += "/"
    canonical_headers = "\n".join(f"{key}:{headers[key]}" for key in keys)
    canonical_request = "\n".join(
        [
            "POST",
            uri,
            parts.query,
            canonical_headers,
            "",
            signed_headers,
            headers["x-sdk-content-sha256"],
        ]
    )
    string_to_sign = "\n".join(
        [
            "SDK-HMAC-SHA256",
            headers["x-sdk-date"],
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    expected = hmac.new(b"sk", string_to_sign.encode(), hashlib.sha256).hexdigest()
    assert authorization.split("Signature=")[1] == expected
