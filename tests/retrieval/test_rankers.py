import pytest

from neurocore.core import semantic as semantic_runtime
from neurocore.retrieval.rankers import SentenceTransformersRanker


def test_sentence_transformers_ranker_raises_when_dependency_resolution_fails(
    monkeypatch,
):
    def raise_missing_dependency() -> object:
        raise RuntimeError(
            "sentence-transformers is required for the sentence-transformers ranker"
        )

    monkeypatch.setattr(
        semantic_runtime,
        "get_sentence_transformer_class",
        raise_missing_dependency,
    )

    with pytest.raises(RuntimeError, match="sentence-transformers"):
        SentenceTransformersRanker("sentence-transformers/all-MiniLM-L6-v2")
