import json
from pathlib import Path

from neurocore.retrieval.eval_harness import (
    generate_eval_snapshot,
    validate_eval_snapshot,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "retrieval_eval"
    / "baseline_fixture.json"
)
SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "retrieval_eval"
    / "baseline_snapshot.json"
)


def test_eval_harness_matches_checked_in_snapshot():
    snapshot = generate_eval_snapshot(FIXTURE_PATH)
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert validate_eval_snapshot(FIXTURE_PATH, snapshot) == []
    assert snapshot == expected
