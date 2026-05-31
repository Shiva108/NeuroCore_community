import json
import importlib.util
from pathlib import Path

from neurocore.adapters.openapi_snapshot import (
    OPENAPI_SNAPSHOT_PATH,
    build_openapi_snapshot,
)

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_openapi_snapshot.py"
)


def _load_generate_openapi_snapshot_module():
    spec = importlib.util.spec_from_file_location(
        "generate_openapi_snapshot_script", SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openapi_snapshot_matches_checked_in_file():
    expected = json.loads(OPENAPI_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert build_openapi_snapshot() == expected


def test_generate_openapi_snapshot_check_passes_for_current_snapshot(monkeypatch):
    generate_openapi_snapshot = _load_generate_openapi_snapshot_module()
    monkeypatch.setattr(
        generate_openapi_snapshot,
        "OPENAPI_SNAPSHOT_PATH",
        OPENAPI_SNAPSHOT_PATH,
    )

    assert generate_openapi_snapshot.main(["--check"]) == 0


def test_generate_openapi_snapshot_check_fails_on_drift(monkeypatch, tmp_path: Path):
    generate_openapi_snapshot = _load_generate_openapi_snapshot_module()
    snapshot_path = tmp_path / "openapi.json"
    snapshot_path.write_text(
        json.dumps({"openapi": "3.1.0", "info": {"title": "stale"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        generate_openapi_snapshot,
        "OPENAPI_SNAPSHOT_PATH",
        snapshot_path,
    )

    assert generate_openapi_snapshot.main(["--check"]) == 1
