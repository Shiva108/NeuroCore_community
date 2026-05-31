"""Runtime configuration loading and validation for NeuroCore."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from neurocore.core.ingest_profiles import (
    BUCKET_PATTERN,
    VALID_SENSITIVITIES,
    validate_ingest_profiles,
)
from neurocore.core.operator_state import (
    default_primary_store_path,
    default_sealed_store_path,
)

VALID_STORAGE_BACKENDS = ("in_memory", "sqlite", "postgres", "mirror")
VALID_MIRROR_READ_PREFERENCES = ("local", "cloud")
VALID_MIRROR_SEALED_MODES = ("full", "local_only")
VALID_SEMANTIC_BACKENDS = ("none", "sentence-transformers")
VALID_PRODUCTION_BACKEND_PROVIDERS = ("none", "neon", "supabase")
VALID_CONSENSUS_PROVIDERS = ("none", "openai_compatible")
VALID_REPORTING_STRATEGIES = ("single_provider", "primary_with_fallback")
VALID_REPORTING_CONSENSUS_MODES = (
    "lexical_select",
    "claim_voting",
    "claim_voting_with_judge",
)
VALID_REPORTING_PROVIDER_TYPES = ("openai_compatible",)

_REPORTING_PROVIDER_ENV = re.compile(
    r"^NEUROCORE_REPORTING_PROVIDER_([A-Z0-9_]+)_(BASE_URL|API_KEY|MODELS|TYPE)$"
)


class ConfigError(ValueError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True)
class ReportingProviderConfig:
    """Resolved configuration for one external reporting provider."""

    name: str
    provider_type: str = "openai_compatible"
    base_url: str | None = None
    api_key: str | None = None
    model_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class NeuroCoreConfig:
    """Resolved runtime configuration for a NeuroCore process."""

    default_namespace: str
    allowed_buckets: tuple[str, ...]
    default_sensitivity: str
    storage_backend: str = "in_memory"
    mirror_read_preference: str = "local"
    mirror_sealed_mode: str = "full"
    primary_store_path: str = field(default_factory=default_primary_store_path)
    sealed_store_path: str = field(default_factory=default_sealed_store_path)
    semantic_backend: str = "none"
    semantic_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_atomic_tokens: int = 350
    target_chunk_tokens: int = 600
    max_chunk_tokens: int = 900
    chunk_overlap_tokens: int = 75
    max_content_tokens: int = 50000
    default_top_k: int = 8
    allow_hard_delete: bool = False
    enable_admin_surface: bool = False
    enable_cli_adapter: bool = True
    enable_http_adapter: bool = False
    enable_mcp_adapter: bool = False
    enable_dashboard: bool = False
    enable_background_summarization: bool = False
    enable_multi_model_consensus: bool = False
    consensus_provider: str = "none"
    consensus_model_names: tuple[str, ...] = ()
    consensus_base_url: str | None = None
    consensus_api_key: str | None = None
    reporting_strategy: str = "single_provider"
    reporting_consensus_mode: str = "lexical_select"
    reporting_primary_provider: str | None = None
    reporting_fallback_provider: str | None = None
    reporting_provider_configs: dict[str, ReportingProviderConfig] = field(
        default_factory=dict
    )
    production_backend_provider: str = "none"
    production_database_url: str | None = None
    production_sealed_database_url: str | None = None
    dedup_merge_metadata: bool = True
    ingest_profile_path: str | None = None
    ingest_profiles: dict[str, object] = field(
        default_factory=lambda: {"version": "1", "profiles": []}
    )

    def uses_reporting_provider_registry(self) -> bool:
        """Return whether provider-aware reporting config is active."""
        return bool(self.reporting_provider_configs)


def load_config(env: dict[str, str] | None = None) -> NeuroCoreConfig:
    """Load and validate configuration from environment-style key/value data."""
    values = dict(os.environ if env is None else env)
    default_namespace = _required(values, "NEUROCORE_DEFAULT_NAMESPACE")
    allowed_buckets = _parse_buckets(_required(values, "NEUROCORE_ALLOWED_BUCKETS"))
    default_sensitivity = _parse_sensitivity(
        _required(values, "NEUROCORE_DEFAULT_SENSITIVITY")
    )
    production_backend_provider = _parse_enum(
        values,
        "NEUROCORE_PRODUCTION_BACKEND_PROVIDER",
        VALID_PRODUCTION_BACKEND_PROVIDERS,
        "none",
    )
    production_database_url = _parse_optional_string(
        values, "NEUROCORE_PRODUCTION_DATABASE_URL"
    )
    production_sealed_database_url = _parse_optional_string(
        values, "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL"
    )
    storage_backend = _resolve_storage_backend(
        values,
        production_backend_provider=production_backend_provider,
        production_database_url=production_database_url,
        production_sealed_database_url=production_sealed_database_url,
    )
    _validate_storage_configuration(
        storage_backend=storage_backend,
        mirror_sealed_mode=_parse_enum(
            values,
            "NEUROCORE_MIRROR_SEALED_MODE",
            VALID_MIRROR_SEALED_MODES,
            "full",
        ),
        production_backend_provider=production_backend_provider,
        production_database_url=production_database_url,
        production_sealed_database_url=production_sealed_database_url,
    )

    ingest_profile_path = _parse_optional_string(
        values, "NEUROCORE_INGEST_PROFILE_PATH"
    )
    (
        reporting_strategy,
        reporting_primary_provider,
        reporting_fallback_provider,
        reporting_provider_configs,
    ) = _parse_reporting_providers(values)
    reporting_consensus_mode = _parse_enum(
        values,
        "NEUROCORE_REPORTING_CONSENSUS_MODE",
        VALID_REPORTING_CONSENSUS_MODES,
        "lexical_select",
    )
    if (
        reporting_consensus_mode == "claim_voting_with_judge"
        and reporting_fallback_provider is None
    ):
        raise ConfigError(
            "Judge-backed claim voting requires "
            "NEUROCORE_REPORTING_FALLBACK_PROVIDER"
        )

    return NeuroCoreConfig(
        default_namespace=default_namespace,
        allowed_buckets=allowed_buckets,
        default_sensitivity=default_sensitivity,
        storage_backend=storage_backend,
        mirror_read_preference=_parse_enum(
            values,
            "NEUROCORE_MIRROR_READ_PREFERENCE",
            VALID_MIRROR_READ_PREFERENCES,
            "local",
        ),
        mirror_sealed_mode=_parse_enum(
            values,
            "NEUROCORE_MIRROR_SEALED_MODE",
            VALID_MIRROR_SEALED_MODES,
            "full",
        ),
        primary_store_path=values.get(
            "NEUROCORE_PRIMARY_STORE_PATH",
            default_primary_store_path(values),
        ),
        sealed_store_path=values.get(
            "NEUROCORE_SEALED_STORE_PATH",
            default_sealed_store_path(values),
        ),
        semantic_backend=_parse_enum(
            values, "NEUROCORE_SEMANTIC_BACKEND", VALID_SEMANTIC_BACKENDS, "none"
        ),
        semantic_model_name=values.get(
            "NEUROCORE_SEMANTIC_MODEL_NAME",
            "sentence-transformers/all-MiniLM-L6-v2",
        ),
        max_atomic_tokens=_parse_int(values, "NEUROCORE_MAX_ATOMIC_TOKENS", 350, 1),
        target_chunk_tokens=_parse_int(values, "NEUROCORE_TARGET_CHUNK_TOKENS", 600, 1),
        max_chunk_tokens=_parse_int(values, "NEUROCORE_MAX_CHUNK_TOKENS", 900, 1),
        chunk_overlap_tokens=_parse_int(
            values, "NEUROCORE_CHUNK_OVERLAP_TOKENS", 75, 0
        ),
        max_content_tokens=_parse_int(values, "NEUROCORE_MAX_CONTENT_TOKENS", 50000, 1),
        default_top_k=_parse_int(values, "NEUROCORE_DEFAULT_TOP_K", 8, 1),
        allow_hard_delete=_parse_bool(values, "NEUROCORE_ALLOW_HARD_DELETE", False),
        enable_admin_surface=_parse_bool(
            values, "NEUROCORE_ENABLE_ADMIN_SURFACE", False
        ),
        enable_cli_adapter=_parse_bool(values, "NEUROCORE_ENABLE_CLI_ADAPTER", True),
        enable_http_adapter=_parse_bool(values, "NEUROCORE_ENABLE_HTTP_ADAPTER", False),
        enable_mcp_adapter=_parse_bool(values, "NEUROCORE_ENABLE_MCP_ADAPTER", False),
        enable_dashboard=_parse_bool(values, "NEUROCORE_ENABLE_DASHBOARD", False),
        enable_background_summarization=_parse_bool(
            values, "NEUROCORE_ENABLE_BACKGROUND_SUMMARIZATION", False
        ),
        enable_multi_model_consensus=_parse_bool(
            values, "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS", False
        ),
        consensus_provider=_parse_enum(
            values,
            "NEUROCORE_CONSENSUS_PROVIDER",
            VALID_CONSENSUS_PROVIDERS,
            "none",
        ),
        consensus_model_names=_parse_csv(values, "NEUROCORE_CONSENSUS_MODEL_NAMES"),
        consensus_base_url=_parse_optional_string(
            values, "NEUROCORE_CONSENSUS_BASE_URL"
        ),
        consensus_api_key=_parse_optional_string(values, "NEUROCORE_CONSENSUS_API_KEY"),
        reporting_strategy=reporting_strategy,
        reporting_consensus_mode=reporting_consensus_mode,
        reporting_primary_provider=reporting_primary_provider,
        reporting_fallback_provider=reporting_fallback_provider,
        reporting_provider_configs=reporting_provider_configs,
        production_backend_provider=production_backend_provider,
        production_database_url=production_database_url,
        production_sealed_database_url=production_sealed_database_url,
        dedup_merge_metadata=_parse_bool(
            values, "NEUROCORE_DEDUP_MERGE_METADATA", True
        ),
        ingest_profile_path=ingest_profile_path,
        ingest_profiles=_load_ingest_profiles(
            ingest_profile_path, allowed_buckets=allowed_buckets
        ),
    )


def _required(values: dict[str, str], key: str) -> str:
    """Return a required configuration value or raise a ConfigError."""
    value = values.get(key, "").strip()
    if not value:
        raise ConfigError(f"Missing required configuration: {key}")
    return value


def _parse_buckets(raw: str) -> tuple[str, ...]:
    """Parse and validate the allowed bucket list."""
    buckets = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not buckets:
        raise ConfigError("At least one allowed bucket must be configured")
    invalid = [bucket for bucket in buckets if not BUCKET_PATTERN.match(bucket)]
    if invalid:
        raise ConfigError(f"Invalid bucket values: {', '.join(invalid)}")
    return buckets


def _parse_sensitivity(raw: str) -> str:
    """Parse the default sensitivity value."""
    value = raw.strip().lower()
    if value not in VALID_SENSITIVITIES:
        raise ConfigError(f"Invalid sensitivity value: {raw}")
    return value


def _parse_int(values: dict[str, str], key: str, default: int, minimum: int) -> int:
    """Parse an integer config value with minimum bound enforcement."""
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Configuration {key} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"Configuration {key} must be >= {minimum}")
    return value


def _parse_bool(values: dict[str, str], key: str, default: bool) -> bool:
    """Parse a boolean config value from common truthy and falsy strings."""
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Configuration {key} must be a boolean")


def _parse_enum(
    values: dict[str, str], key: str, valid: tuple[str, ...], default: str
) -> str:
    """Parse a string enum value."""
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value not in valid:
        label = key.replace("NEUROCORE_", "").replace("_", " ").lower()
        raise ConfigError(f"Invalid {label}: {raw}")
    return value


def _parse_optional_string(values: dict[str, str], key: str) -> str | None:
    """Return a stripped optional string value when present."""
    raw = values.get(key)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _resolve_storage_backend(
    values: dict[str, str],
    *,
    production_backend_provider: str,
    production_database_url: str | None,
    production_sealed_database_url: str | None,
) -> str:
    """Resolve the active storage backend with hosted-profile inference."""
    raw_backend = values.get("NEUROCORE_STORAGE_BACKEND")
    if raw_backend is not None and raw_backend.strip():
        return _parse_enum(
            values, "NEUROCORE_STORAGE_BACKEND", VALID_STORAGE_BACKENDS, "in_memory"
        )

    has_hosted_settings = (
        production_backend_provider != "none"
        or production_database_url is not None
        or production_sealed_database_url is not None
    )
    if not has_hosted_settings:
        return "in_memory"
    if (
        production_backend_provider != "none"
        and production_database_url is not None
        and production_sealed_database_url is not None
    ):
        return "postgres"
    raise ConfigError(
        "Hosted storage configuration is incomplete. Set "
        "NEUROCORE_PRODUCTION_BACKEND_PROVIDER, "
        "NEUROCORE_PRODUCTION_DATABASE_URL, and "
        "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL together, or clear them."
    )


def _validate_storage_configuration(
    *,
    storage_backend: str,
    mirror_sealed_mode: str = "full",
    production_backend_provider: str,
    production_database_url: str | None,
    production_sealed_database_url: str | None,
) -> None:
    """Validate storage backend settings after backend resolution."""
    if storage_backend not in {"postgres", "mirror"}:
        return
    if production_backend_provider == "none":
        raise ConfigError(
            f"{storage_backend.capitalize()} storage backend requires "
            "NEUROCORE_PRODUCTION_BACKEND_PROVIDER"
        )
    if production_database_url is None:
        raise ConfigError(
            f"{storage_backend.capitalize()} storage backend requires "
            "NEUROCORE_PRODUCTION_DATABASE_URL"
        )
    if storage_backend == "mirror" and mirror_sealed_mode == "local_only":
        return
    if production_sealed_database_url is None:
        raise ConfigError(
            f"{storage_backend.capitalize()} storage backend requires "
            "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL"
        )


def _parse_csv(values: dict[str, str], key: str) -> tuple[str, ...]:
    """Parse a comma-separated configuration value."""
    raw = values.get(key)
    if raw is None or not raw.strip():
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _parse_reporting_providers(
    values: dict[str, str],
) -> tuple[str, str | None, str | None, dict[str, ReportingProviderConfig]]:
    """Parse provider-aware reporting configuration with legacy compatibility."""
    provider_fields: dict[str, dict[str, object]] = {}
    for key, value in values.items():
        match = _REPORTING_PROVIDER_ENV.match(key)
        if match is None:
            continue
        provider_name = match.group(1).lower()
        field_name = match.group(2).lower()
        provider_fields.setdefault(provider_name, {})[field_name] = (
            value.strip() if isinstance(value, str) else value
        )

    provider_configs: dict[str, ReportingProviderConfig] = {}
    for provider_name, payload in provider_fields.items():
        base_url = str(payload.get("base_url") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()
        model_names = tuple(
            part.strip()
            for part in str(payload.get("models") or "").split(",")
            if part.strip()
        )
        provider_type = str(payload.get("type") or "openai_compatible").strip().lower()
        if provider_type not in VALID_REPORTING_PROVIDER_TYPES:
            raise ConfigError(
                f"Invalid reporting provider type for {provider_name}: {provider_type}"
            )
        present_fields = {
            "base URL": bool(base_url),
            "API key": bool(api_key),
            "models": bool(model_names),
        }
        if any(present_fields.values()) and not all(present_fields.values()):
            missing = ", ".join(
                label for label, present in present_fields.items() if not present
            )
            raise ConfigError(
                f"Reporting provider {provider_name} is missing required settings: {missing}"
            )
        if present_fields["base URL"]:
            provider_configs[provider_name] = ReportingProviderConfig(
                name=provider_name,
                provider_type=provider_type,
                base_url=base_url,
                api_key=api_key,
                model_names=model_names,
            )

    primary_provider = _parse_optional_string(
        values, "NEUROCORE_REPORTING_PRIMARY_PROVIDER"
    )
    fallback_provider = _parse_optional_string(
        values, "NEUROCORE_REPORTING_FALLBACK_PROVIDER"
    )
    if primary_provider is not None:
        primary_provider = primary_provider.lower()
    if fallback_provider is not None:
        fallback_provider = fallback_provider.lower()

    explicit_strategy = _parse_optional_string(values, "NEUROCORE_REPORTING_STRATEGY")
    strategy = (
        explicit_strategy.lower()
        if explicit_strategy is not None
        else (
            "primary_with_fallback"
            if fallback_provider is not None
            else "single_provider"
        )
    )
    if strategy not in VALID_REPORTING_STRATEGIES:
        raise ConfigError(f"Invalid reporting strategy: {strategy}")

    if not provider_configs:
        if primary_provider is not None or fallback_provider is not None:
            raise ConfigError(
                "Reporting primary/fallback providers require provider-specific configuration"
            )
        return strategy, None, None, {}

    if primary_provider is None:
        if len(provider_configs) == 1:
            primary_provider = next(iter(provider_configs))
        else:
            raise ConfigError(
                "Reporting provider config requires "
                "NEUROCORE_REPORTING_PRIMARY_PROVIDER when multiple providers "
                "are configured"
            )
    if primary_provider not in provider_configs:
        raise ConfigError(
            f"Reporting primary provider {primary_provider} has no matching provider configuration"
        )

    if strategy == "primary_with_fallback":
        if fallback_provider is None:
            raise ConfigError(
                "Reporting strategy primary_with_fallback requires NEUROCORE_REPORTING_FALLBACK_PROVIDER"
            )
        if fallback_provider not in provider_configs:
            raise ConfigError(
                f"Reporting fallback provider {fallback_provider} has no matching provider configuration"
            )
        if fallback_provider == primary_provider:
            raise ConfigError(
                "Reporting fallback provider must differ from primary provider"
            )
    else:
        fallback_provider = None

    return strategy, primary_provider, fallback_provider, provider_configs


def _load_ingest_profiles(
    profile_path: str | None, *, allowed_buckets: tuple[str, ...]
) -> dict[str, object]:
    """Load and validate optional ingest profile configuration."""
    if profile_path is None:
        return {"version": "1", "profiles": []}
    try:
        with open(profile_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise ConfigError(
            f"Failed to read ingest profile configuration: {profile_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Invalid ingest profile JSON in {profile_path}: {exc.msg}"
        ) from exc

    try:
        return validate_ingest_profiles(payload, allowed_buckets=allowed_buckets)
    except ValueError as exc:
        raise ConfigError(f"Invalid ingest profile configuration: {exc}") from exc
