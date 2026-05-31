"""Deterministic distillation helpers for reusable article knowledge."""

from __future__ import annotations

import re

from neurocore.ingest.article_gates import build_article_evaluation_text


def distill_article_knowledge(
    *,
    source: dict[str, object],
    evaluation: dict[str, object],
    operator_note: str | None = None,
) -> dict[str, object]:
    text = build_article_evaluation_text(source)
    sentences = _sentences(text)
    summary = " ".join(sentences[:2]).strip()
    key_claims = _select_key_claims(sentences)
    techniques = _select_techniques(sentences)
    tags = _derive_tags(str(source.get("title") or ""), text)
    reuse_notes = [
        f"Canonical URL: {str(source.get('canonical_url') or '').strip()}",
        "Store as reusable article knowledge for later retrieval and automation.",
    ]
    if operator_note:
        reuse_notes.append(operator_note.strip())
    content = (
        "# Summary\n"
        f"{summary}\n\n"
        "# Key Claims\n"
        + "\n".join(f"- {item}" for item in key_claims)
        + "\n\n# Techniques / Insights\n"
        + "\n".join(f"- {item}" for item in techniques)
        + "\n\n# Reuse Notes\n"
        + "\n".join(f"- {item}" for item in reuse_notes if item)
    )
    return {
        "summary": summary,
        "key_claims": key_claims,
        "techniques": techniques,
        "tags": tags,
        "quality": {
            "score": int(evaluation.get("quality_score") or 0),
            "scores": dict(evaluation.get("scores") or {}),
        },
        "content": content,
    }


def _sentences(text: str) -> list[str]:
    values = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
        if sentence.strip()
    ]
    return values[:12]


def _select_key_claims(sentences: list[str]) -> list[str]:
    if not sentences:
        return ["No reusable claims were extracted."]
    selected: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(
            hint in lowered
            for hint in (
                "reusable",
                "workflow",
                "method",
                "pattern",
                "detection",
                "checklist",
            )
        ):
            selected.append(sentence)
        if len(selected) == 3:
            break
    if not selected:
        selected = sentences[:3]
    return selected


def _select_techniques(sentences: list[str]) -> list[str]:
    selected: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(
            hint in lowered
            for hint in (
                "use ",
                "run ",
                "review",
                "document",
                "enumeration",
                "ldap",
                "detect",
            )
        ):
            selected.append(sentence)
        if len(selected) == 3:
            break
    if not selected and sentences:
        selected = sentences[:2]
    return selected


def _derive_tags(title: str, text: str) -> list[str]:
    lowered = f"{title} {text}".lower()
    tags = ["article", "article-knowledge", "source:slack"]
    for hint, tag in (
        ("ldap", "tech:ldap"),
        ("detection", "theme:detection"),
        ("workflow", "theme:workflow"),
        ("checklist", "theme:checklist"),
        ("automation", "theme:automation"),
        ("payload", "theme:payload"),
    ):
        if hint in lowered and tag not in tags:
            tags.append(tag)
    return tags
