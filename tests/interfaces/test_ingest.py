import json

from neurocore.storage.local_only_sealed_mirrored_store import (
    LocalOnlySealedMirroredStore,
)
from neurocore.storage.mirrored_store import MirroredStore
from neurocore.storage.router import RoutedStore
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.ingest import ingest_discord_event, ingest_slack_event
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


def build_config(**overrides) -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research", "ops"),
        default_sensitivity="standard",
        max_atomic_tokens=6,
        **overrides,
    )


def test_ingest_slack_message_captures_memory_record():
    store = InMemoryStore()

    response = ingest_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "Slack message for memory",
                "ts": "1713897900.000100",
            },
            "bucket": "research",
        },
        store=store,
        config=build_config(),
    )

    assert response["ignored"] is False
    assert response["capture"]["kind"] == "record"
    assert store.get_record(response["capture"]["id"]) is not None


def test_ingest_slack_article_captures_raw_document_and_knowledge_record(monkeypatch):
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
    store = InMemoryStore()

    response = ingest_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
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
            "bucket": "research",
        },
        store=store,
        config=build_config(),
    )

    assert response["ignored"] is False
    assert response["mode"] == "article"
    assert response["evaluation"]["accepted"] is True
    assert response["persistence_state"] == "stored"
    assert response["raw_capture"]["kind"] == "document"
    assert response["knowledge_capture"]["kind"] == "record"
    raw_document = store.get_document(response["raw_capture"]["id"])
    knowledge_record = store.get_record(response["knowledge_capture"]["id"])
    assert raw_document is not None
    assert knowledge_record is not None
    assert raw_document.source_type == "article_raw"
    assert knowledge_record.source_type == "article_knowledge"
    assert (
        raw_document.metadata["canonical_url"]
        == "https://example.invalid/articles/ldap"
    )
    assert knowledge_record.metadata["raw_document_id"] == raw_document.id


def test_ingest_slack_distill_slash_command_reuses_article_pipeline(monkeypatch):
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
    store = InMemoryStore()

    response = ingest_slack_event(
        {
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
        store=store,
        config=build_config(),
    )

    assert response["ignored"] is False
    assert response["mode"] == "article"
    assert response["evaluation"]["accepted"] is True
    raw_document = store.get_document(response["raw_capture"]["id"])
    assert raw_document is not None
    assert raw_document.metadata["slack_command"] == "/distill"


def test_ingest_slack_article_uses_supplied_content_without_fetch():
    store = InMemoryStore()

    response = ingest_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
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
            "article_content": SUPPLIED_ARTICLE_CONTENT,
            "article_content_format": "markdown",
            "article_title": "Reusable LDAP Enumeration",
            "bucket": "research",
        },
        store=store,
        config=build_config(),
    )

    assert response["ignored"] is False
    assert response["mode"] == "article"
    assert response["stored"] is True
    raw_document = store.get_document(response["raw_capture"]["id"])
    knowledge_record = store.get_record(response["knowledge_capture"]["id"])
    assert raw_document is not None
    assert knowledge_record is not None
    assert raw_document.metadata["content_provenance"] == "supplied"
    assert raw_document.metadata["supplied_article_content"] is True
    assert knowledge_record.metadata["content_provenance"] == "supplied"
    assert knowledge_record.metadata["supplied_article_content"] is True


def test_ingest_slack_distill_slash_command_accepts_supplied_html_content():
    store = InMemoryStore()

    response = ingest_slack_event(
        {
            "command": "/distill",
            "team_id": "T123",
            "channel_id": "C123",
            "user_id": "U123",
            "trigger_id": "1337.42",
            "text": "https://example.invalid/articles/ldap",
            "article_content": (
                "<html><body><article><h1>Reusable LDAP Enumeration</h1>"
                "<p>Reusable LDAP reconnaissance workflow for red teams with "
                "detection-aware collection, operator notes, and portable "
                "checklists that apply across environments without "
                "client-specific dependencies. Use narrow LDAP queries first. "
                "Expand scope iteratively. Document assumptions. Turn recurring "
                "checks into reusable operator notes. Review detections before "
                "large collections. Capture follow-up actions for downstream "
                "automation.</p>"
                "<p>Validate bind methods, naming context scope, and collection "
                "fallbacks before moving to larger directory sweeps. Track how "
                "assumptions affect coverage, how failed queries change the plan, "
                "how operator notes should be written for later retrieval, and how "
                "results should feed reusable playbooks for HackingAgent. The "
                "workflow should produce concise checklists and troubleshooting "
                "prompts that another operator can reuse in a different "
                "environment.</p>"
                "<p>It should also explain when to stop broad collection, when to "
                "pivot into host or service validation, when to compare results with "
                "existing detections, and how to write small evidence logging loops "
                "that remain useful for later machine retrieval and downstream "
                "automation.</p>"
                "</article></body></html>"
            ),
            "article_content_format": "html",
            "bucket": "research",
        },
        store=store,
        config=build_config(),
    )

    assert response["ignored"] is False
    assert response["mode"] == "article"
    assert response["stored"] is True
    raw_document = store.get_document(response["raw_capture"]["id"])
    assert raw_document is not None
    assert raw_document.title == "Reusable LDAP Enumeration"
    assert raw_document.raw_content.startswith("# Reusable LDAP Enumeration")
    assert raw_document.metadata["source_content_format"] == "html"
    assert raw_document.metadata["sanitized_from_html"] is True


def test_ingest_slack_ignores_unsupported_slash_commands():
    store = InMemoryStore()

    response = ingest_slack_event(
        {
            "command": "/memory-review",
            "team_id": "T123",
            "channel_id": "C123",
            "user_id": "U123",
            "text": "incident timeline",
        },
        store=store,
        config=build_config(),
    )

    assert response["ignored"] is True
    assert response["reason"] == "unsupported_slash_command"


def test_ingest_slack_distill_slash_command_honors_profile_override(monkeypatch):
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
    store = InMemoryStore()
    config = build_config(
        ingest_profiles={
            "version": "1",
            "profiles": [
                {
                    "name": "slack-ops-intake",
                    "source": "slack",
                    "match": {"team_id": "T123", "channel_id": "C123"},
                    "parsing_hints": {"article_slash_command": "/stash"},
                }
            ],
        }
    )

    response = ingest_slack_event(
        {
            "command": "/stash",
            "team_id": "T123",
            "channel_id": "C123",
            "user_id": "U123",
            "trigger_id": "1337.42",
            "text": "https://example.invalid/articles/ldap",
            "bucket": "research",
        },
        store=store,
        config=config,
    )

    assert response["ignored"] is False
    assert response["mode"] == "article"
    assert response["evaluation"]["accepted"] is True


def test_ingest_slack_article_rejects_low_quality_content_and_records_audit(
    monkeypatch,
):
    class DummyResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self):
            return (
                b"<html><body>"
                b"<nav>Menu Subscribe Sign in Privacy Policy Cookie Settings</nav>"
                b"<div>Menu Subscribe Sign in Privacy Policy Cookie Settings</div>"
                b"<footer>Menu Subscribe Sign in Privacy Policy Cookie Settings</footer>"
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
    store = InMemoryStore()

    response = ingest_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "article: https://example.invalid/navigation-shell",
                "ts": "1713897900.000100",
            },
            "bucket": "research",
        },
        store=store,
        config=build_config(),
    )

    assert response["ignored"] is False
    assert response["mode"] == "article"
    assert response["evaluation"]["accepted"] is False
    assert response["raw_capture"] is None
    assert response["knowledge_capture"] is None
    assert store.list_documents() == []
    assert store.list_records() == []
    assert store.audit_events[-1]["operation"] == "article_rejection"
    assert store.audit_events[-1]["details"]["canonical_url"] == (
        "https://example.invalid/navigation-shell"
    )


def test_ingest_slack_article_is_idempotent_for_repeated_submissions(monkeypatch):
    class DummyResponse:
        headers = {"Content-Type": "text/markdown; charset=utf-8"}

        def read(self):
            return (
                b"# Reusable LDAP Enumeration\n\n"
                b"Reusable LDAP reconnaissance workflow for red teams with "
                b"detection-aware collection, operator notes, and portable "
                b"checklists that apply across environments. Use narrow LDAP "
                b"queries first. Expand scope iteratively. Document assumptions. "
                b"Capture follow-up actions for downstream automation. Validate "
                b"bind methods, naming context scope, and collection fallbacks "
                b"before moving to larger directory sweeps. Track how "
                b"assumptions affect coverage, how failed queries change the "
                b"plan, how operator notes should be written for later "
                b"retrieval, and how results should feed reusable playbooks for "
                b"HackingAgent. The article also explains when to stop broad "
                b"collection, when to pivot into host or service validation, "
                b"when to compare results with existing detections, and how to "
                b"write concise checklists that another operator can reuse in a "
                b"different environment. It closes with repeatable "
                b"troubleshooting questions, evidence logging guidance, and "
                b"small validation loops that make the material durable for "
                b"later machine reuse across teams and future investigations "
                b"and reviews."
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )
    store = InMemoryStore()
    request = {
        "type": "event_callback",
        "team_id": "T123",
        "event": {
            "type": "message",
            "channel": "C123",
            "user": "U123",
            "text": "article: https://example.invalid/articles/ldap",
            "ts": "1713897900.000100",
        },
        "bucket": "research",
    }

    first = ingest_slack_event(request, store=store, config=build_config())
    second = ingest_slack_event(request, store=store, config=build_config())

    assert first["raw_capture"]["id"] == second["raw_capture"]["id"]
    assert first["knowledge_capture"]["id"] == second["knowledge_capture"]["id"]
    assert second["persistence_state"] == "deduplicated"


def test_ingest_slack_article_reports_partial_mirror_writes(monkeypatch):
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

    class FailingLocalStore(InMemoryStore):
        def save_record(self, record, signature):
            raise RuntimeError("disk unavailable")

        def save_document(self, document, chunks, signature):
            raise RuntimeError("disk unavailable")

    monkeypatch.setattr(
        "neurocore.ingest.article_fetch.urllib_request.urlopen",
        lambda request, timeout=0.0: DummyResponse(),
    )

    local = RoutedStore(
        primary_store=FailingLocalStore(),
        sealed_store=InMemoryStore(),
    )
    cloud = RoutedStore(
        primary_store=InMemoryStore(),
        sealed_store=InMemoryStore(),
    )
    store = MirroredStore(local_store=local, cloud_store=cloud, read_preference="local")

    response = ingest_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "article: https://example.invalid/articles/ldap",
                "ts": "1713897900.000100",
            },
            "bucket": "research",
        },
        store=store,
        config=build_config(),
    )

    assert response["evaluation"]["accepted"] is True
    assert response["persistence_state"] == "partial"
    assert response["raw_capture"]["warnings"]
    assert response["knowledge_capture"]["warnings"]
    assert cloud.get_document(response["raw_capture"]["id"]) is not None
    assert cloud.get_record(response["knowledge_capture"]["id"]) is not None


def test_ingest_discord_message_create_captures_memory_record():
    store = InMemoryStore()

    response = ingest_discord_event(
        {
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "m-123",
                "guild_id": "g-123",
                "channel_id": "c-123",
                "author": {"id": "u-123", "username": "alice"},
                "content": "Discord message for memory",
                "timestamp": "2026-04-23T10:00:00+00:00",
            },
            "bucket": "ops",
        },
        store=store,
        config=build_config(),
    )

    assert response["ignored"] is False
    assert response["capture"]["kind"] == "record"
    assert store.get_record(response["capture"]["id"]) is not None


def test_ingest_slack_applies_matching_profile_defaults(tmp_path):
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "version": "1",
                "profiles": [
                    {
                        "name": "slack-ops",
                        "source": "slack",
                        "match": {"team_id": "T123", "channel_id": "C123"},
                        "defaults": {
                            "bucket": "ops",
                            "tags": ["ops-profile"],
                            "sensitivity": "restricted",
                        },
                        "parsing_hints": {"parser": "ops-note"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = InMemoryStore()
    config = build_config(
        ingest_profile_path=str(profile_path),
        ingest_profiles=json.loads(profile_path.read_text(encoding="utf-8")),
    )

    response = ingest_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "Profiled Slack message",
                "ts": "1713897900.000100",
            },
        },
        store=store,
        config=config,
    )

    record = store.get_record(response["capture"]["id"])
    assert record is not None
    assert record.bucket == "ops"
    assert record.sensitivity == "restricted"
    assert set(record.tags) == {"slack", "ops-profile"}
    assert record.metadata["matched_ingest_profile"] == "slack-ops"
    assert record.metadata["ingest_parsing_hints"] == {"parser": "ops-note"}


def test_ingest_discord_uses_more_specific_profile_over_source_default(tmp_path):
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "version": "1",
                "profiles": [
                    {
                        "name": "discord-default",
                        "source": "discord",
                        "match": {"guild_id": "g-123"},
                        "defaults": {
                            "bucket": "research",
                            "tags": ["guild-default"],
                            "sensitivity": "standard",
                        },
                        "parsing_hints": {"mode": "default"},
                    },
                    {
                        "name": "discord-channel-specific",
                        "source": "discord",
                        "match": {"guild_id": "g-123", "channel_id": "c-123"},
                        "defaults": {
                            "bucket": "ops",
                            "tags": ["channel-specific"],
                            "sensitivity": "restricted",
                        },
                        "parsing_hints": {"mode": "specific"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    store = InMemoryStore()
    config = build_config(
        ingest_profile_path=str(profile_path),
        ingest_profiles=json.loads(profile_path.read_text(encoding="utf-8")),
    )

    response = ingest_discord_event(
        {
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "m-123",
                "guild_id": "g-123",
                "channel_id": "c-123",
                "author": {"id": "u-123", "username": "alice"},
                "content": "Discord message for memory",
                "timestamp": "2026-04-23T10:00:00+00:00",
            },
        },
        store=store,
        config=config,
    )

    record = store.get_record(response["capture"]["id"])
    assert record is not None
    assert record.bucket == "ops"
    assert record.sensitivity == "restricted"
    assert "channel-specific" in record.tags
    assert record.metadata["matched_ingest_profile"] == "discord-channel-specific"
    assert record.metadata["ingest_parsing_hints"] == {"mode": "specific"}


def test_ingest_explicit_request_values_override_profile_defaults(tmp_path):
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "version": "1",
                "profiles": [
                    {
                        "name": "slack-ops",
                        "source": "slack",
                        "match": {"team_id": "T123"},
                        "defaults": {
                            "bucket": "ops",
                            "tags": ["ops-profile"],
                            "sensitivity": "restricted",
                        },
                        "parsing_hints": {"parser": "ops-note"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = InMemoryStore()
    config = build_config(
        ingest_profile_path=str(profile_path),
        ingest_profiles=json.loads(profile_path.read_text(encoding="utf-8")),
    )

    response = ingest_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "bucket": "research",
            "sensitivity": "standard",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "Profiled Slack message",
                "ts": "1713897900.000100",
            },
        },
        store=store,
        config=config,
    )

    record = store.get_record(response["capture"]["id"])
    assert record is not None
    assert record.bucket == "research"
    assert record.sensitivity == "standard"
    assert set(record.tags) == {"slack", "ops-profile"}


def test_ingest_without_profiles_keeps_existing_behavior():
    store = InMemoryStore()

    response = ingest_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "Slack message for memory",
                "ts": "1713897900.000100",
            },
            "bucket": "research",
        },
        store=store,
        config=build_config(),
    )

    record = store.get_record(response["capture"]["id"])
    assert record is not None
    assert record.bucket == "research"
    assert record.sensitivity == "standard"
    assert record.tags == ("slack",)
    assert "matched_ingest_profile" not in record.metadata
