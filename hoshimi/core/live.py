"""The calls in flight right now, fanned out to whoever is watching.

A turn streams in a worker thread (a sync generator under a streaming
response), while a watcher is an ``asyncio`` task on the server's loop. The
two never share a queue directly: every event crosses the boundary through
``loop.call_soon_threadsafe``, which is the one hand-off asyncio promises is
safe from a foreign thread.

Two rules keep a watcher from becoming the gateway's problem:

- *bounded queues.* A tab left open on a stalled connection must not hold an
  unbounded backlog of progress frames. When its queue overflows the
  subscriber is marked **dropped** and its stream ends; the client reconnects
  and a fresh snapshot replaces everything it missed, which is cheaper and
  more correct than replaying a backlog.
- *publishing never blocks a turn.* A put is scheduled, not awaited, and a
  loop that has already closed is a watcher that has already gone.

Standard library only: this sits under :mod:`hoshimi.core.stats`, which the
command line imports too.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

QUEUE_LIMIT = 1000
"""Events a watcher may fall behind by before it is cut loose."""

CLOSED: Any = object()
"""Queued to a subscriber whose stream should end now (shutdown, or a drop)."""


class Subscription:
    """One watcher: its loop, its queue, and whether it has been cut loose."""

    def __init__(self, loop: asyncio.AbstractEventLoop, limit: int = QUEUE_LIMIT) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=limit)
        self.dropped = False

    def deliver(self, event: Any) -> None:
        """Runs on the subscriber's own loop; never call it from another thread."""
        if self.dropped:
            return
        if event is CLOSED:
            # Queued behind what is already waiting, so a closing stream still
            # delivers every event published before the close. Only a full
            # queue has no room for the sentinel; its reader then sees the
            # flag on its next read instead.
            if self.queue.full():
                self.dropped = True
            else:
                self.queue.put_nowait(CLOSED)
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped = True


class LiveHub:
    """In-flight calls by id, and the subscribers to tell about them.

    ``begin`` registers a call with a *view* callable rather than a frozen
    dict: a snapshot then reads the call as it is at that moment, so progress
    that was throttled away (or never published because nobody was watching)
    is still current when someone does subscribe.
    """

    def __init__(self, *, queue_limit: int = QUEUE_LIMIT) -> None:
        self.lock = threading.Lock()
        self._active: dict[str, Callable[[], dict[str, Any]]] = {}
        self._subscribers: list[Subscription] = []
        self._queue_limit = queue_limit

    # -- calls --------------------------------------------------------------- #
    def begin(self, call_id: str, view: Callable[[], dict[str, Any]]) -> None:
        with self.lock:
            self._active[call_id] = view
            if self._subscribers:
                self._publish_locked({"type": "started", "call": view()})

    def progress(self, call_id: str) -> None:
        """Tell watchers where a call has got to; free when nobody is watching."""
        if not self._subscribers:
            return
        with self.lock:
            view = self._active.get(call_id)
            if view is not None and self._subscribers:
                self._publish_locked({"type": "progress", "call": view()})

    def end(self, call_id: str, event: dict[str, Any] | None = None) -> None:
        """Forget a call; ``event`` (its ``finished`` frame) goes out in the same breath."""
        with self.lock:
            self._active.pop(call_id, None)
            if event is not None and self._subscribers:
                self._publish_locked(event)

    def active(self) -> list[dict[str, Any]]:
        with self.lock:
            return [view() for view in self._active.values()]

    # -- watchers ------------------------------------------------------------ #
    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> tuple[Subscription, list[dict[str, Any]]]:
        """Register a watcher and return the calls in flight, atomically.

        Atomic is the point: a call that starts after this returns arrives as
        a ``started`` event, and one that started before is in the list —
        never both, never neither.
        """
        subscription = Subscription(loop, self._queue_limit)
        with self.lock:
            self._subscribers.append(subscription)
            return subscription, [view() for view in self._active.values()]

    def unsubscribe(self, subscription: Subscription) -> None:
        with self.lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    def close(self) -> None:
        """End every current watcher's stream (a server shutting down).

        Not terminal for the hub: a later subscriber is served normally, so an
        embedder that reuses its services after a shutdown is not left with a
        stream that ends the moment it opens.
        """
        with self.lock:
            subscribers, self._subscribers = self._subscribers, []
        for subscription in subscribers:
            self._send(subscription, CLOSED)

    def _publish_locked(self, event: dict[str, Any]) -> None:
        for subscription in list(self._subscribers):
            if subscription.dropped or not self._send(subscription, event):
                self._subscribers.remove(subscription)

    @staticmethod
    def _send(subscription: Subscription, event: Any) -> bool:
        try:
            subscription.loop.call_soon_threadsafe(subscription.deliver, event)
        except RuntimeError:
            # The loop is closed: the watcher went with it.
            subscription.dropped = True
            return False
        return True
