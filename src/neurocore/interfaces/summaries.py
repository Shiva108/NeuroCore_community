"""Public summary-running interface for NeuroCore."""

from __future__ import annotations

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.runtime_support import (
    record_runtime_audit,
    runtime_action_name,
    runtime_source_surface,
    supervise_call,
)
from neurocore.runtime import build_summarizer
from neurocore.storage.base import BaseStore
from neurocore.summarization.background import BackgroundSummarizationRunner

BACKGROUND_SUMMARIES_TIMEOUT_SECONDS = 30.0


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
    source_surface = runtime_source_surface(request)
    action = runtime_action_name(request, default="run_background_summaries")
    supervised = supervise_call(
        lambda: runner.run(limit=limit),
        source_surface=source_surface,
        action=action,
        timeout_seconds=BACKGROUND_SUMMARIES_TIMEOUT_SECONDS,
    )
    actor = str(request.get("actor", "system"))
    if supervised.succeeded:
        response = dict(supervised.result or {})
        record_runtime_audit(
            store,
            actor=actor,
            operation="background_summaries_run",
            request=request,
            status=supervised.status,
            result=response,
            duration_ms=supervised.duration_ms,
            consecutive_failures=supervised.consecutive_failures,
        )
        return response
    response = {
        "processed": 0,
        "failed": 1,
        "warnings": [],
        "status": supervised.status,
        "error": supervised.error,
        "timed_out": supervised.timed_out,
        "duration_ms": supervised.duration_ms,
        "consecutive_failures": supervised.consecutive_failures,
    }
    record_runtime_audit(
        store,
        actor=actor,
        operation="background_summaries_run",
        request=request,
        status=supervised.status,
        result=response,
        error=supervised.error,
        duration_ms=supervised.duration_ms,
        consecutive_failures=supervised.consecutive_failures,
        timed_out=supervised.timed_out,
    )
    return response
