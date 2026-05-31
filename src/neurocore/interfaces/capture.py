"""Capture interface for storing records and documents in NeuroCore."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Callable

from neurocore.core.config import NeuroCoreConfig
from neurocore.core.models import MemoryChunk, MemoryDocument, MemoryRecord
from neurocore.core.policies import (
    validate_bucket,
    validate_namespace,
    validate_sensitivity,
)
from neurocore.ingest.chunking import (
    chunk_text_with_offsets,
    classify_content_kind,
)
from neurocore.ingest.normalize import (
    compute_content_fingerprint,
    count_tokens,
    generate_stable_id,
)
from neurocore.runtime import build_summarizer
from neurocore.storage.base import BaseStore


@dataclass(frozen=True)
class CapturePlan:
    content: str
    namespace: str
    bucket: str
    sensitivity: str
    content_format: str
    source_type: str
    metadata: dict[str, object]
    fingerprint: str
    kind: str
    signature: str
    request_tags: tuple[str, ...]
    now: datetime
    document_summary: str | None
    chunk_summary_map: dict[int, str]


@dataclass(frozen=True)
class PreparedCapture:
    index: int
    request: dict[str, object]
    plan: CapturePlan
    signature: str
    record: MemoryRecord | None = None
    document: MemoryDocument | None = None
    chunks: tuple[MemoryChunk, ...] = ()


def capture_memory(
    request: dict[str, object],
    store: BaseStore,
    config: NeuroCoreConfig,
    action_item_generator: Callable[[str], list[str]] | None = None,
) -> dict[str, object]:
    plan = _build_capture_plan(
        request,
        config=config,
        action_item_generator=action_item_generator,
    )
    existing_id = store.find_duplicate(plan.namespace, plan.fingerprint, plan.signature)
    if existing_id is not None:
        return attach_store_warnings(
            _handle_deduplicated_capture(
                store=store,
                config=config,
                existing_id=existing_id,
                plan=plan,
                request_tags=tuple(request.get("tags", ())),
            ),
            store=store,
        )
    if plan.kind == "record":
        return attach_store_warnings(
            _store_record_capture(request, store=store, plan=plan),
            store=store,
        )
    return attach_store_warnings(
        _store_document_capture(request, store=store, config=config, plan=plan),
        store=store,
    )


def capture_many(
    requests: list[dict[str, object]],
    store: BaseStore,
    config: NeuroCoreConfig,
    action_item_generator: Callable[[str], list[str]] | None = None,
) -> dict[str, object]:
    prepared: list[PreparedCapture] = []
    results: list[dict[str, object]] = []
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            results.append(
                _batch_result(index, ok=False, error="request must be an object")
            )
            continue
        try:
            prepared.append(
                _prepare_capture(
                    index=index,
                    request=request,
                    store=store,
                    config=config,
                    action_item_generator=action_item_generator,
                )
            )
        except Exception as exc:
            results.append(_batch_result(index, ok=False, error=str(exc)))

    duplicate_inputs = [
        (item.plan.namespace, item.plan.fingerprint, item.signature)
        for item in prepared
    ]
    duplicates = (
        store.find_duplicates_bulk(duplicate_inputs) if duplicate_inputs else []
    )
    record_entries: list[tuple[MemoryRecord, str]] = []
    record_prepared: list[PreparedCapture] = []
    document_entries: list[tuple[MemoryDocument, list[MemoryChunk], str]] = []
    document_prepared: list[PreparedCapture] = []

    seen_ids: dict[tuple[str, str, str], str] = {}
    for item, existing_id in zip(prepared, duplicates):
        dedup_key = (item.plan.namespace, item.plan.fingerprint, item.signature)
        in_batch_existing_id = seen_ids.get(dedup_key)
        existing_id = in_batch_existing_id or existing_id
        if in_batch_existing_id is not None:
            results.append(
                _batch_result(
                    item.index,
                    ok=True,
                    payload=_capture_response(
                        item_id=in_batch_existing_id,
                        kind=item.plan.kind,
                        namespace=item.plan.namespace,
                        bucket=item.plan.bucket,
                        deduplicated=True,
                        chunk_count=len(item.chunks),
                        storage_outcome=(
                            "document-stored"
                            if item.plan.kind == "document"
                            else "record-stored"
                        ),
                    ),
                )
            )
            continue
        if existing_id is not None:
            payload = _handle_deduplicated_capture(
                store=store,
                config=config,
                existing_id=existing_id,
                plan=item.plan,
                request_tags=tuple(item.request.get("tags", ())),
            )
            results.append(_batch_result(item.index, ok=True, payload=payload))
            continue
        if item.record is not None:
            record_entries.append((item.record, item.signature))
            record_prepared.append(item)
            seen_ids[dedup_key] = item.record.id
            continue
        if item.document is not None:
            document_entries.append((item.document, list(item.chunks), item.signature))
            document_prepared.append(item)
            seen_ids[dedup_key] = item.document.id

    if record_entries:
        try:
            store.save_records_bulk(record_entries)
            for item in record_prepared:
                results.append(
                    _batch_result(
                        item.index,
                        ok=True,
                        payload=_capture_response(
                            item_id=item.record.id,
                            kind="record",
                            namespace=item.plan.namespace,
                            bucket=item.plan.bucket,
                            deduplicated=False,
                            chunk_count=0,
                            storage_outcome="record-stored",
                        ),
                    )
                )
        except Exception as exc:
            error = str(exc)
            for item in record_prepared:
                results.append(_batch_result(item.index, ok=False, error=error))

    if document_entries:
        try:
            store.save_documents_bulk(document_entries)
            for item in document_prepared:
                results.append(
                    _batch_result(
                        item.index,
                        ok=True,
                        payload=_capture_response(
                            item_id=item.document.id,
                            kind="document",
                            namespace=item.plan.namespace,
                            bucket=item.plan.bucket,
                            deduplicated=False,
                            chunk_count=len(item.chunks),
                            storage_outcome="document-stored",
                        ),
                    )
                )
        except Exception as exc:
            error = str(exc)
            for item in document_prepared:
                results.append(_batch_result(item.index, ok=False, error=error))

    ordered = sorted(results, key=lambda item: int(item["index"]))
    warnings = store.pop_warnings()
    return {
        "results": ordered,
        "summary": {
            "processed": len(requests),
            "succeeded": sum(1 for item in ordered if item["ok"]),
            "failed": sum(1 for item in ordered if not item["ok"]),
            "warnings": warnings,
        },
    }


def _build_capture_plan(
    request: dict[str, object],
    *,
    config: NeuroCoreConfig,
    action_item_generator: Callable[[str], list[str]] | None = None,
) -> CapturePlan:
    content = str(request.get("content", "")).strip()
    if not content:
        raise ValueError("content is required")
    if count_tokens(content) > config.max_content_tokens:
        raise ValueError("content exceeds the configured maximum content size")

    namespace = validate_namespace(
        str(request.get("namespace") or config.default_namespace)
    )
    bucket = validate_bucket(str(request.get("bucket")), config.allowed_buckets)
    sensitivity = validate_sensitivity(
        str(request.get("sensitivity") or config.default_sensitivity)
    )
    content_format = str(request.get("content_format") or "markdown").strip()
    source_type = str(request.get("source_type") or "note").strip()
    metadata = dict(request.get("metadata", {}))
    enriched_metadata, enriched_tags = _enrich_content(
        content,
        action_item_generator=action_item_generator
        or _build_action_item_generator(config),
    )
    now = _parse_request_created_at(request.get("created_at")) or datetime.now(UTC)
    metadata = {**enriched_metadata, **metadata}
    fingerprint = compute_content_fingerprint(content)
    force_kind = str(request.get("force_kind") or "").strip()
    token_count = count_tokens(content)
    if force_kind:
        if force_kind not in {"record", "document"}:
            raise ValueError("force_kind must be 'record' or 'document'")
        if force_kind == "record" and token_count > config.max_atomic_tokens:
            raise ValueError("force_kind=record exceeds max_atomic_tokens")
        kind = force_kind
    else:
        kind = classify_content_kind(content, config)
    request_tags = _merge_tags(tuple(request.get("tags", ())), enriched_tags)
    document_summary = _optional_summary_text(request.get("summary"))
    chunk_summary_map = _parse_chunk_summary_map(request.get("chunk_summaries"))
    return CapturePlan(
        content=content,
        namespace=namespace,
        bucket=bucket,
        sensitivity=sensitivity,
        content_format=content_format,
        source_type=source_type,
        metadata=metadata,
        fingerprint=fingerprint,
        kind=kind,
        signature=f"{kind}:{source_type}:{content_format}:{sensitivity}",
        request_tags=request_tags,
        now=now,
        document_summary=document_summary,
        chunk_summary_map=chunk_summary_map,
    )


def _prepare_capture(
    *,
    index: int,
    request: dict[str, object],
    store: BaseStore,
    config: NeuroCoreConfig,
    action_item_generator: Callable[[str], list[str]] | None = None,
) -> PreparedCapture:
    del store
    plan = _build_capture_plan(
        request,
        config=config,
        action_item_generator=action_item_generator,
    )
    if plan.kind == "record":
        record = _record_from_request(request, plan=plan)
        return PreparedCapture(
            index=index,
            request=request,
            plan=plan,
            signature=plan.signature,
            record=record,
        )
    document = _document_from_request(request, plan=plan)
    chunks = tuple(_build_document_chunks(document, plan=plan, config=config))
    return PreparedCapture(
        index=index,
        request=request,
        plan=plan,
        signature=plan.signature,
        document=document,
        chunks=chunks,
    )


def _handle_deduplicated_capture(
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    existing_id: str,
    plan: CapturePlan,
    request_tags: tuple[str, ...],
) -> dict[str, object]:
    _merge_duplicate_metadata(
        store=store,
        existing_id=existing_id,
        metadata=plan.metadata,
        tags=request_tags,
        config=config,
    )
    chunk_count = len(store.get_document_chunk_ids(existing_id))
    return _capture_response(
        item_id=existing_id,
        kind="document" if chunk_count else "record",
        namespace=plan.namespace,
        bucket=plan.bucket,
        deduplicated=True,
        chunk_count=chunk_count,
        storage_outcome="document-stored" if chunk_count else "record-stored",
    )


def _store_record_capture(
    request: dict[str, object],
    *,
    store: BaseStore,
    plan: CapturePlan,
) -> dict[str, object]:
    record = _record_from_request(request, plan=plan)
    store.save_record(record, signature=plan.signature)
    return _capture_response(
        item_id=record.id,
        kind="record",
        namespace=plan.namespace,
        bucket=plan.bucket,
        deduplicated=False,
        chunk_count=0,
        storage_outcome="record-stored",
    )


def _store_document_capture(
    request: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    plan: CapturePlan,
) -> dict[str, object]:
    document = _document_from_request(request, plan=plan)
    chunks = _build_document_chunks(document, plan=plan, config=config)
    store.save_document(document, chunks, signature=plan.signature)
    return _capture_response(
        item_id=document.id,
        kind="document",
        namespace=plan.namespace,
        bucket=plan.bucket,
        deduplicated=False,
        chunk_count=len(chunks),
        storage_outcome="document-stored",
    )


def _build_document_chunks(
    document: MemoryDocument,
    *,
    plan: CapturePlan,
    config: NeuroCoreConfig,
) -> list[MemoryChunk]:
    chunk_values = chunk_text_with_offsets(
        plan.content,
        target_tokens=config.target_chunk_tokens,
        max_tokens=config.max_chunk_tokens,
        overlap_tokens=config.chunk_overlap_tokens,
    )
    return [
        MemoryChunk(
            id=generate_stable_id("chunk", document.id, str(ordinal)),
            document_id=document.id,
            namespace=plan.namespace,
            bucket=plan.bucket,
            ordinal=ordinal,
            chunk_text=chunk_value.text,
            token_count=count_tokens(chunk_value.text),
            sensitivity=plan.sensitivity,
            metadata=plan.metadata,
            created_at=plan.now,
            start_offset=chunk_value.start_offset,
            end_offset=chunk_value.end_offset,
            summary=plan.chunk_summary_map.get(ordinal),
        )
        for ordinal, chunk_value in enumerate(chunk_values, start=1)
    ]


def _record_from_request(
    request: dict[str, object], *, plan: CapturePlan
) -> MemoryRecord:
    return MemoryRecord(
        id=generate_stable_id(
            "rec",
            plan.namespace,
            plan.bucket,
            plan.fingerprint,
            plan.source_type,
            plan.sensitivity,
        ),
        namespace=plan.namespace,
        bucket=plan.bucket,
        content=plan.content,
        content_format=plan.content_format,
        source_type=plan.source_type,
        sensitivity=plan.sensitivity,
        metadata=plan.metadata,
        content_fingerprint=plan.fingerprint,
        created_at=plan.now,
        updated_at=plan.now,
        title=request.get("title"),
        tags=plan.request_tags,
        external_id=request.get("external_id"),
        idempotency_key=request.get("idempotency_key"),
        supersedes_id=request.get("supersedes_id"),
    )


def _document_from_request(
    request: dict[str, object], *, plan: CapturePlan
) -> MemoryDocument:
    return MemoryDocument(
        id=generate_stable_id(
            "doc",
            plan.namespace,
            plan.bucket,
            plan.fingerprint,
            plan.source_type,
            plan.sensitivity,
        ),
        namespace=plan.namespace,
        bucket=plan.bucket,
        title=str(request.get("title") or _synthetic_title(plan.content)),
        raw_content=plan.content,
        source_locator=plan.metadata.get("source_url") if plan.metadata else None,
        source_type=plan.source_type,
        sensitivity=plan.sensitivity,
        metadata=plan.metadata,
        content_fingerprint=plan.fingerprint,
        created_at=plan.now,
        updated_at=plan.now,
        external_id=request.get("external_id"),
        tags=plan.request_tags,
        summary=plan.document_summary,
        supersedes_id=request.get("supersedes_id"),
    )


def _capture_response(
    *,
    item_id: str,
    kind: str,
    namespace: str,
    bucket: str,
    deduplicated: bool,
    chunk_count: int,
    storage_outcome: str,
) -> dict[str, object]:
    return {
        "id": item_id,
        "kind": kind,
        "namespace": namespace,
        "bucket": bucket,
        "stored": True,
        "storage_outcome": storage_outcome,
        "deduplicated": deduplicated,
        "chunk_count": chunk_count,
        "warnings": [],
    }


def _synthetic_title(content: str) -> str:
    return " ".join(content.split()[:8]) or "Untitled document"


def _parse_request_created_at(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("created_at must be an ISO timestamp or datetime")


def _merge_duplicate_metadata(
    store: BaseStore,
    existing_id: str,
    metadata: dict[str, object],
    tags: tuple[str, ...],
    config: NeuroCoreConfig,
) -> None:
    if not config.dedup_merge_metadata:
        return
    record = store.get_record(existing_id, include_archived=True)
    if record is not None:
        store.update_record(
            existing_id,
            patch={
                "metadata": {**record.metadata, **metadata},
                "tags": _merge_tags(record.tags, tags),
            },
            mode="in_place",
        )
        return
    document = store.get_document(existing_id, include_archived=True)
    if document is not None:
        store.update_document(
            existing_id,
            patch={
                "metadata": {**document.metadata, **metadata},
                "tags": _merge_tags(document.tags, tags),
            },
            mode="in_place",
        )


def _optional_summary_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_chunk_summary_map(value: object) -> dict[int, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, (list, tuple)):
        raise ValueError("chunk_summaries must be a list of objects")
    summary_map: dict[int, str] = {}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("chunk_summaries entries must be objects")
        ordinal_raw = item.get("ordinal")
        if isinstance(ordinal_raw, bool):
            raise ValueError("chunk_summaries ordinal must be an integer")
        try:
            ordinal = int(ordinal_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("chunk_summaries ordinal must be an integer") from exc
        if ordinal < 1:
            raise ValueError("chunk_summaries ordinal must be >= 1")
        summary = _optional_summary_text(item.get("summary"))
        if summary is None:
            raise ValueError("chunk_summaries summary is required")
        summary_map[ordinal] = summary
    return summary_map


def attach_store_warnings(
    payload: dict[str, object],
    *,
    store: BaseStore,
) -> dict[str, object]:
    """Attach transient store warnings to a response payload."""
    warnings = list(payload.get("warnings") or [])
    warnings.extend(store.pop_warnings())
    payload["warnings"] = list(dict.fromkeys(str(item) for item in warnings if item))
    return payload


def _batch_result(
    index: int,
    *,
    ok: bool,
    payload: dict[str, object] | None = None,
    error: str = "",
) -> dict[str, object]:
    return {
        "index": index,
        "ok": ok,
        "error": error,
        "payload": payload,
    }


def _merge_tags(existing: tuple[str, ...], new: tuple[str, ...]) -> tuple[str, ...]:
    merged = list(existing)
    for tag in new:
        if tag not in merged:
            merged.append(tag)
    return tuple(merged)


def _enrich_content(
    content: str,
    *,
    action_item_generator: Callable[[str], list[str]] | None = None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    urls = re.findall(r"https?://\S+", content)
    cves = re.findall(r"\bCVE-\d{4}-\d{4,7}\b", content, flags=re.IGNORECASE)
    cwes = re.findall(r"\bCWE-\d+\b", content, flags=re.IGNORECASE)
    attack_ids = re.findall(r"\bT\d{4}(?:\.\d{3})?\b", content, flags=re.IGNORECASE)
    severity_markers = _ordered_unique(
        match.lower()
        for match in re.findall(
            r"\b(critical|high|medium|low|informational)\b",
            content,
            flags=re.IGNORECASE,
        )
    )
    action_items = _extract_action_items(content)
    if action_item_generator is not None:
        try:
            generated_actions = _ordered_unique(action_item_generator(content))
        except Exception:
            generated_actions = []
        if generated_actions:
            action_items = generated_actions
    metadata: dict[str, object] = {}
    if urls:
        metadata["extracted_urls"] = _ordered_unique(urls)
    if cves:
        metadata["extracted_cves"] = _ordered_unique(value.upper() for value in cves)
    if cwes:
        metadata["extracted_cwes"] = _ordered_unique(value.upper() for value in cwes)
    if attack_ids:
        metadata["extracted_attack_ids"] = _ordered_unique(
            value.upper() for value in attack_ids
        )
    if severity_markers:
        metadata["severity_markers"] = severity_markers
    if action_items:
        metadata["suggested_actions"] = action_items
        metadata["action_items_strategy"] = (
            "model-backed" if action_item_generator is not None else "deterministic"
        )
    tags = tuple(severity_markers)
    return metadata, tags


def _extract_action_items(content: str) -> list[str]:
    actions: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", content):
        stripped = sentence.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith(("next action:", "todo:", "action:")):
            actions.append(stripped.split(":", 1)[1].strip() or stripped)
    return _ordered_unique(actions)


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def _build_action_item_generator(
    config: NeuroCoreConfig,
) -> Callable[[str], list[str]] | None:
    if not config.enable_multi_model_consensus:
        return None
    try:
        summarizer = build_summarizer(config)
    except Exception:
        return None

    def generator(content: str) -> list[str]:
        prompt = (
            "Extract up to three concrete operator action items from the following "
            "security memory. Prefer imperative, review-ready next steps.\n\n"
            f"{content}"
        )
        summary = summarizer.summarize(prompt, max_sentences=3)
        text = str(getattr(summary, "summary", "")).strip()
        if not text:
            return []
        actions = [
            item.strip(" -")
            for item in re.split(r"(?:\n+|(?<=[.!?])\s+)", text)
            if item.strip()
        ]
        return actions[:3]

    return generator
