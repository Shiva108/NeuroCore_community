from datetime import datetime

import pytest

import neurocore.interfaces.capture as capture_module

from neurocore.core.config import NeuroCoreConfig
from neurocore.ingest.chunking import chunk_text_with_offsets
from neurocore.interfaces.capture import capture_many, capture_memory
from neurocore.storage.in_memory import InMemoryStore
from neurocore.storage.router import RoutedStore


def build_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research", "planning", "ops"),
        default_sensitivity="standard",
        max_atomic_tokens=6,
        target_chunk_tokens=6,
        max_chunk_tokens=8,
        chunk_overlap_tokens=2,
    )


def test_capture_memory_returns_record_contract_for_short_content():
    store = InMemoryStore()
    config = build_config()

    response = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "short stable note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    assert response["kind"] == "record"
    assert response["stored"] is True
    assert response["storage_outcome"] == "record-stored"
    assert response["deduplicated"] is False
    assert response["chunk_count"] == 0


def test_capture_memory_returns_document_contract_and_deduplicates_repeated_capture():
    store = InMemoryStore()
    config = build_config()
    request = {
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
        "title": "Design note",
    }

    first = capture_memory(request, store=store, config=config)
    chunk_total = len(store.chunks)
    second = capture_memory(request, store=store, config=config)

    assert first["kind"] == "document"
    assert first["storage_outcome"] == "document-stored"
    assert first["chunk_count"] > 0
    assert second["deduplicated"] is True
    assert second["storage_outcome"] == "document-stored"
    assert len(store.chunks) == chunk_total


def test_capture_memory_honors_force_kind_for_document_capture():
    store = InMemoryStore()
    config = build_config()

    response = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "short stable note",
            "content_format": "markdown",
            "source_type": "note",
            "force_kind": "document",
            "title": "Forced document",
        },
        store=store,
        config=config,
    )

    assert response["kind"] == "document"
    assert response["storage_outcome"] == "document-stored"
    assert response["chunk_count"] > 0


def test_capture_memory_rejects_oversized_forced_record():
    store = InMemoryStore()
    config = build_config()

    with pytest.raises(ValueError, match="force_kind=record exceeds max_atomic_tokens"):
        capture_memory(
            {
                "namespace": "project-alpha",
                "bucket": "research",
                "sensitivity": "standard",
                "content": "one two three four five six seven",
                "content_format": "markdown",
                "source_type": "note",
                "force_kind": "record",
            },
            store=store,
            config=config,
        )


def test_capture_module_does_not_expose_admin_operations():
    assert not hasattr(capture_module, "delete_memory")
    assert not hasattr(capture_module, "update_memory")


def test_capture_memory_honors_caller_supplied_created_at():
    store = InMemoryStore()
    config = build_config()
    created_at = "2026-01-05T10:30:00+00:00"

    response = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "created at note",
            "content_format": "markdown",
            "source_type": "note",
            "created_at": created_at,
        },
        store=store,
        config=config,
    )

    stored = store.get_record(response["id"]) or store.get_document(response["id"])

    assert stored is not None
    assert stored.created_at == datetime.fromisoformat(created_at)


def test_capture_memory_defaults_namespace_from_config_when_omitted():
    store = InMemoryStore()
    config = build_config()

    response = capture_memory(
        {
            "bucket": "research",
            "sensitivity": "standard",
            "content": "namespace fallback note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    stored = store.get_record(response["id"]) or store.get_document(response["id"])

    assert response["namespace"] == "project-alpha"
    assert stored is not None
    assert stored.namespace == "project-alpha"


def test_capture_memory_rejects_missing_bucket():
    store = InMemoryStore()
    config = build_config()

    with pytest.raises(ValueError, match="bucket"):
        capture_memory(
            {
                "namespace": "project-alpha",
                "sensitivity": "standard",
                "content": "missing bucket note",
                "content_format": "markdown",
                "source_type": "note",
            },
            store=store,
            config=config,
        )


def test_capture_memory_rejects_oversized_content():
    store = InMemoryStore()
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        max_content_tokens=3,
    )

    with pytest.raises(ValueError, match="maximum content size"):
        capture_memory(
            {
                "namespace": "project-alpha",
                "bucket": "research",
                "sensitivity": "standard",
                "content": "one two three four",
                "content_format": "markdown",
                "source_type": "note",
            },
            store=store,
            config=config,
        )


def test_capture_memory_merges_safe_metadata_when_deduplicating():
    store = InMemoryStore()
    config = build_config()
    request = {
        "namespace": "project-alpha",
        "bucket": "research",
        "sensitivity": "standard",
        "content": "stable dedup note",
        "content_format": "markdown",
        "source_type": "note",
        "metadata": {"owner": "alice"},
        "tags": ["alpha"],
    }

    first = capture_memory(request, store=store, config=config)
    second = capture_memory(
        {
            **request,
            "metadata": {"reviewer": "bob"},
            "tags": ["beta"],
        },
        store=store,
        config=config,
    )

    stored = store.get_record(first["id"])

    assert second["deduplicated"] is True
    assert stored is not None
    assert stored.metadata == {"owner": "alice", "reviewer": "bob"}
    assert stored.tags == ("alpha", "beta")


def test_capture_memory_records_chunk_offsets_for_documents():
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

    chunk_ids = store.get_document_chunk_ids(capture["id"])
    first_chunk = store.get_chunk(chunk_ids[0])

    assert first_chunk is not None
    assert first_chunk.start_offset == 0
    assert first_chunk.end_offset is not None
    assert first_chunk.end_offset > first_chunk.start_offset


def test_capture_memory_persists_precomputed_document_and_chunk_summaries():
    store = InMemoryStore()
    config = build_config()
    content = (
        "Sentence one explains the system. "
        "Sentence two adds retrieval detail. "
        "Sentence three covers isolation policy."
    )
    expected_chunks = chunk_text_with_offsets(
        content,
        target_tokens=config.target_chunk_tokens,
        max_tokens=config.max_chunk_tokens,
        overlap_tokens=config.chunk_overlap_tokens,
    )

    response = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": content,
            "content_format": "markdown",
            "source_type": "note",
            "summary": "Combined reusable summary.",
            "chunk_summaries": [
                {"ordinal": ordinal, "summary": f"chunk-{ordinal} summary"}
                for ordinal in range(1, len(expected_chunks) + 1)
            ],
        },
        store=store,
        config=config,
    )

    document = store.get_document(response["id"], include_archived=True)
    chunk_ids = store.get_document_chunk_ids(response["id"])
    chunks = [store.get_chunk(chunk_id) for chunk_id in chunk_ids]

    assert document is not None
    assert document.summary == "Combined reusable summary."
    assert [chunk.summary for chunk in chunks if chunk is not None] == [
        f"chunk-{ordinal} summary" for ordinal in range(1, len(expected_chunks) + 1)
    ]


def test_capture_memory_enriches_structured_metadata_and_tags():
    store = InMemoryStore()
    config = build_config()

    response = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "ops",
            "sensitivity": "standard",
            "content": (
                "Critical API issue tracked at https://example.com/findings/1. "
                "Mapped to CVE-2026-12345, CWE-79, and ATT&CK T1190. "
                "Next action: validate the auth bypass chain."
            ),
            "content_format": "markdown",
            "source_type": "note",
            "tags": ["manual"],
        },
        store=store,
        config=config,
    )

    stored = store.get_record(response["id"]) or store.get_document(response["id"])

    assert stored is not None
    assert stored.metadata["extracted_urls"] == ["https://example.com/findings/1."]
    assert stored.metadata["extracted_cves"] == ["CVE-2026-12345"]
    assert stored.metadata["extracted_cwes"] == ["CWE-79"]
    assert stored.metadata["extracted_attack_ids"] == ["T1190"]
    assert stored.metadata["severity_markers"] == ["critical"]
    assert "manual" in stored.tags
    assert "critical" in stored.tags


def test_capture_memory_prefers_model_backed_action_items_when_available():
    store = InMemoryStore()
    config = build_config()

    response = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "ops",
            "sensitivity": "standard",
            "content": "Critical API issue. Next action: validate auth bypass chain.",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
        action_item_generator=lambda _content: [
            "Validate the auth bypass chain.",
            "Draft remediation owners.",
        ],
    )

    stored = store.get_record(response["id"]) or store.get_document(response["id"])

    assert stored is not None
    assert stored.metadata["suggested_actions"] == [
        "Validate the auth bypass chain.",
        "Draft remediation owners.",
    ]
    assert stored.metadata["action_items_strategy"] == "model-backed"


class FailingDocumentStore(InMemoryStore):
    def save_document(self, document, chunks, signature):  # type: ignore[override]
        raise RuntimeError("simulated failure")


def test_capture_memory_does_not_persist_partial_document_on_store_failure():
    store = FailingDocumentStore()
    config = build_config()

    with pytest.raises(RuntimeError, match="simulated failure"):
        capture_memory(
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

    assert store.documents == {}
    assert store.chunks == {}


def test_capture_memory_does_not_deduplicate_across_sensitivity_levels():
    store = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    config = build_config()
    request = {
        "namespace": "project-alpha",
        "bucket": "research",
        "content": "shared content",
        "content_format": "markdown",
        "source_type": "note",
    }

    sealed_capture = capture_memory(
        {**request, "sensitivity": "sealed"},
        store=store,
        config=config,
    )
    standard_capture = capture_memory(
        {**request, "sensitivity": "standard"},
        store=store,
        config=config,
    )

    assert sealed_capture["deduplicated"] is False
    assert standard_capture["deduplicated"] is False
    assert sealed_capture["id"] != standard_capture["id"]


def test_capture_many_returns_partial_results_and_preserves_single_capture_contract():
    store = InMemoryStore()
    config = build_config()

    payload = capture_many(
        [
            {
                "namespace": "project-alpha",
                "bucket": "research",
                "sensitivity": "standard",
                "content": "short stable note",
                "content_format": "markdown",
                "source_type": "note",
            },
            {
                "namespace": "project-alpha",
                "sensitivity": "standard",
                "content": "missing bucket note",
                "content_format": "markdown",
                "source_type": "note",
            },
        ],
        store=store,
        config=config,
    )

    assert payload["summary"]["processed"] == 2
    assert payload["summary"]["succeeded"] == 1
    assert payload["summary"]["failed"] == 1
    assert payload["results"][0]["ok"] is True
    assert payload["results"][0]["payload"]["storage_outcome"] == "record-stored"
    assert payload["results"][1]["ok"] is False
    assert "bucket" in payload["results"][1]["error"]


def test_capture_many_deduplicates_repeated_requests_in_batch():
    store = InMemoryStore()
    config = build_config()
    request = {
        "namespace": "project-alpha",
        "bucket": "research",
        "sensitivity": "standard",
        "content": "stable dedup note",
        "content_format": "markdown",
        "source_type": "note",
    }

    payload = capture_many([request, request], store=store, config=config)

    assert payload["summary"]["succeeded"] == 2
    first = payload["results"][0]["payload"]
    second = payload["results"][1]["payload"]
    assert first["id"] == second["id"]
    assert second["deduplicated"] is True
