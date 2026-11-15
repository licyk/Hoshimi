from __future__ import annotations

import pytest

from hoshimi.core.translate.responses import ResponsesRequestError, responses_to_chat


def test_instructions_become_system_message() -> None:
    body = responses_to_chat({"model": "m", "instructions": "be brief", "input": "hi"})
    assert body["model"] == "m"
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"] == "hi"


def test_input_items_are_converted() -> None:
    body = responses_to_chat(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": "question"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]},
                {"type": "function_call", "call_id": "c1", "name": "fn", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c1", "output": "ok"},
            ],
        }
    )
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "tool"]
    assert body["messages"][1]["content"] == "answer"
    assert body["messages"][2]["tool_call_id"] == "c1"


def test_parallel_calls_remain_one_assistant_turn_through_qoder_translation() -> None:
    from hoshimi.core.qoder.protocol import transform_messages

    body = responses_to_chat({
        "model": "qodercli-cn/DeepSeek-Flash",
        "input": [
            {"role": "user", "content": "search twice"},
            {"type": "reasoning", "provider_details": [{"text": "plan"}]},
            {"type": "message", "role": "assistant", "content": "Searching."},
            {"type": "function_call", "call_id": "call-1", "name": "web_search", "arguments": '{"query":"one"}'},
            {"type": "function_call", "call_id": "call-2", "name": "web_search", "arguments": '{"query":"two"}'},
            {"type": "function_call_output", "call_id": "call-2", "output": "second result"},
            {"type": "function_call_output", "call_id": "call-1", "output": "first result"},
            {"type": "function_call", "call_id": "call-3", "name": "web_fetch", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call-3", "output": "page"},
            {"role": "user", "content": "continue"},
        ],
    })
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "tool", "assistant", "tool", "user"]
    assert messages[1]["content"] == "Searching."
    assert messages[1]["reasoning_details"] == [{"text": "plan"}]
    assert [c["id"] for c in messages[1]["tool_calls"]] == ["call-1", "call-2"]
    assert messages[1]["tool_calls"][1]["function"]["arguments"] == '{"query":"two"}'
    wire, _ = transform_messages(messages)
    assert [c["id"] for c in wire[1]["tool_calls"]] == ["call-1", "call-2"]
    assert [m["tool_call_id"] for m in wire[2:4]] == ["call-2", "call-1"]
    assert wire[4]["tool_calls"][0]["id"] == wire[5]["tool_call_id"] == "call-3"


def test_merging_assistant_items_does_not_mutate_input_or_cross_user_boundaries() -> None:
    from copy import deepcopy

    items = [
        {"role": "assistant", "content": "before", "tool_calls": []},
        {"type": "reasoning", "provider_details": [{"text": "reason"}]},
        {"type": "function_call", "call_id": "one", "name": "f", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "one", "output": "ok"},
        {"role": "assistant", "content": "after"},
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "new"},
    ]
    original = deepcopy(items)
    messages = responses_to_chat({"input": items})["messages"]
    assert items == original
    assert [m["role"] for m in messages] == ["assistant", "tool", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "before"
    assert messages[-1]["content"] == "new"


def test_rejects_previous_response_id() -> None:
    with pytest.raises(ResponsesRequestError):
        responses_to_chat({"model": "m", "previous_response_id": "resp_1", "input": "x"})


def test_rejects_structured_output() -> None:
    with pytest.raises(ResponsesRequestError):
        responses_to_chat(
            {"model": "m", "input": "x", "text": {"format": {"type": "json_object"}}}
        )


def test_tools_are_translated() -> None:
    body = responses_to_chat(
        {
            "model": "m",
            "input": "x",
            "tools": [
                {
                    "type": "function",
                    "name": "fn",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        }
    )
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "fn"


def test_custom_tool_grammar_is_preserved_as_input_guidance() -> None:
    grammar = 'start: "*** Begin Patch" /[\\s\\S]*/ "*** End Patch"'
    body = responses_to_chat({
        "input": "edit",
        "tools": [{"type": "custom", "name": "apply_patch", "description": "Edit files.", "format": {"type": "grammar", "syntax": "lark", "definition": grammar}}],
    })
    tool = body["tools"][0]["function"]
    assert tool["name"] == "apply_patch"
    assert tool["parameters"]["properties"]["input"] == {"type": "string"}
    assert "Edit files." in tool["description"]
    assert grammar in tool["description"]
