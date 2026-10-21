# Models and runtime behavior

[Documentation](README.md) | [简体中文](model-behavior.zh-CN.md)

## Discovery and caching

Upstream listings determine model IDs; provider capability tables and account
declarations describe what those IDs can do. Catalogue caches are scoped to the
account pool. Replacing a pool does not inherit another pool's cached models.
Paginated discovery publishes only complete successful results, and failed
refreshes retain the last healthy catalogue.

Models and Playground share `GET /api/v1/catalog/stream`: cached rows arrive
first, then each provider's results arrive independently over SSE. Discovery uses
at most eight workers and shares in-flight work between clients. A round stops
waiting after 20 seconds, marks unfinished providers as timed out, retains models
already received and allows retry. Loading provider metadata does not itself query
upstream model lists. Standard `/v1/models` remains JSON.

The five-minute model cache also guards suspicious shrinkage: a fresh listing
smaller than half the cached one is held back for one cycle. If the upstream
confirms the shrink after another TTL, it is accepted. Static fallback lists
used for empty/unavailable discovery are a separate case and are not held back.
Fallback IDs are historical recovery data, not a claim of present availability.

## Capability sources

Provider tables and explicit account `options` / `model_specs` take precedence.
Where implemented, upstream metadata supplies capabilities and endpoint limits.
Generic model declarations can include `vision`, `reasoning`, `context_window`,
`max_output`, `efforts`, `effort_patch` and `effort_off_patch`.

models.dev only supplements vision and reasoning for otherwise undeclared IDs.
It does not supply context/output limits or thinking menus: those belong to the
specific endpoint, plan and protocol. A bundled snapshot works offline; a daemon
fetch improves subsequent lookups without delaying requests. Failures leave the
existing information intact. Third-party benchmark rankings are not imported,
and a failed model is not silently replaced with a higher-ranked one.

The `free` field has three values: `true`, `false`, or `null` (unknown). It is a
statement about the upstream access/price evidence, not a guarantee of unlimited
quota. Local models are not automatically free. See [pi-free](pi-free.md) for
station-specific filtering, capability and free-status differences.

## Reasoning effort

The policy below applies to translated requests. Native Anthropic Messages
retains the caller's thinking/output configuration and token budget for upstream
validation rather than running it through the Chat effort policy.

An explicit `reasoning_effort` or `reasoning.effort` activates the effort policy.
Without a selected level, the policy leaves the body untouched; provider-specific
protocol translation can still apply its own defaults. Playground's Auto sends
no level. Caller token limits participate in budget calculation.

| Model capability | Explicit effort handling |
| --- | --- |
| Declared thinking menu | Apply the station's JSON merge patch for the selected rung |
| Reasoning model without a menu | Enforce a token ceiling: light 2,048; balanced 8,192; deep uses model output capacity |
| No reasoning capability | No effort-driven rewrite |

For the budget ladder, `low` maps to light, `medium` to balanced, and `high` to
deep. Models that cannot disable thinking have doubled ladder budgets because
thought and visible output share the ceiling. The effective ceiling also respects
the model's capacity and any smaller caller-provided limit, with a minimum of
512 tokens for the ladder. An explicitly selected ladder can therefore raise a
caller limit below 512; menu budgets have a minimum of one token.

Menus vary by provider: Kilo uses a `reasoning` object, MiniMax and ZCode use
`output_config.effort`, CodeArts uses `thinking.type`, and Raccoon uses
`extra_body.thinking`. CodeBuddy, Cline, Loomy and Qoder use their declared
`reasoning_effort` dialect. Gemini Code Assist exposes one `gemini-3.8-flash`
row with `low`, `medium`, `high`, `tiered`; its translator derives the budget and
upstream model suffix. TRAE publishes a context table but no fetched account
thinking menu, so its caller effort field passes through. Use the management
model spec for each ID's menu and budget rather than assuming universal levels.

## Availability probes

`POST /api/v1/probe` starts a low-concurrency background round; `GET` reads it.
Results are also attached to management model rows and persisted in data-directory
`probe.json`. Default rounds target `opencode`; Kilo's free roster is already
filtered by that upstream. To probe specific providers or IDs, send:

```json
{"models": ["kilo/<model-id>", "cline/<model-id>"]}
```

| State | Meaning |
| --- | --- |
| `available` | The model produced a delta or tokens |
| `region-blocked` | Refused for this egress |
| `unavailable` | A model-specific refusal |
| `throttled` | Quota prevented the model from being asked |
| `unknown` | Credential, transport or server failure; no model verdict |

Only model-specific unavailability is grounds for removing a probe result from
the usable listing. Region, quota and unknown results do not declare a model gone.

## Remembered model failures

Serving also learns model failures. `404`/`410`, or a `400`/`422` body explicitly
naming a removed model, can strike a `(provider, model)` entry. Account or quota
complaints prevent ambiguous prose from being treated as a model verdict.
`401`, `429` and `5xx` never strike merely because the body mentions a model.

A strike expires after ten minutes; three strikes within that window create a
session ban. Requests for a currently banned ID return `503 model_unavailable`
without upstream traffic. Management rows mark it unavailable; remembered bans
do not necessarily remove it from the listing. A successful call can clear an
expired entry. `POST /api/v1/models/unavailable/reset` or a restart lifts bans.
This table is deliberately in memory, unlike persisted probe results.

## Account rotation and retries

| Failure | Pool behavior |
| --- | --- |
| `401`, `403` | Credential handling/renewal, then another account when applicable |
| `402`, `429`, `5xx`, Cloudflare `521`–`527`, Anthropic `529` | Eligible for account rotation, bounded by `server.max_account_retries` |
| `404`, `410` | Ask the pool, then remember a model-specific failure |
| `400`, `405`, `406`, `415`, `418`, `422`, `451` | Normally caller errors; do not spend the pool retrying the same request |

Bodies refine ambiguous quota and model failures. Unknown statuses are treated
as potentially account-scoped. CodeBuddy `429` with code `14003` and only
`SWITCH_MODEL` is model saturation, not an account limit; it is returned without
exhausting every account. Code `11140` on `403` holds the affected account for
30 minutes and rotates without an ineffective credential refresh.

Quota holds use the upstream reset timestamp, including its stated UTC offset;
without one the default hold is an hour. Holds are in memory and visible through
credits. Rotation happens before waiting. Retryable transient failures may wait
and retry at most twice, with delays capped at eight seconds. `Retry-After`
outranks a body reset time, and an explicit zero means retry now. Guessed delays
use full jitter. Credential rejection, exhausted allowances and removed models
are not repaired by waiting. **After content has been emitted, a request is never
automatically replayed.**

## Balance, check-in and passive quota

`GET /api/v1/credits` reads balances, holds and check-in state;
`POST /api/v1/credits/claim` claims credits. With `server.auto_checkin` enabled,
a serial sweep runs daily after a short startup delay. Results persist in
`checkin.json`. Providers without a balance/check-in endpoint are skipped.

Upstream responses also supply passive quota without an extra request. The first
parsable remaining/limit pair is used, in this order:

| Remaining header | Limit header |
| --- | --- |
| `x-ratelimit-remaining-requests` | `x-ratelimit-limit-requests` |
| `x-ratelimit-remaining` | `x-ratelimit-limit` |
| `ratelimit-remaining-requests` | `ratelimit-limit-requests` |
| `ratelimit-remaining` | `ratelimit-limit` |
| `x-ratelimit-remaining-requests-day` | `x-ratelimit-limit-requests-day` |
| `x-ratelimit-remaining-day` | `x-ratelimit-limit-day` |

Malformed pairs count as parser drift. Four per-provider counters track responses,
`401`/`403`, `429`, and `5xx`. Readings become stale after five minutes; percentages
are clamped to 0–100. Channels marks 10% or less red, 25% or less amber, and stale
readings amber with an old-data marker. Management returns header names, never raw
header values. Passive quota is in memory and observation cannot fail generation.

## Usage statistics

`GET /api/v1/stats?range=24h|7d|30d|90d` reports one range: totals, the previous
equal-length period, a zero-filled hourly (24h) or daily series, TTFT average/p50/p95,
generation speed, and breakdowns by model, provider, account and origin. Lifetime
totals and request/failure counts ride along. `GET /api/v1/stats/calls` pages the
last 1000 calls (newest first, `before` cursor), each with the account that answered,
attempts, cache reads/writes, finish reason and a short error. `GET /api/v1/stats/live`
streams the calls in flight; `DELETE /api/v1/stats` clears everything.

`stats.json` (version 2; a version 1 file is migrated in place) survives restart and is
written at most every two seconds, plus on shutdown. Hourly buckets are kept 72 hours,
daily buckets 120 days; lifetime rows remain. Cache writes are read from
`prompt_tokens_details.cache_write_tokens` (or `cache_creation_tokens`, Anthropic's
`cache_creation_input_tokens`); DeepSeek's `prompt_cache_hit_tokens` counts as a cache read.

Chat, Responses, Messages, classification and the playground each produce one record
per request. Routing failures
count as failures; a reader disconnecting is a served call rather than a gateway
failure. Records include effective effort and origin. Decode windows below 250 ms
or speeds above 250 tokens/second are excluded from averages and reported as
`null`, so an unmeasurable rate does not look like a fast result.
