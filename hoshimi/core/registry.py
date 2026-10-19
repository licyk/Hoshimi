"""Model routing: `provider/model` prefixes and cross-provider fallback."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.discovery import CatalogDiscovery
from hoshimi.core.effort import apply_effort
from hoshimi.core.errors import ModelNotFoundError, ModelUnavailableError
from hoshimi.core.failure import DeadModelStore
from hoshimi.core.providers import PROVIDER_ORDER, build_providers
from hoshimi.core.providers.base import ModelGone, Provider

logger = logging.getLogger(__name__)


class ModelNotFound(ModelNotFoundError):
    """The requested model does not exist on any enabled provider."""


class Registry:
    """Routes model names to providers and exposes the model catalogue."""

    def __init__(
        self,
        providers: dict[str, Provider],
        *,
        client: httpx.Client | None = None,
        default_model: str | None = None,
        dead_models: DeadModelStore | None = None,
    ):
        self.providers = providers
        self.client = client
        self.default_model = default_model
        self.dead_models = dead_models if dead_models is not None else DeadModelStore()
        self._inputs: dict[str, tuple[Any, Any]] = {}
        self.discovery = CatalogDiscovery()

    # -- routing ---------------------------------------------------------- #
    def resolve(self, model: str) -> tuple[Provider, str]:
        """Return (provider, upstream model name) for a requested model."""
        requested = (model or "").strip()
        if not requested:
            raise ModelNotFound("model is required")

        head, sep, tail = requested.partition("/")
        if sep and head in self.providers and tail:
            return self.providers[head], tail

        # Bare model name: prefer the provider order, then any provider that
        # advertises it, then a single-provider deployment (blind passthrough).
        for provider_id in self._order():
            provider = self.providers[provider_id]
            if requested in provider.models():
                return provider, requested

        hits = [pid for pid in self._order() if requested in self.providers[pid].models()]
        if len(hits) == 1:
            return self.providers[hits[0]], requested

        # No catalogue match: if exactly one provider is enabled, pass through.
        ordered = self._order()
        if len(ordered) == 1:
            return self.providers[ordered[0]], requested

        raise ModelNotFound(
            f"Model {requested!r} not found. Use `provider/model` or see /v1/models."
        )

    def _order(self) -> list[str]:
        return [pid for pid, _ in self.ordered_providers()]

    def ordered_providers(self) -> list[tuple[str, Provider]]:
        """Take one ordered pool snapshot, even while configuration is being rebuilt."""
        providers = self.providers.copy()
        known = [pid for pid in PROVIDER_ORDER if pid in providers]
        extra = [pid for pid in providers if pid not in known]
        return [(pid, providers[pid]) for pid in known + extra]

    def stream(self, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        provider, upstream_model = self.resolve(model)
        # The effort policy is part of routing: the budget/patch a model's
        # reasoning level turns into is decided before any wire sees it.
        prepared = apply_effort(provider.specs().get(upstream_model), body)
        # Checked before any wire sees the request: a station that answered
        # 404 for every account will say so again, and asking costs a round
        # trip to learn the same thing. Raised here rather than on the first
        # `next()` so a refused turn is still a counted turn.
        if self.dead_models.is_dead(provider.id, upstream_model):
            raise ModelUnavailableError(
                f"{provider.id}/{upstream_model} is temporarily unavailable: "
                "the station stopped answering for it.",
                {"provider": provider.id, "model": upstream_model},
            )
        return self._guarded(provider, upstream_model, prepared)

    def _guarded(self, provider: Provider, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Stream one turn, teaching the dead-model store in both directions.

        A :class:`ModelGone` on the way out means every account in the pool
        agreed the station does not know this id, and that is the fact worth
        remembering. An answer on the way in means the id is back — but only
        the *first* chunk clears it, so a turn that dies mid-stream is never
        mistaken for a healthy model.
        """
        key = (provider.id, model)
        answered = False
        try:
            for chunk in provider.stream(model, body):
                if not answered:
                    answered = True
                    self.dead_models.clear(*key)
                yield chunk
        except ModelGone as exc:
            self.dead_models.strike(key[0], key[1], reason=f"HTTP {exc.status}")
            raise

    def spec(self, model: str) -> ModelSpec | None:
        """The capability spec behind a requested model, or None when unroutable."""
        try:
            provider, upstream_model = self.resolve(model)
        except ModelNotFoundError:
            return None
        return provider.specs().get(upstream_model)

    # -- catalogue -------------------------------------------------------- #
    def model_ids(self) -> list[tuple[str, str]]:
        """Return (model id, owning provider) pairs for /v1/models."""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for provider_id in self._order():
            provider = self.providers[provider_id]
            for name in provider.models():
                if name in seen:
                    continue
                seen.add(name)
                out.append((name, provider_id))
        return out

    def probe_targets(self) -> list[tuple[Provider, str]]:
        """(provider, model) pairs a default probe round asks about.

        Only lanes that opt in: their listing is a flat id list, so the only
        thing that can say which models this egress may use is a request.
        """
        return [
            (provider, model_id)
            for provider_id in self._order()
            for provider in (self.providers[provider_id],)
            if provider.probeable
            for model_id in provider.models()
        ]

    def catalog(self) -> list[ModelSpec]:
        """One spec per advertised id, in listing order (first provider wins)."""
        out: list[ModelSpec] = []
        seen: set[str] = set()
        for provider_id in self._order():
            for model_id, spec in self.providers[provider_id].specs().items():
                if model_id in seen:
                    continue
                seen.add(model_id)
                out.append(spec)
        return out

    def close(self) -> None:
        self.discovery.close()
        if self.client is not None:
            _close_quietly("registry", self.client)
            return
        for provider_id, provider in self.providers.items():
            _close_quietly(provider_id, provider.client)

    def rebuild(
        self,
        provider_accounts: dict[str, list[dict[str, Any]]],
        provider_options: dict[str, dict[str, Any]] | None = None,
        *,
        default_model: str | None = None,
        max_account_retries: int = 3,
        disabled: set[str] | None = None,
    ) -> None:
        """Re-derive the provider pool from a new configuration, in place.

        Three things survive here that a full restart would not:

        - *the HTTP client*. It is shared by every lane, and a turn already
          streaming holds a provider object that would go dead the instant its
          client closed — an operator adding an account must not be a reason to
          cut off an answer in flight.
        - *lanes whose own configuration did not move*. A provider carries what
          only memory knows: which accounts are out of rotation because their
          refresh token was refused, which are under a rate-limit hold, and
          which ids the station just said it no longer has. Saving a key for a
          different station is no reason to forget any of that, so a lane is
          rebuilt only when the accounts or options *it* was built from changed.
        - *the ban store*. "This id is gone" is a fact about the station, not
          about the accounts this edit moved.
        """
        options = provider_options or {}
        previous, previous_inputs = self.providers, self._inputs
        client = self.client
        if client is None:
            client = new_client()
            self.client = client
        rebuilt = build_providers(
            client,
            provider_accounts,
            options,
            max_retries=max_account_retries,
            disabled=disabled or set(),
        )
        inputs = lane_inputs(provider_accounts, options, rebuilt)
        for provider_id in rebuilt:
            kept = previous.get(provider_id)
            if kept is not None and previous_inputs.get(provider_id) == inputs[provider_id]:
                rebuilt[provider_id] = kept
            elif kept is not None and kept.client is not client:
                _close_quietly(provider_id, kept.client)
        self.providers = rebuilt
        self._inputs = inputs
        self.default_model = default_model
        for provider_id in set(previous) - set(rebuilt):
            dropped_client = previous[provider_id].client
            if dropped_client is not client:
                _close_quietly(provider_id, dropped_client)


def _close_quietly(provider_id: str, client: httpx.Client) -> None:
    try:
        client.close()
    except Exception:  # closing is best-effort
        logger.debug("provider %s already closed", provider_id, exc_info=True)


def new_client(timeout: float = 600.0) -> httpx.Client:
    """The one HTTP client every lane of a registry shares."""
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=15.0),
        follow_redirects=True,
        http2=False,
    )


def lane_inputs(
    provider_accounts: dict[str, list[dict[str, Any]]],
    provider_options: dict[str, dict[str, Any]] | None,
    provider_ids: Iterable[str],
) -> dict[str, tuple[list[dict[str, Any]], dict[str, Any]]]:
    """Per-lane configuration, as the pool compares it to decide what to rebuild.

    The keys are the lanes that were actually built — a provider the config
    does not name has no entry, so it is never mistaken for an unchanged one.
    """
    options = provider_options or {}
    return {provider_id: (provider_accounts.get(provider_id, []), options.get(provider_id, {})) for provider_id in provider_ids}


def build_registry(
    provider_accounts: dict[str, list[dict[str, Any]]],
    provider_options: dict[str, dict[str, Any]] | None = None,
    *,
    default_model: str | None = None,
    max_account_retries: int = 3,
    timeout: float = 600.0,
    disabled: set[str] | None = None,
) -> Registry:
    client = new_client(timeout)
    providers = build_providers(
        client,
        provider_accounts,
        provider_options,
        max_retries=max_account_retries,
        disabled=disabled,
    )
    registry = Registry(providers, client=client, default_model=default_model)
    # Recorded so the first save of an unchanged config keeps every lane: the
    # pool only learns what it was built from by being told.
    registry._inputs = lane_inputs(provider_accounts, provider_options, providers)
    return registry
