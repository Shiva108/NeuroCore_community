"""Query interface for retrieving NeuroCore records and chunks."""

from __future__ import annotations

from neurocore.core.brains import apply_brain_namespace
from neurocore.core.config import NeuroCoreConfig
from neurocore.retrieval.query import QueryEngine
from neurocore.retrieval.rankers import SemanticRanker
from neurocore.storage.base import BaseStore


def query_memory(
    request: dict[str, object],
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: SemanticRanker | None = None,
) -> dict[str, object]:
    resolved_request = apply_brain_namespace(
        request, store=store, default_namespace=config.default_namespace
    )
    engine = QueryEngine(store=store, semantic_ranker=semantic_ranker)
    payload = engine.execute(resolved_request, config)
    if resolved_request.get("brain_id") is not None:
        payload["brain_id"] = resolved_request["brain_id"]
    return payload
