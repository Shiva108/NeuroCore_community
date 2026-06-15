import io
import json
from pathlib import Path
import runpy

import pytest

from neurocore.adapters import cli as cli_module
from neurocore.adapters.cli import main, run_http_server, run_mcp_server
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.storage.in_memory import InMemoryStore
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage.router import RoutedStore
from neurocore.storage.sqlite_store import SQLiteStore


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


def test_cli_capture_reports_partial_mirror_persistence():
    class FailingLocalStore(InMemoryStore):
        def save_record(self, record, signature):
            raise RuntimeError("disk unavailable")

    store = MirroredStore(
        local_store=RoutedStore(
            primary_store=FailingLocalStore(),
            sealed_store=InMemoryStore(),
        ),
        cloud_store=RoutedStore(
            primary_store=InMemoryStore(),
            sealed_store=InMemoryStore(),
        ),
        read_preference="local",
    )
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="mirror",
        mirror_read_preference="local",
        production_backend_provider="supabase",
        production_database_url="postgresql://primary",
        production_sealed_database_url="postgresql://sealed",
    )
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
                    "content": "cli mirror partial",
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
    payload = json.loads(stdout.getvalue())
    assert payload["persistence_state"] == "partial"
    assert payload["parity_state"] == "degraded"
    assert payload["reconciliation_attempted"] is True
    assert payload["mirror_status"]["local_degraded"] is True


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

    with pytest.raises(PermissionError, match="disabled"):
        main(
            [
                "admin",
                "maintenance",
                "--request-json",
                json.dumps({"action": "report"}),
            ],
            store=store,
            config=config,
            stdout=io.StringIO(),
        )


def test_cli_admin_sync_reconcile_union_reports_bidirectional_copy():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_admin_surface=True,
        storage_backend="mirror",
        mirror_read_preference="local",
        production_backend_provider="supabase",
        production_database_url="postgresql://primary",
        production_sealed_database_url="postgresql://sealed",
    )
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "local only cli union",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=local,
        config=config,
    )
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "cloud only cli union",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=cloud,
        config=config,
    )
    stdout = io.StringIO()

    exit_code = main(
        [
            "admin",
            "sync",
            "--request-json",
            json.dumps({"action": "reconcile_union", "actor": "tester"}),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["action"] == "reconcile_union"
    assert payload["parity"]["repair_mode"] == "union"
    assert payload["parity"]["in_sync_after"] is True


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


def test_cli_admin_maintenance_command_returns_sqlite_payload(tmp_path):
    stdout = io.StringIO()
    store = RoutedStore(
        primary_store=SQLiteStore(tmp_path / "primary.db"),
        sealed_store=SQLiteStore(tmp_path / "sealed.db"),
    )
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        storage_backend="sqlite",
        primary_store_path=str(tmp_path / "primary.db"),
        sealed_store_path=str(tmp_path / "sealed.db"),
        enable_admin_surface=True,
    )

    exit_code = main(
        [
            "admin",
            "maintenance",
            "--request-json",
            json.dumps({"action": "report"}),
        ],
        store=store,
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["action"] == "report"
    assert payload["supported"] is True
    assert [target["name"] for target in payload["targets"]] == ["primary", "sealed"]


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


def test_cli_validate_extension_command_does_not_require_runtime_config():
    stdout = io.StringIO()

    exit_code = main(
        ["validate-extension", "skills/daily-memory-triage"],
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["valid"] is True
    assert payload["kind"] == "contribution"
    assert payload["target"] == "skills/daily-memory-triage"
    assert payload["checks_run"] == [
        "contribution_structure",
        "contribution_metadata",
    ]


def test_cli_validate_extension_command_supports_bundle_targets():
    stdout = io.StringIO()

    exit_code = main(
        ["validate-extension", "extensions/bundles/operator-memory-starter.json"],
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["valid"] is True
    assert payload["kind"] == "bundle"
    assert payload["checks_run"] == ["bundle_manifest"]


def test_cli_validate_extension_command_reports_invalid_target(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(cli_module, "REPO_ROOT", repo_root)
    stdout = io.StringIO()

    exit_code = main(["validate-extension", "missing/path"], stdout=stdout)

    assert exit_code == 1
    payload = json.loads(stdout.getvalue())
    assert payload["valid"] is False
    assert payload["target"] == "missing/path"
    assert payload["checks_run"] == []


def test_cli_validate_extension_command_reports_invalid_metadata_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    repo_root = tmp_path / "repo"
    metadata_path = repo_root / "recipes" / "bad-recipe" / "metadata.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps(
            {
                "name": "Bad Recipe",
                "category": "skills",
                "description": "Broken",
                "owner": {"name": "NeuroCore"},
                "version": "1.0.0",
                "requires": {"neurocore": True},
                "tags": ["broken"],
                "difficulty": "beginner",
                "estimated_time": "5 minutes",
            }
        ),
        encoding="utf-8",
    )
    schema_dir = repo_root / ".github"
    schema_dir.mkdir(parents=True)
    source_schema = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "contribution-metadata.schema.json"
    )
    (schema_dir / "contribution-metadata.schema.json").write_text(
        source_schema.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "REPO_ROOT", repo_root)
    stdout = io.StringIO()

    exit_code = main(
        ["validate-extension", "recipes/bad-recipe/metadata.json"],
        stdout=stdout,
    )

    assert exit_code == 1
    payload = json.loads(stdout.getvalue())
    assert payload["kind"] == "metadata"
    assert payload["checks_run"] == [
        "contribution_metadata",
        "contribution_structure",
    ]
    assert any("category must match parent folder" in error for error in payload["errors"])


def test_cli_validate_extension_command_reports_missing_required_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    repo_root = tmp_path / "repo"
    target = repo_root / "recipes" / "quick-capture"
    target.mkdir(parents=True)
    (target / "README.md").write_text("# Quick Capture\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "REPO_ROOT", repo_root)
    stdout = io.StringIO()

    exit_code = main(
        ["validate-extension", "recipes/quick-capture"],
        stdout=stdout,
    )

    assert exit_code == 1
    payload = json.loads(stdout.getvalue())
    assert payload["kind"] == "contribution"
    assert payload["checks_run"] == ["contribution_structure"]
    assert "recipes/quick-capture: missing required metadata.json" in payload["errors"]


def test_cli_scheduler_commands_use_scheduler_interface(tmp_path):
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_scheduler=True,
        scheduler_store_path=str(tmp_path / "scheduler.db"),
    )
    stdout = io.StringIO()

    exit_code = main(
        [
            "scheduler",
            "create",
            "--request-json",
            json.dumps(
                {
                    "job_type": "sync",
                    "schedule_kind": "once",
                    "run_at": "2026-01-01T00:00:00+00:00",
                    "payload": {"action": "status"},
                }
            ),
        ],
        store=InMemoryStore(),
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    created = json.loads(stdout.getvalue())
    assert created["created"] is True
    job_id = created["job"]["job_id"]

    stdout = io.StringIO()
    exit_code = main(
        [
            "scheduler",
            "list",
            "--request-json",
            "{}",
        ],
        store=InMemoryStore(),
        config=config,
        stdout=stdout,
    )

    assert exit_code == 0
    listed = json.loads(stdout.getvalue())
    assert listed["count"] == 1
    assert listed["jobs"][0]["job_id"] == job_id


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

    def fake_run_mcp_server(*, store, config, transport, mount_path, host, port):
        called["store"] = store
        called["config"] = config
        called["transport"] = transport
        called["mount_path"] = mount_path
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(cli_module, "run_mcp_server", fake_run_mcp_server)

    exit_code = main(
        [
            "serve",
            "mcp",
            "--transport",
            "streamable-http",
            "--mount-path",
            "/mcp",
            "--host",
            "0.0.0.0",
            "--port",
            "9100",
        ],
        store=InMemoryStore(),
        config=build_config(),
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    assert called["transport"] == "streamable-http"
    assert called["mount_path"] == "/mcp"
    assert called["host"] == "0.0.0.0"
    assert called["port"] == 9100


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
            host="127.0.0.1",
            port=8000,
        )
