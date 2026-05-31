"""Retrieval query engine for NeuroCore."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from neurocore.core.config import NeuroCoreConfig
from neurocore.core.models import MemoryChunk, MemoryRecord, QueryContext
from neurocore.core.policies import SENSITIVITY_ORDER, enforce_sensitivity_ceiling
from neurocore.retrieval.rankers import SemanticRanker, SentenceTransformersRanker
from neurocore.storage.base import BaseStore, Candidate


@dataclass
class QueryDiagnostics:
    candidate_counts: dict[str, int] = field(
        default_factory=lambda: {
            "store": 0,
            "policy_allowed": 0,
            "return_mode_allowed": 0,
            "semantic_scored": 0,
            "metadata_matched": 0,
            "ranked": 0,
            "selected_pre_aggregate": 0,
            "selected_post_aggregate": 0,
        }
    )
    filter_counts: dict[str, int] = field(
        default_factory=lambda: {
            "excluded_sealed": 0,
            "excluded_sensitivity": 0,
            "excluded_source_type": 0,
            "excluded_tags_any": 0,
            "excluded_tags_all": 0,
            "excluded_time_range": 0,
            "excluded_return_mode": 0,
        }
    )
    selected_kinds: dict[str, int] = field(
        default_factory=lambda: {
            "record": 0,
            "chunk": 0,
            "document": 0,
        }
    )


@dataclass
class QueryEngine:
    store: BaseStore
    semantic_ranker: SemanticRanker | None = None

    def execute(
        self, request: dict[str, object], config: NeuroCoreConfig
    ) -> dict[str, object]:
        context = QueryContext(
            namespace=str(request.get("namespace") or config.default_namespace),
            allowed_buckets=tuple(
                request.get("allowed_buckets") or config.allowed_buckets
            ),
            sensitivity_ceiling=str(
                request.get("sensitivity_ceiling") or config.default_sensitivity
            ),
            tags_any=tuple(request.get("tags_any", ())),
            tags_all=tuple(request.get("tags_all", ())),
            source_types=tuple(request.get("source_types", ())),
            time_range=_parse_time_range(request.get("time_range")),
            include_archived=bool(request.get("include_archived", False)),
        )
        query_text = str(request.get("query_text", "")).strip()
        top_k = int(request.get("top_k", config.default_top_k))
        return_mode = str(request.get("return_mode", "hybrid"))
        include_diagnostics = bool(request.get("include_diagnostics", False))
        diagnostics = QueryDiagnostics()

        warnings: list[str] = []
        ranker = self.semantic_ranker
        if ranker is None:
            try:
                ranker = _build_ranker_from_config(config)
            except RuntimeError as exc:
                warnings.append(
                    f"Semantic ranker unavailable; using metadata-only fallback. ({exc})"
                )
                ranker = None
        if ranker is None:
            if not warnings:
                warnings.append(
                    "Semantic ranker unavailable; using metadata-only fallback."
                )

        store_candidates = self.store.iter_candidates(
            namespace=context.namespace,
            allowed_buckets=context.allowed_buckets,
            include_archived=context.include_archived,
        )
        diagnostics.candidate_counts["store"] = len(store_candidates)
        filtered_candidates: list[Candidate] = []
        for candidate in store_candidates:
            allowed, reason = _candidate_allowed(candidate, context)
            if allowed:
                filtered_candidates.append(candidate)
                continue
            if reason is not None:
                diagnostics.filter_counts[reason] += 1
        diagnostics.candidate_counts["policy_allowed"] = len(filtered_candidates)

        filtered_candidates, excluded_return_mode = _apply_return_mode_filter(
            filtered_candidates, return_mode
        )
        diagnostics.candidate_counts["return_mode_allowed"] = len(filtered_candidates)
        diagnostics.filter_counts["excluded_return_mode"] = excluded_return_mode
        semantic_scores = (
            ranker.rank(query_text, filtered_candidates)
            if ranker is not None and query_text
            else {}
        )
        diagnostics.candidate_counts["semantic_scored"] = (
            len(filtered_candidates) if ranker is not None and query_text else 0
        )

        ranked: list[tuple[float, Candidate]] = []
        for candidate in filtered_candidates:
            semantic_score = semantic_scores.get(candidate.item.id, 0.0)
            metadata_score = _metadata_score(query_text, candidate)
            if metadata_score > 0:
                diagnostics.candidate_counts["metadata_matched"] += 1
            score = _score_candidate(
                query_text=query_text,
                candidate=candidate,
                semantic_score=semantic_score,
                metadata_score=metadata_score,
                use_semantic=ranker is not None,
            )
            if score <= 0 and query_text and ranker is None:
                continue
            if score <= 0 and query_text and ranker is not None and semantic_score <= 0:
                continue
            ranked.append((score, candidate))

        ranked.sort(key=lambda item: (item[0], _candidate_id(item[1])), reverse=True)
        diagnostics.candidate_counts["ranked"] = len(ranked)
        selected = ranked[:top_k]
        diagnostics.candidate_counts["selected_pre_aggregate"] = len(selected)

        if return_mode == "document_aggregate":
            selected = _aggregate_documents(selected)
        diagnostics.candidate_counts["selected_post_aggregate"] = len(selected)
        for _score, candidate in selected:
            diagnostics.selected_kinds[candidate.kind] = (
                diagnostics.selected_kinds.get(candidate.kind, 0) + 1
            )

        matched_signals = (
            ["semantic", "metadata"] if ranker is not None else ["metadata"]
        )
        matched_by = "hybrid" if ranker is not None else "metadata"
        response = {
            "query_id": f"query-{uuid4()}",
            "results": [
                _serialize_candidate(
                    candidate, score, context, matched_signals, matched_by
                )
                for score, candidate in selected
            ],
            "truncated": len(ranked) > top_k,
            "warnings": warnings,
        }
        if include_diagnostics:
            response["diagnostics"] = {
                "semantic_mode": "hybrid" if ranker is not None else "metadata-only",
                "return_mode": return_mode,
                "candidate_counts": diagnostics.candidate_counts,
                "filter_counts": diagnostics.filter_counts,
                "selected_kinds": diagnostics.selected_kinds,
                "ranker": {
                    "configured_backend": config.semantic_backend,
                    "active": ranker is not None,
                    "warning_emitted": bool(warnings),
                },
            }
        return response


def _apply_return_mode_filter(
    candidates: list[Candidate], return_mode: str
) -> tuple[list[Candidate], int]:
    if return_mode == "record_only":
        filtered = [candidate for candidate in candidates if candidate.kind == "record"]
        return filtered, len(candidates) - len(filtered)
    if return_mode == "chunk_only":
        filtered = [candidate for candidate in candidates if candidate.kind == "chunk"]
        return filtered, len(candidates) - len(filtered)
    return candidates, 0


def _build_ranker_from_config(config: NeuroCoreConfig) -> SemanticRanker | None:
    if config.semantic_backend == "sentence-transformers":
        return SentenceTransformersRanker(config.semantic_model_name)
    return None


def _parse_time_range(value: object) -> tuple[datetime | None, datetime | None] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("time_range must contain start and end timestamps")
    start, end = value
    return (_coerce_datetime(start), _coerce_datetime(end))


def _coerce_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("time_range values must be datetimes or ISO strings")


def _candidate_allowed(
    candidate: Candidate, context: QueryContext
) -> tuple[bool, str | None]:
    sensitivity = candidate.artifact.sensitivity
    if sensitivity == "sealed":
        return False, "excluded_sealed"

    try:
        enforce_sensitivity_ceiling(sensitivity, context.sensitivity_ceiling)
    except PermissionError:
        return False, "excluded_sensitivity"

    source_type = _candidate_source_type(candidate)
    if context.source_types and source_type not in context.source_types:
        return False, "excluded_source_type"

    candidate_tags = set(_candidate_tags(candidate))
    if context.tags_any and not candidate_tags.intersection(context.tags_any):
        return False, "excluded_tags_any"
    if context.tags_all and not set(context.tags_all).issubset(candidate_tags):
        return False, "excluded_tags_all"

    if context.time_range is not None:
        start, end = context.time_range
        timestamp = _candidate_timestamp(candidate)
        if start is not None and timestamp < start:
            return False, "excluded_time_range"
        if end is not None and timestamp > end:
            return False, "excluded_time_range"

    return True, None


def _candidate_timestamp(candidate: Candidate) -> datetime:
    return candidate.artifact.created_at


def _score_candidate(
    query_text: str,
    candidate: Candidate,
    semantic_score: float,
    metadata_score: float,
    use_semantic: bool,
) -> float:
    if use_semantic:
        return (semantic_score * 100.0) + metadata_score
    return metadata_score


def _metadata_score(query_text: str, candidate: Candidate) -> float:
    if not query_text:
        return 1.0
    query_terms = {term.lower() for term in query_text.split()}
    content_terms = {
        term.lower() for term in candidate.artifact.normalized_text.split()
    }
    overlap = query_terms & content_terms
    if not overlap:
        return 0.0
    sensitivity_bonus = 0.01 * (2 - SENSITIVITY_ORDER[candidate.artifact.sensitivity])
    return float(len(overlap)) + sensitivity_bonus


def _candidate_id(candidate: Candidate) -> str:
    return candidate.item.id


def _candidate_source_type(candidate: Candidate) -> str:
    return candidate.artifact.source_type


def _candidate_text(candidate: Candidate) -> str:
    if candidate.artifact.normalized_text:
        return candidate.artifact.normalized_text
    if isinstance(candidate.item, MemoryRecord):
        return candidate.item.content
    if isinstance(candidate.item, MemoryChunk):
        return candidate.item.chunk_text
    if (
        hasattr(candidate.item, "raw_content")
        and candidate.item.raw_content is not None
    ):
        return candidate.item.raw_content
    return ""


def _candidate_tags(candidate: Candidate) -> tuple[str, ...]:
    return candidate.artifact.tags


def _aggregate_documents(
    ranked_candidates: list[tuple[float, Candidate]],
) -> list[tuple[float, Candidate]]:
    seen_documents: set[str] = set()
    aggregated: list[tuple[float, Candidate]] = []
    for score, candidate in ranked_candidates:
        if candidate.kind != "chunk" or candidate.document is None:
            aggregated.append((score, candidate))
            continue
        if candidate.document.id in seen_documents:
            continue
        seen_documents.add(candidate.document.id)
        aggregated.append(
            (
                score,
                Candidate(
                    kind="document",
                    item=candidate.document,
                    artifact=candidate.artifact,
                    document=candidate.document,
                ),
            )
        )
    return aggregated


def _serialize_candidate(
    candidate: Candidate,
    score: float,
    context: QueryContext,
    matched_signals: list[str],
    matched_by: str,
) -> dict[str, object]:
    item = candidate.item
    document_id = (
        candidate.document.id
        if candidate.kind == "chunk" and candidate.document
        else None
    )
    metadata = dict(item.metadata)
    if getattr(item, "title", None):
        metadata.setdefault("title", str(item.title))
    metadata["source_type"] = _candidate_source_type(candidate)
    metadata["tags"] = list(_candidate_tags(candidate))
    return {
        "id": item.id,
        "kind": candidate.kind,
        "document_id": document_id,
        "namespace": item.namespace,
        "bucket": item.bucket,
        "score": score,
        "matched_by": matched_by,
        "explanation": {
            "matched_signals": matched_signals,
            "filters_applied": {
                "namespace": context.namespace,
                "buckets": list(context.allowed_buckets),
                "sensitivity_ceiling": context.sensitivity_ceiling,
            },
        },
        "title": str(getattr(item, "title", "") or ""),
        "content_preview": _candidate_text(candidate)[:160],
        "metadata": metadata,
    }
