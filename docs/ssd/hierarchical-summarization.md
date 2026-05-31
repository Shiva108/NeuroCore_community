# Hierarchical Summarization

## Purpose

NeuroCore stores long-form content as one `MemoryDocument` plus deterministic
`MemoryChunk` children. Hierarchical summarization keeps summary generation on
that same shape:

1. summarize each stored chunk
2. persist each chunk summary on the existing `MemoryChunk.summary` field
3. synthesize the document summary from the chunk summaries
4. persist the final result on `MemoryDocument.summary`

This avoids introducing a second summary store or a summary-only document type.

## Scope

This pattern currently applies to two flows:

- background summarization for unsummarized documents
- provider-backed `import-corpus` capture preparation

It does not change query, storage topology, or adapter contracts beyond
allowing document captures to carry optional precomputed `summary` and
`chunk_summaries`.

## Design Constraints

- keep the existing document and chunk ids stable
- reuse the checked-in chunking configuration
- preserve chunk ordering by `ordinal`
- keep summarization behind existing config-gated surfaces
- avoid storing parallel derived artifacts outside the current models

## Runtime Shape

### Background summaries

- load the existing chunk set for an unsummarized document
- summarize each chunk that does not already have a summary
- build a stable chunk-summary context block
- synthesize one document summary from that block
- update the document and chunks together

### Import-corpus

- before raw document capture, build the same chunk-first summary plan when the
  configured summarizer is available
- thread the synthesized document summary into the capture request as
  `summary`
- thread the per-chunk summaries into the same request as `chunk_summaries`
- record the strategy in raw document metadata

This keeps import-corpus on the canonical document/chunk path instead of
inventing a separate article-summary payload.

## Why This Direction

- long inputs degrade more predictably when summarized chunk-first
- chunk summaries are reusable for later audits, reviews, and regeneration
- document summaries remain explainable because they are derived from stored
  chunk-level summaries
- existing storage backends already support `summary` on both documents and
  chunks

## Explicit Non-Goals

- no new top-level summary tables
- no adapter-specific summary formats
- no guarantee that every import-corpus run will summarize; the configured
  summarizer still has to be available
- no article/book digest template in this pass
