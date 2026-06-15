"""Shared helpers for operator-local state resolution and loading."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping, TextIO

OPERATOR_HOME_ENV = "NEUROCORE_OPERATOR_HOME"
ENV_FILENAME = ".env"
MIRROR_STATUS_FILENAME = "mirror-status.json"
MANAGED_HTTP_SERVICE_STATE_FILENAME = "managed-http-service.json"
MANAGED_HTTP_SERVICE_LOG_FILENAME = "managed-http-service.log"
LEGACY_ENV_WARNING = (
    "Warning: repo-local operator state is deprecated; move {legacy_path} to "
    "{env_path} or set NEUROCORE_OPERATOR_HOME."
)
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def resolve_operator_home(env: Mapping[str, str] | None = None) -> Path:
    """Return the canonical operator-home directory."""
    values = dict(os.environ if env is None else env)
    configured = values.get(OPERATOR_HOME_ENV, "").strip()
    if configured:
        return Path(_expand_value(configured, values)).expanduser().absolute()
    xdg_state_home = values.get("XDG_STATE_HOME", "").strip()
    if xdg_state_home:
        base_dir = Path(_expand_value(xdg_state_home, values)).expanduser()
    else:
        home = values.get("HOME", "~").strip() or "~"
        base_dir = Path(home).expanduser() / ".local" / "state"
    return (base_dir / "neurocore").absolute()


def operator_env_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the canonical operator env-file path."""
    return resolve_operator_home(env) / ENV_FILENAME


def operator_data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the canonical operator data directory."""
    return resolve_operator_home(env) / "data"


def mirror_status_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the canonical persisted mirror-status path."""
    return operator_data_dir(env) / MIRROR_STATUS_FILENAME


def managed_http_service_state_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the canonical managed HTTP service state path."""
    return operator_data_dir(env) / MANAGED_HTTP_SERVICE_STATE_FILENAME


def managed_http_service_log_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the canonical managed HTTP service log path."""
    return operator_data_dir(env) / MANAGED_HTTP_SERVICE_LOG_FILENAME


def default_primary_store_path(env: Mapping[str, str] | None = None) -> str:
    """Return the canonical default primary SQLite path."""
    return str(operator_data_dir(env) / "neurocore.db")


def default_sealed_store_path(env: Mapping[str, str] | None = None) -> str:
    """Return the canonical default sealed SQLite path."""
    return str(operator_data_dir(env) / "neurocore-sealed.db")


def default_scheduler_store_path(env: Mapping[str, str] | None = None) -> str:
    """Return the canonical default scheduler SQLite path."""
    return str(operator_data_dir(env) / "neurocore-scheduler.db")


def load_env_file(
    path: Path,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Parse an env file and expand operator-home-aware path references."""
    if not path.exists():
        return {}

    values = dict(os.environ if base_env is None else base_env)
    raw_items: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        raw_items.append((key.strip(), value.strip().strip('"').strip("'")))

    resolved_operator_home = str(resolve_operator_home(values))
    for key, raw_value in raw_items:
        if key == OPERATOR_HOME_ENV:
            resolved_operator_home = _expand_value(raw_value, values)
            break

    expanded: dict[str, str] = {OPERATOR_HOME_ENV: resolved_operator_home}
    context = {**values, OPERATOR_HOME_ENV: resolved_operator_home}
    for key, raw_value in raw_items:
        expanded_value = (
            resolved_operator_home
            if key == OPERATOR_HOME_ENV
            else _expand_value(raw_value, {**context, **expanded})
        )
        expanded[key] = expanded_value
        context[key] = expanded_value
    return expanded


def load_operator_env(
    repo_root: Path,
    *,
    base_env: Mapping[str, str] | None = None,
    stderr: TextIO | None = None,
) -> tuple[dict[str, str], Path, bool]:
    """Load operator env values with canonical-path preference and legacy fallback."""
    values = dict(os.environ if base_env is None else base_env)
    legacy_path = repo_root / ENV_FILENAME
    explicit_operator_home = bool(values.get(OPERATOR_HOME_ENV, "").strip())
    env_path = operator_env_path(values)

    if explicit_operator_home and env_path.exists():
        return load_env_file(env_path, base_env=values), env_path, False

    if legacy_path.exists():
        if stderr is not None:
            print(
                LEGACY_ENV_WARNING.format(
                    legacy_path=legacy_path,
                    env_path=env_path,
                ),
                file=stderr,
            )
        return load_env_file(legacy_path, base_env=values), env_path, True

    if env_path.exists():
        return load_env_file(env_path, base_env=values), env_path, False

    return {OPERATOR_HOME_ENV: str(resolve_operator_home(values))}, env_path, False


def load_mirror_status(path: Path | None = None) -> dict[str, object]:
    """Load the persisted mirror-status snapshot if one exists."""
    status_path = path or mirror_status_path()
    return _load_json_snapshot(status_path)


def load_managed_http_service_state(path: Path | None = None) -> dict[str, object]:
    """Load the persisted managed HTTP service snapshot if one exists."""
    status_path = path or managed_http_service_state_path()
    return _load_json_snapshot(status_path)


def _load_json_snapshot(path: Path) -> dict[str, object]:
    """Load a persisted JSON object snapshot if one exists."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_mirror_status(
    snapshot: Mapping[str, object],
    path: Path | None = None,
) -> Path:
    """Persist the mirror-status snapshot with atomic replace semantics."""
    status_path = path or mirror_status_path()
    return _save_json_snapshot(snapshot, status_path)


def save_managed_http_service_state(
    snapshot: Mapping[str, object],
    path: Path | None = None,
) -> Path:
    """Persist the managed HTTP service snapshot with atomic replace semantics."""
    status_path = path or managed_http_service_state_path()
    return _save_json_snapshot(snapshot, status_path)


def _save_json_snapshot(snapshot: Mapping[str, object], path: Path) -> Path:
    """Persist a JSON object snapshot with atomic replace semantics."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return path


def pid_is_active(pid: int | None) -> bool:
    """Return whether the provided PID appears to still be alive."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _expand_value(value: str, env: Mapping[str, str]) -> str:
    """Expand ${VAR} references and ~ against the provided environment."""

    def replace(match: re.Match[str]) -> str:
        return env.get(match.group(1), "")

    expanded = _ENV_VAR_PATTERN.sub(replace, value)
    home = env.get("HOME", "").strip()
    if expanded == "~":
        return home or os.path.expanduser(expanded)
    if expanded.startswith("~/") and home:
        return str(Path(home) / expanded[2:])
    return os.path.expanduser(expanded)
