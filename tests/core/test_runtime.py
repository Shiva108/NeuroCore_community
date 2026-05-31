from neurocore.core.config import NeuroCoreConfig, ReportingProviderConfig
import pytest

from neurocore.runtime import (
    build_production_backend_choice,
    build_reporter,
    build_storage_backend_status,
    build_store,
    build_summarizer,
    resolve_reporting_plan,
)


def test_build_store_selects_postgres_backends_for_neon_runtime(monkeypatch):
    captured_urls: list[str] = []

    class FakePostgresStore:
        def __init__(self, database_url: str) -> None:
            captured_urls.append(database_url)

    monkeypatch.setattr("neurocore.runtime.PostgresStore", FakePostgresStore)

    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="postgres",
        production_backend_provider="neon",
        production_database_url="postgresql://primary-host/db",
        production_sealed_database_url="postgresql://sealed-host/db",
    )

    store = build_store(config)

    assert captured_urls == [
        "postgresql://primary-host/db",
        "postgresql://sealed-host/db",
    ]
    assert store.primary_store.__class__.__name__ == "FakePostgresStore"
    assert store.sealed_store.__class__.__name__ == "FakePostgresStore"


def test_build_store_reuses_shared_postgres_store_when_urls_match(monkeypatch):
    created_urls: list[str] = []

    class FakePostgresStore:
        def __init__(self, database_url: str) -> None:
            created_urls.append(database_url)

    monkeypatch.setattr("neurocore.runtime.PostgresStore", FakePostgresStore)

    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="postgres",
        production_backend_provider="supabase",
        production_database_url="postgresql://shared-host/db",
        production_sealed_database_url="postgresql://shared-host/db",
    )

    store = build_store(config)

    assert created_urls == ["postgresql://shared-host/db"]
    assert store.primary_store is store.sealed_store


def test_build_production_backend_choice_redacts_urls():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        production_backend_provider="supabase",
        production_database_url="postgresql://user:secret@primary-host:5432/db",
        production_sealed_database_url="postgresql://user:secret@sealed-host:5432/db",
    )

    payload = build_production_backend_choice(config).to_dict()

    assert payload["provider"] == "supabase"
    assert payload["primary_url"] is None
    assert payload["sealed_url"] is None
    assert payload["primary_target"] == "postgresql://primary-host:5432"
    assert payload["sealed_target"] == "postgresql://sealed-host:5432"


def test_build_store_rejects_postgres_backend_without_production_provider():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="postgres",
        production_backend_provider="none",
        production_database_url="postgresql://primary-host/db",
        production_sealed_database_url="postgresql://sealed-host/db",
    )

    with pytest.raises(ValueError, match="production backend provider"):
        build_store(config)


def test_build_store_selects_mirrored_store_for_dual_backend_runtime(monkeypatch):
    sqlite_paths: list[str] = []
    postgres_urls: list[str] = []

    class FakeSQLiteStore:
        def __init__(self, database_path: str) -> None:
            sqlite_paths.append(str(database_path))

    class FakePostgresStore:
        def __init__(self, database_url: str) -> None:
            postgres_urls.append(database_url)

    monkeypatch.setattr("neurocore.runtime.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr("neurocore.runtime.PostgresStore", FakePostgresStore)

    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="mirror",
        mirror_read_preference="local",
        primary_store_path="data/local.db",
        sealed_store_path="data/local-sealed.db",
        production_backend_provider="supabase",
        production_database_url="postgresql://primary-host/db",
        production_sealed_database_url="postgresql://sealed-host/db",
    )

    store = build_store(config)

    assert store.__class__.__name__ == "MirroredStore"
    assert sqlite_paths == ["data/local.db", "data/local-sealed.db"]
    assert postgres_urls == [
        "postgresql://primary-host/db",
        "postgresql://sealed-host/db",
    ]


def test_build_storage_backend_status_reports_mirror_mode():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="mirror",
        mirror_read_preference="local",
        primary_store_path="data/local.db",
        sealed_store_path="data/local-sealed.db",
        production_backend_provider="supabase",
        production_database_url="postgresql://user:secret@primary-host:5432/db",
        production_sealed_database_url="postgresql://user:secret@sealed-host:5432/db",
    )

    payload = build_storage_backend_status(config).to_dict()

    assert payload["mode"] == "mirror"
    assert payload["read_preference"] == "local"
    assert payload["local_configured"] is True
    assert payload["cloud_configured"] is True
    assert payload["cloud_provider"] == "supabase"
    assert payload["primary_target"] == "postgresql://primary-host:5432"


def test_build_store_selects_local_only_sealed_mirror_store(monkeypatch):
    sqlite_paths: list[str] = []
    postgres_urls: list[str] = []

    class FakeSQLiteStore:
        def __init__(self, database_path: str) -> None:
            sqlite_paths.append(str(database_path))

    class FakePostgresStore:
        def __init__(self, database_url: str) -> None:
            postgres_urls.append(database_url)

    monkeypatch.setattr("neurocore.runtime.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr("neurocore.runtime.PostgresStore", FakePostgresStore)

    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="mirror",
        mirror_read_preference="local",
        mirror_sealed_mode="local_only",
        primary_store_path="data/local.db",
        sealed_store_path="data/local-sealed.db",
        production_backend_provider="supabase",
        production_database_url="postgresql://primary-host/db",
    )

    store = build_store(config)

    assert store.__class__.__name__ == "LocalOnlySealedMirroredStore"
    assert sqlite_paths == ["data/local.db", "data/local-sealed.db"]
    assert postgres_urls == ["postgresql://primary-host/db"]


def test_build_storage_backend_status_reports_local_only_sealed_mode():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="mirror",
        mirror_read_preference="local",
        mirror_sealed_mode="local_only",
        primary_store_path="data/local.db",
        sealed_store_path="data/local-sealed.db",
        production_backend_provider="supabase",
        production_database_url="postgresql://user:secret@primary-host:5432/db",
    )

    payload = build_storage_backend_status(config).to_dict()

    assert payload["mode"] == "mirror"
    assert payload["sealed_mode"] == "local_only"
    assert payload["cloud_configured"] is True
    assert payload["primary_target"] == "postgresql://primary-host:5432"
    assert payload["sealed_target"] is None


def test_build_summarizer_rejects_duplicate_model_names():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
        consensus_provider="openai_compatible",
        consensus_model_names=("model-a", "model-a"),
        consensus_base_url="https://api.example.test/v1",
        consensus_api_key="test-key",
    )

    with pytest.raises(ValueError, match="unique model names"):
        build_summarizer(config)


def test_build_summarizer_requires_consensus_api_key():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
        consensus_provider="openai_compatible",
        consensus_model_names=("model-a", "model-b"),
        consensus_base_url="https://api.example.test/v1",
    )

    with pytest.raises(ValueError, match="consensus API key"):
        build_summarizer(config)


def test_build_reporter_requires_consensus_api_key():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
        consensus_provider="openai_compatible",
        consensus_model_names=("model-a", "model-b"),
        consensus_base_url="https://api.example.test/v1",
    )

    with pytest.raises(ValueError, match="consensus API key"):
        build_reporter(config)


def test_build_reporter_supports_primary_with_fallback_provider_registry():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
        reporting_strategy="primary_with_fallback",
        reporting_consensus_mode="claim_voting",
        reporting_primary_provider="deepseek",
        reporting_fallback_provider="openai",
        reporting_provider_configs={
            "deepseek": ReportingProviderConfig(
                name="deepseek",
                base_url="https://api.deepseek.com",
                api_key="deepseek-key",
                model_names=("deepseek-v4-flash", "deepseek-v4-pro"),
            ),
            "openai": ReportingProviderConfig(
                name="openai",
                base_url="https://api.openai.com/v1",
                api_key="openai-key",
                model_names=("gpt-5-mini",),
            ),
        },
    )

    reporter = build_reporter(config)
    plan = resolve_reporting_plan(config)

    assert reporter.__class__.__name__ == "PrimaryWithFallbackReporter"
    assert reporter.primary_reporter.consensus_mode == "claim_voting"
    assert plan.primary_provider_name == "deepseek"
    assert plan.fallback_provider_name == "openai"


def test_build_reporter_wires_fallback_provider_as_judge_for_judge_mode():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
        reporting_strategy="primary_with_fallback",
        reporting_consensus_mode="claim_voting_with_judge",
        reporting_primary_provider="deepseek",
        reporting_fallback_provider="openai",
        reporting_provider_configs={
            "deepseek": ReportingProviderConfig(
                name="deepseek",
                base_url="https://api.deepseek.com",
                api_key="deepseek-key",
                model_names=("deepseek-v4-flash", "deepseek-v4-pro"),
            ),
            "openai": ReportingProviderConfig(
                name="openai",
                base_url="https://api.openai.com/v1",
                api_key="openai-key",
                model_names=("gpt-5-mini",),
            ),
        },
    )

    reporter = build_reporter(config)

    assert reporter.primary_reporter.consensus_mode == "claim_voting_with_judge"
    assert reporter.primary_reporter.judge_model_name == "gpt-5-mini"
    assert reporter.primary_reporter.judge_provider_name == "openai"
