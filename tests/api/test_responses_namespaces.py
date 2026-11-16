"""Responses namespaces survive the Chat transport and follow-up requests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.core.providers.base import Account
from tests.conftest import FakeProvider, make_services


class NamespaceProvider(FakeProvider):
    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.seen.append(body)
        if len(self.seen) > 1:
            yield {"type": "text_delta", "text": "read complete"}
            yield {"type": "finish", "reason": "stop"}
            return
        for index, tool in enumerate(body["tools"]):
            yield {"type": "tool_call_start", "index": index, "id": f"call-{index}", "name": tool["function"]["name"], "arguments": ""}
            yield {"type": "tool_call_delta", "index": index, "arguments": '{"path":"probe.txt"}'}
        yield {"type": "finish", "reason": "tool_calls"}


@pytest.mark.parametrize("stream", [False, True])
def test_namespace_call_schema_and_history_roundtrip(stream: bool) -> None:
    provider = NamespaceProvider()
    function = {"type": "function", "name": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}
    namespaces = ["mcp__first", "mcp__second", "long_namespace_" * 7]
    tools = [{"type": "namespace", "name": ns, "description": "Filesystem", "tools": [function]} for ns in namespaces] + [function]
    body = {"model": "fake0/fake-1", "stream": stream, "input": "read", "tools": tools, "tool_choice": {"type": "function", "namespace": namespaces[0], "name": "read"}}
    with TestClient(create_app(make_services(provider), serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/responses", json=body)
        assert response.status_code == 200
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
            output = events[-1]["response"]["output"]
            assert [event["item"] for event in events if event["type"] == "response.output_item.done"] == output
            added = [event["item"] for event in events if event["type"] == "response.output_item.added"]
            assert [(item.get("namespace"), item["name"]) for item in added] == [(ns, "read") for ns in namespaces] + [(None, "read")]
        else:
            output = response.json()["output"]
        assert [(item.get("namespace"), item["name"]) for item in output] == [(ns, "read") for ns in namespaces] + [(None, "read")]
        sent = provider.seen[0]
        names = [tool["function"]["name"] for tool in sent["tools"]]
        assert len(set(names)) == 4
        assert all(len(name) <= 64 for name in names)
        assert all(tool["function"]["parameters"] == function["parameters"] for tool in sent["tools"])
        assert all(tool["function"]["description"] == "Filesystem" for tool in sent["tools"][:3])
        assert sent["tool_choice"] == {"type": "function", "function": {"name": names[0]}}
        history = [{"role": "user", "content": "read"}, *output, *({"type": "function_call_output", "call_id": item["call_id"], "output": "OK"} for item in output)]
        followup = client.post("/v1/responses", json={**body, "stream": False, "input": history, "tool_choice": "auto"})
        assert followup.status_code == 200
        calls = provider.seen[1]["messages"][1]["tool_calls"]
        assert [call["function"]["name"] for call in calls] == names
        assert [call["id"] for call in calls] == [message["tool_call_id"] for message in provider.seen[1]["messages"][2:]]


def test_namespace_alias_collision_is_reported_instead_of_misrouting() -> None:
    provider = NamespaceProvider()
    tools = [
        {"type": "namespace", "name": "a", "tools": [{"type": "function", "name": "b"}]},
        {"type": "function", "name": "a__b"},
    ]
    with TestClient(create_app(make_services(provider), serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/v1/responses", json={"model": "fake0/fake-1", "input": "read", "tools": tools})
        assert response.status_code == 400
        assert "collide" in response.text
        assert provider.seen == []
