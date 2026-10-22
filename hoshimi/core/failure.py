"""How an upstream failure is classified, and what that decides.

When a station refuses a call a gateway has to answer two different questions,
and they are not the same question:

* *Could another account answer this?* — the pool's question. On a free lane
  402 is "this account is out of allowance", which is precisely the moment
  rotation helps, so it is quota rather than failure.
* *Does this model still exist?* — the catalogue's question. A 404 is not an
  account problem at all: the station does not know that id any more.

pi-free answers both for a model *picker* (`lib/auto-fallback/classifier.ts`
and `blacklist.ts`): there the recoverable axis is the model, because the
picker is what chooses it. This gateway does not choose the model — the client
does — so the recoverable axis here is the account, and the model question gets
a store of its own below. Without one, a station that dropped an id is hit on
every turn and never once says so.

The status table is transcribed from pi-free's classifier, unmodified: its
recoverable set is what a free tier actually answers, down to Cloudflare's
521-527 and Anthropic's 529, and its unrecoverable set is what switching away
would only run into again.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

RECOVERABLE = "recoverable"
"""Another account might answer this — the pool should keep going."""

UNRECOVERABLE = "unrecoverable"
"""Switching would hit the same wall: the caller's request or its credentials."""

UNKNOWN = "unknown"
"""No table entry. Treated as recoverable — see :func:`classify_status`."""

# pi-free's `RECOVERABLE_HTTP_STATUSES`. 402 is the one that matters most on a
# free lane: "Payment Required" is how a free tier says the allowance is gone,
# and it used to fall through here as an opaque 502-shaped failure.
RECOVERABLE_STATUSES: frozenset[int] = frozenset(
    {
        402,  # Payment Required — quota used up, on free tiers especially
        408,  # Request Timeout
        409,  # Conflict — server-side state, may be transient
        425,  # Too Early
        429,  # Too Many Requests — the explicit rate-limit signal
        500,
        502,
        503,
        504,
        507,  # Insufficient Storage — transient on free tiers
        521,
        522,
        523,
        524,
        525,
        526,
        527,  # Cloudflare's way of saying "not me, the origin"
        529,  # Anthropic: overloaded
    }
)

# pi-free's `UNRECOVERABLE_HTTP_STATUSES`. Note that 401/403 sit here even
# though this gateway treats them as *account* problems elsewhere: a status can
# be recoverable by rotating credentials and unrecoverable by retrying, and
# these two tables only ever answer the second question.
UNRECOVERABLE_STATUSES: frozenset[int] = frozenset(
    {400, 401, 403, 404, 405, 406, 410, 415, 418, 422, 451}
)

MODEL_GONE_STATUSES: frozenset[int] = frozenset({404, 410})
"""The two statuses that are about the id rather than about the caller.

404 is "no such model" and 410 is "this model was removed"; both are also
unrecoverable for retrying, which is why they are a subset of
:data:`UNRECOVERABLE_STATUSES` and a separate, model-scoped signal on top.
"""

MODEL_GONE_PROSE_STATUSES: frozenset[int] = frozenset({400, 405, 406, 415, 418, 422, 451})
"""Statuses whose number says "your request" but whose body may say otherwise.

400 and 422 are how a gateway reports a request it judged badly, and a
request naming an id the station has never heard of is the commonest such
request. The number is right about *whose* problem this is and wrong about
*which* problem it is — which is precisely the case for asking the body, and
the only one: the alternative is a caller who is handed `400` on every turn
by a station that has already told us the id is gone, with the gateway
repeating the trip to learn it once more.

401, 403, 402 and 429 are deliberately absent. Those numbers answer the
account question, and prose about a model cannot contradict them any more
than prose about a model could contradict a `404`. A `5xx` is absent for the
opposite reason: a failure page is not an answer about anything — it is the
station saying it could not answer — and reading one as a verdict would let
a bad afternoon ban an id for the session.
"""


def classify_status(status: int) -> str:
    """Whether another account might answer this call.

    A status outside both tables is :data:`UNKNOWN`, and callers must treat it
    as recoverable: hiding a real outage behind "we don't recognize this code"
    is the worse mistake on a lane whose whole point is that it is free and
    therefore flaky.
    """
    if status in RECOVERABLE_STATUSES:
        return RECOVERABLE
    if status in UNRECOVERABLE_STATUSES:
        return UNRECOVERABLE
    return UNKNOWN


def is_recoverable(status: int) -> bool:
    """Should another account be given this call? :data:`UNKNOWN` says yes."""
    return classify_status(status) != UNRECOVERABLE


def is_model_gone(status: int, text: str = "") -> bool:
    """Did the station answer about the id rather than about the request?

    The status is asked first and never overturned — a body mentioning quota
    does not rename a `404`. Only the statuses whose number says "your
    request" go on to the body, and then only if there is a body to read
    (:data:`MODEL_GONE_PROSE_STATUSES`).
    """
    if status in MODEL_GONE_STATUSES:
        return True
    if text and status in MODEL_GONE_PROSE_STATUSES:
        return body_says_model_gone(text)
    return False


# -- the same answer, spelled out in the body ------------------------------- #

# Transcribed from dsh's `channel-pack/src/dead-model-store.ts`, which carries
# a full page of measured misjudgements beside each pattern. Those notes are
# the reason for the shapes below; the three that changed the code:
#
# * a window of "N characters after the id" cannot be written at all, because
#   `.` is both a decimal point inside ids and a sentence end — so
#   `model x is fine. the container image does not exist` matches one of
#   those and not the other. The fix is adjacency, not a longer window;
# * `model_not_found` appears in telemetry as a *field name*, so it is only
#   accepted at the end of a clause — a comma after it is still another field;
# * `supported` and `available` are not evidence of removal. "not supported
#   in this plan" and "not available in your region" both come back when the
#   plan or the region changes, and hiding a model for a session over either
#   is a far worse answer than asking again.
_GONE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"model\s+not\s+found", re.IGNORECASE),
    re.compile(r"(?:unknown|no\s+such|nonexistent)\s+model", re.IGNORECASE),
    re.compile(r"\bmodel[_-]not[_-]found\b(?=\s*(?:[.!;?)}\]'\"]|$))", re.IGNORECASE),
)

# Wrapped in a non-capturing group, and it has to be: this is spliced into a
# pattern anchored at `^`, and without the group the top-level alternation
# would let every branch but the first escape the anchor.
_GONE_TAIL = r"(?:(?:does\s*not|doesn'?t)\s+(?:exist|found)|\bnot\s+(?:found|exist)|不存在|未找到|未上线|已下线|已下架)"

_GONE_ID_TOKEN = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,63}"
"""An id-shaped token, dot and slash and hyphen included.

The dots are not optional decoration: every id this gateway serves has at
least one, and a token class without them stops at the first decimal point
in `deepseek-v4.1-flash`. Spaces are excluded so that the `x` in
`model x is fine` stops being a candidate id — its next word is `is`, which
carries no verdict at all.
"""

# Quote characters a station may wrap an id in, backtick included: ``The model
# `x` does not exist`` puts one on *both* sides, and a pattern that only allows
# one never reaches the verdict. Kept as bare characters rather than as a class
# — splicing a bracketed class into another class closes it early and silently
# demands a literal `]` from the message, so it matches nothing at all.
_GONE_QUOTES = r"""\"'\x60"""

_GONE_SUBJECT = re.compile(rf"(?:model|模型)[{_GONE_QUOTES}]?[\s,]*", re.IGNORECASE)
_GONE_SUBJECT_ID = re.compile(rf"(?:model|模型)[\s{_GONE_QUOTES}]*({_GONE_ID_TOKEN})", re.IGNORECASE)
# Anchored by `match`, not by `^`: Python's `Pattern.match(text, pos)` still
# reads `^` as "start of string", so a caret here would make the verdict
# unreachable from any position but the first — every adjacent verdict would
# silently fail and the whole pattern would look like it worked.
_GONE_VERDICT = re.compile(rf"[{_GONE_QUOTES}]?[\s,]*{_GONE_TAIL}", re.IGNORECASE)

_NOT_GONE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brate\s*-?\s*limit\b", re.IGNORECASE),
    re.compile(r"\btoo\s+many\s+requests\b", re.IGNORECASE),
    re.compile(r"\bquota\b", re.IGNORECASE),
    re.compile(r"\binsufficient\b", re.IGNORECASE),
    re.compile(r"\bunauthor|\bforbidden\b|\binvalid\s+(?:token|api\s*key|credential)", re.IGNORECASE),
    # `expired` needs a subject. Without one it deletes the ordinary removal
    # sentence `model expired-v3 does not exist`, and a model lease ending is
    # itself a form of removal.
    re.compile(r"\b(?:token|session|credential|key|auth|lease)\s+(?:has\s+|hasn'?t\s+|is\s+|was\s+)?expired\b", re.IGNORECASE),
    re.compile(r"\bexpired\s+(?:token|session|credential|key|auth)\b", re.IGNORECASE),
    # The Chinese side is deliberately loose — these err toward *not* hiding
    # a model, which is the safe direction: a miss costs one more round trip,
    # a hit costs the caller an hour of a model they can no longer see.
    re.compile(r"限流|限速|额度|余额|积分|排队|繁忙|未登录|认证|鉴权|凭据"),
)


def body_says_model_gone(text: str) -> bool:
    """Is this body saying the id itself does not exist?

    Exclusions first, then the outright phrases, then adjacency. Every step
    is load-bearing and the order is the argument: an account complaint
    mentioning a model is still an account complaint, and marking the model
    gone over one would hide a working model for the rest of the session.
    """
    if not text:
        return False
    if any(pattern.search(text) for pattern in _NOT_GONE_PATTERNS):
        return False
    if any(pattern.search(text) for pattern in _GONE_PATTERNS):
        return True
    return _adjacent_gone(text)


def _adjacent_gone(text: str) -> bool:
    """Does some id appear with a removal verdict *immediately* after it?

    Adjacency instead of a window, and anchored instead of searched: the
    search is what makes `model x is fine. the container image does not
    exist` a match, since everything after `x` would be scanned for a
    verdict somewhere along the way.
    """
    for subject in _GONE_SUBJECT.finditer(text):
        if _GONE_VERDICT.match(text, subject.end()):
            return True
    for subject in _GONE_SUBJECT_ID.finditer(text):
        if _GONE_VERDICT.match(text, subject.end()):
            return True
    return False


# -- two more sentences that arrive wearing somebody else's status ---------- #

MODEL_SATURATION_BUSINESS_CODE = 14003
"""The buddy gateway's code for "this model is saturated right now".

Its own payload settles the question: ``actions`` offers `SWITCH_MODEL` and
**no** account-switching option. Measured on 2026-10-05 — four unrelated
accounts all hit it inside twenty seconds, and each was then locked for a
full hour by the fallback duration, because a model-level event was read
as an account-level one. The same shape as zcode's 3009.
"""

MODEL_SATURATION_PATTERN = re.compile(r"模型繁忙|请求量饱和|model\s+busy|currently\s+saturated", re.IGNORECASE)
"""The prose fallback, and deliberately narrow.

`busy` or `saturated` alone would match a reply that merely discusses a
busy server, so only the phrases the gateway is observed to use are listed
— and the caller insists on a failing status first anyway.
"""

POLICY_BLOCK_BUSINESS_CODE = 11140
"""The buddy gateway's code for "this account is being blocked".

Measured as HTTP 403 across four accounts and two different ones: it is
per-account and unrelated to what was asked, so rotating is the answer —
refreshing the credential is not, and would only burn a round on the
refresh endpoint too.
"""

POLICY_BLOCK_PATTERN = re.compile(r"request\s+illegal|安全审核|safety\s+review", re.IGNORECASE)


def _business_code(text: str) -> Any:
    """The top-level ``code`` of a JSON body, or None when there isn't one.

    Read structurally rather than by searching the text for the digits:
    a ``requestId`` is a UUID, so any number at all can occur in a body by
    chance and a substring match would produce a confident wrong answer.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload.get("code") if isinstance(payload, dict) else None


def _code_is(text: str, expected: int) -> bool:
    code = _business_code(text)
    return code is not None and (code == expected or str(code) == str(expected))


def is_model_saturation(status: int | None, text: str) -> bool:
    """Is the *model* saturated, rather than this account being limited?

    The two are not a matter of wording — they take opposite actions. A
    rate limit means "this account, on this model": another account may
    have its own allowance, so the pool should keep going and the account
    should be held back. Saturation means "this model, for everybody": every
    account in the pool meets the same wall, so rotating turns one honest
    refusal into N slow ones and then reports "every account is limited",
    which is both slower and untrue.

    So the check has to come **before** any plain 429 branch: saturation
    arrives as a 429, and reading the number first is exactly what makes
    the two indistinguishable.

    ``status`` is None when there is no HTTP status to read — an error frame
    carried on a 200 stream, where the failure is the frame. Only a status
    that is known to be failing can clear this gate; a healthy 200 with the
    phrase in it is a reply about busy servers, not a refusal.
    """
    if (status is not None and status < 400) or not text:
        return False
    return _code_is(text, MODEL_SATURATION_BUSINESS_CODE) or bool(MODEL_SATURATION_PATTERN.search(text))


def is_policy_block(status: int | None, text: str) -> bool:
    """Is this account being blocked by a safety policy?

    Like auth failure it is account-scoped and rotation helps — but the
    renewal path does not, and the duration is not in the payload.
    """
    if (status is not None and status < 400) or not text:
        return False
    return _code_is(text, POLICY_BLOCK_BUSINESS_CODE) or bool(POLICY_BLOCK_PATTERN.search(text))


# -- a request too long for the model ---------------------------------------- #

CONTEXT_OVERFLOW_PATTERN = re.compile(
    r"context_length_exceeded|context[ _]length|context[ _]window|maximum context|exceed(?:s|ed)? (?:the )?context limit|"
    r"prompt is too long|input is too long|too many (?:input )?tokens|token[ _]limit[ _]exceeded|input token count|"
    r"exceeds the maximum number of tokens|request too large|maximum (?:input|prompt) length|"
    r"上下文长度|上下文窗口|超出最大长度|超过最大长度|超过了最大|超出.{0,6}长度限制|输入过长|token\s*数量?超(?:过|出)",
    re.IGNORECASE,
)
"""Every wording seen for "this request does not fit in the model's context window".

OpenAI's ``context_length_exceeded`` code and "maximum context length",
Anthropic's "prompt is too long", Gemini's "input token count … exceeds the
maximum number of tokens", OpenRouter's ``token_limit_exceeded``, and the
Chinese stations' own. Read before the quota markers, which a phrase like
"context limit exceeded" would otherwise fall into: an overflow is about the
request, so another account only meets the same wall, and holding this one
back for it would be wrong twice.
"""

_OVERFLOW_LIMIT_PATTERNS = (
    re.compile(r"maximum context length is\s*(\d[\d,]*)", re.IGNORECASE),
    re.compile(r"context (?:window|length|limit) (?:of|is)\s*(\d[\d,]*)", re.IGNORECASE),
    re.compile(r">\s*(\d[\d,]*)\s*maximum", re.IGNORECASE),
    re.compile(r"maximum (?:number of tokens allowed|allowed tokens|input tokens?)\s*(?:is|\(|:)?\s*(\d[\d,]*)", re.IGNORECASE),
    re.compile(r"(?:limit|最大)[^\d]{0,12}(\d[\d,]{3,})\s*(?:tokens?|tok)", re.IGNORECASE),
)


def is_context_overflow(status: int | None, text: str) -> bool:
    """Did the station refuse the request for not fitting in the model's context window?

    ``status`` is None for an error frame on a 200 stream. Only a refusal can
    say so (400, 413 and 422 are how stations do): a healthy reply that
    talks about context windows is an answer, not a verdict.
    """
    if not text or (status is not None and status not in (400, 413, 422)):
        return False
    return bool(CONTEXT_OVERFLOW_PATTERN.search(text))


def context_limit_in(text: str) -> int | None:
    """The window an overflow refusal names ("maximum context length is 131072 tokens"), when it names a plausible one."""
    for pattern in _OVERFLOW_LIMIT_PATTERNS:
        match = pattern.search(text or "")
        if match:
            value = int(match.group(1).replace(",", ""))
            if 1024 <= value <= 20_000_000:
                return value
    return None


# "…将在 2026-09-11 18:08:17 UTC+8 重置" and its international twin
# "… your usage will reset at 2026-09-17 09:09:36 UTC+8, alternatively, …".
# Both spell out the offset, and it is captured rather than assumed: the
# international station is not in UTC+8, and hard-coding it once already
# cost the reset time on that station.
_RESET_TIME_PATTERN = re.compile(
    r"(?:将在|reset\s+at)\s+(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(UTC[+-]\d{1,2}(?::\d{2})?)",
    re.IGNORECASE,
)

_UTC_OFFSET_PATTERN = re.compile(r"UTC([+-])(\d{1,2})(?::(\d{2}))?")


def parse_reset_after(text: str, *, now: float | None = None) -> float | None:
    """Seconds until the station says a limit resets. None = it did not say.

    None and zero are different answers and must stay different: zero is
    "released, use it at once", None is "the server declared nothing, guess
    conservatively". Collapsing them either locks a freshly released
    account for an hour or re-picks a limited one immediately.
    """
    match = _RESET_TIME_PATTERN.search(text)
    if match is None:
        return None
    offset = _UTC_OFFSET_PATTERN.fullmatch(match.group(2).upper())
    if offset is None:
        return None
    hours, minutes = int(offset.group(2)), int(offset.group(3) or 0)
    sign = -1.0 if offset.group(1) == "-" else 1.0
    stamp = match.group(1)
    # Parsed as a struct and reassembled aware in one step: the station gives
    # the offset beside the time, and a naive value would be wrong the moment
    # the two spellings disagree about seconds.
    try:
        fields = time.strptime(stamp, "%Y-%m-%d %H:%M:%S" if stamp.split()[-1].count(":") == 2 else "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    aware = datetime(*fields[:6], tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes)))
    return max(0.0, aware.timestamp() - (time.time() if now is None else now))


def parse_retry_after(raw: str | None, *, now: float | None = None) -> float | None:
    """Seconds to wait, from a ``Retry-After`` header. None = not declared.

    pi-free keeps one implementation of this and documents why twice over
    (`retry-after.ts`), because both of its traps are silent:

    * **Zero is a legal answer** — "released, go now" — and must not be read
      as absence. A falsy check here would make a freshly released account
      wait out a fallback it no longer owes.
    * **A bare number is seconds, not a date.** Parsed as a date, ``"900"``
      becomes some instant in 1970, the difference is a large negative, and
      a clamp turns that into zero — the same silent wrong answer as above,
      from a header the station sent correctly.

    A negative number is neither: it is a malformed header, and the caller
    must fall back to its own conservative guess rather than treat a broken
    value as permission to go at once.
    """
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    try:
        seconds = float(text)
    except ValueError:
        seconds = None
    if seconds is not None:
        # A header of `inf` or `nan` is not a wait time, and a clamp would
        # quietly turn both into a number the station never asked for.
        return seconds if math.isfinite(seconds) and seconds >= 0.0 else None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        # An HTTP date without a zone is UTC by definition (RFC 9110).
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, moment.timestamp() - (time.time() if now is None else now))


# -- the model-scoped store -------------------------------------------------- #


@dataclass(frozen=True)
class DeadEntry:
    """One banned (provider, model) pair, and why it is here."""

    strikes: int
    since: float
    last_at: float
    reason: str
    hard: bool

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        moment = time.time() if now is None else now
        return {
            "strikes": self.strikes,
            "reason": self.reason,
            "hard": self.hard,
            "age_seconds": round(max(0.0, moment - self.since), 3),
        }


class DeadModelStore:
    """Which (provider, model) pairs the station has stopped answering.

    Both rules are pi-free's (`lib/auto-fallback/blacklist.ts`) and both exist
    because the signal decays — a free lane flips a model in and out of
    availability, so one bad afternoon must not ban an id forever:

    * a strike older than the TTL window is forgotten, and the id gets another
      chance;
    * ``max_strikes`` inside that window promotes the entry to a hard ban the
      TTL no longer lifts — that id is gone, not flaky.

    In memory only, and deliberately so: "this model was bad last week" is a
    claim that decays faster than a restart takes.

    Thread-safe: the pool serves from more than one thread and a strike is
    recorded from the failure path.
    """

    def __init__(self, *, ttl: float = 600.0, max_strikes: int = 3) -> None:
        self.ttl = float(ttl)
        self.max_strikes = int(max_strikes)
        self._entries: dict[tuple[str, str], DeadEntry] = {}
        self._lock = threading.Lock()

    def strike(self, provider: str, model: str, *, reason: str = "", now: float | None = None) -> DeadEntry:
        """Record one failure. Returns the entry, which may now be banned."""
        moment = time.time() if now is None else now
        key = (str(provider), str(model))
        with self._lock:
            current = self._entries.get(key)
            if current is not None and not current.hard and moment - current.since > self.ttl:
                current = None  # the window expired; the streak starts over
            strikes = 1 if current is None else current.strikes + 1
            entry = DeadEntry(
                strikes=strikes,
                since=moment if current is None else current.since,
                last_at=moment,
                reason=str(reason) or (current.reason if current else ""),
                hard=(current.hard if current else False) or strikes >= self.max_strikes,
            )
            self._entries[key] = entry
            return entry

    def is_dead(self, provider: str, model: str, *, now: float | None = None) -> bool:
        """Should the gateway refuse this pair without asking upstream?"""
        moment = time.time() if now is None else now
        with self._lock:
            entry = self._entries.get((str(provider), str(model)))
        if entry is None:
            return False
        if entry.hard:
            return True
        return moment - entry.since <= self.ttl

    def clear(self, provider: str, model: str) -> None:
        """An answer arrived: the id is back and the streak is over."""
        with self._lock:
            self._entries.pop((str(provider), str(model)), None)

    def clear_all(self) -> int:
        """Forget every ban. Returns how many were dropped."""
        with self._lock:
            dropped = len(self._entries)
            self._entries.clear()
        return dropped

    def snapshot(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Every tracked pair, dead or not, for a diagnostics surface."""
        moment = time.time() if now is None else now
        with self._lock:
            items = list(self._entries.items())
        out = []
        for (provider, model), entry in items:
            row = {"provider": provider, "model": model, **entry.as_dict(moment)}
            row["dead"] = self.is_dead(provider, model, now=moment)
            out.append(row)
        return sorted(out, key=lambda row: (row["provider"], row["model"]))

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# -- the account-scoped store ---------------------------------------------- #


class AccountCooler:
    """Accounts a station has told us to stop offering, and until when.

    A limit and a block are different sentences and want different numbers.
    A limit is expected to lift on its own, and the server usually says when
    — so the caller passes the real reset time when there is one and a full
    hour when there is not, on the reasoning that a wrong "wait longer" costs
    one turn while a wrong "go now" costs a round trip per account. A policy
    block comes with no time at all and is not the same event: it needs a
    duration short enough to let a recovered account back in and long enough
    to survive the retries of a single agent turn, which a half hour does.

    In memory and per lane, deliberately: "which of my accounts is out" is a
    claim about a running pool that decays faster than a restart takes.
    """

    def __init__(self, *, policy_block_seconds: float = 1800.0) -> None:
        self.policy_block_seconds = float(policy_block_seconds)
        self._holds: dict[tuple[str, str], tuple[float, str]] = {}
        self._lock = threading.Lock()

    def hold(self, provider: str, account: str, seconds: float, *, reason: str = "", now: float | None = None) -> float:
        """Hide this account for ``seconds``. Returns the moment it comes back."""
        moment = time.time() if now is None else now
        until = moment + max(0.0, float(seconds))
        key = (str(provider), str(account))
        with self._lock:
            current = self._holds.get(key)
            # Never shorten a hold that is already running: two refusals in
            # one direction must not cancel each other out.
            if current is not None and current[0] > until:
                return current[0]
            self._holds[key] = (until, str(reason))
            return until

    def blocked(self, provider: str, account: str, *, now: float | None = None) -> bool:
        """Is this account still held back?"""
        moment = time.time() if now is None else now
        with self._lock:
            entry = self._holds.get((str(provider), str(account)))
            if entry is None:
                return False
            if entry[0] <= moment:
                del self._holds[(str(provider), str(account))]
                return False
            return True

    def clear(self, provider: str, account: str | None = None) -> int:
        """Let accounts back in. None clears the whole lane. Returns the count."""
        with self._lock:
            if account is None:
                dropped = sum(1 for key in self._holds if key[0] == str(provider))
                for key in [key for key in self._holds if key[0] == str(provider)]:
                    del self._holds[key]
                return dropped
            return 1 if self._holds.pop((str(provider), str(account)), None) else 0

    def snapshot(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """What is held, for a diagnostics surface."""
        moment = time.time() if now is None else now
        with self._lock:
            items = [(key, value) for key, value in self._holds.items() if value[0] > moment]
        return sorted(
            (
                {
                    "provider": provider,
                    "account": account,
                    "reason": reason,
                    "seconds_left": round(until - moment, 3),
                }
                for (provider, account), (until, reason) in items
            ),
            key=lambda row: (row["provider"], row["account"]),
        )

    def __len__(self) -> int:
        with self._lock:
            return len(self._holds)
