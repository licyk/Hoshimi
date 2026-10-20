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
| `providers.<id>.accounts` | [provider accounts](providers.md) |
| `providers.<id>.options` | provider-specific options |

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
field preserves unrelated account holds and login state. The shared HTTP client
is retained so in-flight streams can finish. Rebuilding a provider recreates its
in-memory account state, including `needs_login`. Listening address and port
changes require restarting the server to rebind its socket.

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
