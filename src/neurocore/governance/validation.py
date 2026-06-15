"""Repository governance validation utilities for NeuroCore."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import fields
from pathlib import Path
from typing import get_type_hints

from jsonschema import Draft202012Validator

from neurocore.core.config import NeuroCoreConfig

ECOSYSTEM_CATEGORIES = (
    "extensions",
    "primitives",
    "recipes",
    "skills",
    "dashboards",
    "integrations",
    "schemas",
)
STRICT_CONTRIBUTION_CATEGORIES = (
    "extensions",
    "recipes",
    "skills",
    "dashboards",
)
REQUIRED_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "docs/ai-assisted-setup.md",
    "docs/templates/setup-guide-template.md",
    "docs/reference-stack.md",
    "docs/hosted-stack.md",
    "extensions/bundles/README.md",
    ".github/bundle-manifest.schema.json",
)
REQUIRED_TEMPLATE_FILES = tuple(
    f"{category}/_template/{filename}"
    for category in ECOSYSTEM_CATEGORIES
    for filename in ("README.md", "metadata.json")
) + (".claude/commands/_template.md",)
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?m)^\s*(SECRET[_-]?KEY|API[_-]?KEY)\s*=\s*(.+)$"),
)
REQUIRED_GUIDANCE_PHRASES = (
    "docs-first planning phase",
    "There are no application source files yet",
    "No build, test, or local run commands are defined in this repository yet",
    "Until a runtime is selected",
    "not initialized as a Git repository yet",
)
GUIDANCE_FILES = (
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
    "docs/ai-assisted-setup.md",
    "docs/setup.md",
)
IGNORED_SCAN_PARTS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
}
FORBIDDEN_LOCAL_PATHS = (
    ".env",
    "secrets.json",
    "preferences.json",
    "token.json",
)
FORBIDDEN_LOCAL_PREFIXES = (
    "data/",
    "outputs/",
)
KEBAB_CASE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMAND_PROMPT_REQUIRED_FIELDS = ("description", "mode", "mutates_repo")
COMMAND_PROMPT_MODES = ("plan", "execute", "mixed")
COMMAND_PROMPT_SAFETY_PHRASES = ("safe to auto-apply", "explicit confirmation")
CONTRIBUTION_REQUIREMENTS = {
    "extensions": ("README.md", "metadata.json"),
    "recipes": ("README.md", "metadata.json"),
    "skills": ("README.md", "metadata.json", "SKILL.md"),
    "dashboards": ("README.md", "metadata.json"),
}
BUNDLE_DIR = Path("extensions") / "bundles"
ALLOWED_BUNDLE_ITEM_CATEGORIES = set(ECOSYSTEM_CATEGORIES)


def load_module_metadata_schema(root: Path) -> dict[str, object]:
    schema_path = root / ".github" / "module-metadata.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def load_contribution_metadata_schema(root: Path) -> dict[str, object]:
    schema_path = root / ".github" / "contribution-metadata.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def load_bundle_manifest_schema(root: Path) -> dict[str, object]:
    schema_path = root / ".github" / "bundle-manifest.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_module_metadata(
    metadata: dict[str, object],
    *,
    schema: dict[str, object],
    source: str,
) -> list[str]:
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(metadata), key=str):
        field = ".".join(str(part) for part in error.absolute_path)
        suffix = f" (field: {field})" if field else ""
        errors.append(f"{source}: {error.message}{suffix}")
    return errors


def validate_contribution_metadata(
    metadata: dict[str, object],
    *,
    schema: dict[str, object],
    source: str,
) -> list[str]:
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(metadata), key=str):
        field = ".".join(str(part) for part in error.absolute_path)
        suffix = f" (field: {field})" if field else ""
        errors.append(f"{source}: {error.message}{suffix}")

    expected_category = Path(source).parts[0]
    actual_category = metadata.get("category")
    if (
        expected_category in ECOSYSTEM_CATEGORIES
        and actual_category != expected_category
    ):
        errors.append(
            f"{source}: category must match parent folder ({expected_category})"
        )
    if (
        actual_category in {"extensions", "primitives"}
        and metadata.get("curation") != "curated"
    ):
        errors.append(f"{source}: curated categories must declare curation=curated")
    return errors


def validate_bundle_manifest(
    manifest: dict[str, object],
    *,
    schema: dict[str, object],
    source: str,
    root: Path,
) -> list[str]:
    root = root.resolve()
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(manifest), key=str):
        field = ".".join(str(part) for part in error.absolute_path)
        suffix = f" (field: {field})" if field else ""
        errors.append(f"{source}: {error.message}{suffix}")

    source_path = Path(source)
    if source_path.parent != BUNDLE_DIR:
        errors.append(f"{source}: bundle manifests must live under {BUNDLE_DIR}/")
    if source_path.stem and not _is_kebab_case(source_path.stem):
        errors.append(f"{source}: bundle manifest filenames must use kebab-case.json")

    items = manifest.get("items")
    if isinstance(items, list):
        seen: set[str] = set()
        for item in items:
            item_path = str(item)
            if item_path in seen:
                errors.append(f"{source}: items must not contain duplicates ({item_path})")
                continue
            seen.add(item_path)
            try:
                resolved = _resolve_repo_relative_path(root, item_path)
            except ValueError as exc:
                errors.append(f"{source}: {exc}")
                continue
            relative = resolved.relative_to(root)
            if not relative.parts or relative.parts[0] not in ALLOWED_BUNDLE_ITEM_CATEGORIES:
                errors.append(
                    f"{source}: bundle items must stay under curated surfaces ({', '.join(ECOSYSTEM_CATEGORIES)})"
                )
                continue
            if not resolved.exists():
                errors.append(f"{source}: referenced item does not exist ({item_path})")

    optional_extras = manifest.get("optional_python_extras")
    if isinstance(optional_extras, list):
        declared_extras = _load_pyproject_optional_extras(root)
        for extra in optional_extras:
            if str(extra) not in declared_extras:
                errors.append(
                    f"{source}: optional_python_extras entry is not declared in pyproject.toml ({extra})"
                )

    required_config_flags = manifest.get("required_config_flags")
    if isinstance(required_config_flags, list):
        boolean_flags = _load_boolean_config_flags()
        for flag in required_config_flags:
            if str(flag) not in boolean_flags:
                errors.append(
                    f"{source}: required_config_flags entry must name a boolean NeuroCoreConfig field ({flag})"
                )

    return errors


def discover_metadata_files(root: Path) -> list[Path]:
    discovered: set[Path] = set()
    for path in root.rglob("module-metadata.json"):
        if _should_ignore_path(path.relative_to(root)):
            continue
        discovered.add(path)

    fixture_dir = root / "tests" / "fixtures" / "metadata"
    if fixture_dir.exists():
        for path in fixture_dir.rglob("*.json"):
            if _should_ignore_path(path.relative_to(root)):
                continue
            discovered.add(path)

    return sorted(discovered)


def discover_contribution_metadata_files(root: Path) -> list[Path]:
    discovered: list[Path] = []
    for category in ECOSYSTEM_CATEGORIES:
        category_dir = root / category
        if not category_dir.exists():
            continue
        for path in category_dir.rglob("metadata.json"):
            if _should_ignore_path(path.relative_to(root)):
                continue
            discovered.append(path)
    return sorted(discovered)


def discover_contribution_directories(root: Path) -> list[Path]:
    discovered: list[Path] = []
    for category in STRICT_CONTRIBUTION_CATEGORIES:
        category_dir = root / category
        if not category_dir.exists():
            continue
        for path in sorted(category_dir.iterdir()):
            if not path.is_dir():
                continue
            if _should_ignore_path(path.relative_to(root)):
                continue
            if path.relative_to(root) == BUNDLE_DIR:
                continue
            discovered.append(path)
    return discovered


def discover_bundle_manifest_files(root: Path) -> list[Path]:
    bundle_dir = root / BUNDLE_DIR
    if not bundle_dir.exists():
        return []
    return sorted(
        path
        for path in bundle_dir.glob("*.json")
        if path.is_file() and not _should_ignore_path(path.relative_to(root))
    )


def discover_command_prompt_files(root: Path) -> list[Path]:
    command_dir = root / ".claude" / "commands"
    if not command_dir.exists():
        return []
    return sorted(path for path in command_dir.glob("*.md") if path.name != "README.md")


def find_secret_like_values(text: str) -> list[str]:
    findings: list[str] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if match.lastindex == 2:
                value = match.group(2).strip()
                if _looks_secret_assignment_value(value):
                    findings.append(match.group(0))
                continue
            findings.append(match.group(0))
    return findings


def find_stale_repo_guidance(
    root: Path, *, required_phrases: tuple[str, ...] = REQUIRED_GUIDANCE_PHRASES
) -> list[str]:
    findings: list[str] = []
    for relative_path in GUIDANCE_FILES:
        path = root / relative_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in required_phrases:
            if phrase in text:
                findings.append(
                    f"{relative_path}: stale guidance phrase detected: {phrase}"
                )
    return findings


def validate_repo_contract(root: Path) -> list[str]:
    errors: list[str] = []
    for relative_path in REQUIRED_FILES + REQUIRED_TEMPLATE_FILES:
        if not (root / relative_path).exists():
            errors.append(f"Missing required file: {relative_path}")
    errors.extend(find_stale_repo_guidance(root))
    errors.extend(find_forbidden_local_files(root))
    return errors


def find_forbidden_local_files(root: Path) -> list[str]:
    findings: list[str] = []
    for relative_path in FORBIDDEN_LOCAL_PATHS:
        if (root / relative_path).exists():
            findings.append(
                f"{relative_path}: local-only file must not exist in the repository checkout"
            )
    for prefix in FORBIDDEN_LOCAL_PREFIXES:
        prefix_path = root / prefix.rstrip("/")
        if not prefix_path.exists():
            continue
        for path in sorted(prefix_path.rglob("*")):
            if path.is_file():
                findings.append(
                    f"{path.relative_to(root)}: local runtime artifact must not exist in the repository checkout"
                )
    return findings


def validate_contribution_structure(root: Path, metadata_path: Path) -> list[str]:
    errors: list[str] = []
    parent = metadata_path if metadata_path.is_dir() else metadata_path.parent
    relative_parent = parent.relative_to(root)
    category = relative_parent.parts[0]
    if category not in STRICT_CONTRIBUTION_CATEGORIES:
        return errors
    if relative_parent == BUNDLE_DIR:
        return errors

    if parent.name != "_template" and not _is_kebab_case(parent.name):
        errors.append(
            f"{relative_parent}: contribution directories must use kebab-case"
        )
        return errors

    for filename in CONTRIBUTION_REQUIREMENTS[category]:
        file_path = parent / filename
        relative_file = file_path.relative_to(root)
        if not file_path.exists():
            errors.append(f"{relative_parent}: missing required {filename}")
            continue
        if filename.endswith(".json"):
            try:
                json.loads(file_path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                errors.append(f"{relative_file}: {filename} must be valid UTF-8 text")
            except json.JSONDecodeError as exc:
                errors.append(f"{relative_file}: invalid JSON ({exc.msg})")
            continue

        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{relative_file}: {filename} must be valid UTF-8 text")
            continue

        if filename == "README.md" and not _has_single_top_level_heading(text):
            errors.append(
                f"{relative_file}: README.md must begin with a top-level '# ' heading"
            )
    return errors


def validate_command_prompt_file(root: Path, path: Path) -> list[str]:
    errors: list[str] = []
    relative_path = str(path.relative_to(root))

    if path.stem != "_template" and not _is_kebab_case(path.stem):
        return [f"{relative_path}: command prompt filenames must use kebab-case.md"]

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{relative_path}: command prompts must be valid UTF-8 text"]

    frontmatter, body, frontmatter_errors = _parse_command_frontmatter(text)
    if frontmatter_errors:
        return [f"{relative_path}: {error}" for error in frontmatter_errors]

    keys = tuple(frontmatter)
    if tuple(sorted(keys)) != tuple(sorted(COMMAND_PROMPT_REQUIRED_FIELDS)):
        errors.append(
            f"{relative_path}: frontmatter keys must be exactly: {', '.join(COMMAND_PROMPT_REQUIRED_FIELDS)}"
        )

    mode = frontmatter.get("mode")
    if mode not in COMMAND_PROMPT_MODES:
        errors.append(
            f"{relative_path}: mode must be one of: {', '.join(COMMAND_PROMPT_MODES)}"
        )

    mutates_repo = frontmatter.get("mutates_repo")
    if not isinstance(mutates_repo, bool):
        errors.append(f"{relative_path}: mutates_repo must be a boolean")

    if (
        not isinstance(frontmatter.get("description"), str)
        or not frontmatter.get("description", "").strip()
    ):
        errors.append(f"{relative_path}: description must be a non-empty string")

    if not _has_single_top_level_heading(body):
        errors.append(
            f"{relative_path}: command prompts must contain exactly one top-level '# ' heading"
        )

    if mutates_repo is True and not _has_mutation_safety_language(body):
        errors.append(
            f"{relative_path}: mutating command prompts must describe what is safe to auto-apply and what still requires explicit confirmation"
        )

    return errors


def main(root: str = ".") -> int:
    repo_root = Path(root)
    errors = validate_repo_contract(repo_root)
    schema = load_module_metadata_schema(repo_root)
    contribution_schema = load_contribution_metadata_schema(repo_root)
    bundle_schema = load_bundle_manifest_schema(repo_root)

    for metadata_path in discover_metadata_files(repo_root):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{metadata_path}: invalid JSON ({exc.msg})")
            continue
        errors.extend(
            validate_module_metadata(
                metadata,
                schema=schema,
                source=str(metadata_path.relative_to(repo_root)),
            )
        )

    for metadata_path in discover_contribution_metadata_files(repo_root):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{metadata_path}: invalid JSON ({exc.msg})")
            continue
        errors.extend(
            validate_contribution_metadata(
                metadata,
                schema=contribution_schema,
                source=str(metadata_path.relative_to(repo_root)),
            )
        )

    for contribution_dir in discover_contribution_directories(repo_root):
        errors.extend(validate_contribution_structure(repo_root, contribution_dir))

    for bundle_path in discover_bundle_manifest_files(repo_root):
        try:
            manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{bundle_path}: invalid JSON ({exc.msg})")
            continue
        errors.extend(
            validate_bundle_manifest(
                manifest,
                schema=bundle_schema,
                source=str(bundle_path.relative_to(repo_root)),
                root=repo_root,
            )
        )

    for command_path in discover_command_prompt_files(repo_root):
        errors.extend(validate_command_prompt_file(repo_root, command_path))

    findings: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if _should_ignore_path(path.relative_to(repo_root)):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(
            f"{path.relative_to(repo_root)}: {value}"
            for value in find_secret_like_values(text)
        )

    if errors or findings:
        for error in errors:
            print(error)
        for finding in findings:
            print(f"Secret-like value detected: {finding}")
        return 1
    return 0


def _should_ignore_path(path: Path) -> bool:
    return any(part in IGNORED_SCAN_PARTS for part in path.parts)


def validate_extension_target(root: Path, target: str | Path) -> dict[str, object]:
    repo_root = root.resolve()
    raw_target = str(target)
    try:
        resolved = _resolve_validation_target(repo_root, target)
    except ValueError as exc:
        return {
            "target": raw_target,
            "valid": False,
            "kind": "unknown",
            "errors": [str(exc)],
            "warnings": [],
            "checks_run": [],
        }

    errors: list[str] = []
    checks_run: list[str] = []
    kind = "unknown"

    if resolved.is_dir():
        relative = resolved.relative_to(repo_root)
        if len(relative.parts) < 2 or relative.parts[0] not in ECOSYSTEM_CATEGORIES:
            return {
                "target": str(relative),
                "valid": False,
                "kind": "unknown",
                "errors": ["target directory must be a contribution folder under a curated surface"],
                "warnings": [],
                "checks_run": [],
            }
        kind = "contribution"
        metadata_path = resolved / "metadata.json"
        checks_run.append("contribution_structure")
        errors.extend(validate_contribution_structure(repo_root, resolved))
        if metadata_path.exists():
            checks_run.append("contribution_metadata")
            contribution_schema = load_contribution_metadata_schema(repo_root)
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{metadata_path.relative_to(repo_root)}: invalid JSON ({exc.msg})")
            else:
                errors.extend(
                    validate_contribution_metadata(
                        metadata,
                        schema=contribution_schema,
                        source=str(metadata_path.relative_to(repo_root)),
                    )
                )
        return {
            "target": str(relative),
            "valid": not errors,
            "kind": kind,
            "errors": errors,
            "warnings": [],
            "checks_run": checks_run,
        }

    relative = resolved.relative_to(repo_root)
    if relative.parent == BUNDLE_DIR and relative.suffix == ".json":
        kind = "bundle"
        checks_run.append("bundle_manifest")
        bundle_schema = load_bundle_manifest_schema(repo_root)
        try:
            manifest = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{relative}: invalid JSON ({exc.msg})")
        else:
            errors.extend(
                validate_bundle_manifest(
                    manifest,
                    schema=bundle_schema,
                    source=str(relative),
                    root=repo_root,
                )
            )
        return {
            "target": str(relative),
            "valid": not errors,
            "kind": kind,
            "errors": errors,
            "warnings": [],
            "checks_run": checks_run,
        }

    if relative.name == "metadata.json":
        kind = "metadata"
        checks_run.extend(["contribution_metadata", "contribution_structure"])
        contribution_schema = load_contribution_metadata_schema(repo_root)
        try:
            metadata = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{relative}: invalid JSON ({exc.msg})")
        else:
            errors.extend(
                validate_contribution_metadata(
                    metadata,
                    schema=contribution_schema,
                    source=str(relative),
                )
            )
            errors.extend(validate_contribution_structure(repo_root, resolved))
        return {
            "target": str(relative),
            "valid": not errors,
            "kind": kind,
            "errors": errors,
            "warnings": [],
            "checks_run": checks_run,
        }

    if relative.name == "module-metadata.json":
        kind = "module-metadata"
        checks_run.append("module_metadata")
        schema = load_module_metadata_schema(repo_root)
        try:
            metadata = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{relative}: invalid JSON ({exc.msg})")
        else:
            errors.extend(
                validate_module_metadata(
                    metadata,
                    schema=schema,
                    source=str(relative),
                )
            )
        return {
            "target": str(relative),
            "valid": not errors,
            "kind": kind,
            "errors": errors,
            "warnings": [],
            "checks_run": checks_run,
        }

    return {
        "target": str(relative),
        "valid": False,
        "kind": "unknown",
        "errors": ["target must be a contribution directory, bundle manifest, metadata.json, or module-metadata.json"],
        "warnings": [],
        "checks_run": [],
    }


def _looks_secret_assignment_value(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower().strip("\"'")
    if lowered in {"", "none", "null"}:
        return False
    if lowered in {"changeme", "placeholder", "example", "test-key"}:
        return False
    if any(
        token in lowered for token in ("config.", "os.getenv", "getenv(", "{", "}", ",")
    ):
        return False
    return len(lowered) >= 12


def _is_kebab_case(value: str) -> bool:
    return bool(KEBAB_CASE_PATTERN.fullmatch(value))


def _load_pyproject_optional_extras(root: Path) -> set[str]:
    pyproject = root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    optional = data.get("project", {}).get("optional-dependencies", {})
    if not isinstance(optional, dict):
        return set()
    return {str(key) for key in optional}


def _load_boolean_config_flags() -> set[str]:
    hints = get_type_hints(NeuroCoreConfig)
    return {
        field.name
        for field in fields(NeuroCoreConfig)
        if hints.get(field.name) is bool
    }


def _has_single_top_level_heading(text: str) -> bool:
    headings = [line for line in text.splitlines() if line.startswith("# ")]
    return len(headings) == 1


def _has_mutation_safety_language(text: str) -> bool:
    lowered = text.lower()
    return all(phrase in lowered for phrase in COMMAND_PROMPT_SAFETY_PHRASES)


def _parse_command_frontmatter(
    text: str,
) -> tuple[dict[str, object], str, list[str]]:
    if not text.startswith("---\n"):
        return {}, text, ["command prompts must begin with YAML frontmatter"]

    lines = text.splitlines()
    closing_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        return {}, text, ["command prompts must begin with YAML frontmatter"]

    metadata: dict[str, object] = {}
    errors: list[str] = []
    for raw_line in lines[1:closing_index]:
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            errors.append("frontmatter lines must use 'key: value' entries")
            continue
        key, value = line.split(":", 1)
        parsed_key = key.strip()
        parsed_value = value.strip()
        if parsed_key in metadata:
            errors.append(f"frontmatter key '{parsed_key}' must not be repeated")
            continue
        if parsed_key == "mutates_repo":
            lowered = parsed_value.lower()
            if lowered == "true":
                metadata[parsed_key] = True
            elif lowered == "false":
                metadata[parsed_key] = False
            else:
                metadata[parsed_key] = parsed_value
            continue
        metadata[parsed_key] = parsed_value.strip("\"'")

    body = "\n".join(lines[closing_index + 1 :]).lstrip("\n")
    return metadata, body, errors


def _resolve_validation_target(root: Path, target: str | Path) -> Path:
    raw = Path(target).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if candidate == root:
        raise ValueError("target must point to a concrete contribution path inside the repository")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("target must stay within the repository root") from exc
    if not candidate.exists():
        raise ValueError("target does not exist")
    return candidate


def _resolve_repo_relative_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"bundle item escapes repository root ({relative_path})") from exc
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
