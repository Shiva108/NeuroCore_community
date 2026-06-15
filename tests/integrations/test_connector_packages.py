import importlib.util
import io
import json
from pathlib import Path

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.storage.in_memory import InMemoryStore


def _load_connector(relative_path: str):
    module_path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("findings", "reports", "ops", "recon", "agents"),
        default_sensitivity="restricted",
        enable_multi_model_consensus=False,
    )


def test_slack_connector_ingests_and_queries_end_to_end():
    connector = _load_connector("integrations/slack-connector/connector.py")
    store = InMemoryStore()
    config = _config()

    ingest_payload = connector.run_for_test(
        [
            "ingest",
            "--request-json",
            json.dumps(
                {
                    "type": "event_callback",
                    "team_id": "T123",
                    "event": {
                        "type": "message",
                        "channel": "C123",
                        "user": "U123",
                        "text": "critical ciso concern with ATT&CK note",
                        "ts": "1713897900.000100",
                    },
                    "bucket": "reports",
                }
            ),
        ],
        store=store,
        config=config,
    )
    query_payload = connector.run_for_test(
        [
            "query",
            "--request-json",
            json.dumps(
                {
                    "namespace": "slack-t123",
                    "query_text": "ATT&CK note",
                    "allowed_buckets": ["reports"],
                    "sensitivity_ceiling": "restricted",
                }
            ),
        ],
        store=store,
        config=config,
    )

    assert ingest_payload["ignored"] is False
    assert query_payload["results"]
    assert query_payload["results"][0]["namespace"] == "slack-t123"


def test_slack_connector_supports_brain_management_and_session_resume():
    connector = _load_connector("integrations/slack-connector/connector.py")
    store = InMemoryStore()
    config = _config()

    create_payload = connector.run_for_test(
        [
            "create-brain",
            "--request-json",
            json.dumps(
                {
                    "brain_id": "slack-incident",
                    "namespace": "slack-incident",
                    "display_name": "Slack Incident",
                }
            ),
        ],
        store=store,
        config=config,
    )
    capture_payload = connector.run_for_test(
        [
            "session-capture",
            "--request-json",
            json.dumps(
                {
                    "brain_id": "slack-incident",
                    "session_id": "sess-1",
                    "source_client": "slack",
                    "summary": "Checkpoint: validated incident timeline and next escalation.",
                    "workflow_stage": "triage",
                    "importance": "high",
                }
            ),
        ],
        store=store,
        config=config,
    )
    resume_payload = connector.run_for_test(
        [
            "session-resume",
            "--request-json",
            json.dumps(
                {
                    "brain_id": "slack-incident",
                    "session_id": "sess-1",
                    "query_text": "incident timeline",
                    "allowed_buckets": ["agents"],
                    "sensitivity_ceiling": "restricted",
                }
            ),
        ],
        store=store,
        config=config,
    )

    assert create_payload["brain"]["brain_id"] == "slack-incident"
    assert capture_payload["stored"] is True
    assert "incident timeline" in resume_payload["briefing"].lower()
    assert store.audit_events[-1]["operation"] == "connector_command"
    assert store.audit_events[-1]["details"]["action"] == "resume_session"


def test_slack_connector_exposes_shared_health_and_setup_contract():
    connector = _load_connector("integrations/slack-connector/connector.py")
    store = InMemoryStore()
    config = _config()

    health_payload = connector.run_for_test(["health"], store=store, config=config)

    assert health_payload["healthy"] is True
    assert "describe_capabilities" in health_payload["supported_verbs"]
    assert health_payload["reporting_status"]["status"] == "fallback-only"


def test_slack_connector_ingests_article_into_raw_and_knowledge_memory(monkeypatch):
    class DummyResponse:
        headers = {"Content-Type": "text/markdown; charset=utf-8"}

        def read(self):
            return (
                b"# Reusable LDAP Enumeration\n\n"
                b"Reusable LDAP reconnaissance workflow for red teams with "
                b"detection-aware collection, operator notes, and portable "
                b"checklists that apply across environments without "
                b"client-specific dependencies. Use narrow LDAP queries first. "
                b"Expand scope iteratively. Document assumptions. Turn "
                b"recurring checks into reusable operator notes. Review "
                b"detections before large collections. Capture follow-up "
                b"actions for downstream automation. Validate bind methods, "
                b"naming context scope, and collection fallbacks before moving "
                b"to larger directory sweeps. Track how assumptions affect "
                b"coverage, how failed queries change the plan, how operator "
                b"notes should be written for later retrieval, and how results "
                b"should feed reusable playbooks for HackingAgent. The article "
                b"also explains when to stop broad collection, when to pivot "
                b"into host or service validation, when to compare results with "
                b"existing detections, and how to write concise checklists that "
                b"another operator can reuse in a different environment. It "
                b"closes with repeatable troubleshooting questions, evidence "
                b"logging guidance, and small validation loops that make the "
                b"material durable for later machine reuse."
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )
    connector = _load_connector("integrations/slack-connector/connector.py")
    store = InMemoryStore()
    config = _config()

    ingest_payload = connector.run_for_test(
        [
            "ingest",
            "--request-json",
            json.dumps(
                {
                    "type": "event_callback",
                    "team_id": "T123",
                    "event": {
                        "type": "message",
                        "channel": "C123",
                        "user": "U123",
                        "text": "article: https://example.invalid/articles/ldap",
                        "ts": "1713897900.000100",
                    },
                    "bucket": "reports",
                }
            ),
        ],
        store=store,
        config=config,
    )

    assert ingest_payload["mode"] == "article"
    assert ingest_payload["evaluation"]["accepted"] is True
    assert store.get_document(ingest_payload["raw_capture"]["id"]) is not None
    assert store.get_record(ingest_payload["knowledge_capture"]["id"]) is not None


def test_slack_connector_supports_distill_slash_command_payload(monkeypatch):
    class DummyResponse:
        headers = {"Content-Type": "text/markdown; charset=utf-8"}

        def read(self):
            return (
                b"# Reusable LDAP Enumeration\n\n"
                b"Reusable LDAP reconnaissance workflow for red teams with "
                b"detection-aware collection, operator notes, and portable "
                b"checklists that apply across environments without "
                b"client-specific dependencies. Use narrow LDAP queries first. "
                b"Expand scope iteratively. Document assumptions. Turn "
                b"recurring checks into reusable operator notes. Review "
                b"detections before large collections. Capture follow-up "
                b"actions for downstream automation. Validate bind methods, "
                b"naming context scope, and collection fallbacks before moving "
                b"to larger directory sweeps. Track how assumptions affect "
                b"coverage, how failed queries change the plan, how operator "
                b"notes should be written for later retrieval, and how results "
                b"should feed reusable playbooks for HackingAgent. The article "
                b"also explains when to stop broad collection, when to pivot "
                b"into host or service validation, when to compare results with "
                b"existing detections, and how to write concise checklists that "
                b"another operator can reuse in a different environment. It "
                b"closes with repeatable troubleshooting questions, evidence "
                b"logging guidance, and small validation loops that make the "
                b"material durable for later machine reuse."
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )
    connector = _load_connector("integrations/slack-connector/connector.py")
    store = InMemoryStore()
    config = _config()

    ingest_payload = connector.run_for_test(
        [
            "ingest",
            "--request-json",
            json.dumps(
                {
                    "command": "/distill",
                    "team_id": "T123",
                    "channel_id": "C123",
                    "user_id": "U123",
                    "trigger_id": "1337.42",
                    "text": "https://example.invalid/articles/ldap",
                    "bucket": "reports",
                }
            ),
        ],
        store=store,
        config=config,
    )

    assert ingest_payload["mode"] == "article"
    assert ingest_payload["evaluation"]["accepted"] is True
    assert store.get_document(ingest_payload["raw_capture"]["id"]) is not None


def test_discord_connector_ingests_and_runs_protocol():
    connector = _load_connector("integrations/discord-connector/connector.py")
    store = InMemoryStore()
    config = _config()

    connector.run_for_test(
        [
            "ingest",
            "--request-json",
            json.dumps(
                {
                    "t": "MESSAGE_CREATE",
                    "d": {
                        "id": "msg-1",
                        "guild_id": "G123",
                        "channel_id": "C123",
                        "timestamp": "2026-04-29T10:00:00Z",
                        "content": "critical ciso concern with CVE-2026-0001",
                        "author": {"id": "U1", "username": "analyst"},
                    },
                    "bucket": "findings",
                }
            ),
        ],
        store=store,
        config=config,
    )
    protocol_payload = connector.run_for_test(
        [
            "protocol",
            "--request-json",
            json.dumps(
                {
                    "name": "cti-review-v1",
                    "namespace": "discord-g123",
                    "query_text": "CVE-2026-0001",
                    "allowed_buckets": ["findings"],
                }
            ),
        ],
        store=store,
        config=config,
    )

    assert protocol_payload["protocol"]["name"] == "cti-review-v1"
    assert "## Overview" in protocol_payload["report"]
    assert "## Findings" in protocol_payload["report"]


def test_claude_desktop_connector_lists_tools_and_runs_protocol():
    connector = _load_connector("integrations/claude-desktop-mcp/connector.py")
    store = InMemoryStore()
    config = _config()
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "reports",
            "sensitivity": "restricted",
            "content": "critical operator concern with ATT&CK T1190",
            "content_format": "markdown",
            "source_type": "note",
            "tags": ["ciso-concern", "severity:critical"],
            "title": "Critical external exposure",
        },
        store=store,
        config=config,
    )

    tools_payload = connector.run_for_test(
        ["describe-tools"],
        store=store,
        config=config,
    )
    protocol_payload = connector.run_for_test(
        [
            "protocol",
            "--request-json",
            json.dumps(
                {
                    "name": "cti-review-v1",
                    "namespace": "project-alpha",
                    "query_text": "ATT&CK T1190",
                    "allowed_buckets": ["reports"],
                }
            ),
        ],
        store=store,
        config=config,
    )

    assert "run_protocol" in tools_payload["tools"]
    assert "generate_briefing" in tools_payload["tools"]
    assert (
        protocol_payload["protocol"]["prioritization_strategy"]
        == "severity+intel-tags+operator-concern"
    )
    assert "## Actions" in protocol_payload["report"]


def test_claude_desktop_connector_exposes_shared_contract_commands():
    connector = _load_connector("integrations/claude-desktop-mcp/connector.py")
    store = InMemoryStore()
    config = _config()

    payload = connector.run_for_test(
        ["describe-capabilities"], store=store, config=config
    )

    assert payload["healthy"] is True
    assert "select_brain" in payload["supported_verbs"]
    assert "setup_instructions" in payload


def test_claude_desktop_connector_lists_protocols_and_resumes_sessions():
    connector = _load_connector("integrations/claude-desktop-mcp/connector.py")
    store = InMemoryStore()
    config = _config()
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "agents",
            "sensitivity": "restricted",
            "content": "Checkpoint: validated auth bypass chain and queued exploit retest.",
            "content_format": "markdown",
            "source_type": "session_checkpoint",
            "tags": ["artifact:session-checkpoint", "session-id:sess-1"],
        },
        store=store,
        config=config,
    )

    protocols_payload = connector.run_for_test(
        ["list-protocols"],
        store=store,
        config=config,
    )
    resume_payload = connector.run_for_test(
        [
            "session-resume",
            "--request-json",
            json.dumps(
                {
                    "namespace": "project-alpha",
                    "session_id": "sess-1",
                    "query_text": "auth bypass chain",
                    "allowed_buckets": ["agents"],
                    "sensitivity_ceiling": "restricted",
                }
            ),
        ],
        store=store,
        config=config,
    )

    assert any(
        protocol["name"] == "resume-brain-v1"
        for protocol in protocols_payload["protocols"]
    )
    assert "auth bypass chain" in resume_payload["briefing"].lower()


def test_slack_connector_query_records_runtime_audit():
    connector = _load_connector("integrations/slack-connector/connector.py")
    store = InMemoryStore()
    config = _config()
    capture_memory(
        {
            "namespace": "slack-t123",
            "bucket": "reports",
            "sensitivity": "restricted",
            "content": "connector audit trail note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    payload = connector.run_for_test(
        [
            "query",
            "--request-json",
            json.dumps(
                {
                    "namespace": "slack-t123",
                    "query_text": "audit trail",
                    "allowed_buckets": ["reports"],
                    "sensitivity_ceiling": "restricted",
                }
            ),
        ],
        store=store,
        config=config,
    )

    assert payload["results"]
    assert store.audit_events[-1]["operation"] == "connector_command"
    assert store.audit_events[-1]["details"]["source_surface"] == "slack-connector"
    assert store.audit_events[-1]["details"]["action"] == "query_memory"


def test_claude_desktop_connector_returns_structured_failure_payload(monkeypatch):
    connector = _load_connector("integrations/claude-desktop-mcp/connector.py")
    store = InMemoryStore()
    config = _config()
    stdout = io.StringIO()

    monkeypatch.setattr(
        connector,
        "run_protocol",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("protocol blew up")),
    )

    exit_code = connector.main(
        [
            "protocol",
            "--request-json",
            json.dumps(
                {
                    "name": "cti-review-v1",
                    "namespace": "project-alpha",
                    "query_text": "auth bypass chain",
                    "allowed_buckets": ["reports"],
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["error"] == "protocol blew up"
    assert store.audit_events[-1]["operation"] == "connector_command"
    assert store.audit_events[-1]["details"]["status"] == "failed"
