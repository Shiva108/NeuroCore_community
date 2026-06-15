import importlib.util
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "build_llms_docs.py"
    spec = importlib.util.spec_from_file_location("build_llms_docs", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_build_llms_docs_matches_committed_files():
    llms_text, llms_full_text = MODULE.render_llms_docs(REPO_ROOT)

    assert llms_text == (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")
    assert llms_full_text == (REPO_ROOT / "llms-full.txt").read_text(encoding="utf-8")


def test_build_llms_docs_check_passes_when_outputs_are_current():
    assert MODULE.main(["--check"]) == 0


def test_build_llms_docs_check_fails_when_outputs_are_stale(tmp_path):
    repo_root = tmp_path / "repo"
    (repo_root / "docs" / "ssd").mkdir(parents=True)
    (repo_root / "README.md").write_text("# Repo\n\nPrimary doc.\n", encoding="utf-8")
    (repo_root / "docs" / "setup.md").write_text(
        "# Setup\n\nSetup flow.\n", encoding="utf-8"
    )
    (repo_root / "docs" / "ssd" / "architecture.md").write_text(
        "# Architecture\n\nSSD contract.\n", encoding="utf-8"
    )

    llms_path = repo_root / "llms.txt"
    llms_full_path = repo_root / "llms-full.txt"
    llms_path.write_text("stale\n", encoding="utf-8")
    llms_full_path.write_text("stale\n", encoding="utf-8")

    assert (
        MODULE.write_outputs(
            repo_root=repo_root,
            llms_path=llms_path,
            llms_full_path=llms_full_path,
            check=True,
        )
        == 1
    )
