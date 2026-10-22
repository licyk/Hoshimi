"""Playground conversations in the database: order, titles, the archive, deletion and the session."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hoshimi.core.conversations import TITLE_LENGTH, ConversationStore, title_from
from hoshimi.core.db import Database


@pytest.fixture
def store() -> ConversationStore:
    return ConversationStore(Database())


def test_title_is_the_first_line_of_the_first_question() -> None:
    assert title_from("  How do I\n\nset up   Hoshimi? ") == "How do I set up Hoshimi?"
    long = title_from("x" * 200)
    assert len(long) == TITLE_LENGTH and long.endswith("…")


def test_messages_keep_their_order_and_the_first_question_names_the_conversation(store: ConversationStore) -> None:
    c = store.create(model="m1", effort="deep")
    assert (c.title, c.model, c.effort, c.archived) == ("", "m1", "deep", False)
    store.append(c.id, "user", "What is a gateway?")
    store.append(c.id, "assistant", "A **front door**.", model="m1")
    store.append(c.id, "user", "And a second question")
    got = store.get(c.id)
    assert got is not None
    assert got.title == "What is a gateway?"
    assert [(m.role, m.content, m.model) for m in got.messages] == [
        ("user", "What is a gateway?", ""), ("assistant", "A **front door**.", "m1"), ("user", "And a second question", ""),
    ]


def test_an_assistant_message_does_not_name_it(store: ConversationStore) -> None:
    c = store.create()
    store.append(c.id, "assistant", "Hello")
    assert store.get(c.id).title == ""  # type: ignore[union-attr]


def test_the_most_recently_used_comes_first_and_archiving_moves_it_aside(store: ConversationStore) -> None:
    first, second = store.create(), store.create()
    time.sleep(0.01)
    store.append(first.id, "user", "later")
    assert [c.id for c in store.conversations()] == [first.id, second.id]
    store.update(first.id, archived=True)
    assert [c.id for c in store.conversations()] == [second.id]
    assert [c.id for c in store.conversations(archived=True)] == [first.id]
    # Writing to it again brings it back.
    store.append(first.id, "user", "again")
    assert [c.id for c in store.conversations()] == [first.id, second.id]
    assert store.conversations(archived=True) == []


def test_update_changes_only_what_is_given(store: ConversationStore) -> None:
    c = store.create(model="m1", effort="light")
    updated = store.update(c.id, model="m2")
    assert updated is not None and (updated.model, updated.effort, updated.title) == ("m2", "light", "")
    assert store.update(c.id, title="  Named\nby hand ").title == "Named by hand"  # type: ignore[union-attr]
    assert store.update("missing", title="x") is None


def test_delete_takes_the_messages_and_forgets_the_session(store: ConversationStore) -> None:
    keep, gone = store.create(), store.create()
    store.append(keep.id, "user", "keep me")
    store.append(gone.id, "user", "remove me")
    store.set_session(gone.id)
    assert store.session() == gone.id
    assert store.delete(gone.id)
    assert not store.delete(gone.id)
    assert store.get(gone.id) is None
    assert store.session() is None
    assert [m.content for m in store.get(keep.id).messages] == ["keep me"]  # type: ignore[union-attr]


def test_append_to_a_missing_conversation_or_with_an_unknown_role(store: ConversationStore) -> None:
    assert store.append("missing", "user", "x") is None
    c = store.create()
    with pytest.raises(ValueError):
        store.append(c.id, "system", "x")  # type: ignore[arg-type]


def test_conversations_outlive_the_process(tmp_path: Path) -> None:
    db = Database(tmp_path / "hoshimi.db")
    store = ConversationStore(db)
    c = store.create(model="m1")
    store.append(c.id, "user", "remember me")
    store.set_session(c.id)
    db.close()
    again = ConversationStore(Database(tmp_path / "hoshimi.db"))
    assert again.session() == c.id
    assert [m.content for m in again.get(c.id).messages] == ["remember me"]  # type: ignore[union-attr]
