"""What a model *is*, according to models.dev — never what a station will allow.

A station's own listing names ids and nothing else, so a lane with no vetted
capability table reports every model as inert: not vision, not reasoning. This
module fills that in from models.dev, which pi-free also does
(``lib/model-metadata.ts``) and which is worth porting — but only half of it.

**What is taken:** whether the model reasons, and whether it takes images.
Those are properties of the *model*. `glm-5v-turbo` takes an image whether
LobsterAI proxies it or SambaNova does.

**What is deliberately not taken:** the context window, the output ceiling, and
the thinking menu. Those are properties of an *endpoint*, and models.dev indexes
by a flat id across 226 providers, so the same id answers with whatever limits
one particular reseller publishes. Probing the catalogue for the ids Hoshimi
actually serves shows the shape of the problem plainly: `deepseek-v4-flash`
comes back as a 1M-context model because that is Sensenova's number,
`qwen3.7-max` as 1M because that is Alibaba's token plan, and `kimi-k2.7-code`
returns an output ceiling equal to its context, which is a plan artifact rather
than a model. Reporting any one of those as *this station's* limit would be a
number nobody measured, presented as fact — and `max_output` is worse than a
label here, because it reaches the wire through the token budget.

The thinking menu is left out for a second reason: a menu is a claim about what
the wire accepts, and every menu Hoshimi ships was verified against that
station's own behaviour (C17–C19). models.dev cannot verify anything about the
station in front of us.

**Nothing here can cost a turn.** The bundled snapshot answers immediately and
offline; the live catalogue is fetched by a daemon thread and only ever improves
the *next* lookup. A request path never waits on this module, and a network
failure is invisible by design.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace

import httpx

from hoshimi.core.catalog import ModelSpec

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
"""The same catalogue pi-free reads (`constants.ts`)."""

FETCH_TIMEOUT = 8.0
CACHE_TTL = 300.0
"""pi-free refreshes on the same five-minute window."""

SNAPSHOT: dict[str, tuple[bool, bool]] = {
    # Bundled so an install with no egress still gets real facts. Generated from
    # the catalogue on 2026-10-13; ids Hoshimi serves that have no capability
    # table of their own. The value is (reasoning, vision).
    "deepseek-v4-flash": (True, False),
    "deepseek-v4-pro": (True, False),
    "MiniMax-M3": (True, True),
    "MiniMax-M2.7": (True, False),
    "qwen3.7-max": (True, False),
    "qwen3.7-plus": (True, True),
    "qwen3.6-plus": (True, True),
    "kimi-k2.7-code": (True, True),
    "kimi-k2.7-code-highspeed": (True, True),
    "kimi-k2.6": (True, True),
    "kimi-k2.5": (True, True),
    "doubao-seed-2-1-pro-260628": (True, True),
    "doubao-seed-2-1-turbo-260628": (True, True),
    "doubao-seed-2-0-code-preview-260215": (False, False),
    "glm-5.2": (True, False),
    "glm-5.1": (True, False),
    "glm-5v-turbo": (True, True),
    "glm-5": (True, False),
}
"""`id -> (reasoning, vision)`. One id here is not in the catalogue at all
(`qwen3.5-plus-2026-04-20`): a dated upstream id that no provider has published
yet, so it stays inert rather than borrowing a sibling's answer."""


@dataclass(frozen=True)
class ModelFacts:
    """The two endpoint-independent facts, and where they came from."""

    reasoning: bool
    vision: bool
    source: str


class ModelsDevStore:
    """The live catalogue, plus the snapshot that answers when it cannot be had."""

    def __init__(self, *, url: str = MODELS_DEV_URL, timeout: float = FETCH_TIMEOUT, ttl: float = CACHE_TTL) -> None:
        self.url = url
        self.timeout = float(timeout)
        self.ttl = float(ttl)
        self._lock = threading.Lock()
        self._index: dict[str, tuple[bool, bool]] = {}
        self._fetched_at = 0.0
        self._refreshing = False

    # -- the live half ----------------------------------------------------- #
    def facts(self, model_id: str) -> ModelFacts | None:
        """The facts for one id, never blocking on the network.

        The snapshot answers first because it is instant and offline. The live
        catalogue is consulted only once it has landed, and a lookup that needs
        it and finds it missing kicks off a background fetch for the next one —
        so the worst a request path ever pays here is a dict lookup.
        """
        key = str(model_id or "").strip()
        if not key:
            return None

        snapshot = SNAPSHOT.get(key)
        with self._lock:
            live = self._index.get(key)
            fresh = self._fetched_at > 0.0 and time.time() - self._fetched_at < self.ttl
            stale = not fresh
            in_flight = self._refreshing

        if stale and not in_flight:
            self._start_refresh()

        if live is not None:
            return ModelFacts(reasoning=live[0], vision=live[1], source="live")
        if snapshot is not None:
            return ModelFacts(reasoning=snapshot[0], vision=snapshot[1], source="snapshot")
        return None

    def _start_refresh(self) -> None:
        """Fetch the catalogue on a daemon thread; a failure is simply next time."""
        with self._lock:
            self._refreshing = True

        def work() -> None:
            try:
                index = self._fetch_index()
            except Exception:
                logger.debug("models.dev unavailable; the bundled snapshot stands", exc_info=True)
                return
            finally:
                with self._lock:
                    self._fetched_at = time.time()
                    self._refreshing = False
            with self._lock:
                self._index = index
            logger.debug("models.dev: %d ids", len(index))

        threading.Thread(target=work, name="hoshimi-models-dev", daemon=True).start()

    def _fetch_index(self) -> dict[str, tuple[bool, bool]]:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(self.url, headers={"User-Agent": "hoshimi/1.0"})
            response.raise_for_status()
            payload = response.json()
        return _index_of(payload)

    # -- testing seams ----------------------------------------------------- #
    def preload(self, index: dict[str, tuple[bool, bool]], *, now: float | None = None) -> None:
        """Install a catalogue as though a fetch had landed."""
        with self._lock:
            self._index = dict(index)
            self._fetched_at = time.time() if now is None else now

    def reset(self) -> None:
        with self._lock:
            self._index = {}
            self._fetched_at = 0.0
            self._refreshing = False


def _index_of(payload: object) -> dict[str, tuple[bool, bool]]:
    """Flatten `{provider: {models: {id: …}}}` into one id -> facts map.

    The first provider to publish an id wins, which is pi-free's rule too. It is
    also why limits are never read from here: "first" is an accident of
    iteration order, not an endorsement of anyone's numbers.
    """
    index: dict[str, tuple[bool, bool]] = {}
    if not isinstance(payload, dict):
        return index
    for provider in payload.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, model in models.items():
            if not isinstance(model, dict):
                continue
            inputs = (model.get("modalities") or {}).get("input") if isinstance(model.get("modalities"), dict) else None
            index.setdefault(str(model_id), (bool(model.get("reasoning")), bool(inputs) and "image" in inputs))
    return index


MODELSDEV = ModelsDevStore()
"""The process-wide catalogue the spec layer reads."""


def enrich_spec(spec: ModelSpec, *, declared: bool = False) -> ModelSpec:
    """Fill the two facts a listing could not have said; never argue with a claim.

    ``declared`` is the whole gate: it says whether *anything at all* was said
    about this id — an operator's ``model_specs`` entry, or a vetted capability
    table. A declared id is left entirely alone, because a person or a table
    spoke for it and this module is not a better source than either.

    Without a declaration the spec came from a default, so its ``reasoning`` and
    ``vision`` are whatever a default happens to be — and a lane may build that
    default differently (``agg_spec`` sets ``can_disable_thinking=False`` by
    construction), which is why the gate is the declaration and not a field-by-
    field comparison against one particular default.
    """
    if declared:
        return spec
    facts = MODELSDEV.facts(spec.id)
    if facts is None:
        return spec
    reasoning = spec.reasoning or facts.reasoning
    vision = spec.vision or facts.vision
    if reasoning == spec.reasoning and vision == spec.vision:
        return spec
    return replace(spec, reasoning=reasoning, vision=vision)
