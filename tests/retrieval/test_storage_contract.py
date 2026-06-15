from datetime import UTC, datetime
import os
import sqlite3

import pytest

from neurocore.core.operator_state import load_mirror_status
from neurocore.core.models import (
    BrainManifest,
    MemoryChunk,
    MemoryDocument,
    MemoryRecord,
)
from neurocore.storage.in_memory import InMemoryStore
from neurocore.storage.local_only_sealed_mirrored_store import (
    LocalOnlySealedMirroredStore,
)
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage import postgres_store as postgres_store_module
from neurocore.storage.router import RoutedStore
from neurocore.storage.sqlite_store import SQLiteStore


def test_in_memory_store_can_create_fetch_update_and_tombstone_records():
    store = InMemoryStore()
    record = MemoryRecord(
        id="rec-1",
        namespace="project-alpha",
        bucket="research",
        content="Initial note",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={"author": "user"},
        content_fingerprint="fingerprint-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown")
    fetched = store.get_record("rec-1")
    artifact = store.get_artifact("rec-1")
    assert fetched is not None
    assert fetched.content == "Initial note"
    assert artifact is not None
    assert artifact.item_kind == "record"
    assert artifact.text_hash

    updated = store.update_record(
        "rec-1",
        patch={"title": "Updated title", "metadata": {"author": "reviewer"}},
        mode="in_place",
    )
    assert updated.title == "Updated title"
    assert updated.metadata["author"] == "reviewer"

    store.soft_delete("rec-1", reason="cleanup")
    assert store.get_record("rec-1", include_archived=True).archived_at is not None
    assert store.get_artifact("rec-1").archived_at is not None


def test_in_memory_store_tracks_audit_events():
    store = InMemoryStore()

    store.record_audit(
        actor="tester",
        operation="reindex",
        target_ids=["rec-1"],
        outcome="success",
        details={"scope": "records"},
    )

    assert len(store.audit_events) == 1
    event = store.audit_events[0]
    assert event["actor"] == "tester"
    assert event["operation"] == "reindex"
    assert event["details"] == {"scope": "records"}


def test_in_memory_store_supports_brain_lifecycle_round_trip():
    store = InMemoryStore()
    brain = BrainManifest(
        brain_id="brain-alpha",
        namespace="project-alpha",
        display_name="Project Alpha",
        description="Primary engagement memory",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        owner="analyst",
        tags=("alpha",),
        default_allowed_buckets=("research", "reports"),
        metadata={"source": "test"},
    )

    store.save_brain(brain)
    fetched = store.get_brain("brain-alpha")
    updated = store.update_brain(
        "brain-alpha",
        {"display_name": "Project Alpha Updated", "tags": ("alpha", "priority")},
    )
    archived = store.archive_brain("brain-alpha", reason="completed")

    assert fetched is not None
    assert fetched.namespace == "project-alpha"
    assert updated.display_name == "Project Alpha Updated"
    assert "priority" in updated.tags
    assert archived.status == "archived"
    assert store.list_brains(include_archived=False) == []
    assert len(store.list_brains(include_archived=True)) == 1


def test_in_memory_store_hides_soft_deleted_documents_by_default():
    store = InMemoryStore()
    document = MemoryDocument(
        id="doc-1",
        namespace="project-alpha",
        bucket="research",
        title="Design note",
        raw_content="Long design note",
        source_locator=None,
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fingerprint-doc",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    chunk = MemoryChunk(
        id="chunk-1",
        document_id="doc-1",
        namespace="project-alpha",
        bucket="research",
        ordinal=1,
        chunk_text="Long design note",
        token_count=3,
        sensitivity="standard",
        metadata={},
        created_at=datetime.now(UTC),
    )

    store.save_document(document, [chunk], signature="document:note:markdown")
    store.soft_delete("doc-1", reason="cleanup")

    assert store.get_document("doc-1") is None
    assert store.get_document("doc-1", include_archived=True) is not None
    assert store.get_artifact("chunk-1").archived_at is not None


def test_in_memory_store_tracks_document_chunk_artifacts():
    store = InMemoryStore()
    document = MemoryDocument(
        id="doc-artifact-1",
        namespace="project-alpha",
        bucket="research",
        title="Design note",
        raw_content="Long design note",
        source_locator=None,
        source_type="note",
        sensitivity="standard",
        metadata={"topic": "retrieval"},
        content_fingerprint="fingerprint-doc-artifact",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        tags=("alpha",),
    )
    chunk = MemoryChunk(
        id="chunk-artifact-1",
        document_id="doc-artifact-1",
        namespace="project-alpha",
        bucket="research",
        ordinal=1,
        chunk_text="Long design note",
        token_count=3,
        sensitivity="standard",
        metadata={"topic": "retrieval"},
        created_at=datetime.now(UTC),
    )

    store.save_document(document, [chunk], signature="document:note:markdown")

    artifact = store.get_artifact("chunk-artifact-1")

    assert artifact is not None
    assert artifact.item_kind == "chunk"
    assert artifact.document_id == "doc-artifact-1"
    assert artifact.source_type == "note"
    assert artifact.tags == ("alpha",)


def test_in_memory_store_replaces_removed_document_chunks_cleanly():
    store = InMemoryStore()
    original_document = MemoryDocument(
        id="doc-replace-1",
        namespace="project-alpha",
        bucket="research",
        title="Design note",
        raw_content="Long design note",
        source_locator=None,
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fingerprint-doc-replace",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    first_chunk = MemoryChunk(
        id="chunk-replace-1",
        document_id="doc-replace-1",
        namespace="project-alpha",
        bucket="research",
        ordinal=1,
        chunk_text="first",
        token_count=1,
        sensitivity="standard",
        metadata={},
        created_at=datetime.now(UTC),
    )
    second_chunk = MemoryChunk(
        id="chunk-replace-2",
        document_id="doc-replace-1",
        namespace="project-alpha",
        bucket="research",
        ordinal=2,
        chunk_text="second",
        token_count=1,
        sensitivity="standard",
        metadata={},
        created_at=datetime.now(UTC),
    )
    replacement_document = MemoryDocument(
        id="doc-replace-1",
        namespace="project-alpha",
        bucket="research",
        title="Design note",
        raw_content="Shortened design note",
        source_locator=None,
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fingerprint-doc-replace",
        created_at=original_document.created_at,
        updated_at=datetime.now(UTC),
    )

    store.save_document(
        original_document,
        [first_chunk, second_chunk],
        signature="document:note:markdown",
    )
    store.save_document(
        replacement_document,
        [first_chunk],
        signature="document:note:markdown",
    )

    assert store.get_chunk("chunk-replace-2") is None
    assert store.get_artifact("chunk-replace-2") is None


def test_sqlite_store_can_persist_and_reload_records(tmp_path):
    database_path = tmp_path / "neurocore.db"
    store = SQLiteStore(database_path)
    record = MemoryRecord(
        id="rec-sqlite-1",
        namespace="project-alpha",
        bucket="research",
        content="Persisted note",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={"author": "user"},
        content_fingerprint="fp-sqlite-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown")
    reloaded = SQLiteStore(database_path)

    fetched = reloaded.get_record("rec-sqlite-1")
    artifact = reloaded.get_artifact("rec-sqlite-1")

    assert fetched is not None
    assert fetched.content == "Persisted note"
    assert artifact is not None
    assert artifact.item_kind == "record"
    assert (
        reloaded.find_duplicate("project-alpha", "fp-sqlite-1", "record:note:markdown")
        == "rec-sqlite-1"
    )


def test_sqlite_store_enables_busy_timeout_and_wal_mode(tmp_path):
    database_path = tmp_path / "neurocore.db"
    store = SQLiteStore(database_path)

    with store._connect() as connection:
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]

    assert busy_timeout == 30_000
    assert journal_mode == "wal"
    assert synchronous == 1


def test_sqlite_store_persists_audit_event_details(tmp_path):
    database_path = tmp_path / "neurocore.db"
    store = SQLiteStore(database_path)

    store.record_audit(
        actor="tester",
        operation="article_rejection",
        target_ids=[],
        outcome="rejected",
        details={"canonical_url": "https://example.invalid/articles/ldap"},
    )

    reloaded = SQLiteStore(database_path)

    event = reloaded.list_audit_events(limit=1)[0]
    assert event["operation"] == "article_rejection"
    assert event["details"]["canonical_url"] == "https://example.invalid/articles/ldap"
    assert "event_id" not in event

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row[5]
            for row in connection.execute("PRAGMA table_info(audit_events)").fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(audit_events)").fetchall()
        }

    assert columns["event_id"] == 1
    assert "audit_events_timestamp_idx" in indexes


def test_sqlite_store_upgrades_legacy_audit_event_schema(tmp_path):
    database_path = tmp_path / "legacy-audit.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE audit_events (
                actor TEXT NOT NULL,
                operation TEXT NOT NULL,
                target_ids_json TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                outcome TEXT NOT NULL
            )
            """)
        connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
            ("tester", "legacy", "[]", datetime.now(UTC).isoformat(), "success"),
        )

    store = SQLiteStore(database_path)
    store.record_audit(
        actor="tester",
        operation="article_acceptance",
        target_ids=["doc-1"],
        outcome="accepted",
        details={"canonical_url": "https://example.invalid/articles/ldap"},
    )

    reloaded = SQLiteStore(database_path)
    events = reloaded.list_audit_events(limit=10)

    assert any(event["operation"] == "legacy" for event in events)
    upgraded = next(
        event for event in events if event["operation"] == "article_acceptance"
    )
    assert (
        upgraded["details"]["canonical_url"] == "https://example.invalid/articles/ldap"
    )


def test_postgres_store_audit_schema_adds_primary_key_and_timestamp_index():
    executed_sql: list[str] = []

    class FakeCursor:
        def execute(self, query: str, params=None):
            executed_sql.append(" ".join(query.split()))
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    store = postgres_store_module.PostgresStore.__new__(
        postgres_store_module.PostgresStore
    )
    store.database_url = "postgresql://example.invalid/neurocore"
    store._connect = lambda: FakeConnection()

    postgres_store_module.PostgresStore._ensure_schema(store)

    schema_sql = "\n".join(executed_sql)
    assert "event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY" in schema_sql
    assert "CREATE INDEX IF NOT EXISTS audit_events_timestamp_idx" in schema_sql


def test_postgres_store_audit_queries_use_explicit_columns():
    executed: list[tuple[str, tuple[object, ...] | None]] = []

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConnection:
        def execute(self, query: str, params=None):
            normalized = " ".join(query.split())
            executed.append((normalized, params))
            if normalized.startswith("SELECT actor, operation, target_ids_json"):
                return FakeResult(
                    [
                        {
                            "actor": "tester",
                            "operation": "reindex",
                            "target_ids_json": '["rec-1"]',
                            "timestamp": datetime.now(UTC),
                            "outcome": "success",
                            "details_json": '{"scope":"records"}',
                        }
                    ]
                )
            return FakeResult([])

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    store = postgres_store_module.PostgresStore.__new__(
        postgres_store_module.PostgresStore
    )
    store.database_url = "postgresql://example.invalid/neurocore"
    store._connect = lambda: FakeConnection()

    event = store.list_audit_events(limit=1)[0]
    store.import_audit_event(
        {
            "actor": "tester",
            "operation": "reindex",
            "target_ids": ["rec-1"],
            "timestamp": datetime.now(UTC),
            "outcome": "success",
            "details": {"scope": "records"},
        }
    )

    assert event["details"] == {"scope": "records"}
    assert "event_id" not in event
    assert executed[0][0].startswith(
        "SELECT actor, operation, target_ids_json, timestamp, outcome, details_json"
    )
    insert_sql = next(
        query
        for query, _params in executed
        if query.startswith("INSERT INTO audit_events")
    )
    assert "event_id" not in insert_sql
    assert (
        "actor, operation, target_ids_json, timestamp, outcome, details_json"
        in insert_sql
    )


def test_sqlite_store_hard_delete_clears_dedup_index(tmp_path):
    database_path = tmp_path / "neurocore.db"
    store = SQLiteStore(database_path)
    record = MemoryRecord(
        id="rec-sqlite-delete-1",
        namespace="project-alpha",
        bucket="research",
        content="Reusable note",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-sqlite-delete-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown")
    store.hard_delete(record.id)

    assert (
        store.find_duplicate(
            "project-alpha", "fp-sqlite-delete-1", "record:note:markdown"
        )
        is None
    )


def test_sqlite_store_persists_brain_lifecycle(tmp_path):
    database_path = tmp_path / "neurocore.db"
    store = SQLiteStore(database_path)
    brain = BrainManifest(
        brain_id="brain-sqlite",
        namespace="project-sqlite",
        display_name="Project SQLite",
        description="SQLite-backed brain",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        owner="analyst",
        tags=("sqlite",),
        default_allowed_buckets=("research", "reports"),
        metadata={"source": "sqlite-test"},
    )

    store.save_brain(brain)
    reloaded = SQLiteStore(database_path)
    fetched = reloaded.get_brain("brain-sqlite")
    updated = reloaded.update_brain(
        "brain-sqlite",
        {"description": "Updated SQLite-backed brain"},
    )
    archived = reloaded.archive_brain("brain-sqlite", reason="complete")

    assert fetched is not None
    assert fetched.namespace == "project-sqlite"
    assert updated.description == "Updated SQLite-backed brain"
    assert archived.status == "archived"
    assert reloaded.list_brains(include_archived=False) == []
    assert len(reloaded.list_brains(include_archived=True)) == 1


def test_routed_store_sends_sealed_content_to_the_sealed_backend(tmp_path):
    primary = SQLiteStore(tmp_path / "primary.db")
    sealed = SQLiteStore(tmp_path / "sealed.db")
    store = RoutedStore(primary_store=primary, sealed_store=sealed)
    sealed_record = MemoryRecord(
        id="rec-sealed-1",
        namespace="project-alpha",
        bucket="ops",
        content="Sealed note",
        content_format="markdown",
        source_type="note",
        sensitivity="sealed",
        metadata={},
        content_fingerprint="fp-sealed-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(sealed_record, signature="record:note:markdown")

    assert primary.get_record("rec-sealed-1") is None
    assert sealed.get_record("rec-sealed-1") is not None
    assert primary.get_artifact("rec-sealed-1") is None
    assert sealed.get_artifact("rec-sealed-1") is not None


def test_mirrored_store_reads_local_first_and_falls_back_to_cloud():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-cloud-only-1",
        namespace="project-alpha",
        bucket="research",
        content="Cloud only note",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-cloud-only-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    cloud.save_record(record, signature="record:note:markdown:standard")

    fetched = store.get_record("rec-cloud-only-1")

    assert fetched is not None
    assert fetched.content == "Cloud only note"


def test_mirrored_store_partial_write_marks_local_degradation():
    class FailingLocalStore(InMemoryStore):
        def save_record(self, record: MemoryRecord, signature: str) -> None:
            raise RuntimeError("disk unavailable")

    local = RoutedStore(primary_store=FailingLocalStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-mirror-1",
        namespace="project-alpha",
        bucket="research",
        content="Mirror me",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-mirror-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown:standard")

    assert cloud.get_record("rec-mirror-1") is not None
    assert local.get_record("rec-mirror-1") is None
    status = store.pop_operation_status()
    assert status["persistence_state"] == "partial"
    assert status["parity_state"] == "degraded"
    assert status["reconciliation_attempted"] is True
    assert status["reconciliation_direction"] == "repair_local_from_cloud"
    assert store.mirror_status()["local_degraded"] is True
    assert store.pop_warnings()


def test_mirrored_store_partial_write_marks_cloud_degradation():
    class FailingCloudStore(InMemoryStore):
        def save_record(self, record: MemoryRecord, signature: str) -> None:
            raise RuntimeError("pooler unavailable")

    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=FailingCloudStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-cloud-degraded-1",
        namespace="project-alpha",
        bucket="research",
        content="Mirror me locally",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-cloud-degraded-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown:standard")

    assert local.get_record("rec-cloud-degraded-1") is not None
    assert cloud.get_record("rec-cloud-degraded-1") is None
    status = store.pop_operation_status()
    assert status["persistence_state"] == "partial"
    assert status["parity_state"] == "degraded"
    assert status["reconciliation_attempted"] is True
    assert status["reconciliation_direction"] == "repair_cloud_from_local"
    assert store.mirror_status()["cloud_degraded"] is True
    assert store.pop_warnings()


def test_mirrored_store_auto_repairs_local_after_single_write_failure():
    class FlakyLocalStore(InMemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self._failed_once = False

        def save_record(self, record: MemoryRecord, signature: str) -> None:
            if not self._failed_once:
                self._failed_once = True
                raise RuntimeError("disk unavailable once")
            super().save_record(record, signature)

    local = RoutedStore(primary_store=FlakyLocalStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-auto-repair-local",
        namespace="project-alpha",
        bucket="research",
        content="auto repair local",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-auto-repair-local",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown:standard")

    assert cloud.get_record(record.id) is not None
    assert local.get_record(record.id) is not None
    status = store.pop_operation_status()
    assert status["persistence_state"] == "partial"
    assert status["parity_state"] == "stored"
    assert status["reconciliation_attempted"] is True
    assert status["reconciliation_direction"] == "repair_local_from_cloud"
    assert store.mirror_status()["local_degraded"] is False
    assert store.mirror_status()["parity_verified"] is True


def test_mirrored_store_auto_repairs_cloud_after_single_write_failure():
    class FlakyCloudStore(InMemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self._failed_once = False

        def save_record(self, record: MemoryRecord, signature: str) -> None:
            if not self._failed_once:
                self._failed_once = True
                raise RuntimeError("pooler unavailable once")
            super().save_record(record, signature)

    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=FlakyCloudStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-auto-repair-cloud",
        namespace="project-alpha",
        bucket="research",
        content="auto repair cloud",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-auto-repair-cloud",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown:standard")

    assert local.get_record(record.id) is not None
    assert cloud.get_record(record.id) is not None
    status = store.pop_operation_status()
    assert status["persistence_state"] == "partial"
    assert status["parity_state"] == "stored"
    assert status["reconciliation_attempted"] is True
    assert status["reconciliation_direction"] == "repair_cloud_from_local"
    assert store.mirror_status()["cloud_degraded"] is False
    assert store.mirror_status()["parity_verified"] is True


def test_mirrored_store_auto_repairs_destructive_mutation_from_successful_side():
    class FlakyCloudStore(InMemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self._failed_once = False

        def soft_delete(self, item_id: str, reason: str) -> None:
            if not self._failed_once:
                self._failed_once = True
                raise RuntimeError("soft delete failed once")
            super().soft_delete(item_id, reason)

    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=FlakyCloudStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-soft-delete-auto-repair",
        namespace="project-alpha",
        bucket="research",
        content="delete me",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-soft-delete-auto-repair",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    local.save_record(record, signature="record:note:markdown:standard")
    cloud.save_record(record, signature="record:note:markdown:standard")

    store.soft_delete(record.id, reason="cleanup")

    local_record = local.get_record(record.id, include_archived=True)
    cloud_record = cloud.get_record(record.id, include_archived=True)
    assert local_record is not None and local_record.archived_at is not None
    assert cloud_record is not None and cloud_record.archived_at is not None
    status = store.pop_operation_status()
    assert status["parity_state"] == "stored"
    assert status["reconciliation_direction"] == "repair_cloud_from_local"


def test_mirrored_store_verify_parity_prefers_cloud_when_read_preference_is_cloud():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="cloud")

    local.save_record(
        MemoryRecord(
            id="rec-local-only",
            namespace="project-alpha",
            bucket="research",
            content="local only",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="fp-local-only",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        signature="record:note:markdown:standard",
    )
    cloud.save_record(
        MemoryRecord(
            id="rec-cloud-only",
            namespace="project-alpha",
            bucket="research",
            content="cloud only",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="fp-cloud-only",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        signature="record:note:markdown:standard",
    )

    parity = store.verify_parity()

    assert parity["repair_basis"] == "bidirectional_divergence"
    assert parity["destructive_repair_risk"] is True
    assert parity["recommended_safe_action"] == "reconcile_union"
    assert parity["repair_action"] is None
    assert parity["in_sync_after"] is False
    assert local.get_record("rec-cloud-only") is None
    assert local.get_record("rec-local-only") is not None
    last_check = store.mirror_status()["last_parity_check"]
    assert isinstance(last_check, str)
    assert "T" in last_check
    assert last_check not in {"stored", "degraded"}


def test_mirrored_store_reconcile_union_merges_bidirectional_records():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")

    local.save_record(
        MemoryRecord(
            id="rec-union-local",
            namespace="project-alpha",
            bucket="research",
            content="local only",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="fp-union-local",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        signature="record:note:markdown:standard",
    )
    cloud.save_record(
        MemoryRecord(
            id="rec-union-cloud",
            namespace="project-alpha",
            bucket="research",
            content="cloud only",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="fp-union-cloud",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        signature="record:note:markdown:standard",
    )

    result = store.reconcile_union()

    assert result["repair_mode"] == "union"
    assert result["in_sync_after"] is True
    assert result["counts"]["copied_to_local"]["records"] == 1
    assert result["counts"]["copied_to_cloud"]["records"] == 1
    assert local.get_record("rec-union-cloud") is not None
    assert cloud.get_record("rec-union-local") is not None


def test_mirrored_store_parity_metadata_survives_fresh_store_instance(tmp_path):
    status_path = tmp_path / "mirror-status.json"
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    first_store = MirroredStore(
        local_store=local,
        cloud_store=cloud,
        read_preference="local",
        status_path=status_path,
    )

    local.save_record(
        MemoryRecord(
            id="rec-persisted-parity",
            namespace="project-alpha",
            bucket="research",
            content="persist mirror parity",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="fp-persisted-parity",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        signature="record:note:markdown:standard",
    )
    first_parity = first_store.verify_parity()
    second_store = MirroredStore(
        local_store=local,
        cloud_store=cloud,
        read_preference="local",
        status_path=status_path,
    )

    status = second_store.mirror_status()

    assert first_parity["in_sync_after"] is True
    assert status["parity_verified"] is True
    assert status["last_parity_check"] == first_store.mirror_status()["last_parity_check"]
    assert status["last_reconciliation_direction"] == "repair_cloud_from_local"
    assert status["last_reconciliation_outcome"] == "success"


def test_local_only_sealed_parity_metadata_survives_fresh_store_instance(tmp_path):
    status_path = tmp_path / "mirror-status.json"
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = InMemoryStore()
    first_store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
        status_path=status_path,
    )

    local.primary_store.save_record(
        MemoryRecord(
            id="rec-local-only-persisted-parity",
            namespace="project-alpha",
            bucket="research",
            content="persist local-only parity",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="fp-local-only-persisted-parity",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        signature="record:note:markdown:standard",
    )
    first_parity = first_store.verify_parity()
    second_store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
        status_path=status_path,
    )

    status = second_store.mirror_status()

    assert first_parity["in_sync_after"] is True
    assert status["parity_verified"] is True
    assert status["last_parity_check"] == first_store.mirror_status()["last_parity_check"]
    assert status["last_reconciliation_direction"] == "repair_cloud_from_local"
    assert status["last_reconciliation_outcome"] == "success"


def test_mirrored_store_preserves_newer_sync_snapshot_during_live_writes(tmp_path):
    status_path = tmp_path / "mirror-status.json"
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    stale_store = MirroredStore(
        local_store=local,
        cloud_store=cloud,
        read_preference="local",
        status_path=status_path,
    )
    sync_store = MirroredStore(
        local_store=local,
        cloud_store=cloud,
        read_preference="local",
        status_path=status_path,
    )

    sync_store.mark_sync_started("verify_parity")
    stale_store._record_operation_state(
        persistence_state="stored",
        parity_state="stored",
        reconciliation_attempted=False,
        reconciliation_direction=None,
    )

    snapshot = load_mirror_status(status_path)

    assert snapshot["last_sync_action"] == "verify_parity"
    assert snapshot["last_sync_status"] == "running"
    assert snapshot["last_sync_pid"] == os.getpid()


def test_local_only_sealed_preserves_newer_sync_snapshot_during_live_writes(tmp_path):
    status_path = tmp_path / "mirror-status.json"
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = InMemoryStore()
    stale_store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
        status_path=status_path,
    )
    sync_store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
        status_path=status_path,
    )

    sync_store.mark_sync_started("verify_parity")
    stale_store._record_operation_state(
        persistence_state="stored",
        parity_state="stored",
        reconciliation_attempted=False,
        reconciliation_direction=None,
    )

    snapshot = load_mirror_status(status_path)

    assert snapshot["last_sync_action"] == "verify_parity"
    assert snapshot["last_sync_status"] == "running"
    assert snapshot["last_sync_pid"] == os.getpid()


def test_mirrored_store_reconcile_union_resolves_newest_record_conflict():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    created_at = datetime.now(UTC)
    local.save_record(
        MemoryRecord(
            id="rec-conflict",
            namespace="project-alpha",
            bucket="research",
            content="older local copy",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={"origin": "local"},
            content_fingerprint="fp-conflict-local",
            created_at=created_at,
            updated_at=created_at,
        ),
        signature="record:note:markdown:standard",
    )
    cloud.save_record(
        MemoryRecord(
            id="rec-conflict",
            namespace="project-alpha",
            bucket="research",
            content="newer cloud copy",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={"origin": "cloud"},
            content_fingerprint="fp-conflict-cloud",
            created_at=created_at,
            updated_at=created_at.replace(microsecond=created_at.microsecond + 1),
        ),
        signature="record:note:markdown:standard",
    )

    result = store.reconcile_union()

    assert result["counts"]["resolved_conflicts"]["records"] == 1
    reconciled = local.get_record("rec-conflict")
    assert reconciled is not None
    assert reconciled.content == "newer cloud copy"
    assert cloud.get_record("rec-conflict").content == "newer cloud copy"


def test_mirrored_store_reconcile_union_resolves_equal_timestamp_conflict_to_cloud():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    timestamp = datetime.now(UTC)
    local.save_record(
        MemoryRecord(
            id="rec-tie",
            namespace="project-alpha",
            bucket="research",
            content="local tie",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="fp-tie-local",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        signature="record:note:markdown:standard",
    )
    cloud.save_record(
        MemoryRecord(
            id="rec-tie",
            namespace="project-alpha",
            bucket="research",
            content="cloud tie",
            content_format="markdown",
            source_type="note",
            sensitivity="standard",
            metadata={},
            content_fingerprint="fp-tie-cloud",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        signature="record:note:markdown:standard",
    )

    result = store.reconcile_union()

    assert result["counts"]["resolved_conflicts"]["records"] == 1
    assert local.get_record("rec-tie").content == "cloud tie"


def test_mirrored_store_reconcile_union_syncs_document_graph_and_chunk_artifacts():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    created_at = datetime.now(UTC)
    local_document = MemoryDocument(
        id="doc-union",
        namespace="project-alpha",
        bucket="research",
        title="Older local doc",
        raw_content="alpha",
        source_locator=None,
        source_type="note",
        sensitivity="standard",
        metadata={"version": "local"},
        content_fingerprint="fp-doc-union-local",
        created_at=created_at,
        updated_at=created_at,
    )
    local_chunk = MemoryChunk(
        id="chunk-union-local",
        document_id="doc-union",
        namespace="project-alpha",
        bucket="research",
        ordinal=1,
        chunk_text="alpha",
        token_count=1,
        sensitivity="standard",
        metadata={},
        created_at=created_at,
    )
    cloud_document = MemoryDocument(
        id="doc-union",
        namespace="project-alpha",
        bucket="research",
        title="Newer cloud doc",
        raw_content="beta gamma",
        source_locator=None,
        source_type="note",
        sensitivity="standard",
        metadata={"version": "cloud"},
        content_fingerprint="fp-doc-union-cloud",
        created_at=created_at,
        updated_at=created_at.replace(microsecond=created_at.microsecond + 1),
    )
    cloud_chunk = MemoryChunk(
        id="chunk-union-cloud",
        document_id="doc-union",
        namespace="project-alpha",
        bucket="research",
        ordinal=1,
        chunk_text="beta gamma",
        token_count=2,
        sensitivity="standard",
        metadata={},
        created_at=created_at.replace(microsecond=created_at.microsecond + 1),
    )
    local.save_document(
        local_document,
        [local_chunk],
        signature="document:note:markdown:standard",
    )
    cloud.save_document(
        cloud_document,
        [cloud_chunk],
        signature="document:note:markdown:standard",
    )

    result = store.reconcile_union()

    assert result["counts"]["resolved_conflicts"]["documents"] == 1
    synced_document = local.get_document("doc-union")
    assert synced_document is not None
    assert synced_document.title == "Newer cloud doc"
    assert local.get_chunk("chunk-union-cloud") is not None
    assert local.get_chunk("chunk-union-local") is None
    assert local.get_artifact("chunk-union-cloud") is not None
    assert result["counts"]["rebuilt_dedup_entries"]["local"] >= 1


def test_mirrored_store_raises_when_both_backends_fail():
    class FailingStore(InMemoryStore):
        def save_record(self, record: MemoryRecord, signature: str) -> None:
            raise RuntimeError("write failed")

    local = RoutedStore(primary_store=FailingStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=FailingStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-both-fail-1",
        namespace="project-alpha",
        bucket="research",
        content="fail everywhere",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-both-fail-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.raises(RuntimeError, match="both cloud and local backends"):
        store.save_record(record, signature="record:note:markdown:standard")


def test_mirrored_store_can_backfill_and_repair():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-sync-1",
        namespace="project-alpha",
        bucket="research",
        content="Sync note",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-sync-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    brain = BrainManifest(
        brain_id="brain-sync",
        namespace="project-alpha",
        display_name="Project Alpha",
        description="Synced brain",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        owner="analyst",
        tags=("alpha",),
        default_allowed_buckets=("research",),
        metadata={"source": "test"},
    )

    local.save_record(record, signature="record:note:markdown:standard")
    local.save_brain(brain)
    local.import_audit_event(
        {
            "actor": "tester",
            "operation": "capture",
            "target_ids": ["rec-sync-1"],
            "timestamp": datetime.now(UTC),
            "outcome": "success",
        }
    )

    backfill = store.backfill_local_to_cloud()
    assert backfill["records"] == 1
    assert cloud.get_record("rec-sync-1") is not None
    assert cloud.get_brain("brain-sync") is not None

    local.hard_delete("rec-sync-1")
    repair = store.repair_local_from_cloud()
    assert repair["records"] == 1
    assert local.get_record("rec-sync-1") is not None


def test_mirrored_store_can_repair_cloud_from_local():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    record = MemoryRecord(
        id="rec-repair-cloud-1",
        namespace="project-alpha",
        bucket="research",
        content="repair cloud",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-repair-cloud-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    local.save_record(record, signature="record:note:markdown:standard")
    repair = store.repair_cloud_from_local()

    assert repair["records"] == 1
    assert cloud.get_record("rec-repair-cloud-1") is not None


def test_mirrored_store_verify_parity_repairs_one_sided_drift_without_clearing_target():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    shared = MemoryRecord(
        id="rec-shared",
        namespace="project-alpha",
        bucket="research",
        content="shared",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-shared",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    missing = MemoryRecord(
        id="rec-missing-cloud",
        namespace="project-alpha",
        bucket="research",
        content="missing cloud",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-missing-cloud",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    local.save_record(shared, signature="record:note:markdown:standard")
    cloud.save_record(shared, signature="record:note:markdown:standard")
    local.save_record(missing, signature="record:note:markdown:standard")

    parity = store.verify_parity()

    assert parity["repair_action"] == "repair_cloud_from_local"
    assert parity["in_sync_after"] is True
    assert parity["repair_counts"]["cleared_records"] == 0
    assert cloud.get_record("rec-shared") is not None
    assert cloud.get_record("rec-missing-cloud") is not None


def test_local_only_sealed_mirror_keeps_sealed_records_local():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = InMemoryStore()
    store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
    )
    sealed_record = MemoryRecord(
        id="rec-sealed-local-only",
        namespace="project-alpha",
        bucket="ops",
        content="Local only sealed note",
        content_format="markdown",
        source_type="note",
        sensitivity="sealed",
        metadata={},
        content_fingerprint="fp-sealed-local-only",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(sealed_record, signature="record:note:markdown:sealed")

    assert local.sealed_store.get_record("rec-sealed-local-only") is not None
    assert cloud.get_record("rec-sealed-local-only") is None
    assert cloud.get_artifact("rec-sealed-local-only") is None


def test_local_only_sealed_mirror_still_cloud_mirrors_non_sealed_records():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = InMemoryStore()
    store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
    )
    record = MemoryRecord(
        id="rec-local-only-mirror-1",
        namespace="project-alpha",
        bucket="research",
        content="Mirror me to cloud",
        content_format="markdown",
        source_type="note",
        sensitivity="restricted",
        metadata={},
        content_fingerprint="fp-local-only-mirror-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown:restricted")

    assert local.primary_store.get_record("rec-local-only-mirror-1") is not None
    assert cloud.get_record("rec-local-only-mirror-1") is not None


def test_local_only_sealed_mirror_marks_cloud_degradation_on_partial_write():
    class FailingCloudStore(InMemoryStore):
        def save_record(self, record: MemoryRecord, signature: str) -> None:
            raise RuntimeError("cloud unavailable")

    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = FailingCloudStore()
    store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
    )
    record = MemoryRecord(
        id="rec-local-only-cloud-fail",
        namespace="project-alpha",
        bucket="research",
        content="local survives",
        content_format="markdown",
        source_type="note",
        sensitivity="restricted",
        metadata={},
        content_fingerprint="fp-local-only-cloud-fail",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    store.save_record(record, signature="record:note:markdown:restricted")

    assert local.primary_store.get_record("rec-local-only-cloud-fail") is not None
    assert cloud.get_record("rec-local-only-cloud-fail") is None
    status = store.pop_operation_status()
    assert status["persistence_state"] == "partial"
    assert status["parity_state"] == "degraded"
    assert status["reconciliation_attempted"] is True
    assert status["reconciliation_direction"] == "repair_cloud_from_local"
    assert store.mirror_status()["cloud_degraded"] is True


def test_local_only_sealed_mirror_backfill_skips_sealed_content_and_audit():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = InMemoryStore()
    store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
    )
    standard_record = MemoryRecord(
        id="rec-standard-backfill",
        namespace="project-alpha",
        bucket="research",
        content="Cloud visible note",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-standard-backfill",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    sealed_record = MemoryRecord(
        id="rec-sealed-backfill",
        namespace="project-alpha",
        bucket="ops",
        content="Should stay local",
        content_format="markdown",
        source_type="note",
        sensitivity="sealed",
        metadata={},
        content_fingerprint="fp-sealed-backfill",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    local.save_record(standard_record, signature="record:note:markdown:standard")
    local.save_record(sealed_record, signature="record:note:markdown:sealed")
    store.record_audit(
        actor="tester",
        operation="update",
        target_ids=["rec-sealed-backfill"],
        outcome="success",
    )

    counts = store.backfill_local_to_cloud()

    assert counts["records"] == 1
    assert cloud.get_record("rec-standard-backfill") is not None
    assert cloud.get_record("rec-sealed-backfill") is None
    assert cloud.list_audit_events(limit=100) == []


def test_local_only_sealed_mirror_repair_preserves_local_sealed_data():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = InMemoryStore()
    store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
    )
    standard_record = MemoryRecord(
        id="rec-standard-repair",
        namespace="project-alpha",
        bucket="research",
        content="Repair from cloud",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-standard-repair",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    sealed_record = MemoryRecord(
        id="rec-sealed-repair",
        namespace="project-alpha",
        bucket="ops",
        content="Remain sealed local",
        content_format="markdown",
        source_type="note",
        sensitivity="sealed",
        metadata={},
        content_fingerprint="fp-sealed-repair",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    cloud.save_record(standard_record, signature="record:note:markdown:standard")
    local.sealed_store.save_record(
        sealed_record,
        signature="record:note:markdown:sealed",
    )

    counts = store.repair_local_from_cloud()

    assert counts["records"] == 1
    assert local.primary_store.get_record("rec-standard-repair") is not None
    assert local.sealed_store.get_record("rec-sealed-repair") is not None


def test_local_only_sealed_verify_parity_repairs_one_sided_drift_without_clearing_target():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = InMemoryStore()
    store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
    )
    shared = MemoryRecord(
        id="rec-local-only-shared",
        namespace="project-alpha",
        bucket="research",
        content="shared",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-local-only-shared",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    missing = MemoryRecord(
        id="rec-local-only-missing-cloud",
        namespace="project-alpha",
        bucket="research",
        content="missing cloud",
        content_format="markdown",
        source_type="note",
        sensitivity="restricted",
        metadata={},
        content_fingerprint="fp-local-only-missing-cloud",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    local.primary_store.save_record(shared, signature="record:note:markdown:standard")
    cloud.save_record(shared, signature="record:note:markdown:standard")
    local.primary_store.save_record(
        missing, signature="record:note:markdown:restricted"
    )

    parity = store.verify_parity()

    assert parity["repair_action"] == "repair_cloud_from_local"
    assert parity["in_sync_after"] is True
    assert parity["repair_counts"]["cleared_records"] == 0
    assert cloud.get_record("rec-local-only-shared") is not None
    assert cloud.get_record("rec-local-only-missing-cloud") is not None


def test_local_only_sealed_mirror_reconcile_union_only_merges_primary_data():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = InMemoryStore()
    store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud,
        read_preference="local",
    )
    local_primary = MemoryRecord(
        id="rec-local-primary-only",
        namespace="project-alpha",
        bucket="research",
        content="local primary",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-local-primary-only",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    local_sealed = MemoryRecord(
        id="rec-local-sealed-only",
        namespace="project-alpha",
        bucket="ops",
        content="sealed local only",
        content_format="markdown",
        source_type="note",
        sensitivity="sealed",
        metadata={},
        content_fingerprint="fp-local-sealed-only",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    cloud_only = MemoryRecord(
        id="rec-cloud-primary-only",
        namespace="project-alpha",
        bucket="research",
        content="cloud primary",
        content_format="markdown",
        source_type="note",
        sensitivity="restricted",
        metadata={},
        content_fingerprint="fp-cloud-primary-only",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    local.primary_store.save_record(
        local_primary,
        signature="record:note:markdown:standard",
    )
    local.sealed_store.save_record(
        local_sealed,
        signature="record:note:markdown:sealed",
    )
    cloud.save_record(
        cloud_only,
        signature="record:note:markdown:restricted",
    )

    result = store.reconcile_union()

    assert result["in_sync_after"] is True
    assert local.primary_store.get_record("rec-cloud-primary-only") is not None
    assert cloud.get_record("rec-local-primary-only") is not None
    assert cloud.get_record("rec-local-sealed-only") is None
