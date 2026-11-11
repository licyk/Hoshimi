"""Qoder's agent-chat SSE: envelopes, business codes, and normalized chunks.

The stream is not OpenAI SSE. Every frame is an envelope
``{"headers": ..., "body": "<json string>", "statusCodeValue": 200}`` whose
``body`` is the OpenAI chunk — or a nested business error, or the ``[DONE]``
sentinel. Three behaviours are load-bearing:

* ``[DONE]`` ends the read loop, not just the line loop: the gateway keeps the
  HTTP body open after the sentinel, so waiting for EOF hangs the turn;
* ``statusCodeValue != 200`` carries the station's verdict. Code ``110`` is the
  per-node daily chat allowance (recoverable only by switching nodes) and
  ``10605`` is the scheduler's busy/queue answer, whose inner JSON says whether
  a bounded re-send is enough or the request is genuinely queued;
* a malformed ``data:`` line is noise when the envelope failed to parse and lost
  model output when the payload did — the second must be accounted for, or the
  turn dies later as an unexplained argument error.

Ported from ``pi-provider-qoder`` ``src/protocol/stream.ts`` (the response half).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from hoshimi.core.qoder.thinking import ThinkingTagParser, is_degenerate_dsml_turn, strip_dsml_residue, strip_thinking_tags

# The error taxonomy lives in the provider package, which imports this module in
# turn: importing it at module load would close that cycle. The names are bound
# where they are raised instead.
if TYPE_CHECKING:  # pragma: no cover - typing only
    from hoshimi.core.providers.base import RequestThrottled

logger = logging.getLogger(__name__)

MODEL_BUSY_CODE = "10605"
DAILY_CHAT_LIMIT_CODE = "110"
REASONING_TAIL_CHARS = 512


class DailyChatLimitRejection(Exception):
    """Code 110: this node's daily chat allowance is gone.

    Recoverable only by switching nodes, never by re-sending to the same one.
    ``saw_output`` tells the caller whether a retry would duplicate content.
    """

    def __init__(self, message: str, *, saw_output: bool = False) -> None:
        super().__init__(message)
        self.saw_output = saw_output


class ModelBusyRejection(Exception):
    """Code 10605 with ``serviceAvailable``: the scheduler's recovery answer."""

    def __init__(self, message: str, *, retry_after_ms: int = 0, queued: bool = False, saw_output: bool = False) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms
        self.queued = queued
        self.saw_output = saw_output


def _json_object(value: Any, depth: int = 0) -> dict[str, Any] | None:
    """Parse ``unknown | JSON string`` layers until an object is found (bounded)."""
    if depth > 4:
        return None
    if isinstance(value, str):
        try:
            return _json_object(json.loads(value), depth + 1)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def is_daily_chat_limit(body: Any) -> bool:
    root = _json_object(body)
    if root is None:
        return False
    code = root.get("code")
    return code == DAILY_CHAT_LIMIT_CODE or code == int(DAILY_CHAT_LIMIT_CODE)


def parse_model_busy(body: Any) -> dict[str, Any] | None:
    """The busy snapshot: ``ready`` (re-send) or ``queued`` (wait), else None.

    Envelope body shape is three nested JSON layers; only the documented 10605
    code with ``serviceAvailable: true`` is eligible. ``subCode`` is diagnostic.
    """
    outer = _json_object(body)
    if outer is None:
        return None
    # The documented shape nests three JSON layers (envelope body -> 10605 ->
    # queue). Some builds put the 10605 layer directly in the envelope body;
    # both are the same verdict.
    busy = outer if str(outer.get("code")) == MODEL_BUSY_CODE else _json_object(outer.get("message"))
    if busy is None or str(busy.get("code")) != MODEL_BUSY_CODE:
        return None
    queue = _json_object(busy.get("message"))
    if queue is None or queue.get("serviceAvailable") is not True:
        return None

    def seconds(value: Any) -> int:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            return 0
        return round(value * 1000)

    if queue.get("isQueued") is False:
        return {"kind": "ready", "retry_after_ms": seconds(queue.get("retryAfterSeconds"))}
    if queue.get("isQueued") is True:
        # `waitTime` is this request's slot; `retryAfterSeconds` is only a
        # minimum re-ask interval, and re-asking at it lands at the back again.
        wait = seconds(queue.get("waitTime")) or seconds(queue.get("retryAfterSeconds"))
        return {"kind": "queued", "retry_after_ms": wait, "queue_count": queue.get("queueCount"), "queue_type": queue.get("queueType")}
    return None


def queued_error(provider: str, snapshot: dict[str, Any]) -> RequestThrottled:
    """A queued rejection as a schedulable refusal, not an auth failure."""
    from hoshimi.core.providers.base import RequestThrottled

    wait_ms = int(snapshot.get("retry_after_ms") or 0)
    ahead = snapshot.get("queue_count")
    tier = snapshot.get("queue_type")
    detail = f"{tier}, " if isinstance(tier, str) and tier else ""
    if isinstance(ahead, (int, float)):
        detail += f"{int(ahead)} ahead, "
    return RequestThrottled(
        429,
        f"{provider}: model is busy, request queued (10605 CALL_COUNT_LIMIT, {detail}~{round(wait_ms / 1000)}s wait)",
        retryable=True,
        retry_after=wait_ms / 1000 if wait_ms else None,
    )


def _map_usage(raw: dict[str, Any]) -> dict[str, Any]:
    """OpenAI token counts plus Qoder's request-level billing fields.

    ``prompt_tokens`` includes cached tokens here (OpenAI semantics), so the
    counts are passed through as they are and the cache detail rides beside
    them. Credits are preserved at full precision: ``billable: false`` means the
    request cost nothing even when ``credits`` is positive.
    """
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    out: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(raw.get("total_tokens") or (prompt + completion)),
    }
    details = raw.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    cache: dict[str, Any] = {}
    if details.get("cached_tokens") is not None:
        cache["cached_tokens"] = details["cached_tokens"]
    if details.get("cache_write_tokens") is not None:
        cache["cache_write_tokens"] = details["cache_write_tokens"]
    if cache:
        out["prompt_tokens_details"] = cache
    completion_details = raw.get("completion_tokens_details")
    if isinstance(completion_details, dict) and completion_details.get("reasoning_tokens") is not None:
        out["completion_tokens_details"] = {"reasoning_tokens": completion_details["reasoning_tokens"]}
    for key in ("credits", "original_credits"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    if isinstance(raw.get("billable"), bool):
        out["billable"] = raw["billable"]
        out["charged_credits"] = 0 if raw["billable"] is False else out.get("credits", 0)
    return out


class _ToolSlot:
    __slots__ = ("arguments", "id", "name", "order")

    def __init__(self, order: int) -> None:
        self.id = ""
        self.name = ""
        self.arguments = ""
        self.order = order


def iter_agent_chunks(
    lines: Iterable[str],
    *,
    provider: str,
    thinking_enabled: bool = True,
    retry_after: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Consume one agent-chat response into normalized gateway chunks.

    Raises :class:`DailyChatLimitRejection` / :class:`ModelBusyRejection` for the
    two recoveries the provider owns, and the classified provider error for
    everything else.
    """
    from hoshimi.core.providers.base import ProviderError
    from hoshimi.core.providers.refusals import provider_refusal

    slots: dict[Any, _ToolSlot] = {}
    by_id: dict[str, _ToolSlot] = {}
    order = 0
    last_slot: _ToolSlot | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    saw_any = False
    saw_done = False
    saw_output = False
    dropped_payloads = 0
    reasoning_tail = ""
    text_seen = False
    pending: list[dict[str, Any]] = []

    def emit_text(text: str) -> None:
        nonlocal saw_any, text_seen
        if not text:
            return
        saw_any = True
        if text.strip():
            text_seen = True
        pending.append({"type": "text_delta", "text": text})

    def emit_thinking(text: str) -> None:
        nonlocal saw_any
        if not text:
            return
        saw_any = True
        pending.append({"type": "reasoning_delta", "text": text})

    parser = ThinkingTagParser(emit_text, emit_thinking) if thinking_enabled else None

    for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            saw_done = True
            break
        try:
            envelope = json.loads(data)
        except ValueError:
            logger.debug("qoder: skipping malformed SSE envelope: %s", data[:200])
            continue
        if not isinstance(envelope, dict):
            continue
        status = envelope.get("statusCodeValue")
        body = envelope.get("body")
        if isinstance(status, int) and status != 200:
            message = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            busy = parse_model_busy(body)
            if busy is not None and busy["kind"] == "ready":
                raise ModelBusyRejection(f"Upstream status {status}: {message}", retry_after_ms=int(busy.get("retry_after_ms") or 0), saw_output=saw_output)
            if busy is not None and busy["kind"] == "queued":
                if saw_output:
                    raise ProviderError(429, f"{provider}: model rejected the request after partial output (10605 RESOURCE_LIMIT)")
                raise queued_error(provider, busy)
            if is_daily_chat_limit(body):
                raise DailyChatLimitRejection(f"Upstream status {status}: {message}", saw_output=saw_output)
            refusal = provider_refusal(provider, body, status=status, retry_after=retry_after)
            if refusal is not None:
                raise refusal
            raise ProviderError(status, f"{provider}: {message}", retryable=status >= 500)
        if body == "[DONE]":
            saw_done = True
            break
        if not body:
            continue
        try:
            inner = json.loads(body) if isinstance(body, str) else body
        except ValueError:
            # Model output we will never see: accounted for, so a shortened
            # tool-call argument stream is reported as the transport failure it is.
            dropped_payloads += 1
            logger.debug("qoder: skipping malformed SSE payload: %s", str(body)[:200])
            continue
        if not isinstance(inner, dict):
            continue
        if isinstance(inner.get("usage"), dict):
            usage = _map_usage(inner["usage"])
        choices = inner.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            reasoning = delta.get("reasoning_content")
            content = delta.get("content")
            calls = delta.get("tool_calls")
            if reasoning or content or (isinstance(calls, list) and calls):
                saw_output = True
            if isinstance(reasoning, str) and reasoning:
                reasoning_tail = (reasoning_tail + reasoning)[-REASONING_TAIL_CHARS:]
                cleaned = strip_dsml_residue(strip_thinking_tags(reasoning))
                if cleaned:
                    saw_any = True
                    yield {"type": "reasoning_delta", "text": cleaned}
            if isinstance(content, str) and content:
                if parser is not None:
                    parser.process_chunk(content)
                else:
                    emit_text(content)
            if isinstance(calls, list):
                for entry in calls:
                    if not isinstance(entry, dict):
                        continue
                    call_id = entry.get("id") if isinstance(entry.get("id"), str) else ""
                    function = entry.get("function") if isinstance(entry.get("function"), dict) else {}
                    name = function.get("name") if isinstance(function.get("name"), str) else ""
                    fragment = function.get("arguments")
                    fragment = fragment if isinstance(fragment, str) else ""
                    if not call_id and not name and not fragment:
                        continue
                    index = entry.get("index")
                    slot: _ToolSlot | None = None
                    if isinstance(index, int):
                        slot = slots.get(index)
                        if slot is None:
                            slot = _ToolSlot(order)
                            order += 1
                            slots[index] = slot
                    elif call_id:
                        slot = by_id.get(call_id)
                        if slot is None:
                            slot = _ToolSlot(order)
                            order += 1
                            slots[call_id] = slot
                    if slot is None:
                        slot = last_slot
                    if slot is None:
                        slot = _ToolSlot(order)
                        order += 1
                        slots[f"anon-{order}"] = slot
                    if call_id:
                        slot.id = call_id
                        by_id[call_id] = slot
                    if name:
                        slot.name = name
                    if fragment:
                        slot.arguments += fragment
                        saw_any = True
                    last_slot = slot
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            finish_reason = reason
        # Text the tag parser routed is released in order, as it arrives.
        while pending:
            yield pending.pop(0)
        if saw_done:
            break

    if parser is not None:
        parser.finalize()
    while pending:
        yield pending.pop(0)

    tool_calls = sorted(slots.values(), key=lambda slot: slot.order)
    for position, slot in enumerate(tool_calls):
        if not slot.name and not slot.arguments:
            continue
        arguments = slot.arguments or "{}"
        try:
            json.loads(arguments)
        except ValueError:
            if dropped_payloads:
                # Nothing executable was published: a lost payload is a
                # transport failure, and the turn should be replayed.
                raise ProviderError(502, f"{provider}: server error, tool-call argument stream was cut off (unparseable upstream payload)", retryable=True) from None
            yield {"type": "error", "error": f"{provider}: invalid or incomplete tool-call JSON"}
            return
        yield {"type": "tool_call_start", "id": slot.id or f"call_{position}", "name": slot.name, "arguments": arguments, "index": position}

    if usage is not None:
        yield {"type": "usage", "usage": usage}

    if is_degenerate_dsml_turn(has_text=text_seen, has_tool_call=bool(tool_calls), raw_reasoning_tail=reasoning_tail):
        yield {"type": "error", "error": f"{provider}: server error, degenerate model output (unparseable DSML tool-call markup, no tool call executed)"}
        return

    if not saw_any and not tool_calls:
        if saw_done or finish_reason:
            yield {"type": "finish", "reason": "stop"}
            return
        yield {"type": "error", "error": f"{provider}: upstream returned no content"}
        return

    if tool_calls and finish_reason in (None, "stop", "tool_calls"):
        yield {"type": "finish", "reason": "tool_calls"}
    elif finish_reason == "length":
        yield {"type": "finish", "reason": "length"}
    elif finish_reason == "tool_calls":
        yield {"type": "finish", "reason": "tool_calls"}
    else:
        yield {"type": "finish", "reason": "stop"}