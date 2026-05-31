"""Helpers for resolving optional semantic ranking dependencies."""

from __future__ import annotations


def get_sentence_transformer_class() -> type[object]:
    """Return the SentenceTransformer class or raise a deterministic error."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for the sentence-transformers ranker"
        ) from exc
    return SentenceTransformer


def sentence_transformers_status() -> tuple[str, str | None]:
    """Report whether the sentence-transformers backend is available."""
    try:
        get_sentence_transformer_class()
    except RuntimeError:
        return (
            "unavailable",
            "Semantic backend sentence-transformers is unavailable; artifacts were rebuilt in metadata-only mode.",
        )
    return "ready", None
