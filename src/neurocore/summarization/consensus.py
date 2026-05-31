"""Consensus summarization strategies and external model clients."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol
from urllib import error, request as urllib_request

from neurocore.ingest.normalize import normalize_content


def split_sentences(text: str) -> list[str]:
    """Split normalized text into simple sentence-like segments."""
    normalized = normalize_content(text)
    if not normalized:
        return []
    sentences = [
        sentence.strip()
        for sentence in normalized.replace("! ", ". ").replace("? ", ". ").split(". ")
        if sentence.strip()
    ]
    return [
        sentence if sentence.endswith(".") else f"{sentence}." for sentence in sentences
    ]


@dataclass(frozen=True)
class ConsensusSummary:
    """Structured summary payload with strategy output metadata."""

    summary: str
    strategy_outputs: dict[str, str]
    agreement_score: float

    def to_dict(self) -> dict[str, object]:
        """Serialize the summary for API responses."""
        return {
            "summary": self.summary,
            "strategy_outputs": self.strategy_outputs,
            "agreement_score": self.agreement_score,
        }


class ExternalSummaryModelClient(Protocol):
    """Protocol for multi-model summary backends."""

    def summarize(self, *, model_name: str, text: str, max_sentences: int = 2) -> str:
        """Return a summary produced by an external model."""


class ConsensusSummarizer:
    """Deterministic multi-strategy summarizer for local use."""

    def summarize(self, text: str, max_sentences: int = 2) -> ConsensusSummary:
        sentences = split_sentences(text)
        if not sentences:
            return ConsensusSummary(
                summary="", strategy_outputs={}, agreement_score=1.0
            )

        outputs = {
            "lead": self._lead(sentences, max_sentences=max_sentences),
            "coverage": self._coverage(sentences, max_sentences=max_sentences),
            "balanced": self._balanced(sentences, max_sentences=max_sentences),
        }
        selected = max(
            outputs.values(),
            key=lambda item: (self._agreement(item, outputs), len(item)),
        )
        return ConsensusSummary(
            summary=selected,
            strategy_outputs=outputs,
            agreement_score=self._agreement(selected, outputs),
        )

    def _lead(self, sentences: list[str], max_sentences: int) -> str:
        return " ".join(sentences[:max_sentences])

    def _coverage(self, sentences: list[str], max_sentences: int) -> str:
        scored = sorted(
            sentences,
            key=lambda sentence: (len(set(sentence.lower().split())), len(sentence)),
            reverse=True,
        )
        return " ".join(scored[:max_sentences])

    def _balanced(self, sentences: list[str], max_sentences: int) -> str:
        if len(sentences) <= max_sentences:
            return " ".join(sentences)
        picks = [sentences[0], sentences[-1]]
        return " ".join(picks[:max_sentences])

    def _agreement(self, candidate: str, outputs: dict[str, str]) -> float:
        candidate_terms = set(candidate.lower().split())
        if not candidate_terms:
            return 1.0
        overlaps = []
        for output in outputs.values():
            output_terms = set(output.lower().split())
            overlaps.append(
                len(candidate_terms & output_terms) / max(len(candidate_terms), 1)
            )
        return round(sum(overlaps) / len(overlaps), 2)


@dataclass(frozen=True)
class MultiModelConsensusSummarizer:
    """Consensus summarizer that delegates to multiple external models."""

    model_client: ExternalSummaryModelClient
    model_names: tuple[str, ...]

    def summarize(self, text: str, max_sentences: int = 2) -> ConsensusSummary:
        if len(self.model_names) < 2:
            raise ValueError("Multi-model consensus requires at least two model names")
        if len(set(self.model_names)) != len(self.model_names):
            raise ValueError("Multi-model consensus requires unique model names")
        outputs = {
            model_name: self.model_client.summarize(
                model_name=model_name,
                text=text,
                max_sentences=max_sentences,
            )
            for model_name in self.model_names
        }
        selected = max(
            outputs.values(),
            key=lambda item: (_agreement(item, outputs), len(item)),
        )
        return ConsensusSummary(
            summary=selected,
            strategy_outputs=outputs,
            agreement_score=_agreement(selected, outputs),
        )


@dataclass(frozen=True)
class OpenAICompatibleSummaryClient:
    """Minimal client for OpenAI-compatible chat completion APIs."""

    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 30.0

    def summarize(self, *, model_name: str, text: str, max_sentences: int = 2) -> str:
        prompt = (
            "Summarize the following text in at most "
            f"{max_sentences} sentences. Focus on the most important facts.\n\n{text}"
        )
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib_request.Request(
            url=f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:  # pragma: no cover - network path
            raise RuntimeError(
                f"Failed to call external summary model {model_name}"
            ) from exc

        try:
            return str(body["choices"][0]["message"]["content"]).strip()
        except (
            KeyError,
            IndexError,
            TypeError,
        ) as exc:  # pragma: no cover - malformed remote response
            raise RuntimeError(
                f"Invalid response from external summary model {model_name}"
            ) from exc


def _agreement(candidate: str, outputs: dict[str, str]) -> float:
    """Measure the average lexical agreement between one output and the set."""
    candidate_terms = set(candidate.lower().split())
    if not candidate_terms:
        return 1.0
    overlaps = []
    for output in outputs.values():
        output_terms = set(output.lower().split())
        overlaps.append(
            len(candidate_terms & output_terms) / max(len(candidate_terms), 1)
        )
    return round(sum(overlaps) / len(overlaps), 2)
