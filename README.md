# NeuroCore Community

NeuroCore Community is the public, contributor-friendly edition of NeuroCore: a
Python package for capturing, storing, querying, and governing policy-aware
notes and documents.

This repository is intentionally curated from a private development repo. It
ships a clean public history, the core package, tests, community-facing docs,
and a small set of maintenance scripts. It does not attempt to mirror every
private workflow, document, or integration package.

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

## Quickstart

```bash
python scripts/bootstrap.py
source .venv/bin/activate
pytest
python scripts/validate_checkout.py
```

If you prefer to run the CLI through the repo-local virtual environment wrapper:

```bash
python scripts/neurocore_checkout.py --help
```

## Core Commands

```bash
make test
make lint
make validate
python scripts/generate_openapi_snapshot.py --check
```

## Example Flow

See [examples/quickstart-cli.md](examples/quickstart-cli.md) for a minimal
capture-and-query walkthrough using the public CLI surface.

## Public Contract Docs

- [Architecture](docs/ssd/architecture.md)
- [Specification](docs/ssd/specification.md)
- [Implementation Plan](docs/ssd/implementation-plan.md)
- [Source Matrix](docs/ssd/source-matrix.md)

## Release Model

This repository is maintained as a public community edition. Public releases are
curated drops from a separate private development repository. Community changes
land here first, then are intentionally ported back when appropriate.

## License

NeuroCore Community is licensed under the [Apache License 2.0](LICENSE).
