"""Sanitized runtime diagnostics for NeuroCore."""

from __future__ import annotations

import os
import re
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from neurocore.core.config import (
    ConfigError,
    NeuroCoreConfig,
    ReportingProviderConfig,
    VALID_CONSENSUS_PROVIDERS,
    VALID_MIRROR_READ_PREFERENCES,
    VALID_MIRROR_SEALED_MODES,
    VALID_PRODUCTION_BACKEND_PROVIDERS,
    VALID_REPORTING_CONSENSUS_MODES,
    VALID_REPORTING_STRATEGIES,
    VALID_SEMANTIC_BACKENDS,
    VALID_STORAGE_BACKENDS,
    load_config,
)
from neurocore.core.operator_state import (
    default_primary_store_path,
    default_sealed_store_path,
)
from neurocore.core.policies import BUCKET_PATTERN, NAMESPACE_PATTERN
from neurocore.core.semantic import sentence_transformers_status
from neurocore.interfaces.reporting import build_reporting_status
from neurocore.maintenance.sqlite import inspect_sqlite_footprint
from neurocore.runtime import (
    build_production_backend_choice,
    build_storage_backend_status,
    resolve_reporting_plan,
)
from neurocore.storage.base import BaseStore

_REPORTING_PROVIDER_ENV = re.compile(
    r"^NEUROCORE_REPORTING_PROVIDER_([A-Z0-9_]+)_(BASE_URL|API_KEY|MODELS|TYPE)$"
)


def diagnose_runtime(
    *,
    env: dict[str, str] | None = None,
    config: NeuroCoreConfig | None = None,
    store: BaseStore | None = None,
) -> dict[str, object]:
    """Return a sanitized runtime snapshot without exposing secrets or content."""
    values = dict(os.environ if env is None else env)
    issues: list[str] = []
    resolved_config = config
    if resolved_config is None:
        try:
            resolved_config = load_config(values)
        except ConfigError as exc:
            issues.append(str(exc))

    effective_config = resolved_config or _best_effort_config(values)
    reporting_payload = _sanitize_reporting_payload(
        build_reporting_status(effective_config)
    )
    provider_health = _provider_health_payload(effective_config)
    _apply_provider_health(reporting_payload, provider_health)

    semantic_backend = effective_config.semantic_backend
    if semantic_backend == "none":
        semantic_payload = {
            "backend": semantic_backend,
            "status": "disabled",
            "issue": None,
        }
    else:
        status, issue = sentence_transformers_status()
        semantic_payload = {
            "backend": semantic_backend,
            "status": status,
            "issue": issue,
        }
        if issue is not None:
            issues.append(issue)

    payload = {
        "config_ready": resolved_config is not None,
        "issues": list(dict.fromkeys(issues)),
        "config": _config_summary(values, effective_config),
        "storage_backend": build_storage_backend_status(
            effective_config,
            store=store,
        ).to_dict(),
        "production_backend": build_production_backend_choice(
            effective_config
        ).to_dict(),
        "semantic": semantic_payload,
        "reporting": reporting_payload,
        "provider_health": provider_health,
        "sqlite_footprint": inspect_sqlite_footprint(
            config=effective_config,
            store=store,
        ),
    }
    return payload


def _config_summary(
    values: dict[str, str], config: NeuroCoreConfig
) -> dict[str, object]:
    del values
    return {
        "default_namespace": config.default_namespace,
        "allowed_buckets": list(config.allowed_buckets),
        "default_sensitivity": config.default_sensitivity,
        "storage_backend": config.storage_backend,
        "mirror_read_preference": config.mirror_read_preference,
        "mirror_sealed_mode": config.mirror_sealed_mode,
        "semantic_backend": config.semantic_backend,
        "semantic_model_name": config.semantic_model_name,
        "enable_admin_surface": config.enable_admin_surface,
        "enable_http_adapter": config.enable_http_adapter,
        "enable_mcp_adapter": config.enable_mcp_adapter,
        "enable_dashboard": config.enable_dashboard,
        "enable_background_summarization": config.enable_background_summarization,
        "enable_multi_model_consensus": config.enable_multi_model_consensus,
    }


def _best_effort_config(values: dict[str, str]) -> NeuroCoreConfig:
    reporting_provider_configs = _best_effort_provider_configs(values)
    reporting_primary = _optional_string(
        values.get("NEUROCORE_REPORTING_PRIMARY_PROVIDER")
    )
    reporting_fallback = _optional_string(
        values.get("NEUROCORE_REPORTING_FALLBACK_PROVIDER")
    )
    if reporting_primary is not None:
        reporting_primary = reporting_primary.lower()
    if reporting_fallback is not None:
        reporting_fallback = reporting_fallback.lower()
    return NeuroCoreConfig(
        default_namespace=_safe_namespace(
            _optional_string(values.get("NEUROCORE_DEFAULT_NAMESPACE"))
        ),
        allowed_buckets=tuple(
            _safe_buckets(_csv_list(values.get("NEUROCORE_ALLOWED_BUCKETS")))
        ),
        default_sensitivity=_enum_or_default(
            _optional_string(values.get("NEUROCORE_DEFAULT_SENSITIVITY")),
            {"standard", "restricted", "sealed"},
            "standard",
        ),
        storage_backend=_enum_or_default(
            _optional_string(values.get("NEUROCORE_STORAGE_BACKEND")),
            set(VALID_STORAGE_BACKENDS),
            "in_memory",
        ),
        mirror_read_preference=_enum_or_default(
            _optional_string(values.get("NEUROCORE_MIRROR_READ_PREFERENCE")),
            set(VALID_MIRROR_READ_PREFERENCES),
            "local",
        ),
        mirror_sealed_mode=_enum_or_default(
            _optional_string(values.get("NEUROCORE_MIRROR_SEALED_MODE")),
            set(VALID_MIRROR_SEALED_MODES),
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
        semantic_backend=_enum_or_default(
            _optional_string(values.get("NEUROCORE_SEMANTIC_BACKEND")),
            set(VALID_SEMANTIC_BACKENDS),
            "none",
        ),
        semantic_model_name=values.get(
            "NEUROCORE_SEMANTIC_MODEL_NAME",
            "sentence-transformers/all-MiniLM-L6-v2",
        ),
        enable_admin_surface=_bool_env(
            values.get("NEUROCORE_ENABLE_ADMIN_SURFACE"), False
        ),
        enable_http_adapter=_bool_env(
            values.get("NEUROCORE_ENABLE_HTTP_ADAPTER"), False
        ),
        enable_mcp_adapter=_bool_env(values.get("NEUROCORE_ENABLE_MCP_ADAPTER"), False),
        enable_dashboard=_bool_env(values.get("NEUROCORE_ENABLE_DASHBOARD"), False),
        enable_background_summarization=_bool_env(
            values.get("NEUROCORE_ENABLE_BACKGROUND_SUMMARIZATION"), False
        ),
        enable_multi_model_consensus=_bool_env(
            values.get("NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS"), False
        ),
        consensus_provider=_enum_or_default(
            _optional_string(values.get("NEUROCORE_CONSENSUS_PROVIDER")),
            set(VALID_CONSENSUS_PROVIDERS),
            "none",
        ),
        consensus_model_names=tuple(
            _csv_list(values.get("NEUROCORE_CONSENSUS_MODEL_NAMES"))
        ),
        consensus_base_url=_optional_string(values.get("NEUROCORE_CONSENSUS_BASE_URL")),
        consensus_api_key=_optional_string(values.get("NEUROCORE_CONSENSUS_API_KEY")),
        reporting_strategy=_enum_or_default(
            _optional_string(values.get("NEUROCORE_REPORTING_STRATEGY")),
            set(VALID_REPORTING_STRATEGIES),
            "single_provider",
        ),
        reporting_consensus_mode=_enum_or_default(
            _optional_string(values.get("NEUROCORE_REPORTING_CONSENSUS_MODE")),
            set(VALID_REPORTING_CONSENSUS_MODES),
            "lexical_select",
        ),
        reporting_primary_provider=reporting_primary,
        reporting_fallback_provider=reporting_fallback,
        reporting_provider_configs=reporting_provider_configs,
        production_backend_provider=_enum_or_default(
            _optional_string(values.get("NEUROCORE_PRODUCTION_BACKEND_PROVIDER")),
            set(VALID_PRODUCTION_BACKEND_PROVIDERS),
            "none",
        ),
        production_database_url=_optional_string(
            values.get("NEUROCORE_PRODUCTION_DATABASE_URL")
        ),
        production_sealed_database_url=_optional_string(
            values.get("NEUROCORE_PRODUCTION_SEALED_DATABASE_URL")
        ),
    )


def _best_effort_provider_configs(
    values: dict[str, str],
) -> dict[str, ReportingProviderConfig]:
    providers: dict[str, dict[str, object]] = {}
    for key, value in values.items():
        match = _REPORTING_PROVIDER_ENV.match(key)
        if match is None:
            continue
        provider_name = match.group(1).lower()
        field_name = match.group(2).lower()
        providers.setdefault(provider_name, {})[field_name] = value
    return {
        provider_name: ReportingProviderConfig(
            name=provider_name,
            provider_type=_enum_or_default(
                _optional_string(payload.get("type")),
                {"openai_compatible"},
                "openai_compatible",
            ),
            base_url=_optional_string(payload.get("base_url")),
            api_key=_optional_string(payload.get("api_key")),
            model_names=tuple(_csv_list(payload.get("models"))),
        )
        for provider_name, payload in providers.items()
    }


def _provider_health_payload(config: NeuroCoreConfig) -> dict[str, object]:
    plan = resolve_reporting_plan(config)
    payload: dict[str, object] = {}
    providers: list[tuple[str, str, ReportingProviderConfig]] = []
    if plan.primary_provider_name and plan.primary_provider is not None:
        providers.append(("primary", plan.primary_provider_name, plan.primary_provider))
    if plan.fallback_provider_name and plan.fallback_provider is not None:
        providers.append(
            ("fallback", plan.fallback_provider_name, plan.fallback_provider)
        )
    for role, provider_name, provider in providers:
        healthy, error = check_provider_health(provider)
        payload[provider_name] = {
            "role": role,
            "healthy": healthy,
            "error": error,
            "base_url": _sanitize_target(provider.base_url),
            "model_names": list(provider.model_names),
        }
    return payload


def check_provider_health(
    provider: ReportingProviderConfig,
    *,
    timeout: float = 2.0,
) -> tuple[bool, str | None]:
    base_url = (provider.base_url or "").rstrip("/")
    if provider.provider_type != "openai_compatible":
        return False, "Unsupported provider type"
    if not base_url:
        return False, "Provider base URL is missing"
    headers = {}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    last_error = "Health check failed"
    for suffix in ("/models", "/health"):
        url = f"{base_url}{suffix}"
        req = urllib_request.Request(url=url, headers=headers, method="GET")
        try:
            with urllib_request.urlopen(req, timeout=timeout) as response:
                if 200 <= int(response.status) < 300:
                    return True, None
        except urllib_error.URLError as exc:
            error = getattr(exc, "reason", exc)
            last_error = f"Health check failed: {error}"
        except TimeoutError as exc:
            last_error = f"Health check failed: {exc}"
        else:
            last_error = f"Health check returned HTTP {response.status}"
    return False, last_error


def _sanitize_reporting_payload(payload: dict[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["base_url"] = _sanitize_target(payload.get("base_url"))
    provider_status = {}
    for provider_name, status in dict(payload.get("provider_status") or {}).items():
        status_payload = dict(status)
        status_payload["base_url"] = _sanitize_target(status.get("base_url"))
        provider_status[provider_name] = status_payload
    result["provider_status"] = provider_status
    return result


def _apply_provider_health(
    reporting_payload: dict[str, object],
    provider_health: dict[str, object],
) -> None:
    if not provider_health:
        return
    healthy_providers = [
        name
        for name, status in provider_health.items()
        if bool(dict(status).get("healthy"))
    ]
    reporting_payload["bootstrapped"] = bool(healthy_providers)
    reporting_payload["healthy"] = bool(
        reporting_payload.get("configured") and healthy_providers
    )
    if not healthy_providers and reporting_payload.get("configured"):
        reporting_payload["status"] = "degraded"
        reporting_payload["active_provider"] = None
    elif (
        healthy_providers
        and reporting_payload.get("active_provider") not in healthy_providers
    ):
        reporting_payload["active_provider"] = healthy_providers[0]


def _sanitize_target(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    parsed = urlparse(text)
    if parsed.scheme and parsed.hostname:
        if parsed.port is not None:
            return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        return f"{parsed.scheme}://{parsed.hostname}"
    return "configured"


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _csv_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _enum_or_default(value: str | None, allowed: set[str], default: str) -> str:
    if value is None:
        return default
    return value if value in allowed else default


def _bool_env(value: object, default: bool) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_namespace(value: str | None) -> str:
    if value and NAMESPACE_PATTERN.match(value):
        return value
    return "diagnostic"


def _safe_buckets(values: list[str]) -> list[str]:
    buckets = [value for value in values if BUCKET_PATTERN.match(value)]
    return buckets or ["diagnostic"]
