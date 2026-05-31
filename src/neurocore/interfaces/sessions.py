"""Session-memory extension interfaces for OpenBrain-style workflows."""

from __future__ import annotations

from neurocore.core.brains import apply_brain_namespace
from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.briefing import generate_briefing
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.query import query_memory
from neurocore.storage.base import BaseStore


def capture_session_event(
    request: dict[str, object], *, store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    session_id = _require_text(request.get("session_id"), "session_id")
    source_client = _require_text(request.get("source_client"), "source_client")
    content = _require_text(request.get("content") or request.get("summary"), "content")
    event_type = str(request.get("event_type") or "turn").strip().lower()
    importance = str(request.get("importance") or "normal").strip().lower()
    if not _should_store_event(
        event_type=event_type, importance=importance, request=request
    ):
        return {
            "stored": False,
            "skipped": True,
            "reason": "low-signal session event",
            "session_id": session_id,
        }

    resolved = apply_brain_namespace(
        request, store=store, default_namespace=config.default_namespace
    )
    bucket = str(request.get("bucket") or _default_session_bucket(config))
    metadata = {
        **dict(request.get("metadata") or {}),
        "brain_id": resolved.get("brain_id") or resolved["namespace"],
        "namespace": resolved["namespace"],
        "session_id": session_id,
        "source_client": source_client,
        "actor_role": str(request.get("actor_role") or "assistant"),
        "workflow_stage": str(request.get("workflow_stage") or ""),
        "importance": importance,
        "event_type": event_type,
    }
    tags = _dedupe(
        [
            *[str(tag) for tag in request.get("tags", ())],
            "artifact:session-event",
            f"session-id:{_tag_value(session_id)}",
            f"source-client:{_tag_value(source_client)}",
            f"importance:{_tag_value(importance)}",
            f"event-type:{_tag_value(event_type)}",
            *(
                [
                    f"workflow-stage:{_tag_value(str(request.get('workflow_stage') or ''))}"
                ]
                if str(request.get("workflow_stage") or "").strip()
                else []
            ),
        ]
    )
    response = capture_memory(
        {
            "namespace": resolved["namespace"],
            "bucket": bucket,
            "sensitivity": str(
                request.get("sensitivity") or config.default_sensitivity
            ),
            "content": content,
            "content_format": str(request.get("content_format") or "markdown"),
            "source_type": str(request.get("source_type") or f"session_{event_type}"),
            "title": str(
                request.get("title") or f"{source_client} {event_type} {session_id}"
            ),
            "metadata": metadata,
            "tags": tags,
            "created_at": request.get("created_at"),
        },
        store=store,
        config=config,
    )
    return {
        **response,
        "stored": True,
        "session_id": session_id,
        "brain_id": resolved.get("brain_id") or resolved["namespace"],
    }


def checkpoint_session(
    request: dict[str, object], *, store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    checkpoint_request = dict(request)
    checkpoint_request.setdefault("event_type", "checkpoint")
    checkpoint_request.setdefault("importance", "high")
    checkpoint_request.setdefault("source_type", "session_checkpoint")
    tags = list(checkpoint_request.get("tags", ()))
    tags.append("artifact:session-checkpoint")
    checkpoint_request["tags"] = tags
    return capture_session_event(checkpoint_request, store=store, config=config)


def resume_session(
    request: dict[str, object], *, store: BaseStore, config: NeuroCoreConfig
) -> dict[str, object]:
    session_id = _require_text(request.get("session_id"), "session_id")
    resolved = apply_brain_namespace(
        request, store=store, default_namespace=config.default_namespace
    )
    tags_any = list(request.get("tags_any", ()))
    tags_any.append(f"session-id:{_tag_value(session_id)}")
    query_request = {
        "brain_id": resolved.get("brain_id"),
        "namespace": resolved["namespace"],
        "query_text": str(request.get("query_text") or "session checkpoint summary"),
        "allowed_buckets": request.get("allowed_buckets") or config.allowed_buckets,
        "sensitivity_ceiling": str(
            request.get("sensitivity_ceiling") or config.default_sensitivity
        ),
        "tags_any": _dedupe(tags_any),
        "top_k": int(request.get("top_k", 6)),
        "return_mode": str(request.get("return_mode") or "hybrid"),
    }
    query_response = query_memory(query_request, store=store, config=config)
    briefing = generate_briefing(
        {
            "brain_id": resolved.get("brain_id"),
            "query_response": query_response,
            "max_items": int(request.get("max_items", 6)),
            "include_operator_hints": False,
            "sections": request.get("sections")
            or ("Overview", "Relevant Memory", "Next Actions"),
        },
        store=store,
        config=config,
    )
    return {
        "session_id": session_id,
        "brain_id": resolved.get("brain_id") or resolved["namespace"],
        "namespace": resolved["namespace"],
        "query_response": query_response,
        "briefing": briefing["briefing"],
        "metadata": briefing.get("metadata", {}),
    }


def _default_session_bucket(config: NeuroCoreConfig) -> str:
    for bucket in ("agents", "ops", "reports"):
        if bucket in config.allowed_buckets:
            return bucket
    return config.allowed_buckets[0]


def _should_store_event(
    *, event_type: str, importance: str, request: dict[str, object]
) -> bool:
    if bool(request.get("force_store", False)):
        return True
    if event_type in {"checkpoint", "summary"}:
        return True
    return importance in {"high", "critical"}


def _tag_value(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "-" for char in value.lower()
    ).strip("-")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text and text not in seen:
            ordered.append(text)
            seen.add(text)
    return ordered


def _require_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text
