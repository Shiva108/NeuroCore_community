"""Cloud-primary mirrored storage for NeuroCore."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from neurocore.core.operator_state import (
    load_mirror_status,
    pid_is_active,
    save_mirror_status,
)
from neurocore.core.models import (
    BrainManifest,
    MemoryChunk,
    MemoryDocument,
    MemoryRecord,
    RetrievalArtifact,
)
from neurocore.storage.base import BaseStore, Candidate, canonical_audit_event


class MirroredStore(BaseStore):
    """Wrap local and cloud stores with best-effort mirrored writes."""

    def __init__(
        self,
        *,
        local_store: BaseStore,
        cloud_store: BaseStore,
        read_preference: str = "local",
        status_path: Path | None = None,
    ) -> None:
        if read_preference not in {"local", "cloud"}:
            raise ValueError("read_preference must be local or cloud")
        self.local_store = local_store
        self.cloud_store = cloud_store
        self.read_preference = read_preference
        self._status_path = status_path
        self._warnings: list[str] = []
        self._operation_statuses: list[dict[str, object]] = []
        self._local_degraded = False
        self._cloud_degraded = False
        self._last_local_error: str | None = None
        self._last_cloud_error: str | None = None
        self._last_persistence_state: str | None = None
        self._last_parity_state: str | None = None
        self._last_full_mirror_state: str | None = None
        self._parity_verified: bool | None = None
        self._reconciliation_pending = False
        self._automatic_reconciliation_attempted = False
        self._last_reconciliation_direction: str | None = None
        self._last_reconciliation_outcome: str | None = None
        self._last_parity_check: str | None = None
        self._last_bidirectional_divergence = False
        self._last_destructive_repair_risk = False
        self._last_recommended_safe_action: str | None = None
        self._last_conflict_counts: dict[str, int] = {}
        self._last_repair_mode: str | None = None
        self._last_sync_action: str | None = None
        self._last_sync_started_at: str | None = None
        self._last_sync_finished_at: str | None = None
        self._last_sync_pid: int | None = None
        self._last_sync_status: str | None = None
        self._last_sync_error: str | None = None
        self._load_persisted_status()

    def mirror_status(self) -> dict[str, object]:
        """Return the current mirrored storage health snapshot."""
        self._refresh_persisted_status()
        active_reconciliation = False
        if self._last_sync_status == "running":
            if pid_is_active(self._last_sync_pid):
                active_reconciliation = True
            else:
                self._last_sync_status = "abandoned"
                if self._last_sync_finished_at is None:
                    self._last_sync_finished_at = datetime.now(UTC).isoformat()
                if self._last_sync_error is None:
                    self._last_sync_error = (
                        "Mirror sync process exited before recording completion"
                    )
                self._persist_status()
        return {
            "mode": "mirror",
            "read_preference": self.read_preference,
            "sealed_mode": "full",
            "cloud_primary": True,
            "local_mirror": True,
            "local_degraded": self._local_degraded,
            "cloud_degraded": self._cloud_degraded,
            "last_local_error": self._last_local_error,
            "last_cloud_error": self._last_cloud_error,
            "last_persistence_state": self._last_persistence_state,
            "last_parity_state": self._last_parity_state,
            "last_full_mirror_state": self._last_full_mirror_state,
            "parity_verified": self._parity_verified,
            "reconciliation_pending": self._reconciliation_pending,
            "automatic_reconciliation_attempted": self._automatic_reconciliation_attempted,
            "last_reconciliation_direction": self._last_reconciliation_direction,
            "last_reconciliation_outcome": self._last_reconciliation_outcome,
            "last_parity_check": self._last_parity_check,
            "bidirectional_divergence": self._last_bidirectional_divergence,
            "destructive_repair_risk": self._last_destructive_repair_risk,
            "recommended_safe_action": self._last_recommended_safe_action,
            "conflict_counts": dict(self._last_conflict_counts),
            "repair_mode": self._last_repair_mode,
            "last_sync_action": self._last_sync_action,
            "last_sync_started_at": self._last_sync_started_at,
            "last_sync_finished_at": self._last_sync_finished_at,
            "last_sync_pid": self._last_sync_pid,
            "last_sync_status": self._last_sync_status,
            "last_sync_error": self._last_sync_error,
            "active_reconciliation": active_reconciliation,
        }

    def pop_warnings(self) -> list[str]:
        warnings = list(self._warnings)
        self._warnings.clear()
        return warnings

    def mark_sync_started(self, action: str) -> None:
        self._refresh_persisted_status()
        self._last_sync_action = action
        self._last_sync_started_at = datetime.now(UTC).isoformat()
        self._last_sync_finished_at = None
        self._last_sync_pid = os.getpid()
        self._last_sync_status = "running"
        self._last_sync_error = None
        self._persist_status()

    def mark_sync_finished(self, status: str, *, error: str | None = None) -> None:
        self._refresh_persisted_status()
        self._last_sync_finished_at = datetime.now(UTC).isoformat()
        self._last_sync_status = status
        self._last_sync_error = error
        self._persist_status()

    def pop_operation_status(self) -> dict[str, object]:
        statuses = list(self._operation_statuses)
        self._operation_statuses.clear()
        if not statuses:
            return {}
        persistence_state = (
            "partial"
            if any(item.get("persistence_state") == "partial" for item in statuses)
            else str(statuses[-1].get("persistence_state") or "stored")
        )
        parity_state = "stored"
        if any(item.get("parity_state") == "degraded" for item in statuses):
            parity_state = "degraded"
        elif any(item.get("parity_state") == "partial" for item in statuses):
            parity_state = "partial"
        last = dict(statuses[-1])
        last["persistence_state"] = persistence_state
        last["parity_state"] = parity_state
        last["mirror_status"] = self.mirror_status()
        return last

    def find_duplicate(
        self, namespace: str, fingerprint: str, signature: str
    ) -> str | None:
        cloud_match = self.cloud_store.find_duplicate(namespace, fingerprint, signature)
        local_match = self.local_store.find_duplicate(namespace, fingerprint, signature)
        return cloud_match or local_match

    def find_duplicates_bulk(
        self, entries: list[tuple[str, str, str]]
    ) -> list[str | None]:
        cloud_matches = self.cloud_store.find_duplicates_bulk(entries)
        local_matches = self.local_store.find_duplicates_bulk(entries)
        return [
            cloud_match or local_match
            for cloud_match, local_match in zip(cloud_matches, local_matches)
        ]

    def save_record(self, record: MemoryRecord, signature: str) -> None:
        self._dual_write(
            "save record",
            lambda: self.cloud_store.save_record(record, signature),
            lambda: self.local_store.save_record(record, signature),
            repair_clear_target=False,
        )

    def save_records_bulk(self, entries: list[tuple[MemoryRecord, str]]) -> None:
        self._dual_write(
            "save records",
            lambda: self.cloud_store.save_records_bulk(entries),
            lambda: self.local_store.save_records_bulk(entries),
            repair_clear_target=False,
        )

    def save_document(
        self, document: MemoryDocument, chunks: list[MemoryChunk], signature: str
    ) -> None:
        self._dual_write(
            "save document",
            lambda: self.cloud_store.save_document(document, chunks, signature),
            lambda: self.local_store.save_document(document, chunks, signature),
            repair_clear_target=False,
        )

    def save_documents_bulk(
        self, entries: list[tuple[MemoryDocument, list[MemoryChunk], str]]
    ) -> None:
        self._dual_write(
            "save documents",
            lambda: self.cloud_store.save_documents_bulk(entries),
            lambda: self.local_store.save_documents_bulk(entries),
            repair_clear_target=False,
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
        self._dual_write(
            "save retrieval artifact",
            lambda: self.cloud_store.save_retrieval_artifact(artifact),
            lambda: self.local_store.save_retrieval_artifact(artifact),
            repair_clear_target=False,
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
        for _, store in self._read_order():
            for event in store.list_audit_events(limit=max(limit, 1000)):
                events.setdefault(canonical_audit_event(event), event)
        return sorted(
            events.values(),
            key=lambda item: item.get("timestamp"),
            reverse=True,
        )[:limit]

    def save_brain(self, brain: BrainManifest) -> None:
        self._dual_write(
            "save brain",
            lambda: self.cloud_store.save_brain(brain),
            lambda: self.local_store.save_brain(brain),
            repair_clear_target=False,
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
        cloud_brain: BrainManifest | None = None

        def cloud_write() -> BrainManifest:
            nonlocal cloud_brain
            cloud_brain = self.cloud_store.update_brain(brain_id, patch)
            return cloud_brain

        def local_write() -> BrainManifest:
            if self.local_store.get_brain(brain_id, include_archived=True) is None:
                if cloud_brain is None:
                    raise KeyError(brain_id)
                self.local_store.save_brain(cloud_brain)
                return cloud_brain
            return self.local_store.update_brain(brain_id, patch)

        return self._dual_write_result(
            "update brain",
            cloud_write,
            local_write,
            repair_clear_target=True,
        )

    def archive_brain(self, brain_id: str, reason: str) -> BrainManifest:
        archived_brain: BrainManifest | None = None

        def cloud_write() -> BrainManifest:
            nonlocal archived_brain
            archived_brain = self.cloud_store.archive_brain(brain_id, reason)
            return archived_brain

        def local_write() -> BrainManifest:
            if self.local_store.get_brain(brain_id, include_archived=True) is None:
                if archived_brain is None:
                    raise KeyError(brain_id)
                self.local_store.save_brain(archived_brain)
                return archived_brain
            return self.local_store.archive_brain(brain_id, reason)

        return self._dual_write_result(
            "archive brain",
            cloud_write,
            local_write,
            repair_clear_target=True,
        )

    def delete_brain(self, brain_id: str) -> None:
        self._dual_write(
            "delete brain",
            lambda: self.cloud_store.delete_brain(brain_id),
            lambda: self.local_store.delete_brain(brain_id),
            repair_clear_target=True,
        )

    def update_record(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryRecord:
        cloud_record: MemoryRecord | None = None

        def cloud_write() -> MemoryRecord:
            nonlocal cloud_record
            cloud_record = self.cloud_store.update_record(item_id, patch, mode)
            return cloud_record

        def local_write() -> MemoryRecord:
            if self.local_store.get_record(item_id, include_archived=True) is None:
                if cloud_record is None:
                    raise KeyError(item_id)
                self.local_store.save_record(
                    cloud_record,
                    signature=_record_signature(cloud_record),
                )
                return cloud_record
            return self.local_store.update_record(item_id, patch, mode)

        return self._dual_write_result(
            "update record",
            cloud_write,
            local_write,
            repair_clear_target=True,
        )

    def update_document(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryDocument:
        cloud_document: MemoryDocument | None = None

        def cloud_write() -> MemoryDocument:
            nonlocal cloud_document
            cloud_document = self.cloud_store.update_document(item_id, patch, mode)
            return cloud_document

        def local_write() -> MemoryDocument:
            if self.local_store.get_document(item_id, include_archived=True) is None:
                if cloud_document is None:
                    raise KeyError(item_id)
                chunk_ids = self.cloud_store.get_document_chunk_ids(item_id)
                chunks = [
                    chunk
                    for chunk_id in chunk_ids
                    if (chunk := self.cloud_store.get_chunk(chunk_id)) is not None
                ]
                self.local_store.save_document(
                    cloud_document,
                    chunks,
                    signature=_document_signature(cloud_document),
                )
                return cloud_document
            return self.local_store.update_document(item_id, patch, mode)

        return self._dual_write_result(
            "update document",
            cloud_write,
            local_write,
            repair_clear_target=True,
        )

    def soft_delete(self, item_id: str, reason: str) -> None:
        self._dual_write(
            "soft delete item",
            lambda: self.cloud_store.soft_delete(item_id, reason),
            lambda: self.local_store.soft_delete(item_id, reason),
            repair_clear_target=True,
        )

    def hard_delete(self, item_id: str) -> None:
        self._dual_write(
            "hard delete item",
            lambda: self.cloud_store.hard_delete(item_id),
            lambda: self.local_store.hard_delete(item_id),
            repair_clear_target=True,
        )

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
        cloud_result: tuple[int, int, list[str]] | None = None
        local_result: tuple[int, int, list[str]] | None = None

        def cloud_write() -> tuple[int, int, list[str]]:
            nonlocal cloud_result
            cloud_result = self.cloud_store.reindex(
                ids,
                scope=scope,
                semantic_backend=semantic_backend,
                semantic_model_name=semantic_model_name,
            )
            return cloud_result

        def local_write() -> tuple[int, int, list[str]]:
            nonlocal local_result
            local_result = self.local_store.reindex(
                ids,
                scope=scope,
                semantic_backend=semantic_backend,
                semantic_model_name=semantic_model_name,
            )
            return local_result

        result = self._dual_write_result(
            "reindex",
            cloud_write,
            local_write,
            repair_clear_target=True,
        )
        processed, failed, warnings = result
        if cloud_result is not None and local_result is not None:
            warnings = list(dict.fromkeys([*warnings, *local_result[2]]))
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
        self._dual_write(
            "import audit event",
            lambda: self.cloud_store.import_audit_event(event),
            lambda: self.local_store.import_audit_event(event),
            repair_clear_target=False,
        )

    def clear_audit_events(self) -> None:
        self._dual_write(
            "clear audit events",
            self.cloud_store.clear_audit_events,
            self.local_store.clear_audit_events,
            repair_clear_target=True,
        )

    def has_item(self, item_id: str) -> bool:
        return self.local_store.has_item(item_id) or self.cloud_store.has_item(item_id)

    def backfill_local_to_cloud(self) -> dict[str, int]:
        counts = _sync_store(self.local_store, self.cloud_store, clear_target=False)
        self._record_parity_check(
            _build_parity_report(
                self.local_store,
                self.cloud_store,
                read_preference=self.read_preference,
            )
        )
        return counts

    def repair_local_from_cloud(self) -> dict[str, int]:
        counts = _sync_store(self.cloud_store, self.local_store, clear_target=True)
        self._clear_local_degradation()
        self._record_parity_check(
            _build_parity_report(
                self.local_store,
                self.cloud_store,
                read_preference=self.read_preference,
            )
        )
        return counts

    def repair_cloud_from_local(self) -> dict[str, int]:
        counts = _sync_store(self.local_store, self.cloud_store, clear_target=True)
        self._clear_cloud_degradation()
        self._record_parity_check(
            _build_parity_report(
                self.local_store,
                self.cloud_store,
                read_preference=self.read_preference,
            )
        )
        return counts

    def reconcile_union(self) -> dict[str, object]:
        counts = _reconcile_union_store(self.local_store, self.cloud_store)
        self._clear_local_degradation()
        self._clear_cloud_degradation()
        report = _build_parity_report(
            self.local_store,
            self.cloud_store,
            read_preference=self.read_preference,
        )
        self._record_parity_check(report, repair_mode="union")
        return {
            "repair_mode": "union",
            "counts": counts,
            "in_sync_after": report["in_sync"],
            "parity": report,
        }

    def verify_parity(self) -> dict[str, object]:
        before = _build_parity_report(
            self.local_store,
            self.cloud_store,
            read_preference=self.read_preference,
        )
        self._record_parity_check(
            before,
            repair_mode=(
                "union"
                if before["destructive_repair_risk"]
                else ("none" if before["in_sync"] else "directional")
            ),
        )
        result: dict[str, object] = {
            "in_sync": before["in_sync"],
            "parity_state": before["parity_state"],
            "missing_from_local": before["missing_from_local"],
            "missing_from_cloud": before["missing_from_cloud"],
            "compared": before["compared"],
            "bidirectional_divergence": before["bidirectional_divergence"],
            "destructive_repair_risk": before["destructive_repair_risk"],
            "recommended_safe_action": before["recommended_safe_action"],
            "conflict_counts": before["conflict_counts"],
            "repair_mode": (
                "union"
                if before["destructive_repair_risk"]
                else ("none" if before["in_sync"] else "directional")
            ),
            "repair_action": None,
            "repair_basis": "none" if before["in_sync"] else before["repair_basis"],
            "repair_counts": None,
        }
        if before["in_sync"]:
            result["in_sync_after"] = True
            return result
        if before["destructive_repair_risk"]:
            result["in_sync_after"] = False
            return result

        repair_action = str(before["repair_action"])
        if repair_action == "repair_cloud_from_local":
            repair_counts = _sync_store(
                self.local_store,
                self.cloud_store,
                clear_target=False,
            )
            self._clear_cloud_degradation()
        else:
            repair_counts = _sync_store(
                self.cloud_store,
                self.local_store,
                clear_target=False,
            )
            self._clear_local_degradation()
        after = _build_parity_report(
            self.local_store,
            self.cloud_store,
            read_preference=self.read_preference,
        )
        self._record_parity_check(
            {
                **after,
                "repair_action": repair_action,
                "in_sync_after": after["in_sync"],
            },
            repair_mode="directional",
        )
        result["repair_action"] = repair_action
        result["repair_counts"] = repair_counts
        result["in_sync_after"] = after["in_sync"]
        result["missing_from_local_after"] = after["missing_from_local"]
        result["missing_from_cloud_after"] = after["missing_from_cloud"]
        result["bidirectional_divergence_after"] = after["bidirectional_divergence"]
        result["destructive_repair_risk_after"] = after["destructive_repair_risk"]
        result["recommended_safe_action_after"] = after["recommended_safe_action"]
        result["conflict_counts_after"] = after["conflict_counts"]
        return result

    def _read_order(self) -> tuple[tuple[str, BaseStore], tuple[str, BaseStore]]:
        if self.read_preference == "cloud":
            return (("cloud", self.cloud_store), ("local", self.local_store))
        return (("local", self.local_store), ("cloud", self.cloud_store))

    def _first_present(self, getter):
        for _, store in self._read_order():
            value = getter(store)
            if value is not None:
                return value
        return None

    def _dual_write(
        self,
        operation: str,
        cloud_write,
        local_write,
        *,
        repair_clear_target: bool,
    ) -> None:
        self._dual_write_result(
            operation,
            cloud_write,
            local_write,
            repair_clear_target=repair_clear_target,
        )

    def _dual_write_result(
        self,
        operation: str,
        cloud_write,
        local_write,
        *,
        repair_clear_target: bool,
    ):
        cloud_result = None
        local_result = None
        cloud_error: Exception | None = None
        local_error: Exception | None = None

        try:
            cloud_result = cloud_write()
        except Exception as exc:
            cloud_error = exc
            self._record_cloud_failure(operation, exc)

        try:
            local_result = local_write()
        except Exception as exc:
            local_error = exc
            self._record_local_failure(operation, exc)

        if cloud_error is not None and local_error is not None:
            self._record_operation_state(
                persistence_state="partial",
                parity_state="degraded",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            raise RuntimeError(
                f"Mirror {operation} failed for both cloud and local backends: "
                f"cloud={cloud_error}; local={local_error}"
            ) from cloud_error

        if cloud_error is None and local_error is None:
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return cloud_result

        if cloud_error is not None:
            reconcile_direction = "repair_cloud_from_local"
            repair_source = self.local_store
            repair_target = self.cloud_store
        else:
            reconcile_direction = "repair_local_from_cloud"
            repair_source = self.cloud_store
            repair_target = self.local_store

        self._automatic_reconciliation_attempted = True
        self._last_reconciliation_direction = reconcile_direction
        self._reconciliation_pending = True
        try:
            _sync_store(
                repair_source,
                repair_target,
                clear_target=repair_clear_target,
            )
        except Exception as exc:
            if reconcile_direction == "repair_cloud_from_local":
                self._record_cloud_failure(f"{operation} auto-repair", exc)
            else:
                self._record_local_failure(f"{operation} auto-repair", exc)
            self._last_reconciliation_outcome = "failed"
            self._record_operation_state(
                persistence_state="partial",
                parity_state="degraded",
                reconciliation_attempted=True,
                reconciliation_direction=reconcile_direction,
            )
            return cloud_result if cloud_error is None else local_result

        if reconcile_direction == "repair_cloud_from_local":
            self._clear_cloud_degradation()
        else:
            self._clear_local_degradation()
        parity_report = _build_parity_report(
            self.local_store,
            self.cloud_store,
            read_preference=self.read_preference,
        )
        self._record_parity_check(parity_report, repair_mode="directional")
        self._last_reconciliation_outcome = "success"
        self._record_operation_state(
            persistence_state="partial",
            parity_state="stored" if parity_report["in_sync"] else "degraded",
            reconciliation_attempted=True,
            reconciliation_direction=reconcile_direction,
        )
        return cloud_result if cloud_error is None else local_result

    def _record_operation_state(
        self,
        *,
        persistence_state: str,
        parity_state: str,
        reconciliation_attempted: bool,
        reconciliation_direction: str | None,
    ) -> None:
        self._refresh_persisted_status()
        self._last_persistence_state = persistence_state
        self._last_parity_state = parity_state
        if parity_state == "stored":
            self._last_full_mirror_state = "stored"
        self._operation_statuses.append(
            {
                "persistence_state": persistence_state,
                "parity_state": parity_state,
                "reconciliation_attempted": reconciliation_attempted,
                "reconciliation_direction": reconciliation_direction,
            }
        )
        self._persist_status()

    def _record_local_failure(self, operation: str, exc: Exception) -> None:
        self._refresh_persisted_status()
        message = f"Local mirror {operation} failed: {exc}"
        self._warnings.append(message)
        self._local_degraded = True
        self._last_local_error = message
        self._parity_verified = False
        self._reconciliation_pending = True
        self._persist_status()

    def _record_cloud_failure(self, operation: str, exc: Exception) -> None:
        self._refresh_persisted_status()
        message = f"Cloud mirror {operation} failed: {exc}"
        self._warnings.append(message)
        self._cloud_degraded = True
        self._last_cloud_error = message
        self._parity_verified = False
        self._reconciliation_pending = True
        self._persist_status()

    def _clear_local_degradation(self) -> None:
        self._local_degraded = False
        self._last_local_error = None

    def _clear_cloud_degradation(self) -> None:
        self._cloud_degraded = False
        self._last_cloud_error = None

    def _record_parity_check(
        self,
        report: dict[str, object],
        *,
        repair_mode: str | None = None,
    ) -> None:
        self._refresh_persisted_status()
        self._last_parity_check = datetime.now(UTC).isoformat()
        self._parity_verified = bool(report["in_sync"])
        self._reconciliation_pending = not bool(report["in_sync"])
        self._last_bidirectional_divergence = bool(
            report.get("bidirectional_divergence", False)
        )
        self._last_destructive_repair_risk = bool(
            report.get("destructive_repair_risk", False)
        )
        self._last_recommended_safe_action = (
            str(report["recommended_safe_action"])
            if report.get("recommended_safe_action") is not None
            else None
        )
        self._last_conflict_counts = {
            str(key): int(value)
            for key, value in dict(report.get("conflict_counts") or {}).items()
        }
        self._last_repair_mode = repair_mode
        if report.get("repair_action") is not None and "in_sync_after" in report:
            self._last_reconciliation_direction = str(report["repair_action"])
            self._last_reconciliation_outcome = (
                "success" if bool(report.get("in_sync_after")) else "failed"
            )
        if report["in_sync"]:
            self._last_parity_state = "stored"
            self._last_full_mirror_state = "stored"
        self._persist_status()

    def _snapshot(self) -> dict[str, object]:
        return {
            "local_degraded": self._local_degraded,
            "cloud_degraded": self._cloud_degraded,
            "last_local_error": self._last_local_error,
            "last_cloud_error": self._last_cloud_error,
            "last_persistence_state": self._last_persistence_state,
            "last_parity_state": self._last_parity_state,
            "last_full_mirror_state": self._last_full_mirror_state,
            "parity_verified": self._parity_verified,
            "reconciliation_pending": self._reconciliation_pending,
            "automatic_reconciliation_attempted": self._automatic_reconciliation_attempted,
            "last_reconciliation_direction": self._last_reconciliation_direction,
            "last_reconciliation_outcome": self._last_reconciliation_outcome,
            "last_parity_check": self._last_parity_check,
            "bidirectional_divergence": self._last_bidirectional_divergence,
            "destructive_repair_risk": self._last_destructive_repair_risk,
            "recommended_safe_action": self._last_recommended_safe_action,
            "conflict_counts": dict(self._last_conflict_counts),
            "repair_mode": self._last_repair_mode,
            "last_sync_action": self._last_sync_action,
            "last_sync_started_at": self._last_sync_started_at,
            "last_sync_finished_at": self._last_sync_finished_at,
            "last_sync_pid": self._last_sync_pid,
            "last_sync_status": self._last_sync_status,
            "last_sync_error": self._last_sync_error,
        }

    def _load_persisted_status(self) -> None:
        if self._status_path is None:
            return
        self._apply_snapshot(load_mirror_status(self._status_path))

    def _refresh_persisted_status(self) -> None:
        self._load_persisted_status()

    def _persist_status(self) -> None:
        if self._status_path is None:
            return
        save_mirror_status(self._snapshot(), self._status_path)

    def _apply_snapshot(self, snapshot: dict[str, object]) -> None:
        if not snapshot:
            return
        self._local_degraded = bool(snapshot.get("local_degraded", self._local_degraded))
        self._cloud_degraded = bool(snapshot.get("cloud_degraded", self._cloud_degraded))
        self._last_local_error = _snapshot_str(snapshot.get("last_local_error"))
        self._last_cloud_error = _snapshot_str(snapshot.get("last_cloud_error"))
        self._last_persistence_state = _snapshot_str(
            snapshot.get("last_persistence_state")
        )
        self._last_parity_state = _snapshot_str(snapshot.get("last_parity_state"))
        self._last_full_mirror_state = _snapshot_str(
            snapshot.get("last_full_mirror_state")
        )
        self._parity_verified = _snapshot_bool(snapshot.get("parity_verified"))
        self._reconciliation_pending = bool(
            snapshot.get("reconciliation_pending", self._reconciliation_pending)
        )
        self._automatic_reconciliation_attempted = bool(
            snapshot.get(
                "automatic_reconciliation_attempted",
                self._automatic_reconciliation_attempted,
            )
        )
        self._last_reconciliation_direction = _snapshot_str(
            snapshot.get("last_reconciliation_direction")
        )
        self._last_reconciliation_outcome = _snapshot_str(
            snapshot.get("last_reconciliation_outcome")
        )
        self._last_parity_check = _snapshot_str(snapshot.get("last_parity_check"))
        self._last_bidirectional_divergence = bool(
            snapshot.get(
                "bidirectional_divergence",
                self._last_bidirectional_divergence,
            )
        )
        self._last_destructive_repair_risk = bool(
            snapshot.get(
                "destructive_repair_risk",
                self._last_destructive_repair_risk,
            )
        )
        self._last_recommended_safe_action = _snapshot_str(
            snapshot.get("recommended_safe_action")
        )
        self._last_conflict_counts = {
            str(key): int(value)
            for key, value in dict(snapshot.get("conflict_counts") or {}).items()
        }
        self._last_repair_mode = _snapshot_str(snapshot.get("repair_mode"))
        self._last_sync_action = _snapshot_str(snapshot.get("last_sync_action"))
        self._last_sync_started_at = _snapshot_str(snapshot.get("last_sync_started_at"))
        self._last_sync_finished_at = _snapshot_str(
            snapshot.get("last_sync_finished_at")
        )
        self._last_sync_pid = _snapshot_int(snapshot.get("last_sync_pid"))
        self._last_sync_status = _snapshot_str(snapshot.get("last_sync_status"))
        self._last_sync_error = _snapshot_str(snapshot.get("last_sync_error"))


def _sync_store(
    source_store: BaseStore,
    target_store: BaseStore,
    *,
    clear_target: bool,
) -> dict[str, int]:
    counts = {
        "records": 0,
        "documents": 0,
        "chunks": 0,
        "artifacts": 0,
        "brains": 0,
        "audit_events": 0,
        "cleared_records": 0,
        "cleared_documents": 0,
        "cleared_brains": 0,
        "cleared_audit_events": 0,
    }
    if clear_target:
        counts.update(_clear_store(target_store))

    for record in sorted(
        source_store.list_records(include_archived=True),
        key=lambda item: item.created_at,
    ):
        target_store.save_record(record, signature=_record_signature(record))
        artifact = source_store.get_artifact(record.id)
        if artifact is not None:
            target_store.save_retrieval_artifact(artifact)
            counts["artifacts"] += 1
        counts["records"] += 1

    for document in sorted(
        source_store.list_documents(include_archived=True),
        key=lambda item: item.created_at,
    ):
        chunk_ids = source_store.get_document_chunk_ids(document.id)
        chunks = [
            chunk
            for chunk_id in chunk_ids
            if (chunk := source_store.get_chunk(chunk_id)) is not None
        ]
        target_store.save_document(
            document,
            chunks,
            signature=_document_signature(document),
        )
        counts["documents"] += 1
        counts["chunks"] += len(chunks)
        for chunk in chunks:
            artifact = source_store.get_artifact(chunk.id)
            if artifact is not None:
                target_store.save_retrieval_artifact(artifact)
                counts["artifacts"] += 1

    for brain in reversed(source_store.list_brains(include_archived=True)):
        target_store.save_brain(brain)
        counts["brains"] += 1

    audit_events = list(reversed(source_store.list_audit_events(limit=1_000_000)))
    for event in audit_events:
        target_store.import_audit_event(event)
        counts["audit_events"] += 1

    return counts


def _snapshot_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _snapshot_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _snapshot_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clear_store(store: BaseStore) -> dict[str, int]:
    counts = {
        "cleared_records": 0,
        "cleared_documents": 0,
        "cleared_brains": 0,
        "cleared_audit_events": 0,
    }
    for document in store.list_documents(include_archived=True):
        store.hard_delete(document.id)
        counts["cleared_documents"] += 1
    for record in store.list_records(include_archived=True):
        store.hard_delete(record.id)
        counts["cleared_records"] += 1
    for brain in store.list_brains(include_archived=True):
        store.delete_brain(brain.brain_id)
        counts["cleared_brains"] += 1
    counts["cleared_audit_events"] = len(store.list_audit_events(limit=1_000_000))
    store.clear_audit_events()
    return counts


@dataclass(frozen=True)
class StoreInventory:
    records: dict[str, MemoryRecord]
    record_signatures: dict[str, tuple[object, ...]]
    documents: dict[str, MemoryDocument]
    document_signatures: dict[str, tuple[object, ...]]
    document_graph_signatures: dict[str, tuple[object, ...]]
    document_chunks: dict[str, tuple[MemoryChunk, ...]]
    chunks: dict[str, MemoryChunk]
    chunk_signatures: dict[str, tuple[object, ...]]
    artifacts: dict[str, RetrievalArtifact]
    artifact_signatures: dict[str, tuple[object, ...]]
    brains: dict[str, BrainManifest]
    brain_signatures: dict[str, tuple[object, ...]]
    audit_events: dict[tuple[object, ...], dict[str, object]]
    record_dedup_entries: set[tuple[object, ...]]
    document_dedup_entries: set[tuple[object, ...]]


def _reconcile_union_store(
    local_store: BaseStore,
    cloud_store: BaseStore,
) -> dict[str, object]:
    counts = {
        "copied_to_local": {
            "records": 0,
            "documents": 0,
            "chunks": 0,
            "artifacts": 0,
            "brains": 0,
        },
        "copied_to_cloud": {
            "records": 0,
            "documents": 0,
            "chunks": 0,
            "artifacts": 0,
            "brains": 0,
        },
        "resolved_conflicts": {
            "records": 0,
            "documents": 0,
            "artifacts": 0,
            "brains": 0,
        },
        "rebuilt_dedup_entries": {"local": 0, "cloud": 0},
        "audit_events_imported": {"local": 0, "cloud": 0},
        "in_sync_after": False,
    }

    initial = {
        "local": _collect_store_inventory(local_store),
        "cloud": _collect_store_inventory(cloud_store),
    }
    _reconcile_records(
        initial["local"], initial["cloud"], local_store, cloud_store, counts
    )
    _reconcile_documents(
        initial["local"],
        initial["cloud"],
        local_store,
        cloud_store,
        counts,
    )
    _reconcile_brains(
        initial["local"], initial["cloud"], local_store, cloud_store, counts
    )
    _reconcile_audit_events(
        initial["local"],
        initial["cloud"],
        local_store,
        cloud_store,
        counts,
    )

    refreshed = {
        "local": _collect_store_inventory(local_store),
        "cloud": _collect_store_inventory(cloud_store),
    }
    _reconcile_artifacts(
        refreshed["local"],
        refreshed["cloud"],
        local_store,
        cloud_store,
        counts,
    )

    counts["rebuilt_dedup_entries"]["local"] = _rebuild_dedup_index(local_store)
    counts["rebuilt_dedup_entries"]["cloud"] = _rebuild_dedup_index(cloud_store)
    report = _build_parity_report(local_store, cloud_store, read_preference="local")
    counts["in_sync_after"] = bool(report["in_sync"])
    return counts


def _reconcile_records(
    local_inventory: StoreInventory,
    cloud_inventory: StoreInventory,
    local_store: BaseStore,
    cloud_store: BaseStore,
    counts: dict[str, object],
) -> None:
    for item_id in sorted(set(local_inventory.records) | set(cloud_inventory.records)):
        local_record = local_inventory.records.get(item_id)
        cloud_record = cloud_inventory.records.get(item_id)
        if local_record is None and cloud_record is not None:
            local_store.save_record(
                cloud_record, signature=_record_signature(cloud_record)
            )
            counts["copied_to_local"]["records"] += 1
            continue
        if cloud_record is None and local_record is not None:
            cloud_store.save_record(
                local_record, signature=_record_signature(local_record)
            )
            counts["copied_to_cloud"]["records"] += 1
            continue
        if local_record is None or cloud_record is None:
            continue
        if (
            local_inventory.record_signatures[item_id]
            == cloud_inventory.record_signatures[item_id]
        ):
            continue
        if _choose_winner_record(local_record, cloud_record) == "local":
            cloud_store.save_record(
                local_record, signature=_record_signature(local_record)
            )
            counts["copied_to_cloud"]["records"] += 1
        else:
            local_store.save_record(
                cloud_record, signature=_record_signature(cloud_record)
            )
            counts["copied_to_local"]["records"] += 1
        counts["resolved_conflicts"]["records"] += 1


def _reconcile_documents(
    local_inventory: StoreInventory,
    cloud_inventory: StoreInventory,
    local_store: BaseStore,
    cloud_store: BaseStore,
    counts: dict[str, object],
) -> None:
    for item_id in sorted(
        set(local_inventory.documents) | set(cloud_inventory.documents)
    ):
        local_document = local_inventory.documents.get(item_id)
        cloud_document = cloud_inventory.documents.get(item_id)
        if local_document is None and cloud_document is not None:
            chunks = list(cloud_inventory.document_chunks.get(item_id, ()))
            local_store.save_document(
                cloud_document,
                chunks,
                signature=_document_signature(cloud_document),
            )
            counts["copied_to_local"]["documents"] += 1
            counts["copied_to_local"]["chunks"] += len(chunks)
            continue
        if cloud_document is None and local_document is not None:
            chunks = list(local_inventory.document_chunks.get(item_id, ()))
            cloud_store.save_document(
                local_document,
                chunks,
                signature=_document_signature(local_document),
            )
            counts["copied_to_cloud"]["documents"] += 1
            counts["copied_to_cloud"]["chunks"] += len(chunks)
            continue
        if local_document is None or cloud_document is None:
            continue
        if (
            local_inventory.document_graph_signatures[item_id]
            == cloud_inventory.document_graph_signatures[item_id]
        ):
            continue
        if _choose_winner_document(local_document, cloud_document) == "local":
            winner_document = local_document
            winner_chunks = list(local_inventory.document_chunks.get(item_id, ()))
            cloud_store.save_document(
                winner_document,
                winner_chunks,
                signature=_document_signature(winner_document),
            )
            counts["copied_to_cloud"]["documents"] += 1
            counts["copied_to_cloud"]["chunks"] += len(winner_chunks)
        else:
            winner_document = cloud_document
            winner_chunks = list(cloud_inventory.document_chunks.get(item_id, ()))
            local_store.save_document(
                winner_document,
                winner_chunks,
                signature=_document_signature(winner_document),
            )
            counts["copied_to_local"]["documents"] += 1
            counts["copied_to_local"]["chunks"] += len(winner_chunks)
        counts["resolved_conflicts"]["documents"] += 1


def _reconcile_brains(
    local_inventory: StoreInventory,
    cloud_inventory: StoreInventory,
    local_store: BaseStore,
    cloud_store: BaseStore,
    counts: dict[str, object],
) -> None:
    for brain_id in sorted(set(local_inventory.brains) | set(cloud_inventory.brains)):
        local_brain = local_inventory.brains.get(brain_id)
        cloud_brain = cloud_inventory.brains.get(brain_id)
        if local_brain is None and cloud_brain is not None:
            local_store.save_brain(cloud_brain)
            counts["copied_to_local"]["brains"] += 1
            continue
        if cloud_brain is None and local_brain is not None:
            cloud_store.save_brain(local_brain)
            counts["copied_to_cloud"]["brains"] += 1
            continue
        if local_brain is None or cloud_brain is None:
            continue
        if (
            local_inventory.brain_signatures[brain_id]
            == cloud_inventory.brain_signatures[brain_id]
        ):
            continue
        if _choose_winner_brain(local_brain, cloud_brain) == "local":
            cloud_store.save_brain(local_brain)
            counts["copied_to_cloud"]["brains"] += 1
        else:
            local_store.save_brain(cloud_brain)
            counts["copied_to_local"]["brains"] += 1
        counts["resolved_conflicts"]["brains"] += 1


def _reconcile_artifacts(
    local_inventory: StoreInventory,
    cloud_inventory: StoreInventory,
    local_store: BaseStore,
    cloud_store: BaseStore,
    counts: dict[str, object],
) -> None:
    for item_id in sorted(
        set(local_inventory.artifacts) | set(cloud_inventory.artifacts)
    ):
        local_artifact = local_inventory.artifacts.get(item_id)
        cloud_artifact = cloud_inventory.artifacts.get(item_id)
        if local_artifact is None and cloud_artifact is not None:
            local_store.save_retrieval_artifact(cloud_artifact)
            counts["copied_to_local"]["artifacts"] += 1
            continue
        if cloud_artifact is None and local_artifact is not None:
            cloud_store.save_retrieval_artifact(local_artifact)
            counts["copied_to_cloud"]["artifacts"] += 1
            continue
        if local_artifact is None or cloud_artifact is None:
            continue
        if (
            local_inventory.artifact_signatures[item_id]
            == cloud_inventory.artifact_signatures[item_id]
        ):
            continue
        if _choose_winner_artifact(local_artifact, cloud_artifact) == "local":
            cloud_store.save_retrieval_artifact(local_artifact)
            counts["copied_to_cloud"]["artifacts"] += 1
        else:
            local_store.save_retrieval_artifact(cloud_artifact)
            counts["copied_to_local"]["artifacts"] += 1
        counts["resolved_conflicts"]["artifacts"] += 1


def _reconcile_audit_events(
    local_inventory: StoreInventory,
    cloud_inventory: StoreInventory,
    local_store: BaseStore,
    cloud_store: BaseStore,
    counts: dict[str, object],
) -> None:
    for identity, event in local_inventory.audit_events.items():
        if identity in cloud_inventory.audit_events:
            continue
        cloud_store.import_audit_event(event)
        counts["audit_events_imported"]["cloud"] += 1
    for identity, event in cloud_inventory.audit_events.items():
        if identity in local_inventory.audit_events:
            continue
        local_store.import_audit_event(event)
        counts["audit_events_imported"]["local"] += 1


def _choose_winner_record(
    local_record: MemoryRecord,
    cloud_record: MemoryRecord,
) -> str:
    return _choose_winner_from_datetimes(
        local_primary=local_record.updated_at,
        cloud_primary=cloud_record.updated_at,
        local_fallback=local_record.created_at,
        cloud_fallback=cloud_record.created_at,
    )


def _choose_winner_document(
    local_document: MemoryDocument,
    cloud_document: MemoryDocument,
) -> str:
    return _choose_winner_from_datetimes(
        local_primary=local_document.updated_at,
        cloud_primary=cloud_document.updated_at,
        local_fallback=local_document.created_at,
        cloud_fallback=cloud_document.created_at,
    )


def _choose_winner_artifact(
    local_artifact: RetrievalArtifact,
    cloud_artifact: RetrievalArtifact,
) -> str:
    return _choose_winner_from_datetimes(
        local_primary=local_artifact.indexed_at,
        cloud_primary=cloud_artifact.indexed_at,
        local_fallback=local_artifact.created_at,
        cloud_fallback=cloud_artifact.created_at,
    )


def _choose_winner_brain(
    local_brain: BrainManifest,
    cloud_brain: BrainManifest,
) -> str:
    return _choose_winner_from_datetimes(
        local_primary=local_brain.updated_at,
        cloud_primary=cloud_brain.updated_at,
        local_fallback=local_brain.created_at,
        cloud_fallback=cloud_brain.created_at,
    )


def _choose_winner_from_datetimes(
    *,
    local_primary: datetime,
    cloud_primary: datetime,
    local_fallback: datetime,
    cloud_fallback: datetime,
) -> str:
    if local_primary > cloud_primary:
        return "local"
    if cloud_primary > local_primary:
        return "cloud"
    if local_fallback > cloud_fallback:
        return "local"
    return "cloud"


def _rebuild_dedup_index(store: BaseStore) -> int:
    from neurocore.storage.in_memory import InMemoryStore
    from neurocore.storage.postgres_store import PostgresStore
    from neurocore.storage.router import RoutedStore
    from neurocore.storage.sqlite_store import SQLiteStore

    if isinstance(store, RoutedStore):
        return _rebuild_dedup_index(store.primary_store) + _rebuild_dedup_index(
            store.sealed_store
        )
    entries = _dedup_entries_for_store(store)
    if isinstance(store, InMemoryStore):
        store.dedup_index._entries.clear()
        for namespace, fingerprint, signature, item_id in entries:
            store.dedup_index.register(
                namespace=namespace,
                fingerprint=fingerprint,
                item_id=item_id,
                signature=signature,
            )
        return len(entries)
    if isinstance(store, SQLiteStore):
        with store._connect() as connection:
            connection.execute("DELETE FROM dedup_index")
            connection.executemany(
                "INSERT INTO dedup_index VALUES (?, ?, ?, ?)",
                entries,
            )
        return len(entries)
    if isinstance(store, PostgresStore):
        with store._connect() as connection:
            connection.execute("DELETE FROM dedup_index")
            for entry in entries:
                connection.execute(
                    "INSERT INTO dedup_index VALUES (%s, %s, %s, %s)",
                    entry,
                )
        return len(entries)
    raise TypeError(f"Dedup rebuild is unsupported for {type(store).__name__}")


def _dedup_entries_for_store(
    store: BaseStore,
) -> list[tuple[str, str, str, str]]:
    entries: dict[tuple[str, str, str], tuple[tuple[object, object, str], str]] = {}
    for record in store.list_records(include_archived=True):
        key = (record.namespace, record.content_fingerprint, _record_signature(record))
        sort_key = (record.updated_at, record.created_at, record.id)
        existing = entries.get(key)
        if existing is None or sort_key >= existing[0]:
            entries[key] = (sort_key, record.id)
    for document in store.list_documents(include_archived=True):
        key = (
            document.namespace,
            document.content_fingerprint,
            _document_signature(document),
        )
        sort_key = (document.updated_at, document.created_at, document.id)
        existing = entries.get(key)
        if existing is None or sort_key >= existing[0]:
            entries[key] = (sort_key, document.id)
    return [
        (namespace, fingerprint, signature, item_id)
        for (namespace, fingerprint, signature), (_, item_id) in sorted(entries.items())
    ]


def _build_parity_report(
    local_store: BaseStore,
    cloud_store: BaseStore,
    *,
    read_preference: str = "local",
) -> dict[str, object]:
    local_inventory = _collect_store_inventory(local_store)
    cloud_inventory = _collect_store_inventory(cloud_store)
    record_diff = _diff_signature_maps(
        local_inventory.record_signatures,
        cloud_inventory.record_signatures,
    )
    document_diff = _diff_signature_maps(
        local_inventory.document_graph_signatures,
        cloud_inventory.document_graph_signatures,
    )
    chunk_diff = _diff_signature_maps(
        local_inventory.chunk_signatures,
        cloud_inventory.chunk_signatures,
    )
    artifact_diff = _diff_signature_maps(
        local_inventory.artifact_signatures,
        cloud_inventory.artifact_signatures,
    )
    brain_diff = _diff_signature_maps(
        local_inventory.brain_signatures,
        cloud_inventory.brain_signatures,
    )
    audit_diff = _diff_sets(
        set(local_inventory.audit_events),
        set(cloud_inventory.audit_events),
    )
    record_dedup_diff = _diff_sets(
        local_inventory.record_dedup_entries,
        cloud_inventory.record_dedup_entries,
    )
    document_dedup_diff = _diff_sets(
        local_inventory.document_dedup_entries,
        cloud_inventory.document_dedup_entries,
    )

    missing_from_local = {
        "records": record_diff["missing_from_local"],
        "documents": document_diff["missing_from_local"],
        "chunks": chunk_diff["missing_from_local"],
        "artifacts": artifact_diff["missing_from_local"],
        "brains": brain_diff["missing_from_local"],
        "audit_events": audit_diff["missing_from_local"],
        "record_dedup_entries": record_dedup_diff["missing_from_local"],
        "document_dedup_entries": document_dedup_diff["missing_from_local"],
    }
    missing_from_cloud = {
        "records": record_diff["missing_from_cloud"],
        "documents": document_diff["missing_from_cloud"],
        "chunks": chunk_diff["missing_from_cloud"],
        "artifacts": artifact_diff["missing_from_cloud"],
        "brains": brain_diff["missing_from_cloud"],
        "audit_events": audit_diff["missing_from_cloud"],
        "record_dedup_entries": record_dedup_diff["missing_from_cloud"],
        "document_dedup_entries": document_dedup_diff["missing_from_cloud"],
    }
    conflict_counts = {
        "records": record_diff["conflicts"],
        "documents": document_diff["conflicts"],
        "chunks": chunk_diff["conflicts"],
        "artifacts": artifact_diff["conflicts"],
        "brains": brain_diff["conflicts"],
    }
    missing_from_local_total = sum(missing_from_local.values())
    missing_from_cloud_total = sum(missing_from_cloud.values())
    conflict_total = sum(conflict_counts.values())
    local_only_total = sum(
        (
            record_diff["local_only"],
            document_diff["local_only"],
            chunk_diff["local_only"],
            artifact_diff["local_only"],
            brain_diff["local_only"],
            audit_diff["local_only"],
            record_dedup_diff["local_only"],
            document_dedup_diff["local_only"],
        )
    )
    cloud_only_total = sum(
        (
            record_diff["cloud_only"],
            document_diff["cloud_only"],
            chunk_diff["cloud_only"],
            artifact_diff["cloud_only"],
            brain_diff["cloud_only"],
            audit_diff["cloud_only"],
            record_dedup_diff["cloud_only"],
            document_dedup_diff["cloud_only"],
        )
    )
    in_sync = (
        missing_from_local_total == 0
        and missing_from_cloud_total == 0
        and conflict_total == 0
    )
    bidirectional_divergence = conflict_total > 0 or (
        local_only_total > 0 and cloud_only_total > 0
    )
    destructive_repair_risk = bidirectional_divergence
    recommended_safe_action = (
        None
        if in_sync
        else (
            "reconcile_union"
            if destructive_repair_risk
            else _choose_repair_action(
                missing_from_local_total=missing_from_local_total,
                missing_from_cloud_total=missing_from_cloud_total,
                read_preference=read_preference,
            )
        )
    )
    repair_action = None if destructive_repair_risk else recommended_safe_action
    return {
        "in_sync": in_sync,
        "parity_state": "stored" if in_sync else "degraded",
        "missing_from_local": missing_from_local,
        "missing_from_cloud": missing_from_cloud,
        "missing_from_local_total": missing_from_local_total,
        "missing_from_cloud_total": missing_from_cloud_total,
        "compared": {
            "records": max(
                len(local_inventory.records),
                len(cloud_inventory.records),
            ),
            "documents": max(
                len(local_inventory.documents),
                len(cloud_inventory.documents),
            ),
            "chunks": max(
                len(local_inventory.chunks),
                len(cloud_inventory.chunks),
            ),
            "artifacts": max(
                len(local_inventory.artifacts),
                len(cloud_inventory.artifacts),
            ),
            "brains": max(
                len(local_inventory.brains),
                len(cloud_inventory.brains),
            ),
            "audit_events": max(
                len(local_inventory.audit_events),
                len(cloud_inventory.audit_events),
            ),
            "record_dedup_entries": max(
                len(local_inventory.record_dedup_entries),
                len(cloud_inventory.record_dedup_entries),
            ),
            "document_dedup_entries": max(
                len(local_inventory.document_dedup_entries),
                len(cloud_inventory.document_dedup_entries),
            ),
        },
        "conflict_counts": conflict_counts,
        "bidirectional_divergence": bidirectional_divergence,
        "destructive_repair_risk": destructive_repair_risk,
        "recommended_safe_action": recommended_safe_action,
        "repair_mode": (
            "none"
            if in_sync
            else ("union" if destructive_repair_risk else "directional")
        ),
        "repair_action": repair_action,
        "repair_basis": _repair_basis(
            missing_from_local_total=missing_from_local_total,
            missing_from_cloud_total=missing_from_cloud_total,
            read_preference=read_preference,
            conflict_total=conflict_total,
            bidirectional_divergence=bidirectional_divergence,
        ),
    }


def _choose_repair_action(
    *,
    missing_from_local_total: int,
    missing_from_cloud_total: int,
    read_preference: str,
) -> str | None:
    if missing_from_local_total == 0 and missing_from_cloud_total == 0:
        return None
    if missing_from_cloud_total and not missing_from_local_total:
        return "repair_cloud_from_local"
    if missing_from_local_total and not missing_from_cloud_total:
        return "repair_local_from_cloud"
    return (
        "repair_cloud_from_local"
        if read_preference == "local"
        else "repair_local_from_cloud"
    )


def _repair_basis(
    *,
    missing_from_local_total: int,
    missing_from_cloud_total: int,
    read_preference: str,
    conflict_total: int = 0,
    bidirectional_divergence: bool = False,
) -> str:
    if missing_from_local_total == 0 and missing_from_cloud_total == 0:
        return "in_sync"
    if conflict_total:
        return "conflicting_items"
    if bidirectional_divergence:
        return "bidirectional_divergence"
    if missing_from_cloud_total and not missing_from_local_total:
        return "cloud_missing_data"
    if missing_from_local_total and not missing_from_cloud_total:
        return "local_missing_data"
    return f"diverged_prefer_{read_preference}"


def _collect_store_inventory(store: BaseStore) -> StoreInventory:
    records = list(store.list_records(include_archived=True))
    documents = list(store.list_documents(include_archived=True))
    record_map: dict[str, MemoryRecord] = {}
    record_signatures: dict[str, tuple[object, ...]] = {}
    document_map: dict[str, MemoryDocument] = {}
    document_signatures: dict[str, tuple[object, ...]] = {}
    document_graph_signatures: dict[str, tuple[object, ...]] = {}
    document_chunks: dict[str, tuple[MemoryChunk, ...]] = {}
    chunk_map: dict[str, MemoryChunk] = {}
    chunk_signatures: dict[str, tuple[object, ...]] = {}
    artifact_map: dict[str, RetrievalArtifact] = {}
    artifact_signatures: dict[str, tuple[object, ...]] = {}
    brain_map: dict[str, BrainManifest] = {}
    brain_signatures: dict[str, tuple[object, ...]] = {}
    audit_events: dict[tuple[object, ...], dict[str, object]] = {}
    record_dedup_entries: set[tuple[object, ...]] = set()
    document_dedup_entries: set[tuple[object, ...]] = set()

    for record in records:
        record_map[record.id] = record
        record_signatures[record.id] = _record_state_signature(record)
        artifact = store.get_artifact(record.id)
        if artifact is not None:
            artifact_map[artifact.item_id] = artifact
            artifact_signatures[artifact.item_id] = _artifact_state_signature(artifact)
        if (
            store.find_duplicate(
                record.namespace,
                record.content_fingerprint,
                _record_signature(record),
            )
            == record.id
        ):
            record_dedup_entries.add(
                (
                    record.namespace,
                    record.content_fingerprint,
                    _record_signature(record),
                    record.id,
                )
            )

    for document in documents:
        document_map[document.id] = document
        document_signatures[document.id] = _document_state_signature(document)
        chunk_ids = store.get_document_chunk_ids(document.id)
        chunks: list[MemoryChunk] = []
        chunk_artifact_signatures: list[tuple[object, ...]] = []
        for chunk_id in chunk_ids:
            chunk = store.get_chunk(chunk_id)
            if chunk is None:
                continue
            chunks.append(chunk)
            chunk_map[chunk.id] = chunk
            chunk_signatures[chunk.id] = _chunk_state_signature(chunk)
            artifact = store.get_artifact(chunk.id)
            if artifact is not None:
                artifact_map[artifact.item_id] = artifact
                artifact_signatures[artifact.item_id] = _artifact_state_signature(
                    artifact
                )
                chunk_artifact_signatures.append(_artifact_state_signature(artifact))
        document_chunks[document.id] = tuple(chunks)
        document_graph_signatures[document.id] = (
            document_signatures[document.id],
            tuple(sorted(_chunk_state_signature(chunk) for chunk in chunks)),
            tuple(sorted(chunk_artifact_signatures)),
        )
        if (
            store.find_duplicate(
                document.namespace,
                document.content_fingerprint,
                _document_signature(document),
            )
            == document.id
        ):
            document_dedup_entries.add(
                (
                    document.namespace,
                    document.content_fingerprint,
                    _document_signature(document),
                    document.id,
                )
            )

    for brain in store.list_brains(include_archived=True):
        brain_map[brain.brain_id] = brain
        brain_signatures[brain.brain_id] = _brain_state_signature(brain)
    for event in store.list_audit_events(limit=1_000_000):
        audit_events[canonical_audit_event(event)] = event
    return StoreInventory(
        records=record_map,
        record_signatures=record_signatures,
        documents=document_map,
        document_signatures=document_signatures,
        document_graph_signatures=document_graph_signatures,
        document_chunks=document_chunks,
        chunks=chunk_map,
        chunk_signatures=chunk_signatures,
        artifacts=artifact_map,
        artifact_signatures=artifact_signatures,
        brains=brain_map,
        brain_signatures=brain_signatures,
        audit_events=audit_events,
        record_dedup_entries=record_dedup_entries,
        document_dedup_entries=document_dedup_entries,
    )


def _diff_signature_maps(
    local_state: dict[str, tuple[object, ...]],
    cloud_state: dict[str, tuple[object, ...]],
) -> dict[str, int]:
    local_ids = set(local_state)
    cloud_ids = set(cloud_state)
    local_only = local_ids - cloud_ids
    cloud_only = cloud_ids - local_ids
    conflicts = {
        item_id
        for item_id in (local_ids & cloud_ids)
        if local_state[item_id] != cloud_state[item_id]
    }
    return {
        "local_only": len(local_only),
        "cloud_only": len(cloud_only),
        "conflicts": len(conflicts),
        "missing_from_local": len(cloud_only) + len(conflicts),
        "missing_from_cloud": len(local_only) + len(conflicts),
    }


def _diff_sets(
    local_state: set[tuple[object, ...]],
    cloud_state: set[tuple[object, ...]],
) -> dict[str, int]:
    local_only = local_state - cloud_state
    cloud_only = cloud_state - local_state
    return {
        "local_only": len(local_only),
        "cloud_only": len(cloud_only),
        "conflicts": 0,
        "missing_from_local": len(cloud_only),
        "missing_from_cloud": len(local_only),
    }


def _record_state_signature(record: MemoryRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.namespace,
        record.bucket,
        record.content_fingerprint,
        record.content_format,
        record.source_type,
        record.sensitivity,
        record.title,
        record.external_id,
        record.idempotency_key,
        record.supersedes_id,
        record.created_at.isoformat(),
        record.updated_at.isoformat(),
        _iso_or_none(record.archived_at),
        _stable_json(record.tags),
        _stable_json(record.metadata),
    )


def _document_state_signature(document: MemoryDocument) -> tuple[object, ...]:
    return (
        document.id,
        document.namespace,
        document.bucket,
        document.title,
        document.raw_content,
        document.source_locator,
        document.source_type,
        document.sensitivity,
        document.content_fingerprint,
        document.created_at.isoformat(),
        document.updated_at.isoformat(),
        document.external_id,
        document.summary,
        document.supersedes_id,
        _iso_or_none(document.archived_at),
        _stable_json(document.tags),
        _stable_json(document.metadata),
    )


def _chunk_state_signature(chunk: MemoryChunk) -> tuple[object, ...]:
    return (
        chunk.id,
        chunk.document_id,
        chunk.namespace,
        chunk.bucket,
        chunk.ordinal,
        chunk.chunk_text,
        chunk.token_count,
        chunk.sensitivity,
        chunk.created_at.isoformat(),
        chunk.start_offset,
        chunk.end_offset,
        chunk.summary,
        _stable_json(chunk.metadata),
    )


def _artifact_state_signature(artifact: RetrievalArtifact) -> tuple[object, ...]:
    return (
        artifact.item_id,
        artifact.item_kind,
        artifact.document_id,
        artifact.namespace,
        artifact.bucket,
        artifact.sensitivity,
        artifact.text_hash,
        artifact.source_type,
        artifact.normalized_text,
        artifact.semantic_backend,
        artifact.semantic_model_name,
        artifact.semantic_status,
        _iso_or_none(artifact.archived_at),
        _stable_json(artifact.tags),
    )


def _brain_state_signature(brain: BrainManifest) -> tuple[object, ...]:
    return (
        brain.brain_id,
        brain.namespace,
        brain.display_name,
        brain.description,
        brain.status,
        brain.created_at.isoformat(),
        brain.updated_at.isoformat(),
        brain.owner,
        _stable_json(brain.tags),
        _stable_json(brain.default_allowed_buckets),
        _stable_json(brain.metadata),
    )


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _iso_or_none(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _record_signature(record: MemoryRecord) -> str:
    return f"record:{record.source_type}:{record.content_format}:{record.sensitivity}"


def _document_signature(document: MemoryDocument) -> str:
    return f"document:{document.source_type}:markdown:{document.sensitivity}"
