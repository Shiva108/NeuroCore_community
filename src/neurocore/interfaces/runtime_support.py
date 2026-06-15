"""Shared runtime helpers for supervision and redacted audit details."""

from __future__ import annotations

import concurrent.futures
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from neurocore.storage.base import BaseStore

RUNTIME_SOURCE_SURFACE_KEY = "_runtime_source_surface"
RUNTIME_ACTION_KEY = "_runtime_action"

_ACTION_ALIASES = {
    "audit": "audit_memory",
    "background-summaries": "run_background_summaries",
    "briefing": "generate_briefing",
    "capture-event": "capture_session_event",
    "checkpoint": "checkpoint_session",
    "create-brain": "create_brain",
    "delete": "delete_memory",
    "describe-tools": "describe_tools",
    "ingest": "ingest_event",
    "list-brains": "list_brains",
    "list-protocols": "list_protocols",
    "protocol": "run_protocol",
    "query": "query_memory",
    "report": "generate_consensus_report",
    "resume": "resume_session",
    "run-due": "run_due_jobs",
    "select-brain": "select_brain",
    "session-capture": "capture_session_event",
    "session-resume": "resume_session",
    "summaries-run": "run_background_summaries",
    "update": "update_memory",
    "validate-extension": "validate_extension",
}
_REQUEST_SUMMARY_KEYS = (
    "action",
    "allowed_buckets",
    "brain_id",
    "enabled_only",
    "id",
    "ids",
    "include_archived",
    "interval_minutes",
    "job_id",
    "job_type",
    "limit",
    "mode",
    "name",
    "namespace",
    "operation",
    "run_at",
    "schedule_kind",
    "scope",
    "session_id",
)
_RESULT_SUMMARY_KEYS = (
    "action",
    "count",
    "created",
    "deleted",
    "failed",
    "id",
    "ignored",
    "kind",
    "mode",
    "processed",
    "stored",
    "supported",
    "updated",
)
_SANITIZE_ACTION_RE = re.compile(r"[^a-z0-9]+")
_RUNTIME_LOCK = threading.Lock()
_RUNTIME_FAILURE_STREAKS: dict[str, int] = {}


@dataclass(frozen=True)
class SupervisedResult:
    """Result for a supervised runtime call."""

    status: str
    result: Any = None
    error: str | None = None
    duration_ms: int = 0
    timed_out: bool = False
    consecutive_failures: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


def attach_runtime_metadata(
    request: dict[str, object],
    *,
    source_surface: str,
    action: str,
) -> dict[str, object]:
    """Return a request copy with private runtime metadata attached."""
    payload = dict(request)
    payload[RUNTIME_SOURCE_SURFACE_KEY] = source_surface
    payload[RUNTIME_ACTION_KEY] = normalize_runtime_action(action)
    return payload


def runtime_source_surface(
    request: dict[str, object] | None, *, default: str = "library"
) -> str:
    if not isinstance(request, dict):
        return default
    value = str(request.get(RUNTIME_SOURCE_SURFACE_KEY) or "").strip().lower()
    return value or default


def runtime_action_name(
    request: dict[str, object] | None,
    *,
    default: str,
) -> str:
    if isinstance(request, dict):
        raw = str(request.get(RUNTIME_ACTION_KEY) or "").strip()
        if raw:
            return normalize_runtime_action(raw)
    return normalize_runtime_action(default)


def normalize_runtime_action(action: str) -> str:
    text = _ACTION_ALIASES.get(action.strip().lower(), action.strip().lower())
    normalized = _SANITIZE_ACTION_RE.sub("_", text).strip("_")
    return normalized or "runtime_action"


def supervise_call(
    fn: Callable[[], Any],
    *,
    source_surface: str,
    action: str,
    timeout_seconds: float | None = None,
) -> SupervisedResult:
    """Run a callable with optional timeout and local failure streak tracking."""
    key = f"{source_surface}:{normalize_runtime_action(action)}"
    started = time.perf_counter()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        try:
            if timeout_seconds is None or timeout_seconds <= 0:
                result = future.result()
            else:
                result = future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return SupervisedResult(
                status="timed_out",
                error=f"timed out after {timeout_seconds:.1f}s",
                duration_ms=duration_ms,
                timed_out=True,
                consecutive_failures=_record_failure(key),
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return SupervisedResult(
                status="failed",
                error=str(exc),
                duration_ms=duration_ms,
                consecutive_failures=_record_failure(key),
            )

        _clear_failure_streak(key)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return SupervisedResult(
            status="succeeded",
            result=result,
            duration_ms=duration_ms,
        )
    finally:
        executor.shutdown(wait=False)


def record_runtime_audit(
    store: BaseStore | None,
    *,
    actor: str,
    operation: str,
    request: dict[str, object] | None,
    status: str,
    result: Any = None,
    error: str | None = None,
    duration_ms: int | None = None,
    consecutive_failures: int | None = None,
    timed_out: bool | None = None,
    extra_details: dict[str, object] | None = None,
    target_ids: list[str] | None = None,
) -> None:
    """Persist one redacted runtime audit event when a store is available."""
    if store is None:
        return
    details = {
        "source_surface": runtime_source_surface(request),
        "action": runtime_action_name(request, default=operation),
        "status": status,
        "request_summary": summarize_request(request),
        "result_summary": summarize_result(result),
    }
    if error:
        details["error"] = error
    if duration_ms is not None:
        details["duration_ms"] = duration_ms
    if consecutive_failures is not None:
        details["consecutive_failures"] = consecutive_failures
    if timed_out:
        details["timed_out"] = True
    if extra_details:
        details.update(extra_details)
    store.record_audit(
        actor=actor,
        operation=operation,
        target_ids=target_ids or collect_target_ids(request, result),
        outcome=status,
        details=details,
    )


def summarize_request(request: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(request, dict):
        return {}
    summary: dict[str, object] = {}
    for key in _REQUEST_SUMMARY_KEYS:
        if key not in request:
            continue
        value = request.get(key)
        if key in {"allowed_buckets", "ids"}:
            summary[key] = [str(item) for item in list(value or [])]
        else:
            summary[key] = value
    if "payload" in request and isinstance(request["payload"], dict):
        summary["payload"] = _summarize_nested_mapping(request["payload"])
    if "query_request" in request and isinstance(request["query_request"], dict):
        summary["query_request"] = {
            "keys": sorted(str(key) for key in request["query_request"].keys())
        }
    summary["keys"] = sorted(
        key
        for key in request
        if not key.startswith("_") and key not in {"content", "summary", "query_text"}
    )
    return summary


def summarize_result(result: Any) -> dict[str, object]:
    if not isinstance(result, dict):
        return {}
    summary: dict[str, object] = {}
    for key in _RESULT_SUMMARY_KEYS:
        if key in result:
            summary[key] = result[key]
    if "processed_ids" in result:
        summary["processed_ids"] = [str(item) for item in list(result["processed_ids"])]
    if "job" in result and isinstance(result["job"], dict):
        summary["job"] = {
            "job_id": result["job"].get("job_id"),
            "job_type": result["job"].get("job_type"),
            "enabled": result["job"].get("enabled"),
            "last_status": result["job"].get("last_status"),
        }
    if "jobs" in result and isinstance(result["jobs"], list):
        summary["jobs"] = {
            "count": len(result["jobs"]),
            "statuses": [
                str(item.get("status") or "")
                for item in result["jobs"]
                if isinstance(item, dict)
            ],
        }
    if "results" in result and isinstance(result["results"], list):
        summary["results_count"] = len(result["results"])
    if "findings" in result and isinstance(result["findings"], list):
        summary["findings_count"] = len(result["findings"])
    if "candidate_actions" in result and isinstance(result["candidate_actions"], list):
        summary["candidate_actions_count"] = len(result["candidate_actions"])
    if "warnings" in result and isinstance(result["warnings"], list):
        summary["warnings_count"] = len(result["warnings"])
    if "protocol" in result and isinstance(result["protocol"], dict):
        summary["protocol"] = {
            "name": result["protocol"].get("name"),
            "query_preset": result["protocol"].get("query_preset"),
        }
    return summary


def collect_target_ids(
    request: dict[str, object] | None,
    result: Any,
) -> list[str]:
    target_ids: list[str] = []
    if isinstance(request, dict):
        if request.get("id"):
            target_ids.append(str(request["id"]))
        if request.get("ids"):
            target_ids.extend(str(item) for item in list(request.get("ids") or []))
        if request.get("job_id"):
            target_ids.append(str(request["job_id"]))
    if isinstance(result, dict):
        if result.get("id"):
            target_ids.append(str(result["id"]))
        if isinstance(result.get("job"), dict) and result["job"].get("job_id"):
            target_ids.append(str(result["job"]["job_id"]))
        if result.get("job_id"):
            target_ids.append(str(result["job_id"]))
        if result.get("processed_ids"):
            target_ids.extend(str(item) for item in list(result["processed_ids"]))
    deduped: list[str] = []
    seen: set[str] = set()
    for item_id in target_ids:
        if item_id and item_id not in seen:
            deduped.append(item_id)
            seen.add(item_id)
    return deduped


def _record_failure(key: str) -> int:
    with _RUNTIME_LOCK:
        count = _RUNTIME_FAILURE_STREAKS.get(key, 0) + 1
        _RUNTIME_FAILURE_STREAKS[key] = count
        return count


def _clear_failure_streak(key: str) -> None:
    with _RUNTIME_LOCK:
        _RUNTIME_FAILURE_STREAKS.pop(key, None)


def _summarize_nested_mapping(payload: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {"keys": sorted(str(key) for key in payload.keys())}
    for key in ("action", "operation", "job_id", "job_type", "scope"):
        if key in payload:
            summary[key] = payload[key]
    return summary
