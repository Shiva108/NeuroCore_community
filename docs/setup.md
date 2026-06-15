# Setup Guide

This guide keeps the root README short while giving new contributors a complete
path from clone to verified local install.

## Prerequisites

- Python 3.11 or newer
- `pip`
- Optional: `venv` or `uv`

## Recommended Bootstrap

The fastest safe path is the bootstrap script:

```bash
python scripts/bootstrap.py
source .venv/bin/activate
```

This flow:

- creates or reuses `.venv`
- installs the package in editable mode with development extras
- copies local configuration templates into the operator home outside the repo
- runs `pytest` and repository validation unless you pass `--skip-verify`

Use the guided mode if you want prompts for namespace and overwrite behavior:

```bash
python scripts/bootstrap.py --wizard
```

## Manual Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,semantic]"
```

Create local-only config in the operator home:

```bash
export NEUROCORE_OPERATOR_HOME="${XDG_STATE_HOME:-$HOME/.local/state}/neurocore"
mkdir -p "$NEUROCORE_OPERATOR_HOME"
cp .env.example "$NEUROCORE_OPERATOR_HOME/.env"
cp secrets.json.example "$NEUROCORE_OPERATOR_HOME/secrets.json"
cp preferences.json.example "$NEUROCORE_OPERATOR_HOME/preferences.json"
```

If you need custom ingest defaults:

```bash
cp ingest-profiles.json.example ingest-profiles.json
```

Do not commit populated `.env`, `secrets.json`, `preferences.json`, or local
database files.

## Validation

Run the publication baseline before opening a pull request:

```bash
make test
make lint
make validate
make openapi-check
```

## Next Steps

- Read [reference-stack.md](reference-stack.md) for the recommended local path.
- Read [hosted-stack.md](hosted-stack.md) if you plan to use hosted Postgres or
  mirror mode.
- Read [troubleshooting.md](troubleshooting.md) if setup fails.
