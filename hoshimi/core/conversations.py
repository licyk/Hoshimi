"""Playground conversations, kept in ``hoshimi.db`` so they outlive the page and the process.

A conversation is a title, the model and effort it was last set to, whether it
is archived, and its messages in order. The web UI appends each message as it
is sent or received; nothing here talks to a model. Which conversation was
open last (the *session*) is kept too, so the Playground can come back to it
on the next start, from any browser.

The title is the first thing the user asked, cut to one line, until someone
names it otherwise. Writing to an archived conversation brings it back: it is
in use again.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from hoshimi.core.db import Database

Role = Literal["user", "assistant"]
ROLES: tuple[Role, ...] = ("user", "assistant")

TITLE_LENGTH = 80
"""The longest title taken from a first message; longer ones end in an ellipsis."""

SESSION_KEY = "playground.session"
"""The ``meta`` key holding the id of the conversation open last."""


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    model: str = ""
    created_at: float = 0.0


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    model: str
    effort: str
    archived: bool
    created_at: float
    updated_at: float
    messages: list[Message] = field(default_factory=list)


def title_from(text: str) -> str:
    """The first line's worth of ``text``, whitespace folded, cut at :data:`TITLE_LENGTH`."""
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= TITLE_LENGTH else flat[: TITLE_LENGTH - 1].rstrip() + "…"


class ConversationStore:
    """Conversations and their messages in the shared database.

    Every method is one short statement or transaction; the database's own
    lock serialises them, and the store's lock keeps an append's read of the
    next position and its write together.
    """

    def __init__(self, db: Database | None = None) -> None:
        self._db = db or Database()
        self._lock = threading.Lock()

    # -- conversations ----------------------------------------------------- #
    def conversations(self, *, archived: bool = False) -> list[Conversation]:
        """Every conversation on one side of the archive, the most recently used first (no messages)."""
        rows = self._db.fetchall(
            "SELECT * FROM conversations WHERE archived = ? ORDER BY updated_at DESC, created_at DESC", (int(archived),)
        )
        return [_conversation(row) for row in rows]

    def create(self, *, model: str = "", effort: str = "", title: str = "") -> Conversation:
        now = time.time()
        conversation_id = uuid.uuid4().hex
        self._db.execute(
            "INSERT INTO conversations(id, title, model, effort, archived, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
            (conversation_id, title_from(title), model, effort, now, now),
        )
        return Conversation(id=conversation_id, title=title_from(title), model=model, effort=effort, archived=False, created_at=now, updated_at=now)

    def get(self, conversation_id: str) -> Conversation | None:
        """One conversation with its messages, oldest first; None when there is none by that id."""
        row = self._db.fetchone("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        if row is None:
            return None
        messages = self._db.fetchall(
            "SELECT role, content, model, created_at FROM conversation_messages WHERE conversation_id = ? ORDER BY position", (conversation_id,)
        )
        return _conversation(row, [Message(role=m["role"], content=m["content"], model=m["model"], created_at=m["created_at"]) for m in messages])

    def update(
        self, conversation_id: str, *, title: str | None = None, model: str | None = None,
        effort: str | None = None, archived: bool | None = None,
    ) -> Conversation | None:
        """Change what is given; archiving does not count as use, so the order of the list stays."""
        changes: dict[str, object] = {}
        if title is not None:
            changes["title"] = title_from(title)
        if model is not None:
            changes["model"] = model
        if effort is not None:
            changes["effort"] = effort
        if archived is not None:
            changes["archived"] = int(archived)
        if changes:
            assignments = ", ".join(f"{column} = ?" for column in changes)
            self._db.execute(f"UPDATE conversations SET {assignments} WHERE id = ?", (*changes.values(), conversation_id))
        return self.get(conversation_id)

    def delete(self, conversation_id: str) -> bool:
        """Remove a conversation and its messages; False when there was none. A deleted session is forgotten."""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM conversation_messages WHERE conversation_id = ?", (conversation_id,))
            gone = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount > 0
        if gone and self.session() == conversation_id:
            self.set_session(None)
        return gone

    # -- messages ----------------------------------------------------------- #
    def append(self, conversation_id: str, role: Role, content: str, *, model: str = "") -> Message | None:
        """Add a message at the end; None when the conversation does not exist.

        The first user message names an untitled conversation, and any message
        brings an archived one back and moves it to the top of the list.
        """
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        now = time.time()
        with self._lock, self._db.transaction(immediate=True) as conn:
            row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row is None:
                return None
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM conversation_messages WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO conversation_messages(conversation_id, position, role, content, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, position, role, content, model, now),
            )
            title = row["title"] or (title_from(content) if role == "user" else "")
            conn.execute("UPDATE conversations SET title = ?, archived = 0, updated_at = ? WHERE id = ?", (title, now, conversation_id))
        return Message(role=role, content=content, model=model, created_at=now)

    # -- session ------------------------------------------------------------ #
    def session(self) -> str | None:
        """The conversation open last, if it still exists."""
        conversation_id = self._db.get_meta(SESSION_KEY)
        if not conversation_id:
            return None
        exists = self._db.fetchone("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,))
        return conversation_id if exists else None

    def set_session(self, conversation_id: str | None) -> None:
        self._db.set_meta(SESSION_KEY, conversation_id or "")


def _conversation(row: sqlite3.Row, messages: list[Message] | None = None) -> Conversation:
    return Conversation(
        id=row["id"], title=row["title"], model=row["model"], effort=row["effort"], archived=bool(row["archived"]),
        created_at=row["created_at"], updated_at=row["updated_at"], messages=messages or [],
    )
