"""Which rows of a station's catalogue are chat models.

The upstream record is pi-free's per-provider fetchers — `agnes-models.ts`,
`deepinfra.ts`, `novita.ts`, `routeway.ts`, `tokenrouter.ts`,
`orcarouter.ts`, `xkiro.ts`, `zenmux-models.ts`, `bai.ts`,
`fastrouter-models.ts` and the shared `model-fetcher.ts` — in a clone that is
gitignored, so every row below is transcribed rather than imported, except the
seven under `OWN_CASES`, which are hoshimi's own judgement of a station
pi-free never reads its catalogue for. `UPSTREAM_FILTERED` and `OWN_FILTERED`
keep the two apart, because a predicate nobody can trace to upstream should
never be able to look like one somebody can.

The table is locked as a whole rather than spot-checked, and the cases are
grouped by station instead of by assertion, for two reasons that only show up
when the table drifts: a station that quietly loses its predicate keeps every
row and looks perfectly healthy, and two adjacent stations deliberately
disagree about the same field (B.AI keeps a row with no endpoint list,
TokenRouter drops it) so a test written against "the rule" rather than against
the station would pass while one of them inverted.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hoshimi.core.chat_catalog import (
    FILTERED_STATIONS,
    is_chat_row,
    station_for_base,
)
from hoshimi.core.presets import PRESETS
from hoshimi.core.providers.agg import AggProvider
from hoshimi.core.providers.base import Account

Row = dict[str, Any]

# The stations pi-free reads a catalogue predicate from, and no others.
UPSTREAM_FILTERED = {
    "agnes",
    "bai",
    "deepinfra",
    "fastrouter",
    "novita",
    "orcarouter",
    "routeway",
    "tokenrouter",
    "xkiro",
    "zenmux",
}

# hoshimi's own judgement. pi-free never reads LLM7's catalogue — it registers
# three routing selectors built locally instead — so there is nothing to
# transcribe here and the split is kept structural rather than in a comment:
# a reader must be able to tell which ten are somebody else's evidence.
OWN_FILTERED = {"llm7"}

# Rows that are not chat targets under anyone's schema — used to show what a
# station without a predicate does with one.
NOT_A_CHAT_TARGET = [
    {"id": "some/image-model", "output_modalities": ["image"]},
    {"id": "bge-reranker-v2", "metadata": {"tags": ["image-gen"]}},
    {"id": "text-embedding-3-small", "supported_endpoint_types": ["embeddings"]},
]


# -- the transcribed table ---------------------------------------------------- #

CASES: list[tuple[str, Row, bool]] = [
    # -- agnes: the id names the class -------------------------------------- #
    # `agnes-image-2.1-flash` is a paid generator whose name reads like the
    # free flash class; only the full id tells them apart.
    ("agnes", {"id": "agnes-2.5-flash"}, True),
    ("agnes", {"id": "agnes-2.0-flash"}, True),
    ("agnes", {"id": "agnes-2.5-pro"}, True),
    ("agnes", {"id": "agnes-image-2.1-flash"}, False),
    ("agnes", {"id": "AGNES-VIDEO-2.5"}, False),
    # -- bai: an endpoint list, when there is one --------------------------- #
    ("bai", {"id": "deepseek-v4-flash", "supported_endpoint_types": ["openai"]}, True),
    ("bai", {"id": "deepseek-v4-flash", "supported_endpoint_types": ["openai", "rerank"]}, True),
    ("bai", {"id": "deepseek-v4-flash", "supported_endpoint_types": []}, True),
    ("bai", {"id": "deepseek-v4-flash"}, True),
    ("bai", {"id": "bge-en-v1.5", "supported_endpoint_types": ["embeddings"]}, False),
    # -- deepinfra: the station's own tags, then the id -------------------- #
    ("deepinfra", {"id": "meta/llama-3.1-8b", "metadata": {"tags": ["chat"]}}, True),
    ("deepinfra", {"id": "deepseek-ai/DeepSeek-V4-Flash"}, True),
    ("deepinfra", {"id": "stabilityai/stable-diffusion", "metadata": {"tags": ["image-gen"]}}, False),
    ("deepinfra", {"id": "openai/whisper-large-v3"}, False),
    ("deepinfra", {"id": "Qwen/Qwen3-Embedding-8B"}, False),
    ("deepinfra", {"id": "my/speech-lite"}, False),
    # -- fastrouter: text output, then no media output at all -------------- #
    # The second pass is stricter than the shared rule on purpose: a hybrid
    # produces text *and* an image, so the shared rule would keep a generator.
    ("fastrouter", {"id": "deepseek/deepseek-v4-flash", "architecture": {"output_modalities": ["text"]}}, True),
    ("fastrouter", {"id": "kimi-k2.7-code", "architecture": {"output_modalities": []}}, True),
    ("fastrouter", {"id": "glm-5.2"}, True),
    ("fastrouter", {"id": "black-forest/flux", "architecture": {"output_modalities": ["image"]}}, False),
    (
        "fastrouter",
        {"id": "google/gemini-flash-image", "architecture": {"output_modalities": ["text", "image"]}},
        False,
    ),
    ("fastrouter", {"id": "xiaomi/tts", "architecture": {"output_modalities": ["text", "audio"]}}, False),
    # FastRouter reads the nested path; a row that only states it at the top
    # level is not its schema, and treating it as one would drop models on
    # evidence the station never offered.
    ("fastrouter", {"id": "glm-5.2", "output_modalities": ["image"]}, True),
    # -- novita: the station's own contract, and its smoke tests ----------- #
    # The one station that reads an *absent* field as a refusal: its rows say
    # `status` and `model_type`, and a row that does not is not one of them.
    ("novita", {"id": "deepseek/deepseek-v4-flash", "status": 1, "model_type": "chat"}, True),
    ("novita", {"id": "glm-5.2", "status": 0, "model_type": "chat"}, False),
    ("novita", {"id": "bge-m3", "status": 1, "model_type": "embedding"}, False),
    ("novita", {"id": "ai_infer_test_1", "status": 1, "model_type": "chat"}, False),
    ("novita", {"id": "deepseek/deepseek-v4-flash"}, False),
    # -- orcarouter: output first, endpoints second ------------------------- #
    ("orcarouter", {"id": "glm-5.2", "architecture": {"output_modalities": ["text"]}, "supported_endpoint_types": ["gemini"]}, True),
    ("orcarouter", {"id": "glm-5.2", "architecture": {"output_modalities": ["image"]}}, False),
    # The shared rule only: text *among* the outputs keeps the row. The extra
    # pass that drops media hybrids is FastRouter's alone — putting it here
    # would have dropped rows OrcaRouter never said anything about.
    ("orcarouter", {"id": "glm-5.2", "architecture": {"output_modalities": ["text", "audio"]}}, True),
    ("orcarouter", {"id": "glm-5.2"}, True),
    ("orcarouter", {"id": "bge-en-v1.5", "supported_endpoint_types": ["embeddings"]}, False),
    # -- routeway: available, then a chat endpoint -------------------------- #
    ("routeway", {"id": "glm-5.2", "type": "chat.completions"}, True),
    ("routeway", {"id": "glm-5.2", "type": "responses", "endpoints": ["/v1/chat/completions"]}, True),
    ("routeway", {"id": "glm-5.2", "available": False, "type": "chat.completions"}, False),
    ("routeway", {"id": "glm-5.2", "available": False, "endpoints": ["/v1/chat/completions"]}, False),
    ("routeway", {"id": "bge-m3", "type": "responses", "endpoints": ["/v1/embeddings"]}, False),
    ("routeway", {"id": "glm-5.2"}, False),
    # -- tokenrouter: tags first, endpoints only when the tags say nothing -- #
    ("tokenrouter", {"id": "glm-5.2", "tags": "text"}, True),
    ("tokenrouter", {"id": "glm-5.2", "tags": "text,image"}, True),
    ("tokenrouter", {"id": "flux", "tags": "image"}, False),
    ("tokenrouter", {"id": "glm-5.2", "tags": "", "supported_endpoint_types": ["openai"]}, True),
    ("tokenrouter", {"id": "bge-m3", "tags": "", "supported_endpoint_types": ["embeddings"]}, False),
    # The asymmetry is upstream's: here an empty endpoint list means *keep
    # nothing*, where B.AI's identical shape means *keep*. TokenRouter only
    # reaches this branch when its tags said nothing at all.
    ("tokenrouter", {"id": "glm-5.2", "tags": ""}, False),
    # -- xkiro: a named modality that is not chat --------------------------- #
    ("xkiro", {"id": "claude-sonnet-5"}, True),
    ("xkiro", {"id": "claude-sonnet-5", "modality": "chat"}, True),
    ("xkiro", {"id": "flux-dev", "modality": "image"}, False),
    ("xkiro", {"id": "flux-dev", "modality": None}, True),
    ("xkiro", {"id": "", "modality": "chat"}, False),
    # -- zenmux: output modalities, at the top level ------------------------ #
    ("zenmux", {"id": "glm-5.2", "output_modalities": ["text"]}, True),
    ("zenmux", {"id": "glm-5.2", "output_modalities": []}, True),
    ("zenmux", {"id": "glm-5.2"}, True),
    ("zenmux", {"id": "flux", "output_modalities": ["image"]}, False),
    # ZenMux states it at the top level; a row that only nests it is another
    # station's schema, and believing it would drop models on a field name
    # this gateway should not be reading here.
    ("zenmux", {"id": "flux", "architecture": {"output_modalities": ["image"]}}, True),
]


# -- hoshimi's own judgement -------------------------------------------------- #

OWN_CASES: list[tuple[str, Row, bool]] = [
    # LLM7 types every row itself, and it is the *only* field that answers
    # here. The two rows below are why the shared text-output rule cannot be
    # borrowed: both produce text, and neither is a chat target.
    ("llm7", {"id": "glm-5.2", "model_type": "chat"}, True),
    ("llm7", {"id": "DeepSeek-V4.1-Flash", "model_type": "chat"}, True),
    # Speech-to-text on another endpoint — output is `text`.
    ("llm7", {"id": "whisper-large-v3", "model_type": "audio_to_text", "modalities": {"output": ["text"]}}, False),
    # A generator that emits a caption — output is `text, image`.
    ("llm7", {"id": "gpt-image-2", "model_type": "image", "modalities": {"output": ["text", "image"]}}, False),
    ("llm7", {"id": "kling-v3.0-pro", "model_type": "video"}, False),
    # The one row where the station names a type this gateway cannot read:
    # `systemone`, output `json`, its own endpoint schema. The station said
    # what it is; it did not say chat, and an unreadable answer is not a
    # missing one — it is a declaration we are not party to.
    ("llm7", {"id": "jev-latest", "model_type": "systemone", "modalities": {"output": ["json"]}}, False),
    # Absent keeps, as everywhere: a field the station stops publishing must
    # not be able to empty a catalogue on its own.
    ("llm7", {"id": "glm-5.2"}, True),
]


@pytest.mark.parametrize(
    "station, row, expected",
    CASES + OWN_CASES,
    ids=[f"{s}-{i}" for i, (s, _, _) in enumerate(CASES + OWN_CASES)],
)
def test_a_row_is_read_the_way_its_station_reads_it(station: str, row: Row, expected: bool) -> None:
    assert is_chat_row(station, row) is expected


def test_the_membership_is_exactly_what_each_side_judged() -> None:
    """Ten transcribed, one of ours, and cases covering both.

    Without the last two lines, a station could be added to the table with no
    case behind it — the gate would open and no test would be any the wiser.
    And without the first, somebody else's ten and hoshimi's one would be
    indistinguishable, so an invented predicate could pass as transcribed.
    """
    assert UPSTREAM_FILTERED | OWN_FILTERED == FILTERED_STATIONS
    assert not UPSTREAM_FILTERED & OWN_FILTERED
    assert {station for station, _, _ in CASES} == UPSTREAM_FILTERED
    assert {station for station, _, _ in OWN_CASES} == OWN_FILTERED
    assert {station for station, _, _ in CASES + OWN_CASES} == FILTERED_STATIONS


def test_a_station_nobody_judges_keeps_every_row() -> None:
    """Including rows another station would drop on an identical field.

    Eleven of the twenty-two presets are here on purpose: pi-free reads no
    predicate for them either, and a rule invented for one station's schema
    would be somebody else's evidence.
    """
    unjudged = {preset.id for preset in PRESETS} - FILTERED_STATIONS
    assert len(unjudged) == 11
    for station in sorted(unjudged):
        for row in NOT_A_CHAT_TARGET:
            assert is_chat_row(station, row), f"{station} judged {row}"


def test_an_address_the_table_does_not_carry_gets_no_filter() -> None:
    """A relay, a proxy, a station added next year — all unjudged."""
    assert station_for_base("https://openrouter.ai/api/v1") is None
    assert station_for_base("") is None
    assert is_chat_row(station_for_base("https://openrouter.ai/api/v1"), NOT_A_CHAT_TARGET[0]) is True


def test_the_address_decides_the_station_not_the_name_on_the_line() -> None:
    """`preset: zenmux` beside a `base_url` elsewhere is not ZenMux's list."""
    assert station_for_base("https://zenmux.ai/api/v1") == "zenmux"
    assert station_for_base(" https://zenmux.ai/api/v1/ ") == "zenmux"
    # The name would say zenmux; the address says it is not.
    assert station_for_base("https://my-relay.example/v1") is None


def test_every_station_address_in_the_table_resolves_to_itself() -> None:
    for preset in PRESETS:
        assert station_for_base(preset.base_url) == preset.id


def test_a_row_that_is_not_an_object_is_not_a_model() -> None:
    assert is_chat_row("zenmux", "glm-5.2") is False  # type: ignore[arg-type]
    assert is_chat_row(None, []) is False  # type: ignore[arg-type]


def test_an_unknown_station_is_not_given_someone_elses_schema() -> None:
    assert is_chat_row(None, NOT_A_CHAT_TARGET[0]) is True
    assert is_chat_row("", NOT_A_CHAT_TARGET[0]) is True


# -- the same fact at the lane ------------------------------------------------ #


def _agg(account: Account, catalogue: Row | list[Any]) -> AggProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=catalogue)

    return AggProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account])


def _account(**extra: Any) -> Account:
    data: dict[str, Any] = {"id": "s1", "api_key": "sk-test"}
    data.update(extra)
    return Account(data, provider_id="agg")


def test_the_lane_offers_only_the_rows_the_station_can_actually_chat_with() -> None:
    provider = _agg(
        _account(preset="novita"),
        {
            "data": [
                {"id": "deepseek/deepseek-v4-flash", "status": 1, "model_type": "chat"},
                {"id": "bge-m3", "status": 1, "model_type": "embedding"},
                {"id": "ai_infer_test_1", "status": 1, "model_type": "chat"},
            ]
        },
    )
    assert provider.models() == ["deepseek/deepseek-v4-flash"]


def test_an_account_of_an_unknown_station_lists_exactly_what_came_back() -> None:
    provider = _agg(_account(base_url="https://my-relay.example/v1"), {"data": [{"id": "a"}, {"id": "b"}]})
    assert provider.models() == ["a", "b"]


def test_a_declared_catalogue_is_the_operator_speaking_and_is_never_filtered() -> None:
    """The one place the table must not reach.

    An account that names its ids has already said which ones it means, and
    `agnes-image-2.1-flash` in a declaration is a request, not a catalogue
    accident.
    """
    provider = _agg(_account(preset="agnes", models=["agnes-image-2.1-flash"]), {"data": []})
    assert provider.models() == ["agnes-image-2.1-flash"]


def test_the_plain_id_path_and_the_filtered_path_see_the_same_entries() -> None:
    """`model_ids(model_rows(x))` is the unfiltered answer the filter is subtracted from.

    They are separate functions so they can be composed; this is what stops
    them from quietly disagreeing about which entries exist.
    """
    from hoshimi.core.providers.openai_compat import model_ids, model_rows

    data: Row = {"data": [{"id": "a", "status": 1}, {"id": "b"}, {"no": "id"}, "raw", {"id": 7}]}
    rows = model_rows(data)
    assert model_ids(rows) == ["a", "b"]
    assert model_ids([row for row in rows if is_chat_row("zenmux", row)]) == ["a", "b"]
    # A bare array of ids, the other shape every station uses, reads the same.
    assert model_ids(model_rows([{"id": "a"}, {"id": 7}, "raw"])) == ["a"]
