from pathlib import Path

from neurocore.core.operator_state import (
    OPERATOR_HOME_ENV,
    load_env_file,
    load_operator_env,
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
        base_env={"HOME": str(tmp_path / "home")},
        stderr=capture,
    )

    assert legacy is True
    assert values["NEUROCORE_DEFAULT_NAMESPACE"] == "legacy"
    assert any(
        "repo-local operator state is deprecated" in line for line in capture.lines
    )
