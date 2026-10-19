"""A catalogue that comes back halved is a partial answer, once.

The upstream record is pi-free's `saveProviderCacheGuarded`
(`lib/provider-cache.ts`): refuse to overwrite a healthy entry with less
than half the ids, and never cache an empty list. The threshold is a
fraction there and here, because catalogue sizes differ by two orders of
magnitude between a lane with three models and one with three hundred.

The half this project adds is the second cycle. Refusing the overwrite
forever is the wrong fix in the other direction: a station that really did
drop its free tier would be reported with ids that 404 at the wire, which
is a worse bug than the one being guarded against. So the guard holds for
one TTL and then believes the station.

Empty is a separate answer with an answer of its own — the lane's static
list — and never passes through the guard at all.
"""

from __future__ import annotations

import httpx
import pytest

from hoshimi.core.providers.base import MODELS_SHRINK_FRACTION, Account, Provider


class _Lane(Provider):
    """A lane whose listing can be changed between fetches."""

    id = "lane"

    def __init__(self, listings: list[list[str]], fallback: list[str] | None = None):
        super().__init__(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="{}"))), [Account({"id": "a"}, provider_id="lane")])
        self._listings = list(listings)
        self._fallback = fallback or []
        self.fetches = 0

    def fallback_models(self) -> list[str]:
        return list(self._fallback)

    def fetch_models(self) -> list[str]:
        self.fetches += 1
        return self._listings.pop(0) if self._listings else []

    def stream_raw(self, account: Account, model: str, body: dict) -> object:
        yield {"type": "finish", "reason": "stop"}

    def next_cycle(self) -> None:
        """Let the TTL lapse without waiting five minutes for it."""
        self._models_expires_at = 0.0


FULL = [f"model-{index}" for index in range(10)]
HALF = FULL[:3]


def test_a_shrunk_listing_does_not_overwrite_a_healthy_one() -> None:
    lane = _Lane([FULL, HALF])

    assert lane.models() == FULL
    lane.next_cycle()

    assert lane.models() == FULL, "a partial answer must not shrink the catalogue"
    assert lane.fetches == 2, "the guard holds the answer; it does not stop asking"


def test_the_hold_lasts_exactly_one_cycle() -> None:
    """Believed after a full cycle, or the gateway reports ids that 404."""
    lane = _Lane([FULL, HALF, HALF])

    lane.models()
    lane.next_cycle()
    lane.models()
    lane.next_cycle()

    assert lane.models() == HALF, "a station that said the same thing twice was telling the truth"


def test_a_catalogue_that_grows_back_is_taken_at_once() -> None:
    """The station recovered; holding the shrunken one is now the bug."""
    lane = _Lane([FULL, HALF, FULL])

    lane.models()
    lane.next_cycle()
    lane.models()
    lane.next_cycle()

    assert lane.models() == FULL


def test_a_mild_shrink_is_a_real_answer() -> None:
    """Two of ten gone is a station tidying up, not a truncated page."""
    lane = _Lane([FULL, FULL[2:]])

    lane.models()
    lane.next_cycle()

    assert lane.models() == FULL[2:]


def test_an_empty_listing_falls_back_and_is_never_held_back() -> None:
    """An empty answer already has one — the lane's own list.

    A static list is not a shrunk answer, so the guard does not apply to it:
    there is nothing to protect, and holding the previous cycle's ids would
    hide the fact that the station stopped listing anything.
    """
    lane = _Lane([FULL, [], ["only-one"]], fallback=["static-a", "static-b"])

    assert lane.models() == FULL
    lane.next_cycle()
    assert lane.models() == ["static-a", "static-b"]

    # And a station that comes back with one model after answering empty is
    # answering, not truncating: the fallback is not a healthy catalogue.
    lane.next_cycle()
    assert lane.models() == ["only-one"]


def test_a_failed_fetch_falls_back_without_tripping_the_guard() -> None:
    class _ThrowingLane(_Lane):
        def fetch_models(self) -> list[str]:
            raise RuntimeError("the listing endpoint is down")

    lane = _ThrowingLane([FULL], fallback=["static-a"])

    assert lane.models() == ["static-a"]
    lane.next_cycle()
    assert lane.models() == ["static-a"]


def test_the_threshold_is_a_fraction_not_a_count() -> None:
    """The same absolute drop means opposite things on two catalogues.

    Dropping two ids is the whole of a three-model lane and a rounding error
    on a twenty-model one, so a count would either refuse the second or
    accept the first — and the first is the truncation this exists to catch.
    """
    assert MODELS_SHRINK_FRACTION == 0.5

    small = _Lane([["a", "b", "c"], ["a"]])
    assert small.models() == ["a", "b", "c"]
    small.next_cycle()
    assert small.models() == ["a", "b", "c"], "two of three gone is a truncated page"

    big = _Lane([[f"model-{index}" for index in range(20)], [f"model-{index}" for index in range(2, 20)]])
    assert len(big.models()) == 20
    big.next_cycle()
    assert len(big.models()) == 18, "the same two gone is a station tidying up"


def test_fetch_failure_preserves_the_last_healthy_catalogue(monkeypatch) -> None:
    lane = _Lane([FULL], fallback=["static-a"])
    assert lane.models() == FULL
    lane.next_cycle()

    def fail():
        raise RuntimeError("offline")

    monkeypatch.setattr(lane, "fetch_models", fail)
    assert lane.models() == FULL
    assert lane.catalog_failed is True

    # Exactly half is not less than half, so it is taken rather than held.
    edge = _Lane([FULL, FULL[:5]])
    assert len(edge.models()) == 10
    edge.next_cycle()
    assert len(edge.models()) == 5


def test_the_cache_is_still_served_within_its_window() -> None:
    """Holding is not a licence to ask again before the TTL."""
    lane = _Lane([FULL, HALF])

    assert lane.models() == FULL
    assert lane.models() == FULL
    assert lane.fetches == 1


def test_an_initial_shrunk_listing_is_taken_because_there_is_nothing_to_compare() -> None:
    """The first answer has no healthy entry behind it to protect."""
    lane = _Lane([HALF])

    assert lane.models() == HALF
    assert lane.fetches == 1


def test_the_hold_survives_a_cycle_in_which_the_station_was_unreachable() -> None:
    """An outage must not silently clear the evidence in either direction."""
    lane = _Lane([FULL, HALF, [], HALF])

    lane.models()
    lane.next_cycle()
    lane.models()  # held
    lane.next_cycle()
    assert lane.models() == lane.fallback_models()
    lane.next_cycle()

    assert lane.models() == HALF, "the station said the same shrunk thing twice; the outage in between is not an answer"


@pytest.mark.parametrize("listing", [FULL, HALF])
def test_a_listing_is_returned_as_a_copy(listing: list[str]) -> None:
    """A caller must not be able to edit the catalogue through the returned list."""
    lane = _Lane([listing])

    got = lane.models()
    got.append("someone-elses-model")

    assert lane.models() == listing
