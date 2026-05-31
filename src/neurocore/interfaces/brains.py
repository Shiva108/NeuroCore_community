"""First-class brain lifecycle interfaces for NeuroCore."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from neurocore.core.models import BrainManifest
from neurocore.interfaces.capture import attach_store_warnings
from neurocore.storage.base import BaseStore


def create_brain(
    request: dict[str, object],
    *,
    store: BaseStore,
    default_allowed_buckets: tuple[str, ...],
) -> dict[str, object]:
    brain_id = str(request.get("brain_id") or request.get("namespace") or "").strip()
    if not brain_id:
        raise ValueError("brain_id is required")
    namespace = str(request.get("namespace") or brain_id).strip()
    existing = store.get_brain(brain_id, include_archived=True)
    if existing is not None and existing.namespace != namespace:
        raise ValueError("brain_id and namespace mismatch existing brain mapping")
    now = datetime.now(UTC)
    if existing is not None:
        manifest = BrainManifest(
            brain_id=existing.brain_id,
            namespace=existing.namespace,
            display_name=str(
                request.get("display_name")
                or existing.display_name
                or existing.namespace
            ),
            description=str(request.get("description") or existing.description or ""),
            status=str(request.get("status") or existing.status or "active"),
            created_at=existing.created_at,
            updated_at=now,
            owner=_optional_str(request.get("owner")) or existing.owner,
            tags=tuple(request.get("tags") or existing.tags),
            default_allowed_buckets=tuple(
                request.get("default_allowed_buckets")
                or existing.default_allowed_buckets
                or default_allowed_buckets
            ),
            metadata=_metadata(request, fallback=existing.metadata),
        )
        store.save_brain(manifest)
        return attach_store_warnings(
            {
                "brain": _serialize_brain(manifest),
                "created": False,
                "updated": True,
                "warnings": [],
            },
            store=store,
        )
    manifest = BrainManifest(
        brain_id=brain_id,
        namespace=namespace,
        display_name=str(request.get("display_name") or namespace),
        description=str(request.get("description") or ""),
        status=str(request.get("status") or "active"),
        created_at=now,
        updated_at=now,
        owner=_optional_str(request.get("owner")),
        tags=tuple(request.get("tags") or ()),
        default_allowed_buckets=tuple(
            request.get("default_allowed_buckets") or default_allowed_buckets
        ),
        metadata=_metadata(request),
    )
    store.save_brain(manifest)
    return attach_store_warnings(
        {
            "brain": _serialize_brain(manifest),
            "created": True,
            "updated": False,
            "warnings": [],
        },
        store=store,
    )


def get_brain(request: dict[str, object], *, store: BaseStore) -> dict[str, object]:
    brain_id = str(request.get("brain_id") or "").strip()
    if not brain_id:
        raise ValueError("brain_id is required")
    brain = store.get_brain(
        brain_id, include_archived=bool(request.get("include_archived", False))
    )
    if brain is None:
        raise KeyError(brain_id)
    return {"brain": _serialize_brain(brain)}


def list_brains(request: dict[str, object], *, store: BaseStore) -> dict[str, object]:
    brains = store.list_brains(
        include_archived=bool(request.get("include_archived", False))
    )
    return {"brains": [_serialize_brain(brain) for brain in brains]}


def update_brain(request: dict[str, object], *, store: BaseStore) -> dict[str, object]:
    brain_id = str(request.get("brain_id") or "").strip()
    if not brain_id:
        raise ValueError("brain_id is required")
    patch = dict(request.get("patch") or {})
    updated = store.update_brain(brain_id, patch)
    return attach_store_warnings(
        {"brain": _serialize_brain(updated), "updated": True, "warnings": []},
        store=store,
    )


def archive_brain(request: dict[str, object], *, store: BaseStore) -> dict[str, object]:
    brain_id = str(request.get("brain_id") or "").strip()
    if not brain_id:
        raise ValueError("brain_id is required")
    archived = store.archive_brain(brain_id, reason=str(request.get("reason") or ""))
    return attach_store_warnings(
        {"brain": _serialize_brain(archived), "archived": True, "warnings": []},
        store=store,
    )


def _serialize_brain(brain: BrainManifest) -> dict[str, object]:
    payload = asdict(brain)
    payload["tags"] = list(brain.tags)
    payload["default_allowed_buckets"] = list(brain.default_allowed_buckets)
    payload["created_at"] = brain.created_at.isoformat()
    payload["updated_at"] = brain.updated_at.isoformat()
    return payload


def _metadata(
    request: dict[str, object], *, fallback: dict[str, object] | None = None
) -> dict[str, object]:
    value = request.get("metadata")
    if isinstance(value, dict):
        return dict(value)
    return dict(fallback or {})


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
