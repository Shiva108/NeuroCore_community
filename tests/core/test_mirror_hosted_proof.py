import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


def _load_module(script_name: str, module_name: str):
    module_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


PROOF = _load_module("mirror_hosted_proof.py", "mirror_hosted_proof_module")


def test_extract_query_term_skips_common_words():
    term = PROOF._extract_query_term("mirror proof local capture AlphaSignal details")

    assert term == "AlphaSignal"


def test_select_witness_item_ignores_sealed_items():
    sealed_item = SimpleNamespace(
        id="rec-sealed",
        namespace="security-lab",
        bucket="ops",
        sensitivity="sealed",
        title="",
        content="SensitiveWitness token",
        raw_content="",
    )
    usable_item = SimpleNamespace(
        id="rec-usable",
        namespace="security-lab",
        bucket="recon",
        sensitivity="restricted",
        title="",
        content="UsableWitness token for proof",
        raw_content="",
    )

    witness = PROOF._select_witness_item([sealed_item, usable_item], [])

    assert witness is not None
    assert witness.item_id == "rec-usable"
    assert witness.bucket == "recon"
    assert witness.term == "UsableWitness"


def test_assert_query_contains_accepts_matching_content_preview():
    payload = {
        "results": [
            {
                "id": "rec-1",
                "namespace": "mirror-proof",
                "bucket": "recon",
                "content_preview": "Hosted proof capture token hosted-20260516",
            }
        ]
    }

    PROOF._assert_query_contains(
        payload,
        item_id="",
        namespace="mirror-proof",
        bucket="recon",
        term="hosted-20260516",
    )
