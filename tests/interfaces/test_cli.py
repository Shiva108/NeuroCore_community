import io
import json
import runpy

import pytest

from neurocore.adapters import cli as cli_module
from neurocore.adapters.cli import main, run_http_server, run_mcp_server
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.storage.in_memory import InMemoryStore


def build_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        max_atomic_tokens=6,
        enable_admin_surface=False,
        enable_background_summarization=True,
    )


def test_cli_capture_and_query_commands_use_library_contracts():
    store = InMemoryStore()
    config = build_config()
    stdout = io.StringIO()

    exit_code = main(
        [
            "capture",
            "--request-json",
            json.dumps(
                {
                    "namespace": "project-alpha",
                    "bucket": "research",
                    "sensitivity": "standard",
                    "content": "cli note",
                    "content_format": "markdown",
                    "source_type": "note",
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    capture_response = json.loads(stdout.getvalue())
    assert capture_response["kind"] == "record"

    stdout = io.StringIO()
    exit_code = main(
        [
            "query",
            "--request-json",
            json.dumps(
                {
                    "query_text": "cli",
                    "namespace": "project-alpha",
                    "allowed_buckets": ["research"],
                    "sensitivity_ceiling": "standard",
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    query_response = json.loads(stdout.getvalue())
    assert len(query_response["results"]) == 1

    stdout = io.StringIO()
    exit_code = main(
        [
            "query",
            "--request-json",
            json.dumps(
                {
                    "query_text": "cli",
                    "namespace": "project-alpha",
                    "allowed_buckets": ["research"],
                    "sensitivity_ceiling": "standard",
                    "include_diagnostics": True,
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    diagnostics_response = json.loads(stdout.getvalue())
    assert diagnostics_response["diagnostics"]["semantic_mode"] == "metadata-only"


def test_cli_capture_batch_command_uses_library_contracts():
    store = InMemoryStore()
    config = build_config()
    stdout = io.StringIO()

    exit_code = main(
        [
            "capture-batch",
            "--request-json",
            json.dumps(
                {
                    "requests": [
                        {
                            "namespace": "project-alpha",
                            "bucket": "research",
                            "sensitivity": "standard",
                            "content": "cli batch note",
                            "content_format": "markdown",
                            "source_type": "note",
                        }
                    ]
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["summary"]["succeeded"] == 1
    assert payload["results"][0]["payload"]["kind"] == "record"


def test_cli_admin_commands_respect_admin_toggle():
    store = InMemoryStore()
    config = build_config()

    with pytest.raises(PermissionError, match="disabled"):
        main(
            [
                "admin",
                "reindex",
                "--request-json",
                json.dumps({"ids": ["rec-1"], "scope": "records"}),
            ],
            store=store,
            config=config,
            stdout=io.StringIO(),
        )

    with pytest.raises(PermissionError, match="disabled"):
        main(
            [
                "admin",
                "sync",
                "--request-json",
                json.dumps({"action": "status"}),
            ],
            store=store,
            config=config,
            stdout=io.StringIO(),
        )

    with pytest.raises(PermissionError, match="disabled"):
        main(
            [
                "admin",
                "audit",
                "--request-json",
                json.dumps({}),
            ],
            store=store,
            config=config,
            stdout=io.StringIO(),
        )


def test_cli_ingest_and_summarize_commands_use_library_contracts():
    store = InMemoryStore()
    config = build_config()
    stdout = io.StringIO()

    exit_code = main(
        [
            "ingest",
            "slack",
            "--request-json",
            json.dumps(
                {
                    "type": "event_callback",
                    "team_id": "T123",
                    "event": {
                        "type": "message",
                        "channel": "C123",
                        "user": "U123",
                        "text": (
                            "Sentence one explains the system. "
                            "Sentence two adds retrieval detail. "
                            "Sentence three covers isolation policy."
                        ),
                        "ts": "1713897900.000100",
                    },
                    "bucket": "research",
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    ingest_response = json.loads(stdout.getvalue())
    assert ingest_response["source"] == "slack"

    stdout = io.StringIO()
    exit_code = main(
        ["summaries", "run", "--request-json", json.dumps({"limit": 10})],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    summary_response = json.loads(stdout.getvalue())
    assert summary_response["processed"] >= 1


def test_cli_admin_audit_command_returns_findings():
    store = InMemoryStore()
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_admin_surface=True,
    )

    main(
        [
            "capture",
            "--request-json",
            json.dumps(
                {
                    "namespace": "project-alpha",
                    "bucket": "research",
                    "sensitivity": "standard",
                    "content": "API_KEY=super-secret-value",
                    "content_format": "markdown",
                    "source_type": "note",
                }
            ),
        ],
        store=store,
        config=config,
        stdout=io.StringIO(),
    )

    stdout = io.StringIO()
    exit_code = main(
        [
            "admin",
            "audit",
            "--request-json",
            json.dumps(
                {
                    "namespace": "project-alpha",
                    "allowed_buckets": ["research"],
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["findings"]


def test_cli_admin_sync_command_returns_status_payload():
    stdout = io.StringIO()
    exit_code = main(
        ["admin", "sync", "--request-json", json.dumps({"action": "status"})],
        store=InMemoryStore(),
        config=NeuroCoreConfig(
            default_namespace="project-alpha",
            allowed_buckets=("research",),
            default_sensitivity="standard",
            enable_admin_surface=True,
        ),
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["action"] == "status"
    assert payload["supported"] is False


def test_cli_report_consensus_command_returns_report_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    called: dict[str, object] = {}

    def fake_generate_consensus_report(
        request, *, store, config, semantic_ranker=None, reporter=None
    ):
        called["request"] = request
        return {
            "report": "## Overview\nReady.",
            "agreement_score": 1.0,
            "model_outputs": {"model-a": "## Overview\nReady."},
            "metadata": {"objective": request["objective"]},
        }

    monkeypatch.setattr(
        cli_module,
        "generate_consensus_report",
        fake_generate_consensus_report,
    )

    stdout = io.StringIO()
    exit_code = main(
        [
            "report",
            "consensus",
            "--request-json",
            json.dumps(
                {
                    "objective": "Generate a review report.",
                    "context_markdown": "Retrieved context",
                }
            ),
        ],
        store=InMemoryStore(),
        config=NeuroCoreConfig(
            default_namespace="project-alpha",
            allowed_buckets=("research",),
            default_sensitivity="standard",
            enable_multi_model_consensus=True,
        ),
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["report"].startswith("## Overview")
    assert called["request"]["objective"] == "Generate a review report."


def test_cli_protocol_list_command_returns_protocol_manifests():
    stdout = io.StringIO()
    exit_code = main(
        ["protocol", "list"],
        store=InMemoryStore(),
        config=build_config(),
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    names = {entry["name"] for entry in payload["protocols"]}
    assert "cti-review-v1" in names


def test_cli_diagnose_returns_sanitized_payload(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEUROCORE_DEFAULT_NAMESPACE", "project-alpha")
    monkeypatch.setenv("NEUROCORE_ALLOWED_BUCKETS", "research")
    monkeypatch.setenv("NEUROCORE_DEFAULT_SENSITIVITY", "standard")
    stdout = io.StringIO()

    exit_code = main(["diagnose"], stdout=stdout)

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["config_ready"] is True
    assert payload["config"]["default_namespace"] == "project-alpha"
    assert payload["semantic"]["backend"] == "none"
    assert payload["reporting"]["status"] == "fallback-only"


def test_cli_diagnose_reports_invalid_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NEUROCORE_DEFAULT_NAMESPACE", raising=False)
    monkeypatch.setenv("NEUROCORE_ALLOWED_BUCKETS", "research")
    monkeypatch.setenv("NEUROCORE_DEFAULT_SENSITIVITY", "standard")
    stdout = io.StringIO()

    exit_code = main(["diagnose"], stdout=stdout)

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["config_ready"] is False
    assert any("NEUROCORE_DEFAULT_NAMESPACE" in issue for issue in payload["issues"])


def test_python_module_entrypoint_delegates_to_cli(
    monkeypatch: pytest.MonkeyPatch,
):
    called: dict[str, object] = {}

    def fake_main(argv=None, **_kwargs):
        called["argv"] = argv
        return 0

    monkeypatch.setattr("neurocore.adapters.cli.main", fake_main)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("neurocore", run_name="__main__")

    assert excinfo.value.code == 0
    assert called["argv"] is None


def test_cli_protocol_run_command_returns_protocol_payload():
    store = InMemoryStore()
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("findings", "reports", "ops", "recon", "agents"),
        default_sensitivity="restricted",
        enable_multi_model_consensus=False,
    )
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "reports",
            "sensitivity": "restricted",
            "content": "critical ciso concern with ATT&CK T1190",
            "content_format": "markdown",
            "source_type": "note",
            "tags": ["ciso-concern", "severity:critical"],
            "title": "Critical external exposure",
        },
        store=store,
        config=config,
    )

    stdout = io.StringIO()
    exit_code = main(
        [
            "protocol",
            "run",
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
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["protocol"]["name"] == "cti-review-v1"
    assert "## Findings" in payload["report"]


def test_cli_brain_and_session_commands_work_end_to_end():
    store = InMemoryStore()
    config = build_config()

    stdout = io.StringIO()
    exit_code = main(
        [
            "brain",
            "create",
            "--request-json",
            json.dumps(
                {
                    "brain_id": "brain-alpha",
                    "namespace": "project-alpha",
                    "display_name": "Project Alpha",
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    create_payload = json.loads(stdout.getvalue())
    assert create_payload["brain"]["brain_id"] == "brain-alpha"

    stdout = io.StringIO()
    exit_code = main(
        [
            "session",
            "checkpoint",
            "--request-json",
            json.dumps(
                {
                    "brain_id": "brain-alpha",
                    "session_id": "sess-1",
                    "source_client": "claude-desktop",
                    "content": "Checkpointed auth bypass investigation.",
                    "bucket": "research",
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    checkpoint_payload = json.loads(stdout.getvalue())
    assert checkpoint_payload["stored"] is True

    stdout = io.StringIO()
    exit_code = main(
        [
            "session",
            "resume",
            "--request-json",
            json.dumps(
                {
                    "brain_id": "brain-alpha",
                    "session_id": "sess-1",
                    "query_text": "auth bypass",
                    "allowed_buckets": ["research"],
                    "sensitivity_ceiling": "standard",
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    resume_payload = json.loads(stdout.getvalue())
    assert resume_payload["session_id"] == "sess-1"
    assert "auth bypass" in resume_payload["briefing"].lower()


def test_cli_report_consensus_command_falls_back_to_briefing_when_disabled():
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

    stdout = io.StringIO()
    exit_code = main(
        [
            "report",
            "consensus",
            "--request-json",
            json.dumps(
                {
                    "objective": "Generate a review report.",
                    "query_request": {
                        "query_text": "SSRF finding",
                        "namespace": "project-alpha",
                        "allowed_buckets": ["research"],
                        "sensitivity_ceiling": "standard",
                    },
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["mode"] == "fallback-briefing"
    assert payload["report"].startswith("## Overview")


def test_cli_briefing_command_returns_briefing_payload():
    store = InMemoryStore()
    config = build_config()
    stdout = io.StringIO()

    main(
        [
            "capture",
            "--request-json",
            json.dumps(
                {
                    "namespace": "project-alpha",
                    "bucket": "research",
                    "sensitivity": "standard",
                    "content": "Validated GraphQL auth bypass note.",
                    "content_format": "markdown",
                    "source_type": "note",
                }
            ),
        ],
        store=store,
        config=config,
        stdout=io.StringIO(),
    )

    exit_code = main(
        [
            "briefing",
            "--request-json",
            json.dumps(
                {
                    "brain_id": "project-alpha",
                    "query_request": {
                        "query_text": "GraphQL auth bypass",
                        "allowed_buckets": ["research"],
                        "sensitivity_ceiling": "standard",
                    },
                }
            ),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert "## Overview" in payload["briefing"]


def test_cli_report_consensus_command_respects_consensus_toggle():
    stdout = io.StringIO()
    exit_code = main(
        [
            "report",
            "consensus",
            "--request-json",
            json.dumps(
                {
                    "objective": "Generate a review report.",
                    "context_markdown": "Retrieved context",
                }
            ),
        ],
        store=InMemoryStore(),
        config=build_config(),
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["mode"] == "fallback-briefing"


def test_cli_serve_http_command_invokes_http_runner(monkeypatch: pytest.MonkeyPatch):
    called: dict[str, object] = {}

    def fake_run_http_server(*, store, config, host, port):
        called["store"] = store
        called["config"] = config
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(cli_module, "run_http_server", fake_run_http_server)

    exit_code = main(
        ["serve", "http", "--host", "0.0.0.0", "--port", "9000"],
        store=InMemoryStore(),
        config=build_config(),
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    assert called["host"] == "0.0.0.0"
    assert called["port"] == 9000


def test_cli_serve_mcp_command_invokes_mcp_runner(monkeypatch: pytest.MonkeyPatch):
    called: dict[str, object] = {}

    def fake_run_mcp_server(*, store, config, transport, mount_path):
        called["store"] = store
        called["config"] = config
        called["transport"] = transport
        called["mount_path"] = mount_path

    monkeypatch.setattr(cli_module, "run_mcp_server", fake_run_mcp_server)

    exit_code = main(
        [
            "serve",
            "mcp",
            "--transport",
            "streamable-http",
            "--mount-path",
            "/mcp",
        ],
        store=InMemoryStore(),
        config=build_config(),
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    assert called["transport"] == "streamable-http"
    assert called["mount_path"] == "/mcp"


def test_run_http_server_requires_http_adapter_toggle():
    with pytest.raises(PermissionError, match="HTTP adapter is disabled"):
        run_http_server(
            store=InMemoryStore(),
            config=build_config(),
            host="127.0.0.1",
            port=8000,
        )


def test_run_mcp_server_requires_mcp_adapter_toggle():
    with pytest.raises(PermissionError, match="MCP adapter is disabled"):
        run_mcp_server(
            store=InMemoryStore(),
            config=build_config(),
            transport="stdio",
            mount_path=None,
        )
