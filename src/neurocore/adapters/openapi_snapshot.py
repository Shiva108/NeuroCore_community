"""Helpers for building a stable checked-in HTTP OpenAPI snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neurocore.adapters.http_api import create_app
from neurocore.core.config import NeuroCoreConfig
from neurocore.storage.in_memory import InMemoryStore

OPENAPI_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[3] / "schemas" / "neurocore-http-openapi.json"
)
_SORTED_LIST_KEYS = {"enum", "required"}


def build_openapi_snapshot() -> dict[str, object]:
    """Build a stable OpenAPI snapshot for the HTTP adapter."""
    app = create_app(store=InMemoryStore(), config=_snapshot_config())
    return _normalize_value(app.openapi())


def write_openapi_snapshot() -> Path:
    """Write the current OpenAPI snapshot to the checked-in schema path."""
    OPENAPI_SNAPSHOT_PATH.write_text(
        json.dumps(build_openapi_snapshot(), indent=2) + "\n",
        encoding="utf-8",
    )
    return OPENAPI_SNAPSHOT_PATH


def _snapshot_config() -> NeuroCoreConfig:
    return NeuroCoreConfig(
        default_namespace="project-alpha",
        allowed_buckets=("research", "ops", "reports"),
        default_sensitivity="standard",
        enable_admin_surface=True,
        enable_dashboard=True,
        enable_background_summarization=True,
        enable_multi_model_consensus=True,
        production_backend_provider="supabase",
        production_database_url="postgresql://primary",
        production_sealed_database_url="postgresql://sealed",
    )


def _normalize_value(value: Any, *, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_value(value[key], parent_key=key) for key in sorted(value)
        }
    if isinstance(value, list):
        normalized = [_normalize_value(item, parent_key=parent_key) for item in value]
        if parent_key in _SORTED_LIST_KEYS and all(
            isinstance(item, str) for item in normalized
        ):
            return sorted(normalized)
        return normalized
    return value
