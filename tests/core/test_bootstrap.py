import importlib.util
import io
import subprocess
import sys
from pathlib import Path


def load_bootstrap_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap.py"
    spec = importlib.util.spec_from_file_location("bootstrap_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BOOTSTRAP = load_bootstrap_module()


def create_repo_scaffold(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "\n".join(
            [
                "NEUROCORE_OPERATOR_HOME=~/.local/state/neurocore",
                "NEUROCORE_DEFAULT_NAMESPACE=default",
                "NEUROCORE_ALLOWED_BUCKETS=work,research,planning,personal,ops",
                "NEUROCORE_DEFAULT_SENSITIVITY=standard",
                "NEUROCORE_STORAGE_BACKEND=sqlite",
                "NEUROCORE_PRIMARY_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore.db",
                "NEUROCORE_SEALED_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore-sealed.db",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def configure_operator_home(monkeypatch, tmp_path: Path) -> Path:
    operator_home = tmp_path.parent / f"{tmp_path.name}-operator-state"
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(operator_home))
    return operator_home


class FakeRunner:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.commands = []

    def __call__(self, command, cwd, env):
        self.commands.append({"command": command, "cwd": cwd, "env": env})
        if self.fail_on and self.fail_on(command):
            raise subprocess.CalledProcessError(returncode=1, cmd=command)
        if command[0] == sys.executable and command[1:3] == ["-m", "venv"]:
            venv_dir = Path(command[-1])
            python_path = BOOTSTRAP._venv_python_path(venv_dir)
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")


def test_bootstrap_creates_local_setup_files_and_runs_verification(
    tmp_path: Path, monkeypatch
):
    create_repo_scaffold(tmp_path)
    operator_home = configure_operator_home(monkeypatch, tmp_path)
    runner = FakeRunner()
    stdout = io.StringIO()

    exit_code = BOOTSTRAP.main(
        [],
        repo_root=tmp_path,
        stdout=stdout,
        stderr=io.StringIO(),
        runner=runner,
    )

    assert exit_code == 0
    assert (tmp_path / ".venv").exists()
    assert not (tmp_path / ".env").exists()
    assert (operator_home / ".env").exists()
    assert (operator_home / "data").exists()

    commands = [entry["command"] for entry in runner.commands]
    assert [
        str(BOOTSTRAP._venv_python_path(tmp_path / ".venv")),
        "-m",
        "pip",
        "install",
        "-e",
        ".[dev]",
    ] in commands
    assert [
        str(BOOTSTRAP._venv_python_path(tmp_path / ".venv")),
        "-m",
        "pytest",
    ] in commands
    assert [
        str(BOOTSTRAP._venv_python_path(tmp_path / ".venv")),
        "-m",
        "neurocore.governance.validation",
    ] in commands
    assert "NeuroCore Community bootstrap is complete." in stdout.getvalue()


def test_bootstrap_preserves_existing_env_by_default(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    operator_home = configure_operator_home(monkeypatch, tmp_path)
    operator_home.mkdir(parents=True, exist_ok=True)
    env_path = operator_home / ".env"
    env_path.write_text("NEUROCORE_DEFAULT_NAMESPACE=keep-me\n", encoding="utf-8")

    exit_code = BOOTSTRAP.main(
        ["--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        runner=FakeRunner(),
    )

    assert exit_code == 0
    assert (
        env_path.read_text(encoding="utf-8") == "NEUROCORE_DEFAULT_NAMESPACE=keep-me\n"
    )


def test_bootstrap_force_env_rewrites_profile(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    operator_home = configure_operator_home(monkeypatch, tmp_path)
    env_path = operator_home / ".env"
    operator_home.mkdir(parents=True, exist_ok=True)
    env_path.write_text("NEUROCORE_DEFAULT_NAMESPACE=keep-me\n", encoding="utf-8")

    exit_code = BOOTSTRAP.main(
        ["--force-env", "--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        runner=FakeRunner(),
    )

    assert exit_code == 0
    contents = env_path.read_text(encoding="utf-8")
    assert f"NEUROCORE_OPERATOR_HOME={operator_home}" in contents
    assert "NEUROCORE_DEFAULT_NAMESPACE=default" in contents
    assert "NEUROCORE_STORAGE_BACKEND=sqlite" in contents
