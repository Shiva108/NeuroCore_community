import importlib.util
import threading
from pathlib import Path

from neurocore.core.config import NeuroCoreConfig
from neurocore.core.config import ReportingProviderConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.reporting import (
    build_reporting_status,
    generate_consensus_report,
)
from neurocore.storage.in_memory import InMemoryStore


def _load_mock_provider_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "mock_openai_compatible.py"
    )
    spec = importlib.util.spec_from_file_location("mock_openai_compatible", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    spec.loader.exec_module(module)
    return module


MOCK_PROVIDER = _load_mock_provider_module()


class FakeReporter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        objective: str,
        context_markdown: str,
        sections: tuple[str, ...] = ("Overview", "Findings", "Risks", "Actions"),
    ):
        self.calls.append(
            {
                "objective": objective,
                "context_markdown": context_markdown,
                "sections": sections,
            }
        )
        return {
            "report": "## Overview\nReady.",
            "model_outputs": {"model-a": "## Overview\nReady."},
            "agreement_score": 1.0,
            "metadata": {"sections": list(sections)},
        }


def test_generate_consensus_report_uses_query_request_context():
    store = InMemoryStore()
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
    )
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "Validated SSRF finding with evidence and remediation notes.",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    reporter = FakeReporter()

    response = generate_consensus_report(
        {
            "objective": "Generate a security review report.",
            "query_request": {
                "query_text": "SSRF finding",
                "namespace": "project-alpha",
                "allowed_buckets": ["research"],
                "sensitivity_ceiling": "standard",
            },
            "sections": ["Overview", "Findings"],
            "max_items": 1,
        },
        store=store,
        config=config,
        reporter=reporter,
    )

    assert response["report"].startswith("## Overview")
    assert response["metadata"]["context_source"] == "query_request"
    assert response["metadata"]["query_id"].startswith("query-")
    assert "Validated SSRF finding" in response["context_markdown"]
    assert reporter.calls[0]["sections"] == ("Overview", "Findings")


def test_generate_consensus_report_uses_explicit_context_when_provided():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
    )
    reporter = FakeReporter()

    response = generate_consensus_report(
        {
            "objective": "Generate an operator handoff report.",
            "context_markdown": "Explicit operator context",
        },
        store=InMemoryStore(),
        config=config,
        reporter=reporter,
    )

    assert response["metadata"]["context_source"] == "context_markdown"
    assert response["context_markdown"] == "Explicit operator context"
    assert reporter.calls[0]["context_markdown"] == "Explicit operator context"


def test_generate_consensus_report_requires_enabled_consensus_reporting():
    store = InMemoryStore()
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=False,
    )
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "Validated SSRF finding with evidence and remediation notes.",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = generate_consensus_report(
        {
            "objective": "Generate a report.",
            "query_request": {
                "query_text": "SSRF finding",
                "namespace": "project-alpha",
                "allowed_buckets": ["research"],
                "sensitivity_ceiling": "standard",
            },
        },
        store=store,
        config=config,
        reporter=FakeReporter(),
    )

    assert response["mode"] == "fallback-briefing"
    assert response["report"].startswith("## Overview")
    assert "## Known gaps / stale context" in response["report"]
    assert "(provenance: source=note;" in response["report"]


def test_generate_consensus_report_falls_back_when_reporter_raises():
    store = InMemoryStore()
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
    )
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "Validated SSRF finding with evidence and remediation notes.",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    class FailingReporter:
        def generate(self, **_kwargs):
            raise RuntimeError("reporter unavailable")

    response = generate_consensus_report(
        {
            "objective": "Generate a report.",
            "query_request": {
                "query_text": "SSRF finding",
                "namespace": "project-alpha",
                "allowed_buckets": ["research"],
                "sensitivity_ceiling": "standard",
            },
        },
        store=store,
        config=config,
        reporter=FailingReporter(),
    )

    assert response["mode"] == "fallback-briefing"
    assert response["metadata"]["fallback_reason"] == "reporter unavailable"


def test_generate_consensus_report_success_shape_remains_report_mode():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
    )
    reporter = FakeReporter()

    response = generate_consensus_report(
        {
            "objective": "Generate an operator report.",
            "context_markdown": "Explicit context only.",
        },
        store=InMemoryStore(),
        config=config,
        reporter=reporter,
    )

    assert response["mode"] == "report"
    assert response["report"] == "## Overview\nReady."
    assert "## Known gaps / stale context" not in response["report"]


def test_build_reporting_status_emits_explicit_readiness_fields():
    disabled = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=False,
    )
    enabled = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
        consensus_provider="openai_compatible",
        consensus_model_names=("gpt-1", "gpt-2"),
        consensus_base_url="http://reporter.test",
        consensus_api_key="token",
    )
    invalid_provider = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
        consensus_provider="none",
    )

    disabled_status = build_reporting_status(disabled)
    enabled_status = build_reporting_status(enabled)
    invalid_status = build_reporting_status(invalid_provider)

    assert disabled_status["status"] == "fallback-only"
    assert disabled_status["configured"] is False
    assert disabled_status["bootstrapped"] is False
    assert disabled_status["healthy"] is False
    assert disabled_status["judge_required"] is False
    assert disabled_status["judge_ready"] is False
    assert disabled_status["issues"]

    assert enabled_status["status"] == "healthy"
    assert enabled_status["configured"] is True
    assert enabled_status["bootstrapped"] is True
    assert enabled_status["healthy"] is True
    assert enabled_status["judge_required"] is False
    assert enabled_status["judge_ready"] is False
    assert enabled_status["issues"] == []

    assert invalid_status["status"] == "degraded"
    assert invalid_status["configured"] is False
    assert invalid_status["bootstrapped"] is False
    assert invalid_status["healthy"] is False
    assert invalid_status["judge_required"] is False
    assert invalid_status["judge_ready"] is False
    assert invalid_status["issues"] == [
        "Consensus reporting requires a supported consensus provider"
    ]


def test_build_reporting_status_exposes_judge_readiness():
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

    status = build_reporting_status(config)

    assert status["healthy"] is True
    assert status["judge_required"] is True
    assert status["judge_ready"] is True
    assert status["judge_provider"] == "openai"
    assert status["judge_model"] == "gpt-5-mini"


def test_generate_consensus_report_preserves_claim_consensus_metadata():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_multi_model_consensus=True,
    )

    class ClaimVotingReporter:
        def generate(self, **_kwargs):
            return {
                "report": "## Overview\nReady.\n## Findings\n- Shared finding",
                "model_outputs": {
                    "model-a": "## Overview\nReady.",
                    "model-b": "## Overview\nReady.",
                },
                "agreement_score": 1.0,
                "metadata": {
                    "consensus": {
                        "version": "claim_reconciled_v1",
                        "mode": "claim_voting",
                        "models": ["model-a", "model-b"],
                        "claim_counts": {
                            "total": 1,
                            "agreed": 1,
                            "partial": 0,
                            "disputed": 0,
                            "unique": 0,
                            "included": 1,
                        },
                        "claims": [
                            {
                                "claim_id": "findings:shared-finding",
                                "section": "Findings",
                                "title": "Shared finding",
                                "summary": "Shared finding",
                                "severity": "medium",
                                "evidence": ["shared evidence"],
                                "actions": ["shared action"],
                                "supporting_models": ["model-a", "model-b"],
                                "status": "agreed",
                                "confidence": 1.0,
                            }
                        ],
                        "degraded_to_lexical_selection": False,
                        "judge_used": False,
                        "judge_provider": None,
                        "judge_model": None,
                        "judge_failures": [],
                        "final_confidence": 1.0,
                    }
                },
            }

    response = generate_consensus_report(
        {
            "objective": "Generate an operator handoff report.",
            "context_markdown": "Explicit operator context",
        },
        store=InMemoryStore(),
        config=config,
        reporter=ClaimVotingReporter(),
    )

    consensus = response["metadata"]["consensus"]

    assert response["mode"] == "report"
    assert consensus["mode"] == "claim_voting"
    assert consensus["claim_counts"]["agreed"] == 1
    assert consensus["claims"][0]["claim_id"] == "findings:shared-finding"


def test_generate_consensus_report_uses_provider_registry_with_mock_provider():
    server = MOCK_PROVIDER.ThreadingHTTPServer(("127.0.0.1", 0), MOCK_PROVIDER._Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        config = NeuroCoreConfig(
            default_namespace="project-alpha",
            allowed_buckets=("research",),
            default_sensitivity="standard",
            enable_multi_model_consensus=True,
            reporting_strategy="primary_with_fallback",
            reporting_consensus_mode="lexical_select",
            reporting_primary_provider="deepseek",
            reporting_fallback_provider="openai",
            reporting_provider_configs={
                "deepseek": ReportingProviderConfig(
                    name="deepseek",
                    base_url=base_url,
                    api_key="deepseek-key",
                    model_names=("deepseek-v4-flash", "deepseek-v4-pro"),
                ),
                "openai": ReportingProviderConfig(
                    name="openai",
                    base_url=base_url,
                    api_key="openai-key",
                    model_names=("gpt-5-mini",),
                ),
            },
        )

        response = generate_consensus_report(
            {
                "objective": "Generate an operator handoff report.",
                "context_markdown": "Explicit operator context",
            },
            store=InMemoryStore(),
            config=config,
        )

        status = response["metadata"]["reporting_status"]

        assert response["mode"] == "report"
        assert response["report"].startswith("## Overview")
        assert response["metadata"]["context_source"] == "context_markdown"
        assert status["strategy"] == "primary_with_fallback"
        assert status["consensus_mode"] == "lexical_select"
        assert status["active_provider"] == "deepseek"
        assert status["healthy"] is True
        assert status["provider_status"]["deepseek"]["configured"] is True
        assert status["provider_status"]["openai"]["configured"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
