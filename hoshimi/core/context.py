"""Build every service once. The API and the command line both start here."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoshimi.core.agent.runtime import AgentRuntime
from hoshimi.core.config import Settings, load_settings
from hoshimi.core.conversations import ConversationStore
from hoshimi.core.credentials import collect_provider_accounts
from hoshimi.core.credits import CHECKIN_FILE_NAME, CheckinStore, CreditsService
from hoshimi.core.db import DB_FILE_NAME, Database
from hoshimi.core.login import LoginManager
from hoshimi.core.modelsdev import MODELSDEV
from hoshimi.core.net.client import NetworkClient
from hoshimi.core.paths import config_file_path, default_data_dir
from hoshimi.core.probe import PROBE_FILE_NAME, ProbeStore
from hoshimi.core.refresh import Refresher
from hoshimi.core.registry import Registry, build_registry
from hoshimi.core.stats import STATS_FILE_NAME, StatsStore
from hoshimi.core.updates import UpdateChecker


@dataclass
class Services:
    """Everything a command or a request handler needs.

    ``settings`` is the effective configuration (config file + CLI overrides), ``registry``
    owns provider HTTP clients and the enabled providers, ``db`` is the one
    ``hoshimi.db`` connection the stores below share, ``probe`` holds the last
    availability verdict per model, ``stats`` counts what the gateway served
    (and, through ``stats.live``, what it is serving right now),
    ``refresher`` renews credentials on a timer, ``updates`` asks PyPI
    whether a newer release exists, ``conversations`` keeps the
    Playground's conversations, and ``agent`` its tools' background commands
    and approvals.

    Built directly (tests, embedders), every store keeps its rows in memory.
    """

    settings: Settings
    registry: Registry
    db: Database = field(default_factory=Database)
    probe: ProbeStore = field(default_factory=ProbeStore)
    stats: StatsStore = field(default_factory=StatsStore)
    refresher: Refresher | None = None
    credits: CreditsService | None = None
    login: LoginManager | None = None
    updates: UpdateChecker | None = None
    conversations: ConversationStore = field(default_factory=ConversationStore)
    agent: AgentRuntime = field(default_factory=AgentRuntime)

    def __post_init__(self) -> None:
        MODELSDEV.network = self.settings.network
        self.stats.keep_days = self.settings.stats_keep_days
        if self.updates is not None:
            self.updates.network = self.settings.network

    @property
    def data_dir(self) -> Path:
        return default_data_dir()

    @property
    def config_path(self) -> Path:
        return config_file_path()

    def close(self) -> None:
        if self.refresher is not None:
            self.refresher.stop()
        if self.credits is not None:
            self.credits.stop()
        if self.login is not None:
            self.login.close()
        if self.updates is not None:
            self.updates.stop()
        self.agent.close()
        self.registry.close()
        # Writes are batched; whatever the last second counted lands now. Then
        # the database closes — a second close (the lifespan and the serve
        # command both close) is a no-op.
        self.stats.close()
        self.probe.close()
        self.db.close()

    def apply_settings(self, settings: Settings) -> bool:
        """Adopt a new configuration: rebuild the lanes it actually moved.

        Returns whether the pool was rebuilt. A settings save must land in the
        running gateway, not only in the file — otherwise adding an account
        means restarting the process, which is a strange thing to ask of an
        operator who is already talking to the web UI.

        Two details make this a rebuild rather than a save:

        - *what counts as moved* is the input the pool is built from, not the
          whole file. A port change rebuilds nothing: a rebuild makes fresh
          :class:`Account` objects, and a fresh account has forgotten that its
          refresh token was refused and that it is out of rotation until a
          human logs in again. That memory is worth more than uniformity.
        - *every service holding this settings object* is re-pointed. The
          renewal timer and the login manager each persist through their own
          reference, so leaving one behind would have it write the previous
          configuration over the file the operator just edited.
        """
        before = pool_inputs(self.settings)
        after = pool_inputs(settings)
        self.settings = settings
        self.stats.keep_days = settings.stats_keep_days
        MODELSDEV.network = settings.network
        if isinstance(self.registry.client, NetworkClient):
            self.registry.client.network = settings.network
        if self.refresher is not None:
            self.refresher.settings = settings
        if self.credits is not None:
            self.credits.settings = settings
        if self.login is not None:
            self.login.settings = settings
        if self.updates is not None:
            self.updates.network = settings.network
            # Switched on or off in the file: the timer follows without a restart.
            if settings.check_updates:
                self.updates.start()
            else:
                self.updates.stop()
        if before == after:
            return False
        self.registry.rebuild(
            after.accounts,
            after.options,
            default_model=settings.default_model,
            max_account_retries=settings.max_account_retries,
            disabled=after.disabled,
        )
        if isinstance(self.registry.client, NetworkClient):
            self.registry.client.network = settings.network
        return True


@dataclass(frozen=True)
class PoolInputs:
    """The inputs `build_registry` reads, as one comparable value."""

    accounts: dict[str, list[dict[str, Any]]]
    options: dict[str, dict[str, Any]]
    disabled: set[str]


def pool_inputs(settings: Settings) -> PoolInputs:
    """The configuration the provider pool is built from, as one comparable value.

    This is deliberately *not* the whole settings object: it is exactly what
    :func:`build_registry` consumes, so a save that moves anything else leaves
    the running pool — and the memory only it carries — alone.
    """
    accounts, options = collect_provider_accounts(settings)
    disabled = {pid for pid, cfg in settings.providers.items() if not cfg.enabled}
    return PoolInputs(accounts=accounts, options=options, disabled=disabled)


def build_services(settings: Settings | None = None) -> Services:
    """Load this gateway's configuration and build the provider registry.

    Supplied ``settings`` already carry any command-line overrides; otherwise
    ``config.json`` is read from Hoshimi's data directory.
    """
    if settings is None:
        settings = load_settings()
    pool = pool_inputs(settings)
    registry = build_registry(
        pool.accounts,
        pool.options,
        default_model=settings.default_model,
        max_account_retries=settings.max_account_retries,
        disabled=pool.disabled,
    )
    if isinstance(registry.client, NetworkClient):
        registry.client.network = settings.network

    def rebuild_after_login(login: LoginManager) -> None:
        """Adopt an account the moment a browser login files it in settings."""
        current = login.settings
        pool = pool_inputs(current)
        registry.rebuild(
            pool.accounts,
            pool.options,
            default_model=current.default_model,
            max_account_retries=current.max_account_retries,
            disabled=pool.disabled,
        )

    data_dir = default_data_dir()
    # One connection for every store; each imports (then renames) its old JSON file once.
    db = Database(data_dir / DB_FILE_NAME)
    return Services(
        settings=settings,
        registry=registry,
        db=db,
        probe=ProbeStore(data_dir / PROBE_FILE_NAME, db=db),
        stats=StatsStore(data_dir / STATS_FILE_NAME, db=db, keep_days=settings.stats_keep_days),
        refresher=Refresher(settings=settings, registry=registry),
        credits=CreditsService(settings=settings, registry=registry, store=CheckinStore(data_dir / CHECKIN_FILE_NAME, db=db)),
        login=LoginManager(settings=settings, on_persist=rebuild_after_login),
        updates=UpdateChecker(network=settings.network),
        conversations=ConversationStore(db),
    )
