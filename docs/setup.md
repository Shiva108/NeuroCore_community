# Setup Guide

This guide covers the supported local-development path for NeuroCore Community.

## Prerequisites

- Python 3.11 or newer
- `venv` support enabled in your Python installation
- network access for `pip install`

## Recommended Bootstrap Flow

```bash
python scripts/bootstrap.py
source .venv/bin/activate
```

The bootstrap script:

- creates `.venv` when needed
- installs the package in editable mode with development dependencies
- creates an operator-home `.env` file outside the repository checkout
- optionally runs tests and repo validation

## Manual Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

After installation, create your operator-home env file by copying values from
`.env.example`.

## Validation Commands

Run these before opening a pull request:

```bash
make test
make lint
make validate
python scripts/generate_openapi_snapshot.py --check
```

Optional local formatting:

```bash
make format
```

## First CLI Smoke Test

```bash
neurocore capture --request-json '{"bucket":"research","content":"community repo note","content_format":"markdown","source_type":"note"}'
neurocore query --request-json '{"query_text":"community repo","namespace":"default","allowed_buckets":["research"],"sensitivity_ceiling":"standard"}'
```

See [../examples/quickstart-cli.md](../examples/quickstart-cli.md) for the same
flow in a copyable reference format.
