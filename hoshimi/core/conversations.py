"""Playground conversations, kept in ``hoshimi.db`` so they outlive the page and the process.

A conversation is a tree of messages. Each message names the one it follows
(``parent_id``; None for the first), so editing a question or retrying a reply
adds a *sibling* beside the old one instead of replacing it, and both stay
readable. ``current_leaf`` is the last message of the path on screen; the path
from the root to it is what a model is sent.

A conversation also carries its title (and how it came to be: ``provisional``
from the first question until a model names it ``auto``, or the user's own
``user``), the model and effort it was last set to, whether it is archived,
and whether it is *temporary*: a temporary conversation works like any other
but is never listed, never titled, and is deleted when the user leaves it
(and on the next start, for one left behind).

Attachments (images and files) are stored once and linked to the messages
that carry them, so a branch shares them rather than copying the bytes. A
file's extracted text and an image's description (for models that cannot
read images) are kept beside the bytes, so each is worked out once.

Which conversation was open last (the *session*) is kept too, so the
Playground comes back to it on the next start, from any browser.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from hoshimi.core.db import Database

Role = Literal["user", "assistant"]
ROLES: tuple[Role, ...] = ("user", "assistant")
Status = Literal["complete", "streaming", "stopped", "error"]
TitleState = Literal["", "provisional", "auto", "user"]
AttachmentKind = Literal["image", "file"]

TITLE_LENGTH = 80
"""The longest title taken from a first message; longer ones end in an ellipsis."""

SESSION_KEY = "playground.session"
"""The ``meta`` key holding the id of the conversation open last."""

UNSENT_ATTACHMENT_SECONDS = 24 * 3600
"""An attachment no message carries is deleted after a day: it was uploaded and never sent."""


@dataclass(frozen=True)
class AttachmentInfo:
    """What a page shows of an attachment; the bytes are fetched on their own."""

    id: str
    kind: AttachmentKind
    name: str
    mime: str
    size: int
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class Attachment(AttachmentInfo):
    data: bytes = b""
    text: str = ""
    description: str = ""


@dataclass(frozen=True)
class ChatMessage:
    id: str
    conversation_id: str
    parent_id: str | None
    role: Role
    content: str
    model: str = ""
    effort: str = ""
    status: Status = "complete"
    error: str = ""
    notices: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = 0.0
    attachments: list[AttachmentInfo] = field(default_factory=list)
    reasoning: str = ""
    """What the model reasoned before it answered, when it shows that; empty otherwise."""
    reasoning_ms: int = 0
    """How long it reasoned: from its first reasoning to its first word (or to the end)."""
    steps: list[dict[str, Any]] = field(default_factory=list)
    """A reply that used tools, step by step (as the Playground runner keeps them); empty for one that did not."""


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    title_state: TitleState
    model: str
    effort: str
    archived: bool
    temporary: bool
    current_leaf: str
    branched_from: str
    branched_at: str
    created_at: float
    updated_at: float
    messages: list[ChatMessage] = field(default_factory=list)
    tools_off: list[str] = field(default_factory=list)
    """The tools (by their Settings switch) this conversation does without."""
    full_access: bool = False
    """Its tools run without asking, whatever ``playground.approval`` says."""


def title_from(text: str) -> str:
    """The first line's worth of ``text``, whitespace folded, cut at :data:`TITLE_LENGTH`."""
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= TITLE_LENGTH else flat[: TITLE_LENGTH - 1].rstrip() + "…"


def _id() -> str:
    return uuid.uuid4().hex


class ConversationStore:
    """Conversations, their message trees and attachments in the shared database.

    The database's own lock serialises statements; the store's lock keeps the
    reads and writes of one change together.
    """

    def __init__(self, db: Database | None = None) -> None:
        self._db = db or Database()
        self._lock = threading.RLock()

    # -- conversations ----------------------------------------------------- #
    def conversations(self, *, archived: bool = False) -> list[Conversation]:
        """Every kept conversation on one side of the archive, the most recently used first (no messages)."""
        rows = self._db.fetchall(
            "SELECT * FROM conversations WHERE archived = ? AND temporary = 0 ORDER BY updated_at DESC, created_at DESC", (int(archived),)
        )
        return [_conversation(row) for row in rows]

    def create(
        self, *, model: str = "", effort: str = "", title: str = "", temporary: bool = False, tools_off: list[str] | None = None, full_access: bool = False,
    ) -> Conversation:
        now = time.time()
        conversation_id = _id()
        self._db.execute(
            "INSERT INTO conversations(id, title, title_state, model, effort, archived, temporary, tools_off, full_access, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
            (conversation_id, title_from(title), "user" if title.strip() else "", model, effort, int(temporary), json.dumps(tools_off or []), int(full_access), now, now),
        )
        conversation = self.get(conversation_id)
        assert conversation is not None
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        """One conversation with every message of its tree, oldest first; None when there is none by that id."""
        row = self._db.fetchone("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        if row is None:
            return None
        messages = self._db.fetchall("SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY created_at, rowid", (conversation_id,))
        attached = self._attachments_of([m["id"] for m in messages])
        return _conversation(row, [_message(m, attached.get(m["id"], [])) for m in messages])

    def update(
        self, conversation_id: str, *, title: str | None = None, title_state: TitleState | None = None, model: str | None = None,
        effort: str | None = None, archived: bool | None = None, current_leaf: str | None = None, tools_off: list[str] | None = None,
        full_access: bool | None = None,
    ) -> Conversation | None:
        """Change what is given; archiving and moving between versions do not count as use.

        A title given without a ``title_state`` is the user's own, and is then
        never replaced by a generated one. ``current_leaf`` must be a message
        of this conversation.
        """
        changes: dict[str, object] = {}
        if title is not None:
            changes["title"] = title_from(title)
            changes["title_state"] = title_state or "user"
        elif title_state is not None:
            changes["title_state"] = title_state
        if model is not None:
            changes["model"] = model
        if effort is not None:
            changes["effort"] = effort
        if archived is not None:
            changes["archived"] = int(archived)
        if tools_off is not None:
            changes["tools_off"] = json.dumps(list(dict.fromkeys(tools_off)))
        if full_access is not None:
            changes["full_access"] = int(full_access)
        if current_leaf is not None:
            if self._db.fetchone("SELECT 1 FROM chat_messages WHERE id = ? AND conversation_id = ?", (current_leaf, conversation_id)) is None:
                raise ValueError(f"No message '{current_leaf}' in this conversation.")
            changes["current_leaf"] = current_leaf
        if changes:
            assignments = ", ".join(f"{column} = ?" for column in changes)
            self._db.execute(f"UPDATE conversations SET {assignments} WHERE id = ?", (*changes.values(), conversation_id))
        return self.get(conversation_id)

    def delete(self, conversation_id: str) -> bool:
        """Remove a conversation, its messages and the attachments only it carried; False when there was none."""
        with self._lock, self._db.transaction() as conn:
            ids = [row[0] for row in conn.execute("SELECT id FROM chat_messages WHERE conversation_id = ?", (conversation_id,))]
            attachment_ids = _linked_attachments(conn, ids)
            conn.executemany("DELETE FROM message_attachments WHERE message_id = ?", [(i,) for i in ids])
            conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,))
            gone = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount > 0
            _drop_unlinked(conn, attachment_ids)
        if gone and self.session() == conversation_id:
            self.set_session(None)
        return gone

    def purge_temporary(self) -> int:
        """Delete every temporary conversation: what is left of them after a restart. Returns how many."""
        ids = [row[0] for row in self._db.fetchall("SELECT id FROM conversations WHERE temporary = 1")]
        for conversation_id in ids:
            self.delete(conversation_id)
        return len(ids)

    def tidy(self) -> None:
        """What a start leaves behind: temporary conversations, unsent attachments, and replies cut off mid-stream (kept as stopped)."""
        self.purge_temporary()
        self.purge_unsent()
        self._db.execute("UPDATE chat_messages SET status = 'stopped' WHERE status = 'streaming'")

    # -- messages ----------------------------------------------------------- #
    def message(self, message_id: str) -> ChatMessage | None:
        row = self._db.fetchone("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
        if row is None:
            return None
        return _message(row, self._attachments_of([message_id]).get(message_id, []))

    def add_message(
        self, conversation_id: str, parent_id: str | None, role: Role, content: str, *, model: str = "", effort: str = "",
        status: Status = "complete", attachment_ids: list[str] | tuple[str, ...] = (), notices: list[dict[str, Any]] | None = None,
    ) -> ChatMessage | None:
        """Add a message after ``parent_id`` (None: at the root) and make it the end of the path on screen.

        None when the conversation does not exist. The first question names an
        untitled conversation (provisionally, until a model names it), and any
        message brings an archived conversation back and moves it to the top.
        """
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        now = time.time()
        message_id = _id()
        with self._lock, self._db.transaction(immediate=True) as conn:
            row = conn.execute("SELECT title, title_state FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row is None:
                return None
            if parent_id is not None and conn.execute("SELECT 1 FROM chat_messages WHERE id = ? AND conversation_id = ?", (parent_id, conversation_id)).fetchone() is None:
                raise ValueError(f"No message '{parent_id}' in this conversation.")
            for attachment_id in attachment_ids:
                if conn.execute("SELECT 1 FROM attachments WHERE id = ?", (attachment_id,)).fetchone() is None:
                    raise ValueError(f"No attachment '{attachment_id}'.")
            conn.execute(
                "INSERT INTO chat_messages(id, conversation_id, parent_id, role, content, model, effort, status, notices, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, conversation_id, parent_id, role, content, model, effort, status, json.dumps(notices or []), now),
            )
            conn.executemany(
                "INSERT INTO message_attachments(message_id, attachment_id, position) VALUES (?, ?, ?)",
                [(message_id, attachment_id, i) for i, attachment_id in enumerate(attachment_ids)],
            )
            title, title_state = row["title"], row["title_state"]
            if not title and role == "user" and content.strip():
                title, title_state = title_from(content), "provisional"
            conn.execute(
                "UPDATE conversations SET title = ?, title_state = ?, current_leaf = ?, archived = 0, updated_at = ? WHERE id = ?",
                (title, title_state, message_id, now, conversation_id),
            )
        return self.message(message_id)

    def finish_message(
        self, message_id: str, *, content: str, status: Status, error: str = "", notices: list[dict[str, Any]] | None = None,
        reasoning: str = "", reasoning_ms: int = 0, steps: list[dict[str, Any]] | None = None,
    ) -> None:
        """Settle a streamed reply: what it said (and reasoned, and did), and how it ended."""
        changes: dict[str, object] = {"content": content, "status": status, "error": error, "reasoning": reasoning, "reasoning_ms": reasoning_ms}
        if notices is not None:
            changes["notices"] = json.dumps(notices)
        if steps is not None:
            changes["steps"] = json.dumps(steps, ensure_ascii=False)
        assignments = ", ".join(f"{column} = ?" for column in changes)
        self._db.execute(f"UPDATE chat_messages SET {assignments} WHERE id = ?", (*changes.values(), message_id))

    def full_access(self, conversation_id: str) -> bool:
        """Whether a conversation's tools run without asking (read alone, as each call is about to run)."""
        row = self._db.fetchone("SELECT full_access FROM conversations WHERE id = ?", (conversation_id,))
        return bool(row and row["full_access"])

    def save_steps(self, message_id: str, steps: list[dict[str, Any]], content: str) -> None:
        """Keep a reply's steps so far, while it goes on: a tool's effects are real before the reply ends."""
        self._db.execute("UPDATE chat_messages SET steps = ?, content = ? WHERE id = ?", (json.dumps(steps, ensure_ascii=False), content, message_id))

    def path(self, conversation_id: str, leaf_id: str | None = None) -> list[ChatMessage]:
        """The messages from the root to ``leaf_id`` (by default the conversation's current leaf), in order."""
        conversation = self.get(conversation_id)
        if conversation is None:
            return []
        return path_to(conversation.messages, leaf_id or conversation.current_leaf)

    def branch(self, conversation_id: str, message_id: str) -> Conversation | None:
        """A new conversation holding the path up to ``message_id``: the same messages, under new ids."""
        source = self.get(conversation_id)
        if source is None:
            return None
        path = path_to(source.messages, message_id)
        if not path or path[-1].id != message_id:
            raise ValueError(f"No message '{message_id}' in this conversation.")
        now = time.time()
        branch_id = _id()
        with self._lock, self._db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO conversations(id, title, title_state, model, effort, archived, temporary, branched_from, branched_at, tools_off, full_access, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
                (branch_id, source.title, source.title_state, source.model, source.effort, int(source.temporary), source.id, message_id, json.dumps(source.tools_off),
                 int(source.full_access), now, now),
            )
            parent: str | None = None
            for i, m in enumerate(path):
                copy_id = _id()
                # Kept in their order: each copy a moment after the last.
                conn.execute(
                    "INSERT INTO chat_messages(id, conversation_id, parent_id, role, content, model, effort, status, error, notices, reasoning, reasoning_ms, steps, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (copy_id, branch_id, parent, m.role, m.content, m.model, m.effort, m.status, m.error, json.dumps(m.notices), m.reasoning, m.reasoning_ms, json.dumps(m.steps, ensure_ascii=False), m.created_at + i * 1e-6),
                )
                conn.executemany(
                    "INSERT INTO message_attachments(message_id, attachment_id, position) VALUES (?, ?, ?)",
                    [(copy_id, a.id, j) for j, a in enumerate(m.attachments)],
                )
                parent = copy_id
            conn.execute("UPDATE conversations SET current_leaf = ? WHERE id = ?", (parent or "", branch_id))
        return self.get(branch_id)

    # -- attachments -------------------------------------------------------- #
    def add_attachment(
        self, *, kind: AttachmentKind, name: str, mime: str, data: bytes, text: str = "", width: int | None = None, height: int | None = None,
    ) -> AttachmentInfo:
        attachment_id = _id()
        self._db.execute(
            "INSERT INTO attachments(id, kind, name, mime, size, data, text, width, height, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (attachment_id, kind, name, mime, len(data), sqlite3.Binary(data), text, width, height, time.time()),
        )
        return AttachmentInfo(id=attachment_id, kind=kind, name=name, mime=mime, size=len(data), width=width, height=height)

    def attachment(self, attachment_id: str) -> Attachment | None:
        row = self._db.fetchone("SELECT * FROM attachments WHERE id = ?", (attachment_id,))
        if row is None:
            return None
        return Attachment(
            id=row["id"], kind=row["kind"], name=row["name"], mime=row["mime"], size=row["size"], width=row["width"], height=row["height"],
            data=bytes(row["data"]), text=row["text"], description=row["description"],
        )

    def describe(self, attachment_id: str, description: str) -> None:
        """Keep what a model saw in an image, for the models that cannot see it themselves."""
        self._db.execute("UPDATE attachments SET description = ? WHERE id = ?", (description, attachment_id))

    def delete_attachment(self, attachment_id: str) -> bool:
        """Remove an attachment no message carries (one taken back before sending); False otherwise."""
        with self._lock, self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM message_attachments WHERE attachment_id = ?", (attachment_id,)).fetchone() is not None:
                return False
            return conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,)).rowcount > 0

    def purge_unsent(self, *, older_than: float = UNSENT_ATTACHMENT_SECONDS) -> int:
        """Delete attachments uploaded but never sent, once they are ``older_than`` seconds old."""
        cutoff = time.time() - older_than
        return self._db.execute(
            "DELETE FROM attachments WHERE created_at < ? AND id NOT IN (SELECT attachment_id FROM message_attachments)", (cutoff,)
        ).rowcount

    def _attachments_of(self, message_ids: list[str]) -> dict[str, list[AttachmentInfo]]:
        out: dict[str, list[AttachmentInfo]] = {}
        # In slices: SQLite caps how many parameters one statement may take.
        for start in range(0, len(message_ids), 500):
            chunk = message_ids[start : start + 500]
            rows = self._db.fetchall(
                "SELECT ma.message_id, a.id, a.kind, a.name, a.mime, a.size, a.width, a.height FROM message_attachments ma "
                f"JOIN attachments a ON a.id = ma.attachment_id WHERE ma.message_id IN ({','.join('?' * len(chunk))}) ORDER BY ma.message_id, ma.position",
                chunk,
            )
            for r in rows:
                out.setdefault(r["message_id"], []).append(
                    AttachmentInfo(id=r["id"], kind=r["kind"], name=r["name"], mime=r["mime"], size=r["size"], width=r["width"], height=r["height"])
                )
        return out

    # -- session ------------------------------------------------------------ #
    def session(self) -> str | None:
        """The conversation open last, if it still exists and is not temporary."""
        conversation_id = self._db.get_meta(SESSION_KEY)
        if not conversation_id:
            return None
        exists = self._db.fetchone("SELECT 1 FROM conversations WHERE id = ? AND temporary = 0", (conversation_id,))
        return conversation_id if exists else None

    def set_session(self, conversation_id: str | None) -> None:
        self._db.set_meta(SESSION_KEY, conversation_id or "")


def path_to(messages: list[ChatMessage], leaf_id: str) -> list[ChatMessage]:
    """The messages from the root down to ``leaf_id``; empty when it is not among ``messages``."""
    by_id = {m.id: m for m in messages}
    out: list[ChatMessage] = []
    current = by_id.get(leaf_id)
    while current is not None:
        out.append(current)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return out[::-1]


def _linked_attachments(conn: sqlite3.Connection, message_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(message_ids), 500):
        chunk = message_ids[start : start + 500]
        found.update(row[0] for row in conn.execute(f"SELECT attachment_id FROM message_attachments WHERE message_id IN ({','.join('?' * len(chunk))})", chunk))
    return found


def _drop_unlinked(conn: sqlite3.Connection, attachment_ids: set[str]) -> None:
    for attachment_id in attachment_ids:
        if conn.execute("SELECT 1 FROM message_attachments WHERE attachment_id = ?", (attachment_id,)).fetchone() is None:
            conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))


def _message(row: sqlite3.Row, attachments: list[AttachmentInfo]) -> ChatMessage:
    try:
        notices = json.loads(row["notices"] or "[]")
    except ValueError:
        notices = []
    return ChatMessage(
        id=row["id"], conversation_id=row["conversation_id"], parent_id=row["parent_id"], role=row["role"], content=row["content"],
        model=row["model"], effort=row["effort"], status=row["status"], error=row["error"], notices=notices if isinstance(notices, list) else [],
        created_at=row["created_at"], attachments=attachments, reasoning=row["reasoning"], reasoning_ms=row["reasoning_ms"], steps=_json_list(row["steps"]),
    )


def _json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _conversation(row: sqlite3.Row, messages: list[ChatMessage] | None = None) -> Conversation:
    return Conversation(
        id=row["id"], title=row["title"], title_state=row["title_state"], model=row["model"], effort=row["effort"], archived=bool(row["archived"]),
        temporary=bool(row["temporary"]), current_leaf=row["current_leaf"], branched_from=row["branched_from"], branched_at=row["branched_at"],
        created_at=row["created_at"], updated_at=row["updated_at"], messages=messages or [], tools_off=[str(t) for t in _json_list(row["tools_off"])],
        full_access=bool(row["full_access"]),
    )
