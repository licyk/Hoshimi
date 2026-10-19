"""Shared fixtures: a fake provider behind a real registry, services and app."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.config import ProviderConfig, Settings
from hoshimi.core.context import Services
from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account, Provider
from hoshimi.core.registry import Registry


class FakeProvider(Provider):
    """Echoes the model name and the message count back, without touching the network."""

    id = "fake"
    display_name = "Fake"

    def __init__(
        self,
        client: httpx.Client | None = None,
        accounts: list[Account] | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        # Defaults keep `FakeProvider()` usable, while the base signature lets
        # a probe round clone this provider onto its own short-timeout client.
        super().__init__(
            client or httpx.Client(),
            accounts or [Account({"access_token": "t"}, provider_id="fake")],
            options,
        )
        self.seen: list[dict[str, Any]] = []

    def fallback_models(self) -> list[str]:
        return ["fake-1", "fake-2"]

    def specs(self) -> dict[str, ModelSpec]:
        """The base's inert specs, with one id priced — so a field that carries
        a price has something to travel through instead of being None end to
        end. `fake-2` stays unpriced, which is the answer most ids get."""
        specs = super().specs()
        if "fake-1" in specs:
            specs["fake-1"] = replace(specs["fake-1"], free=True)
        return specs

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.seen.append(body)
        yield {"type": "text_delta", "text": f"echo:{model}:"}
        yield {"type": "text_delta", "text": str(len(body.get("messages") or []))}
        yield {"type": "usage", "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
        yield {"type": "finish", "reason": "stop"}


# Registered so a settings save can rebuild the pool into the same lane. Without
# this the rebuild path would drop `fake` as an unknown provider id, and every
# test that saved a config would silently lose its only provider.
PROVIDER_CLASSES["fake"] = FakeProvider


def make_services(*extra: Provider, **settings_kwargs: Any) -> Services:
    """A Services whose ``fake`` lane is declared in the config, not just in the pool.

    Declaring it means a settings save can rebuild the pool back into the same
    lanes it started with. A pool that exists only in memory would make every
    rebuild look like it deleted everything — which hides the very bug the
    rebuild path is there to catch.

    ``extra`` lanes stay pool-only: their keys (``fake0``, ``fake1``) are not
    provider ids the gateway knows, so nothing could rebuild them, and the
    tests that pass them never save a config that would try.

    ``config_path`` is left ``None``, which is only safe while nothing here
    saves: a ``None`` path makes a save fall through to ``find_config_file()``,
    and that finds the repository's own ``config.json`` and replaces it
    outright. The ``services`` fixture points it at ``tmp_path``; a test
    building services by hand that goes on to *save* must do the same.
    """
    providers: dict[str, Provider] = {"fake": FakeProvider()}
    declared: dict[str, ProviderConfig] = {"fake": _declared("fake", providers["fake"])}
    for index, provider in enumerate(extra):
        providers[f"fake{index}"] = provider
    settings = Settings(host="127.0.0.1", port=0, providers=declared, **settings_kwargs)
    return Services(settings=settings, registry=Registry(providers))


def _declared(provider_id: str, provider: Provider) -> ProviderConfig:
    """The config entry that would have built this lane: one real account.

    The account's own ``id`` is carried across. Without it the entry is not
    what the pool was built from — a renewal writing a token back looks the
    account up by id and would find nothing, which is the sort of gap that
    reads as a refresher bug.
    """
    accounts = [dict(account.data, id=account.id) for account in provider.accounts if not account.anonymous]
    return ProviderConfig(id=provider_id, enabled=True, accounts=accounts or [{"id": f"{provider_id}-0", "access_token": "t"}])


@pytest.fixture
def services(tmp_path: Path) -> Services:
    """A fresh gateway whose saves land in a directory pytest is about to delete.

    Without this, ``config_path`` is ``None`` and every settings save falls
    through to ``find_config_file()`` — which finds the repository's own
    ``config.json`` and ``os.replace``s it outright. The file is valid JSON, so
    the write-before-backup guard never fires, and a test that never meant to
    write a file would quietly destroy a real configuration.
    """
    built = make_services()
    built.config_path = tmp_path / "config.json"
    return built


@pytest.fixture
def client(services: Services) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as test_client:
        yield test_client
