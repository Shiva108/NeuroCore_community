from __future__ import annotations

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.protocols import list_protocols, run_protocol
from neurocore.storage.in_memory import InMemoryStore


def build_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="security-lab",
        allowed_buckets=("recon", "findings", "reports", "agents", "ops"),
        default_sensitivity="restricted",
        enable_multi_model_consensus=False,
    )


def test_run_protocol_cti_review_v1_returns_protocol_payload_with_required_sections():
    store = InMemoryStore()
    config = build_config()
    capture_memory(
        {
            "namespace": "security-lab",
            "bucket": "findings",
            "sensitivity": "restricted",
            "content": (
                "Critical finding. CVE-2026-1234 affects the review target. "
                "Next action: validate exposure and prioritize remediation."
            ),
            "content_format": "markdown",
            "source_type": "note",
            "tags": ["ciso-concern"],
        },
        store=store,
        config=config,
    )

    response = run_protocol(
        {
            "name": "cti-review-v1",
            "namespace": "security-lab",
            "query_text": "critical finding",
        },
        store=store,
        config=config,
    )

    assert response["protocol"]["name"] == "cti-review-v1"
    assert response["report"].startswith("## Overview")
    assert "## Findings" in response["report"]
    assert "## Actions" in response["report"]


def test_list_protocols_returns_supported_protocol_manifests():
    protocols = list_protocols()

    assert len(protocols) >= 11
    names = {protocol["name"] for protocol in protocols}
    assert "resume-brain-v1" in names
    assert "project-review-v1" in names
    assert "memory-audit-v1" in names
    assert "cti-review-v1" in names
    assert "engagement-review-v1" in names
    assert "brain-inbox-triage-v1" in names
    assert "operator-briefing-v1" in names
    assert "project-handoff-v1" in names
    assert "session-review-v1" in names
    assert "engagement-next-actions-v1" in names
    assert "report-prep-v1" in names


def test_run_protocol_resume_brain_v1_returns_required_sections():
    store = InMemoryStore()
    config = build_config()
    capture_memory(
        {
            "namespace": "security-lab",
            "bucket": "agents",
            "sensitivity": "restricted",
            "content": "Checkpoint: validated auth bypass path and queued retest.",
            "content_format": "markdown",
            "source_type": "session_checkpoint",
            "tags": ["artifact:session-checkpoint", "session-id:sess-1"],
        },
        store=store,
        config=config,
    )

    response = run_protocol(
        {
            "name": "resume-brain-v1",
            "namespace": "security-lab",
            "query_text": "auth bypass checkpoint",
            "allowed_buckets": ["agents"],
        },
        store=store,
        config=config,
    )

    assert response["protocol"]["name"] == "resume-brain-v1"
    assert "## Overview" in response["report"]
    assert "## Relevant Memory" in response["report"]
    assert "## Next Actions" in response["report"]
