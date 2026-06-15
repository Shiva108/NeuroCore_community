"""Optional local scheduler interface for recurring operator jobs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.admin import (
    audit_memory,
    maintain_storage,
    reindex_memory,
    sync_storage,
)
from neurocore.interfaces.briefing import generate_briefing
from neurocore.interfaces.diagnostics import diagnose_runtime
from neurocore.interfaces.runtime_support import (
    attach_runtime_metadata,
    record_runtime_audit,
    runtime_source_surface,
    supervise_call,
)
from neurocore.interfaces.summaries import run_background_summaries
from neurocore.retrieval.rankers import SemanticRanker
from neurocore.runtime import build_semantic_ranker, build_store
from neurocore.storage.base import BaseStore

VALID_JOB_TYPES = ("briefing", "sync", "reindex", "maintenance")
VALID_SCHEDULE_KINDS = ("once", "interval")
VALID_MAINTENANCE_OPERATIONS = (
    "audit",
    "background-summaries",
    "diagnose",
    "sqlite-maintenance",
)
SCHEDULER_JOB_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class SchedulerJob:
    """Normalized scheduler record."""

    job_id: str
    job_type: str
    schedule_kind: str
    run_at: str | None
    interval_minutes: int | None
    payload: dict[str, object]
    enabled: bool
    last_run_at: str | None
    last_status: str | None
    last_error: str | None
    created_at: str
    updated_at: str


def create_job(
    request: dict[str, object],
    *,
    config: NeuroCoreConfig,
    store: BaseStore | None = None,
) -> dict[str, object]:
    """Create a scheduler job."""
    _require_scheduler_enabled(config)
    job = _normalize_job_request(request)
    try:
        with _scheduler_connection(config) as conn:
            conn.execute(
                """
                INSERT INTO scheduler_jobs (
                    job_id,
                    job_type,
                    schedule_kind,
                    run_at,
                    interval_minutes,
                    payload_json,
                    enabled,
                    last_run_at,
                    last_status,
                    last_error,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.job_type,
                    job.schedule_kind,
                    job.run_at,
                    job.interval_minutes,
                    json.dumps(job.payload, sort_keys=True),
                    1 if job.enabled else 0,
                    job.last_run_at,
                    job.last_status,
                    job.last_error,
                    job.created_at,
                    job.updated_at,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"job_id already exists: {job.job_id}") from exc
    response = {"job": asdict(job), "created": True}
    record_runtime_audit(
        store,
        actor=str(request.get("actor", "system")),
        operation="scheduler_create_job",
        request=request,
        status="succeeded",
        result=response,
        target_ids=[job.job_id],
    )
    return response


def list_jobs(
    request: dict[str, object],
    *,
    config: NeuroCoreConfig,
) -> dict[str, object]:
    """List scheduler jobs."""
    _require_scheduler_enabled(config)
    enabled_only = bool(request.get("enabled_only", False))
    job_type = str(request.get("job_type") or "").strip() or None
    if job_type is not None and job_type not in VALID_JOB_TYPES:
        raise ValueError(f"job_type must be one of: {', '.join(VALID_JOB_TYPES)}")

    query = "SELECT * FROM scheduler_jobs"
    clauses: list[str] = []
    params: list[object] = []
    if enabled_only:
        clauses.append("enabled = 1")
    if job_type is not None:
        clauses.append("job_type = ?")
        params.append(job_type)
    if clauses:
        query = f"{query} WHERE {' AND '.join(clauses)}"
    query = f"{query} ORDER BY created_at ASC, job_id ASC"

    with _scheduler_connection(config) as conn:
        rows = conn.execute(query, params).fetchall()
    jobs = [_row_to_job(row) for row in rows]
    return {"jobs": [asdict(job) for job in jobs], "count": len(jobs)}


def delete_job(
    request: dict[str, object],
    *,
    config: NeuroCoreConfig,
    store: BaseStore | None = None,
) -> dict[str, object]:
    """Delete one scheduler job by id."""
    _require_scheduler_enabled(config)
    job_id = str(request.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")

    with _scheduler_connection(config) as conn:
        cursor = conn.execute("DELETE FROM scheduler_jobs WHERE job_id = ?", (job_id,))
    deleted = cursor.rowcount > 0
    response = {"job_id": job_id, "deleted": deleted}
    record_runtime_audit(
        store,
        actor=str(request.get("actor", "system")),
        operation="scheduler_delete_job",
        request=request,
        status="succeeded",
        result=response,
        target_ids=[job_id],
    )
    return response


def run_due_jobs(
    request: dict[str, object],
    *,
    config: NeuroCoreConfig,
    store: BaseStore | None = None,
) -> dict[str, object]:
    """Run due scheduler jobs once for the current process."""
    _require_scheduler_enabled(config)
    store = store or build_store(config)
    semantic_ranker = build_semantic_ranker(config)
    limit = int(request.get("limit", 10))
    if limit < 1:
        raise ValueError("limit must be >= 1")
    now = (
        _parse_timestamp(str(request.get("now") or "").strip())
        if request.get("now")
        else _utcnow()
    )
    source_surface = runtime_source_surface(request)

    due_jobs = _select_due_jobs(config, now=now, limit=limit)
    results: list[dict[str, object]] = []
    processed = 0
    failed = 0
    for job in due_jobs:
        job_action = _job_action(job)
        supervised = supervise_call(
            lambda: _dispatch_job(
                job,
                config=config,
                store=store,
                semantic_ranker=semantic_ranker,
            ),
            source_surface=source_surface,
            action=job_action,
            timeout_seconds=SCHEDULER_JOB_TIMEOUT_SECONDS,
        )
        payload = supervised.result if supervised.succeeded else None
        error = supervised.error
        status = supervised.status
        if supervised.succeeded:
            processed += 1
        else:
            failed += 1

        updated_job = _record_job_attempt(
            config,
            job,
            status=status,
            error=error,
            attempted_at=now,
        )
        entry = {
            "job": asdict(updated_job),
            "status": status,
            "duration_ms": supervised.duration_ms,
            "consecutive_failures": supervised.consecutive_failures,
        }
        if supervised.timed_out:
            entry["timed_out"] = True
        if payload is not None:
            entry["result"] = payload
        if error is not None:
            entry["error"] = error
        results.append(entry)

    response = {
        "processed": processed,
        "failed": failed,
        "jobs": results,
    }
    record_runtime_audit(
        store,
        actor=str(request.get("actor", "system")),
        operation="scheduler_run_due",
        request=request,
        status="succeeded" if failed == 0 else "failed",
        result=response,
        target_ids=[job.job_id for job in due_jobs],
    )
    return response


def _dispatch_job(
    job: SchedulerJob,
    *,
    config: NeuroCoreConfig,
    store: BaseStore,
    semantic_ranker: SemanticRanker | None,
) -> dict[str, object]:
    """Dispatch one normalized scheduler job to its target interface."""
    payload = attach_runtime_metadata(
        dict(job.payload),
        source_surface="scheduler",
        action=_job_action(job),
    )
    if job.job_type == "briefing":
        return generate_briefing(
            payload,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        )
    if job.job_type == "sync":
        return sync_storage(payload, store=store, config=config)
    if job.job_type == "reindex":
        return reindex_memory(payload, store=store, config=config)
    operation = str(payload.get("operation") or "").strip()
    if operation == "audit":
        return audit_memory(payload, store=store, config=config)
    if operation == "background-summaries":
        return run_background_summaries(payload, store=store, config=config)
    if operation == "diagnose":
        return diagnose_runtime(config=config, store=store)
    if operation == "sqlite-maintenance":
        return maintain_storage(payload, store=store, config=config)
    raise ValueError(
        "maintenance payload operation must be one of: "
        + ", ".join(VALID_MAINTENANCE_OPERATIONS)
    )


def _job_action(job: SchedulerJob) -> str:
    """Return the runtime audit action name for a scheduler job."""
    if job.job_type != "maintenance":
        return job.job_type
    operation = str(job.payload.get("operation") or "").strip()
    return operation or "maintenance"


def _select_due_jobs(
    config: NeuroCoreConfig,
    *,
    now: datetime,
    limit: int,
) -> list[SchedulerJob]:
    with _scheduler_connection(config) as conn:
        rows = conn.execute(
            "SELECT * FROM scheduler_jobs WHERE enabled = 1 ORDER BY created_at ASC, job_id ASC"
        ).fetchall()
    jobs = [_row_to_job(row) for row in rows]
    due = [job for job in jobs if _is_due(job, now=now)]
    return due[:limit]


def _record_job_attempt(
    config: NeuroCoreConfig,
    job: SchedulerJob,
    *,
    status: str,
    error: str | None,
    attempted_at: datetime,
) -> SchedulerJob:
    enabled = job.enabled
    if job.schedule_kind == "once":
        enabled = False
    attempted = _format_timestamp(attempted_at)
    updated = SchedulerJob(
        job_id=job.job_id,
        job_type=job.job_type,
        schedule_kind=job.schedule_kind,
        run_at=job.run_at,
        interval_minutes=job.interval_minutes,
        payload=job.payload,
        enabled=enabled,
        last_run_at=attempted,
        last_status=status,
        last_error=error,
        created_at=job.created_at,
        updated_at=attempted,
    )
    with _scheduler_connection(config) as conn:
        conn.execute(
            """
            UPDATE scheduler_jobs
            SET enabled = ?, last_run_at = ?, last_status = ?, last_error = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (
                1 if updated.enabled else 0,
                updated.last_run_at,
                updated.last_status,
                updated.last_error,
                updated.updated_at,
                updated.job_id,
            ),
        )
    return updated


def _normalize_job_request(request: dict[str, object]) -> SchedulerJob:
    job_type = str(request.get("job_type") or "").strip()
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(f"job_type must be one of: {', '.join(VALID_JOB_TYPES)}")
    schedule_kind = str(request.get("schedule_kind") or "").strip()
    if schedule_kind not in VALID_SCHEDULE_KINDS:
        raise ValueError(
            f"schedule_kind must be one of: {', '.join(VALID_SCHEDULE_KINDS)}"
        )
    raw_payload = request.get("payload")
    if not isinstance(raw_payload, dict):
        raise ValueError("payload must be an object")
    _validate_job_payload(job_type, raw_payload)
    run_at = None
    interval_minutes = None
    if schedule_kind == "once":
        run_at = str(request.get("run_at") or "").strip()
        if not run_at:
            raise ValueError("run_at is required for once jobs")
        _parse_timestamp(run_at)
    else:
        interval_minutes = int(request.get("interval_minutes", 0))
        if interval_minutes < 1:
            raise ValueError("interval_minutes must be >= 1 for interval jobs")
    now = _utcnow()
    timestamp = _format_timestamp(now)
    return SchedulerJob(
        job_id=str(request.get("job_id") or "").strip()
        or f"job-{uuid.uuid4().hex[:12]}",
        job_type=job_type,
        schedule_kind=schedule_kind,
        run_at=run_at,
        interval_minutes=interval_minutes,
        payload=raw_payload,
        enabled=bool(request.get("enabled", True)),
        last_run_at=None,
        last_status=None,
        last_error=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _is_due(job: SchedulerJob, *, now: datetime) -> bool:
    if not job.enabled:
        return False
    if job.schedule_kind == "once":
        return _parse_timestamp(job.run_at or job.created_at) <= now
    if job.interval_minutes is None:
        return False
    anchor = job.last_run_at or job.created_at
    return _parse_timestamp(anchor) + timedelta(minutes=job.interval_minutes) <= now


def _row_to_job(row: sqlite3.Row) -> SchedulerJob:
    """Convert a persisted SQLite row into a scheduler job model."""
    return SchedulerJob(
        job_id=str(row["job_id"]),
        job_type=str(row["job_type"]),
        schedule_kind=str(row["schedule_kind"]),
        run_at=str(row["run_at"]) if row["run_at"] is not None else None,
        interval_minutes=(
            int(row["interval_minutes"])
            if row["interval_minutes"] is not None
            else None
        ),
        payload=json.loads(str(row["payload_json"])),
        enabled=bool(row["enabled"]),
        last_run_at=str(row["last_run_at"]) if row["last_run_at"] is not None else None,
        last_status=str(row["last_status"]) if row["last_status"] is not None else None,
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


@contextmanager
def _scheduler_connection(config: NeuroCoreConfig) -> Iterator[sqlite3.Connection]:
    """Open the scheduler SQLite database and ensure its schema exists."""
    path = Path(config.scheduler_store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduler_jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                schedule_kind TEXT NOT NULL,
                run_at TEXT,
                interval_minutes INTEGER,
                payload_json TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                last_run_at TEXT,
                last_status TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _require_scheduler_enabled(config: NeuroCoreConfig) -> None:
    if not config.enable_scheduler:
        raise PermissionError("Scheduler surface is disabled")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: str) -> datetime:
    if not value:
        raise ValueError("timestamp must be a non-empty ISO 8601 string")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _validate_job_payload(job_type: str, payload: dict[str, object]) -> None:
    if job_type == "maintenance":
        operation = str(payload.get("operation") or "").strip()
        if operation not in VALID_MAINTENANCE_OPERATIONS:
            raise ValueError(
                "maintenance payload operation must be one of: "
                + ", ".join(VALID_MAINTENANCE_OPERATIONS)
            )
        return
    if job_type == "sync":
        action = str(payload.get("action") or "").strip()
        if not action:
            raise ValueError("sync job payload must include action")
        return
    if job_type == "reindex":
        scope = str(payload.get("scope") or "").strip()
        if not scope:
            raise ValueError("reindex job payload must include scope")
