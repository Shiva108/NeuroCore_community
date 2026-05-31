from neurocore.core.config import NeuroCoreConfig
from neurocore.ingest.chunking import chunk_text, classify_content_kind


def test_classify_content_kind_keeps_short_notes_atomic():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        max_atomic_tokens=6,
    )

    assert classify_content_kind("short note stays atomic", config) == "record"


def test_classify_content_kind_routes_long_content_to_document():
    config = NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research",),
        default_sensitivity="standard",
        max_atomic_tokens=4,
    )

    assert classify_content_kind("one two three four five", config) == "document"


def test_chunk_text_is_deterministic_and_ordered():
    text = (
        "Sentence one explains the system. "
        "Sentence two adds more retrieval detail. "
        "Sentence three covers isolation policy. "
        "Sentence four closes the example."
    )

    first = chunk_text(text, target_tokens=6, max_tokens=8, overlap_tokens=2)
    second = chunk_text(text, target_tokens=6, max_tokens=8, overlap_tokens=2)

    assert first == second
    assert len(first) > 1
    assert first[0].startswith("Sentence one")
