# Troubleshooting

## `ModuleNotFoundError: No module named neurocore`

Install the package in editable mode:

```bash
python -m pip install -e ".[dev]"
```

## `ConfigError: Missing required configuration`

Create the operator home and copy the example files:

```bash
export NEUROCORE_OPERATOR_HOME="${XDG_STATE_HOME:-$HOME/.local/state}/neurocore"
mkdir -p "$NEUROCORE_OPERATOR_HOME"
cp .env.example "$NEUROCORE_OPERATOR_HOME/.env"
cp secrets.json.example "$NEUROCORE_OPERATOR_HOME/secrets.json"
cp preferences.json.example "$NEUROCORE_OPERATOR_HOME/preferences.json"
```

At minimum, configure:

- `NEUROCORE_DEFAULT_NAMESPACE`
- `NEUROCORE_ALLOWED_BUCKETS`
- `NEUROCORE_DEFAULT_SENSITIVITY`

## `black` or `flake8` not found

Reinstall development dependencies:

```bash
python -m pip install -e ".[dev]"
```

## Postgres backend fails at startup

If `NEUROCORE_STORAGE_BACKEND=postgres`, set both:

- `NEUROCORE_PRODUCTION_DATABASE_URL`
- `NEUROCORE_PRODUCTION_SEALED_DATABASE_URL`

If you use mirror mode, verify the same hosted values are available to the
runtime before starting the HTTP or CLI surfaces.

## Governance validation reports secret-like values

The validator is conservative by design. Replace real credentials with
placeholders, move local values into ignored files, then rerun:

```bash
python scripts/validate_checkout.py
```
