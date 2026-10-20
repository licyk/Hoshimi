# Hoshimi

[English](https://github.com/licyk/Hoshimi/blob/main/README.md) | [简体中文](https://github.com/licyk/Hoshimi/blob/main/README.zh-CN.md)

An OpenAI- and Anthropic-compatible gateway connecting free model stations,
cloud services and local models through one API and Web UI.

- **One API** for Chat Completions, Responses and Anthropic Messages.
- **Account management** with API keys, browser/device login, renewal and rotation.
- **Model discovery** with capabilities, reasoning menus and availability probes.
- **Usage visibility** with token totals, first-token latency and generation speed.
- **A multilingual Web UI** for channels, models, chat, usage and settings.

## Quick start

Requires **Python 3.10 or newer**:

```bash
python -m pip install -U hoshimi
hoshimi serve
```

Open **http://127.0.0.1:10600**. The package includes the built Web UI;
no frontend build is needed. If that port is busy, use the URL printed by the server.

OpenCode Zen and Kilo anonymous entry points are enabled by default, without a
configuration file. Availability depends on upstream models, quota and service state.
Add other provider accounts in **Channels**, and configure the gateway in **Settings**.

`python -m hoshimi` is equivalent to `hoshimi`. To locate the shared configuration:

```bash
hoshimi config path
```

For optional cloud dependencies, CLI options, configuration migration and source
installation, see the [installation guide](https://github.com/licyk/Hoshimi/blob/main/docs/installation.md)
and [configuration guide](https://github.com/licyk/Hoshimi/blob/main/docs/configuration.md).

## First API call

List models and replace `<model-id>` with an ID from the response:

```bash
curl http://127.0.0.1:10600/v1/models

curl http://127.0.0.1:10600/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"hi"}]}'
```

If a model API key is configured, add `Authorization: Bearer <model-api-key>`
to both requests. Chat Completions streams by default; send `"stream": false`
for JSON.

OpenAI-compatible clients generally use `http://127.0.0.1:10600/v1` as Base URL.
Anthropic-compatible agents use `http://127.0.0.1:10600` and a model from the same
catalogue. See [API integration](https://github.com/licyk/Hoshimi/blob/main/docs/api.md)
and [Anthropic agents](https://github.com/licyk/Hoshimi/blob/main/docs/anthropic-api.md).

## Access keys

Configure two independent keys in Settings:

- **Tool access key** protects management APIs, settings and API documentation.
- **Model API key** protects external `/v1/*` inference requests.

Provider account credentials are configured separately in Channels. An empty gateway
key disables authentication only for its own surface. For LAN access, configure the
tool key and permitted client networks; see
[authentication](https://github.com/licyk/Hoshimi/blob/main/docs/authentication.md).

## Documentation

[Documentation index](https://github.com/licyk/Hoshimi/blob/main/docs/README.md)

| Task | Guide |
| --- | --- |
| Install, upgrade or use the Web UI | [Installation](https://github.com/licyk/Hoshimi/blob/main/docs/installation.md) |
| Configure paths, fields or migrate an older install | [Configuration](https://github.com/licyk/Hoshimi/blob/main/docs/configuration.md) |
| Set keys, sign in or use EAC | [Authentication](https://github.com/licyk/Hoshimi/blob/main/docs/authentication.md) |
| Connect clients or agents | [API](https://github.com/licyk/Hoshimi/blob/main/docs/api.md) · [Anthropic](https://github.com/licyk/Hoshimi/blob/main/docs/anthropic-api.md) |
| Add cloud or local accounts | [Providers](https://github.com/licyk/Hoshimi/blob/main/docs/providers.md) · [Cloud platforms](https://github.com/licyk/Hoshimi/blob/main/docs/cloud-providers.md) · [NVIDIA](https://github.com/licyk/Hoshimi/blob/main/docs/nvidia.md) |
| Understand capabilities, retries, quota or statistics | [Model behavior](https://github.com/licyk/Hoshimi/blob/main/docs/model-behavior.md) |
| Develop, build or release | [Development](https://github.com/licyk/Hoshimi/blob/main/docs/development.md) |
| Review migration coverage and protocol differences | [Inventory](https://github.com/licyk/Hoshimi/blob/main/docs/provider-inventory.md) · [OpenCode](https://github.com/licyk/Hoshimi/blob/main/docs/opencode-integration.md) · [pi-free](https://github.com/licyk/Hoshimi/blob/main/docs/pi-free.md) |

Full endpoint schemas are available at the running server's `/docs` and `/openapi.json`.
They use the tool access key when configured.

## License

GNU General Public License, version 3 only (**GPL-3.0-only**).
See [LICENSE](https://github.com/licyk/Hoshimi/blob/main/LICENSE).
