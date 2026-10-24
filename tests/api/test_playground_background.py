"""Replies that work in the background: a page that leaves stops following, not the reply; another follows it from its start, or stops it."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from hoshimi.api.playground import PlaygroundRunner
from hoshimi.api.routers.playground import _start
from hoshimi.core.agent.runs import Run, Runs
from hoshimi.core.context import Services
from hoshimi.core.errors import ConflictError
from tests.api.test_playground_agent import AgentProvider
from tests.conftest import make_services

BASE = "/api/v1/playground/conversations"


@pytest.fixture
def provider() -> AgentProvider:
    return AgentProvider()


@pytest.fixture
def services(provider: AgentProvider) -> Services:
    services = make_services(provider, playground_tools=["read", "shell"], playground_approval="ask")
    PlaygroundRunner(services).workspace().mkdir(parents=True)
    return services


def _asking(services: Services) -> tuple[str, Run]:
    """A reply started in the background whose command waits for the user's answer."""
    conversation = services.conversations.create(model="agent")
    question = services.conversations.add_message(conversation.id, None, "user", json.dumps([["shell", {"command": "echo hi"}]]))
    assert question is not None
    cancel = threading.Event()
    events = PlaygroundRunner(services).reply(conversation.id, question.id, model="agent", effort="", cancel=cancel)
    return conversation.id, _start(services, conversation.id, "reply", events, cancel)


def _read(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    return (json.loads(line) for line in lines)


def _until_waiting(events: Iterator[dict[str, Any]]) -> dict[str, Any]:
    for item in events:
        if item["type"] == "tool" and item["step"]["status"] == "waiting":
            return item["step"]
    raise AssertionError("no call waited for approval")


def _events(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _listed(client: Any, cid: str) -> dict[str, Any]:
    return next(c for c in client.get(BASE).json()["conversations"] if c["id"] == cid)


def test_a_reply_goes_on_when_its_page_leaves_and_another_follows_it_from_its_start(client: Any, services: Services) -> None:
    cid, run = _asking(services)
    follower = run.follow()
    step = _until_waiting(_read(follower))
    follower.close()  # the page opened another chat
    assert (run.followers, run.finished) == (0, False)
    # The list says it works, and that it waits for an answer.
    assert {k: _listed(client, cid)[k] for k in ("running", "waiting")} == {"running": True, "waiting": True}
    assert client.get(f"{BASE}/{cid}").json()["running"] is True
    # Opened again: its events from the first, as they were sent, then the rest as the answer lets it go on.
    threading.Timer(0.2, lambda: services.agent.approvals.answer(cid, step["id"], "allow")).start()
    events = _events(client.get(f"{BASE}/{cid}/events"))
    assert [e["type"] for e in events][:2] == ["assistant", "reasoning"]
    assert [e["step"]["status"] for e in events if e["type"] == "tool"] == ["pending", "waiting", "running", "done"]
    assert events[-1]["type"] == "done" and events[-1]["message"]["status"] == "complete"
    run.join(5)
    assert {k: _listed(client, cid)[k] for k in ("running", "waiting")} == {"running": False, "waiting": False}
    assert client.get(f"{BASE}/{cid}/events").status_code == 404


def test_a_follower_can_start_part_way_and_is_let_go_at_once(services: Services) -> None:
    _, run = _asking(services)
    first = list(_until_waiting_lines(run.follow()))
    late = run.follow(after=len(first) - 1)
    assert json.loads(next(late))["step"]["status"] == "waiting"
    # Let go while it waits for the next event: it ends now, not when the reply does.
    threading.Timer(0.1, late.release).start()
    assert list(late) == []
    run.stop()
    run.join(5)


def _until_waiting_lines(follower: Iterator[str]) -> Iterator[str]:
    for line in follower:
        yield line
        item = json.loads(line)
        if item["type"] == "tool" and item["step"]["status"] == "waiting":
            return


def test_stop_ends_a_reply_no_page_follows_and_its_followers_hear_of_it(client: Any, services: Services) -> None:
    cid, run = _asking(services)
    follower = run.follow()
    _until_waiting(_read(follower))
    assert client.post(f"{BASE}/{cid}/stop").status_code == 204
    rest = list(_read(follower))
    assert rest[-1]["type"] == "done" and rest[-1]["message"]["status"] == "stopped"
    run.join(5)
    reply = services.conversations.path(cid)[-1]
    assert reply.status == "stopped"
    assert [s["status"] for s in reply.steps if s["type"] == "tool"] == ["stopped"]
    # Nothing running is no error.
    assert client.post(f"{BASE}/{cid}/stop").status_code == 204


def test_one_reply_at_a_time_in_a_conversation(client: Any, services: Services) -> None:
    cid, run = _asking(services)
    _until_waiting(_read(run.follow()))
    question = services.conversations.path(cid)[0]
    assert client.post(f"{BASE}/{cid}/messages", json={"content": "another"}).status_code == 409
    assert client.post(f"{BASE}/{cid}/messages/{question.id}/retry", json={}).status_code == 409
    assert client.post(f"{BASE}/{cid}/compact", json={}).status_code == 409
    # Nothing was added for the refused send.
    assert len(services.conversations.path(cid)) == 2
    run.stop()
    run.join(5)


def test_a_reply_that_ends_with_no_page_following_names_its_conversation(services: Services) -> None:
    cid, run = _asking(services)
    follower = run.follow()
    step = _until_waiting(_read(follower))
    follower.close()
    services.agent.approvals.answer(cid, step["id"], "allow")
    run.join(5)
    pause = threading.Event()
    for _ in range(50):
        conversation = services.conversations.get(cid)
        assert conversation is not None
        if conversation.title_state == "auto":
            break
        pause.wait(0.05)
    assert conversation.title_state == "auto"


def test_deleting_a_conversation_stops_its_reply_first(client: Any, services: Services) -> None:
    cid, run = _asking(services)
    _until_waiting(_read(run.follow()))
    assert client.delete(f"{BASE}/{cid}").status_code == 204
    assert run.finished
    assert services.conversations.get(cid) is None


def test_a_run_that_fails_outside_the_reply_still_ends_with_its_message() -> None:
    runs = Runs()

    def events() -> Iterator[dict[str, Any]]:
        yield {"type": "assistant", "message": {"id": "m1", "status": "streaming"}}
        raise RuntimeError("boom")

    run = runs.start("c1", "reply", events(), threading.Event(), closing=lambda last: {"type": "done", "message": {**(last or {}), "status": "error"}})
    run.join(5)
    assert [json.loads(e)["type"] for e in run.events] == ["assistant", "done"]
    assert run.done == {"id": "m1", "status": "error"}
    assert not runs.running("c1")
    with pytest.raises(ConflictError):
        blocker = threading.Event()

        def waiting() -> Iterator[dict[str, Any]]:
            blocker.wait(5)
            yield {"type": "done", "message": {"id": "m2"}}

        runs.start("c2", "reply", waiting(), threading.Event(), closing=lambda last: None)
        try:
            runs.start("c2", "reply", iter(()), threading.Event(), closing=lambda last: None)
        finally:
            blocker.set()
    runs.close()
