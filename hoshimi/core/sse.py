"""Tiny SSE parser shared by all upstream adapters."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any


def iter_sse(lines: Iterable[str]) -> Iterator[tuple[str | None, str]]:
    """Yield (event, data) pairs from an iterable of SSE text lines."""
    event: str | None = None
    data_lines: list[str] = []

    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            event = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())

    if data_lines:
        yield event, "\n".join(data_lines)


def parse_sse_events(
    pairs: Iterable[tuple[str | None, str]],
) -> Iterator[tuple[str | None, dict[str, Any]]]:
    """Parse the JSON ``data`` of each SSE event, skipping unparseable ones."""
    for event, data in pairs:
        if not data:
            continue
        if data == "[DONE]":
            yield "done", {"__done__": True}
            continue
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield event, obj
