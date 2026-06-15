"""External event ingestion interfaces for NeuroCore."""

from __future__ import annotations

from datetime import UTC, datetime
import re

from neurocore.core.config import NeuroCoreConfig
from neurocore.core.models import MemoryRecord
from neurocore.ingest.article_distill import distill_article_knowledge
from neurocore.ingest.article_fetch import (
    canonicalize_article_url,
    fetch_article_source,
    title_from_url,
)
from neurocore.ingest.article_html import sanitize_article_html_to_markdown
from neurocore.ingest.article_gates import (
    ArticleGateConfig,
    evaluate_article_gate,
)
from neurocore.ingest.normalize import generate_stable_id
from neurocore.ingest.profiles import resolve_ingest_profile
from neurocore.ingest.source_tracking import (
    build_source_manifest,
    source_manifest_supersedes_id,
)
from neurocore.interfaces.capture import (
    _merge_duplicate_metadata,
    attach_store_warnings,
    capture_memory,
)
from neurocore.storage.base import BaseStore


def ingest_slack_event(
    payload: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    """Normalize a Slack event payload into a capture request."""
    if payload.get("type") == "url_verification":
        return {
            "source": "slack",
            "ignored": True,
            "challenge": payload.get("challenge"),
            "reason": "url_verification",
        }

    if _looks_like_slack_slash_command(payload):
        return _ingest_slack_slash_command(payload, store=store, config=config)

    event = dict(payload.get("event", {}))
    if payload.get("type") != "event_callback" or event.get("type") != "message":
        return {"source": "slack", "ignored": True, "reason": "unsupported_event"}
    if event.get("subtype"):
        return {"source": "slack", "ignored": True, "reason": "unsupported_subtype"}

    context = {
        "team_id": payload.get("team_id"),
        "channel_id": event.get("channel"),
        "user_id": event.get("user"),
    }
    profile = resolve_ingest_profile(
        source="slack",
        context=context,
        configured_profiles=config.ingest_profiles,
    )
    parsing_hints = _profile_parsing_hints(profile)
    article_prefix = str(parsing_hints.get("article_prefix") or "article:").strip()
    text = str(event.get("text") or "")
    if article_prefix and text.strip().lower().startswith(article_prefix.lower()):
        return _ingest_slack_article(
            payload=payload,
            event=event,
            store=store,
            config=config,
            profile=profile,
            parsing_hints=parsing_hints,
        )
    defaults = _profile_defaults(profile)
    capture = capture_memory(
        {
            "namespace": _external_namespace(
                payload.get("namespace"),
                payload.get("team_id"),
                prefix="slack",
                fallback=config.default_namespace,
            ),
            "bucket": str(
                payload.get("bucket")
                or defaults.get("bucket")
                or config.allowed_buckets[0]
            ),
            "sensitivity": str(
                payload.get("sensitivity")
                or defaults.get("sensitivity")
                or config.default_sensitivity
            ),
            "content": str(event.get("text") or ""),
            "content_format": "markdown",
            "source_type": "slack_message",
            "created_at": _slack_timestamp_to_iso(event.get("ts")),
            "external_id": event.get("client_msg_id") or event.get("ts"),
            "metadata": {
                "platform": "slack",
                "team_id": payload.get("team_id"),
                "channel_id": event.get("channel"),
                "user_id": event.get("user"),
                "event_type": event.get("type"),
                **_profile_metadata(profile),
            },
            "tags": _merge_tags(["slack"], defaults.get("tags", [])),
            "title": f"Slack {event.get('channel')}",
        },
        store=store,
        config=config,
    )
    return {"source": "slack", "ignored": False, "capture": capture}


def ingest_discord_event(
    payload: dict[str, object], store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    """Normalize a Discord message payload into a capture request."""
    envelope_type = payload.get("t")
    data = dict(payload.get("d", payload))
    if envelope_type and envelope_type != "MESSAGE_CREATE":
        return {"source": "discord", "ignored": True, "reason": "unsupported_event"}

    content = str(data.get("content") or "")
    if not content.strip():
        return {"source": "discord", "ignored": True, "reason": "empty_message"}

    author = dict(data.get("author", {}))
    context = {
        "guild_id": data.get("guild_id"),
        "channel_id": data.get("channel_id"),
        "author_id": author.get("id"),
    }
    profile = resolve_ingest_profile(
        source="discord",
        context=context,
        configured_profiles=config.ingest_profiles,
    )
    defaults = _profile_defaults(profile)
    capture = capture_memory(
        {
            "namespace": _external_namespace(
                payload.get("namespace"),
                data.get("guild_id"),
                prefix="discord",
                fallback=config.default_namespace,
            ),
            "bucket": str(
                payload.get("bucket")
                or defaults.get("bucket")
                or config.allowed_buckets[0]
            ),
            "sensitivity": str(
                payload.get("sensitivity")
                or defaults.get("sensitivity")
                or config.default_sensitivity
            ),
            "content": content,
            "content_format": "markdown",
            "source_type": "discord_message",
            "created_at": data.get("timestamp"),
            "external_id": data.get("id"),
            "metadata": {
                "platform": "discord",
                "guild_id": data.get("guild_id"),
                "channel_id": data.get("channel_id"),
                "author_id": author.get("id"),
                "author_username": author.get("username"),
                **_profile_metadata(profile),
            },
            "tags": _merge_tags(["discord"], defaults.get("tags", [])),
            "title": f"Discord {data.get('channel_id')}",
        },
        store=store,
        config=config,
    )
    return {"source": "discord", "ignored": False, "capture": capture}


def _looks_like_slack_slash_command(payload: dict[str, object]) -> bool:
    return (
        bool(payload.get("command"))
        and "text" in payload
        and (
            payload.get("channel_id") is not None or payload.get("user_id") is not None
        )
    )


def _ingest_slack_slash_command(
    payload: dict[str, object], *, store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    context = {
        "team_id": payload.get("team_id"),
        "channel_id": payload.get("channel_id"),
        "user_id": payload.get("user_id"),
    }
    profile = resolve_ingest_profile(
        source="slack",
        context=context,
        configured_profiles=config.ingest_profiles,
    )
    parsing_hints = _profile_parsing_hints(profile)
    expected_command = str(
        parsing_hints.get("article_slash_command") or "/distill"
    ).strip()
    command = str(payload.get("command") or "").strip()
    if not _matches_slack_command(command, expected_command):
        return {
            "source": "slack",
            "ignored": True,
            "reason": "unsupported_slash_command",
            "command": command,
        }
    event = _build_slack_slash_command_event(payload, parsing_hints=parsing_hints)
    normalized_payload = dict(payload)
    normalized_payload["type"] = "slash_command"
    normalized_payload["event"] = event
    return _ingest_slack_article(
        payload=normalized_payload,
        event=event,
        store=store,
        config=config,
        profile=profile,
        parsing_hints=parsing_hints,
    )


def _matches_slack_command(command: str, expected_command: str) -> bool:
    normalized_command = command.strip().lower()
    normalized_expected = expected_command.strip().lower()
    if not normalized_command or not normalized_expected:
        return False
    return normalized_command == normalized_expected


def _build_slack_slash_command_event(
    payload: dict[str, object], *, parsing_hints: dict[str, object]
) -> dict[str, object]:
    article_prefix = str(parsing_hints.get("article_prefix") or "article:").strip()
    submitted_text = str(payload.get("text") or "").strip()
    if article_prefix and not submitted_text.lower().startswith(article_prefix.lower()):
        submitted_text = f"{article_prefix} {submitted_text}".strip()
    return {
        "type": "slash_command",
        "channel": payload.get("channel_id"),
        "user": payload.get("user_id"),
        "text": submitted_text,
        "ts": payload.get("message_ts"),
        "client_msg_id": payload.get("trigger_id") or payload.get("command"),
    }


def _slack_timestamp_to_iso(value: object) -> str | None:
    """Convert a Slack floating-point timestamp into an ISO 8601 string."""
    if value is None:
        return None
    return datetime.fromtimestamp(float(str(value)), tz=UTC).isoformat()


def _external_namespace(
    explicit_namespace: object,
    external_identifier: object,
    *,
    prefix: str,
    fallback: str,
) -> str:
    """Derive a stable namespace for events coming from external platforms."""
    if explicit_namespace is not None and str(explicit_namespace).strip():
        return str(explicit_namespace)
    if external_identifier is None:
        return fallback
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(external_identifier).strip().lower())
    normalized = normalized.strip("-")
    if not normalized:
        return fallback
    return f"{prefix}-{normalized}"


def _profile_defaults(profile: dict[str, object] | None) -> dict[str, object]:
    if profile is None:
        return {}
    defaults = profile.get("defaults", {})
    return defaults if isinstance(defaults, dict) else {}


def _profile_metadata(profile: dict[str, object] | None) -> dict[str, object]:
    if profile is None:
        return {}
    metadata = {"matched_ingest_profile": profile["name"]}
    parsing_hints = _profile_parsing_hints(profile)
    if isinstance(parsing_hints, dict) and parsing_hints:
        metadata["ingest_parsing_hints"] = parsing_hints
    return metadata


def _profile_parsing_hints(profile: dict[str, object] | None) -> dict[str, object]:
    if profile is None:
        return {}
    hints = profile.get("parsing_hints", {})
    return hints if isinstance(hints, dict) else {}


def _merge_tags(base_tags: list[str], profile_tags: object) -> list[str]:
    merged = list(base_tags)
    if not isinstance(profile_tags, list):
        return merged
    for tag in profile_tags:
        value = str(tag).strip()
        if value and value not in merged:
            merged.append(value)
    return merged


def _ingest_slack_article(
    *,
    payload: dict[str, object],
    event: dict[str, object],
    store: BaseStore,
    config: NeuroCoreConfig,
    profile: dict[str, object] | None,
    parsing_hints: dict[str, object],
) -> dict[str, object]:
    defaults = _profile_defaults(profile)
    namespace = _external_namespace(
        payload.get("namespace"),
        payload.get("team_id"),
        prefix="slack",
        fallback=config.default_namespace,
    )
    bucket = str(
        payload.get("bucket") or defaults.get("bucket") or config.allowed_buckets[0]
    )
    sensitivity = str(
        payload.get("sensitivity")
        or defaults.get("sensitivity")
        or config.default_sensitivity
    )
    submitted_text = str(event.get("text") or "")
    prefix = str(parsing_hints.get("article_prefix") or "article:").strip()
    remainder = (
        submitted_text.strip()[len(prefix) :].strip() if prefix else submitted_text
    )
    url_match = re.search(r"https?://\S+", remainder)
    actor = f"slack:{str(event.get('user') or 'unknown')}"
    if url_match is None:
        details = {
            "reason": "missing-url",
            "slack_team_id": payload.get("team_id"),
            "slack_channel_id": event.get("channel"),
            "slack_user_id": event.get("user"),
            "slack_ts": event.get("ts"),
        }
        store.record_audit(
            actor=actor,
            operation="article_rejection",
            target_ids=[],
            outcome="rejected",
            details=details,
        )
        return {
            "source": "slack",
            "ignored": False,
            "mode": "article",
            "stored": False,
            "evaluation": {
                "accepted": False,
                "decision": "rejected",
                "hard_fail_reasons": ["missing-url"],
            },
            "raw_capture": None,
            "knowledge_capture": None,
            "persistence_state": "rejected",
        }
    source_url = url_match.group(0).rstrip("),.>")
    operator_note = remainder.replace(url_match.group(0), "", 1).strip() or None
    supplied_source = _build_supplied_article_source(payload, source_url=source_url)
    store.record_audit(
        actor=actor,
        operation="article_submission",
        target_ids=[],
        outcome="received",
        details={
            "source_url": source_url,
            "content_provenance": (
                "supplied" if supplied_source is not None else "fetched"
            ),
            "slack_team_id": payload.get("team_id"),
            "slack_channel_id": event.get("channel"),
            "slack_user_id": event.get("user"),
            "slack_ts": event.get("ts"),
        },
    )
    try:
        source = supplied_source or fetch_article_source(source_url)
    except ValueError as exc:
        source_manifest = build_source_manifest(
            store=store,
            namespace=namespace,
            source_type="article_raw",
            source_url=_safe_canonical_article_url(source_url),
            content_fingerprint="",
            content_provenance="supplied" if supplied_source is not None else "fetched",
            rejected=True,
        )
        details = {
            "reason": "fetch-failed",
            "source_url": source_url,
            "source_manifest": source_manifest,
            "error": str(exc),
            "slack_team_id": payload.get("team_id"),
            "slack_channel_id": event.get("channel"),
            "slack_user_id": event.get("user"),
            "slack_ts": event.get("ts"),
        }
        store.record_audit(
            actor=actor,
            operation="article_rejection",
            target_ids=[],
            outcome="rejected",
            details=details,
        )
        return {
            "source": "slack",
            "ignored": False,
            "mode": "article",
            "stored": False,
            "evaluation": {
                "accepted": False,
                "decision": "rejected",
                "hard_fail_reasons": ["fetch-failed"],
                "error": str(exc),
            },
            "raw_capture": None,
            "knowledge_capture": None,
            "source_manifest": source_manifest,
            "persistence_state": "rejected",
        }
    source.setdefault("raw_content", source.get("content"))
    gate_config = ArticleGateConfig(
        min_word_count=_positive_int(parsing_hints.get("article_min_word_count"), 150),
        min_quality_score=_positive_int(
            parsing_hints.get("article_min_quality_score"), 4
        ),
    )
    evaluation = evaluate_article_gate(
        source=source,
        store=store,
        namespace=namespace,
        config=gate_config,
    )
    source_manifest = build_source_manifest(
        store=store,
        namespace=namespace,
        source_type="article_raw",
        source_url=str(source.get("canonical_url") or ""),
        content_fingerprint=str(evaluation.get("content_fingerprint") or ""),
        content_provenance=str(source.get("content_provenance") or "fetched"),
        rejected=not bool(evaluation["accepted"]),
    )
    if not evaluation["accepted"]:
        details = {
            "canonical_url": evaluation.get("canonical_url"),
            "source_manifest": source_manifest,
            "quality_score": evaluation.get("quality_score"),
            "scores": evaluation.get("scores"),
            "hard_fail_reasons": evaluation.get("hard_fail_reasons"),
            "mandatory_failures": evaluation.get("mandatory_failures"),
            "slack_team_id": payload.get("team_id"),
            "slack_channel_id": event.get("channel"),
            "slack_user_id": event.get("user"),
            "slack_ts": event.get("ts"),
        }
        store.record_audit(
            actor=actor,
            operation="article_rejection",
            target_ids=[],
            outcome="rejected",
            details=details,
        )
        return {
            "source": "slack",
            "ignored": False,
            "mode": "article",
            "stored": False,
            "evaluation": evaluation,
            "raw_capture": None,
            "knowledge_capture": None,
            "source_manifest": source_manifest,
            "persistence_state": "rejected",
        }

    artifact_bucket = _resolve_article_artifact_bucket(
        parsing_hints.get("article_artifact_bucket"),
        raw_bucket=bucket,
        allowed_buckets=config.allowed_buckets,
    )
    source_fingerprint = str(evaluation["content_fingerprint"])
    source_metadata = {
        "platform": "slack",
        "team_id": payload.get("team_id"),
        "channel_id": event.get("channel"),
        "user_id": event.get("user"),
        "event_type": event.get("type"),
        "slack_ts": event.get("ts"),
        "canonical_url": source["canonical_url"],
        "source_url": source["canonical_url"],
        "submitted_source_url": source_url,
        "source_content_fingerprint": source_fingerprint,
        "source_manifest": source_manifest,
        "operator_note": operator_note,
        "canonical_title": source["title"],
        "content_provenance": str(source.get("content_provenance") or "fetched"),
        **_profile_metadata(profile),
    }
    if source.get("content_provenance") == "supplied":
        source_metadata["supplied_article_content"] = True
    if payload.get("command"):
        source_metadata["slack_command"] = str(payload.get("command"))
    if source.get("original_content_format"):
        source_metadata["source_content_format"] = source["original_content_format"]
    if source.get("sanitized_from_html"):
        source_metadata["sanitized_from_html"] = True

    store.record_audit(
        actor=actor,
        operation="article_acceptance",
        target_ids=[],
        outcome="accepted",
        details={
            "canonical_url": source["canonical_url"],
            "quality_score": evaluation.get("quality_score"),
            "scores": evaluation.get("scores"),
            "bucket": bucket,
            "artifact_bucket": artifact_bucket,
            "source_manifest": source_manifest,
        },
    )
    raw_capture = capture_memory(
        {
            "namespace": namespace,
            "bucket": bucket,
            "sensitivity": sensitivity,
            "content": str(source["content"]),
            "content_format": str(source["content_format"]),
            "source_type": "article_raw",
            "title": source["title"],
            "metadata": source_metadata,
            "tags": _merge_tags(
                ["slack", "article", "article-raw"], defaults.get("tags", [])
            ),
            "external_id": event.get("client_msg_id") or event.get("ts"),
            "idempotency_key": _article_idempotency_key(
                event=event,
                kind="raw",
                canonical_url=str(source["canonical_url"]),
            ),
            "created_at": _slack_timestamp_to_iso(event.get("ts")),
            "supersedes_id": source_manifest_supersedes_id(
                source_manifest,
                artifact_kind="document",
            ),
            "force_kind": "document",
        },
        store=store,
        config=config,
    )
    raw_document_id = str(raw_capture["id"])
    knowledge = distill_article_knowledge(
        source=source,
        evaluation=evaluation,
        operator_note=operator_note,
    )
    knowledge_capture = _store_article_knowledge_record(
        namespace=namespace,
        bucket=artifact_bucket,
        sensitivity=sensitivity,
        raw_document_id=raw_document_id,
        source=source,
        evaluation=evaluation,
        knowledge=knowledge,
        source_manifest=source_manifest,
        operator_note=operator_note,
        event=event,
        payload=payload,
        defaults=defaults,
        store=store,
        config=config,
    )
    persistence_state = _article_persistence_state(raw_capture, knowledge_capture)
    warnings = list(
        dict.fromkeys(
            [
                *list(raw_capture.get("warnings") or []),
                *list(knowledge_capture.get("warnings") or []),
            ]
        )
    )
    store.record_audit(
        actor=actor,
        operation="article_persistence",
        target_ids=[raw_document_id, str(knowledge_capture["id"])],
        outcome="success" if persistence_state != "partial" else "partial",
        details={
            "canonical_url": source["canonical_url"],
            "persistence_state": persistence_state,
            "raw_document_id": raw_document_id,
            "knowledge_record_id": knowledge_capture["id"],
            "source_manifest": source_manifest,
            "warnings": warnings,
        },
    )
    return {
        "source": "slack",
        "ignored": False,
        "mode": "article",
        "stored": True,
        "evaluation": evaluation,
        "raw_capture": raw_capture,
        "knowledge_capture": knowledge_capture,
        "source_manifest": source_manifest,
        "persistence_state": persistence_state,
    }


def _build_supplied_article_source(
    payload: dict[str, object], *, source_url: str
) -> dict[str, object] | None:
    supplied_content = payload.get("article_content")
    if supplied_content is None:
        return None
    content = str(supplied_content)
    if not content.strip():
        return None

    canonical_url = canonicalize_article_url(source_url)
    content_format = (
        str(payload.get("article_content_format") or "markdown").strip().lower()
    )
    title = str(payload.get("article_title") or "").strip() or title_from_url(
        canonical_url
    )
    source: dict[str, object] = {
        "url": canonical_url,
        "canonical_url": canonical_url,
        "content": content,
        "content_format": content_format or "markdown",
        "title": title,
        "raw_content": content,
        "content_provenance": "supplied",
    }
    if content_format == "html":
        sanitized_content, extracted_title = sanitize_article_html_to_markdown(
            content,
            fallback_title=title,
        )
        source["content"] = sanitized_content
        source["content_format"] = "markdown"
        source["original_content_format"] = "html"
        source["sanitized_from_html"] = True
        if extracted_title:
            source["title"] = extracted_title
    elif payload.get("article_original_content_format") is not None:
        source["original_content_format"] = (
            str(payload.get("article_original_content_format") or "").strip().lower()
        )
    return source


def _safe_canonical_article_url(url: str) -> str:
    try:
        return canonicalize_article_url(url)
    except ValueError:
        return str(url or "").strip()


def _store_article_knowledge_record(
    *,
    namespace: str,
    bucket: str,
    sensitivity: str,
    raw_document_id: str,
    source: dict[str, object],
    evaluation: dict[str, object],
    knowledge: dict[str, object],
    source_manifest: dict[str, object],
    operator_note: str | None,
    event: dict[str, object],
    payload: dict[str, object],
    defaults: dict[str, object],
    store: BaseStore,
    config: NeuroCoreConfig,
) -> dict[str, object]:
    content = str(knowledge["content"])
    knowledge_source_manifest = build_source_manifest(
        store=store,
        namespace=namespace,
        source_type="article_knowledge",
        source_url=str(source["canonical_url"]),
        content_fingerprint=str(evaluation["content_fingerprint"]),
        content_provenance=str(source.get("content_provenance") or "fetched"),
    )
    metadata = {
        "platform": "slack",
        "team_id": payload.get("team_id"),
        "channel_id": event.get("channel"),
        "user_id": event.get("user"),
        "slack_ts": event.get("ts"),
        "canonical_url": source["canonical_url"],
        "source_url": source["canonical_url"],
        "canonical_title": source["title"],
        "summary": knowledge["summary"],
        "key_claims": list(knowledge["key_claims"]),
        "techniques": list(knowledge["techniques"]),
        "security_entities": dict(knowledge["security_entities"]),
        "mitigations": list(knowledge["mitigations"]),
        "open_questions": list(knowledge["open_questions"]),
        "source_backed_claims": list(knowledge["source_backed_claims"]),
        "quality": dict(knowledge["quality"]),
        "raw_document_id": raw_document_id,
        "source_content_fingerprint": evaluation["content_fingerprint"],
        "source_manifest": knowledge_source_manifest,
        "raw_source_manifest": source_manifest,
        "operator_note": operator_note,
        "content_provenance": str(source.get("content_provenance") or "fetched"),
    }
    if source.get("content_provenance") == "supplied":
        metadata["supplied_article_content"] = True
    content_fingerprint = _record_fingerprint(content)
    signature = f"record:article_knowledge:markdown:{sensitivity}"
    existing_id = store.find_duplicate(namespace, content_fingerprint, signature)
    tags = tuple(
        _merge_tags(
            [*list(knowledge["tags"]), "article-knowledge"],
            defaults.get("tags", []),
        )
    )
    if existing_id is not None:
        _merge_duplicate_metadata(
            store=store,
            existing_id=existing_id,
            metadata=metadata,
            tags=tags,
            config=config,
        )
        return attach_store_warnings(
            {
                "id": existing_id,
                "kind": "record",
                "namespace": namespace,
                "bucket": bucket,
                "stored": True,
                "storage_outcome": "record-stored",
                "deduplicated": True,
                "chunk_count": 0,
                "warnings": [],
            },
            store=store,
        )
    record = MemoryRecord(
        id=generate_stable_id(
            "rec",
            namespace,
            bucket,
            content_fingerprint,
            "article_knowledge",
            sensitivity,
        ),
        namespace=namespace,
        bucket=bucket,
        content=content,
        content_format="markdown",
        source_type="article_knowledge",
        sensitivity=sensitivity,
        metadata=metadata,
        content_fingerprint=content_fingerprint,
        created_at=_parse_capture_time(event),
        updated_at=_parse_capture_time(event),
        title=f"{str(source['title']).strip()} Knowledge",
        tags=tags,
        external_id=event.get("client_msg_id") or event.get("ts"),
        idempotency_key=_article_idempotency_key(
            event=event,
            kind="knowledge",
            canonical_url=str(source["canonical_url"]),
        ),
        supersedes_id=source_manifest_supersedes_id(
            knowledge_source_manifest,
            artifact_kind="record",
        ),
    )
    store.save_record(record, signature=signature)
    return attach_store_warnings(
        {
            "id": record.id,
            "kind": "record",
            "namespace": namespace,
            "bucket": bucket,
            "stored": True,
            "storage_outcome": "record-stored",
            "deduplicated": False,
            "chunk_count": 0,
            "warnings": [],
        },
        store=store,
    )


def _article_persistence_state(
    raw_capture: dict[str, object], knowledge_capture: dict[str, object]
) -> str:
    if raw_capture.get("warnings") or knowledge_capture.get("warnings"):
        return "partial"
    if raw_capture.get("deduplicated") and knowledge_capture.get("deduplicated"):
        return "deduplicated"
    return "stored"


def _resolve_article_artifact_bucket(
    value: object,
    *,
    raw_bucket: str,
    allowed_buckets: tuple[str, ...],
) -> str:
    candidate = str(value or "").strip()
    if candidate and candidate in allowed_buckets:
        return candidate
    return raw_bucket


def _article_idempotency_key(
    *, event: dict[str, object], kind: str, canonical_url: str
) -> str:
    return (
        f"slack-article:{kind}:{str(event.get('channel') or '')}:"
        f"{str(event.get('ts') or '')}:{canonical_url}"
    )


def _record_fingerprint(content: str) -> str:
    from neurocore.ingest.normalize import compute_content_fingerprint

    return compute_content_fingerprint(content)


def _parse_capture_time(event: dict[str, object]) -> datetime:
    return datetime.fromisoformat(
        _slack_timestamp_to_iso(event.get("ts")) or datetime.now(UTC).isoformat()
    )


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
