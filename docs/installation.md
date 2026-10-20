# Installation and Web UI

[Documentation](README.md) | [简体中文](installation.zh-CN.md)

## Install or upgrade

Requires Python 3.10 or newer:

```bash
python -m pip install -U hoshimi
hoshimi serve
```

Open `http://127.0.0.1:10600`. The package includes the built Web UI, so ordinary
users do not need Bun or a frontend build. `python -m hoshimi` is equivalent to
the `hoshimi` command. Source checkouts need a UI build; see [development](development.md).

OpenCode Zen and Kilo provide anonymous entry points by default, without an
account or configuration file. Available models depend on the upstream catalogue,
quota and service state. Add other accounts in Channels, or disable unused
providers in Settings. A configured key can expose a different upstream catalogue.

## Optional cloud dependencies

```bash
python -m pip install -U "hoshimi[google]"   # Vertex ADC / service accounts
python -m pip install -U "hoshimi[bedrock]"  # Bedrock SDK and bearer event streams
```

Use `"hoshimi[google,bedrock]"` for both. Bedrock needs this extra even in bearer
mode to decode its event stream. Other ordinary HTTP API integrations do not need
these SDKs. See [cloud platforms](cloud-providers.md) for accounts and protocols.

## Commands

| Command | Purpose |
| --- | --- |
| `hoshimi serve` | Start the API and Web UI |
| `hoshimi check` | Validate configuration without serving |
| `hoshimi models` | List models |
| `hoshimi providers` | List providers |
| `hoshimi stations` | List `agg` presets |
| `hoshimi login [provider]` | List login providers or start authorization |
| `hoshimi version` | Show application and component versions |
| `hoshimi env` | List environment variables |
| `hoshimi config show/get/set/path` | Inspect and change configuration |

Use `<command> --help` for parameters.

## Server options

| Option | Purpose |
| --- | --- |
| `--host`, `--port` | Listening address and starting port; port `0` chooses a free port |
| `--strict-port/--no-strict-port` | Fail on a busy port, or try the next one |
| `--open/--no-open` | Open the browser |
| `--ui/--no-ui` | Serve the Web UI or only the API |
| `--access-token` | Tool access key |
| `--model-api-key` | External inference key |
| `--default-model` | Model to use when omitted from a request |
| `--refresh/--no-refresh` | Automatic credential renewal |
| `--checkin/--no-checkin` | Automatic daily credit claims |

If port 10600 is busy, the default mode tries the next port; use the URL printed
by the server. Use `--strict-port` when a client requires a fixed address.
See [configuration](configuration.md) for persistence and migration instructions,
and [authentication](authentication.md) before enabling remote access.

## Web UI

| Page | Features |
| --- | --- |
| Overview | Health, endpoints and provider summary |
| Models | Search, capabilities, free markers, availability probes and catalogue retry |
| Channels | Accounts, OAuth/device login, expiry, renewal, balance, check-in and deletion |
| Usage | Daily and per-model tokens, TTFT, generation speed and recent calls |
| Playground | Streaming chat, model selection and reasoning effort |
| Settings | Server, independent gateway keys, network policy, providers and appearance |

The interface follows the system language (`en`, `zh-CN`, `ja`). Theme, accent
color and the saved tool access key live in localStorage. Playground's Auto effort
does not select an explicit level. Models and Playground share an incremental SSE
catalogue: cached rows appear first, then providers update independently. Failure
preserves the last healthy catalogue and offers retry. Standard `/v1/models`
remains JSON for SDKs. See [model behavior](model-behavior.md) for details.
