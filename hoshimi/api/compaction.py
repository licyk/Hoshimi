"""Compacting a Playground conversation: its earlier part, summarised, in place of itself.

A conversation that nears its model's context window (see
:mod:`hoshimi.core.agent.context_window`) is compacted: a model writes a
handoff summary of everything before a point, and from then on the model is
sent that summary (with the user's latest messages from that part, verbatim)
instead. What it is sent after a compaction, in order:

- the system prompt;
- the summary, as a user message (:data:`SUMMARY_PREFIX`), and an
  acknowledgement as the assistant's, so the messages still alternate;
- for a compaction inside a reply: the question it answers, the round of tool
  calls just made (long outputs cut, see :func:`elide`), and every round
  since;
- then everything on the path after it.

A compaction is kept as a *record*, a step of type ``compaction``: on a
``checkpoint`` message (the user compacted between turns), or among a
reply's steps at the round it came before (it was compacted as the reply
began, or between its rounds of tool calls). Only the last finished one on
a path counts; an earlier summary is merged into the next by the model that
writes it. This module builds the texts; the Playground runner asks the model.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from hoshimi.core.agent.context_window import message_tokens, text_tokens

Trigger = Literal["manual", "auto", "overflow"]
Phase = Literal["manual", "pre-turn", "mid-turn"]

COMPACT_PROMPT = """You are compacting a conversation between a user and an AI assistant that can use tools, so that the assistant can go on \
working with less context. Write a handoff summary for the assistant that will continue: it will see your summary in place of the \
conversation, and nothing else of it.

Use these sections, leaving out any that would be empty:

## Goal
What the user wants, in their terms, and the constraints and preferences they stated.

## Decisions
Choices made and why ("X because Y"), including approaches tried and given up.

## Progress
What is done, under way and still to do, each marked [DONE], [IN PROGRESS] or [PENDING].

## Files and commands
Files read, created or changed, and commands run, with what matters about each.

## Key facts
Details needed to continue: names, values, results, error messages.

## Next step
What the assistant was about to do.

Rules:
- Keep file paths, identifiers, numbers, commands and error messages exactly as written.
- Quote the user's own words where their wording matters.
- If the conversation starts with an earlier summary, merge it in: keep what still holds, drop what was superseded.
- Leave out raw tool output; say what it showed.
- Write in the language the user writes in.
- Reply with the summary only."""

SUMMARY_PREFIX = (
    "This conversation was compacted to fit the model's context window. What follows is a summary of everything before this "
    "point, written as a handoff for you to continue from."
)
RECENT_HEADER = "The user's latest messages from that part, verbatim:"
ACK = "Understood. I have the summary and will continue from it."

TRANSCRIPT_OUTPUT_LIMIT = 2_000
"""The most of one tool output the summarising model reads."""
TRANSCRIPT_ARGUMENTS_LIMIT = 1_000
TAIL_OUTPUT_LIMIT = 8_000
"""A tool output kept after a compaction (the round just made) longer than this is cut to its first :data:`TAIL_OUTPUT_HEAD` characters."""
TAIL_OUTPUT_HEAD = 2_000
RECENT_TOKENS = 20_000
"""The most of the user's own words kept verbatim; never more than :data:`RECENT_SHARE` of the window."""
RECENT_SHARE = 0.15


def new_record(trigger: Trigger, phase: Phase, round_: int) -> dict[str, Any]:
    return {
        "type": "compaction", "id": f"cmp_{uuid.uuid4().hex[:24]}", "status": "running", "trigger": trigger, "phase": phase, "round": round_,
        "summary": "", "recent": [], "tokens_before": 0, "tokens_after": 0, "model": "", "ms": 0, "error": "", "at": time.time(),
    }


def last_done(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last finished compaction among a message's steps."""
    return next((s for s in reversed(steps) if s.get("type") == "compaction" and s.get("status") == "done" and s.get("summary")), None)


def prefix(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The summary as the model reads it: a user message with the summary (and the user's latest words), and its acknowledgement."""
    text = f"{SUMMARY_PREFIX}\n\n{record['summary']}"
    recent = [r for r in record.get("recent") or [] if isinstance(r, str) and r.strip()]
    if recent:
        text += f"\n\n{RECENT_HEADER}\n\n" + "\n\n---\n\n".join(recent)
    return [{"role": "user", "content": text}, {"role": "assistant", "content": ACK}]


def is_summary(message: dict[str, Any]) -> bool:
    return message.get("role") == "user" and isinstance(message.get("content"), str) and message["content"].startswith(SUMMARY_PREFIX)


def text_of(message: dict[str, Any]) -> str:
    """A message's words; an image is ``[image]``."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") if p.get("type") == "text" else "[image]" for p in content if isinstance(p, dict))
    return ""


def elide(messages: list[dict[str, Any]], *, limit: int = TAIL_OUTPUT_LIMIT, head: int = TAIL_OUTPUT_HEAD) -> list[dict[str, Any]]:
    """Copies of ``messages`` whose long tool outputs are cut to their start, saying how much was left out."""
    out = []
    for m in messages:
        content = m.get("content")
        if m.get("role") == "tool" and isinstance(content, str) and len(content) > limit:
            m = {**m, "content": f"{content[:head]}\n[… {len(content) - head} more characters left out to fit the context window; run the tool again if you need them]"}
        out.append(m)
    return out


def transcript_entries(messages: list[dict[str, Any]]) -> list[str]:
    """The messages as the summarising model reads them, one entry each: who said it, and what (tool outputs cut)."""
    entries = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            text = text_of(m)
            if len(text) > TRANSCRIPT_OUTPUT_LIMIT:
                text = f"{text[:TRANSCRIPT_OUTPUT_LIMIT]}… ({len(text)} characters in all)"
            entries.append(f"[tool result]: {text}")
            continue
        text = text_of(m)
        parts = [f"[{role}]: {text}"] if text.strip() else []
        for call in m.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = str(function.get("arguments") or "")
            if len(arguments) > TRANSCRIPT_ARGUMENTS_LIMIT:
                arguments = arguments[:TRANSCRIPT_ARGUMENTS_LIMIT] + "…"
            parts.append(f"[{role} called {function.get('name')}]: {arguments}")
        if parts:
            entries.append("\n".join(parts))
    return entries


def fit(entries: list[str], tokens: int) -> str:
    """The transcript, its oldest entries left out until it takes at most ``tokens`` (an earlier summary first in it is kept)."""
    keep_first = bool(entries) and entries[0].startswith(f"[user]: {SUMMARY_PREFIX}")
    head, rest = (entries[:1], entries[1:]) if keep_first else ([], entries)
    used = sum(text_tokens(e) + 2 for e in head)
    kept: list[str] = []
    for entry in reversed(rest):
        size = text_tokens(entry) + 2
        if kept and used + size > tokens:
            break
        kept.append(entry)
        used += size
    kept.reverse()
    dropped = len(rest) - len(kept)
    note = [f"[… {dropped} earlier messages left out]"] if dropped else []
    text = "\n\n".join(head + note + kept)
    # One entry may be too long alone: its middle is what is left out then.
    data, limit = text.encode("utf-8"), tokens * 4
    if len(data) <= limit:
        return text
    return data[: limit // 2].decode("utf-8", "ignore") + "\n[… cut]\n" + data[-(limit // 2):].decode("utf-8", "ignore")


def recent_user_texts(messages: list[dict[str, Any]], window: int) -> list[str]:
    """The user's own latest messages (not summaries), newest first until :data:`RECENT_TOKENS`, back in their order.

    The one at the edge of the budget is kept in part, its end.
    """
    room = min(RECENT_TOKENS, int(window * RECENT_SHARE))
    out: list[str] = []
    for m in reversed(messages):
        if m.get("role") != "user" or is_summary(m):
            continue
        text = text_of(m).strip()
        if not text:
            continue
        size = message_tokens({"content": text})
        if size > room:
            if room > 200:
                out.append("…" + text.encode("utf-8")[-room * 4:].decode("utf-8", "ignore"))
            break
        out.append(text)
        room -= size
    return list(reversed(out))
