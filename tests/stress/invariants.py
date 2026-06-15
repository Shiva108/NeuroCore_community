from __future__ import annotations

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.scheduler import list_jobs
from neurocore.storage.base import BaseStore


def assert_store_invariants(store: BaseStore, config: NeuroCoreConfig) -> None:
    for record in store.list_records():
        assert record.archived_at is None
    for document in store.list_documents():
        assert document.archived_at is None
    for event in store.list_audit_events(limit=10_000):
        assert event["operation"]
        assert isinstance(event.get("details"), dict)


def assert_scheduler_invariants(config: NeuroCoreConfig) -> None:
    listed = list_jobs({}, config=config)
    for job in listed["jobs"]:
        if job["schedule_kind"] == "once" and job["last_status"] == "succeeded":
            assert job["enabled"] is False
