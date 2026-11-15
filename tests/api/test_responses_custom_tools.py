"""Custom tool transport, including its next request, through the HTTP API."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.core.providers.base import Account
from tests.conftest import FakeProvider, make_services

PATCH = "*** Begin Patch\n*** Add File: 中文.txt\n+hello \"world\"\n*** End Patch"
TOOLS = [
    {"type": "custom", "name": "apply_patch", "description": "Edit a file.", "format": {"type": "text"}},
    {"type": "function", "name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
]


class PatchProvider(FakeProvider):
    """Emit function arguments as a Chat-only upstream would, in small chunks."""

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.seen.append(body)
        if len(self.seen) > 1:
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "finish", "reason": "stop"}
            return
        yield {"type": "tool_call_start", "index": 0, "id": "patch-1", "name": "apply_patch", "arguments": ""}
        for character in json.dumps({"input": PATCH}, ensure_ascii=False):
            yield {"type": "tool_call_delta", "index": 0, "arguments": character}
        yield {"type": "tool_call_start", "index": 1, "id": "read-1", "name": "read_file", "arguments": '{"path":"a"}'}
        yield {"type": "finish", "reason": "tool_calls"}


@pytest.mark.parametrize("stream", [False, True])
def test_custom_tool_roundtrip_preserves_input_and_call_result_pairs(stream: bool) -> None:
    provider = PatchProvider()
    services = make_services(provider)
    body = {"model": "fake0/fake-1", "stream": stream, "input": "edit", "tools": TOOLS, "tool_choice": {"type": "custom", "name": "apply_patch"}}
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/responses", json=body)
        assert response.status_code == 200
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
            assert [e["sequence_number"] for e in events] == list(range(len(events)))
            deltas = [e["delta"] for e in events if e["type"] == "response.custom_tool_call_input.delta"]
            assert "".join(deltas) == PATCH
            assert not any(e["type"].startswith("response.function_call_arguments.") and e["item_id"] == "patch-1" for e in events)
            output = events[-1]["response"]["output"]
            assert [e["item"] for e in events if e["type"] == "response.output_item.done"] == output
        else:
            output = response.json()["output"]
        assert output[0] == {"type": "custom_tool_call", "id": "patch-1", "call_id": "patch-1", "name": "apply_patch", "input": PATCH}
        assert output[1]["type"] == "function_call"
        first = provider.seen[0]
        assert first["tools"][0]["function"]["parameters"]["required"] == ["input"]
        assert first["tool_choice"] == {"type": "function", "function": {"name": "apply_patch"}}
        history = [
            {"role": "user", "content": "edit"}, *output,
            {"type": "custom_tool_call_output", "call_id": "patch-1", "output": "patched"},
            {"type": "function_call_output", "call_id": "read-1", "output": "read"},
        ]
        followup = client.post("/v1/responses", json={**body, "stream": False, "input": history, "tool_choice": "auto"})
        assert followup.status_code == 200
        messages = provider.seen[1]["messages"]
        assert messages[:1] == first["messages"]
        assert provider.seen[1]["tools"] == first["tools"]
        assert [m["role"] for m in messages] == ["user", "assistant", "tool", "tool"]
        calls = messages[1]["tool_calls"]
        assert json.loads(calls[0]["function"]["arguments"]) == {"input": PATCH}
        assert calls[0]["id"] == messages[2]["tool_call_id"] == "patch-1"
        assert calls[1]["id"] == messages[3]["tool_call_id"] == "read-1"
