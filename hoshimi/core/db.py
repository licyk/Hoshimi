"""SQLite storage for what the gateway records: usage, check-in history, probe verdicts, conversations.

One file, ``hoshimi.db`` in the data directory, replaces the JSON stores that
used to be rewritten whole on every change (``stats.json``, ``checkin.json``,
``probe.json``). A JSON store costs a full rewrite per save and caps how much
history is affordable to keep; a table is appended to and pruned in place.

Configuration, credentials, ``eac-user.json``, ``server.json`` and the catalog
cache stay files on purpose: they are read or edited by hand and by other
programs, and none of them grows with traffic.

Standard library only. The schema moves forward through :data:`MIGRATIONS`,
numbered by ``PRAGMA user_version``; an entry is never edited once released.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_FILE_NAME = "hoshimi.db"
"""The database file in the data directory."""

MEMORY = ":memory:"

_BUCKET_COLUMNS = """
        input          INTEGER NOT NULL DEFAULT 0,
        output         INTEGER NOT NULL DEFAULT 0,
        reasoning      INTEGER NOT NULL DEFAULT 0,
        cache_read     INTEGER NOT NULL DEFAULT 0,
        cache_write    INTEGER NOT NULL DEFAULT 0,
        calls          INTEGER NOT NULL DEFAULT 0,
        failed         INTEGER NOT NULL DEFAULT 0,
        no_usage       INTEGER NOT NULL DEFAULT 0,
        ttft_ms        INTEGER NOT NULL DEFAULT 0,
        ttft_count     INTEGER NOT NULL DEFAULT 0,
        decode_ms      INTEGER NOT NULL DEFAULT 0,
        decode_tokens  INTEGER NOT NULL DEFAULT 0,
        last_at        REAL    NOT NULL DEFAULT 0"""

# Each entry is one migration. Append only; never edit a released entry.
MIGRATIONS: list[str] = [
    # 1: usage (hoshimi.core.stats), check-in state and history (hoshimi.core.credits), probe verdicts (hoshimi.core.probe).
    f"""
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE calls (
        seq            INTEGER PRIMARY KEY AUTOINCREMENT,
        id             TEXT    NOT NULL,
        ts             REAL    NOT NULL,
        at             TEXT    NOT NULL,
        model          TEXT    NOT NULL DEFAULT '',
        provider       TEXT    NOT NULL DEFAULT '',
        account        TEXT    NOT NULL DEFAULT '',
        origin         TEXT    NOT NULL DEFAULT 'chat',
        effort         TEXT    NOT NULL DEFAULT '',
        stream         INTEGER NOT NULL DEFAULT 0,
        ok             INTEGER NOT NULL DEFAULT 1,
        input          INTEGER NOT NULL DEFAULT 0,
        output         INTEGER NOT NULL DEFAULT 0,
        reasoning      INTEGER NOT NULL DEFAULT 0,
        cache_read     INTEGER NOT NULL DEFAULT 0,
        cache_write    INTEGER NOT NULL DEFAULT 0,
        ttft_ms        INTEGER,
        decode_ms      INTEGER NOT NULL DEFAULT 0,
        decode_tokens  INTEGER NOT NULL DEFAULT 0,
        tps            INTEGER,
        elapsed_ms     INTEGER,
        attempts       INTEGER NOT NULL DEFAULT 0,
        finish_reason  TEXT    NOT NULL DEFAULT '',
        error_code     TEXT    NOT NULL DEFAULT '',
        error_message  TEXT    NOT NULL DEFAULT '',
        no_usage       INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX calls_ts ON calls(ts);
    CREATE INDEX calls_model ON calls(model, seq);
    CREATE INDEX calls_provider ON calls(provider, account, seq);
    CREATE INDEX calls_origin ON calls(origin, seq);
    CREATE INDEX calls_failed ON calls(seq) WHERE ok = 0;
    CREATE TABLE usage_hourly (
        bucket_start   INTEGER NOT NULL,
        model          TEXT    NOT NULL,
        provider       TEXT    NOT NULL,
        account        TEXT    NOT NULL,
        origin         TEXT    NOT NULL,{_BUCKET_COLUMNS},
        PRIMARY KEY (bucket_start, model, provider, account, origin)
    ) WITHOUT ROWID;
    CREATE TABLE usage_daily (
        day            TEXT    NOT NULL,
        model          TEXT    NOT NULL,
        provider       TEXT    NOT NULL,
        account        TEXT    NOT NULL,
        origin         TEXT    NOT NULL,{_BUCKET_COLUMNS},
        PRIMARY KEY (day, model, provider, account, origin)
    ) WITHOUT ROWID;
    CREATE TABLE usage_hist_hourly (
        bucket_start   INTEGER NOT NULL,
        model          TEXT    NOT NULL,
        provider       TEXT    NOT NULL,
        account        TEXT    NOT NULL,
        origin         TEXT    NOT NULL,
        kind           TEXT    NOT NULL,
        bin            INTEGER NOT NULL,
        count          INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (bucket_start, model, provider, account, origin, kind, bin)
    ) WITHOUT ROWID;
    CREATE TABLE usage_hist_daily (
        day            TEXT    NOT NULL,
        model          TEXT    NOT NULL,
        provider       TEXT    NOT NULL,
        account        TEXT    NOT NULL,
        origin         TEXT    NOT NULL,
        kind           TEXT    NOT NULL,
        bin            INTEGER NOT NULL,
        count          INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, model, provider, account, origin, kind, bin)
    ) WITHOUT ROWID;
    CREATE TABLE lifetime (
        grp            TEXT    NOT NULL,
        name           TEXT    NOT NULL,
        input          INTEGER NOT NULL DEFAULT 0,
        output         INTEGER NOT NULL DEFAULT 0,
        reasoning      INTEGER NOT NULL DEFAULT 0,
        cache_read     INTEGER NOT NULL DEFAULT 0,
        cache_write    INTEGER NOT NULL DEFAULT 0,
        calls          INTEGER NOT NULL DEFAULT 0,
        failed         INTEGER NOT NULL DEFAULT 0,
        no_usage       INTEGER NOT NULL DEFAULT 0,
        last_at        REAL    NOT NULL DEFAULT 0,
        PRIMARY KEY (grp, name)
    ) WITHOUT ROWID;
    CREATE TABLE checkins (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        at             REAL    NOT NULL,
        date           TEXT    NOT NULL,
        provider       TEXT    NOT NULL,
        account        TEXT    NOT NULL,
        status         TEXT    NOT NULL,
        ok             INTEGER NOT NULL,
        credits        INTEGER NOT NULL DEFAULT 0,
        message        TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX checkins_at ON checkins(at);
    CREATE INDEX checkins_provider ON checkins(provider, at);
    CREATE TABLE probe_results (
        model          TEXT PRIMARY KEY,
        result         TEXT NOT NULL
    );
    """,
    # 2: Playground conversations and their messages (hoshimi.core.conversations).
    """
    CREATE TABLE conversations (
        id             TEXT    PRIMARY KEY,
        title          TEXT    NOT NULL DEFAULT '',
        model          TEXT    NOT NULL DEFAULT '',
        effort         TEXT    NOT NULL DEFAULT '',
        archived       INTEGER NOT NULL DEFAULT 0,
        created_at     REAL    NOT NULL,
        updated_at     REAL    NOT NULL
    );
    CREATE INDEX conversations_recent ON conversations(archived, updated_at);
    CREATE TABLE conversation_messages (
        conversation_id TEXT    NOT NULL,
        position        INTEGER NOT NULL,
        role            TEXT    NOT NULL,
        content         TEXT    NOT NULL,
        model           TEXT    NOT NULL DEFAULT '',
        created_at      REAL    NOT NULL,
        PRIMARY KEY (conversation_id, position)
    ) WITHOUT ROWID;
    """,
    # 3: Playground conversations become trees (edits and retries are sibling messages), with
    # attachments, temporary conversations, generated titles and branches. Each message of a
    # version-2 conversation follows the one before it, so the list converts to a single path.
    """
    ALTER TABLE conversations ADD COLUMN current_leaf  TEXT    NOT NULL DEFAULT '';
    ALTER TABLE conversations ADD COLUMN temporary     INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE conversations ADD COLUMN title_state   TEXT    NOT NULL DEFAULT '';
    ALTER TABLE conversations ADD COLUMN branched_from TEXT    NOT NULL DEFAULT '';
    ALTER TABLE conversations ADD COLUMN branched_at   TEXT    NOT NULL DEFAULT '';
    CREATE TABLE chat_messages (
        id              TEXT    PRIMARY KEY,
        conversation_id TEXT    NOT NULL,
        parent_id       TEXT,
        role            TEXT    NOT NULL,
        content         TEXT    NOT NULL,
        model           TEXT    NOT NULL DEFAULT '',
        effort          TEXT    NOT NULL DEFAULT '',
        status          TEXT    NOT NULL DEFAULT 'complete',
        error           TEXT    NOT NULL DEFAULT '',
        notices         TEXT    NOT NULL DEFAULT '[]',
        created_at      REAL    NOT NULL
    );
    CREATE INDEX chat_messages_conversation ON chat_messages(conversation_id, created_at);
    CREATE INDEX chat_messages_parent ON chat_messages(parent_id);
    INSERT INTO chat_messages(id, conversation_id, parent_id, role, content, model, created_at)
        SELECT conversation_id || '-' || position, conversation_id,
               CASE WHEN position = 0 THEN NULL ELSE conversation_id || '-' || (position - 1) END,
               role, content, model, created_at
        FROM conversation_messages;
    UPDATE conversations SET
        current_leaf = COALESCE((SELECT conversation_id || '-' || MAX(position) FROM conversation_messages m WHERE m.conversation_id = conversations.id), ''),
        title_state = CASE WHEN title = '' THEN '' ELSE 'auto' END;
    DROP TABLE conversation_messages;
    CREATE TABLE attachments (
        id             TEXT    PRIMARY KEY,
        kind           TEXT    NOT NULL,
        name           TEXT    NOT NULL,
        mime           TEXT    NOT NULL,
        size           INTEGER NOT NULL,
        data           BLOB    NOT NULL,
        text           TEXT    NOT NULL DEFAULT '',
        description    TEXT    NOT NULL DEFAULT '',
        width          INTEGER,
        height         INTEGER,
        created_at     REAL    NOT NULL
    );
    CREATE TABLE message_attachments (
        message_id     TEXT    NOT NULL,
        attachment_id  TEXT    NOT NULL,
        position       INTEGER NOT NULL,
        PRIMARY KEY (message_id, position)
    ) WITHOUT ROWID;
    CREATE INDEX message_attachments_attachment ON message_attachments(attachment_id);
    """,
    # 4: what a reply reasoned before it answered, and for how long (a model that shows no reasoning keeps none).
    """
    ALTER TABLE chat_messages ADD COLUMN reasoning    TEXT    NOT NULL DEFAULT '';
    ALTER TABLE chat_messages ADD COLUMN reasoning_ms INTEGER NOT NULL DEFAULT 0;
    """,
]


class Database:
    """One shared connection guarded by a lock. Fine for a single local process.

    ``isolation_level=None`` leaves transactions to :meth:`transaction`: a
    statement outside one commits on its own, so nothing is left half-open by
    a caller that forgot to commit.
    """

    def __init__(self, path: Path | str = MEMORY) -> None:
        self.path: Path | str = Path(path) if str(path) != MEMORY else MEMORY
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # The command line may read the same file while a server writes it.
            self._conn.execute("PRAGMA busy_timeout=5000")
        self.created = self.schema_version == 0
        self.migrate()

    @property
    def memory(self) -> bool:
        return self.path == MEMORY

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> None:
        with self._lock:
            current = self.schema_version
            if current > len(MIGRATIONS):
                logger.warning(
                    "%s was last used by a newer Hoshimi (database schema %d, this one knows %d); what it recorded may not show correctly", self.path, current, len(MIGRATIONS)
                )
            for number, script in enumerate(MIGRATIONS, start=1):
                if number <= current:
                    continue
                self._conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")

    @contextmanager
    def transaction(self, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """One transaction on the shared connection; rolled back if the body raises.

        ``immediate`` takes the write lock at once: a transaction that reads
        before it writes, while another process may write the same rows, then
        waits for it instead of failing when it upgrades to a write.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        """Close the connection; a second close is a no-op (shutdown paths overlap)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    # -- meta ------------------------------------------------------------- #
    def get_meta(self, key: str) -> str | None:
        row = self.fetchone("SELECT value FROM meta WHERE key = ?", (key,))
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        self.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def get_json(self, key: str) -> Any:
        """A meta value stored as JSON; None when missing or unreadable."""
        raw = self.get_meta(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def set_json(self, key: str, value: Any) -> None:
        self.set_meta(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def open_store_database(path: Path | None, db: Database | None) -> tuple[Database, bool]:
    """The database a store should use, and whether the store owns (and must close) it.

    A shared ``db`` wins. Otherwise a store given the path of its old JSON
    file opens ``hoshimi.db`` beside it — so ``StatsStore(data_dir /
    "stats.json")`` still persists, and imports that file — and a store given
    nothing keeps its rows in memory, as the JSON stores did without a path.
    """
    if db is not None:
        return db, False
    if path is not None:
        return Database(path.with_name(DB_FILE_NAME)), True
    return Database(MEMORY), True


def retire_legacy_file(path: Path, suffix: str) -> Path | None:
    """Rename an imported (or unreadable) JSON store aside, so it is neither read again nor lost."""
    target = path.with_name(f"{path.name}.{suffix}")
    try:
        path.replace(target)
    except OSError as exc:
        logger.warning("could not rename %s to %s: %s", path, target, exc)
        return None
    return target
