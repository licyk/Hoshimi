"""How full a conversation is, when it is compacted, and how a refusal for being too long is told from every other refusal."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from hoshimi.api.compaction import SUMMARY_PREFIX, fit, prefix, recent_user_texts, transcript_entries
from hoshimi.core.agent.context_window import IMAGE_TOKENS, LearnedWindows, budget, estimate, message_tokens, text_tokens
from hoshimi.core.catalog import FALLBACK_CONTEXT_WINDOW, default_spec
from hoshimi.core.failure import body_says_model_gone, context_limit_in, is_context_overflow
from hoshimi.core.providers.base import ContextOverflow, ProviderError, QuotaExceeded
from hoshimi.core.providers.openai_compat import _classify_in_band, raise_for_status


def test_a_message_is_counted_at_four_bytes_a_token_with_its_images_and_calls() -> None:
    assert text_tokens("abcd" * 10) == 10
    assert text_tokens("一二三四") == 3  # twelve bytes of UTF-8
    image = {"role": "user", "content": [{"type": "text", "text": "abcd"}, {"type": "image_url", "image_url": {"url": "data:..."}}]}
    assert message_tokens(image) == 4 + 1 + IMAGE_TOKENS
    call = {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "read", "arguments": '{"path": "a"}'}}]}
    assert message_tokens(call) == 4 + 1 + 4
    assert estimate([image, call], [{"type": "function"}]) > message_tokens(image) + message_tokens(call)


@pytest.mark.parametrize(
    ("window", "output", "limit"),
    [
        (128_000, 64_000, 108_800),  # the share of the window comes first
        (1_048_576, 131_072, 891_289),
        (32_768, 4_096, 27_852),
        (16_000, 2_000, 13_600),
        (8_192, 8_192, 4_096),  # a reply needs room: what is left decides
    ],
)
def test_compaction_starts_at_the_share_of_the_window_or_where_a_reply_would_no_longer_fit(window: int, output: int, limit: int) -> None:
    spec = replace(default_spec("m", "p"), context_window=window, max_output=output)
    got = budget(spec)
    assert (got.window, got.limit, got.source) == (window, limit, "catalog")
    assert got.limit <= window - got.headroom


def test_a_learned_window_lowers_the_catalogue_s_and_none_known_is_the_default() -> None:
    spec = replace(default_spec("m", "p"), context_window=200_000)
    assert budget(spec, learned=100_000).window == 100_000
    assert budget(spec, learned=100_000).source == "learned"
    assert budget(spec, learned=300_000).window == 200_000
    assert budget(None).window == FALLBACK_CONTEXT_WINDOW
    assert budget(None).source == "default"
    windows = LearnedWindows()
    windows.learn("m", 90_000)
    windows.learn("m", 120_000)
    assert windows.get("m") == 90_000


@pytest.mark.parametrize(
    "text",
    [
        '{"error": {"code": "context_length_exceeded", "message": "This model\'s maximum context length is 131072 tokens."}}',
        "prompt is too long: 210000 tokens > 200000 maximum",
        "The input token count (1200000) exceeds the maximum number of tokens allowed (1048576).",
        "Input is too long for requested model.",
        "请求的上下文长度超过模型限制",
        "输入过长，请缩短后重试",
    ],
)
def test_every_wording_of_too_long_is_an_overflow_and_never_a_dropped_model(text: str) -> None:
    assert is_context_overflow(400, text)
    assert is_context_overflow(None, text)
    assert not body_says_model_gone(text) or is_context_overflow(400, text)
    with pytest.raises(ContextOverflow) as caught:
        raise_for_status(httpx.Response(400, content=text.encode()), provider="lane")
    assert caught.value.should_retry is False
    with pytest.raises(ContextOverflow):
        _classify_in_band("lane", text)


def test_an_overflow_is_not_read_as_quota_and_a_healthy_answer_is_not_an_overflow() -> None:
    # "limit exceeded" reads as quota, which would hold the account back for a request no account can take.
    with pytest.raises(ContextOverflow):
        raise_for_status(httpx.Response(400, content=b"context length limit exceeded"), provider="lane")
    with pytest.raises(QuotaExceeded):
        raise_for_status(httpx.Response(429, content=b"rate limit exceeded"), provider="lane")
    assert not is_context_overflow(200, "the context window of this model is large")
    assert not is_context_overflow(500, "maximum context length")
    with pytest.raises(ProviderError) as caught:
        raise_for_status(httpx.Response(400, content=b"invalid temperature"), provider="lane")
    assert not isinstance(caught.value, ContextOverflow)


def test_the_window_a_refusal_names_is_read() -> None:
    assert context_limit_in("This model's maximum context length is 131,072 tokens. However, you requested 140000") == 131_072
    assert context_limit_in("prompt is too long: 210000 tokens > 200000 maximum") == 200_000
    assert context_limit_in("exceeds the context window of 32768 tokens") == 32_768
    assert context_limit_in("input is too long") is None
    assert context_limit_in("maximum context length is 12 tokens") is None


def test_the_summarising_model_reads_cut_outputs_and_the_newest_messages_when_all_will_not_fit() -> None:
    messages = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "read", "arguments": '{"path": "a"}'}}]},
        {"role": "tool", "tool_call_id": "c", "content": "x" * 5000},
        {"role": "user", "content": "second"},
    ]
    entries = transcript_entries(messages)
    assert entries[0] == "[user]: first"
    assert entries[1] == '[assistant called read]: {"path": "a"}'
    assert entries[2].startswith("[tool result]: xxx") and entries[2].endswith("(5000 characters in all)")
    assert fit(entries, 10_000) == "\n\n".join(entries)
    short = fit(entries, 20)
    assert short.startswith("[… 3 earlier messages left out]")
    assert short.endswith("[user]: second")
    # An earlier summary stays, whatever else is left out.
    summary = transcript_entries(prefix({"summary": "S", "recent": []}) + messages[1:])
    assert fit(summary, 80).startswith(f"[user]: {SUMMARY_PREFIX}")
    assert fit(summary, 80).endswith("[user]: second")
    # One entry too long alone loses its middle.
    cut = fit(["a" * 1000 + "b" * 1000], 100)
    assert cut.startswith("a" * 200) and cut.endswith("b" * 200) and "[… cut]" in cut


def test_the_user_s_latest_words_are_kept_verbatim_up_to_a_budget() -> None:
    messages = [{"role": "user", "content": f"message {i} " + "w" * 400} for i in range(10)]
    kept = recent_user_texts(messages, window=4_000)  # 600 tokens of room: five whole messages of 107
    assert [k.split(" w")[0] for k in kept] == [f"message {i}" for i in range(5, 10)]
    # One too long for what is left is kept in part: its end.
    kept = recent_user_texts([{"role": "user", "content": "old " + "o" * 4000 + " end"}, {"role": "user", "content": "m1"}, {"role": "user", "content": "m2"}], window=4_000)
    assert kept[1:] == ["m1", "m2"]
    assert kept[0].startswith("…") and kept[0].endswith(" end") and len(kept[0]) < 2500
    assert recent_user_texts(prefix({"summary": "S", "recent": []}), window=100_000) == []
    assert "S" in prefix({"summary": "S", "recent": ["hi"]})[0]["content"]
    assert prefix({"summary": "S", "recent": ["hi"]})[0]["content"].endswith("hi")
