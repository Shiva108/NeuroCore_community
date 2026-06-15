from neurocore.core import semantic as semantic_runtime
from neurocore.core.config import NeuroCoreConfig
from neurocore.ingest.normalize import compute_content_fingerprint, generate_stable_id
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.query import query_memory
from neurocore.retrieval.rankers import FakeSemanticRanker
from neurocore.storage.in_memory import InMemoryStore


def build_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research", "planning", "ops"),
        default_sensitivity="standard",
        max_atomic_tokens=6,
        target_chunk_tokens=6,
        max_chunk_tokens=8,
        chunk_overlap_tokens=2,
    )


def test_query_memory_enforces_namespace_before_ranking():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "memory chunking tradeoffs and architecture",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    capture_memory(
        {
            "namespace": "project-beta",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "memory chunking tradeoffs and architecture",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "memory architecture",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "top_k": 5,
            "return_mode": "hybrid",
        },
        store=store,
        config=config,
    )

    assert len(response["results"]) == 1
    assert response["results"][0]["namespace"] == "project-alpha"


def test_query_memory_falls_back_to_metadata_only_when_no_ranker_is_configured():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "retrieval fallback note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "retrieval fallback",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "top_k": 5,
        },
        store=store,
        config=config,
    )

    assert "Semantic ranker unavailable" in response["warnings"][0]
    assert response["results"][0]["matched_by"] == "metadata"


def test_query_memory_omits_diagnostics_by_default():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "diagnostics default note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "diagnostics",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
        },
        store=store,
        config=config,
    )

    assert "diagnostics" not in response


def test_query_memory_respects_sensitivity_ceiling():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "ops",
            "sensitivity": "restricted",
            "content": "restricted response plan",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "response plan",
            "namespace": "project-alpha",
            "allowed_buckets": ["ops"],
            "sensitivity_ceiling": "standard",
            "top_k": 5,
        },
        store=store,
        config=config,
    )

    assert response["results"] == []


def test_query_memory_includes_diagnostics_for_metadata_and_policy_filters():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "ops",
            "sensitivity": "restricted",
            "content": "restricted response plan",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "response plan",
            "namespace": "project-alpha",
            "allowed_buckets": ["ops"],
            "sensitivity_ceiling": "standard",
            "include_diagnostics": True,
        },
        store=store,
        config=config,
    )

    assert response["results"] == []
    assert response["diagnostics"]["semantic_mode"] == "metadata-only"
    assert response["diagnostics"]["candidate_counts"] == {
        "store": 1,
        "policy_allowed": 0,
        "return_mode_allowed": 0,
        "semantic_scored": 0,
        "metadata_matched": 0,
        "ranked": 0,
        "selected_pre_aggregate": 0,
        "selected_post_aggregate": 0,
    }
    assert response["diagnostics"]["filter_counts"]["excluded_sensitivity"] == 1
    assert response["diagnostics"]["ranker"] == {
        "configured_backend": "none",
        "active": False,
        "warning_emitted": True,
    }


def test_query_memory_returns_real_scores():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "memory architecture architecture note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "architecture",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
        },
        store=store,
        config=config,
    )

    assert response["results"][0]["score"] > 1.0


def test_query_memory_document_aggregate_surfaces_parent_document_id():
    config = build_config()
    store = InMemoryStore()

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
            "title": "Design note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "retrieval detail",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "return_mode": "document_aggregate",
        },
        store=store,
        config=config,
    )

    assert response["results"][0]["kind"] == "document"
    assert response["results"][0]["id"] == capture["id"]
    assert response["results"][0]["document_id"] == capture["id"]


def test_query_memory_document_aggregate_diagnostics_track_pre_and_post_counts():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "retrieval fallback note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
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
            "title": "Design note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "retrieval detail",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "return_mode": "document_aggregate",
            "top_k": 2,
            "include_diagnostics": True,
        },
        store=store,
        config=config,
    )

    assert [item["id"] for item in response["results"]] == [
        generate_stable_id(
            "rec",
            "project-alpha",
            "research",
            compute_content_fingerprint("retrieval fallback note"),
            "note",
            "standard",
        ),
        capture["id"],
    ]
    assert response["diagnostics"]["candidate_counts"]["selected_pre_aggregate"] == 2
    assert response["diagnostics"]["candidate_counts"]["selected_post_aggregate"] == 2
    assert response["diagnostics"]["selected_kinds"] == {
        "record": 1,
        "chunk": 0,
        "document": 1,
    }


def test_query_memory_supports_record_only_and_chunk_only_modes():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "short record note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    capture_memory(
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

    record_only = query_memory(
        {
            "query_text": "note",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "return_mode": "record_only",
        },
        store=store,
        config=config,
    )
    chunk_only = query_memory(
        {
            "query_text": "retrieval detail",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "return_mode": "chunk_only",
        },
        store=store,
        config=config,
    )

    assert all(result["kind"] == "record" for result in record_only["results"])
    assert all(result["kind"] == "chunk" for result in chunk_only["results"])


def test_query_memory_chunk_only_diagnostics_track_return_mode_and_semantic_scores():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "short record note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    document = capture_memory(
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
    chunk_ids = store.get_document_chunk_ids(document["id"])
    ranker = FakeSemanticRanker(
        scores={
            chunk_ids[1]: 0.9,
            chunk_ids[2]: 0.2,
        }
    )

    response = query_memory(
        {
            "query_text": "semantic query",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "return_mode": "chunk_only",
            "top_k": 1,
            "include_diagnostics": True,
        },
        store=store,
        config=config,
        semantic_ranker=ranker,
    )

    assert response["results"][0]["id"] == chunk_ids[1]
    assert response["diagnostics"]["semantic_mode"] == "hybrid"
    assert response["diagnostics"]["candidate_counts"]["semantic_scored"] == 3
    assert response["diagnostics"]["filter_counts"]["excluded_return_mode"] == 1
    assert response["diagnostics"]["selected_kinds"] == {
        "record": 0,
        "chunk": 1,
        "document": 0,
    }
    assert response["diagnostics"]["ranker"] == {
        "configured_backend": "none",
        "active": True,
        "warning_emitted": False,
    }


def test_query_memory_honors_source_types_and_time_range():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "article note",
            "content_format": "markdown",
            "source_type": "article",
            "created_at": "2025-01-01T00:00:00+00:00",
        },
        store=store,
        config=config,
    )
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "recent note",
            "content_format": "markdown",
            "source_type": "note",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "note",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "source_types": ["note"],
            "time_range": ["2025-06-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00"],
        },
        store=store,
        config=config,
    )

    assert len(response["results"]) == 1
    assert response["results"][0]["metadata"]["source_type"] == "note"

    diagnostics_response = query_memory(
        {
            "query_text": "note",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "source_types": ["note"],
            "time_range": ["2025-06-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00"],
            "include_diagnostics": True,
        },
        store=store,
        config=config,
    )

    assert diagnostics_response["diagnostics"]["filter_counts"] == {
        "excluded_sealed": 0,
        "excluded_sensitivity": 0,
        "excluded_source_type": 1,
        "excluded_tags_any": 0,
        "excluded_tags_all": 0,
        "excluded_time_range": 0,
        "excluded_return_mode": 0,
    }


def test_query_memory_can_include_archived_items():
    config = build_config()
    store = InMemoryStore()

    capture = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "archived note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    store.soft_delete(capture["id"], reason="archive")

    hidden = query_memory(
        {
            "query_text": "archived",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
        },
        store=store,
        config=config,
    )
    visible = query_memory(
        {
            "query_text": "archived",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
            "include_archived": True,
        },
        store=store,
        config=config,
    )

    assert hidden["results"] == []
    assert len(visible["results"]) == 1


def test_query_memory_uses_semantic_ranker_when_provided():
    config = build_config()
    store = InMemoryStore()

    first = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "alpha note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )
    second = capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "beta note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    ranker = FakeSemanticRanker(
        scores={
            first["id"]: 0.2,
            second["id"]: 0.9,
        }
    )
    response = query_memory(
        {
            "query_text": "semantic query",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
        },
        store=store,
        config=config,
        semantic_ranker=ranker,
    )

    assert response["warnings"] == []
    assert response["results"][0]["id"] == second["id"]
    assert response["results"][0]["matched_by"] == "hybrid"
    assert response["results"][0]["explanation"]["matched_signals"] == [
        "semantic",
        "metadata",
    ]


def test_query_memory_falls_back_when_semantic_backend_is_unavailable(monkeypatch):
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        semantic_backend="sentence-transformers",
        semantic_model_name="sentence-transformers/all-MiniLM-L6-v2",
    )
    store = InMemoryStore()
    monkeypatch.setattr(
        semantic_runtime,
        "get_sentence_transformer_class",
        lambda: (_ for _ in ()).throw(
            RuntimeError(
                "sentence-transformers is required for the sentence-transformers ranker"
            )
        ),
    )

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "fallback semantic note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "fallback",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
        },
        store=store,
        config=config,
    )

    assert len(response["results"]) == 1
    assert response["results"][0]["matched_by"] == "metadata"
    assert any(
        "Semantic ranker unavailable" in warning for warning in response["warnings"]
    )
