"""Repository governance validation utilities for NeuroCore."""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

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
REQUIRED_TEMPLATE_FILES: tuple[str, ...] = ()
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
    "README.md",
    "CONTRIBUTING.md",
    "ROADMAP.md",
    "docs/ssd/implementation-plan.md",
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


def load_module_metadata_schema(root: Path) -> dict[str, object]:
    schema_path = root / ".github" / "module-metadata.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def load_contribution_metadata_schema(root: Path) -> dict[str, object]:
    schema_path = root / ".github" / "contribution-metadata.schema.json"
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
            discovered.append(path)
    return discovered


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
                errors.append(f"{relative_file}: invalid JSON: {exc.msg}")
        else:
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(f"{relative_file}: {filename} must be valid UTF-8 text")
                continue
            if filename == "README.md" and not text.startswith("# "):
                errors.append(
                    f"{relative_file}: README.md must begin with a top-level '# ' heading"
                )

    return errors


def validate_command_prompt_file(root: Path, path: Path) -> list[str]:
    relative_path = path.relative_to(root)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{relative_path}: command prompt must be valid UTF-8 text"]

    if not text.startswith("---\n"):
        return [f"{relative_path}: command prompts must begin with YAML frontmatter"]

    end = text.find("\n---\n", 4)
    if end == -1:
        return [
            f"{relative_path}: command prompt frontmatter must end with a closing '---'"
        ]

    frontmatter = text[4:end]
    body = text[end + 5 :]
    metadata: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            return [f"{relative_path}: invalid frontmatter line: {line}"]
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()

    errors: list[str] = []
    for field in COMMAND_PROMPT_REQUIRED_FIELDS:
        if not metadata.get(field):
            errors.append(f"{relative_path}: missing frontmatter field '{field}'")

    mode = metadata.get("mode", "")
    if mode and mode not in COMMAND_PROMPT_MODES:
        errors.append(
            f"{relative_path}: mode must be one of {', '.join(COMMAND_PROMPT_MODES)}"
        )

    mutates_repo = metadata.get("mutates_repo", "")
    if mutates_repo and mutates_repo not in {"true", "false"}:
        errors.append(f"{relative_path}: mutates_repo must be 'true' or 'false'")

    if metadata.get("mutates_repo") == "true":
        lowered_body = body.lower()
        if not any(phrase in lowered_body for phrase in COMMAND_PROMPT_SAFETY_PHRASES):
            errors.append(
                f"{relative_path}: mutating prompts must mention auto-apply safety or explicit confirmation"
            )

    return errors


def _should_ignore_path(relative_path: Path) -> bool:
    return any(part in IGNORED_SCAN_PARTS for part in relative_path.parts)


def _is_kebab_case(value: str) -> bool:
    return bool(KEBAB_CASE_PATTERN.fullmatch(value))


def _looks_secret_assignment_value(value: str) -> bool:
    stripped = value.strip().strip('"').strip("'")
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered in {
        "placeholder",
        "replace-me",
        "replace-with-real-key",
        "replace-with-real-secret",
        "your-api-key",
    }:
        return False
    if any(
        token in lowered for token in ("config.", "os.getenv", "getenv(", "{", "}", ",")
    ):
        return False
    if len(stripped) < 8:
        return False
    return True


def main() -> int:
    root = Path.cwd()
    errors: list[str] = []
    errors.extend(validate_repo_contract(root))

    try:
        module_schema = load_module_metadata_schema(root)
        for metadata_path in discover_metadata_files(root):
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            errors.extend(
                validate_module_metadata(
                    payload,
                    schema=module_schema,
                    source=str(metadata_path.relative_to(root)),
                )
            )
    except FileNotFoundError:
        errors.append("Missing required file: .github/module-metadata.schema.json")

    try:
        contribution_schema = load_contribution_metadata_schema(root)
        for metadata_path in discover_contribution_metadata_files(root):
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            errors.extend(
                validate_contribution_metadata(
                    payload,
                    schema=contribution_schema,
                    source=str(metadata_path.relative_to(root)),
                )
            )
            errors.extend(validate_contribution_structure(root, metadata_path))
        for directory in discover_contribution_directories(root):
            errors.extend(validate_contribution_structure(root, directory))
    except FileNotFoundError:
        errors.append(
            "Missing required file: .github/contribution-metadata.schema.json"
        )

    for prompt_path in discover_command_prompt_files(root):
        errors.extend(validate_command_prompt_file(root, prompt_path))

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root)
        if _should_ignore_path(relative_path):
            continue
        if relative_path.name.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico")
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings = find_secret_like_values(text)
        errors.extend(f"{relative_path}: secret-like value detected" for _ in findings)

    if errors:
        for error in errors:
            print(error)
        return 1

    print("Repo contract validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
