from __future__ import annotations

import httpx
import pytest

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.providers.base import Account, Provider
from hoshimi.core.registry import ModelNotFound, Registry


class FakeProvider(Provider):
    def __init__(self, provider_id: str, models: list[str]) -> None:
        super().__init__(httpx.Client(), [Account({"access_token": "x"}, provider_id=provider_id)])
        self.id = provider_id
        self._models = models

    def fallback_models(self) -> list[str]:
        return list(self._models)

    def fetch_models(self) -> list[str]:
        return []

    def stream_raw(self, account, model, body):  # type: ignore[no-untyped-def]
        yield {"type": "text_delta", "text": f"{self.id}:{model}"}


def test_resolve_prefixed_model() -> None:
    registry = Registry({"cline": FakeProvider("cline", ["a"]), "trae": FakeProvider("trae", ["b"])})
    provider, model = registry.resolve("cline/a")
    assert provider.id == "cline"
    assert model == "a"


def test_resolve_bare_model_searches_catalogues() -> None:
    registry = Registry({"cline": FakeProvider("cline", ["shared"]), "trae": FakeProvider("trae", ["shared"])})
    provider, model = registry.resolve("shared")
    assert provider.id == "cline"
    assert model == "shared"


def test_resolve_ignores_unknown_provider_prefix() -> None:
    registry = Registry({"cline": FakeProvider("cline", ["anthropic/claude-x"])})
    provider, model = registry.resolve("anthropic/claude-x")
    assert provider.id == "cline"
    assert model == "anthropic/claude-x"


def test_resolve_missing_model_raises() -> None:
    registry = Registry(
        {"cline": FakeProvider("cline", ["a"]), "trae": FakeProvider("trae", ["b"])}
    )
    with pytest.raises(ModelNotFound):
        registry.resolve("nope")


def test_model_ids_have_owner() -> None:
    registry = Registry({"cline": FakeProvider("cline", ["a"]), "trae": FakeProvider("trae", ["b"])})
    assert {name for name, _ in registry.model_ids()} == {"a", "b"}
    assert all(owner in ("cline", "trae") for _, owner in registry.model_ids())


def test_single_provider_passthrough() -> None:
    registry = Registry({"cline": FakeProvider("cline", [])})
    provider, model = registry.resolve("whatever")
    assert provider.id == "cline"
    assert model == "whatever"


class SpecProvider(FakeProvider):
    """A station whose listing names one reasoning model."""

    def specs(self):  # type: ignore[no-untyped-def]
        return {
            model_id: ModelSpec(
                id=model_id,
                provider=self.id,
                name=model_id,
                reasoning=True,
                max_output=65536,
                can_disable_thinking=False,
            )
            for model_id in self._models
        }

    def stream_raw(self, account, model, body):  # type: ignore[no-untyped-def]
        yield {"type": "body", "body": body}


def test_stream_applies_the_effort_policy_before_the_wire() -> None:
    provider = SpecProvider("opencode", ["mimo-v2.6-flash-free"])
    registry = Registry({"opencode": provider})
    chunks = list(
        registry.stream(
            "mimo-v2.6-flash-free",
            {"model": "mimo-v2.6-flash-free", "messages": [], "reasoning_effort": "medium"},
        )
    )
    sent = chunks[0]["body"]
    # Balanced on a model that cannot think less: 8192 widened to 16384.
    assert sent["max_tokens"] == 16384
    assert "reasoning_effort" not in sent


def test_stream_leaves_a_body_that_named_no_level_alone() -> None:
    provider = SpecProvider("opencode", ["mimo-v2.6-flash-free"])
    registry = Registry({"opencode": provider})
    chunks = list(registry.stream("mimo-v2.6-flash-free", {"model": "m", "messages": []}))
    assert "max_tokens" not in chunks[0]["body"]


def test_spec_and_catalog_report_what_the_route_would_use() -> None:
    registry = Registry({"cline": FakeProvider("cline", ["a"]), "trae": SpecProvider("trae", ["a", "b"])})
    spec = registry.spec("trae/b")
    assert spec is not None
    assert (spec.provider, spec.reasoning) == ("trae", True)
    assert registry.spec("missing") is None
    # First provider wins the bare id; trae's copy is reachable as trae/a.
    assert [(row.id, row.provider) for row in registry.catalog()] == [
        ("a", "cline"),
        ("b", "trae"),
        ("trae/a", "trae"),
    ]


# -- a name two lanes publish ------------------------------------------------ #


def test_a_shadowed_model_keeps_its_bare_name_on_the_first_lane() -> None:
    """The rule that must not move: a caller naming no lane gets the same lane."""
    registry = Registry({"cline": FakeProvider("cline", ["shared"]), "trae": FakeProvider("trae", ["shared"])})
    provider, model = registry.resolve("shared")
    assert (provider.id, model) == ("cline", "shared")
    assert registry.model_ids()[0] == ("shared", "cline")


def test_a_shadowed_model_is_reachable_under_its_lane() -> None:
    """The regression: the second lane's copy used to vanish from the catalogue."""
    registry = Registry({"cline": FakeProvider("cline", ["shared"]), "trae": FakeProvider("trae", ["shared"])})
    assert dict(registry.model_ids()) == {"shared": "cline", "trae/shared": "trae"}
    # And the alias routes to the lane it names, not back to the winner.
    provider, model = registry.resolve("trae/shared")
    assert (provider.id, model) == ("trae", "shared")


def test_an_alias_carries_the_capabilities_of_the_lane_it_names() -> None:
    """Same id, two stations, two different offers — and the spec must say which."""
    registry = Registry({"cline": FakeProvider("cline", ["shared"]), "trae": SpecProvider("trae", ["shared"])})
    specs = {row.id: row for row in registry.catalog()}
    assert specs["shared"].provider == "cline"
    assert specs["shared"].reasoning is False
    assert specs["trae/shared"].provider == "trae"
    assert specs["trae/shared"].reasoning is True
    # The bare spec is untouched by the alias beside it.
    assert specs["shared"].id == "shared"


def test_three_lanes_sharing_a_name_get_one_alias_each() -> None:
    registry = Registry({
        "cline": FakeProvider("cline", ["m"]),
        "trae": FakeProvider("trae", ["m"]),
        "zcode": FakeProvider("zcode", ["m"]),
    })
    assert dict(registry.model_ids()) == {"m": "cline", "trae/m": "trae", "zcode/m": "zcode"}


def test_an_alias_never_displaces_a_name_a_lane_really_publishes() -> None:
    """A station may genuinely sell an id shaped like another lane's alias.

    ``cline`` sells an id literally named ``trae/m``, which is also the alias
    ``trae`` would have been given for its ``m``. A real id wins: it keeps its
    bare form and its lane, and the alias is dropped rather than published
    twice under one name.
    """
    registry = Registry({
        "cline": FakeProvider("cline", ["trae/m"]),
        "trae": FakeProvider("trae", ["m"]),
        "zcode": FakeProvider("zcode", ["m"]),
    })
    ids = dict(registry.model_ids())
    # The real id keeps its bare form and its own lane.
    assert ids["trae/m"] == "cline"
    # zcode precedes trae in the provider order, so it keeps the bare name.
    assert ids["m"] == "zcode"
    # The colliding alias is not emitted a second time under one name.
    assert [name for name, _ in registry.model_ids()].count("trae/m") == 1
    # The cost is stated rather than hidden: trae's copy is unreachable here.
    assert "trae/m" in ids


def test_the_catalogue_stays_ordered_bare_first() -> None:
    """Every bare id precedes every alias, so existing readers see a prefix."""
    registry = Registry({
        "cline": FakeProvider("cline", ["x", "y"]),
        "trae": FakeProvider("trae", ["y", "z"]),
    })
    names = [name for name, _ in registry.model_ids()]
    assert names == ["x", "y", "z", "trae/y"]


def test_the_bounded_listing_agrees_with_the_full_one() -> None:
    """`/v1/models?limit=N` goes through quick_models; it must not differ."""
    registry = Registry({"cline": FakeProvider("cline", ["shared"]), "trae": FakeProvider("trae", ["shared"])})
    bounded = [(name, owner) for name, owner, _spec in registry.quick_models(timeout=0.01)]
    assert bounded == registry.model_ids()
    assert dict(bounded)["trae/shared"] == "trae"
