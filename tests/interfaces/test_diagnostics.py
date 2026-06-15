from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces import diagnostics as diagnostics_module
from neurocore.interfaces.diagnostics import diagnose_runtime
from neurocore.storage.in_memory import InMemoryStore
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage.router import RoutedStore
from neurocore.storage.sqlite_store import SQLiteStore


def test_diagnose_runtime_reports_valid_minimal_config():
    payload = diagnose_runtime(
        env={
            "NEUROCORE_DEFAULT_NAMESPACE": "project-alpha",
            "NEUROCORE_ALLOWED_BUCKETS": "research",
            "NEUROCORE_DEFAULT_SENSITIVITY": "standard",
        }
    )

    assert payload["config_ready"] is True
    assert payload["issues"] == []
    assert payload["config"]["default_namespace"] == "project-alpha"
    assert payload["storage_backend"]["mode"] == "in_memory"
    assert payload["semantic"] == {
        "backend": "none",
        "status": "disabled",
        "issue": None,
    }
    assert payload["reporting"]["status"] == "fallback-only"
    assert payload["sqlite_footprint"] == {
        "supported": False,
        "targets": [],
        "warnings": [],
    }


def test_diagnose_runtime_reports_invalid_config():
    payload = diagnose_runtime(
        env={
            "NEUROCORE_ALLOWED_BUCKETS": "research",
            "NEUROCORE_DEFAULT_SENSITIVITY": "standard",
        }
    )

    assert payload["config_ready"] is False
    assert any("NEUROCORE_DEFAULT_NAMESPACE" in issue for issue in payload["issues"])
    assert payload["config"]["default_namespace"] == "diagnostic"


def test_diagnose_runtime_sanitizes_invalid_best_effort_values():
    payload = diagnose_runtime(
        env={
            "NEUROCORE_DEFAULT_NAMESPACE": "bad namespace",
            "NEUROCORE_ALLOWED_BUCKETS": "good-bucket,bad bucket",
            "NEUROCORE_DEFAULT_SENSITIVITY": "not-real",
        }
    )

    assert payload["config_ready"] is False
    assert payload["config"]["default_namespace"] == "diagnostic"
    assert payload["config"]["allowed_buckets"] == ["good-bucket"]
    assert payload["config"]["default_sensitivity"] == "standard"
    assert payload["storage_backend"]["mode"] == "in_memory"


def test_diagnose_runtime_reports_unavailable_sentence_transformers(
    monkeypatch,
):
    monkeypatch.setattr(
        diagnostics_module,
        "sentence_transformers_status",
        lambda: ("unavailable", "sentence-transformers missing"),
    )

    payload = diagnose_runtime(
        env={
            "NEUROCORE_DEFAULT_NAMESPACE": "project-alpha",
            "NEUROCORE_ALLOWED_BUCKETS": "research",
            "NEUROCORE_DEFAULT_SENSITIVITY": "standard",
            "NEUROCORE_SEMANTIC_BACKEND": "sentence-transformers",
        }
    )

    assert payload["config_ready"] is True
    assert payload["semantic"] == {
        "backend": "sentence-transformers",
        "status": "unavailable",
        "issue": "sentence-transformers missing",
    }
    assert "sentence-transformers missing" in payload["issues"]


def test_diagnose_runtime_reports_unhealthy_provider(monkeypatch):
    monkeypatch.setattr(
        diagnostics_module,
        "check_provider_health",
        lambda provider, timeout=2.0: (False, "timed out"),
    )

    payload = diagnose_runtime(
        env={
            "NEUROCORE_DEFAULT_NAMESPACE": "project-alpha",
            "NEUROCORE_ALLOWED_BUCKETS": "research",
            "NEUROCORE_DEFAULT_SENSITIVITY": "standard",
            "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS": "true",
            "NEUROCORE_CONSENSUS_PROVIDER": "openai_compatible",
            "NEUROCORE_CONSENSUS_MODEL_NAMES": "model-a,model-b",
            "NEUROCORE_CONSENSUS_BASE_URL": "https://user:secret@example.test/v1",
            "NEUROCORE_CONSENSUS_API_KEY": "token",
        }
    )

    assert payload["config_ready"] is True
    assert payload["reporting"]["configured"] is True
    assert payload["reporting"]["healthy"] is False
    assert payload["reporting"]["status"] == "degraded"
    assert payload["provider_health"]["consensus"]["healthy"] is False
    assert payload["provider_health"]["consensus"]["base_url"] == "https://example.test"


def test_diagnose_runtime_keeps_provider_health_on_invalid_config(monkeypatch):
    monkeypatch.setattr(
        diagnostics_module,
        "check_provider_health",
        lambda provider, timeout=2.0: (False, "timed out"),
    )

    payload = diagnose_runtime(
        env={
            "NEUROCORE_ALLOWED_BUCKETS": "research",
            "NEUROCORE_DEFAULT_SENSITIVITY": "standard",
            "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS": "true",
            "NEUROCORE_CONSENSUS_PROVIDER": "openai_compatible",
            "NEUROCORE_CONSENSUS_MODEL_NAMES": "model-a,model-b",
            "NEUROCORE_CONSENSUS_BASE_URL": "https://example.test/v1",
            "NEUROCORE_CONSENSUS_API_KEY": "token",
        }
    )

    assert payload["config_ready"] is False
    assert payload["provider_health"]["consensus"]["healthy"] is False
    assert payload["reporting"]["status"] == "degraded"


def test_diagnose_runtime_reports_dual_write_readiness():
    payload = diagnose_runtime(
        env={
            "NEUROCORE_DEFAULT_NAMESPACE": "project-alpha",
            "NEUROCORE_ALLOWED_BUCKETS": "research",
            "NEUROCORE_DEFAULT_SENSITIVITY": "standard",
            "NEUROCORE_STORAGE_BACKEND": "mirror",
            "NEUROCORE_MIRROR_READ_PREFERENCE": "local",
            "NEUROCORE_MIRROR_SEALED_MODE": "full",
            "NEUROCORE_PRODUCTION_BACKEND_PROVIDER": "supabase",
            "NEUROCORE_PRODUCTION_DATABASE_URL": "postgresql://primary",
            "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL": "postgresql://sealed",
        }
    )

    assert payload["config_ready"] is True
    assert payload["config"]["storage_backend"] == "mirror"
    assert payload["config"]["mirror_sealed_mode"] == "full"
    assert payload["storage_backend"]["full_dual_write_configured"] is True
    assert payload["storage_backend"]["cloud_primary_configured"] is True
    assert payload["storage_backend"]["cloud_sealed_configured"] is True


def test_diagnose_runtime_reports_sqlite_footprint_for_sqlite_backend(tmp_path):
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="sqlite",
        primary_store_path=str(tmp_path / "primary.db"),
        sealed_store_path=str(tmp_path / "sealed.db"),
    )
    store = RoutedStore(
        primary_store=SQLiteStore(tmp_path / "primary.db"),
        sealed_store=SQLiteStore(tmp_path / "sealed.db"),
    )

    payload = diagnose_runtime(config=config, store=store)

    assert payload["sqlite_footprint"]["supported"] is True
    assert [target["name"] for target in payload["sqlite_footprint"]["targets"]] == [
        "primary",
        "sealed",
    ]
    assert all(target["path"].endswith(".db") for target in payload["sqlite_footprint"]["targets"])
    assert all(target["page_count"] >= 0 for target in payload["sqlite_footprint"]["targets"])
    assert all(
        target["reclaimable_bytes_estimate"]
        == target["page_size"] * target["freelist_count"]
        for target in payload["sqlite_footprint"]["targets"]
    )


def test_diagnose_runtime_reports_local_sqlite_targets_only_for_mirror(tmp_path):
    local = RoutedStore(
        primary_store=SQLiteStore(tmp_path / "local-primary.db"),
        sealed_store=SQLiteStore(tmp_path / "local-sealed.db"),
    )
    cloud = RoutedStore(
        primary_store=InMemoryStore(),
        sealed_store=InMemoryStore(),
    )
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="mirror",
        mirror_read_preference="local",
        primary_store_path=str(tmp_path / "configured-primary.db"),
        sealed_store_path=str(tmp_path / "configured-sealed.db"),
        production_backend_provider="supabase",
        production_database_url="postgresql://primary",
        production_sealed_database_url="postgresql://sealed",
    )

    payload = diagnose_runtime(config=config, store=store)

    assert payload["sqlite_footprint"]["supported"] is True
    assert [target["path"] for target in payload["sqlite_footprint"]["targets"]] == [
        str(tmp_path / "local-primary.db"),
        str(tmp_path / "local-sealed.db"),
    ]


def test_diagnose_runtime_surfaces_live_parity_degradation():
    store = MirroredStore(
        local_store=RoutedStore(
            primary_store=InMemoryStore(),
            sealed_store=InMemoryStore(),
        ),
        cloud_store=RoutedStore(
            primary_store=InMemoryStore(),
            sealed_store=InMemoryStore(),
        ),
        read_preference="local",
    )
    store._cloud_degraded = True
    store._last_cloud_error = "supabase unavailable"
    store._last_persistence_state = "partial"
    store._last_parity_state = "degraded"
    store._last_full_mirror_state = "stored"
    store._parity_verified = False
    store._reconciliation_pending = True
    store._automatic_reconciliation_attempted = True
    store._last_reconciliation_direction = "local_to_cloud"
    store._last_reconciliation_outcome = "failed"
    store._last_parity_check = "2026-06-06T12:34:56+00:00"
    store._last_bidirectional_divergence = True
    store._last_destructive_repair_risk = True
    store._last_recommended_safe_action = "reconcile_union"
    store._last_conflict_counts = {"documents": 2}
    store._last_repair_mode = "union"
    store._last_sync_action = "reconcile_union"
    store._last_sync_started_at = "2026-06-06T12:35:00+00:00"
    store._last_sync_finished_at = "2026-06-06T12:35:05+00:00"
    store._last_sync_pid = 5150
    store._last_sync_status = "success"

    payload = diagnose_runtime(
        env={
            "NEUROCORE_DEFAULT_NAMESPACE": "project-alpha",
            "NEUROCORE_ALLOWED_BUCKETS": "research",
            "NEUROCORE_DEFAULT_SENSITIVITY": "standard",
            "NEUROCORE_STORAGE_BACKEND": "mirror",
            "NEUROCORE_MIRROR_READ_PREFERENCE": "local",
            "NEUROCORE_MIRROR_SEALED_MODE": "full",
            "NEUROCORE_PRODUCTION_BACKEND_PROVIDER": "supabase",
            "NEUROCORE_PRODUCTION_DATABASE_URL": "postgresql://primary",
            "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL": "postgresql://sealed",
        },
        store=store,
    )

    assert payload["storage_backend"]["cloud_degraded"] is True
    assert payload["storage_backend"]["last_cloud_error"] == "supabase unavailable"
    assert payload["storage_backend"]["last_persistence_state"] == "partial"
    assert payload["storage_backend"]["last_parity_state"] == "degraded"
    assert payload["storage_backend"]["last_full_mirror_state"] == "stored"
    assert payload["storage_backend"]["parity_verified"] is False
    assert payload["storage_backend"]["reconciliation_pending"] is True
    assert payload["storage_backend"]["automatic_reconciliation_attempted"] is True
    assert payload["storage_backend"]["last_reconciliation_direction"] == (
        "local_to_cloud"
    )
    assert payload["storage_backend"]["last_reconciliation_outcome"] == "failed"
    assert payload["storage_backend"]["last_parity_check"] == (
        "2026-06-06T12:34:56+00:00"
    )
    assert payload["storage_backend"]["bidirectional_divergence"] is True
    assert payload["storage_backend"]["destructive_repair_risk"] is True
    assert payload["storage_backend"]["recommended_safe_action"] == "reconcile_union"
    assert payload["storage_backend"]["conflict_counts"] == {"documents": 2}
    assert payload["storage_backend"]["repair_mode"] == "union"
    assert payload["storage_backend"]["last_sync_action"] == "reconcile_union"
    assert payload["storage_backend"]["last_sync_started_at"] == (
        "2026-06-06T12:35:00+00:00"
    )
    assert payload["storage_backend"]["last_sync_finished_at"] == (
        "2026-06-06T12:35:05+00:00"
    )
    assert payload["storage_backend"]["last_sync_pid"] == 5150
    assert payload["storage_backend"]["last_sync_status"] == "success"
    assert payload["storage_backend"]["last_sync_error"] is None
    assert payload["storage_backend"]["active_reconciliation"] is False
