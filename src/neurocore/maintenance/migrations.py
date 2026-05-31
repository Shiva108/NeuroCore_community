"""Maintenance and migration helpers for NeuroCore storage."""

from __future__ import annotations

from dataclasses import replace

from neurocore.storage.base import BaseStore
from neurocore.storage.router import RoutedStore


def backfill_namespace(store: BaseStore, item_ids: list[str], namespace: str) -> int:
    updated = 0
    for item_id in item_ids:
        record = store.get_record(item_id, include_archived=True)
        if record is not None:
            store.save_record(
                replace(record, namespace=namespace),
                signature=f"record:{record.source_type}:{record.content_format}",
            )
            updated += 1
            continue
        document = store.get_document(item_id, include_archived=True)
        if document is not None:
            chunk_ids = store.get_document_chunk_ids(item_id)
            chunks = [
                replace(store.get_chunk(chunk_id), namespace=namespace)
                for chunk_id in chunk_ids
                if store.get_chunk(chunk_id) is not None
            ]
            store.save_document(
                replace(document, namespace=namespace),
                chunks,
                signature=f"document:{document.source_type}:markdown",
            )
            updated += 1
    return updated


def validate_bucket_assignments(
    store: BaseStore, allowed_buckets: tuple[str, ...]
) -> list[str]:
    invalid_ids: list[str] = []
    seen_namespaces: set[str] = set()
    for namespace in ["default", "project-alpha", "project-beta"]:
        for candidate in store.iter_candidates(
            namespace=namespace,
            allowed_buckets=allowed_buckets + ("ops", "personal", "work"),
            include_archived=True,
        ):
            seen_namespaces.add(namespace)
            if (
                candidate.item.bucket not in allowed_buckets
                and candidate.item.id not in invalid_ids
            ):
                invalid_ids.append(candidate.item.id)
    return invalid_ids


def migrate_sealed_records(store: RoutedStore, item_ids: list[str]) -> int:
    migrated = 0
    for item_id in item_ids:
        record = store.primary_store.get_record(item_id, include_archived=True)
        if record is not None and record.sensitivity == "sealed":
            store.sealed_store.save_record(
                record,
                signature=f"record:{record.source_type}:{record.content_format}",
            )
            store.primary_store.hard_delete(item_id)
            migrated += 1
            continue
        document = store.primary_store.get_document(item_id, include_archived=True)
        if document is not None and document.sensitivity == "sealed":
            chunk_ids = store.primary_store.get_document_chunk_ids(item_id)
            chunks = [
                store.primary_store.get_chunk(chunk_id)
                for chunk_id in chunk_ids
                if store.primary_store.get_chunk(chunk_id) is not None
            ]
            store.sealed_store.save_document(
                document,
                chunks,
                signature=f"document:{document.source_type}:markdown",
            )
            store.primary_store.hard_delete(item_id)
            migrated += 1
    return migrated
