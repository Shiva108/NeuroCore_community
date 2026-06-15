from pathlib import Path


def test_env_example_defaults_to_mirror_first_operator_path():
    contents = (Path(".") / ".env.example").read_text(encoding="utf-8")

    assert "NEUROCORE_OPERATOR_HOME=~/.local/state/neurocore" in contents
    assert "NEUROCORE_STORAGE_BACKEND=mirror" in contents
    assert "NEUROCORE_MIRROR_SEALED_MODE=full" in contents
    assert "NEUROCORE_PRODUCTION_BACKEND_PROVIDER=supabase" in contents
    assert (
        "NEUROCORE_PRIMARY_STORE_PATH=${NEUROCORE_OPERATOR_HOME}/data/neurocore.db"
        in contents
    )
    assert "NEUROCORE_STORAGE_BACKEND=in_memory" not in contents


def test_readme_presents_mirror_first_operator_path():
    contents = (Path(".") / "README.md").read_text(encoding="utf-8")

    assert "mirror-first bootstrap script" in contents
    assert "Local-only SQLite remains available as an explicit fallback" in contents


def test_hosted_stack_describes_remote_runtime_contract():
    contents = (Path(".") / "docs" / "hosted-stack.md").read_text(encoding="utf-8")

    assert "default operator path" in contents
    assert "hosted NeuroCore HTTP/MCP service" in contents
    assert "deferred guidance" not in contents
