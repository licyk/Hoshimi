"""Playground conversations in the database: trees, titles, the archive, branches, attachments and the session."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from hoshimi.core.conversations import TITLE_LENGTH, ConversationStore, path_to, title_from
from hoshimi.core.db import MIGRATIONS, Database


@pytest.fixture
def store() -> ConversationStore:
    return ConversationStore(Database())


def test_title_is_the_first_line_of_the_first_question() -> None:
    assert title_from("  How do I\n\nset up   Hoshimi? ") == "How do I set up Hoshimi?"
    long = title_from("x" * 200)
    assert len(long) == TITLE_LENGTH and long.endswith("…")


def test_a_conversation_is_a_tree_whose_current_leaf_is_the_path_on_screen(store: ConversationStore) -> None:
    c = store.create(model="m1", effort="deep")
    assert (c.title, c.title_state, c.current_leaf, c.temporary) == ("", "", "", False)
    q1 = store.add_message(c.id, None, "user", "What is a gateway?")
    a1 = store.add_message(c.id, q1.id, "assistant", "A **front door**.", model="m1")  # type: ignore[union-attr]
    assert q1 is not None and a1 is not None
    got = store.get(c.id)
    assert got is not None
    assert (got.title, got.title_state, got.current_leaf) == ("What is a gateway?", "provisional", a1.id)
    # An edit is a second question beside the first; a retry, a second reply beside the first.
    q2 = store.add_message(c.id, None, "user", "What is a proxy?")
    a2 = store.add_message(c.id, q2.id, "assistant", "A stand-in.")  # type: ignore[union-attr]
    a1b = store.add_message(c.id, q1.id, "assistant", "An entrance.")
    assert q2 is not None and a2 is not None and a1b is not None
    assert [m.content for m in store.path(c.id)] == ["What is a gateway?", "An entrance."]
    assert [m.content for m in store.path(c.id, a2.id)] == ["What is a proxy?", "A stand-in."]
    siblings = [m.id for m in store.get(c.id).messages if m.parent_id == q1.id]  # type: ignore[union-attr]
    assert siblings == [a1.id, a1b.id]
    # The first question named it; the edit does not rename it.
    assert store.get(c.id).title == "What is a gateway?"  # type: ignore[union-attr]


def test_a_message_must_follow_one_of_its_own_conversation(store: ConversationStore) -> None:
    c, other = store.create(), store.create()
    q = store.add_message(other.id, None, "user", "elsewhere")
    assert q is not None
    with pytest.raises(ValueError):
        store.add_message(c.id, q.id, "assistant", "x")
    with pytest.raises(ValueError):
        store.add_message(c.id, None, "system", "x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.add_message(c.id, None, "user", "x", attachment_ids=["missing"])
    assert store.add_message("missing", None, "user", "x") is None


def test_moving_between_versions_and_settling_a_reply(store: ConversationStore) -> None:
    c = store.create()
    q = store.add_message(c.id, None, "user", "hi")
    a = store.add_message(c.id, q.id, "assistant", "", status="streaming")  # type: ignore[union-attr]
    assert a is not None
    store.finish_message(a.id, content="partial", status="stopped", notices=[{"kind": "image-omitted", "name": "x.png"}], reasoning="hmm", reasoning_ms=1200)
    settled = store.message(a.id)
    assert settled is not None and (settled.content, settled.status, settled.notices) == ("partial", "stopped", [{"kind": "image-omitted", "name": "x.png"}])
    assert (settled.reasoning, settled.reasoning_ms) == ("hmm", 1200)
    # A branch keeps what each reply reasoned.
    branch = store.branch(c.id, a.id)
    assert branch is not None and (branch.messages[-1].reasoning, branch.messages[-1].reasoning_ms) == ("hmm", 1200)
    assert store.update(c.id, current_leaf=q.id).current_leaf == q.id  # type: ignore[union-attr,union-attr]
    with pytest.raises(ValueError):
        store.update(c.id, current_leaf="missing")


def test_a_title_set_by_hand_is_the_user_s(store: ConversationStore) -> None:
    c = store.create()
    assert store.update(c.id, title="  Named\nby hand ").title_state == "user"  # type: ignore[union-attr]
    assert store.get(c.id).title == "Named by hand"  # type: ignore[union-attr]
    assert store.update(c.id, title="Generated", title_state="auto").title_state == "auto"  # type: ignore[union-attr]
    assert store.update("missing", title="x") is None


def test_the_most_recently_used_comes_first_and_archiving_moves_it_aside(store: ConversationStore) -> None:
    first, second = store.create(), store.create()
    time.sleep(0.01)
    store.add_message(first.id, None, "user", "later")
    assert [c.id for c in store.conversations()] == [first.id, second.id]
    store.update(first.id, archived=True)
    assert [c.id for c in store.conversations()] == [second.id]
    assert [c.id for c in store.conversations(archived=True)] == [first.id]
    store.add_message(first.id, None, "user", "again")
    assert [c.id for c in store.conversations()] == [first.id, second.id]


def test_a_temporary_conversation_is_never_listed_or_resumed_and_ends_with_the_process(store: ConversationStore) -> None:
    kept, temporary = store.create(), store.create(temporary=True)
    store.add_message(temporary.id, None, "user", "off the record")
    assert [c.id for c in store.conversations()] == [kept.id]
    store.set_session(temporary.id)
    assert store.session() is None
    store.tidy()
    assert store.get(temporary.id) is None
    assert store.get(kept.id) is not None


def test_a_branch_copies_the_path_and_shares_its_attachments(store: ConversationStore) -> None:
    c = store.create(model="m1")
    image = store.add_attachment(kind="image", name="a.png", mime="image/png", data=b"png")
    q = store.add_message(c.id, None, "user", "look", attachment_ids=[image.id])
    a = store.add_message(c.id, q.id, "assistant", "a square")  # type: ignore[union-attr]
    store.add_message(c.id, a.id, "user", "later question")  # type: ignore[union-attr]
    assert q is not None and a is not None
    branch = store.branch(c.id, a.id)
    assert branch is not None
    assert (branch.branched_from, branch.title, branch.model) == (c.id, "look", "m1")
    path = path_to(branch.messages, branch.current_leaf)
    assert [(m.role, m.content) for m in path] == [("user", "look"), ("assistant", "a square")]
    assert [x.id for x in path[0].attachments] == [image.id]
    assert {m.id for m in branch.messages}.isdisjoint({q.id, a.id})
    # The source is untouched; deleting it keeps what the branch still carries.
    assert len(store.get(c.id).messages) == 3  # type: ignore[union-attr]
    assert store.delete(c.id)
    assert store.attachment(image.id) is not None
    assert store.delete(branch.id)
    assert store.attachment(image.id) is None
    with pytest.raises(ValueError):
        store.branch(store.create().id, "missing")


def test_attachments_keep_their_bytes_text_and_description(store: ConversationStore) -> None:
    image = store.add_attachment(kind="image", name="a.png", mime="image/png", data=b"\x89PNG", width=10, height=20)
    note = store.add_attachment(kind="file", name="n.txt", mime="text/plain", data=b"hello", text="hello")
    got = store.attachment(image.id)
    assert got is not None and (got.data, got.width, got.height, got.size) == (b"\x89PNG", 10, 20, 4)
    store.describe(image.id, "a tiny image")
    assert store.attachment(image.id).description == "a tiny image"  # type: ignore[union-attr]
    assert store.attachment(note.id).text == "hello"  # type: ignore[union-attr]
    c = store.create()
    store.add_message(c.id, None, "user", "see", attachment_ids=[image.id])
    # A sent attachment stays; one taken back before sending goes.
    assert not store.delete_attachment(image.id)
    assert store.delete_attachment(note.id)
    stale = store.add_attachment(kind="file", name="old.txt", mime="text/plain", data=b"x", text="x")
    assert store.purge_unsent(older_than=0) == 1
    assert store.attachment(stale.id) is None and store.attachment(image.id) is not None


def test_a_reply_cut_off_by_a_stop_is_kept_as_stopped(store: ConversationStore) -> None:
    c = store.create()
    q = store.add_message(c.id, None, "user", "hi")
    a = store.add_message(c.id, q.id, "assistant", "", status="streaming")  # type: ignore[union-attr]
    store.tidy()
    assert store.message(a.id).status == "stopped"  # type: ignore[union-attr]


def test_delete_forgets_the_session(store: ConversationStore) -> None:
    c = store.create()
    store.set_session(c.id)
    assert store.session() == c.id
    assert store.delete(c.id) and not store.delete(c.id)
    assert store.session() is None


def test_a_version_2_conversation_becomes_a_single_path(tmp_path: Path) -> None:
    path = tmp_path / "hoshimi.db"
    conn = sqlite3.connect(path)
    for number, script in enumerate(MIGRATIONS[:2], start=1):
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
    conn.execute("INSERT INTO conversations(id, title, model, effort, archived, created_at, updated_at) VALUES ('c1', 'Old question', 'm1', '', 0, 1, 2)")
    conn.execute("INSERT INTO conversations(id, title, model, effort, archived, created_at, updated_at) VALUES ('c2', '', '', '', 0, 1, 1)")
    for position, (role, content) in enumerate([("user", "Old question"), ("assistant", "Old answer"), ("user", "Follow-up")]):
        conn.execute("INSERT INTO conversation_messages VALUES ('c1', ?, ?, ?, 'm1', ?)", (position, role, content, 10 + position))
    conn.commit()
    conn.close()
    store = ConversationStore(Database(path))
    old = store.get("c1")
    assert old is not None
    assert (old.current_leaf, old.title_state) == ("c1-2", "auto")
    assert [(m.id, m.parent_id, m.content) for m in path_to(old.messages, old.current_leaf)] == [
        ("c1-0", None, "Old question"), ("c1-1", "c1-0", "Old answer"), ("c1-2", "c1-1", "Follow-up"),
    ]
    empty = store.get("c2")
    assert empty is not None and (empty.current_leaf, empty.title_state, empty.messages) == ("", "", [])
    # It goes on like any new one.
    assert store.add_message("c1", "c1-2", "assistant", "New answer") is not None
