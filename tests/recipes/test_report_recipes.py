from pathlib import Path

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Inputs",
    "## Flow",
    "## Expected Output",
    "## Validation",
)


def test_opinionated_report_recipes_include_required_sections():
    recipe_paths = (
        Path("recipes/security-memory-review-report/README.md"),
        Path("recipes/ops-weekly-memory-report/README.md"),
    )

    for recipe_path in recipe_paths:
        content = recipe_path.read_text(encoding="utf-8")
        for section in REQUIRED_SECTIONS:
            assert section in content, f"{recipe_path} missing section: {section}"
