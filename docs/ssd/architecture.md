# NeuroCore Architecture Overview

## Problem statement

NeuroCore needs an implementation-ready foundation for a general AI memory/core
layer before any product-specific workflows are allowed to dominate the design.
The architecture must keep capture, query, and admin concerns separate while
still working as one cohesive codebase across hosted and local runtime paths.

## Current implementation

- one Python package under `src/neurocore/`
- `pytest`-driven validation under `tests/`
- configurable storage abstraction with in-memory, SQLite, and Postgres
  backends
- routed sealed storage using separate primary and isolated stores
- persisted retrieval artifacts for records and chunks
- library, CLI, FastAPI, and MCP adapters that share the same core interfaces
- first-class brain manifests, `brain_id` routing, and session-memory
  interfaces layered on top of the same capture/query contracts
- reusable protocol registry plus a workflow-oriented reference app
- Slack/Discord ingestion, background summarization, dashboard,
  production-backend support, and Slack article gating/distillation as
  config-gated extensions
- GitHub repo gate plus metadata schema validation and source traceability

The architecture remains contract-first, but it now documents the concrete
reference implementation in this repository.

## Goals

- Define a canonical memory model that supports both atomic notes and long-form
  source material.
- Make retrieval hybrid by design: metadata filtering first, semantic recall
  second.
- Establish explicit isolation boundaries for project, work, personal, and
  sensitive context.
- Preserve clear logical boundaries between capture, query, and admin
  operations.
- Keep storage and ranking integrations replaceable behind interfaces.
- Treat documentation, governance, and provenance tracking as first-class
  architecture, not repo afterthoughts.
- Preserve a clear path for future adapters without changing the domain
  contracts.

## Non-goals

- Making CTI-specific workflows or prompt packs part of the base contract.
- Requiring multi-model orchestration for core memory operations.
- Requiring the dashboard, background summaries, or production backend for local
  core usage.
- Splitting the system into multiple deployable services on day one.
- Copying upstream repository structure, prompts, or vendor choices verbatim
  from related projects.

## Architecture summary

NeuroCore runs as one codebase with three stable logical service boundaries:

1. `capture`
2. `query`
3. `admin`

These boundaries currently share one runtime, but their contracts stay separate
so library, CLI, HTTP, and MCP surfaces can expose only the minimum
capabilities needed for a given workflow.

Supported extensions sit on top of those core contracts:

- `brains`: create, list, update, archive, and resolve product-layer brain
  manifests onto namespace isolation
- `ingest`: normalize external Slack/Discord events into capture requests,
  including prefix-triggered Slack article intake
- `sessions`: capture high-signal AI/client session events, checkpoints, and
  resumable session handoffs
- `summaries`: summarize stored long-form documents, optionally with
  multi-model consensus
- `briefing`: synthesize compact markdown handoffs from durable memory
- `reporting`: generate consensus reports when a live provider is available and
  degrade to synthesized briefings when it is not
- `protocols`: expose named reusable OpenBrain workflows through the same
  adapter surfaces
- `dashboard`: present a reference-app view over brains, capture/query/review,
  session resume, protocols, audit, and report readiness
- `production backend`: switch the routed store to cloud-hosted
  Postgres-backed stores when remote availability is actually needed, with
  local SQLite retained as the default operator profile

Core flow:

1. Ingest raw content through a `capture` interface.
2. Normalize and validate the payload.
3. Assign isolation metadata: namespace, bucket, tags, and sensitivity.
4. Compute a fingerprint for deduplication.
5. Store the content as either:
   - one canonical memory record for atomic content, or
   - one parent document plus retrievable chunks for long-form content
6. For accepted Slack article submissions, store:
   - one raw source document, and
   - one distilled reusable knowledge record
7. Persist retrieval artifacts for records or chunks.
8. Resolve queries through a policy-aware query path that enforces isolation
   before ranking results.
9. Rebuild artifacts through the admin reindex path when backend settings or
   derived retrieval state changes.

## Major components and responsibilities

### 1. Core domain model

Responsible for the canonical entities and validation rules used across the
system.

Primary entities:

- `MemoryRecord`
- `MemoryDocument`
- `MemoryChunk`
- `RetrievalArtifact`
- `QueryContext`
- `BrainManifest`

### 2. Capture pipeline

Responsible for accepting new content and converting it into NeuroCore-native
structures.

Responsibilities:

- input validation
- content normalization
- namespace defaulting or validation
- bucket and sensitivity validation
- deduplication
- chunking decision
- article-specific quality gating and deterministic distillation when the Slack
  article flow is used
- persistence handoff
- retrieval artifact creation

### 3. Chunking and enrichment

Responsible for making long-form material retrievable without collapsing too
many ideas into one record.

Responsibilities:

- detect when content is atomic versus document-like
- split long-form content into coherent chunks
- retain parent-child links
- preserve source metadata and ordering
- preserve stable offsets for deterministic chunk references
- enrich captures with deterministic structured metadata such as URLs, CVEs,
  CWEs, ATT&CK IDs, severity markers, and suggested actions
- allow optional provider-backed action-item generation only when the live
  reporting/summarization provider is available

### 4. Storage and indexing abstraction

Responsible for persisting records and exposing retrieval primitives without
binding the rest of the system to one backend.

Required capabilities:

- create
- update
- soft delete or tombstone
- list by filters
- fetch by id
- persist retrieval artifacts
- metadata filtering
- reindex or refresh hooks

Current backends:

- `InMemoryStore` for fast local tests
- `SQLiteStore` for the current default operator runtime
- `PostgresStore` for deferred hosted deployments across supported Postgres providers such as Neon and Supabase
- `MirroredStore` for deferred local-first reads with cloud-primary mirrored writes
- `RoutedStore` for sealed-store routing between primary and isolated backends

### 5. Query engine

Responsible for turning a query plus retrieval scope into a bounded result set.

Responsibilities:

- apply namespace and sensitivity policies first
- resolve `brain_id` into a namespace before query execution when callers use
  the product-layer routing contract
- apply deterministic metadata filters second
- run semantic recall over the allowed subset when available
- degrade to deterministic metadata-only retrieval with an explicit warning if
  no semantic backend is configured
- aggregate chunk hits back into document-aware responses when needed
- return explainable ranking metadata

Current implementation note:

- artifact metadata is the retrieval substrate
- semantic ranking is optional and currently pluggable through a ranker
  interface
- `sentence-transformers` is the supported local semantic backend when
  installed

### 6. Isolation policy engine

Responsible for context separation.

It enforces:

- hard boundary: `namespace`
- medium boundary: `sensitivity`
- soft boundary: `bucket`
- optional recall aids: `tags`

`brain_id` is a lifecycle and UX alias, not a separate isolation boundary.
NeuroCore must resolve it onto exactly one namespace before reading or writing
memory.

Sealed content routes to a physically separate store and is excluded from the
default query path.

### 7. Interface adapters and extension surfaces

Responsible for exposing NeuroCore capabilities to external callers without
leaking internal storage details.

Core adapters:

- local library interface
- CLI
- HTTP API via FastAPI
- MCP adapter

Supported extension surfaces:

- brain management and routing helpers
- session-memory capture and resume
- protocol discovery and execution
- Slack and Discord ingest helpers
- Slack article retrieval, normalization, gating, audit, and dual-artifact
  persistence
- connector-oriented interface metadata for future external packages
- background summarization runner
- dashboard data builder and HTML reference app
- connector discovery and readiness telemetry when metadata is present

All adapters delegate to the same capture, query, admin, or extension modules.

Cross-surface readiness contract:

- dashboards, HTTP/CLI/MCP responses, and connector health surfaces include the
  same reporting readiness payload shape so clients can distinguish configured,
  bootstrapped, and healthy states without adapter-specific interpretation.

### 8. Documentation, governance, and provenance layer

Responsible for repo practices that keep implementation and onboarding aligned.

Current artifacts:

- `docs/ssd/architecture.md`
- `docs/ssd/specification.md`
- `docs/ssd/implementation-plan.md`
- `docs/ssd/source-matrix.md`
- `.claude/commands/*.md`
- `.github/workflows/repo-gate.yml`
- `.github/module-metadata.schema.json`
- `python scripts/validate_checkout.py`

Responsibilities:

- required-doc presence checks
- metadata file discovery
- JSON Schema validation for module metadata
- secret-like content detection
- source coverage and follow-up tracking

## Canonical memory model

NeuroCore uses two canonical storage shapes plus one derived retrieval shape:

### Atomic memory

Use when the content expresses one retrievable idea and fits within the
configured single-item threshold.

Examples:

- a preference
- a decision
- a short note
- a structured event

### Document memory

Use when the content contains multiple retrievable ideas, sections, or extended
narrative.

Examples:

- transcripts
- articles
- meeting notes
- reports
- manuals

Document memory is stored as:

- one `MemoryDocument` parent for source-level metadata and lineage
- one or more `MemoryChunk` children as the primary retrieval units

### Retrieval artifacts

Use as the persisted retrieval-facing representation.

Rules:

- records produce one artifact each
- chunks produce one artifact each
- document parents do not require a standalone artifact in v1
- reindex rebuilds artifacts without changing canonical ids

## Recommended isolation model

NeuroCore should not rely on tags alone for isolation.

Recommended default:

- `namespace` is required at runtime but may default from config when callers do
  not provide it explicitly
- `bucket` is required and represents the intended context within a namespace
- `tags` are optional retrieval hints, not security controls
- `sensitivity` is required for every stored item

Recommended sensitivity handling:

- `standard`: default store and default query path
- `restricted`: default store, but queryable only under an adequate ceiling
- `sealed`: routed to a separate store and excluded from the default query path

## Tool-surface governance

NeuroCore preserves three logical tool groups even though they share one code
package:

- `capture`: create and ingest only
- `query`: search, fetch, and assemble only
- `admin`: update, delete, reindex, audit, and repair only

Rationale:

- capture workflows need minimal write-focused tools
- query workflows need read-focused tools with strong routing clarity
- admin workflows are infrequent and should stay out of normal agent context

Implementation guidance:

- future adapters should continue to expose these groups separately
- admin capabilities should remain disabled by default in end-user contexts
- extension surfaces should stay clearly config-gated so they do not blur the
  core contract boundaries

## Core request flow

### Capture flow

1. Caller submits capture payload.
2. Capture layer validates required fields and defaults `namespace` if omitted.
3. Content is normalized and fingerprinted.
4. Isolation metadata is assigned or validated.
5. System decides atomic versus document path.
6. Long-form content is chunked and linked to a parent document.
7. Storage persists canonical records plus retrieval artifacts.
8. Caller receives a stable id and storage summary.

### Query flow

1. Caller submits query text and retrieval scope.
2. Policy engine enforces namespace and sensitivity eligibility.
3. Query layer applies metadata filters.
4. Retrieval layer ranks records or chunks from the eligible artifact set.
5. Results are aggregated into chunk-level or document-level responses.
6. Caller receives explainable matches plus enough metadata to trace why they
   were returned.

### Admin flow

1. Caller requests update, delete, reindex, or audit.
2. Admin policy verifies elevated access.
3. Operation executes against canonical records and retrieval artifacts.
4. Audit trail is emitted for later inspection.

### Extension flows

- ingest flow: adapter-specific payloads become canonical capture requests
- Slack article ingest flow:
  1. opt-in Slack message prefix or `/distill` slash command selects article mode
  2. URL retrieval and HTML normalization produce one canonical article body
  3. explicit gates decide accepted, rejected, or idempotent duplicate reuse
  4. accepted articles persist as one raw document plus one reusable knowledge
     record
  5. audit events record submission, gate outcome, persistence state, and sync
     follow-up details
- summary flow: unsummarized documents are processed through the configured
  summarizer, with long-form documents summarized chunk-first and synthesized
  back into one document summary
- dashboard flow: repo-local stats and recent document state are surfaced only
  when enabled
- production-backend flow: runtime configuration switches routed storage from
  local backends to Postgres-backed stores

## Dependencies and integration points

NeuroCore defines pluggable integration points rather than hard vendor lock-in:

- storage provider
- semantic ranker
- summarization backend
- transport adapters
- governance validation

Current reference stack:

- Python 3
- SQLite for persistent local storage
- Postgres for configured production-style storage across supported providers
- `pytest` for automated validation
- FastAPI for the HTTP adapter
- Python MCP SDK for the MCP adapter
- `jsonschema` for metadata validation
