"""Which models a station documents as free.

``chat_catalog.py`` asks one question about a catalogue row — is this a chat
model? — because offering an image generator as a chat model fails at the wire
and costs the caller nothing but a round trip. This asks the other question a
free-model gateway has to answer about a row, and the two have opposite
asymmetries: saying a paid model is free is a promise about somebody else's
money, and the failure lands on the operator rather than on a caller who can
watch a 400 arrive.

So a mark is three-way — ``True``, ``False`` or ``None``, where ``None`` means
the station did not say — and pi-free's stamps are three-way for the same
reason (`_isFree` is only ever consulted when `_freeKnown` is set). A station
that publishes no price leaves its models unmarked rather than leaving them to
a default, and the caller sees "unknown", which is what the gateway knows.

**What is transcribed is the stamp, not the filter.** pi-free has a second
layer underneath — `isFreeModel` in `lib/registry.ts`, two routes that turn
those stamps into the boolean its free-only view shows by default — and it is
deliberately not carried. That layer is a policy about what a personal picker
opens on: it scans a whole catalogue to decide whether the station publishes
prices at all, and it fills in "not free" wherever the station was silent. The
stamps are what the station said; the routes are what pi-free decided to do
with the silence. Carrying the routes would make `None` unreachable and this
gateway would be claiming a price list it never read — which is the same
borrowed fact C23 refused when it stopped taking context windows from
models.dev.

Nine of the twenty-two presets can answer at all. Seven read their answer out of
the row itself. The two that cannot — ``agnes`` and ``commandcode`` — keep a
table, because both publish their prices somewhere other than the listing:

- **agnes** prices nothing in ``/models``; per its pricing docs only the
  flash-class chat models are free and the pro models are billed.
- **commandcode** is the same shape: its listing carries no price either, and
  the two ids its own pricing page prices at $0 come from that page.

``bai`` has a table *and* a reader, because it is the one station that answers
both ways: three of its ids are priced free on its pricing page, and the rest
encode free in the id itself (``<model>:free``) — the same encoding
tokenrouter uses, which is why tokenrouter needs no table of its own.

**Venice is deliberately absent.** It lists ``stealth-ox-alpha`` at $0/$0, and
pi-free's own audit records what a $0 price there means: the gateway requires a
positive account balance for every inference and answers HTTP 402 to an
unfunded key. A mark that is right about the price list and wrong about the
gate is worse than no mark, so the station gets none.

**So does llm7** (see ``chat_catalog.py``): it prices every row it publishes,
which is a list price rather than a statement about the account's balance.

A free window this gateway cannot expire — bai's promotions, or a station's
week-long giveaway — is carried anyway and is the one thing here that can go
stale on its own. It goes stale in the direction of a missing badge, never in
the direction of a promise: nothing re-verifies a mark, and nothing turns
``None`` into ``True`` except the station publishing the fact again.
"""

from __future__ import annotations

import math
import re
from typing import Any

from hoshimi.core.chat_catalog import Row

__all__ = [
    "FREE_MARK_STATIONS",
    "STATION_FREE_IDS",
    "documented_free",
    "row_free",
]


STATION_FREE_IDS: dict[str, frozenset[str]] = {
    "agnes": frozenset({"agnes-2.0-flash", "agnes-2.5-flash"}),
    "bai": frozenset({"deepseek-v4-flash", "deepseek-v4-flash-vision-exp", "mimo-v2.5"}),
    "commandcode": frozenset({"poolside/laguna-s-2.1-free", "stealth/ox-alpha"}),
}
"""The ids a station prices away from its listing, by station.

All three tables are transcriptions of pi-free's (`FREE_CHAT_MODEL_IDS`,
`BAI_DOCUMENTED_FREE_MODEL_IDS`, and the two `$0` entries of CommandCode's
`MODEL_COSTS`), and each comes with its own caveat upstream records and this
gateway inherits: agnes's is a pricing document that changes without telling
anyone, bai's is worded "currently free" — three promotions that end — and
commandcode's two ids carry a `-free` suffix and a `stealth/` prefix that
nobody re-verifies. None can be expired by code that never re-reads a price
page, so all three are carried as the last thing anyone verified and nothing
more.

CommandCode's table holds only its two $0 rows. The other 56 entries are
priced, and a paid model's price is not this gateway's business — the rule
this migration has followed throughout is that paid and unverifiable entries
do not get carried, so what came across was the free half of the file and the
half that says what a model is.
"""

FREE_MARK_STATIONS: frozenset[str] = frozenset(
    {"agnes", "anyapi", "bai", "commandcode", "gmi", "opengateway", "orcarouter", "tokenrouter", "xkiro"}
)
"""The presets that can say whether an id is free, and no others.

Nine of the twenty-two. The other thirteen are recorded rather than inferred,
for the same reason the catalogue filters are: a station quietly losing its
answer is invisible, and this list is the only place the loss would show.
"""

# pi-free matches a free *label* with a word boundary, so `gpt-4o-free` counts
# and `freedom-8b` does not. It is the one rule every station that reads its
# labels agrees on, and it is safe in the direction that matters: it can only
# ever add a badge.
_FREE_LABEL = re.compile(r"\bfree\b", re.IGNORECASE)

# Xkiro's own tiers that answer the question outright. `free` is the fourth,
# and it is the only one of the four that is True.
_PAID_TIERS = frozenset({"paid", "premium", "enterprise"})

# The pricing fields a station may publish, by the names pi-free's shared
# hasPricing checks. Any of them being *present* is what makes a price list
# exist; whether it is zero is a separate question.
_PRICING_KEYS_ANYAPI = ("prompt", "completion", "input_cache_read", "input_cache_write")
# OrcaRouter's own price list counts its `request` field, which is how its free
# tier is priced — every other station's list is the four token fields.
_PRICING_KEYS_ORCAROUTER = _PRICING_KEYS_ANYAPI + ("request",)
_PRICING_KEYS_XKIRO = ("input", "output", "cache_read", "cache_write")


def _get(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return None


def _present(value: Any) -> bool:
    """Whether a field is there at all.

    ``""`` is absence here because JavaScript's ``Number("")`` is ``0``, and a
    station that leaves a price blank has not said it is free — it has said
    nothing. Only the *presence* check needs this; ``_number`` never sees an
    absent field.
    """
    return value is not None and value != ""


def _number(value: Any) -> float | None:
    """Parse a price field the way the upstream tables parse it.

    ``Number("abc")`` is NaN, and every comparison against NaN is false, so an
    unparseable price is recorded as None and the ``== 0`` below fails — which
    is exactly the answer upstream gives, and the safe one.
    """
    if not _present(value):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _pricing_of(row: Row, keys: tuple[str, ...]) -> dict[str, Any] | None:
    """The row's ``pricing`` object, but only if it carries any of ``keys``."""
    pricing = _get(row, "pricing")
    if not isinstance(pricing, dict):
        return None
    if not any(_present(pricing.get(key)) for key in keys):
        return None
    return pricing


def _label(row: Row, *keys: str) -> str:
    return " ".join(str(_get(row, key)) for key in keys if _get(row, key) is not None).lower()


def _both_zero(pricing: dict[str, Any], *keys: str) -> bool:
    return all(_number(pricing.get(key)) == 0 for key in keys)


def _free_if_zero(pricing: dict[str, Any], *keys: str) -> bool | None:
    """A price list that is zero says free; one that is not says nothing.

    This is the whole reason the answer is three-way. pi-free stamps
    ``_isFree`` only where it also sets ``_freeKnown``, and for these stations
    it sets ``_freeKnown`` only when its own detector agreed the model *is*
    free — so a priced model is unmarked rather than stamped paid. That is the
    layer transcribed here: the station's statement, not pi-free's default
    view, which decides what a picker shows by default and is a policy about
    that picker rather than a fact about the station.

    It is also why a blank price returns None rather than zero: upstream's
    ``Number("")`` is ``0``, so anyapi and xkiro read an empty string as a
    stated zero. A field left blank is a field nobody filled in, and this
    gateway does not turn that into a promise.
    """
    return True if _both_zero(pricing, *keys) else None


def _row_free_anyapi(row: Row) -> bool | None:
    flag = _get(row, "isFree")
    if isinstance(flag, bool):
        return flag
    if _FREE_LABEL.search(_label(row, "id", "name")):
        return True
    pricing = _pricing_of(row, _PRICING_KEYS_ANYAPI)
    if pricing is None:
        return None
    return _free_if_zero(pricing, "prompt", "completion")


def _row_free_orcarouter(row: Row) -> bool | None:
    """OrcaRouter prices its free tier with a zero ``request`` field.

    Its house ``orcarouter/fusion*`` router models publish no price at all, and
    they are not free — which is why an absent price list returns None rather
    than False: nothing here has to notice that they are not free, only that
    nobody said they were.
    """
    flag = _get(row, "isFree")
    if isinstance(flag, bool):
        return flag
    if _FREE_LABEL.search(_label(row, "id", "name")):
        return True
    pricing = _pricing_of(row, _PRICING_KEYS_ORCAROUTER)
    if pricing is None:
        return None
    request = _get(pricing, "request")
    if _present(request) and _number(request) == 0:
        return True
    return _free_if_zero(pricing, "prompt", "completion")


def _row_free_xkiro(row: Row) -> bool | None:
    """Xkiro states a tier, and the tier outranks every other clue.

    Below the tier the station falls back to a label and then to zero pricing,
    but the four tier names it publishes answer the question on their own, so
    neither fallback can contradict them.
    """
    tier = _get(row, "access_tier")
    if tier == "free":
        return True
    if tier in _PAID_TIERS:
        return False
    if _FREE_LABEL.search(_label(row, "id", "display_name")):
        return True
    pricing = _pricing_of(row, _PRICING_KEYS_XKIRO)
    if pricing is None:
        return None
    return _free_if_zero(pricing, "input", "output")


def _row_free_opengateway(row: Row) -> bool | None:
    """OpenGateway prices per model, and an effective price supersedes the list one.

    ``effective_pricing`` is what a promotion actually charges, so it is read
    first and only when it is complete; a half-written effective price falls
    back to the list price rather than half-answering.
    """
    pricing = _get(row, "effective_pricing")
    if not isinstance(pricing, dict) or not any(_present(pricing.get(key)) for key in ("prompt", "completion")):
        pricing = _get(row, "pricing")
    if not isinstance(pricing, dict) or not any(_present(pricing.get(key)) for key in ("prompt", "completion")):
        return None
    return _both_zero(pricing, "prompt", "completion")


def _row_free_gmi(row: Row) -> bool | None:
    """GMI publishes an ``is_free`` boolean; the shared mapper also accepts ``isFree``.

    An explicit False is an answer, so it is kept: a station that says a model
    is not free has said something, and only a station that says nothing gets
    None.
    """
    for key in ("isFree", "is_free"):
        flag = _get(row, key)
        if isinstance(flag, bool):
            return flag
    return None


def _free_suffix(row: Row) -> bool | None:
    """B.AI and TokenRouter encode the whole answer in the id.

    Both publish no usable price list at all, and both advertise promotional
    ids as ``<model>:free``. The suffix is therefore the only fact the row
    carries, and an id without it is not paid — it is unpriced.
    """
    ident = str(_get(row, "id") or "")
    return True if ident.lower().endswith(":free") else None


_ROW_READERS = {
    "anyapi": _row_free_anyapi,
    "bai": _free_suffix,
    "gmi": _row_free_gmi,
    "opengateway": _row_free_opengateway,
    "orcarouter": _row_free_orcarouter,
    "tokenrouter": _free_suffix,
    "xkiro": _row_free_xkiro,
}


def row_free(station: str | None, row: Row | None) -> bool | None:
    """What this station's own row says about the price of one id.

    ``None`` means the row said nothing, which is a real answer and not a
    failure: ``documented_free`` is asked next, and past that the model stays
    unmarked. ``agnes`` reads nothing here because its listing carries no
    price at all — its whole answer is the table.
    """
    if station is None or row is None:
        return None
    reader = _ROW_READERS.get(station)
    if reader is None:
        return None
    return reader(row)


def documented_free(station: str | None, model_id: str) -> bool | None:
    """The three stations that price their free ids somewhere other than the listing.

    Called only when the row itself has not answered, so a station that starts
    publishing a price list keeps its own answer over this one: the table is a
    transcription of somebody else's audit, and the row is the station speaking
    for itself today.
    """
    if station is None:
        return None
    return True if model_id in STATION_FREE_IDS.get(station, frozenset()) else None
