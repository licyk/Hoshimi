# Development

[Documentation](README.md) | [简体中文](development.zh-CN.md)

## Source setup

For development, work from a source checkout and install the editable package
with its development dependencies. Frontend development also requires Bun:

```bash
python -m pip install -e ".[dev]"
python scripts/dev.py web-install
python scripts/dev.py dev
```

Vite on `:5173` proxies the API on `:10600`. Options include `--api-port`,
`--web-port`; set `HOSHIMI_DATA_DIR` to choose the shared data directory. Ctrl+C stops both processes. To serve the UI directly
with `hoshimi serve` from a checkout, build it with `python scripts/dev.py web`.
Without a UI build, the server logs a warning and serves the API only.

## Checks and builds

```bash
python scripts/dev.py lint         # Ruff
python scripts/dev.py test-py      # pytest
python scripts/dev.py test-web     # Vitest + vue-tsc
python scripts/dev.py check        # lint, Python types, all tests, generated types
python scripts/dev.py wheel        # web build + wheel/sdist in dist/
```

`dev.py` also has `format`, `typecheck-py`, `web-install`, `web`, `web-dev`, `dev`,
`openapi`, `typegen`, and `typegen-check`. Run it without arguments to see the full list.

The development extra includes the official Anthropic SDK. Messages regression
tests use SDK JSON and stream helpers against a local mock upstream; no real
provider credentials are required. Heartbeat/cancellation tests separately check
timing and complete SSE-frame boundaries. Real Claude Code can also be exercised
against a configured gateway using the [client setup](anthropic-api.md#claude-code-configuration).

## CI and releases

CI uses one automatic entry point, `ci.yml`, for pushes to `main` and `hoshimi_main`,
`v*` tags and pull requests. It runs the shared `checks.yml` once, then `build.yml`
builds the Web UI, wheel and source distribution and checks installed packages.
When a branch push changes `hoshimi/version.py`, or a version tag is pushed, CI
calls `release.yml` to publish those same artifacts to PyPI; tags also create a
GitHub release. Release has no separate push trigger, so one push never starts
a second copy of the checks or build. The release plan reads local Git history,
including the oldest pushed commit's parent for a new branch with an all-zero
`before` SHA, without depending on the compare API.

A manual Release run performs the same shared checks and build first. Its
`publish` switch defaults to off, producing checked artifacts only. The shared
checks expose a `CI passed` result; automatic releases wait for both the checks
and the installed-package checks in the build. A newer push does not cancel an
automatic release already in progress.

## Generated API types

The web UI's TypeScript types come from the server's OpenAPI document:

```bash
python scripts/generate_openapi.py > hoshimi/webui/openapi.json
cd hoshimi/webui && bun run typegen   # rewrites src/api/schema.d.ts — commit it
```

## Architecture

```
hoshimi/
  core/        config, credentials, registry, refresh, expiry, chunks, SSE, translate/, providers/ (native adapters and shared transports)
  api/         FastAPI app, middleware, routers/ (openai + management), SPA serving
  cli/         Typer commands: serve, check, models, providers, stations, login, version, env, config
  webui/       Vue 3 + Vite: src/{ui,views,api,stores,i18n,theme}, dist/ ships in the package
tests/         core/ api/ cli/ — the layering rule keeps core free of HTTP frameworks
scripts/       generate_openapi.py — the document behind webui/src/api/schema.d.ts
```

The adapter contract lives in `hoshimi/core/providers/base.py`: every provider
yields normalized chunks (`text_delta`, `reasoning_delta`, `reasoning_details`, `tool_call_start`,
`tool_call_delta`, `usage`, `finish`, `error`) which `chunks.py` re-emits as Chat
Completions SSE, Responses SSE, or aggregated JSON.

Protocol references: `codex-api` (server/security), `dsh-our-free-model` (station
protocols) and `Hanaikada` (the layered Python + Vue skeleton this project follows,
including `hoshimi/webui`). All three are external clones and are git-ignored.

## Documentation maintenance

Keep handwritten English topics and their `.zh-CN.md` counterparts in sync.
Configuration follows `hoshimi/core/config.py` and `paths.py`; routes follow
`hoshimi/api/routers/`; provider registration and account fields follow
`factory.py` and `provider_forms.py`. Generate the shared source inventory from
`provider_sources.py`:

```bash
python scripts/generate_provider_inventory.py
```

Keep one generated table with bilingual index guidance. Check relative links,
heading anchors, JSON examples and `git diff --check` after maintenance.
Root README links use full GitHub URLs so they also work in the package description.
