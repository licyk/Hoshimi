"""Which models a station documents as free, and what "free" may mean here.

The upstream record is pi-free's per-provider mappers — `agnes-models.ts`,
`anyapi.ts`, `bai.ts`, `gmi-models.ts`, `opengateway.ts`, `orcarouter.ts`,
`tokenrouter.ts`, `xkiro.ts` — plus the `_freeKnown`/`_isFree` stamps in
`lib/model-map.ts`, in a clone that is gitignored, so every expectation below
is transcribed rather than imported.

The *three-way* answer is what is locked here, because it is the part a summary
would lose. pi-free's stamps are `_isFree` guarded by `_freeKnown`, and a
second layer sits underneath them — `isFreeModel` in `lib/registry.ts`, two
routes that turn the stamps into the boolean pi-free's free-only view shows by
default. A test written against that boolean would pass while every unpriced
model in the gateway had quietly become a promise about the operator's money,
which is why the last group of tests below exists at all.
"""

from __future__ import annotations

import pytest

from hoshimi.core.free_marks import FREE_MARK_STATIONS, STATION_FREE_IDS, documented_free, row_free
from hoshimi.core.presets import PRESETS

Row = dict[str, object]

# The stations pi-free reads a price out of a catalogue row, and no others.
# `agnes` and `commandcode` are in FREE_MARK_STATIONS with no reader: both
# publish their prices somewhere other than the listing.
UPSTREAM_FREE_STATIONS = {
    "agnes",
    "anyapi",
    "bai",
    "commandcode",
    "gmi",
    "opengateway",
    "orcarouter",
    "tokenrouter",
    "xkiro",
}

NOT_A_PRICE_LIST = [
    {"id": "gpt-6"},
    {"id": "x", "is_free": True},
    {"id": "x", "isFree": True},
    {"id": "x", "access_tier": "free"},
    {"id": "x:free"},
    {"id": "my free model"},
    {"id": "x", "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "x", "pricing": {"input": "0", "output": "0"}},
]

# -- the stations that answer from the row ---------------------------------- #

CASES: list[tuple[str, Row, bool | None]] = [
    # agnes: no reader, because its listing carries no price to read.
    ("agnes", {"id": "agnes-2.0-flash"}, None),
    ("agnes", {"id": "agnes-pro"}, None),
    ("agnes", {"id": "agnes-pro", "pricing": {"prompt": "0", "completion": "0"}}, None),
    # commandcode: the same shape, and the same reason.
    ("commandcode", {"id": "stealth/ox-alpha"}, None),
    ("commandcode", {"id": "stealth/ox-alpha", "pricing": {"prompt": "0", "completion": "0"}}, None),
    # bai: free is spelled in the id itself, and only in that form.
    ("bai", {"id": "deepseek-v4-flash:free"}, True),
    ("bai", {"id": "DEEPSEEK-V4-FLASH:FREE"}, True),
    ("bai", {"id": "deepseek-v4-flash"}, None),
    ("bai", {"id": "claude-opus-5"}, None),
    # tokenrouter: the same encoding, and no table behind it.
    ("tokenrouter", {"id": "nvidia/nemotron-3-ultra-550b-a55b:free"}, True),
    ("tokenrouter", {"id": "openai/gpt-6"}, None),
    ("tokenrouter", {"id": "some-free-thing"}, None),  # -free is not :free
    # gmi: an explicit boolean, kept whether it is true or false.
    ("gmi", {"id": "MiniMaxAI/MiniMax-M3", "is_free": True}, True),
    ("gmi", {"id": "MiniMaxAI/MiniMax-M2.7", "is_free": False}, False),
    ("gmi", {"id": "x", "isFree": False, "is_free": True}, False),  # isFree is read first
    ("gmi", {"id": "x"}, None),
    # anyapi: a flag, then a label, then a price list — in that order.
    ("anyapi", {"id": "x", "isFree": False}, False),
    ("anyapi", {"id": "stealth/ox-alpha-free"}, True),
    ("anyapi", {"id": "my free model"}, True),
    ("anyapi", {"id": "freedom-8b"}, None),  # the label check has word boundaries
    ("anyapi", {"id": "x", "pricing": {"prompt": "0", "completion": "0.000000"}}, True),
    ("anyapi", {"id": "x", "pricing": {"prompt": 0, "completion": 0.5}}, None),
    ("anyapi", {"id": "x", "pricing": {"completion": "0"}}, None),  # a half price list says nothing
    ("anyapi", {"id": "x", "pricing": {}}, None),
    ("anyapi", {"id": "x", "pricing": {"prompt": "n/a", "completion": "0"}}, None),
    # A blank price is a field nobody filled in. Upstream's Number("") is 0, so
    # anyapi would read this as a stated zero; this gateway does not.
    ("anyapi", {"id": "x", "pricing": {"prompt": "", "completion": ""}}, None),
    # orcarouter: its free tier is priced with a zero `request` field, and its
    # house router models publish no price at all.
    ("orcarouter", {"id": "openrouter/auto", "isFree": True}, True),
    ("orcarouter", {"id": "meta/llama-3.3-70b-instruct:free"}, True),
    ("orcarouter", {"id": "orcarouter/fusion"}, None),
    ("orcarouter", {"id": "x", "pricing": {"request": "0.000000"}}, True),
    ("orcarouter", {"id": "x", "pricing": {"request": ""}}, None),
    # `request` is part of a price list at OrcaRouter and nowhere else, which
    # is the whole reason the two stations are spelled out separately.
    ("anyapi", {"id": "x", "pricing": {"request": "0"}}, None),
    ("orcarouter", {"id": "x", "pricing": {"prompt": "0", "completion": "0"}}, True),
    ("orcarouter", {"id": "x", "pricing": {"prompt": "0.3", "completion": "1.1"}}, None),
    # xkiro: the tier outranks every other clue, in both directions.
    ("xkiro", {"id": "x", "access_tier": "free"}, True),
    ("xkiro", {"id": "x", "access_tier": "paid"}, False),
    ("xkiro", {"id": "x", "access_tier": "premium"}, False),
    ("xkiro", {"id": "x", "access_tier": "enterprise"}, False),
    ("xkiro", {"id": "grok-4.5-free", "access_tier": "unknown"}, True),
    ("xkiro", {"id": "grok-4.5", "access_tier": "unknown", "pricing": {"input": "0", "output": "0"}}, True),
    ("xkiro", {"id": "grok-4.5", "access_tier": "unknown", "pricing": {"input": "0.3"}}, None),
    ("xkiro", {"id": "grok-4.5", "access_tier": "unknown"}, None),
]

# -- the one station whose price list answers both ways --------------------- #

OPENGATEWAY: list[tuple[str, Row, bool | None]] = [
    ("opengateway", {"id": "mindai/macaron-v1-tall", "pricing": {"prompt": 0, "completion": 0}}, True),
    ("opengateway", {"id": "tencent/hy3", "pricing": {"prompt": "0.00000024", "completion": "0.00000096"}}, False),
    ("opengateway", {"id": "x", "pricing": {"prompt": "0", "completion": "1"}}, False),
    ("opengateway", {"id": "x"}, None),
    # An effective price supersedes the list one, but only when it is complete:
    # a half-written promotion falls back rather than half-answering.
    (
        "opengateway",
        {"id": "x", "effective_pricing": {"prompt": 0, "completion": 0}, "pricing": {"prompt": "1", "completion": "1"}},
        True,
    ),
    (
        "opengateway",
        {"id": "x", "effective_pricing": {"prompt": 0}, "pricing": {"prompt": "1", "completion": "1"}},
        False,
    ),
]


@pytest.mark.parametrize("station, row, expected", CASES + OPENGATEWAY, ids=[f"{s}-{i}" for i, (s, _, _) in enumerate(CASES + OPENGATEWAY)])
def test_a_row_is_read_the_way_its_station_reads_it(station: str, row: Row, expected: bool | None) -> None:
    assert row_free(station, row) is expected


def test_the_membership_is_exactly_what_upstream_reads() -> None:
    """Nine of the twenty-two presets, and the cases above cover all nine."""
    assert FREE_MARK_STATIONS == UPSTREAM_FREE_STATIONS
    assert {station for station, _, _ in CASES + OPENGATEWAY} == FREE_MARK_STATIONS
    assert FREE_MARK_STATIONS <= {preset.id for preset in PRESETS}


def test_a_station_nobody_reads_leaves_every_row_unanswered() -> None:
    """Including rows another station would answer on an identical field.

    Thirteen presets are here on purpose. The two worth naming are ``venice``
    (which lists a model at $0/$0 that an unfunded key cannot call at all —
    HTTP 402 — so a right answer about the price list is a wrong answer about
    the account) and ``llm7`` (which prices everything it publishes, as a list
    price rather than as a statement about a balance).
    """
    unjudged = {preset.id for preset in PRESETS} - FREE_MARK_STATIONS
    assert len(unjudged) == 13
    for station in sorted(unjudged):
        for row in NOT_A_PRICE_LIST:
            assert row_free(station, row) is None, f"{station} answered {row}"


def test_an_absent_station_or_row_is_not_an_answer() -> None:
    assert row_free(None, {"id": "x", "is_free": True}) is None
    assert row_free("gmi", None) is None


def test_the_three_tables_are_the_three_upstream_tables() -> None:
    """And only their free side: the ids a station prices elsewhere."""
    assert set(STATION_FREE_IDS) == {"agnes", "bai", "commandcode"}
    assert STATION_FREE_IDS["agnes"] == {"agnes-2.0-flash", "agnes-2.5-flash"}
    assert STATION_FREE_IDS["bai"] == {
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
        "mimo-v2.5",
    }
    assert STATION_FREE_IDS["commandcode"] == {"poolside/laguna-s-2.1-free", "stealth/ox-alpha"}
    # Every table is a subset of what its station can answer for at all.
    assert set(STATION_FREE_IDS) <= FREE_MARK_STATIONS


def test_the_table_is_only_asked_when_the_row_has_not_answered() -> None:
    """A station publishing a price list keeps its own answer over our audit."""
    assert documented_free("agnes", "agnes-2.0-flash") is True
    assert documented_free("bai", "mimo-v2.5") is True
    assert documented_free("agnes", "agnes-pro") is None
    assert documented_free("bai", "claude-opus-5") is None
    assert documented_free("novita", "mimo-v2.5") is None
    assert documented_free(None, "agnes-2.0-flash") is None
    # And a table id at a station with no table is worth nothing.
    assert documented_free("tokenrouter", "agnes-2.0-flash") is None


def test_a_promotion_is_carried_as_the_last_thing_anyone_verified() -> None:
    """The one thing here that can go stale on its own, and how it goes stale.

    bai's three ids are worded "currently free" on its pricing page. Nothing
    re-reads that page, so the day the window ends the gateway will still show
    the badge — which is why no mark anywhere is allowed to become an input to
    a decision. It is a badge.
    """
    assert documented_free("bai", "deepseek-v4-flash") is True
    assert row_free("bai", {"id": "deepseek-v4-flash"}) is None
