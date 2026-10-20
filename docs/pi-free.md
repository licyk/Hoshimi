# pi-free compatibility

[Documentation](README.md) | [简体中文](pi-free.zh-CN.md)

## Scope and inventory

pi-free is a reference for station behavior and shared cataloguing rules, not a
runtime dependency. pi and free-claude-code are also migration references.
The [provider inventory](provider-inventory.md) records source IDs, endpoint,
protocol, credential type, plan and coverage. Its snapshot IDs describe the source
baseline, not the latest upstream revisions or successful live integration.

The gateway preserves native provider IDs and all 22 `agg` presets. Credentials
come from Hoshimi accounts; environment-variable names printed by `hoshimi stations`
document upstream conventions and are not automatically read as Hoshimi secrets.
See [providers](providers.md) for account declarations and protocol distinctions.

## Catalogue filtering

`agg` chooses station-specific rules by endpoint address, not merely preset name,
so an unrelated relay does not inherit another service's schema. Ten presets
use chat-task filters transcribed from pi-free. Unknown metadata is retained
instead of silently dropping the entire catalogue on schema drift.

LLM7 uses Hoshimi's own `model_type` filter. Text output alone would incorrectly
admit speech transcription and caption-producing image generators. pi-free's
static `default`/`fast`/`pro` routing selectors are not added: they are not the
live model listing, and the old probe did not establish usable anonymous access.
This is a historical design reason, not a current endpoint health report.

## Free status

Hoshimi retains `free: true/false/null`, keeping unknown distinct from paid.
Sources include `:free` IDs (B.AI, TokenRouter), `is_free` (GMI), `access_tier`
(XKiro), price rows (AnyAPI, OrcaRouter, OpenGateway) and curated free IDs
(Agnes, CommandCode).

pi-free's policy of treating missing prices as paid after finding any priced row
is not copied. Venice's zero-price rows are not used as a free-access claim:
an unfunded account can still be gated. Free markers do not promise available
credits or access for every key.

## Capabilities and selection

CommandCode's station-specific vision/reasoning lists are retained, with account
declarations taking precedence. Its unrelated paid price table and default output
ceilings are not imported. models.dev only supplements otherwise undeclared vision
and reasoning; limits and menus must describe the endpoint actually being used.

Artificial Analysis benchmark snapshots and fuzzy model-name rankings are not
imported. Hoshimi routes the requested model and records unavailable IDs rather
than silently selecting a benchmark-ranked replacement.

## Runtime differences

The remembered-model strike policy uses a ten-minute expiry and promotion after
three strikes. Rotation acts on accounts rather than substituting model IDs.
Passive quota uses the same ordered header pairs, but numeric parsing is strict:
`10 requests` is rejected and counted as parser drift instead of treated as 10.
See [model behavior](model-behavior.md) for the full current rules.
