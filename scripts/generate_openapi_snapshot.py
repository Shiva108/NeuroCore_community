"""Generate or verify the checked-in FastAPI OpenAPI snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neurocore.adapters.openapi_snapshot import (  # noqa: E402
    OPENAPI_SNAPSHOT_PATH,
    build_openapi_snapshot,
    write_openapi_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the checked-in NeuroCore HTTP OpenAPI snapshot."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the checked-in snapshot does not match the current app schema.",
    )
    args = parser.parse_args(argv)

    current_snapshot = build_openapi_snapshot()
    if args.check:
        if not OPENAPI_SNAPSHOT_PATH.exists():
            print(f"Missing snapshot: {OPENAPI_SNAPSHOT_PATH}")
            return 1
        expected_snapshot = json.loads(
            OPENAPI_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        if expected_snapshot != current_snapshot:
            print(
                "OpenAPI snapshot drift detected. Re-run "
                "`python scripts/generate_openapi_snapshot.py`."
            )
            return 1
        print(f"OpenAPI snapshot matches {OPENAPI_SNAPSHOT_PATH}")
        return 0

    path = write_openapi_snapshot()
    print(f"Wrote OpenAPI snapshot to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
