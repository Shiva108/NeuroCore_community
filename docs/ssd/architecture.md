# NeuroCore Architecture Overview

## Problem Statement

NeuroCore needs a stable memory-core foundation that supports capture, query,
governance, and reporting without coupling all behavior to one delivery surface.

## Current Implementation

- Python package under `src/neurocore/`
- shared interfaces used by CLI, HTTP, and MCP adapters
- storage backends for in-memory, SQLite, Postgres, and mirrored operation
- optional semantic ranking, summarization, reporting, dashboard, and scheduler
  surfaces

## Architectural Boundaries

- adapters expose external surfaces
- interfaces implement business logic
- runtime factories assemble stores and optional services
- storage backends preserve primary and sealed data separation

## Publication Constraint

The community repository must remain safe to publish: no local secrets, no
private operator state, and no internal-only runbooks.
