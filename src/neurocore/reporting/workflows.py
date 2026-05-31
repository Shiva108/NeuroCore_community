"""Helpers for assembling report prompts from NeuroCore query responses."""

from __future__ import annotations

import json


def build_report_context_from_query_response(
    query_response: dict[str, object], *, max_items: int = 5
) -> str:
    """Build a compact markdown context block from query results."""
    if max_items < 1:
        raise ValueError("max_items must be >= 1")
    results = query_response.get("results", [])
    if not isinstance(results, list) or not results:
        return "No query results were provided."

    lines = ["## Retrieved Memory Context"]
    for result in results[:max_items]:
        if not isinstance(result, dict):
            continue
        item_id = str(result.get("id", "unknown"))
        kind = str(result.get("kind", "unknown"))
        score = result.get("score")
        metadata = result.get("metadata", {})
        source_type = str(
            result.get("source_type")
            or (metadata.get("source_type") if isinstance(metadata, dict) else None)
            or "unknown"
        )
        content = str(
            result.get("content") or result.get("content_preview") or ""
        ).strip()
        metadata_json = (
            json.dumps(metadata, sort_keys=True)
            if isinstance(metadata, dict)
            else str(metadata)
        )
        lines.append(f"- id: {item_id}")
        lines.append(f"  kind: {kind}")
        lines.append(f"  score: {score}")
        lines.append(f"  source_type: {source_type}")
        lines.append(f"  content: {content}")
        lines.append(f"  metadata: {metadata_json}")
    return "\n".join(lines)


def build_sectioned_report_prompt(
    *,
    objective: str,
    context_markdown: str,
    sections: tuple[str, ...] = ("Overview", "Findings", "Risks", "Actions"),
) -> str:
    """Build a deterministic markdown reporting prompt."""
    headings = "\n".join(f"## {section}" for section in sections)
    return (
        "Create a concise markdown report.\n"
        f"Objective: {objective}\n\n"
        "Use the context below and produce only these sections in order.\n\n"
        f"{headings}\n\n"
        "Context:\n"
        f"{context_markdown}"
    )
