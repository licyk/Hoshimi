"""Passive quota: the headers a station was going to send anyway.

The upstream record is pi-free's `lib/quota-monitor.ts`, in a clone that is
gitignored — so the header pairs and the counter names are transcribed here
rather than imported.

The pairs are locked as a whole tuple rather than spot-checked: the reason a
priority *list* exists is that a station sends several dialects at once, and a
spot check would not notice the SambaNova pair quietly losing its place in the
order and a percentage appearing from the wrong window.
"""

from __future__ import annotations

import json

import httpx

from hoshimi.core.providers.agg import AggProvider
from hoshimi.core.providers.base import Account
from hoshimi.core.quota import (
    HEADER_PAIRS,
    QUOTA,
    STALE_AFTER_SECONDS,
    QuotaStore,
    extract_quota,
    looks_like_drift,
    observe_response,
)

# pi-free's `HEADER_PAIRS`, in order.
UPSTREAM_PAIRS = (
    ("x-ratelimit-remaining-requests", "x-ratelimit-limit-requests"),
    ("x-ratelimit-remaining", "x-ratelimit-limit"),
    ("ratelimit-remaining-requests", "ratelimit-limit-requests"),
    ("ratelimit-remaining", "ratelimit-limit"),
    ("x-ratelimit-remaining-requests-day", "x-ratelimit-limit-requests-day"),
    ("x-ratelimit-remaining-day", "x-ratelimit-limit-day"),
)

SSE = (
    'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"!"}}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
    "data: [DONE]\n\n"
)


# -- the table --------------------------------------------------------------- #


def test_the_header_pairs_are_upstreams_not_ours() -> None:
    assert HEADER_PAIRS == UPSTREAM_PAIRS


def test_the_scoped_pair_wins_over_the_generic_one_also_sent() -> None:
    """SambaNova sends both; the window the row is about is the one to read."""
    reading = extract_quota(
        {
            "x-ratelimit-remaining": "1",
            "x-ratelimit-limit": "10",
            "x-ratelimit-remaining-requests": "7",
            "x-ratelimit-limit-requests": "10",
        }
    )
    assert reading is not None
    assert reading.percent == 70
    assert reading.source == "x-ratelimit-remaining-requests"


def test_a_pair_counts_only_when_both_halves_parse() -> None:
    assert extract_quota({"x-ratelimit-remaining": "5"}) is None
    assert extract_quota({"x-ratelimit-limit": "10"}) is None
    # A limit of zero is the station saying "none", not a fraction to divide by.
    assert extract_quota({"x-ratelimit-remaining": "0", "x-ratelimit-limit": "0"}) is None
    assert extract_quota({"x-ratelimit-remaining": "abc", "x-ratelimit-limit": "10"}) is None


def test_the_percentage_is_clamped_to_what_a_bar_can_show() -> None:
    assert extract_quota({"x-ratelimit-remaining": "50", "x-ratelimit-limit": "10"}).percent == 100
    assert extract_quota({"x-ratelimit-remaining": "-5", "x-ratelimit-limit": "10"}).percent == 0
    assert extract_quota({"x-ratelimit-remaining": "3", "x-ratelimit-limit": "4"}).percent == 75


def test_header_casing_does_not_matter() -> None:
    """Proxies re-case headers; the station's answer does not change meaning."""
    reading = extract_quota({"X-RateLimit-Remaining": "3", "X-RateLimit-Limit": "4"})
    assert reading is not None and reading.percent == 75


def test_a_dialect_that_does_not_parse_is_refused_rather_than_guessed() -> None:
    """Deliberate deviation: pi-free's `parseFloat("10 requests")` reads 10 here.

    Refusing is what gives the drift counter below something to report — a
    number read out of junk is a wrong quota that nothing will ever flag.
    """
    assert extract_quota({"x-ratelimit-remaining": "10 requests", "x-ratelimit-limit": "100"}) is None


def test_drift_needs_both_halves_and_neither_alone() -> None:
    """A station that publishes only one of them is a half-signal, not a dialect."""
    assert looks_like_drift(["x-ratelimit-remaining-quota", "x-ratelimit-limit-quota"]) is True
    assert looks_like_drift(["ratelimit-limit-remaining", "x-limit"]) is True
    assert looks_like_drift(["x-ratelimit-remaining-quota"]) is False
    assert looks_like_drift(["x-ratelimit-limit-quota"]) is False
    assert looks_like_drift(["content-type", "accept"]) is False
    # "unlimited" is not a limit: the hyphen is what makes `limit` a boundary.
    assert looks_like_drift(["x-ratelimit-remaining", "x-unlimited"]) is False


# -- the counters ------------------------------------------------------------ #


def test_the_four_outcomes_land_in_their_own_counters() -> None:
    store = QuotaStore()
    store.observe("p", 200, {}, now=0.0)
    store.observe("p", 401, {}, now=1.0)
    store.observe("p", 403, {}, now=2.0)
    store.observe("p", 429, {}, now=3.0)
    store.observe("p", 503, {}, now=4.0)
    row = store.snapshot(now=5.0)[0]
    assert row["counters"] == {
        "responses": 5,
        "auth_failures": 2,
        "rate_limited": 1,
        "server_errors": 1,
        "quota_header_drift": 0,
    }


def test_a_dialect_the_table_cannot_read_is_counted_instead_of_shown() -> None:
    store = QuotaStore()
    store.observe("p", 200, {"x-ratelimit-remaining-quota": "3", "x-ratelimit-limit-quota": "10"}, now=0.0)
    row = store.snapshot(now=1.0)[0]
    assert row["percent"] is None and row["counters"]["quota_header_drift"] == 1
    # Nothing was read, so there is no figure to call current.
    assert row["stale"] is True


def test_a_429_that_still_reports_quota_is_counted_and_read() -> None:
    store = QuotaStore()
    store.observe("p", 429, {"x-ratelimit-remaining": "0", "x-ratelimit-limit": "100"})
    row = store.snapshot()[0]
    assert row["counters"]["rate_limited"] == 1 and row["percent"] == 0


def test_counters_accumulate_and_the_reading_is_replaced() -> None:
    store = QuotaStore()
    store.observe("p", 200, {"ratelimit-remaining": "5", "ratelimit-limit": "10"}, now=0.0)
    store.observe("p", 200, {"ratelimit-remaining": "1", "ratelimit-limit": "10"}, now=1.0)
    row = store.snapshot(now=2.0)[0]
    assert row["percent"] == 10 and row["counters"]["responses"] == 2


def test_a_provider_that_says_nothing_interesting_has_no_row() -> None:
    """`responses` on its own is the call count stats already keeps.

    Quota headers are the whole reason this store exists; a provider that never
    sends one and never fails belongs in the numbers, not in a quota panel.
    """
    store = QuotaStore()
    store.observe("quiet", 200, {}, now=0.0)
    assert store.snapshot(now=1.0) == []
    store.observe("loud", 200, {"ratelimit-remaining": "1", "ratelimit-limit": "10"}, now=0.0)
    assert [row["provider"] for row in store.snapshot(now=1.0)] == ["loud"]


# -- staleness --------------------------------------------------------------- #


def test_a_reading_is_only_worth_five_minutes() -> None:
    store = QuotaStore()
    store.observe("p", 200, {"ratelimit-remaining": "5", "ratelimit-limit": "10"}, now=1000.0)
    assert store.is_stale("p", now=1000.0) is False
    assert store.is_stale("p", now=1000.0 + STALE_AFTER_SECONDS) is False
    assert store.is_stale("p", now=1000.5 + STALE_AFTER_SECONDS) is True
    # The figure is still reported — a client may show it — but flagged as past
    # the moment it stops being worth believing.
    assert store.snapshot(now=2000.0)[0]["stale"] is True
    assert store.is_stale("never-seen", now=0.0) is True


def test_the_store_holds_numbers_and_header_names_and_nothing_else() -> None:
    """Why this can be returned from a diagnostics endpoint at all."""
    store = QuotaStore()
    store.observe(
        "p",
        200,
        {
            "authorization": "Bearer sk-secret",
            "x-api-key": "sk-also-secret",
            "x-ratelimit-remaining": "5",
            "x-ratelimit-limit": "10",
        },
    )
    text = json.dumps(store.snapshot())
    assert "sk-secret" not in text and "Bearer" not in text
    assert "sk-also-secret" not in text
    # The one string kept is the header's *name*.
    assert store.snapshot()[0]["source"] == "x-ratelimit-remaining"


def test_the_store_is_scoped_per_provider_and_resets() -> None:
    store = QuotaStore()
    store.observe("a", 200, {"ratelimit-remaining": "1", "ratelimit-limit": "2"}, now=0.0)
    store.observe("b", 429, {}, now=0.0)
    assert [row["provider"] for row in store.snapshot(now=1.0)] == ["a", "b"]
    assert len(store) == 2
    assert store.reset() == 2
    assert store.snapshot() == [] and len(store) == 0


# -- the wire ---------------------------------------------------------------- #


def test_a_turn_records_the_quota_it_was_sent_with() -> None:
    """The whole mechanism: no extra request, no code in the turn's own path.

    The headers ride the answer that carries the tokens, so the reading costs
    nothing beyond the line that observes it.
    """
    QUOTA.reset()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SSE, headers={"x-ratelimit-remaining": "3", "x-ratelimit-limit": "4"})

    account = Account({"id": "or-1", "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-1"}, provider_id="agg")
    provider = AggProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account])
    try:
        list(provider.stream_raw(account, "openrouter/auto", {"messages": []}))
        rows = [row for row in QUOTA.snapshot() if row["provider"] == "agg"]
        assert rows and rows[0]["percent"] == 75
        assert rows[0]["counters"]["responses"] == 1
    finally:
        QUOTA.reset()


def test_the_monitor_never_takes_a_turn_down_with_it() -> None:
    """This runs on the path carrying a user's turn; a header store is not worth a failed generation."""

    class BrokenResponse:
        status_code = 200

        @property
        def headers(self) -> dict[str, str]:
            raise ValueError("no headers here")

    observe_response("p", BrokenResponse())  # type: ignore[arg-type]
    assert "p" not in {row["provider"] for row in QUOTA.snapshot()}
