"""Document chunking helpers for NeuroCore ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass

from neurocore.core.config import NeuroCoreConfig
from neurocore.ingest.normalize import count_tokens, normalize_content

SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkSlice:
    text: str
    start_offset: int
    end_offset: int


def classify_content_kind(content: str, config: NeuroCoreConfig) -> str:
    if count_tokens(content) <= config.max_atomic_tokens:
        return "record"
    return "document"


def chunk_text(
    content: str, target_tokens: int, max_tokens: int, overlap_tokens: int
) -> list[str]:
    return [
        chunk.text
        for chunk in chunk_text_with_offsets(
            content, target_tokens, max_tokens, overlap_tokens
        )
    ]


def chunk_text_with_offsets(
    content: str, target_tokens: int, max_tokens: int, overlap_tokens: int
) -> list[ChunkSlice]:
    normalized = normalize_content(content)
    if not normalized:
        return []

    sentences = [
        sentence.strip()
        for sentence in SENTENCE_PATTERN.split(normalized)
        if sentence.strip()
    ]
    if not sentences:
        return [normalized]

    chunks: list[str] = []
    current_words: list[str] = []

    for sentence in sentences:
        sentence_words = sentence.split()
        projected_size = len(current_words) + len(sentence_words)

        if current_words and projected_size > target_tokens:
            chunks.append(" ".join(current_words))
            overlap = current_words[-overlap_tokens:] if overlap_tokens else []
            current_words = overlap.copy()

        if current_words and len(current_words) + len(sentence_words) > max_tokens:
            chunks.append(" ".join(current_words))
            overlap = current_words[-overlap_tokens:] if overlap_tokens else []
            current_words = overlap.copy()

        if len(sentence_words) > max_tokens:
            for slice_start in range(0, len(sentence_words), max_tokens):
                window = sentence_words[slice_start : slice_start + max_tokens]
                if current_words:
                    chunks.append(" ".join(current_words))
                    overlap = current_words[-overlap_tokens:] if overlap_tokens else []
                    current_words = overlap.copy()
                chunks.append(" ".join(window))
            current_words = []
            continue

        current_words.extend(sentence_words)

    if current_words:
        chunks.append(" ".join(current_words))

    results: list[ChunkSlice] = []
    search_start = 0
    for chunk in chunks:
        start_offset = normalized.find(chunk, search_start)
        if start_offset == -1:
            start_offset = normalized.find(chunk)
        end_offset = start_offset + len(chunk)
        results.append(
            ChunkSlice(text=chunk, start_offset=start_offset, end_offset=end_offset)
        )
        search_start = max(start_offset + 1, end_offset - overlap_tokens)

    return results
