from pathlib import Path

from neurocore.governance.validation import (
    REQUIRED_GUIDANCE_PHRASES,
    discover_command_prompt_files,
    find_forbidden_local_files,
    find_secret_like_values,
    find_stale_repo_guidance,
    load_contribution_metadata_schema,
    load_module_metadata_schema,
    validate_command_prompt_file,
    validate_contribution_metadata,
    validate_module_metadata,
    validate_repo_contract,
)


def test_validate_module_metadata_reports_missing_required_fields():
    schema = load_module_metadata_schema(Path("."))
    errors = validate_module_metadata(
        {
            "name": "memory-query",
            "kind": "module",
        },
        schema=schema,
        source="memory-query/module-metadata.json",
    )

    assert any("memory-query/module-metadata.json" in error for error in errors)
    assert any("description" in error for error in errors)
    assert any("test_coverage" in error for error in errors)


def test_find_secret_like_values_detects_obvious_secret_patterns():
    findings = find_secret_like_values(
        f"AWS_KEY={'AKIA' + 'IOSFODNN7EXAMPLE'}\n{'SECRET' + '_KEY'}=super-secret-value\n"
    )

    assert findings


def test_find_secret_like_values_ignores_placeholders_and_code_references():
    findings = find_secret_like_values(
        "API_KEY=\napi_key=config.consensus_api_key,\nSECRET_KEY=placeholder\n"
    )

    assert findings == []


def test_find_forbidden_local_files_flags_local_runtime_artifacts(tmp_path: Path):
    (tmp_path / ".env").write_text("NEUROCORE_DEFAULT_NAMESPACE=community\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "neurocore.db").write_text("db", encoding="utf-8")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "report.md").write_text("report", encoding="utf-8")

    findings = find_forbidden_local_files(tmp_path)

    assert ".env: local-only file must not exist in the repository checkout" in findings
    assert (
        "data/neurocore.db: local runtime artifact must not exist in the repository checkout"
        in findings
    )
    assert (
        "outputs/report.md: local runtime artifact must not exist in the repository checkout"
        in findings
    )


def test_validate_repo_contract_requires_expected_community_docs(tmp_path: Path):
    (tmp_path / "README.md").write_text("# NeuroCore Community\n", encoding="utf-8")
    errors = validate_repo_contract(tmp_path)

    assert "Missing required file: CONTRIBUTING.md" in errors
    assert "Missing required file: ROADMAP.md" in errors
    assert "Missing required file: SECURITY.md" in errors


def test_validate_repo_contract_accepts_minimal_community_layout(tmp_path: Path):
    required_files = (
        "README.md",
        "CONTRIBUTING.md",
        "ROADMAP.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "LICENSE",
        ".env.example",
        "docs/ssd/architecture.md",
        "docs/ssd/specification.md",
        "docs/ssd/implementation-plan.md",
        "docs/ssd/source-matrix.md",
    )
    for relative_path in required_files:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder\n", encoding="utf-8")

    assert validate_repo_contract(tmp_path) == []


def test_find_stale_repo_guidance_flags_docs_first_language(tmp_path: Path):
    readme_path = tmp_path / "README.md"
    readme_path.write_text(
        (
            "# NeuroCore\n\n"
            "This repository is currently in a docs-first planning phase.\n"
            "There are no application source files yet.\n"
        ),
        encoding="utf-8",
    )

    findings = find_stale_repo_guidance(
        tmp_path,
        required_phrases=REQUIRED_GUIDANCE_PHRASES,
    )

    assert any("docs-first planning phase" in finding for finding in findings)


def test_validate_contribution_metadata_accepts_minimal_recipe_contract():
    schema = load_contribution_metadata_schema(Path("."))
    errors = validate_contribution_metadata(
        {
            "name": "Quick Capture Recipe",
            "category": "recipes",
            "description": "Capture notes with the NeuroCore CLI.",
            "owner": {"name": "NeuroCore"},
            "version": "1.0.0",
            "requires": {"neurocore": True, "tools": ["Python 3.11+"]},
            "tags": ["capture"],
            "difficulty": "beginner",
            "estimated_time": "10 minutes",
        },
        schema=schema,
        source="recipes/quick-capture/metadata.json",
    )

    assert errors == []


def test_discover_command_prompt_files_ignores_readme(tmp_path: Path):
    command_dir = tmp_path / ".claude" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "README.md").write_text("# Commands\n", encoding="utf-8")
    (command_dir / "tdd.md").write_text(
        (
            "---\n"
            "description: Test workflow\n"
            "mode: execute\n"
            "mutates_repo: true\n"
            "---\n\n"
            "# TDD\n\n"
            "Safe to auto-apply: tests and minimal implementation.\n"
            "Anything broader still requires explicit confirmation.\n"
        ),
        encoding="utf-8",
    )

    discovered = discover_command_prompt_files(tmp_path)

    assert command_dir / "tdd.md" in discovered
    assert command_dir / "README.md" not in discovered


def test_validate_command_prompt_requires_structured_frontmatter(tmp_path: Path):
    path = tmp_path / ".claude" / "commands" / "tdd.md"
    path.parent.mkdir(parents=True)
    path.write_text("# TDD\n", encoding="utf-8")

    errors = validate_command_prompt_file(tmp_path, path)

    assert errors == [
        ".claude/commands/tdd.md: command prompts must begin with YAML frontmatter"
    ]
