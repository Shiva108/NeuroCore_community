"""Deterministic retrieval evaluation harness for repo-local diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.query import query_memory
from neurocore.retrieval.rankers import FakeSemanticRanker
from neurocore.storage.in_memory import InMemoryStore


def load_eval_fixture(path: str | Path) -> dict[str, object]:
    fixture_path = Path(path)
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def generate_eval_snapshot(path: str | Path) -> dict[str, object]:
    fixture = load_eval_fixture(path)
    config = _config_from_fixture(dict(fixture.get("config") or {}))
    store = InMemoryStore()
    aliases = _seed_fixture_data(
        store=store,
        config=config,
        captures=list(fixture.get("captures") or []),
    )
    queries = []
    for query_case in list(fixture.get("queries") or []):
        if not isinstance(query_case, dict):
            continue
        request = dict(query_case.get("request") or {})
        request["include_diagnostics"] = True
        ranker = _ranker_from_fixture_case(query_case, aliases)
        response = query_memory(
            request,
            store=store,
            config=config,
            semantic_ranker=ranker,
        )
        queries.append(
            {
                "name": str(query_case.get("name") or ""),
                "request": _sanitized_request(request),
                "result_ids": [str(item["id"]) for item in response["results"]],
                "matched_by": [
                    str(item.get("matched_by") or "") for item in response["results"]
                ],
                "warnings": list(response.get("warnings") or []),
                "diagnostics": dict(response.get("diagnostics") or {}),
            }
        )
    return {
        "fixture": str(fixture.get("name") or Path(path).stem),
        "config": _sanitized_config(config),
        "queries": queries,
    }


def validate_eval_snapshot(
    path: str | Path,
    snapshot: dict[str, object],
) -> list[str]:
    fixture = load_eval_fixture(path)
    config = _config_from_fixture(dict(fixture.get("config") or {}))
    store = InMemoryStore()
    aliases = _seed_fixture_data(
        store=store,
        config=config,
        captures=list(fixture.get("captures") or []),
    )
    observed_queries = {
        str(query.get("name") or ""): query
        for query in list(snapshot.get("queries") or [])
    }
    errors: list[str] = []
    for query_case in list(fixture.get("queries") or []):
        if not isinstance(query_case, dict):
            continue
        name = str(query_case.get("name") or "")
        observed = observed_queries.get(name)
        if observed is None:
            errors.append(f"{name}: missing snapshot entry")
            continue
        expected_ids = _resolve_aliases(
            list(query_case.get("expected_result_ids") or []),
            aliases,
        )
        if expected_ids and list(observed.get("result_ids") or []) != expected_ids:
            errors.append(
                f"{name}: expected result_ids {expected_ids}, got {observed.get('result_ids')}"
            )
        expected_diagnostics = dict(query_case.get("expected_diagnostics") or {})
        observed_diagnostics = dict(observed.get("diagnostics") or {})
        for key, expected_value in expected_diagnostics.items():
            observed_value = observed_diagnostics.get(key)
            if observed_value != expected_value:
                errors.append(
                    f"{name}: expected diagnostics[{key}]={expected_value!r}, got {observed_value!r}"
                )
    return errors


def _seed_fixture_data(
    *,
    store: InMemoryStore,
    config: NeuroCoreConfig,
    captures: list[object],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for entry in captures:
        if not isinstance(entry, dict):
            continue
        request = dict(entry.get("request") or {})
        response = capture_memory(request, store=store, config=config)
        alias = str(entry.get("alias") or "").strip()
        if alias:
            aliases[alias] = str(response["id"])
            if response["kind"] == "document":
                for ordinal, chunk_id in enumerate(
                    store.get_document_chunk_ids(str(response["id"])),
                    start=1,
                ):
                    aliases[f"{alias}#{ordinal}"] = chunk_id
        archive_reason = str(entry.get("archive_reason") or "").strip()
        if archive_reason:
            store.soft_delete(str(response["id"]), reason=archive_reason)
    return aliases


def _ranker_from_fixture_case(
    query_case: dict[str, object],
    aliases: dict[str, str],
) -> FakeSemanticRanker | None:
    raw_scores = dict(query_case.get("semantic_scores") or {})
    if not raw_scores:
        return None
    scores = {
        aliases.get(str(alias), str(alias)): float(score)
        for alias, score in raw_scores.items()
    }
    return FakeSemanticRanker(scores=scores)


def _resolve_aliases(values: list[object], aliases: dict[str, str]) -> list[str]:
    resolved = []
    for value in values:
        key = str(value)
        resolved.append(aliases.get(key, key))
    return resolved


def _config_from_fixture(payload: dict[str, object]) -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace=str(payload["default_namespace"]),
        allowed_buckets=tuple(payload["allowed_buckets"]),
        default_sensitivity=str(payload["default_sensitivity"]),
        max_atomic_tokens=int(payload.get("max_atomic_tokens", 350)),
        target_chunk_tokens=int(payload.get("target_chunk_tokens", 600)),
        max_chunk_tokens=int(payload.get("max_chunk_tokens", 900)),
        chunk_overlap_tokens=int(payload.get("chunk_overlap_tokens", 75)),
        semantic_backend=str(payload.get("semantic_backend", "none")),
        semantic_model_name=str(
            payload.get("semantic_model_name", "sentence-transformers/all-MiniLM-L6-v2")
        ),
    )


def _sanitized_config(config: NeuroCoreConfig) -> dict[str, object]:
    return {
        "default_namespace": config.default_namespace,
        "allowed_buckets": list(config.allowed_buckets),
        "default_sensitivity": config.default_sensitivity,
        "max_atomic_tokens": config.max_atomic_tokens,
        "target_chunk_tokens": config.target_chunk_tokens,
        "max_chunk_tokens": config.max_chunk_tokens,
        "chunk_overlap_tokens": config.chunk_overlap_tokens,
        "semantic_backend": config.semantic_backend,
        "semantic_model_name": config.semantic_model_name,
    }


def _sanitized_request(request: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in request.items()
        if key not in {"metadata", "context_markdown"}
    }
