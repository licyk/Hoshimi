"""The aggregator lane: any OpenAI-compatible station the install holds a key for.

One provider id carries many stations because the station is the account, not
the class — each account names its own ``base_url`` (the root whose
``/chat/completions`` and ``/models`` hang, exactly as EAC does) and its own
credential. That makes OpenRouter's free pool, SiliconFlow, Cloudflare Workers
AI, Groq or SambaNova a configuration entry rather than new code.

An account may name a ``preset`` in place of its ``base_url`` when the station
is one of the twenty-two the gateway already knows the address of (``hoshimi
stations`` lists them, with the environment variable the upstream reads its key
from). The credential is still the account's own, always — the table knows
addresses, not secrets.

The wire is the plain OpenAI chat completions shape, so the only real design
questions are the ones a generic lane cannot answer on its own:

- *which station a model belongs to* — an account may declare ``models``, and
  then that account is the one a turn for those ids runs on (a multi-station
  pool would otherwise 404 on the ids it does not carry);
- *what a model can do* — a listing names ids and nothing else, so the
  default spec is inert (``reasoning=False``: the caller's body passes
  through untouched) until the account's ``options`` / ``options.model_specs``
  declare a capacity or a thinking menu.

A missing ``base_url`` or credential is a configuration mistake, not a runtime
failure: such an account is dropped at construction with a warning, so a half
-filled config still boots and the other stations still serve.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from typing import Any, ClassVar

import httpx

from hoshimi.core.catalog import ModelSpec, agg_capability, agg_spec
from hoshimi.core.chat_catalog import Row, is_chat_row, station_for_base
from hoshimi.core.errors import ValidationError
from hoshimi.core.free_marks import documented_free, row_free
from hoshimi.core.modelsdev import enrich_spec
from hoshimi.core.presets import preset_base_url
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.compat import gateway_chat_body
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider, model_ids, raise_for_status, read_body
from hoshimi.core.providers.station_catalogs import discover, row_capabilities
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.anthropic import build_anthropic_body, iter_anthropic_chunks

logger = logging.getLogger(__name__)

TOKEN_KEYS = ("api_key", "access_token", "token")
"""The credential fields accepted, in the order a station usually names them."""


class AggProvider(OpenAiCompatProvider):
    """One id, many stations: the account carries the base URL and the key."""

    id = "agg"
    display_name = "Aggregator"
    keyless = False
    # The listing is live, but so is the question "does this egress reach the
    # station at all" — which is exactly what a probe answers.
    probeable = True
    # Every account here is a key the install had to go get.
    requires_authorization = True
    fallback_model_list: ClassVar[list[str]] = []

    def __init__(
        self,
        client: httpx.Client,
        accounts: list[Account],
        options: dict[str, Any] | None = None,
    ):
        usable: list[Account] = []
        dropped: list[str] = []
        for account in accounts:
            if self._base_or_empty(account) and self._token_or_empty(account):
                usable.append(account)
            else:
                dropped.append(account.id)
        if dropped:
            logger.warning("agg: dropping accounts without base_url or key: %s", ", ".join(dropped))
        super().__init__(client, usable, options)
        # What each station said about each id, filled while the listing is
        # read and read back when the specs are built — a spec has no idea
        # which station an id came from, and the same id at two stations can
        # be two different models at two different prices.
        self._station_of: dict[str, str] = {}
        self._free_of: dict[str, bool] = {}
        self._rows_of: dict[str, Row] = {}

    # -- credential --------------------------------------------------------- #
    @staticmethod
    def _base_or_empty(account: Account) -> str:
        """The station's root: its own URL, or a name this gateway already knows.

        ``preset`` names a station from the table under ``hoshimi stations``, so
        the line an operator writes is a name and a key rather than a URL they
        had to look up. An explicit ``base_url`` always wins — the table is a
        convenience, never an override.
        """
        base = str(account.get("base_url") or "").strip().rstrip("/")
        if base:
            return base
        return preset_base_url(str(account.get("preset") or "")).rstrip("/")

    @classmethod
    def _base(cls, account: Account) -> str:
        base = cls._base_or_empty(account)
        if not base:
            preset = str(account.get("preset") or "").strip()
            hint = f" (unknown preset {preset!r}; `hoshimi stations` lists them)" if preset else ""
            raise ProviderError(
                400,
                "agg: the account needs base_url (the station's OpenAI root, e.g. https://openrouter.ai/api/v1)"
                f" or a preset naming a known station{hint}",
            )
        return base

    @staticmethod
    def _token_or_empty(account: Account) -> str:
        for key in TOKEN_KEYS:
            value = str(account.get(key) or "").strip()
            if value:
                return value
        return ""

    @classmethod
    def _token(cls, account: Account) -> str:
        token = cls._token_or_empty(account)
        if not token:
            raise ProviderError(400, "agg: the account needs api_key (or access_token/token)")
        return token

    @classmethod
    def check_account(cls, data: dict[str, Any]) -> None:
        """Both halves this lane discards at construction: a station and a key.

        ``_base`` and ``_token`` are reused rather than restated, so the
        sentence a form shows the operator is the sentence the pool would have
        logged when it dropped the account — one source for a message that has
        to be right in two very different places.
        """
        account = Account(data, provider_id=cls.id)
        try:
            cls._base(account)
            cls._token(account)
        except ProviderError as exc:
            raise ValidationError(exc.message) from None

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        headers["Authorization"] = f"Bearer {self._token(account)}"
        extra = account.get("headers")
        if isinstance(extra, dict):
            for key, value in extra.items():
                # A stray header must not be able to replace the credential.
                if str(key).lower() == "authorization":
                    continue
                headers[str(key)] = str(value)
        return headers

    # -- capabilities ------------------------------------------------------- #
    def _declared_models(self, account: Account) -> list[str] | None:
        """The account's own catalogue, or None when it relies on the listing."""
        declared = account.get("models")
        if not isinstance(declared, list):
            return None
        return [str(item).strip() for item in declared if str(item).strip()]

    def _options_for(self, account: Account) -> dict[str, Any]:
        """Provider defaults overridden by the account's own options."""
        merged = dict(self.options)
        account_options = account.get("options")
        if isinstance(account_options, dict):
            merged.update(account_options)
        return merged

    def _spec_overrides(self, model_id: str) -> dict[str, Any]:
        """The capacity declarations for one id: provider-level, then account.

        An account's flat keys are its defaults for every id it carries; the
        ``model_specs`` row for this id then wins over them.
        """
        merged: dict[str, Any] = {}

        def absorb(options: dict[str, Any]) -> None:
            for key, value in options.items():
                if key != "model_specs":
                    merged[key] = value
            rows = options.get("model_specs")
            if isinstance(rows, dict):
                row = rows.get(model_id)
                if isinstance(row, dict):
                    merged.update(row)

        absorb(self.options)
        for account in self.accounts:
            account_options = account.get("options")
            if isinstance(account_options, dict):
                absorb(account_options)
        return merged

    def specs(self) -> dict[str, ModelSpec]:
        """The account's declarations; everything else is a listing with no facts.

        A declaration speaks for its id and is left entirely alone. An id nobody
        declared gets the two facts models.dev can vouch for whatever endpoint
        carries it — whether it reasons, and whether it takes images — and never
        a borrowed limit, since those numbers describe someone else's station.
        """
        out: dict[str, ModelSpec] = {}
        for model_id in self.models():
            overrides = self._spec_overrides(model_id)
            station_caps = {**agg_capability(self._station_of.get(model_id), model_id), **row_capabilities(self._rows_of.get(model_id, {}))}
            spec = enrich_spec(
                agg_spec(model_id, self.id, overrides, station_caps=station_caps),
                # A station's own catalogue is a declaration in `enrich_spec`'s
                # sense: it said something about this id, and models.dev is not
                # a better source than the station selling it.
                declared=bool(overrides) or bool(station_caps),
            )
            # The station's own price list, recorded while the listing was
            # read: a spec has no idea which station an id came from, and the
            # same id at two stations can cost two different amounts.
            if model_id in self._free_of:
                spec = replace(spec, free=self._free_of[model_id])
            out[model_id] = spec
        return out

    # -- model discovery ---------------------------------------------------- #
    def fallback_models(self) -> list[str]:
        return list(self._rows_of)

    def _candidate_accounts(self, model: str) -> list[Account]:
        """Accounts that should serve this id, declared stations first.

        An account that declares ``models`` is the only one that can serve
        those ids; accounts without a declaration stay in the pool, so a
        single-station config keeps working with no declaration at all.
        """
        declaring = [account for account in self.accounts if model in (self._declared_models(account) or [])]
        open = [account for account in self.accounts if self._declared_models(account) is None]
        return declaring + open + [account for account in self.accounts if account not in declaring and account not in open]

    def _stream_candidates(self, model: str) -> list[Account]:
        return self._candidate_accounts(model)

    def fetch_models(self) -> list[str]:
        previous = (self._station_of, self._free_of)
        self._station_of = {}
        self._free_of = {}
        models: list[str] = []
        fresh_rows: dict[str, Row] = {}
        for account in self.accounts:
            # The station, before anything is asked of it. An account that
            # declares its roster still names a station, and both tables in
            # `free_marks` are keyed by that name — the two ids agnes documents
            # are worthless if nothing knows which account is agnes.
            station = station_for_base(self._base_or_empty(account))
            declared = self._declared_models(account)
            if declared is not None:
                # The operator speaking for their own station: its shape is not
                # ours to second-guess, and an account that names its ids has
                # already told us which ones it means.
                models.extend(declared)
                for model_id in declared:
                    self._remember_row(model_id, station, None)
                continue
            try:
                base = self._base(account)
            except ProviderError:
                self.catalog_failed = True
                continue
            try:
                fetched = discover(self.client, base, station, self.build_headers(account, ""))
            except (httpx.HTTPError, ValueError):
                self.catalog_failed = True
                continue
            rows = [row for row in fetched if is_chat_row(station, row)]
            for row in rows:
                model_id = row.get("id")
                if isinstance(model_id, str):
                    fresh_rows.setdefault(model_id, row)
                    self._remember_row(model_id, station, row)
            models.extend(model_ids(rows))
        if models:
            self._rows_of.update(fresh_rows)
        else:
            self._station_of, self._free_of = previous
        return models

    def _remember_row(self, model_id: str, station: str | None, row: Row | None) -> None:
        """Record what this station said about one id, station included.

        The station itself is the first fact and the least negotiable: without
        it nothing downstream can tell which of the twenty-two tables an id
        belongs to, and a capability or a price would be applied to whoever
        happens to share the string.

        The price is then read the way pi-free stamps it — what the row says
        today outranks what a table recorded once, so the table is only asked
        when the row has not answered. The first account to name an id keeps
        its verdict, in the order the listing uses and the order a turn would
        be served in. And a station that says nothing records nothing: `None`
        is the honest answer for most ids, and turning it into False would be
        this gateway quoting a price it never read.
        """
        if station is not None:
            self._station_of.setdefault(model_id, station)
        mark = row_free(station, row)
        if station == "merge" and row is not None and isinstance(row.get("isFree"), bool):
            mark = row["isFree"]
        if mark is None:
            mark = documented_free(station, model_id)
        if mark is not None:
            self._free_of.setdefault(model_id, mark)

    # -- chat --------------------------------------------------------------- #
    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        station = station_for_base(self._base(account))
        messages = station == "commandcode" and model.rsplit("/", 1)[-1].startswith("claude-")
        prepared = gateway_chat_body(body) if station is not None else body
        payload = build_anthropic_body(prepared, model=model) if messages else self.build_body(prepared, model)
        url = f"{self._base(account)}/{'messages' if messages else 'chat/completions'}"
        headers = self.build_headers(account, model)
        if messages:
            headers["anthropic-version"] = "2023-06-01"
        with self.client.stream("POST", url, json=payload, headers=headers) as response:
            observe_response(self.id, response)
            if response.status_code >= 400:
                raise_for_status(response, provider=self.id)
            # Many stations answer a stream request with the whole document;
            # the shape decides how it is read, not the headers.
            if messages:
                yield from iter_anthropic_chunks(response.iter_lines())
            else:
                yield from read_body(response, provider=self.id)
