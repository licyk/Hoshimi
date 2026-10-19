"""Inventory integrity without depending on reference checkouts."""

from hoshimi.core.presets import PRESET_BY_ID
from hoshimi.core.provider_definitions import PROVIDER_BY_ID, definition_for
from hoshimi.core.provider_sources import FCC_PROVIDERS, PI_FREE_PROVIDERS, PI_PROVIDERS
from hoshimi.core.providers import PROVIDER_CLASSES


def test_every_reference_registration_has_one_inventory_entry():
    assert len(PI_PROVIDERS) == 42
    assert len({d.source_id for d in PI_PROVIDERS}) == 42
    assert len(PI_FREE_PROVIDERS) == 25
    assert len({d.source_id for d in PI_FREE_PROVIDERS}) == 25
    for definition in PI_FREE_PROVIDERS:
        if definition.coverage == "agg":
            assert definition.base_url == PRESET_BY_ID[definition.source_id].base_url
        else:
            assert definition.id in PROVIDER_CLASSES


def test_brand_names_and_shared_urls_do_not_collapse_distinct_services():
    minimax = definition_for("minimax-api")
    assert minimax is not None and minimax.id == "minimax-api"
    assert definition_for("codex").protocol == "responses"
    assert definition_for("google").id != "gemini"
    assert definition_for("qwen-token-plan").identity != definition_for("qwen-token-plan-individual").identity
    assert len({d.identity for d in PI_PROVIDERS}) == 42


def test_fcc_inventory_is_complete_and_does_not_register_unimplemented_services():
    assert len(FCC_PROVIDERS) == len({d.source_id for d in FCC_PROVIDERS}) == 61
    assert sum(d.coverage == "planned" for d in FCC_PROVIDERS) == 0
    assert all(d.id == "agg" or d.id in PROVIDER_BY_ID or d.id in PROVIDER_CLASSES for d in FCC_PROVIDERS)
    assert not any(d.id in PROVIDER_CLASSES for d in PROVIDER_BY_ID.values() if not d.implemented)
