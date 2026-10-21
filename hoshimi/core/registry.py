"""Model routing: `provider/model` prefixes and cross-provider fallback."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from concurrent.futures import wait
from typing import Any

import httpx

from hoshimi.core.catalog import ModelSpec
from hoshimi.core.discovery import CatalogDiscovery
from hoshimi.core.effort import apply_effort
from hoshimi.core.errors import ModelNotFoundError, ModelUnavailableError
from hoshimi.core.failure import DeadModelStore
from hoshimi.core.native_messages import MessagesRequest, stream_native_messages
from hoshimi.core.net.client import NetworkClient
from hoshimi.core.providers import PROVIDER_ORDER, build_providers
from hoshimi.core.providers.base import Account, ModelGone, Provider, RouteCallback

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

    def provider_hint(self, model: str) -> str:
        """The lane a ``provider/model`` id names, without resolving anything.

        Accounting wants a provider for a turn that failed before any account
        answered; a bare id would need a catalogue lookup to say, and that is
        not worth a round trip, so it gets ``""`` until ``on_route`` knows.
        """
        head, sep, tail = (model or "").strip().partition("/")
        return head if sep and tail and head in self.providers else ""

    def _order(self) -> list[str]:
        return [pid for pid, _ in self.ordered_providers()]

    def ordered_providers(self) -> list[tuple[str, Provider]]:
        """Take one ordered pool snapshot, even while configuration is being rebuilt."""
        providers = self.providers.copy()
        known = [pid for pid in PROVIDER_ORDER if pid in providers]
        extra = [pid for pid in providers if pid not in known]
        return [(pid, providers[pid]) for pid in known + extra]

    def stream(self, model: str, body: dict[str, Any], *, on_route: RouteCallback | None = None) -> Iterator[dict[str, Any]]:
        """One turn on the provider ``model`` resolves to; see :meth:`Provider.stream_using` for ``on_route``."""
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
        return self._guarded(provider, upstream_model, prepared, on_route)

    def _guarded(self, provider: Provider, model: str, body: dict[str, Any], on_route: RouteCallback | None = None) -> Iterator[dict[str, Any]]:
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
            for chunk in provider.stream(model, body, on_route=on_route):
                if not answered:
                    answered = True
                    self.dead_models.clear(*key)
                yield chunk
        except ModelGone as exc:
            self.dead_models.strike(key[0], key[1], reason=f"HTTP {exc.status}")
            raise

    def native_messages(self, model: str, context: MessagesRequest, *, on_route: RouteCallback | None = None) -> Iterator[dict[str, Any]] | None:
        """Route native requests through compatible accounts without Chat conversion."""
        provider, upstream_model = self.resolve(model)
        candidates = [account for account in provider._stream_candidates(upstream_model)
                      if provider.messages_endpoint(account, upstream_model, count_only=context.count_only) is not None]
        if not candidates:
            return None
        if not context.count_only and self.dead_models.is_dead(provider.id, upstream_model):
            raise ModelUnavailableError(f"{provider.id}/{upstream_model} is temporarily unavailable.")

        def send(account: Account, selected: str, _body: dict[str, Any]) -> Iterator[dict[str, Any]]:
            return stream_native_messages(provider, account, selected, context)

        def guarded() -> Iterator[dict[str, Any]]:
            try:
                for chunk in provider.stream_using(upstream_model, context.body, send, candidates=candidates, on_route=on_route):
                    if chunk.get("type") == "messages_response" and not context.count_only:
                        self.dead_models.clear(provider.id, upstream_model)
                    yield chunk
            except ModelGone as exc:
                if not context.count_only:
                    self.dead_models.strike(provider.id, upstream_model, reason=f"HTTP {exc.status}")
                raise

        return guarded()

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

    def quick_models(self, *, timeout: float = 2.0) -> list[tuple[str, str, ModelSpec | None]]:
        """Bound startup discovery, retaining cached and configured IDs on timeout."""
        providers = self.ordered_providers()
        pending = [self.discovery.submit(provider) for _id, provider in providers]
        if pending:
            wait(pending, timeout=timeout)
        result: list[tuple[str, str, ModelSpec | None]] = []
        seen: set[str] = set()
        for provider_id, provider in providers:
            specs = self.discovery.cached(provider).specs
            declared = []
            for account in provider.accounts:
                models = account.get("models", provider.options.get("models", []))
                if isinstance(models, list):
                    declared.extend(model for model in models if isinstance(model, str))
            for model in list(specs) or provider.cached_model_ids() or declared or provider.fallback_models():
                if model not in seen:
                    seen.add(model)
                    result.append((model, provider_id, specs.get(model)))
        return result

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

        - *the HTTP clients*. The registry owns each lane's isolated client.
          A turn already streaming retains its connection while new requests
          use the updated policy.
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
            before = previous_inputs.get(provider_id)
            after = inputs[provider_id]
            route_only = before is not None and before[0] == after[0] and (
                {key: value for key, value in before[1].items() if key != "network"}
                == {key: value for key, value in after[1].items() if key != "network"}
            )
            if kept is not None and (before == after or route_only):
                kept.options = options.get(provider_id, {})
                rebuilt[provider_id] = kept
            elif kept is not None and kept.client is not client and not isinstance(kept.client, NetworkClient):
                _close_quietly(provider_id, kept.client)
        self.providers = rebuilt
        self._inputs = inputs
        self.default_model = default_model
        for provider_id in set(previous) - set(rebuilt):
            dropped_client = previous[provider_id].client
            if dropped_client is not client and not isinstance(dropped_client, NetworkClient):
                _close_quietly(provider_id, dropped_client)


def _close_quietly(provider_id: str, client: httpx.Client) -> None:
    try:
        client.close()
    except Exception:  # closing is best-effort
        logger.debug("provider %s already closed", provider_id, exc_info=True)


def new_client(timeout: float = 600.0) -> httpx.Client:
    """The root client owning isolated provider connections and shared policy."""
    return NetworkClient(timeout=timeout)


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
