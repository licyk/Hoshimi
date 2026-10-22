"""The Playground's conversations over HTTP: streamed replies, edits, retries, branches, titles, attachments and images."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from hoshimi.api.playground import TITLE_PROMPT, VISION_PROMPT, PlaygroundRunner, clean_title
from hoshimi.core.catalog import ModelSpec
from hoshimi.core.context import Services
from hoshimi.core.providers.base import Account
from tests.conftest import FakeProvider, make_services

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


class SightProvider(FakeProvider):
    """Models that do and do not read images, one whose station refuses them anyway, one that fails, and one that describes."""

    def fallback_models(self) -> list[str]:
        return ["seer", "blind", "claims", "broken", "thinker"]

    def specs(self) -> dict[str, ModelSpec]:
        specs = super().specs()
        for name in ("seer", "claims"):
            specs[name] = replace(specs[name], vision=True)
        return specs

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.seen.append({"model": model, **body})
        messages = body.get("messages") or []
        last = messages[-1]["content"] if messages else ""
        images = any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"]) for m in messages)
        if model == "thinker":
            for piece in ("First, ", "consider the question."):
                yield {"type": "reasoning_delta", "text": piece}
            yield {"type": "text_delta", "text": "The answer."}
            return
        if model == "broken":
            yield {"type": "error", "message": "the station is down"}
            return
        if model == "claims" and images:
            yield {"type": "error", "message": "This model does not support image input"}
            return
        if isinstance(last, list) and last and last[0].get("text") == VISION_PROMPT:
            yield {"type": "text_delta", "text": "A red square with the word HELLO."}
            return
        yield {"type": "text_delta", "text": f"{model} saw " + ("an image" if images else "text")}


def _events(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _send(client: Any, cid: str, content: str, **body: Any) -> list[dict[str, Any]]:
    return _events(client.post(f"/api/v1/playground/conversations/{cid}/messages", json={"content": content, **body}))


def _new(client: Any, **body: Any) -> str:
    response = client.post("/api/v1/playground/conversations", json={"model": "fake-1", **body})
    assert response.status_code == 201
    return response.json()["id"]


@pytest.fixture
def sight() -> SightProvider:
    return SightProvider()


@pytest.fixture
def services(sight: SightProvider) -> Services:
    return make_services(sight)


def test_a_send_streams_the_reply_and_keeps_both(client: Any) -> None:
    cid = _new(client)
    events = _send(client, cid, "What is this?")
    assert [e["type"] for e in events] == ["user", "assistant", "delta", "delta", "done"]
    question, reply = events[0]["message"], events[-1]["message"]
    assert (question["role"], question["parent_id"], question["content"]) == ("user", None, "What is this?")
    assert (reply["parent_id"], reply["status"], reply["content"], reply["model"]) == (question["id"], "complete", "echo:fake-1:1", "fake-1")
    got = client.get(f"/api/v1/playground/conversations/{cid}").json()
    assert got["current_leaf"] == reply["id"]
    assert (got["title"], got["title_state"]) == ("What is this?", "provisional")
    assert [m["id"] for m in got["messages"]] == [question["id"], reply["id"]]


def test_the_reply_follows_the_path_on_screen(client: Any) -> None:
    cid = _new(client)
    first = _send(client, cid, "one")
    second = _send(client, cid, "two", parent_id=first[-1]["message"]["id"])
    # The fake model answers with how many messages it was sent: the whole path.
    assert second[-1]["message"]["content"] == "echo:fake-1:3"
    # An edit of the first question: a sibling of it, answered on its own.
    edit = _send(client, cid, "one, edited", parent_id=None)
    assert edit[-1]["message"]["content"] == "echo:fake-1:1"
    got = client.get(f"/api/v1/playground/conversations/{cid}").json()
    roots = [m for m in got["messages"] if m["parent_id"] is None]
    assert [m["content"] for m in roots] == ["one", "one, edited"]
    assert got["current_leaf"] == edit[-1]["message"]["id"]


def test_a_retry_is_a_second_reply_beside_the_first_and_versions_can_be_switched(client: Any) -> None:
    cid = _new(client)
    first = _send(client, cid, "hello")[-1]["message"]
    retried = _events(client.post(f"/api/v1/playground/conversations/{cid}/messages/{first['id']}/retry", json={"model": "fake-2"}))
    assert retried[0]["type"] == "assistant"
    second = retried[-1]["message"]
    assert (second["parent_id"], second["model"], second["content"]) == (first["parent_id"], "fake-2", "echo:fake-2:1")
    assert client.get(f"/api/v1/playground/conversations/{cid}").json()["current_leaf"] == second["id"]
    # The picker stays on the conversation's model: a retry with another is a one-off.
    assert client.get(f"/api/v1/playground/conversations/{cid}").json()["model"] == "fake-1"
    switched = client.patch(f"/api/v1/playground/conversations/{cid}", json={"current_leaf": first["id"]})
    assert switched.status_code == 200
    assert client.get(f"/api/v1/playground/conversations/{cid}").json()["current_leaf"] == first["id"]
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"current_leaf": "missing"}).status_code == 400
    # A question left unanswered can be answered by retrying it.
    question = first["parent_id"]
    again = _events(client.post(f"/api/v1/playground/conversations/{cid}/messages/{question}/retry", json={}))
    assert again[-1]["message"]["parent_id"] == question


def test_a_branch_is_a_new_conversation_up_to_a_message(client: Any) -> None:
    cid = _new(client)
    first = _send(client, cid, "hello")[-1]["message"]
    _send(client, cid, "and more", parent_id=first["id"])
    branched = client.post(f"/api/v1/playground/conversations/{cid}/branch", json={"message_id": first["id"]})
    assert branched.status_code == 201
    branch = branched.json()
    assert branch["branched_from"] == cid
    assert [m["content"] for m in branch["messages"]] == ["hello", "echo:fake-1:1"]
    assert {c["id"] for c in client.get("/api/v1/playground/conversations").json()["conversations"]} == {cid, branch["id"]}
    assert client.post(f"/api/v1/playground/conversations/{cid}/branch", json={"message_id": "missing"}).status_code == 400


def test_a_title_is_written_by_a_model_once_and_never_over_the_user_s(client: Any, services: Services) -> None:
    cid = _new(client)
    _send(client, cid, "hello there")
    titled = client.post(f"/api/v1/playground/conversations/{cid}/title").json()
    assert (titled["title"], titled["title_state"]) == ("echo:fake-1:2", "auto")
    fake = services.registry.providers["fake"]
    asked = [b for b in fake.seen if b["messages"][0].get("content") == TITLE_PROMPT]  # type: ignore[attr-defined]
    assert len(asked) == 1 and "User: hello there" in asked[0]["messages"][1]["content"]
    # Asked again, it is not renamed.
    assert client.post(f"/api/v1/playground/conversations/{cid}/title").json()["title"] == "echo:fake-1:2"
    named = _new(client)
    _send(client, named, "question")
    client.patch(f"/api/v1/playground/conversations/{named}", json={"title": "Mine"})
    assert client.post(f"/api/v1/playground/conversations/{named}/title").json()["title"] == "Mine"


@pytest.mark.parametrize(("raw", "title"), [("Title: “Setting up Hoshimi”.", "Setting up Hoshimi"), ("\n\n**Gateway basics**\nmore", "Gateway basics"), ("标题：网关入门。", "网关入门")])
def test_a_model_s_title_is_cleaned(raw: str, title: str) -> None:
    assert clean_title(raw) == title


def test_a_temporary_conversation_works_but_is_never_listed_titled_or_resumed(client: Any) -> None:
    cid = _new(client, temporary=True)
    assert _send(client, cid, "off the record")[-1]["message"]["status"] == "complete"
    assert client.get("/api/v1/playground/conversations").json()["conversations"] == []
    assert client.post(f"/api/v1/playground/conversations/{cid}/title").json()["title_state"] == "provisional"
    client.put("/api/v1/playground/session", json={"conversation_id": cid})
    assert client.get("/api/v1/playground/session").json() == {"conversation_id": None}


def test_a_failed_reply_is_kept_with_its_error(client: Any) -> None:
    cid = _new(client, model="broken")
    reply = _send(client, cid, "hello")[-1]["message"]
    assert (reply["status"], reply["error"], reply["content"]) == ("error", "the station is down", "")


def test_sending_needs_a_message_or_a_file_and_a_known_parent(client: Any) -> None:
    cid = _new(client)
    assert client.post(f"/api/v1/playground/conversations/{cid}/messages", json={"content": "  "}).status_code == 400
    assert client.post(f"/api/v1/playground/conversations/{cid}/messages", json={"content": "x", "parent_id": "missing"}).status_code == 400
    assert client.post("/api/v1/playground/conversations/missing/messages", json={"content": "x"}).status_code == 404


def test_the_system_prompt_goes_first_and_the_request_can_be_copied(client: Any) -> None:
    client.patch("/api/v1/settings", json={"playground": {"system_prompt": "Be brief."}})
    cid = _new(client)
    reply = _send(client, cid, "hi")[-1]["message"]
    assert reply["content"] == "echo:fake-1:2"
    request = client.get(f"/api/v1/playground/conversations/{cid}/messages/{reply['id']}/request").json()
    assert request["model"] == "fake-1" and request["stream"] is True
    assert request["messages"] == [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]
    # The old stateless route keeps its behaviour, for the API's own clients.
    old = client.post("/api/v1/playground/chat/completions", json={"model": "fake-1", "messages": [{"role": "user", "content": "hi"}], "stream": False})
    assert old.json()["choices"][0]["message"]["content"] == "echo:fake-1:2"


def _upload(client: Any, data: bytes, content_type: str, name: str, **query: Any) -> Any:
    return client.post("/api/v1/playground/attachments", params={"name": name, **query}, content=data, headers={"Content-Type": content_type})


def test_attachments_are_uploaded_served_and_taken_back(client: Any) -> None:
    image = _upload(client, PNG, "image/png", "dot.png", width=1, height=1)
    assert image.status_code == 201
    assert {k: image.json()[k] for k in ("kind", "name", "mime", "size", "width")} == {"kind": "image", "name": "dot.png", "mime": "image/png", "size": len(PNG), "width": 1}
    served = client.get(f"/api/v1/playground/attachments/{image.json()['id']}")
    assert (served.content, served.headers["content-type"]) == (PNG, "image/png")
    pdf = _upload(client, b"Page one text", "text/plain; charset=utf-8", "report.pdf", type="application/pdf")
    assert (pdf.json()["kind"], pdf.json()["mime"]) == ("file", "application/pdf")
    assert client.get(f"/api/v1/playground/attachments/{pdf.json()['id']}").text == "Page one text"
    assert _upload(client, b"\0\1", "application/octet-stream", "blob.bin").status_code == 400
    assert _upload(client, b"", "image/png", "empty.png").status_code == 400
    cid = _new(client)
    _send(client, cid, "see", attachment_ids=[image.json()["id"]])
    assert client.delete(f"/api/v1/playground/attachments/{image.json()['id']}").status_code == 400
    assert client.delete(f"/api/v1/playground/attachments/{pdf.json()['id']}").status_code == 204
    assert client.get(f"/api/v1/playground/attachments/{pdf.json()['id']}").status_code == 404


def test_a_file_goes_inline_under_its_name(client: Any, services: Services) -> None:
    note = _upload(client, b"line one\nline two", "text/markdown", "notes.md").json()
    cid = _new(client)
    reply = _send(client, cid, "Summarise", attachment_ids=[note["id"]])[-1]["message"]
    assert reply["status"] == "complete"
    sent = services.registry.providers["fake"].seen[-1]["messages"][-1]["content"]  # type: ignore[attr-defined]
    assert sent == 'Summarise\n\n<file name="notes.md">\nline one\nline two\n</file>'
    got = client.get(f"/api/v1/playground/conversations/{cid}").json()
    assert [a["name"] for a in got["messages"][0]["attachments"]] == ["notes.md"]


def test_an_image_goes_as_an_image_to_a_model_that_reads_them(client: Any, sight: SightProvider) -> None:
    image = _upload(client, PNG, "image/png", "dot.png").json()
    cid = _new(client, model="seer")
    events = _send(client, cid, "What is it?", attachment_ids=[image["id"]])
    assert events[-1]["message"]["content"] == "seer saw an image"
    assert not [e for e in events if e["type"] == "notice"]
    content = sight.seen[-1]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "What is it?"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_an_image_is_described_once_for_a_model_that_cannot_read_it(client: Any, sight: SightProvider) -> None:
    image = _upload(client, PNG, "image/png", "dot.png").json()
    cid = _new(client, model="blind")
    events = _send(client, cid, "What is it?", attachment_ids=[image["id"]])
    kinds = [e["type"] for e in events]
    assert kinds.index("stage") < kinds.index("delta")
    assert next(e for e in events if e["type"] == "stage") == {"type": "stage", "stage": "describing", "model": "seer"}
    assert events[-1]["message"]["content"] == "blind saw text"
    assert events[-1]["message"]["notices"] == [{"kind": "image-described", "name": "dot.png", "model": "seer"}]
    sent = sight.seen[-1]
    assert sent["model"] == "blind"
    assert "A red square with the word HELLO." in sent["messages"][-1]["content"]
    # Kept with the image: the next reply does not ask again.
    describes = len([b for b in sight.seen if b["model"] == "seer"])
    followup = _send(client, cid, "And its colour?", parent_id=events[-1]["message"]["id"])
    assert "stage" not in [e["type"] for e in followup]
    assert len([b for b in sight.seen if b["model"] == "seer"]) == describes


def test_with_describing_off_an_image_is_left_out_and_the_reply_says_so(client: Any, sight: SightProvider) -> None:
    client.patch("/api/v1/settings", json={"playground": {"vision_model": "off"}})
    image = _upload(client, PNG, "image/png", "dot.png").json()
    cid = _new(client, model="blind")
    reply = _send(client, cid, "What is it?", attachment_ids=[image["id"]])[-1]["message"]
    assert reply["notices"] == [{"kind": "image-omitted", "name": "dot.png", "model": ""}]
    assert "left out" in sight.seen[-1]["messages"][-1]["content"]


def test_a_station_that_refuses_images_is_asked_again_with_descriptions(client: Any) -> None:
    image = _upload(client, PNG, "image/png", "dot.png").json()
    cid = _new(client, model="claims")
    reply = _send(client, cid, "What is it?", attachment_ids=[image["id"]])[-1]["message"]
    assert (reply["status"], reply["content"]) == ("complete", "claims saw text")
    assert [n["kind"] for n in reply["notices"]] == ["image-refused", "image-described"]


def test_a_reply_stopped_midway_is_kept_as_stopped(services: Services) -> None:
    conversation = services.conversations.create(model="fake-1")
    question = services.conversations.add_message(conversation.id, None, "user", "hello")
    assert question is not None
    events = PlaygroundRunner(services).reply(conversation.id, question.id, model="fake-1", effort="")
    assert next(events)["type"] == "assistant"
    assert next(events) == {"type": "delta", "text": "echo:fake-1:"}
    events.close()
    reply = services.conversations.path(conversation.id)[-1]
    assert (reply.status, reply.content) == ("stopped", "echo:fake-1:")


def test_the_session_remembers_the_open_conversation(client: Any) -> None:
    assert client.get("/api/v1/playground/session").json() == {"conversation_id": None}
    cid = _new(client)
    assert client.put("/api/v1/playground/session", json={"conversation_id": cid}).json() == {"conversation_id": cid}
    assert client.put("/api/v1/playground/session", json={"conversation_id": "missing"}).status_code == 400
    client.delete(f"/api/v1/playground/conversations/{cid}")
    assert client.get("/api/v1/playground/session").json() == {"conversation_id": None}


def test_archive_and_list(client: Any) -> None:
    cid = _new(client)
    _send(client, cid, "hello")
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"archived": True}).json()["archived"] is True
    assert client.get("/api/v1/playground/conversations").json()["conversations"] == []
    assert [c["id"] for c in client.get("/api/v1/playground/conversations", params={"archived": True}).json()["conversations"]] == [cid]
    assert client.delete(f"/api/v1/playground/conversations/{cid}").status_code == 204
    assert client.get(f"/api/v1/playground/conversations/{cid}").status_code == 404


def test_a_reply_s_reasoning_streams_first_and_is_kept_with_how_long_it_took(client: Any) -> None:
    cid = _new(client, model="thinker")
    events = _send(client, cid, "Think about it")
    kinds = [e["type"] for e in events]
    assert kinds == ["user", "assistant", "reasoning", "reasoning", "delta", "done"]
    assert "".join(e["text"] for e in events if e["type"] == "reasoning") == "First, consider the question."
    reply = events[-1]["message"]
    assert (reply["content"], reply["reasoning"]) == ("The answer.", "First, consider the question.")
    assert reply["reasoning_ms"] >= 0
    kept = client.get(f"/api/v1/playground/conversations/{cid}").json()["messages"][-1]
    assert kept["reasoning"] == "First, consider the question."
    # A model that shows no reasoning keeps none.
    plain = _send(client, _new(client), "hello")[-1]["message"]
    assert (plain["reasoning"], plain["reasoning_ms"]) == ("", 0)
    assert "reasoning" not in [e["type"] for e in _send(client, _new(client), "again")]


def test_a_reply_stopped_while_reasoning_keeps_what_it_reasoned(services: Services) -> None:
    conversation = services.conversations.create(model="thinker")
    question = services.conversations.add_message(conversation.id, None, "user", "hmm")
    assert question is not None
    events = PlaygroundRunner(services).reply(conversation.id, question.id, model="thinker", effort="")
    assert [next(events)["type"] for _ in range(2)] == ["assistant", "reasoning"]
    events.close()
    reply = services.conversations.path(conversation.id)[-1]
    assert (reply.status, reply.content, reply.reasoning) == ("stopped", "", "First, ")
