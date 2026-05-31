"""Core data models for NeuroCore."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from neurocore.core.policies import (
    validate_bucket,
    validate_namespace,
    validate_sensitivity,
)


def _require_text(value: str, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _validate_metadata(metadata: dict[str, object]) -> dict[str, object]:
    if metadata is None:
        raise ValueError("metadata is required")
    return metadata


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    namespace: str
    bucket: str
    content: str
    content_format: str
    source_type: str
    sensitivity: str
    metadata: dict[str, object]
    content_fingerprint: str
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    tags: tuple[str, ...] = ()
    external_id: str | None = None
    idempotency_key: str | None = None
    supersedes_id: str | None = None
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text(self.id, "id"))
        object.__setattr__(self, "namespace", validate_namespace(self.namespace))
        object.__setattr__(self, "bucket", validate_bucket(self.bucket))
        object.__setattr__(self, "content", _require_text(self.content, "content"))
        object.__setattr__(
            self, "content_format", _require_text(self.content_format, "content_format")
        )
        object.__setattr__(
            self, "source_type", _require_text(self.source_type, "source_type")
        )
        object.__setattr__(self, "sensitivity", validate_sensitivity(self.sensitivity))
        object.__setattr__(self, "metadata", _validate_metadata(self.metadata))
        object.__setattr__(
            self,
            "content_fingerprint",
            _require_text(self.content_fingerprint, "content_fingerprint"),
        )


@dataclass(frozen=True)
class MemoryDocument:
    id: str
    namespace: str
    bucket: str
    title: str
    raw_content: str | None
    source_locator: str | None
    source_type: str
    sensitivity: str
    metadata: dict[str, object]
    content_fingerprint: str
    created_at: datetime
    updated_at: datetime
    external_id: str | None = None
    tags: tuple[str, ...] = ()
    summary: str | None = None
    supersedes_id: str | None = None
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text(self.id, "id"))
        object.__setattr__(self, "namespace", validate_namespace(self.namespace))
        object.__setattr__(self, "bucket", validate_bucket(self.bucket))
        object.__setattr__(self, "title", _require_text(self.title, "title"))
        if not (self.raw_content or self.source_locator):
            raise ValueError("A document must preserve its source content or locator")
        object.__setattr__(
            self, "source_type", _require_text(self.source_type, "source_type")
        )
        object.__setattr__(self, "sensitivity", validate_sensitivity(self.sensitivity))
        object.__setattr__(self, "metadata", _validate_metadata(self.metadata))
        object.__setattr__(
            self,
            "content_fingerprint",
            _require_text(self.content_fingerprint, "content_fingerprint"),
        )


@dataclass(frozen=True)
class MemoryChunk:
    id: str
    document_id: str
    namespace: str
    bucket: str
    ordinal: int
    chunk_text: str
    token_count: int
    sensitivity: str
    metadata: dict[str, object]
    created_at: datetime
    start_offset: int | None = None
    end_offset: int | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text(self.id, "id"))
        object.__setattr__(
            self, "document_id", _require_text(self.document_id, "document_id")
        )
        object.__setattr__(self, "namespace", validate_namespace(self.namespace))
        object.__setattr__(self, "bucket", validate_bucket(self.bucket))
        if self.ordinal < 1:
            raise ValueError("ordinal must be >= 1")
        object.__setattr__(
            self, "chunk_text", _require_text(self.chunk_text, "chunk_text")
        )
        if self.token_count < 1:
            raise ValueError("token_count must be >= 1")
        object.__setattr__(self, "sensitivity", validate_sensitivity(self.sensitivity))
        object.__setattr__(self, "metadata", _validate_metadata(self.metadata))


@dataclass(frozen=True)
class RetrievalArtifact:
    item_id: str
    item_kind: str
    document_id: str | None
    namespace: str
    bucket: str
    sensitivity: str
    source_type: str
    tags: tuple[str, ...]
    normalized_text: str
    text_hash: str
    created_at: datetime
    archived_at: datetime | None
    semantic_backend: str
    semantic_model_name: str | None
    semantic_status: str
    indexed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _require_text(self.item_id, "item_id"))
        if self.item_kind not in {"record", "chunk"}:
            raise ValueError("item_kind must be record or chunk")
        if self.item_kind == "chunk":
            object.__setattr__(
                self,
                "document_id",
                _require_text(self.document_id or "", "document_id"),
            )
        object.__setattr__(self, "namespace", validate_namespace(self.namespace))
        object.__setattr__(self, "bucket", validate_bucket(self.bucket))
        object.__setattr__(self, "sensitivity", validate_sensitivity(self.sensitivity))
        object.__setattr__(
            self, "source_type", _require_text(self.source_type, "source_type")
        )
        object.__setattr__(
            self,
            "normalized_text",
            _require_text(self.normalized_text, "normalized_text"),
        )
        object.__setattr__(
            self, "text_hash", _require_text(self.text_hash, "text_hash")
        )
        object.__setattr__(
            self,
            "semantic_backend",
            _require_text(self.semantic_backend, "semantic_backend"),
        )
        object.__setattr__(
            self,
            "semantic_status",
            _require_text(self.semantic_status, "semantic_status"),
        )


@dataclass(frozen=True)
class QueryContext:
    namespace: str
    allowed_buckets: tuple[str, ...]
    sensitivity_ceiling: str
    tags_any: tuple[str, ...] = ()
    tags_all: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    time_range: tuple[datetime | None, datetime | None] | None = None
    include_archived: bool = False
    extra_filters: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "namespace", validate_namespace(self.namespace))
        if not self.allowed_buckets:
            raise ValueError("allowed_buckets must not be empty")
        object.__setattr__(
            self,
            "allowed_buckets",
            tuple(validate_bucket(bucket) for bucket in self.allowed_buckets),
        )
        object.__setattr__(
            self,
            "sensitivity_ceiling",
            validate_sensitivity(self.sensitivity_ceiling),
        )


@dataclass(frozen=True)
class BrainManifest:
    brain_id: str
    namespace: str
    display_name: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    owner: str | None = None
    tags: tuple[str, ...] = ()
    default_allowed_buckets: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "brain_id", _require_text(self.brain_id, "brain_id"))
        object.__setattr__(self, "namespace", validate_namespace(self.namespace))
        object.__setattr__(
            self, "display_name", _require_text(self.display_name, "display_name")
        )
        if self.status not in {"active", "archived"}:
            raise ValueError("status must be active or archived")
        object.__setattr__(self, "metadata", _validate_metadata(self.metadata))
        object.__setattr__(
            self,
            "default_allowed_buckets",
            tuple(validate_bucket(bucket) for bucket in self.default_allowed_buckets),
        )
