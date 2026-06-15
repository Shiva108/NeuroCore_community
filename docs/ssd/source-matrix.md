# NeuroCore Community Source Matrix

This matrix maps the public SSD package to code and tests in the repository.

| source | public requirement | implementation reference | status |
| --- | --- | --- | --- |
| `docs/ssd/architecture.md` | capture, query, and admin remain separate logical boundaries | `src/neurocore/interfaces/`, `tests/interfaces/` | `implemented` |
| `docs/ssd/specification.md` | core contracts stay shared across CLI, HTTP, and MCP | `src/neurocore/adapters/`, `tests/interfaces/` | `implemented` |
| `docs/ssd/implementation-plan.md` | the public repo stays limited to community-safe code and docs | `README.md`, `CONTRIBUTING.md`, `.github/workflows/repo-gate.yml` | `implemented` |
