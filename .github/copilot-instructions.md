Purpose

This file gives concise, project-specific instructions to an AI coding agent (Copilot-style) to make productive edits in the NeuroCore Community repo.

**Overview**
- **Architecture:** The Python package lives under `src/neurocore/` and is organized into thin *adapters* (external surfaces), *interfaces* (business logic functions), *runtime* factories, and multiple domain subpackages (`ingest`, `retrieval`, `summarization`, `storage`, `reporting`). See `src/neurocore/runtime.py` for how components are assembled.
- **Data flow:** capture/ingest -> storage -> retrieval/rank -> summarization/reporting -> dashboard/CLI. Adapters call pure functions in `neurocore.interfaces.*`.

**Key developer workflows**
- **Bootstrapping:** `python scripts/bootstrap.py` then `source .venv/bin/activate` (the project expects Python 3.11+). See `pyproject.toml` for test/dev dependencies.
- **Run tests:** `pytest` (pyproject adds `src` to `PYTHONPATH`). `make test` is also available.
- **Lint / format / validation:** `make lint`, `make validate`, `make test` and `python scripts/generate_openapi_snapshot.py --check` are common checks used by CI.
- **Local OpenAI-compatible mock:** to test consensus/reporting flows run `python scripts/mock_openai_compatible.py` (defaults to `127.0.0.1:8787`) and point configured `base_url` for consensus to that address.
- **Dev server (HTTP):** the FastAPI app factory is `neurocore.adapters.http_api.create_app`. To run locally with auto-reload:
  - `python -m uvicorn "neurocore.adapters.http_api:create_app()" --reload`
- **MCP tools:** the MCP adapter factory is `neurocore.adapters.mcp_server.create_mcp_server()`; call from a small wrapper or test harness to expose server tools.

**Project-specific patterns & conventions**
- **Adapters are thin:** Changes that expose new surface behavior should live in `src/neurocore/adapters/*` and delegate to `neurocore.interfaces.*` functions.
- **Interfaces are stable business logic:** Modify `src/neurocore/interfaces/*` when changing core behavior. Tests exercise these directly; keep function signatures and payload shapes stable when possible.
- **Runtime assembly:** Use `src/neurocore/runtime.py` factories to construct stores, rankers, summarizers, and reporters. Prefer adding config flags over hard-coding options.
- **Storage backends:** Supported backends include `InMemoryStore`, `SQLiteStore`, `PostgresStore`, and mirrored variants. Storage selection is via config keys (see `neurocore.core.config`).
- **OpenAI-compatible clients:** Reporting and summarization use `OpenAICompatible*` clients—you can swap to the local mock for development.

**Concrete examples**
- To invoke the HTTP capture route (from tests or API clients): POST `/capture` with a JSON body like `{"text": "...", "brain_id": "<id>"}`.
- To run background summarization in dev: ensure `enable_background_summarization` is set in config and call the `run_background_summaries` interface or the `/summaries/run` HTTP endpoint.

**Files to consult first**
- `pyproject.toml` — dependencies, test config, and script entrypoints.
- `README.md` — quickstart and core commands.
- `src/neurocore/runtime.py` — how components are wired.
- `src/neurocore/adapters/http_api.py` and `src/neurocore/adapters/mcp_server.py` — external surfaces and HTTP/MCP routes.
- `scripts/mock_openai_compatible.py` — use for local consensus/report testing.
- `docs/ssd/*` — architecture and specification docs to understand design rationale.

**What to avoid changing casually**
- Don’t casually change JSON payload shapes for `interfaces` functions or HTTP endpoints without updating tests; many tests rely on exact request/response structures.
- Avoid adding heavy runtime secrets or provider-specific code — the repo intentionally keeps provider-agnostic plumbing and a local mock for development.

If anything here is unclear or you'd like examples expanded (e.g., exact sample request bodies, test-entrypoints, or a simple run script), tell me which sections to flesh out and I will iterate.
