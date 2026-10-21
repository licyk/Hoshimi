"""Shared model selection and accounting for the public inference protocols."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from hoshimi.core.context import Services
from hoshimi.core.effort import requested_level, resolve_level
from hoshimi.core.errors import ValidationError


def prepare_request(services: Services, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        model = services.settings.default_model or services.registry.default_model or ""
    if not model:
        raise ValidationError("Missing 'model' field.")
    body = dict(body)
    body["model"] = model
    body.setdefault("prompt_cache_key", f"hoshimi-{int(time.time())}")
    return model, body


def tracked_chunks(
    services: Services,
    model: str,
    body: dict[str, Any],
    *,
    origin: str,
    stream: bool | None = None,
) -> Iterator[dict[str, Any]]:
    """The upstream stream with this call's usage, latency and outcome recorded.

    The turn starts here so a routing failure still counts: a request the
    gateway refused is a request it answered. Effort is the rung the wire
    carries: a caller who named none sends none, and the sample must not
    claim a level the body never had. The account is the one that answered,
    reported by the registry as the first chunk arrives.
    """
    level = requested_level(body)
    effort = (resolve_level(level, services.registry.spec(model)) or "") if level else ""
    streaming = bool(body.get("stream", False)) if stream is None else stream
    turn = services.stats.turn(model, effort=effort, origin=origin, stream=streaming, provider=services.registry.provider_hint(model))
    try:
        upstream = services.registry.stream(model, body, on_route=turn.routed)
    except BaseException as exc:
        turn.fail(exc)
        raise
    return turn.wrap(upstream)
