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
    assert roles == ["user", "assistant", "assistant", "tool"]
    assert body["messages"][3]["tool_call_id"] == "c1"


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
