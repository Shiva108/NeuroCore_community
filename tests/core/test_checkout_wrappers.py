import importlib.util
from pathlib import Path


def _load_module(script_name: str, module_name: str):
    module_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    spec.loader.exec_module(module)
    return module


NEUROCORE_CHECKOUT = _load_module("neurocore_checkout.py", "neurocore_checkout_module")
VALIDATE_CHECKOUT = _load_module("validate_checkout.py", "validate_checkout_module")


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
    monkeypatch.setenv("NEUROCORE_OPERATOR_HOME", str(tmp_path / ".operator-state"))
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
