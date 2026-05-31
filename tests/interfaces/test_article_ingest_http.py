import json

from fastapi.testclient import TestClient

from neurocore.adapters.http_api import create_app
from neurocore.core.config import NeuroCoreConfig
from neurocore.storage.in_memory import InMemoryStore

SUPPLIED_ARTICLE_CONTENT = (
    "# Reusable LDAP Enumeration\n\n"
    "Reusable LDAP reconnaissance workflow for red teams with "
    "detection-aware collection, operator notes, and portable "
    "checklists that apply across environments without "
    "client-specific dependencies. Use narrow LDAP queries first. "
    "Expand scope iteratively. Document assumptions. Turn "
    "recurring checks into reusable operator notes. Review "
    "detections before large collections. Capture follow-up "
    "actions for downstream automation. Validate bind methods, "
    "naming context scope, and collection fallbacks before moving "
    "to larger directory sweeps. Track how assumptions affect "
    "coverage, how failed queries change the plan, how operator "
    "notes should be written for later retrieval, and how results "
    "should feed reusable playbooks for HackingAgent. The article "
    "also explains when to stop broad collection, when to pivot "
    "into host or service validation, when to compare results with "
    "existing detections, and how to write concise checklists that "
    "another operator can reuse in a different environment. It "
    "closes with repeatable troubleshooting questions, evidence "
    "logging guidance, and small validation loops that make the "
    "material durable for later machine reuse."
)


def test_http_api_slack_article_ingest_returns_article_payload(monkeypatch):
    class DummyResponse:
        headers = {"Content-Type": "text/markdown; charset=utf-8"}

        def read(self):
            return (
                b"# Reusable LDAP Enumeration\n\n"
                b"Reusable LDAP reconnaissance workflow for red teams with "
                b"detection-aware collection, operator notes, and portable "
                b"checklists that apply across environments without "
                b"client-specific dependencies. Use narrow LDAP queries first. "
                b"Expand scope iteratively. Document assumptions. Turn "
                b"recurring checks into reusable operator notes. Review "
                b"detections before large collections. Capture follow-up "
                b"actions for downstream automation. Validate bind methods, "
                b"naming context scope, and collection fallbacks before moving "
                b"to larger directory sweeps. Track how assumptions affect "
                b"coverage, how failed queries change the plan, how operator "
                b"notes should be written for later retrieval, and how results "
                b"should feed reusable playbooks for HackingAgent. The article "
                b"also explains when to stop broad collection, when to pivot "
                b"into host or service validation, when to compare results with "
                b"existing detections, and how to write concise checklists that "
                b"another operator can reuse in a different environment. It "
                b"closes with repeatable troubleshooting questions, evidence "
                b"logging guidance, and small validation loops that make the "
                b"material durable for later machine reuse."
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )

    app = create_app(
        store=InMemoryStore(),
        config=NeuroCoreConfig(
            default_namespace="project-alpha",
            allowed_buckets=("research", "ops"),
            default_sensitivity="standard",
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/ingest/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "bucket": "research",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": (
                    "article: https://example.invalid/articles/ldap "
                    "Use this for LDAP playbook updates."
                ),
                "ts": "1713897900.000100",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "article"
    assert payload["evaluation"]["accepted"] is True
    assert payload["raw_capture"]["kind"] == "document"
    assert payload["knowledge_capture"]["kind"] == "record"


def test_http_api_slack_distill_slash_command_accepts_form_payload(monkeypatch):
    class DummyResponse:
        headers = {"Content-Type": "text/markdown; charset=utf-8"}

        def read(self):
            return (
                b"# Reusable LDAP Enumeration\n\n"
                b"Reusable LDAP reconnaissance workflow for red teams with "
                b"detection-aware collection, operator notes, and portable "
                b"checklists that apply across environments without "
                b"client-specific dependencies. Use narrow LDAP queries first. "
                b"Expand scope iteratively. Document assumptions. Turn "
                b"recurring checks into reusable operator notes. Review "
                b"detections before large collections. Capture follow-up "
                b"actions for downstream automation. Validate bind methods, "
                b"naming context scope, and collection fallbacks before moving "
                b"to larger directory sweeps. Track how assumptions affect "
                b"coverage, how failed queries change the plan, how operator "
                b"notes should be written for later retrieval, and how results "
                b"should feed reusable playbooks for HackingAgent. The article "
                b"also explains when to stop broad collection, when to pivot "
                b"into host or service validation, when to compare results with "
                b"existing detections, and how to write concise checklists that "
                b"another operator can reuse in a different environment. It "
                b"closes with repeatable troubleshooting questions, evidence "
                b"logging guidance, and small validation loops that make the "
                b"material durable for later machine reuse."
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )

    app = create_app(
        store=InMemoryStore(),
        config=NeuroCoreConfig(
            default_namespace="project-alpha",
            allowed_buckets=("research", "ops"),
            default_sensitivity="standard",
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/ingest/slack",
        data={
            "command": "/distill",
            "team_id": "T123",
            "channel_id": "C123",
            "user_id": "U123",
            "trigger_id": "1337.42",
            "text": (
                "https://example.invalid/articles/ldap "
                "Use this for LDAP playbook updates."
            ),
            "bucket": "research",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "article"
    assert payload["evaluation"]["accepted"] is True
    assert payload["raw_capture"]["kind"] == "document"
    assert payload["knowledge_capture"]["kind"] == "record"


def test_http_api_slack_article_fetch_failure_returns_rejected_payload(monkeypatch):
    def raise_url_error(request, timeout=0.0):
        del request, timeout
        raise OSError("network unavailable")

    class FailingOpener:
        def open(self, request, timeout=0.0):
            del request, timeout
            raise OSError("network unavailable")

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        raise_url_error,
    )
    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.build_opener",
        lambda *handlers: FailingOpener(),
    )

    app = create_app(
        store=InMemoryStore(),
        config=NeuroCoreConfig(
            default_namespace="project-alpha",
            allowed_buckets=("research", "ops"),
            default_sensitivity="standard",
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/ingest/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "bucket": "research",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "article: https://example.invalid/articles/ldap",
                "ts": "1713897900.000100",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "article"
    assert payload["stored"] is False
    assert payload["evaluation"]["accepted"] is False
    assert payload["evaluation"]["hard_fail_reasons"] == ["fetch-failed"]
    assert payload["raw_capture"] is None
    assert payload["knowledge_capture"] is None


def test_http_api_slack_article_ingest_accepts_supplied_content():
    app = create_app(
        store=InMemoryStore(),
        config=NeuroCoreConfig(
            default_namespace="project-alpha",
            allowed_buckets=("research", "ops"),
            default_sensitivity="standard",
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/ingest/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "bucket": "research",
            "article_content": SUPPLIED_ARTICLE_CONTENT,
            "article_content_format": "markdown",
            "article_title": "Reusable LDAP Enumeration",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "article: https://example.invalid/articles/ldap",
                "ts": "1713897900.000100",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "article"
    assert payload["stored"] is True
    assert payload["raw_capture"]["kind"] == "document"
    assert payload["knowledge_capture"]["kind"] == "record"


def test_http_api_slack_distill_form_payload_accepts_supplied_content():
    app = create_app(
        store=InMemoryStore(),
        config=NeuroCoreConfig(
            default_namespace="project-alpha",
            allowed_buckets=("research", "ops"),
            default_sensitivity="standard",
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/ingest/slack",
        data={
            "command": "/distill",
            "team_id": "T123",
            "channel_id": "C123",
            "user_id": "U123",
            "trigger_id": "1337.42",
            "text": "https://example.invalid/articles/ldap",
            "article_content": SUPPLIED_ARTICLE_CONTENT,
            "article_content_format": "markdown",
            "article_title": "Reusable LDAP Enumeration",
            "bucket": "research",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "article"
    assert payload["stored"] is True
    assert payload["raw_capture"]["kind"] == "document"
    assert payload["knowledge_capture"]["kind"] == "record"
