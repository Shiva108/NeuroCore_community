"""Checkout-safe wrapper for NeuroCore CLI commands."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO
from urllib import error as urllib_error
from urllib import request as urllib_request

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neurocore.core.operator_state import (
    load_managed_http_service_state,
    load_mirror_status,
    load_operator_env,
    managed_http_service_log_path,
    managed_http_service_state_path,
    mirror_status_path,
    pid_is_active,
    save_managed_http_service_state,
)

READY_TIMEOUT_SECONDS = 10.0
READY_POLL_INTERVAL_SECONDS = 0.2
STOP_TIMEOUT_SECONDS = 5.0


def _resolve_python(env: dict[str, str]) -> Path:
    override = env.get("NEUROCORE_PYTHON_EXECUTABLE", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend(
        [
            REPO_ROOT / ".venv" / "bin" / "python",
            REPO_ROOT / ".venv" / "Scripts" / "python.exe",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.expanduser().absolute()
    return Path(sys.executable).expanduser().absolute()


def _runtime_env(*, stderr: TextIO | None = None) -> tuple[dict[str, str], Path]:
    env = dict(os.environ)
    operator_env, env_path, _legacy = load_operator_env(
        REPO_ROOT, base_env=env, stderr=stderr
    )
    env.update(operator_env)
    src_path = str(SRC_ROOT)
    existing = env.get("PYTHONPATH", "").strip()
    if existing:
        parts = existing.split(os.pathsep)
        if src_path not in parts:
            env["PYTHONPATH"] = os.pathsep.join([src_path, *parts])
    else:
        env["PYTHONPATH"] = src_path
    return env, env_path


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    env, env_path = _runtime_env(stderr=stderr)
    if args[:2] == ["service", "http"]:
        return _service_http_main(args[2:], env=env, env_path=env_path, stdout=stdout)
    python_path = _resolve_python(env)
    completed = subprocess.run(
        [str(python_path), "-m", "neurocore.adapters.cli", *args],
        cwd=REPO_ROOT,
        env=env,
    )
    return int(completed.returncode)


def _service_http_main(
    args: list[str],
    *,
    env: dict[str, str],
    env_path: Path,
    stdout: TextIO,
) -> int:
    parser = argparse.ArgumentParser(prog="neurocore_checkout.py service http")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "status", "stop", "restart"):
        child = subparsers.add_parser(action)
        child.add_argument("--host", default="127.0.0.1")
        child.add_argument("--port", type=int, default=8000)
    parsed = parser.parse_args(args)

    try:
        if parsed.action == "start":
            payload = _start_managed_http_service(
                env=env, env_path=env_path, host=parsed.host, port=parsed.port
            )
        elif parsed.action == "status":
            payload = _build_service_status_payload(
                env=env,
                env_path=env_path,
                requested_host=parsed.host,
                requested_port=parsed.port,
            )
        elif parsed.action == "stop":
            payload = _stop_managed_http_service(
                env=env, env_path=env_path, requested_host=parsed.host, requested_port=parsed.port
            )
        else:
            payload = _restart_managed_http_service(
                env=env, env_path=env_path, host=parsed.host, port=parsed.port
            )
        _write_json(stdout, payload)
        return int(payload.get("exit_code", 0))
    except RuntimeError as exc:
        _write_json(
            stdout,
            {
                "action": parsed.action,
                "error": str(exc),
                "healthy": False,
            },
        )
        return 1


def _write_json(stdout: TextIO, payload: dict[str, object]) -> None:
    stdout.write(json.dumps(payload))
    stdout.write("\n")


def _start_managed_http_service(
    *,
    env: dict[str, str],
    env_path: Path,
    host: str,
    port: int,
) -> dict[str, object]:
    current = _build_service_status_payload(
        env=env,
        env_path=env_path,
        requested_host=host,
        requested_port=port,
    )
    if bool(current["process"].get("running")):
        current.update(
            {
                "action": "start",
                "started": False,
                "already_running": True,
                "exit_code": 0,
            }
        )
        return current
    if not _port_is_available(host, port):
        raise RuntimeError(
            f"Refusing to start managed NeuroCore HTTP service on {host}:{port}: port is already in use"
        )

    python_path = _resolve_python(env)
    log_path = managed_http_service_log_path(env)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        str(python_path),
        "-m",
        "neurocore.adapters.cli",
        "serve",
        "http",
        "--host",
        host,
        "--port",
        str(port),
    ]
    launch_env = dict(env)
    launch_env["PYTHONUNBUFFERED"] = "1"
    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            argv,
            cwd=REPO_ROOT,
            env=launch_env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            text=True,
        )

    snapshot = {
        "pid": process.pid,
        "host": host,
        "port": port,
        "started_at": datetime.now(UTC).isoformat(),
        "log_path": str(log_path),
        "repo_root": str(REPO_ROOT),
        "env_path": str(env_path),
        "argv": argv,
    }
    save_managed_http_service_state(snapshot, managed_http_service_state_path(env))
    try:
        _wait_for_managed_http_readiness(
            pid=process.pid,
            host=host,
            port=port,
            log_path=log_path,
        )
    except Exception:
        _terminate_pid(process.pid)
        _delete_managed_http_service_state(env)
        raise

    payload = _build_service_status_payload(
        env=env,
        env_path=env_path,
        requested_host=host,
        requested_port=port,
    )
    payload.update(
        {
            "action": "start",
            "started": True,
            "already_running": False,
            "exit_code": 0,
        }
    )
    return payload


def _stop_managed_http_service(
    *,
    env: dict[str, str],
    env_path: Path,
    requested_host: str,
    requested_port: int,
) -> dict[str, object]:
    snapshot = load_managed_http_service_state(managed_http_service_state_path(env))
    pid = _coerce_int(snapshot.get("pid"))
    if pid is None:
        payload = _build_service_status_payload(
            env=env,
            env_path=env_path,
            requested_host=requested_host,
            requested_port=requested_port,
        )
        payload.update({"action": "stop", "stopped": False, "exit_code": 0})
        return payload
    if not _is_managed_http_service_pid(pid, snapshot):
        _delete_managed_http_service_state(env)
        payload = _build_service_status_payload(
            env=env,
            env_path=env_path,
            requested_host=requested_host,
            requested_port=requested_port,
        )
        payload.update(
            {
                "action": "stop",
                "stopped": False,
                "stale_state_cleared": True,
                "exit_code": 0,
            }
        )
        return payload
    _terminate_pid(pid)
    _delete_managed_http_service_state(env)
    payload = _build_service_status_payload(
        env=env,
        env_path=env_path,
        requested_host=requested_host,
        requested_port=requested_port,
    )
    payload.update(
        {
            "action": "stop",
            "stopped": True,
            "stopped_pid": pid,
            "exit_code": 0,
        }
    )
    return payload


def _restart_managed_http_service(
    *,
    env: dict[str, str],
    env_path: Path,
    host: str,
    port: int,
) -> dict[str, object]:
    _stop_managed_http_service(
        env=env,
        env_path=env_path,
        requested_host=host,
        requested_port=port,
    )
    payload = _start_managed_http_service(
        env=env, env_path=env_path, host=host, port=port
    )
    payload.update({"action": "restart", "restarted": True, "exit_code": 0})
    return payload


def _build_service_status_payload(
    *,
    env: dict[str, str],
    env_path: Path,
    requested_host: str,
    requested_port: int,
) -> dict[str, object]:
    snapshot = load_managed_http_service_state(managed_http_service_state_path(env))
    pid = _coerce_int(snapshot.get("pid"))
    managed_running = pid is not None and _is_managed_http_service_pid(pid, snapshot)
    host = str(snapshot.get("host") or requested_host)
    port = _coerce_int(snapshot.get("port")) or requested_port
    started_at = _coerce_datetime(snapshot.get("started_at"))
    stale_code = managed_running and _has_stale_code(started_at)
    unmanaged_listener = not managed_running and not _port_is_available(host, port)
    http_payload = (
        _build_http_health_payload(host=host, port=port)
        if (managed_running or unmanaged_listener)
        else _stopped_http_health_payload(host=host, port=port)
    )
    storage_payload = _build_storage_summary(env)
    process_payload = {
        "state": (
            "running"
            if managed_running
            else ("conflict" if unmanaged_listener else "stopped")
        ),
        "running": managed_running,
        "managed": managed_running,
        "unmanaged_listener": unmanaged_listener,
        "pid": pid,
        "host": host,
        "port": port,
        "started_at": snapshot.get("started_at"),
        "stale_code": stale_code,
        "log_path": str(snapshot.get("log_path") or managed_http_service_log_path(env)),
        "repo_root": str(snapshot.get("repo_root") or REPO_ROOT),
        "env_path": str(snapshot.get("env_path") or env_path),
        "argv": list(snapshot.get("argv") or []),
    }
    healthy = bool(
        managed_running and http_payload["semantic_ready"] and not storage_payload["degraded"]
    )
    return {
        "process": process_payload,
        "http": http_payload,
        "storage": storage_payload,
        "healthy": healthy,
        "exit_code": 0,
    }


def _build_http_health_payload(*, host: str, port: int) -> dict[str, object]:
    base_url = f"http://{host}:{port}"
    checks = {
        "openapi": _http_check(f"{base_url}/openapi.json", expected_status=200),
        "query": _http_check(
            f"{base_url}/query",
            method="POST",
            payload={},
            expected_status=200,
        ),
        "capture": _http_check(
            f"{base_url}/capture",
            method="POST",
            payload={},
            expected_status=400,
        ),
        "brains_get": _http_check(
            f"{base_url}/brains/get",
            method="POST",
            payload={"brain_id": "missing-brain"},
            expected_status=404,
        ),
    }
    return {
        "base_url": base_url,
        "semantic_ready": all(check["ok"] for check in checks.values()),
        **checks,
    }


def _stopped_http_health_payload(*, host: str, port: int) -> dict[str, object]:
    base_url = f"http://{host}:{port}"
    error = {"ok": False, "status": None, "error": "service not running"}
    return {
        "base_url": base_url,
        "semantic_ready": False,
        "openapi": dict(error),
        "query": dict(error),
        "capture": dict(error),
        "brains_get": dict(error),
    }


def _http_check(
    url: str,
    *,
    expected_status: int,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(request, timeout=1.0) as response:
            status = response.status
            return {
                "ok": status == expected_status,
                "status": status,
                "expected_status": expected_status,
            }
    except urllib_error.HTTPError as exc:
        return {
            "ok": exc.code == expected_status,
            "status": exc.code,
            "expected_status": expected_status,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "expected_status": expected_status,
            "error": str(exc),
        }


def _build_storage_summary(env: dict[str, str]) -> dict[str, object]:
    storage_backend = str(env.get("NEUROCORE_STORAGE_BACKEND") or "").strip().lower()
    if storage_backend != "mirror":
        return {
            "backend": storage_backend or "unknown",
            "tracked": False,
            "degraded": False,
            "local_degraded": False,
            "cloud_degraded": False,
            "reconciliation_pending": False,
            "last_sync_status": None,
            "last_local_error": None,
            "last_cloud_error": None,
            "last_sync_error": None,
            "last_sync_action": None,
            "remediation_hint": None,
        }
    snapshot = load_mirror_status(mirror_status_path(env))
    degraded = any(
        [
            bool(snapshot.get("local_degraded")),
            bool(snapshot.get("cloud_degraded")),
            bool(snapshot.get("reconciliation_pending")),
            snapshot.get("last_sync_status") in {"failed", "abandoned"},
            bool(str(snapshot.get("last_local_error") or "").strip()),
            bool(str(snapshot.get("last_cloud_error") or "").strip()),
        ]
    )
    return {
        "backend": storage_backend,
        "tracked": bool(snapshot),
        "degraded": degraded,
        "local_degraded": bool(snapshot.get("local_degraded")),
        "cloud_degraded": bool(snapshot.get("cloud_degraded")),
        "reconciliation_pending": bool(snapshot.get("reconciliation_pending")),
        "last_sync_status": snapshot.get("last_sync_status"),
        "last_local_error": snapshot.get("last_local_error"),
        "last_cloud_error": snapshot.get("last_cloud_error"),
        "last_sync_error": snapshot.get("last_sync_error"),
        "last_sync_action": snapshot.get("last_sync_action"),
        "remediation_hint": (
            "Inspect mirror status and repair with the admin sync workflow."
            if degraded
            else None
        ),
    }


def _wait_for_managed_http_readiness(
    *,
    pid: int,
    host: str,
    port: int,
    log_path: Path,
) -> None:
    deadline = time.time() + READY_TIMEOUT_SECONDS
    last_http = _stopped_http_health_payload(host=host, port=port)
    while time.time() < deadline:
        if not pid_is_active(pid):
            raise RuntimeError(
                f"Managed NeuroCore HTTP service exited before readiness (log: {log_path})"
            )
        last_http = _build_http_health_payload(host=host, port=port)
        if last_http["semantic_ready"]:
            return
        time.sleep(READY_POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        "Managed NeuroCore HTTP service failed semantic readiness within "
        f"{READY_TIMEOUT_SECONDS:.1f}s (log: {log_path}, last_http={json.dumps(last_http, sort_keys=True)})"
    )


def _delete_managed_http_service_state(env: dict[str, str]) -> None:
    state_path = managed_http_service_state_path(env)
    if state_path.exists():
        state_path.unlink()


def _terminate_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + STOP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if not pid_is_active(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _is_managed_http_service_pid(pid: int, snapshot: dict[str, object]) -> bool:
    if not pid_is_active(pid):
        return False
    cmdline = _read_pid_cmdline(pid)
    if cmdline is None:
        return _managed_http_service_ready(snapshot)
    required = ["-m", "neurocore.adapters.cli", "serve", "http"]
    if not all(item in cmdline for item in required):
        return False
    host = str(snapshot.get("host") or "")
    port = str(snapshot.get("port") or "")
    return (not host or host in cmdline) and (not port or port in cmdline)


def _read_pid_cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return _read_pid_cmdline_via_ps(pid)
    return [part for part in raw.decode("utf-8", errors="ignore").split("\0") if part]


def _read_pid_cmdline_via_ps(pid: int) -> list[str] | None:
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    command = completed.stdout.strip()
    if not command:
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _managed_http_service_ready(snapshot: dict[str, object]) -> bool:
    host = str(snapshot.get("host") or "").strip()
    port = _coerce_int(snapshot.get("port"))
    if not host or port is None:
        return False
    return bool(
        _build_http_health_payload(host=host, port=port).get("semantic_ready", False)
    )


def _coerce_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _has_stale_code(started_at: datetime | None) -> bool:
    if started_at is None:
        return False
    candidate_paths = [SRC_ROOT, REPO_ROOT / "scripts" / "neurocore_checkout.py"]
    started_ts = started_at.timestamp()
    for candidate in candidate_paths:
        if candidate.is_file():
            if candidate.stat().st_mtime > started_ts:
                return True
            continue
        if not candidate.exists():
            continue
        for path in candidate.rglob("*.py"):
            if path.stat().st_mtime > started_ts:
                return True
    return False


def _port_is_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
