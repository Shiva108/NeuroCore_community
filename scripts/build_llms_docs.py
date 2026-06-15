"""Build agent-facing llms.txt artifacts from the checked-in docs set."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAP_OUTPUT = REPO_ROOT / "llms.txt"
FULL_OUTPUT = REPO_ROOT / "llms-full.txt"
DOC_PATHS = ("README.md", "docs/setup.md")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/build_llms_docs.py",
        description="Build llms.txt and llms-full.txt from repo docs.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if committed llms artifacts are stale.",
    )
    return parser


def collect_source_paths(repo_root: Path) -> list[Path]:
    paths = [repo_root / relative_path for relative_path in DOC_PATHS]
    paths.extend(sorted((repo_root / "docs" / "ssd").glob("*.md")))
    return paths


def render_llms_docs(repo_root: Path) -> tuple[str, str]:
    source_paths = collect_source_paths(repo_root)
    entries = [_build_entry(repo_root, path) for path in source_paths]
    doc_map = _render_doc_map(entries)
    full = _render_full(doc_map, entries)
    return doc_map, full


def write_outputs(
    *,
    repo_root: Path = REPO_ROOT,
    llms_path: Path = MAP_OUTPUT,
    llms_full_path: Path = FULL_OUTPUT,
    check: bool = False,
) -> int:
    doc_map, full = render_llms_docs(repo_root)
    expected_outputs = {
        llms_path: doc_map,
        llms_full_path: full,
    }
    stale_paths: list[Path] = []
    for path, expected in expected_outputs.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != expected:
            stale_paths.append(path)
            if not check:
                path.write_text(expected, encoding="utf-8")
    if stale_paths and check:
        for path in stale_paths:
            sys.stderr.write(f"stale llms artifact: {path.relative_to(repo_root)}\n")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return write_outputs(check=args.check)


def _build_entry(repo_root: Path, path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    return {
        "path": path.relative_to(repo_root).as_posix(),
        "title": _extract_title(path, content),
        "purpose": _extract_purpose(content),
        "content": content.strip(),
    }


def _extract_title(path: Path, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


def _extract_purpose(content: str) -> str:
    paragraph = _first_body_paragraph(content)
    if paragraph:
        normalized = re.sub(r"\s+", " ", paragraph).strip()
        normalized = _strip_markdown_links(normalized)
        sentence = normalized.split(". ", 1)[0].strip()
        return sentence.rstrip(".") + "."
    return "Reference documentation for NeuroCore operators and agent tooling."


def _first_body_paragraph(content: str) -> str:
    in_code_block = False
    in_front_matter = False
    paragraph_lines: list[str] = []

    for index, raw_line in enumerate(content.splitlines()):
        stripped = raw_line.strip()
        if index == 0 and stripped == "---":
            in_front_matter = True
            continue
        if in_front_matter:
            if stripped == "---":
                in_front_matter = False
            continue
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if not stripped:
            if paragraph_lines:
                break
            continue
        if stripped.startswith("#"):
            if paragraph_lines:
                break
            continue
        if stripped.startswith("[!") or stripped.startswith("!["):
            if paragraph_lines:
                break
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(stripped)

    return " ".join(paragraph_lines)


def _strip_markdown_links(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)


def _render_doc_map(entries: list[dict[str, str]]) -> str:
    lines = [
        "# NeuroCore LLM Docs Map",
        "",
        "Read in this order for the repo contract, setup flow, and SSD source of truth.",
        "",
    ]
    for ordinal, entry in enumerate(entries, start=1):
        lines.append(
            f"{ordinal}. `{entry['path']}`"
            f" | {entry['title']}"
            f" | {entry['purpose']}"
        )
    return "\n".join(lines).strip() + "\n"


def _render_full(doc_map: str, entries: list[dict[str, str]]) -> str:
    lines = [doc_map.strip(), "", "# Inlined Source Docs", ""]
    for entry in entries:
        lines.extend(
            [
                f"## {entry['path']}",
                "",
                f"Title: {entry['title']}",
                "",
                "```markdown",
                entry["content"],
                "```",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
