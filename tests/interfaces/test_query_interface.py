from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.query import query_memory
from neurocore.storage.in_memory import InMemoryStore


def build_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research", "planning"),
        default_sensitivity="standard",
        max_atomic_tokens=6,
    )


def test_query_memory_includes_explainability_metadata():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "memory architecture note",
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
            "sensitivity_ceiling": "standard",
        },
        store=store,
        config=config,
    )

    result = response["results"][0]
    assert result["explanation"]["filters_applied"]["namespace"] == "project-alpha"
    assert result["content_preview"]


def test_query_memory_uses_configured_buckets_when_not_explicitly_provided():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "planning",
            "sensitivity": "standard",
            "content": "planning note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "planning",
            "namespace": "project-alpha",
            "sensitivity_ceiling": "standard",
        },
        store=store,
        config=config,
    )

    assert len(response["results"]) == 1
    assert response["results"][0]["bucket"] == "planning"


def test_query_memory_honors_tags_any_and_tags_all_filters():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "architecture note",
            "content_format": "markdown",
            "source_type": "note",
            "tags": ["architecture", "memory"],
        },
        store=store,
        config=config,
    )
    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "planning note",
            "content_format": "markdown",
            "source_type": "note",
            "tags": ["planning"],
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "note",
            "namespace": "project-alpha",
            "sensitivity_ceiling": "standard",
            "tags_any": ["memory"],
            "tags_all": ["architecture"],
        },
        store=store,
        config=config,
    )

    assert len(response["results"]) == 1
    assert response["results"][0]["metadata"]["tags"] == ["architecture", "memory"]
