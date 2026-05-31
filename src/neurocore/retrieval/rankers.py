"""Semantic and metadata ranking helpers for NeuroCore retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from neurocore.core import semantic as semantic_runtime
from neurocore.storage.base import Candidate


class SemanticRanker(Protocol):
    def rank(self, query_text: str, candidates: list[Candidate]) -> dict[str, float]:
        """Return candidate-id keyed semantic scores."""


@dataclass
class FakeSemanticRanker:
    scores: dict[str, float]

    def rank(self, query_text: str, candidates: list[Candidate]) -> dict[str, float]:
        return {
            candidate.item.id: self.scores.get(candidate.item.id, 0.0)
            for candidate in candidates
        }


class SentenceTransformersRanker:
    def __init__(self, model_name: str) -> None:
        self._model = semantic_runtime.get_sentence_transformer_class()(model_name)

    def rank(self, query_text: str, candidates: list[Candidate]) -> dict[str, float]:
        if not candidates:
            return {}

        query_embedding = self._model.encode(query_text, normalize_embeddings=True)
        candidate_texts = [_candidate_text(candidate) for candidate in candidates]
        candidate_embeddings = self._model.encode(
            candidate_texts, normalize_embeddings=True
        )
        scores: dict[str, float] = {}
        for candidate, embedding in zip(candidates, candidate_embeddings, strict=False):
            scores[candidate.item.id] = float(query_embedding @ embedding)
        return scores


def _candidate_text(candidate: Candidate) -> str:
    if (
        getattr(candidate, "artifact", None) is not None
        and candidate.artifact.normalized_text
    ):
        return candidate.artifact.normalized_text
    item = candidate.item
    if hasattr(item, "content"):
        return getattr(item, "content")
    if hasattr(item, "chunk_text"):
        return getattr(item, "chunk_text")
    if hasattr(item, "raw_content") and getattr(item, "raw_content") is not None:
        return getattr(item, "raw_content")
    return ""
