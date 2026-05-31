from datetime import UTC, datetime

import pytest

from neurocore.core.models import (
    MemoryChunk,
    MemoryDocument,
    MemoryRecord,
    QueryContext,
)


def test_memory_record_accepts_valid_required_fields():
    record = MemoryRecord(
        id="rec-1",
        namespace="project-alpha",
        bucket="research",
        content="Important architecture note",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={"author": "user"},
        content_fingerprint="abc123",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    assert record.id == "rec-1"
    assert record.namespace == "project-alpha"
    assert record.bucket == "research"


def test_memory_record_rejects_invalid_namespace():
    with pytest.raises(ValueError, match="namespace"):
        MemoryRecord(
            id="rec-1",
            namespace="invalid namespace",
            bucket="research",
            content="Important architecture note",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="abc123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def test_memory_document_requires_source_preservation_field():
    with pytest.raises(ValueError, match="source"):
        MemoryDocument(
            id="doc-1",
            namespace="project-alpha",
            bucket="research",
            title="Transcript",
            raw_content=None,
            source_locator=None,
            source_type="transcript",
            sensitivity="restricted",
            metadata={},
            content_fingerprint="fingerprint",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def test_memory_chunk_requires_positive_ordinal_and_token_count():
    with pytest.raises(ValueError, match="ordinal"):
        MemoryChunk(
            id="chunk-1",
            document_id="doc-1",
            namespace="project-alpha",
            bucket="research",
            ordinal=0,
            chunk_text="Text",
            token_count=10,
            sensitivity="standard",
            metadata={},
            created_at=datetime.now(UTC),
        )

    with pytest.raises(ValueError, match="token_count"):
        MemoryChunk(
            id="chunk-1",
            document_id="doc-1",
            namespace="project-alpha",
            bucket="research",
            ordinal=1,
            chunk_text="Text",
            token_count=0,
            sensitivity="standard",
            metadata={},
            created_at=datetime.now(UTC),
        )


def test_query_context_requires_buckets_and_valid_sensitivity_ceiling():
    with pytest.raises(ValueError, match="allowed_buckets"):
        QueryContext(
            namespace="project-alpha",
            allowed_buckets=(),
            sensitivity_ceiling="restricted",
        )

    with pytest.raises(ValueError, match="sensitivity"):
        QueryContext(
            namespace="project-alpha",
            allowed_buckets=("research",),
            sensitivity_ceiling="invalid",
        )
