"""Briefing interface for turning NeuroCore memory into compact markdown context."""

from __future__ import annotations

from collections import Counter

from neurocore.core.brains import apply_brain_namespace
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.query import query_memory
from neurocore.storage.base import BaseStore

DEFAULT_BRIEFING_SECTIONS = (
    "Overview",
    "Relevant Memory",
    "Prior Decisions / Payloads",
    "Next Actions",
)


def generate_briefing(
    request: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None = None,
    summarizer: object | None = None,
) -> dict[str, object]:
    """Generate a compact briefing from explicit or queried NeuroCore context."""
    max_items = _parse_max_items(request.get("max_items", 5))
    include_operator_hints = bool(request.get("include_operator_hints", False))
    sections = _parse_sections(
        request.get("sections"),
        include_operator_hints=include_operator_hints,
    )
    brain_id = _optional_string(request.get("brain_id"))
    context_source, context_markdown, query_id, query_response = _resolve_context(
        request,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
        brain_id=brain_id,
        max_items=max_items,
    )
    if include_operator_hints and isinstance(request.get("query_request"), dict):
        query_response = _augment_with_operator_hints(
            query_response,
            query_request=dict(request["query_request"]),
            brain_id=brain_id,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        )
    briefing = _synthesize_briefing(
        context_markdown=context_markdown,
        query_response=query_response,
        sections=sections,
        max_items=max_items,
        include_operator_hints=include_operator_hints,
        summarizer=summarizer,
    )
    metadata: dict[str, object] = {"context_source": context_source}
    if query_id:
        metadata["query_id"] = query_id
    if brain_id:
        metadata["brain_id"] = brain_id
    return {
        "briefing": briefing,
        "context_markdown": context_markdown,
        "metadata": metadata,
    }


def _resolve_context(
    request: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
    brain_id: str | None,
    max_items: int,
) -> tuple[str, str, str | None, dict[str, object] | None]:
    raw_context = request.get("context_markdown")
    if isinstance(raw_context, str) and raw_context.strip():
        return "context_markdown", raw_context.strip(), None, None

    raw_query_response = request.get("query_response")
    if isinstance(raw_query_response, dict):
        return (
            "query_response",
            _build_context_markdown(raw_query_response, max_items=max_items),
            _optional_string(raw_query_response.get("query_id")),
            raw_query_response,
        )

    raw_query_request = request.get("query_request")
    if isinstance(raw_query_request, dict):
        query_request = apply_brain_namespace(
            {
                **dict(raw_query_request),
                **({"brain_id": brain_id} if brain_id else {}),
            },
            store=store,
            default_namespace=config.default_namespace,
        )
        query_response = query_memory(
            query_request,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        )
        return (
            "query_request",
            _build_context_markdown(query_response, max_items=max_items),
            _optional_string(query_response.get("query_id")),
            query_response,
        )

    raise ValueError(
        "request must include context_markdown, query_response, or query_request"
    )


def _build_context_markdown(
    query_response: dict[str, object], *, max_items: int
) -> str:
    results = query_response.get("results", [])
    if not isinstance(results, list) or not results:
        return "No matching memory found."
    lines = ["## Retrieved Memory Context"]
    for result in results[:max_items]:
        if not isinstance(result, dict):
            continue
        metadata = result.get("metadata", {})
        title = ""
        if isinstance(metadata, dict):
            title = str(metadata.get("title") or "")
        label = title or str(result.get("id", "unknown"))
        bucket = str(result.get("bucket", "unknown"))
        preview = str(result.get("content_preview") or "").strip()
        lines.append(f"- {label} [{bucket}]")
        if preview:
            lines.append(f"  {preview}")
    return "\n".join(lines)


def _augment_with_operator_hints(
    query_response: dict[str, object] | None,
    *,
    query_request: dict[str, object],
    brain_id: str | None,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
) -> dict[str, object] | None:
    if not isinstance(query_response, dict):
        return query_response
    operator_query = apply_brain_namespace(
        {
            **dict(query_request),
            **({"brain_id": brain_id} if brain_id else {}),
        },
        store=store,
        default_namespace=config.default_namespace,
    )
    operator_query["query_text"] = ""
    existing_tags_any = operator_query.get("tags_any")
    tags_any: list[str] = []
    if isinstance(existing_tags_any, (list, tuple)):
        tags_any = [str(tag) for tag in existing_tags_any if str(tag).strip()]
    operator_tags = ["artifact:operator-retrospective", "operator-retrospective"]
    for tag in operator_tags:
        if tag not in tags_any:
            tags_any.append(tag)
    operator_query["tags_any"] = tags_any
    operator_query["top_k"] = max(int(operator_query.get("top_k", 2)), 2)
    operator_results = query_memory(
        operator_query,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
    ).get("results", [])
    if not isinstance(operator_results, list) or not operator_results:
        return query_response
    merged_results = list(query_response.get("results", []))
    seen_ids = {
        str(result.get("id"))
        for result in merged_results
        if isinstance(result, dict) and result.get("id")
    }
    for result in operator_results:
        if not isinstance(result, dict):
            continue
        result_id = str(result.get("id") or "")
        if result_id and result_id in seen_ids:
            continue
        merged_results.append(result)
        if result_id:
            seen_ids.add(result_id)
    merged = dict(query_response)
    merged["results"] = merged_results
    return merged


def _synthesize_briefing(
    *,
    context_markdown: str,
    query_response: dict[str, object] | None,
    sections: tuple[str, ...],
    max_items: int,
    include_operator_hints: bool,
    summarizer: object | None,
) -> str:
    results = _coerce_results(query_response, max_items=max_items)
    section_map = {
        "Overview": _overview_section(
            context_markdown=context_markdown,
            results=results,
            summarizer=summarizer,
        ),
        "Relevant Memory": _relevant_memory_section(results),
        "Prior Decisions / Payloads": _prior_decisions_section(results),
        "Operator Hints": _operator_hints_section(results),
        "Next Actions": _next_actions_section(
            results, context_markdown=context_markdown
        ),
    }
    lines: list[str] = []
    for section in sections:
        if section == "Operator Hints" and not include_operator_hints:
            continue
        content = section_map.get(section, "").strip()
        if not content:
            continue
        lines.append(f"## {section}")
        lines.append(content)
    return "\n\n".join(lines).strip()


def _coerce_results(
    query_response: dict[str, object] | None, *, max_items: int
) -> list[dict[str, object]]:
    if not isinstance(query_response, dict):
        return []
    results = query_response.get("results", [])
    if not isinstance(results, list):
        return []
    return [result for result in results[:max_items] if isinstance(result, dict)]


def _overview_section(
    *,
    context_markdown: str,
    results: list[dict[str, object]],
    summarizer: object | None,
) -> str:
    if summarizer is not None and context_markdown.strip():
        try:
            summary = summarizer.summarize(context_markdown, max_sentences=2)
            summary_text = str(getattr(summary, "summary", "")).strip()
            if summary_text:
                return summary_text
        except Exception:
            pass

    if not results:
        return context_markdown.strip() or "No relevant durable memory was found."
    buckets = Counter(str(result.get("bucket", "unknown")) for result in results)
    most_common = ", ".join(
        f"{bucket} ({count})" for bucket, count in buckets.most_common(3)
    )
    return (
        f"Retrieved {len(results)} durable memory item(s). "
        f"Most relevant buckets: {most_common or 'unknown'}."
    )


def _relevant_memory_section(results: list[dict[str, object]]) -> str:
    if not results:
        return "- No relevant memory matched the request."
    lines: list[str] = []
    for result in results:
        metadata = result.get("metadata", {})
        title = ""
        tags: list[str] = []
        if isinstance(metadata, dict):
            title = str(metadata.get("title") or "")
            raw_tags = metadata.get("tags", [])
            if isinstance(raw_tags, list):
                tags = [str(tag) for tag in raw_tags]
        label = title or str(result.get("id", "unknown"))
        preview = str(result.get("content_preview") or "").strip() or "No preview."
        bucket = str(result.get("bucket", "unknown"))
        tag_suffix = f" tags={', '.join(tags)}" if tags else ""
        lines.append(f"- {label} [{bucket}]{tag_suffix}: {preview}")
    return "\n".join(lines)


def _prior_decisions_section(results: list[dict[str, object]]) -> str:
    decisions = [
        result
        for result in results
        if _matches_any_marker(
            result,
            markers=(
                "payload",
                "validated",
                "finding",
                "report",
                "decision",
                "retrospective",
            ),
            buckets=("payloads", "findings", "reports", "ops"),
        )
    ]
    if not decisions:
        return "- No prior decisions or reusable payload memory matched."
    lines = []
    for result in decisions[:4]:
        label = _result_label(result)
        preview = str(result.get("content_preview") or "").strip() or "No preview."
        lines.append(f"- {label}: {preview}")
    return "\n".join(lines)


def _operator_hints_section(results: list[dict[str, object]]) -> str:
    hints = [
        result
        for result in results
        if _matches_any_marker(
            result,
            markers=(
                "operator-retrospective",
                "operator",
                "retrospective",
                "preferred",
            ),
            buckets=("ops",),
        )
    ]
    if not hints:
        return "- No operator-specific retrospective memory matched."
    return "\n".join(
        f"- {_result_label(result)}: {str(result.get('content_preview') or '').strip() or 'No preview.'}"
        for result in hints[:3]
    )


def _next_actions_section(
    results: list[dict[str, object]], *, context_markdown: str
) -> str:
    if not results:
        return "- Capture new durable findings or operator notes for this workflow."
    priorities = []
    if any(str(result.get("bucket")) == "findings" for result in results):
        priorities.append(
            "- Validate and preserve the highest-signal findings in the current workflow."
        )
    if any(str(result.get("bucket")) == "payloads" for result in results):
        priorities.append(
            "- Re-test reusable payloads before generating new exploit variants."
        )
    if any(
        _matches_any_marker(
            result, markers=("operator-retrospective",), buckets=("ops",)
        )
        for result in results
    ):
        priorities.append(
            "- Apply prior operator preferences when choosing format, pacing, and next steps."
        )
    if not priorities:
        priorities.append(
            "- Reuse the referenced durable memory before exploring from scratch."
        )
    if context_markdown.strip():
        priorities.append(
            "- Export new validated decisions back into NeuroCore to keep the briefing current."
        )
    return "\n".join(priorities[:3])


def _matches_any_marker(
    result: dict[str, object], *, markers: tuple[str, ...], buckets: tuple[str, ...]
) -> bool:
    bucket = str(result.get("bucket", "")).lower()
    if bucket in buckets:
        return True
    metadata = result.get("metadata", {})
    values: list[str] = [bucket]
    if isinstance(metadata, dict):
        values.extend(
            str(metadata.get(key, "")).lower()
            for key in ("source_type", "title", "artifact_type")
        )
        raw_tags = metadata.get("tags", [])
        if isinstance(raw_tags, list):
            values.extend(str(tag).lower() for tag in raw_tags)
    haystack = " ".join(values)
    return any(marker in haystack for marker in markers)


def _result_label(result: dict[str, object]) -> str:
    metadata = result.get("metadata", {})
    if isinstance(metadata, dict):
        title = str(metadata.get("title") or "").strip()
        if title:
            return title
    return str(result.get("id", "unknown"))


def _parse_sections(raw: object, *, include_operator_hints: bool) -> tuple[str, ...]:
    if raw is None:
        sections = list(DEFAULT_BRIEFING_SECTIONS)
        if include_operator_hints:
            sections.insert(3, "Operator Hints")
        return tuple(sections)
    if not isinstance(raw, (list, tuple)):
        raise ValueError("sections must be a list of strings")
    sections = tuple(str(section).strip() for section in raw if str(section).strip())
    if not sections:
        raise ValueError("sections must include at least one heading")
    return sections


def _parse_max_items(raw: object) -> int:
    if isinstance(raw, bool):
        raise ValueError("max_items must be an integer")
    try:
        max_items = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_items must be an integer") from exc
    if max_items < 1:
        raise ValueError("max_items must be >= 1")
    return max_items


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
