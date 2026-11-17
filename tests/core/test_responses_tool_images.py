from __future__ import annotations

from copy import deepcopy

import pytest

from hoshimi.core.qoder.protocol import transform_messages
from hoshimi.core.translate.responses import responses_to_chat


@pytest.mark.parametrize("kind", ["function", "custom_tool"])
@pytest.mark.parametrize("followup", [None, "user", "assistant"])
def test_tool_images_survive_responses_and_qoder_translation(kind: str, followup: str | None) -> None:
    urls = ["data:image/png;base64,aW1hZ2Ux", "data:image/webp;base64,aW1hZ2Uy"]
    items = [{"role": "user", "content": "view both"}]
    for index in range(2):
        items.append({"type": f"{kind}_call", "call_id": f"call-{index}", "name": "view_image", "arguments": "{}", "input": "image"})
    for index, url in enumerate(urls):
        items.append({
            "type": f"{kind}_call_output", "call_id": f"call-{index}",
            "output": [
                {"type": "input_text", "text": f"image {index}"},
                {"type": "input_image", "image_url": url, "detail": "high"},
            ],
        })
    if followup:
        items.append({"role": followup, "content": "next turn"})
    original = deepcopy(items)
    messages = responses_to_chat({"input": items})["messages"]
    assert items == original
    expected_roles = ["user", "assistant", "tool", "tool", "user"] + ([followup] if followup else [])
    assert [message["role"] for message in messages] == expected_roles
    assert [message["tool_call_id"] for message in messages[2:4]] == ["call-0", "call-1"]
    for index, message in enumerate(messages[2:4]):
        assert message["content"].startswith(f"image {index}")
        assert "base64" not in message["content"]
    images = messages[4]["content"]
    for index, url in enumerate(urls):
        assert f"call-{index}" in images[index * 2]["text"]
        assert images[index * 2 + 1] == {"type": "image_url", "image_url": {"url": url, "detail": "high"}}
    wire, _ = transform_messages(messages)
    assert [message["role"] for message in wire] == expected_roles
    assert [part["image_url"]["url"] for part in wire[4]["content"] if part["type"] == "image_url"] == urls


def test_image_only_output_is_not_lost_and_plain_outputs_stay_plain() -> None:
    url = "data:image/png;base64,aW1hZ2U="
    messages = responses_to_chat({"input": [
        {"type": "function_call_output", "call_id": "plain", "output": "plain text"},
        {"type": "function_call_output", "call_id": "blocks", "output": [{"type": "input_text", "text": "block text"}]},
        {"type": "function_call_output", "call_id": "image", "output": [{"type": "input_image", "image_url": url}]},
    ]})["messages"]
    assert [message["role"] for message in messages] == ["tool", "tool", "tool", "user"]
    assert messages[0]["content"] == "plain text"
    assert messages[1]["content"] == "block text"
    assert messages[-1]["content"][1] == {"type": "image_url", "image_url": {"url": url}}
