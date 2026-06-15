import os
from pathlib import Path

from neurocore.core.operator_state import (
    OPERATOR_HOME_ENV,
    load_env_file,
    load_managed_http_service_state,
    load_mirror_status,
    load_operator_env,
    managed_http_service_log_path,
    managed_http_service_state_path,
    pid_is_active,
    save_managed_http_service_state,
    save_mirror_status,
)


def test_load_env_file_expands_operator_home_references(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"{OPERATOR_HOME_ENV}=~/neurocore-state",
                "NEUROCORE_PRIMARY_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore.db",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    values = load_env_file(env_path, base_env={"HOME": str(tmp_path)})

    assert values[OPERATOR_HOME_ENV] == str(tmp_path / "neurocore-state")
    assert values["NEUROCORE_PRIMARY_STORE_PATH"] == str(
        tmp_path / "neurocore-state" / "data" / "neurocore.db"
    )


def test_load_operator_env_prefers_external_env_path(tmp_path: Path):
    operator_home = tmp_path / "state-home"
    operator_home.mkdir()
    (operator_home / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=external\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=legacy\n",
        encoding="utf-8",
    )

    values, env_path, legacy = load_operator_env(
        tmp_path,
        base_env={OPERATOR_HOME_ENV: str(operator_home)},
    )

    assert legacy is False
    assert env_path == operator_home / ".env"
    assert values["NEUROCORE_DEFAULT_NAMESPACE"] == "external"


def test_load_operator_env_warns_when_falling_back_to_legacy_env(tmp_path: Path):
    legacy_env = tmp_path / ".env"
    legacy_env.write_text("NEUROCORE_DEFAULT_NAMESPACE=legacy\n", encoding="utf-8")

    class Capture:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def write(self, text: str) -> int:
            self.lines.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    capture = Capture()
    values, _, legacy = load_operator_env(
        tmp_path,
        base_env={"HOME": str(tmp_path)},
        stderr=capture,
    )

    assert legacy is True
    assert values["NEUROCORE_DEFAULT_NAMESPACE"] == "legacy"
    assert any(
        "repo-local operator state is deprecated" in line for line in capture.lines
    )


def test_load_operator_env_prefers_legacy_env_over_ambient_default_operator_home(
    tmp_path: Path,
):
    operator_home = tmp_path / "neurocore"
    operator_home.mkdir()
    (operator_home / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=external\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "NEUROCORE_DEFAULT_NAMESPACE=legacy\n",
        encoding="utf-8",
    )

    values, env_path, legacy = load_operator_env(
        tmp_path,
        base_env={
            "HOME": str(tmp_path),
            "XDG_STATE_HOME": str(tmp_path),
        },
    )

    assert legacy is True
    assert env_path == operator_home / ".env"
    assert values["NEUROCORE_DEFAULT_NAMESPACE"] == "legacy"


def test_save_and_load_mirror_status_round_trip(tmp_path: Path):
    snapshot = {
        "parity_verified": True,
        "last_parity_check": "2026-06-12T10:00:00+00:00",
        "last_sync_action": "verify_parity",
        "last_sync_status": "success",
    }
    status_path = tmp_path / "state" / "mirror-status.json"

    saved_path = save_mirror_status(snapshot, status_path)

    assert saved_path == status_path
    assert load_mirror_status(status_path) == snapshot


def test_save_and_load_managed_http_service_state_round_trip(tmp_path: Path):
    snapshot = {
        "pid": 1234,
        "host": "127.0.0.1",
        "port": 8000,
        "started_at": "2026-06-14T10:00:00+00:00",
        "log_path": str(tmp_path / "managed-http-service.log"),
        "repo_root": str(tmp_path / "repo"),
        "env_path": str(tmp_path / ".env"),
        "argv": ["python", "-m", "neurocore.adapters.cli", "serve", "http"],
    }
    state_path = tmp_path / "state" / "managed-http-service.json"

    saved_path = save_managed_http_service_state(snapshot, state_path)

    assert saved_path == state_path
    assert load_managed_http_service_state(state_path) == snapshot


def test_managed_http_service_paths_use_operator_home(tmp_path: Path):
    values = {OPERATOR_HOME_ENV: str(tmp_path / "operator-home")}

    assert managed_http_service_state_path(values) == (
        tmp_path / "operator-home" / "data" / "managed-http-service.json"
    )
    assert managed_http_service_log_path(values) == (
        tmp_path / "operator-home" / "data" / "managed-http-service.log"
    )


def test_pid_is_active_recognizes_current_process():
    assert pid_is_active(os.getpid()) is True
    assert pid_is_active(-1) is False
