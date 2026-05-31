# NeuroCore Community Source Matrix

This matrix maps the public SSD package onto the code and tests shipped in the
community repository.

| source | public requirement | implementation reference | status |
|---|---|---|---|
| `docs/ssd/architecture.md` | Capture, query, and admin remain the stable logical boundaries | `src/neurocore/interfaces/`, `tests/interfaces/` | `implemented` |
| `docs/ssd/specification.md` | Core v1 supports records, documents, chunks, retrieval artifacts, and isolation-aware queries | `src/neurocore/core/`, `src/neurocore/storage/`, `tests/retrieval/` | `implemented` |
| `docs/ssd/specification.md` | CLI, HTTP, and MCP adapters expose the same shared core contracts | `src/neurocore/adapters/`, `tests/interfaces/test_cli.py`, `tests/interfaces/test_http_api.py`, `tests/interfaces/test_mcp_server.py` | `implemented` |
| `docs/ssd/hierarchical-summarization.md` | Long-form summaries are chunk-first and persist on existing models | `src/neurocore/summarization/`, `tests/summarization/`, `tests/interfaces/test_capture.py` | `implemented` |
| `docs/ssd/implementation-plan.md` | The public repo should stay limited to community-safe code, docs, and tooling | `README.md`, `CONTRIBUTING.md`, `scripts/`, `.github/workflows/repo-gate.yml` | `implemented` |
