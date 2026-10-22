"""How full a conversation is: the model's context window, what a request takes of it, and when to compact.

Counted, not tokenised: a gateway in front of a hundred stations has no one
tokenizer to ask. A station that reports usage is believed (the prompt and
completion of the last round, plus an estimate for what came after it); one
that does not is estimated whole, at four bytes of UTF-8 to a token, as
coding agents do. Each count says which it was (``source``).

The window is the catalogue's (``ModelSpec.context_window``, from a station's
own listing where it publishes one), lowered to what a station has said
in refusing a request as too long (see :class:`LearnedWindows`).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Literal

from hoshimi.core.catalog import FALLBACK_CONTEXT_WINDOW, FALLBACK_MAX_OUTPUT, ModelSpec

BYTES_PER_TOKEN = 4
IMAGE_TOKENS = 1_800
"""What an image is counted as: about what a model reading a screenshot-sized image is charged."""
MESSAGE_TOKENS = 4
"""Each message's own framing (role, separators)."""

DEFAULT_THRESHOLD = 0.85
"""The share of the window at which a conversation is compacted (``playground.compact_threshold``)."""

Source = Literal["usage", "estimate"]
WindowSource = Literal["catalog", "default", "learned"]


def text_tokens(text: str) -> int:
    return (len(text.encode("utf-8")) + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN


def message_tokens(message: dict[str, Any]) -> int:
    """One Chat Completions message: its text, images, and tool calls."""
    total = MESSAGE_TOKENS
    content = message.get("content")
    if isinstance(content, str):
        total += text_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                total += text_tokens(str(part.get("text") or ""))
            elif part.get("type") in ("image_url", "image", "input_image"):
                total += IMAGE_TOKENS
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += text_tokens(str(function.get("name") or "")) + text_tokens(str(function.get("arguments") or ""))
    return total


def estimate(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """What a request of these messages (and tool definitions) is estimated to take."""
    total = sum(message_tokens(m) for m in messages)
    if tools:
        total += text_tokens(json.dumps(tools, ensure_ascii=False))
    return total


@dataclass(frozen=True)
class Budget:
    """A model's window, the room kept for its reply, and the point at which a conversation is compacted."""

    window: int
    headroom: int
    limit: int
    source: WindowSource

    def left(self, used: int) -> int:
        return max(0, self.window - used)


def budget(spec: ModelSpec | None, *, learned: int | None = None, threshold: float = DEFAULT_THRESHOLD) -> Budget:
    """When to compact: at ``threshold`` of the window, or sooner when what is left could not hold a reply.

    The headroom is the model's output ceiling, but no more than a tenth of
    the window (a reply seldom uses all of a 128K ceiling) and no less than
    4,096 tokens; the limit is whichever comes first of ``threshold × window``
    and ``window − headroom``.
    """
    window = spec.context_window if spec is not None and spec.context_window > 0 else FALLBACK_CONTEXT_WINDOW
    source: WindowSource = "catalog" if spec is not None and spec.context_window not in (0, FALLBACK_CONTEXT_WINDOW) else "default"
    if learned and learned < window:
        window, source = learned, "learned"
    output = spec.max_output if spec is not None and spec.max_output > 0 else FALLBACK_MAX_OUTPUT
    headroom = min(output, max(4096, window // 10), window // 2)
    return Budget(window=window, headroom=headroom, limit=max(1, min(int(window * threshold), window - headroom)), source=source)


class LearnedWindows:
    """Windows smaller than the catalogue's, as stations said in refusing requests too long for them.

    Kept for the process: a catalogue that overstates a model (or a model on
    the fallback window) is corrected once, by the first refusal, and every
    later check compacts in time.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, int] = {}

    def get(self, model: str) -> int | None:
        with self._lock:
            return self._windows.get(model)

    def learn(self, model: str, window: int) -> None:
        with self._lock:
            known = self._windows.get(model)
            if known is None or window < known:
                self._windows[model] = window
