# Configuration

[Documentation](README.md) | [简体中文](configuration.zh-CN.md)

## File location

Channels and Settings can configure the gateway without editing a file. For
file-based configuration, use [config.example.json](../config.example.json) as a
reference and save your own configuration at the path printed by:

```bash
hoshimi config path
```

The command prints an absolute destination even before the file exists. Every
command and the Web UI use this same file, independent of the working directory.

| Platform | Default configuration file |
| --- | --- |
| Linux | `~/.local/share/hoshimi/config.json`, or `$XDG_DATA_HOME/hoshimi/config.json` |
| Windows | `%APPDATA%\hoshimi\config.json` |
| macOS | `~/Library/Application Support/hoshimi/config.json` |

`HOSHIMI_DATA_DIR` relocates all data together; configuration is always
`<data directory>/config.json`. `HOSHIMI_LOG_LEVEL` controls logging.
Run `hoshimi env` to list application environment variables. Provider inventory
environment-variable names describe upstream conventions; they do not replace
credentials in Hoshimi's account configuration.

## Migrating an older installation

`--config` and `HOSHIMI_CONFIG` have been removed. Working-directory configuration
is neither loaded nor migrated automatically. Move the old file to the destination
printed by `hoshimi config path` before starting the upgraded gateway.

## Minimal example

```json
{
  "server": {
    "access_token": "your-tool-access-key",
    "model_api_key": "your-model-api-key"
  },
  "providers": {
    "nvidia": {"accounts": [{"id": "nvidia-1", "api_key": "your-provider-api-key"}]}
  }
}
```

The keys above are placeholders. Add only the providers you use; the full example
contains demonstration credentials and is not a ready-to-run account bundle.

## Fields

| Key | Meaning |
| --- | --- |
| `server.host` / `server.port` | bind address (default `127.0.0.1:10600`); `0` lets the system pick any free port |
| `server.strict_port` | `true` fails at start when `server.port` is taken instead of searching for a free one (default `false`; `serve --strict-port/--no-strict-port` overrides it) |
| `server.access_token` | tool access key for `/api/`, `/docs` and `/openapi.json` |
| `server.model_api_key` | independent API key for external `/v1/` model requests |
| `server.default_model` | model used when a request omits `model` |
| `server.max_body_bytes` / `server.max_account_retries` | limits |
| `server.auto_refresh` | renew credentials on a timer (`false` = rotate tokens by hand) |
| `server.auto_checkin` | daily check-in timer for free credits (`false` = claim by hand) |
| `server.check_updates` | ask PyPI at startup and once a day whether a newer release exists, shown on the Overview page (`false` = no request) |
| `playground.system_prompt` | sent first in every Playground conversation (set in Settings → General); empty sends none |
| `playground.title_model` | the model that names new Playground chats after their first reply; empty uses the chat's own model |
| `playground.vision_model` | describes images for models that cannot read them: `auto` (default) picks the first available model that reads images, `off` leaves images out, or a model id |
| `playground.tools` | the built-in tools Playground models may call (Settings → Tools): `read`, `apply_patch`, `shell`, `terminal` (background commands: `exec_command`, `write_stdin`, `kill_session`), `web_search`, `web_fetch`; none by default. Commands and edits run on this machine as the user running Hoshimi, with no sandbox |
| `playground.workspace` | where the tools work; relative paths start here, and `apply_patch` changes nothing outside it. Empty: `<data dir>/workspace` |
| `playground.approval` | `ask` (default): commands, file changes and reads outside the workspace wait in the chat for the user's approval; `auto` runs them without asking |
| `playground.max_steps` | the most rounds of tool calls one reply makes (default 50); the last round must answer |
| `playground.search_url` | a SearXNG instance for `web_search` (with its JSON format on); empty searches DuckDuckGo |
| `server.stats_keep_days` | days each recorded call is kept in `hoshimi.db` (default `90`, at least `1`); daily totals and lifetime counters are kept regardless |
| `security.allow_cidrs` | client IP allowlist; required to bind non-loopback |
| `security.allow_origins` | CORS origins; requires `server.access_token` |
| `security.allow_hosts` | allowed `Host` header names |
| `providers.<id>.enabled` | `false` disables the provider entirely (this is also how the keyless lanes are turned off) |
| `providers.<id>.accounts` | [provider accounts](providers.md) |
| `providers.<id>.options` | provider-specific options |
| `network` | global upstream connection policy |
| `providers.<id>.options.network` | provider override; defaults to inheriting the global policy |

## Upstream connections and blocked requests

Settings includes global network controls and an override on each provider.
The same policy covers model discovery, inference, login, token refresh, balance
and check-in calls made by the gateway. For example:

```json
{
  "network": {"mode": "proxy", "proxy": "http://127.0.0.1:7890"},
  "providers": {
    "cline": {"options": {"network": {"mode": "inherit"}, "client_profile": "extension"}},
    "ollama": {"options": {"network": {"mode": "direct"}}}
  }
}
```

- `auto` (default) reads `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY`;
  lowercase variables take precedence. `system_proxy: true` enables Windows
  system proxy fallback when no environment proxy is selected.
- `direct` disables proxy selection. `proxy` requires a valid HTTP(S) or SOCKS5
  URL in `proxy`. A configured proxy failure never falls back to direct access.
- Provider mode `inherit` uses the global policy. An explicit provider policy
  replaces it. Loopback destinations always stay local; `NO_PROXY` applies to
  automatic selection, not an explicitly configured proxy.

Proxy URLs may contain credentials. They are stored in the local configuration;
the Settings API only returns whether one is configured, and CLI output masks
them. Clearing a proxy in Settings returns to automatic selection (or inheritance).
Changes apply to new requests while active streams finish on their original
connections. Ordinary API clients do not retain response cookies; each login has
its own isolated cookie session.

Browser pages use the browser's own network configuration. Vertex Google Auth
HTTP calls use the provider policy. Supplementary models.dev metadata uses the
global policy. Bedrock SDK service calls receive the selected
HTTP(S) proxy; the AWS credential discovery chain remains controlled by the AWS
SDK. SOCKS proxies are supported by HTTPX calls, not by the Bedrock SDK transport.
Gateway diagnostics cover HTTPX calls, not internal AWS SDK requests.

HTML block pages, Cloudflare challenges, client restrictions, region restrictions
and signature refusals are classified before token handling. They do not
invalidate stored refresh tokens. A bare HTTP 403 is not treated as expired
credentials. No automatic browser challenge solver or TLS fingerprint emulation
is provided.

Settings displays the latest result per provider and operation.
`GET /api/v1/network/diagnostics` exposes the same management-only data: operation,
host, route source, timestamp, status and failure category. It excludes request
paths, query strings, headers, bodies, cookies and proxy URLs.

Cline loads its free model catalogue from the public recommended-models endpoint.
`providers.cline.options.client_profile` selects the login method: `sdk` (default)
keeps WorkOS device authorization; `extension` uses browser authorization with a
local callback on ports 48801–48811. New credentials record the selected profile,
so existing accounts retain the identity used when they logged in. Changing the
selector applies to subsequent logins. A failed single-use code exchange requires
a new login rather than replaying the code.

Successful model catalogues are saved under `<data directory>/catalog-cache`.
Snapshots are keyed by a hash of the provider, options and account configuration;
the files contain model metadata and account identifiers, not credentials.
Only snapshots at most seven days old are restored. Account or endpoint changes
do not reuse another configuration's snapshot. Failed refreshes retain the last
good catalogue and impose a 30-second retry cooldown; the existing shrink guard
still requires a second successful listing before accepting a large removal.
The catalogue API includes source, last-success time, failure category and retry
time. Cache files can be deleted to discard saved model metadata.

## Data directory files

Records the gateway accumulates live in one SQLite database,
`<data directory>/hoshimi.db`: usage statistics (one row per call for
`server.stats_keep_days` days, hourly totals for 120 days, daily totals and
lifetime counters forever), the daily check-in state and its history (365 days),
probe verdicts, and the Playground's conversations with their attachments (kept
until deleted, with the one open last; temporary chats are deleted when left and
on the next start). Statistics are written in batches about once a second and on
shutdown; a conversation's messages as they are sent.

Files people or other programs read and edit stay plain files: `config.json`,
credentials, `eac-user.json`, `server.json` (the running server's host, actual
port, URL and PID) and `catalog-cache/`.

On the first start after upgrading, `stats.json`, `checkin.json` and
`probe.json` are imported into the database and renamed to `*.migrated`; an
unreadable one is renamed to `*.corrupt` and that store starts empty. Delete
`hoshimi.db` (with the server stopped) to discard every record.

## Port selection

`hoshimi serve` binds the socket itself, so the port it tested is the port it
serves. When `server.port` is taken or reserved, it tries the next port, up to
65535, then wraps around to the ports from 1024 up to the requested one; any
other bind error (such as an address this machine does not have) fails at once.
With `server.strict_port` (or `--strict-port`) only the requested port is tried.
The port actually bound is logged, written to `server.json` and reported by
`GET /api/v1/settings` as `listening`; a fallback port is not a pending change and
does not ask for a restart.

## CLI changes

```bash
hoshimi config show
hoshimi config get server.port
hoshimi config set server.port 10600
hoshimi config set providers.kilo.enabled false
hoshimi check
```

Values are parsed as JSON when possible; `null` clears a value. Read commands mask
credentials. CLI changes and direct file edits require a server restart.

## Applying changes from the UI

Settings saves write the file and update the running gateway. Account additions
append rather than replace credentials, validate the account before saving, and
enable the corresponding provider. Account deletion also updates the live pool.

Only pools whose inputs changed are rebuilt. Editing another provider or a server
field preserves unrelated account holds and login state. Network-only edits also
preserve account state. Provider HTTP clients are retained so in-flight streams
can finish. Other provider rebuilds recreate their
in-memory account state, including `needs_login`. Listening address, port and
`strict_port` changes require restarting the server to rebind its socket;
`stats_keep_days` applies from the next pruning, which runs with newly recorded
calls at most once a minute.

Writes use atomic replacement and best-effort restrictive permissions. An invalid
or unreadable file stops startup; malformed configuration is backed up when possible.

## LAN access

For example, configure these fields, then restart:

```json
{
  "server": {
    "host": "0.0.0.0",
    "access_token": "your-tool-access-key",
    "model_api_key": "your-model-api-key"
  },
  "security": {"allow_cidrs": ["192.168.1.0/24"]}
}
```

Give inference clients the model API key. Management needs the separate tool key.
Add `security.allow_origins` only for browser clients using another origin, and
`security.allow_hosts` as needed for the deployment's Host checks. See
[authentication](authentication.md) for the access boundaries.
