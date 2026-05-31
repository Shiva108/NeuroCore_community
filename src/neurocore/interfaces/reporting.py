"""Public reporting interfaces built on top of NeuroCore retrieval."""

from __future__ import annotations

from typing import Any

from neurocore.core.brains import apply_brain_namespace
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.briefing import generate_briefing
from neurocore.interfaces.query import query_memory
from neurocore.reporting.consensus import ReportGenerator
from neurocore.reporting.workflows import build_report_context_from_query_response
from neurocore.runtime import build_reporter, resolve_reporting_plan
from neurocore.storage.base import BaseStore


def build_reporting_status(config: NeuroCoreConfig) -> dict[str, object]:
    plan = resolve_reporting_plan(config)
    primary_provider = plan.primary_provider
    fallback_provider = plan.fallback_provider
    consensus_mode = getattr(config, "reporting_consensus_mode", "lexical_select")
    judge_required = consensus_mode == "claim_voting_with_judge"
    provider = (
        str(primary_provider.provider_type)
        if primary_provider is not None
        else str(config.consensus_provider or "none")
    )
    base_url = (
        str(primary_provider.base_url or "").strip()
        if primary_provider is not None
        else str(config.consensus_base_url or "").strip()
    )
    model_names = (
        list(primary_provider.model_names)
        if primary_provider
        else list(config.consensus_model_names)
    )
    if not config.enable_multi_model_consensus:
        return {
            "provider": provider,
            "status": "fallback-only",
            "configured": False,
            "bootstrapped": False,
            "healthy": False,
            "strategy": plan.strategy,
            "consensus_mode": consensus_mode,
            "judge_required": judge_required,
            "judge_ready": False,
            "judge_provider": None,
            "judge_model": None,
            "active_provider": None,
            "primary_provider": plan.primary_provider_name,
            "fallback_provider": plan.fallback_provider_name,
            "issues": ["Consensus reporting is disabled."],
        }
    issues: list[str] = []
    configured = True
    try:
        build_reporter(config)
    except Exception as exc:
        configured = False
        issues.append(str(exc))
    bootstrapped = bool(base_url) if provider == "openai_compatible" else configured
    healthy = configured and bootstrapped
    status = "healthy" if healthy else ("configured" if configured else "degraded")
    judge_ready = bool(
        judge_required
        and fallback_provider is not None
        and fallback_provider.base_url
        and fallback_provider.api_key
        and fallback_provider.model_names
    )
    judge_model = (
        fallback_provider.model_names[0]
        if judge_required and fallback_provider and fallback_provider.model_names
        else None
    )
    provider_status = {}
    if primary_provider is not None:
        provider_status[primary_provider.name] = {
            "role": "primary",
            "provider_type": primary_provider.provider_type,
            "base_url": primary_provider.base_url,
            "model_names": list(primary_provider.model_names),
            "configured": bool(
                primary_provider.base_url
                and primary_provider.api_key
                and primary_provider.model_names
            ),
        }
    if fallback_provider is not None:
        provider_status[fallback_provider.name] = {
            "role": "fallback",
            "provider_type": fallback_provider.provider_type,
            "base_url": fallback_provider.base_url,
            "model_names": list(fallback_provider.model_names),
            "configured": bool(
                fallback_provider.base_url
                and fallback_provider.api_key
                and fallback_provider.model_names
            ),
        }
    return {
        "provider": provider,
        "status": status,
        "configured": configured,
        "bootstrapped": bootstrapped,
        "healthy": healthy,
        "model_names": model_names,
        "base_url": base_url or None,
        "strategy": plan.strategy,
        "consensus_mode": consensus_mode,
        "judge_required": judge_required,
        "judge_ready": judge_ready,
        "judge_provider": plan.fallback_provider_name if judge_required else None,
        "judge_model": judge_model,
        "active_provider": plan.primary_provider_name if configured else None,
        "primary_provider": plan.primary_provider_name,
        "fallback_provider": plan.fallback_provider_name,
        "provider_status": provider_status,
        "issues": issues,
    }


def generate_consensus_report(
    request: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None = None,
    reporter: ReportGenerator | Any | None = None,
) -> dict[str, object]:
    """Generate a multi-model consensus report from explicit or queried context."""
    objective = str(request.get("objective", "")).strip()
    if not objective:
        raise ValueError("objective is required")

    max_items = _parse_max_items(request.get("max_items", 5))
    sections = _parse_sections(request.get("sections"))
    context_source, context_markdown, query_id = _resolve_context(
        request,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
        max_items=max_items,
        brain_id=_optional_query_id({"query_id": request.get("brain_id")}),
    )
    try:
        if not config.enable_multi_model_consensus:
            raise PermissionError("Reporting is disabled")
        active_reporter = reporter or build_reporter(config)
        result = active_reporter.generate(
            objective=objective,
            context_markdown=context_markdown,
            sections=sections,
        )
        payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        metadata = dict(payload.get("metadata", {}))
        metadata["context_source"] = context_source
        reporting_status = build_reporting_status(config)
        active_provider = metadata.get("active_provider")
        if active_provider:
            reporting_status["active_provider"] = active_provider
        if "fallback_used" in metadata:
            reporting_status["fallback_used"] = bool(metadata["fallback_used"])
        metadata["reporting_status"] = reporting_status
        if query_id:
            metadata["query_id"] = query_id
        payload["metadata"] = metadata
        payload["context_markdown"] = context_markdown
        payload["mode"] = "report"
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        fallback = _fallback_briefing_response(
            request,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
            max_items=max_items,
            fallback_reason=str(exc),
            context_markdown=context_markdown,
        )
        metadata = dict(fallback.get("metadata", {}))
        metadata["context_source"] = context_source
        metadata["reporting_status"] = build_reporting_status(config)
        if query_id:
            metadata["query_id"] = query_id
        fallback["metadata"] = metadata
        return fallback


def _resolve_context(
    request: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
    max_items: int,
    brain_id: str | None,
) -> tuple[str, str, str | None]:
    raw_context = request.get("context_markdown")
    if isinstance(raw_context, str) and raw_context.strip():
        return "context_markdown", raw_context.strip(), None

    raw_query_response = request.get("query_response")
    if isinstance(raw_query_response, dict):
        return (
            "query_response",
            build_report_context_from_query_response(
                raw_query_response, max_items=max_items
            ),
            _optional_query_id(raw_query_response),
        )

    raw_query_request = request.get("query_request")
    if isinstance(raw_query_request, dict):
        query_request = apply_brain_namespace(
            {
                **dict(raw_query_request),
                **({"brain_id": brain_id} if brain_id else {}),
            },
            store=store,
            default_namespace=config.default_namespace,
        )
        query_response = query_memory(
            query_request,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        )
        return (
            "query_request",
            build_report_context_from_query_response(
                query_response, max_items=max_items
            ),
            _optional_query_id(query_response),
        )

    raise ValueError(
        "request must include context_markdown, query_response, or query_request"
    )


def _parse_max_items(raw: object) -> int:
    if isinstance(raw, bool):
        raise ValueError("max_items must be an integer")
    try:
        max_items = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_items must be an integer") from exc
    if max_items < 1:
        raise ValueError("max_items must be >= 1")
    return max_items


def _parse_sections(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ("Overview", "Findings", "Risks", "Actions")
    if not isinstance(raw, (list, tuple)):
        raise ValueError("sections must be a list of strings")
    sections = tuple(str(section).strip() for section in raw if str(section).strip())
    if not sections:
        raise ValueError("sections must include at least one heading")
    return sections


def _optional_query_id(query_response: dict[str, object]) -> str | None:
    query_id = query_response.get("query_id")
    if query_id in (None, ""):
        return None
    return str(query_id)


def _fallback_briefing_response(
    request: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
    max_items: int,
    fallback_reason: str,
    context_markdown: str,
) -> dict[str, object]:
    briefing_request: dict[str, object] = {
        "include_operator_hints": True,
        "max_items": max_items,
    }
    if isinstance(request.get("query_response"), dict):
        briefing_request["query_response"] = request["query_response"]
    elif isinstance(request.get("query_request"), dict):
        briefing_request["query_request"] = request["query_request"]
    elif context_markdown.strip():
        briefing_request["context_markdown"] = context_markdown
    if request.get("brain_id") not in (None, ""):
        briefing_request["brain_id"] = request["brain_id"]
    briefing = generate_briefing(
        briefing_request,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
    )
    metadata = dict(briefing.get("metadata", {}))
    metadata["fallback_reason"] = fallback_reason
    return {
        "mode": "fallback-briefing",
        "report": briefing["briefing"],
        "metadata": metadata,
    }
