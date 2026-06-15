import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import time


def _load_module(script_name: str, module_name: str):
    module_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    spec.loader.exec_module(module)
    return module


NEUROCORE_CHECKOUT = _load_module("neurocore_checkout.py", "neurocore_checkout_module")
VALIDATE_CHECKOUT = _load_module("validate_checkout.py", "validate_checkout_module")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _write_operator_env(
    operator_home: Path,
    *,
    enable_http_adapter: bool = True,
    storage_backend: str = "sqlite",
) -> None:
    operator_home.mkdir(parents=True, exist_ok=True)
    (operator_home / ".env").write_text(
        "\n".join(
            [
                f"NEUROCORE_OPERATOR_HOME={operator_home}",
                "NEUROCORE_DEFAULT_NAMESPACE=project-alpha",
                "NEUROCORE_ALLOWED_BUCKETS=research,ops",
                "NEUROCORE_DEFAULT_SENSITIVITY=standard",
                f"NEUROCORE_STORAGE_BACKEND={storage_backend}",
                f"NEUROCORE_PRIMARY_STORE_PATH={operator_home / 'data' / 'neurocore.db'}",
                f"NEUROCORE_SEALED_STORE_PATH={operator_home / 'data' / 'neurocore-sealed.db'}",
                f"NEUROCORE_ENABLE_HTTP_ADAPTER={'true' if enable_http_adapter else 'false'}",
                "NEUROCORE_ENABLE_MCP_ADAPTER=false",
                "NEUROCORE_ENABLE_DASHBOARD=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_neurocore_checkout_prefers_repo_virtualenv(tmp_path, monkeypatch):
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    captured = {}

    def fake_run(command, cwd, env):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(NEUROCORE_CHECKOUT.subprocess, "run", fake_run)

    exit_code = NEUROCORE_CHECKOUT.main(["query", "--request-json", "{}"])

    assert exit_code == 0
    assert captured["command"][:3] == [
        str(venv_python),
        "-m",
        "neurocore.adapters.cli",
    ]
    assert captured["cwd"] == tmp_path


def test_neurocore_checkout_falls_back_to_current_python_with_src_path(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=security-lab\n", encoding="utf-8"
    )
    monkeypatch.delenv("NEUROCORE_OPERATOR_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    captured = {}

    def fake_run(command, cwd, env):
        captured["command"] = command
        captured["env"] = env
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(NEUROCORE_CHECKOUT.subprocess, "run", fake_run)

    exit_code = NEUROCORE_CHECKOUT.main(["capture", "--request-json", "{}"])

    assert exit_code == 0
    assert captured["command"][0] == str(
        Path(NEUROCORE_CHECKOUT.sys.executable).absolute()
    )
    assert captured["env"]["PYTHONPATH"] == str(tmp_path / "src")
    assert captured["env"]["NEUROCORE_DEFAULT_NAMESPACE"] == "security-lab"
    assert "repo-local operator state is deprecated" in capsys.readouterr().err


def test_neurocore_checkout_loads_operator_home_env_by_default(tmp_path, monkeypatch):
    operator_home = tmp_path / ".operator-state"
    operator_home.mkdir()
    (operator_home / ".env").write_text(
        "\n".join(
            [
                f"NEUROCORE_OPERATOR_HOME={operator_home}",
                "NEUROCORE_DEFAULT_NAMESPACE=security-lab",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    captured = {}

    def fake_run(command, cwd, env):
        captured["command"] = command
        captured["env"] = env
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(NEUROCORE_CHECKOUT.subprocess, "run", fake_run)

    exit_code = NEUROCORE_CHECKOUT.main(["capture", "--request-json", "{}"])

    assert exit_code == 0
    assert captured["env"]["NEUROCORE_OPERATOR_HOME"] == str(operator_home)
    assert captured["env"]["NEUROCORE_DEFAULT_NAMESPACE"] == "security-lab"


def test_validate_checkout_prefers_override_and_calls_governance_module(
    tmp_path, monkeypatch
):
    override = tmp_path / "custom-python"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("", encoding="utf-8")
    monkeypatch.setattr(VALIDATE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(VALIDATE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    monkeypatch.setenv("NEUROCORE_PYTHON_EXECUTABLE", str(override))
    captured = {}

    def fake_run(command, cwd, env):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(VALIDATE_CHECKOUT.subprocess, "run", fake_run)

    exit_code = VALIDATE_CHECKOUT.main([])

    assert exit_code == 0
    assert captured["command"] == [
        str(override),
        "-m",
        "neurocore.governance.validation",
    ]
    assert captured["cwd"] == tmp_path
    assert captured["env"]["PYTHONPATH"] == str(tmp_path / "src")


def test_validate_checkout_loads_operator_home_env_by_default(tmp_path, monkeypatch):
    operator_home = tmp_path / ".operator-state"
    operator_home.mkdir()
    (operator_home / ".env").write_text(
        "\n".join(
            [
                f"NEUROCORE_OPERATOR_HOME={operator_home}",
                "NEUROCORE_DEFAULT_NAMESPACE=security-lab",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(VALIDATE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(VALIDATE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    captured = {}

    def fake_run(command, cwd, env):
        captured["command"] = command
        captured["env"] = env
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(VALIDATE_CHECKOUT.subprocess, "run", fake_run)

    exit_code = VALIDATE_CHECKOUT.main([])

    assert exit_code == 0
    assert captured["env"]["NEUROCORE_OPERATOR_HOME"] == str(operator_home)
    assert captured["env"]["NEUROCORE_DEFAULT_NAMESPACE"] == "security-lab"


def test_neurocore_checkout_service_http_start_launches_background_process_and_saves_state(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    captured = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(command, cwd, env, stdin, stdout, stderr, start_new_session, text):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env
        captured["start_new_session"] = start_new_session
        captured["text"] = text
        return FakeProcess()

    monkeypatch.setattr(NEUROCORE_CHECKOUT.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_wait_for_managed_http_readiness",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_port_is_available", lambda host, port: True)

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "start"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    state_path = operator_home / "data" / "managed-http-service.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["action"] == "start"
    assert payload["started"] is True
    assert captured["command"][1:5] == ["-m", "neurocore.adapters.cli", "serve", "http"]
    assert captured["cwd"] == tmp_path
    assert captured["start_new_session"] is True
    assert state["pid"] == 4321
    assert state["host"] == "127.0.0.1"
    assert state["port"] == 8000
    assert state["log_path"].endswith("managed-http-service.log")


def test_neurocore_checkout_service_http_status_marks_stale_code_and_storage_degradation(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home, storage_backend="mirror")
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    source_file = tmp_path / "src" / "neurocore" / "runtime.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("print('fresh')\n", encoding="utf-8")
    started_at = "2000-01-01T00:00:00+00:00"
    state_path = operator_home / "data" / "managed-http-service.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "pid": 1234,
                "host": "127.0.0.1",
                "port": 8000,
                "started_at": started_at,
                "log_path": str(operator_home / "data" / "managed-http-service.log"),
                "repo_root": str(tmp_path),
                "env_path": str(operator_home / ".env"),
                "argv": ["python", "-m", "neurocore.adapters.cli", "serve", "http"],
            }
        ),
        encoding="utf-8",
    )
    (operator_home / "data" / "mirror-status.json").write_text(
        json.dumps(
            {
                "local_degraded": True,
                "reconciliation_pending": True,
                "last_sync_status": "abandoned",
                "last_local_error": "database is locked",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_is_managed_http_service_pid", lambda pid, snapshot: True)
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_build_http_health_payload",
        lambda **kwargs: {
            "base_url": "http://127.0.0.1:8000",
            "semantic_ready": True,
            "openapi": {"ok": True, "status": 200},
            "query": {"ok": True, "status": 200},
            "capture": {"ok": True, "status": 400},
            "brains_get": {"ok": True, "status": 404},
        },
    )

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "status"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["process"]["running"] is True
    assert payload["process"]["stale_code"] is True
    assert payload["storage"]["degraded"] is True
    assert payload["healthy"] is False


def test_neurocore_checkout_service_http_status_marks_cloud_only_degradation(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home, storage_backend="mirror")
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    state_path = operator_home / "data" / "managed-http-service.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "pid": 1234,
                "host": "127.0.0.1",
                "port": 8000,
                "started_at": "2026-01-01T00:00:00+00:00",
                "log_path": str(operator_home / "data" / "managed-http-service.log"),
                "repo_root": str(tmp_path),
                "env_path": str(operator_home / ".env"),
                "argv": ["python", "-m", "neurocore.adapters.cli", "serve", "http"],
            }
        ),
        encoding="utf-8",
    )
    (operator_home / "data" / "mirror-status.json").write_text(
        json.dumps(
            {
                "cloud_degraded": True,
                "last_cloud_error": "supabase unavailable",
                "last_sync_status": "success",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_is_managed_http_service_pid",
        lambda pid, snapshot: True,
    )
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_build_http_health_payload",
        lambda **kwargs: {
            "base_url": "http://127.0.0.1:8000",
            "semantic_ready": True,
            "openapi": {"ok": True, "status": 200},
            "query": {"ok": True, "status": 200},
            "capture": {"ok": True, "status": 400},
            "brains_get": {"ok": True, "status": 404},
        },
    )

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "status"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["storage"]["degraded"] is True
    assert payload["storage"]["cloud_degraded"] is True
    assert payload["storage"]["last_cloud_error"] == "supabase unavailable"
    assert payload["healthy"] is False


def test_neurocore_checkout_service_http_status_reports_unmanaged_listener_conflict(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_port_is_available", lambda host, port: False)
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_build_http_health_payload",
        lambda **kwargs: {
            "base_url": "http://127.0.0.1:8000",
            "semantic_ready": False,
            "openapi": {"ok": True, "status": 200},
            "query": {"ok": True, "status": 200},
            "capture": {"ok": False, "status": 500},
            "brains_get": {"ok": False, "status": 500},
        },
    )

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "status"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["process"]["state"] == "conflict"
    assert payload["process"]["managed"] is False
    assert payload["process"]["unmanaged_listener"] is True
    assert payload["http"]["openapi"]["status"] == 200
    assert payload["healthy"] is False


def test_neurocore_checkout_service_http_stop_refuses_unrelated_pid(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    state_path = operator_home / "data" / "managed-http-service.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "pid": 9999,
                "host": "127.0.0.1",
                "port": 8000,
                "started_at": "2026-06-14T10:00:00+00:00",
                "log_path": str(operator_home / "data" / "managed-http-service.log"),
                "repo_root": str(tmp_path),
                "env_path": str(operator_home / ".env"),
                "argv": ["python", "-m", "neurocore.adapters.cli", "serve", "http"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_is_managed_http_service_pid", lambda pid, snapshot: False)
    monkeypatch.setattr(NEUROCORE_CHECKOUT.os, "kill", lambda pid, sig: (_ for _ in ()).throw(AssertionError("kill should not be called")))

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "stop"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["action"] == "stop"
    assert payload["stopped"] is False
    assert payload["stale_state_cleared"] is True
    assert state_path.exists() is False


def test_is_managed_http_service_pid_accepts_ps_fallback_when_proc_unavailable(
    monkeypatch,
):
    snapshot = {"host": "127.0.0.1", "port": 8000}

    monkeypatch.setattr(NEUROCORE_CHECKOUT, "pid_is_active", lambda pid: True)
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT.Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(OSError("missing /proc")),
    )

    def fake_run(command, capture_output, text, check):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "python -m neurocore.adapters.cli serve http "
                "--host 127.0.0.1 --port 8000\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(NEUROCORE_CHECKOUT.subprocess, "run", fake_run)

    assert NEUROCORE_CHECKOUT._is_managed_http_service_pid(4321, snapshot) is True


def test_neurocore_checkout_service_http_status_uses_readiness_fallback_when_pid_args_unavailable(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    state_path = operator_home / "data" / "managed-http-service.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "pid": 4321,
                "host": "127.0.0.1",
                "port": 8000,
                "started_at": "2026-06-14T10:00:00+00:00",
                "log_path": str(operator_home / "data" / "managed-http-service.log"),
                "repo_root": str(tmp_path),
                "env_path": str(operator_home / ".env"),
                "argv": ["python", "-m", "neurocore.adapters.cli", "serve", "http"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "pid_is_active", lambda pid: True)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_read_pid_cmdline", lambda pid: None)
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_build_http_health_payload",
        lambda **kwargs: {
            "base_url": "http://127.0.0.1:8000",
            "semantic_ready": True,
            "openapi": {"ok": True, "status": 200},
            "query": {"ok": True, "status": 200},
            "capture": {"ok": True, "status": 400},
            "brains_get": {"ok": True, "status": 404},
        },
    )

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "status"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["process"]["running"] is True
    assert payload["process"]["managed"] is True
    assert payload["process"]["state"] == "running"


def test_neurocore_checkout_service_http_stop_uses_readiness_fallback_when_pid_args_unavailable(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    state_path = operator_home / "data" / "managed-http-service.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "pid": 4321,
                "host": "127.0.0.1",
                "port": 8000,
                "started_at": "2026-06-14T10:00:00+00:00",
                "log_path": str(operator_home / "data" / "managed-http-service.log"),
                "repo_root": str(tmp_path),
                "env_path": str(operator_home / ".env"),
                "argv": ["python", "-m", "neurocore.adapters.cli", "serve", "http"],
            }
        ),
        encoding="utf-8",
    )
    terminated: list[int] = []
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "pid_is_active", lambda pid: True)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_read_pid_cmdline", lambda pid: None)
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_build_http_health_payload",
        lambda **kwargs: {
            "base_url": "http://127.0.0.1:8000",
            "semantic_ready": True,
            "openapi": {"ok": True, "status": 200},
            "query": {"ok": True, "status": 200},
            "capture": {"ok": True, "status": 400},
            "brains_get": {"ok": True, "status": 404},
        },
    )
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_terminate_pid",
        lambda pid: terminated.append(pid),
    )

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "stop"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["stopped"] is True
    assert terminated == [4321]
    assert state_path.exists() is False


def test_neurocore_checkout_service_http_stop_does_not_kill_when_pid_is_unverifiable(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    state_path = operator_home / "data" / "managed-http-service.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "pid": 4321,
                "host": "127.0.0.1",
                "port": 8000,
                "started_at": "2026-06-14T10:00:00+00:00",
                "log_path": str(operator_home / "data" / "managed-http-service.log"),
                "repo_root": str(tmp_path),
                "env_path": str(operator_home / ".env"),
                "argv": ["python", "-m", "neurocore.adapters.cli", "serve", "http"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "pid_is_active", lambda pid: True)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_read_pid_cmdline", lambda pid: None)
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_build_http_health_payload",
        lambda **kwargs: {
            "base_url": "http://127.0.0.1:8000",
            "semantic_ready": False,
            "openapi": {"ok": True, "status": 200},
            "query": {"ok": False, "status": 500},
            "capture": {"ok": False, "status": 500},
            "brains_get": {"ok": False, "status": 500},
        },
    )
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_terminate_pid",
        lambda pid: (_ for _ in ()).throw(AssertionError("terminate should not run")),
    )

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "stop"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["stopped"] is False
    assert payload["stale_state_cleared"] is True
    assert state_path.exists() is False


def test_neurocore_checkout_service_http_status_ignores_stale_mirror_state_for_sqlite(
    tmp_path, monkeypatch
):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    (operator_home / "data").mkdir(parents=True, exist_ok=True)
    (operator_home / "data" / "mirror-status.json").write_text(
        json.dumps(
            {
                "local_degraded": True,
                "reconciliation_pending": True,
                "last_sync_status": "abandoned",
                "last_local_error": "database is locked",
            }
        ),
        encoding="utf-8",
    )

    stdout = io.StringIO()
    exit_code = NEUROCORE_CHECKOUT.main(
        ["service", "http", "status"],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["storage"]["backend"] == "sqlite"
    assert payload["storage"]["tracked"] is False
    assert payload["storage"]["degraded"] is False


def test_neurocore_checkout_service_http_restart_refreshes_state(tmp_path, monkeypatch):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "SRC_ROOT", tmp_path / "src")
    pids = iter([1111, 2222])

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command, cwd, env, stdin, stdout, stderr, start_new_session, text):
        return FakeProcess(next(pids))

    monkeypatch.setattr(NEUROCORE_CHECKOUT.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        NEUROCORE_CHECKOUT,
        "_wait_for_managed_http_readiness",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_terminate_pid", lambda pid: None)
    monkeypatch.setattr(NEUROCORE_CHECKOUT, "_port_is_available", lambda host, port: True)

    start_stdout = io.StringIO()
    restart_stdout = io.StringIO()
    NEUROCORE_CHECKOUT.main(["service", "http", "start"], stdout=start_stdout)
    restart_exit = NEUROCORE_CHECKOUT.main(
        ["service", "http", "restart"],
        stdout=restart_stdout,
    )

    payload = json.loads(restart_stdout.getvalue())
    state = json.loads(
        (operator_home / "data" / "managed-http-service.json").read_text(
            encoding="utf-8"
        )
    )

    assert restart_exit == 0
    assert payload["action"] == "restart"
    assert payload["restarted"] is True
    assert state["pid"] == 2222


def test_neurocore_checkout_service_http_start_status_stop_smoke(tmp_path, monkeypatch):
    operator_home = tmp_path / ".operator-state"
    _write_operator_env(operator_home)
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    monkeypatch.setenv("NEUROCORE_PYTHON_EXECUTABLE", sys.executable)
    port = _free_port()

    start_stdout = io.StringIO()
    status_stdout = io.StringIO()
    stop_stdout = io.StringIO()

    try:
        start_exit = NEUROCORE_CHECKOUT.main(
            ["service", "http", "start", "--port", str(port)],
            stdout=start_stdout,
        )
        status_exit = NEUROCORE_CHECKOUT.main(
            ["service", "http", "status", "--port", str(port)],
            stdout=status_stdout,
        )
    finally:
        stop_exit = NEUROCORE_CHECKOUT.main(
            ["service", "http", "stop", "--port", str(port)],
            stdout=stop_stdout,
        )

    start_payload = json.loads(start_stdout.getvalue())
    status_payload = json.loads(status_stdout.getvalue())
    stop_payload = json.loads(stop_stdout.getvalue())

    assert start_exit == 0
    assert status_exit == 0
    assert stop_exit == 0
    assert start_payload["started"] is True
    assert status_payload["process"]["running"] is True
    assert status_payload["http"]["semantic_ready"] is True
    assert stop_payload["stopped"] is True
