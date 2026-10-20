"""Heartbeats do not split native frames and cancellation releases the producer."""

import threading

import anyio

from hoshimi.api.heartbeat import PING, with_heartbeats
from hoshimi.api.routers.messages import _native_sse


def test_heartbeat_during_thinking_and_close_releases_upstream():
    waiting = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    first = b'event: message_start\ndata: {"type":"message_start"}\n\n'

    def frames():
        try:
            yield first
            waiting.set()
            release.wait(3)
            yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        finally:
            closed.set()

    async def run():
        stream = with_heartbeats(frames(), cancel=release.set, interval=0.01)
        assert await anext(stream) == first
        assert await anext(stream) == PING
        assert waiting.is_set()
        await stream.aclose()
        assert await anyio.to_thread.run_sync(closed.wait, 1)

    anyio.run(run)


def test_heartbeats_are_between_whole_native_events_even_when_json_is_split():
    release = threading.Event()
    first = b'event: message_start\ndata: {"type":"message_start"}\n\n'
    second = b'event: future_event\ndata: {"type":"future_event","text":"long pause"}\n\n'

    def chunks():
        yield {"type": "messages_bytes", "data": first + second[:40]}
        release.wait(3)
        yield {"type": "messages_bytes", "data": second[40:]}

    async def run():
        stream = with_heartbeats(_native_sse(chunks()), cancel=release.set, interval=0.01)
        try:
            assert await anext(stream) == first
            assert await anext(stream) == PING
            release.set()
            remaining = [part async for part in stream]
            assert [part for part in remaining if part != PING] == [second]
        finally:
            await stream.aclose()

    anyio.run(run)
