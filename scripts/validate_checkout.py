"""Checkout-safe wrapper for NeuroCore repo validation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neurocore.core.operator_state import load_operator_env


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


def _runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(load_operator_env(REPO_ROOT, base_env=env, stderr=sys.stderr)[0])
    src_path = str(SRC_ROOT)
    existing = env.get("PYTHONPATH", "").strip()
    if existing:
        parts = existing.split(os.pathsep)
        if src_path not in parts:
            env["PYTHONPATH"] = os.pathsep.join([src_path, *parts])
    else:
        env["PYTHONPATH"] = src_path
    return env


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env = _runtime_env()
    python_path = _resolve_python(env)
    completed = subprocess.run(
        [str(python_path), "-m", "neurocore.governance.validation", *args],
        cwd=REPO_ROOT,
        env=env,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
