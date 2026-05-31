from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.brains import create_brain
from neurocore.interfaces.sessions import (
    capture_session_event,
    checkpoint_session,
    resume_session,
)
from neurocore.storage.in_memory import InMemoryStore


def _config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="default-brain",
        allowed_buckets=("agents", "ops", "reports", "findings"),
        default_sensitivity="restricted",
        enable_multi_model_consensus=False,
    )


def test_capture_session_event_skips_low_signal_turn_by_default():
    store = InMemoryStore()
    config = _config()

    response = capture_session_event(
        {
            "namespace": "project-alpha",
            "session_id": "sess-1",
            "source_client": "claude-desktop",
            "content": "Minor conversational turn",
            "event_type": "turn",
            "importance": "low",
        },
        store=store,
        config=config,
    )

    assert response["stored"] is False
    assert response["skipped"] is True


def test_checkpoint_and_resume_session_work_with_brain_id_only():
    store = InMemoryStore()
    config = _config()
    create_brain(
        {
            "brain_id": "alpha-brain",
            "namespace": "project-alpha",
            "display_name": "Project Alpha",
        },
        store=store,
        default_allowed_buckets=config.allowed_buckets,
    )

    checkpoint = checkpoint_session(
        {
            "brain_id": "alpha-brain",
            "session_id": "sess-1",
            "source_client": "claude-desktop",
            "summary": "Validated SSRF path and queued remediation follow-up.",
            "workflow_stage": "phase5",
            "importance": "high",
        },
        store=store,
        config=config,
    )
    resumed = resume_session(
        {
            "brain_id": "alpha-brain",
            "session_id": "sess-1",
            "query_text": "validated ssrf path",
            "allowed_buckets": ["agents"],
            "sensitivity_ceiling": "restricted",
        },
        store=store,
        config=config,
    )

    assert checkpoint["stored"] is True
    assert checkpoint["brain_id"] == "alpha-brain"
    assert resumed["namespace"] == "project-alpha"
    assert resumed["brain_id"] == "alpha-brain"
    assert "validated ssrf path" in resumed["briefing"].lower()
