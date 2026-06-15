import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_security_workflow_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "security_workflow.py"
    )
    spec = importlib.util.spec_from_file_location(
        "security_workflow_module", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SECURITY_WORKFLOW = load_security_workflow_module()


def test_runtime_env_sets_bridge_defaults_when_env_file_is_missing(tmp_path: Path):
    env = SECURITY_WORKFLOW._runtime_env(tmp_path)

    assert env["NEUROCORE_ALLOWED_BUCKETS"] == ",".join(
        SECURITY_WORKFLOW.SECURITY_BUCKETS
    )
    assert env["NEUROCORE_DEFAULT_SENSITIVITY"] == "restricted"
    assert env["NEUROCORE_DEFAULT_NAMESPACE"] == "security-lab"
    assert env["PYTHONPATH"] == str(tmp_path / "src")


def test_runtime_env_loads_operator_home_env_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    operator_home = tmp_path / ".operator-state"
    operator_home.mkdir()
    (operator_home / ".env").write_text(
        "\n".join(
            [
                f"NEUROCORE_OPERATOR_HOME={operator_home}",
                "NEUROCORE_DEFAULT_NAMESPACE=project-alpha",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))

    env = SECURITY_WORKFLOW._runtime_env(tmp_path)

    assert env["NEUROCORE_OPERATOR_HOME"] == str(operator_home)
    assert env["NEUROCORE_DEFAULT_NAMESPACE"] == "project-alpha"


def test_capabilities_reports_missing_namespace_and_disabled_reporting(tmp_path: Path):
    env = {
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
    }

    payload = SECURITY_WORKFLOW._capabilities_payload(tmp_path, env)

    assert payload["config_ready"] is False
    assert payload["query_ready"] is False
    assert payload["report_ready"] is False
    assert payload["semantic_ready"] is False
    assert payload["default_namespace_ready"] is False
    assert payload["consensus_report_ready"] is False
    assert payload["report_provider_mode"] == "disabled"
    assert payload["resolved_python"] is None
    assert "default_namespace" in payload["issues_by_surface"]
    assert any("NEUROCORE_DEFAULT_NAMESPACE" in issue for issue in payload["issues"])
    assert any("operator env file" in issue for issue in payload["issues"])


def test_capabilities_reports_missing_operator_env_without_explicit_state_hints(
    tmp_path: Path,
):
    payload = SECURITY_WORKFLOW._capabilities_payload(
        tmp_path,
        {
            "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
            "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
            "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        },
    )

    assert any("operator env file" in issue for issue in payload["issues"])


def test_capabilities_reports_ready_config_and_consensus(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=security-lab\n", encoding="utf-8"
    )
    python_path = tmp_path / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("", encoding="utf-8")
    env = {
        "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        "NEUROCORE_SEMANTIC_BACKEND": "none",
        "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS": "true",
        "NEUROCORE_CONSENSUS_PROVIDER": "openai_compatible",
        "NEUROCORE_CONSENSUS_MODEL_NAMES": "model-a,model-b",
        "NEUROCORE_CONSENSUS_BASE_URL": "https://example.invalid/v1",
        "NEUROCORE_CONSENSUS_API_KEY": "test-key",
    }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        SECURITY_WORKFLOW, "_check_reporter_health", lambda config: (True, None)
    )
    try:
        payload = SECURITY_WORKFLOW._capabilities_payload(tmp_path, env)
    finally:
        monkeypatch.undo()

    assert payload["config_ready"] is True
    assert payload["query_ready"] is True
    assert payload["briefing_ready"] is True
    assert payload["report_ready"] is True
    assert payload["semantic_ready"] is True
    assert payload["default_namespace_ready"] is True
    assert payload["consensus_report_ready"] is True
    assert payload["distillation_ready"] is True
    assert payload["shared_corpus_ready"] is True
    assert payload["report_provider_mode"] == "external_openai_compatible"
    assert payload["resolved_python"] == str(python_path.absolute())
    assert payload["issues"] == []


def test_capabilities_reports_mock_local_provider_mode(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=security-lab\n", encoding="utf-8"
    )
    env = {
        "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        "NEUROCORE_SEMANTIC_BACKEND": "none",
        "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS": "true",
        "NEUROCORE_CONSENSUS_PROVIDER": "openai_compatible",
        "NEUROCORE_CONSENSUS_MODEL_NAMES": "model-a,model-b",
        "NEUROCORE_CONSENSUS_BASE_URL": SECURITY_WORKFLOW.LOCAL_CONSENSUS_BASE_URL,
        "NEUROCORE_CONSENSUS_API_KEY": "local-dev-key",
    }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        SECURITY_WORKFLOW, "_check_reporter_health", lambda config: (True, None)
    )
    try:
        payload = SECURITY_WORKFLOW._capabilities_payload(tmp_path, env)
    finally:
        monkeypatch.undo()

    assert payload["report_ready"] is True
    assert payload["briefing_ready"] is True
    assert payload["report_provider_mode"] == "mock_local"
    assert payload["distillation_ready"] is False
    assert payload["shared_corpus_ready"] is False


def test_capabilities_marks_report_unready_when_local_mock_health_fails(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=security-lab\n", encoding="utf-8"
    )
    env = {
        "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        "NEUROCORE_SEMANTIC_BACKEND": "none",
        "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS": "true",
        "NEUROCORE_CONSENSUS_PROVIDER": "openai_compatible",
        "NEUROCORE_CONSENSUS_MODEL_NAMES": "model-a,model-b",
        "NEUROCORE_CONSENSUS_BASE_URL": SECURITY_WORKFLOW.LOCAL_CONSENSUS_BASE_URL,
        "NEUROCORE_CONSENSUS_API_KEY": "local-dev-key",
    }
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_check_reporter_health",
        lambda config: (False, "mock provider health check failed"),
    )
    try:
        payload = SECURITY_WORKFLOW._capabilities_payload(tmp_path, env)
    finally:
        monkeypatch.undo()

    assert payload["query_ready"] is True
    assert payload["briefing_ready"] is True
    assert payload["report_ready"] is False
    assert payload["distillation_ready"] is False
    assert payload["shared_corpus_ready"] is False
    assert payload["issues_by_surface"]["report"] == [
        "mock provider health check failed"
    ]


def test_capabilities_bootstraps_local_mock_and_rechecks_health(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=security-lab\n", encoding="utf-8"
    )
    env = {
        "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        "NEUROCORE_SEMANTIC_BACKEND": "none",
        "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS": "true",
        "NEUROCORE_CONSENSUS_PROVIDER": "openai_compatible",
        "NEUROCORE_CONSENSUS_MODEL_NAMES": "model-a,model-b",
        "NEUROCORE_CONSENSUS_BASE_URL": SECURITY_WORKFLOW.LOCAL_CONSENSUS_BASE_URL,
        "NEUROCORE_CONSENSUS_API_KEY": "local-dev-key",
    }
    health_checks = iter(
        [
            (False, "mock provider health check failed"),
            (True, None),
        ]
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_check_reporter_health",
        lambda config: next(health_checks),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_bootstrap_reporter",
        lambda repo_root, env, config: {
            "mode": "mock_local",
            "started": True,
            "healthy": True,
            "base_url": SECURITY_WORKFLOW.LOCAL_CONSENSUS_BASE_URL,
        },
    )
    try:
        payload = SECURITY_WORKFLOW._capabilities_payload(tmp_path, env)
    finally:
        monkeypatch.undo()

    assert payload["report_ready"] is True
    assert payload["report_bootstrap_attempted"] is True
    assert payload["report_bootstrap_started"] is True


def test_check_reporter_health_uses_root_health_endpoint_for_local_mock(
    monkeypatch: pytest.MonkeyPatch,
):
    class DummyConfig:
        consensus_base_url = SECURITY_WORKFLOW.LOCAL_CONSENSUS_BASE_URL
        consensus_api_key = "local-dev-key"

    requested_urls: list[str] = []

    class DummyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout: float = 0.0):
        requested_urls.append(request.full_url)
        return DummyResponse()

    monkeypatch.setattr(SECURITY_WORKFLOW.urllib_request, "urlopen", fake_urlopen)

    ready, issue = SECURITY_WORKFLOW._check_reporter_health(DummyConfig())

    assert ready is True
    assert issue is None
    assert requested_urls == ["http://127.0.0.1:8787/health"]


def test_check_provider_health_returns_error_when_request_times_out(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = SECURITY_WORKFLOW.ReportingProviderConfig(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model_names=("gpt-5-mini",),
    )

    def fake_urlopen(request, timeout: float = 0.0):
        raise TimeoutError("timed out")

    monkeypatch.setattr(SECURITY_WORKFLOW.urllib_request, "urlopen", fake_urlopen)

    ready, issue = SECURITY_WORKFLOW._check_provider_health("openai", provider)

    assert ready is False
    assert issue == "Provider openai health check failed: timed out"


def test_is_local_mock_base_url_accepts_ephemeral_local_ports():
    assert (
        SECURITY_WORKFLOW._is_local_mock_base_url("http://127.0.0.1:39321/v1") is True
    )
    assert SECURITY_WORKFLOW._is_local_mock_base_url("http://localhost:8080/v1") is True
    assert (
        SECURITY_WORKFLOW._is_local_mock_base_url("https://api.example.test/v1")
        is False
    )


def test_capabilities_marks_config_unready_when_semantic_backend_dependency_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=security-lab\n", encoding="utf-8"
    )
    env = {
        "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        "NEUROCORE_SEMANTIC_BACKEND": "sentence-transformers",
    }
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "sentence_transformers_status",
        lambda: ("unavailable", "sentence-transformers missing"),
    )

    payload = SECURITY_WORKFLOW._capabilities_payload(tmp_path, env)

    assert payload["config_ready"] is False
    assert payload["query_ready"] is False
    assert payload["briefing_ready"] is False
    assert payload["consensus_report_ready"] is False
    assert payload["report_ready"] is False
    assert payload["semantic_ready"] is False
    assert payload["issues_by_surface"]["semantic"] == ["sentence-transformers missing"]
    assert "sentence-transformers missing" in payload["issues"]


def test_capabilities_query_ready_without_report_when_consensus_disabled(
    tmp_path: Path,
):
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=security-lab\n", encoding="utf-8"
    )
    env = {
        "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        "NEUROCORE_SEMANTIC_BACKEND": "none",
        "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS": "false",
    }

    payload = SECURITY_WORKFLOW._capabilities_payload(tmp_path, env)

    assert payload["config_ready"] is True
    assert payload["query_ready"] is True
    assert payload["briefing_ready"] is True
    assert payload["semantic_ready"] is True
    assert payload["report_ready"] is False
    assert payload["consensus_report_ready"] is False
    assert payload["report_provider_mode"] == "disabled"
    assert payload["issues_by_surface"]["report"] == ["Consensus reporting disabled"]


def test_capabilities_query_ready_without_report_when_consensus_key_missing(
    tmp_path: Path,
):
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=security-lab\n", encoding="utf-8"
    )
    env = {
        "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        "NEUROCORE_SEMANTIC_BACKEND": "none",
        "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS": "true",
        "NEUROCORE_CONSENSUS_PROVIDER": "openai_compatible",
        "NEUROCORE_CONSENSUS_MODEL_NAMES": "model-a,model-b",
        "NEUROCORE_CONSENSUS_BASE_URL": "https://example.invalid/v1",
    }

    payload = SECURITY_WORKFLOW._capabilities_payload(tmp_path, env)

    assert payload["query_ready"] is True
    assert payload["briefing_ready"] is True
    assert payload["report_ready"] is False
    assert payload["consensus_report_ready"] is False
    assert payload["report_provider_mode"] == "external_openai_compatible"
    assert payload["issues_by_surface"]["report"] == [
        "Consensus reporting requires a consensus API key"
    ]


def test_resolve_repo_python_prefers_override_then_unix_then_windows(tmp_path: Path):
    override = tmp_path / "python-custom"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("", encoding="utf-8")

    assert (
        SECURITY_WORKFLOW._resolve_repo_python(
            tmp_path, {"NEUROCORE_PYTHON_EXECUTABLE": str(override)}
        )
        == override
    )

    unix_python = tmp_path / ".venv" / "bin" / "python"
    unix_python.parent.mkdir(parents=True, exist_ok=True)
    unix_python.write_text("", encoding="utf-8")
    assert SECURITY_WORKFLOW._resolve_repo_python(tmp_path, {}) == unix_python

    unix_python.unlink()
    windows_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    windows_python.parent.mkdir(parents=True, exist_ok=True)
    windows_python.write_text("", encoding="utf-8")
    assert SECURITY_WORKFLOW._resolve_repo_python(tmp_path, {}) == windows_python


def test_maybe_reexec_into_repo_runtime_uses_repo_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    python_path = tmp_path / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "prefix", "/usr")
    called: dict[str, object] = {}

    def fake_execve(binary, argv, env):
        called["binary"] = binary
        called["argv"] = argv
        called["env"] = env
        raise SystemExit(0)

    monkeypatch.setattr(SECURITY_WORKFLOW.os, "execve", fake_execve)

    with pytest.raises(SystemExit):
        SECURITY_WORKFLOW._maybe_reexec_into_repo_runtime(["capabilities"], tmp_path)

    expected_script = str(Path(SECURITY_WORKFLOW.__file__).resolve())
    assert called["binary"] == str(python_path.absolute())
    assert called["argv"] == [
        str(python_path.absolute()),
        expected_script,
        "capabilities",
    ]
    assert called["env"]["NEUROCORE_SKIP_RUNTIME_REEXEC"] == "1"


def test_run_neurocore_uses_resolved_python_and_formats_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    python_path = tmp_path / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("", encoding="utf-8")

    def fake_run(command, cwd, env, text, capture_output):
        assert command[:4] == [
            str(python_path),
            "-m",
            "neurocore.adapters.cli",
            "query",
        ]
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""},
        )()

    monkeypatch.setattr(SECURITY_WORKFLOW.subprocess, "run", fake_run)
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW._run_neurocore(
        tmp_path,
        {"NEUROCORE_DEFAULT_NAMESPACE": "security-lab"},
        "query",
        {"query_text": "hello"},
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["ok"] is True


def test_main_capabilities_prints_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)
    monkeypatch.setattr(SECURITY_WORKFLOW.Path, "resolve", lambda self: tmp_path)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    exit_code = SECURITY_WORKFLOW.main(["capabilities"])

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert "config_ready" in payload
    assert "report_provider_mode" in payload


def test_main_forwards_capture_hackingagent_query_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return 0

    monkeypatch.setattr(SECURITY_WORKFLOW, "_run_neurocore", fake_run_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW.Path, "resolve", lambda self: tmp_path)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW, "_read_text_file", lambda path, description: "artifact body"
    )

    artifact = tmp_path / "artifact.md"
    artifact.write_text("artifact body", encoding="utf-8")

    assert (
        SECURITY_WORKFLOW.main(
            [
                "capture-hackingagent",
                "--namespace",
                "pt-acme",
                "--artifact-type",
                "report-summary",
                "--force-kind",
                "document",
                "--target",
                "acme",
                str(artifact),
            ]
        )
        == 0
    )
    assert (
        SECURITY_WORKFLOW.main(["query", "--namespace", "pt-acme", "acme findings"])
        == 0
    )
    assert (
        SECURITY_WORKFLOW.main(
            [
                "report",
                "--namespace",
                "pt-acme",
                "--objective",
                "Generate report",
                "acme findings",
            ]
        )
        == 0
    )

    capture_command, capture_request = calls[0]
    query_command, query_request = calls[1]
    report_command, report_request = calls[2]

    assert capture_command == "capture"
    assert capture_request["namespace"] == "pt-acme"
    assert capture_request["metadata"]["artifact_type"] == "report-summary"
    assert capture_request["force_kind"] == "document"
    assert query_command == "query"
    assert query_request["namespace"] == "pt-acme"
    assert report_command == ["report", "consensus"]
    assert report_request["query_request"]["namespace"] == "pt-acme"


def test_main_forwards_force_kind_for_capture_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return 0

    monkeypatch.setattr(SECURITY_WORKFLOW, "_run_neurocore", fake_run_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW.Path, "resolve", lambda self: tmp_path)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    assert (
        SECURITY_WORKFLOW.main(
            [
                "capture-note",
                "--namespace",
                "pt-acme",
                "--force-kind",
                "record",
                "stable operator note",
            ]
        )
        == 0
    )

    assert calls[0][0] == "capture"
    assert calls[0][1]["force_kind"] == "record"


def test_main_capture_paper_resolves_default_bucket_from_active_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return 0

    monkeypatch.setattr(SECURITY_WORKFLOW, "_run_neurocore", fake_run_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(allowed_buckets=("research", "ops")),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "__file__",
        str(tmp_path / "scripts" / "security_workflow.py"),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    summary_file = tmp_path / "paper-summary.md"
    summary_file.write_text("# Summary\n\nReusable notes.", encoding="utf-8")

    assert (
        SECURITY_WORKFLOW.main(
            [
                "capture-paper",
                "--preset",
                "paper",
                "--title",
                "Practical SDR Notes",
                "--summary-file",
                str(summary_file),
            ]
        )
        == 0
    )

    assert calls[0][0] == "capture"
    assert calls[0][1]["bucket"] == "research"


def test_main_capture_note_rejects_bucket_not_allowed_by_active_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(allowed_buckets=("research", "ops")),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "__file__",
        str(tmp_path / "scripts" / "security_workflow.py"),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    with pytest.raises(SystemExit, match="Bucket 'reports' is not allowed"):
        SECURITY_WORKFLOW.main(
            [
                "capture-note",
                "--bucket",
                "reports",
                "paper-like note",
            ]
        )


def test_main_capture_paper_rejects_unmappable_default_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(allowed_buckets=("ops",)),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "__file__",
        str(tmp_path / "scripts" / "security_workflow.py"),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    summary_file = tmp_path / "paper-summary.md"
    summary_file.write_text("# Summary\n\nReusable notes.", encoding="utf-8")

    with pytest.raises(
        SystemExit,
        match="No default capture bucket is allowed by the active configuration",
    ):
        SECURITY_WORKFLOW.main(
            [
                "capture-paper",
                "--preset",
                "paper",
                "--title",
                "Practical SDR Notes",
                "--summary-file",
                str(summary_file),
            ]
        )


def test_main_query_paper_preset_filters_default_buckets_to_active_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return 0

    monkeypatch.setattr(SECURITY_WORKFLOW, "_run_neurocore", fake_run_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(allowed_buckets=("research", "ops")),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "__file__",
        str(tmp_path / "scripts" / "security_workflow.py"),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    assert SECURITY_WORKFLOW.main(["query", "--preset", "paper", "rf notes"]) == 0

    assert calls[0][0] == "query"
    assert calls[0][1]["allowed_buckets"] == ["research", "ops"]


def test_main_import_corpus_captures_shared_raw_document_with_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"]["raw_document_id"],
            "kind": "document",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    source = tmp_path / "sample-report.md"
    source.write_text("# Sample Report\n\nEvidence body.", encoding="utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "bug-bounty-report",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    command, request = calls[0]
    assert command == "capture"
    assert request["namespace"] == "shared-tradecraft"
    assert request["bucket"] == "reports"
    assert request["sensitivity"] == "standard"
    assert request["source_type"] == "bug_bounty_report"
    assert request["metadata"]["knowledge_space"] == "shared"
    assert request["metadata"]["source_kind"] == "bug-bounty-report"
    assert request["metadata"]["source_origin"] == "external-public"
    assert request["metadata"]["distillation_status"] == "skipped-no-provider"
    assert request["metadata"]["source_path"] == str(source.resolve())
    assert "space:shared" in request["tags"]
    assert "corpus:bug-bounty-report" in request["tags"]
    assert "artifact:raw-document" in request["tags"]
    assert "workflow:corpus-import" in request["tags"]
    assert "state:raw-captured" in request["tags"]
    payload = json.loads(stdout.getvalue())
    assert payload["distilled_count"] == 0


def test_normalize_corpus_tags_canonicalizes_and_backfills_required_families():
    tags = SECURITY_WORKFLOW._normalize_corpus_tags(
        [
            "Class:BOLA",
            "tech:Graph_QL",
            "auth:anon",
            "artifact:raw",
            "workflow:custom",
            "state:raw",
            "Needs Review",
        ],
        space="shared",
        source_kind="bug-bounty-report",
        artifact="raw-document",
        state="raw-captured",
    )

    assert "space:shared" in tags
    assert "corpus:bug-bounty-report" in tags
    assert "class:idor" in tags
    assert "tech:graphql" in tags
    assert "auth:anonymous" in tags
    assert "artifact:raw-document" in tags
    assert "workflow:corpus-import" in tags
    assert "state:raw-captured" in tags
    assert "needs-review" in tags


def test_parse_distillation_records_validates_source_kind_contract():
    payload = SECURITY_WORKFLOW._parse_distillation_records(
        json.dumps(
            {
                "records": [
                    {
                        "title": "Cross-tenant proof pattern",
                        "bucket": "findings",
                        "content": "Triager required invoice rendering across tenants.",
                        "tags": ["class:idor", "tech:graphql", "auth:user"],
                        "metadata": {
                            "record_kind": "accepted-proof-pattern",
                            "source_section": "proof-pattern",
                        },
                    }
                ]
            }
        ),
        source_kind="bug-bounty-report",
    )

    assert payload[0]["metadata"]["record_kind"] == "accepted-proof-pattern"


def test_parse_distillation_records_accepts_expanded_bug_bounty_record_kinds():
    payload = SECURITY_WORKFLOW._parse_distillation_records(
        json.dumps(
            {
                "records": [
                    {
                        "title": "Authorization heuristic",
                        "bucket": "findings",
                        "content": "Compare role boundaries before assuming tenant scoping.",
                        "tags": ["class:idor", "tech:graphql", "auth:user"],
                        "metadata": {
                            "record_kind": "authz-reasoning",
                            "source_section": "authz-reasoning",
                        },
                        "source_type": "auth_observation",
                    },
                    {
                        "title": "Workflow decision",
                        "bucket": "ops",
                        "content": "Validate duplicate-risk before drafting the report.",
                        "tags": ["class:idor", "tech:graphql", "auth:user"],
                        "metadata": {
                            "record_kind": "workflow-decision",
                            "source_section": "workflow-decisions",
                        },
                        "source_type": "workflow_note",
                    },
                ]
            }
        ),
        source_kind="bug-bounty-report",
    )

    assert payload[0]["metadata"]["record_kind"] == "authz-reasoning"
    assert payload[1]["metadata"]["record_kind"] == "workflow-decision"


def test_parse_distillation_records_accepts_expanded_htb_writeup_record_kinds():
    payload = SECURITY_WORKFLOW._parse_distillation_records(
        json.dumps(
            {
                "records": [
                    {
                        "title": "Credential path pattern",
                        "bucket": "findings",
                        "content": "Anonymous share -> default password -> WinRM foothold.",
                        "tags": ["class:unknown", "tech:smb", "auth:anonymous"],
                        "metadata": {
                            "record_kind": "credential-path-pattern",
                            "source_section": "credentials",
                        },
                        "source_type": "finding_note",
                    },
                    {
                        "title": "Workflow decision",
                        "bucket": "ops",
                        "content": "Bound the spray before expanding credentials.",
                        "tags": ["class:unknown", "tech:ldap", "auth:user"],
                        "metadata": {
                            "record_kind": "workflow-decision",
                            "source_section": "workflow-decisions",
                        },
                        "source_type": "workflow_note",
                    },
                ]
            }
        ),
        source_kind="htb-writeup",
    )

    assert payload[0]["metadata"]["record_kind"] == "credential-path-pattern"
    assert payload[1]["metadata"]["record_kind"] == "workflow-decision"


def test_parse_distillation_records_rejects_missing_contract_fields():
    with pytest.raises(ValueError, match="record_kind and source_section"):
        SECURITY_WORKFLOW._parse_distillation_records(
            json.dumps(
                {
                    "records": [
                        {
                            "title": "Broken record",
                            "bucket": "findings",
                            "content": "Missing metadata contract fields.",
                            "tags": ["class:idor", "tech:graphql", "auth:user"],
                            "metadata": {},
                        }
                    ]
                }
            ),
            source_kind="bug-bounty-report",
        )


def test_main_import_corpus_captures_distilled_records_when_provider_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"].get("raw_document_id", "rec-id"),
            "kind": "document" if request["bucket"] == "reports" else "record",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_build_corpus_distiller",
        lambda config: (
            lambda **kwargs: [
                {
                    "bucket": "findings",
                    "title": "Accepted proof pattern",
                    "content": "Triager required cross-tenant invoice rendering proof.",
                    "tags": ["class:idor", "tech:graphql", "auth:user"],
                    "metadata": {
                        "accepted": True,
                        "record_kind": "accepted-proof-pattern",
                        "source_section": "proof-pattern",
                    },
                    "source_type": "finding_note",
                },
                {
                    "bucket": "payloads",
                    "title": "Reusable payload",
                    "content": "GraphQL alias batching request.",
                    "tags": ["class:idor", "tech:graphql", "auth:user"],
                    "metadata": {
                        "kind": "payload",
                        "record_kind": "payload-variant",
                        "source_section": "payloads",
                    },
                    "source_type": "payload_note",
                },
            ]
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(
            enable_background_summarization=True,
            enable_multi_model_consensus=True,
            consensus_provider="openai_compatible",
            consensus_base_url="https://example.invalid/v1",
            consensus_api_key="test-key",
            consensus_model_names=("model-a", "model-b"),
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    source = tmp_path / "bug-bounty.md"
    source.write_text("# Bug bounty\n\nCross-tenant proof details.", encoding="utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "bug-bounty-report",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 3
    raw_request = calls[0][1]
    finding_request = calls[1][1]
    payload_request = calls[2][1]
    assert raw_request["metadata"]["distillation_status"] == "completed"
    assert finding_request["bucket"] == "findings"
    assert payload_request["bucket"] == "payloads"
    assert (
        finding_request["metadata"]["raw_document_id"]
        == raw_request["metadata"]["raw_document_id"]
    )
    assert (
        payload_request["metadata"]["raw_document_id"]
        == raw_request["metadata"]["raw_document_id"]
    )
    assert "space:shared" in finding_request["tags"]
    assert "artifact:distilled-record" in finding_request["tags"]
    payload = json.loads(stdout.getvalue())
    assert payload["distilled_count"] == 2


def test_main_import_corpus_threads_hierarchical_summary_into_raw_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"].get("raw_document_id", "rec-id"),
            "kind": "document" if request["bucket"] == "reports" else "record",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_build_corpus_distiller",
        lambda config: (
            lambda **kwargs: [
                {
                    "bucket": "ops",
                    "title": "Checklist",
                    "content": "Validate LDAP collection prerequisites before running tooling.",
                    "tags": ["class:enumeration", "tech:ldap", "auth:user"],
                    "metadata": {
                        "record_kind": "checklist",
                        "source_section": "checklists",
                    },
                    "source_type": "ops_note",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_build_corpus_hierarchical_summary",
        lambda **kwargs: {
            "document_summary": "Combined reusable summary.",
            "chunk_summaries": [
                {"ordinal": 1, "summary": "chunk-1 summary"},
                {"ordinal": 2, "summary": "chunk-2 summary"},
            ],
            "strategy": "hierarchical_chunk_synthesis_v1",
        },
        raising=False,
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(
            enable_multi_model_consensus=True,
            consensus_provider="openai_compatible",
            consensus_base_url="https://example.invalid/v1",
            consensus_api_key="test-key",
            consensus_model_names=("model-a", "model-b"),
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    source = tmp_path / "tradecraft.md"
    source.write_text(
        (
            "# Tradecraft\n\n"
            "Reusable LDAP methodology for red teams, including collection scope, "
            "detection-aware sequencing, operator note-taking, checklist design, "
            "and portable validation steps that can be reused across different "
            "engagements without customer-specific context."
        ),
        encoding="utf-8",
    )

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "article",
            str(source),
        ]
    )

    assert exit_code == 0
    raw_request = calls[0][1]
    assert raw_request["summary"] == "Combined reusable summary."
    assert raw_request["chunk_summaries"] == [
        {"ordinal": 1, "summary": "chunk-1 summary"},
        {"ordinal": 2, "summary": "chunk-2 summary"},
    ]
    assert raw_request["metadata"]["summary_strategy"] == (
        "hierarchical_chunk_synthesis_v1"
    )


def test_main_import_corpus_skips_hierarchical_summary_when_summary_gate_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"].get("raw_document_id", "rec-id"),
            "kind": "document" if request["bucket"] == "reports" else "record",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(
            enable_background_summarization=False,
            enable_multi_model_consensus=False,
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )

    source = tmp_path / "tradecraft.md"
    source.write_text(
        (
            "# Tradecraft\n\n"
            "Reusable LDAP methodology for red teams, including collection scope, "
            "detection-aware sequencing, operator note-taking, checklist design, "
            "and portable validation steps that can be reused across different "
            "engagements without customer-specific context."
        ),
        encoding="utf-8",
    )

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "article",
            str(source),
        ]
    )

    assert exit_code == 0
    raw_request = calls[0][1]
    assert "summary" not in raw_request
    assert "chunk_summaries" not in raw_request
    assert "summary_strategy" not in raw_request["metadata"]


def test_main_import_corpus_rejects_shared_sealed_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "sample-report.md"
    source.write_text("# Sample Report\n\nEvidence body.", encoding="utf-8")

    with pytest.raises(
        SystemExit, match="sealed corpus imports must use --space engagement"
    ):
        SECURITY_WORKFLOW.main(
            [
                "import-corpus",
                "--space",
                "shared",
                "--source-kind",
                "bug-bounty-report",
                "--sensitivity",
                "sealed",
                str(source),
            ]
        )


def test_main_import_corpus_blocks_low_fit_shared_article_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_call_neurocore",
        lambda repo_root, env, command, request: calls.append((command, request)),
    )
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "target-specific.md"
    source.write_text(
        (
            "# Acme notes\n\n"
            "Client-specific admin portal workflow for the Acme engagement. "
            "Use the customer VPN, sign in with the provided credentials, and "
            "validate whether invoice 4412 can be rendered across tenants in "
            "the internal dashboard."
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "article",
            str(source),
        ]
    )

    assert exit_code == 1
    assert calls == []
    payload = json.loads(stdout.getvalue())
    assert payload["stored"] is False
    assert payload["blocked"] is True
    assert payload["evaluation"]["decision"] == "blocked"


def test_main_import_corpus_allows_override_for_low_fit_shared_article(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {"id": request["metadata"]["raw_document_id"], "kind": "document"}

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "target-specific.md"
    source.write_text(
        (
            "# Acme notes\n\n"
            "Client-specific admin portal workflow for the Acme engagement. "
            "Use the customer VPN, sign in with the provided credentials, and "
            "validate whether invoice 4412 can be rendered across tenants in "
            "the internal dashboard."
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "article",
            "--allow-low-fit-import",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    request = calls[0][1]
    assert request["metadata"]["pre_ingest_evaluation"]["decision"] == (
        "accepted-with-override"
    )
    payload = json.loads(stdout.getvalue())
    assert payload["evaluation"]["decision"] == "accepted-with-override"
    assert payload["evaluation"]["override_applied"] is True


def test_main_import_corpus_fetches_url_and_marks_provider_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    class DummyResponse:
        status = 200
        headers = {"Content-Type": "text/markdown; charset=utf-8"}

        def read(self):
            return (
                b"# Active Directory Enumeration\n\n"
                b"This article explains reusable Active Directory enumeration "
                b"methodology for red teams. It focuses on LDAP collection, SMB "
                b"service discovery, BloodHound collection constraints, detection "
                b"considerations, and how to turn findings into repeatable "
                b"operator checklists without target-specific evidence."
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {"id": request["metadata"]["raw_document_id"], "kind": "document"}

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW.urllib_request,
        "urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "article",
            "--url",
            "https://example.invalid/articles/heuristics.md",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    request = calls[0][1]
    assert request["content_format"] == "markdown"
    assert (
        request["metadata"]["source_url"]
        == "https://example.invalid/articles/heuristics.md"
    )
    assert request["metadata"]["distillation_status"] == "skipped-no-provider"
    assert request["metadata"]["pre_ingest_evaluation"]["decision"] == "accepted"
    payload = json.loads(stdout.getvalue())
    assert payload["evaluation"]["decision"] == "accepted"
    assert payload["source"]["kind"] == "url"


def test_main_import_corpus_sanitizes_html_article_path_before_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {"id": request["metadata"]["raw_document_id"], "kind": "document"}

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "tradecraft.html"
    source.write_text(
        (
            "<html><body>"
            "<nav>Menu Subscribe Sign in Privacy Policy</nav>"
            "<article>"
            "<h1>Reusable LDAP Enumeration</h1>"
            "<p>This article explains reusable LDAP reconnaissance patterns, "
            "detection-aware collection, and operator checklists.</p>"
            "<h2>Workflow</h2>"
            "<p>Query narrow scopes first, then expand.</p>"
            "<pre>Get-DomainUser -LDAPFilter '(samAccountType=805306368)'</pre>"
            "</article>"
            "<footer>Cookie Settings Careers Contact</footer>"
            "</body></html>"
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "engagement",
            "--source-kind",
            "article",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    request = calls[0][1]
    assert request["content_format"] == "markdown"
    assert request["title"] == "Reusable LDAP Enumeration"
    assert "<nav>" not in request["content"]
    assert "Menu Subscribe Sign in Privacy Policy" not in request["content"]
    assert "# Reusable LDAP Enumeration" in request["content"]
    assert "## Workflow" in request["content"]
    assert "```text" in request["content"]
    assert request["metadata"]["source_content_format"] == "html"
    assert request["metadata"]["sanitized_from_html"] is True


def test_main_import_corpus_prefers_article_fragment_and_h1_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {"id": request["metadata"]["raw_document_id"], "kind": "document"}

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "tradecraft.html"
    source.write_text(
        (
            "<html><head><title>Directory Ops | MDSec</title></head><body>"
            "<div>Marketing banner and related posts</div>"
            "<article>"
            "<h1>Directory Operations for Red Teams</h1>"
            "<p>Reusable LDAP methodology and operator notes.</p>"
            "</article>"
            "<div>Newsletter signup and more links</div>"
            "</body></html>"
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "engagement",
            "--source-kind",
            "article",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    request = calls[0][1]
    assert request["title"] == "Directory Operations for Red Teams"
    assert request["content"].startswith("# Directory Operations for Red Teams")
    assert "Marketing banner and related posts" not in request["content"]
    assert "Newsletter signup and more links" not in request["content"]
    assert "Reusable LDAP methodology and operator notes." in request["content"]


def test_main_import_corpus_sanitizes_html_article_url_before_shared_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    class DummyResponse:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self):
            return (
                b"<html><body>"
                b"<nav>Menu Subscribe Sign in Privacy Policy</nav>"
                b"<article>"
                b"<h1>Active Directory Enumeration for Red Teams</h1>"
                b"<p>This article explains reusable LDAP enumeration "
                b"methodology for red teams, including detection-aware "
                b"collection and operator checklists.</p>"
                b"</article>"
                b"<footer>Cookie Settings Careers Contact</footer>"
                b"</body></html>"
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {"id": request["metadata"]["raw_document_id"], "kind": "document"}

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW.urllib_request,
        "urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "article",
            "--url",
            "https://example.invalid/articles/ad-enumeration",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    request = calls[0][1]
    assert request["content_format"] == "markdown"
    assert request["metadata"]["source_content_format"] == "html"
    assert request["metadata"]["sanitized_from_html"] is True
    assert "Menu Subscribe Sign in Privacy Policy" not in request["content"]
    assert "Active Directory Enumeration for Red Teams" in request["content"]
    assert request["metadata"]["pre_ingest_evaluation"]["local"]["text_source"] == "raw"
    payload = json.loads(stdout.getvalue())
    assert payload["evaluation"]["decision"] == "accepted"


def test_main_import_corpus_provider_block_prevents_shared_article_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_call_neurocore",
        lambda repo_root, env, command, request: calls.append((command, request)),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_evaluate_shared_article_via_provider",
        lambda **kwargs: {
            "decision": "blocked",
            "summary": "Provider judged the article too engagement-specific.",
            "provider_name": "openai",
            "model_name": "gpt-5-mini",
        },
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(
            enable_multi_model_consensus=True,
            reporting_provider_configs={
                "openai": SECURITY_WORKFLOW.ReportingProviderConfig(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="test-key",
                    model_names=("gpt-5-mini", "gpt-5"),
                )
            },
            reporting_primary_provider="openai",
            reporting_fallback_provider=None,
            reporting_strategy="single_provider",
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "tradecraft.md"
    source.write_text(
        (
            "# Tradecraft article\n\n"
            "Reusable enumeration guidance for LDAP, SMB, Kerberos, operator "
            "checklists, detection heuristics, and portable review steps that "
            "can be applied across environments without client-specific context."
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "article",
            str(source),
        ]
    )

    assert exit_code == 1
    assert calls == []
    payload = json.loads(stdout.getvalue())
    assert payload["blocked"] is True
    assert payload["evaluation"]["provider"]["used"] is True
    assert payload["evaluation"]["decision"] == "blocked"


def test_main_import_corpus_falls_back_to_raw_only_on_invalid_distillation_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"]["raw_document_id"],
            "kind": "document",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_build_corpus_distiller",
        lambda config: (
            lambda **kwargs: SECURITY_WORKFLOW._parse_distillation_records(
                json.dumps(
                    {
                        "records": [
                            {
                                "title": "Malformed record",
                                "bucket": "findings",
                                "content": "Missing contract metadata",
                                "tags": ["class:idor", "tech:graphql", "auth:user"],
                                "metadata": {},
                            }
                        ]
                    }
                ),
                source_kind="bug-bounty-report",
            )
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(
            enable_multi_model_consensus=True,
            consensus_provider="openai_compatible",
            consensus_base_url="https://example.invalid/v1",
            consensus_api_key="test-key",
            consensus_model_names=("model-a",),
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "invalid-provider.md"
    source.write_text(
        "# Sample\n\nProvider emits malformed JSON shape.", encoding="utf-8"
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "bug-bounty-report",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][1]["metadata"]["distillation_status"] == "skipped-provider-error"
    payload = json.loads(stdout.getvalue())
    assert payload["distilled_count"] == 0


def test_evaluate_shared_article_pre_ingest_uses_provider_and_can_block(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = SECURITY_WORKFLOW.ReportingProviderConfig(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model_names=(
            "gpt-5-mini",
            "gpt-5",
        ),
    )
    config = SimpleNamespace(
        enable_multi_model_consensus=True,
        reporting_provider_configs={"openai": provider},
        reporting_primary_provider="openai",
        reporting_fallback_provider=None,
        reporting_strategy="single_provider",
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_evaluate_shared_article_via_provider",
        lambda **kwargs: {
            "decision": "blocked",
            "summary": "Target-specific, low-transferability article.",
            "provider_name": "openai",
            "model_name": "gpt-5-mini",
        },
    )

    evaluation = SECURITY_WORKFLOW._evaluate_shared_article_pre_ingest(
        source={
            "content": (
                "# Tradecraft article\n\n"
                "Reusable enumeration guidance for LDAP, SMB, and Kerberos. "
                "The workflow explains how to collect directory data, validate "
                "service exposure, capture operator notes, and turn results into "
                "shared checklists without target-specific dependencies."
            ),
            "content_format": "markdown",
            "kind": "path",
            "title": "Tradecraft article",
        },
        config=config,
    )

    assert evaluation["decision"] == "blocked"
    assert evaluation["provider"]["used"] is True
    assert evaluation["provider"]["provider_name"] == "openai"


def test_evaluate_shared_article_pre_ingest_degrades_to_local_only_on_provider_error():
    provider = SECURITY_WORKFLOW.ReportingProviderConfig(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model_names=("gpt-5-mini", "gpt-5"),
    )
    config = SimpleNamespace(
        enable_multi_model_consensus=True,
        reporting_provider_configs={"openai": provider},
        reporting_primary_provider="openai",
        reporting_fallback_provider=None,
        reporting_strategy="single_provider",
    )

    def raise_provider_error(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_evaluate_shared_article_via_provider",
        raise_provider_error,
    )
    try:
        evaluation = SECURITY_WORKFLOW._evaluate_shared_article_pre_ingest(
            source={
                "content": (
                    "# Tradecraft article\n\n"
                    "Reusable enumeration guidance for LDAP, SMB, Kerberos, "
                    "detection heuristics, operator checklists, triage steps, "
                    "and reusable reporting notes that another operator can "
                    "apply in a different environment without engagement-specific context."
                ),
                "content_format": "markdown",
                "kind": "path",
                "title": "Tradecraft article",
            },
            config=config,
        )
    finally:
        monkeypatch.undo()

    assert evaluation["decision"] == "accepted"
    assert "Provider evaluation failed" in evaluation["warnings"][0]
    assert evaluation["provider"]["attempted"] is True
    assert evaluation["provider"]["used"] is False


def test_evaluate_shared_article_pre_ingest_blocks_navigation_heavy_html():
    evaluation = SECURITY_WORKFLOW._evaluate_shared_article_pre_ingest(
        source={
            "content": (
                "<html><body>"
                "<nav>Menu Subscribe Sign in Privacy Policy Cookie Settings</nav>"
                "<div>Menu Subscribe Sign in Privacy Policy Cookie Settings</div>"
                "<footer>Menu Subscribe Sign in Privacy Policy Cookie Settings</footer>"
                "</body></html>"
            ),
            "content_format": "html",
            "kind": "url",
            "title": "Navigation shell",
        },
        config=None,
    )

    assert evaluation["decision"] == "blocked"
    assert evaluation["local"]["text_source"] == "html-stripped"
    assert "mostly-markup-or-navigation" in evaluation["local"]["signals"]


def test_evaluate_shared_article_pre_ingest_skips_local_mock_provider(
    monkeypatch: pytest.MonkeyPatch,
):
    config = SimpleNamespace(
        enable_multi_model_consensus=True,
        consensus_provider="openai_compatible",
        consensus_base_url=SECURITY_WORKFLOW.LOCAL_CONSENSUS_BASE_URL,
        consensus_api_key="local-dev-key",
        consensus_model_names=("model-a", "model-b"),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_evaluate_shared_article_via_provider",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("local mock should not be used for article gating")
        ),
    )

    evaluation = SECURITY_WORKFLOW._evaluate_shared_article_pre_ingest(
        source={
            "content": (
                "# Tradecraft article\n\n"
                "Reusable guidance for enumeration, validation, operator notes, "
                "portable checklists, detection heuristics, and repeatable review "
                "steps that another operator can apply across engagements without "
                "client-specific context."
            ),
            "content_format": "markdown",
            "kind": "path",
            "title": "Tradecraft article",
        },
        config=config,
    )

    assert evaluation["decision"] == "accepted"
    assert evaluation["provider"]["attempted"] is False
    assert evaluation["provider"]["used"] is False


def test_main_import_corpus_threads_source_evaluation_status_to_distilled_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"].get("raw_document_id", "rec-id"),
            "kind": "document" if request["bucket"] == "reports" else "record",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_build_corpus_distiller",
        lambda config: (
            lambda **kwargs: [
                {
                    "bucket": "ops",
                    "title": "Checklist",
                    "content": "Validate LDAP collection prerequisites before running tooling.",
                    "tags": ["class:enumeration", "tech:ldap", "auth:user"],
                    "metadata": {
                        "record_kind": "checklist",
                        "source_section": "checklists",
                    },
                    "source_type": "ops_note",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_safe_load_config",
        lambda env: SimpleNamespace(
            enable_multi_model_consensus=True,
            reporting_provider_configs={
                "openai": SECURITY_WORKFLOW.ReportingProviderConfig(
                    name="openai",
                    base_url="https://api.openai.com/v1",
                    api_key="test-key",
                    model_names=("gpt-5-mini", "gpt-5"),
                )
            },
            reporting_primary_provider="openai",
            reporting_fallback_provider=None,
            reporting_strategy="single_provider",
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_evaluate_shared_article_via_provider",
        lambda **kwargs: {
            "decision": "accepted",
            "summary": "Reusable shared tradecraft.",
            "provider_name": "openai",
            "model_name": "gpt-5-mini",
        },
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "tradecraft.md"
    source.write_text(
        (
            "# Tradecraft article\n\n"
            "Reusable enumeration guidance for LDAP, SMB, Kerberos, detection "
            "heuristics, and operator checklists that can be applied across environments."
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "article",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 2
    distilled_request = calls[1][1]
    assert distilled_request["metadata"]["source_evaluation_status"] == "accepted"


def test_main_import_corpus_reports_raw_only_as_non_ready_shared_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"]["raw_document_id"],
            "kind": "document",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "raw-only.md"
    source.write_text("# Shared\n\nRaw capture only.", encoding="utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "htb-writeup",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    payload = json.loads(stdout.getvalue())
    assert payload["distilled_count"] == 0
    assert payload["distillation_status"] == "skipped-no-provider"
    assert payload["shared_corpus_ready"] is False


def test_main_import_corpus_only_gates_shared_articles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"]["raw_document_id"],
            "kind": "document",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_evaluate_shared_article_pre_ingest",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("shared article gate should be bypassed")
        ),
    )
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "source.md"
    source.write_text(
        "# Reusable content\n\nDetailed notes about enumeration and reporting.",
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    cases = [
        ["import-corpus", "--space", "shared", "--source-kind", "bug-bounty-report"],
        ["import-corpus", "--space", "shared", "--source-kind", "htb-writeup"],
        ["import-corpus", "--space", "shared", "--source-kind", "book-note"],
        ["import-corpus", "--space", "engagement", "--source-kind", "article"],
    ]
    for argv in cases:
        exit_code = SECURITY_WORKFLOW.main([*argv, str(source)])
        assert exit_code == 0

    assert len(calls) == len(cases)


def test_main_import_corpus_leaves_non_article_html_imports_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"]["raw_document_id"],
            "kind": "document",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "writeup.html"
    source.write_text(
        "<html><body><h1>HTB Walkthrough</h1><p>Foothold notes.</p></body></html>",
        encoding="utf-8",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "shared",
            "--source-kind",
            "htb-writeup",
            str(source),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    request = calls[0][1]
    assert request["content_format"] == "html"
    assert "<html>" in request["content"]
    assert "sanitized_from_html" not in request["metadata"]


def test_main_import_corpus_threads_explicit_engagement_sealed_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_call_neurocore(repo_root, env, command, request):
        calls.append((command, request))
        return {
            "id": request["metadata"]["raw_document_id"],
            "kind": "document",
            "namespace": request["namespace"],
            "bucket": request["bucket"],
        }

    monkeypatch.setattr(SECURITY_WORKFLOW, "_call_neurocore", fake_call_neurocore)
    monkeypatch.setattr(SECURITY_WORKFLOW, "_safe_load_config", lambda env: None)
    monkeypatch.setattr(
        SECURITY_WORKFLOW,
        "_maybe_reexec_into_repo_runtime",
        lambda argv, repo_root: None,
    )
    source = tmp_path / "evidence.md"
    source.write_text("# Evidence\n\nSensitive proof.", encoding="utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(SECURITY_WORKFLOW.sys, "stdout", stdout)

    exit_code = SECURITY_WORKFLOW.main(
        [
            "import-corpus",
            "--space",
            "engagement",
            "--source-kind",
            "article",
            "--sensitivity",
            "sealed",
            str(source),
        ]
    )

    assert exit_code == 0
    assert calls[0][1]["sensitivity"] == "sealed"


def test_print_readiness_summary_reports_missing_report_prereqs(tmp_path: Path):
    stdout = io.StringIO()
    env = {
        "NEUROCORE_DEFAULT_NAMESPACE": "security-lab",
        "NEUROCORE_ALLOWED_BUCKETS": ",".join(SECURITY_WORKFLOW.SECURITY_BUCKETS),
        "NEUROCORE_DEFAULT_SENSITIVITY": "restricted",
        "NEUROCORE_SEMANTIC_BACKEND": "none",
    }

    SECURITY_WORKFLOW.print_readiness_summary(
        repo_root=tmp_path, env=env, stdout=stdout
    )

    text = stdout.getvalue()
    assert "semantic=ready" in text
    assert "query=ready" in text
    assert "report=not ready" in text
    assert "Report prerequisites still missing" in text
