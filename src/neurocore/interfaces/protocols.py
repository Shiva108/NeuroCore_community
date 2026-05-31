"""Named protocol/orchestration entrypoints built on top of NeuroCore retrieval."""

from __future__ import annotations

import re
from typing import Any, Callable

from neurocore.core.brains import apply_brain_namespace
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.admin import audit_memory
from neurocore.interfaces.briefing import generate_briefing
from neurocore.interfaces.query import query_memory
from neurocore.interfaces.reporting import generate_consensus_report
from neurocore.interfaces.sessions import resume_session
from neurocore.storage.base import BaseStore

ProtocolExecutor = Callable[..., dict[str, object]]


PROTOCOLS: dict[str, dict[str, object]] = {
    "resume-brain-v1": {
        "name": "resume-brain-v1",
        "purpose": "Resume a prior AI/client session from shared durable memory.",
        "query_preset": "session-resume",
        "bucket_scope": ["agents", "ops", "reports"],
        "objective_template": "Summarize the most relevant prior session checkpoints and next actions.",
        "required_sections": ["Overview", "Relevant Memory", "Next Actions"],
        "prioritization_strategy": "session-checkpoints+importance",
        "output_mode": "markdown-briefing",
        "supports_fallback": True,
    },
    "project-review-v1": {
        "name": "project-review-v1",
        "purpose": "Review the current state of a brain-scoped project.",
        "query_preset": "project-review",
        "bucket_scope": ["research", "reports", "agents", "ops", "findings", "recon"],
        "objective_template": "Summarize the current project state, important memory, risks, and next actions.",
        "required_sections": ["Overview", "Findings", "Risks", "Actions"],
        "prioritization_strategy": "severity+recency+operator-concern",
        "output_mode": "markdown-report",
        "supports_fallback": True,
    },
    "memory-audit-v1": {
        "name": "memory-audit-v1",
        "purpose": "Audit a brain for sensitive content and remediation candidates.",
        "query_preset": "memory-audit",
        "bucket_scope": ["recon", "findings", "reports", "agents", "ops"],
        "objective_template": "Summarize the most important sensitive-memory findings and remediation actions.",
        "required_sections": ["Overview", "Findings", "Risks", "Actions"],
        "prioritization_strategy": "secret-like-values+severity",
        "output_mode": "markdown-report",
        "supports_fallback": True,
    },
    "cti-review-v1": {
        "name": "cti-review-v1",
        "purpose": "Summarize high-risk CTI memory indicators and actions.",
        "query_preset": "cti-priority",
        "bucket_scope": ["findings", "reports", "agents", "ops", "recon"],
        "objective_template": "Summarize the highest-risk CTI memory indicators and next actions.",
        "required_sections": ["Overview", "Findings", "Risks", "Actions"],
        "prioritization_strategy": "severity+intel-tags+operator-concern",
        "output_mode": "markdown-report",
        "supports_fallback": True,
    },
    "engagement-review-v1": {
        "name": "engagement-review-v1",
        "purpose": "Review a security engagement from durable checkpoints and findings.",
        "query_preset": "engagement-review",
        "bucket_scope": ["findings", "reports", "agents", "ops", "recon"],
        "objective_template": "Summarize the engagement status, confirmed findings, gaps, and next actions.",
        "required_sections": ["Overview", "Findings", "Risks", "Actions"],
        "prioritization_strategy": "validated-findings+checkpoint-priority",
        "output_mode": "markdown-report",
        "supports_fallback": True,
    },
    "brain-inbox-triage-v1": {
        "name": "brain-inbox-triage-v1",
        "purpose": "Prioritize recent memory requiring operator attention.",
        "query_preset": "brain-inbox-triage",
        "bucket_scope": ["agents", "ops", "reports", "findings"],
        "objective_template": "Summarize what matters now and what should be triaged first.",
        "required_sections": ["Overview", "Findings", "Risks", "Actions"],
        "prioritization_strategy": "severity+importance+recency+operator-concern",
        "output_mode": "markdown-report",
        "supports_fallback": True,
    },
    "operator-briefing-v1": {
        "name": "operator-briefing-v1",
        "purpose": "Prepare a concise operator briefing from shared memory.",
        "query_preset": "operator-briefing",
        "bucket_scope": ["agents", "ops", "findings", "reports"],
        "objective_template": "Summarize the current operator state, decisions, and next actions.",
        "required_sections": ["Overview", "Relevant Memory", "Next Actions"],
        "prioritization_strategy": "importance+validated-findings+operator-concern",
        "output_mode": "markdown-briefing",
        "supports_fallback": True,
    },
    "project-handoff-v1": {
        "name": "project-handoff-v1",
        "purpose": "Create a handoff summary for another operator or model.",
        "query_preset": "project-handoff",
        "bucket_scope": ["agents", "ops", "reports", "findings", "recon"],
        "objective_template": "Summarize the current project state and the next handoff actions.",
        "required_sections": ["Overview", "Findings", "Risks", "Actions"],
        "prioritization_strategy": "importance+checkpoint-status+recency",
        "output_mode": "markdown-report",
        "supports_fallback": True,
    },
    "session-review-v1": {
        "name": "session-review-v1",
        "purpose": "Review the key outcomes from a prior session.",
        "query_preset": "session-review",
        "bucket_scope": ["agents", "ops", "reports"],
        "objective_template": "Summarize the most important prior session outcomes and pending actions.",
        "required_sections": ["Overview", "Relevant Memory", "Next Actions"],
        "prioritization_strategy": "session-checkpoints+importance+recency",
        "output_mode": "markdown-briefing",
        "supports_fallback": True,
    },
    "engagement-next-actions-v1": {
        "name": "engagement-next-actions-v1",
        "purpose": "Prioritize the next concrete actions for an active engagement.",
        "query_preset": "engagement-next-actions",
        "bucket_scope": ["findings", "ops", "agents", "reports", "recon"],
        "objective_template": "Summarize the most important next actions for the engagement.",
        "required_sections": ["Overview", "Findings", "Risks", "Actions"],
        "prioritization_strategy": "validated-findings+exploitability+importance",
        "output_mode": "markdown-report",
        "supports_fallback": True,
    },
    "report-prep-v1": {
        "name": "report-prep-v1",
        "purpose": "Prepare the most relevant evidence and framing for final reporting.",
        "query_preset": "report-prep",
        "bucket_scope": ["reports", "findings", "ops", "agents"],
        "objective_template": "Summarize the most important evidence and framing for the final report.",
        "required_sections": ["Overview", "Findings", "Risks", "Actions"],
        "prioritization_strategy": "severity+validated-findings+importance+recency",
        "output_mode": "markdown-report",
        "supports_fallback": True,
    },
}


def list_protocols() -> list[dict[str, object]]:
    """Return the currently supported named protocol manifests."""
    return [dict(PROTOCOLS[name]) for name in sorted(PROTOCOLS)]


def run_protocol(
    request: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None = None,
    reporter: Any | None = None,
) -> dict[str, object]:
    name = str(request.get("name") or "").strip()
    manifest = PROTOCOLS.get(name)
    if manifest is None:
        raise ValueError(f"Unknown protocol: {name or '<missing>'}")
    if name == "resume-brain-v1":
        return _run_resume_protocol(
            request,
            manifest=manifest,
            store=store,
            config=config,
        )
    if name == "memory-audit-v1":
        return _run_memory_audit_protocol(
            request,
            manifest=manifest,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
            reporter=reporter,
        )
    return _run_query_backed_protocol(
        request,
        manifest=manifest,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
        reporter=reporter,
    )


def _run_resume_protocol(
    request: dict[str, object],
    *,
    manifest: dict[str, object],
    store: BaseStore,
    config: NeuroCoreConfig,
) -> dict[str, object]:
    if str(request.get("session_id") or "").strip():
        payload = resume_session(request, store=store, config=config)
        return {
            "mode": "briefing",
            "report": payload["briefing"],
            "metadata": payload.get("metadata", {}),
            "protocol": {
                **manifest,
                "ranked_result_count": len(
                    payload.get("query_response", {}).get("results", [])
                ),
                "output_mode": "briefing",
            },
        }

    resolved = apply_brain_namespace(
        request, store=store, default_namespace=config.default_namespace
    )
    query_payload = query_memory(
        {
            "brain_id": resolved.get("brain_id"),
            "namespace": resolved["namespace"],
            "query_text": str(
                request.get("query_text") or "recent checkpoints and next actions"
            ),
            "allowed_buckets": list(
                request.get("allowed_buckets") or manifest["bucket_scope"]
            ),
            "sensitivity_ceiling": str(
                request.get("sensitivity_ceiling") or config.default_sensitivity
            ),
            "top_k": int(request.get("top_k", 6)),
            "tags_any": list(
                request.get("tags_any") or ["artifact:session-checkpoint"]
            ),
        },
        store=store,
        config=config,
    )
    briefing_payload = generate_briefing(
        {
            "brain_id": resolved.get("brain_id"),
            "query_response": query_payload,
            "sections": list(request.get("sections") or manifest["required_sections"]),
            "max_items": int(request.get("max_items", 6)),
            "include_operator_hints": False,
        },
        store=store,
        config=config,
    )
    return {
        "mode": "briefing",
        "report": briefing_payload["briefing"],
        "metadata": briefing_payload.get("metadata", {}),
        "protocol": {
            **manifest,
            "ranked_result_count": len(query_payload.get("results", [])),
            "output_mode": "briefing",
        },
    }


def _run_memory_audit_protocol(
    request: dict[str, object],
    *,
    manifest: dict[str, object],
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
    reporter: Any | None,
) -> dict[str, object]:
    resolved = apply_brain_namespace(
        request, store=store, default_namespace=config.default_namespace
    )
    audit_payload = audit_memory(
        {
            "namespace": resolved["namespace"],
            "allowed_buckets": request.get("allowed_buckets")
            or manifest["bucket_scope"],
            "include_archived": bool(request.get("include_archived", False)),
        },
        store=store,
        config=config,
    )
    findings = audit_payload.get("findings", [])
    context_markdown = _render_audit_context(
        findings if isinstance(findings, list) else []
    )
    report_request = {
        "objective": str(request.get("objective") or manifest["objective_template"]),
        "context_markdown": context_markdown,
        "sections": list(request.get("sections") or manifest["required_sections"]),
        "max_items": int(request.get("max_items", 8)),
        "brain_id": resolved.get("brain_id"),
    }
    payload = generate_consensus_report(
        report_request,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
        reporter=reporter,
    )
    if str(payload.get("mode") or "") == "fallback-briefing":
        payload["report"] = _render_audit_fallback(
            findings if isinstance(findings, list) else []
        )
    return {
        **payload,
        "protocol": {
            **manifest,
            "query_request": {
                "namespace": resolved["namespace"],
                "allowed_buckets": request.get("allowed_buckets")
                or manifest["bucket_scope"],
            },
            "ranked_result_count": len(findings) if isinstance(findings, list) else 0,
            "output_mode": payload.get("mode", manifest["output_mode"]),
        },
    }


def _run_query_backed_protocol(
    request: dict[str, object],
    *,
    manifest: dict[str, object],
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
    reporter: Any | None,
) -> dict[str, object]:
    resolved = apply_brain_namespace(
        request, store=store, default_namespace=config.default_namespace
    )
    query_text = str(request.get("query_text") or "").strip()
    if not query_text:
        raise ValueError("query_text is required")
    query_request = {
        "brain_id": resolved.get("brain_id"),
        "namespace": resolved["namespace"],
        "query_text": query_text,
        "allowed_buckets": list(
            request.get("allowed_buckets") or manifest["bucket_scope"]
        ),
        "sensitivity_ceiling": str(
            request.get("sensitivity_ceiling") or config.default_sensitivity
        ),
        "top_k": int(request.get("top_k", 8)),
    }
    raw_tags_any = list(request.get("tags_any") or [])
    if raw_tags_any:
        query_request["tags_any"] = raw_tags_any
    query_payload = query_memory(
        query_request,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
    )
    ranked_results = prioritize_memory_results(
        query_payload.get("results", []),
        strategy=str(manifest.get("prioritization_strategy") or ""),
    )
    context_markdown = _render_protocol_context(
        ranked_results[: int(request.get("max_items", 8))]
    )
    report_request = {
        "brain_id": resolved.get("brain_id"),
        "objective": str(request.get("objective") or manifest["objective_template"]),
        "query_request": query_request,
        "max_items": int(request.get("max_items", 8)),
        "sections": list(request.get("sections") or manifest["required_sections"]),
        "context_markdown": context_markdown,
    }
    payload = generate_consensus_report(
        report_request,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
        reporter=reporter,
    )
    if str(payload.get("mode") or "") == "fallback-briefing":
        payload["report"] = _render_protocol_fallback(ranked_results)
    return {
        **payload,
        "protocol": {
            **manifest,
            "query_request": query_request,
            "ranked_result_count": len(ranked_results),
            "output_mode": payload.get("mode", manifest["output_mode"]),
        },
    }


def prioritize_memory_results(
    results: object, *, strategy: str
) -> list[dict[str, object]]:
    if not isinstance(results, list):
        return []

    def score(result: object) -> int:
        if not isinstance(result, dict):
            return 0
        metadata = result.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        tags = metadata.get("tags", [])
        tags = tags if isinstance(tags, list) else []
        preview = str(
            result.get("content_preview") or result.get("title") or ""
        ).lower()
        explanation = result.get("explanation", {})
        matched_signals = explanation.get("matched_signals", [])
        matched_signals = matched_signals if isinstance(matched_signals, list) else []
        severity = (
            preview.count("critical") * 5
            + preview.count("high") * 3
            + preview.count("medium")
            + preview.count("important") * 2
        )
        severity += sum(
            5 if str(tag).lower() == "severity:critical" else 3
            for tag in tags
            if str(tag).lower() in {"severity:critical", "severity:high"}
        )
        intel_tags = sum(
            3
            for tag in tags
            if str(tag).lower()
            in {"ciso-concern", "severity:critical", "severity:high"}
        )
        operator_concern = 4 if "operator" in preview or "ciso" in preview else 0
        source_weight = (
            2 if str(result.get("bucket") or "") in {"findings", "reports"} else 0
        )
        checkpoint_weight = (
            3 if "checkpoint" in strategy and "checkpoint" in preview else 0
        )
        session_weight = 3 if "session" in strategy and "session" in preview else 0
        importance_weight = (
            3 if any("importance:high" == str(tag).lower() for tag in tags) else 0
        )
        validated_weight = (
            4
            if "validated" in preview
            or any("state:confirmed-vuln" == str(tag).lower() for tag in tags)
            else 0
        )
        exploitability_weight = (
            4
            if "exploited" in preview
            or any("state:exploited" == str(tag).lower() for tag in tags)
            else 0
        )
        recency_weight = 2 if matched_signals else 0
        cti_signal_weight = (
            4 if re.search(r"cve[-_]?\d{4,7}|cwe[-_]?\d+|t\d{4}", preview) else 0
        )
        return (
            severity
            + intel_tags
            + operator_concern
            + source_weight
            + checkpoint_weight
            + session_weight
            + importance_weight
            + validated_weight
            + exploitability_weight
            + recency_weight
            + cti_signal_weight
        )

    ranked = [result for result in results if isinstance(result, dict)]
    ranked.sort(
        key=lambda item: (
            -score(item),
            str(item.get("title") or item.get("content_preview") or ""),
        )
    )
    return ranked


def _render_protocol_context(results: list[dict[str, object]]) -> str:
    if not results:
        return "## Retrieved Context\nNo memory matched the requested scope."
    lines = ["## Retrieved Context"]
    for result in results:
        title = str(result.get("title") or result.get("bucket") or "Untitled memory")
        preview = str(result.get("content_preview") or "").strip()
        bucket = str(result.get("bucket") or "unknown")
        lines.append(f"- [{bucket}] {title}: {preview}")
    return "\n".join(lines)


def _render_protocol_fallback(results: list[dict[str, object]]) -> str:
    overview = "Protocol fallback generated from durable memory."
    findings = ["No findings available."]
    risks = [
        "Consensus reporting is unavailable, so this protocol is using deterministic retrieval output."
    ]
    actions = [
        "Review the highest-priority memory items.",
        "Validate current indicators before operational use.",
        "Export any new confirmed conclusions back into NeuroCore.",
    ]
    if results:
        findings = []
        for result in results[:3]:
            title = str(
                result.get("title") or result.get("bucket") or "Untitled memory"
            )
            preview = (
                str(result.get("content_preview") or "").strip()
                or "No preview available."
            )
            findings.append(f"- {title}: {preview}")
        risks = [
            f"{str(result.get('title') or result.get('bucket') or 'Untitled memory')}: "
            f"{str(result.get('content_preview') or 'No preview available.').strip()}"
            for result in results[:1]
        ]
    return (
        "## Overview\n"
        f"{overview}\n\n"
        "## Findings\n"
        f"{chr(10).join(findings)}\n\n"
        "## Risks\n"
        f"{chr(10).join(f'- {risk}' for risk in risks)}\n\n"
        "## Actions\n"
        f"- {actions[0]}\n- {actions[1]}\n- {actions[2]}"
    )


def _render_audit_context(findings: list[dict[str, object]]) -> str:
    if not findings:
        return "## Audit Findings\nNo secret-like memory findings were detected."
    lines = ["## Audit Findings"]
    for finding in findings[:8]:
        lines.append(
            "- "
            f"{finding.get('item_kind', 'item')} {finding.get('item_id', 'unknown')}: "
            f"{finding.get('field', 'content')} -> {finding.get('finding_type', 'secret-like')}"
        )
    return "\n".join(lines)


def _render_audit_fallback(findings: list[dict[str, object]]) -> str:
    if not findings:
        return (
            "## Overview\nNo secret-like memory findings were detected.\n\n"
            "## Findings\n- No findings available.\n\n"
            "## Risks\n- Low immediate remediation pressure.\n\n"
            "## Actions\n- Re-run the audit after future imports."
        )
    lines = [
        "## Overview",
        "Potentially sensitive memory items were detected.",
        "",
        "## Findings",
    ]
    for finding in findings[:5]:
        lines.append(
            f"- {finding.get('item_id', 'unknown')}: "
            f"{finding.get('field', 'content')} -> {finding.get('finding_type', 'secret-like')}"
        )
    lines.extend(
        [
            "",
            "## Risks",
            "- Sensitive material may be retrievable without remediation.",
            "",
            "## Actions",
            "- Review the flagged memory entries.",
            "- Supersede, redact, or delete confirmed sensitive items.",
            "- Reindex after remediation if content changed.",
        ]
    )
    return "\n".join(lines)
