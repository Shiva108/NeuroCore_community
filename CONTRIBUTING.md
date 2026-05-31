# Contributing to NeuroCore Community

## Workflow

Start by reading the active contract docs:

- `docs/ssd/architecture.md`
- `docs/ssd/specification.md`
- `docs/ssd/implementation-plan.md`
- `docs/ssd/source-matrix.md`

Keep behavior, tests, and SSD docs aligned. Prefer small, reviewable changes.

## Local Setup

```bash
python scripts/bootstrap.py
source .venv/bin/activate
```

The bootstrap script creates `.venv`, installs the package in editable mode,
and writes a local operator env file outside the repo checkout.

## Validation

Run the standard checks before opening a PR:

```bash
make test
make lint
make validate
python scripts/generate_openapi_snapshot.py --check
```

## Contribution Expectations

- Add or update tests with every behavior change.
- Keep public CLI, HTTP, and MCP behavior aligned when changing shared
  contracts.
- Do not commit secrets, local env files, database files, or runtime artifacts.
- Update the SSD docs when a public contract or supported workflow changes.
- Prefer generic, community-safe examples over personal or provider-specific
  setup guidance.

## Pull Requests

Include:

- a short change summary
- validation evidence
- doc updates when public behavior changes
- sample output or screenshots for user-facing changes
