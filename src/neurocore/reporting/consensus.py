"""Multi-model consensus reporting helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Protocol
from urllib import error, request as urllib_request
import re

from neurocore.reporting.workflows import build_sectioned_report_prompt


@dataclass(frozen=True)
class ConsensusReport:
    """Structured consensus report output."""

    report: str
    model_outputs: dict[str, str]
    agreement_score: float
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "report": self.report,
            "model_outputs": self.model_outputs,
            "agreement_score": self.agreement_score,
            "metadata": self.metadata,
        }


class ExternalReportModelClient(Protocol):
    """Protocol for report-generation model backends."""

    def generate_report(self, *, model_name: str, prompt: str) -> str:
        """Generate a markdown report from a model-specific prompt."""

    def generate_claims(
        self,
        *,
        model_name: str,
        objective: str,
        report_markdown: str,
        sections: tuple[str, ...],
    ) -> dict[str, object]:
        """Extract a normalized claim payload from one markdown draft."""

    def generate_judgments(
        self,
        *,
        model_name: str,
        objective: str,
        context_markdown: str,
        sections: tuple[str, ...],
        model_outputs: dict[str, str],
        reconciled_claims: tuple[ReconciledClaim, ...],
    ) -> dict[str, object]:
        """Return judge decisions for disputed claim candidates."""


class ReportGenerator(Protocol):
    """Protocol for report generation strategies."""

    def generate(
        self,
        *,
        objective: str,
        context_markdown: str,
        sections: tuple[str, ...] = ("Overview", "Findings", "Risks", "Actions"),
    ) -> ConsensusReport:
        """Generate a report and return normalized metadata."""


@dataclass(frozen=True)
class ExtractedClaim:
    """Normalized claim extracted from one model-specific report draft."""

    section: str
    title: str
    summary: str
    severity: str
    evidence: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True)
class ModelClaimSet:
    """Collection of extracted claims attributed to one model."""

    model_name: str
    claims: tuple[ExtractedClaim, ...]


@dataclass(frozen=True)
class ReconciledClaim:
    """Claim reconciled across multiple model claim sets."""

    claim_id: str
    section: str
    title: str
    summary: str
    severity: str
    evidence: tuple[str, ...]
    actions: tuple[str, ...]
    supporting_models: tuple[str, ...]
    status: str
    confidence: float


@dataclass(frozen=True)
class JudgeDecision:
    """Normalized decision emitted by the judge model."""

    claim_id: str
    decision: str
    summary: str
    severity: str
    rationale: str


@dataclass(frozen=True)
class OpenAICompatibleReportClient:
    """Minimal client for OpenAI-compatible chat completion APIs."""

    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 90.0

    def generate_report(self, *, model_name: str, prompt: str) -> str:
        body = self._request_completion(model_name=model_name, prompt=prompt)
        try:
            return str(body["choices"][0]["message"]["content"]).strip()
        except (
            KeyError,
            IndexError,
            TypeError,
        ) as exc:  # pragma: no cover - malformed remote response
            raise RuntimeError(
                f"Invalid response from external report model {model_name}"
            ) from exc

    def generate_claims(
        self,
        *,
        model_name: str,
        objective: str,
        report_markdown: str,
        sections: tuple[str, ...],
    ) -> dict[str, object]:
        response = self.generate_report(
            model_name=model_name,
            prompt=_build_claim_extraction_prompt(
                objective=objective,
                report_markdown=report_markdown,
                sections=sections,
            ),
        )
        try:
            parsed = json.loads(_strip_code_fences(response))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid JSON claim extraction response from {model_name}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(
                f"Invalid JSON claim extraction response from {model_name}"
            )
        return parsed

    def generate_judgments(
        self,
        *,
        model_name: str,
        objective: str,
        context_markdown: str,
        sections: tuple[str, ...],
        model_outputs: dict[str, str],
        reconciled_claims: tuple[ReconciledClaim, ...],
    ) -> dict[str, object]:
        response = self.generate_report(
            model_name=model_name,
            prompt=_build_judge_prompt(
                objective=objective,
                context_markdown=context_markdown,
                sections=sections,
                model_outputs=model_outputs,
                reconciled_claims=reconciled_claims,
            ),
        )
        try:
            parsed = json.loads(_strip_code_fences(response))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid JSON judge response from {model_name}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Invalid JSON judge response from {model_name}")
        return parsed

    def _request_completion(self, *, model_name: str, prompt: str) -> dict[str, object]:
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib_request.Request(
            url=f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:  # pragma: no cover - network path
            raise RuntimeError(
                f"Failed to call external report model {model_name}"
            ) from exc


@dataclass(frozen=True)
class MultiModelConsensusReporter:
    """Consensus reporter that aggregates outputs from multiple models."""

    model_client: ExternalReportModelClient
    model_names: tuple[str, ...]
    provider_name: str = "external"
    consensus_mode: str = "lexical_select"
    judge_client: ExternalReportModelClient | None = None
    judge_model_name: str | None = None
    judge_provider_name: str | None = None

    def generate(
        self,
        *,
        objective: str,
        context_markdown: str,
        sections: tuple[str, ...] = ("Overview", "Findings", "Risks", "Actions"),
    ) -> ConsensusReport:
        if len(self.model_names) < 2:
            raise ValueError("Multi-model consensus requires at least two model names")
        if len(set(self.model_names)) != len(self.model_names):
            raise ValueError("Multi-model consensus requires unique model names")

        prompt = build_sectioned_report_prompt(
            objective=objective,
            context_markdown=context_markdown,
            sections=sections,
        )
        outputs = {
            model_name: self.model_client.generate_report(
                model_name=model_name, prompt=prompt
            )
            for model_name in self.model_names
        }
        if self.consensus_mode != "lexical_select":
            return self._generate_claim_voting_result(
                objective=objective,
                context_markdown=context_markdown,
                outputs=outputs,
                sections=sections,
            )
        selected = max(
            outputs.values(),
            key=lambda item: (_agreement(item, outputs), len(item)),
        )
        return ConsensusReport(
            report=selected,
            model_outputs=outputs,
            agreement_score=_agreement(selected, outputs),
            metadata={
                "objective": objective,
                "sections": list(sections),
                "model_count": len(self.model_names),
                "model_names": list(self.model_names),
                "active_provider": self.provider_name,
                "reporting_strategy": "multi_model_consensus",
                "consensus_mode": self.consensus_mode,
            },
        )

    def _generate_claim_voting_result(
        self,
        *,
        objective: str,
        context_markdown: str,
        outputs: dict[str, str],
        sections: tuple[str, ...],
    ) -> ConsensusReport:
        claim_sets = tuple(
            validate_model_claim_set(
                model_name,
                self.model_client.generate_claims(
                    model_name=model_name,
                    objective=objective,
                    report_markdown=outputs[model_name],
                    sections=sections,
                ),
            )
            for model_name in self.model_names
        )
        reconciled = reconcile_claim_sets(claim_sets)
        judge_failures: list[str] = []
        judge_used = False
        consensus_version = "claim_reconciled_v1"
        if self.consensus_mode == "claim_voting_with_judge":
            consensus_version = "claim_reconciled_v2"
            included_claims = tuple(
                claim for claim in reconciled if claim.status == "agreed"
            )
            judge_candidates = tuple(
                claim
                for claim in reconciled
                if claim.status in {"partial", "disputed", "unique"}
            )
            if judge_candidates and self.judge_client and self.judge_model_name:
                try:
                    decisions = validate_judge_decisions(
                        self.judge_client.generate_judgments(
                            model_name=self.judge_model_name,
                            objective=objective,
                            context_markdown=context_markdown,
                            sections=sections,
                            model_outputs=outputs,
                            reconciled_claims=judge_candidates,
                        )
                    )
                    included_claims = _apply_judge_decisions(
                        base_claims=included_claims,
                        candidates=judge_candidates,
                        decisions=decisions,
                    )
                    judge_used = True
                except Exception as exc:
                    judge_failures.append(str(exc))
                    included_claims = tuple(
                        claim
                        for claim in reconciled
                        if claim.status in {"agreed", "partial"}
                    )
            else:
                included_claims = tuple(
                    claim
                    for claim in reconciled
                    if claim.status in {"agreed", "partial"}
                )
        else:
            included_claims = tuple(
                claim for claim in reconciled if claim.status in {"agreed", "partial"}
            )
        degraded_to_lexical_selection = not included_claims
        if degraded_to_lexical_selection:
            report = max(
                outputs.values(),
                key=lambda item: (_agreement(item, outputs), len(item)),
            )
            agreement_score = 0.0
        else:
            report = _synthesize_claim_report(
                objective=objective,
                sections=sections,
                claims=included_claims,
                model_count=len(self.model_names),
            )
            agreement_score = round(
                sum(1 for claim in included_claims if len(claim.supporting_models) > 1)
                / len(included_claims),
                2,
            )
        final_confidence = (
            round(
                sum(claim.confidence for claim in included_claims)
                / len(included_claims),
                2,
            )
            if included_claims
            else 0.0
        )
        consensus = {
            "version": consensus_version,
            "mode": self.consensus_mode,
            "models": list(self.model_names),
            "claim_counts": _claim_counts(reconciled, len(included_claims)),
            "claims": [_reconciled_claim_to_dict(claim) for claim in reconciled],
            "degraded_to_lexical_selection": degraded_to_lexical_selection,
            "judge_used": judge_used,
            "judge_provider": self.judge_provider_name,
            "judge_model": self.judge_model_name,
            "judge_failures": judge_failures,
            "final_confidence": final_confidence,
        }
        return ConsensusReport(
            report=report,
            model_outputs=outputs,
            agreement_score=agreement_score,
            metadata={
                "objective": objective,
                "sections": list(sections),
                "model_count": len(self.model_names),
                "model_names": list(self.model_names),
                "active_provider": self.provider_name,
                "reporting_strategy": "multi_model_consensus",
                "consensus_mode": self.consensus_mode,
                "consensus": consensus,
            },
        )


@dataclass(frozen=True)
class SingleModelReportReporter:
    """Single-model reporter used for degraded external fallback."""

    model_client: ExternalReportModelClient
    model_name: str
    provider_name: str = "external"

    def generate(
        self,
        *,
        objective: str,
        context_markdown: str,
        sections: tuple[str, ...] = ("Overview", "Findings", "Risks", "Actions"),
    ) -> ConsensusReport:
        prompt = build_sectioned_report_prompt(
            objective=objective,
            context_markdown=context_markdown,
            sections=sections,
        )
        output = self.model_client.generate_report(
            model_name=self.model_name,
            prompt=prompt,
        )
        return ConsensusReport(
            report=output,
            model_outputs={self.model_name: output},
            agreement_score=1.0,
            metadata={
                "objective": objective,
                "sections": list(sections),
                "model_count": 1,
                "model_names": [self.model_name],
                "active_provider": self.provider_name,
                "reporting_strategy": "single_model_fallback",
            },
        )


@dataclass(frozen=True)
class PrimaryWithFallbackReporter:
    """Reporting strategy that prefers one provider and degrades to another."""

    primary_reporter: ReportGenerator
    fallback_reporter: ReportGenerator
    primary_provider_name: str
    fallback_provider_name: str

    def generate(
        self,
        *,
        objective: str,
        context_markdown: str,
        sections: tuple[str, ...] = ("Overview", "Findings", "Risks", "Actions"),
    ) -> ConsensusReport:
        try:
            primary = self.primary_reporter.generate(
                objective=objective,
                context_markdown=context_markdown,
                sections=sections,
            )
        except Exception as primary_exc:
            try:
                fallback = self.fallback_reporter.generate(
                    objective=objective,
                    context_markdown=context_markdown,
                    sections=sections,
                )
            except Exception as fallback_exc:
                raise RuntimeError(
                    "Primary provider "
                    f"{self.primary_provider_name} failed: {primary_exc}; "
                    "fallback provider "
                    f"{self.fallback_provider_name} failed: {fallback_exc}"
                ) from fallback_exc
            metadata = dict(fallback.metadata)
            metadata.update(
                {
                    "active_provider": self.fallback_provider_name,
                    "fallback_used": True,
                    "primary_provider": self.primary_provider_name,
                    "fallback_provider": self.fallback_provider_name,
                    "primary_provider_error": str(primary_exc),
                    "reporting_strategy": "primary_with_fallback",
                }
            )
            return ConsensusReport(
                report=fallback.report,
                model_outputs=fallback.model_outputs,
                agreement_score=fallback.agreement_score,
                metadata=metadata,
            )

        metadata = dict(primary.metadata)
        metadata.update(
            {
                "active_provider": self.primary_provider_name,
                "fallback_used": False,
                "primary_provider": self.primary_provider_name,
                "fallback_provider": self.fallback_provider_name,
                "reporting_strategy": "primary_with_fallback",
            }
        )
        return ConsensusReport(
            report=primary.report,
            model_outputs=primary.model_outputs,
            agreement_score=primary.agreement_score,
            metadata=metadata,
        )


def _agreement(candidate: str, outputs: dict[str, str]) -> float:
    """Measure lexical agreement for one candidate against all outputs."""
    candidate_terms = set(candidate.lower().split())
    if not candidate_terms:
        return 1.0
    overlaps = []
    for output in outputs.values():
        output_terms = set(output.lower().split())
        overlaps.append(
            len(candidate_terms & output_terms) / max(len(candidate_terms), 1)
        )
    return round(sum(overlaps) / len(overlaps), 2)


def validate_model_claim_set(
    model_name: str,
    payload: dict[str, object],
) -> ModelClaimSet:
    """Validate and normalize a model-emitted claim payload."""
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        raise ValueError("claims must be a list")
    claims: list[ExtractedClaim] = []
    seen_fingerprints: set[str] = set()
    for index, raw_claim in enumerate(raw_claims):
        if not isinstance(raw_claim, dict):
            raise ValueError(f"claims[{index}] must be an object")
        claim = ExtractedClaim(
            section=_required_claim_text(raw_claim, "section"),
            title=_required_claim_text(raw_claim, "title"),
            summary=_required_claim_text(raw_claim, "summary"),
            severity=_optional_claim_text(raw_claim, "severity", "info").lower(),
            evidence=_normalize_claim_list(raw_claim, "evidence"),
            actions=_normalize_claim_list(raw_claim, "actions"),
        )
        fingerprint = _claim_fingerprint(claim)
        if fingerprint in seen_fingerprints:
            raise ValueError(
                f"Duplicate claim emitted by {model_name}: {claim.section} / {claim.title}"
            )
        seen_fingerprints.add(fingerprint)
        claims.append(claim)
    return ModelClaimSet(model_name=model_name, claims=tuple(claims))


def reconcile_claim_sets(
    claim_sets: tuple[ModelClaimSet, ...],
) -> tuple[ReconciledClaim, ...]:
    """Reconcile per-model claims into deterministic consensus records."""
    total_models = len(claim_sets)
    if total_models < 1:
        return ()

    grouped: dict[str, list[tuple[str, ExtractedClaim]]] = {}
    for claim_set in claim_sets:
        for claim in claim_set.claims:
            grouped.setdefault(_claim_fingerprint(claim), []).append(
                (claim_set.model_name, claim)
            )

    reconciled: list[ReconciledClaim] = []
    for entries in grouped.values():
        supporting_models = tuple(model_name for model_name, _ in entries)
        exemplar = entries[0][1]
        severities = {claim.severity for _, claim in entries}
        summaries = {claim.summary for _, claim in entries}
        evidences = {claim.evidence for _, claim in entries}
        actions = {claim.actions for _, claim in entries}
        if len(entries) == 1:
            status = "unique"
        elif len(entries) == total_models and len(severities) == 1:
            if len(summaries) == 1 and len(evidences) == 1 and len(actions) == 1:
                status = "agreed"
            else:
                status = "partial"
        else:
            status = "disputed"
        reconciled.append(
            ReconciledClaim(
                claim_id=_claim_id(exemplar.section, exemplar.title),
                section=exemplar.section,
                title=exemplar.title,
                summary=exemplar.summary,
                severity=exemplar.severity,
                evidence=exemplar.evidence,
                actions=exemplar.actions,
                supporting_models=supporting_models,
                status=status,
                confidence=_claim_confidence(status, len(entries), total_models),
            )
        )
    return tuple(reconciled)


def _required_claim_text(payload: dict[str, object], key: str) -> str:
    raw = payload.get(key)
    value = str(raw).strip() if raw is not None else ""
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_claim_text(payload: dict[str, object], key: str, default: str) -> str:
    raw = payload.get(key)
    value = str(raw).strip() if raw is not None else ""
    return value or default


def _normalize_claim_list(payload: dict[str, object], key: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if isinstance(raw, str):
        values = [raw.strip()] if raw.strip() else []
    elif isinstance(raw, (list, tuple)):
        values = [str(item).strip() for item in raw if str(item).strip()]
    else:
        values = []
    return tuple(values)


def _claim_fingerprint(claim: ExtractedClaim) -> str:
    return f"{claim.section.strip().lower()}::{_slugify(claim.title)}"


def _claim_id(section: str, title: str) -> str:
    return f"{section.strip().lower()}:{_slugify(title)}"


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _claim_confidence(status: str, support_count: int, total_models: int) -> float:
    support_ratio = support_count / max(total_models, 1)
    if status == "agreed":
        return round(support_ratio, 2)
    if status == "partial":
        return round(0.75 * support_ratio, 2)
    return round(0.5 * support_ratio, 2)


def _claim_counts(
    claims: tuple[ReconciledClaim, ...],
    included_count: int,
) -> dict[str, int]:
    counts = {"agreed": 0, "partial": 0, "disputed": 0, "unique": 0}
    for claim in claims:
        counts[claim.status] += 1
    return {
        "total": len(claims),
        **counts,
        "included": included_count,
    }


def validate_judge_decisions(payload: dict[str, object]) -> tuple[JudgeDecision, ...]:
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("decisions must be a list")
    decisions: list[JudgeDecision] = []
    for index, raw_decision in enumerate(raw_decisions):
        if not isinstance(raw_decision, dict):
            raise ValueError(f"decisions[{index}] must be an object")
        decision = _required_claim_text(raw_decision, "decision").lower()
        if decision not in {"include", "exclude", "downgrade"}:
            raise ValueError(f"Unsupported judge decision: {decision}")
        decisions.append(
            JudgeDecision(
                claim_id=_required_claim_text(raw_decision, "claim_id"),
                decision=decision,
                summary=_required_claim_text(raw_decision, "summary"),
                severity=_required_claim_text(raw_decision, "severity").lower(),
                rationale=_required_claim_text(raw_decision, "rationale"),
            )
        )
    return tuple(decisions)


def _apply_judge_decisions(
    *,
    base_claims: tuple[ReconciledClaim, ...],
    candidates: tuple[ReconciledClaim, ...],
    decisions: tuple[JudgeDecision, ...],
) -> tuple[ReconciledClaim, ...]:
    decision_map = {decision.claim_id: decision for decision in decisions}
    included = list(base_claims)
    for candidate in candidates:
        decision = decision_map.get(candidate.claim_id)
        if decision is None or decision.decision == "exclude":
            continue
        included.append(
            replace(
                candidate,
                summary=decision.summary,
                severity=decision.severity,
            )
        )
    return tuple(included)


def _reconciled_claim_to_dict(claim: ReconciledClaim) -> dict[str, object]:
    return {
        "claim_id": claim.claim_id,
        "section": claim.section,
        "title": claim.title,
        "summary": claim.summary,
        "severity": claim.severity,
        "evidence": list(claim.evidence),
        "actions": list(claim.actions),
        "supporting_models": list(claim.supporting_models),
        "status": claim.status,
        "confidence": claim.confidence,
    }


def _synthesize_claim_report(
    *,
    objective: str,
    sections: tuple[str, ...],
    claims: tuple[ReconciledClaim, ...],
    model_count: int,
) -> str:
    claims_by_section: dict[str, list[ReconciledClaim]] = {}
    for claim in claims:
        claims_by_section.setdefault(claim.section, []).append(claim)

    lines: list[str] = []
    for section in sections:
        lines.append(f"## {section}")
        if section == "Overview":
            lines.append(
                f"Consensus report for: {objective} (reconciled across {model_count} models)."
            )
            continue
        section_claims = claims_by_section.get(section, [])
        if not section_claims:
            lines.append("No consensus claims.")
            continue
        for claim in section_claims:
            lines.append(f"### {claim.title}")
            lines.append(claim.summary)
            lines.append(f"Severity: {claim.severity}")
            lines.append("Evidence:")
            lines.extend(f"- {item}" for item in claim.evidence)
            lines.append("Actions:")
            lines.extend(f"- {item}" for item in claim.actions)
    return "\n".join(lines)


def _build_claim_extraction_prompt(
    *,
    objective: str,
    report_markdown: str,
    sections: tuple[str, ...],
) -> str:
    headings = ", ".join(sections)
    return (
        "Extract normalized security claims from the markdown report and respond with "
        'JSON only using the shape {"claims":[{"section","title","summary","severity","evidence","actions"}]}. '
        "Use evidence and actions as arrays of strings. "
        f"Objective: {objective}. "
        f"Expected sections: {headings}.\n\n"
        "Report:\n"
        f"{report_markdown}"
    )


def _build_judge_prompt(
    *,
    objective: str,
    context_markdown: str,
    sections: tuple[str, ...],
    model_outputs: dict[str, str],
    reconciled_claims: tuple[ReconciledClaim, ...],
) -> str:
    return (
        "Judge disputed security report claims and respond with JSON only using the "
        'shape {"decisions":[{"claim_id","decision","summary","severity","rationale"}]}. '
        "Valid decisions are include, exclude, or downgrade. "
        f"Objective: {objective}. "
        f"Sections: {', '.join(sections)}.\n\n"
        "Context:\n"
        f"{context_markdown}\n\n"
        "Model outputs:\n"
        f"{json.dumps(model_outputs, sort_keys=True)}\n\n"
        "Candidate claims:\n"
        f"{json.dumps([_reconciled_claim_to_dict(claim) for claim in reconciled_claims], sort_keys=True)}"
    )


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped
