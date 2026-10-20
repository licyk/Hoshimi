"""Keep SSE readers alive while synchronous upstream providers are thinking."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Iterator

PING = b'event: ping\ndata: {"type":"ping"}\n\n'
HEARTBEAT_SECONDS = 15.0


async def with_heartbeats(
    frames: Iterator[bytes],
    *,
    cancel: Callable[[], None] | None = None,
    interval: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[bytes]:
    """Emit pings only between complete SSE frames, with one pending read."""
    stopped = threading.Event()
    lock = threading.Lock()
    sentinel = object()

    def close() -> None:
        closer = getattr(frames, "close", None)
        if closer is not None:
            closer()

    def pull() -> bytes | object:
        with lock:
            try:
                return sentinel if stopped.is_set() else next(frames, sentinel)
            finally:
                if stopped.is_set():
                    close()

    # A pending read survives heartbeat timeouts. Do not hold an AnyIO cancel
    # scope across yield: an ASGI server may close the generator from another task.
    pending: asyncio.Task[bytes | object] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(asyncio.to_thread(pull))
            done, _ = await asyncio.wait([pending], timeout=interval)
            if not done:
                yield PING
                continue
            value = pending.result()
            pending = None
            if value is sentinel:
                return
            assert isinstance(value, bytes)
            yield value
    finally:
        stopped.set()
        if cancel is not None:
            cancel()
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        if lock.acquire(blocking=False):
            try:
                close()
            finally:
                lock.release()
