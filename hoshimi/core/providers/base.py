"""Provider abstractions: accounts, base class, and error taxonomy."""

from __future__ import annotations

import logging
import random
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, ClassVar

import httpx

from hoshimi.core.catalog import ModelSpec, default_spec
from hoshimi.core.errors import UpstreamError
from hoshimi.core.failure import AccountCooler, is_recoverable, parse_reset_after
from hoshimi.core.modelsdev import enrich_spec

logger = logging.getLogger(__name__)


class ProviderError(UpstreamError):
    """Upstream failure with an HTTP status and body."""

    code = "upstream_error"
    exit_code = 5

    def __init__(self, status: int, body: str = "", *, retryable: bool = False, retry_after: float | None = None):
        super().__init__(body or f"upstream returned {status}", {"status": status})
        self.status = status
        self.body = body
        self.retryable = retryable
        # The station's own answer to "when", off the standard header. Kept
        # separate from `retryable` because the two disagree: a 500 is
        # retryable and names no time, a 401 names none either, and only a
        # rate limit speaks about both.
        self.retry_after = retry_after
        if 400 <= status < 600:
            # Pass a meaningful upstream status through to the client.
            self.http_status = status


class QuotaExceeded(ProviderError):
    """Rate limit / quota exhausted — the pool should rotate accounts."""

    code = "quota_exceeded"


class AuthFailed(ProviderError):
    """Credentials rejected — the pool should rotate or refresh."""

    code = "auth_failed"


class ModelSaturated(ProviderError):
    """The *model* is saturated, for every account alike.

    Not a pool question and not an account question: rotating would burn
    the whole pool against one wall and then report "every account is
    limited", which is both slower and a lie. The pool stands down and the
    station's own words go to the caller, who can change model.
    """

    code = "model_saturated"


class PolicyBlocked(AuthFailed):
    """The station is blocking this account on safety grounds.

    Account-scoped, so the pool rotates — but renewal is pointless (the
    credential is fine) and the account is held back for a while, or the
    next turn picks it first again and burns the same round.
    """

    code = "policy_blocked"


class ModelGone(ProviderError):
    """The station answered 404/410 for this id: it is the model, not the account.

    The pool still asks the other accounts before giving up — a station with a
    per-account catalogue can legitimately answer 404 for one of them — so this
    is raised only when every account agreed.
    """

    code = "model_gone"


class RefreshTokenRejected(AuthFailed):
    """The station refused the refresh token itself.

    Unlike a plain auth failure this is terminal for the account: the renewal
    credential is spent, so the pool stops offering the account until a human
    logs in again rather than burning a turn on every request.
    """

    code = "refresh_token_rejected"


# Claim / balance outcomes. Kept next to the protocol vocabulary because every
# adapter fills one in; the credits service and the API only ever read them.
CLAIM_CLAIMED = "claimed"
CLAIM_ALREADY = "already"
CLAIM_UNSUPPORTED = "unsupported"
CLAIM_FAILED = "failed"


class ClaimOutcome:
    """One account's result from a daily check-in attempt."""

    __slots__ = ("account", "covers_today", "credits", "message", "provider", "status")

    def __init__(self, provider: str, account: str, status: str, *, credits: int = 0, covers_today: bool = True, message: str = ""):
        self.provider = provider
        self.account = account
        self.status = status
        self.credits = credits
        self.covers_today = covers_today
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "account": self.account,
            "status": self.status,
            "credits": self.credits,
            "covers_today": self.covers_today,
            "message": self.message,
        }


class BalanceOutcome:
    """One account's remaining balance or quota window."""

    __slots__ = ("account", "available", "message", "provider", "unit")

    def __init__(self, provider: str, account: str, available: int | None, *, unit: str = "credit", message: str = ""):
        self.provider = provider
        self.account = account
        self.available = available
        self.unit = unit
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "account": self.account,
            "available": self.available,
            "unit": self.unit,
            "message": self.message,
        }


def _is_transient(exc: ProviderError) -> bool:
    """Is this the kind of refusal that time could change?

    Not a per-account answer. A rejected credential, an exhausted allowance,
    a held-back account and a dropped id are all *this account's* business,
    and each already has its own branch in the pool loop; waiting would only
    postpone the same verdict from the same account, which is the one thing
    a wait cannot fix.
    """
    if isinstance(exc, (AuthFailed, ModelGone)):
        return False
    if isinstance(exc, (QuotaExceeded, PolicyBlocked)):
        # Account-scoped: the answer is another account, not a moment.
        return False
    return is_recoverable(exc.status)


class Account:
    """A single upstream account (credential blob + metadata)."""

    def __init__(self, data: dict[str, Any], *, index: int = 0, provider_id: str = "", anonymous: bool = False):
        self.data = data
        self.index = index
        self.provider_id = provider_id
        self.anonymous = anonymous
        self.id = str(data.get("id") or f"{provider_id}-{index}")
        # Set by the refresher when the station rejected the refresh token:
        # the pool stops offering this account until a human logs in again.
        self.needs_login = False
        self.refresh_lock = threading.RLock()
        self.auth_revision = 0

    @property
    def credential(self) -> dict[str, Any]:
        return self.data

    def get(self, key: str, default: Any = None) -> Any:
        value = self.data.get(key)
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


_MODELS_TTL_SECONDS = 300

# A station that answers with less than half of what it had is a partial
# answer rather than a station that dropped half its catalogue in one
# instant: a paginated fetch cut short, an error page served with a 200.
# The threshold is pi-free's (`lib/provider-cache.ts` `saveProviderCacheGuarded`),
# and it is a fraction rather than a count because catalogue sizes differ by
# two orders of magnitude between a lane with three models and one with three
# hundred.
MODELS_SHRINK_FRACTION = 0.5


class Provider(ABC):
    """Base class for every API station."""

    id: str = ""
    display_name: str = ""
    # Keyless lanes need no configured account: they are built with a synthetic
    # one whenever the config does not disable them (see build_providers).
    keyless: ClassVar[bool] = False
    # Whether a probe round asks this station about its models by default. A
    # listing that is already the verdict (Kilo's free pool), or one gated on
    # the caller's own credential, has nothing a ping would learn.
    probeable: ClassVar[bool] = False
    # Whether this lane is gated on an authorization this install has to hold
    # (EAC's GitHub authorization). The API labels the row with it, so the
    # settings page can say "needs authorization" before an account exists.
    requires_authorization: ClassVar[bool] = False
    # Seconds an account stays out of rotation after a refusal that named no
    # time of its own. A wrong "wait longer" costs one turn; a wrong "go now"
    # costs a round trip per account on every turn until someone notices.
    quota_hold_seconds: ClassVar[float] = 3600.0
    # The same question with a different answer: a safety block comes with no
    # time field at all and is not the same event as running out of allowance.
    policy_block_seconds: ClassVar[float] = 1800.0
    # How long to wait before asking the *same* station again, when there is
    # no other account left to ask. Rotating is instant and free; waiting is
    # neither, so this only ever runs once the pool has nothing else to offer.
    backoff_base_seconds: ClassVar[float] = 0.25
    backoff_max_seconds: ClassVar[float] = 8.0
    backoff_retries: ClassVar[int] = 2
    max_retries: int

    def __init__(
        self,
        client: httpx.Client,
        accounts: list[Account],
        options: dict[str, Any] | None = None,
    ):
        self.client = client
        self.accounts = accounts
        self.options = options or {}
        self._lock = threading.Lock()
        self._models_cache: list[str] | None = None
        self._models_expires_at = 0.0
        self._models_shrunk = False
        self.catalog_failed = False
        self.cooler = AccountCooler()

    # -- credentials ------------------------------------------------------ #
    @classmethod
    def check_account(cls, data: dict[str, Any]) -> None:
        """Refuse a credential blob this lane could not use, before it reaches the config.

        ``config.json`` is the credential store, so an account that lands there
        is one the gateway has already promised to serve. A lane that discards
        a blob when the pool is built must say so *here*: an "add account" save
        that reports a credential the pool just threw away is telling the
        operator something untrue about their own gateway.

        The default trusts the caller, and deliberately so. Most lanes never
        look at a blob until they are asked to use it, and the lane is a better
        judge than a guess made from the outside. Only a lane that drops
        unusable credentials overrides this — raising ``ValidationError`` with
        the same sentence the pool would have given.
        """

    def enabled(self) -> bool:
        return bool(self.accounts)

    def fallback_models(self) -> list[str]:
        return []

    @property
    def cached_model_count(self) -> int | None:
        """Known model count, without performing discovery or acquiring its lock."""
        cached = self._models_cache
        return len(cached) if cached is not None else None

    def models(self) -> list[str]:
        now = time.time()
        cached = self._models_cache
        if cached is not None and now < self._models_expires_at:
            return list(cached)
        with self._lock:
            if self._models_cache is not None and time.time() < self._models_expires_at:
                return list(self._models_cache)
            previous = self._models_cache
            self.catalog_failed = False
            try:
                fetched = self.fetch_models()
            except Exception:  # noqa: BLE001 - model discovery is best-effort
                self.catalog_failed = True
                fetched = previous or []
            if self.catalog_failed and previous is not None:
                fetched = previous
            fresh = _dedupe(fetched or [])
            if not fresh:
                # Nothing came back at all. That already has an answer — the
                # lane's own list — and it is never held back by the guard
                # below, because a static list is not a shrunk answer.
                fresh = _dedupe(self.fallback_models())
                self._models_shrunk = False
            elif previous and len(fresh) < len(previous) * MODELS_SHRINK_FRACTION:
                fresh, self._models_shrunk = self._refuse_shrink(previous, fresh)
            else:
                self._models_shrunk = False
            self._models_cache = fresh
            self._models_expires_at = time.time() + _MODELS_TTL_SECONDS
            return list(self._models_cache)

    def _refuse_shrink(self, previous: list[str], fresh: list[str]) -> tuple[list[str], bool]:
        """Keep a catalogue that came back halved — once.

        A fetch that succeeds with a fraction of the ids is a partial answer
        more often than it is a station dropping half its catalogue in an
        instant: a paginated listing cut short, an error page served with a
        200. Refusing to overwrite costs one stale cycle.

        But only one. Asked an entire TTL later and it said the same thing,
        so the shrink is the station's own answer, and holding a catalogue of
        ids that no longer exist is its own bug — every one of them then
        fails at the wire instead of being absent from the list.
        """
        if self._models_shrunk:
            logger.warning(
                "%s: the catalogue stayed at %d of %d models a full cycle later; accepting it as the station's own answer",
                self.id,
                len(fresh),
                len(previous),
            )
            return fresh, False
        logger.warning(
            "%s: the catalogue came back at %d of %d models (likely a partial answer); keeping what we had until the next cycle",
            self.id,
            len(fresh),
            len(previous),
        )
        return previous, True

    def fetch_models(self) -> list[str]:
        """Optionally discover models from the upstream; default: none."""
        return []

    def specs(self) -> dict[str, ModelSpec]:
        """What each advertised id can do; see :mod:`hoshimi.core.catalog`.

        The default is deliberately inert: a station with no capability table
        gets `reasoning=False`, so the effort policy leaves its bodies alone
        and whatever the caller sent reaches the upstream unchanged.

        The two facts a flat listing cannot say — whether the model reasons and
        whether it takes images — are then filled from models.dev, which never
        touches a spec that a table already spoke for
        (:mod:`hoshimi.core.modelsdev`).
        """
        return {model_id: enrich_spec(default_spec(model_id, self.id)) for model_id in self.models()}

    # -- chat ------------------------------------------------------------- #
    @abstractmethod
    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield normalized chunks using one specific account."""

    def _stream_candidates(self, model: str) -> list[Account]:
        """The accounts a turn may run on, in order.

        The default is the whole pool. A provider that routes several stations
        (see the aggregator lane) narrows this to the accounts that actually
        advertise the requested model.
        """
        return self.accounts

    def stream(self, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield normalized chunks, rotating accounts on quota/auth errors."""
        candidates = self._stream_candidates(model)
        if not candidates:
            raise ProviderError(401, f"No accounts configured for provider {self.id!r}.")

        tried: set[str] = set()
        refreshed: set[str] = set()
        last_error: ProviderError | None = None
        attempts = min(len(candidates), getattr(self, "max_retries", len(candidates)))
        emitted = False
        retries_left = self.backoff_retries

        while True:
            account = self._pick_account(tried, candidates)
            if account is None:
                # The pool has nothing left to ask. A transient refusal is
                # the one answer that time can change, so the pool gets
                # another pass — but only one that has emitted nothing yet:
                # once content has gone out, a second answer would arrive as
                # a duplicate.
                if emitted or last_error is None or retries_left <= 0 or not _is_transient(last_error):
                    break
                # Attempt 0 on the first retry, so the ceiling doubles
                # upward rather than a later retry waiting out less than an
                # earlier one.
                attempt = self.backoff_retries - retries_left
                retries_left -= 1
                self._backoff(attempt, last_error)
                tried.clear()
                account = self._pick_account(tried, candidates)
                if account is None:
                    break
            elif len(tried) >= attempts:
                break
            tried.add(account.id)
            try:
                # Consume lazily; quota errors can also surface mid-stream.
                revision = account.auth_revision
                iterator = self.stream_raw(account, model, body)
                while True:
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        break
                    emitted = True
                    yield chunk
                return
            except ModelSaturated:
                # The model, not the account: every candidate meets the same
                # wall, so standing the pool down is both faster and the true
                # answer. Rotating would end as "every account is limited",
                # which is slower and untrue.
                raise
            except ProviderError as exc:
                last_error = exc
                if isinstance(exc, PolicyBlocked):
                    # Account-scoped, and the credential is not the problem:
                    # rotate, do not renew, and hold the account back or the
                    # next turn picks it first and burns the same round again.
                    self.cooler.hold(self.id, account.id, self.policy_block_seconds, reason="policy_blocked")
                    continue
                if isinstance(exc, AuthFailed):
                    if emitted:
                        raise  # A second account would duplicate the partial answer.
                    # Account-scoped by definition: the credentials are what
                    # failed, so renewal or another account is the answer.
                    if account.id not in refreshed:
                        refreshed.add(account.id)
                        if self._try_refresh(account, expected_revision=revision):
                            tried.discard(account.id)
                    continue
                if isinstance(exc, QuotaExceeded):
                    # Account-scoped on a free lane: this account is out of
                    # allowance and the next one may not be. Hold it for as
                    # long as the station said — or for the fallback when it
                    # said nothing, which is the usual case.
                    #
                    # The header is asked first: `Retry-After` is the standard
                    # spelling of this answer, the prose is a station's own
                    # dialect, and where both exist the standard one is the
                    # one the operator actually configured.
                    held = exc.retry_after
                    if held is None:
                        held = parse_reset_after(exc.body)
                    self.cooler.hold(
                        self.id,
                        account.id,
                        self.quota_hold_seconds if held is None else held,
                        reason="rate_limited",
                    )
                    continue
                if isinstance(exc, ModelGone):
                    # Id-scoped: keep asking, because a station with a
                    # per-account catalogue may still carry it for someone else.
                    continue
                # Everything else is judged by the status table: a 4xx about
                # the caller's own request fails identically on every account,
                # so burning the pool on it only makes the refusal slower.
                if not is_recoverable(exc.status):
                    break
                continue

        if last_error is not None:
            raise last_error
        raise ProviderError(502, f"Provider {self.id!r} exhausted all accounts.")

    def _backoff(self, attempt: int, exc: ProviderError) -> None:
        """Wait before asking the same station again.

        The station's own number wins where it gave one — `Retry-After` is
        the standard spelling of "not now", and the reset time in the prose
        is a dialect of it. Both are capped: a request path that parks for
        an hour is a worse answer than a failure the caller can route
        elsewhere, and the header is a claim about the station's own limiter
        rather than a licence to block a caller's turn.

        Without one, the wait is full jitter — `uniform(0, base * 2**attempt)`,
        itself capped. Jitter because a fleet of clients backing off in step
        is the thundering herd the retry was meant to escape, and the base
        is small because this runs only once the pool has nothing else.
        """
        declared = exc.retry_after
        if declared is None:
            declared = parse_reset_after(exc.body)
        if declared is not None:
            delay = min(declared, self.backoff_max_seconds)
        else:
            ceiling = min(self.backoff_max_seconds, self.backoff_base_seconds * (2**attempt))
            delay = random.uniform(0.0, ceiling)
        if delay > 0:
            time.sleep(delay)

    def _pick_account(self, exclude: set[str], candidates: list[Account] | None = None) -> Account | None:
        for account in candidates if candidates is not None else self.accounts:
            if account.id in exclude or account.needs_login:
                continue
            if self.cooler.blocked(self.id, account.id):
                # Held back by a refusal of its own; the pool works from the
                # accounts that have not just said no.
                continue
            return account
        return None

    # -- refresh ---------------------------------------------------------- #
    def renew_account(self, account: Account, *, expected_revision: int) -> bool:
        """Serialize scheduled and request-driven refreshes of rotating credentials."""
        with account.refresh_lock:
            if account.needs_login:
                return False
            if account.auth_revision != expected_revision:
                return True
            try:
                changed = self.refresh_account(account)
            except RefreshTokenRejected:
                account.needs_login = True
                raise
            if changed:
                account.auth_revision += 1
            return changed

    def _try_refresh(self, account: Account, *, expected_revision: int | None = None) -> bool:
        """Renew one account, never letting a station's own failure escape.

        A rejected refresh token is terminal: the account is taken out of
        rotation here, so the next turn does not spend one discovering the
        same fact again.
        """
        try:
            return self.renew_account(account, expected_revision=account.auth_revision if expected_revision is None else expected_revision)
        except RefreshTokenRejected:
            account.needs_login = True
            return False
        except (ProviderError, httpx.HTTPError, ValueError):
            return False

    def refresh_account(self, account: Account) -> bool:
        """Best-effort token refresh. Return True when credentials changed.

        Raise :class:`RefreshTokenRejected` when the station refused the
        refresh token itself; return False for anything worth retrying.
        """
        return False

    # -- credits ---------------------------------------------------------- #
    def claim_daily(self, account: Account) -> ClaimOutcome | None:
        """Claim the daily check-in. None = this station has no such endpoint.

        A station that answers "already claimed today" must still return an
        outcome with status ``CLAIM_ALREADY``, not None — None is reserved for
        lanes that simply do not offer the endpoint at all.
        """
        return None

    def balance_of(self, account: Account) -> BalanceOutcome | None:
        """Query the remaining balance or quota. None = unsupported."""
        return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
