"""models.dev: what a model is, and the limits this module refuses to borrow.

The upstream record is pi-free's `lib/model-metadata.ts`, in a clone that is
gitignored. pi-free enriches context window, output ceiling, reasoning, vision
and the thinking menu from the catalogue. This module takes two of those five,
and the tests below are mostly about the three it leaves behind — because the
interesting fact about porting this feature is that half of it is unsafe to
port, and a test that only proved the happy path would never say so.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hoshimi.core.catalog import (
    FALLBACK_CONTEXT_WINDOW,
    FALLBACK_MAX_OUTPUT,
    ModelSpec,
    default_spec,
)
from hoshimi.core.effort import apply_effort
from hoshimi.core.modelsdev import ModelsDevStore, _index_of, enrich_spec
from hoshimi.core.providers.agg import AggProvider
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.lobsterai import LobsteraiProvider

# The ids Lobsterai advertises (its own `fallback_model_list`), and how the
# catalogue answered for each when this snapshot was taken.
LOBSTERAI_SNAPSHOT = {
    "deepseek-v4-flash": (True, False),
    "deepseek-v4-pro": (True, False),
    "MiniMax-M3": (True, True),
    "MiniMax-M2.7": (True, False),
    "qwen3.7-max": (True, False),
    "qwen3.7-plus": (True, True),
    "qwen3.6-plus": (True, True),
    "kimi-k2.7-code": (True, True),
    "kimi-k2.7-code-highspeed": (True, True),
    "kimi-k2.6": (True, True),
    "kimi-k2.5": (True, True),
    "doubao-seed-2-1-pro-260628": (True, True),
    "doubao-seed-2-1-turbo-260628": (True, True),
    "doubao-seed-2-0-code-preview-260215": (False, False),
    "glm-5.2": (True, False),
    "glm-5.1": (True, False),
    "glm-5v-turbo": (True, True),
    "glm-5": (True, False),
}


def _provider_with(ids: list[str], *, name: str = "Test") -> Any:
    """A lane with no capability table at all, so only defaults stand behind it."""

    class Lane:
        id = "testlane"
        display_name = name

        def __init__(self) -> None:
            self._ids = ids

        def models(self) -> list[str]:
            return list(self._ids)

    return Lane()


# -- the snapshot ------------------------------------------------------------ #


def test_the_snapshot_is_the_whole_set_there_is() -> None:
    """Transcribed, not spot-checked: a missing id is an inert model, not a gap.

    The ids are Lobsterai's own fallback table — the lane that has no capability
    table and is the reason this module exists.
    """
    from hoshimi.core.modelsdev import SNAPSHOT

    assert SNAPSHOT == LOBSTERAI_SNAPSHOT


def test_the_snapshot_says_which_id_it_could_not_answer_for() -> None:
    """A dated upstream id nobody has published yet stays inert.

    The alternative — borrowing a sibling's answer — is how a model ends up
    reporting a context window it never had.
    """
    from hoshimi.core.modelsdev import SNAPSHOT

    assert "qwen3.5-plus-2026-04-20" not in SNAPSHOT
    assert "qwen3.5-plus-2026-04-20" in LobsteraiProvider.fallback_model_list


def test_every_snapshot_id_is_one_the_lane_actually_serves() -> None:
    from hoshimi.core.modelsdev import SNAPSHOT

    assert set(SNAPSHOT) <= set(LobsteraiProvider.fallback_model_list)


# -- the flattening ---------------------------------------------------------- #


def test_the_first_provider_to_publish_an_id_wins() -> None:
    """pi-free's rule, kept — and the reason limits are never read from here."""
    index = _index_of(
        {
            "reseller-a": {"models": {"glm-5.2": {"reasoning": False, "modalities": {"input": ["text"]}}}},
            "reseller-b": {"models": {"glm-5.2": {"reasoning": True, "modalities": {"input": ["text", "image"]}}}},
        }
    )
    assert index == {"glm-5.2": (False, False)}


def test_a_model_without_an_input_modality_is_not_vision() -> None:
    index = _index_of({"p": {"models": {"m": {"reasoning": True}}}})
    assert index == {"m": (True, False)}


def test_video_is_not_image() -> None:
    """`attachment`/video do not make a model vision-capable on this wire."""
    index = _index_of({"p": {"models": {"m": {"reasoning": False, "modalities": {"input": ["text", "video"]}}}}})
    assert index == {"m": (False, False)}


def test_a_malformed_catalogue_flattens_to_nothing_rather_than_raising() -> None:
    for payload in (None, [], "x", 7, {"p": None}, {"p": {}}, {"p": {"models": None}}, {"p": {"models": {"m": "x"}}}):
        assert _index_of(payload) == {}


# -- what is taken ----------------------------------------------------------- #


def test_the_two_endpoint_independent_facts_are_taken() -> None:
    store = ModelsDevStore()
    store.preload({"glm-5v-turbo": (True, True)})
    facts = store.facts("glm-5v-turbo")
    assert facts is not None
    assert facts.reasoning is True and facts.vision is True
    assert facts.source == "live"


def test_an_inert_spec_is_filled() -> None:
    store = ModelsDevStore()
    store.preload({"some-model": (True, True)})
    try:
        original = default_spec("some-model", "testlane")
        assert original.reasoning is False and original.vision is False
        with _patched(store):
            filled = enrich_spec(original)
        assert filled.reasoning is True and filled.vision is True
        # The identity is untouched: this is not a licence to invent one.
        assert filled.id == original.id and filled.provider == original.provider
    finally:
        store.reset()


# -- what is refused --------------------------------------------------------- #


def test_the_limits_are_never_borrowed_from_another_endpoint() -> None:
    """The whole safety property of this module.

    The catalogue will cheerfully hand over a 1M context window for an id a
    reseller publishes; that number describes someone else's endpoint, and
    `max_output` would reach the wire through the token budget.
    """
    store = ModelsDevStore()
    store.preload({"glm-5.2": (True, False)})
    try:
        with _patched(store):
            spec = enrich_spec(default_spec("glm-5.2", "testlane"))
        assert spec.context_window == FALLBACK_CONTEXT_WINDOW
        assert spec.max_output == FALLBACK_MAX_OUTPUT
        assert spec.efforts == ()
        assert spec.effort_patch is None
    finally:
        store.reset()


def test_the_thinking_menu_is_not_taken_from_a_catalogue_that_cannot_vouch_for_a_station() -> None:
    """Every menu Hoshimi ships was verified against that station's own wire."""
    store = ModelsDevStore()
    store.preload({"glm-5.2": (True, False)})
    try:
        with _patched(store):
            spec = enrich_spec(default_spec("glm-5.2", "testlane"))
        assert spec.effort_default is None and spec.effort_off_id is None
    finally:
        store.reset()


def test_a_declared_id_is_left_alone_whatever_the_catalogue_says() -> None:
    """The gate is the declaration, not the shape of the spec.

    An operator who wrote `model_specs` for an id, or a lane with a vetted
    table, spoke for it — and this module is not a better source than they are,
    even when it disagrees.
    """
    store = ModelsDevStore()
    store.preload({"glm-5.2": (True, True)})
    try:
        with _patched(store):
            for variant in (
                ModelSpec(id="glm-5.2", provider="testlane", name="GLM 5.2"),
                ModelSpec(id="glm-5.2", provider="testlane", name="GLM 5.2", context_window=200000),
                ModelSpec(id="glm-5.2", provider="testlane", name="GLM 5.2", reasoning=False),
                ModelSpec(id="glm-5.2", provider="testlane", name="GLM 5.2", efforts=("low", "high"), effort_default="high"),
                ModelSpec(id="glm-5.2", provider="testlane", name="GLM 5.2", wire="responses", region_sensitive=True),
            ):
                assert enrich_spec(variant, declared=True) is variant
    finally:
        store.reset()


def test_the_agg_lane_does_not_fill_an_id_the_account_declared() -> None:
    """`model_specs` is a person saying what an id may do here."""
    provider = AggProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="{}"))),
        [Account({"id": "s1", "base_url": "https://station.example/v1", "api_key": "sk", "models": ["glm-5.2"]}, provider_id="agg")],
        {"model_specs": {"glm-5.2": {"reasoning": False, "vision": False, "context_window": 200000}}},
    )
    spec = provider.specs()["glm-5.2"]
    assert spec.reasoning is False and spec.vision is False
    assert spec.context_window == 200000


def test_the_agg_lane_does_fill_an_id_nobody_declared() -> None:
    """The other half of the rule: a plain listing is not a claim."""
    provider = AggProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="{}"))),
        [Account({"id": "s1", "base_url": "https://station.example/v1", "api_key": "sk", "models": ["glm-5.2"]}, provider_id="agg")],
    )
    spec = provider.specs()["glm-5.2"]
    assert spec.reasoning is True and spec.vision is False
    # Still never a borrowed limit.
    assert spec.context_window == FALLBACK_CONTEXT_WINDOW
    assert spec.max_output == FALLBACK_MAX_OUTPUT


def test_a_spec_carrying_a_capacity_is_left_alone() -> None:
    """A capacity is a declaration on its own, with no other field involved."""
    store = ModelsDevStore()
    store.preload({"m": (True, True)})
    try:
        varied = ModelSpec(id="m", provider="testlane", name="M", context_window=204800)
        assert enrich_spec(varied, declared=True) is varied
    finally:
        store.reset()


def test_an_id_nobody_can_answer_for_stays_inert() -> None:
    store = ModelsDevStore()
    assert store.facts("never-heard-of-it") is None
    spec = default_spec("never-heard-of-it", "testlane")
    assert enrich_spec(spec) is spec


def test_a_model_that_reasons_about_nothing_gets_no_vision_flag_alone() -> None:
    """Both facts false is nothing to report; the spec is returned untouched."""
    store = ModelsDevStore()
    store.preload({"m": (False, False)})
    try:
        spec = default_spec("m", "testlane")
        assert enrich_spec(spec) is spec
    finally:
        store.reset()


def test_a_blank_id_is_not_a_lookup() -> None:
    store = ModelsDevStore()
    assert store.facts("") is None
    assert store.facts("   ") is None


# -- where the facts came from ----------------------------------------------- #


def test_the_snapshot_answers_before_the_network_is_involved() -> None:
    """An install with no egress still gets real facts, on the first lookup."""
    store = ModelsDevStore()
    facts = store.facts("glm-5.2")
    assert facts is not None and facts.source == "snapshot"


def test_a_live_catalogue_improves_on_the_snapshot() -> None:
    store = ModelsDevStore()
    store.preload({"glm-5.2": (True, True)})
    try:
        facts = store.facts("glm-5.2")
        assert facts is not None and facts.source == "live" and facts.vision is True
    finally:
        store.reset()


def test_an_id_the_snapshot_missed_is_answered_live() -> None:
    """The point of fetching at all: a station ships a model between releases."""
    store = ModelsDevStore()
    store.preload({"brand-new-model": (True, False)})
    try:
        facts = store.facts("brand-new-model")
        assert facts is not None and facts.reasoning is True
    finally:
        store.reset()


def test_a_live_catalogue_cannot_overwrite_a_snapshot_id_it_does_not_carry() -> None:
    store = ModelsDevStore()
    store.preload({"unrelated": (True, True)})
    try:
        facts = store.facts("glm-5.2")
        assert facts is not None and facts.source == "snapshot"
    finally:
        store.reset()


# -- it owes the request path nothing ---------------------------------------- #


def test_a_failing_catalogue_is_invisible(monkeypatch: pytest.MonkeyPatch) -> None:
    """No egress, a 403, a truncated body: the snapshot stands and nobody notices.

    models.dev rejects a request without a real User-Agent with a 403, which is
    exactly the kind of ordinary failure that must not reach a caller.
    """
    store = ModelsDevStore()

    def explode() -> dict[str, tuple[bool, bool]]:
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(store, "_fetch_index", explode)
    facts = store.facts("glm-5.2")
    assert facts is not None and facts.source == "snapshot"


def test_a_lookup_does_not_wait_for_the_network() -> None:
    """The request path pays a dict lookup; the fetch happens after it returns.

    The refresh here blocks on an event that is only released once `facts()`
    has returned, so a lookup that started waiting would deadlock this test
    into its timeout rather than pass silently.
    """
    import threading

    release = threading.Event()

    def block() -> dict[str, tuple[bool, bool]]:
        release.wait(10)
        return {}

    store = ModelsDevStore()
    store._fetch_index = block  # type: ignore[method-assign]
    try:
        facts = store.facts("glm-5.2")
        assert facts is not None and facts.source == "snapshot"
    finally:
        release.set()


def test_a_real_lane_without_a_table_gets_its_models_filled() -> None:
    """The whole chain: Lobsterai's default specs, through the process singleton.

    This lane lists ids and nothing else, so before this module every one of its
    models reported `reasoning=False`. The bundled snapshot is what answers when
    there is no egress — which is the case here, deliberately.
    """
    provider = LobsteraiProvider(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="{}"))), [])
    specs = provider.specs()

    reasoning = sorted(model_id for model_id, spec in specs.items() if spec.reasoning)
    assert "glm-5.2" in reasoning
    assert "MiniMax-M3" in reasoning
    # The dated id the catalogue has never heard of stays what it was.
    assert specs["qwen3.5-plus-2026-04-20"].reasoning is False

    # Vision is taken from the model's input modality, not its name: `glm-5v`
    # takes an image, `glm-5` does not, and `deepseek-v4-pro` does not either.
    assert specs["glm-5v-turbo"].vision is True
    assert specs["glm-5"].vision is False
    assert specs["deepseek-v4-pro"].vision is False

    # And the limits are still the gateway's own defaults, never a reseller's.
    assert specs["glm-5.2"].context_window == FALLBACK_CONTEXT_WINDOW
    assert specs["glm-5.2"].max_output == FALLBACK_MAX_OUTPUT
    assert specs["glm-5.2"].efforts == ()


# -- the wire after filling -------------------------------------------------- #


def test_a_filled_spec_still_leaves_a_body_without_a_level_alone() -> None:
    """Turning `reasoning` on must not make the gateway start editing turns."""
    store = ModelsDevStore()
    store.preload({"m": (True, False)})
    try:
        with _patched(store):
            spec = enrich_spec(default_spec("m", "testlane"))
        body = {"model": "m", "messages": [], "max_tokens": 5000}
        assert apply_effort(spec, body) == body
    finally:
        store.reset()


def test_a_filled_spec_strips_the_effort_fields_it_cannot_vouch_for() -> None:
    """With no menu, the gateway strips rather than injects — and only lowers."""
    store = ModelsDevStore()
    store.preload({"m": (True, False)})
    try:
        with _patched(store):
            spec = enrich_spec(default_spec("m", "testlane"))
        out = apply_effort(spec, {"model": "m", "messages": [], "reasoning_effort": "high", "max_tokens": 5000})
        assert "reasoning_effort" not in out and "reasoning" not in out
        assert out["max_tokens"] <= 5000
    finally:
        store.reset()


class _patched:
    """Point `enrich_spec`'s store at one under test."""

    def __init__(self, store: ModelsDevStore) -> None:
        self.store = store

    def __enter__(self) -> None:
        import hoshimi.core.modelsdev as module

        self._was = module.MODELSDEV
        module.MODELSDEV = self.store

    def __exit__(self, *exc: object) -> None:
        import hoshimi.core.modelsdev as module

        module.MODELSDEV = self._was
