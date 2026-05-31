"""SQLite-backed storage backend for NeuroCore."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from neurocore.core import semantic as semantic_runtime
from neurocore.core.content_normalization import (
    compute_content_fingerprint,
    normalize_content,
)
from neurocore.core.models import (
    BrainManifest,
    MemoryChunk,
    MemoryDocument,
    MemoryRecord,
    RetrievalArtifact,
)
from neurocore.storage.base import BaseStore, Candidate, canonical_audit_event


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class SQLiteStore(BaseStore):
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_format TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT,
                    external_id TEXT,
                    idempotency_key TEXT,
                    supersedes_id TEXT,
                    archived_at TEXT
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    title TEXT NOT NULL,
                    raw_content TEXT,
                    source_locator TEXT,
                    source_type TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    external_id TEXT,
                    summary TEXT,
                    supersedes_id TEXT,
                    archived_at TEXT
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    chunk_text TEXT NOT NULL,
                    token_count INTEGER NOT NULL,
                    sensitivity TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    start_offset INTEGER,
                    end_offset INTEGER,
                    summary TEXT
                );
                CREATE TABLE IF NOT EXISTS dedup_index (
                    namespace TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    PRIMARY KEY (namespace, fingerprint, signature)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY,
                    actor TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    target_ids_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS brains (
                    brain_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    owner TEXT,
                    tags_json TEXT NOT NULL,
                    default_allowed_buckets_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS retrieval_artifacts (
                    item_id TEXT PRIMARY KEY,
                    item_kind TEXT NOT NULL,
                    document_id TEXT,
                    namespace TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    archived_at TEXT,
                    semantic_backend TEXT NOT NULL,
                    semantic_model_name TEXT,
                    semantic_status TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                );
            """)
            audit_event_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(audit_events)"
                ).fetchall()
            }
            if "details_json" not in audit_event_columns:
                connection.execute(
                    "ALTER TABLE audit_events ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute("""
                CREATE INDEX IF NOT EXISTS audit_events_timestamp_idx
                ON audit_events (timestamp DESC)
                """)

    def find_duplicate(
        self, namespace: str, fingerprint: str, signature: str
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT item_id FROM dedup_index
                WHERE namespace = ? AND fingerprint = ? AND signature = ?
                """,
                (namespace, fingerprint, signature),
            ).fetchone()
        return None if row is None else str(row["item_id"])

    def find_duplicates_bulk(
        self, entries: list[tuple[str, str, str]]
    ) -> list[str | None]:
        with self._connect() as connection:
            return [
                (
                    None
                    if (
                        row := connection.execute(
                            """
                            SELECT item_id FROM dedup_index
                            WHERE namespace = ? AND fingerprint = ? AND signature = ?
                            """,
                            (namespace, fingerprint, signature),
                        ).fetchone()
                    )
                    is None
                    else str(row["item_id"])
                )
                for namespace, fingerprint, signature in entries
            ]

    def save_record(self, record: MemoryRecord, signature: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN")
            self._save_record_connection(connection, record, signature)
            connection.commit()

    def save_records_bulk(self, entries: list[tuple[MemoryRecord, str]]) -> None:
        if not entries:
            return
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                for record, signature in entries:
                    self._save_record_connection(connection, record, signature)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def save_document(
        self, document: MemoryDocument, chunks: list[MemoryChunk], signature: str
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                self._save_document_connection(connection, document, chunks, signature)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def save_documents_bulk(
        self, entries: list[tuple[MemoryDocument, list[MemoryChunk], str]]
    ) -> None:
        if not entries:
            return
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                for document, chunks, signature in entries:
                    self._save_document_connection(
                        connection, document, chunks, signature
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _save_record_connection(
        self, connection: sqlite3.Connection, record: MemoryRecord, signature: str
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.namespace,
                record.bucket,
                record.content,
                record.content_format,
                record.source_type,
                record.sensitivity,
                json.dumps(record.metadata, sort_keys=True),
                json.dumps(list(record.tags)),
                record.content_fingerprint,
                _dt(record.created_at),
                _dt(record.updated_at),
                record.title,
                record.external_id,
                record.idempotency_key,
                record.supersedes_id,
                _dt(record.archived_at),
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO dedup_index VALUES (?, ?, ?, ?)
            """,
            (record.namespace, record.content_fingerprint, signature, record.id),
        )
        self._save_artifact(connection, _record_artifact(record))

    def _save_document_connection(
        self,
        connection: sqlite3.Connection,
        document: MemoryDocument,
        chunks: list[MemoryChunk],
        signature: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.id,
                document.namespace,
                document.bucket,
                document.title,
                document.raw_content,
                document.source_locator,
                document.source_type,
                document.sensitivity,
                json.dumps(document.metadata, sort_keys=True),
                json.dumps(list(document.tags)),
                document.content_fingerprint,
                _dt(document.created_at),
                _dt(document.updated_at),
                document.external_id,
                document.summary,
                document.supersedes_id,
                _dt(document.archived_at),
            ),
        )
        connection.execute(
            "DELETE FROM chunks WHERE document_id = ?",
            (document.id,),
        )
        for chunk in chunks:
            connection.execute(
                """
                INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.id,
                    chunk.document_id,
                    chunk.namespace,
                    chunk.bucket,
                    chunk.ordinal,
                    chunk.chunk_text,
                    chunk.token_count,
                    chunk.sensitivity,
                    json.dumps(chunk.metadata, sort_keys=True),
                    _dt(chunk.created_at),
                    chunk.start_offset,
                    chunk.end_offset,
                    chunk.summary,
                ),
            )
        connection.execute(
            """
            INSERT OR REPLACE INTO dedup_index VALUES (?, ?, ?, ?)
            """,
            (
                document.namespace,
                document.content_fingerprint,
                signature,
                document.id,
            ),
        )
        connection.execute(
            "DELETE FROM retrieval_artifacts WHERE document_id = ?",
            (document.id,),
        )
        for chunk in chunks:
            self._save_artifact(connection, _chunk_artifact(document, chunk))

    def get_record(
        self, item_id: str, include_archived: bool = False
    ) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM records WHERE id = ?",
                (item_id,),
            ).fetchone()
        if row is None:
            return None
        record = self._record_from_row(row)
        if record.archived_at and not include_archived:
            return None
        return record

    def get_document(
        self, item_id: str, include_archived: bool = False
    ) -> MemoryDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (item_id,),
            ).fetchone()
        if row is None:
            return None
        document = self._document_from_row(row)
        if document.archived_at and not include_archived:
            return None
        return document

    def get_chunk(self, item_id: str) -> MemoryChunk | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chunks WHERE id = ?",
                (item_id,),
            ).fetchone()
        return None if row is None else self._chunk_from_row(row)

    def get_document_chunk_ids(self, document_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM chunks WHERE document_id = ? ORDER BY ordinal",
                (document_id,),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def get_artifact(self, item_id: str) -> RetrievalArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM retrieval_artifacts WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        return None if row is None else self._artifact_from_row(row)

    def save_retrieval_artifact(self, artifact: RetrievalArtifact) -> None:
        with self._connect() as connection:
            self._save_artifact(connection, artifact)

    def list_records(self, include_archived: bool = False) -> list[MemoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM records ORDER BY created_at DESC"
            ).fetchall()
        records = [self._record_from_row(row) for row in rows]
        if include_archived:
            return records
        return [record for record in records if record.archived_at is None]

    def list_documents(self, include_archived: bool = False) -> list[MemoryDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        documents = [self._document_from_row(row) for row in rows]
        if include_archived:
            return documents
        return [document for document in documents if document.archived_at is None]

    def list_audit_events(self, limit: int = 20) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT actor, operation, target_ids_json, timestamp, outcome, details_json
                FROM audit_events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "actor": str(row["actor"]),
                "operation": str(row["operation"]),
                "target_ids": json.loads(str(row["target_ids_json"])),
                "timestamp": _parse_dt(str(row["timestamp"])),
                "outcome": str(row["outcome"]),
                "details": json.loads(str(row["details_json"] or "{}")),
            }
            for row in rows
        ]

    def save_brain(self, brain: BrainManifest) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO brains VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    brain.brain_id,
                    brain.namespace,
                    brain.display_name,
                    brain.description,
                    brain.status,
                    _dt(brain.created_at),
                    _dt(brain.updated_at),
                    brain.owner,
                    json.dumps(list(brain.tags)),
                    json.dumps(list(brain.default_allowed_buckets)),
                    json.dumps(brain.metadata, sort_keys=True),
                ),
            )

    def get_brain(
        self, brain_id: str, include_archived: bool = False
    ) -> BrainManifest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM brains WHERE brain_id = ?",
                (brain_id,),
            ).fetchone()
        if row is None:
            return None
        brain = self._brain_from_row(row)
        if brain.status == "archived" and not include_archived:
            return None
        return brain

    def list_brains(self, include_archived: bool = False) -> list[BrainManifest]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM brains ORDER BY updated_at DESC"
            ).fetchall()
        brains = [self._brain_from_row(row) for row in rows]
        if include_archived:
            return brains
        return [brain for brain in brains if brain.status != "archived"]

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
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM brains WHERE brain_id = ?",
                (brain_id,),
            )
        if cursor.rowcount == 0:
            raise KeyError(brain_id)

    def update_record(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryRecord:
        record = self.get_record(item_id, include_archived=True)
        if record is None:
            raise KeyError(item_id)
        updated = replace(
            record,
            title=patch.get("title", record.title),
            metadata=patch.get("metadata", record.metadata),
            tags=tuple(patch.get("tags", record.tags)),
            updated_at=datetime.now(UTC),
            supersedes_id=patch.get("supersedes_id", record.supersedes_id),
        )
        self.save_record(
            updated, signature=f"record:{updated.source_type}:{updated.content_format}"
        )
        return updated

    def update_document(
        self, item_id: str, patch: dict[str, object], mode: str
    ) -> MemoryDocument:
        document = self.get_document(item_id, include_archived=True)
        if document is None:
            raise KeyError(item_id)
        raw_chunks = patch.get("chunks")
        chunks = (
            [chunk for chunk in raw_chunks if isinstance(chunk, MemoryChunk)]
            if isinstance(raw_chunks, list)
            else [
                chunk
                for chunk_id in self.get_document_chunk_ids(item_id)
                if (chunk := self.get_chunk(chunk_id)) is not None
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
        self.save_document(
            updated,
            chunks,
            signature=self._document_signature(updated),
        )
        return updated

    def soft_delete(self, item_id: str, reason: str) -> None:
        timestamp = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN")
            if connection.execute(
                "UPDATE records SET archived_at = ?, updated_at = ? WHERE id = ?",
                (_dt(timestamp), _dt(timestamp), item_id),
            ).rowcount:
                connection.execute(
                    "UPDATE retrieval_artifacts SET archived_at = ?, indexed_at = ? WHERE item_id = ?",
                    (_dt(timestamp), _dt(timestamp), item_id),
                )
                connection.commit()
                return
            if connection.execute(
                "UPDATE documents SET archived_at = ?, updated_at = ? WHERE id = ?",
                (_dt(timestamp), _dt(timestamp), item_id),
            ).rowcount:
                connection.execute(
                    "UPDATE retrieval_artifacts SET archived_at = ?, indexed_at = ? WHERE document_id = ?",
                    (_dt(timestamp), _dt(timestamp), item_id),
                )
                connection.commit()
                return
            connection.rollback()
        raise KeyError(item_id)

    def hard_delete(self, item_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN")
            record = self.get_record(item_id, include_archived=True)
            if connection.execute(
                "DELETE FROM records WHERE id = ?", (item_id,)
            ).rowcount:
                connection.execute(
                    "DELETE FROM retrieval_artifacts WHERE item_id = ?",
                    (item_id,),
                )
                if record is not None:
                    connection.execute(
                        "DELETE FROM dedup_index WHERE namespace = ? AND fingerprint = ? AND item_id = ?",
                        (record.namespace, record.content_fingerprint, item_id),
                    )
                connection.commit()
                return
            document = self.get_document(item_id, include_archived=True)
            if connection.execute(
                "DELETE FROM documents WHERE id = ?", (item_id,)
            ).rowcount:
                connection.execute(
                    "DELETE FROM chunks WHERE document_id = ?", (item_id,)
                )
                connection.execute(
                    "DELETE FROM retrieval_artifacts WHERE document_id = ?",
                    (item_id,),
                )
                if document is not None:
                    connection.execute(
                        "DELETE FROM dedup_index WHERE namespace = ? AND fingerprint = ? AND item_id = ?",
                        (document.namespace, document.content_fingerprint, item_id),
                    )
                connection.commit()
                return
            connection.rollback()
        raise KeyError(item_id)

    def iter_candidates(
        self,
        namespace: str,
        allowed_buckets: tuple[str, ...],
        include_archived: bool = False,
    ) -> list[Candidate]:
        candidates: list[Candidate] = []
        with self._connect() as connection:
            artifact_rows = connection.execute(
                "SELECT * FROM retrieval_artifacts WHERE namespace = ? ORDER BY item_id",
                (namespace,),
            ).fetchall()
            for row in artifact_rows:
                artifact = self._artifact_from_row(row)
                if artifact.bucket not in allowed_buckets:
                    continue
                if artifact.archived_at and not include_archived:
                    continue
                if artifact.item_kind == "record":
                    record = self.get_record(
                        artifact.item_id, include_archived=include_archived
                    )
                    if record is None:
                        continue
                    candidates.append(
                        Candidate(kind="record", item=record, artifact=artifact)
                    )
                    continue

                chunk = self.get_chunk(artifact.item_id)
                document = self.get_document(
                    artifact.document_id or "",
                    include_archived=include_archived,
                )
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
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                for item_id in ids:
                    rebuilt = False
                    if scope in {"records", "all"}:
                        record = self.get_record(item_id, include_archived=True)
                        if record is not None:
                            self._save_artifact(
                                connection,
                                _record_artifact(
                                    record,
                                    semantic_backend=semantic_backend,
                                    semantic_model_name=semantic_model_name,
                                    semantic_status=status,
                                ),
                            )
                            rebuilt = True
                    if scope in {"documents", "all"}:
                        document = self.get_document(item_id, include_archived=True)
                        if document is not None:
                            connection.execute(
                                "DELETE FROM retrieval_artifacts WHERE document_id = ?",
                                (item_id,),
                            )
                            for chunk_id in self.get_document_chunk_ids(item_id):
                                chunk = self.get_chunk(chunk_id)
                                if chunk is None:
                                    continue
                                self._save_artifact(
                                    connection,
                                    _chunk_artifact(
                                        document,
                                        chunk,
                                        semantic_backend=semantic_backend,
                                        semantic_model_name=semantic_model_name,
                                        semantic_status=status,
                                    ),
                                )
                            rebuilt = True
                    if rebuilt:
                        processed += 1
                    else:
                        failed += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
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
            for existing in self.list_audit_events(limit=1_000_000)
        ):
            return
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, datetime):
            raise ValueError("audit event timestamp must be a datetime")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    actor,
                    operation,
                    target_ids_json,
                    timestamp,
                    outcome,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.get("actor") or ""),
                    str(event.get("operation") or ""),
                    json.dumps(list(event.get("target_ids") or [])),
                    _dt(timestamp),
                    str(event.get("outcome") or ""),
                    json.dumps(dict(event.get("details") or {})),
                ),
            )

    def clear_audit_events(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM audit_events")

    def has_item(self, item_id: str) -> bool:
        return (
            self.get_brain(item_id, include_archived=True) is not None
            or self.get_record(item_id, include_archived=True) is not None
            or self.get_document(item_id, include_archived=True) is not None
            or self.get_chunk(item_id) is not None
        )

    def _brain_from_row(self, row: sqlite3.Row) -> BrainManifest:
        return BrainManifest(
            brain_id=str(row["brain_id"]),
            namespace=str(row["namespace"]),
            display_name=str(row["display_name"]),
            description=str(row["description"]),
            status=str(row["status"]),
            created_at=_parse_dt(str(row["created_at"])),
            updated_at=_parse_dt(str(row["updated_at"])),
            owner=row["owner"],
            tags=tuple(json.loads(str(row["tags_json"]))),
            default_allowed_buckets=tuple(
                json.loads(str(row["default_allowed_buckets_json"]))
            ),
            metadata=json.loads(str(row["metadata_json"])),
        )

    def _record_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=str(row["id"]),
            namespace=str(row["namespace"]),
            bucket=str(row["bucket"]),
            content=str(row["content"]),
            content_format=str(row["content_format"]),
            source_type=str(row["source_type"]),
            sensitivity=str(row["sensitivity"]),
            metadata=json.loads(str(row["metadata_json"])),
            content_fingerprint=str(row["content_fingerprint"]),
            created_at=_parse_dt(str(row["created_at"])),
            updated_at=_parse_dt(str(row["updated_at"])),
            title=row["title"],
            tags=tuple(json.loads(str(row["tags_json"]))),
            external_id=row["external_id"],
            idempotency_key=row["idempotency_key"],
            supersedes_id=row["supersedes_id"],
            archived_at=_parse_dt(row["archived_at"]),
        )

    def _document_from_row(self, row: sqlite3.Row) -> MemoryDocument:
        return MemoryDocument(
            id=str(row["id"]),
            namespace=str(row["namespace"]),
            bucket=str(row["bucket"]),
            title=str(row["title"]),
            raw_content=row["raw_content"],
            source_locator=row["source_locator"],
            source_type=str(row["source_type"]),
            sensitivity=str(row["sensitivity"]),
            metadata=json.loads(str(row["metadata_json"])),
            content_fingerprint=str(row["content_fingerprint"]),
            created_at=_parse_dt(str(row["created_at"])),
            updated_at=_parse_dt(str(row["updated_at"])),
            external_id=row["external_id"],
            tags=tuple(json.loads(str(row["tags_json"]))),
            summary=row["summary"],
            supersedes_id=row["supersedes_id"],
            archived_at=_parse_dt(row["archived_at"]),
        )

    def _chunk_from_row(self, row: sqlite3.Row) -> MemoryChunk:
        return MemoryChunk(
            id=str(row["id"]),
            document_id=str(row["document_id"]),
            namespace=str(row["namespace"]),
            bucket=str(row["bucket"]),
            ordinal=int(row["ordinal"]),
            chunk_text=str(row["chunk_text"]),
            token_count=int(row["token_count"]),
            sensitivity=str(row["sensitivity"]),
            metadata=json.loads(str(row["metadata_json"])),
            created_at=_parse_dt(str(row["created_at"])),
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
            summary=row["summary"],
        )

    def _document_signature(self, document: MemoryDocument) -> str:
        return f"document:{document.source_type}:markdown:{document.sensitivity}"

    def _artifact_from_row(self, row: sqlite3.Row) -> RetrievalArtifact:
        return RetrievalArtifact(
            item_id=str(row["item_id"]),
            item_kind=str(row["item_kind"]),
            document_id=row["document_id"],
            namespace=str(row["namespace"]),
            bucket=str(row["bucket"]),
            sensitivity=str(row["sensitivity"]),
            source_type=str(row["source_type"]),
            tags=tuple(json.loads(str(row["tags_json"]))),
            normalized_text=str(row["normalized_text"]),
            text_hash=str(row["text_hash"]),
            created_at=_parse_dt(str(row["created_at"])),
            archived_at=_parse_dt(row["archived_at"]),
            semantic_backend=str(row["semantic_backend"]),
            semantic_model_name=row["semantic_model_name"],
            semantic_status=str(row["semantic_status"]),
            indexed_at=_parse_dt(str(row["indexed_at"])),
        )

    def _save_artifact(
        self, connection: sqlite3.Connection, artifact: RetrievalArtifact
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO retrieval_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.item_id,
                artifact.item_kind,
                artifact.document_id,
                artifact.namespace,
                artifact.bucket,
                artifact.sensitivity,
                artifact.source_type,
                json.dumps(list(artifact.tags)),
                artifact.normalized_text,
                artifact.text_hash,
                _dt(artifact.created_at),
                _dt(artifact.archived_at),
                artifact.semantic_backend,
                artifact.semantic_model_name,
                artifact.semantic_status,
                _dt(artifact.indexed_at),
            ),
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
