"""Source manifest helpers for source-backed ingest paths."""

from __future__ import annotations

from neurocore.storage.base import BaseStore


def build_source_manifest(
    *,
    store: BaseStore,
    namespace: str,
    source_type: str,
    source_url: str,
    content_fingerprint: str,
    content_provenance: str = "captured",
    rejected: bool = False,
) -> dict[str, object]:
    canonical_url = str(source_url or "").strip()
    prior = find_prior_source_artifacts(
        store=store,
        namespace=namespace,
        source_type=source_type,
        source_url=canonical_url,
    )
    delta_state = _delta_state(
        prior=prior,
        content_fingerprint=content_fingerprint,
        rejected=rejected,
    )
    manifest: dict[str, object] = {
        "canonical_url": canonical_url,
        "source_url": canonical_url,
        "source_type": source_type,
        "content_fingerprint": content_fingerprint,
        "content_provenance": content_provenance,
        "delta_state": delta_state,
    }
    prior_document_id = prior.get("document_id")
    prior_record_id = prior.get("record_id")
    if prior_document_id:
        manifest["prior_document_id"] = prior_document_id
    if prior_record_id:
        manifest["prior_record_id"] = prior_record_id
    prior_fingerprint = prior.get("content_fingerprint")
    if prior_fingerprint:
        manifest["prior_content_fingerprint"] = prior_fingerprint
    return manifest


def find_prior_source_artifacts(
    *,
    store: BaseStore,
    namespace: str,
    source_type: str,
    source_url: str,
) -> dict[str, str | None]:
    canonical_url = str(source_url or "").strip()
    result: dict[str, str | None] = {
        "document_id": None,
        "record_id": None,
        "content_fingerprint": None,
    }
    if not canonical_url:
        return result
    for document in store.list_documents(include_archived=True):
        if document.namespace != namespace or document.source_type != source_type:
            continue
        if _source_url(document.metadata) != canonical_url:
            continue
        result["document_id"] = document.id
        result["content_fingerprint"] = document.content_fingerprint
        break
    for record in store.list_records(include_archived=True):
        if record.namespace != namespace or record.source_type != source_type:
            continue
        if _source_url(record.metadata) != canonical_url:
            continue
        result["record_id"] = record.id
        if result["content_fingerprint"] is None:
            result["content_fingerprint"] = str(
                record.metadata.get("source_content_fingerprint")
                or record.content_fingerprint
            )
        break
    return result


def source_manifest_supersedes_id(
    manifest: object, *, artifact_kind: str = "document"
) -> str | None:
    if not isinstance(manifest, dict):
        return None
    if manifest.get("delta_state") != "changed":
        return None
    key = "prior_record_id" if artifact_kind == "record" else "prior_document_id"
    value = str(manifest.get(key) or "").strip()
    return value or None


def _delta_state(
    *,
    prior: dict[str, str | None],
    content_fingerprint: str,
    rejected: bool,
) -> str:
    if rejected:
        return "rejected"
    prior_fingerprint = prior.get("content_fingerprint")
    if not prior_fingerprint:
        return "new"
    if prior_fingerprint == content_fingerprint:
        return "unchanged"
    return "changed"


def _source_url(metadata: dict[str, object]) -> str:
    return str(
        metadata.get("canonical_url")
        or metadata.get("source_url")
        or metadata.get("submitted_source_url")
        or ""
    ).strip()
