"""Bootstrap local NeuroCore Community development."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neurocore.core.operator_state import (
    OPERATOR_HOME_ENV,
    operator_data_dir,
    operator_env_path,
    resolve_operator_home,
)

INSTALL_SPEC = ".[dev]"


class BootstrapError(RuntimeError):
    """Raised when bootstrap cannot complete safely."""

    def __init__(
        self,
        message: str,
        *,
        command: list[str] | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.remediation = remediation


Runner = Callable[[list[str], Path, dict[str, str] | None], None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/bootstrap.py",
        description="Set up a local NeuroCore Community workspace.",
    )
    parser.add_argument(
        "--force-env",
        action="store_true",
        help="Overwrite an existing operator env file.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip pytest and repo validation at the end of setup.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    repo_root: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    runner: Runner | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = (repo_root or REPO_ROOT).resolve()
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    command_runner = runner or _run_subprocess

    try:
        run_bootstrap(args, repo_root=root, stdout=stdout, runner=command_runner)
    except BootstrapError as exc:
        print(f"Bootstrap failed: {exc}", file=stderr)
        if exc.command:
            print(f"Failed command: {_format_command(exc.command)}", file=stderr)
        if exc.remediation:
            print(f"Try this: {exc.remediation}", file=stderr)
        return 1

    return 0


def run_bootstrap(
    args: argparse.Namespace,
    *,
    repo_root: Path,
    stdout: TextIO,
    runner: Runner,
) -> None:
    print("Starting NeuroCore Community bootstrap.", file=stdout)
    base_env = dict(os.environ)
    operator_home = resolve_operator_home(base_env)
    _ensure_operator_home_outside_checkout(
        operator_home=operator_home, repo_root=repo_root
    )
    runtime_env = {**base_env, OPERATOR_HOME_ENV: str(operator_home)}
    env_path = operator_env_path(runtime_env)
    data_dir = operator_data_dir(runtime_env)

    venv_dir = repo_root / ".venv"
    _ensure_virtualenv(venv_dir, repo_root=repo_root, stdout=stdout, runner=runner)
    venv_python = _venv_python_path(venv_dir)

    _run_checked(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=repo_root,
        env=base_env,
        runner=runner,
        remediation="Activate the virtual environment and confirm pip is available.",
    )
    _run_checked(
        [str(venv_python), "-m", "pip", "install", "-e", INSTALL_SPEC],
        cwd=repo_root,
        env=base_env,
        runner=runner,
        remediation="Check network access and editable-install metadata in pyproject.toml.",
    )

    operator_home.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    _ensure_env_file(
        repo_root=repo_root,
        env_path=env_path,
        operator_home=operator_home,
        overwrite=args.force_env,
        stdout=stdout,
    )

    if not args.skip_verify:
        _run_checked(
            [str(venv_python), "-m", "pytest"],
            cwd=repo_root,
            env=runtime_env,
            runner=runner,
            remediation="Review the pytest failures, fix them, and rerun bootstrap.",
        )
        _run_checked(
            [str(venv_python), "-m", "neurocore.governance.validation"],
            cwd=repo_root,
            env=runtime_env,
            runner=runner,
            remediation="Fix the reported repo-contract issue and rerun bootstrap.",
        )
        print("Verification completed successfully.", file=stdout)
    else:
        print("Skipped verification at your request.", file=stdout)

    print("NeuroCore Community bootstrap is complete.", file=stdout)
    print(f"Operator env file: {env_path}", file=stdout)
    print("Next steps:", file=stdout)
    print("1. source .venv/bin/activate", file=stdout)
    print("2. pytest", file=stdout)
    print("3. python scripts/validate_checkout.py", file=stdout)


def _ensure_virtualenv(
    venv_dir: Path,
    *,
    repo_root: Path,
    stdout: TextIO,
    runner: Runner,
) -> None:
    if _venv_python_path(venv_dir).exists():
        print(f"Reusing existing virtual environment at {venv_dir}.", file=stdout)
        return

    print(f"Creating virtual environment at {venv_dir}.", file=stdout)
    _run_checked(
        [sys.executable, "-m", "venv", str(venv_dir)],
        cwd=repo_root,
        env=None,
        runner=runner,
        remediation="Install Python 3.11+ with the venv module available.",
    )


def _ensure_env_file(
    *,
    repo_root: Path,
    env_path: Path,
    operator_home: Path,
    overwrite: bool,
    stdout: TextIO,
) -> None:
    if env_path.exists() and not overwrite:
        print(f"Preserving existing operator env file at {env_path}.", file=stdout)
        return

    template_path = repo_root / ".env.example"
    template = template_path.read_text(encoding="utf-8")
    rendered = template.replace(
        "NEUROCORE_OPERATOR_HOME=~/.local/state/neurocore",
        f"NEUROCORE_OPERATOR_HOME={operator_home}",
    )
    env_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote operator env file to {env_path}.", file=stdout)


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    runner: Runner,
    remediation: str,
) -> None:
    try:
        runner(command, cwd, env)
    except subprocess.CalledProcessError as exc:
        raise BootstrapError(
            f"Command failed with exit code {exc.returncode}",
            command=command,
            remediation=remediation,
        ) from exc


def _ensure_operator_home_outside_checkout(
    *, operator_home: Path, repo_root: Path
) -> None:
    try:
        operator_home.relative_to(repo_root)
    except ValueError:
        return
    raise BootstrapError(
        "Operator home must live outside the repository checkout.",
        remediation=(
            "Unset NEUROCORE_OPERATOR_HOME or point it at a directory outside the repo."
        ),
    )


def _venv_python_path(venv_dir: Path) -> Path:
    return venv_dir / "bin" / "python"


def _run_subprocess(command: list[str], cwd: Path, env: dict[str, str] | None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _format_command(command: list[str]) -> str:
    return " ".join(command)


if __name__ == "__main__":
    raise SystemExit(main())
