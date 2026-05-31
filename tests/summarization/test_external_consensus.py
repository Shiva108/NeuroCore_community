from neurocore.summarization.consensus import MultiModelConsensusSummarizer
import pytest


class FakeExternalSummaryClient:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def summarize(self, *, model_name: str, text: str, max_sentences: int = 2) -> str:
        self.calls.append(model_name)
        return self.responses[model_name]


def test_multi_model_consensus_uses_multiple_external_models():
    client = FakeExternalSummaryClient(
        {
            "model-a": "The system stores records and chunks for retrieval.",
            "model-b": "The system stores records and chunks for retrieval.",
            "model-c": "The system separates sealed data and supports retrieval.",
        }
    )
    summarizer = MultiModelConsensusSummarizer(
        model_client=client,
        model_names=("model-a", "model-b", "model-c"),
    )

    result = summarizer.summarize(
        "Sentence one explains the system. Sentence two covers retrieval."
    )

    assert client.calls == ["model-a", "model-b", "model-c"]
    assert result.summary == "The system stores records and chunks for retrieval."
    assert set(result.strategy_outputs) == {"model-a", "model-b", "model-c"}
    assert result.agreement_score >= 0.66


def test_multi_model_consensus_rejects_duplicate_model_names():
    summarizer = MultiModelConsensusSummarizer(
        model_client=FakeExternalSummaryClient({"model-a": "same"}),
        model_names=("model-a", "model-a"),
    )

    with pytest.raises(ValueError, match="unique model names"):
        summarizer.summarize("text")
