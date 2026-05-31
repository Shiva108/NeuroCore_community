from neurocore.ingest.normalize import compute_content_fingerprint, normalize_content


def test_normalize_content_collapses_whitespace_for_fingerprinting():
    original = "NeuroCore   keeps\n\nstable\tcontent."
    equivalent = "NeuroCore keeps stable content."

    assert normalize_content(original) == equivalent
    assert compute_content_fingerprint(original) == compute_content_fingerprint(
        equivalent
    )
