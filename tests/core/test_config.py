import json
from pathlib import Path

import pytest

from neurocore.core.config import ConfigError, NeuroCoreConfig, load_config
from neurocore.core.operator_state import (
    default_primary_store_path,
    default_scheduler_store_path,
    default_sealed_store_path,
)


def minimal_env(**overrides: str) -> dict[str, str]:
    values = {
        "NEUROCORE_DEFAULT_NAMESPACE": "project-alpha",
        "NEUROCORE_ALLOWED_BUCKETS": "research,planning",
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
    }
    values.update(overrides)
    return values


def test_load_config_requires_mandatory_environment_variables(monkeypatch):
    required_keys = [
        "NEUROCORE_DEFAULT_NAMESPACE",
        "NEUROCORE_ALLOWED_BUCKETS",
        "NEUROCORE_DEFAULT_SENSITIVITY",
    ]

    for key in required_keys:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ConfigError, match="NEUROCORE_DEFAULT_NAMESPACE"):
        load_config()


def test_load_config_rejects_invalid_bucket_entries(monkeypatch):
    with pytest.raises(ConfigError, match="bucket"):
        load_config(
            env=minimal_env(
                NEUROCORE_ALLOWED_BUCKETS="research,invalid bucket",
                NEUROCORE_DEFAULT_SENSITIVITY="standard",
            )
        )


def test_load_config_rejects_invalid_sensitivity(monkeypatch):
    with pytest.raises(ConfigError, match="sensitivity"):
        load_config(env=minimal_env(NEUROCORE_DEFAULT_SENSITIVITY="top-secret"))


def test_load_config_applies_documented_defaults(monkeypatch):
    monkeypatch.setenv("NEUROCORE_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("NEUROCORE_SEMANTIC_BACKEND", "sentence-transformers")

    config = load_config(env=minimal_env())

    assert isinstance(config, NeuroCoreConfig)
    assert config.default_namespace == "project-alpha"
    assert config.allowed_buckets == ("research", "planning")
    assert config.default_sensitivity == "restricted"
    assert config.max_atomic_tokens == 350
    assert config.target_chunk_tokens == 600
    assert config.max_chunk_tokens == 900
    assert config.chunk_overlap_tokens == 75
    assert config.default_top_k == 8
    assert config.allow_hard_delete is False
    assert config.enable_admin_surface is False
    assert config.storage_backend == "in_memory"
    assert config.primary_store_path == default_primary_store_path(minimal_env())
    assert config.sealed_store_path == default_sealed_store_path(minimal_env())
    assert config.semantic_backend == "none"
    assert config.semantic_model_name == "sentence-transformers/all-MiniLM-L6-v2"
    assert config.max_content_tokens == 50000
    assert config.enable_cli_adapter is True
    assert config.enable_http_adapter is False
    assert config.enable_mcp_adapter is False
    assert config.enable_dashboard is False
    assert config.enable_background_summarization is False
    assert config.enable_scheduler is False
    assert config.enable_multi_model_consensus is False
    assert config.consensus_provider == "none"
    assert config.consensus_model_names == ()
    assert config.scheduler_store_path == default_scheduler_store_path(minimal_env())
    assert config.dedup_merge_metadata is True


def test_load_config_accepts_extended_backend_and_adapter_settings(monkeypatch):
    config = load_config(
        env=minimal_env(
            NEUROCORE_STORAGE_BACKEND="postgres",
            NEUROCORE_PRIMARY_STORE_PATH="/tmp/neurocore.db",
            NEUROCORE_SEALED_STORE_PATH="/tmp/neurocore.sealed.db",
            NEUROCORE_SEMANTIC_BACKEND="sentence-transformers",
            NEUROCORE_SEMANTIC_MODEL_NAME="test-model",
            NEUROCORE_MAX_CONTENT_TOKENS="1024",
            NEUROCORE_ENABLE_HTTP_ADAPTER="true",
            NEUROCORE_ENABLE_MCP_ADAPTER="true",
            NEUROCORE_ENABLE_DASHBOARD="true",
            NEUROCORE_ENABLE_BACKGROUND_SUMMARIZATION="true",
            NEUROCORE_ENABLE_SCHEDULER="true",
            NEUROCORE_PRODUCTION_BACKEND_PROVIDER="supabase",
            NEUROCORE_PRODUCTION_DATABASE_URL="postgresql://primary",
            NEUROCORE_PRODUCTION_SEALED_DATABASE_URL="postgresql://sealed",
            NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS="true",
            NEUROCORE_CONSENSUS_PROVIDER="openai_compatible",
            NEUROCORE_CONSENSUS_MODEL_NAMES="gpt-4.1-mini,claude-3.5-sonnet",
            NEUROCORE_CONSENSUS_BASE_URL="https://api.example.test/v1",
            NEUROCORE_CONSENSUS_API_KEY="test-key",
        )
    )

    assert config.storage_backend == "postgres"
    assert config.primary_store_path == "/tmp/neurocore.db"
    assert config.sealed_store_path == "/tmp/neurocore.sealed.db"
    assert config.semantic_backend == "sentence-transformers"
    assert config.semantic_model_name == "test-model"
    assert config.max_content_tokens == 1024
    assert config.enable_http_adapter is True
    assert config.enable_mcp_adapter is True
    assert config.enable_dashboard is True
    assert config.enable_background_summarization is True
    assert config.enable_scheduler is True
    assert config.enable_multi_model_consensus is True
    assert config.consensus_provider == "openai_compatible"
    assert config.consensus_model_names == ("gpt-4.1-mini", "claude-3.5-sonnet")
    assert config.consensus_base_url == "https://api.example.test/v1"
    assert config.consensus_api_key == "test-key"
    assert config.production_backend_provider == "supabase"
    assert config.production_database_url == "postgresql://primary"
    assert config.production_sealed_database_url == "postgresql://sealed"


def test_load_config_infers_postgres_backend_from_hosted_settings():
    config = load_config(
        env=minimal_env(
            NEUROCORE_PRODUCTION_BACKEND_PROVIDER="neon",
            NEUROCORE_PRODUCTION_DATABASE_URL="postgresql://primary",
            NEUROCORE_PRODUCTION_SEALED_DATABASE_URL="postgresql://sealed",
        )
    )

    assert config.storage_backend == "postgres"


def test_load_config_rejects_incomplete_hosted_storage_settings():
    with pytest.raises(ConfigError, match="Hosted storage configuration is incomplete"):
        load_config(
            env=minimal_env(
                NEUROCORE_PRODUCTION_BACKEND_PROVIDER="neon",
                NEUROCORE_PRODUCTION_DATABASE_URL="postgresql://primary",
            )
        )


def test_load_config_rejects_postgres_backend_without_provider():
    with pytest.raises(
        ConfigError,
        match="Postgres storage backend requires NEUROCORE_PRODUCTION_BACKEND_PROVIDER",
    ):
        load_config(
            env=minimal_env(
                NEUROCORE_STORAGE_BACKEND="postgres",
                NEUROCORE_PRODUCTION_DATABASE_URL="postgresql://primary",
                NEUROCORE_PRODUCTION_SEALED_DATABASE_URL="postgresql://sealed",
            )
        )


def test_load_config_accepts_mirror_backend_settings():
    config = load_config(
        env=minimal_env(
            NEUROCORE_STORAGE_BACKEND="mirror",
            NEUROCORE_MIRROR_READ_PREFERENCE="cloud",
            NEUROCORE_PRIMARY_STORE_PATH="data/local.db",
            NEUROCORE_SEALED_STORE_PATH="data/local-sealed.db",
            NEUROCORE_PRODUCTION_BACKEND_PROVIDER="supabase",
            NEUROCORE_PRODUCTION_DATABASE_URL="postgresql://primary",
            NEUROCORE_PRODUCTION_SEALED_DATABASE_URL="postgresql://sealed",
        )
    )

    assert config.storage_backend == "mirror"
    assert config.mirror_read_preference == "cloud"


def test_load_config_accepts_local_only_sealed_mirror_mode():
    config = load_config(
        env=minimal_env(
            NEUROCORE_STORAGE_BACKEND="mirror",
            NEUROCORE_MIRROR_SEALED_MODE="local_only",
            NEUROCORE_PRODUCTION_BACKEND_PROVIDER="supabase",
            NEUROCORE_PRODUCTION_DATABASE_URL="postgresql://primary",
        )
    )

    assert config.storage_backend == "mirror"
    assert config.mirror_sealed_mode == "local_only"
    assert config.production_database_url == "postgresql://primary"
    assert config.production_sealed_database_url is None


def test_load_config_rejects_mirror_backend_without_cloud_settings():
    with pytest.raises(
        ConfigError,
        match="Mirror storage backend requires NEUROCORE_PRODUCTION_BACKEND_PROVIDER",
    ):
        load_config(
            env=minimal_env(
                NEUROCORE_STORAGE_BACKEND="mirror",
            )
        )


def test_load_config_rejects_local_only_sealed_mirror_mode_without_primary_url():
    with pytest.raises(
        ConfigError,
        match="Mirror storage backend requires NEUROCORE_PRODUCTION_DATABASE_URL",
    ):
        load_config(
            env=minimal_env(
                NEUROCORE_STORAGE_BACKEND="mirror",
                NEUROCORE_MIRROR_SEALED_MODE="local_only",
                NEUROCORE_PRODUCTION_BACKEND_PROVIDER="supabase",
            )
        )


def test_load_config_rejects_full_mirror_mode_without_sealed_url():
    with pytest.raises(
        ConfigError,
        match="Mirror storage backend requires NEUROCORE_PRODUCTION_SEALED_DATABASE_URL",
    ):
        load_config(
            env=minimal_env(
                NEUROCORE_STORAGE_BACKEND="mirror",
                NEUROCORE_MIRROR_SEALED_MODE="full",
                NEUROCORE_PRODUCTION_BACKEND_PROVIDER="supabase",
                NEUROCORE_PRODUCTION_DATABASE_URL="postgresql://primary",
            )
        )


def test_load_config_accepts_provider_aware_reporting_settings():
    config = load_config(
        env=minimal_env(
            NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS="true",
            NEUROCORE_REPORTING_STRATEGY="primary_with_fallback",
            NEUROCORE_REPORTING_CONSENSUS_MODE="claim_voting",
            NEUROCORE_REPORTING_PRIMARY_PROVIDER="deepseek",
            NEUROCORE_REPORTING_FALLBACK_PROVIDER="openai",
            NEUROCORE_REPORTING_PROVIDER_DEEPSEEK_BASE_URL="https://api.deepseek.com",
            NEUROCORE_REPORTING_PROVIDER_DEEPSEEK_API_KEY="deepseek-key",
            NEUROCORE_REPORTING_PROVIDER_DEEPSEEK_MODELS="deepseek-v4-flash,deepseek-v4-pro",
            NEUROCORE_REPORTING_PROVIDER_OPENAI_BASE_URL="https://api.openai.com/v1",
            NEUROCORE_REPORTING_PROVIDER_OPENAI_API_KEY="openai-key",
            NEUROCORE_REPORTING_PROVIDER_OPENAI_MODELS="gpt-5-mini",
        )
    )

    assert config.reporting_strategy == "primary_with_fallback"
    assert config.reporting_consensus_mode == "claim_voting"
    assert config.reporting_primary_provider == "deepseek"
    assert config.reporting_fallback_provider == "openai"
    assert (
        config.reporting_provider_configs["deepseek"].base_url
        == "https://api.deepseek.com"
    )
    assert config.reporting_provider_configs["deepseek"].model_names == (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    )
    assert config.reporting_provider_configs["openai"].model_names == ("gpt-5-mini",)


def test_load_config_rejects_partial_provider_aware_reporting_settings():
    with pytest.raises(ConfigError, match="missing required settings"):
        load_config(
            env=minimal_env(
                NEUROCORE_REPORTING_PRIMARY_PROVIDER="deepseek",
                NEUROCORE_REPORTING_PROVIDER_DEEPSEEK_BASE_URL="https://api.deepseek.com",
            )
        )


def test_load_config_defaults_reporting_consensus_mode_to_lexical_select():
    config = load_config(env=minimal_env())

    assert config.reporting_consensus_mode == "lexical_select"


def test_load_config_rejects_invalid_reporting_consensus_mode():
    with pytest.raises(ConfigError, match="reporting consensus mode"):
        load_config(
            env=minimal_env(
                NEUROCORE_REPORTING_CONSENSUS_MODE="judge_everything",
            )
        )


def test_load_config_rejects_judge_mode_without_fallback_provider():
    with pytest.raises(
        ConfigError,
        match="Judge-backed claim voting requires NEUROCORE_REPORTING_FALLBACK_PROVIDER",
    ):
        load_config(
            env=minimal_env(
                NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS="true",
                NEUROCORE_REPORTING_STRATEGY="single_provider",
                NEUROCORE_REPORTING_CONSENSUS_MODE="claim_voting_with_judge",
                NEUROCORE_REPORTING_PRIMARY_PROVIDER="deepseek",
                NEUROCORE_REPORTING_PROVIDER_DEEPSEEK_BASE_URL="https://api.deepseek.com",
                NEUROCORE_REPORTING_PROVIDER_DEEPSEEK_API_KEY="deepseek-key",
                NEUROCORE_REPORTING_PROVIDER_DEEPSEEK_MODELS="deepseek-v4-flash,deepseek-v4-pro",
            )
        )


def test_load_config_accepts_ingest_profile_path_and_parses_profiles(tmp_path):
    profile_path = tmp_path / "ingest-profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "version": "1",
                "profiles": [
                    {
                        "name": "slack-default",
                        "source": "slack",
                        "match": {"team_id": "T123"},
                        "defaults": {
                            "bucket": "planning",
                            "tags": ["slack-profile"],
                            "sensitivity": "restricted",
                        },
                        "parsing_hints": {"mode": "ops"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    config = load_config(
        env=minimal_env(
            NEUROCORE_INGEST_PROFILE_PATH=str(profile_path),
        )
    )

    assert config.ingest_profile_path == str(profile_path)
    assert config.ingest_profiles["version"] == "1"
    assert config.ingest_profiles["profiles"][0]["name"] == "slack-default"


def test_load_config_rejects_invalid_ingest_profile_json(tmp_path):
    profile_path = tmp_path / "ingest-profiles.json"
    profile_path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ConfigError, match="ingest profile"):
        load_config(
            env=minimal_env(
                NEUROCORE_INGEST_PROFILE_PATH=str(profile_path),
            )
        )


def test_load_config_rejects_invalid_ingest_profile_structure(tmp_path):
    profile_path = tmp_path / "ingest-profiles.json"
    profile_path.write_text(
        json.dumps({"version": "1", "profiles": [{"name": "broken"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="ingest profile"):
        load_config(
            env=minimal_env(
                NEUROCORE_INGEST_PROFILE_PATH=str(profile_path),
            )
        )


def test_checked_in_ingest_profile_example_matches_current_schema():
    profile_path = Path(__file__).resolve().parents[2] / "ingest-profiles.json.example"

    config = load_config(
        env=minimal_env(
            NEUROCORE_ALLOWED_BUCKETS="research,planning,ops,findings,reports",
            NEUROCORE_INGEST_PROFILE_PATH=str(profile_path),
        )
    )

    assert config.ingest_profile_path == str(profile_path)
    assert config.ingest_profiles["version"] == "1"
    profiles = config.ingest_profiles["profiles"]
    assert len(profiles) == 2
    assert {profile["source"] for profile in profiles} == {"slack", "discord"}
    assert {profile["defaults"]["bucket"] for profile in profiles} == {
        "ops",
        "findings",
    }


def test_load_config_rejects_invalid_storage_backend(monkeypatch):
    with pytest.raises(ConfigError, match="storage backend"):
        load_config(env=minimal_env(NEUROCORE_STORAGE_BACKEND="oracle"))


def test_load_config_rejects_invalid_mirror_sealed_mode():
    with pytest.raises(ConfigError, match="mirror sealed mode"):
        load_config(env=minimal_env(NEUROCORE_MIRROR_SEALED_MODE="cloud_only"))


def test_load_config_rejects_invalid_production_backend_provider(monkeypatch):
    with pytest.raises(ConfigError, match="production backend provider"):
        load_config(
            env=minimal_env(NEUROCORE_PRODUCTION_BACKEND_PROVIDER="mystery-cloud")
        )


def test_load_config_rejects_invalid_consensus_provider(monkeypatch):
    with pytest.raises(ConfigError, match="consensus provider"):
        load_config(env=minimal_env(NEUROCORE_CONSENSUS_PROVIDER="mystery"))
