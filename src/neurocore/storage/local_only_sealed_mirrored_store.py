"""Mirror store that keeps sealed content local-only."""

from __future__ import annotations

from datetime import UTC, datetime
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
from neurocore.storage.mirrored_store import (
    _build_parity_report,
    _document_signature,
    _reconcile_union_store,
    _record_signature,
    _snapshot_bool,
    _snapshot_int,
    _snapshot_str,
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
        status_path: Path | None = None,
    ) -> None:
        if read_preference not in {"local", "cloud"}:
            raise ValueError("read_preference must be local or cloud")
        self.local_store = local_store
        self.cloud_primary_store = cloud_primary_store
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
            "sealed_mode": "local_only",
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
        local_match = self.local_store.find_duplicate(namespace, fingerprint, signature)
        cloud_match = self.cloud_primary_store.find_duplicate(
            namespace, fingerprint, signature
        )
        return local_match or cloud_match

    def save_record(self, record: MemoryRecord, signature: str) -> None:
        if record.sensitivity == "sealed":
            self.local_store.save_record(record, signature)
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return
        self._dual_write(
            "save record",
            lambda: self.cloud_primary_store.save_record(record, signature),
            lambda: self.local_store.save_record(record, signature),
            repair_clear_target=False,
        )

    def save_document(
        self, document: MemoryDocument, chunks: list[MemoryChunk], signature: str
    ) -> None:
        if document.sensitivity == "sealed":
            self.local_store.save_document(document, chunks, signature)
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return
        self._dual_write(
            "save document",
            lambda: self.cloud_primary_store.save_document(document, chunks, signature),
            lambda: self.local_store.save_document(document, chunks, signature),
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
        if artifact.sensitivity == "sealed":
            self.local_store.save_retrieval_artifact(artifact)
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return
        self._dual_write(
            "save retrieval artifact",
            lambda: self.cloud_primary_store.save_retrieval_artifact(artifact),
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
        self._dual_write(
            "save brain",
            lambda: self.cloud_primary_store.save_brain(brain),
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
            cloud_brain = self.cloud_primary_store.update_brain(brain_id, patch)
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
            archived_brain = self.cloud_primary_store.archive_brain(brain_id, reason)
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
            lambda: self.cloud_primary_store.delete_brain(brain_id),
            lambda: self.local_store.delete_brain(brain_id),
            repair_clear_target=True,
        )

    def update_record(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryRecord:
        if self._is_sealed_item(item_id):
            updated = self.local_store.update_record(item_id, patch, mode)
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return updated

        cloud_record: MemoryRecord | None = None

        def cloud_write() -> MemoryRecord:
            nonlocal cloud_record
            cloud_record = self.cloud_primary_store.update_record(item_id, patch, mode)
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
        if self._is_sealed_item(item_id):
            updated = self.local_store.update_document(item_id, patch, mode)
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return updated

        cloud_document: MemoryDocument | None = None

        def cloud_write() -> MemoryDocument:
            nonlocal cloud_document
            cloud_document = self.cloud_primary_store.update_document(
                item_id, patch, mode
            )
            return cloud_document

        def local_write() -> MemoryDocument:
            if self.local_store.get_document(item_id, include_archived=True) is None:
                if cloud_document is None:
                    raise KeyError(item_id)
                chunk_ids = self.cloud_primary_store.get_document_chunk_ids(item_id)
                chunks = [
                    chunk
                    for chunk_id in chunk_ids
                    if (chunk := self.cloud_primary_store.get_chunk(chunk_id))
                    is not None
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
        if self._is_sealed_item(item_id):
            self.local_store.soft_delete(item_id, reason)
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return
        self._dual_write(
            "soft delete item",
            lambda: self.cloud_primary_store.soft_delete(item_id, reason),
            lambda: self.local_store.soft_delete(item_id, reason),
            repair_clear_target=True,
        )

    def hard_delete(self, item_id: str) -> None:
        if self._is_sealed_item(item_id):
            self.local_store.hard_delete(item_id)
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return
        self._dual_write(
            "hard delete item",
            lambda: self.cloud_primary_store.hard_delete(item_id),
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
                self._record_operation_state(
                    persistence_state="stored",
                    parity_state="stored",
                    reconciliation_attempted=False,
                    reconciliation_direction=None,
                )
                continue

            cloud_result: tuple[int, int, list[str]] | None = None
            local_result: tuple[int, int, list[str]] | None = None

            def cloud_write() -> tuple[int, int, list[str]]:
                nonlocal cloud_result
                cloud_result = self.cloud_primary_store.reindex(
                    [item_id],
                    scope=scope,
                    semantic_backend=semantic_backend,
                    semantic_model_name=semantic_model_name,
                )
                return cloud_result

            def local_write() -> tuple[int, int, list[str]]:
                nonlocal local_result
                local_result = self.local_store.reindex(
                    [item_id],
                    scope=scope,
                    semantic_backend=semantic_backend,
                    semantic_model_name=semantic_model_name,
                )
                return local_result

            item_processed, item_failed, item_warnings = self._dual_write_result(
                "reindex",
                cloud_write,
                local_write,
                repair_clear_target=True,
            )
            processed += item_processed
            failed += item_failed
            warnings.extend(item_warnings)
            if cloud_result is not None and local_result is not None:
                warnings.extend(local_result[2])
        return processed, failed, list(dict.fromkeys(warnings))

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
            self._record_operation_state(
                persistence_state="stored",
                parity_state="stored",
                reconciliation_attempted=False,
                reconciliation_direction=None,
            )
            return
        self._dual_write(
            "import audit event",
            lambda: self.cloud_primary_store.import_audit_event(event),
            lambda: self._local_primary_store().import_audit_event(event),
            repair_clear_target=False,
        )

    def clear_audit_events(self) -> None:
        def local_write() -> None:
            self._local_primary_store().clear_audit_events()
            self._local_sealed_store().clear_audit_events()

        self._dual_write(
            "clear audit events",
            self.cloud_primary_store.clear_audit_events,
            local_write,
            repair_clear_target=True,
        )

    def has_item(self, item_id: str) -> bool:
        return self.local_store.has_item(item_id) or self.cloud_primary_store.has_item(
            item_id
        )

    def backfill_local_to_cloud(self) -> dict[str, int]:
        counts = _sync_store(
            self._local_primary_store(),
            self.cloud_primary_store,
            clear_target=False,
        )
        self._record_parity_check(
            _build_parity_report(
                self._local_primary_store(),
                self.cloud_primary_store,
                read_preference=self.read_preference,
            )
        )
        return counts

    def repair_local_from_cloud(self) -> dict[str, int]:
        counts = _sync_store(
            self.cloud_primary_store,
            self._local_primary_store(),
            clear_target=True,
        )
        self._clear_local_degradation()
        self._record_parity_check(
            _build_parity_report(
                self._local_primary_store(),
                self.cloud_primary_store,
                read_preference=self.read_preference,
            )
        )
        return counts

    def repair_cloud_from_local(self) -> dict[str, int]:
        counts = _sync_store(
            self._local_primary_store(),
            self.cloud_primary_store,
            clear_target=True,
        )
        self._clear_cloud_degradation()
        self._record_parity_check(
            _build_parity_report(
                self._local_primary_store(),
                self.cloud_primary_store,
                read_preference=self.read_preference,
            )
        )
        return counts

    def reconcile_union(self) -> dict[str, object]:
        counts = _reconcile_union_store(
            self._local_primary_store(),
            self.cloud_primary_store,
        )
        self._clear_local_degradation()
        self._clear_cloud_degradation()
        report = _build_parity_report(
            self._local_primary_store(),
            self.cloud_primary_store,
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
            self._local_primary_store(),
            self.cloud_primary_store,
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
                self._local_primary_store(),
                self.cloud_primary_store,
                clear_target=False,
            )
            self._clear_cloud_degradation()
        else:
            repair_counts = _sync_store(
                self.cloud_primary_store,
                self._local_primary_store(),
                clear_target=False,
            )
            self._clear_local_degradation()
        after = _build_parity_report(
            self._local_primary_store(),
            self.cloud_primary_store,
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
            return (("cloud", self.cloud_primary_store), ("local", self.local_store))
        return (("local", self.local_store), ("cloud", self.cloud_primary_store))

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
            repair_source = self._local_primary_store()
            repair_target = self.cloud_primary_store
        else:
            reconcile_direction = "repair_local_from_cloud"
            repair_source = self.cloud_primary_store
            repair_target = self._local_primary_store()

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
            self._local_primary_store(),
            self.cloud_primary_store,
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
