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
    security_entities = _extract_security_entities(
        f"{source.get('title') or ''} {text}"
    )
    mitigations = _select_mitigations(sentences)
    open_questions = _select_open_questions(sentences)
    source_backed_claims = _source_backed_claims(
        key_claims,
        source_url=str(source.get("canonical_url") or "").strip(),
    )
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
        + "\n\n# Security Entities\n"
        + _render_security_entities(security_entities)
        + "\n\n# Mitigations\n"
        + "\n".join(f"- {item}" for item in mitigations)
        + "\n\n# Open Questions\n"
        + "\n".join(f"- {item}" for item in open_questions)
        + "\n\n# Source-backed Claims\n"
        + "\n".join(
            f"- {item['claim']} (evidence: {item['evidence']})"
            for item in source_backed_claims
        )
        + "\n\n# Reuse Notes\n"
        + "\n".join(f"- {item}" for item in reuse_notes if item)
    )
    return {
        "summary": summary,
        "key_claims": key_claims,
        "techniques": techniques,
        "security_entities": security_entities,
        "mitigations": mitigations,
        "open_questions": open_questions,
        "source_backed_claims": source_backed_claims,
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


def _extract_security_entities(text: str) -> dict[str, list[str]]:
    lowered = text.lower()
    technologies = _matching_terms(
        lowered,
        (
            "active directory",
            "api",
            "graphql",
            "kerberos",
            "ldap",
            "oauth",
            "saml",
            "windows defender",
        ),
    )
    tactics = _matching_terms(
        lowered,
        (
            "bypass",
            "collection",
            "enumeration",
            "evasion",
            "exploitation",
            "pivot",
            "reconnaissance",
            "triage",
        ),
    )
    artifacts = _matching_terms(
        lowered,
        (
            "checklist",
            "detection",
            "evidence",
            "finding",
            "payload",
            "playbook",
            "report",
            "workflow",
        ),
    )
    return {
        "technologies": technologies or ["unspecified"],
        "tactics": tactics or ["unspecified"],
        "artifacts": artifacts or ["unspecified"],
    }


def _select_mitigations(sentences: list[str]) -> list[str]:
    selected = _select_sentences(
        sentences,
        (
            "avoid ",
            "detect",
            "harden",
            "limit ",
            "mitigat",
            "monitor",
            "prevent",
            "review",
            "validate",
        ),
        limit=3,
    )
    return selected or ["No explicit mitigations were extracted."]


def _select_open_questions(sentences: list[str]) -> list[str]:
    questions = [sentence for sentence in sentences if sentence.endswith("?")]
    if questions:
        return questions[:3]
    if not sentences:
        return ["What source-backed follow-up should be verified before reuse?"]
    return [
        "What assumptions from this source need validation in the target context?",
        "Which extracted techniques require evidence before operational reuse?",
    ]


def _source_backed_claims(
    key_claims: list[str], *, source_url: str
) -> list[dict[str, str]]:
    return [
        {
            "claim": claim,
            "evidence": claim,
            "source_url": source_url,
        }
        for claim in key_claims[:3]
    ]


def _select_sentences(
    sentences: list[str], hints: tuple[str, ...], *, limit: int
) -> list[str]:
    selected: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(hint in lowered for hint in hints):
            selected.append(sentence)
        if len(selected) == limit:
            break
    return selected


def _matching_terms(text: str, candidates: tuple[str, ...]) -> list[str]:
    return [term for term in candidates if term in text]


def _render_security_entities(entities: dict[str, list[str]]) -> str:
    lines: list[str] = []
    for group, values in entities.items():
        lines.append(f"- {group}: {', '.join(values)}")
    return "\n".join(lines)


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
