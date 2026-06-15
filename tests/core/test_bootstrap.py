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
    (tmp_path / ".env.mirror.example").write_text(
        "\n".join(
            [
                "NEUROCORE_OPERATOR_HOME=~/.local/state/neurocore",
                "NEUROCORE_DEFAULT_NAMESPACE=security-lab",
                "NEUROCORE_ALLOWED_BUCKETS=recon,targets,findings,payloads,reports,agents,ops",
                "NEUROCORE_DEFAULT_SENSITIVITY=restricted",
                "NEUROCORE_STORAGE_BACKEND=mirror",
                "NEUROCORE_MIRROR_READ_PREFERENCE=local",
                "NEUROCORE_MIRROR_SEALED_MODE=full",
                "NEUROCORE_PRIMARY_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore.db",
                "NEUROCORE_SEALED_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore-sealed.db",
                "NEUROCORE_ENABLE_HTTP_ADAPTER=true",
                "NEUROCORE_ENABLE_MCP_ADAPTER=true",
                "NEUROCORE_ENABLE_DASHBOARD=true",
                "NEUROCORE_PRODUCTION_BACKEND_PROVIDER=supabase",
                "NEUROCORE_PRODUCTION_DATABASE_URL=postgresql://primary-host.example:5432/neurocore",
                "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL=postgresql://sealed-host.example:5432/neurocore",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.hosted.example").write_text(
        "\n".join(
            [
                "NEUROCORE_OPERATOR_HOME=~/.local/state/neurocore",
                "NEUROCORE_DEFAULT_NAMESPACE=production",
                "NEUROCORE_ALLOWED_BUCKETS=work,research,planning,ops",
                "NEUROCORE_DEFAULT_SENSITIVITY=standard",
                "NEUROCORE_STORAGE_BACKEND=postgres",
                "NEUROCORE_PRIMARY_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore.db",
                "NEUROCORE_SEALED_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore-sealed.db",
                "NEUROCORE_ENABLE_HTTP_ADAPTER=true",
                "NEUROCORE_ENABLE_MCP_ADAPTER=true",
                "NEUROCORE_ENABLE_DASHBOARD=true",
                "NEUROCORE_PRODUCTION_BACKEND_PROVIDER=supabase",
                "NEUROCORE_PRODUCTION_DATABASE_URL=postgresql://primary-host.example:5432/neurocore",
                "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL=postgresql://sealed-host.example:5432/neurocore",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.security-operator.example").write_text(
        "\n".join(
            [
                "NEUROCORE_OPERATOR_HOME=~/.local/state/neurocore",
                "NEUROCORE_DEFAULT_NAMESPACE=security-lab",
                "NEUROCORE_ALLOWED_BUCKETS=recon,targets,findings,payloads,reports,agents,ops",
                "NEUROCORE_DEFAULT_SENSITIVITY=restricted",
                "NEUROCORE_STORAGE_BACKEND=mirror",
                "NEUROCORE_MIRROR_READ_PREFERENCE=local",
                "NEUROCORE_MIRROR_SEALED_MODE=full",
                "NEUROCORE_PRIMARY_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore.db",
                "NEUROCORE_SEALED_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore-sealed.db",
                "NEUROCORE_SEMANTIC_BACKEND=sentence-transformers",
                "NEUROCORE_ENABLE_ADMIN_SURFACE=true",
                "NEUROCORE_ENABLE_HTTP_ADAPTER=true",
                "NEUROCORE_ENABLE_MCP_ADAPTER=true",
                "NEUROCORE_ENABLE_DASHBOARD=true",
                "NEUROCORE_ENABLE_BACKGROUND_SUMMARIZATION=false",
                "NEUROCORE_ENABLE_MULTI_MODEL_CONSENSUS=false",
                "NEUROCORE_PRODUCTION_BACKEND_PROVIDER=supabase",
                "NEUROCORE_PRODUCTION_DATABASE_URL=postgresql://primary-host.example:5432/neurocore",
                "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL=postgresql://sealed-host.example:5432/neurocore",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "secrets.json.example").write_text("{}", encoding="utf-8")
    (tmp_path / "preferences.json.example").write_text("{}", encoding="utf-8")
    (tmp_path / ".githooks").mkdir()
    (tmp_path / ".githooks" / "pre-commit").write_text(
        "#!/usr/bin/env sh\npython scripts/validate_checkout.py\n",
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "security_workflow.py").write_text(
        "def print_readiness_summary(*, repo_root, env, stdout):\n"
        "    print(\n"
        "        'Readiness summary: semantic=ready; query=ready; report=not ready',\n"
        "        file=stdout,\n"
        "    )\n"
        "    print('Report prerequisites still missing:', file=stdout)\n"
        "    print('- Consensus reporting disabled', file=stdout)\n"
        "    print(\n"
        "        'Local-only report generation can use the bundled mock provider at http://127.0.0.1:8787/v1.',\n"
        "        file=stdout,\n"
        "    )\n",
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
    assert not (tmp_path / "data").exists()
    assert (operator_home / ".env").exists()
    assert (operator_home / "secrets.json").exists()
    assert (operator_home / "preferences.json").exists()
    assert (operator_home / "data").exists()

    commands = [entry["command"] for entry in runner.commands]
    assert [
        str(BOOTSTRAP._venv_python_path(tmp_path / ".venv")),
        "-m",
        "pip",
        "install",
        "-e",
        ".[dev,semantic]",
    ] in commands
    assert [
        "git",
        "config",
        "core.hooksPath",
        str(tmp_path / ".githooks"),
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
    assert "NeuroCore bootstrap is complete." in stdout.getvalue()
    assert (
        "Readiness summary: semantic=ready; query=ready; report=not ready"
        in stdout.getvalue()
    )
    assert "mock provider at http://127.0.0.1:8787/v1" in stdout.getvalue()


def test_bootstrap_preserves_existing_env_by_default(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    operator_home = configure_operator_home(monkeypatch, tmp_path)
    operator_home.mkdir(parents=True, exist_ok=True)
    env_path = operator_home / ".env"
    env_path.write_text("NEUROCORE_DEFAULT_NAMESPACE=keep-me\n", encoding="utf-8")
    runner = FakeRunner()

    exit_code = BOOTSTRAP.main(
        ["--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        runner=runner,
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
    assert "NEUROCORE_DEFAULT_NAMESPACE=security-lab" in contents
    assert "NEUROCORE_STORAGE_BACKEND=mirror" in contents
    assert "NEUROCORE_MIRROR_SEALED_MODE=full" in contents


def test_bootstrap_generates_expected_security_profile_env(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    operator_home = configure_operator_home(monkeypatch, tmp_path)
    runner = FakeRunner()

    exit_code = BOOTSTRAP.main(
        [],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        runner=runner,
    )

    assert exit_code == 0
    contents = (operator_home / ".env").read_text(encoding="utf-8")
    assert f"NEUROCORE_OPERATOR_HOME={operator_home}" in contents
    assert "NEUROCORE_DEFAULT_NAMESPACE=security-lab" in contents
    assert (
        "NEUROCORE_ALLOWED_BUCKETS=recon,targets,findings,payloads,reports,agents,ops"
        in contents
    )
    assert (
        "NEUROCORE_PRIMARY_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore.db"
        in contents
    )
    assert "NEUROCORE_STORAGE_BACKEND=mirror" in contents
    assert "NEUROCORE_MIRROR_SEALED_MODE=full" in contents
    assert "NEUROCORE_ENABLE_HTTP_ADAPTER=true" in contents
    assert "NEUROCORE_ENABLE_MCP_ADAPTER=true" in contents
    assert "NEUROCORE_ENABLE_DASHBOARD=true" in contents
    assert "NEUROCORE_PRODUCTION_BACKEND_PROVIDER=supabase" in contents
    assert (
        "NEUROCORE_PRODUCTION_DATABASE_URL=postgresql://primary-host.example:5432/neurocore"
        in contents
    )
    assert (
        "NEUROCORE_PRODUCTION_SEALED_DATABASE_URL=postgresql://sealed-host.example:5432/neurocore"
        in contents
    )
    assert "NEUROCORE_SEMANTIC_BACKEND=sentence-transformers" in contents
    verify_commands = [
        entry for entry in runner.commands if entry["command"][1:3] == ["-m", "pytest"]
    ]
    assert verify_commands
    assert verify_commands[0]["env"]["NEUROCORE_DEFAULT_NAMESPACE"] == "security-lab"
    assert verify_commands[0]["env"]["NEUROCORE_PRIMARY_STORE_PATH"] == str(
        operator_home / "data" / "neurocore.db"
    )


def test_bootstrap_can_write_hosted_profile_env(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    operator_home = configure_operator_home(monkeypatch, tmp_path)

    exit_code = BOOTSTRAP.main(
        ["--profile", "hosted", "--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        runner=FakeRunner(),
    )

    assert exit_code == 0
    contents = (operator_home / ".env").read_text(encoding="utf-8")
    assert "NEUROCORE_STORAGE_BACKEND=postgres" in contents
    assert "NEUROCORE_ENABLE_HTTP_ADAPTER=true" in contents
    assert "NEUROCORE_PRODUCTION_BACKEND_PROVIDER=supabase" in contents


def test_bootstrap_can_write_mirror_profile_env(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    operator_home = configure_operator_home(monkeypatch, tmp_path)

    exit_code = BOOTSTRAP.main(
        ["--profile", "mirror", "--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        runner=FakeRunner(),
    )

    assert exit_code == 0
    contents = (operator_home / ".env").read_text(encoding="utf-8")
    assert "NEUROCORE_STORAGE_BACKEND=mirror" in contents
    assert "NEUROCORE_MIRROR_READ_PREFERENCE=local" in contents
    assert "NEUROCORE_MIRROR_SEALED_MODE=full" in contents
    assert "NEUROCORE_PRODUCTION_BACKEND_PROVIDER=supabase" in contents


def test_makefile_setup_target_uses_bootstrap_command():
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(
        encoding="utf-8"
    )

    assert "setup:\n\tpython scripts/bootstrap.py\n" in makefile


def test_makefile_validate_target_uses_checkout_wrapper():
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(
        encoding="utf-8"
    )

    assert "validate:\n\tpython scripts/validate_checkout.py\n" in makefile


def test_makefile_lint_target_includes_scripts():
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(
        encoding="utf-8"
    )

    assert "black --check src tests scripts" in makefile
    assert "flake8 src tests scripts" in makefile


def test_bootstrap_reports_readable_install_failures(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    configure_operator_home(monkeypatch, tmp_path)
    stderr = io.StringIO()

    exit_code = BOOTSTRAP.main(
        ["--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=stderr,
        runner=FakeRunner(
            fail_on=lambda command: command[1:5] == ["-m", "pip", "install", "-e"]
        ),
    )

    assert exit_code == 1
    assert (tmp_path / ".venv").exists()
    message = stderr.getvalue()
    assert "Bootstrap failed" in message
    assert "Failed command:" in message
    assert "editable install" in message


def test_bootstrap_wizard_rejects_invalid_namespace(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    configure_operator_home(monkeypatch, tmp_path)
    stderr = io.StringIO()

    exit_code = BOOTSTRAP.main(
        ["--wizard", "--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=stderr,
        input_fn=lambda prompt: "Bad Namespace",
        runner=FakeRunner(),
    )

    assert exit_code == 1
    assert "Namespace must start" in stderr.getvalue()


def test_bootstrap_recreates_incomplete_virtualenv(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    configure_operator_home(monkeypatch, tmp_path)
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "stale.txt").write_text("stale", encoding="utf-8")
    runner = FakeRunner()
    stdout = io.StringIO()

    exit_code = BOOTSTRAP.main(
        ["--skip-verify"],
        repo_root=tmp_path,
        stdout=stdout,
        stderr=io.StringIO(),
        runner=runner,
    )

    assert exit_code == 0
    assert not (venv_dir / "stale.txt").exists()
    assert "recreating it" in stdout.getvalue()


def test_bootstrap_requires_committed_pre_commit_hook(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    configure_operator_home(monkeypatch, tmp_path)
    (tmp_path / ".githooks" / "pre-commit").unlink()
    stderr = io.StringIO()

    exit_code = BOOTSTRAP.main(
        ["--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=stderr,
        runner=FakeRunner(),
    )

    assert exit_code == 1
    assert "Missing required Git hook" in stderr.getvalue()


def test_bootstrap_migrates_legacy_operator_state(tmp_path: Path, monkeypatch):
    create_repo_scaffold(tmp_path)
    operator_home = configure_operator_home(monkeypatch, tmp_path)
    legacy_env = tmp_path / ".env"
    legacy_env.write_text("NEUROCORE_DEFAULT_NAMESPACE=legacy\n", encoding="utf-8")
    (tmp_path / "secrets.json").write_text("{}", encoding="utf-8")
    (tmp_path / "preferences.json").write_text("{}", encoding="utf-8")
    legacy_data_dir = tmp_path / "data"
    legacy_data_dir.mkdir()
    (legacy_data_dir / "neurocore.db").write_text("db", encoding="utf-8")

    exit_code = BOOTSTRAP.main(
        ["--skip-verify"],
        repo_root=tmp_path,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        runner=FakeRunner(),
    )

    assert exit_code == 0
    assert not legacy_env.exists()
    assert not legacy_data_dir.exists()
    assert (operator_home / ".env").exists()
    assert (operator_home / "secrets.json").exists()
    assert (operator_home / "preferences.json").exists()
    assert (operator_home / "data" / "neurocore.db").exists()
