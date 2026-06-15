"""SQLite footprint inspection and maintenance helpers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from neurocore.core.config import NeuroCoreConfig
from neurocore.storage.base import BaseStore
from neurocore.storage.local_only_sealed_mirrored_store import (
    LocalOnlySealedMirroredStore,
)
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage.router import RoutedStore
from neurocore.storage.sqlite_store import SQLiteStore

SQLITE_MAINTENANCE_ACTIONS = ("report", "checkpoint", "compact")
SQLITE_TARGET_MAINTENANCE_OPERATION = "sqlite_maintenance_target"


@dataclass(frozen=True)
class SQLiteTarget:
    """One local SQLite target in the active runtime topology."""

    name: str
    path: Path
    store: SQLiteStore | None = None


def inspect_sqlite_footprint(
    *,
    config: NeuroCoreConfig,
    store: BaseStore | None = None,
) -> dict[str, object]:
    """Return per-target SQLite footprint diagnostics for the active topology."""
    targets = resolve_local_sqlite_targets(config=config, store=store)
    payload_targets = [_target_snapshot(target) for target in targets]
    return {
        "supported": bool(payload_targets),
        "targets": payload_targets,
        "warnings": [],
    }


def maintain_local_sqlite(
    action: str,
    *,
    config: NeuroCoreConfig,
    store: BaseStore | None = None,
) -> dict[str, object]:
    """Inspect or maintain active local SQLite targets."""
    normalized_action = normalize_sqlite_maintenance_action(action)
    targets = resolve_local_sqlite_targets(config=config, store=store)
    results = [
        _target_snapshot(target, action=normalized_action)
        for target in targets
    ]
    return {
        "action": normalized_action,
        "supported": bool(results),
        "targets": results,
        "warnings": [],
    }


def normalize_sqlite_maintenance_action(action: str | None) -> str:
    """Return the normalized maintenance action."""
    normalized = str(action or "report").strip().lower() or "report"
    if normalized not in SQLITE_MAINTENANCE_ACTIONS:
        raise ValueError(
            "action must be one of: " + ", ".join(SQLITE_MAINTENANCE_ACTIONS)
        )
    return normalized


def resolve_local_sqlite_targets(
    *,
    config: NeuroCoreConfig,
    store: BaseStore | None = None,
) -> list[SQLiteTarget]:
    """Resolve active local SQLite targets from runtime topology or config."""
    if isinstance(store, RoutedStore):
        return _targets_from_routed_store(store)
    if isinstance(store, MirroredStore):
        return _targets_from_routed_store(store.local_store)
    if isinstance(store, LocalOnlySealedMirroredStore):
        return _targets_from_routed_store(store.local_store)
    if store is not None:
        return []
    if config.storage_backend not in {"sqlite", "mirror"}:
        return []
    return [
        SQLiteTarget(name="primary", path=Path(config.primary_store_path)),
        SQLiteTarget(name="sealed", path=Path(config.sealed_store_path)),
    ]


def record_target_maintenance_audit(
    target: SQLiteTarget,
    *,
    actor: str,
    action: str,
    outcome: str,
) -> None:
    """Record a per-target maintenance audit event when backed by SQLite."""
    if target.store is None:
        return
    target.store.record_audit(
        actor=actor,
        operation=SQLITE_TARGET_MAINTENANCE_OPERATION,
        target_ids=[target.name],
        outcome=outcome,
        details={
            "sqlite_target": target.name,
            "action": action,
            "path": str(target.path),
        },
    )


def _targets_from_routed_store(store: BaseStore) -> list[SQLiteTarget]:
    if not isinstance(store, RoutedStore):
        return []
    primary = store.primary_store
    sealed = store.sealed_store
    if not isinstance(primary, SQLiteStore) or not isinstance(sealed, SQLiteStore):
        return []
    return [
        SQLiteTarget(
            name="primary",
            path=primary.database_path,
            store=primary,
        ),
        SQLiteTarget(
            name="sealed",
            path=sealed.database_path,
            store=sealed,
        ),
    ]


def _target_snapshot(
    target: SQLiteTarget,
    *,
    action: str = "report",
) -> dict[str, object]:
    exists = target.path.exists()
    db_size_bytes = target.path.stat().st_size if exists else 0
    wal_path = Path(f"{target.path}-wal")
    wal_size_bytes = wal_path.stat().st_size if wal_path.exists() else 0
    page_size = 0
    page_count = 0
    freelist_count = 0
    checkpoint_performed = False
    compact_performed = False

    if exists or target.store is not None:
        page_size, page_count, freelist_count, checkpoint_performed, compact_performed = (
            _inspect_or_maintain_target(target, action=action)
        )
        exists = target.path.exists()
        db_size_bytes = target.path.stat().st_size if exists else 0
        wal_size_bytes = wal_path.stat().st_size if wal_path.exists() else 0

    return {
        "name": target.name,
        "path": str(target.path),
        "exists": exists,
        "db_size_bytes": db_size_bytes,
        "wal_size_bytes": wal_size_bytes,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "reclaimable_bytes_estimate": page_size * freelist_count,
        "last_maintenance_at": _last_maintenance_at(target),
        "checkpoint_performed": checkpoint_performed,
        "compact_performed": compact_performed,
    }


def _inspect_or_maintain_target(
    target: SQLiteTarget,
    *,
    action: str,
) -> tuple[int, int, int, bool, bool]:
    path = target.path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        checkpoint_performed = False
        compact_performed = False
        if action in {"checkpoint", "compact"}:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            checkpoint_performed = True
        if action == "compact":
            connection.execute("VACUUM")
            compact_performed = True
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        return (
            page_size,
            page_count,
            freelist_count,
            checkpoint_performed,
            compact_performed,
        )
    finally:
        connection.close()


def _last_maintenance_at(target: SQLiteTarget) -> str | None:
    if not target.path.exists():
        return None
    connection = sqlite3.connect(target.path)
    try:
        row = connection.execute(
            """
            SELECT timestamp
            FROM audit_events
            WHERE operation = ? AND target_ids_json = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (
                SQLITE_TARGET_MAINTENANCE_OPERATION,
                json.dumps([target.name]),
            ),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    return str(row[0])
