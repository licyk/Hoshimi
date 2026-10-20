# Authentication and accounts

[Documentation](README.md) | [简体中文](authentication.zh-CN.md)

## Gateway keys and upstream credentials

| Credential | Purpose | How to send it |
| --- | --- | --- |
| `server.access_token` | Management `/api/v1/*`, `/docs`, `/openapi.json` | `Authorization: Bearer <tool-access-key>` |
| `server.model_api_key` | External inference `/v1/*` | `Authorization: Bearer <model-api-key>` or `x-api-key` |
| Provider account key/token | Authenticate Hoshimi to the upstream service | Store it under `providers.<id>.accounts` |

The two gateway keys are independent. Leaving one empty disables authentication
only for its surface. An explicit Authorization header takes precedence over
`x-api-key`; the latter never grants management access. Health probes and the UI
shell are public, subject to the network policy below.

Configure gateway keys in Settings. The browser prompts for the tool key when
required, validates it, and stores it in localStorage. A failed sign-in does not
replace the saved credential. Saving or clearing that key updates the current
browser after the server accepts it; other browsers must sign in again.
Playground uses tool-authenticated management routes and does not need the model
API key. Settings responses expose configured flags, not secret values.

## Signing in

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
See [providers](providers.md) for account fields.

Cancellation prevents a late callback from saving an account. For a remote or
headless installation, use `--no-open --manual` when the provider supports manual
callback input. This does not make every loopback flow work across devices.

## Credential storage and renewal

Accounts live in the data-directory `config.json`; use `hoshimi config path` to
locate it. Treat the file as a credential store and do not share filled examples.
Writes use atomic replacement and best-effort owner-only permissions. Invalid
configuration is backed up before startup fails; it is not silently replaced.

With automatic refresh enabled, a sweep every 30 minutes renews accounts expiring
within the next hour. A rejected refresh token marks an account `needs_login`.
`GET /api/v1/refresh` reads renewal state; `POST /api/v1/refresh` renews on demand.
Refresh operations share account-level locks. Account failures, quota exhaustion
and model failures have different handling; see [model behavior](model-behavior.md).

## EAC

`eac` has no anonymous mode: it is built only when an account is configured, and
`GET /api/v1/providers` lists it anyway with `requires_authorization: true`, so the
settings page can label the lane as needing one. Two modes, decided by the account's
credential:

- **direct** — `access_token` is sent as `Authorization: Bearer …`.
- **worker** — the account holds a shared `signing_secret`; the request carries no
  secret, only `x-ofm-timestamp` / `x-ofm-signature` = HMAC-SHA256 over
  `timestamp\nMETHOD\npath\nsha256(body)`, computed over the exact bytes sent
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
See [authentication and EAC behavior](authentication.md).

## Network access

Non-loopback listening requires a tool access key; configure `security.allow_cidrs`
to admit remote clients. Configure a model API key to control external inference
access independently. CORS allowlists require a tool access key. Host and browser
origin checks also apply; keys do not bypass them.

See [configuration](configuration.md) for a LAN example and the full field list.
