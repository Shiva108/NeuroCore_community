"""Administrative interfaces for updating and deleting NeuroCore content."""

from __future__ import annotations

import json

from neurocore.core.config import NeuroCoreConfig
from neurocore.core.models import MemoryDocument, MemoryRecord
from neurocore.core.policies import validate_bucket, validate_namespace
from neurocore.governance.validation import find_secret_like_values
from neurocore.interfaces.capture import attach_store_warnings, capture_memory
from neurocore.interfaces.runtime_support import record_runtime_audit
from neurocore.maintenance.sqlite import (
    maintain_local_sqlite,
    normalize_sqlite_maintenance_action,
    record_target_maintenance_audit,
    resolve_local_sqlite_targets,
)
from neurocore.runtime import build_storage_backend_status
from neurocore.storage.local_only_sealed_mirrored_store import (
    LocalOnlySealedMirroredStore,
)
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage.base import BaseStore


def update_memory(
    request: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    _ensure_admin_enabled(config)
    item_id = str(request["id"])
    patch = dict(request.get("patch", {}))
    mode = str(request.get("mode", "in_place"))
    actor = str(request.get("actor", "system"))

    if mode == "replace_content":
        replacement = _replace_content(item_id, patch, store, config)
        response = attach_store_warnings(
            {
                "id": replacement["id"],
                "updated": True,
                "mode": mode,
                "superseded_id": item_id,
                "warnings": replacement["warnings"],
            },
            store=store,
        )
        record_runtime_audit(
            store,
            actor=actor,
            operation="update",
            request=request,
            status="succeeded",
            result=response,
            target_ids=[item_id, replacement["id"]],
        )
        return response

    if store.get_record(item_id, include_archived=True) is not None:
        store.update_record(item_id, patch=patch, mode=mode)
    elif store.get_document(item_id, include_archived=True) is not None:
        store.update_document(item_id, patch=patch, mode=mode)
    else:
        raise KeyError(item_id)

    response = attach_store_warnings(
        {
            "id": item_id,
            "updated": True,
            "mode": mode,
            "superseded_id": None,
            "warnings": [],
        },
        store=store,
    )
    record_runtime_audit(
        store,
        actor=actor,
        operation="update",
        request=request,
        status="succeeded",
        result=response,
        target_ids=[item_id],
    )
    return response


def delete_memory(
    request: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    _ensure_admin_enabled(config)
    item_id = str(request["id"])
    mode = str(request.get("mode", "soft_delete"))
    actor = str(request.get("actor", "system"))
    if mode == "hard_delete" and not config.allow_hard_delete:
        raise PermissionError("Hard delete is disabled")

    if mode == "hard_delete":
        store.hard_delete(item_id)
    else:
        store.soft_delete(item_id, reason=str(request.get("reason", "")))
    response = attach_store_warnings(
        {"id": item_id, "deleted": True, "mode": mode, "warnings": []},
        store=store,
    )
    record_runtime_audit(
        store,
        actor=actor,
        operation="delete",
        request=request,
        status="succeeded",
        result=response,
        target_ids=[item_id],
    )
    return response


def reindex_memory(
    request: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    _ensure_admin_enabled(config)
    ids = [str(item_id) for item_id in request.get("ids", [])]
    actor = str(request.get("actor", "system"))
    processed, failed, warnings = store.reindex(
        ids,
        scope=str(request.get("scope", "records")),
        semantic_backend=config.semantic_backend,
        semantic_model_name=config.semantic_model_name,
    )
    response = attach_store_warnings(
        {"processed": processed, "failed": failed, "warnings": warnings},
        store=store,
    )
    record_runtime_audit(
        store,
        actor=actor,
        operation="reindex",
        request=request,
        status="succeeded",
        result=response,
        target_ids=ids,
    )
    return response


def audit_memory(
    request: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    _ensure_admin_enabled(config)
    namespace = validate_namespace(
        str(request.get("namespace") or config.default_namespace)
    )
    allowed_buckets = tuple(
        validate_bucket(str(bucket), config.allowed_buckets)
        for bucket in (request.get("allowed_buckets") or config.allowed_buckets)
    )
    include_archived = _parse_boolish(request.get("include_archived", False))
    actor = str(request.get("actor", "system"))

    findings: list[dict[str, object]] = []
    candidate_actions: list[dict[str, object]] = []

    for record in store.list_records(include_archived=include_archived):
        if record.namespace != namespace or record.bucket not in allowed_buckets:
            continue
        item_findings = _scan_record(record)
        findings.extend(item_findings)
        candidate_actions.extend(_candidate_actions(item_findings))

    for document in store.list_documents(include_archived=include_archived):
        if document.namespace != namespace or document.bucket not in allowed_buckets:
            continue
        item_findings = _scan_document(document)
        findings.extend(item_findings)
        candidate_actions.extend(_candidate_actions(item_findings))

    response = attach_store_warnings(
        {
            "namespace": namespace,
            "allowed_buckets": list(allowed_buckets),
            "include_archived": include_archived,
            "findings": findings,
            "candidate_actions": candidate_actions,
            "warnings": [],
        },
        store=store,
    )
    record_runtime_audit(
        store,
        actor=actor,
        operation="audit",
        request=request,
        status="succeeded",
        result=response,
        target_ids=sorted({str(finding["item_id"]) for finding in findings}),
    )
    return response


def sync_storage(
    request: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    _ensure_admin_enabled(config)
    action = str(request.get("action") or "status").strip().lower()
    actor = str(request.get("actor", "system"))
    if action == "status":
        response = {
            "action": "status",
            "supported": isinstance(
                store,
                (MirroredStore, LocalOnlySealedMirroredStore),
            ),
            "storage_backend": build_storage_backend_status(
                config,
                store=store,
            ).to_dict(),
            "warnings": [],
        }
        record_runtime_audit(
            store,
            actor=actor,
            operation="sync_storage",
            request=request,
            status="succeeded",
            result=response,
            extra_details={"sync_action": action},
        )
        return response
    if not isinstance(store, (MirroredStore, LocalOnlySealedMirroredStore)):
        raise ValueError(
            "Storage sync operations require NEUROCORE_STORAGE_BACKEND=mirror"
        )
    try:
        store.mark_sync_started(action)
        if action == "backfill_local_to_cloud":
            counts = store.backfill_local_to_cloud()
            parity = None
        elif action == "repair_local_from_cloud":
            counts = store.repair_local_from_cloud()
            parity = None
        elif action == "repair_cloud_from_local":
            counts = store.repair_cloud_from_local()
            parity = None
        elif action == "reconcile_union":
            parity = store.reconcile_union()
            counts = parity.get("counts")
        elif action == "verify_parity":
            parity = store.verify_parity()
            counts = parity.get("repair_counts")
        else:
            raise ValueError(
                "action must be status, backfill_local_to_cloud, "
                "repair_local_from_cloud, repair_cloud_from_local, "
                "reconcile_union, or verify_parity"
            )
    except Exception as exc:
        store.mark_sync_finished("failed", error=str(exc))
        record_runtime_audit(
            store,
            actor=actor,
            operation="sync_storage",
            request=request,
            status="failed",
            error=str(exc),
            extra_details={"sync_action": action},
        )
        raise
    store.mark_sync_finished("success")
    response = attach_store_warnings(
        {
            "action": action,
            "supported": True,
            "counts": counts,
            "parity": parity,
            "storage_backend": build_storage_backend_status(
                config,
                store=store,
            ).to_dict(),
            "warnings": [],
        },
        store=store,
    )
    record_runtime_audit(
        store,
        actor=actor,
        operation="sync_storage",
        request=request,
        status="succeeded",
        result=response,
        extra_details={"counts": counts, "parity": parity, "sync_action": action},
    )
    return response


def maintain_storage(
    request: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    _ensure_admin_enabled(config)
    actor = str(request.get("actor", "system"))
    action = normalize_sqlite_maintenance_action(str(request.get("action") or "report"))
    response = attach_store_warnings(
        maintain_local_sqlite(action, config=config, store=store),
        store=store,
    )
    if action != "report":
        for target in resolve_local_sqlite_targets(config=config, store=store):
            record_target_maintenance_audit(
                target,
                actor=actor,
                action=action,
                outcome="succeeded",
            )
    record_runtime_audit(
        store,
        actor=actor,
        operation="maintain_storage",
        request=request,
        status="succeeded",
        result=response,
        extra_details={"maintenance_action": action},
        target_ids=[str(target["name"]) for target in response["targets"]],
    )
    return response


def _ensure_admin_enabled(config: NeuroCoreConfig) -> None:
    if not config.enable_admin_surface:
        raise PermissionError("Admin surface is disabled")


def _replace_content(
    item_id: str, patch: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    record = store.get_record(item_id, include_archived=True)
    if record is not None:
        replacement = capture_memory(
            {
                "namespace": record.namespace,
                "bucket": record.bucket,
                "sensitivity": record.sensitivity,
                "content": patch["content"],
                "content_format": record.content_format,
                "source_type": record.source_type,
                "title": patch.get("title", record.title),
                "tags": patch.get("tags", record.tags),
                "metadata": patch.get("metadata", record.metadata),
                "external_id": record.external_id,
                "created_at": record.created_at,
                "supersedes_id": item_id,
            },
            store=store,
            config=config,
        )
        store.soft_delete(item_id, reason="superseded")
        return replacement

    document = store.get_document(item_id, include_archived=True)
    if document is not None:
        replacement = capture_memory(
            {
                "namespace": document.namespace,
                "bucket": document.bucket,
                "sensitivity": document.sensitivity,
                "content": patch["content"],
                "content_format": "markdown",
                "source_type": document.source_type,
                "title": patch.get("title", document.title),
                "tags": patch.get("tags", document.tags),
                "metadata": patch.get("metadata", document.metadata),
                "external_id": document.external_id,
                "created_at": document.created_at,
                "supersedes_id": item_id,
                "force_kind": "document",
            },
            store=store,
            config=config,
        )
        store.soft_delete(item_id, reason="superseded")
        return replacement

    raise KeyError(item_id)


def _scan_record(record: MemoryRecord) -> list[dict[str, object]]:
    return _scan_fields(
        item_id=record.id,
        item_kind="record",
        namespace=record.namespace,
        bucket=record.bucket,
        sensitivity=record.sensitivity,
        title=record.title,
        content_field_name="content",
        content_value=record.content,
        metadata=record.metadata,
    )


def _scan_document(document: MemoryDocument) -> list[dict[str, object]]:
    return _scan_fields(
        item_id=document.id,
        item_kind="document",
        namespace=document.namespace,
        bucket=document.bucket,
        sensitivity=document.sensitivity,
        title=document.title,
        content_field_name="raw_content",
        content_value=document.raw_content,
        metadata=document.metadata,
    )


def _scan_fields(
    *,
    item_id: str,
    item_kind: str,
    namespace: str,
    bucket: str,
    sensitivity: str,
    title: str | None,
    content_field_name: str,
    content_value: str | None,
    metadata: dict[str, object],
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    if title:
        findings.extend(
            _match_findings(
                item_id=item_id,
                item_kind=item_kind,
                namespace=namespace,
                bucket=bucket,
                sensitivity=sensitivity,
                field="title",
                text=title,
            )
        )
    if content_value:
        findings.extend(
            _match_findings(
                item_id=item_id,
                item_kind=item_kind,
                namespace=namespace,
                bucket=bucket,
                sensitivity=sensitivity,
                field=content_field_name,
                text=content_value,
            )
        )
    for field, value in _metadata_strings(metadata):
        findings.extend(
            _match_findings(
                item_id=item_id,
                item_kind=item_kind,
                namespace=namespace,
                bucket=bucket,
                sensitivity=sensitivity,
                field=field,
                text=value,
            )
        )
    return findings


def _match_findings(
    *,
    item_id: str,
    item_kind: str,
    namespace: str,
    bucket: str,
    sensitivity: str,
    field: str,
    text: str,
) -> list[dict[str, object]]:
    return [
        {
            "item_id": item_id,
            "item_kind": item_kind,
            "namespace": namespace,
            "bucket": bucket,
            "sensitivity": sensitivity,
            "field": field,
            "match": match,
            "snippet": _snippet(text, match),
        }
        for match in find_secret_like_values(text)
    ]


def _candidate_actions(findings: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    soft_delete_items: set[str] = set()
    actions: list[dict[str, object]] = []
    for finding in findings:
        field = str(finding["field"])
        item_id = str(finding["item_id"])
        if field in {"content", "raw_content"}:
            action = {
                "item_id": item_id,
                "item_kind": finding["item_kind"],
                "field": field,
                "action": "manual_redact_content",
                "via": "update_memory",
                "mode": "replace_content",
            }
            key = (item_id, field, "manual_redact_content")
        else:
            action = {
                "item_id": item_id,
                "item_kind": finding["item_kind"],
                "field": field,
                "action": "manual_redact_metadata",
                "via": "update_memory",
                "mode": "in_place",
            }
            key = (item_id, field, "manual_redact_metadata")
        if key not in seen:
            actions.append(action)
            seen.add(key)
        if item_id not in soft_delete_items:
            actions.append(
                {
                    "item_id": item_id,
                    "item_kind": finding["item_kind"],
                    "field": "item",
                    "action": "soft_delete_item",
                    "via": "delete_memory",
                    "mode": "soft_delete",
                }
            )
            soft_delete_items.add(item_id)
    return actions


def _metadata_strings(
    metadata: dict[str, object], *, prefix: str = "metadata"
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for key, value in metadata.items():
        field = f"{prefix}.{key}"
        if isinstance(value, dict):
            values.extend(_metadata_strings(value, prefix=field))
        elif isinstance(value, (list, tuple)):
            values.append((field, json.dumps(value, sort_keys=True)))
        else:
            values.append((field, str(value)))
    return values


def _snippet(text: str, match: str, *, limit: int = 120) -> str:
    index = text.find(match)
    if index < 0:
        return text[:limit]
    start = max(index - 20, 0)
    end = min(index + len(match) + 20, len(text))
    snippet = text[start:end]
    if len(snippet) > limit:
        return snippet[:limit]
    return snippet


def _parse_boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
