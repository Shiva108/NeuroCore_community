from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.summaries import run_background_summaries
from neurocore.storage.in_memory import InMemoryStore
from neurocore.summarization.background import BackgroundSummarizationRunner
from neurocore.summarization.consensus import ConsensusSummary
from neurocore.summarization.consensus import ConsensusSummarizer


def build_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        max_atomic_tokens=6,
        target_chunk_tokens=6,
        max_chunk_tokens=8,
        chunk_overlap_tokens=2,
        enable_background_summarization=True,
    )


def test_background_runner_summarizes_unsummarized_documents():
    store = InMemoryStore()
    config = build_config()
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": (
                "Sentence one explains the system. "
                "Sentence two adds retrieval detail. "
                "Sentence three covers isolation policy."
            ),
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    runner = BackgroundSummarizationRunner(
        store=store,
        config=config,
        summarizer=ConsensusSummarizer(),
    )
    result = runner.run(limit=10)
    document = store.get_document(capture["id"], include_archived=True)

    assert result["processed"] == 1
    assert result["failed"] == 0
    assert document is not None
    assert document.summary
    assert "Sentence" in document.summary


def test_background_runner_skips_sealed_documents():
    store = InMemoryStore()
    config = build_config()
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "sealed",
            "content": (
                "Sentence one explains the system. "
                "Sentence two adds retrieval detail. "
                "Sentence three covers isolation policy."
            ),
            "content_format": "markdown",
            "source_type": "note",
            "force_kind": "document",
        },
        store=store,
        config=config,
    )

    runner = BackgroundSummarizationRunner(
        store=store,
        config=config,
        summarizer=ConsensusSummarizer(),
    )
    result = runner.run(limit=10)
    document = store.get_document(capture["id"], include_archived=True)

    assert result["processed"] == 0
    assert result["failed"] == 0
    assert document is not None
    assert document.summary is None


class RecordingSummarizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def summarize(self, text: str, max_sentences: int = 2) -> ConsensusSummary:
        self.calls.append(text)
        return ConsensusSummary(
            summary=f"summary-{len(self.calls)}",
            strategy_outputs={"recording": f"summary-{len(self.calls)}"},
            agreement_score=1.0,
        )


def test_background_runner_summarizes_chunks_before_document():
    store = InMemoryStore()
    config = build_config()
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": (
                "Sentence one explains the system. "
                "Sentence two adds retrieval detail. "
                "Sentence three covers isolation policy."
            ),
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    summarizer = RecordingSummarizer()
    runner = BackgroundSummarizationRunner(
        store=store,
        config=config,
        summarizer=summarizer,
    )

    result = runner.run(limit=10)
    document = store.get_document(capture["id"], include_archived=True)
    chunk_ids = store.get_document_chunk_ids(capture["id"])
    chunks = [store.get_chunk(chunk_id) for chunk_id in chunk_ids]

    assert result["processed"] == 1
    assert document is not None
    assert document.summary == f"summary-{len(chunk_ids) + 1}"
    assert [chunk.summary for chunk in chunks if chunk is not None] == [
        f"summary-{ordinal}" for ordinal in range(1, len(chunk_ids) + 1)
    ]
    assert "summary-1" in summarizer.calls[-1]
    assert f"summary-{len(chunk_ids)}" in summarizer.calls[-1]


def test_run_background_summaries_returns_structured_failure_and_audits(monkeypatch):
    store = InMemoryStore()
    config = build_config()

    class FailingRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, limit: int = 10):
            raise RuntimeError(f"summary failed for limit={limit}")

    monkeypatch.setattr(
        "neurocore.interfaces.summaries.BackgroundSummarizationRunner",
        FailingRunner,
    )

    result = run_background_summaries(
        {"limit": 2},
        store=store,
        config=config,
    )

    assert result["processed"] == 0
    assert result["failed"] == 1
    assert result["status"] == "failed"
    assert result["error"] == "summary failed for limit=2"
    assert store.audit_events[-1]["operation"] == "background_summaries_run"
    assert store.audit_events[-1]["details"]["status"] == "failed"
