"""Helpers for resolving first-class brain identifiers to namespaces."""

from __future__ import annotations

from neurocore.storage.base import BaseStore


def resolve_namespace_for_brain(
    *,
    store: BaseStore,
    default_namespace: str,
    namespace: object = None,
    brain_id: object = None,
) -> tuple[str, str | None, dict[str, object]]:
    requested_namespace = _optional_str(namespace)
    requested_brain_id = _optional_str(brain_id)
    if requested_brain_id:
        manifest = store.get_brain(requested_brain_id, include_archived=True)
        if requested_namespace:
            if manifest is not None and manifest.namespace != requested_namespace:
                raise ValueError("brain_id and namespace mismatch")
            if manifest is None and requested_namespace != requested_brain_id:
                raise ValueError("brain_id and namespace mismatch")
            return (
                requested_namespace,
                requested_brain_id,
                {
                    "brain_resolved": manifest is not None,
                    "brain_status": (
                        manifest.status if manifest is not None else "implicit"
                    ),
                },
            )
        if manifest is not None:
            return (
                manifest.namespace,
                requested_brain_id,
                {
                    "brain_resolved": True,
                    "brain_status": manifest.status,
                },
            )
        # Compatibility fallback for existing callers that already treat brain_id
        # as a namespace alias before an explicit manifest exists.
        return (
            requested_brain_id,
            requested_brain_id,
            {
                "brain_resolved": False,
                "brain_status": "implicit",
            },
        )
    if requested_namespace:
        return (
            requested_namespace,
            None,
            {
                "brain_resolved": False,
                "brain_status": "namespace-only",
            },
        )
    return (
        default_namespace,
        None,
        {
            "brain_resolved": False,
            "brain_status": "default",
        },
    )


def apply_brain_namespace(
    request: dict[str, object],
    *,
    store: BaseStore,
    default_namespace: str,
) -> dict[str, object]:
    resolved_namespace, resolved_brain_id, brain_meta = resolve_namespace_for_brain(
        store=store,
        default_namespace=default_namespace,
        namespace=request.get("namespace"),
        brain_id=request.get("brain_id"),
    )
    resolved = dict(request)
    resolved["namespace"] = resolved_namespace
    if resolved_brain_id is not None:
        resolved["brain_id"] = resolved_brain_id
    resolved["brain_metadata"] = brain_meta
    return resolved


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
