"""Provider registry: all supported API stations."""

from __future__ import annotations

from typing import Any

import httpx

from hoshimi.core.net.client import NetworkClient
from hoshimi.core.providers.agg import AggProvider
from hoshimi.core.providers.base import Account, Provider
from hoshimi.core.providers.bedrock import BedrockProvider
from hoshimi.core.providers.buddy import CodeBuddyProvider, WorkBuddyProvider
from hoshimi.core.providers.cline import ClineProvider
from hoshimi.core.providers.cloud import AzureProvider, CloudflareGatewayProvider, CloudflareWorkersProvider, GoogleProvider, VertexProvider
from hoshimi.core.providers.codearts import CodeArtsProvider
from hoshimi.core.providers.configured import regular_provider_classes
from hoshimi.core.providers.eac import EacProvider
from hoshimi.core.providers.gemini import GeminiProvider
from hoshimi.core.providers.kilo import KiloProvider
from hoshimi.core.providers.lobsterai import LobsteraiProvider
from hoshimi.core.providers.local import local_provider_classes
from hoshimi.core.providers.loomy import LoomyProvider
from hoshimi.core.providers.minimax import MinimaxProvider
from hoshimi.core.providers.nvidia import NvidiaProvider
from hoshimi.core.providers.opencode import OpencodeProvider
from hoshimi.core.providers.qoder import QoderCnProvider, QoderProvider
from hoshimi.core.providers.raccoon import RaccoonProvider
from hoshimi.core.providers.radius import RadiusProvider
from hoshimi.core.providers.trae import TraeProvider
from hoshimi.core.providers.typesafe import TypeSafeProvider
from hoshimi.core.providers.zcode import ZcodeProvider
from hoshimi.core.providers.zhipu import ZhipuProvider

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    cls.id: cls
    for cls in (
        CodeArtsProvider,
        EacProvider,
        CodeBuddyProvider,
        WorkBuddyProvider,
        LobsteraiProvider,
        QoderProvider,
        QoderCnProvider,
        TraeProvider,
        ClineProvider,
        LoomyProvider,
        RaccoonProvider,
        MinimaxProvider,
        GeminiProvider,
        OpencodeProvider,
        ZcodeProvider,
        KiloProvider,
        AggProvider,
        ZhipuProvider,
        NvidiaProvider,
        AzureProvider,
        CloudflareGatewayProvider,
        CloudflareWorkersProvider,
        GoogleProvider,
        VertexProvider,
        BedrockProvider,
        RadiusProvider,
        TypeSafeProvider,
    )
}

PROVIDER_ORDER = [
    "cline",
    "opencode",
    "kilo",
    "gemini",
    "zcode",
    "minimax",
    "trae",
    "qoder",
    "qodercn",
    "buddy",
    "workbuddy",
    "codearts",
    "loomy",
    "raccoon",
    "lobsterai",
    "eac",
    "agg",
    "zhipu",
    "nvidia",
]

PROVIDER_CLASSES.update(regular_provider_classes())
PROVIDER_CLASSES.update(local_provider_classes())
PROVIDER_ORDER.extend(provider_id for provider_id in PROVIDER_CLASSES if provider_id not in PROVIDER_ORDER)


def build_providers(
    client: httpx.Client,
    provider_accounts: dict[str, list[dict[str, Any]]],
    provider_options: dict[str, dict[str, Any]] | None = None,
    *,
    max_retries: int = 3,
    disabled: set[str] | None = None,
) -> dict[str, Provider]:
    provider_options = provider_options or {}
    disabled = disabled or set()
    providers: dict[str, Provider] = {}
    for provider_id, accounts_data in provider_accounts.items():
        if provider_id in disabled:
            continue
        cls = PROVIDER_CLASSES.get(provider_id)
        if cls is None:
            continue
        accounts = [Account(data, index=index, provider_id=provider_id) for index, data in enumerate(accounts_data)]
        if not accounts:
            if not cls.keyless:
                continue
            accounts = [anonymous_account(provider_id)]
        options = provider_options.get(provider_id, {})
        selected = client.for_provider(provider_id, options) if isinstance(client, NetworkClient) else client
        provider = cls(selected, accounts, options)
        # A provider may drop accounts it cannot use at all (the aggregator
        # lane wants a base_url and a key); with none left it is not a lane.
        if not provider.enabled():
            continue
        provider.max_retries = max_retries  # type: ignore[attr-defined]
        providers[provider_id] = provider

    # Keyless free lanes need no configuration at all: they are served whenever
    # the config does not explicitly disable them.
    for provider_id in PROVIDER_ORDER:
        cls = PROVIDER_CLASSES.get(provider_id)
        if cls is None or provider_id in providers or provider_id in disabled or not cls.keyless:
            continue
        options = provider_options.get(provider_id, {})
        selected = client.for_provider(provider_id, options) if isinstance(client, NetworkClient) else client
        provider = cls(selected, [anonymous_account(provider_id)], options)
        provider.max_retries = max_retries  # type: ignore[attr-defined]
        providers[provider_id] = provider
    return providers


def anonymous_account(provider_id: str) -> Account:
    """The placeholder account a keyless lane runs on (no credential in it)."""
    return Account(
        {"id": f"{provider_id}-public"},
        index=0,
        provider_id=provider_id,
        anonymous=True,
    )


def real_account_count(provider: Provider) -> int:
    """Configured accounts on this provider, ignoring the keyless placeholder."""
    return sum(1 for account in provider.accounts if not account.anonymous)
