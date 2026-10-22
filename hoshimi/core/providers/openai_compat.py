"""Shared implementation for OpenAI-compatible upstream stations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Iterator
from typing import Any, ClassVar

import httpx

from hoshimi.core.failure import body_says_model_gone, is_context_overflow, is_model_gone, is_model_saturation, is_policy_block, is_recoverable, parse_retry_after
from hoshimi.core.net.responses import reject_interception
from hoshimi.core.providers.base import Account, AuthFailed, ContextOverflow, ModelGone, ModelSaturated, PolicyBlocked, Provider, ProviderError, QuotaExceeded
from hoshimi.core.quota import observe_response
from hoshimi.core.sse import iter_sse


def _looks_like_quota(status: int | None, text: str) -> bool:
    # 402 is how a free tier says "allowance gone" — the status is the message,
    # there is nothing useful to read in the body.
    if status in (402, 429):
        return True
    lowered = text.lower()
    markers = (
        "quota",
        "rate limit",
        "rate_limit",
        "insufficient",
        "余额不足",
        "积分不足",
        "额度不足",
        "频率限制",
        "频率超出",
        "使用量已超出",
        "limit exceeded",
        "too many requests",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_auth(text: str) -> bool:
    """A credential complaint in the body, for statuses that do not say so."""
    lowered = text.lower()
    return "invalid token" in lowered or "unauthorized" in lowered or "登录" in text


def _classify(provider: str, status: int | None, text: str, *, retry_after: float | None = None) -> None:
    """Raise for one refusal, in this order and no other.

    The order is the argument, and the first two entries exist only because
    of it. A saturated model arrives as a plain HTTP 429 and a policy block
    as a plain HTTP 403; read by number alone they are indistinguishable
    from a rate limit and from a bad credential. That is not a wording
    problem — the two pairs take opposite actions — and it is exactly how
    four unrelated accounts came to be locked out for a full hour over one
    busy model.

    ``status`` is None for an error frame riding a 200 stream: there the
    failure is the frame, and there is no status to be fooled by. Only the
    two named entries accept that; the rest still need a number.

    ``retry_after`` is the station's own answer to "when", read off the
    standard header. It rides along rather than deciding anything here: the
    classification is about *whose* problem this is, and a wait time does
    not change that.
    """
    if is_model_saturation(status, text):
        raise ModelSaturated(status or 429, f"{provider}: {text}", retryable=True, retry_after=retry_after)
    if is_policy_block(status, text):
        raise PolicyBlocked(status or 403, f"{provider}: {text}", retryable=True, retry_after=retry_after)
    if status == 401 or (status == 403 and _looks_like_auth(text)):
        raise AuthFailed(status, f"{provider}: {text}", retryable=True, retry_after=retry_after)
    if status == 403:
        error = ProviderError(403, f"{provider}: permission denied", retryable=False)
        error.code = "permission_denied"
        error.should_retry = False
        raise error
    if _looks_like_quota(status, text):
        raise QuotaExceeded(status or 200, f"{provider}: {text}", retryable=True, retry_after=retry_after)
    if _looks_like_auth(text):
        raise AuthFailed(status or 401, f"{provider}: {text}", retryable=True, retry_after=retry_after)
    raise ProviderError(status or 502, f"{provider}: {text}", retryable=is_recoverable(status or 502), retry_after=retry_after)


def raise_for_status(response: httpx.Response, *, provider: str) -> None:
    """Raise for an upstream answer, classified by the status table.

    The status is read first and prose cannot contradict it: 404 is "there
    is no such thing here" and 401 is "your credentials", whatever the body
    happens to mention. The body is consulted for the answers whose *number*
    does not say what they mean — and for the two that a number actively
    hides (see :func:`_classify`).
    """
    reject_interception(response, provider=provider)
    status = response.status_code
    if status < 400:
        return
    text = response.read().decode("utf-8", "replace")
    # Before the id and quota readings: "maximum context length" names the model and "limit exceeded" reads as quota.
    if is_context_overflow(status, text):
        raise ContextOverflow(status, f"{provider}: {text}")
    if is_model_gone(status, text):
        raise ModelGone(status, f"{provider}: {text}")
    _classify(provider, status, text, retry_after=parse_retry_after(response.headers.get("retry-after")))


def _classify_in_band(provider: str, message: str) -> None:
    """Raise only for the in-band errors that mean "somewhere else might help".

    Everything else stays a yielded error chunk: a station that answers 200
    with an error frame is reporting its own verdict, and turning it into an
    exception would hide the words behind a status code nobody chose.

    The id verdict is the one exception worth taking. dsh reads it here too
    (`trackStream`), because a station that has dropped an id is at least as
    likely to say so inside a `200` stream as on a status line — and if it is
    only ever yielded, the dead-model store never hears about it and the
    caller is handed the same refusal every turn for the rest of the session.
    """
    if is_context_overflow(None, message):
        raise ContextOverflow(400, f"{provider}: {message}")
    if body_says_model_gone(message):
        raise ModelGone(404, f"{provider}: {message}")
    if is_model_saturation(None, message):
        raise ModelSaturated(429, f"{provider}: {message}", retryable=True)
    if is_policy_block(None, message):
        raise PolicyBlocked(403, f"{provider}: {message}", retryable=True)
    if _looks_like_quota(None, message):
        raise QuotaExceeded(200, f"{provider}: {message}", retryable=True)


def consume_openai_sse(lines: Iterable[str], *, provider: str) -> Iterator[dict[str, Any]]:
    """Consume an OpenAI-style SSE stream into normalized chunks."""
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    saw_any = False
    saw_done = False

    for _event_name, data in iter_sse(lines):
        if data == "[DONE]":
            saw_done = True
            break
        if not data:
            continue
        try:
            frame = json.loads(data)
        except ValueError:
            continue
        if not isinstance(frame, dict):
            continue

        if frame.get("error") is not None:
            error = frame["error"]
            message = str(error)
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = error["message"]
            _classify_in_band(provider, message)
            yield {"type": "error", "error": message}
            return

        # Non-SSE error envelopes some gateways return with 200.
        if "code" in frame and "message" in frame and "choices" not in frame:
            message = str(frame.get("message"))
            _classify_in_band(provider, message)
            yield {"type": "error", "error": message}
            return

        # In-band error frames (e.g. CodeArts) carried on a 200 stream.
        error_code = frame.get("error_code")
        if isinstance(error_code, str) and error_code and "choices" not in frame:
            message = str(frame.get("error_msg") or error_code)
            _classify_in_band(provider, f"{error_code}: {message}")
            yield {"type": "error", "error": f"{error_code}: {message}"}
            return

        if frame.get("usage") is not None and isinstance(frame["usage"], dict):
            usage = _map_usage(frame["usage"])

        choices = frame.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue

        delta = choice.get("delta") or {}
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text" and isinstance(part.get("text"), str):
                        saw_any = True
                        yield {"type": "text_delta", "text": part["text"]}
                    if part.get("type") == "thinking" and isinstance(part.get("thinking"), list):
                        for thought in part["thinking"]:
                            if isinstance(thought, dict) and isinstance(thought.get("text"), str):
                                saw_any = True
                                yield {"type": "reasoning_delta", "text": thought["text"]}
            if isinstance(delta.get("reasoning_details"), list):
                yield {"type": "reasoning_details", "details": delta["reasoning_details"]}
            if isinstance(content, str) and content:
                saw_any = True
                yield {"type": "text_delta", "text": content}
            reasoning = delta.get("reasoning_content")
            if not isinstance(reasoning, str):
                reasoning = delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                saw_any = True
                yield {"type": "reasoning_delta", "text": reasoning}
            for entry in delta.get("tool_calls") or []:
                if not isinstance(entry, dict):
                    continue
                index = int(entry.get("index") or 0)
                slot = tool_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
                if entry.get("id"):
                    slot["id"] = entry["id"]
                if isinstance(entry.get("extra_content"), dict):
                    slot["extra_content"] = entry["extra_content"]
                fn = entry.get("function") or {}
                if isinstance(fn, dict):
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    arguments = fn.get("arguments")
                    if isinstance(arguments, str) and arguments:
                        if not saw_any or slot.get("emitted_start") is None:
                            pass
                        slot["arguments"] += arguments
                        saw_any = True

        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            finish_reason = reason

    # Announce tool calls after accumulation (single start event per call).
    for index in sorted(tool_calls):
        slot = tool_calls[index]
        if not slot["name"] and not slot["arguments"]:
            continue
        yield {
            "type": "tool_call_start",
            "id": slot["id"] or f"call_{index}",
            "name": slot["name"] or "",
            "arguments": slot["arguments"],
            "index": index,
        }

        if isinstance(slot.get("extra_content"), dict):
            yield {"type": "reasoning_details", "details": [{"type": "hoshimi_tool_extra", "call_id": slot["id"] or f"call_{index}", "extra_content": slot["extra_content"]}]}

    if usage is not None:
        yield {"type": "usage", "usage": usage}

    if not saw_any and not tool_calls:
        if saw_done or finish_reason:
            yield {"type": "finish", "reason": "stop"}
            return
        yield {"type": "error", "error": "Upstream returned no content."}
        return

    if tool_calls and (finish_reason in (None, "stop")):
        yield {"type": "finish", "reason": "tool_calls"}
    elif finish_reason == "length":
        yield {"type": "finish", "reason": "length"}
    elif finish_reason == "tool_calls":
        yield {"type": "finish", "reason": "tool_calls"}
    else:
        yield {"type": "finish", "reason": "stop"}


def _map_usage(usage: dict[str, Any]) -> dict[str, Any]:
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    out: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    cache = _cache_details(usage)
    if cache:
        out["prompt_tokens_details"] = cache
    completion_details = usage.get("completion_tokens_details")
    if isinstance(completion_details, dict) and completion_details.get("reasoning_tokens") is not None:
        out["completion_tokens_details"] = {"reasoning_tokens": completion_details["reasoning_tokens"]}
    return out


def _cache_details(usage: dict[str, Any]) -> dict[str, Any]:
    """Prompt-cache reads and writes, across the dialects compatible stations speak.

    OpenAI itself reports only ``prompt_tokens_details.cached_tokens``. The
    stations in front of cache-writing models add the write side: OpenRouter
    as ``prompt_tokens_details.cache_write_tokens``, others as
    ``cache_creation_tokens`` there or Anthropic's top-level
    ``cache_creation_input_tokens``. DeepSeek reports hits as a top-level
    ``prompt_cache_hit_tokens``. All of them land in the one vocabulary the
    rest of the gateway reads; a station that names none of them gets no
    details at all, exactly as before.
    """
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    out: dict[str, Any] = {}
    for value in (details.get("cached_tokens"), usage.get("prompt_cache_hit_tokens"), usage.get("cache_read_input_tokens")):
        if value is not None:
            out["cached_tokens"] = value
            break
    for value in (details.get("cache_write_tokens"), details.get("cache_creation_tokens"), usage.get("cache_creation_input_tokens")):
        if value is not None:
            out["cache_write_tokens"] = value
            break
    return out


def model_rows(data: Any) -> list[dict[str, Any]]:
    """The catalogue's entries, whatever shape the station wrapped them in.

    Split from the id extraction because a filter needs the row: whether an
    entry is a chat target is answered by its fields, and ``id`` alone cannot
    say it. One shape for both means the plain id path and the filtered path
    can never disagree about which entries exist.
    """
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return [item for item in data["data"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def model_ids(rows: list[dict[str, Any]]) -> list[str]:
    """The ids in ``rows``, dropping any entry that does not carry one."""
    return [row["id"] for row in rows if isinstance(row.get("id"), str)]


class OpenAiCompatProvider(Provider):
    """Base for stations that speak OpenAI chat completions on the wire."""

    base_url: str = ""
    chat_path: str = "/v1/chat/completions"
    models_path: str | None = None
    user_agent: str | None = None
    fallback_model_list: ClassVar[list[str]] = []

    def fallback_models(self) -> list[str]:
        return list(self.fallback_model_list)

    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.chat_path}"

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {account.get('access_token', '')}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        return headers

    def build_body(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        out = dict(body)
        out["model"] = model
        out["stream"] = True
        out.pop("stream_options", None)
        return out

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        payload = self.build_body(body, model)
        headers = self.build_headers(account, model)
        with self.client.stream(
            "POST",
            self.chat_url(),
            json=payload,
            headers=headers,
        ) as response:
            # Once per turn, before the verdict: the rate-limit headers ride the
            # answer that carries the tokens, and they are worth nothing once a
            # failure is being unwound.
            observe_response(self.id, response)
            if response.status_code >= 400:
                raise_for_status(response, provider=self.id)
            yield from consume_openai_sse(response.iter_lines(), provider=self.id)

    # -- model discovery -------------------------------------------------- #
    def fetch_models(self) -> list[str]:
        if not self.models_path or not self.accounts:
            return []
        url = f"{self.base_url.rstrip('/')}{self.models_path}"
        account = self.accounts[0]
        headers = self.build_headers(account, "")
        headers["Accept"] = "application/json"
        try:
            response = self.client.get(url, headers=headers, timeout=30.0)
        except httpx.HTTPError:
            self.catalog_failed = True
            return []
        raise_for_status(response, provider=self.id)
        result = self.parse_models_response(response.json())
        self.catalog_authoritative = True
        return result

    def parse_models_response(self, data: Any) -> list[str]:
        if not isinstance(data, list) and not (isinstance(data, dict) and isinstance(data.get("data"), list)):
            raise TypeError("Invalid model catalogue response")
        return model_ids(model_rows(data))


def _chain(first: str, rest: Iterable[str]) -> Iterator[str]:
    yield first
    yield from rest


def read_body(response: httpx.Response, *, provider: str) -> Iterator[dict[str, Any]]:
    """Consume one response by its shape, not by what its headers claim.

    The answer either streams as SSE frames or arrives as one JSON document —
    a relay that answers a stream request with the whole answer must not be
    read as an empty stream.
    """
    lines = response.iter_lines()
    first = next((line for line in lines if line.strip()), None)
    if first is None:
        raise ProviderError(502, f"{provider}: model stream returned no body")
    if first.lstrip().startswith("data:"):
        yield from consume_openai_sse(_chain(first, lines), provider=provider)
        return
    text = first + "".join(lines)
    try:
        json.loads(text)
    except ValueError:
        raise ProviderError(502, f"{provider}: unexpected non-stream response: {text[:200]}") from None
    yield from consume_openai_sse([f"data: {text}", "", "data: [DONE]"], provider=provider)


def new_uuid() -> str:
    return str(uuid.uuid4())
