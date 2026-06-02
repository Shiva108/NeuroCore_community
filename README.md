# NeuroCore Community

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)

NeuroCore Community is the public, contributor-friendly edition of NeuroCore: a
contract-first Python package for capturing, storing, querying, summarizing,
and governing policy-aware notes and documents.

**Repository version:** `0.2` (derived from `git rev-list --count HEAD`)

This repository is intentionally curated from a private development repo. It
ships the public package source, automated tests, community-facing docs, JSON
contracts, and a small set of checkout-safe maintenance scripts. It does not
attempt to mirror every private workflow, document, or integration package.

## Overview

NeuroCore Community provides a shared core for memory-style workflows with
multiple adapters around it. The current public repository includes:

- a Python package under `src/neurocore/`
- CLI commands for capture, query, diagnosis, reporting, sessions, ingest, and
  adapter serving
- optional FastAPI and MCP adapters behind environment flags
- local SQLite, in-memory, Postgres, and mirrored storage backends
- governance checks, an OpenAPI snapshot, and a broad pytest suite
- public setup, configuration, troubleshooting, and SSD design docs

## Highlights

- contract-first Python package under `src/neurocore/`
- CLI, HTTP, and MCP adapters backed by shared interfaces
- automated tests, repo validation, and OpenAPI snapshot checks
- SSD architecture and specification docs under `docs/ssd/`
- bootstrap and checkout-safe helper scripts for local development

## Security First

Do not commit API keys, local `.env` files, database files, `token.json`,
`secrets.json`, or `preferences.json`.

- Copy `.env.example` into your operator-home env file, not into the repo root.
- Treat `secrets.json.example`, `preferences.json.example`, and
  `ingest-profiles.json.example` as templates only.
- Sanitize provider URLs, headers, and logs before opening issues or sharing
  test output.
- Use the private reporting guidance in [SECURITY.md](SECURITY.md) for
  vulnerabilities. Do not file public security issues.

## Quickstart

Use the supported bootstrap path first:

```bash
python scripts/bootstrap.py
source .venv/bin/activate
python scripts/neurocore_checkout.py diagnose
pytest
python scripts/validate_checkout.py
```

For the smallest end-to-end workflow, use the checkout-safe CLI wrapper:

```bash
python scripts/neurocore_checkout.py capture --request-json '{"bucket":"research","content":"community repo note","content_format":"markdown","source_type":"note"}'
python scripts/neurocore_checkout.py query --request-json '{"query_text":"community repo","namespace":"default","allowed_buckets":["research"],"sensitivity_ceiling":"standard"}'
```

The installed `neurocore` console script is also available after
`pip install -e ".[dev]"`, but it expects the required `NEUROCORE_*`
configuration variables to already be present in the process environment.

## Installation Instructions

### Prerequisites

- Python 3.11 or newer
- Python `venv` support
- network access for `pip install`

### Recommended Setup

```bash
python scripts/bootstrap.py
source .venv/bin/activate
```

`scripts/bootstrap.py` performs the supported local setup flow:

- creates `.venv` if needed
- installs the package in editable mode with `.[dev]`
- writes an operator-home `.env` file outside the repository checkout
- runs `pytest` and repository validation unless `--skip-verify` is used

If you already have an operator-home env file and want to preserve it, run the
bootstrap script as-is. If you want to overwrite it, use:

```bash
python scripts/bootstrap.py --force-env
```

### Manual Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

After manual setup, copy values from `.env.example` into your operator-home env
file. Do not create a committed `.env` file in the repository root.

If you want the optional sentence-transformers semantic ranker, install the
extra explicitly:

```bash
python -m pip install -e ".[dev,semantic]"
```

## Usage Guide

The repository exposes three practical usage modes: checkout-safe local CLI
work, direct console-script usage in an already-configured environment, and
optional adapter serving.

### 1. Checkout-safe local CLI workflow

`scripts/neurocore_checkout.py` loads the operator-home env file and runs the
CLI with the repo-local interpreter when available.

Inspect runtime health:

```bash
python scripts/neurocore_checkout.py diagnose
```

Run a capture and query cycle:

```bash
python scripts/neurocore_checkout.py capture --request-json '{"bucket":"research","content":"community repo note","content_format":"markdown","source_type":"note"}'
python scripts/neurocore_checkout.py query --request-json '{"query_text":"community repo","namespace":"default","allowed_buckets":["research"],"sensitivity_ceiling":"standard"}'
```

Other implemented top-level CLI commands are:

- `capture-batch`
- `briefing`
- `report consensus`
- `protocol list`
- `protocol run`
- `session capture-event`
- `session checkpoint`
- `session resume`
- `brain create|get|list|update|archive`
- `ingest slack|discord`
- `summaries run`
- `admin update|delete|reindex|audit|sync` when
  `NEUROCORE_ENABLE_ADMIN_SURFACE=true`
- `serve http`
- `serve mcp`

### 2. Direct `neurocore` console script

After editable installation, this entrypoint is provided by `pyproject.toml`:

```bash
neurocore --help
```

Use it only after exporting or otherwise supplying the required
`NEUROCORE_*` variables to the current shell or process.

### 3. Optional adapter serving

HTTP and MCP adapters are present in the package but disabled by default.
Enable them in your environment before serving.

FastAPI HTTP adapter:

```bash
NEUROCORE_ENABLE_HTTP_ADAPTER=true \
python scripts/neurocore_checkout.py serve http --host 127.0.0.1 --port 8000
```

MCP adapter:

```bash
NEUROCORE_ENABLE_MCP_ADAPTER=true \
python scripts/neurocore_checkout.py serve mcp --transport stdio
```

The checked-in HTTP contract snapshot lives at
[`schemas/neurocore-http-openapi.json`](schemas/neurocore-http-openapi.json).

## Example Flow

See [examples/quickstart-cli.md](examples/quickstart-cli.md) for the minimal
capture-and-query walkthrough.

If you prefer to run the installed console script instead of the wrapper,
provide the required environment values first and then run:

```bash
neurocore capture --request-json '{"bucket":"research","content":"community repo note","content_format":"markdown","source_type":"note"}'
neurocore query --request-json '{"query_text":"community repo","namespace":"default","allowed_buckets":["research"],"sensitivity_ceiling":"standard"}'
```

## Repository Structure

```text
.
|-- .github/
|   |-- ISSUE_TEMPLATE/
|   `-- workflows/
|-- .env.example
|-- CHANGELOG.md
|-- CONTRIBUTING.md
|-- Makefile
|-- SECURITY.md
|-- pyproject.toml
|-- assets/
|   `-- screenshots/
|-- docs/
|   |-- configuration.md
|   |-- setup.md
|   |-- troubleshooting.md
|   `-- ssd/
|-- examples/
|   `-- quickstart-cli.md
|-- schemas/
|   `-- neurocore-http-openapi.json
|-- scripts/
|   |-- bootstrap.py
|   |-- generate_openapi_snapshot.py
|   |-- mock_openai_compatible.py
|   |-- neurocore_checkout.py
|   `-- validate_checkout.py
|-- src/
|   `-- neurocore/
|       |-- adapters/
|       |-- core/
|       |-- governance/
|       |-- ingest/
|       |-- interfaces/
|       |-- maintenance/
|       |-- reporting/
|       |-- retrieval/
|       |-- storage/
|       `-- summarization/
`-- tests/
    |-- core/
    |-- ingest/
    |-- interfaces/
    |-- reporting/
    |-- retrieval/
    `-- summarization/
```

Major components:

- `.github/`: issue templates, contribution metadata schemas, and GitHub
  Actions workflows such as repo gating and CodeQL.
- `.env.example`: default operator-home environment template for the supported
  local SQLite workflow.
- `pyproject.toml` and `Makefile`: package metadata, dependency extras,
  console-script registration, and common development commands.
- `src/neurocore/adapters/`: CLI, FastAPI, MCP, dashboard rendering, and
  OpenAPI snapshot helpers.
- `src/neurocore/core/`: configuration, models, operator-state helpers,
  policies, semantic setup, and shared runtime logic.
- `src/neurocore/interfaces/`: stable request/response oriented entrypoints for
  capture, query, briefing, reporting, admin, ingest, sessions, and protocols.
- `src/neurocore/storage/`: in-memory, SQLite, Postgres, mirrored, and routed
  storage implementations.
- `src/neurocore/reporting/` and `src/neurocore/summarization/`: consensus
  reporting and summarization workflows.
- `src/neurocore/ingest/`: article fetch, normalization, chunking, and dedup
  utilities.
- `tests/`: pytest coverage for config, runtime, storage contracts, adapters,
  interfaces, reporting, retrieval, summarization, and ingest.
- `docs/`: public contributor docs plus SSD architecture and specification
  material.
- `scripts/`: supported local bootstrap, wrapper, validation, mock, and schema
  snapshot entrypoints.
- `schemas/`: checked-in API contract artifacts generated from the HTTP adapter.
- `CHANGELOG.md`, `CONTRIBUTING.md`, and `SECURITY.md`: release notes,
  contribution rules, and security disclosure guidance.

## Repository Layout

See [Repository Structure](#repository-structure) for the current annotated tree.

## Core Commands

```bash
make test
make lint
make validate
make openapi-check
```

Optional local formatting:

```bash
make format
```

## Documentation

Public-facing setup guides live under [docs/](docs/README.md):

- [Setup Guide](docs/setup.md)
- [Configuration Guide](docs/configuration.md)
- [Troubleshooting](docs/troubleshooting.md)

Public contract docs:

- [Architecture](docs/ssd/architecture.md)
- [Specification](docs/ssd/specification.md)
- [Implementation Plan](docs/ssd/implementation-plan.md)
- [Source Matrix](docs/ssd/source-matrix.md)
- [Hierarchical Summarization](docs/ssd/hierarchical-summarization.md)

## What Is Included

- the `neurocore` Python package under `src/neurocore/`
- automated tests under `tests/`
- SSD architecture and specification docs under `docs/ssd/`
- local development helpers under `scripts/`
- CI, repo validation, and OpenAPI snapshot checks

## What Is Intentionally Excluded

- private Git history and operational artifacts
- personal development workflows and internal planning notes
- specialized hosted proof, replication, and security-operator workflows
- concrete Slack, Discord, or desktop connector packages
- secrets, local runtime state, and provider-specific credentials

## Troubleshooting

Common first-time issues:

- `ModuleNotFoundError: neurocore`: activate `.venv` or reinstall with
  `python -m pip install -e ".[dev]"`.
- config errors from the installed `neurocore` command: export the required
  `NEUROCORE_*` variables first, or use `python scripts/neurocore_checkout.py`.
- bootstrap env-file errors: make sure `NEUROCORE_OPERATOR_HOME` points outside
  the repository checkout.
- repo validation failures: remove local secrets or runtime artifacts from the
  checkout and rerun `python scripts/validate_checkout.py`.
- OpenAPI snapshot drift: rerun `python scripts/generate_openapi_snapshot.py`
  and then `make openapi-check` if the contract intentionally changed.

More detail is available in [docs/troubleshooting.md](docs/troubleshooting.md).

## Contributing

Contribution guidance lives in [CONTRIBUTING.md](CONTRIBUTING.md). Before
opening a pull request, run:

```bash
make test
make lint
make validate
make openapi-check
```

## Security

Security reporting guidance lives in [SECURITY.md](SECURITY.md).

## Release Model

This repository is maintained as a public community edition. Public releases are
curated drops from a separate private development repository. Community changes
land here first, then are intentionally ported back when appropriate.

## License

NeuroCore Community is licensed under the [Apache License 2.0](LICENSE).
