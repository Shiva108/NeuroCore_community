"""Shared content normalization helpers used across NeuroCore layers."""

from __future__ import annotations

import hashlib
import re
import uuid

WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_content(content: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", content.strip())


def compute_content_fingerprint(content: str) -> str:
    normalized = normalize_content(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def count_tokens(content: str) -> int:
    normalized = normalize_content(content)
    if not normalized:
        return 0
    return len(normalized.split(" "))


def generate_stable_id(prefix: str, *parts: str) -> str:
    joined = "::".join(parts)
    value = uuid.uuid5(uuid.NAMESPACE_URL, joined)
    return f"{prefix}-{value}"
