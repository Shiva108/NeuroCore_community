import json
from pathlib import Path

from neurocore.governance.validation import (
    ECOSYSTEM_CATEGORIES,
    REQUIRED_GUIDANCE_PHRASES,
    discover_bundle_manifest_files,
    discover_command_prompt_files,
    discover_contribution_metadata_files,
    discover_metadata_files,
    find_forbidden_local_files,
    find_secret_like_values,
    find_stale_repo_guidance,
    load_bundle_manifest_schema,
    load_contribution_metadata_schema,
    load_module_metadata_schema,
    validate_bundle_manifest,
    validate_contribution_metadata,
    validate_command_prompt_file,
    validate_contribution_structure,
    validate_extension_target,
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


def test_validate_module_metadata_reports_schema_type_errors():
    schema = load_module_metadata_schema(Path("."))
    errors = validate_module_metadata(
        {
            "name": "memory-query",
            "kind": "module",
            "description": "Query adapter and ranking support.",
            "owner": "neurocore",
            "status": "active",
            "interfaces": "library",
            "test_coverage": "pytest",
        },
        schema=schema,
        source="memory-query/module-metadata.json",
    )

    assert any("interfaces" in error for error in errors)


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


def test_find_secret_like_values_ignores_lowercase_code_assignments():
    findings = find_secret_like_values(
        'api_key = str(payload.get("api_key") or "").strip()\n'
    )

    assert findings == []


def test_find_forbidden_local_files_flags_local_runtime_artifacts(tmp_path: Path):
    (tmp_path / ".env").write_text("NEUROCORE_DEFAULT_NAMESPACE=security-lab\n")
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


def test_validate_repo_contract_requires_expected_docs(tmp_path: Path):
    (tmp_path / "README.md").write_text("# NeuroCore\n", encoding="utf-8")
    errors = validate_repo_contract(tmp_path)

    assert "Missing required file: CONTRIBUTING.md" in errors


def test_validate_repo_contract_requires_reference_stack_docs_and_templates(
    tmp_path: Path,
):
    for relative_path in (
        "README.md",
        "CONTRIBUTING.md",
        "docs/ai-assisted-setup.md",
        "docs/templates/setup-guide-template.md",
    ):
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder\n", encoding="utf-8")

    errors = validate_repo_contract(tmp_path)

    assert "Missing required file: docs/reference-stack.md" in errors
    assert "Missing required file: docs/hosted-stack.md" in errors
    assert "Missing required file: extensions/bundles/README.md" in errors
    assert "Missing required file: .github/bundle-manifest.schema.json" in errors
    assert "Missing required file: recipes/_template/README.md" in errors
    assert "Missing required file: skills/_template/metadata.json" in errors


def test_find_stale_repo_guidance_flags_docs_first_language(tmp_path: Path):
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text(
        (
            "# Repository Guidelines\n\n"
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


def test_validate_contribution_metadata_rejects_category_mismatch():
    schema = load_contribution_metadata_schema(Path("."))
    errors = validate_contribution_metadata(
        {
            "name": "Quick Capture Recipe",
            "category": "skills",
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

    assert any("category must match parent folder" in error for error in errors)


def test_ecosystem_categories_match_expected_taxonomy():
    assert ECOSYSTEM_CATEGORIES == (
        "extensions",
        "primitives",
        "recipes",
        "skills",
        "dashboards",
        "integrations",
        "schemas",
    )


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


def test_validate_command_prompt_rejects_invalid_frontmatter_values(tmp_path: Path):
    path = tmp_path / ".claude" / "commands" / "tdd.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        (
            "---\n"
            "description: Test workflow\n"
            "mode: author\n"
            "mutates_repo: sometimes\n"
            "---\n\n"
            "# TDD\n"
        ),
        encoding="utf-8",
    )

    errors = validate_command_prompt_file(tmp_path, path)

    assert any("mode must be one of: plan, execute, mixed" in error for error in errors)
    assert any("mutates_repo must be a boolean" in error for error in errors)


def test_validate_command_prompt_rejects_missing_required_frontmatter_fields(
    tmp_path: Path,
):
    path = tmp_path / ".claude" / "commands" / "tdd.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        ("---\n" "description: Test workflow\n" "---\n\n" "# TDD\n"),
        encoding="utf-8",
    )

    errors = validate_command_prompt_file(tmp_path, path)

    assert any(
        "frontmatter keys must be exactly: description, mode, mutates_repo" in error
        for error in errors
    )


def test_validate_command_prompt_rejects_non_kebab_case_filename(tmp_path: Path):
    path = tmp_path / ".claude" / "commands" / "TestPrompt.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        (
            "---\n"
            "description: Test workflow\n"
            "mode: plan\n"
            "mutates_repo: false\n"
            "---\n\n"
            "# TDD\n"
        ),
        encoding="utf-8",
    )

    errors = validate_command_prompt_file(tmp_path, path)

    assert errors == [
        ".claude/commands/TestPrompt.md: command prompt filenames must use kebab-case.md"
    ]


def test_validate_command_prompt_requires_top_level_heading(tmp_path: Path):
    path = tmp_path / ".claude" / "commands" / "tdd.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        (
            "---\n"
            "description: Test workflow\n"
            "mode: plan\n"
            "mutates_repo: false\n"
            "---\n\n"
            "No heading here.\n"
        ),
        encoding="utf-8",
    )

    errors = validate_command_prompt_file(tmp_path, path)

    assert errors == [
        ".claude/commands/tdd.md: command prompts must contain exactly one top-level '# ' heading"
    ]


def test_validate_command_prompt_requires_mutation_safety_language(tmp_path: Path):
    path = tmp_path / ".claude" / "commands" / "tdd.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        (
            "---\n"
            "description: Test workflow\n"
            "mode: execute\n"
            "mutates_repo: true\n"
            "---\n\n"
            "# TDD\n\n"
            "Run the workflow normally.\n"
        ),
        encoding="utf-8",
    )

    errors = validate_command_prompt_file(tmp_path, path)

    assert errors == [
        ".claude/commands/tdd.md: mutating command prompts must describe what is safe to auto-apply and what still requires explicit confirmation"
    ]


def test_validate_command_prompt_accepts_valid_mutating_prompt(tmp_path: Path):
    path = tmp_path / ".claude" / "commands" / "tdd.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        (
            "---\n"
            "description: Test workflow\n"
            "mode: execute\n"
            "mutates_repo: true\n"
            "---\n\n"
            "# TDD\n\n"
            "Safe to auto-apply: write the failing test, implement the narrow fix, and rerun targeted checks.\n"
            "Anything broader still requires explicit confirmation from the user.\n"
        ),
        encoding="utf-8",
    )

    assert validate_command_prompt_file(tmp_path, path) == []


def test_validate_contribution_structure_rejects_non_kebab_case_directory(
    tmp_path: Path,
):
    parent = tmp_path / "recipes" / "QuickCapture"
    parent.mkdir(parents=True)
    (parent / "README.md").write_text("# Quick Capture\n", encoding="utf-8")
    (parent / "metadata.json").write_text("{}", encoding="utf-8")

    errors = validate_contribution_structure(tmp_path, parent / "metadata.json")

    assert errors == [
        "recipes/QuickCapture: contribution directories must use kebab-case"
    ]


def test_validate_contribution_structure_requires_top_level_readme_heading(
    tmp_path: Path,
):
    parent = tmp_path / "recipes" / "quick-capture"
    parent.mkdir(parents=True)
    (parent / "README.md").write_text("Quick Capture\n", encoding="utf-8")
    (parent / "metadata.json").write_text("{}", encoding="utf-8")

    errors = validate_contribution_structure(tmp_path, parent / "metadata.json")

    assert errors == [
        "recipes/quick-capture/README.md: README.md must begin with a top-level '# ' heading"
    ]


def test_validate_contribution_structure_requires_metadata_json(tmp_path: Path):
    parent = tmp_path / "recipes" / "quick-capture"
    parent.mkdir(parents=True)
    (parent / "README.md").write_text("# Quick Capture\n", encoding="utf-8")

    errors = validate_contribution_structure(tmp_path, parent)

    assert errors == ["recipes/quick-capture: missing required metadata.json"]


def test_validate_contribution_structure_requires_skill_md_for_skills(
    tmp_path: Path,
):
    parent = tmp_path / "skills" / "daily-memory-triage"
    parent.mkdir(parents=True)
    (parent / "README.md").write_text("# Skill\n", encoding="utf-8")
    (parent / "metadata.json").write_text("{}", encoding="utf-8")

    errors = validate_contribution_structure(tmp_path, parent / "metadata.json")

    assert errors == ["skills/daily-memory-triage: missing required SKILL.md"]


def test_validate_contribution_structure_requires_utf8_readme(tmp_path: Path):
    parent = tmp_path / "recipes" / "quick-capture"
    parent.mkdir(parents=True)
    (parent / "README.md").write_bytes(b"\xff\xfe\x00")
    (parent / "metadata.json").write_text("{}", encoding="utf-8")

    errors = validate_contribution_structure(tmp_path, parent / "metadata.json")

    assert errors == [
        "recipes/quick-capture/README.md: README.md must be valid UTF-8 text"
    ]


def test_validate_bundle_manifest_accepts_repo_bundle():
    root = Path(".")
    schema = load_bundle_manifest_schema(root)
    bundle_path = root / "extensions" / "bundles" / "operator-memory-starter.json"
    manifest = json.loads(bundle_path.read_text(encoding="utf-8"))

    assert (
        validate_bundle_manifest(
            manifest,
            schema=schema,
            source=str(bundle_path.relative_to(root)),
            root=root,
        )
        == []
    )


def test_validate_bundle_manifest_rejects_invalid_items_and_flags(tmp_path: Path):
    schema_dir = tmp_path / ".github"
    schema_dir.mkdir(parents=True)
    source_schema = load_bundle_manifest_schema(Path("."))
    (schema_dir / "bundle-manifest.schema.json").write_text(
        json.dumps(source_schema),
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n[project.optional-dependencies]\ndev = []\n",
        encoding="utf-8",
    )
    bundle_dir = tmp_path / "extensions" / "bundles"
    bundle_dir.mkdir(parents=True)
    bundle_path = bundle_dir / "bad-bundle.json"
    manifest = {
        "schema_version": 1,
        "name": "Bad Bundle",
        "description": "Broken references.",
        "items": ["README.md", "recipes/missing-item", "README.md"],
        "optional_python_extras": ["semantic"],
        "required_config_flags": ["nope_flag"],
    }

    errors = validate_bundle_manifest(
        manifest,
        schema=source_schema,
        source=str(bundle_path.relative_to(tmp_path)),
        root=tmp_path,
    )

    assert any("items must not contain duplicates" in error for error in errors)
    assert any("bundle items must stay under curated surfaces" in error for error in errors)
    assert any("referenced item does not exist" in error for error in errors)
    assert any("optional_python_extras entry is not declared" in error for error in errors)
    assert any("required_config_flags entry must name a boolean" in error for error in errors)


def test_validate_extension_target_supports_contribution_dir_and_bundle():
    root = Path(".")

    contribution = validate_extension_target(root, "skills/daily-memory-triage")
    assert contribution["valid"] is True
    assert contribution["kind"] == "contribution"
    assert contribution["target"] == "skills/daily-memory-triage"
    assert contribution["checks_run"] == [
        "contribution_structure",
        "contribution_metadata",
    ]

    bundle = validate_extension_target(root, "extensions/bundles/operator-memory-starter.json")
    assert bundle["valid"] is True
    assert bundle["kind"] == "bundle"
    assert bundle["target"] == "extensions/bundles/operator-memory-starter.json"
    assert bundle["checks_run"] == ["bundle_manifest"]


def test_validate_extension_target_rejects_unknown_paths(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    result = validate_extension_target(repo_root, "missing/path")

    assert result["valid"] is False
    assert result["kind"] == "unknown"
    assert result["target"] == "missing/path"
    assert result["errors"] == ["target does not exist"]
    assert result["checks_run"] == []


def test_discover_bundle_manifest_files_finds_bundle_json_only(tmp_path: Path):
    bundle_dir = tmp_path / "extensions" / "bundles"
    bundle_dir.mkdir(parents=True)
    bundle_path = bundle_dir / "starter.json"
    bundle_path.write_text("{}", encoding="utf-8")
    (bundle_dir / "README.md").write_text("# Bundles\n", encoding="utf-8")

    discovered = discover_bundle_manifest_files(tmp_path)

    assert bundle_path in discovered
    assert bundle_dir / "README.md" not in discovered


def test_repo_contribution_templates_and_examples_validate():
    root = Path(".")
    schema = load_contribution_metadata_schema(root)
    metadata_files = discover_contribution_metadata_files(root)

    assert root / "recipes" / "_template" / "metadata.json" in metadata_files
    assert root / "skills" / "_template" / "metadata.json" in metadata_files
    assert root / "integrations" / "_template" / "metadata.json" in metadata_files
    assert (
        root / "recipes" / "quickstart-memory-capture" / "metadata.json"
        in metadata_files
    )
    assert (
        root / "recipes" / "hosted-stack-quickstart" / "metadata.json" in metadata_files
    )
    assert root / "recipes" / "mirror-hosted-proof" / "metadata.json" in metadata_files
    assert (
        root / "recipes" / "slack-slash-flow-report" / "metadata.json" in metadata_files
    )
    assert (
        root / "recipes" / "discord-slash-flow-report" / "metadata.json"
        in metadata_files
    )
    assert (
        root / "recipes" / "security-memory-review-report" / "metadata.json"
        in metadata_files
    )
    assert (
        root / "recipes" / "ops-weekly-memory-report" / "metadata.json"
        in metadata_files
    )
    assert root / "skills" / "daily-memory-triage" / "metadata.json" in metadata_files
    assert root / "integrations" / "chat-capture" / "metadata.json" in metadata_files
    assert root / "integrations" / "slack-starter" / "metadata.json" in metadata_files
    assert root / "integrations" / "discord-starter" / "metadata.json" in metadata_files
    assert (
        root / ".claude" / "commands" / "_template.md"
        in discover_command_prompt_files(root)
    )
    assert (
        root / "extensions" / "bundles" / "operator-memory-starter.json"
        in discover_bundle_manifest_files(root)
    )

    for metadata_path in metadata_files:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert (
            validate_contribution_metadata(
                metadata,
                schema=schema,
                source=str(metadata_path.relative_to(root)),
            )
            == []
        )
        assert validate_contribution_structure(root, metadata_path) == []

    for command_path in discover_command_prompt_files(root):
        assert validate_command_prompt_file(root, command_path) == []


def test_discover_metadata_files_finds_repo_targets_and_ignores_other_json(
    tmp_path: Path,
):
    metadata_dir = tmp_path / "src" / "memory"
    metadata_dir.mkdir(parents=True)
    fixture_dir = tmp_path / "tests" / "fixtures" / "metadata"
    fixture_dir.mkdir(parents=True)
    (metadata_dir / "module-metadata.json").write_text("{}", encoding="utf-8")
    (fixture_dir / "sample-module.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src" / "memory" / "other.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".git" / "ignored").mkdir(parents=True)
    (tmp_path / ".git" / "ignored" / "module-metadata.json").write_text(
        "{}", encoding="utf-8"
    )

    discovered = discover_metadata_files(tmp_path)

    assert metadata_dir / "module-metadata.json" in discovered
    assert fixture_dir / "sample-module.json" in discovered
    assert tmp_path / "src" / "memory" / "other.json" not in discovered


def test_validate_module_metadata_accepts_sample_fixture():
    root = Path(".")
    schema = load_module_metadata_schema(root)
    fixture_path = root / "tests" / "fixtures" / "metadata" / "sample-module.json"
    metadata = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert (
        validate_module_metadata(metadata, schema=schema, source=str(fixture_path))
        == []
    )
