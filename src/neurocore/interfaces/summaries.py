"""Public summary-running interface for NeuroCore."""

from __future__ import annotations

from neurocore.core.config import NeuroCoreConfig
from neurocore.runtime import build_summarizer
from neurocore.storage.base import BaseStore
from neurocore.summarization.background import BackgroundSummarizationRunner


def run_background_summaries(
    request: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    """Run the background summarization worker with request-level overrides."""
    runner = BackgroundSummarizationRunner(
        store=store,
        config=config,
        summarizer=build_summarizer(config),
    )
    limit = int(request.get("limit", 10))
    return runner.run(limit=limit)
