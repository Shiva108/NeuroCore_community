"""Dashboard data interface for NeuroCore."""

from __future__ import annotations

from neurocore.core.brains import resolve_namespace_for_brain
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.brains import list_brains
from neurocore.interfaces.connectors import list_connector_statuses
from neurocore.interfaces.protocols import prioritize_memory_results
from neurocore.interfaces.reporting import build_reporting_status
from neurocore.runtime import (
    build_production_backend_choice,
    build_storage_backend_status,
)
from neurocore.storage.base import BaseStore


def build_dashboard_data(
    store: BaseStore,
    config: NeuroCoreConfig,
    *,
    bucket_filter: str | None = None,
    brain_id: str | None = None,
) -> dict[str, object]:
    """Build a dashboard-safe snapshot of non-sealed repository activity."""
    active_namespace, resolved_brain_id, brain_meta = resolve_namespace_for_brain(
        store=store,
        default_namespace=config.default_namespace,
        brain_id=brain_id,
        namespace=None,
    )
    records = [
        record
        for record in store.list_records(include_archived=True)
        if record.sensitivity != "sealed"
        and record.namespace == active_namespace
        and (bucket_filter is None or record.bucket == bucket_filter)
    ]
    documents = [
        document
        for document in store.list_documents(include_archived=True)
        if document.sensitivity != "sealed"
        and document.namespace == active_namespace
        and (bucket_filter is None or document.bucket == bucket_filter)
    ]
    recent_records = []
    for record in records[:10]:
        recent_records.append(
            {
                "id": record.id,
                "title": record.title,
                "content": record.content,
                "namespace": record.namespace,
                "bucket": record.bucket,
                "archived": record.archived_at is not None,
            }
        )
    recent_documents = []
    for document in documents[:10]:
        recent_documents.append(
            {
                "id": document.id,
                "title": document.title,
                "namespace": document.namespace,
                "bucket": document.bucket,
                "summary": document.summary,
                "archived": document.archived_at is not None,
            }
        )
    prioritized_feed = prioritize_memory_results(
        [
            {
                "id": record.id,
                "namespace": record.namespace,
                "bucket": record.bucket,
                "content_preview": record.content[:160],
                "metadata": {
                    **dict(record.metadata),
                    "tags": list(record.tags),
                },
            }
            for record in records[:24]
        ],
        strategy="severity+importance+validated-findings+operator-concern+recency",
    )

    return {
        "stats": {
            "record_count": len(records),
            "document_count": len(documents),
            "archived_document_count": sum(
                1 for document in documents if document.archived_at is not None
            ),
            "summarized_document_count": sum(
                1 for document in documents if document.summary
            ),
        },
        "recent_documents": recent_documents,
        "recent_records": recent_records,
        "recent_audit_events": store.list_audit_events(limit=10),
        "brains": list_brains({"include_archived": True}, store=store)["brains"],
        "active_brain_id": resolved_brain_id or active_namespace,
        "active_namespace": active_namespace,
        "brain_metadata": brain_meta,
        "connectors": list_connector_statuses(config=config),
        "reporting_status": build_reporting_status(config),
        "prioritized_feed": prioritized_feed[:8],
        "production_backend": build_production_backend_choice(config).to_dict(),
        "storage_backend": build_storage_backend_status(config, store=store).to_dict(),
        "available_buckets": list(config.allowed_buckets),
        "active_bucket_filter": bucket_filter,
    }
