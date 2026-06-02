# Configuration Guide

NeuroCore Community reads runtime configuration from environment variables. The
recommended starting point is `.env.example`.

## Safe Defaults

The default local workflow uses:

- local SQLite storage
- operator state outside the repository checkout
- CLI enabled
- HTTP, MCP, dashboard, and multi-model consensus disabled by default

## Operator-Home Env File

Do not create a committed `.env` file in the repository root. Instead, copy the
values from `.env.example` into the operator-home env file created by
`scripts/bootstrap.py`.

Sensitive fields include:

- `NEUROCORE_CONSENSUS_API_KEY`
- `NEUROCORE_PRODUCTION_DATABASE_URL`
- `NEUROCORE_PRODUCTION_SEALED_DATABASE_URL`
- provider-specific API keys and bearer tokens

## Example Templates

The repository includes safe templates for local helper tooling:

- `secrets.json.example`
- `preferences.json.example`
- `ingest-profiles.json.example`

Copy them to local-only files before use and keep the real files out of git.

## Provider Hygiene

- never paste real keys into committed JSON, Markdown, or screenshots
- sanitize provider base URLs if they contain embedded credentials
- prefer test accounts and low-privilege tokens during development

## Common Overrides

- `NEUROCORE_DEFAULT_NAMESPACE`: default namespace for capture/query operations
- `NEUROCORE_ALLOWED_BUCKETS`: allowed bucket list
- `NEUROCORE_STORAGE_BACKEND`: `in_memory`, `sqlite`, `postgres`, or `mirror`
- `NEUROCORE_ENABLE_HTTP_ADAPTER`: enable the FastAPI surface
- `NEUROCORE_ENABLE_MCP_ADAPTER`: enable the MCP adapter
- `NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS`: enable multi-model summarization and reporting

For the full contract and supported settings, see `docs/ssd/specification.md`.
