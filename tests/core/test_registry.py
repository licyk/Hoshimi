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
    # First provider wins, exactly like /v1/models.
    assert [(row.id, row.provider) for row in registry.catalog()] == [("a", "cline"), ("b", "trae")]
