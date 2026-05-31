from datetime import UTC, datetime
import sqlite3

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


def test_mirrored_store_cloud_first_write_marks_local_degradation():
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
    assert store.mirror_status()["local_degraded"] is True
    assert store.pop_warnings()


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
