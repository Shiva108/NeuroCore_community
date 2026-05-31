import pytest

from neurocore.reporting.consensus import (
    ExtractedClaim,
    ModelClaimSet,
    MultiModelConsensusReporter,
    OpenAICompatibleReportClient,
    PrimaryWithFallbackReporter,
    ReconciledClaim,
    SingleModelReportReporter,
    reconcile_claim_sets,
    validate_model_claim_set,
)


class FakeExternalReportClient:
    def __init__(
        self,
        outputs: dict[str, str],
        claim_payloads: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.outputs = outputs
        self.claim_payloads = claim_payloads or {}
        self.calls: list[str] = []
        self.claim_calls: list[str] = []
        self.judgment_payload: dict[str, object] | None = None
        self.judgment_error: Exception | None = None
        self.judgment_calls: list[dict[str, object]] = []

    def generate_report(self, *, model_name: str, prompt: str) -> str:
        self.calls.append(model_name)
        return self.outputs[model_name]

    def generate_claims(
        self,
        *,
        model_name: str,
        objective: str,
        report_markdown: str,
        sections: tuple[str, ...],
    ) -> dict[str, object]:
        self.claim_calls.append(model_name)
        return self.claim_payloads[model_name]

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
        self.judgment_calls.append(
            {
                "model_name": model_name,
                "objective": objective,
                "context_markdown": context_markdown,
                "sections": sections,
                "model_outputs": model_outputs,
                "claim_ids": [claim.claim_id for claim in reconciled_claims],
            }
        )
        if self.judgment_error is not None:
            raise self.judgment_error
        return self.judgment_payload or {"decisions": []}


def test_multi_model_consensus_reporter_uses_all_models_and_returns_consensus():
    client = FakeExternalReportClient(
        {
            "model-a": (
                "## Overview\nA.\n## Findings\nB.\n## Risks\nC.\n## Actions\nD."
            ),
            "model-b": (
                "## Overview\nA.\n## Findings\nB.\n## Risks\nC.\n## Actions\nD."
            ),
            "model-c": (
                "## Overview\nX.\n## Findings\nY.\n## Risks\nZ.\n## Actions\nW."
            ),
        }
    )
    reporter = MultiModelConsensusReporter(
        model_client=client,
        model_names=("model-a", "model-b", "model-c"),
    )

    result = reporter.generate(
        objective="Generate a security review report.",
        context_markdown="Incident and query context",
    )

    assert client.calls == ["model-a", "model-b", "model-c"]
    assert result.report.startswith("## Overview")
    assert result.report == (
        "## Overview\nA.\n## Findings\nB.\n## Risks\nC.\n## Actions\nD."
    )
    assert set(result.model_outputs) == {"model-a", "model-b", "model-c"}
    assert result.agreement_score >= 0.66
    assert result.metadata["model_count"] == 3


def test_multi_model_consensus_reporter_rejects_duplicate_models():
    reporter = MultiModelConsensusReporter(
        model_client=FakeExternalReportClient({"model-a": "same"}),
        model_names=("model-a", "model-a"),
    )

    with pytest.raises(ValueError, match="unique model names"):
        reporter.generate(
            objective="Generate a review report.",
            context_markdown="Context",
        )


def test_multi_model_consensus_reporter_requires_two_models():
    reporter = MultiModelConsensusReporter(
        model_client=FakeExternalReportClient({"model-a": "only"}),
        model_names=("model-a",),
    )

    with pytest.raises(ValueError, match="at least two"):
        reporter.generate(
            objective="Generate a review report.",
            context_markdown="Context",
        )


def test_primary_with_fallback_reporter_uses_fallback_when_primary_fails():
    class FailingClient:
        def generate_report(self, *, model_name: str, prompt: str) -> str:
            raise RuntimeError(f"{model_name} unavailable")

    fallback_client = FakeExternalReportClient(
        {"gpt-5-mini": "## Overview\nFallback.\n## Findings\nReady."}
    )
    reporter = PrimaryWithFallbackReporter(
        primary_reporter=MultiModelConsensusReporter(
            model_client=FailingClient(),
            model_names=("deepseek-v4-flash", "deepseek-v4-pro"),
            provider_name="deepseek",
        ),
        fallback_reporter=SingleModelReportReporter(
            model_client=fallback_client,
            model_name="gpt-5-mini",
            provider_name="openai",
        ),
        primary_provider_name="deepseek",
        fallback_provider_name="openai",
    )

    result = reporter.generate(
        objective="Generate a review report.",
        context_markdown="Context",
    )

    assert result.report.startswith("## Overview")
    assert result.metadata["active_provider"] == "openai"
    assert result.metadata["fallback_used"] is True
    assert result.metadata["primary_provider"] == "deepseek"
    assert result.metadata["fallback_provider"] == "openai"


def test_single_model_report_reporter_uses_one_model():
    client = FakeExternalReportClient(
        {"gpt-5-mini": "## Overview\nFallback.\n## Findings\nReady."}
    )
    reporter = SingleModelReportReporter(
        model_client=client,
        model_name="gpt-5-mini",
        provider_name="openai",
    )

    result = reporter.generate(
        objective="Generate a review report.",
        context_markdown="Context",
    )

    assert client.calls == ["gpt-5-mini"]
    assert result.agreement_score == 1.0
    assert result.metadata["active_provider"] == "openai"
    assert result.metadata["model_count"] == 1


def test_openai_compatible_report_client_uses_external_reporting_timeout():
    client = OpenAICompatibleReportClient(base_url="https://api.example.test")

    assert client.timeout_seconds == 90.0


def test_multi_model_consensus_reporter_claim_voting_returns_reconciled_report():
    client = FakeExternalReportClient(
        outputs={
            "model-a": "## Overview\nReady.\n## Findings\nA.\n## Risks\nB.\n## Actions\nC.",
            "model-b": "## Overview\nReady.\n## Findings\nA.\n## Risks\nB.\n## Actions\nC.",
        },
        claim_payloads={
            "model-a": {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Server-side request forgery",
                        "summary": "Metadata endpoint is reachable from the application.",
                        "severity": "high",
                        "evidence": ["GET /latest/meta-data returned 200"],
                        "actions": ["Restrict egress to metadata IPs"],
                    },
                    {
                        "section": "Findings",
                        "title": "Verbose error disclosure",
                        "summary": "Stack traces are exposed on invalid input.",
                        "severity": "medium",
                        "evidence": ["POST /api/search returned framework trace"],
                        "actions": ["Replace raw exceptions with generic handlers"],
                    },
                ]
            },
            "model-b": {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Server-side request forgery",
                        "summary": "Metadata endpoint is reachable from the application.",
                        "severity": "high",
                        "evidence": ["GET /latest/meta-data returned 200"],
                        "actions": ["Restrict egress to metadata IPs"],
                    },
                    {
                        "section": "Findings",
                        "title": "Verbose error disclosure",
                        "summary": "Unhandled exceptions leak application internals.",
                        "severity": "medium",
                        "evidence": ["POST /api/search returned framework trace"],
                        "actions": ["Replace raw exceptions with generic handlers"],
                    },
                    {
                        "section": "Actions",
                        "title": "Segment admin plane",
                        "summary": "Move admin endpoints behind VPN access.",
                        "severity": "medium",
                        "evidence": ["Admin panel is exposed on the public hostname"],
                        "actions": ["Restrict admin hostname to internal access"],
                    },
                ]
            },
        },
    )
    reporter = MultiModelConsensusReporter(
        model_client=client,
        model_names=("model-a", "model-b"),
        consensus_mode="claim_voting",
    )

    result = reporter.generate(
        objective="Generate a security review report.",
        context_markdown="Incident and query context",
    )

    consensus = result.metadata["consensus"]

    assert client.calls == ["model-a", "model-b"]
    assert client.claim_calls == ["model-a", "model-b"]
    assert "Server-side request forgery" in result.report
    assert "Verbose error disclosure" in result.report
    assert "Segment admin plane" not in result.report
    assert result.agreement_score == 1.0
    assert consensus["version"] == "claim_reconciled_v1"
    assert consensus["mode"] == "claim_voting"
    assert consensus["claim_counts"] == {
        "total": 3,
        "agreed": 1,
        "partial": 1,
        "disputed": 0,
        "unique": 1,
        "included": 2,
    }
    assert consensus["degraded_to_lexical_selection"] is False
    assert consensus["judge_used"] is False
    assert consensus["final_confidence"] == 0.88


def test_multi_model_consensus_reporter_claim_voting_degrades_to_lexical_selection():
    client = FakeExternalReportClient(
        outputs={
            "model-a": "## Overview\nA.\n## Findings\nOnly A.\n## Risks\nR.\n## Actions\nDo A.",
            "model-b": "## Overview\nB.\n## Findings\nOnly B.\n## Risks\nR.\n## Actions\nDo B.",
        },
        claim_payloads={
            "model-a": {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Only in model A",
                        "summary": "Claim A.",
                        "severity": "medium",
                        "evidence": ["evidence a"],
                        "actions": ["action a"],
                    }
                ]
            },
            "model-b": {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Only in model B",
                        "summary": "Claim B.",
                        "severity": "medium",
                        "evidence": ["evidence b"],
                        "actions": ["action b"],
                    }
                ]
            },
        },
    )
    reporter = MultiModelConsensusReporter(
        model_client=client,
        model_names=("model-a", "model-b"),
        consensus_mode="claim_voting",
    )

    result = reporter.generate(
        objective="Generate a security review report.",
        context_markdown="Incident and query context",
    )

    consensus = result.metadata["consensus"]

    assert result.report == client.outputs["model-a"]
    assert result.agreement_score == 0.0
    assert consensus["degraded_to_lexical_selection"] is True
    assert consensus["claim_counts"]["included"] == 0
    assert consensus["claim_counts"]["unique"] == 2


def test_claim_voting_with_judge_includes_only_judge_approved_candidates():
    client = FakeExternalReportClient(
        outputs={
            "model-a": "## Overview\nReady.\n## Findings\nA.\n## Risks\nB.\n## Actions\nC.",
            "model-b": "## Overview\nReady.\n## Findings\nA.\n## Risks\nB.\n## Actions\nC.",
        },
        claim_payloads={
            "model-a": {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Server-side request forgery",
                        "summary": "Metadata endpoint is reachable from the application.",
                        "severity": "high",
                        "evidence": ["GET /latest/meta-data returned 200"],
                        "actions": ["Restrict egress to metadata IPs"],
                    },
                    {
                        "section": "Risks",
                        "title": "Outdated TLS baseline",
                        "summary": "TLS 1.0 and 1.1 remain enabled.",
                        "severity": "medium",
                        "evidence": ["nmap ssl-enum-ciphers lists TLSv1.0"],
                        "actions": ["Disable TLS 1.0 and TLS 1.1"],
                    },
                ]
            },
            "model-b": {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Server-side request forgery",
                        "summary": "Metadata endpoint is reachable from the application.",
                        "severity": "high",
                        "evidence": ["GET /latest/meta-data returned 200"],
                        "actions": ["Restrict egress to metadata IPs"],
                    },
                    {
                        "section": "Risks",
                        "title": "Outdated TLS baseline",
                        "summary": "TLS 1.0 and 1.1 remain enabled.",
                        "severity": "low",
                        "evidence": ["nmap ssl-enum-ciphers lists TLSv1.0"],
                        "actions": ["Disable TLS 1.0 and TLS 1.1"],
                    },
                    {
                        "section": "Actions",
                        "title": "Segment admin plane",
                        "summary": "Move admin endpoints behind VPN access.",
                        "severity": "medium",
                        "evidence": ["Admin panel is exposed on the public hostname"],
                        "actions": ["Restrict admin hostname to internal access"],
                    },
                ]
            },
        },
    )
    judge = FakeExternalReportClient(outputs={})
    judge.judgment_payload = {
        "decisions": [
            {
                "claim_id": "risks:outdated-tls-baseline",
                "decision": "downgrade",
                "summary": "Legacy TLS versions remain enabled and should be retired.",
                "severity": "low",
                "rationale": "Both models observed the issue; the lower severity is safer.",
            },
            {
                "claim_id": "actions:segment-admin-plane",
                "decision": "exclude",
                "summary": "Move admin endpoints behind VPN access.",
                "severity": "medium",
                "rationale": "Single-model action item lacked corroboration.",
            },
        ]
    }
    reporter = MultiModelConsensusReporter(
        model_client=client,
        model_names=("model-a", "model-b"),
        consensus_mode="claim_voting_with_judge",
        judge_client=judge,
        judge_model_name="gpt-5-mini",
        judge_provider_name="openai",
    )

    result = reporter.generate(
        objective="Generate a security review report.",
        context_markdown="Incident and query context",
    )

    consensus = result.metadata["consensus"]

    assert "Server-side request forgery" in result.report
    assert "Legacy TLS versions remain enabled and should be retired." in result.report
    assert "Segment admin plane" not in result.report
    assert judge.judgment_calls[0]["claim_ids"] == [
        "risks:outdated-tls-baseline",
        "actions:segment-admin-plane",
    ]
    assert consensus["version"] == "claim_reconciled_v2"
    assert consensus["judge_used"] is True
    assert consensus["judge_provider"] == "openai"
    assert consensus["judge_model"] == "gpt-5-mini"
    assert consensus["claim_counts"]["included"] == 2


def test_claim_voting_with_judge_degrades_to_plain_claim_voting_when_judge_fails():
    client = FakeExternalReportClient(
        outputs={
            "model-a": "## Overview\nReady.\n## Findings\nA.\n## Risks\nB.\n## Actions\nC.",
            "model-b": "## Overview\nReady.\n## Findings\nA.\n## Risks\nB.\n## Actions\nC.",
        },
        claim_payloads={
            "model-a": {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Server-side request forgery",
                        "summary": "Metadata endpoint is reachable from the application.",
                        "severity": "high",
                        "evidence": ["GET /latest/meta-data returned 200"],
                        "actions": ["Restrict egress to metadata IPs"],
                    }
                ]
            },
            "model-b": {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Server-side request forgery",
                        "summary": "Metadata endpoint is reachable from the application.",
                        "severity": "high",
                        "evidence": ["GET /latest/meta-data returned 200"],
                        "actions": ["Restrict egress to metadata IPs"],
                    },
                    {
                        "section": "Actions",
                        "title": "Segment admin plane",
                        "summary": "Move admin endpoints behind VPN access.",
                        "severity": "medium",
                        "evidence": ["Admin panel is exposed on the public hostname"],
                        "actions": ["Restrict admin hostname to internal access"],
                    },
                ]
            },
        },
    )
    judge = FakeExternalReportClient(outputs={})
    judge.judgment_error = RuntimeError("judge unavailable")
    reporter = MultiModelConsensusReporter(
        model_client=client,
        model_names=("model-a", "model-b"),
        consensus_mode="claim_voting_with_judge",
        judge_client=judge,
        judge_model_name="gpt-5-mini",
        judge_provider_name="openai",
    )

    result = reporter.generate(
        objective="Generate a security review report.",
        context_markdown="Incident and query context",
    )

    consensus = result.metadata["consensus"]

    assert "Server-side request forgery" in result.report
    assert "Segment admin plane" not in result.report
    assert consensus["version"] == "claim_reconciled_v2"
    assert consensus["judge_used"] is False
    assert consensus["judge_failures"] == ["judge unavailable"]


def test_validate_model_claim_set_accepts_valid_claim_payload():
    payload = {
        "claims": [
            {
                "section": "Findings",
                "title": "Server-side request forgery",
                "summary": "Metadata endpoint is reachable from the application.",
                "severity": "high",
                "evidence": ["GET /latest/meta-data returned 200"],
                "actions": ["Restrict egress to metadata IPs"],
            }
        ]
    }

    result = validate_model_claim_set("deepseek-v4-pro", payload)

    assert result == ModelClaimSet(
        model_name="deepseek-v4-pro",
        claims=(
            ExtractedClaim(
                section="Findings",
                title="Server-side request forgery",
                summary="Metadata endpoint is reachable from the application.",
                severity="high",
                evidence=("GET /latest/meta-data returned 200",),
                actions=("Restrict egress to metadata IPs",),
            ),
        ),
    )


def test_validate_model_claim_set_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="title"):
        validate_model_claim_set(
            "deepseek-v4-pro",
            {
                "claims": [
                    {
                        "section": "Findings",
                        "summary": "Metadata endpoint is reachable.",
                        "severity": "high",
                        "evidence": ["GET /latest/meta-data returned 200"],
                        "actions": ["Restrict egress to metadata IPs"],
                    }
                ]
            },
        )


def test_validate_model_claim_set_rejects_duplicate_claim_fingerprints():
    with pytest.raises(ValueError, match="Duplicate claim emitted by model-a"):
        validate_model_claim_set(
            "model-a",
            {
                "claims": [
                    {
                        "section": "Findings",
                        "title": "Server-side request forgery",
                        "summary": "Metadata endpoint is reachable from the application.",
                        "severity": "high",
                        "evidence": ["GET /latest/meta-data returned 200"],
                        "actions": ["Restrict egress to metadata IPs"],
                    },
                    {
                        "section": "Findings",
                        "title": "Server-side request forgery",
                        "summary": "Duplicate wording from the same model.",
                        "severity": "medium",
                        "evidence": ["Same endpoint repeated"],
                        "actions": ["Ignore duplicate output"],
                    },
                ]
            },
        )


def test_validate_model_claim_set_allows_empty_evidence_and_actions():
    result = validate_model_claim_set(
        "deepseek-v4-pro",
        {
            "claims": [
                {
                    "section": "Overview",
                    "title": "No input data provided",
                    "summary": "The security assessment cannot be performed without input data.",
                    "severity": "info",
                    "evidence": [],
                    "actions": [],
                }
            ]
        },
    )

    assert result.claims[0].evidence == ()
    assert result.claims[0].actions == ()


def test_validate_model_claim_set_defaults_missing_severity_to_info():
    result = validate_model_claim_set(
        "deepseek-v4-pro",
        {
            "claims": [
                {
                    "section": "Overview",
                    "title": "No input data provided",
                    "summary": "The security assessment cannot be performed without input data.",
                    "evidence": [],
                    "actions": [],
                }
            ]
        },
    )

    assert result.claims[0].severity == "info"


def test_reconcile_claim_sets_classifies_agreed_partial_disputed_and_unique():
    model_a = ModelClaimSet(
        model_name="model-a",
        claims=(
            ExtractedClaim(
                section="Findings",
                title="Server-side request forgery",
                summary="Metadata endpoint is reachable from the application.",
                severity="high",
                evidence=("GET /latest/meta-data returned 200",),
                actions=("Restrict egress to metadata IPs",),
            ),
            ExtractedClaim(
                section="Findings",
                title="Verbose error disclosure",
                summary="Stack traces are exposed on invalid input.",
                severity="medium",
                evidence=("POST /api/search returned framework trace",),
                actions=("Replace raw exceptions with generic handlers",),
            ),
            ExtractedClaim(
                section="Risks",
                title="Outdated TLS baseline",
                summary="TLS 1.0 and 1.1 remain enabled.",
                severity="medium",
                evidence=("nmap ssl-enum-ciphers lists TLSv1.0",),
                actions=("Disable TLS 1.0 and TLS 1.1",),
            ),
        ),
    )
    model_b = ModelClaimSet(
        model_name="model-b",
        claims=(
            ExtractedClaim(
                section="Findings",
                title="Server-side request forgery",
                summary="Metadata endpoint is reachable from the application.",
                severity="high",
                evidence=("GET /latest/meta-data returned 200",),
                actions=("Restrict egress to metadata IPs",),
            ),
            ExtractedClaim(
                section="Findings",
                title="Verbose error disclosure",
                summary="Unhandled exceptions leak application internals.",
                severity="medium",
                evidence=("POST /api/search returned framework trace",),
                actions=("Replace raw exceptions with generic handlers",),
            ),
            ExtractedClaim(
                section="Risks",
                title="Outdated TLS baseline",
                summary="TLS 1.0 and 1.1 remain enabled.",
                severity="low",
                evidence=("nmap ssl-enum-ciphers lists TLSv1.0",),
                actions=("Disable TLS 1.0 and TLS 1.1",),
            ),
            ExtractedClaim(
                section="Actions",
                title="Segment admin plane",
                summary="Move admin endpoints behind VPN access.",
                severity="medium",
                evidence=("Admin panel is exposed on the public hostname",),
                actions=("Restrict admin hostname to internal access",),
            ),
        ),
    )

    claims = reconcile_claim_sets((model_a, model_b))

    assert claims == (
        ReconciledClaim(
            claim_id="findings:server-side-request-forgery",
            section="Findings",
            title="Server-side request forgery",
            summary="Metadata endpoint is reachable from the application.",
            severity="high",
            evidence=("GET /latest/meta-data returned 200",),
            actions=("Restrict egress to metadata IPs",),
            supporting_models=("model-a", "model-b"),
            status="agreed",
            confidence=1.0,
        ),
        ReconciledClaim(
            claim_id="findings:verbose-error-disclosure",
            section="Findings",
            title="Verbose error disclosure",
            summary="Stack traces are exposed on invalid input.",
            severity="medium",
            evidence=("POST /api/search returned framework trace",),
            actions=("Replace raw exceptions with generic handlers",),
            supporting_models=("model-a", "model-b"),
            status="partial",
            confidence=0.75,
        ),
        ReconciledClaim(
            claim_id="risks:outdated-tls-baseline",
            section="Risks",
            title="Outdated TLS baseline",
            summary="TLS 1.0 and 1.1 remain enabled.",
            severity="medium",
            evidence=("nmap ssl-enum-ciphers lists TLSv1.0",),
            actions=("Disable TLS 1.0 and TLS 1.1",),
            supporting_models=("model-a", "model-b"),
            status="disputed",
            confidence=0.5,
        ),
        ReconciledClaim(
            claim_id="actions:segment-admin-plane",
            section="Actions",
            title="Segment admin plane",
            summary="Move admin endpoints behind VPN access.",
            severity="medium",
            evidence=("Admin panel is exposed on the public hostname",),
            actions=("Restrict admin hostname to internal access",),
            supporting_models=("model-b",),
            status="unique",
            confidence=0.25,
        ),
    )
