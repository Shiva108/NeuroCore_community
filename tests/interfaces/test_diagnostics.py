from neurocore.interfaces import diagnostics as diagnostics_module
from neurocore.interfaces.diagnostics import diagnose_runtime


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
