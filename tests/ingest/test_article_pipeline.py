import pytest

from neurocore.core.config import NeuroCoreConfig
from neurocore.ingest.article_distill import distill_article_knowledge
from neurocore.ingest.article_fetch import (
    ARTICLE_FETCH_FALLBACK_HEADERS,
    ARTICLE_FETCH_PRIMARY_HEADERS,
    canonicalize_article_url,
    fetch_article_source,
)
from neurocore.ingest.article_gates import ArticleGateConfig, evaluate_article_gate
from neurocore.storage.in_memory import InMemoryStore


def _config(**overrides) -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research", "ops"),
        default_sensitivity="standard",
        **overrides,
    )


def test_canonicalize_article_url_drops_tracking_params_and_fragment():
    assert (
        canonicalize_article_url(
            "HTTPS://Example.com/articles/ldap/?utm_source=slack&ref=ops#intro"
        )
        == "https://example.com/articles/ldap?ref=ops"
    )


def test_fetch_article_source_normalizes_html_into_markdown(monkeypatch):
    class DummyResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self):
            return (
                b"<html><body>"
                b"<nav>Menu Subscribe Sign in Privacy Policy</nav>"
                b"<article>"
                b"<h1>Reusable LDAP Enumeration</h1>"
                b"<p>This article explains reusable LDAP reconnaissance patterns, "
                b"detection-aware collection, and operator checklists that apply "
                b"across environments without client-specific dependencies.</p>"
                b"</article>"
                b"</body></html>"
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )

    source = fetch_article_source("https://Example.com/articles/ldap/?utm_source=x")

    assert source["canonical_url"] == "https://example.com/articles/ldap"
    assert source["content_format"] == "markdown"
    assert source["original_content_format"] == "html"
    assert source["sanitized_from_html"] is True
    assert source["title"] == "Reusable LDAP Enumeration"
    assert "Menu Subscribe Sign in Privacy Policy" not in source["content"]
    assert source["content"].startswith("# Reusable LDAP Enumeration")


def test_fetch_article_source_keeps_content_after_void_header_tags(monkeypatch):
    class DummyResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self):
            return (
                b"<html><body>"
                b"<article class='post'>"
                b"<header>"
                b"<h1>The Art of Windows Defender Bypass and Shellcode Obfuscation</h1>"
                b"<p>Posted Apr 12, 2025 Updated Apr 13, 2025</p>"
                b"<img src='/assets/header.png' alt='header image'>"
                b"</header>"
                b"<section>"
                b"<p>This walkthrough explains shellcode obfuscation tradeoffs, "
                b"Windows Defender bypass constraints, staging design choices, "
                b"and repeatable operator considerations for reuse in later "
                b"research workflows.</p>"
                b"<p>It also covers the practical impact of encoder selection, "
                b"memory layout, API choice, and telemetry-aware execution when "
                b"building payloads for lab analysis.</p>"
                b"</section>"
                b"</article>"
                b"</body></html>"
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )

    source = fetch_article_source(
        "https://dr-b3tman.github.io/posts/the-art-of-av-windows-defender-bypass-shellcode-obfuscation/"
    )

    assert (
        source["title"]
        == "The Art of Windows Defender Bypass and Shellcode Obfuscation"
    )
    assert source["content_format"] == "markdown"
    assert source["sanitized_from_html"] is True
    assert "shellcode obfuscation tradeoffs" in source["content"]
    assert "telemetry-aware execution" in source["content"]
    assert len(source["content"].split()) > 40


def test_fetch_article_source_raises_for_unreachable_urls(monkeypatch):
    def raise_url_error(request, timeout=0.0):
        raise OSError("network unavailable")

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        raise_url_error,
    )
    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.build_opener",
        lambda *handlers: type(
            "FailingOpener",
            (),
            {
                "open": staticmethod(
                    lambda request, timeout=0.0: (_ for _ in ()).throw(
                        OSError("network unavailable")
                    )
                )
            },
        )(),
    )

    with pytest.raises(ValueError, match="Could not fetch article URL"):
        fetch_article_source("https://example.invalid/articles/ldap")


def test_fetch_article_source_sends_browser_like_headers(monkeypatch):
    captured: dict[str, str] = {}

    class DummyResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self):
            return (
                b"<html><body><article><h1>Reusable LDAP Enumeration</h1>"
                b"<p>Reusable detection-aware workflow.</p></article></body></html>"
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=0.0):
        del timeout
        captured.update({key.lower(): value for key, value in request.header_items()})
        return DummyResponse()

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        fake_urlopen,
    )

    fetch_article_source("https://example.invalid/articles/ldap")

    for key, value in ARTICLE_FETCH_PRIMARY_HEADERS.items():
        assert captured[key.lower()] == value


def test_fetch_article_source_retries_with_fallback_headers(monkeypatch):
    attempts: list[dict[str, object]] = []

    class DummyResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self):
            return (
                b"<html><body><article><h1>Reusable LDAP Enumeration</h1>"
                b"<p>Reusable detection-aware workflow.</p></article></body></html>"
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fail_primary(request, timeout=0.0):
        del timeout
        attempts.append(
            {
                "source": "primary",
                "headers": {
                    key.lower(): value for key, value in request.header_items()
                },
            }
        )
        raise OSError("timed out")

    class DummyOpener:
        def open(self, request, timeout=0.0):
            del timeout
            attempts.append(
                {
                    "source": "fallback",
                    "headers": {
                        key.lower(): value for key, value in request.header_items()
                    },
                }
            )
            return DummyResponse()

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        fail_primary,
    )
    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.build_opener",
        lambda *handlers: DummyOpener(),
    )

    source = fetch_article_source("https://example.invalid/articles/ldap")

    assert source["title"] == "Reusable LDAP Enumeration"
    assert [attempt["source"] for attempt in attempts] == ["primary", "fallback"]
    for key, value in ARTICLE_FETCH_FALLBACK_HEADERS.items():
        assert attempts[1]["headers"][key.lower()] == value


def test_evaluate_article_gate_blocks_navigation_heavy_content():
    evaluation = evaluate_article_gate(
        source={
            "canonical_url": "https://example.invalid/navigation-shell",
            "title": "Navigation shell",
            "content": (
                "<html><body>"
                "<nav>Menu Subscribe Sign in Privacy Policy Cookie Settings</nav>"
                "<div>Menu Subscribe Sign in Privacy Policy Cookie Settings</div>"
                "<footer>Menu Subscribe Sign in Privacy Policy Cookie Settings</footer>"
                "</body></html>"
            ),
            "content_format": "html",
            "original_content_format": "html",
        },
        store=InMemoryStore(),
        namespace="project-alpha",
        config=ArticleGateConfig(),
    )

    assert evaluation["accepted"] is False
    assert "mostly-markup-or-navigation" in evaluation["hard_fail_reasons"]


def test_evaluate_article_gate_marks_exact_duplicates_for_idempotent_reuse():
    store = InMemoryStore()
    existing = {
        "namespace": "project-alpha",
        "bucket": "research",
        "sensitivity": "standard",
        "content": (
            "# Reusable LDAP Enumeration\n\n"
            "Reusable LDAP reconnaissance workflow for red teams with "
            "detection-aware collection, operator notes, and portable "
            "checklists that apply across environments."
        ),
        "content_format": "markdown",
        "source_type": "article_raw",
        "title": "Reusable LDAP Enumeration",
        "metadata": {
            "canonical_url": "https://example.invalid/articles/ldap",
            "source_url": "https://example.invalid/articles/ldap",
        },
        "force_kind": "document",
    }
    from neurocore.interfaces.capture import capture_memory

    capture_memory(existing, store=store, config=_config())

    evaluation = evaluate_article_gate(
        source={
            "canonical_url": "https://example.invalid/articles/ldap",
            "title": "Reusable LDAP Enumeration",
            "content": existing["content"],
            "content_format": "markdown",
        },
        store=store,
        namespace="project-alpha",
        config=ArticleGateConfig(),
    )

    assert evaluation["accepted"] is True
    assert evaluation["duplicate"] is True
    assert evaluation["decision"] == "deduplicated"


def test_distill_article_knowledge_returns_structured_reusable_fields():
    artifact = distill_article_knowledge(
        source={
            "canonical_url": "https://example.invalid/articles/ldap",
            "title": "Reusable LDAP Enumeration",
            "content": (
                "# Reusable LDAP Enumeration\n\n"
                "This article explains reusable LDAP reconnaissance patterns for "
                "red teams. Use narrow LDAP queries first, expand scope "
                "iteratively, document assumptions, and convert recurring "
                "checks into operator checklists. Detection-aware collection and "
                "portable review notes make the workflow useful for later reuse."
            ),
            "content_format": "markdown",
        },
        evaluation={
            "accepted": True,
            "quality_score": 5,
            "scores": {
                "relevance": 1,
                "novelty": 1,
                "signal_to_noise": 1,
                "actionability": 1,
                "credibility": 1,
                "downstream_usefulness": 0,
            },
        },
        operator_note="Use this for HackingAgent LDAP playbooks.",
    )

    assert artifact["summary"]
    assert artifact["key_claims"]
    assert artifact["techniques"]
    assert artifact["security_entities"]["technologies"] == ["ldap"]
    assert artifact["mitigations"]
    assert artifact["open_questions"]
    assert artifact["source_backed_claims"][0]["source_url"] == (
        "https://example.invalid/articles/ldap"
    )
    assert artifact["tags"]
    assert artifact["quality"]["score"] == 5
    assert artifact["content"].startswith("# Summary")
    assert "# Security Entities" in artifact["content"]
    assert "# Source-backed Claims" in artifact["content"]
