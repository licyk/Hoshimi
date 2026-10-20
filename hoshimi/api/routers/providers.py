"""The enabled providers: id, accounts and the models they advertise, and the stations an ``agg`` account can name."""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import Field

from hoshimi.api.deps import ServicesDep
from hoshimi.api.errors import ERROR_RESPONSES
from hoshimi.core.config import ProviderConfig, save_settings
from hoshimi.core.errors import ConflictError, NotFoundError
from hoshimi.core.login import login_providers
from hoshimi.core.net.client import NetworkClient
from hoshimi.core.presets import PRESETS
from hoshimi.core.provider_forms import credential_forms
from hoshimi.core.providers import PROVIDER_CLASSES, real_account_count
from hoshimi.core.record import Record

router = APIRouter(prefix="/v1", tags=["providers"])


class NetworkDiagnostic(Record):
    provider: str
    operation: str
    kind: str
    status: int | None = None
    at: float
    host: str
    route: str


@router.get("/network/diagnostics", operation_id="network_diagnostics")
def network_diagnostics(services: ServicesDep) -> list[NetworkDiagnostic]:
    rows = [
        NetworkDiagnostic(provider=pid, **row)
        for pid, provider in services.registry.ordered_providers()
        if isinstance(provider.client, NetworkClient)
        for row in provider.client.diagnostics()
    ]
    if services.login is not None:
        rows.extend(NetworkDiagnostic(provider=pid, **row) for pid, row in services.login.diagnostics())
    latest = {(row.provider, row.operation): row for row in sorted(rows, key=lambda row: row.at)}
    return list(latest.values())


class ProviderSummary(Record):
    id: str
    display_name: str
    accounts: int
    account_ids: list[str]
    models: int | None
    enabled: bool
    keyless: bool
    requires_authorization: bool = False


class AccountRemoval(Record):
    """What "forget" removed: the pool always, the config file when the account was in it."""

    provider: str
    account: str
    removed_from_config: bool


class StationView(Record):
    """One station an ``agg`` account may name in place of a URL."""

    id: str
    display_name: str
    base_url: str
    key_env: str
    catalog_keyless: bool = False


class AccountSubmission(Record):
    """The credential to append, in the shape it takes in ``config.json``."""

    account: dict[str, Any]


class CredentialFieldView(Record):
    key: str
    kind: str
    required: bool
    choices: list[str] = Field(default_factory=list)


class CredentialFormView(Record):
    method: str
    fields: list[CredentialFieldView]
    defaults: dict[str, str]


class ProviderFormsView(Record):
    id: str
    forms: list[CredentialFormView]


@router.get("/provider-forms", operation_id="list_provider_forms")
def list_provider_forms() -> list[ProviderFormsView]:
    return [ProviderFormsView(id=provider, forms=[CredentialFormView(method=form.method, fields=[CredentialFieldView(**asdict(field)) for field in form.fields], defaults=dict(form.defaults)) for form in forms])
            for provider, forms in credential_forms().items()]


class AccountAddition(Record):
    """What the save accepted: the lane, the account's id, and how many it now holds."""

    provider: str
    account: str
    accounts: int


@router.get("/providers", operation_id="list_providers")
def list_providers(services: ServicesDep) -> list[ProviderSummary]:
    configured = services.settings.providers
    out: list[ProviderSummary] = []
    for provider_id, provider in services.registry.providers.items():
        cfg = configured.get(provider_id)
        out.append(
            ProviderSummary(
                id=provider_id,
                display_name=provider.display_name or provider_id,
                accounts=real_account_count(provider),
                account_ids=[account.id for account in provider.accounts if not account.anonymous],
                # Metadata must never wait for an upstream catalogue request.
                models=provider.cached_model_count,
                enabled=cfg.enabled if cfg else True,
                keyless=provider.keyless,
                requires_authorization=provider.requires_authorization,
            )
        )
    # Configured but disabled providers are worth showing too.
    for provider_id, cfg in configured.items():
        if cfg.enabled and provider_id in services.registry.providers:
            continue
        if not cfg.enabled:
            out.append(
                ProviderSummary(
                    id=provider_id,
                    display_name=_declared_display_name(provider_id),
                    accounts=len(cfg.accounts),
                    account_ids=[str(account.get("id") or "") for account in cfg.accounts],
                    models=0,
                    enabled=False,
                    keyless=_declared_keyless(provider_id),
                    requires_authorization=_declared_authorization(provider_id),
                )
            )
    # A lane that cannot be served without an outside authorization is worth
    # listing even unconfigured: that is how the UI knows to ask for one.
    listed = {row.id for row in out}
    for provider_id, cls in PROVIDER_CLASSES.items():
        if provider_id in listed or not (cls.requires_authorization or provider_id in credential_forms()):
            continue
        out.append(
            ProviderSummary(
                id=provider_id,
                display_name=cls.display_name or provider_id,
                accounts=0,
                account_ids=[],
                models=0,
                enabled=False,
                keyless=cls.keyless,
                requires_authorization=cls.requires_authorization,
            )
        )
    # Every browser-login station is actionable before it has an account, even
    # when its provider class is otherwise treated as an ordinary configured
    # lane. Listing these rows is what lets Channels offer CodeArts, Cline,
    # Gemini, and the other sign-in stations from a fresh installation.
    login_pending = sorted(set(login_providers()) - {row.id for row in out})
    for provider_id in login_pending:
        cfg = configured.get(provider_id)
        cls = PROVIDER_CLASSES.get(provider_id)
        out.append(
            ProviderSummary(
                id=provider_id,
                display_name=_pending_login_display_name(provider_id),
                accounts=len(cfg.accounts) if cfg else 0,
                account_ids=[str(account.get("id") or "") for account in cfg.accounts] if cfg else [],
                models=0,
                enabled=bool(cfg and cfg.enabled),
                keyless=bool(cls.keyless if cls else False),
                requires_authorization=True,
            )
        )
    return out


def _pending_login_display_name(provider_id: str) -> str:
    cls = PROVIDER_CLASSES.get(provider_id)
    if cls is not None:
        return cls.display_name or provider_id
    names = {"codex": "Codex (ChatGPT OAuth)"}
    return names.get(provider_id, provider_id.title())


@router.get("/stations", operation_id="list_stations")
def list_stations() -> list[StationView]:
    """The table behind ``preset``, so a form can offer a name instead of a URL.

    Reference data, and only that: the key stays the operator's to bring.
    ``key_env`` is *reported* rather than read — quietly starting to accept an
    environment variable would be a second credential source with its own
    security story, and the name is here only so an operator knows what to
    export before pasting.
    """
    return [
        StationView(id=preset.id, display_name=preset.display_name, base_url=preset.base_url, key_env=preset.key_env, catalog_keyless=preset.catalog_keyless)
        for preset in PRESETS
    ]


@router.post("/providers/{provider_id}/accounts", operation_id="add_provider_account", responses=ERROR_RESPONSES)
def add_provider_account(
    provider_id: str,
    services: ServicesDep,
    submission: Annotated[AccountSubmission, Body(description="The credential to append, exactly as it would be written into config.json.")],
) -> AccountAddition:
    """Append one credential to a lane: the config file and the live pool alike.

    Two decisions carry the weight here, and both are about not reporting
    something untrue:

    - *appended, never replaced.* The settings a client can read carry account
      **counts** only, so a caller that let this endpoint overwrite the list
      would be overwriting it blind — and taking away every other account on
      the lane is not what "add" means.
    - *the lane gets to refuse first.* A provider that discards an unusable
      blob while building the pool would otherwise leave this endpoint
      announcing a credential that does not exist, which is exactly the lie
      ``PATCH /settings`` used to tell. ``Provider.check_account`` is where
      that refusal lives.

    A lane that is switched off is switched back on. The promise is that the
    account reaches the **pool**, and a disabled lane has no pool to reach; the
    switch on the settings page turns it straight off again for an operator who
    meant to keep it off.
    """
    cls = PROVIDER_CLASSES.get(provider_id)
    if cls is None:
        raise NotFoundError(f"No provider '{provider_id}'.")
    data = dict(submission.account)
    cls.check_account(data)

    # The whole file is taken, copied, edited and only then written: the running
    # services hold the original, so comparing the two is what tells
    # `apply_settings` that this lane actually moved.
    candidate = copy.deepcopy(services.settings)
    cfg = candidate.providers.get(provider_id)
    existing = list(cfg.accounts) if cfg else []
    taken = _account_ids(provider_id, existing)
    wanted = str(data.get("id") or "").strip()
    if wanted and wanted in taken:
        raise ConflictError(f"Provider '{provider_id}' already has an account '{wanted}'.")
    data["id"] = wanted or _free_account_id(provider_id, existing, taken)
    if cfg is None:
        candidate.providers[provider_id] = ProviderConfig(id=provider_id, enabled=True, accounts=[data])
    else:
        cfg.accounts.append(data)
        cfg.enabled = True

    save_settings(candidate)
    services.apply_settings(candidate)
    return AccountAddition(provider=provider_id, account=str(data["id"]), accounts=len(candidate.providers[provider_id].accounts))


@router.delete("/providers/{provider_id}/accounts/{account_id}", operation_id="delete_provider_account", responses=ERROR_RESPONSES)
def delete_provider_account(provider_id: str, account_id: str, services: ServicesDep) -> AccountRemoval:
    """Forget one account: dropped from the config file and from the live pool alike.

    An account imported from an OFM data dir has no entry in ``config.json``; it is
    still removed from the running pool, and the answer says so.
    """
    cfg = services.settings.providers.get(provider_id)
    removed_from_config = False
    if cfg is not None:
        # Compared the way the pool names it, not the way the file stores it: an
        # entry with no `id` is named from its position, so matching on the
        # stored field alone would leave it behind in the file and the next
        # rebuild would hand it straight back.
        keep = [raw for index, raw in enumerate(cfg.accounts) if _config_account_id(provider_id, raw, index) != account_id]
        if len(keep) != len(cfg.accounts):
            cfg.accounts = keep
            removed_from_config = True
    provider = services.registry.providers.get(provider_id)
    in_pool = provider is not None and any(account.id == account_id for account in provider.accounts)
    if provider is not None and in_pool:
        provider.accounts = [account for account in provider.accounts if account.id != account_id]
    if not removed_from_config and not in_pool:
        raise NotFoundError(f"No account '{account_id}' on provider '{provider_id}'.")
    if removed_from_config:
        save_settings(services.settings)
    return AccountRemoval(provider=provider_id, account=account_id, removed_from_config=removed_from_config)


def _declared_keyless(provider_id: str) -> bool:
    cls = PROVIDER_CLASSES.get(provider_id)
    return cls is not None and cls.keyless


def _declared_display_name(provider_id: str) -> str:
    cls = PROVIDER_CLASSES.get(provider_id)
    return (cls.display_name if cls else "") or provider_id


def _declared_authorization(provider_id: str) -> bool:
    cls = PROVIDER_CLASSES.get(provider_id)
    return cls is not None and cls.requires_authorization


def _config_account_id(provider_id: str, raw: dict[str, Any], index: int) -> str:
    """The name this entry answers to: its own, or its position when it has none.

    ``Account`` derives an id from where the entry sits in the lane, so the file
    and the pool only agree about *which account is which* when they are compared
    this way. Both ends of this module go through here for that reason: the add
    endpoint picks a name the pool has not already handed out, and the forget
    endpoint finds the entry under the name the pool gave it.
    """
    return str(raw.get("id") or f"{provider_id}-{index}")


def _account_ids(provider_id: str, accounts: list[dict[str, Any]]) -> set[str]:
    """The names this lane already answers to, including the ones the pool derives.

    A derived name counts as taken: handing it out again would give two accounts
    the same name, and "forget" would then match whichever it found first — which
    is how an operator deletes the account they meant to keep.
    """
    return {_config_account_id(provider_id, raw, index) for index, raw in enumerate(accounts)}


def _free_account_id(provider_id: str, accounts: list[dict[str, Any]], taken: set[str]) -> str:
    """A name nothing on this lane owns, in the shape the pool generates itself."""
    index = len(accounts)
    while f"{provider_id}-{index}" in taken:
        index += 1
    return f"{provider_id}-{index}"
