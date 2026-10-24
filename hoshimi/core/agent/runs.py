"""Replies and compactions that work in the background, apart from the page that started them.

Each runs on a thread of its own and keeps the events it has sent, so a page
can follow it from its first event: the one that started it, and any that
opens the conversation later (another chat was open meanwhile, the page was
reloaded, another tab). An event is kept encoded, as it was when it was sent:
a step the reply goes on changing (a tool call that runs, then is done) is
read later as each event had it. A page that stops following only stops reading; the
run goes on until it ends or is stopped (:meth:`Runs.stop`). One conversation
has at most one run at a time.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Any

from hoshimi.core.errors import ConflictError

logger = logging.getLogger(__name__)

Event = dict[str, Any]

WAIT_SECONDS = 1.0
"""How long a follower waits for the next event before it looks again whether it was let go."""


class Run:
    """One reply or compaction under way: its events so far (encoded by ``encode``), and whether it has ended.

    ``closing`` gives the last event of a run that ended without its own
    ``done`` (it was stopped, or failed outside the reply), from the last
    message an event carried; ``ended`` is called once it has ended, with
    whether anyone was following it then. ``done`` is the message of the
    ``done`` it ended with.
    """

    def __init__(
        self,
        conversation_id: str,
        kind: str,
        events: Iterator[Event],
        cancel: threading.Event,
        *,
        closing: Callable[[Event | None], Event | None],
        ended: Callable[[Run, bool], None],
        encode: Callable[[Event], str] = json.dumps,
    ) -> None:
        self.conversation_id = conversation_id
        self.kind = kind
        self.cancel = cancel
        self.events: list[str] = []
        self.done: Event | None = None
        self.finished = False
        self.followers = 0
        self._source = events
        self._closing = closing
        self._ended = ended
        self._encode = encode
        self._changed = threading.Condition()
        self.thread = threading.Thread(target=self._drive, name=f"playground-{kind}-{conversation_id[:8]}", daemon=True)

    def _push(self, item: Event) -> None:
        if item.get("type") == "done" and isinstance(item.get("message"), dict):
            self.done = item["message"]
        line = self._encode(item)
        with self._changed:
            self.events.append(line)
            self._changed.notify_all()

    def _drive(self) -> None:
        last: Event | None = None
        done = False
        try:
            for item in self._source:
                message = item.get("message")
                if isinstance(message, dict) and item.get("type") in ("assistant", "checkpoint", "done"):
                    last = message
                done = done or item.get("type") == "done"
                self._push(item)
                if self.cancel.is_set():
                    break
        except Exception:  # the run must still end, and say how
            logger.exception("a Playground %s failed", self.kind)
        finally:
            try:
                # Closing a reply stopped before its end settles it as stopped, with what came.
                close = getattr(self._source, "close", None)
                if close is not None:
                    close()
            except Exception:
                logger.exception("a Playground %s did not close cleanly", self.kind)
            if not done:
                try:
                    final = self._closing(last)
                except Exception:
                    logger.exception("a Playground %s could not say how it ended", self.kind)
                    final = None
                if final is not None:
                    self._push(final)
            with self._changed:
                self.finished = True
                followed = self.followers > 0
                self._changed.notify_all()
            self._ended(self, followed)

    def follow(self, after: int = 0) -> Follower:
        """The run's events (encoded) from the ``after``-th on, as they come, until it ends or the follower is let go."""
        return Follower(self, after)

    def stop(self) -> None:
        self.cancel.set()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)


class Follower:
    """One page reading a run. :meth:`release` lets it go at once, even while it waits for the next event."""

    def __init__(self, run: Run, after: int) -> None:
        self.run = run
        self.next = max(0, after)
        self.released = False
        with run._changed:
            run.followers += 1
        self.counted = True

    def __iter__(self) -> Follower:
        return self

    def __next__(self) -> str:
        run = self.run
        with run._changed:
            while not self.released and self.next >= len(run.events) and not run.finished:
                run._changed.wait(WAIT_SECONDS)
            if self.released or self.next >= len(run.events):
                self._leave()
                raise StopIteration
            item = run.events[self.next]
            self.next += 1
            return item

    def release(self) -> None:
        with self.run._changed:
            self.released = True
            self.run._changed.notify_all()

    def close(self) -> None:
        self.release()
        with self.run._changed:
            self._leave()

    def _leave(self) -> None:
        if self.counted:
            self.counted = False
            self.run.followers -= 1


class Runs:
    """The runs working now, one at most per conversation."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active: dict[str, Run] = {}

    def start(
        self,
        conversation_id: str,
        kind: str,
        events: Iterator[Event],
        cancel: threading.Event,
        *,
        closing: Callable[[Event | None], Event | None],
        after: Callable[[Run, bool], None] | None = None,
        encode: Callable[[Event], str] = json.dumps,
    ) -> Run:
        """Start ``events`` on a thread of its own; a :class:`ConflictError` when the conversation has a run already."""

        def ended(run: Run, followed: bool) -> None:
            with self.lock:
                if self.active.get(conversation_id) is run:
                    del self.active[conversation_id]
            if after is not None:
                try:
                    after(run, followed)
                except Exception:
                    logger.exception("after a Playground %s", kind)

        run = Run(conversation_id, kind, events, cancel, closing=closing, ended=ended, encode=encode)
        with self.lock:
            if conversation_id in self.active:
                close = getattr(events, "close", None)
                if close is not None:
                    close()
                raise ConflictError("A reply is running in this conversation; wait for it to end, or stop it.")
            self.active[conversation_id] = run
        run.thread.start()
        return run

    def get(self, conversation_id: str) -> Run | None:
        with self.lock:
            return self.active.get(conversation_id)

    def running(self, conversation_id: str) -> bool:
        return self.get(conversation_id) is not None

    def stop(self, conversation_id: str, *, wait: float = 0.0) -> bool:
        """Stop the conversation's run, waiting up to ``wait`` seconds for it to end; False when none runs."""
        run = self.get(conversation_id)
        if run is None:
            return False
        run.stop()
        if wait:
            run.join(wait)
        return True

    def close(self, wait: float = 5.0) -> None:
        """Stop every run (the server is going down), each settling as stopped."""
        with self.lock:
            runs = list(self.active.values())
        for run in runs:
            run.stop()
        for run in runs:
            run.join(wait)
