from neurocore.ingest.dedup import DedupIndex


def test_dedup_index_matches_within_the_same_namespace():
    dedup_index = DedupIndex()
    dedup_index.register(
        namespace="project-alpha",
        fingerprint="fingerprint-1",
        item_id="rec-1",
        signature="record:note:markdown",
    )

    assert (
        dedup_index.lookup(
            namespace="project-alpha",
            fingerprint="fingerprint-1",
            signature="record:note:markdown",
        )
        == "rec-1"
    )


def test_dedup_index_keeps_namespaces_isolated():
    dedup_index = DedupIndex()
    dedup_index.register(
        namespace="project-alpha",
        fingerprint="fingerprint-1",
        item_id="rec-1",
        signature="record:note:markdown",
    )

    assert (
        dedup_index.lookup(
            namespace="project-beta",
            fingerprint="fingerprint-1",
            signature="record:note:markdown",
        )
        is None
    )
