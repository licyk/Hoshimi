"""The OpenCode free lane: fingerprint gate, endpoint split, stable sessions."""

from __future__ import annotations

import copy
import json

import httpx
import pytest

from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.opencode import (
    CLIENT_UA,
    MAX_UNSUPPORTED_ROUNDS,
    REQUEST_RE,
    SESSION_RE,
    OpencodeProvider,
    apply_fingerprint,
    is_free_lane,
    mint_request_id,
    session_for_conversation,
    unsupported_result,
    wire_for,
)
from hoshimi.core.providers.opencode_fingerprint import FINGERPRINT_TOOL_DEFINITIONS, fingerprint_tool
from hoshimi.core.translate.responses import chat_to_responses, iter_responses_chunks


def _names(tools: list[dict]) -> list[str]:
    out = []
    for tool in tools:
        fn = tool.get("function")
        out.append(fn["name"] if isinstance(fn, dict) else tool.get("name"))
    return out


# -- fingerprint gate ------------------------------------------------------- #


def test_fingerprint_declares_exact_v2_tools_on_an_empty_tool_list() -> None:
    body: dict = {}
    rename = apply_fingerprint(body, "chat")
    assert rename == {}
    assert _names(body["tools"]) == ["read", "shell"]
    assert "tool_choice" not in body
    for tool in body["tools"]:
        fn = tool["function"]
        assert fn == {"name": fn["name"], **FINGERPRINT_TOOL_DEFINITIONS[fn["name"]]}
    assert "placeholders" in body["messages"][0]["content"]


def test_fingerprint_keeps_caller_case_and_legacy_tool_names() -> None:
    body = {"tools": [
        {"type": "function", "function": {"name": "Bash", "parameters": {}}},
        {"type": "function", "function": {"name": "bash", "parameters": {}}},
    ]}
    rename = apply_fingerprint(body, "chat")
    assert rename == {}
    assert _names(body["tools"]) == ["Bash", "bash", "read", "shell"]


def test_fingerprint_keeps_pwsh_instead_of_promoting_it() -> None:
    body = {"tools": [{"type": "function", "function": {"name": "pwsh", "parameters": {}}}]}
    rename = apply_fingerprint(body, "chat")
    assert rename == {}
    names = _names(body["tools"])
    assert names == ["pwsh", "read", "shell"]


def test_fingerprint_shapes_follow_the_wire() -> None:
    flat: dict = {}
    apply_fingerprint(flat, "flat")
    assert all(tool["type"] == "function" and "function" not in tool for tool in flat["tools"])
    assert flat["tool_choice"] == "auto"

    claude: dict = {}
    apply_fingerprint(claude, "claude")
    assert all("input_schema" in tool and "type" not in tool for tool in claude["tools"])
    assert "tool_choice" not in claude
    assert "placeholders" in claude["system"]


def test_fingerprint_keeps_caller_tools_and_leaves_tool_choice_alone() -> None:
    body = {
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
        "tool_choice": "required",
    }
    apply_fingerprint(body, "chat")
    assert body["tool_choice"] == "required"
    names = _names(body["tools"])
    assert names == ["get_weather", "read", "shell"]


@pytest.mark.parametrize("style", ["chat", "flat", "claude"])
def test_tool_aliases_retarget_history_and_forced_choice_without_losing_metadata(style: str) -> None:
    custom = fingerprint_tool("read", style)
    definition = custom.get("function", custom)
    definition["description"] = "The caller's own read tool"
    occupied = copy.deepcopy(custom)
    if "function" in occupied:
        occupied["function"]["name"] = "read_client"
    else:
        occupied["name"] = "read_client"
    message = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"file"}'}, "extra_content": {"signature": "opaque"}},
    ]}
    body = {"tools": [custom, occupied], "messages": [message], "tool_choice": {"type": "function", "function": {"name": "read"}}}
    if style == "flat":
        body["input"] = [{"type": "function_call", "name": "read", "call_id": "call_1", "arguments": "{}", "extra_content": {"signature": "opaque"}}]
        body["tool_choice"] = {"type": "function", "name": "read"}
    elif style == "claude":
        body["messages"] = [{"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "read", "input": {}, "signature": "opaque"}]}]
        body["tool_choice"] = {"type": "tool", "name": "read"}
        body["system"] = [{"type": "text", "text": "Original instruction", "cache_control": {"type": "ephemeral"}}]
    before = copy.deepcopy(body)
    assert apply_fingerprint(body, style) == {"read_client2": "read"}
    assert _names(body["tools"]) == ["read_client2", "read_client", "read", "shell"]
    assert body["tools"][0].get("function", body["tools"][0])["description"] == "The caller's own read tool"
    if style == "flat":
        assert body["tool_choice"]["name"] == "read_client2"
        assert body["input"][0] == {**before["input"][0], "name": "read_client2"}
    elif style == "claude":
        assert body["tool_choice"]["name"] == "read_client2"
        assert body["messages"][0]["content"][0] == {**before["messages"][0]["content"][0], "name": "read_client2"}
        assert body["system"][0] == before["system"][0]
    else:
        assert body["tool_choice"]["function"]["name"] == "read_client2"
        call = body["messages"][1]["tool_calls"][0]
        assert call["function"]["name"] == "read_client2"
        assert call["extra_content"] == {"signature": "opaque"}
        assert message == before["messages"][0]


@pytest.mark.parametrize("style", ["chat", "flat", "claude"])
def test_exact_tool_definitions_are_reused_and_duplicate_gate_names_dropped(style: str) -> None:
    body = {"tools": [fingerprint_tool("read", style), fingerprint_tool("read", style), fingerprint_tool("shell", style)]}
    assert apply_fingerprint(body, style) == {}
    assert _names(body["tools"]) == ["read", "shell"]
    assert "system" not in body and "messages" not in body and "instructions" not in body


@pytest.mark.parametrize("choice", ["none", {"type": "none"}])
@pytest.mark.parametrize("style", ["chat", "flat", "claude"])
def test_v2_requests_never_send_none_tool_choice(style: str, choice: object) -> None:
    body = {"tool_choice": choice}
    apply_fingerprint(body, style)
    assert body.get("tool_choice") == ("auto" if style == "flat" else None)


# -- sessions and endpoints ------------------------------------------------- #


def test_session_ids_are_stable_and_gateway_shaped() -> None:
    first = session_for_conversation("same conversation")
    assert first == session_for_conversation("same conversation")
    assert first != session_for_conversation("another conversation")
    assert SESSION_RE.match(first)
    assert session_for_conversation(first) == first
    assert REQUEST_RE.match(mint_request_id())


def test_sessions_match_reference_vectors_and_reencode_v1_ids() -> None:
    assert session_for_conversation("hello") == "ses_c51d137aaffe2ss37XmkOuk1FJ"
    assert session_for_conversation(None) == "ses_4c86fa44effe3A2voSQN7agsVR"
    legacy = "ses_000000000000ABCDEFGHIJKLMN"
    assert SESSION_RE.fullmatch(legacy)
    assert session_for_conversation(legacy) != legacy
    assert session_for_conversation("hello", namespace="account-a") != session_for_conversation("hello", namespace="account-b")


def test_session_seed_prefers_the_explicit_conversation() -> None:
    provider = OpencodeProvider(httpx.Client(), [Account({"id": "anon"}, provider_id="opencode")])
    from hoshimi.core.providers.opencode import _session_seed

    assert _session_seed({"session_id": "abc"}) == "abc"
    assert _session_seed({"conversation_id": "c-1"}) == "c-1"
    assert _session_seed({"user": "u-1"}) == "u-1"
    assert _session_seed({"messages": [{"role": "user", "content": "hello"}]}) == "hello"
    assert _session_seed({}) == "global"
    assert provider.endpoint_url("chat") == "https://opencode.ai/inference/v1/chat/completions"
    assert provider.endpoint_url("responses") == "https://opencode.ai/inference/v1/responses"
    assert provider.endpoint_url("messages") == "https://opencode.ai/inference/v1/messages"


def test_endpoint_split_per_model() -> None:
    assert wire_for("mimo-v2.6-flash-free") == "chat"
    assert wire_for("muse-spark-1.2-contributor-free") == "responses"
    assert wire_for("muse-spark-1.3-contributor-free (deep)") == "responses"
    assert wire_for("union-alpha") == "messages"


def test_free_lane_filter() -> None:
    assert is_free_lane("mimo-v2.6-flash-free")
    assert is_free_lane("space-bunny-free")
    assert is_free_lane("jev-1.13-free")
    # An id without the `-free` suffix that the lane serves anonymously anyway.
    assert is_free_lane("big-pickle")
    assert not is_free_lane("claude-opus-5")
    assert not is_free_lane("gpt-6-sol")


# -- discovery -------------------------------------------------------------- #


def _provider(payload: dict) -> OpencodeProvider:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    return OpencodeProvider(client, [Account({"id": "anon"}, provider_id="opencode")])


def test_anonymous_discovery_lists_only_the_free_lane() -> None:
    provider = _provider({"data": [
        {"id": "claude-opus-5"},
        {"id": "mimo-v2.6-flash-free"},
        {"id": "big-pickle"},
        {"id": "space-bunny-free"},
    ]})
    assert provider.models() == ["mimo-v2.6-flash-free", "big-pickle", "space-bunny-free"]


def test_keyed_discovery_lists_the_whole_roster() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [{"id": "claude-opus-5"}]})))
    provider = OpencodeProvider(client, [Account({"id": "mine", "api_key": "k"}, provider_id="opencode")])
    assert provider.models() == ["claude-opus-5"]
    assert provider.build_headers(provider.accounts[0], "x")["User-Agent"] == CLIENT_UA


def test_fallback_roster_is_all_free_lane() -> None:
    provider = _provider({"data": []})
    assert provider.models()
    assert all(is_free_lane(model_id) for model_id in provider.fallback_models())


def test_explicit_public_key_is_still_anonymous_and_catalog_failure_retains_cache() -> None:
    fail = False

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/inference/v1/models"
        assert request.headers["Authorization"] == "Bearer public"
        return httpx.Response(503) if fail else httpx.Response(200, json={"data": [{"id": "gpt-6-sol"}, {"id": "deepseek-v4.1-flash-free"}]})

    provider = OpencodeProvider(httpx.Client(transport=httpx.MockTransport(handle)), [Account({"api_key": "public"}, provider_id="opencode")])
    assert provider.models() == ["deepseek-v4.1-flash-free"]
    fail = True
    provider._models_expires_at = 0
    assert provider.models() == ["deepseek-v4.1-flash-free"]


def test_stream_sends_v2_headers_restores_tool_aliases_and_isolates_accounts() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        name = "read_client" if request.headers["Authorization"] == "Bearer public" else "read"
        return httpx.Response(200, text="\n".join(_frames(json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function", "function": {"name": name, "arguments": "{}"}},
        ]}, "finish_reason": "tool_calls"}]}))), headers={"Content-Type": "text/event-stream"})

    anonymous = Account({"id": "anon"}, provider_id="opencode")
    keyed = Account({"id": "keyed", "api_key": "key"}, provider_id="opencode")
    provider = OpencodeProvider(httpx.Client(transport=httpx.MockTransport(handle)), [keyed, anonymous])
    body = {"messages": [{"role": "user", "content": "read a file"}], "session_id": "conversation", "tools": [
        {"type": "function", "function": {"name": "read", "description": "Custom", "parameters": {"type": "object"}}},
    ], "tool_choice": {"type": "function", "function": {"name": "read"}}}
    original = copy.deepcopy(body)
    for account in (anonymous, anonymous, keyed):
        chunks = list(provider.stream_raw(account, "mimo-v2.6-flash-free", body))
        assert next(chunk for chunk in chunks if chunk["type"] == "tool_call_start")["name"] == "read"
    assert body == original
    sessions = [request.headers["x-opencode-session"] for request in requests]
    assert sessions[0] == sessions[1] != sessions[2]
    for request in requests:
        assert request.url.path == "/inference/v1/chat/completions"
        assert request.headers["User-Agent"] == "opencode/latest/2.0.26/cli"
        assert request.headers["x-opencode-client"] == "cli"
        assert "x-opencode-request" not in request.headers
        assert all(request.headers[key] == request.headers["x-opencode-session"] for key in ("x-opencode-session-id", "x-session-affinity", "x-session-id"))
        payload = json.loads(request.content)
        assert "session_id" not in payload and payload["stream"] is True
    assert json.loads(requests[-1].content)["tools"] == body["tools"]
    assert json.loads(requests[0].content)["tool_choice"]["function"]["name"] == "read_client"


def _chat_reply(*, text: str = "", calls: list[tuple[str, str]] = (), tokens: tuple[int, int] = (3, 2)) -> str:
    """A chat-completions SSE body: some text, some tool calls (id, name), usage, and the finish reason."""
    events = []
    if text:
        events.append(json.dumps({"choices": [{"delta": {"content": text}}]}))
    if calls:
        events.append(json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": i, "id": call_id, "type": "function", "function": {"name": name, "arguments": '{"path": "a.txt"}'}} for i, (call_id, name) in enumerate(calls)
        ]}, "finish_reason": "tool_calls"}]}))
    events.append(json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls" if calls else "stop"}], "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "total_tokens": sum(tokens)}}))
    return "\n".join(_frames(*events))


def _scripted(*replies: str) -> tuple[OpencodeProvider, list[httpx.Request]]:
    """An anonymous provider answering each request with the next reply."""
    requests: list[httpx.Request] = []
    queue = list(replies)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=queue.pop(0), headers={"Content-Type": "text/event-stream"})

    return OpencodeProvider(httpx.Client(transport=httpx.MockTransport(handle)), [Account({}, provider_id="opencode")]), requests


@pytest.mark.parametrize("choice", [None, "none"])
def test_a_call_to_a_tool_the_caller_never_offered_is_answered_here_and_the_model_asked_again(choice: str | None) -> None:
    """The lane's read/shell gate is not the caller's to run: like a coding agent answering a tool it does
    not have ("unsupported call"), the gateway answers it and the caller sees only the reply that follows."""
    provider, requests = _scripted(_chat_reply(text="Let me look. ", calls=[("call_1", "read")]), _chat_reply(text="It says hello.", tokens=(9, 4)))
    body: dict = {"messages": [{"role": "user", "content": "what is in a.txt?"}], "session_id": "chat-1"}
    if choice:
        body["tool_choice"] = choice
    original = copy.deepcopy(body)
    chunks = list(provider.stream_raw(provider.accounts[0], "mimo-v2.6-flash-free", body))
    assert [c["text"] for c in chunks if c["type"] == "text_delta"] == ["Let me look. ", "It says hello."]
    assert not [c for c in chunks if c["type"] == "tool_call_start"]
    assert [c for c in chunks if c["type"] == "usage"] == [{"type": "usage", "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}}]
    assert chunks[-1] == {"type": "finish", "reason": "stop"}
    assert len(requests) == 2 and requests[0].headers["x-opencode-session"] == requests[1].headers["x-opencode-session"]
    follow_up = json.loads(requests[1].content)["messages"]
    assert follow_up[-2] == {"role": "assistant", "content": "Let me look. ", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path": "a.txt"}'}}]}
    assert follow_up[-1] == {"role": "tool", "tool_call_id": "call_1", "content": unsupported_result("read")}
    assert body == original


def test_a_model_that_keeps_calling_them_is_given_up_on() -> None:
    replies = [_chat_reply(calls=[(f"call_{i}", "shell")]) for i in range(MAX_UNSUPPORTED_ROUNDS + 1)]
    provider, requests = _scripted(*replies)
    with pytest.raises(ProviderError, match=r"kept calling tools this request does not offer \(shell\)"):
        list(provider.stream_raw(provider.accounts[0], "mimo-v2.6-flash-free", {"messages": [{"role": "user", "content": "hi"}]}))
    assert len(requests) == MAX_UNSUPPORTED_ROUNDS + 1


def test_next_to_the_caller_s_own_calls_undeclared_ones_are_left_out() -> None:
    provider, requests = _scripted(_chat_reply(calls=[("call_1", "search"), ("call_2", "read")]))
    body = {"messages": [{"role": "user", "content": "find it"}], "tools": [{"type": "function", "function": {"name": "search", "description": "Search", "parameters": {"type": "object"}}}]}
    chunks = list(provider.stream_raw(provider.accounts[0], "mimo-v2.6-flash-free", body))
    assert [(c["id"], c["name"]) for c in chunks if c["type"] == "tool_call_start"] == [("call_1", "search")]
    assert chunks[-1] == {"type": "finish", "reason": "tool_calls"}
    assert len(requests) == 1


def test_a_caller_s_own_read_is_told_apart_from_the_gate_s() -> None:
    """A caller's ``read`` travels as ``read_client``: its call is the caller's, the gate's ``read`` is answered here."""
    provider, requests = _scripted(_chat_reply(calls=[("call_1", "read")]), _chat_reply(calls=[("call_2", "read_client")]))
    body = {"messages": [{"role": "user", "content": "read it"}], "tools": [{"type": "function", "function": {"name": "read", "description": "Mine", "parameters": {"type": "object"}}}]}
    chunks = list(provider.stream_raw(provider.accounts[0], "mimo-v2.6-flash-free", body))
    assert [(c["id"], c["name"]) for c in chunks if c["type"] == "tool_call_start"] == [("call_2", "read")]
    assert len(requests) == 2


def test_the_follow_up_carries_the_answer_on_the_responses_wire() -> None:
    first = "\n".join(_frames(
        '{"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","name":"read","call_id":"call_1","arguments":""}}',
        '{"type":"response.function_call_arguments.delta","output_index":0,"delta":"{}"}',
        '{"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":5,"output_tokens":1}}}',
    ))
    second = "\n".join(_frames(
        '{"type":"response.output_text.delta","output_index":0,"delta":"Done."}',
        '{"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":7,"output_tokens":2}}}',
    ))
    provider, requests = _scripted(first, second)
    chunks = list(provider.stream_raw(provider.accounts[0], "muse-spark-1.3-contributor-free", {"messages": [{"role": "user", "content": "hi"}]}))
    assert [c["text"] for c in chunks if c["type"] == "text_delta"] == ["Done."]
    assert chunks[-1] == {"type": "finish", "reason": "stop"}
    items = json.loads(requests[1].content)["input"]
    assert {"type": "function_call", "call_id": "call_1", "name": "read"}.items() <= next(i for i in items if i.get("type") == "function_call").items()
    output = next(i for i in items if i.get("type") == "function_call_output")
    assert (output["call_id"], output["output"]) == ("call_1", unsupported_result("read"))


# -- chat -> responses ------------------------------------------------------ #


def test_chat_to_responses_shapes_the_request() -> None:
    body = {
        "model": "ignored",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\":\"Paris\"}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ],
        "stream": False,
        "max_tokens": 128,
        "temperature": 0.2,
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
    }
    out = chat_to_responses(body, model="muse-spark-1.2-contributor-free")
    assert out["model"] == "muse-spark-1.2-contributor-free"
    assert out["stream"] is True and out["store"] is False
    assert out["max_output_tokens"] == 128
    assert out["temperature"] == 0.2
    kinds = [(item["type"], item.get("role")) for item in out["input"]]
    assert kinds == [
        ("message", "system"),
        ("message", "user"),
        ("message", "assistant"),
        ("function_call", None),
        ("function_call_output", None),
    ]
    assert out["input"][3]["name"] == "get_weather"
    assert out["tools"][0] == {"type": "function", "name": "get_weather", "parameters": {"type": "object"}}


def test_chat_to_responses_never_sends_an_empty_input() -> None:
    out = chat_to_responses({"messages": []}, model="muse-spark-1.2-contributor-free")
    assert out["input"][0]["type"] == "message"


# -- responses SSE ---------------------------------------------------------- #


def _frames(*events: str) -> list[str]:
    out: list[str] = []
    for event in events:
        out += [f"data: {event}", ""]
    return out + ["data: [DONE]", ""]


def test_responses_stream_yields_text_usage_and_stop() -> None:
    lines = _frames(
        '{"type":"response.created"}',
        '{"type":"response.output_item.added","output_index":0,"item":{"type":"message","role":"assistant","content":[]}}',
        '{"type":"response.output_text.delta","output_index":0,"delta":"Hel"}',
        '{"type":"response.output_text.delta","output_index":0,"delta":"lo"}',
        '{"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":10,"output_tokens":4,"output_tokens_details":{"reasoning_tokens":1}}}}',
    )
    chunks = list(iter_responses_chunks(lines))
    assert [(c["type"], c.get("text")) for c in chunks[:2]] == [("text_delta", "Hel"), ("text_delta", "lo")]
    usage = next(c for c in chunks if c["type"] == "usage")["usage"]
    assert usage["prompt_tokens"] == 10 and usage["completion_tokens"] == 4
    assert usage["completion_tokens_details"]["reasoning_tokens"] == 1
    assert chunks[-1] == {"type": "finish", "reason": "stop"}


def test_responses_stream_reports_length_and_tool_calls() -> None:
    tool_lines = _frames(
        '{"type":"response.output_item.added","output_index":1,"item":{"type":"function_call","name":"get_weather","call_id":"call_9","arguments":""}}',
        '{"type":"response.function_call_arguments.delta","output_index":1,"delta":"{\\"city\\": "}',
        '{"type":"response.function_call_arguments.delta","output_index":1,"delta":"\\"Paris\\"}"}',
        '{"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":5,"output_tokens":6}}}',
    )
    chunks = list(iter_responses_chunks(tool_lines))
    start = next(c for c in chunks if c["type"] == "tool_call_start")
    assert start["name"] == "get_weather" and start["id"] == "call_9"
    assert start["arguments"] == '{"city": "Paris"}'
    assert chunks[-1] == {"type": "finish", "reason": "tool_calls"}

    length_lines = _frames(
        '{"type":"response.output_text.delta","output_index":0,"delta":"x"}',
        '{"type":"response.incomplete","response":{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}}}',
    )
    assert list(iter_responses_chunks(length_lines))[-1] == {"type": "finish", "reason": "length"}


def test_responses_stream_surfaces_upstream_errors() -> None:
    lines = _frames('{"type":"error","error":{"type":"RateLimitError","message":"free tier exhausted"}}')
    chunks = list(iter_responses_chunks(lines))
    assert chunks == [{"type": "error", "error": "free tier exhausted"}]


def _anthropic(*events: dict) -> str:
    return "\n".join(line for e in events for line in (f"event: {e['type']}", f"data: {json.dumps(e)}", ""))


def test_the_follow_up_carries_the_answer_on_the_messages_wire() -> None:
    first = _anthropic(
        {"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "shell", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"command": "ls"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    )
    second = _anthropic(
        {"type": "message_start", "message": {"usage": {"input_tokens": 9, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "No shell here."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
        {"type": "message_stop"},
    )
    provider, requests = _scripted(first, second)
    chunks = list(provider.stream_raw(provider.accounts[0], "union-alpha", {"messages": [{"role": "user", "content": "list files"}]}))
    assert [c["text"] for c in chunks if c["type"] == "text_delta"] == ["No shell here."]
    assert not [c for c in chunks if c["type"] == "tool_call_start"]
    messages = json.loads(requests[1].content)["messages"]
    use = next(b for m in messages if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use")
    result = next(b for m in messages if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result")
    assert (use["id"], use["name"], use["input"]) == ("toolu_1", "shell", {"command": "ls"})
    assert (result["tool_use_id"], result["content"]) == ("toolu_1", unsupported_result("shell"))
