import json

import pytest

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces import scheduler as scheduler_module
from neurocore.storage.in_memory import InMemoryStore


def _config(tmp_path, *, enabled: bool = True) -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_scheduler=enabled,
        enable_admin_surface=True,
        enable_background_summarization=True,
        scheduler_store_path=str(tmp_path / "scheduler.db"),
    )


def test_scheduler_create_list_and_delete_job(tmp_path):
    config = _config(tmp_path)

    created = scheduler_module.create_job(
        {
            "job_type": "sync",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"action": "status"},
        },
        config=config,
    )
    listed = scheduler_module.list_jobs({}, config=config)
    deleted = scheduler_module.delete_job(
        {"job_id": created["job"]["job_id"]},
        config=config,
    )

    assert created["created"] is True
    assert listed["count"] == 1
    assert listed["jobs"][0]["job_type"] == "sync"
    assert deleted == {"job_id": created["job"]["job_id"], "deleted": True}


def test_scheduler_run_due_executes_once_and_disables_job(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = InMemoryStore()
    seen: list[dict[str, object]] = []

    def fake_sync(request, *, store, config):
        seen.append(dict(request))
        return {"status": "ok"}

    monkeypatch.setattr(scheduler_module, "sync_storage", fake_sync)
    created = scheduler_module.create_job(
        {
            "job_type": "sync",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"action": "status"},
        },
        config=config,
    )

    result = scheduler_module.run_due_jobs(
        {"now": "2026-01-01T00:05:00+00:00"},
        config=config,
        store=store,
    )
    listed = scheduler_module.list_jobs({}, config=config)

    assert result["processed"] == 1
    assert result["failed"] == 0
    assert seen[0]["action"] == "status"
    assert seen[0]["_runtime_source_surface"] == "scheduler"
    assert seen[0]["_runtime_action"] == "sync"
    assert listed["jobs"][0]["enabled"] is False
    assert listed["jobs"][0]["last_status"] == "succeeded"
    assert created["job"]["job_id"] == listed["jobs"][0]["job_id"]


def test_scheduler_run_due_skips_interval_job_before_deadline(tmp_path, monkeypatch):
    config = _config(tmp_path)

    monkeypatch.setattr(
        scheduler_module,
        "sync_storage",
        lambda request, *, store, config: {"status": "should-not-run"},
    )
    scheduler_module.create_job(
        {
            "job_type": "sync",
            "schedule_kind": "interval",
            "interval_minutes": 30,
            "payload": {"action": "status"},
        },
        config=config,
    )

    result = scheduler_module.run_due_jobs(
        {"now": "2026-01-01T00:10:00+00:00"},
        config=config,
        store=InMemoryStore(),
    )

    assert result == {"processed": 0, "failed": 0, "jobs": []}


def test_scheduler_run_due_dispatches_briefing_reindex_and_maintenance(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    store = InMemoryStore()
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        scheduler_module,
        "generate_briefing",
        lambda request, *, store, config, semantic_ranker: calls.append(("briefing", dict(request))) or {"briefing": "ok"},
    )
    monkeypatch.setattr(
        scheduler_module,
        "reindex_memory",
        lambda request, *, store, config: calls.append(("reindex", dict(request))) or {"processed_ids": []},
    )
    monkeypatch.setattr(
        scheduler_module,
        "run_background_summaries",
        lambda request, *, store, config: calls.append(("maintenance", dict(request))) or {"processed": 0},
    )

    scheduler_module.create_job(
        {
            "job_type": "briefing",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"query_request": {"query_text": "auth"}},
        },
        config=config,
    )
    scheduler_module.create_job(
        {
            "job_type": "reindex",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"ids": ["rec-1"], "scope": "records"},
        },
        config=config,
    )
    scheduler_module.create_job(
        {
            "job_type": "maintenance",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"operation": "background-summaries", "limit": 1},
        },
        config=config,
    )

    result = scheduler_module.run_due_jobs(
        {"now": "2026-01-01T00:05:00+00:00"},
        config=config,
        store=store,
    )

    assert result["processed"] == 3
    assert calls[0][0] == "briefing"
    assert calls[0][1]["query_request"] == {"query_text": "auth"}
    assert calls[0][1]["_runtime_source_surface"] == "scheduler"
    assert calls[1][0] == "reindex"
    assert calls[1][1]["ids"] == ["rec-1"]
    assert calls[1][1]["_runtime_action"] == "reindex"
    assert calls[2][0] == "maintenance"
    assert calls[2][1]["operation"] == "background-summaries"
    assert calls[2][1]["_runtime_action"] == "run_background_summaries"


def test_scheduler_run_due_dispatches_sqlite_maintenance(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = InMemoryStore()
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        scheduler_module,
        "maintain_storage",
        lambda request, *, store, config: calls.append(dict(request)) or {"supported": False, "targets": [], "warnings": [], "action": request.get("action", "report")},
    )
    scheduler_module.create_job(
        {
            "job_type": "maintenance",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"operation": "sqlite-maintenance", "action": "checkpoint"},
        },
        config=config,
    )

    result = scheduler_module.run_due_jobs(
        {"now": "2026-01-01T00:05:00+00:00"},
        config=config,
        store=store,
    )

    assert result["processed"] == 1
    assert calls[0]["operation"] == "sqlite-maintenance"
    assert calls[0]["action"] == "checkpoint"
    assert calls[0]["_runtime_source_surface"] == "scheduler"
    assert calls[0]["_runtime_action"] == "sqlite_maintenance"


def test_scheduler_disabled_raises_permission_error(tmp_path):
    config = _config(tmp_path, enabled=False)

    with pytest.raises(PermissionError, match="Scheduler surface is disabled"):
        scheduler_module.list_jobs({}, config=config)


def test_scheduler_list_filters_job_type(tmp_path):
    config = _config(tmp_path)
    scheduler_module.create_job(
        {
            "job_type": "sync",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"action": "status"},
        },
        config=config,
    )
    scheduler_module.create_job(
        {
            "job_type": "maintenance",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"operation": "diagnose"},
        },
        config=config,
    )

    listed = scheduler_module.list_jobs({"job_type": "maintenance"}, config=config)

    assert listed["count"] == 1
    assert listed["jobs"][0]["job_type"] == "maintenance"


def test_scheduler_create_rejects_duplicate_job_id_and_invalid_payload(tmp_path):
    config = _config(tmp_path)
    scheduler_module.create_job(
        {
            "job_id": "job-1",
            "job_type": "sync",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"action": "status"},
        },
        config=config,
    )

    with pytest.raises(ValueError, match="job_id already exists"):
        scheduler_module.create_job(
            {
                "job_id": "job-1",
                "job_type": "sync",
                "schedule_kind": "once",
                "run_at": "2026-01-01T00:00:00+00:00",
                "payload": {"action": "status"},
            },
            config=config,
        )

    with pytest.raises(ValueError, match="sync job payload must include action"):
        scheduler_module.create_job(
            {
                "job_type": "sync",
                "schedule_kind": "once",
                "run_at": "2026-01-01T00:00:00+00:00",
                "payload": {},
            },
            config=config,
        )


def test_scheduler_create_delete_and_run_due_record_audit_events(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = InMemoryStore()
    monkeypatch.setattr(
        scheduler_module,
        "sync_storage",
        lambda request, *, store, config: {"status": "ok"},
    )

    created = scheduler_module.create_job(
        {
            "job_type": "sync",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"action": "status"},
        },
        config=config,
        store=store,
    )
    scheduler_module.run_due_jobs(
        {"now": "2026-01-01T00:05:00+00:00"},
        config=config,
        store=store,
    )
    scheduler_module.delete_job(
        {"job_id": created["job"]["job_id"]},
        config=config,
        store=store,
    )

    operations = [event["operation"] for event in store.audit_events]
    assert operations == [
        "scheduler_create_job",
        "scheduler_run_due",
        "scheduler_delete_job",
    ]
    assert store.audit_events[1]["details"]["source_surface"] == "library"
    assert store.audit_events[1]["details"]["result_summary"]["processed"] == 1


def test_scheduler_run_due_returns_structured_failure_details(tmp_path, monkeypatch):
    config = _config(tmp_path)
    store = InMemoryStore()

    monkeypatch.setattr(
        scheduler_module,
        "sync_storage",
        lambda request, *, store, config: (_ for _ in ()).throw(
            RuntimeError("sync exploded")
        ),
    )
    scheduler_module.create_job(
        {
            "job_type": "sync",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"action": "status"},
        },
        config=config,
    )

    result = scheduler_module.run_due_jobs(
        {"now": "2026-01-01T00:05:00+00:00"},
        config=config,
        store=store,
    )

    assert result["processed"] == 0
    assert result["failed"] == 1
    assert result["jobs"][0]["status"] == "failed"
    assert result["jobs"][0]["error"] == "sync exploded"
    assert result["jobs"][0]["consecutive_failures"] == 1
