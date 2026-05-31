"""Hierarchical document summarization helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from neurocore.core.config import NeuroCoreConfig
from neurocore.ingest.chunking import chunk_text_with_offsets

SUMMARY_STRATEGY = "hierarchical_chunk_synthesis_v1"


class SupportsSummarize(Protocol):
    def summarize(self, text: str, max_sentences: int = 2) -> object:
        """Return an object with a summary attribute or summary-like string."""


@dataclass(frozen=True)
class ChunkSummary:
    ordinal: int
    summary: str


@dataclass(frozen=True)
class HierarchicalSummary:
    document_summary: str
    chunk_summaries: tuple[ChunkSummary, ...]
    strategy: str = SUMMARY_STRATEGY


def build_hierarchical_summary_from_content(
    content: str,
    *,
    config: NeuroCoreConfig,
    summarizer: SupportsSummarize,
    max_sentences: int = 2,
) -> HierarchicalSummary:
    chunk_values = chunk_text_with_offsets(
        content,
        target_tokens=config.target_chunk_tokens,
        max_tokens=config.max_chunk_tokens,
        overlap_tokens=config.chunk_overlap_tokens,
    )
    chunk_inputs = [
        (ordinal, chunk_value.text)
        for ordinal, chunk_value in enumerate(chunk_values, start=1)
    ]
    return build_hierarchical_summary_from_chunks(
        chunk_inputs,
        summarizer=summarizer,
        max_sentences=max_sentences,
    )


def build_hierarchical_summary_from_chunks(
    chunk_inputs: Sequence[tuple[int, str]],
    *,
    summarizer: SupportsSummarize,
    max_sentences: int = 2,
) -> HierarchicalSummary:
    chunk_summaries = tuple(
        ChunkSummary(
            ordinal=ordinal,
            summary=_summary_text(
                summarizer.summarize(text, max_sentences=max_sentences)
            ),
        )
        for ordinal, text in chunk_inputs
        if text.strip()
    )
    return HierarchicalSummary(
        document_summary=synthesize_document_summary(
            chunk_summaries,
            summarizer=summarizer,
            max_sentences=max_sentences,
        ),
        chunk_summaries=chunk_summaries,
    )


def synthesize_document_summary(
    chunk_summaries: Sequence[ChunkSummary],
    *,
    summarizer: SupportsSummarize,
    max_sentences: int = 2,
) -> str:
    if not chunk_summaries:
        return ""
    context = render_chunk_summary_context(chunk_summaries)
    return _summary_text(summarizer.summarize(context, max_sentences=max_sentences))


def render_chunk_summary_context(chunk_summaries: Sequence[ChunkSummary]) -> str:
    lines = ["Summarize the following chunk summaries into one document summary:"]
    for chunk_summary in chunk_summaries:
        lines.append(f"Chunk {chunk_summary.ordinal}: {chunk_summary.summary}")
    return "\n".join(lines)


def _summary_text(summary_result: object) -> str:
    text = getattr(summary_result, "summary", summary_result)
    return str(text).strip()
