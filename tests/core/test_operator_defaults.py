from pathlib import Path


def test_env_example_defaults_to_sqlite_operator_path():
    contents = (Path(".") / ".env.example").read_text(encoding="utf-8")

    assert "NEUROCORE_OPERATOR_HOME=~/.local/state/neurocore" in contents
    assert "NEUROCORE_STORAGE_BACKEND=sqlite" in contents
    assert (
        "NEUROCORE_PRIMARY_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore.db"
        in contents
    )
    assert ".env.hosted.example" not in contents
    assert ".env.mirror.example" not in contents


def test_readme_presents_community_quickstart():
    contents = (Path(".") / "README.md").read_text(encoding="utf-8")

    assert "NeuroCore Community" in contents
    assert "python scripts/bootstrap.py" in contents
    assert "clean public history" in contents


def test_implementation_plan_describes_public_repo_boundaries():
    contents = (Path(".") / "docs" / "ssd" / "implementation-plan.md").read_text(
        encoding="utf-8"
    )

    assert "community repository" in contents
    assert "Intentionally Excluded" in contents
