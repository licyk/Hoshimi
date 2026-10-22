"""The Playground's conversation routes and the system prompt from Settings."""

from __future__ import annotations

from typing import Any


def test_a_conversation_from_creation_to_deletion(client: Any) -> None:
    created = client.post("/api/v1/playground/conversations", json={"model": "fake-1", "effort": "deep"})
    assert created.status_code == 201
    cid = created.json()["id"]
    assert created.json()["messages"] == []

    for role, content in (("user", "What is this?"), ("assistant", "A gateway.")):
        appended = client.post(f"/api/v1/playground/conversations/{cid}/messages", json={"role": role, "content": content, "model": "fake-1" if role == "assistant" else ""})
        assert appended.status_code == 201
    got = client.get(f"/api/v1/playground/conversations/{cid}").json()
    assert got["title"] == "What is this?"
    assert [(m["role"], m["content"]) for m in got["messages"]] == [("user", "What is this?"), ("assistant", "A gateway.")]

    listed = client.get("/api/v1/playground/conversations").json()["conversations"]
    assert [c["id"] for c in listed] == [cid]
    assert "messages" not in listed[0]

    archived = client.patch(f"/api/v1/playground/conversations/{cid}", json={"archived": True})
    assert archived.json()["archived"] is True
    assert client.get("/api/v1/playground/conversations").json()["conversations"] == []
    assert [c["id"] for c in client.get("/api/v1/playground/conversations?archived=true").json()["conversations"]] == [cid]

    assert client.delete(f"/api/v1/playground/conversations/{cid}").status_code == 204
    assert client.get(f"/api/v1/playground/conversations/{cid}").status_code == 404
    assert client.delete(f"/api/v1/playground/conversations/{cid}").status_code == 404
    assert client.post(f"/api/v1/playground/conversations/{cid}/messages", json={"role": "user", "content": "x"}).status_code == 404


def test_a_message_must_be_the_user_s_or_the_assistant_s(client: Any) -> None:
    cid = client.post("/api/v1/playground/conversations", json={}).json()["id"]
    assert client.post(f"/api/v1/playground/conversations/{cid}/messages", json={"role": "system", "content": "x"}).status_code == 422


def test_the_session_remembers_the_open_conversation(client: Any) -> None:
    assert client.get("/api/v1/playground/session").json() == {"conversation_id": None}
    cid = client.post("/api/v1/playground/conversations", json={}).json()["id"]
    assert client.put("/api/v1/playground/session", json={"conversation_id": cid}).json() == {"conversation_id": cid}
    assert client.get("/api/v1/playground/session").json() == {"conversation_id": cid}
    assert client.put("/api/v1/playground/session", json={"conversation_id": "missing"}).status_code == 400
    # A deleted conversation is not reopened.
    client.delete(f"/api/v1/playground/conversations/{cid}")
    assert client.get("/api/v1/playground/session").json() == {"conversation_id": None}


def _reply(client: Any, messages: list[dict[str, str]]) -> str:
    response = client.post("/api/v1/playground/chat/completions", json={"model": "fake-1", "messages": messages, "stream": False})
    assert response.status_code == 200
    return response.json()["choices"][0]["message"]["content"]


def test_the_system_prompt_from_settings_goes_first(client: Any) -> None:
    question = [{"role": "user", "content": "hi"}]
    # The fake model answers with how many messages it was sent.
    assert _reply(client, question) == "echo:fake-1:1"
    saved = client.patch("/api/v1/settings", json={"playground": {"system_prompt": "  Be brief.  "}})
    assert saved.json()["playground"] == {"system_prompt": "Be brief."}
    assert _reply(client, question) == "echo:fake-1:2"
    # A request with a system message of its own keeps it, alone.
    assert _reply(client, [{"role": "system", "content": "own"}, *question]) == "echo:fake-1:2"
    # The public endpoint never gets it.
    public = client.post("/v1/chat/completions", json={"model": "fake-1", "messages": question, "stream": False}).json()
    assert public["choices"][0]["message"]["content"] == "echo:fake-1:1"
    client.patch("/api/v1/settings", json={"playground": {"system_prompt": ""}})
    assert _reply(client, question) == "echo:fake-1:1"
