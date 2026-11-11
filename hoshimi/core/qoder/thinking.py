"""Thinking-tag routing and DSML residue, the two artefacts Qoder's stream leaks.

The gateway sometimes routes a literal ``<thinking>`` opener into the
``reasoning_content`` channel with its closer landing in ``content``, and
sometimes leaks the model's internal DSML tool-call markup into whichever
channel it was halfway through. Both have to be cleaned before the text reaches
a caller: the reasoning channel is replayed into the next turn's prompt, so
residue left in it poisons every later request.

The content-channel parser buffers across deltas, so a tag split between two
chunks is never emitted half as visible text.

Ported from ``pi-provider-qoder`` ``src/protocol/thinking.ts``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

THINKING_TAG_VARIANTS: tuple[tuple[str, str], ...] = (
    ("<thinking>", "</thinking>"),
    ("<think>", "</think>"),
    ("<reasoning>", "</reasoning>"),
    ("<thought>", "</thought>"),
)
ALL_THINKING_TAGS: tuple[str, ...] = tuple(tag for pair in THINKING_TAG_VARIANTS for tag in pair)

# Leaked DSML: full-width or ASCII pipes around the tag name, and the bare
# invoke/parameter/function_call closers, alone or repeated dozens of times.
DSML_TAG = re.compile(r"</?[\uFF5C|]{0,2}\s*DSML\s*[\uFF5C|]{0,2}[^>]*>|</?(?:invoke|invocation|parameter|function_call|function_calls)\b[^>]*>")
DEGENERATE_DSML_TAIL = re.compile(
    r"(?:(?:</?(?:invoke|invocation|parameter|function_call|function_calls)\b[^>]*>)\s*|(?:</?[\uFF5C|]{0,2}\s*DSML\s*[\uFF5C|]{0,2}[^>]*>)\s*){2,}$"
)


def strip_thinking_tags(text: str) -> str:
    """Remove every thinking/reasoning tag variant from one chunk (best effort)."""
    out = text
    for opener, closer in THINKING_TAG_VARIANTS:
        if opener and opener in out:
            out = out.replace(opener, "")
        if closer and closer in out:
            out = out.replace(closer, "")
    return out


def strip_dsml_residue(text: str) -> str:
    """Strip leaked DSML markup and collapse the blank lines it leaves."""
    return re.sub(r"\n{3,}", "\n\n", DSML_TAG.sub("", text))


def is_degenerate_dsml_turn(*, has_text: bool, has_tool_call: bool, raw_reasoning_tail: str) -> bool:
    """A turn where the model tried to call a tool and the gateway dropped it.

    Nothing usable came back — no text, no executed call — and the reasoning
    channel ends in leaked markup. Reported as an error so the turn is retried
    instead of reading as a finished task.
    """
    if has_tool_call or has_text:
        return False
    return bool(DEGENERATE_DSML_TAIL.search(raw_reasoning_tail))


def _trailing_prefix_length(text: str, tag: str) -> int:
    """How much of ``text``'s tail could be the start of ``tag``."""
    for length in range(min(len(text), len(tag) - 1), 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0


def _max_trailing_prefix_length(text: str, tags: tuple[str, ...]) -> int:
    return max((_trailing_prefix_length(text, tag) for tag in tags), default=0)


class ThinkingTagParser:
    """Split a content stream into visible text and in-band thinking.

    ``emit_text`` and ``emit_thinking`` receive the routed fragments; the parser
    owns the buffer and the state, so a caller only has to feed chunks and
    finalize.
    """

    def __init__(self, emit_text: Callable[[str], None], emit_thinking: Callable[[str], None]) -> None:
        self._emit_text = emit_text
        self._emit_thinking = emit_thinking
        self._buffer = ""
        self._in_thinking = False
        self._extracted = False
        self._active_end_tag = THINKING_TAG_VARIANTS[0][1]

    def process_chunk(self, chunk: str) -> None:
        self._buffer += chunk
        while self._buffer:
            previous = len(self._buffer)
            if not self._in_thinking and not self._extracted:
                self._before_thinking()
                if not self._buffer:
                    break
            if self._in_thinking:
                self._inside_thinking()
                if not self._buffer:
                    break
            if self._extracted:
                self._after_thinking()
                break
            if len(self._buffer) >= previous:
                break

    def finalize(self) -> None:
        if not self._buffer:
            return
        if self._in_thinking:
            self._emit_thinking(self._buffer)
        else:
            self._emit_text(self._buffer)
        self._buffer = ""

    # -- the three states -------------------------------------------------- #
    def _before_thinking(self) -> None:
        best_open: tuple[int, str, str] | None = None
        best_close: tuple[int, str] | None = None
        for opener, closer in THINKING_TAG_VARIANTS:
            position = self._buffer.find(opener)
            if position != -1 and (best_open is None or position < best_open[0]):
                best_open = (position, opener, closer)
            position = self._buffer.find(closer)
            if position != -1 and (best_close is None or position < best_close[0]):
                best_close = (position, closer)

        if best_open is not None and (best_close is None or best_open[0] < best_close[0]):
            position, opener, closer = best_open
            if position > 0:
                self._emit_text(self._buffer[:position])
            self._buffer = self._buffer[position + len(opener):]
            self._active_end_tag = closer
            self._in_thinking = True
            return

        if best_close is not None:
            # An orphan closer: its opener came through `reasoning_content`, so
            # there is no block to close here. Drop it and the separator the
            # model emits right after it.
            position, closer = best_close
            if position > 0:
                self._emit_text(self._buffer[:position])
            self._buffer = self._buffer[position + len(closer):]
            self._buffer = self._buffer.removeprefix("\n\n").removeprefix("\n")
            return

        hold = _max_trailing_prefix_length(self._buffer, ALL_THINKING_TAGS)
        safe = len(self._buffer) - hold
        if safe > 0:
            self._emit_text(self._buffer[:safe])
            self._buffer = self._buffer[safe:]

    def _inside_thinking(self) -> None:
        position = self._buffer.find(self._active_end_tag)
        if position != -1:
            if position > 0:
                self._emit_thinking(self._buffer[:position])
            self._buffer = self._buffer[position + len(self._active_end_tag):]
            self._in_thinking = False
            self._extracted = True
            self._buffer = self._buffer.removeprefix("\n\n")
            return
        hold = _trailing_prefix_length(self._buffer, self._active_end_tag)
        safe = len(self._buffer) - hold
        if safe > 0:
            self._emit_thinking(self._buffer[:safe])
            self._buffer = self._buffer[safe:]

    def _after_thinking(self) -> None:
        self._emit_text(self._buffer)
        self._buffer = ""