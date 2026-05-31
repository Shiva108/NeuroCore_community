"""Shared deduplication helpers used across NeuroCore layers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DedupIndex:
    _entries: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def register(
        self, namespace: str, fingerprint: str, item_id: str, signature: str
    ) -> None:
        self._entries[(namespace, fingerprint, signature)] = item_id

    def lookup(self, namespace: str, fingerprint: str, signature: str) -> str | None:
        return self._entries.get((namespace, fingerprint, signature))
