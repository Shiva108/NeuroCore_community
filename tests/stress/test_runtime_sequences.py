from __future__ import annotations

import tempfile

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, settings
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule
import hypothesis.strategies as st

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.admin import sync_storage, update_memory
from neurocore.interfaces.brains import archive_brain, create_brain
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.scheduler import create_job, run_due_jobs
from neurocore.interfaces.sessions import checkpoint_session, resume_session
from neurocore.interfaces.summaries import run_background_summaries
from neurocore.storage.in_memory import InMemoryStore
from neurocore.storage.local_only_sealed_mirrored_store import (
    LocalOnlySealedMirroredStore,
)
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage.router import RoutedStore
from .invariants import assert_scheduler_invariants, assert_store_invariants


def _config(path: str) -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="stress-default",
        allowed_buckets=("research", "agents", "reports"),
        default_sensitivity="restricted",
        enable_admin_surface=True,
        enable_scheduler=True,
        enable_background_summarization=True,
        scheduler_store_path=path,
    )


class RuntimeSequenceMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.tempdir = tempfile.TemporaryDirectory(prefix="neurocore-stress-")
        self.config = _config(f"{self.tempdir.name}/scheduler.db")
        self.store = InMemoryStore()
        self.brains: set[str] = set()
        self.session_pairs: set[tuple[str, str]] = set()
        self.record_ids: list[str] = []

    @initialize()
    def seed(self):
        create_brain(
            {
                "brain_id": "brain-a",
                "namespace": "brain-a",
                "display_name": "Brain A",
            },
            store=self.store,
            default_allowed_buckets=self.config.allowed_buckets,
        )
        self.brains.add("brain-a")

    @rule(brain_id=st.sampled_from(["brain-a", "brain-b", "brain-c"]))
    def create_or_refresh_brain(self, brain_id: str):
        create_brain(
            {
                "brain_id": brain_id,
                "namespace": brain_id,
                "display_name": brain_id.upper(),
            },
            store=self.store,
            default_allowed_buckets=self.config.allowed_buckets,
        )
        self.brains.add(brain_id)

    @rule(brain_id=st.sampled_from(["brain-a", "brain-b", "brain-c"]))
    def maybe_archive_brain(self, brain_id: str):
        if brain_id not in self.brains:
            return
        archive_brain({"brain_id": brain_id, "reason": "stress"}, store=self.store)

    @rule(
        brain_id=st.sampled_from(["brain-a", "brain-b", "brain-c"]),
        session_id=st.sampled_from(["sess-1", "sess-2", "sess-3"]),
    )
    def checkpoint(self, brain_id: str, session_id: str):
        if brain_id not in self.brains:
            self.create_or_refresh_brain(brain_id)
        response = checkpoint_session(
            {
                "brain_id": brain_id,
                "session_id": session_id,
                "source_client": "stress",
                "summary": f"Checkpoint for {brain_id} {session_id}",
                "importance": "high",
            },
            store=self.store,
            config=self.config,
        )
        self.session_pairs.add((brain_id, session_id))
        self.record_ids.append(str(response["id"]))

    @rule(
        brain_id=st.sampled_from(["brain-a", "brain-b", "brain-c"]),
        session_id=st.sampled_from(["sess-1", "sess-2", "sess-3"]),
    )
    def resume(self, brain_id: str, session_id: str):
        if (brain_id, session_id) not in self.session_pairs:
            return
        response = resume_session(
            {
                "brain_id": brain_id,
                "session_id": session_id,
                "allowed_buckets": ["agents"],
                "sensitivity_ceiling": "restricted",
            },
            store=self.store,
            config=self.config,
        )
        assert response["namespace"] == brain_id

    @rule(content=st.sampled_from(["short note", "longer research note", "ops trace"]))
    def capture_record(self, content: str):
        response = capture_memory(
            {
                "namespace": "brain-a",
                "bucket": "research",
                "sensitivity": "restricted",
                "content": content,
                "content_format": "markdown",
                "source_type": "note",
            },
            store=self.store,
            config=self.config,
        )
        if response["kind"] == "record":
            self.record_ids.append(str(response["id"]))

    @rule()
    def update_latest_record(self):
        if not self.record_ids:
            return
        record_id = self.record_ids[-1]
        if not self.store.has_item(record_id):
            return
        update_memory(
            {
                "id": record_id,
                "patch": {"title": "stress-reviewed"},
                "mode": "in_place",
            },
            self.store,
            self.config,
        )

    @rule()
    def create_and_run_scheduler_job(self):
        create_job(
            {
                "job_type": "maintenance",
                "schedule_kind": "once",
                "run_at": "2026-01-01T00:00:00+00:00",
                "payload": {"operation": "diagnose"},
            },
            config=self.config,
            store=self.store,
        )
        run_due_jobs(
            {"now": "2026-01-01T00:05:00+00:00"},
            config=self.config,
            store=self.store,
        )

    @invariant()
    def invariants_hold(self):
        assert_store_invariants(self.store, self.config)
        assert_scheduler_invariants(self.config)

    def teardown(self):
        self.tempdir.cleanup()


RuntimeSequenceMachine.TestCase.settings = settings(
    max_examples=25,
    stateful_step_count=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    derandomize=True,
)

TestRuntimeSequenceMachine = RuntimeSequenceMachine.TestCase


def test_session_checkpoint_then_resume_after_admin_mutation():
    store = InMemoryStore()
    config = _config(f"{tempfile.mkdtemp(prefix='neurocore-seq-')}/scheduler.db")
    create_brain(
        {
            "brain_id": "brain-a",
            "namespace": "brain-a",
            "display_name": "Brain A",
        },
        store=store,
        default_allowed_buckets=config.allowed_buckets,
    )
    checkpoint = checkpoint_session(
        {
            "brain_id": "brain-a",
            "session_id": "sess-1",
            "source_client": "stress",
            "summary": "Checkpoint: auth chain validated",
            "importance": "high",
        },
        store=store,
        config=config,
    )

    update_memory(
        {
            "id": checkpoint["id"],
            "patch": {"title": "updated checkpoint"},
            "mode": "in_place",
        },
        store,
        config,
    )
    resumed = resume_session(
        {
            "brain_id": "brain-a",
            "session_id": "sess-1",
            "query_text": "auth chain validated",
            "allowed_buckets": ["agents"],
            "sensitivity_ceiling": "restricted",
        },
        store=store,
        config=config,
    )

    assert resumed["namespace"] == "brain-a"
    assert "auth chain validated" in resumed["briefing"].lower()


def test_scheduler_maintenance_and_summaries_paths_record_audit_events():
    store = InMemoryStore()
    config = _config(f"{tempfile.mkdtemp(prefix='neurocore-seq-')}/scheduler.db")
    capture_memory(
        {
            "namespace": "brain-a",
            "bucket": "research",
            "sensitivity": "restricted",
            "content": "Sentence one. Sentence two. Sentence three.",
            "content_format": "markdown",
            "source_type": "note",
            "force_kind": "document",
        },
        store=store,
        config=config,
    )
    create_job(
        {
            "job_type": "maintenance",
            "schedule_kind": "once",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload": {"operation": "background-summaries", "limit": 1},
        },
        config=config,
        store=store,
    )

    result = run_due_jobs(
        {"now": "2026-01-01T00:05:00+00:00"},
        config=config,
        store=store,
    )

    assert result["processed"] == 1
    assert any(
        event["operation"] == "background_summaries_run"
        for event in store.audit_events
    )


def test_mirror_sync_preserves_runtime_audit_visibility():
    config = NeuroCoreConfig(
        default_namespace="brain-a",
        allowed_buckets=("research", "agents"),
        default_sensitivity="restricted",
        enable_admin_surface=True,
        storage_backend="mirror",
        production_backend_provider="supabase",
        production_database_url="postgresql://primary",
        production_sealed_database_url="postgresql://sealed",
    )
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")

    checkpoint_session(
        {
            "brain_id": "brain-a",
            "namespace": "brain-a",
            "session_id": "sess-1",
            "source_client": "stress",
            "summary": "Checkpoint: mirror audit",
            "importance": "high",
        },
        store=store,
        config=config,
    )
    sync_storage({"action": "backfill_local_to_cloud"}, store, config)

    assert store.list_audit_events(limit=100)
    assert cloud.list_audit_events(limit=100)


def test_local_only_mirror_preserves_runtime_audit_visibility():
    config = NeuroCoreConfig(
        default_namespace="brain-a",
        allowed_buckets=("research", "agents"),
        default_sensitivity="restricted",
        enable_admin_surface=True,
        storage_backend="mirror",
        production_backend_provider="supabase",
        production_database_url="postgresql://primary",
    )
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud_primary = InMemoryStore()
    store = LocalOnlySealedMirroredStore(
        local_store=local,
        cloud_primary_store=cloud_primary,
    )

    checkpoint_session(
        {
            "brain_id": "brain-a",
            "namespace": "brain-a",
            "session_id": "sess-2",
            "source_client": "stress",
            "summary": "Checkpoint: local only audit",
            "importance": "high",
        },
        store=store,
        config=config,
    )
    sync_storage({"action": "status"}, store, config)

    assert store.list_audit_events(limit=100)
    assert cloud_primary.list_audit_events(limit=100)
