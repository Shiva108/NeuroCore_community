"""Run the operational proof for local mirror plus hosted Postgres."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib import error, request

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neurocore.core.config import ConfigError, NeuroCoreConfig, load_config
from neurocore.core.operator_state import load_operator_env
from neurocore.storage.router import RoutedStore
from neurocore.storage.sqlite_store import SQLiteStore

STOPWORDS = {
    "about",
    "after",
    "agent",
    "against",
    "bucket",
    "capture",
    "cloud",
    "content",
    "could",
    "degradation",
    "document",
    "evidence",
    "finding",
    "hosted",
    "local",
    "memory",
    "mirror",
    "namespace",
    "proof",
    "query",
    "record",
    "report",
    "runtime",
    "should",
    "source",
    "storage",
    "their",
    "there",
    "these",
    "those",
    "through",
    "where",
    "which",
    "write",
}


class ProofError(RuntimeError):
    """Raised when the proof cannot complete successfully."""


@dataclass(frozen=True)
class Witness:
    item_id: str
    namespace: str
    bucket: str
    term: str
    sensitivity_ceiling: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/mirror_hosted_proof.py",
        description=(
            "Execute the local mirror plus hosted Postgres operational proof."
        ),
    )
    parser.add_argument(
        "--hosted-base-url",
        default=os.environ.get("NEUROCORE_PROOF_HOSTED_BASE_URL", "").strip(),
        help="Hosted HTTP base URL, for example http://127.0.0.1:8000.",
    )
    parser.add_argument(
        "--witness-namespace",
        default="",
        help="Witness namespace. Omit to auto-select a local witness item.",
    )
    parser.add_argument(
        "--witness-bucket",
        default="",
        help="Witness bucket. Omit to auto-select a local witness item.",
    )
    parser.add_argument(
        "--witness-term",
        default="",
        help="Witness query term. Omit to auto-select a local witness item.",
    )
    parser.add_argument(
        "--witness-id",
        default="",
        help="Optional witness item ID used for stricter remote-query matching.",
    )
    parser.add_argument(
        "--proof-namespace",
        default="mirror-proof",
        help="Namespace used for hosted and local proof captures.",
    )
    parser.add_argument(
        "--hosted-proof-bucket",
        default="recon",
        help="Bucket used for the hosted proof capture.",
    )
    parser.add_argument(
        "--local-proof-bucket",
        default="ops",
        help="Bucket used for the local degradation proof capture.",
    )
    parser.add_argument(
        "--sensitivity",
        default="restricted",
        help="Sensitivity used for proof captures and queries.",
    )
    parser.add_argument(
        "--keep-proof-dir",
        action="store_true",
        help="Keep the disposable SQLite clone directory after the run.",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Print only the final JSON summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_proof(args, repo_root=REPO_ROOT)
    except (ConfigError, ProofError, subprocess.CalledProcessError) as exc:
        print(f"Operational proof failed: {exc}", file=sys.stderr)
        return 1

    if args.output_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("Operational proof completed successfully.")
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def run_proof(args: argparse.Namespace, *, repo_root: Path) -> dict[str, object]:
    base_env = _runtime_env(repo_root)
    config = load_config(base_env)
    _ensure_mirror_runtime_ready(config)
    _ensure_allowed_bucket(config, args.hosted_proof_bucket)
    _ensure_allowed_bucket(config, args.local_proof_bucket)
    if not args.hosted_base_url:
        raise ProofError(
            "Hosted base URL is required. Pass --hosted-base-url or set "
            "NEUROCORE_PROOF_HOSTED_BASE_URL."
        )

    witness = _resolve_witness(
        args,
        config=config,
        repo_root=repo_root,
    )
    hosted_base_url = args.hosted_base_url.rstrip("/")
    summary: dict[str, object] = {
        "witness": {
            "id": witness.item_id,
            "namespace": witness.namespace,
            "bucket": witness.bucket,
            "term": witness.term,
        }
    }

    capabilities = _run_capabilities(repo_root, base_env)
    summary["capabilities"] = capabilities

    local_status = _run_cli(
        repo_root,
        base_env,
        ["admin", "sync"],
        {"action": "status"},
    )
    _assert_status(local_status, expected_mode="mirror", degraded=False)
    summary["baseline_status"] = local_status

    backfill = _run_cli(
        repo_root,
        base_env,
        ["admin", "sync"],
        {"action": "backfill_local_to_cloud"},
    )
    summary["backfill"] = backfill

    backfill_status = _run_cli(
        repo_root,
        base_env,
        ["admin", "sync"],
        {"action": "status"},
    )
    _assert_status(backfill_status, expected_mode="mirror", degraded=False)
    summary["post_backfill_status"] = backfill_status

    witness_query = _post_json(
        f"{hosted_base_url}/query",
        {
            "query_text": witness.term,
            "allowed_buckets": [witness.bucket],
            "sensitivity_ceiling": witness.sensitivity_ceiling,
            "namespace": witness.namespace,
        },
    )
    _assert_query_contains(
        witness_query,
        item_id=witness.item_id,
        namespace=witness.namespace,
        bucket=witness.bucket,
        term=witness.term,
    )
    summary["hosted_witness_query"] = witness_query

    hosted_token = _proof_token("hosted")
    hosted_capture = _post_json(
        f"{hosted_base_url}/capture",
        {
            "namespace": args.proof_namespace,
            "bucket": args.hosted_proof_bucket,
            "content": f"Hosted proof capture token {hosted_token}",
            "content_format": "markdown",
            "source_type": "note",
            "sensitivity": args.sensitivity,
        },
    )
    hosted_item_id = str(hosted_capture["id"])
    summary["hosted_capture"] = hosted_capture

    local_query = _run_cli(
        repo_root,
        base_env,
        ["query"],
        {
            "namespace": args.proof_namespace,
            "query_text": hosted_token,
            "allowed_buckets": [args.hosted_proof_bucket],
            "sensitivity_ceiling": args.sensitivity,
        },
    )
    _assert_query_contains(
        local_query,
        item_id=hosted_item_id,
        namespace=args.proof_namespace,
        bucket=args.hosted_proof_bucket,
        term=hosted_token,
    )
    summary["local_query_for_hosted_capture"] = local_query

    failure_summary = _run_local_failure_proof(
        repo_root=repo_root,
        base_env=base_env,
        config=config,
        hosted_base_url=hosted_base_url,
        proof_namespace=args.proof_namespace,
        proof_bucket=args.local_proof_bucket,
        sensitivity=args.sensitivity,
        keep_proof_dir=args.keep_proof_dir,
    )
    summary["local_failure_proof"] = failure_summary
    return summary


def _run_local_failure_proof(
    *,
    repo_root: Path,
    base_env: dict[str, str],
    config: NeuroCoreConfig,
    hosted_base_url: str,
    proof_namespace: str,
    proof_bucket: str,
    sensitivity: str,
    keep_proof_dir: bool,
) -> dict[str, object]:
    temp_dir = Path(tempfile.mkdtemp(prefix="neurocore-mirror-proof-"))
    primary_clone = temp_dir / Path(config.primary_store_path).name
    sealed_clone = temp_dir / Path(config.sealed_store_path).name
    shutil.copy2(repo_root / config.primary_store_path, primary_clone)
    shutil.copy2(repo_root / config.sealed_store_path, sealed_clone)

    proof_env = dict(base_env)
    proof_env["NEUROCORE_PRIMARY_STORE_PATH"] = str(primary_clone)
    proof_env["NEUROCORE_SEALED_STORE_PATH"] = str(sealed_clone)
    token = _proof_token("degradation")
    summary: dict[str, object] = {
        "proof_directory": str(temp_dir),
        "token": token,
    }

    try:
        pre_failure_status = _run_cli(
            repo_root,
            proof_env,
            ["admin", "sync"],
            {"action": "status"},
        )
        _assert_status(pre_failure_status, expected_mode="mirror", degraded=False)
        summary["pre_failure_status"] = pre_failure_status

        os.chmod(primary_clone, 0o444)
        os.chmod(sealed_clone, 0o444)
        os.chmod(temp_dir, 0o555)

        capture = _run_cli(
            repo_root,
            proof_env,
            ["capture"],
            {
                "namespace": proof_namespace,
                "bucket": proof_bucket,
                "content": f"Local mirror degradation proof token {token}",
                "content_format": "markdown",
                "source_type": "note",
                "sensitivity": sensitivity,
            },
        )
        if not capture.get("warnings"):
            raise ProofError("Expected local mirror degradation capture warnings.")
        if capture.get("reconciliation_attempted") is not True:
            raise ProofError("Expected degraded capture to attempt reconciliation.")
        if capture.get("parity_state") != "degraded":
            raise ProofError(
                "Expected degraded capture to report parity_state=degraded."
            )
        summary["degradation_capture"] = capture

        degraded_status = _run_cli(
            repo_root,
            proof_env,
            ["admin", "sync"],
            {"action": "status"},
        )
        _assert_status(degraded_status, expected_mode="mirror", degraded=True)
        if not degraded_status["storage_backend"].get("last_local_error"):
            raise ProofError(
                "Expected local_degraded status to include last_local_error."
            )
        summary["degraded_status"] = degraded_status

        hosted_query = _post_json(
            f"{hosted_base_url}/query",
            {
                "query_text": token,
                "allowed_buckets": [proof_bucket],
                "sensitivity_ceiling": sensitivity,
                "namespace": proof_namespace,
            },
        )
        _assert_query_contains(
            hosted_query,
            item_id=str(capture["id"]),
            namespace=proof_namespace,
            bucket=proof_bucket,
            term=token,
        )
        summary["hosted_query_for_degradation_capture"] = hosted_query
    finally:
        os.chmod(temp_dir, 0o755)
        os.chmod(primary_clone, 0o644)
        os.chmod(sealed_clone, 0o644)

    repair = _run_cli(
        repo_root,
        proof_env,
        ["admin", "sync"],
        {"action": "repair_local_from_cloud"},
    )
    summary["repair"] = repair

    repaired_status = _run_cli(
        repo_root,
        proof_env,
        ["admin", "sync"],
        {"action": "status"},
    )
    _assert_status(repaired_status, expected_mode="mirror", degraded=False)
    summary["repaired_status"] = repaired_status

    parity = _run_cli(
        repo_root,
        proof_env,
        ["admin", "sync"],
        {"action": "verify_parity"},
    )
    if not parity.get("parity", {}).get(
        "in_sync_after", parity.get("parity", {}).get("in_sync")
    ):
        raise ProofError(
            "Expected verify_parity to report full mirror parity after repair."
        )
    summary["verify_parity"] = parity

    local_query = _run_cli(
        repo_root,
        proof_env,
        ["query"],
        {
            "namespace": proof_namespace,
            "query_text": token,
            "allowed_buckets": [proof_bucket],
            "sensitivity_ceiling": sensitivity,
        },
    )
    _assert_query_contains(
        local_query,
        item_id=str(summary["degradation_capture"]["id"]),
        namespace=proof_namespace,
        bucket=proof_bucket,
        term=token,
    )
    summary["local_query_after_repair"] = local_query

    if not keep_proof_dir:
        shutil.rmtree(temp_dir)
        summary["proof_directory_removed"] = True
    else:
        summary["proof_directory_removed"] = False
    return summary


def _resolve_witness(
    args: argparse.Namespace,
    *,
    config: NeuroCoreConfig,
    repo_root: Path,
) -> Witness:
    if args.witness_namespace and args.witness_bucket and args.witness_term:
        return Witness(
            item_id=args.witness_id.strip() or "",
            namespace=args.witness_namespace.strip(),
            bucket=args.witness_bucket.strip(),
            term=args.witness_term.strip(),
            sensitivity_ceiling=args.sensitivity,
        )

    local_store = RoutedStore(
        primary_store=SQLiteStore(repo_root / config.primary_store_path),
        sealed_store=SQLiteStore(repo_root / config.sealed_store_path),
    )
    witness = _select_witness_item(
        list(local_store.list_records(include_archived=False)),
        list(local_store.list_documents(include_archived=False)),
    )
    if witness is None:
        raise ProofError(
            "Could not auto-select a witness item from the local SQLite corpus. "
            "Pass --witness-namespace, --witness-bucket, and --witness-term."
        )
    return witness


def _select_witness_item(
    records: list[object], documents: list[object]
) -> Witness | None:
    for item in [*records, *documents]:
        sensitivity = str(getattr(item, "sensitivity", "") or "").lower()
        if sensitivity == "sealed":
            continue
        text = " ".join(
            part
            for part in (
                str(getattr(item, "title", "") or ""),
                str(getattr(item, "content", "") or ""),
                str(getattr(item, "raw_content", "") or ""),
            )
            if part
        )
        term = _extract_query_term(text)
        if not term:
            continue
        return Witness(
            item_id=str(getattr(item, "id")),
            namespace=str(getattr(item, "namespace")),
            bucket=str(getattr(item, "bucket")),
            term=term,
            sensitivity_ceiling=sensitivity or "restricted",
        )
    return None


def _extract_query_term(text: str) -> str | None:
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{4,}", text):
        lowered = token.lower()
        if lowered not in STOPWORDS:
            return token
    return None


def _ensure_mirror_runtime_ready(config: NeuroCoreConfig) -> None:
    if config.storage_backend != "mirror":
        raise ProofError("The local runtime must use NEUROCORE_STORAGE_BACKEND=mirror.")
    if not config.enable_admin_surface:
        raise ProofError(
            "The local runtime must enable NEUROCORE_ENABLE_ADMIN_SURFACE=true."
        )
    if not config.production_database_url or not config.production_sealed_database_url:
        raise ProofError(
            "The local runtime must configure both production Postgres URLs."
        )


def _ensure_allowed_bucket(config: NeuroCoreConfig, bucket: str) -> None:
    if bucket not in config.allowed_buckets:
        raise ProofError(
            f"Bucket {bucket!r} is not allowed by the local mirror configuration."
        )


def _assert_status(
    payload: dict[str, object],
    *,
    expected_mode: str,
    degraded: bool,
) -> None:
    storage = payload.get("storage_backend")
    if not isinstance(storage, dict):
        raise ProofError("Missing storage_backend status payload.")
    if storage.get("mode") != expected_mode:
        raise ProofError(
            f"Expected storage_backend.mode={expected_mode}, got {storage.get('mode')!r}."
        )
    if bool(storage.get("local_degraded", False)) != degraded:
        raise ProofError(
            f"Expected local_degraded={degraded}, got {storage.get('local_degraded')!r}."
        )
    if not bool(storage.get("local_configured")):
        raise ProofError("Expected local_configured=true.")
    if not bool(storage.get("cloud_configured")):
        raise ProofError("Expected cloud_configured=true.")
    if payload.get("supported") is False:
        raise ProofError("Mirror sync status reported supported=false.")


def _assert_query_contains(
    payload: dict[str, object],
    *,
    item_id: str,
    namespace: str,
    bucket: str,
    term: str,
) -> None:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ProofError("Query response did not include a results list.")
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("namespace") != namespace or result.get("bucket") != bucket:
            continue
        if item_id and result.get("id") == item_id:
            return
        preview = str(result.get("content_preview", "") or "")
        if term and term.lower() in preview.lower():
            return
    raise ProofError(
        f"Query did not return the expected item for term {term!r} in {namespace}/{bucket}."
    )


def _proof_token(label: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{label}-{timestamp}"


def _runtime_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(load_operator_env(repo_root, base_env=env, stderr=sys.stderr)[0])
    src_path = str(repo_root / "src")
    existing = env.get("PYTHONPATH", "").strip()
    if existing:
        parts = existing.split(os.pathsep)
        if src_path not in parts:
            env["PYTHONPATH"] = os.pathsep.join([src_path, *parts])
    else:
        env["PYTHONPATH"] = src_path
    return env


def _resolve_python(repo_root: Path, env: dict[str, str]) -> Path:
    override = env.get("NEUROCORE_PYTHON_EXECUTABLE", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend(
        [
            repo_root / ".venv" / "bin" / "python",
            repo_root / ".venv" / "Scripts" / "python.exe",
            Path(sys.executable).expanduser(),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.expanduser().absolute()
    raise ProofError("Could not resolve a Python executable for the repo runtime.")


def _run_cli(
    repo_root: Path,
    env: dict[str, str],
    command: list[str],
    request_payload: dict[str, object],
) -> dict[str, object]:
    python_path = _resolve_python(repo_root, env)
    completed = subprocess.run(
        [
            str(python_path),
            "-m",
            "neurocore.adapters.cli",
            *command,
            "--request-json",
            json.dumps(request_payload),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ProofError(completed.stderr.strip() or completed.stdout.strip())
    return _parse_json_payload(completed.stdout.strip())


def _run_capabilities(repo_root: Path, env: dict[str, str]) -> dict[str, object]:
    python_path = _resolve_python(repo_root, env)
    completed = subprocess.run(
        [
            str(python_path),
            str(repo_root / "scripts" / "security_workflow.py"),
            "capabilities",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ProofError(completed.stderr.strip() or completed.stdout.strip())
    return _parse_json_payload(completed.stdout.strip())


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProofError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except error.URLError as exc:
        raise ProofError(
            f"Could not reach hosted endpoint {url}: {exc.reason}"
        ) from exc
    return _parse_json_payload(raw)


def _parse_json_payload(raw: str) -> dict[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProofError(f"Expected JSON payload, got: {raw}") from exc
    if not isinstance(payload, dict):
        raise ProofError(f"Expected JSON object payload, got: {type(payload).__name__}")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
