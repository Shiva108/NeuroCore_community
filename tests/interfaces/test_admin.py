import pytest

from neurocore.core import semantic as semantic_runtime
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.admin import (
    audit_memory,
    delete_memory,
    reindex_memory,
    sync_storage,
    update_memory,
)
from neurocore.interfaces.capture import capture_memory
from neurocore.storage.in_memory import InMemoryStore
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage.router import RoutedStore


def disabled_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_admin_surface=False,
    )


def enabled_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        max_atomic_tokens=6,
        target_chunk_tokens=6,
        max_chunk_tokens=8,
        chunk_overlap_tokens=2,
        enable_admin_surface=True,
        allow_hard_delete=False,
    )


def test_admin_operations_are_disabled_by_default():
    store = InMemoryStore()
    config = disabled_config()

    with pytest.raises(PermissionError, match="disabled"):
        update_memory({"id": "rec-1", "patch": {}, "mode": "in_place"}, store, config)

    with pytest.raises(PermissionError, match="disabled"):
        delete_memory({"id": "rec-1", "mode": "soft_delete"}, store, config)

    with pytest.raises(PermissionError, match="disabled"):
        reindex_memory({"ids": ["rec-1"], "scope": "records"}, store, config)

    with pytest.raises(PermissionError, match="disabled"):
        audit_memory({}, store, config)


def test_admin_update_and_delete_emit_audit_events():
    store = InMemoryStore()
    config = enabled_config()

    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "admin managed note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    updated = update_memory(
        {
            "id": capture["id"],
            "patch": {"title": "Reviewed"},
            "mode": "in_place",
            "actor": "tester",
        },
        store,
        config,
    )
    deleted = delete_memory(
        {
            "id": capture["id"],
            "mode": "soft_delete",
            "reason": "cleanup",
            "actor": "tester",
        },
        store,
        config,
    )

    assert updated["updated"] is True
    assert deleted["deleted"] is True
    assert len(store.audit_events) >= 2


def test_admin_sync_records_audit_event_details():
    local = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    cloud = RoutedStore(primary_store=InMemoryStore(), sealed_store=InMemoryStore())
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_admin_surface=True,
        storage_backend="mirror",
        production_backend_provider="supabase",
        production_database_url="postgresql://primary",
        production_sealed_database_url="postgresql://sealed",
    )

    response = sync_storage(
        {"action": "backfill_local_to_cloud", "actor": "tester"},
        store,
        config,
    )

    assert response["supported"] is True
    assert (
        store.list_audit_events(limit=1)[0]["details"]["action"]
        == "backfill_local_to_cloud"
    )


def test_admin_update_can_replace_content_and_set_supersedes_id():
    store = InMemoryStore()
    config = enabled_config()

    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": (
                "Sentence one explains the system. "
                "Sentence two adds retrieval detail. "
                "Sentence three covers isolation policy."
            ),
            "content_format": "markdown",
            "source_type": "note",
            "title": "Before",
        },
        store=store,
        config=config,
    )

    replaced = update_memory(
        {
            "id": capture["id"],
            "patch": {
                "content": "Replacement sentence one. Replacement sentence two.",
                "title": "After",
            },
            "mode": "replace_content",
            "actor": "tester",
        },
        store,
        config,
    )

    assert replaced["id"] != capture["id"]
    assert replaced["superseded_id"] == capture["id"]
    replacement_doc = store.get_document(replaced["id"])
    assert replacement_doc is not None
    assert replacement_doc.supersedes_id == capture["id"]
    assert store.get_document(capture["id"], include_archived=True) is not None


def test_admin_delete_hard_delete_requires_explicit_policy():
    store = InMemoryStore()
    config = enabled_config()
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "hard delete note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    with pytest.raises(PermissionError, match="Hard delete"):
        delete_memory(
            {
                "id": capture["id"],
                "mode": "hard_delete",
                "reason": "cleanup",
                "actor": "tester",
            },
            store,
            config,
        )

    permissive_config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_admin_surface=True,
        allow_hard_delete=True,
    )
    deleted = delete_memory(
        {
            "id": capture["id"],
            "mode": "hard_delete",
            "reason": "cleanup",
            "actor": "tester",
        },
        store,
        permissive_config,
    )

    assert deleted["deleted"] is True
    assert store.get_record(capture["id"], include_archived=True) is None


def test_admin_hard_delete_allows_recapture_of_the_same_content():
    store = InMemoryStore()
    permissive_config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        enable_admin_surface=True,
        allow_hard_delete=True,
    )
    first = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "reusable note after hard delete",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=permissive_config,
    )

    delete_memory(
        {
            "id": first["id"],
            "mode": "hard_delete",
            "reason": "cleanup",
            "actor": "tester",
        },
        store,
        permissive_config,
    )
    second = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "reusable note after hard delete",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=permissive_config,
    )

    assert second["deduplicated"] is False
    assert store.get_record(second["id"], include_archived=True) is not None


def test_admin_reindex_reports_processed_ids_without_changing_identity():
    store = InMemoryStore()
    config = enabled_config()
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "reindex note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = reindex_memory(
        {"ids": [capture["id"], "missing-id"], "scope": "records", "actor": "tester"},
        store,
        config,
    )

    assert response["processed"] == 1
    assert response["failed"] == 1
    assert response["warnings"] == []
    assert store.get_record(capture["id"]) is not None


def test_admin_audit_finds_secret_like_content_in_records_and_returns_actions():
    store = InMemoryStore()
    config = enabled_config()

    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "API_KEY=super-secret-value",
            "content_format": "markdown",
            "source_type": "note",
            "title": "Leaky note",
            "metadata": {"source": "manual"},
        },
        store=store,
        config=config,
    )

    response = audit_memory(
        {
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "actor": "tester",
        },
        store,
        config,
    )

    assert response["findings"]
    assert response["findings"][0]["item_id"] == capture["id"]
    assert response["findings"][0]["field"] == "content"
    assert response["candidate_actions"]
    assert {action["action"] for action in response["candidate_actions"]} == {
        "manual_redact_content",
        "soft_delete_item",
    }
    assert store.get_record(capture["id"]) is not None


def test_admin_audit_finds_secret_like_content_in_documents():
    store = InMemoryStore()
    config = enabled_config()

    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "sealed",
            "content": (
                "Sentence one explains the system.\n"
                "SECRET_KEY=super-secret-value\n"
                "Sentence three covers isolation policy."
            ),
            "content_format": "markdown",
            "source_type": "report",
            "title": "Leaky document",
        },
        store=store,
        config=config,
    )

    response = audit_memory(
        {
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
        },
        store,
        config,
    )

    assert any(finding["item_id"] == capture["id"] for finding in response["findings"])
    assert any(finding["sensitivity"] == "sealed" for finding in response["findings"])


def test_admin_audit_respects_namespace_bucket_and_include_archived_filters():
    store = InMemoryStore()
    config = enabled_config()
    kept = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "API_KEY=keep-me-visible",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    archived = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "API_KEY=archived-secret",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    capture_memory(
        {
            "namespace": "other-project",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "API_KEY=other-namespace",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    delete_memory(
        {
            "id": archived["id"],
            "mode": "soft_delete",
            "reason": "archive",
            "actor": "tester",
        },
        store,
        config,
    )

    response = audit_memory(
        {
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
        },
        store,
        config,
    )
    included_response = audit_memory(
        {
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "include_archived": True,
        },
        store,
        config,
    )

    visible_ids = {finding["item_id"] for finding in response["findings"]}
    included_ids = {finding["item_id"] for finding in included_response["findings"]}
    assert kept["id"] in visible_ids
    assert archived["id"] not in visible_ids
    assert archived["id"] in included_ids


def test_admin_audit_finds_metadata_secrets_and_returns_metadata_redaction_action():
    store = InMemoryStore()
    config = enabled_config()
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "safe content",
            "content_format": "markdown",
            "source_type": "note",
            "metadata": {"config_line": "SECRET_KEY=super-secret-value"},
        },
        store=store,
        config=config,
    )

    response = audit_memory(
        {
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
        },
        store,
        config,
    )

    assert any(
        finding["item_id"] == capture["id"]
        and finding["field"] == "metadata.config_line"
        for finding in response["findings"]
    )
    assert any(
        action["action"] == "manual_redact_metadata"
        for action in response["candidate_actions"]
    )


def test_admin_audit_emits_single_soft_delete_action_per_item():
    store = InMemoryStore()
    config = enabled_config()
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "API_KEY=super-secret-value",
            "content_format": "markdown",
            "source_type": "note",
            "metadata": {"config_line": "SECRET_KEY=super-secret-value"},
        },
        store=store,
        config=config,
    )

    response = audit_memory(
        {
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
        },
        store,
        config,
    )

    soft_delete_actions = [
        action
        for action in response["candidate_actions"]
        if action["item_id"] == capture["id"] and action["action"] == "soft_delete_item"
    ]
    assert len(soft_delete_actions) == 1


def test_admin_reindex_rebuilds_record_artifacts():
    store = InMemoryStore()
    config = enabled_config()
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "artifact rebuild note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    before = store.get_artifact(capture["id"])

    response = reindex_memory(
        {"ids": [capture["id"]], "scope": "records", "actor": "tester"},
        store,
        config,
    )

    after = store.get_artifact(capture["id"])

    assert before is not None
    assert after is not None
    assert after.item_id == capture["id"]
    assert after.indexed_at >= before.indexed_at
    assert response["processed"] == 1
    assert response["failed"] == 0


def test_admin_reindex_rebuilds_document_chunk_artifacts_and_reports_missing_semantic_backend(
    monkeypatch,
):
    store = InMemoryStore()
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        max_atomic_tokens=6,
        target_chunk_tokens=6,
        max_chunk_tokens=8,
        chunk_overlap_tokens=2,
        enable_admin_surface=True,
        semantic_backend="sentence-transformers",
    )
    monkeypatch.setattr(
        semantic_runtime,
        "sentence_transformers_status",
        lambda: (
            "unavailable",
            "Semantic backend sentence-transformers is unavailable; artifacts were rebuilt in metadata-only mode.",
        ),
    )
    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": (
                "Sentence one explains the system. "
                "Sentence two adds retrieval detail. "
                "Sentence three covers isolation policy."
            ),
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    chunk_id = store.get_document_chunk_ids(capture["id"])[0]
    before = store.get_artifact(chunk_id)

    response = reindex_memory(
        {"ids": [capture["id"]], "scope": "documents", "actor": "tester"},
        store,
        config,
    )

    after = store.get_artifact(chunk_id)

    assert before is not None
    assert after is not None
    assert after.document_id == capture["id"]
    assert after.indexed_at >= before.indexed_at
    assert response["processed"] == 1
    assert response["failed"] == 0
    assert response["warnings"]
