"""Storage router for separating standard and sealed NeuroCore content."""

from __future__ import annotations

from neurocore.core.models import (
    BrainManifest,
    MemoryChunk,
    MemoryDocument,
    MemoryRecord,
    RetrievalArtifact,
)
from neurocore.storage.base import BaseStore, Candidate


class RoutedStore(BaseStore):
    def __init__(self, primary_store: BaseStore, sealed_store: BaseStore) -> None:
        self.primary_store = primary_store
        self.sealed_store = sealed_store

    def _route_for_sensitivity(self, sensitivity: str) -> BaseStore:
        if sensitivity == "sealed":
            return self.sealed_store
        return self.primary_store

    def _find_store_for_id(self, item_id: str) -> BaseStore:
        if self.primary_store.has_item(item_id):
            return self.primary_store
        if self.sealed_store.has_item(item_id):
            return self.sealed_store
        raise KeyError(item_id)

    def find_duplicate(
        self, namespace: str, fingerprint: str, signature: str
    ) -> str | None:
        return self.primary_store.find_duplicate(
            namespace, fingerprint, signature
        ) or self.sealed_store.find_duplicate(namespace, fingerprint, signature)

    def find_duplicates_bulk(
        self, entries: list[tuple[str, str, str]]
    ) -> list[str | None]:
        primary_matches = self.primary_store.find_duplicates_bulk(entries)
        sealed_matches = self.sealed_store.find_duplicates_bulk(entries)
        return [
            primary_match or sealed_match
            for primary_match, sealed_match in zip(primary_matches, sealed_matches)
        ]

    def save_record(self, record: MemoryRecord, signature: str) -> None:
        self._route_for_sensitivity(record.sensitivity).save_record(record, signature)

    def save_records_bulk(self, entries: list[tuple[MemoryRecord, str]]) -> None:
        primary_entries = [
            entry for entry in entries if entry[0].sensitivity != "sealed"
        ]
        sealed_entries = [
            entry for entry in entries if entry[0].sensitivity == "sealed"
        ]
        if primary_entries:
            self.primary_store.save_records_bulk(primary_entries)
        if sealed_entries:
            self.sealed_store.save_records_bulk(sealed_entries)

    def save_document(
        self, document: MemoryDocument, chunks: list[MemoryChunk], signature: str
    ) -> None:
        self._route_for_sensitivity(document.sensitivity).save_document(
            document, chunks, signature
        )

    def save_documents_bulk(
        self, entries: list[tuple[MemoryDocument, list[MemoryChunk], str]]
    ) -> None:
        primary_entries = [
            entry for entry in entries if entry[0].sensitivity != "sealed"
        ]
        sealed_entries = [
            entry for entry in entries if entry[0].sensitivity == "sealed"
        ]
        if primary_entries:
            self.primary_store.save_documents_bulk(primary_entries)
        if sealed_entries:
            self.sealed_store.save_documents_bulk(sealed_entries)

    def get_record(
        self, item_id: str, include_archived: bool = False
    ) -> MemoryRecord | None:
        return self.primary_store.get_record(
            item_id, include_archived=include_archived
        ) or self.sealed_store.get_record(item_id, include_archived=include_archived)

    def get_document(
        self, item_id: str, include_archived: bool = False
    ) -> MemoryDocument | None:
        return self.primary_store.get_document(
            item_id, include_archived=include_archived
        ) or self.sealed_store.get_document(item_id, include_archived=include_archived)

    def get_chunk(self, item_id: str) -> MemoryChunk | None:
        return self.primary_store.get_chunk(item_id) or self.sealed_store.get_chunk(
            item_id
        )

    def get_document_chunk_ids(self, document_id: str) -> list[str]:
        if self.primary_store.has_item(document_id):
            return self.primary_store.get_document_chunk_ids(document_id)
        if self.sealed_store.has_item(document_id):
            return self.sealed_store.get_document_chunk_ids(document_id)
        return []

    def get_artifact(self, item_id: str) -> RetrievalArtifact | None:
        return self.primary_store.get_artifact(
            item_id
        ) or self.sealed_store.get_artifact(item_id)

    def save_retrieval_artifact(self, artifact: RetrievalArtifact) -> None:
        self._route_for_sensitivity(artifact.sensitivity).save_retrieval_artifact(
            artifact
        )

    def list_records(self, include_archived: bool = False) -> list[MemoryRecord]:
        records = self.primary_store.list_records(include_archived=include_archived)
        records.extend(
            self.sealed_store.list_records(include_archived=include_archived)
        )
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def list_documents(self, include_archived: bool = False) -> list[MemoryDocument]:
        documents = self.primary_store.list_documents(include_archived=include_archived)
        documents.extend(
            self.sealed_store.list_documents(include_archived=include_archived)
        )
        return sorted(documents, key=lambda item: item.created_at, reverse=True)

    def list_audit_events(self, limit: int = 20) -> list[dict[str, object]]:
        events = self.primary_store.list_audit_events(limit=limit)
        events.extend(self.sealed_store.list_audit_events(limit=limit))
        return sorted(
            events,
            key=lambda item: item.get("timestamp"),
            reverse=True,
        )[:limit]

    def save_brain(self, brain: BrainManifest) -> None:
        self.primary_store.save_brain(brain)

    def get_brain(
        self, brain_id: str, include_archived: bool = False
    ) -> BrainManifest | None:
        return self.primary_store.get_brain(brain_id, include_archived=include_archived)

    def list_brains(self, include_archived: bool = False) -> list[BrainManifest]:
        return self.primary_store.list_brains(include_archived=include_archived)

    def update_brain(self, brain_id: str, patch: dict[str, object]) -> BrainManifest:
        return self.primary_store.update_brain(brain_id, patch)

    def archive_brain(self, brain_id: str, reason: str) -> BrainManifest:
        return self.primary_store.archive_brain(brain_id, reason)

    def delete_brain(self, brain_id: str) -> None:
        self.primary_store.delete_brain(brain_id)

    def update_record(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryRecord:
        return self._find_store_for_id(item_id).update_record(item_id, patch, mode)

    def update_document(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryDocument:
        return self._find_store_for_id(item_id).update_document(item_id, patch, mode)

    def soft_delete(self, item_id: str, reason: str) -> None:
        self._find_store_for_id(item_id).soft_delete(item_id, reason)

    def hard_delete(self, item_id: str) -> None:
        self._find_store_for_id(item_id).hard_delete(item_id)

    def iter_candidates(
        self,
        namespace: str,
        allowed_buckets: tuple[str, ...],
        include_archived: bool = False,
    ) -> list[Candidate]:
        return self.primary_store.iter_candidates(
            namespace, allowed_buckets, include_archived=include_archived
        )

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
            if self.primary_store.has_item(item_id):
                local_processed, local_failed, local_warnings = (
                    self.primary_store.reindex(
                        [item_id],
                        scope=scope,
                        semantic_backend=semantic_backend,
                        semantic_model_name=semantic_model_name,
                    )
                )
                processed += local_processed
                failed += local_failed
                warnings.extend(local_warnings)
            elif self.sealed_store.has_item(item_id):
                local_processed, local_failed, local_warnings = (
                    self.sealed_store.reindex(
                        [item_id],
                        scope=scope,
                        semantic_backend=semantic_backend,
                        semantic_model_name=semantic_model_name,
                    )
                )
                processed += local_processed
                failed += local_failed
                warnings.extend(local_warnings)
            else:
                failed += 1
        return processed, failed, list(dict.fromkeys(warnings))

    def record_audit(
        self,
        actor: str,
        operation: str,
        target_ids: list[str],
        outcome: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.primary_store.record_audit(
            actor, operation, target_ids, outcome, details=details
        )
        self.sealed_store.record_audit(
            actor, operation, target_ids, outcome, details=details
        )

    def import_audit_event(self, event: dict[str, object]) -> None:
        self.primary_store.import_audit_event(event)
        self.sealed_store.import_audit_event(event)

    def clear_audit_events(self) -> None:
        self.primary_store.clear_audit_events()
        self.sealed_store.clear_audit_events()

    def has_item(self, item_id: str) -> bool:
        return self.primary_store.has_item(item_id) or self.sealed_store.has_item(
            item_id
        )
