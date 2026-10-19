# Hoshimi

[English](README.md) | [简体中文](README.zh-CN.md)

An OpenAI-compatible gateway in front of a set of free model stations (Cline, TRAE, ZCode,
Qoder, MiniMax, Gemini, CodeBuddy, CodeArts, ...) behind one API surface, with a web UI for
watching and steering it:

- `POST /v1/chat/completions` — Chat Completions (streaming by default, `"stream": false` works)
- `POST /v1/responses` — Responses API (translated internally to Chat)
- `POST /v1/messages` — Anthropic Messages for agents (JSON by default, `"stream": true` for SSE)
- `POST /v1/messages/count_tokens` — local input-token estimate, explicitly marked as an estimate
- `GET  /v1/models` — model catalogue
- `GET  /health` — liveness probe (public, no API key)

What watching and steering it means:

- **Reasoning effort** as an enforced budget — `reasoning_effort` becomes a token ceiling (or
  the model's own thinking menu), and only when the caller named a level.
- **Availability probes** — a round asks which ids of the free lanes' flat listing *this egress*
  may actually use; the verdict rides `GET /api/v1/models` and the UI groups by it.
- **Usage stats** — per-day and per-model token totals, TTFT and tok/s with the two bounds that
  keep a measured rate honest, plus the recent calls behind them.
- **Two independent keys** — the model API key opens `/v1/`; the tool access key protects
  configuration, accounts and settings.
- **A web UI** — Overview, Models (probe round included), Usage, Playground (effort picker),
  Settings.

Two lanes need **no credential and no configuration at all** — they are built with a
synthetic account as soon as the server starts (disable one with
`providers.<id>.enabled: false`):

- `opencode` — OpenCode Zen's anonymous free tier (`Bearer public`, the `-free` slice of
  the gateway's live listing; an `api_key` in the config unlocks the whole roster)
- `kilo` — Kilo AI's free pool (`isFree: true` slice of `api.kilo.ai`, ~16 ids)

The gateway logic lives in `hoshimi/core` and knows nothing about HTTP frameworks; FastAPI,
Typer and Vue are thin shells around it.

## Install and run

Requires **Python 3.10 or newer**. Install or upgrade from PyPI, then start the gateway:

```bash
python -m pip install -U hoshimi
hoshimi serve
```

Open `http://127.0.0.1:10600` for the web UI. The package includes the built UI;
no frontend build is needed. The anonymous free lanes work without a config file.
Add provider accounts in **Channels** and configure the gateway in **Settings**.
For file-based configuration, use [config.example.json](config.example.json) as a
reference and save your own `config.json` in the directory where you start Hoshimi,
or select a file with `hoshimi serve --config /path/to/config.json`.

`python -m hoshimi` is equivalent to the `hoshimi` script. Useful commands:

```bash
hoshimi check       # validate configuration without starting the server
hoshimi models     # list available models
hoshimi providers  # list providers
```

Read an available model ID from `GET /v1/models`, then replace `<model-id>` below:

```bash
curl http://127.0.0.1:10600/v1/models
curl http://127.0.0.1:10600/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"hi"}]}'
```

If you configure a model API key, add `Authorization: Bearer <model-api-key>` to
both requests. The tool access key is separate; see [The API](#the-api).

Cloud SDKs are optional. Install the matching extra only when using Vertex
ADC/service-account credentials or Bedrock's AWS SDK modes:

```bash
python -m pip install -U "hoshimi[google]"   # Vertex ADC / service accounts
python -m pip install -U "hoshimi[bedrock]"  # AWS credential chains / ConverseStream
```

Both extras can be installed together with `"hoshimi[google,bedrock]"`.
See [cloud setup](docs/cloud-providers.md) for account configuration.

`serve` options: `--host`, `--port`, `--strict-port`, `--open/--no-open` (the browser),
`--ui/--no-ui` (the web UI beside the API), `--config`, `--access-token`, `--model-api-key`,
`--default-model`. Other commands: `hoshimi version`, `hoshimi env` (the variables it reads),
`hoshimi config show|get|set|path`.

For Anthropic-compatible agents, set the base URL to `http://127.0.0.1:10600`,
use a model ID from `/v1/models`, and supply the gateway's model API key.
The inference routes accept either `x-api-key` or `Authorization: Bearer`.
See [Anthropic agent integration](docs/anthropic-api.md) for examples, tool and
thinking replay, and token-counting limits.

## Web UI

The PyPI package includes the web UI. `hoshimi serve` serves it at `/` alongside
the API. Use `--no-ui` for an API-only server.

Pages: **Overview** (health, endpoints, providers), **Models** (the catalogue with search, the
last probe round's verdicts per model, and a one-click round over every model), **Channels**
(the accounts behind the gateway: each station's cards with expiry, balance, today's check-in
and a *needs login* badge, sign-in through the station's own OAuth page, renew / claim / forget
per account, and the two automation switches for daily check-in and credential renewal),
**Usage** (what
this gateway served: per-day totals, per-model token counts with TTFT and tok/s, and the recent
calls with their effort), **Playground** (a streaming chat through the tool's management API
with the model's effort picker — *Auto* sends no level at all, so the body leaves untouched),
**Settings** (server, tool access key, model API key, security, providers with a *needs authorization*
badge, appearance, about). Language follows the system (`en`, `zh-CN`, `ja`), the theme and
accent colour live in `localStorage`, and the tool access key a protected server asks for is stored
there too and sent as `Authorization: Bearer`.

## The API

Two surfaces, told apart by their prefix:

- **`/v1/*`** — OpenAI-compatible inference, plus `/classifiers` and `/classify` for TypeSafe classification. Errors are OpenAI's shape: `{"error": {"message", "type", "code"}}`.
- **`/api/v1/*`** — management: `/app/version`, `/app/health`, `/app/meta` (version, whether a
  key is required, data dir, config path, default model, provider and account counts),
  `/settings` (`GET`/`PATCH` — the config file, never the key itself; a save also reaches the
  running gateway, so an account added this way serves immediately — see below), `/providers`
  (id, accounts, model counts, enabled), `/provider-forms` (credential fields and methods); `POST /providers/{id}/accounts` appends one credential
  — appended, never replaced, and the lane refuses first if it would drop it — and
  `DELETE /providers/{id}/accounts/{account}` forgets one, both reaching the config file and
  the running pool alike; `/stations` (the twenty-two stations an `agg` account may name
  instead of a URL, which is what the Channels form's dropdown is built from), `/models`
  (every id's
  capability spec, effort menu
  and last availability verdict), `/probe` (`POST` starts a round, `GET` reads it),
  `/refresh` (`GET` per-account renewal state, `POST` renews), `/credits` (`GET` balance plus
  the check-in document, `POST /credits/claim` takes today's free credits), `/login`
  (`GET /login/providers`, `POST /login/start`, `GET /login/poll`,
  `POST /login/input`, `POST /login/cancel` — browser and device sign-in), `/stats` (per-day and per-model usage, the recent-call ring). Errors are
  `{"code", "message", "detail"}`.

`/docs` and `/openapi.json` describe both. Two independent credentials control access:
`server.access_token` (`--access-token`) protects the tool's management API and docs;
`server.model_api_key` (`--model-api-key`) protects external `/v1/` model requests.
Neither key substitutes for the other. Leaving one empty disables authentication only for
its own surface. Health probes and the UI shell remain public.

Configure both keys in Settings. The browser automatically asks for the tool access key when
management access requires authentication, following rvc-next's access-token flow; there is
no separate key button in the top bar. Saving or clearing the tool key updates the current
browser's saved credential after the server accepts the change. Other browsers sign in again.
Playground uses tool-authenticated `/api/v1/playground/` routes, so the model API key is never
returned to or separately entered into the browser. Failed login attempts remain open and
do not replace the saved credential. Settings responses expose configured flags, never keys.

The UI loads provider metadata without querying upstream model lists. Models and Playground
share `GET /api/v1/catalog/stream`: cached rows arrive first, then each provider's models and
capabilities arrive independently over SSE. Discovery uses at most eight workers and shares
in-flight work across clients. A round stops waiting after 20 seconds, marks unfinished
providers as timed out, and retains received/cached models with an explicit retry action.
Failed refreshes retain a provider's last healthy catalogue; account-pool replacements do
not inherit another pool's catalogue. Standard `/v1/models` remains a JSON response for SDKs.

Model routing:

- `provider/model` — explicit routing, e.g. `zcode/GLM-5.3-Flash` (the provider id must be
  configured, everything after the first `/` is sent upstream verbatim).
- bare model names are looked up across the catalogues of all enabled providers
  (see `GET /v1/models`); a single-provider deployment passes unknown names through.
- `server.default_model` (or `--default-model`) fills in `model` when the request omits it.

### Reasoning effort

The free lane's gateway accepts `reasoning_effort`, `thinking.budget_tokens`, `enable_thinking`
and `thinking_budget` and then **ignores all of them** — the only control it enforces is
`max_tokens`, which thinking and the visible answer share. So a level here is a budget, not a
hint, and only what the caller actually asked for is applied:

- `reasoning_effort` (or `reasoning.effort`) `low`/`medium`/`high` → the ladder
  `light` (2 048 output tokens), `balanced` (8 192), `deep` (the model's own capacity).
  A model that cannot switch thinking off (MiMo V2.6) has every rung **doubled**, because
  thinking is paid out of the same ceiling before the answer starts.
- A model with its own thinking menu takes the level as a JSON merge patch instead — the
  menu, not the ladder, is what reaches the wire, and the patch is the station's own
  dialect: a top-level `reasoning_effort` (Buddy, Cline, Loomy, Qoder), Kilo's OpenRouter
  object `{"reasoning": {"effort": "high"}}` and `{"reasoning": {"enabled": false}}` for
  the `disabled` rung on families that accept it (stepfun/liquid answer 400 without it),
  MiniMax's `thinking.type` plus `output_config.effort`, ZCode's `output_config.effort`,
  CodeArts' `thinking.type`, Raccoon's `extra_body.thinking`.
- **No level named → nothing changes.** Your own `max_tokens` is never overridden, and a model
  with no capability table (every account channel) passes the body through untouched.
- The rung's description and its token count are generated from the same call that will decide
  the request, so what `GET /api/v1/models` advertises is what goes out on the wire.

### What a listing cannot say

A station's `/models` names ids and nothing else, so a lane with no capability table would
report every model as inert. Three sources fill that in, layered so no single one can break a
request.

**The vetted local table** is the authority for every lane that has one (Cline, Trae, CodeArts,
Gemini, MiniMax, ZCode, Qoder, Buddy, Loomy, Raccoon, EAC, Kilo, OpenCode, Zhipu): capacities,
thinking menus and their patches, each one verified against that station's own wire.

**models.dev** covers the rest, and deliberately takes only two things: whether the model
**reasons**, and whether it takes **images**. Those are properties of the *model* — `glm-5v-turbo`
takes an image whether LobsterAI proxies it or SambaNova does.

The catalogue also publishes context windows, output ceilings and thinking menus, and none of
those are taken:

- **Limits describe an endpoint, not a model.** The catalogue indexes by a flat id across 226
  providers, so the same id answers with whichever publisher's entry came first. Asked about the
  ids this gateway actually serves, `deepseek-v4-flash` comes back as a 1M-context model because
  that is Sensenova's number, `qwen3.7-max` as 1M because that is Alibaba's token plan, and
  `kimi-k2.7-code` returns an output ceiling equal to its context, which is a plan artifact.
  Reporting any of those as *this station's* limit would be a number nobody measured. And
  `max_output` is worse than a label: it reaches the wire through the token budget.
- **A menu is a claim about the wire.** Every menu above was verified against the station that
  will receive it; a catalogue cannot vouch for the station in front of us.

So the fill applies to an id **nobody declared** — a plain listing, with no `model_specs` entry
and no table. A declaration is left entirely alone, whatever the catalogue says, because a
person or a table spoke for it.

An install with no egress gets the same facts: a small snapshot bundled for the ids this
gateway serves without a table. The live catalogue is fetched by a daemon thread and only ever
improves the *next* lookup — a request path never waits on it, and a failure is invisible by
design. (models.dev answers `403` to a request with no real `User-Agent`, which is exactly the
kind of ordinary failure that must not reach a caller.)

**A third-party benchmark is not carried at all.** pi-free keeps a 678-entry Artificial Analysis
snapshot (`hle`, `gpqa`, `mmluPro`, `mathIndex`, `codingIndex`) and uses it to rank candidate
models when it substitutes one — a picker that quietly replaces the model you asked for with
the best-scoring model it happens to have. This gateway does not do that: `dead_models` bans an
id and never picks a replacement, so the ranking has nothing to rank and nothing to feed. The
other thing pi-free does with the scores is append them to a model's display name, and reaching
*that* would mean vendoring a snapshot — every entry dated the same day, already a fortnight
old — plus 773 lines of name-matching heuristics, over a catalogue whose 678 keys carry no
vendor prefix whatsoever. Each of those matches is a guess about which model an id is, and a
wrong one attaches somebody else's measurement to a model that never took the test. The same
file also publishes context windows and capability flags, which is the borrowed-numbers
argument above, one step further from the station.

### A listing that came back halved

Model discovery runs every five minutes, and it can succeed while being wrong. A paginated
listing cut short, an error page served with a `200`, a relay that answers with its first page
— each returns a catalogue that is smaller than the last and no different to read. Taken at
face value it overwrites the real one, and the next request gets a gateway that has quietly
lost half its models.

So a fresh listing under half the size of the one already held is refused, and the catalogue
stays as it was for one cycle. The threshold is a fraction, not a count: dropping two ids is
the whole of a three-model lane and a rounding error on a twenty-model one, and a count would
have to be wrong about one of them.

One cycle, not forever. Asked an entire TTL later and the station said the same thing again,
so the shrink is its own answer — and holding on to ids that no longer exist is a bug of its
own, since each of them then fails at the wire instead of being absent from the list. The
first refusal is the gateway protecting itself; the second is the station being believed.

An empty answer is a third case and already has one: the lane's own static list. There is
nothing to protect against a static list, so it is never held back.

### Availability probes

The free lane's listing is a flat id list: which of those ids **this egress** may use only a
request can say. `POST /api/v1/probe` starts a round in the background — deliberately low
fan-out, because a wide burst is what earns the lane's `429` — `GET /api/v1/probe` reports the
last verdict per model, and `GET /api/v1/models` carries it so the UI can group by it.
Verdicts survive a restart (`probe.json` in the data dir).

By default a round asks every lane whose listing cannot answer for itself (`opencode`; Kilo's
roster *is* its verdict, and an account channel's models belong to its own credential). Pass
`{"models": ["kilo/...", "cline/..."]}` to ask about specific ids instead.

| state | meaning |
| --- | --- |
| `available` | the model answered — a delta, or tokens generated |
| `region-blocked` | the station refused it for this egress |
| `unavailable` | the refusal named *this model* (400/404/422, "no such model", "Model is unavailable") |
| `throttled` | the lane's quota window; the model itself was never asked |
| `unknown` | 5xx, a credential failure, a transport error — **no verdict** |

Only `unavailable` is a statement about the model, and only it drops a row. Everything else
leaves the model advertised on purpose: a stale entry costs one retryable turn, while a model
removed on a hiccup costs a re-probe the user has no visibility into.

### Refusals the gateway remembers

A probe is a measurement someone asked for. Serving is where the gateway learns the same
thing without being asked: a station that has dropped an id answers `404` or `410` for it,
and that answer is about the model rather than about the caller or the account, so asking
again spends a round trip to learn it once more.

`404` and `410` are not the only spellings. A gateway that judges every request it dislikes
as *your request* answers `400` or `422` for an id it has never heard of — and then the
number is right about whose problem it is and wrong about which problem it is. So those two
statuses are also read against the body, and only against the body: an id with a removal
verdict next to it (`model gpt-5.6-astra does not exist`, `该模型已下线`) is the same fact
in prose. The verdict has to be *adjacent* to an id, not merely present somewhere in the
message, because the window cannot be written at all — `.` is a decimal point inside ids
and a full stop between clauses, and `model x is fine. the container image does not exist`
has both.

Those answers go into an in-memory table of `(provider, model)` pairs the gateway will not
ask about. `GET /api/v1/models` marks them `unavailable`, and a turn for one comes back
`503 model_unavailable` **without reaching the station** — which is the point: the flag is
a promise about the next call, not a report about the last one.

Two rules, both from pi-free's `blacklist.ts` and both there because the signal decays — a
free lane flips a model in and out of availability, so one bad afternoon must not ban an id
forever:

- a strike older than **10 minutes** is forgotten and the id is asked again;
- **3 strikes** inside that window promote the entry to a ban the TTL no longer lifts.

Only the id verdicts strike: `404`/`410` on the status line, and a `400`/`422` body that
spells the same verdict out. A `401` is the account's credential and neither it nor a `429`
ever strikes, whatever the body happens to mention — those numbers answer the account
question, and prose about a model cannot overturn them. Neither does a `5xx`: a failure page
is the station saying it could not answer, and reading one as a verdict would let a bad
afternoon ban an id for the rest of the session. The row stays listed either way (see the
note under Availability probes), and `POST /api/v1/models/unavailable/reset` lifts every ban
— a turn that gets an answer clears its own entry only after its window has expired, so an
id that is banned right now can never be the request that unbans it. The table is in memory
on purpose ("this model was bad last week" decays faster than a restart takes), so a restart
lifts everything too.

### Two refusals that arrive wearing somebody else's status

When every account has been asked, a gateway has to say which of them it was. Two of the
answers arrive wearing a status that means something else, and reading the number alone turns
one honest refusal into a pool-wide outage.

**A saturated model answers `429`.** Its own payload settles the question — the buddy gateway
sends `code: 14003` with an `actions` list offering `SWITCH_MODEL` and no account-switching
option at all. Measured 2026-10-05: four unrelated accounts all hit it inside twenty seconds,
and each was then locked for a full hour by a fallback duration, because a *model*-level event
had been read as an *account*-level one. So the pool stands down and the station's own words go
to the caller, who can change model. Rotating instead would burn every account against the same
wall and then report "every account is limited" — slower, and untrue.

**A blocked account answers `403`.** `code: 11140` ("request illegal") is the buddy gateway's
safety policy acting per account and unrelated to what was asked. It is account-scoped, so the
pool rotates — but renewing the credential would only burn a round on the refresh endpoint, and
without a hold the next turn picks the same account first and burns the same round again. The
account goes back for 30 minutes: long enough to survive one agent turn's retries, short enough
that a recovered account is not locked out for an hour.

The same two readings apply to error frames riding a `200` stream, where there is no status to
be fooled by and the failure is the frame.

A plain rate limit holds the account too, for as long as the station says. Both spellings of
that sentence are parsed — `…将在 2026-09-11 18:08:17 UTC+8 重置` and `… your usage will reset
at 2026-09-17 09:09:36 UTC+8` — with the offset captured rather than assumed, because the
international station is not in UTC+8. When the body names no time, which is the usual case,
the hold is an hour: a wrong "wait longer" costs one turn, while a wrong "go now" costs a round
trip per account on every turn until someone notices.

Holds are in memory, per lane, and visible in `GET /api/v1/credits`. "Which of my accounts is
out" is a claim about a running pool that decays faster than a restart takes.

### Waiting, and when it is worth it

Rotating to another account is instant and costs nothing. Waiting is neither. So the pool
rotates first and waits only once it has nothing left to ask — and then only for a refusal
that time could change. A rejected credential, an exhausted allowance, a held-back account and
a dropped id are all *this account's* verdict; a wait would only postpone the same answer from
the same account, which is the one thing a wait cannot fix.

A `5xx` on a single-account lane is the case this exists for. Without a wait it fails the turn
at once, even though the station may have been mid-deploy. With one, the pool asks again —
twice, at most — and the delay is capped at eight seconds, because a request path that parks
for a minute is a worse answer than a failure the caller can route elsewhere.

Two rules about the wait itself:

- **The station's own number wins.** `Retry-After` is the standard spelling of "not now", and
  a reset time in the body is a dialect of it; where both exist the header is the one the
  operator actually configured. It is capped like everything else.
- **The delay is jittered when it is guessed.** `uniform(0, base·2ⁿ)` — full jitter, because a
  fleet of clients backing off in step is the thundering herd the retry was meant to escape,
  and a fixed interval only moves the step.

`Retry-After: 0` means "released, go now" and is honoured as such. It is not the same answer as
a header that is absent, and the two must not be collapsed: zero is a released account, absent
is a station that told us nothing and left us to guess. (A bare number is also seconds, not a
date — parsed as one, `900` becomes some instant in 1970 and a clamp turns the difference into
exactly the wrong answer.)

Nothing is retried once content has gone out. A second answer then arrives as a duplicate,
which is worse than the failure it was meant to fix, so the break is reported instead.

### What the stations report on their own

Most free stations answer every turn with the headers that say how much of the window is
left. Reading them costs one line and no request at all, which is the whole reason this
exists: a balance endpoint is a round trip, needs credentials the anonymous lane may not
have, and several of these stations have none to begin with.

So each serving turn observes its own response — once, at the top of the station's
`stream_raw`, before the status is even looked at. Successes and failures both count,
because a `429` is exactly when the remaining number is worth the most. Six header pairs
are tried in pi-free's order, and only when both halves parse:

| # | remaining | limit |
|---|---|---|
| 1 | `x-ratelimit-remaining-requests` | `x-ratelimit-limit-requests` |
| 2 | `x-ratelimit-remaining` | `x-ratelimit-limit` |
| 3 | `ratelimit-remaining-requests` | `ratelimit-limit-requests` |
| 4 | `ratelimit-remaining` | `ratelimit-limit` |
| 5 | `x-ratelimit-remaining-requests-day` | `x-ratelimit-limit-requests-day` |
| 6 | `x-ratelimit-remaining-day` | `x-ratelimit-limit-day` |

The order is not decoration: SambaNova sends several windows at once, and reading the
wrong one shows a healthy quota the call cannot spend.

One deliberate deviation. pi-free parses with `Number.parseFloat`, so `"10 requests"`
becomes 10; here `float()` refuses it. Refusing is what makes the **drift counter** worth
having: when a dialect has both halves and neither parses, that is counted rather than
guessed, and the count is shown. A number read out of junk is a wrong quota that nothing
would ever flag.

Alongside the reading, four counters per provider accumulate over the install's life:
responses seen, `401`/`403`, `429`, and 5xx. They are the cheap half of the same
observation, and they are why a station that sends no quota headers at all is still worth
a row once it has started refusing.

Two bounds on believing the figure: a reading is marked **stale** after five minutes
(shown, but not as a current one), and the percentage is clamped to 0–100. `GET
/api/v1/credits` returns all of it as `passive` — numbers and header *names*, never header
values, which is why the report is safe to read out loud — and the channels page turns it
into a badge: red at 10% or less, amber at 25% or less, and amber-and-marked-old past
five minutes.

Nothing here is persisted, and no observation can fail a turn: it runs inside a `try` on
the path carrying the user's answer, because a header store is not worth a generation.

### Usage stats

`GET /api/v1/stats` reports what this gateway actually served: per-day and per-model
token totals, lifetime rows with `avg_ttft_ms` and `tps`, `requests` / `failures`, and the
last 200 calls. Counters survive a restart (`stats.json` in the data dir); days older than
120 are dropped, the lifetime rows are not.

Each `/v1/chat/completions`, `/v1/responses` and `/v1/messages` turn becomes exactly one row, whether it
streamed or not:

- a model the gateway could not route is a **failed** call — a refusal is still usage;
- a reader that hangs up mid-answer is a **served** call; `failed` is reserved for the
  gateway or the upstream failing the call;
- effort and origin (`chat` / `responses`) are recorded as the wire carried them.

Two bounds decide whether a speed is a speed: a decode window shorter than 250 ms or a
rate above 250 tok/s is dropped rather than averaged in, and reported as `null`. An
unmeasurable number must not look like a fast one.

## Credentials

Accounts are stored in Hoshimi's **`config.json`** under
`providers.<id>.accounts` (see `config.example.json`). Add them through the
Channels page, `hoshimi login`, or the configuration file. Use `--config` or
`HOSHIMI_CONFIG` to select a different Hoshimi configuration file.

Credential renewal runs on a timer (`server.auto_refresh`, on by default): a sweep
every 30 minutes renews any account whose expiry is within the next hour, and a
station that refuses the refresh token marks the account `needs_login` until a
human logs in again. `GET /api/v1/refresh` shows the per-account state,
`POST /api/v1/refresh` renews on demand.

Which failures make the pool try another account is decided by one status table
rather than case by case, and the distinction it draws is *whose problem the
failure is* — the same reading pi-free's classifier makes for a model picker,
with the account standing in for the model:

| failure | why |
| --- | --- |
| `401`, `403` | the account's credentials — renew, then ask the next account |
| `402`, `429`, 5xx, Cloudflare 521-527, Anthropic 529 | possibly this account only — ask the next one, up to `server.max_account_retries` |
| `404`, `410` | the model id — every account is asked, and then the id is remembered |
| `400`, `405`, `406`, `415`, `418`, `422`, `451` | the caller's own request — it would fail identically everywhere, so the pool is not spent finding out; a `400` body that names the id itself is the one exception (see Refusals the gateway remembers) |

`402` is the one that mattered most: on a free tier it is the station saying the
allowance is gone, and it used to arrive here as an opaque upstream error the
pool had no answer for. A status the table does not name counts as *possibly
this account*, because hiding a real outage behind "we don't recognize this
code" is the worse mistake on a lane whose whole point is that it is free. The
body is read only where the number does not already say what it means — `402`,
`429`, and a `400` that talks about quota rather than about the request. A `400`
body is in fact read twice for two different questions: *whose* problem is this
(quota, or the request), and *is the id gone*. The id reading runs first, and it
yields: any word of complaint about the account stops it, because a miss costs
one more round trip while a hit costs the caller a model they can no longer see.

### Signing in

The stations hand out their credentials through the browser rather than a
pasted key: `hoshimi login <provider>` prints (and opens) the station's login
link, waits for you to finish, and files the account in `config.json` when the
station answers. The same flow is available to the web UI through
`GET /api/v1/login/providers`, `POST /api/v1/login/start` and
`GET /api/v1/login/poll`. Re-signing into the same station identity replaces
the account instead of stacking a second one.

| Station | How the login comes back |
| --- | --- |
| `cline` | device code on workos.com, then a register call |
| `codex` | OpenAI consent, PKCE redirect to the fixed loopback port 1455 |
| `minimax` | device code + PKCE on account.minimax.cn |
| `buddy` / `workbuddy` | external login page, the plugin polls for the token |
| `zcode` | server-mediated flow on zcode.z.ai (no local callback) |
| `qoder` / `qodercn` | device link on qoder.com, polled on the open API host |
| `gemini` | Google consent, redirect to a loopback port |
| `trae` | console login, redirect to port 18080 |
| `lobsterai` | portal login, redirect to a loopback port |
| `loomy` | WeChat QR on a local page, with phone binding for a first-time account |
| `codearts` | Huawei Cloud portal, PKCE + a DPoP proof on the token exchange |
| `raccoon` | WeChat QR page, polled by the gateway |
  | `eac` | GitHub authorization through the gateway (the per-install `x-ofm-user` token is what unlocks the lane) |

The loopback stations bind `127.0.0.1` on an ephemeral port (trae prefers
18080, codearts requires one above 10000); codex uses the fixed port 1455
required by its registered redirect URI. Nothing listens on a public
interface. Codex credentials now route through the native Responses adapter.

Additional sign-in flows include Anthropic, GitHub Copilot, Kilo, Kimi Coding,
Meta, OpenAI, OpenRouter, Radius and xAI. Use `hoshimi login <provider> --no-open`
to print the link without opening a browser, and `--manual` to paste a callback
when the browser runs on another machine. Device flows show a user code.
The Channels dialog supports these same flows, manual callbacks and cancellation.
See [native provider authentication](docs/providers.md).

### Config keys

| Key | Meaning |
| --- | --- |
| `server.host` / `server.port` | bind address (default `127.0.0.1:10600`) |
| `server.access_token` | tool access key for `/api/`, `/docs` and `/openapi.json` |
| `server.model_api_key` | independent API key for external `/v1/` model requests |
| `server.default_model` | model used when a request omits `model` |
| `server.max_body_bytes` / `server.max_account_retries` | limits |
| `server.auto_refresh` | renew credentials on a timer (`false` = rotate tokens by hand) |
| `server.auto_checkin` | daily check-in timer for free credits (`false` = claim by hand) |
| `security.allow_cidrs` | client IP allowlist; required to bind non-loopback |
| `security.allow_origins` | CORS origins; requires `server.access_token` |
| `security.allow_hosts` | allowed `Host` header names |
| `providers.<id>.enabled` | `false` disables the provider entirely (this is also how the keyless lanes are turned off) |
| `providers.<id>.accounts` | list of credential blobs (see below) |
| `providers.<id>.options` | provider-specific options |

Safety rules (same idea as codex-api): binding a non-loopback host requires both
`allow_cidrs` and `access_token`; CORS requires `access_token`.

The file itself is treated as a credential store, because it is one. Every write
restricts it to the owner — the same reason `~/.ssh` is `700` — best-effort, since a
filesystem that cannot express that (Windows, some network mounts) is not a reason to
fail a save. And a config that cannot be parsed is copied aside before the refusal:
`os.replace` is the only write path here, so a save that did not look first is the one
moment this code could destroy something it never read. The startup error names the
copy, which is otherwise a file nobody remembers creating.

An unreadable config still stops the gateway. Starting anyway would be the worse
answer — an install that came up with every provider disabled looks exactly like
every account had been lost, and there is no way to tell the two apart from outside.

### A save reaches the running gateway

Editing `config.json` by hand is not the only way in: `PATCH /api/v1/settings` writes the
file *and* brings the pool in line with it, so an account added through the web UI serves
on the next request rather than after a restart. Two rules keep that from costing more
than it saves:

- **Only a lane whose own configuration moved is rebuilt.** A provider carries what only
  memory knows — which accounts are out of rotation because their refresh token was
  refused, which are under a rate-limit hold, which ids the station just stopped
  answering for. Saving a key for a different station is no reason to forget any of that,
  and neither is changing the port. The comparison is the input the pool is actually
  built from (`accounts`, `options`, `enabled`, `default_model`, `max_account_retries`),
  not the file: a save that touches none of those rebuilds nothing.
- **The HTTP client is kept, not replaced.** Every lane shares one, and a turn already
  streaming holds a provider object that would go dead the instant its client closed.
  An operator adding an account must not be a reason to cut off an answer in flight.

What a rebuild cannot preserve is what it recreates by definition: a replaced lane's
accounts are new objects, so an account flagged `needs_login` on *that* lane goes back
into rotation until it fails again. That is the same state a restart would leave it in,
which is the trade the rule above is sized to — the flag survives an edit to another
station, and does not survive an edit to its own.

The Channels page's **Add account** button is that path from the browser: pick one of the
twenty-two stations (or paste the address of one that is not in the table), paste the key, and
the same `POST /api/v1/providers/{id}/accounts` does the rest. Two rules are what make its
answer worth believing. It **appends** — the settings a client can read carry account counts
only, so anything that replaced the list would be overwriting accounts it had never seen. And
the lane **refuses first**: `Provider.check_account` is the same predicate the pool uses when
it discards a credential while building, so a save never reports an account the gateway would
throw away on the next request. A lane left switched off is switched back on, because the
promise is that the account reaches the pool, and a disabled lane has no pool to reach.

Sharing the gateway with the LAN is then one extra line: give the other machine
`server.model_api_key`. The model API key is checked in constant time,
opens `/v1/` (models, chat, responses, Messages) and is refused on protected `/api/` routes — it cannot
read the configuration, list credentials or change settings, and `config show` masks it
like the tool access key. Set it in `config.json`, with `--model-api-key`, or with
`hoshimi config set server.model_api_key <secret>`.

## Daily check-in and balance

Stations that grant free credits once a day are claimed on a timer
(`server.auto_checkin`, on by default): one serial sweep a day, after a short
startup delay, records the result in `checkin.json` next to the probe and
stats stores. `GET /api/v1/credits` reads the remaining balance (or quota
window) per account; `POST /api/v1/credits/claim` renews on demand. A lane
without an endpoint is skipped rather than guessed at.

## Providers

The original providers below remain valid in `provider/model` and as `providers.<id>` keys.

New native integrations include NVIDIA, OpenRouter, public API and OAuth services,
Azure, Cloudflare, Vertex, Bedrock, Radius and TypeSafe classification. See the
[complete source and deduplication inventory](docs/provider-inventory.md),
[native provider setup](docs/providers.md), [NVIDIA](docs/nvidia.md),
[pi-free compatibility](docs/pi-free.md) and [cloud setup](docs/cloud-providers.md).
The 22 existing `agg` presets remain available. FCC migration also adds 19 cloud
services, ClinePass, MiniMax Chat, Bedrock Mantle, and opt-in LM Studio,
llama.cpp and local Ollama. The inventory maps all 61 FCC source entries; see
[provider setup](docs/providers.md) for account/protocol distinctions.
"Builtin" is the fallback model list used when upstream model discovery is unavailable.
`kilo` and `opencode` are **keyless**: they need no account and serve without any config.

| id | upstream | account fields | refresh | builtin models |
| --- | --- | --- | --- | --- |
| `cline` | `api.cline.bot` | `access_token`, `refresh_token` | yes (workos) | `stealth/space-bunny-alpha`, `cline-free/*` |
| `codex` | ChatGPT/Codex OAuth + Responses | `access_token`, `refresh_token`, `account_id`, `client_id`, `expires_at` | yes | account catalog or configured models |
| `lobsterai` | `lobsterai-server.youdao.com` | `access_token`, `refresh_token`, `uuid` | yes | `deepseek-v4-flash`, `MiniMax-M3`, ... |
| `loomy` | `loomyad.xunfei.cn` | `access_token` (also sent as `token` header) | no | `deepseek-v4-flash-0731`, `spark-x`, ... |
| `buddy` (CodeBuddy) | `copilot.tencent.com` | `access_token`, `refresh_token` | yes | 16 (hy4/hy3, deepseek-v4, glm-5.x, kimi-k2/3, minimax-m3) |
| `workbuddy` | `www.workbuddy.ai` | `access_token`, `refresh_token` | yes | 23 (gpt-5.x, gemini-3.5-flash, glm/kimi, ...) |
| `qoder` | `openapi.qoder.sh` | `access_token`, `security_oauth_token`, `refresh_token`, `machine_id` | yes | `auto`, `qmodel_38max`, `gmodel`, ... |
| `qodercn` | `openapi.qoder.com.cn` | same as `qoder` | yes | same as `qoder` |
| `trae` | `trae-api-cn.mchost.guru` | `access_token`, `refresh_token`, `device_id`, `machine_id`, `uid` | yes | `glm-5.2`, `DeepSeek-V4-*`, `kimi-k*`, ... |
| `raccoon` | `xiaohuanxiong.com` | `access_token`, `refresh_token`, `device_id`, `office_identity` | yes | `sn-glm-5-3`, `sn-sensenova-6-8-*`, ... |
| `minimax` | `agent.minimax.cn` | `access_token`, `refresh_token` | yes | `MiniMax-M3.1-Flash-Preview`, `MiniMax-M3`, ... |
| `zcode` | `zcode.z.ai` | `zcode_jwt` (or `access_token`), `device_mid` | no | `GLM-5.3-Flash`, `GLM-5.3` |
| `opencode` | `opencode.ai` | `api_key`, `project_id` (anonymous lane: none) | no | `big-pickle`, `mimo-v2.6-flash-free`, ... |
| `kilo` | `api.kilo.ai` | optional API key or device login; keyless free pool by default | no | 16 free ids (`openrouter/free`, `kilo-auto/free`, ...) |
| `gemini` | `daily-cloudcode-pa.googleapis.com` | `access_token`, `refresh_token` (+ `client_id`/`client_secret` in options) | yes | `gemini-3.8-flash-{low,medium,high,tiered}` |
| `codearts` | ModelArts `snap-access.cn-north-4` | `access_key_id`, `secret_access_key`, `security_token` | no | `GLM-5.x`, `deepseek-v4*`, `openpangu-2.0-*`, ... |
| `eac` | bundled or custom co-paid relay | fresh login uses `relay: "bundled"`; custom `base_url` + `access_token` (direct) or `signing_secret` (worker); `user_token` = this install's GitHub authorization (auto-loaded from `eac-user.json`) | no | `deepseek-ai/deepseek-v4.1-flash`, `moonshotai/kimi-*`, `z-ai/glm-5.3*`, `openai/gpt-oss-20b`, plus the two relayed free-lane ids (`opencode/deepseek-v4.1-flash-free`, `opencode/glm-5.3-flash-free`) |
| `agg` | any OpenAI-compatible station (`base_url`) | `base_url` + `api_key` (or `access_token`/`token`); optional `models`, `headers`, `options` | no | the station's own `/models`, or the account's `models` list |
| `zhipu` | `open.bigmodel.cn/api/paas/v4` | `api_key` (from the bigmodel.cn console) | no | 7 permanently free ids (`glm-4.7-flash`, `glm-z1-flash`, `glm-4.6v-flash`, ...) |

`eac` has no anonymous mode: it is built only when an account is configured, and
`GET /api/v1/providers` lists it anyway with `requires_authorization: true`, so the
settings page can label the lane as needing one. Two modes, decided by the account's
credential:

- **direct** — `access_token` is sent as `Authorization: Bearer …`.
- **worker** — the account holds a shared `signing_secret`; the request carries no
  secret, only `x-ofm-timestamp` / `x-ofm-signature` = HMAC-SHA256 over
  `timestamp 
 METHOD 
 path 
 sha256(body)`, computed over the exact bytes sent
  (a body re-encoded after signing fails the gateway's check).

Worker mode carries the GitHub authorization in `x-ofm-user`; direct mode sends
only its Bearer credential. Run `hoshimi login eac` or use Channels' Sign in
action on a fresh install: the bundled relay supplies the endpoint and signing
credential, and a successful GitHub authorization creates `eac-default` with
`relay: "bundled"`. Shared signing material is not written to the config.
Custom worker/direct relays can be added through Channels. Worker login updates
the configured relay account and keeps its URL and signing secret; direct
relays use their Bearer credential without GitHub OAuth.
Omitting `user_token` loads the latest `eac-user.json` token on each request;
explicit `null` or an empty string disables that fallback for this account.
A worker without authorization receives `401 AuthorizationRequired`.
See [authentication and EAC behavior](docs/authentication.md).

### The aggregator lane (`agg`)

`agg` carries stations the install brings its own key for: one provider id, many
stations, because the station is the account — each names its own `base_url` (the
OpenAI root, so `{base_url}/chat/completions` is where a turn goes, exactly as with
`eac`) and its own key. OpenRouter's free pool, SiliconFlow, Cloudflare Workers AI,
Groq or SambaNova are then a configuration entry rather than new code:

```jsonc
"providers": {
  "agg": {
    "accounts": [
      { "id": "openrouter", "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-...",
        "models": ["openrouter/auto"] },                                  // the account's own catalogue
      { "id": "siliconflow", "base_url": "https://api.siliconflow.cn/v1", "api_key": "sk-..." }
    ]
  }
}
```

- **`preset` (optional)**: a name for one of the twenty-two stations this gateway
  already knows the address of, in place of `base_url` — so the line is a name and
  a key rather than a URL you had to look up. `hoshimi stations` lists them with the
  environment variable the *upstream* reads its key from (reported, never read —
  Hoshimi's credentials come from `config.json`). An explicit `base_url` always
  wins, an unknown name drops the account with a warning naming it, and the table
  carries **no model list** on purpose: a static catalogue would be wrong by the
  time a station ships a new model, and listing them is the gateway's job.
- **`models` (optional)**: the account's catalogue. Without it the station's own
  `/models` is the source; with it the listing is never asked. An account that
  declares `models` is the only one a turn for those ids runs on, so several
  stations can share the id without 404-ing on each other's models.
- **A station's `/models` is a list of endpoints, not of chat models.** The same
  array carries image and video generators, embeddings, rerankers and — on at
  least one gateway — internal smoke-test rows, and `agg` used to offer every one
  of them as available. Ten of the twenty-two presets are read the way pi-free
  reads them (it needs the same fact: its picker cannot show an embedding model
  either), keyed by the **address** rather than by the `preset` name, so an
  account pointed at its own relay is not judged by a schema it never had. A row
  with no field to judge is kept — a filter that suddenly eats a whole catalogue
  is a schema change, and over-listing costs one failed turn while under-listing
  is invisible.
- **One of those predicates is hoshimi's, not pi-free's: `llm7`.** Upstream never
  reads that catalogue — it registers three routing selectors built locally
  instead — but the station's own endpoint types every row, and of its
  sixty-four rows fifteen are `image`, `video` or `audio_to_text`. The
  shared text-output rule would keep `whisper-large-v3` (speech-to-text; its
  output *is* text) and `gpt-image-2` (a generator that emits a caption), so
  llm7 is judged on `model_type`. pi-free's static `default`/`fast`/`pro`
  selectors are deliberately **not** added: `/models` does not publish them,
  `pro` is paid, and the anonymous chat lane answered `503` for all three
  when probed — an id this gateway cannot show to be usable does not get
  listed on somebody else's word.
- **Whether calling an id costs anything is now an answer, and it has three
  values.** Nine of the twenty-two stations can say so: seven read it out of
  the row (`bai` and `tokenrouter` spell free into the id as `<model>:free`;
  `gmi` publishes `is_free`; `xkiro` states an `access_tier`; `anyapi`,
  `orcarouter` and `opengateway` publish price lists), while `agnes` and
  `commandcode` price nothing anywhere near their listings, so their free ids
  come from tables. Both `/v1/models` and `/api/v1/models` carry it as `free`,
  and `null` means **the station did not say** — which is most ids at most
  stations, and not a claim that they are paid. What is transcribed is
  pi-free's stamp (`_isFree` guarded by `_freeKnown`), not the filter
  underneath it: that filter scans a whole catalogue to decide whether the
  station publishes prices at all and fills in "not free" wherever the station
  was silent, which is a policy about what pi-free opens on rather than a fact
  about the station. **`venice` is deliberately absent** — it lists a model at
  $0/$0 that an unfunded key cannot call at all (HTTP 402), and being right
  about the price list while wrong about the gate is worse than saying
  nothing.
- **`commandcode` is the one aggregator that publishes what its models are, so
  its spec no longer rests on models.dev.** Its catalogue lists which of its
  41 ids take images and which of its 48 reason, and both reach the spec
  directly — which matters for `reasoning` in particular, because that is the
  field that decides whether the effort policy enforces a budget on the body
  or leaves it alone. Two lists of what a model *is*, and no numbers: the same
  upstream file also carries a cost table (56 of its 58 entries are priced,
  and a paid model's price is not this gateway's business) and a three-row
  output ceiling whose substance is really pi-free's own default. An id the
  station does not name gets nothing, and an account's own `options` still
  outrank the table — an operator knows their contract better than a pricing
  page does.
- **`options`** (account-level, falling back to the provider's own `options`): a
  generic lane cannot know what an arbitrary id can do, so the default spec is
  inert — `reasoning=False`, the caller's body passes through untouched — until the
  account declares otherwise: `vision`, `reasoning`, `context_window`, `max_output`,
  and per-id overrides under `model_specs` (`{ "<id>": { … } }`). A model that
  carries its own thinking menu also states `efforts`, `effort_patch`,
  `effort_off_patch` the same way.
- **`headers` (optional)**: extra request headers (a station that wants
  `HTTP-Referer`, an org id, ...). It can never replace the `Authorization` bearer.
- A missing `base_url` (or an unknown `preset`) or key drops the account at startup
  with a warning; the lane is simply absent until one is complete.

Every `agg` key is this install's own credential: keep it out of shared configs.

### The Zhipu free lane (`zhipu`)

Zhipu's OpenAI-compatible station keeps seven permanently free ids (免费 in the
platform's own pricing table), so this lane is a fixed base and one bearer rather
than an aggregator: `glm-4.7-flash` (200K), `glm-4.5-flash`, `glm-4-flash-250414`,
`glm-z1-flash` (thinking), `glm-4.6v-flash`, `glm-4.1v-thinking-flash` and
`glm-4v-flash`. The live `/models` listing is the source whenever it answers (an
account also sees whatever its balance covers); the seven ids are the floor when
it does not.

```jsonc
"providers": {
  "zhipu": {
    "accounts": [{ "id": "zhipu-1", "api_key": "<your bigmodel.cn API key>" }]
  }
}
```

- **`options.base_url`** moves the whole lane to the international station
  (`https://api.z.ai/api/paas/v4`) — the same wire under another host.
- GLM's thinking is a `thinking: {type}` switch, not a token ladder, and the free
  variants publish no menu: a text row stays inert, so the caller's body reaches
  the station untouched. The two models that are thinking models by construction
  (`glm-z1-flash`, `glm-4.1v-thinking-flash`) carry the free lane's budget ladder
  instead, since `max_tokens` is the only control that reaches the wire.

Per-provider `options`:

- `trae`: `channel` (default `solo_work_lite`), `user_agent`, `app_id`, `ide_version`,
  `ide_version_code`, `os_version`, `device_brand`
- `zcode`: `app_version` (default `3.14.3`)
- `codearts`: `benefit_models` — models allowed to send the `maas_type: benefit` header
  (default `glm-5.3-flash`, `deepseek-v4.1-flash`); the header is retried without it
  when upstream refuses.
- `gemini`: `client_id`, `client_secret` for OAuth refresh
- `zhipu`: `base_url` — the station root, to move the lane to the international site (`https://api.z.ai/api/paas/v4`)

Notes:

- **qoder**: the packaged reference implementation drives inference through an encrypted
  WASM path, so the plain `model/v1/chat/completions` public endpoint may answer `503`
  depending on the station. Everything else (headers, models, refresh) matches upstream.
- **opencode (free lane)**: follows dsh-our-free-model's v2 wire implementation
  (`src/upstream.js`, snapshot `f0b979f`). The provider uses `/inference/v1`,
  `user-agent: opencode/latest/2.0.26/cli`, `x-opencode-client: cli`, and four
  matching session headers (`x-opencode-session`, `x-opencode-session-id`,
  `x-session-affinity`, `x-session-id`). It omits the legacy `x-opencode-request`.
  Session ids use the descending encoding and remain stable per account and
  conversation, derived from `session_id`, `conversation_id`, `user`, or the first
  user message. Send `session_id` for conversations with the same opener.
  Anonymous requests declare the exact v2 `read`/`shell` tool schemas; conflicting
  caller tools receive aliases in declarations, history and forced choices, then
  regain their original names in responses. Injected tools are described as
  placeholders; an attempt to call one the caller did not authorize returns an
  explicit error. The wire omits `tool_choice: none`, while the response guard
  still enforces that choice. The existing per-model Chat/Responses/Messages split
  is retained under `/inference/v1`. API-key accounts keep their original tools.
  Live discovery remains authoritative; failures retain the last cached roster,
  and only a cold start uses the historical fallback list. New free ids retain
  their upstream spelling. DeepSeek V4.1 Flash, GLM 5.3 Flash, Nemotron Super VL
  and Space Bunny use the reference's declared effort menus. See
  [OpenCode integration verification](docs/opencode-integration.md) for validation.
- **`/v1/responses`**: `previous_response_id` and structured output (`text.format`)
  are rejected with `400`; everything else (inputs, tools, streaming events) works.
- Reasoning: a station that declares a menu is what rewrites the level (see "Reasoning
  effort" above). A station with no capability table — gemini, trae, lobsterai — keeps the
  caller's own `reasoning_effort` untouched.

## Development

For development, work from a source checkout and install the editable package
with its development dependencies. Frontend development also requires Bun:

```bash
python -m pip install -e ".[dev]"
python scripts/dev.py web-install
python scripts/dev.py dev
```

Vite on `:5173` proxies the API on `:10600`. Options include `--api-port`,
`--web-port` and `--config`; Ctrl+C stops both processes. To serve the UI directly
with `hoshimi serve` from a checkout, build it with `python scripts/dev.py web`.
Without a UI build, the server logs a warning and serves the API only.

Validation and build commands:

```bash
python scripts/dev.py lint         # Ruff
python scripts/dev.py test-py      # pytest
python scripts/dev.py test-web     # Vitest + vue-tsc
python scripts/dev.py check        # lint, Python types, all tests, generated types
python scripts/dev.py wheel        # web build + wheel/sdist in dist/
```

`dev.py` also has `format`, `typecheck-py`, `web-install`, `web`, `web-dev`, `dev`,
`openapi`, `typegen`, and `typegen-check`. Run it without arguments to see the full list.

The web UI's TypeScript types come from the server's OpenAPI document:

```bash
python scripts/generate_openapi.py > hoshimi/webui/openapi.json
cd hoshimi/webui && bun run typegen   # rewrites src/api/schema.d.ts — commit it
```

Layout:

```
hoshimi/
  core/        config, credentials, registry, refresh, expiry, chunks, SSE, translate/, providers/ (native adapters and shared transports)
  api/         FastAPI app, middleware, routers/ (openai + management), SPA serving
  cli/         Typer commands: serve, check, models, providers, version, env, config
  webui/       Vue 3 + Vite: src/{ui,views,api,stores,i18n,theme}, dist/ ships in the package
tests/         core/ api/ cli/ — the layering rule keeps core free of HTTP frameworks
scripts/       generate_openapi.py — the document behind webui/src/api/schema.d.ts
```

The adapter contract lives in `hoshimi/core/providers/base.py`: every provider
yields normalized chunks (`text_delta`, `reasoning_delta`, `tool_call_start`,
`tool_call_delta`, `usage`, `finish`, `error`) which `chunks.py` re-emits as Chat
Completions SSE, Responses SSE, or aggregated JSON.

Protocol references: `codex-api` (server/security), `dsh-our-free-model` (station
protocols) and `Hanaikada` (the layered Python + Vue skeleton this project follows,
including `hoshimi/webui`). All three are external clones and are git-ignored.

## License

Hoshimi is licensed under the GNU General Public License, version 3 only
(`GPL-3.0-only`). See [LICENSE](LICENSE) for the full text.
