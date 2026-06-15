"""Run the repo-local retrieval evaluation harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neurocore.retrieval.eval_harness import generate_eval_snapshot

DEFAULT_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "retrieval_eval" / "baseline_fixture.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/retrieval_eval.py",
        description="Run the deterministic NeuroCore retrieval evaluation harness.",
    )
    parser.add_argument(
        "--fixture-path",
        default=str(DEFAULT_FIXTURE),
        help="Path to the retrieval eval fixture JSON.",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="Output path for the generated snapshot JSON, or '-' for stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot = generate_eval_snapshot(args.fixture_path)
    rendered = json.dumps(snapshot, indent=2, sort_keys=True)
    if args.output == "-":
        sys.stdout.write(rendered)
        sys.stdout.write("\n")
        return 0
    output_path = Path(args.output)
    output_path.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
