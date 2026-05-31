"""In-memory storage backend for NeuroCore."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from neurocore.core import semantic as semantic_runtime
from neurocore.core.content_normalization import (
    compute_content_fingerprint,
    normalize_content,
)
from neurocore.core.dedup_index import DedupIndex
from neurocore.core.models import (
    BrainManifest,
    MemoryChunk,
    MemoryDocument,
    MemoryRecord,
    RetrievalArtifact,
)
from neurocore.storage.base import BaseStore, Candidate, canonical_audit_event


class InMemoryStore(BaseStore):
    def __init__(self) -> None:
        self.brains: dict[str, BrainManifest] = {}
        self.records: dict[str, MemoryRecord] = {}
        self.documents: dict[str, MemoryDocument] = {}
        self.chunks: dict[str, MemoryChunk] = {}
        self.artifacts: dict[str, RetrievalArtifact] = {}
        self.document_chunks: dict[str, list[str]] = {}
        self.dedup_index = DedupIndex()
        self.audit_events: list[dict[str, object]] = []

    def find_duplicate(
        self, namespace: str, fingerprint: str, signature: str
    ) -> str | None:
        return self.dedup_index.lookup(namespace, fingerprint, signature)

    def save_record(self, record: MemoryRecord, signature: str) -> None:
        self.records[record.id] = record
        self.artifacts[record.id] = _record_artifact(record)
        self.dedup_index.register(
            namespace=record.namespace,
            fingerprint=record.content_fingerprint,
            item_id=record.id,
            signature=signature,
        )

    def save_document(
        self, document: MemoryDocument, chunks: list[MemoryChunk], signature: str
    ) -> None:
        self.documents[document.id] = document
        self.document_chunks[document.id] = [chunk.id for chunk in chunks]
        existing_chunk_ids = {
            artifact_id
            for artifact_id, artifact in self.artifacts.items()
            if artifact.document_id == document.id
        }
        for chunk in chunks:
            self.chunks[chunk.id] = chunk
            self.artifacts[chunk.id] = _chunk_artifact(document, chunk)
            existing_chunk_ids.discard(chunk.id)
        for chunk_id in existing_chunk_ids:
            self.chunks.pop(chunk_id, None)
            self.artifacts.pop(chunk_id, None)
        self.dedup_index.register(
            namespace=document.namespace,
            fingerprint=document.content_fingerprint,
            item_id=document.id,
            signature=signature,
        )

    def get_record(
        self, item_id: str, include_archived: bool = False
    ) -> MemoryRecord | None:
        record = self.records.get(item_id)
        if record is None:
            return None
        if record.archived_at and not include_archived:
            return None
        return record

    def get_document(
        self, item_id: str, include_archived: bool = False
    ) -> MemoryDocument | None:
        document = self.documents.get(item_id)
        if document is None:
            return None
        archived_at = getattr(document, "archived_at", None)
        if archived_at and not include_archived:
            return None
        return document

    def get_chunk(self, item_id: str) -> MemoryChunk | None:
        return self.chunks.get(item_id)

    def get_document_chunk_ids(self, document_id: str) -> list[str]:
        return list(self.document_chunks.get(document_id, []))

    def get_artifact(self, item_id: str) -> RetrievalArtifact | None:
        return self.artifacts.get(item_id)

    def save_retrieval_artifact(self, artifact: RetrievalArtifact) -> None:
        self.artifacts[artifact.item_id] = artifact

    def list_records(self, include_archived: bool = False) -> list[MemoryRecord]:
        records = list(self.records.values())
        if include_archived:
            return sorted(records, key=lambda item: item.created_at, reverse=True)
        return sorted(
            [record for record in records if record.archived_at is None],
            key=lambda item: item.created_at,
            reverse=True,
        )

    def list_documents(self, include_archived: bool = False) -> list[MemoryDocument]:
        documents = list(self.documents.values())
        if include_archived:
            return sorted(documents, key=lambda item: item.created_at, reverse=True)
        return sorted(
            [document for document in documents if document.archived_at is None],
            key=lambda item: item.created_at,
            reverse=True,
        )

    def list_audit_events(self, limit: int = 20) -> list[dict[str, object]]:
        return list(reversed(self.audit_events[-limit:]))

    def save_brain(self, brain: BrainManifest) -> None:
        self.brains[brain.brain_id] = brain

    def get_brain(
        self, brain_id: str, include_archived: bool = False
    ) -> BrainManifest | None:
        brain = self.brains.get(brain_id)
        if brain is None:
            return None
        if brain.status == "archived" and not include_archived:
            return None
        return brain

    def list_brains(self, include_archived: bool = False) -> list[BrainManifest]:
        brains = list(self.brains.values())
        if not include_archived:
            brains = [brain for brain in brains if brain.status != "archived"]
        return sorted(brains, key=lambda item: item.updated_at, reverse=True)

    def update_brain(self, brain_id: str, patch: dict[str, object]) -> BrainManifest:
        brain = self.get_brain(brain_id, include_archived=True)
        if brain is None:
            raise KeyError(brain_id)
        updated = replace(
            brain,
            display_name=str(patch.get("display_name", brain.display_name)),
            description=str(patch.get("description", brain.description)),
            owner=patch.get("owner", brain.owner),
            tags=tuple(patch.get("tags", brain.tags)),
            default_allowed_buckets=tuple(
                patch.get("default_allowed_buckets", brain.default_allowed_buckets)
            ),
            metadata=dict(patch.get("metadata", brain.metadata)),
            updated_at=datetime.now(UTC),
        )
        self.save_brain(updated)
        return updated

    def archive_brain(self, brain_id: str, reason: str) -> BrainManifest:
        del reason
        brain = self.get_brain(brain_id, include_archived=True)
        if brain is None:
            raise KeyError(brain_id)
        archived = replace(brain, status="archived", updated_at=datetime.now(UTC))
        self.save_brain(archived)
        return archived

    def delete_brain(self, brain_id: str) -> None:
        if brain_id not in self.brains:
            raise KeyError(brain_id)
        del self.brains[brain_id]

    def update_record(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryRecord:
        record = self.records[item_id]
        updated = replace(
            record,
            title=patch.get("title", record.title),
            metadata=patch.get("metadata", record.metadata),
            tags=tuple(patch.get("tags", record.tags)),
            updated_at=datetime.now(UTC),
            supersedes_id=patch.get("supersedes_id", record.supersedes_id),
        )
        self.records[item_id] = updated
        self.artifacts[item_id] = _record_artifact(updated)
        return updated

    def update_document(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryDocument:
        document = self.documents[item_id]
        raw_chunks = patch.get("chunks")
        chunks = (
            [chunk for chunk in raw_chunks if isinstance(chunk, MemoryChunk)]
            if isinstance(raw_chunks, list)
            else [
                self.chunks[chunk_id]
                for chunk_id in self.document_chunks.get(item_id, [])
                if chunk_id in self.chunks
            ]
        )
        updated = replace(
            document,
            title=patch.get("title", document.title),
            metadata=patch.get("metadata", document.metadata),
            tags=tuple(patch.get("tags", document.tags)),
            summary=patch.get("summary", document.summary),
            updated_at=datetime.now(UTC),
            supersedes_id=patch.get("supersedes_id", document.supersedes_id),
        )
        self.documents[item_id] = updated
        self.document_chunks[item_id] = [chunk.id for chunk in chunks]
        existing_chunk_ids = {
            artifact_id
            for artifact_id, artifact in self.artifacts.items()
            if artifact.document_id == item_id
        }
        for chunk in chunks:
            self.chunks[chunk.id] = chunk
            self.artifacts[chunk.id] = _chunk_artifact(updated, chunk)
            existing_chunk_ids.discard(chunk.id)
        for chunk_id in existing_chunk_ids:
            self.chunks.pop(chunk_id, None)
            self.artifacts.pop(chunk_id, None)
        return updated

    def soft_delete(self, item_id: str, reason: str) -> None:
        timestamp = datetime.now(UTC)
        if item_id in self.records:
            self.records[item_id] = replace(
                self.records[item_id],
                archived_at=timestamp,
                updated_at=timestamp,
            )
            artifact = self.artifacts.get(item_id)
            if artifact is not None:
                self.artifacts[item_id] = replace(
                    artifact, archived_at=timestamp, indexed_at=timestamp
                )
            return
        if item_id in self.documents:
            document = self.documents[item_id]
            archived_document = replace(
                document,
                archived_at=timestamp,
                updated_at=timestamp,
            )
            self.documents[item_id] = archived_document
            for chunk_id in self.document_chunks.get(item_id, []):
                artifact = self.artifacts.get(chunk_id)
                if artifact is not None:
                    self.artifacts[chunk_id] = replace(
                        artifact,
                        archived_at=timestamp,
                        indexed_at=timestamp,
                    )
            return
        raise KeyError(item_id)

    def hard_delete(self, item_id: str) -> None:
        if item_id in self.records:
            del self.records[item_id]
            self.artifacts.pop(item_id, None)
            _remove_dedup_entries(self.dedup_index, item_id)
            return
        if item_id in self.documents:
            for chunk_id in self.document_chunks.get(item_id, []):
                self.chunks.pop(chunk_id, None)
                self.artifacts.pop(chunk_id, None)
            self.document_chunks.pop(item_id, None)
            del self.documents[item_id]
            _remove_dedup_entries(self.dedup_index, item_id)
            return
        raise KeyError(item_id)

    def iter_candidates(
        self,
        namespace: str,
        allowed_buckets: tuple[str, ...],
        include_archived: bool = False,
    ) -> list[Candidate]:
        candidates: list[Candidate] = []
        for artifact in self.artifacts.values():
            if (
                artifact.namespace != namespace
                or artifact.bucket not in allowed_buckets
            ):
                continue
            if artifact.archived_at and not include_archived:
                continue
            if artifact.item_kind == "record":
                record = self.records.get(artifact.item_id)
                if record is None:
                    continue
                candidates.append(
                    Candidate(kind="record", item=record, artifact=artifact)
                )
                continue

            chunk = self.chunks.get(artifact.item_id)
            document = self.documents.get(artifact.document_id or "")
            if chunk is None or document is None:
                continue
            candidates.append(
                Candidate(
                    kind="chunk",
                    item=chunk,
                    artifact=artifact,
                    document=document,
                )
            )

        return candidates

    def reindex(
        self,
        ids: list[str],
        scope: str,
        semantic_backend: str = "none",
        semantic_model_name: str | None = None,
    ) -> tuple[int, int, list[str]]:
        processed = 0
        failed = 0
        warnings: list[str] = []
        status, status_warning = _semantic_status(semantic_backend)
        if status_warning is not None:
            warnings.append(status_warning)
        for item_id in ids:
            rebuilt = False
            if scope in {"records", "all"}:
                record = self.records.get(item_id)
                if record is not None:
                    self.artifacts[item_id] = _record_artifact(
                        record,
                        semantic_backend=semantic_backend,
                        semantic_model_name=semantic_model_name,
                        semantic_status=status,
                    )
                    rebuilt = True
            if scope in {"documents", "all"}:
                document = self.documents.get(item_id)
                if document is not None:
                    for chunk_id in self.document_chunks.get(item_id, []):
                        chunk = self.chunks.get(chunk_id)
                        if chunk is None:
                            continue
                        self.artifacts[chunk_id] = _chunk_artifact(
                            document,
                            chunk,
                            semantic_backend=semantic_backend,
                            semantic_model_name=semantic_model_name,
                            semantic_status=status,
                        )
                    rebuilt = True
            if rebuilt:
                processed += 1
            else:
                failed += 1
        return processed, failed, warnings

    def record_audit(
        self,
        actor: str,
        operation: str,
        target_ids: list[str],
        outcome: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.import_audit_event(
            {
                "actor": actor,
                "operation": operation,
                "target_ids": list(target_ids),
                "timestamp": datetime.now(UTC),
                "outcome": outcome,
                "details": dict(details or {}),
            }
        )

    def import_audit_event(self, event: dict[str, object]) -> None:
        identity = canonical_audit_event(event)
        if any(
            canonical_audit_event(existing) == identity
            for existing in self.audit_events
        ):
            return
        self.audit_events.append(
            {
                "actor": str(event.get("actor") or ""),
                "operation": str(event.get("operation") or ""),
                "target_ids": list(event.get("target_ids") or []),
                "timestamp": event.get("timestamp") or datetime.now(UTC),
                "outcome": str(event.get("outcome") or ""),
                "details": dict(event.get("details") or {}),
            }
        )

    def clear_audit_events(self) -> None:
        self.audit_events.clear()

    def has_item(self, item_id: str) -> bool:
        return (
            item_id in self.brains
            or item_id in self.records
            or item_id in self.documents
            or item_id in self.chunks
        )


def _record_artifact(
    record: MemoryRecord,
    *,
    semantic_backend: str = "none",
    semantic_model_name: str | None = None,
    semantic_status: str = "metadata_only",
) -> RetrievalArtifact:
    normalized_text = normalize_content(record.content)
    return RetrievalArtifact(
        item_id=record.id,
        item_kind="record",
        document_id=None,
        namespace=record.namespace,
        bucket=record.bucket,
        sensitivity=record.sensitivity,
        source_type=record.source_type,
        tags=record.tags,
        normalized_text=normalized_text,
        text_hash=compute_content_fingerprint(normalized_text),
        created_at=record.created_at,
        archived_at=record.archived_at,
        semantic_backend=semantic_backend,
        semantic_model_name=semantic_model_name,
        semantic_status=semantic_status,
        indexed_at=datetime.now(UTC),
    )


def _chunk_artifact(
    document: MemoryDocument,
    chunk: MemoryChunk,
    *,
    semantic_backend: str = "none",
    semantic_model_name: str | None = None,
    semantic_status: str = "metadata_only",
) -> RetrievalArtifact:
    normalized_text = normalize_content(chunk.chunk_text)
    return RetrievalArtifact(
        item_id=chunk.id,
        item_kind="chunk",
        document_id=document.id,
        namespace=chunk.namespace,
        bucket=chunk.bucket,
        sensitivity=chunk.sensitivity,
        source_type=document.source_type,
        tags=document.tags,
        normalized_text=normalized_text,
        text_hash=compute_content_fingerprint(normalized_text),
        created_at=chunk.created_at,
        archived_at=document.archived_at,
        semantic_backend=semantic_backend,
        semantic_model_name=semantic_model_name,
        semantic_status=semantic_status,
        indexed_at=datetime.now(UTC),
    )


def _semantic_status(semantic_backend: str) -> tuple[str, str | None]:
    if semantic_backend == "none":
        return "metadata_only", None
    if semantic_backend == "sentence-transformers":
        return semantic_runtime.sentence_transformers_status()
    return (
        "unknown",
        f"Semantic backend {semantic_backend} is unknown; artifacts were rebuilt in metadata-only mode.",
    )


def _remove_dedup_entries(dedup_index: DedupIndex, item_id: str) -> None:
    stale_keys = [
        key
        for key, existing_item_id in dedup_index._entries.items()
        if existing_item_id == item_id
    ]
    for key in stale_keys:
        dedup_index._entries.pop(key, None)
