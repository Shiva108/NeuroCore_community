"""Mirror store that keeps sealed content local-only."""

from __future__ import annotations

from datetime import UTC, datetime

from neurocore.core.models import (
    BrainManifest,
    MemoryChunk,
    MemoryDocument,
    MemoryRecord,
    RetrievalArtifact,
)
from neurocore.storage.base import BaseStore, Candidate, canonical_audit_event
from neurocore.storage.mirrored_store import (
    _document_signature,
    _record_signature,
    _sync_store,
)


class LocalOnlySealedMirroredStore(BaseStore):
    """Mirror standard/restricted content to cloud while keeping sealed local."""

    def __init__(
        self,
        *,
        local_store: BaseStore,
        cloud_primary_store: BaseStore,
        read_preference: str = "local",
    ) -> None:
        if read_preference not in {"local", "cloud"}:
            raise ValueError("read_preference must be local or cloud")
        self.local_store = local_store
        self.cloud_primary_store = cloud_primary_store
        self.read_preference = read_preference
        self._warnings: list[str] = []
        self._local_degraded = False
        self._last_local_error: str | None = None

    def mirror_status(self) -> dict[str, object]:
        return {
            "mode": "mirror",
            "read_preference": self.read_preference,
            "sealed_mode": "local_only",
            "cloud_primary": True,
            "local_mirror": True,
            "local_degraded": self._local_degraded,
            "last_local_error": self._last_local_error,
        }

    def pop_warnings(self) -> list[str]:
        warnings = list(self._warnings)
        self._warnings.clear()
        return warnings

    def find_duplicate(
        self, namespace: str, fingerprint: str, signature: str
    ) -> str | None:
        local_match = self.local_store.find_duplicate(namespace, fingerprint, signature)
        cloud_match = self.cloud_primary_store.find_duplicate(
            namespace, fingerprint, signature
        )
        return local_match or cloud_match

    def save_record(self, record: MemoryRecord, signature: str) -> None:
        if record.sensitivity == "sealed":
            self.local_store.save_record(record, signature)
            return
        self._cloud_first_write(
            "save record",
            lambda: self.cloud_primary_store.save_record(record, signature),
            lambda: self.local_store.save_record(record, signature),
        )

    def save_document(
        self, document: MemoryDocument, chunks: list[MemoryChunk], signature: str
    ) -> None:
        if document.sensitivity == "sealed":
            self.local_store.save_document(document, chunks, signature)
            return
        self._cloud_first_write(
            "save document",
            lambda: self.cloud_primary_store.save_document(document, chunks, signature),
            lambda: self.local_store.save_document(document, chunks, signature),
        )

    def get_record(
        self, item_id: str, include_archived: bool = False
    ) -> MemoryRecord | None:
        return self._first_present(
            lambda store: store.get_record(item_id, include_archived=include_archived)
        )

    def get_document(
        self, item_id: str, include_archived: bool = False
    ) -> MemoryDocument | None:
        return self._first_present(
            lambda store: store.get_document(item_id, include_archived=include_archived)
        )

    def get_chunk(self, item_id: str) -> MemoryChunk | None:
        return self._first_present(lambda store: store.get_chunk(item_id))

    def get_document_chunk_ids(self, document_id: str) -> list[str]:
        for _, store in self._read_order():
            if store.get_document(document_id, include_archived=True) is not None:
                return store.get_document_chunk_ids(document_id)
        return []

    def get_artifact(self, item_id: str) -> RetrievalArtifact | None:
        return self._first_present(lambda store: store.get_artifact(item_id))

    def save_retrieval_artifact(self, artifact: RetrievalArtifact) -> None:
        if artifact.sensitivity == "sealed":
            self.local_store.save_retrieval_artifact(artifact)
            return
        self._cloud_first_write(
            "save retrieval artifact",
            lambda: self.cloud_primary_store.save_retrieval_artifact(artifact),
            lambda: self.local_store.save_retrieval_artifact(artifact),
        )

    def list_records(self, include_archived: bool = False) -> list[MemoryRecord]:
        records: dict[str, MemoryRecord] = {}
        for _, store in self._read_order():
            for record in store.list_records(include_archived=include_archived):
                records.setdefault(record.id, record)
        return sorted(records.values(), key=lambda item: item.created_at, reverse=True)

    def list_documents(self, include_archived: bool = False) -> list[MemoryDocument]:
        documents: dict[str, MemoryDocument] = {}
        for _, store in self._read_order():
            for document in store.list_documents(include_archived=include_archived):
                documents.setdefault(document.id, document)
        return sorted(
            documents.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )

    def list_audit_events(self, limit: int = 20) -> list[dict[str, object]]:
        events: dict[tuple[object, ...], dict[str, object]] = {}
        for store in (
            self.cloud_primary_store,
            self._local_primary_store(),
            self._local_sealed_store(),
        ):
            for event in store.list_audit_events(limit=max(limit, 1000)):
                events.setdefault(canonical_audit_event(event), event)
        return sorted(
            events.values(),
            key=lambda item: item.get("timestamp"),
            reverse=True,
        )[:limit]

    def save_brain(self, brain: BrainManifest) -> None:
        self._cloud_first_write(
            "save brain",
            lambda: self.cloud_primary_store.save_brain(brain),
            lambda: self.local_store.save_brain(brain),
        )

    def get_brain(
        self, brain_id: str, include_archived: bool = False
    ) -> BrainManifest | None:
        return self._first_present(
            lambda store: store.get_brain(brain_id, include_archived=include_archived)
        )

    def list_brains(self, include_archived: bool = False) -> list[BrainManifest]:
        brains: dict[str, BrainManifest] = {}
        for _, store in self._read_order():
            for brain in store.list_brains(include_archived=include_archived):
                brains.setdefault(brain.brain_id, brain)
        return sorted(brains.values(), key=lambda item: item.updated_at, reverse=True)

    def update_brain(self, brain_id: str, patch: dict[str, object]) -> BrainManifest:
        updated = self.cloud_primary_store.update_brain(brain_id, patch)
        try:
            if self.local_store.get_brain(brain_id, include_archived=True) is None:
                self.local_store.save_brain(updated)
            else:
                self.local_store.update_brain(brain_id, patch)
        except Exception as exc:
            self._record_local_failure("update brain", exc)
        return updated

    def archive_brain(self, brain_id: str, reason: str) -> BrainManifest:
        archived = self.cloud_primary_store.archive_brain(brain_id, reason)
        try:
            if self.local_store.get_brain(brain_id, include_archived=True) is None:
                self.local_store.save_brain(archived)
            else:
                self.local_store.archive_brain(brain_id, reason)
        except Exception as exc:
            self._record_local_failure("archive brain", exc)
        return archived

    def delete_brain(self, brain_id: str) -> None:
        self.cloud_primary_store.delete_brain(brain_id)
        try:
            self.local_store.delete_brain(brain_id)
        except Exception as exc:
            self._record_local_failure("delete brain", exc)

    def update_record(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryRecord:
        if self._is_sealed_item(item_id):
            return self.local_store.update_record(item_id, patch, mode)

        updated = self.cloud_primary_store.update_record(item_id, patch, mode)
        try:
            if self.local_store.get_record(item_id, include_archived=True) is None:
                self.local_store.save_record(
                    updated,
                    signature=_record_signature(updated),
                )
            else:
                self.local_store.update_record(item_id, patch, mode)
        except Exception as exc:
            self._record_local_failure("update record", exc)
        return updated

    def update_document(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryDocument:
        if self._is_sealed_item(item_id):
            return self.local_store.update_document(item_id, patch, mode)

        updated = self.cloud_primary_store.update_document(item_id, patch, mode)
        try:
            if self.local_store.get_document(item_id, include_archived=True) is None:
                chunk_ids = self.cloud_primary_store.get_document_chunk_ids(item_id)
                chunks = [
                    chunk
                    for chunk_id in chunk_ids
                    if (chunk := self.cloud_primary_store.get_chunk(chunk_id))
                    is not None
                ]
                self.local_store.save_document(
                    updated,
                    chunks,
                    signature=_document_signature(updated),
                )
            else:
                self.local_store.update_document(item_id, patch, mode)
        except Exception as exc:
            self._record_local_failure("update document", exc)
        return updated

    def soft_delete(self, item_id: str, reason: str) -> None:
        if self._is_sealed_item(item_id):
            self.local_store.soft_delete(item_id, reason)
            return
        self.cloud_primary_store.soft_delete(item_id, reason)
        try:
            self.local_store.soft_delete(item_id, reason)
        except Exception as exc:
            self._record_local_failure("soft delete item", exc)

    def hard_delete(self, item_id: str) -> None:
        if self._is_sealed_item(item_id):
            self.local_store.hard_delete(item_id)
            return
        self.cloud_primary_store.hard_delete(item_id)
        try:
            self.local_store.hard_delete(item_id)
        except Exception as exc:
            self._record_local_failure("hard delete item", exc)

    def iter_candidates(
        self,
        namespace: str,
        allowed_buckets: tuple[str, ...],
        include_archived: bool = False,
    ) -> list[Candidate]:
        candidates: dict[str, Candidate] = {}
        for _, store in self._read_order():
            for candidate in store.iter_candidates(
                namespace,
                allowed_buckets,
                include_archived=include_archived,
            ):
                candidates.setdefault(candidate.artifact.item_id, candidate)
        return list(candidates.values())

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
        for item_id in ids:
            if self._is_sealed_item(item_id):
                local_processed, local_failed, local_warnings = (
                    self.local_store.reindex(
                        [item_id],
                        scope=scope,
                        semantic_backend=semantic_backend,
                        semantic_model_name=semantic_model_name,
                    )
                )
                processed += local_processed
                failed += local_failed
                warnings.extend(local_warnings)
                continue

            cloud_processed, cloud_failed, cloud_warnings = (
                self.cloud_primary_store.reindex(
                    [item_id],
                    scope=scope,
                    semantic_backend=semantic_backend,
                    semantic_model_name=semantic_model_name,
                )
            )
            processed += cloud_processed
            failed += cloud_failed
            warnings.extend(cloud_warnings)
            try:
                local_processed, local_failed, local_warnings = (
                    self.local_store.reindex(
                        [item_id],
                        scope=scope,
                        semantic_backend=semantic_backend,
                        semantic_model_name=semantic_model_name,
                    )
                )
                del local_processed
                if local_failed:
                    self._record_local_failure(
                        "reindex local mirror",
                        RuntimeError(
                            f"{local_failed} local items could not be reindexed"
                        ),
                    )
                warnings.extend(local_warnings)
            except Exception as exc:
                self._record_local_failure("reindex local mirror", exc)
        return processed, failed, warnings

    def record_audit(
        self,
        actor: str,
        operation: str,
        target_ids: list[str],
        outcome: str,
        details: dict[str, object] | None = None,
    ) -> None:
        event = {
            "actor": actor,
            "operation": operation,
            "target_ids": list(target_ids),
            "timestamp": datetime.now(UTC),
            "outcome": outcome,
            "details": dict(details or {}),
        }
        self.import_audit_event(event)

    def import_audit_event(self, event: dict[str, object]) -> None:
        target_ids = [str(item_id) for item_id in event.get("target_ids") or []]
        if self._contains_sealed_targets(target_ids):
            self._local_sealed_store().import_audit_event(event)
            return
        self._cloud_first_write(
            "import audit event",
            lambda: self.cloud_primary_store.import_audit_event(event),
            lambda: self._local_primary_store().import_audit_event(event),
        )

    def clear_audit_events(self) -> None:
        self.cloud_primary_store.clear_audit_events()
        try:
            self._local_primary_store().clear_audit_events()
            self._local_sealed_store().clear_audit_events()
        except Exception as exc:
            self._record_local_failure("clear local audit events", exc)

    def has_item(self, item_id: str) -> bool:
        return self.local_store.has_item(item_id) or self.cloud_primary_store.has_item(
            item_id
        )

    def backfill_local_to_cloud(self) -> dict[str, int]:
        return _sync_store(
            self._local_primary_store(),
            self.cloud_primary_store,
            clear_target=False,
        )

    def repair_local_from_cloud(self) -> dict[str, int]:
        counts = _sync_store(
            self.cloud_primary_store,
            self._local_primary_store(),
            clear_target=True,
        )
        self._local_degraded = False
        self._last_local_error = None
        return counts

    def _read_order(self) -> tuple[tuple[str, BaseStore], tuple[str, BaseStore]]:
        if self.read_preference == "cloud":
            return (("cloud", self.cloud_primary_store), ("local", self.local_store))
        return (("local", self.local_store), ("cloud", self.cloud_primary_store))

    def _first_present(self, getter):
        for _, store in self._read_order():
            value = getter(store)
            if value is not None:
                return value
        return None

    def _cloud_first_write(self, operation: str, cloud_write, local_write) -> None:
        cloud_write()
        try:
            local_write()
        except Exception as exc:
            self._record_local_failure(operation, exc)

    def _record_local_failure(self, operation: str, exc: Exception) -> None:
        message = f"Local mirror {operation} failed: {exc}"
        self._warnings.append(message)
        self._local_degraded = True
        self._last_local_error = message

    def _local_primary_store(self) -> BaseStore:
        return getattr(self.local_store, "primary_store", self.local_store)

    def _local_sealed_store(self) -> BaseStore:
        return getattr(self.local_store, "sealed_store", self.local_store)

    def _is_sealed_item(self, item_id: str) -> bool:
        record = self.local_store.get_record(item_id, include_archived=True)
        if record is not None:
            return record.sensitivity == "sealed"
        document = self.local_store.get_document(item_id, include_archived=True)
        if document is not None:
            return document.sensitivity == "sealed"
        artifact = self.local_store.get_artifact(item_id)
        if artifact is not None:
            return artifact.sensitivity == "sealed"
        return False

    def _contains_sealed_targets(self, target_ids: list[str]) -> bool:
        return any(self._is_sealed_item(item_id) for item_id in target_ids)
