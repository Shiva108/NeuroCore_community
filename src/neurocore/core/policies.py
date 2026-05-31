"""Policy validation helpers for NeuroCore."""

from __future__ import annotations

import re

from neurocore.core.config import VALID_SENSITIVITIES

NAMESPACE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SENSITIVITY_ORDER = {"standard": 0, "restricted": 1, "sealed": 2}


def validate_namespace(namespace: str) -> str:
    value = namespace.strip()
    if not value or not NAMESPACE_PATTERN.match(value):
        raise ValueError("Invalid namespace")
    return value


def validate_bucket(bucket: str, allowed_buckets: tuple[str, ...] | None = None) -> str:
    value = bucket.strip()
    if not value or not BUCKET_PATTERN.match(value):
        raise ValueError("Invalid bucket")
    if allowed_buckets is not None and value not in allowed_buckets:
        raise ValueError("Bucket is not allowed by configuration")
    return value


def validate_sensitivity(sensitivity: str) -> str:
    value = sensitivity.strip().lower()
    if value not in VALID_SENSITIVITIES:
        raise ValueError("Invalid sensitivity")
    return value


def enforce_sensitivity_ceiling(content_sensitivity: str, ceiling: str) -> None:
    item_level = SENSITIVITY_ORDER[validate_sensitivity(content_sensitivity)]
    ceiling_level = SENSITIVITY_ORDER[validate_sensitivity(ceiling)]
    if item_level > ceiling_level:
        raise PermissionError("Content sensitivity exceeds allowed ceiling")
