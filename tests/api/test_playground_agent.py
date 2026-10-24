"""The Playground as an agent: tool rounds (as many as a reply takes), approvals (the user's and the reviewer's), stops, and replies with tools sent again."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from hoshimi.api.playground import DENIED_CALL, DENIED_TOO_OFTEN_CALL, STOPPED_CALL, PlaygroundRunner
from hoshimi.core.agent.review import ACTION_START, REVIEW_POLICY
from hoshimi.core.catalog import ModelSpec
from hoshimi.core.context import Services
from hoshimi.core.providers.base import Account
from tests.conftest import FakeProvider, make_services

# More rounds of tool calls than replies were once allowed (50 by default).
TIRELESS_ROUNDS = 60


class AgentProvider(FakeProvider):
    """``agent`` calls what the question asks for (a JSON list of calls), then says what the last result was.

    ``tireless`` calls ``read`` round after round, ``TIRELESS_ROUNDS`` times, then answers; ``plain`` makes no tool calls at all.

    Asked to review a call (by any model), it denies ``rm -rf``, answers nonsense for a command that says ``nonsense``, fails for
    one that says ``explode``, and allows anything else (in prose around the JSON, which still counts).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reviews: list[dict[str, Any]] = []

    def fallback_models(self) -> list[str]:
        return ["agent", "tireless", "plain", "picky", "judge"]

    def specs(self) -> dict[str, ModelSpec]:
        specs = super().specs()
        specs["plain"] = replace(specs["plain"], tools=False)
        return specs

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if body["messages"][0] == {"role": "system", "content": REVIEW_POLICY}:
            self.reviews.append(json.loads(json.dumps(body)))
            action = body["messages"][1]["content"].split(ACTION_START)[1]
            if "rm -rf" in action:
                yield {"type": "text_delta", "text": json.dumps({"risk_level": "high", "user_authorization": "unknown", "outcome": "deny", "rationale": "It deletes files nobody asked to delete."})}
            elif "nonsense" in action:
                yield {"type": "text_delta", "text": "Looks fine to me!"}
            elif "explode" in action:
                yield {"type": "error", "message": "the reviewer's station is down"}
            else:
                yield {"type": "text_delta", "text": 'Sure. {"outcome": "allow"}'}
            return
        self.seen.append(json.loads(json.dumps(body)))
        last = body["messages"][-1]
        if model == "picky":
            if "tools" in body:
                yield {"type": "error", "message": "This model does not support tools"}
            else:
                yield {"type": "text_delta", "text": "answered plainly"}
            return
        if model == "tireless":
            if sum(m["role"] == "tool" for m in body["messages"]) >= TIRELESS_ROUNDS:
                yield {"type": "text_delta", "text": "done at last"}
                return
            yield {"type": "tool_call_start", "id": "", "name": "read", "arguments": '{"path": "a.txt"}', "index": 0}
            yield {"type": "finish", "reason": "tool_calls"}
            return
        if last["role"] == "tool":
            yield {"type": "text_delta", "text": f"tool said: {last['content'][:40]}"}
            return
        try:
            calls = json.loads(last["content"])
        except (TypeError, ValueError):
            yield {"type": "text_delta", "text": "no tools needed"}
            return
        yield {"type": "reasoning_delta", "text": "I should look."}
        yield {"type": "text_delta", "text": "Let me look."}
        for index, (name, args) in enumerate(calls):
            raw = json.dumps(args)
            yield {"type": "tool_call_start", "id": f"call_{index}", "name": name, "arguments": raw[:5], "index": index}
            yield {"type": "tool_call_delta", "index": index, "arguments": raw[5:]}
        yield {"type": "finish", "reason": "tool_calls"}


@pytest.fixture
def provider() -> AgentProvider:
    return AgentProvider()


@pytest.fixture
def services(provider: AgentProvider) -> Services:
    services = make_services(provider, playground_tools=["read", "apply_patch", "shell", "terminal"], playground_approval="workspace")
    workspace = PlaygroundRunner(services).workspace()
    workspace.mkdir(parents=True)
    (workspace / "a.txt").write_text("hello\n")
    return services


def _events(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _new(client: Any, model: str = "agent", **body: Any) -> str:
    return client.post("/api/v1/playground/conversations", json={"model": model, **body}).json()["id"]


def _send(client: Any, cid: str, content: Any, **body: Any) -> list[dict[str, Any]]:
    text = content if isinstance(content, str) else json.dumps(content)
    return _events(client.post(f"/api/v1/playground/conversations/{cid}/messages", json={"content": text, **body}))


def test_a_reply_calls_a_tool_reads_its_result_and_keeps_its_steps(client: Any, provider: AgentProvider) -> None:
    cid = _new(client)
    events = _send(client, cid, [["read", {"path": "a.txt"}]])
    kinds = [e["type"] for e in events]
    assert kinds == ["user", "assistant", "reasoning", "delta", "tool", "usage", "tool", "tool", "delta", "usage", "done"]
    assert [e["step"]["status"] for e in events if e["type"] == "tool"] == ["pending", "running", "done"]
    reply = events[-1]["message"]
    assert reply["status"] == "complete"
    assert reply["content"] == "Let me look.\n\ntool said: L1: hello"
    assert [(s["type"], s["round"]) for s in reply["steps"]] == [("reasoning", 0), ("text", 0), ("tool", 0), ("text", 1)]
    call = reply["steps"][2]
    assert (call["id"], call["name"], call["arguments"], call["output"]) == ("call_0", "read", '{"path": "a.txt"}', "L1: hello")
    # The first round offered the tools turned on; the second sent the call and its result back.
    first, second = provider.seen
    assert [t["function"]["name"] for t in first["tools"]] == ["read", "apply_patch", "shell", "exec_command", "write_stdin", "kill_session"]
    assert first["tool_choice"] == "auto"
    assert second["messages"][-2:] == [
        {"role": "assistant", "content": "Let me look.", "tool_calls": [{"id": "call_0", "type": "function", "function": {"name": "read", "arguments": '{"path": "a.txt"}'}}]},
        {"role": "tool", "tool_call_id": "call_0", "content": "L1: hello"},
    ]
    # Kept, and sent again with the next question.
    _send(client, cid, "thanks", parent_id=reply["id"])
    sent = provider.seen[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "tool", "assistant", "user"]
    assert sent[3] == {"role": "assistant", "content": "tool said: L1: hello"}


def test_a_patch_changes_the_workspace(client: Any, services: Services) -> None:
    cid = _new(client)
    patch = "*** Begin Patch\n*** Update File: a.txt\n@@\n-hello\n+bonjour\n*** End Patch"
    reply = _send(client, cid, [["apply_patch", {"input": patch}]])[-1]["message"]
    assert reply["steps"][2]["output"] == "Success. Updated the following files:\nM a.txt\n"
    assert (PlaygroundRunner(services).workspace() / "a.txt").read_text() == "bonjour\n"


def test_an_unknown_tool_and_bad_arguments_go_back_to_the_model(client: Any, provider: AgentProvider) -> None:
    cid = _new(client)
    reply = _send(client, cid, [["teleport", {}], ["web_fetch", {"url": "https://example.com"}]])[-1]["message"]
    calls = [s for s in reply["steps"] if s["type"] == "tool"]
    assert [(c["status"], c["output"]) for c in calls] == [("error", "unsupported call: teleport"), ("error", "unsupported call: web_fetch")]
    assert [m["content"] for m in provider.seen[-1]["messages"] if m["role"] == "tool"] == ["unsupported call: teleport", "unsupported call: web_fetch"]


def test_a_conversation_can_do_without_tools_and_a_model_without_tool_calls_gets_none(client: Any, provider: AgentProvider) -> None:
    cid = _new(client, tools_off=["read", "apply_patch", "shell", "terminal"])
    assert client.get(f"/api/v1/playground/conversations/{cid}").json()["tools_off"] == ["read", "apply_patch", "shell", "terminal"]
    _send(client, cid, "hi")
    assert "tools" not in provider.seen[-1]
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"tools_off": ["shell"]}).json()["tools_off"] == ["shell"]
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"tools_off": ["nope"]}).status_code == 400
    plain = _new(client, model="plain")
    events = _send(client, plain, "hi")
    assert {"type": "notice", "notice": {"kind": "tools-unsupported", "model": "plain"}} in events
    assert "tools" not in provider.seen[-1]


def test_a_station_that_refuses_tools_is_asked_again_without_them(client: Any, provider: AgentProvider) -> None:
    cid = _new(client, model="picky")
    events = _send(client, cid, "hi")
    assert {"type": "notice", "notice": {"kind": "tools-unsupported", "model": "picky"}} in events
    reply = events[-1]["message"]
    assert (reply["status"], reply["content"]) == ("complete", "answered plainly")
    assert ["tools" in body for body in provider.seen] == [True, False]


def test_a_conversation_can_turn_on_a_tool_settings_leave_off(client: Any, provider: AgentProvider) -> None:
    cid = _new(client, tools_on=["web_search"], tools_off=["shell", "terminal"])
    view = client.get(f"/api/v1/playground/conversations/{cid}").json()
    assert (view["tools_on"], view["tools_off"]) == (["web_search"], ["shell", "terminal"])
    _send(client, cid, "hi")
    assert [t["function"]["name"] for t in provider.seen[-1]["tools"]] == ["read", "apply_patch", "web_search"]
    client.patch(f"/api/v1/playground/conversations/{cid}", json={"tools_on": [], "tools_off": ["read", "apply_patch", "shell", "terminal"]})
    _send(client, cid, "again")
    assert "tools" not in provider.seen[-1]
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"tools_on": ["nope"]}).status_code == 400


def test_calls_go_as_text_once_tools_are_off(client: Any, provider: AgentProvider) -> None:
    cid = _new(client)
    reply = _send(client, cid, [["read", {"path": "a.txt"}]])[-1]["message"]
    client.patch(f"/api/v1/playground/conversations/{cid}", json={"tools_off": ["read", "apply_patch", "shell", "terminal"]})
    _send(client, cid, "and now?", parent_id=reply["id"])
    sent = provider.seen[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "assistant", "user"]
    assert sent[1]["content"] == 'Let me look.\n\n[Called read({"path": "a.txt"}); it gave:]\nL1: hello'


def test_a_reply_calls_tools_for_as_many_rounds_as_it_takes(provider: AgentProvider) -> None:
    services = make_services(provider, playground_tools=["read"], playground_approval="workspace")
    PlaygroundRunner(services).workspace().mkdir(parents=True)
    (PlaygroundRunner(services).workspace() / "a.txt").write_text("hello\n")
    conversation = services.conversations.create(model="tireless")
    question = services.conversations.add_message(conversation.id, None, "user", "go")
    assert question is not None
    events = list(PlaygroundRunner(services).reply(conversation.id, question.id, model="tireless", effort=""))
    assert [b["tool_choice"] for b in provider.seen] == ["auto"] * (TIRELESS_ROUNDS + 1)
    reply = events[-1]["message"]
    assert [s["status"] for s in reply.steps if s["type"] == "tool"] == ["done"] * TIRELESS_ROUNDS
    assert reply.content == "done at last" and reply.notices == []


# -- approvals ------------------------------------------------------------------------- #
@pytest.fixture
def asking(provider: AgentProvider) -> Services:
    services = make_services(provider, playground_tools=["read", "shell"], playground_approval="ask")
    PlaygroundRunner(services).workspace().mkdir(parents=True)
    return services


def _reply(services: Services, content: Any, cancel: threading.Event | None = None) -> tuple[str, Iterator[dict[str, Any]]]:
    conversation = services.conversations.create(model="agent")
    question = services.conversations.add_message(conversation.id, None, "user", json.dumps(content))
    assert question is not None
    return conversation.id, PlaygroundRunner(services).reply(conversation.id, question.id, model="agent", effort="", cancel=cancel)


def _until_waiting(events: Iterator[dict[str, Any]]) -> dict[str, Any]:
    for item in events:
        if item["type"] == "tool" and item["step"]["status"] == "waiting":
            return item["step"]
    raise AssertionError("no call waited for approval")


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_a_command_waits_for_approval_and_always_lasts_the_conversation(asking: Services, provider: AgentProvider) -> None:
    cid, events = _reply(asking, [["shell", {"command": "echo approved"}], ["shell", {"command": "echo again"}]])
    step = _until_waiting(events)
    assert step["approval"] == "command"
    threading.Timer(0.1, lambda: asking.agent.approvals.answer(cid, step["id"], "always")).start()
    rest = list(events)
    calls = [s for s in rest[-1]["message"].steps if s["type"] == "tool"]
    # The second command ran without asking: "always" covers the shell in this conversation.
    assert [c["status"] for c in calls] == ["done", "done"]
    assert "approved" in calls[0]["output"] and not any(e["type"] == "tool" and e["step"]["status"] == "waiting" for e in rest)


def test_reads_inside_the_workspace_never_ask_and_a_denied_call_is_told(asking: Services, provider: AgentProvider) -> None:
    cid, events = _reply(asking, [["read", {"path": "."}], ["shell", {"command": "rm -rf /"}]])
    step = _until_waiting(events)
    assert step["name"] == "shell"
    threading.Timer(0.1, lambda: asking.agent.approvals.answer(cid, step["id"], "deny")).start()
    reply = list(events)[-1]["message"]
    assert [s["status"] for s in reply.steps if s["type"] == "tool"] == ["done", "denied"]
    assert provider.seen[-1]["messages"][-1] == {"role": "tool", "tool_call_id": step["id"], "content": DENIED_CALL}


def test_a_stop_while_a_call_waits_keeps_the_reply_as_stopped(asking: Services) -> None:
    cancel = threading.Event()
    cid, events = _reply(asking, [["shell", {"command": "echo never"}]], cancel)
    _until_waiting(events)
    threading.Timer(0.1, cancel.set).start()
    stopped = next(events)
    assert (stopped["type"], stopped["step"]["status"]) == ("tool", "stopped")
    events.close()  # what the page going away does
    conversation = asking.conversations.get(cid)
    assert conversation is not None
    reply = conversation.messages[-1]
    assert reply.status == "stopped"
    assert [(s["status"], s["output"]) for s in reply.steps if s["type"] == "tool"] == [("stopped", STOPPED_CALL)]


def _patch(name: str) -> list[Any]:
    return ["apply_patch", {"input": f"*** Begin Patch\n*** Add File: {name}\n+hi\n*** End Patch"}]


def test_workspace_asks_only_before_reaching_outside_the_workspace(provider: AgentProvider, tmp_path: Any) -> None:
    services = make_services(provider, playground_tools=["apply_patch", "shell"], playground_approval="workspace")
    workspace = PlaygroundRunner(services).workspace()
    cid, events = _reply(services, [_patch("in.txt"), ["shell", {"command": "true"}], _patch(str(tmp_path / "out.txt"))])
    step = _until_waiting(events)
    assert (step["arguments"].count("out.txt"), step["approval"]) == (1, "edit-outside")
    threading.Timer(0.1, lambda: services.agent.approvals.answer(cid, step["id"], "allow")).start()
    reply = list(events)[-1]["message"]
    assert [s["status"] for s in reply.steps if s["type"] == "tool"] == ["done", "done", "done"]
    assert (workspace / "in.txt").exists() and (tmp_path / "out.txt").read_text() == "hi\n"


def test_always_inside_the_workspace_is_not_leave_outside_it(asking: Services, tmp_path: Any) -> None:
    asking.settings.playground_tools = ["apply_patch"]
    cid, events = _reply(asking, [_patch("one.txt"), _patch("two.txt"), _patch(str(tmp_path / "far.txt"))])
    first = _until_waiting(events)
    threading.Timer(0.1, lambda: asking.agent.approvals.answer(cid, first["id"], "always")).start()
    second = _until_waiting(events)
    # The second patch, inside, ran without asking; the third reaches outside and asks.
    assert second["approval"] == "edit-outside" and "far.txt" in second["arguments"]
    threading.Timer(0.1, lambda: asking.agent.approvals.answer(cid, second["id"], "deny")).start()
    reply = list(events)[-1]["message"]
    assert [s["status"] for s in reply.steps if s["type"] == "tool"] == ["done", "done", "denied"]
    assert not (tmp_path / "far.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_full_access_runs_everything_without_asking(asking: Services, tmp_path: Any) -> None:
    asking.settings.playground_tools = ["apply_patch", "shell", "read"]
    conversation = asking.conversations.create(model="agent", approval="full")
    question = asking.conversations.add_message(conversation.id, None, "user", json.dumps([["shell", {"command": "echo free"}], _patch(str(tmp_path / "x.txt")), ["read", {"path": str(tmp_path / "x.txt")}]]))
    assert question is not None
    events = list(PlaygroundRunner(asking).reply(conversation.id, question.id, model="agent", effort=""))
    assert not any(e["type"] == "tool" and e["step"]["status"] == "waiting" for e in events)
    assert [s["status"] for s in events[-1]["message"].steps if s["type"] == "tool"] == ["done", "done", "done"]
    # The whole of Settings can say so too.
    asking.settings.playground_approval = "full"
    _, again = _reply(asking, [["shell", {"command": "echo free"}]])
    assert [s["status"] for s in list(again)[-1]["message"].steps if s["type"] == "tool"] == ["done"]


def test_a_conversation_keeps_its_own_approval_mode(client: Any, services: Services) -> None:
    cid = client.post("/api/v1/playground/conversations", json={"model": "agent", "approval": "full"}).json()["id"]
    assert client.get(f"/api/v1/playground/conversations/{cid}").json()["approval"] == "full"
    runner = PlaygroundRunner(services)
    assert runner.approval_mode(cid) == "full"
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"approval": "ask"}).json()["approval"] == "ask"
    assert runner.approval_mode(cid) == "ask"
    # Empty follows Settings again.
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"approval": ""}).json()["approval"] == ""
    assert runner.approval_mode(cid) == services.settings.playground_approval == "workspace"
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"approval": "never"}).status_code == 422
    # "auto" is the old name of "workspace".
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"approval": "auto"}).json()["approval"] == "workspace"
    assert client.patch(f"/api/v1/playground/conversations/{cid}", json={"approval": "review"}).json()["approval"] == "review"


def test_full_access_from_before_becomes_the_full_mode(tmp_path: Any) -> None:
    import sqlite3

    from hoshimi.core.conversations import ConversationStore
    from hoshimi.core.db import MIGRATIONS, Database

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for number, script in enumerate(MIGRATIONS[:7], start=1):
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
    conn.execute("INSERT INTO conversations(id, title, model, effort, archived, created_at, updated_at, full_access) VALUES ('c1', 't', 'm', '', 0, 1, 1, 1)")
    conn.commit()
    conn.close()
    assert ConversationStore(Database(path)).approval("c1") == "full"


def test_the_old_auto_mode_becomes_workspace(tmp_path: Any) -> None:
    import sqlite3

    from hoshimi.core.conversations import ConversationStore
    from hoshimi.core.db import MIGRATIONS, Database

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for number, script in enumerate(MIGRATIONS[:9], start=1):
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
    conn.execute("INSERT INTO conversations(id, title, model, effort, archived, created_at, updated_at, approval) VALUES ('c1', 't', 'm', '', 0, 1, 1, 'auto')")
    conn.commit()
    conn.close()
    store = ConversationStore(Database(path))
    assert store.approval("c1") == "workspace"
    conversation = store.get("c1")
    assert conversation is not None and conversation.approval == "workspace"


def test_an_answer_with_nothing_waiting_is_not_found(client: Any) -> None:
    cid = _new(client)
    assert client.post(f"/api/v1/playground/conversations/{cid}/approvals/call_x", json={"decision": "allow"}).status_code == 404
    assert client.post(f"/api/v1/playground/conversations/{cid}/approvals/call_x", json={"decision": "maybe"}).status_code == 422


# -- the reviewer ---------------------------------------------------------------------------- #
@pytest.fixture
def reviewing(provider: AgentProvider) -> Services:
    services = make_services(provider, playground_tools=["read", "apply_patch", "shell"])
    assert services.settings.playground_approval == "review"
    PlaygroundRunner(services).workspace().mkdir(parents=True)
    return services


def _calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in events[-1]["message"].steps if s["type"] == "tool"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_the_reviewer_allows_a_call_and_it_runs_without_asking(reviewing: Services, provider: AgentProvider) -> None:
    _, stream = _reply(reviewing, [["read", {"path": "."}], ["shell", {"command": "echo reviewed", "shell": "sh"}]])
    events, statuses = [], []
    for item in stream:  # a step is sent as it stands, and changes after
        events.append(item)
        if item["type"] == "tool" and item["step"]["name"] == "shell":
            statuses.append((item["step"]["status"], item["step"]["review"] is not None))
    # Sent for review, then with what the reviewer said, then run.
    assert statuses == [("pending", False), ("reviewing", False), ("reviewing", True), ("running", True), ("done", True)]
    read, shell = _calls(events)
    # A read inside the workspace needs no one's leave; the command was reviewed, by the conversation's own model.
    assert read["review"] is None and len(provider.reviews) == 1
    assert shell["review"] == {"outcome": "allow", "risk_level": "low", "user_authorization": "unknown", "rationale": "", "model": "agent", "ms": shell["review"]["ms"], "failed": False}
    assert "reviewed" in shell["output"] and shell["meta"]["shell"] == "sh"
    # The reviewer read the user's question, the call, and which shell it runs in.
    review = provider.reviews[0]
    assert review["model"] == "agent" and "tools" not in review
    sent = review["messages"][1]["content"]
    assert '[user]: [["read"' in sent and '"tool": "shell"' in sent and '"shell": "sh"' in sent and '"needs": "command"' in sent


def test_the_reviewer_denies_a_call_and_the_model_is_told_why(reviewing: Services, provider: AgentProvider) -> None:
    _, events = _reply(reviewing, [["shell", {"command": "rm -rf ./build"}]])
    events = list(events)
    (call,) = _calls(events)
    assert call["status"] == "denied" and (call["review"]["outcome"], call["review"]["risk_level"]) == ("deny", "high")
    told = provider.seen[-1]["messages"][-1]
    assert told["role"] == "tool" and told["content"].startswith("The automatic review denied this call; it did not run. It deletes files nobody asked to delete.")
    assert not any(e["type"] == "tool" and e["step"]["status"] == "waiting" for e in events)


def test_a_call_the_reviewer_cannot_judge_asks_the_user(reviewing: Services) -> None:
    for command in ("echo nonsense", "echo explode"):
        cid, events = _reply(reviewing, [["shell", {"command": command}]])
        step = _until_waiting(events)
        assert step["review"]["failed"] and step["review"]["outcome"] == ""
        assert ("not valid JSON" if "nonsense" in command else "station is down") in step["review"]["rationale"]
        threading.Timer(0.1, reviewing.agent.approvals.answer, (cid, step["id"], "deny")).start()
        (call,) = _calls(list(events))
        assert (call["status"], call["output"]) == ("denied", "")


def test_review_model_reviews_in_the_conversations_place(reviewing: Services, provider: AgentProvider) -> None:
    reviewing.settings.playground_review_model = "judge"
    _, events = _reply(reviewing, [["shell", {"command": "true"}]])
    (call,) = _calls(list(events))
    assert call["review"]["model"] == "judge" and [r["model"] for r in provider.reviews] == ["judge"]


def test_a_reply_whose_calls_keep_being_denied_is_stopped(reviewing: Services, provider: AgentProvider) -> None:
    cid, events = _reply(reviewing, [["shell", {"command": f"rm -rf ./dir{i}"}] for i in range(4)])
    events = list(events)
    assert {"type": "notice", "notice": {"kind": "review-stopped", "name": "3"}} in events
    reply = events[-1]["message"]
    assert reply.status == "stopped"
    assert [c["status"] for c in _calls(events)] == ["denied", "denied", "denied", "stopped"]
    assert _calls(events)[-1]["output"] == DENIED_TOO_OFTEN_CALL
    # The model was not asked again after the third denial.
    assert len(provider.seen) == 1 and len(provider.reviews) == 3
    # Sent again, every call has its result.
    conversation = reviewing.conversations.get(cid)
    assert conversation is not None
    sent = PlaygroundRunner(reviewing).prepare(conversation, conversation.messages, "agent", describe=False, tools=True).messages
    assert [m["content"] for m in sent if m["role"] == "tool"][-1] == DENIED_TOO_OFTEN_CALL


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_always_from_the_user_skips_the_review(reviewing: Services, provider: AgentProvider) -> None:
    cid, events = _reply(reviewing, [["shell", {"command": "echo nonsense"}], ["shell", {"command": "echo nonsense again"}]])
    step = _until_waiting(events)
    threading.Timer(0.1, lambda: reviewing.agent.approvals.answer(cid, step["id"], "always")).start()
    assert [c["status"] for c in _calls(list(events))] == ["done", "done"]
    assert len(provider.reviews) == 1


# -- background commands ---------------------------------------------------------------- #
@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_background_commands_are_listed_stopped_and_end_with_their_conversation(client: Any, services: Services) -> None:
    cid = _new(client)
    reply = _send(client, cid, [["exec_command", {"cmd": "sleep 30", "yield_time_ms": 250}]])[-1]["message"]
    session = reply["steps"][2]["meta"]["session_id"]
    listed = client.get(f"/api/v1/playground/conversations/{cid}/terminals").json()["terminals"]
    assert [(t["id"], t["command"], t["running"]) for t in listed] == [(session, "sleep 30", True)]
    assert client.delete(f"/api/v1/playground/conversations/{cid}/terminals/{session}").status_code == 204
    assert client.get(f"/api/v1/playground/conversations/{cid}/terminals").json()["terminals"] == []
    assert client.delete(f"/api/v1/playground/conversations/{cid}/terminals/{session}").status_code == 404
    _send(client, cid, [["exec_command", {"cmd": "sleep 30", "yield_time_ms": 250}]])
    assert len(services.agent.terminals.of(cid)) == 1
    assert client.delete(f"/api/v1/playground/conversations/{cid}").status_code == 204
    assert services.agent.terminals.of(cid) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_stop_all_background_commands_is_scoped_and_repeatable(client: Any, services: Services, tmp_path: Any) -> None:
    cid, other = _new(client), _new(client)
    manager = services.agent.terminals
    for owner in (cid, cid, other):
        manager.start(owner, "sleep 30", str(tmp_path), 0, threading.Event())
    owned = [session for session in manager.sessions.values() if session.owner == cid]
    unrelated = [session for session in manager.sessions.values() if session.owner == other]
    try:
        assert client.delete("/api/v1/playground/conversations/missing/terminals").status_code == 404
        assert all(session.running for session in owned + unrelated)
        assert client.delete(f"/api/v1/playground/conversations/{cid}/terminals").status_code == 204
        assert manager.of(cid) == []
        assert all(not session.running for session in owned)
        assert all(session.running for session in unrelated)
        assert client.delete(f"/api/v1/playground/conversations/{cid}/terminals").status_code == 204
        # The conversation remains usable after stopping its current commands.
        manager.start(cid, "sleep 30", str(tmp_path), 0, threading.Event())
        assert len(manager.of(cid)) == 1
    finally:
        manager.close()


# -- settings ---------------------------------------------------------------------------- #
def test_every_tool_is_on_and_the_reviewer_approves_by_default(provider: AgentProvider) -> None:
    from hoshimi.core.agent.tools import GROUPS
    from hoshimi.core.config import Settings, settings_from_dict, settings_to_dict

    services = make_services(provider)
    assert (services.settings.playground_tools, services.settings.playground_approval) == (list(GROUPS), "review")
    assert "playground" not in settings_to_dict(Settings())
    # A config that turned every tool off keeps them off; the old "auto" reads as "workspace".
    loaded = settings_from_dict({"playground": {"tools": [], "approval": "auto", "shell": "pwsh"}}, Settings())
    assert (loaded.playground_tools, loaded.playground_approval, loaded.playground_shell) == ([], "workspace", "powershell")


def test_the_tools_are_set_in_settings(client: Any, services: Services) -> None:
    view = client.get("/api/v1/settings").json()["playground"]
    assert view["tools"] == ["read", "apply_patch", "shell", "terminal"] and view["approval"] == "workspace"
    assert view["shell"] == "auto" and view["shell_in_effect"] in view["shells"] and view["review_model"] == ""
    assert view["workspace_path"] == str(PlaygroundRunner(services).workspace())
    saved = client.patch("/api/v1/settings", json={"playground": {"tools": ["web_search"], "approval": "ask", "workspace": "/tmp/ws"}}).json()["playground"]
    assert (saved["tools"], saved["approval"], saved["workspace_path"]) == (["web_search"], "ask", "/tmp/ws")
    assert "max_steps" not in saved
    assert client.patch("/api/v1/settings", json={"playground": {"approval": "full"}}).json()["playground"]["approval"] == "full"
    assert client.patch("/api/v1/settings", json={"playground": {"approval": "auto"}}).json()["playground"]["approval"] == "workspace"
    saved = client.patch("/api/v1/settings", json={"playground": {"review_model": "judge", "shell": "sh"}}).json()["playground"]
    assert (saved["review_model"], saved["shell"], saved["shell_in_effect"]) == ("judge", "sh", "sh")
    for bad in ({"tools": ["rm"]}, {"approval": "never"}, {"search_url": "searx.local"}, {"shell": "fish"}):
        assert client.patch("/api/v1/settings", json={"playground": bad}).status_code == 400, bad

# -- steering ------------------------------------------------------------------------------ #
def test_a_message_sent_mid_run_is_read_at_the_next_round(services: Services, provider: AgentProvider) -> None:
    cid, events = _reply(services, [["read", {"path": "a.txt"}]])
    for item in events:
        if item["type"] == "tool" and item["step"]["status"] == "running":
            break
    steer = services.agent.steering.push(cid, "Also check b.txt")
    assert steer is not None
    rest = list(events)
    steered = [e for e in rest if e["type"] == "steer"]
    assert [(e["step"]["id"], e["step"]["text"], e["step"]["round"]) for e in steered] == [(steer.id, "Also check b.txt", 1)]
    # The model read it after the call's result, before its next words.
    assert [m["role"] for m in provider.seen[-1]["messages"]][-3:] == ["assistant", "tool", "user"]
    assert provider.seen[-1]["messages"][-1] == {"role": "user", "content": "Also check b.txt"}
    reply = rest[-1]["message"]
    assert [s["type"] for s in reply.steps] == ["reasoning", "text", "tool", "user", "text"]
    assert reply.content == "Let me look.\n\nno tools needed"
    # The reply is over: nothing takes a message now.
    assert services.agent.steering.push(cid, "too late") is None


def test_a_message_sent_as_the_model_finishes_keeps_the_reply_going(services: Services, provider: AgentProvider) -> None:
    conversation = services.conversations.create(model="agent")
    question = services.conversations.add_message(conversation.id, None, "user", "hello")
    assert question is not None
    events = PlaygroundRunner(services).reply(conversation.id, question.id, model="agent", effort="")
    # Sent while the model writes its last words.
    assert next(item for item in events if item["type"] == "delta")
    assert services.agent.steering.push(conversation.id, "one more thing") is not None
    rest = list(events)
    reply = rest[-1]["message"]
    assert [(s["type"], s["round"]) for s in reply.steps] == [("text", 0), ("user", 1), ("text", 1)]
    assert provider.seen[-1]["messages"][-2:] == [{"role": "assistant", "content": "no tools needed"}, {"role": "user", "content": "one more thing"}]
    # Sent again later, the steering message goes where it was read.
    follow = services.conversations.add_message(conversation.id, reply.id, "user", "thanks")
    assert follow is not None
    list(PlaygroundRunner(services).reply(conversation.id, follow.id, model="agent", effort=""))
    assert [(m["role"], m["content"]) for m in provider.seen[-1]["messages"]] == [
        ("user", "hello"), ("assistant", "no tools needed"), ("user", "one more thing"), ("assistant", "no tools needed"), ("user", "thanks"),
    ]


def test_a_stopped_reply_takes_no_more_messages(services: Services) -> None:
    cid, events = _reply(services, [["read", {"path": "a.txt"}]])
    next(events)
    next(events)
    services.agent.steering.push(cid, "never read")
    events.close()
    assert services.agent.steering.push(cid, "after the stop") is None


def test_steer_over_http_needs_a_reply_with_tools_running(client: Any) -> None:
    cid = _new(client)
    assert client.post(f"/api/v1/playground/conversations/{cid}/steer", json={"content": "hi"}).status_code == 409
    assert client.post(f"/api/v1/playground/conversations/{cid}/steer", json={"content": "  "}).status_code == 400
    assert client.post("/api/v1/playground/conversations/missing/steer", json={"content": "hi"}).status_code == 404
