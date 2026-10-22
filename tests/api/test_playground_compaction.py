"""Compacting Playground conversations: on the user's word, as a reply begins, between its rounds, and when a station refuses a round as too long.

Also what a reply now keeps of itself (its tokens and how long it worked) and what ``/context`` reports.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from hoshimi.api.compaction import ACK, COMPACT_PROMPT, SUMMARY_PREFIX
from hoshimi.api.playground import TOO_LONG, PlaygroundRunner
from hoshimi.core.agent.context_window import estimate
from hoshimi.core.catalog import ModelSpec
from hoshimi.core.context import Services
from hoshimi.core.providers.base import Account, ContextOverflow
from tests.conftest import FakeProvider, make_services

WINDOW = 16_000
"""The fake model's window: compaction starts at 13,600 tokens (85%), about 54 KB of text."""


class LongProvider(FakeProvider):
    """``long`` answers ``ok`` (or calls ``read`` on the path a ``READ <path>`` question names, then says ``read it``),
    and summarises when asked to with the compaction prompt. ``refuse_above`` refuses requests estimated longer, as a station does."""

    def __init__(self) -> None:
        super().__init__()
        self.refuse_above: int | None = None
        self.fail_summary = False

    def fallback_models(self) -> list[str]:
        return ["long"]

    def specs(self) -> dict[str, ModelSpec]:
        specs = super().specs()
        specs["long"] = replace(specs["long"], context_window=WINDOW, max_output=2_000)
        return specs

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.seen.append(json.loads(json.dumps(body)))
        messages = body["messages"]
        if messages[0].get("content") == COMPACT_PROMPT:
            if self.fail_summary:
                yield {"type": "error", "message": "the summary was refused"}
                return
            yield {"type": "text_delta", "text": f"SUMMARY of {len(messages[1]['content'])} characters"}
            return
        if self.refuse_above is not None and estimate(messages) > self.refuse_above:
            raise ContextOverflow(400, f"long: This model's maximum context length is {self.refuse_above} tokens.")
        last = messages[-1]
        if last["role"] == "user" and isinstance(last["content"], str) and last["content"].startswith("READ "):
            path = last["content"].split()[1]
            yield {"type": "tool_call_start", "id": "call_0", "name": "read", "arguments": json.dumps({"path": path}), "index": 0}
            yield {"type": "finish", "reason": "tool_calls"}
        else:
            yield {"type": "text_delta", "text": "read it" if last["role"] == "tool" else "ok"}
        yield {"type": "usage", "usage": {"prompt_tokens": estimate(messages), "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 7}}}


@pytest.fixture
def provider() -> LongProvider:
    return LongProvider()


@pytest.fixture
def services(provider: LongProvider) -> Services:
    services = make_services(provider, playground_tools=["read"], playground_approval="auto")
    PlaygroundRunner(services).workspace().mkdir(parents=True)
    return services


def _events(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _new(client: Any) -> str:
    return client.post("/api/v1/playground/conversations", json={"model": "long"}).json()["id"]


def _send(client: Any, cid: str, content: str, parent: str | None = None) -> list[dict[str, Any]]:
    return _events(client.post(f"/api/v1/playground/conversations/{cid}/messages", json={"content": content, "parent_id": parent}))


def _chat(client: Any, cid: str, *questions: str) -> str:
    """Ask each question after the last reply; the last reply's id."""
    leaf = None
    for question in questions:
        leaf = _send(client, cid, question, leaf)[-1]["message"]["id"]
    assert leaf is not None
    return leaf


def _compactions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e["step"] for e in events if e["type"] == "compaction"]


# -- what a reply keeps ------------------------------------------------------------------ #
def test_a_reply_keeps_its_tokens_and_how_long_it_worked(client: Any) -> None:
    cid = _new(client)
    events = _send(client, cid, "hello")
    usage = next(e for e in events if e["type"] == "usage")
    assert (usage["window"], usage["limit"]) == (WINDOW, 13_600)
    reply = events[-1]["message"]
    assert reply["usage"]["last"] == {"input": reply["usage"]["last"]["input"], "cached": 7, "output": 5, "reasoning": 0}
    assert reply["usage"]["source"] == "usage"
    assert reply["usage"]["context"] == reply["usage"]["last"]["input"] + 5
    assert reply["duration_ms"] >= 0 and reply["finished_at"] > 0
    view = client.get(f"/api/v1/playground/conversations/{cid}/context").json()
    assert (view["model"], view["window"], view["window_source"], view["limit"], view["used"], view["used_source"]) == (
        "long", WINDOW, "catalog", 13_600, reply["usage"]["context"], "usage",
    )
    assert view["totals"]["cached"] == 7 and view["replies"] == 1 and view["compactions"] == 0 and view["running_since"] is None


def test_a_tool_round_s_usage_adds_up_and_a_branch_keeps_it(client: Any, services: Services) -> None:
    (PlaygroundRunner(services).workspace() / "a.txt").write_text("hello\n")
    cid = _new(client)
    reply = _send(client, cid, "READ a.txt")[-1]["message"]
    assert reply["usage"]["rounds"] == 2
    assert reply["usage"]["total"]["output"] == 10
    branch = client.post(f"/api/v1/playground/conversations/{cid}/branch", json={"message_id": reply["id"]}).json()
    copied = branch["messages"][-1]
    assert (copied["usage"], copied["duration_ms"]) == (reply["usage"], reply["duration_ms"])


# -- on the user's word ------------------------------------------------------------------- #
def test_compacting_files_a_checkpoint_and_the_next_question_is_sent_the_summary(client: Any, provider: LongProvider) -> None:
    cid = _new(client)
    leaf = _chat(client, cid, "first question", "second question")
    events = _events(client.post(f"/api/v1/playground/conversations/{cid}/compact", json={"instructions": "the questions"}))
    assert [e["type"] for e in events] == ["checkpoint", "done"]
    checkpoint = events[-1]["message"]
    assert (checkpoint["role"], checkpoint["parent_id"], checkpoint["status"]) == ("checkpoint", leaf, "complete")
    record = checkpoint["steps"][0]
    assert (record["type"], record["status"], record["trigger"], record["model"]) == ("compaction", "done", "manual", "long")
    assert checkpoint["content"] == record["summary"] and record["summary"].startswith("SUMMARY of")
    assert record["recent"] == ["first question", "second question"]
    asked = provider.seen[-1]["messages"][1]["content"]
    assert "[user]: first question" in asked and "[assistant]: ok" in asked and asked.endswith("pay particular attention to: the questions")
    # The next question follows the checkpoint, and the model reads the summary in place of what came before.
    _send(client, cid, "third question", checkpoint["id"])
    sent = provider.seen[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"].startswith(SUMMARY_PREFIX) and "SUMMARY of" in sent[0]["content"] and sent[0]["content"].endswith("second question")
    assert sent[1]["content"] == ACK and sent[2]["content"] == "third question"
    view = client.get(f"/api/v1/playground/conversations/{cid}/context").json()
    assert view["compactions"] == 1 and view["last_compaction"]["trigger"] == "manual"


def test_a_failed_compaction_leaves_the_conversation_as_it_was(client: Any, provider: LongProvider) -> None:
    cid = _new(client)
    leaf = _chat(client, cid, "hello")
    provider.fail_summary = True
    events = _events(client.post(f"/api/v1/playground/conversations/{cid}/compact", json={}))
    assert events[-1]["message"]["status"] == "error" and "the summary was refused" in events[-1]["message"]["error"]
    got = client.get(f"/api/v1/playground/conversations/{cid}").json()
    assert got["current_leaf"] == leaf and all(m["role"] != "checkpoint" for m in got["messages"])


def test_there_must_be_something_to_compact_and_nothing_else_working_on_it(client: Any, services: Services) -> None:
    cid = _new(client)
    assert client.post(f"/api/v1/playground/conversations/{cid}/compact", json={}).status_code == 400
    _chat(client, cid, "hello")
    services.agent.busy.start(cid)
    assert client.post(f"/api/v1/playground/conversations/{cid}/compact", json={}).status_code == 409
    services.agent.busy.end(cid)
    _events(client.post(f"/api/v1/playground/conversations/{cid}/compact", json={}))
    # Compacted, with no reply since: nothing more to compact.
    assert client.post(f"/api/v1/playground/conversations/{cid}/compact", json={}).status_code == 400


# -- on its own ---------------------------------------------------------------------------- #
def test_a_conversation_near_the_window_is_compacted_as_the_reply_begins(client: Any, provider: LongProvider) -> None:
    cid = _new(client)
    # Each just under the limit as it was sent; the third question takes it over.
    leaf = _chat(client, cid, "a" * 27_000, "b" * 27_000)
    events = _send(client, cid, "and now? " + "g" * 2_000, leaf)
    kinds = [e["type"] for e in events]
    assert kinds.index("compaction") < kinds.index("delta")
    running, done = _compactions(events)
    assert (running["status"], done["status"], done["phase"], done["trigger"], done["round"]) == ("running", "done", "pre-turn", "auto", 0)
    assert done["tokens_before"] > 13_600 > done["tokens_after"]
    sent = provider.seen[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"].startswith(SUMMARY_PREFIX) and sent[-1]["content"].startswith("and now?")
    reply = events[-1]["message"]
    assert [s["type"] for s in reply["steps"]] == ["compaction", "text"]
    # Kept: the next question is sent the summary, the question it compacted before, and that reply.
    _send(client, cid, "more", reply["id"])
    sent = provider.seen[-1]["messages"]
    assert [(m["role"], m["content"][:8]) for m in sent] == [("user", SUMMARY_PREFIX[:8]), ("assistant", ACK[:8]), ("user", "and now?"), ("assistant", "ok"), ("user", "more")]


def test_a_reply_compacted_inside_can_be_compacted_again_by_hand(client: Any, provider: LongProvider) -> None:
    cid = _new(client)
    leaf = _chat(client, cid, "a" * 27_000, "b" * 27_000)
    reply = _send(client, cid, "and now? " + "g" * 2_000, leaf)[-1]["message"]
    assert reply["steps"][0]["type"] == "compaction"
    events = _events(client.post(f"/api/v1/playground/conversations/{cid}/compact", json={}))
    assert events[-1]["message"]["status"] == "complete"
    # What the hand-made summary was written from: the earlier summary, the question, and the reply after it.
    asked = provider.seen[-1]["messages"][1]["content"]
    assert SUMMARY_PREFIX in asked and "[user]: and now?" in asked and "[assistant]: ok" in asked


def test_auto_compaction_can_be_turned_off(provider: LongProvider) -> None:
    services = make_services(provider, playground_auto_compact=False)
    runner = PlaygroundRunner(services)
    conversation = services.conversations.create(model="long")
    first = services.conversations.add_message(conversation.id, None, "user", "a" * 60_000)
    assert first is not None
    events = list(runner.reply(conversation.id, first.id, model="long", effort=""))
    assert not _compactions(events) and events[-1]["message"].status == "complete"


def test_a_reply_is_compacted_between_its_rounds_and_goes_on(client: Any, services: Services, provider: LongProvider) -> None:
    (PlaygroundRunner(services).workspace() / "big.txt").write_text("\n".join(f"line {i} " + "z" * 60 for i in range(1_000)))
    cid = _new(client)
    leaf = _chat(client, cid, "c" * 20_000)
    events = _send(client, cid, "READ big.txt", leaf)
    _running, done = _compactions(events)
    assert (done["status"], done["phase"], done["round"]) == ("done", "mid-turn", 1)
    reply = events[-1]["message"]
    assert reply["content"] == "read it" and reply["status"] == "complete"
    assert [s["type"] for s in reply["steps"]] == ["tool", "compaction", "text"]
    # The round after it: the summary, the question, the call just made with its output cut, and nothing older.
    sent = provider.seen[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user", "assistant", "tool"]
    assert sent[2]["content"] == "READ big.txt" and sent[3]["tool_calls"][0]["function"]["name"] == "read"
    assert "more characters left out to fit the context window" in sent[4]["content"]
    assert reply["steps"][1]["tokens_after"] < 13_600
    # As the next question is sent it again.
    request = client.get(f"/api/v1/playground/conversations/{cid}/messages/{reply['id']}/request").json()
    assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]


def test_a_round_refused_as_too_long_is_compacted_and_asked_again(client: Any, services: Services, provider: LongProvider) -> None:
    cid = _new(client)
    leaf = _chat(client, cid, "d" * 20_000, "e" * 20_000)
    provider.refuse_above = 9_000
    events = _send(client, cid, "go on", leaf)
    _running, done = _compactions(events)
    assert (done["status"], done["trigger"], done["phase"]) == ("done", "overflow", "pre-turn")
    assert events[-1]["message"]["status"] == "complete" and events[-1]["message"]["content"] == "ok"
    # What the station said is learned: the window is what it named, and the next check compacts by it.
    assert services.agent.windows.get("long") == 9_000
    assert client.get(f"/api/v1/playground/conversations/{cid}/context").json()["window_source"] == "learned"


def test_a_question_too_long_alone_ends_in_an_error_that_says_so(client: Any, provider: LongProvider) -> None:
    provider.refuse_above = 1_000
    cid = _new(client)
    reply = _send(client, cid, "f" * 10_000)[-1]["message"]
    assert reply["status"] == "error" and reply["error"] == TOO_LONG.format(model="long")


def test_the_compaction_settings_are_kept_and_checked(client: Any, services: Services) -> None:
    view = client.get("/api/v1/settings").json()["playground"]
    assert (view["auto_compact"], view["compact_threshold"], view["compact_model"]) == (True, 0.85, "")
    saved = client.patch("/api/v1/settings", json={"playground": {"auto_compact": False, "compact_threshold": 0.7, "compact_model": "long"}}).json()["playground"]
    assert (saved["auto_compact"], saved["compact_threshold"], saved["compact_model"]) == (False, 0.7, "long")
    assert PlaygroundRunner(services).budget("long").limit == 11_200
    for bad in ({"compact_threshold": 0.2}, {"compact_threshold": 1}, {"auto_compact": "yes"}):
        assert client.patch("/api/v1/settings", json={"playground": bad}).status_code == 400, bad


def test_a_reply_stopped_while_it_compacts_is_kept_as_stopped(provider: LongProvider) -> None:
    import threading

    services = make_services(provider)
    conversation = services.conversations.create(model="long")
    first = services.conversations.add_message(conversation.id, None, "user", "h" * 60_000)
    assert first is not None
    answer = services.conversations.add_message(conversation.id, first.id, "assistant", "ok")
    assert answer is not None
    question = services.conversations.add_message(conversation.id, answer.id, "user", "and?")
    assert question is not None
    cancel = threading.Event()
    cancel.set()
    events = list(PlaygroundRunner(services).reply(conversation.id, question.id, model="long", effort="", cancel=cancel))
    reply = events[-1]["message"]
    assert reply.status == "stopped" and [s["status"] for s in reply.steps] == ["stopped"]
    assert services.agent.busy.started(conversation.id) is None
