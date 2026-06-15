"""Bootstrap local NeuroCore development and security workflows."""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shlex
import shutil
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
    load_env_file,
    operator_data_dir,
    operator_env_path,
    resolve_operator_home,
)

DEFAULT_PROFILE = "security-operator"
NAMESPACE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
PROFILE_TEMPLATES = {
    "hosted": ".env.hosted.example",
    "mirror": ".env.mirror.example",
    "security-operator": ".env.security-operator.example",
}
LOCAL_TEMPLATE_FILES = (
    ("secrets.json.example", "secrets.json"),
    ("preferences.json.example", "preferences.json"),
)


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
    """Create the bootstrap CLI parser."""
    parser = argparse.ArgumentParser(
        prog="python scripts/bootstrap.py",
        description="Set up a local NeuroCore workspace with security defaults.",
    )
    parser.add_argument(
        "--wizard",
        action="store_true",
        help="Prompt for namespace, operator env overwrite, and verification choices.",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        choices=sorted(PROFILE_TEMPLATES),
        help="Select the onboarding profile to apply.",
    )
    parser.add_argument(
        "--force-env",
        action="store_true",
        help="Overwrite an existing operator env file with the selected profile.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip pytest and governance verification at the end of setup.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    repo_root: Path | None = None,
    input_fn: Callable[[str], str] = input,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    runner: Runner | None = None,
) -> int:
    """Run the local bootstrap workflow."""
    parser = build_parser()
    args = parser.parse_args(argv)
    root = (repo_root or REPO_ROOT).resolve()
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    command_runner = runner or _run_subprocess

    try:
        run_bootstrap(
            args,
            repo_root=root,
            input_fn=input_fn,
            stdout=stdout,
            runner=command_runner,
        )
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
    input_fn: Callable[[str], str],
    stdout: TextIO,
    runner: Runner,
) -> None:
    """Execute the bootstrap steps for a repo root."""
    print(
        f"Starting NeuroCore bootstrap with the {args.profile} profile.",
        file=stdout,
    )
    base_env = dict(os.environ)
    operator_home = resolve_operator_home(base_env)
    _ensure_operator_home_outside_checkout(
        operator_home=operator_home,
        repo_root=repo_root,
    )
    env_path = operator_env_path({**base_env, OPERATOR_HOME_ENV: str(operator_home)})
    data_dir = operator_data_dir({OPERATOR_HOME_ENV: str(operator_home)})

    venv_dir = repo_root / ".venv"
    _ensure_virtualenv(venv_dir, repo_root=repo_root, stdout=stdout, runner=runner)
    venv_python = _venv_python_path(venv_dir)

    install_env = dict(base_env)
    _run_checked(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=repo_root,
        env=install_env,
        runner=runner,
        remediation="Activate the virtual environment and confirm pip is available.",
    )
    _run_checked(
        [str(venv_python), "-m", "pip", "install", "-e", ".[dev,semantic]"],
        cwd=repo_root,
        env=install_env,
        runner=runner,
        remediation=(
            "Check internet access, Python build tooling, and the editable install"
            " metadata in pyproject.toml."
        ),
    )

    namespace = "security-lab"
    overwrite_env = args.force_env
    run_verification = not args.skip_verify

    if args.wizard:
        namespace, overwrite_env, run_verification = _run_wizard(
            env_path=env_path,
            default_namespace=namespace,
            default_overwrite=overwrite_env,
            default_verify=run_verification,
            input_fn=input_fn,
            stdout=stdout,
        )

    operator_home.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Ensured operator home at {operator_home}.", file=stdout)
    print(f"Ensured local runtime data directory at {data_dir}.", file=stdout)
    _migrate_legacy_operator_state(
        repo_root=repo_root,
        operator_home=operator_home,
        stdout=stdout,
    )
    _ensure_env_file(
        profile=args.profile,
        namespace=namespace,
        env_path=env_path,
        operator_home=operator_home,
        repo_root=repo_root,
        overwrite=overwrite_env,
        stdout=stdout,
    )
    for template_name, target_name in LOCAL_TEMPLATE_FILES:
        _copy_if_missing(
            source=repo_root / template_name,
            destination=operator_home / target_name,
            stdout=stdout,
        )
    _configure_git_hooks(repo_root=repo_root, stdout=stdout, runner=runner)

    if run_verification:
        runtime_env = dict(base_env)
        runtime_env.update(_load_env_values(env_path))
        _run_checked(
            [str(venv_python), "-m", "pytest"],
            cwd=repo_root,
            env=runtime_env,
            runner=runner,
            remediation="Review the pytest output, then rerun bootstrap after fixing the failing test.",
        )
        _run_checked(
            [str(venv_python), "-m", "neurocore.governance.validation"],
            cwd=repo_root,
            env=runtime_env,
            runner=runner,
            remediation=(
                "Inspect the reported contract or secret-hygiene issue, adjust the"
                " local files, and rerun bootstrap."
            ),
        )
        print("Verification completed successfully.", file=stdout)
        _print_readiness_summary(
            repo_root=repo_root,
            runtime_env=runtime_env,
            stdout=stdout,
        )
    else:
        print("Skipped verification at your request.", file=stdout)
        runtime_env = dict(base_env)
        runtime_env.update(_load_env_values(env_path))
        _print_readiness_summary(
            repo_root=repo_root,
            runtime_env=runtime_env,
            stdout=stdout,
        )

    _print_next_steps(stdout=stdout, env_path=env_path)


def _run_wizard(
    *,
    env_path: Path,
    default_namespace: str,
    default_overwrite: bool,
    default_verify: bool,
    input_fn: Callable[[str], str],
    stdout: TextIO,
) -> tuple[str, bool, bool]:
    """Collect the limited v1 setup decisions interactively."""
    print("Running bootstrap wizard.", file=stdout)
    namespace = (
        input_fn(
            f"Namespace to write into the operator env file [{default_namespace}]: "
        ).strip()
        or default_namespace
    )
    _validate_namespace(namespace)

    overwrite = default_overwrite
    if env_path.exists() and not default_overwrite:
        overwrite = _prompt_yes_no(
            input_fn,
            f"Overwrite the existing operator env file at {env_path}? [y/N]: ",
            default=False,
        )

    verify = default_verify
    if default_verify:
        verify = _prompt_yes_no(
            input_fn,
            "Run pytest and governance checks after setup? [Y/n]: ",
            default=True,
        )

    return namespace, overwrite, verify


def _ensure_virtualenv(
    venv_dir: Path,
    *,
    repo_root: Path,
    stdout: TextIO,
    runner: Runner,
) -> None:
    """Create the virtual environment if it is missing."""
    if _venv_python_path(venv_dir).exists():
        print(f"Reusing existing virtual environment at {venv_dir}.", file=stdout)
        return
    if venv_dir.exists():
        print(
            f"Virtual environment directory exists but is incomplete at {venv_dir}; "
            "recreating it.",
            file=stdout,
        )
        shutil.rmtree(venv_dir)

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
    profile: str,
    namespace: str,
    env_path: Path,
    operator_home: Path,
    repo_root: Path,
    overwrite: bool,
    stdout: TextIO,
) -> None:
    """Create or preserve the canonical operator env file."""
    existed_before = env_path.exists()
    if existed_before and not overwrite:
        print(f"Preserved existing environment file at {env_path}.", file=stdout)
        return

    template_name = PROFILE_TEMPLATES[profile]
    template_path = repo_root / template_name
    if not template_path.exists():
        raise BootstrapError(
            f"Missing bootstrap profile template: {template_name}",
            remediation="Restore the checked-in profile template before rerunning setup.",
        )

    env_path.write_text(
        _render_env_template(
            template_path.read_text(encoding="utf-8"),
            namespace=namespace,
            operator_home=operator_home,
        ),
        encoding="utf-8",
    )
    action = "Updated" if existed_before else "Wrote"
    print(f"{action} environment file at {env_path}.", file=stdout)


def _copy_if_missing(*, source: Path, destination: Path, stdout: TextIO) -> None:
    """Copy a local-only template if the target does not exist."""
    if not source.exists():
        raise BootstrapError(
            f"Missing local template file: {source.name}",
            remediation="Restore the checked-in example files before rerunning setup.",
        )
    if destination.exists():
        print(f"Preserved existing local file at {destination}.", file=stdout)
        return
    shutil.copyfile(source, destination)
    print(f"Created local file at {destination}.", file=stdout)


def _configure_git_hooks(*, repo_root: Path, stdout: TextIO, runner: Runner) -> None:
    """Point the local checkout at the committed repo hooks."""
    hooks_dir = repo_root / ".githooks"
    pre_commit_hook = hooks_dir / "pre-commit"
    if not pre_commit_hook.exists():
        raise BootstrapError(
            "Missing required Git hook: .githooks/pre-commit",
            remediation="Restore the committed hook file before rerunning setup.",
        )
    _run_checked(
        ["git", "config", "core.hooksPath", str(hooks_dir)],
        cwd=repo_root,
        env=None,
        runner=runner,
        remediation=(
            "Confirm git is installed and the checkout is a writable repository, "
            "then rerun bootstrap."
        ),
    )
    print(f"Configured git hooks path to {hooks_dir}.", file=stdout)


def _load_env_values(env_path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file into process environment values."""
    return load_env_file(env_path, base_env=os.environ)


def _render_env_template(
    template_text: str,
    *,
    namespace: str,
    operator_home: Path,
) -> str:
    """Return the profile template with the selected namespace applied."""
    _validate_namespace(namespace)
    rendered_lines = []
    replaced_namespace = False
    replaced_operator_home = False
    for line in template_text.splitlines():
        if line.startswith("NEUROCORE_DEFAULT_NAMESPACE="):
            rendered_lines.append(f"NEUROCORE_DEFAULT_NAMESPACE={namespace}")
            replaced_namespace = True
            continue
        if line.startswith(f"{OPERATOR_HOME_ENV}="):
            rendered_lines.append(f"{OPERATOR_HOME_ENV}={operator_home}")
            replaced_operator_home = True
            continue
        rendered_lines.append(line)
    if not replaced_namespace:
        rendered_lines.append(f"NEUROCORE_DEFAULT_NAMESPACE={namespace}")
    if not replaced_operator_home:
        rendered_lines.append(f"{OPERATOR_HOME_ENV}={operator_home}")
    return "\n".join(rendered_lines) + "\n"


def _venv_python_path(venv_dir: Path) -> Path:
    """Resolve the venv Python interpreter for the current platform."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    runner: Runner,
    remediation: str,
) -> None:
    """Run a command and raise a bootstrap-specific error when it fails."""
    try:
        runner(command, cwd, env)
    except FileNotFoundError as exc:
        raise BootstrapError(
            f"Could not start command: {_format_command(command)}",
            command=command,
            remediation=remediation,
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise BootstrapError(
            f"Command exited with status {exc.returncode}.",
            command=command,
            remediation=remediation,
        ) from exc


def _run_subprocess(
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None,
) -> None:
    """Execute a command with inherited stdio."""
    subprocess.run(command, check=True, cwd=cwd, env=env)


def _print_readiness_summary(
    *,
    repo_root: Path,
    runtime_env: dict[str, str],
    stdout: TextIO,
) -> None:
    module_path = repo_root / "scripts" / "security_workflow.py"
    spec = importlib.util.spec_from_file_location(
        "security_workflow_bootstrap", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    spec.loader.exec_module(module)
    module.print_readiness_summary(repo_root=repo_root, env=runtime_env, stdout=stdout)


def _ensure_operator_home_outside_checkout(
    *,
    operator_home: Path,
    repo_root: Path,
) -> None:
    """Reject operator homes that would violate checkout hygiene."""
    resolved_repo = repo_root.resolve()
    resolved_home = operator_home.resolve()
    if resolved_home == resolved_repo or resolved_home.is_relative_to(resolved_repo):
        raise BootstrapError(
            f"Operator home must live outside the repository checkout: {operator_home}",
            remediation=(
                "Unset NEUROCORE_OPERATOR_HOME or point it at a directory outside "
                "the repo, such as ~/.local/state/neurocore."
            ),
        )


def _migrate_legacy_operator_state(
    *,
    repo_root: Path,
    operator_home: Path,
    stdout: TextIO,
) -> None:
    """Move legacy repo-local operator files into the canonical operator home."""
    legacy_files = (
        (repo_root / ".env", operator_home / ".env"),
        (repo_root / "secrets.json", operator_home / "secrets.json"),
        (repo_root / "preferences.json", operator_home / "preferences.json"),
    )
    for source, destination in legacy_files:
        _move_if_legacy(source=source, destination=destination, stdout=stdout)

    legacy_data_dir = repo_root / "data"
    if not legacy_data_dir.exists():
        return
    for source in sorted(legacy_data_dir.iterdir()):
        _move_if_legacy(
            source=source,
            destination=operator_home / "data" / source.name,
            stdout=stdout,
        )
    if legacy_data_dir.exists() and not any(legacy_data_dir.iterdir()):
        legacy_data_dir.rmdir()


def _move_if_legacy(*, source: Path, destination: Path, stdout: TextIO) -> None:
    """Move one legacy file or directory when it still lives in the checkout."""
    if not source.exists():
        return
    if destination.exists():
        raise BootstrapError(
            f"Legacy operator state still exists at {source} but {destination} already exists.",
            remediation=(
                "Move or remove the repo-local file manually, then rerun bootstrap."
            ),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    print(
        f"Migrated legacy operator state from {source} to {destination}.", file=stdout
    )


def _prompt_yes_no(
    input_fn: Callable[[str], str],
    prompt: str,
    *,
    default: bool,
) -> bool:
    """Prompt until the user enters a valid yes/no response."""
    while True:
        raw = input_fn(prompt).strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False


def _print_next_steps(*, stdout: TextIO, env_path: Path) -> None:
    """Print the next commands a user can run after bootstrap."""
    activate_command = (
        r".venv\Scripts\activate" if os.name == "nt" else "source .venv/bin/activate"
    )
    print("", file=stdout)
    print("NeuroCore bootstrap is complete.", file=stdout)
    print("Next steps:", file=stdout)
    print(f"1. {activate_command}", file=stdout)
    print(f"2. review operator env file at {env_path}", file=stdout)
    print(
        '3. python scripts/neurocore_checkout.py capture --request-json \'{"bucket":"recon","content":"initial '
        'recon note","content_format":"markdown","source_type":"note"}\'',
        file=stdout,
    )
    print(
        '4. python scripts/neurocore_checkout.py query --request-json \'{"query_text":"recon",'
        '"allowed_buckets":["recon","findings"],'
        '"sensitivity_ceiling":"restricted"}\'',
        file=stdout,
    )
    print("5. python scripts/validate_checkout.py", file=stdout)


def _validate_namespace(namespace: str) -> None:
    """Validate a namespace value before writing it into local config."""
    if not NAMESPACE_PATTERN.match(namespace):
        raise BootstrapError(
            "Namespace must start with a lowercase letter or number and use only "
            "lowercase letters, numbers, underscores, or hyphens.",
            remediation="Choose a namespace like security-lab, h1-acme, or pt_client.",
        )


def _format_command(command: list[str]) -> str:
    """Render a shell-safe command preview."""
    return " ".join(shlex.quote(part) for part in command)


if __name__ == "__main__":
    raise SystemExit(main())
