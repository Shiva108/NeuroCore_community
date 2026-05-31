from datetime import UTC, datetime

import pytest

from neurocore.core.brains import apply_brain_namespace, resolve_namespace_for_brain
from neurocore.core.models import BrainManifest
from neurocore.storage.in_memory import InMemoryStore


def _brain_manifest(brain_id: str = "alpha-brain") -> BrainManifest:
    return BrainManifest(
        brain_id=brain_id,
        namespace="project-alpha",
        display_name="Project Alpha",
        description="Primary project brain",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        owner="analyst",
        tags=("alpha",),
        default_allowed_buckets=("research", "reports"),
        metadata={"source": "test"},
    )


def test_resolve_namespace_for_brain_uses_manifest_namespace_when_brain_id_exists():
    store = InMemoryStore()
    store.save_brain(_brain_manifest())

    namespace, brain_id, meta = resolve_namespace_for_brain(
        store=store,
        default_namespace="default-brain",
        brain_id="alpha-brain",
    )

    assert namespace == "project-alpha"
    assert brain_id == "alpha-brain"
    assert meta["brain_resolved"] is True


def test_resolve_namespace_for_brain_keeps_namespace_only_requests_compatible():
    store = InMemoryStore()

    namespace, brain_id, meta = resolve_namespace_for_brain(
        store=store,
        default_namespace="default-brain",
        namespace="project-alpha",
    )

    assert namespace == "project-alpha"
    assert brain_id is None
    assert meta["brain_status"] == "namespace-only"


def test_apply_brain_namespace_rejects_conflicting_brain_and_namespace():
    store = InMemoryStore()
    store.save_brain(_brain_manifest())

    with pytest.raises(ValueError, match="brain_id and namespace mismatch"):
        apply_brain_namespace(
            {"brain_id": "alpha-brain", "namespace": "project-beta"},
            store=store,
            default_namespace="default-brain",
        )
