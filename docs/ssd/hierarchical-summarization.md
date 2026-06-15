# Hierarchical Summarization

## Purpose

Long-form content is stored as one document plus chunk children. Hierarchical
summarization preserves that shape:

1. summarize each chunk
2. persist chunk summaries
3. synthesize the document summary from chunk summaries
4. persist the final document summary

## Scope

This pattern supports background summarization and corpus-preparation flows
without introducing a separate summary-only storage model.
