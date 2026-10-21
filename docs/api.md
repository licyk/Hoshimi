# API and client integration

[Documentation](README.md) | [简体中文](api.zh-CN.md)

## Client connection

The default server is `http://127.0.0.1:10600`. OpenAI-compatible clients generally
use `http://127.0.0.1:10600/v1` as Base URL; Anthropic-compatible clients use
`http://127.0.0.1:10600`. Use an ID from `/v1/models` and the gateway's model API
key when configured. Upstream account credentials stay in Hoshimi.

```bash
curl http://127.0.0.1:10600/v1/models
curl http://127.0.0.1:10600/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"hi"}]}'
```

Replace `<model-id>` with a returned ID. If a model API key is configured, add
`-H "Authorization: Bearer <model-api-key>"` to both requests. Inference also
accepts `x-api-key`. See [authentication](authentication.md) for key boundaries.

## Inference routes

| Route | Behavior |
| --- | --- |
| `POST /v1/chat/completions` | Streaming by default; `stream: false` returns JSON |
| `POST /v1/responses` | Stateless Responses translated internally to Chat |
| `POST /v1/messages` | Native forwarding where supported, otherwise protocol conversion; JSON by default, SSE with `stream: true` |
| `POST /v1/messages/count_tokens` | Native counting where declared, otherwise a marked local estimate |
| `GET /v1/models` | JSON model catalogue |
| `GET /v1/classifiers`, `POST /v1/classify` | TypeSafe classification |
| `GET /health` | Public liveness probe |

Responses rejects `previous_response_id` and structured output `text.format`
with `400`; send the full conversation history. Tools, supported input conversion
and streaming events pass through the protocol translator. Compatibility with one
protocol does not imply that every upstream model supports every feature.
See [Anthropic agents](anthropic-api.md) for Messages-specific behavior.
Native Messages retains upstream extension fields and protocol/error metadata.
Converted structured output requires the provider's explicit `structured_outputs`
option; this does not change the direct `/v1/responses` limitations above.
`GET /v1/models?limit=1000` provides bounded startup discovery and display names
for Claude Code while retaining the OpenAI-compatible `data` list.

## Model routing

- `provider/model` explicitly selects a configured provider. Everything after the
  first slash is sent as its upstream model ID, including any further slashes.
- Bare IDs are matched against enabled provider catalogues in provider order.
  With one provider, an unknown bare ID can pass through to that upstream.
- `server.default_model` or `--default-model` fills an omitted model.

Use [model behavior](model-behavior.md) for capability, effort and failure rules.

## Management routes

Management uses the tool access key under `/api/v1/*`.

| Route group | Purpose |
| --- | --- |
| `/app/version`, `/app/health`, `/app/meta` | Version, health, paths, configured flags and counts |
| `/settings` (`GET`, `PATCH`) | Read redacted settings, save and apply updates; `restart_required` flags a saved host/port awaiting restart |
| `/providers`, `/provider-forms` | Providers, account summaries and credential form metadata |
| `/channels` | Every provider with accounts, renewal, cooling, passive quota, diagnostics, today's usage and a status word (memory only, no upstream calls) |
| `/providers/{id}/accounts` (`POST`) | Validate and append an account |
| `/providers/{id}/accounts/{account}` (`DELETE`) | Remove an account |
| `/stations` | `agg` presets |
| `/models`, `/catalog/stream` | Model capabilities and incremental catalogue SSE |
| `/models/unavailable/reset` (`POST`) | Reset remembered model bans |
| `/probe` (`GET`, `POST`) | Read or start an availability round |
| `/refresh` (`GET`, `POST`) | Read or request credential renewal |
| `/credits`, `/credits/claim` (`POST` for claim) | Balances, passive quota and check-in |
| `/login/providers`, `/login/start`, `/login/poll`, `/login/input`, `/login/cancel` | Browser/device authorization |
| `/stats?range=24h\|7d\|30d\|90d` (`GET`, `DELETE` to reset) | Range totals, zero-filled series, latency percentiles and model/provider/account/origin breakdowns |
| `/stats/calls` | Recent calls, newest first; `limit`, `before` cursor and filters |
| `/stats/live` | SSE: a snapshot of calls in flight, then `started`/`progress`/`finished` events |
| `/playground/*` | Tool-authenticated chat without exposing the model API key |

The running server's `/docs` and `/openapi.json` are the authoritative field and
method reference. Both are protected by the tool access key when configured.
This repository's `docs/` directory is the user guide, separate from the server's
Swagger `/docs` route.

OpenAI inference domain errors use `{"error":{"message":...,"type":...,"code":...}}`;
management errors use `{"code":...,"message":...,"detail":...}`. Messages uses
Anthropic error envelopes. Security middleware refusals on non-Messages routes
use the management shape, and framework request validation can use its own
`detail` response. Once streaming headers and content have gone out, an error is
conveyed by the stream; HTTP status alone cannot describe its outcome.
