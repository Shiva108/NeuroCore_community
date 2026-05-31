from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.query import query_memory
from neurocore.storage.in_memory import InMemoryStore


def build_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research", "ops"),
        default_sensitivity="standard",
        max_atomic_tokens=6,
    )


def test_query_memory_fails_closed_across_namespaces():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-beta",
            "bucket": "research",
            "sensitivity": "standard",
            "content": "isolated beta note",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "beta note",
            "namespace": "project-alpha",
            "allowed_buckets": ["research"],
            "sensitivity_ceiling": "standard",
        },
        store=store,
        config=config,
    )

    assert response["results"] == []


def test_query_memory_does_not_return_sealed_content_on_default_path():
    config = build_config()
    store = InMemoryStore()

    capture_memory(
        {
            "namespace": "project-alpha",
            "bucket": "ops",
            "sensitivity": "sealed",
            "content": "sealed ops memo",
            "content_format": "markdown",
            "source_type": "note",
        },
        store=store,
        config=config,
    )

    response = query_memory(
        {
            "query_text": "ops memo",
            "namespace": "project-alpha",
            "allowed_buckets": ["ops"],
            "sensitivity_ceiling": "sealed",
        },
        store=store,
        config=config,
    )

    assert response["results"] == []
