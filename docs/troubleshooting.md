# Troubleshooting

## `ModuleNotFoundError: neurocore`

Activate `.venv` and reinstall in editable mode:

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Bootstrap Fails While Creating the Operator Env File

Check whether `NEUROCORE_OPERATOR_HOME` points inside the repository checkout.
The bootstrap script rejects that layout to keep local secrets and runtime state
out of git.

## Repo Validation Fails

Run:

```bash
python scripts/validate_checkout.py
```

Typical causes:

- committed or untracked local secrets such as `.env`, `token.json`, or `secrets.json`
- database files or runtime outputs under the checkout
- stale repo guidance or metadata drift

## OpenAPI Snapshot Drift

If a contract change was intentional:

```bash
python scripts/generate_openapi_snapshot.py
python scripts/generate_openapi_snapshot.py --check
```

If the change was not intentional, inspect recent adapter or schema edits before
updating the snapshot.

## Lint Failures

Apply formatting first:

```bash
make format
make lint
```

## Multi-Model Consensus Is Reported As Unhealthy

Check that all required provider settings are present and valid:

- base URL
- API key
- at least two unique model names for consensus mode

Use sanitized values when sharing diagnostics publicly.
