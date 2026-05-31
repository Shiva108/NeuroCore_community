from __future__ import annotations

import os

import pytest

from neurocore.core.config import NeuroCoreConfig
from neurocore.core.semantic import sentence_transformers_status
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.query import query_memory
from neurocore.storage.in_memory import InMemoryStore


@pytest.mark.skipif(
    os.getenv("NEUROCORE_RUN_SEMANTIC_E2E") != "1",
    reason="Set NEUROCORE_RUN_SEMANTIC_E2E=1 to run semantic retrieval quality checks.",
)
def test_sentence_transformers_prefers_semantic_similarity():
    status, issue = sentence_transformers_status()
    if status != "ready":
        pytest.skip(issue or "sentence-transformers not ready")

    config = NeuroCoreConfig(
        default_namespace="shared-tradecraft",
        allowed_buckets=("reports",),
        default_sensitivity="standard",
        semantic_backend="sentence-transformers",
        semantic_model_name="sentence-transformers/all-MiniLM-L6-v2",
    )
    store = InMemoryStore()

    first = capture_memory(
        {
            "namespace": "shared-tradecraft",
            "bucket": "reports",
            "sensitivity": "standard",
            "content": "IDOR across tenant invoice objects with cross-account impact proof.",
            "content_format": "markdown",
            "source_type": "report",
            "title": "IDOR reference",
        },
        store=store,
        config=config,
    )
    second = capture_memory(
        {
            "namespace": "shared-tradecraft",
            "bucket": "reports",
            "sensitivity": "standard",
            "content": "Static checklist for screenshot annotation and report formatting.",
            "content_format": "markdown",
            "source_type": "report",
            "title": "Formatting reference",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "cross tenant direct object reference proof",
            "namespace": "shared-tradecraft",
            "allowed_buckets": ["reports"],
            "sensitivity_ceiling": "standard",
            "top_k": 2,
        },
        store=store,
        config=config,
    )

    assert response["results"][0]["id"] == first["id"]
    assert response["results"][0]["id"] != second["id"]
