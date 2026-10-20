# Hoshimi documentation

[Project home](../README.md) | [简体中文](README.zh-CN.md)

Start with installation, configuration and client integration. Anonymous entry
points can be used immediately without an account or configuration file.

| Read by task |
| --- |
| [Install, upgrade, CLI and Web UI](installation.md) |
| [Configuration, paths and migration](configuration.md) |
| [Keys, login, renewal and EAC](authentication.md) |
| [API, clients and model routing](api.md) |
| [Anthropic and agent integration](anthropic-api.md) |
| [Provider accounts, agg and local models](providers.md) |
| [Azure, Cloudflare, Vertex, Bedrock and special protocols](cloud-providers.md) |
| [NVIDIA NIM](nvidia.md) |
| [Capabilities, effort, probes, retries, quota and stats](model-behavior.md) |
| [Development, architecture, checks and releases](development.md) |
| [OpenCode protocol and verification scope](opencode-integration.md) |
| [pi-free migration and compatibility differences](pi-free.md) |
| [Provider source and deduplication inventory (generated)](provider-inventory.md) |

Handwritten guides have matching English and Chinese pages, linked at the top.
The running server's `/docs` and `/openapi.json` provide complete API definitions;
this directory explains usage and compatibility.

## Provider inventory

`provider-inventory.md` is shared between languages and generated from
`hoshimi/core/provider_sources.py`:

```bash
python scripts/generate_provider_inventory.py
```

Source snapshots and coverage labels do not establish current live availability.
Use the provider guides for registration, plan and authentication distinctions.
Reference repositories are not runtime dependencies.
