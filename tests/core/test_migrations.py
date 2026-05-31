from datetime import UTC, datetime

from neurocore.core.models import MemoryRecord
from neurocore.maintenance.migrations import (
    backfill_namespace,
    migrate_sealed_records,
    validate_bucket_assignments,
)
from neurocore.storage.in_memory import InMemoryStore
from neurocore.storage.router import RoutedStore


def test_backfill_namespace_updates_missing_namespaces():
    store = InMemoryStore()
    record = MemoryRecord(
        id="rec-1",
        namespace="default",
        bucket="research",
        content="namespace record",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    store.save_record(record, signature="record:note:markdown")

    updated = backfill_namespace(
        store=store,
        item_ids=["rec-1"],
        namespace="project-alpha",
    )

    assert updated == 1
    assert store.get_record("rec-1").namespace == "project-alpha"


def test_validate_bucket_assignments_reports_invalid_buckets():
    store = InMemoryStore()
    record = MemoryRecord(
        id="rec-1",
        namespace="project-alpha",
        bucket="ops",
        content="bucket record",
        content_format="markdown",
        source_type="note",
        sensitivity="standard",
        metadata={},
        content_fingerprint="fp-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    store.save_record(record, signature="record:note:markdown")

    invalid_ids = validate_bucket_assignments(
        store, allowed_buckets=("research", "planning")
    )

    assert invalid_ids == ["rec-1"]


def test_migrate_sealed_records_moves_items_to_sealed_store():
    primary = InMemoryStore()
    sealed = InMemoryStore()
    store = RoutedStore(primary_store=primary, sealed_store=sealed)
    record = MemoryRecord(
        id="rec-sealed-1",
        namespace="project-alpha",
        bucket="ops",
        content="sealed record",
        content_format="markdown",
        source_type="note",
        sensitivity="sealed",
        metadata={},
        content_fingerprint="fp-sealed-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    primary.save_record(record, signature="record:note:markdown")

    migrated = migrate_sealed_records(store, item_ids=["rec-sealed-1"])

    assert migrated == 1
    assert primary.get_record("rec-sealed-1") is None
    assert sealed.get_record("rec-sealed-1") is not None
