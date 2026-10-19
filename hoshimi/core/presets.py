"""Known stations the aggregator lane can be pointed at.

An `agg` account is one line: it names its own ``base_url``, and that is what
makes the lane generic. This file exists so the line does not start with a web
search — an operator should have to supply a key, not look up what SambaNova
calls its root. Each entry is three facts: the URL, the environment variable
the upstream project reads its key from, and whether the station publishes a
model list without one.

The table is transcribed from pi-free's ``constants.ts`` (base URLs) and
``config.ts`` (key environment variables), which is where pi-free keeps the same
three facts for the same twenty-odd stations.

What is deliberately **not** here is a model list. pi-free fetches every one of
these catalogues live — only four of its providers ship a static list at all —
and a static table here would be wrong by the time the next station adds a
model. Listing models is the gateway's job, and ``agg`` already does it against
whatever the station publishes.

Three kinds of station are left out on purpose:

- **kilo / cline / qoder / opencode** — hoshimi has native lanes for these, with
  OAuth and credential refresh. Pointing ``agg`` at one would trade a renewing
  credential for a bearer token that expires.
- **openrouter** — not one of pi-free's own providers (it is a pi built-in that
  pi-free toggles), and already the worked example under ``agg`` in the README.
- **nvidia** — appears in pi-free's key table with no base URL beside it, so
  there is nothing to transcribe faithfully.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StationPreset:
    """One station, as far as a configuration line needs to know it."""

    id: str
    display_name: str
    base_url: str
    key_env: str
    """The variable the upstream reads its key from — reported, never read.

    Hoshimi's credentials come from ``config.json``; silently starting to accept
    an environment variable would be a second credential source with its own
    security story. The name is here so an operator knows what to export before
    pasting, which is the same answer pi-free would give.
    """

    catalog_keyless: bool = False
    """Does the station publish its model list without a credential?

    Six of these do, and none of them answer a *chat* call without one — so this
    is a fact about browsing the catalogue, never about running without a key.
    """


PRESETS: tuple[StationPreset, ...] = (
    StationPreset("zenmux", "ZenMux", "https://zenmux.ai/api/v1", "ZENMUX_API_KEY", catalog_keyless=True),
    StationPreset("llm7", "LLM7", "https://api.llm7.io/v1", "LLM7_API_KEY"),
    # Upstream names this one `DEEPINFRA_TOKEN` on purpose and says so in the
    # open ("intentionally `DEEPINFRA_TOKEN` (not `DEEPINFRA_API_KEY`)"), so
    # the prefix rule that gives every other row its name is wrong here. The
    # docstring promises pi-free's answer, and this is it.
    StationPreset("deepinfra", "DeepInfra", "https://api.deepinfra.com/v1/openai", "DEEPINFRA_TOKEN", catalog_keyless=True),
    StationPreset("sambanova", "SambaNova", "https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY", catalog_keyless=True),
    StationPreset("novita", "Novita", "https://api.novita.ai/openai/v1", "NOVITA_API_KEY", catalog_keyless=True),
    StationPreset("routeway", "Routeway", "https://api.routeway.ai/v1", "ROUTEWAY_API_KEY", catalog_keyless=True),
    StationPreset("opengateway", "OpenGateway", "https://opengateway.gitlawb.com/v1", "OPENGATEWAY_API_KEY"),
    StationPreset("tokenrouter", "TokenRouter", "https://api.tokenrouter.com/v1", "TOKENROUTER_API_KEY"),
    StationPreset("anyapi", "AnyAPI", "https://api.anyapi.ai/v1", "ANYAPI_API_KEY"),
    StationPreset("bai", "B.AI", "https://api.b.ai/v1", "BAI_API_KEY"),
    StationPreset("orcarouter", "OrcaRouter", "https://api.orcarouter.ai/v1", "ORCAROUTER_API_KEY"),
    StationPreset("xkiro", "XKiro", "https://api.xkiro.com/v1", "XKIRO_API_KEY"),
    StationPreset("fastrouter", "FastRouter", "https://api.fastrouter.ai/api/v1", "FASTROUTER_API_KEY", catalog_keyless=True),
    StationPreset("requesty", "Requesty", "https://router.requesty.ai/v1", "REQUESTY_API_KEY"),
    StationPreset("stepfun", "StepFun", "https://api.stepfun.ai/step_plan/v1", "STEPFUN_API_KEY"),
    StationPreset("gmi", "GMI Cloud", "https://api.gmi-serving.com/v1", "GMI_API_KEY"),
    StationPreset("agnes", "Agnes AI", "https://apihub.agnes-ai.com/v1", "AGNES_API_KEY"),
    StationPreset("venice", "Venice AI", "https://api.venice.ai/api/v1", "VENICE_API_KEY"),
    StationPreset("infron", "Infron", "https://llm.onerouter.pro/v1", "INFRON_API_KEY"),
    StationPreset("merge", "Merge Gateway", "https://api-gateway.merge.dev/v1/openai", "MERGE_API_KEY"),
    # Upstream's metadata table prefixes this one COMMANDCODE, but the accessor
    # that actually resolves the key reads COMMAND_CODE_API_KEY. The accessor
    # wins: it is the name a user would have to export.
    StationPreset("commandcode", "CommandCode", "https://api.commandcode.ai/provider/v1", "COMMAND_CODE_API_KEY"),
    StationPreset("ollama-cloud", "Ollama Cloud", "https://ollama.com/v1", "OLLAMA_API_KEY"),
)

PRESET_BY_ID: dict[str, StationPreset] = {preset.id: preset for preset in PRESETS}


def get_preset(preset_id: str) -> StationPreset | None:
    """The named station, or None for a name this table does not carry."""
    return PRESET_BY_ID.get(str(preset_id or "").strip())


def preset_base_url(preset_id: str) -> str:
    """The station's OpenAI root, or ``""`` for a name this table does not carry."""
    preset = get_preset(preset_id)
    return preset.base_url if preset else ""
