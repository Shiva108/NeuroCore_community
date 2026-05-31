"""Compatibility wrappers for shared ingest-profile helpers."""

from __future__ import annotations

from neurocore.core.ingest_profiles import (
    resolve_ingest_profile,
    validate_ingest_profiles,
)

__all__ = ["resolve_ingest_profile", "validate_ingest_profiles"]
