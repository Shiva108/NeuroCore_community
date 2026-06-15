from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.briefing import generate_briefing
from neurocore.interfaces.capture import capture_memory
from neurocore.storage.in_memory import InMemoryStore


def build_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research", "ops", "findings", "payloads"),
        default_sensitivity="standard",
    )


def test_generate_briefing_uses_query_request_and_brain_id_alias():
    store = InMemoryStore()
    config = build_config()
    capture_memory(
        {
            "namespace": "brain-alpha",
            "bucket": "findings",
            "sensitivity": "standard",
            "content": "Validated SSRF finding with reproduction steps.",
            "content_format": "markdown",
            "source_type": "note",
            "title": "FIND-001 SSRF",
            "tags": ["validated-finding"],
        },
        store=store,
        config=config,
    )
    capture_memory(
        {
            "namespace": "brain-alpha",
            "bucket": "ops",
            "sensitivity": "standard",
            "content": "Operator retrospective: prefers concise payload diffs and clear next actions.",
            "content_format": "markdown",
            "source_type": "note",
            "title": "Operator Retrospective",
            "tags": ["operator-retrospective", "artifact:operator-retrospective"],
        },
        store=store,
        config=config,
    )

    response = generate_briefing(
        {
            "brain_id": "brain-alpha",
            "query_request": {
                "query_text": "SSRF reproduction",
                "allowed_buckets": ["findings", "ops"],
                "sensitivity_ceiling": "standard",
            },
            "include_operator_hints": True,
            "max_items": 5,
        },
        store=store,
        config=config,
    )

    briefing = response["briefing"]
    assert response["metadata"]["context_source"] == "query_request"
    assert response["metadata"]["brain_id"] == "brain-alpha"
    assert "## Overview" in briefing
    assert "## Relevant Memory" in briefing
    assert "## Prior Decisions / Payloads" in briefing
    assert "## Operator Hints" in briefing
    assert "## Known gaps / stale context" in briefing
    assert "## Next Actions" in briefing
    assert "Validated SSRF finding" in briefing
    assert "prefers concise payload diffs" in briefing
    assert "(provenance: source=note; title=FIND-001 SSRF; id=" in briefing
    assert "Semantic ranker unavailable; using metadata-only fallback." in briefing


def test_generate_briefing_uses_explicit_context_markdown():
    response = generate_briefing(
        {
            "context_markdown": "Recovered operator notes about token leakage.",
            "sections": ["Overview", "Relevant Memory", "Next Actions"],
        },
        store=InMemoryStore(),
        config=build_config(),
    )

    briefing = response["briefing"]
    assert response["metadata"]["context_source"] == "context_markdown"
    assert "Recovered operator notes about token leakage." in briefing
    assert "## Overview" in briefing
    assert "## Relevant Memory" in briefing
    assert "## Next Actions" in briefing
    assert "## Operator Hints" not in briefing
    assert "## Known gaps / stale context" not in briefing


def test_generate_briefing_shows_known_gaps_for_query_warnings():
    response = generate_briefing(
        {
            "query_response": {
                "query_id": "query-1",
                "results": [
                    {
                        "id": "rec-1",
                        "bucket": "research",
                        "content_preview": "Fallback-only retrieval preview.",
                        "metadata": {
                            "source_type": "note",
                            "title": "Fallback note",
                            "tags": [],
                        },
                    }
                ],
                "warnings": [
                    "Semantic ranker unavailable; using metadata-only fallback."
                ],
            }
        },
        store=InMemoryStore(),
        config=build_config(),
    )

    briefing = response["briefing"]
    assert "## Known gaps / stale context" in briefing
    assert "Semantic ranker unavailable; using metadata-only fallback." in briefing
    assert "fewer than 2 matching items were available" in briefing


def test_generate_briefing_shows_low_result_gap_without_warnings():
    response = generate_briefing(
        {
            "query_response": {
                "query_id": "query-2",
                "results": [
                    {
                        "id": "rec-2",
                        "bucket": "research",
                        "content_preview": "Single result preview.",
                        "metadata": {
                            "source_type": "note",
                            "title": "Single result",
                            "tags": [],
                        },
                    }
                ],
                "warnings": [],
            }
        },
        store=InMemoryStore(),
        config=build_config(),
    )

    assert "## Known gaps / stale context" in response["briefing"]
    assert "fewer than 2 matching items were available" in response["briefing"]


def test_generate_briefing_skips_known_gaps_for_healthy_query_response():
    response = generate_briefing(
        {
            "query_response": {
                "query_id": "query-healthy",
                "results": [
                    {
                        "id": "rec-a",
                        "bucket": "research",
                        "content_preview": "First preview.",
                        "metadata": {
                            "source_type": "note",
                            "title": "First note",
                            "tags": [],
                        },
                    },
                    {
                        "id": "rec-b",
                        "bucket": "findings",
                        "content_preview": "Second preview.",
                        "metadata": {
                            "source_type": "note",
                            "title": "Second note",
                            "tags": [],
                        },
                    },
                ],
                "warnings": [],
            }
        },
        store=InMemoryStore(),
        config=build_config(),
    )

    assert "## Known gaps / stale context" not in response["briefing"]
