Purpose

This file gives concise, project-specific instructions to an AI coding agent
working in the NeuroCore Community repository.

**Overview**
- **Architecture:** The Python package lives under `src/neurocore/` and is
  organized into thin adapters, stable interfaces, runtime factories, and
  domain subpackages such as `ingest`, `retrieval`, `summarization`, `storage`,
  and `reporting`.
- **Data flow:** capture and ingest feed storage, retrieval, summarization,
  reporting, and the dashboard or CLI surfaces.

**Key developer workflows**
- Bootstrap with `python scripts/bootstrap.py`.
- Run checks with `make test`, `make lint`, and `make validate`.
- Refresh the OpenAPI snapshot with
  `python scripts/generate_openapi_snapshot.py --check`.
- Use `python scripts/mock_openai_compatible.py` for local consensus and
  reporting tests.

**Project-specific patterns**
- Keep adapters thin and delegate behavior to `neurocore.interfaces.*`.
- Route construction through `src/neurocore/runtime.py`.
- Avoid changing interface payload shapes casually without updating tests.
- Keep secrets and provider-specific credentials out of the repository.
