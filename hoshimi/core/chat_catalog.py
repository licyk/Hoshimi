"""Which rows in a station's ``/models`` are text chat models.

An OpenAI-shaped catalogue is a list of *endpoints the station sells*, not a
list of things a chat completion can drive. The same array carries image and
video generators, embeddings, rerankers, transcription, a row the station left
enabled by mistake, and — on at least one gateway — internal smoke-test entries
it never meant to publish. ``agg`` used to pass every one of them to the caller
as an available model, and the caller's first turn was how they found out.

So this is a per-station predicate over a catalogue *row*, for the most part
transcribed from pi-free's providers (the exception is named below). pi-free
needs the same fact for the same reason (its picker cannot show an embedding
model either), and it is the one part of its model knowledge that live fetching
cannot recover: a station that publishes
``agnes-image-2.1-flash`` publishes it correctly, and no amount of asking will
say that it is not a chat model.

Two rules govern what is here:

- **A row is judged on the fields it carries.** Absent fields keep the row.
  Every station below would otherwise be one schema change away from an empty
  catalogue, and a gateway that lists nothing is a worse answer than one that
  lists something odd.
- **Nothing that is not a chat filter is here.** Pricing, free/paid stamps and
  capability numbers are other questions (see ``catalog.py`` and
  ``modelsdev.py``); this module only decides whether a row is a chat target
  at all.

Stations hoshimi reaches by a *different* address than pi-free's are not in the
table — an address that does not appear in ``presets`` gets no filter, which is
also what an operator's own relay gets. Guessing from a hostname would apply
somebody else's schema to a station we have never seen.

One entry is **not** transcribed: ``llm7``, which has no predicate upstream
because upstream never reads its catalogue at all (it registers three routing
selectors built locally instead). Read against the live endpoint it plainly
needs one — of sixty-four rows, fifteen are typed ``image``, ``video`` or
``audio_to_text`` — and the shared rule cannot substitute, because the two rows
that show why are ``whisper-large-v3`` (a speech-to-text row whose output *is*
``text``, served by another endpoint) and ``gpt-image-2`` (which produces text
*and* an image). So llm7 is judged on its own ``model_type``, and it is marked
in the tests as hoshimi's judgement rather than somebody else's.

The address is what identifies a station, not the ``preset`` name: an account
may name ``preset: sambanova`` and still point ``base_url`` somewhere else, and
then it is not SambaNova's catalogue we are reading.
"""

from __future__ import annotations

import re
from typing import Any

from hoshimi.core.presets import PRESETS

Row = dict[str, Any]

# -- the shared vocabularies ------------------------------------------------- #

CHAT_ENDPOINT_TYPES: frozenset[str] = frozenset(
    {"openai", "openai-response", "anthropic", "anthropic-compatible", "gemini"}
)
"""What B.AI, OrcaRouter and TokenRouter call an endpoint a chat turn can use.

Transcribed verbatim: three stations share this list in pi-free, and adding a
name to it would keep rows that none of the three can serve.
"""

MEDIA_TAGS: frozenset[str] = frozenset({"image-gen", "video-gen", "tts", "stt"})
"""DeepInfra's own labels for entries that are not chat.

Tag first, substring second: the tags are what the station says the row *is*,
and a substring can only ever be a guess. pi-free's comment is that the tags
exist precisely so the guess is unnecessary.
"""

NON_TEXT_OUTPUTS: frozenset[str] = frozenset({"image", "audio", "video", "speech"})
"""FastRouter's exclusion list, which is stricter than the shared rule.

The shared rule keeps a row that produces text among other things;
FastRouter's free view must not, because an image-and-text hybrid is a
generator that happens to emit a caption. Its pi-free comment names the
family: ``gemini-*-image``, ``prompt-to-audio``.
"""

_ID_SKIPPED_ON_DEEPINFRA = ("embed", "rerank", "whisper", "speech")
"""The fallback for rows DeepInfra left untagged — deliberately crude.

Begins-with and contains are not distinguished upstream either; what matters
is that this only ever runs when ``metadata.tags`` had nothing to say.
"""


# -- reading a row without being able to trip over it ------------------------ #


def _get(row: Row, *path: str) -> Any:
    """One field, following a short path, or None when any hop is missing."""
    node: Any = row
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _strings(row: Row, *path: str) -> list[str]:
    """A field that should be a list of strings, as an always-safe list."""
    value = _get(row, *path)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _text_output_kept(modalities: list[str]) -> bool:
    """The shared rule: no stated output, or text among them, is a chat target.

    Empty means *unstated*, not *none*. Stations with older or partial rows
    would otherwise lose every model they have, and pi-free's note says the
    same: keep them, over-filtering is the expensive direction.
    """
    return not modalities or "text" in modalities


# -- the ten stations that say something pi-free reads ----------------------- #


def _is_chat_row_for_station(station: str, row: Row) -> bool:
    if station == "llm7":
        # hoshimi's own judgement, not a transcription — this station's rows
        # carry their own `model_type`, and the modalities beside it are the
        # wrong evidence: `whisper-large-v3` outputs text because it is
        # speech-to-text on a different endpoint, and `gpt-image-2` outputs
        # text *and* an image. Absent keeps the row, as everywhere else: a
        # field the station stops publishing is a schema change, and losing a
        # whole catalogue to it is invisible.
        declared = _get(row, "model_type")
        return declared is None or declared == "chat"

    if station == "agnes":
        ident = str(_get(row, "id") or "").lower()
        # Not "is this a generation model": Agnes puts `image`/`video` in the
        # id of exactly those, and `agnes-image-2.1-flash` is a paid
        # generator whose name otherwise reads like the free flash class.
        return "image" not in ident and "video" not in ident

    if station == "bai":
        endpoints = _strings(row, "supported_endpoint_types")
        # No endpoint info is not a refusal — pi-free's comment ties this to
        # the same fallback TokenRouter has.
        return not endpoints or any(name in CHAT_ENDPOINT_TYPES for name in endpoints)

    if station == "deepinfra":
        if any(tag in MEDIA_TAGS for tag in _strings(row, "metadata", "tags")):
            return False
        ident = str(_get(row, "id") or "").lower()
        return not any(word in ident for word in _ID_SKIPPED_ON_DEEPINFRA)

    if station == "fastrouter":
        modalities = _strings(row, "architecture", "output_modalities")
        if not _text_output_kept(modalities):
            return False
        # Then the stricter pass: text *plus* image is still a generator.
        return not any(name in NON_TEXT_OUTPUTS for name in modalities)

    if station == "novita":
        # The one row here that drops on an absent field, because upstream
        # reads those two as the station's own contract: a Novita row is a
        # usable endpoint when it says so. C26's shrink guard is what stops a
        # schema change from turning this into an empty catalogue.
        if _get(row, "status") != 1 or _get(row, "model_type") != "chat":
            return False
        return re.match("ai_infer_test", str(_get(row, "id") or "")) is None

    if station == "orcarouter":
        if not _text_output_kept(_strings(row, "architecture", "output_modalities")):
            return False
        endpoints = _strings(row, "supported_endpoint_types")
        return not endpoints or any(name in CHAT_ENDPOINT_TYPES for name in endpoints)

    if station == "routeway":
        if _get(row, "available") is False:
            return False
        if _get(row, "type") == "chat.completions":
            return True
        return "/v1/chat/completions" in _strings(row, "endpoints")

    if station == "tokenrouter":
        tags = str(_get(row, "tags") or "").lower()
        if "text" in tags:
            return True
        if any(name in tags for name in ("image", "video", "audio")):
            return False
        # The asymmetry is upstream's, not a slip: here an empty endpoint list
        # means *keep nothing*, where B.AI's identical shape means *keep*.
        # TokenRouter reaches this branch only when its tags said nothing, and
        # then its endpoints are the only evidence there is.
        return any(name in CHAT_ENDPOINT_TYPES for name in _strings(row, "supported_endpoint_types"))

    if station == "xkiro":
        if not _get(row, "id"):
            return False
        modality = _get(row, "modality")
        # Python's None stands for both "absent" and "null"; upstream keeps
        # the row for either and drops only on a named non-chat modality.
        return modality is None or modality == "chat"

    if station == "zenmux":
        # Top-level ``output_modalities``, unlike OrcaRouter's and
        # FastRouter's nested one. Reading the wrong path would keep every row
        # and look exactly like having no filter at all.
        return _text_output_kept(_strings(row, "output_modalities"))

    return True


# -- the two questions a caller can ask -------------------------------------- #


def station_for_base(base_url: str) -> str | None:
    """The station this address is, or None for one the table does not carry.

    The address is compared as ``agg`` uses it — trailing slashes stripped —
    so ``preset: zenmux`` and a written-out ``base_url`` identify the station
    the same way.
    """
    wanted = (base_url or "").strip().rstrip("/")
    if not wanted:
        return None
    for preset in PRESETS:
        if preset.base_url.rstrip("/") == wanted:
            return preset.id
    return None


def is_chat_row(station: str | None, row: Row) -> bool:
    """Should this catalogue row be offered to a caller as a chat model?

    An address with no filter says yes to everything, which is what this did
    for every station before the table existed. The membership test is the
    gate rather than a missing branch: a station that is listed above and has
    no case in the chain would otherwise keep everything and look exactly like
    a station nobody judged.
    """
    if not isinstance(row, dict):
        return False
    if station not in FILTERED_STATIONS:
        return True
    return _is_chat_row_for_station(station, row)


FILTERED_STATIONS: frozenset[str] = frozenset(
    {
        "agnes",
        "bai",
        "deepinfra",
        "fastrouter",
        "llm7",
        "novita",
        "orcarouter",
        "routeway",
        "tokenrouter",
        "xkiro",
        "zenmux",
    }
)
"""The stations with a catalogue predicate, and no others.

Eleven of the twenty-two presets: ten transcribed from pi-free, plus ``llm7``,
which upstream never reads and hoshimi does. The remaining eleven are recorded
as a list rather than inferred from the predicates above so that a station
quietly gaining (or losing) its filter has to be written down: nothing here can
fail loudly on its own, because a filter that vanishes simply stops filtering.
"""
