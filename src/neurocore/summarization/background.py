"""Background summarization runner primitives for NeuroCore."""

from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass
from typing import Protocol

from neurocore.core.config import NeuroCoreConfig
from neurocore.storage.base import BaseStore
from neurocore.summarization.consensus import ConsensusSummary
from neurocore.summarization.hierarchical import (
    ChunkSummary,
    synthesize_document_summary,
)


class Summarizer(Protocol):
    """Protocol for summary engines used by the background runner."""

    def summarize(self, text: str, max_sentences: int = 2) -> ConsensusSummary:
        """Summarize text into a consensus summary."""


@dataclass
class BackgroundSummarizationRunner:
    """Iterate over eligible documents and write back summaries."""

    store: BaseStore
    config: NeuroCoreConfig
    summarizer: Summarizer

    def run(self, limit: int = 10) -> dict[str, object]:
        """Summarize up to ``limit`` unsummarized documents."""
        if not self.config.enable_background_summarization:
            raise PermissionError("Background summarization is disabled")

        processed = 0
        failed = 0
        warnings: list[str] = []
        for document in self.store.list_documents(include_archived=False):
            if processed >= limit:
                break
            if document.sensitivity == "sealed":
                continue
            if document.summary or not document.raw_content:
                continue
            try:
                self._summarize_document(document.id)
                processed += 1
            except Exception as exc:
                failed += 1
                warnings.append(f"{document.id}: {exc}")

        return {"processed": processed, "failed": failed, "warnings": warnings}

    def _summarize_document(self, document_id: str) -> None:
        document = self.store.get_document(document_id, include_archived=True)
        if document is None or not document.raw_content:
            return
        chunks = [
            chunk
            for chunk_id in self.store.get_document_chunk_ids(document.id)
            if (chunk := self.store.get_chunk(chunk_id)) is not None
        ]
        if not chunks:
            consensus = self.summarizer.summarize(document.raw_content)
            self.store.update_document(
                document.id,
                patch={"summary": consensus.summary},
                mode="in_place",
            )
            return
        chunk_summaries: list[ChunkSummary] = []
        updated_chunks = []
        for chunk in chunks:
            summary_text = (
                chunk.summary or self.summarizer.summarize(chunk.chunk_text).summary
            )
            chunk_summaries.append(
                ChunkSummary(ordinal=chunk.ordinal, summary=str(summary_text).strip())
            )
            updated_chunks.append(replace(chunk, summary=str(summary_text).strip()))
        document_summary = synthesize_document_summary(
            chunk_summaries,
            summarizer=self.summarizer,
        )
        self.store.update_document(
            document.id,
            patch={"summary": document_summary, "chunks": updated_chunks},
            mode="in_place",
        )
