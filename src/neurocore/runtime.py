"""Runtime factories for NeuroCore storage, ranking, and summarization."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from neurocore.core.config import NeuroCoreConfig, ReportingProviderConfig
from neurocore.core.operator_state import mirror_status_path
from neurocore.retrieval.rankers import SemanticRanker, SentenceTransformersRanker
from neurocore.reporting.consensus import (
    MultiModelConsensusReporter,
    OpenAICompatibleReportClient,
    PrimaryWithFallbackReporter,
    SingleModelReportReporter,
)
from neurocore.storage.base import BaseStore
from neurocore.storage.in_memory import InMemoryStore
from neurocore.storage.local_only_sealed_mirrored_store import (
    LocalOnlySealedMirroredStore,
)
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage.postgres_store import PostgresStore
from neurocore.storage.router import RoutedStore
from neurocore.storage.sqlite_store import SQLiteStore
from neurocore.summarization.background import Summarizer
from neurocore.summarization.consensus import (
    ConsensusSummarizer,
    MultiModelConsensusSummarizer,
    OpenAICompatibleSummaryClient,
)


@dataclass(frozen=True)
class ReportingPlan:
    """Resolved reporting topology after config parsing."""

    strategy: str
    primary_provider_name: str | None
    primary_provider: ReportingProviderConfig | None
    fallback_provider_name: str | None = None
    fallback_provider: ReportingProviderConfig | None = None
    uses_legacy_consensus: bool = False


def build_store(config: NeuroCoreConfig) -> BaseStore:
    """Build the configured routed storage backend."""
    if config.storage_backend == "sqlite":
        return RoutedStore(
            primary_store=SQLiteStore(config.primary_store_path),
            sealed_store=SQLiteStore(config.sealed_store_path),
        )
    if config.storage_backend == "postgres":
        return _build_cloud_store(config)
    if config.storage_backend == "mirror":
        local_store = RoutedStore(
            primary_store=SQLiteStore(config.primary_store_path),
            sealed_store=SQLiteStore(config.sealed_store_path),
        )
        if config.mirror_sealed_mode == "local_only":
            return LocalOnlySealedMirroredStore(
                local_store=local_store,
                cloud_primary_store=PostgresStore(config.production_database_url or ""),
                read_preference=config.mirror_read_preference,
                status_path=mirror_status_path(),
            )
        return MirroredStore(
            local_store=local_store,
            cloud_store=_build_cloud_store(config),
            read_preference=config.mirror_read_preference,
            status_path=mirror_status_path(),
        )
    return RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())


def _build_cloud_store(config: NeuroCoreConfig) -> BaseStore:
    """Build the hosted storage backend after validating required URLs."""
    if config.production_backend_provider == "none":
        raise ValueError(
            f"{config.storage_backend.capitalize()} storage backend requires "
            "a configured production backend provider"
        )
    if not config.production_database_url or not config.production_sealed_database_url:
        raise ValueError(
            f"{config.storage_backend.capitalize()} storage backend requires "
            "primary and sealed production database URLs"
        )
    if config.production_database_url == config.production_sealed_database_url:
        # When both URLs point at the same physical database, wrapping the same
        # store twice through RoutedStore duplicates list/read traversals and
        # breaks parity/reconciliation flows that inventory the cloud side.
        return PostgresStore(config.production_database_url)
    return RoutedStore(
        primary_store=PostgresStore(config.production_database_url),
        sealed_store=PostgresStore(config.production_sealed_database_url),
    )


def build_semantic_ranker(config: NeuroCoreConfig) -> SemanticRanker | None:
    """Build the configured semantic ranker when one is enabled."""
    if config.semantic_backend == "sentence-transformers":
        return SentenceTransformersRanker(config.semantic_model_name)
    return None


def build_summarizer(config: NeuroCoreConfig) -> Summarizer:
    """Build the summary engine for the current runtime configuration."""
    if config.enable_multi_model_consensus:
        primary = resolve_reporting_plan(config).primary_provider
        if primary is None:
            raise ValueError(
                "Multi-model consensus requires a configured primary provider"
            )
        _validate_multi_model_provider(primary)
        return MultiModelConsensusSummarizer(
            model_client=OpenAICompatibleSummaryClient(
                base_url=primary.base_url or "",
                api_key=primary.api_key,
            ),
            model_names=primary.model_names,
        )
    return ConsensusSummarizer()


def build_reporter(
    config: NeuroCoreConfig,
) -> MultiModelConsensusReporter | PrimaryWithFallbackReporter:
    """Build the consensus reporting engine for the current runtime."""
    if not config.enable_multi_model_consensus:
        raise PermissionError("Reporting is disabled")
    plan = resolve_reporting_plan(config)
    consensus_mode = getattr(config, "reporting_consensus_mode", "lexical_select")
    primary = plan.primary_provider
    if primary is None:
        raise ValueError("Consensus reporting requires a configured primary provider")
    if consensus_mode == "claim_voting_with_judge" and plan.fallback_provider is None:
        raise ValueError(
            "Judge-backed claim voting requires a configured fallback provider"
        )
    _validate_multi_model_provider(primary)
    primary_reporter = MultiModelConsensusReporter(
        model_client=OpenAICompatibleReportClient(
            base_url=primary.base_url or "",
            api_key=primary.api_key,
        ),
        model_names=primary.model_names,
        provider_name=plan.primary_provider_name or primary.name,
        consensus_mode=consensus_mode,
        judge_client=(
            OpenAICompatibleReportClient(
                base_url=plan.fallback_provider.base_url or "",
                api_key=plan.fallback_provider.api_key,
            )
            if (
                consensus_mode == "claim_voting_with_judge"
                and plan.fallback_provider is not None
            )
            else None
        ),
        judge_model_name=(
            plan.fallback_provider.model_names[0]
            if (
                consensus_mode == "claim_voting_with_judge"
                and plan.fallback_provider is not None
            )
            else None
        ),
        judge_provider_name=(
            plan.fallback_provider_name
            if consensus_mode == "claim_voting_with_judge"
            else None
        ),
    )
    if plan.strategy != "primary_with_fallback" or plan.fallback_provider is None:
        return primary_reporter

    fallback = plan.fallback_provider
    _validate_single_model_provider(fallback)
    fallback_reporter = SingleModelReportReporter(
        model_client=OpenAICompatibleReportClient(
            base_url=fallback.base_url or "",
            api_key=fallback.api_key,
        ),
        model_name=fallback.model_names[0],
        provider_name=plan.fallback_provider_name or fallback.name,
    )
    return PrimaryWithFallbackReporter(
        primary_reporter=primary_reporter,
        fallback_reporter=fallback_reporter,
        primary_provider_name=plan.primary_provider_name or primary.name,
        fallback_provider_name=plan.fallback_provider_name or fallback.name,
    )


def resolve_reporting_plan(config: NeuroCoreConfig) -> ReportingPlan:
    """Resolve provider-aware reporting config with legacy fallback."""
    provider_registry = getattr(config, "reporting_provider_configs", {}) or {}
    if bool(provider_registry):
        primary_name = getattr(config, "reporting_primary_provider", None)
        primary_provider = (
            provider_registry.get(primary_name or "") if primary_name else None
        )
        fallback_name = getattr(config, "reporting_fallback_provider", None)
        fallback_provider = (
            provider_registry.get(fallback_name or "") if fallback_name else None
        )
        return ReportingPlan(
            strategy=getattr(config, "reporting_strategy", "single_provider"),
            primary_provider_name=primary_name,
            primary_provider=primary_provider,
            fallback_provider_name=fallback_name,
            fallback_provider=fallback_provider,
            uses_legacy_consensus=False,
        )

    primary = ReportingProviderConfig(
        name="consensus",
        provider_type=getattr(config, "consensus_provider", "none"),
        base_url=getattr(config, "consensus_base_url", None),
        api_key=getattr(config, "consensus_api_key", None),
        model_names=getattr(config, "consensus_model_names", ()),
    )
    return ReportingPlan(
        strategy="single_provider",
        primary_provider_name="consensus",
        primary_provider=primary if primary.provider_type else None,
        uses_legacy_consensus=True,
    )


def _validate_supported_provider(provider: ReportingProviderConfig) -> None:
    """Reject provider configurations unsupported by the reporting layer."""
    if provider.provider_type != "openai_compatible":
        raise ValueError("Consensus reporting requires a supported consensus provider")


def _validate_multi_model_provider(provider: ReportingProviderConfig) -> None:
    """Validate a provider intended for multi-model consensus execution."""
    _validate_supported_provider(provider)
    if len(provider.model_names) < 2:
        raise ValueError(
            "Consensus reporting requires at least two configured model names"
        )
    if len(set(provider.model_names)) != len(provider.model_names):
        raise ValueError("Consensus reporting requires unique model names")
    if not provider.base_url:
        raise ValueError("Consensus reporting requires a consensus base URL")
    if not provider.api_key:
        raise ValueError("Consensus reporting requires a consensus API key")


def _validate_single_model_provider(provider: ReportingProviderConfig) -> None:
    """Validate a provider intended for single-model fallback execution."""
    _validate_supported_provider(provider)
    if not provider.model_names:
        raise ValueError(
            "Consensus reporting fallback requires at least one model name"
        )
    if len(set(provider.model_names)) != len(provider.model_names):
        raise ValueError("Consensus reporting requires unique model names")
    if not provider.base_url:
        raise ValueError("Consensus reporting requires a consensus base URL")
    if not provider.api_key:
        raise ValueError("Consensus reporting requires a consensus API key")


@dataclass(frozen=True)
class ProductionBackendChoice:
    """Sanitized view of the production backend configuration."""

    provider: str
    primary_configured: bool
    sealed_configured: bool
    status: str
    primary_url: str | None = None
    sealed_url: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a redacted dashboard-safe representation."""
        payload = asdict(self)
        payload["primary_url"] = None
        payload["sealed_url"] = None
        payload["primary_target"] = _redact_target(self.primary_url)
        payload["sealed_target"] = _redact_target(self.sealed_url)
        return payload


@dataclass(frozen=True)
class StorageBackendStatus:
    """Dashboard-safe summary of active storage topology."""

    mode: str
    read_preference: str
    sealed_mode: str
    local_configured: bool
    cloud_configured: bool
    cloud_primary_configured: bool
    cloud_sealed_configured: bool
    full_dual_write_configured: bool
    cloud_provider: str
    local_degraded: bool = False
    cloud_degraded: bool = False
    last_local_error: str | None = None
    last_cloud_error: str | None = None
    last_persistence_state: str | None = None
    last_parity_state: str | None = None
    last_full_mirror_state: str | None = None
    parity_verified: bool | None = None
    reconciliation_pending: bool = False
    automatic_reconciliation_attempted: bool = False
    last_reconciliation_direction: str | None = None
    last_reconciliation_outcome: str | None = None
    last_parity_check: str | None = None
    bidirectional_divergence: bool = False
    destructive_repair_risk: bool = False
    recommended_safe_action: str | None = None
    conflict_counts: dict[str, int] | None = None
    repair_mode: str | None = None
    last_sync_action: str | None = None
    last_sync_started_at: str | None = None
    last_sync_finished_at: str | None = None
    last_sync_pid: int | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    active_reconciliation: bool = False
    primary_url: str | None = None
    sealed_url: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["primary_url"] = None
        payload["sealed_url"] = None
        payload["primary_target"] = _redact_target(self.primary_url)
        payload["sealed_target"] = _redact_target(self.sealed_url)
        return payload


def build_production_backend_choice(config: NeuroCoreConfig) -> ProductionBackendChoice:
    """Summarize production backend readiness without exposing secrets."""
    if config.production_backend_provider == "none":
        return ProductionBackendChoice(
            provider="none",
            primary_configured=False,
            sealed_configured=False,
            status="disabled",
        )

    primary_configured = bool(config.production_database_url)
    sealed_configured = bool(config.production_sealed_database_url)
    status = "configured" if primary_configured and sealed_configured else "partial"
    return ProductionBackendChoice(
        provider=config.production_backend_provider,
        primary_configured=primary_configured,
        sealed_configured=sealed_configured,
        status=status,
        primary_url=config.production_database_url,
        sealed_url=config.production_sealed_database_url,
    )


def build_storage_backend_status(
    config: NeuroCoreConfig,
    *,
    store: BaseStore | None = None,
) -> StorageBackendStatus:
    """Summarize the active storage backend and mirror health."""
    mirror_status = store.mirror_status() if isinstance(store, MirroredStore) else {}
    if isinstance(store, LocalOnlySealedMirroredStore):
        mirror_status = store.mirror_status()
    local_configured = bool(config.primary_store_path and config.sealed_store_path)
    cloud_primary_configured = bool(config.production_database_url)
    cloud_sealed_configured = bool(config.production_sealed_database_url)
    cloud_configured = cloud_primary_configured and (
        config.storage_backend != "mirror"
        or config.mirror_sealed_mode == "local_only"
        or cloud_sealed_configured
    )
    full_dual_write_configured = (
        config.storage_backend == "mirror"
        and config.mirror_sealed_mode == "full"
        and config.production_backend_provider != "none"
        and cloud_primary_configured
        and cloud_sealed_configured
    )
    read_preference = (
        config.mirror_read_preference
        if config.storage_backend == "mirror"
        else ("cloud" if config.storage_backend == "postgres" else "local")
    )
    return StorageBackendStatus(
        mode=config.storage_backend,
        read_preference=read_preference,
        sealed_mode=(
            config.mirror_sealed_mode if config.storage_backend == "mirror" else "full"
        ),
        local_configured=local_configured,
        cloud_configured=cloud_configured,
        cloud_primary_configured=cloud_primary_configured,
        cloud_sealed_configured=cloud_sealed_configured,
        full_dual_write_configured=full_dual_write_configured,
        cloud_provider=config.production_backend_provider,
        local_degraded=bool(mirror_status.get("local_degraded", False)),
        cloud_degraded=bool(mirror_status.get("cloud_degraded", False)),
        last_local_error=mirror_status.get("last_local_error"),
        last_cloud_error=mirror_status.get("last_cloud_error"),
        last_persistence_state=mirror_status.get("last_persistence_state"),
        last_parity_state=mirror_status.get("last_parity_state"),
        last_full_mirror_state=mirror_status.get("last_full_mirror_state"),
        parity_verified=mirror_status.get("parity_verified"),
        reconciliation_pending=bool(mirror_status.get("reconciliation_pending", False)),
        automatic_reconciliation_attempted=bool(
            mirror_status.get("automatic_reconciliation_attempted", False)
        ),
        last_reconciliation_direction=mirror_status.get(
            "last_reconciliation_direction"
        ),
        last_reconciliation_outcome=mirror_status.get("last_reconciliation_outcome"),
        last_parity_check=mirror_status.get("last_parity_check"),
        bidirectional_divergence=bool(
            mirror_status.get("bidirectional_divergence", False)
        ),
        destructive_repair_risk=bool(
            mirror_status.get("destructive_repair_risk", False)
        ),
        recommended_safe_action=mirror_status.get("recommended_safe_action"),
        conflict_counts=mirror_status.get("conflict_counts"),
        repair_mode=mirror_status.get("repair_mode"),
        last_sync_action=mirror_status.get("last_sync_action"),
        last_sync_started_at=mirror_status.get("last_sync_started_at"),
        last_sync_finished_at=mirror_status.get("last_sync_finished_at"),
        last_sync_pid=mirror_status.get("last_sync_pid"),
        last_sync_status=mirror_status.get("last_sync_status"),
        last_sync_error=mirror_status.get("last_sync_error"),
        active_reconciliation=bool(mirror_status.get("active_reconciliation", False)),
        primary_url=config.production_database_url,
        sealed_url=(
            None
            if config.storage_backend == "mirror"
            and config.mirror_sealed_mode == "local_only"
            else config.production_sealed_database_url
        ),
    )


def _redact_target(value: str | None) -> str | None:
    """Strip sensitive path and credential details from connection targets."""
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname:
        if parsed.port is not None:
            return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        return f"{parsed.scheme}://{parsed.hostname}"
    return "configured"
