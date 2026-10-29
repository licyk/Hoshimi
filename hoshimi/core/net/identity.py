"""Conversation metadata carried separately from provider request bodies."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InferenceIdentity:
    session: str = ""
    user_agent: str = ""
    project: str = ""
    forwarded: bool = False

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> InferenceIdentity:
        values = {key.lower(): value.strip() for key, value in headers.items()}
        session = next((values[name] for name in (
            "x-opencode-session", "x-opencode-session-id", "session-id", "x-session-id", "x-claude-code-session-id", "session_id",
        ) if values.get(name)), "")
        agent = values.get("user-agent", "")
        return cls(session, agent if agent.isascii() else "", values.get("x-opencode-project", ""), True)


_EMPTY_IDENTITY = InferenceIdentity()
identity: ContextVar[InferenceIdentity] = ContextVar("inference_identity", default=_EMPTY_IDENTITY)


def identified_chunks(chunks: Iterator[dict[str, Any]], value: InferenceIdentity) -> Iterator[dict[str, Any]]:
    """Bind each iterator operation, including cleanup, without leaking across yields."""
    try:
        while True:
            token = identity.set(value)
            try:
                chunk = next(chunks)
            except StopIteration:
                return
            finally:
                identity.reset(token)
            yield chunk
    finally:
        token = identity.set(value)
        try:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
        finally:
            identity.reset(token)
