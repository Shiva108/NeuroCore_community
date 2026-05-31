"""Deterministic quality gates for reusable article storage."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from neurocore.ingest.article_html import (
    looks_like_markup_or_navigation,
    looks_like_target_specific_article,
    strip_html_to_text,
)
from neurocore.ingest.normalize import compute_content_fingerprint
from neurocore.storage.base import BaseStore

SECURITY_KEYWORDS = (
    "attack",
    "automation",
    "checklist",
    "collection",
    "detect",
    "detection",
    "enumeration",
    "exploit",
    "hackingagent",
    "incident",
    "kerberos",
    "ldap",
    "methodology",
    "operator",
    "payload",
    "playbook",
    "reconnaissance",
    "reuse",
    "reusable",
    "review",
    "security",
    "technique",
    "triage",
    "validation",
    "vulnerability",
    "workflow",
)
ACTIONABILITY_HINTS = (
    "checklist",
    "document",
    "expand scope",
    "how to",
    "operator",
    "playbook",
    "review",
    "run ",
    "step",
    "use ",
    "workflow",
)
USEFULNESS_HINTS = (
    "automation",
    "checklist",
    "downstream",
    "playbook",
    "portable",
    "repeatable",
    "retrieval",
    "reusable",
    "workflow",
)


@dataclass(frozen=True)
class ArticleGateConfig:
    min_word_count: int = 150
    min_quality_score: int = 4
    mandatory_scores: tuple[str, ...] = ("novelty", "signal_to_noise")
    security_keywords: tuple[str, ...] = field(
        default_factory=lambda: SECURITY_KEYWORDS
    )


def build_article_evaluation_text(source: dict[str, object]) -> str:
    content = str(source.get("content") or "")
    content_format = str(source.get("content_format") or "").strip().lower()
    if content_format == "html":
        return strip_html_to_text(content)
    return " ".join(content.split())


def find_existing_article_artifacts(
    *,
    store: BaseStore,
    namespace: str,
    canonical_url: str,
    content_fingerprint: str,
) -> dict[str, str | None]:
    raw_document_id: str | None = None
    knowledge_record_id: str | None = None
    for document in store.list_documents(include_archived=True):
        if document.namespace != namespace or document.source_type != "article_raw":
            continue
        if str(document.metadata.get("canonical_url") or "") != canonical_url:
            continue
        if document.content_fingerprint != content_fingerprint:
            continue
        raw_document_id = document.id
        break
    for record in store.list_records(include_archived=True):
        if record.namespace != namespace or record.source_type != "article_knowledge":
            continue
        if str(record.metadata.get("canonical_url") or "") != canonical_url:
            continue
        if (
            str(record.metadata.get("source_content_fingerprint") or "")
            != content_fingerprint
        ):
            continue
        knowledge_record_id = record.id
        break
    return {
        "raw_document_id": raw_document_id,
        "knowledge_record_id": knowledge_record_id,
    }


def evaluate_article_gate(
    *,
    source: dict[str, object],
    store: BaseStore,
    namespace: str,
    config: ArticleGateConfig,
) -> dict[str, object]:
    canonical_url = str(source.get("canonical_url") or "").strip()
    evaluation_text = build_article_evaluation_text(source)
    normalized_text = " ".join(evaluation_text.split())
    fingerprint = compute_content_fingerprint(str(source.get("content") or ""))
    existing = find_existing_article_artifacts(
        store=store,
        namespace=namespace,
        canonical_url=canonical_url,
        content_fingerprint=fingerprint,
    )
    word_count = len(normalized_text.split()) if normalized_text else 0
    hard_fail_reasons: list[str] = []
    if word_count < config.min_word_count:
        hard_fail_reasons.append("below-min-word-count")
    original_content_format = str(
        source.get("original_content_format") or source.get("content_format") or ""
    )
    if looks_like_markup_or_navigation(
        raw_content=str(source.get("raw_content") or source.get("content") or ""),
        content_format=original_content_format,
        evaluation_text=normalized_text,
    ):
        hard_fail_reasons.append("mostly-markup-or-navigation")
    if looks_like_target_specific_article(normalized_text):
        hard_fail_reasons.append("target-specific-or-engagement-bound")

    duplicate = bool(existing["raw_document_id"] or existing["knowledge_record_id"])
    if duplicate:
        return {
            "accepted": True,
            "decision": "deduplicated",
            "duplicate": True,
            "content_fingerprint": fingerprint,
            "canonical_url": canonical_url,
            "word_count": word_count,
            "quality_score": config.min_quality_score,
            "scores": {
                "relevance": 1,
                "novelty": 1,
                "signal_to_noise": 1,
                "actionability": 1,
                "credibility": 1,
                "downstream_usefulness": 1,
            },
            "mandatory_failures": [],
            "hard_fail_reasons": [],
            "existing_ids": existing,
        }

    unique_ratio = _unique_word_ratio(normalized_text)
    relevance = int(_contains_any(normalized_text, config.security_keywords))
    novelty = 1
    signal_to_noise = int(unique_ratio >= 0.35 and not hard_fail_reasons)
    actionability = int(_contains_any(normalized_text, ACTIONABILITY_HINTS))
    credibility = int(bool(canonical_url and word_count >= config.min_word_count))
    downstream_usefulness = int(
        _contains_any(normalized_text, USEFULNESS_HINTS)
        or (relevance and actionability)
    )
    scores = {
        "relevance": relevance,
        "novelty": novelty,
        "signal_to_noise": signal_to_noise,
        "actionability": actionability,
        "credibility": credibility,
        "downstream_usefulness": downstream_usefulness,
    }
    quality_score = sum(scores.values())
    mandatory_failures = [
        name for name in config.mandatory_scores if scores.get(name, 0) != 1
    ]
    accepted = (
        not hard_fail_reasons
        and quality_score >= config.min_quality_score
        and not mandatory_failures
    )
    return {
        "accepted": accepted,
        "decision": "accepted" if accepted else "rejected",
        "duplicate": False,
        "content_fingerprint": fingerprint,
        "canonical_url": canonical_url,
        "word_count": word_count,
        "quality_score": quality_score,
        "scores": scores,
        "mandatory_failures": mandatory_failures,
        "hard_fail_reasons": hard_fail_reasons,
        "existing_ids": existing,
    }


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(value in lowered for value in values)


def _unique_word_ratio(text: str) -> float:
    words = re.findall(r"[a-zA-Z0-9_/-]+", text.lower())
    if not words:
        return 0.0
    return len(set(words)) / len(words)
