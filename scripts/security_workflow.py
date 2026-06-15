"""Helper workflow for NeuroCore security operations and research capture."""

from __future__ import annotations

import argparse
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neurocore.core.config import (  # noqa: E402
    ConfigError,
    ReportingProviderConfig,
    load_config,
)
from neurocore.core.operator_state import (  # noqa: E402
    OPERATOR_HOME_ENV,
    load_operator_env,
    operator_env_path,
)
from neurocore.core.semantic import sentence_transformers_status  # noqa: E402
from neurocore.ingest.article_gates import (  # noqa: E402
    build_article_evaluation_text as shared_article_evaluation_text,
)
from neurocore.ingest.article_html import (  # noqa: E402
    looks_like_markup_or_navigation as shared_looks_like_markup_or_navigation,
    looks_like_target_specific_article as shared_looks_like_target_specific_article,
    preferred_article_html_fragment as shared_preferred_article_html_fragment,
    sanitize_article_html_to_markdown as shared_sanitize_article_html_to_markdown,
    strip_html_to_text as shared_strip_html_to_text,
)
from neurocore.ingest.normalize import (  # noqa: E402
    compute_content_fingerprint,
    generate_stable_id,
)
from neurocore.runtime import (  # noqa: E402
    build_reporter,
    build_summarizer,
    build_storage_backend_status,
    resolve_reporting_plan,
)
from neurocore.summarization.hierarchical import (  # noqa: E402
    build_hierarchical_summary_from_content,
)

SECURITY_BUCKETS = (
    "recon",
    "targets",
    "findings",
    "payloads",
    "reports",
    "agents",
    "ops",
)
DEFAULT_BUCKET_ALIASES = {
    "reports": ("research",),
}
PRESETS = {
    "bb": {
        "description": "Bug bounty recon, triage, and payload recall.",
        "capture_bucket": "recon",
        "capture_tags": ["bug-bounty"],
        "query_buckets": ["targets", "recon", "findings", "payloads", "agents"],
    },
    "pentest": {
        "description": "Penetration testing and red-team operator knowledge.",
        "capture_bucket": "ops",
        "capture_tags": ["pentest"],
        "query_buckets": [
            "targets",
            "recon",
            "findings",
            "payloads",
            "reports",
            "agents",
            "ops",
        ],
    },
    "paper": {
        "description": "External research, article digests, and arXiv-style papers.",
        "capture_bucket": "reports",
        "capture_source_type": "paper",
        "capture_tags": ["research", "paper"],
        "query_buckets": ["reports", "agents", "payloads", "ops"],
    },
    "agent": {
        "description": "Hackingagent traces, LLM experiments, and agent artifacts.",
        "capture_bucket": "agents",
        "capture_source_type": "agent_trace",
        "capture_tags": ["hackingagent"],
        "query_buckets": ["agents", "recon", "findings", "payloads", "ops"],
    },
}
PRESET_NAMES = tuple(PRESETS)
LOCAL_CONSENSUS_BASE_URL = "http://127.0.0.1:8787/v1"
SHARED_TRADECRAFT_NAMESPACE = "shared-tradecraft"
CORPUS_SOURCE_KINDS = (
    "bug-bounty-report",
    "htb-writeup",
    "article",
    "book-note",
)
CORPUS_DISTILLATION_BUCKETS = {
    "bug-bounty-report": ("findings", "payloads", "ops", "reports"),
    "htb-writeup": ("recon", "payloads", "findings", "ops"),
    "article": ("ops", "payloads", "reports"),
    "book-note": ("ops", "payloads", "reports"),
}
CORPUS_RECORD_KIND_SPECS = {
    "bug-bounty-report": {
        "record_kinds": {
            "recon-strategy": "recon",
            "attack-surface-pattern": "recon",
            "finding-validation-rule": "findings",
            "authz-reasoning": "findings",
            "exploit-chain": "findings",
            "poc-pattern": "payloads",
            "dead-end-pattern": "ops",
            "false-positive-trap": "ops",
            "triage-decision": "ops",
            "severity-heuristic": "ops",
            "duplicate-risk-lesson": "ops",
            "reporting-lesson": "ops",
            "escalation-decision": "ops",
            "workflow-decision": "ops",
            "operator-retrospective": "ops",
            "exploit-prerequisite": "findings",
            "accepted-proof-pattern": "findings",
            "failure-mode": "findings",
            "report-wording": "reports",
            "payload-variant": "payloads",
            "pivot-idea": "ops",
        },
        "source_sections": {
            "recon-strategy",
            "attack-surface",
            "validation-rules",
            "authz-reasoning",
            "exploit-chains",
            "payloads",
            "dead-ends",
            "false-positive-traps",
            "triage",
            "severity",
            "duplicate-risk",
            "reporting",
            "escalation",
            "workflow-decisions",
            "retrospective",
            "prerequisites",
            "proof-pattern",
            "failure-modes",
            "report-wording",
            "pivot-ideas",
        },
    },
    "htb-writeup": {
        "record_kinds": {
            "recon-pivot": "recon",
            "foothold-chain": "findings",
            "privesc-chain": "findings",
            "blocker": "ops",
            "dead-end": "ops",
            "tool-interpretation": "payloads",
            "validated-finding": "findings",
            "credential-path-pattern": "findings",
            "payload-lesson": "payloads",
            "workflow-decision": "ops",
            "postexploitation-lesson": "ops",
            "operator-retrospective": "ops",
            "attack-surface-summary": "recon",
            "detection-prevention": "reports",
        },
        "source_sections": {
            "recon",
            "foothold",
            "privesc",
            "blockers",
            "dead-ends",
            "tool-output",
            "attack-surface",
            "validated-findings",
            "credentials",
            "payloads",
            "workflow-decisions",
            "post-exploitation",
            "retrospective",
            "detection-prevention",
        },
    },
    "article": {
        "record_kinds": {
            "methodology-note": "ops",
            "checklist": "ops",
            "detection-heuristic": "ops",
            "attack-path-template": "payloads",
            "defensive-assumption": "reports",
        },
        "source_sections": {
            "methodology",
            "checklists",
            "detection",
            "attack-paths",
            "defensive-assumptions",
        },
    },
    "book-note": {
        "record_kinds": {
            "methodology-note": "ops",
            "checklist": "ops",
            "detection-heuristic": "ops",
            "attack-path-template": "payloads",
            "defensive-assumption": "reports",
        },
        "source_sections": {
            "methodology",
            "checklists",
            "detection",
            "attack-paths",
            "defensive-assumptions",
        },
    },
}
REQUIRED_CORPUS_TAG_FAMILIES = ("class", "tech", "auth")
FIXED_CORPUS_TAG_VALUES = {
    "workflow": "corpus-import",
}
ARTICLE_EVAL_MIN_WORDS = 20
ARTICLE_EVAL_NAVIGATION_HINTS = (
    "cookie",
    "privacy policy",
    "subscribe",
    "sign in",
    "menu",
    "newsletter",
)
ARTICLE_EVAL_TARGET_SPECIFIC_PATTERNS = (
    r"\bclient-specific\b",
    r"\bcustomer vpn\b",
    r"\bprovided credentials\b",
    r"\binternal dashboard\b",
    r"\binternal portal\b",
    r"\bclient portal\b",
    r"\bthis engagement\b",
    r"\bthe engagement\b",
    r"\binvoice\s+#?\d+\b",
    r"https?://[^\s]+?\.(?:internal|corp|local)\b",
)
ARTICLE_HTML_IGNORED_TAGS = frozenset(
    {
        "aside",
        "button",
        "figure",
        "footer",
        "form",
        "head",
        "iframe",
        "img",
        "input",
        "link",
        "meta",
        "nav",
        "noscript",
        "script",
        "style",
        "svg",
    }
)
CORPUS_TAG_ALIASES = {
    "space": {
        "tradecraft": "shared",
        "engagements": "engagement",
    },
    "corpus": {
        "bugbounty-report": "bug-bounty-report",
        "bug-bounty": "bug-bounty-report",
        "htb": "htb-writeup",
        "book": "book-note",
    },
    "class": {
        "bola": "idor",
        "server-side-request-forgery": "ssrf",
        "sql-injection": "sqli",
    },
    "tech": {
        "graph-ql": "graphql",
    },
    "auth": {
        "anon": "anonymous",
        "unauth": "anonymous",
        "low-priv": "user",
    },
    "artifact": {
        "raw": "raw-document",
        "distilled": "distilled-record",
    },
    "state": {
        "raw": "raw-captured",
        "distilled-record": "distilled",
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture and query security research, notes, and agent artifacts "
            "through the local NeuroCore install."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_capture_note_parser(subparsers)
    _add_capture_file_parser(subparsers)
    _add_capture_paper_parser(subparsers)
    _add_capture_hackingagent_parser(subparsers)
    _add_import_corpus_parser(subparsers)
    _add_query_parser(subparsers)
    _add_briefing_parser(subparsers)
    _add_report_parser(subparsers)
    _add_utility_parsers(subparsers)
    return parser


def _add_capture_note_parser(subparsers) -> None:
    note_parser = subparsers.add_parser(
        "capture-note", help="Store a short note or observation."
    )
    _add_capture_args(
        note_parser,
        default_bucket="ops",
        default_source_type="note",
    )
    note_parser.add_argument("content", help="Note content to store.")


def _add_capture_file_parser(subparsers) -> None:
    file_parser = subparsers.add_parser(
        "capture-file",
        help="Store a local file as a note, article digest, playbook, or report.",
    )
    _add_capture_args(
        file_parser,
        default_bucket="reports",
        default_source_type="article",
    )
    file_parser.add_argument("path", help="Path to the source file to ingest.")
    file_parser.add_argument(
        "--content-format",
        default=None,
        help="Override the auto-detected content format.",
    )


def _add_capture_paper_parser(subparsers) -> None:
    paper_parser = subparsers.add_parser(
        "capture-paper",
        help="Store a scientific paper summary, notes, or arXiv-style digest.",
    )
    _add_capture_args(
        paper_parser,
        default_bucket="reports",
        default_source_type="paper",
    )
    paper_parser.add_argument("--url", help="Paper URL or arXiv link.")
    paper_parser.add_argument(
        "--authors",
        action="append",
        default=[],
        help="Repeat for multiple authors.",
    )
    paper_parser.add_argument(
        "--topic",
        action="append",
        default=[],
        help="Repeat for topic tags such as llm, red-team, or web-security.",
    )
    paper_parser.add_argument(
        "--published-at",
        help="Publication date in ISO format or freeform text.",
    )
    paper_parser.add_argument(
        "--summary",
        help="Inline paper summary or key findings.",
    )
    paper_parser.add_argument(
        "--summary-file",
        help="Path to a markdown or text file containing the summary.",
    )
    paper_parser.add_argument(
        "--notes",
        help="Inline operator notes about why the paper matters.",
    )
    paper_parser.add_argument(
        "--notes-file",
        help="Path to a markdown or text file containing extra notes.",
    )


def _add_capture_hackingagent_parser(subparsers) -> None:
    agent_parser = subparsers.add_parser(
        "capture-hackingagent",
        help="Store a local hackingagent artifact, session log, or prompt trace.",
    )
    _add_capture_args(
        agent_parser,
        default_bucket="agents",
        default_source_type="agent_trace",
    )
    agent_parser.add_argument("path", help="Path to the hackingagent artifact.")
    agent_parser.add_argument(
        "--artifact-type",
        default="session-log",
        help="Describe the artifact kind, for example session-log or prompt-trace.",
    )
    agent_parser.add_argument(
        "--target",
        help="Optional target, client, or lab name tied to the artifact.",
    )
    agent_parser.add_argument(
        "--project",
        default="hackingagent",
        help="Source project name. Defaults to hackingagent.",
    )


def _add_import_corpus_parser(subparsers) -> None:
    corpus_parser = subparsers.add_parser(
        "import-corpus",
        help="Store a reusable security corpus source as one raw document plus optional distilled records.",
    )
    corpus_parser.add_argument("path", nargs="?", help="Optional local source path.")
    corpus_parser.add_argument(
        "--path",
        dest="path_option",
        help="Optional local source path.",
    )
    corpus_parser.add_argument("--url", help="Optional HTTP(S) source URL.")
    corpus_parser.add_argument(
        "--source-kind",
        choices=CORPUS_SOURCE_KINDS,
        required=True,
        help="Corpus source type.",
    )
    corpus_parser.add_argument(
        "--space",
        choices=("shared", "engagement"),
        required=True,
        help="Whether the import targets shared tradecraft or engagement memory.",
    )
    corpus_parser.add_argument(
        "--namespace",
        help="Override the destination namespace. Shared imports default to shared-tradecraft.",
    )
    corpus_parser.add_argument("--title", help="Optional canonical title override.")
    corpus_parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Repeat to attach additional tags.",
    )
    corpus_parser.add_argument(
        "--metadata-json",
        default="{}",
        help="JSON object merged into raw and distilled metadata.",
    )
    corpus_parser.add_argument(
        "--sensitivity",
        choices=("standard", "restricted", "sealed"),
        help="Override the default corpus sensitivity.",
    )
    corpus_parser.add_argument(
        "--allow-low-fit-import",
        action="store_true",
        help="Override the shared-article pre-ingest gate and import anyway.",
    )


def _add_query_parser(subparsers) -> None:
    query_parser = subparsers.add_parser(
        "query",
        help="Search across captured security knowledge and agent artifacts.",
    )
    query_parser.add_argument("query_text", help="Search text.")
    query_parser.add_argument(
        "--namespace",
        help="Override the default namespace from the operator env file.",
    )
    query_parser.add_argument(
        "--preset",
        choices=PRESET_NAMES,
        help="Apply a saved workflow preset.",
    )
    query_parser.add_argument(
        "--bucket",
        action="append",
        default=[],
        help="Repeat to search one or more buckets. Defaults to all security buckets.",
    )
    query_parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Repeat to match any of these tags.",
    )
    query_parser.add_argument(
        "--source-type",
        action="append",
        default=[],
        help="Repeat to filter by source type such as paper or agent_trace.",
    )
    query_parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="Maximum number of results to return.",
    )
    query_parser.add_argument(
        "--return-mode",
        choices=("hybrid", "record_only", "chunk_only", "document_aggregate"),
        default="hybrid",
        help="Query response mode.",
    )
    query_parser.add_argument(
        "--sensitivity-ceiling",
        default=None,
        help="Override the retrieval sensitivity ceiling.",
    )


def _add_briefing_parser(subparsers) -> None:
    briefing_parser = subparsers.add_parser(
        "briefing",
        help="Generate a compact markdown briefing from NeuroCore query context.",
    )
    briefing_parser.add_argument("query_text", help="Search text.")
    briefing_parser.add_argument(
        "--namespace",
        help="Override the default namespace from the operator env file.",
    )
    briefing_parser.add_argument(
        "--brain-id",
        help="Alias for namespace used by integrations and the reference app.",
    )
    briefing_parser.add_argument(
        "--preset",
        choices=PRESET_NAMES,
        help="Apply a saved workflow preset.",
    )
    briefing_parser.add_argument(
        "--bucket",
        action="append",
        default=[],
        help="Repeat to search one or more buckets. Defaults to preset query buckets.",
    )
    briefing_parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Repeat to match any of these tags.",
    )
    briefing_parser.add_argument(
        "--source-type",
        action="append",
        default=[],
        help="Repeat to filter by source type such as paper or agent_trace.",
    )
    briefing_parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="Maximum number of query results to retrieve.",
    )
    briefing_parser.add_argument(
        "--max-items",
        type=int,
        default=5,
        help="Maximum number of retrieved items to include in briefing context.",
    )
    briefing_parser.add_argument(
        "--return-mode",
        choices=("hybrid", "record_only", "chunk_only", "document_aggregate"),
        default="hybrid",
        help="Query response mode.",
    )
    briefing_parser.add_argument(
        "--sensitivity-ceiling",
        default=None,
        help="Override the retrieval sensitivity ceiling.",
    )
    briefing_parser.add_argument(
        "--include-operator-hints",
        action="store_true",
        help="Include operator retrospective memory when available.",
    )


def _add_report_parser(subparsers) -> None:
    report_parser = subparsers.add_parser(
        "report",
        help="Generate a consensus report from NeuroCore query context.",
    )
    report_parser.add_argument("query_text", help="Search text.")
    report_parser.add_argument(
        "--objective",
        required=True,
        help="Report objective sent to the consensus reporter.",
    )
    report_parser.add_argument(
        "--namespace",
        help="Override the default namespace from the operator env file.",
    )
    report_parser.add_argument(
        "--preset",
        choices=PRESET_NAMES,
        help="Apply a saved workflow preset.",
    )
    report_parser.add_argument(
        "--bucket",
        action="append",
        default=[],
        help="Repeat to search one or more buckets. Defaults to preset query buckets.",
    )
    report_parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Repeat to match any of these tags.",
    )
    report_parser.add_argument(
        "--source-type",
        action="append",
        default=[],
        help="Repeat to filter by source type such as paper or agent_trace.",
    )
    report_parser.add_argument(
        "--section",
        action="append",
        default=[],
        help="Repeat to choose report sections.",
    )
    report_parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="Maximum number of query results to retrieve before reporting.",
    )
    report_parser.add_argument(
        "--max-items",
        type=int,
        default=5,
        help="Maximum number of retrieved items to include in report context.",
    )
    report_parser.add_argument(
        "--return-mode",
        choices=("hybrid", "record_only", "chunk_only", "document_aggregate"),
        default="hybrid",
        help="Query response mode.",
    )
    report_parser.add_argument(
        "--sensitivity-ceiling",
        default=None,
        help="Override the retrieval sensitivity ceiling.",
    )
    report_parser.add_argument(
        "--target",
        default=None,
        help="Optional target or engagement label carried with the request.",
    )


def _add_utility_parsers(subparsers) -> None:
    subparsers.add_parser(
        "capabilities",
        help="Report helper readiness for sibling bridge integrations.",
    )
    subparsers.add_parser(
        "report-bootstrap",
        help="Start the bundled local mock reporter when local development mode is configured.",
    )
    subparsers.add_parser("presets", help="List the saved workflow presets.")
    protocol_list_parser = subparsers.add_parser(
        "protocols",
        help="List supported named protocols.",
    )
    protocol_list_parser.add_argument(
        "--request-json",
        default="{}",
        help="Optional JSON object passed through to the NeuroCore CLI surface.",
    )
    protocol_run_parser = subparsers.add_parser(
        "protocol-run",
        help="Run a named protocol using the NeuroCore CLI surface.",
    )
    protocol_run_parser.add_argument("--request-json", required=True)
    query_json_parser = subparsers.add_parser(
        "query-json",
        help="Forward a structured query request to the NeuroCore CLI surface.",
    )
    query_json_parser.add_argument("--request-json", required=True)
    capture_json_parser = subparsers.add_parser(
        "capture-json",
        help="Forward a structured capture request to the NeuroCore CLI surface.",
    )
    capture_json_parser.add_argument("--request-json", required=True)
    capture_batch_json_parser = subparsers.add_parser(
        "capture-batch-json",
        help="Forward a structured batch capture request to the NeuroCore CLI surface.",
    )
    capture_batch_json_parser.add_argument("--request-json", required=True)
    for name in (
        "brain-create",
        "brain-get",
        "brain-list",
        "brain-update",
        "brain-archive",
        "session-capture",
        "session-checkpoint",
        "session-resume",
    ):
        child = subparsers.add_parser(
            name,
            help=f"Forward {name} to the NeuroCore CLI surface.",
        )
        child.add_argument(
            "--request-json",
            required=name != "brain-list",
            default="{}",
        )


def _add_capture_args(
    parser: argparse.ArgumentParser,
    *,
    default_bucket: str,
    default_source_type: str,
) -> None:
    parser.set_defaults(
        fallback_bucket=default_bucket,
        fallback_source_type=default_source_type,
    )
    parser.add_argument(
        "--preset",
        choices=PRESET_NAMES,
        help="Apply a saved workflow preset.",
    )
    parser.add_argument(
        "--namespace",
        help="Override the default namespace from the operator env file.",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="Destination bucket.",
    )
    parser.add_argument(
        "--sensitivity",
        default=None,
        help="Override the default sensitivity from the operator env file.",
    )
    parser.add_argument(
        "--source-type",
        default=None,
        help="Logical source type, for example note, paper, article, or agent_trace.",
    )
    parser.add_argument("--title", help="Optional title for longer captures.")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Repeat to attach tags.",
    )
    parser.add_argument(
        "--metadata-json",
        default="{}",
        help="JSON object merged into metadata.",
    )
    parser.add_argument(
        "--force-kind",
        default=None,
        choices=("record", "document"),
        help="Force NeuroCore to store this capture as a record or document.",
    )


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    _maybe_reexec_into_repo_runtime(argv or sys.argv[1:], repo_root)
    parser = build_parser()
    args = parser.parse_args(argv)
    env = _runtime_env(repo_root)
    config = _safe_load_config(env)

    if args.command == "presets":
        print(json.dumps(PRESETS, indent=2, sort_keys=True))
        return 0

    if args.command == "capabilities":
        print(
            json.dumps(_capabilities_payload(repo_root, env), indent=2, sort_keys=True)
        )
        return 0

    if args.command == "report-bootstrap":
        payload = _report_bootstrap_payload(repo_root, env)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "protocols":
        return _run_neurocore(
            repo_root,
            env,
            ["protocol", "list"],
            _parse_request_object(args.request_json),
        )

    if args.command == "protocol-run":
        return _run_neurocore(
            repo_root,
            env,
            ["protocol", "run"],
            _parse_request_object(args.request_json),
        )

    if args.command == "query-json":
        return _run_neurocore(
            repo_root,
            env,
            "query",
            _parse_request_object(args.request_json),
        )

    if args.command == "capture-json":
        return _run_neurocore(
            repo_root,
            env,
            "capture",
            _parse_request_object(args.request_json),
        )

    if args.command == "capture-batch-json":
        return _run_neurocore(
            repo_root,
            env,
            "capture-batch",
            _parse_request_object(args.request_json),
        )

    if args.command == "import-corpus":
        payload = _import_corpus(repo_root, env, args)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("stored", True) else 1

    forwarded_admin_commands = {
        "brain-create": ["brain", "create"],
        "brain-get": ["brain", "get"],
        "brain-list": ["brain", "list"],
        "brain-update": ["brain", "update"],
        "brain-archive": ["brain", "archive"],
        "session-capture": ["session", "capture-event"],
        "session-checkpoint": ["session", "checkpoint"],
        "session-resume": ["session", "resume"],
    }
    if args.command in forwarded_admin_commands:
        return _run_neurocore(
            repo_root,
            env,
            forwarded_admin_commands[args.command],
            _parse_request_object(args.request_json),
        )

    if args.command == "capture-note":
        request = _build_capture_request(
            args,
            config=config,
            content=args.content,
            metadata={},
            content_format="markdown",
        )
        return _run_neurocore(repo_root, env, "capture", request)

    if args.command == "capture-file":
        path = Path(args.path).expanduser().resolve()
        content = _read_text_file(path, description="capture file")
        request = _build_capture_request(
            args,
            config=config,
            content=content,
            metadata={"source_path": str(path)},
            content_format=args.content_format or _detect_content_format(path),
            title=args.title or path.stem.replace("-", " ").replace("_", " "),
        )
        return _run_neurocore(repo_root, env, "capture", request)

    if args.command == "capture-paper":
        if not args.title:
            parser.error("capture-paper requires --title")
        summary_text = _optional_text(args.summary, args.summary_file)
        notes_text = _optional_text(args.notes, args.notes_file)
        request = _build_capture_request(
            args,
            config=config,
            content=_paper_markdown(
                title=args.title,
                url=args.url,
                authors=args.authors,
                published_at=args.published_at,
                summary=summary_text,
                notes=notes_text,
            ),
            metadata={
                "paper_url": args.url,
                "authors": args.authors,
                "published_at": args.published_at,
                "topics": args.topic,
            },
            content_format="markdown",
            title=args.title,
            extra_tags=args.topic,
        )
        return _run_neurocore(repo_root, env, "capture", request)

    if args.command == "capture-hackingagent":
        path = Path(args.path).expanduser().resolve()
        content = _read_text_file(path, description="hackingagent artifact")
        artifact_tags = []
        if args.artifact_type:
            artifact_tags.extend([args.artifact_type, f"artifact:{args.artifact_type}"])
        request = _build_capture_request(
            args,
            config=config,
            content=content,
            metadata={
                "source_project": args.project,
                "artifact_type": args.artifact_type,
                "source_path": str(path),
                "target": args.target,
            },
            content_format=_detect_content_format(path),
            title=args.title or path.name,
            extra_tags=artifact_tags,
        )
        return _run_neurocore(repo_root, env, "capture", request)

    if args.command == "report":
        query_request = _build_query_request(args, env, config=config)
        report_request: dict[str, object] = {
            "objective": args.objective,
            "query_request": query_request,
            "max_items": args.max_items,
        }
        if args.section:
            report_request["sections"] = args.section
        if args.target:
            report_request["target"] = args.target
        return _run_neurocore(repo_root, env, ["report", "consensus"], report_request)

    if args.command == "briefing":
        briefing_request: dict[str, object] = {
            "query_request": _build_query_request(args, env, config=config),
            "max_items": args.max_items,
            "include_operator_hints": args.include_operator_hints,
        }
        if args.brain_id:
            briefing_request["brain_id"] = args.brain_id
        return _run_neurocore(repo_root, env, "briefing", briefing_request)

    return _run_neurocore(
        repo_root,
        env,
        "query",
        _build_query_request(args, env, config=config),
    )


def _build_capture_request(
    args: argparse.Namespace,
    *,
    config,
    content: str,
    metadata: dict[str, object],
    content_format: str,
    title: str | None = None,
    extra_tags: list[str] | None = None,
) -> dict[str, object]:
    preset = PRESETS.get(args.preset or "", {})
    request: dict[str, object] = {
        "bucket": _resolve_capture_bucket(args, config=config, preset=preset),
        "content": content,
        "content_format": content_format,
        "source_type": (
            args.source_type
            or preset.get("capture_source_type")
            or args.fallback_source_type
        ),
        "metadata": _merge_metadata(args.metadata_json, metadata),
    }
    if args.namespace:
        request["namespace"] = args.namespace
    if args.sensitivity:
        request["sensitivity"] = args.sensitivity
    if args.force_kind:
        request["force_kind"] = args.force_kind
    tags = list(preset.get("capture_tags", [])) + list(args.tag)
    if extra_tags:
        tags.extend(extra_tags)
    if tags:
        request["tags"] = _dedupe_strings(tags)
    final_title = title or args.title
    if final_title:
        request["title"] = final_title
    return request


def _import_corpus(
    repo_root: Path,
    env: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, object]:
    source = _load_corpus_source(args)
    extra_metadata = _merge_metadata(args.metadata_json, {})
    config = _safe_load_config(env)
    evaluation = None
    if _should_evaluate_shared_article(args):
        evaluation = _evaluate_shared_article_pre_ingest(source=source, config=config)
        if evaluation["decision"] == "blocked":
            if not args.allow_low_fit_import:
                return {
                    "blocked": True,
                    "evaluation": evaluation,
                    "knowledge_space": args.space,
                    "namespace": _corpus_namespace(args, env),
                    "source": {
                        "kind": source["kind"],
                        "path": source.get("path"),
                        "title": str(args.title or source["title"]).strip(),
                        "url": source.get("url"),
                    },
                    "stored": False,
                }
            evaluation = _apply_shared_article_override(evaluation)
    distiller = _build_corpus_distiller(config)
    hierarchical_summary = _build_corpus_hierarchical_summary(
        source=source,
        config=config,
    )
    distillation_status = "skipped-no-provider"
    distillation_model = None
    distilled_items: list[dict[str, object]] = []
    if distiller is not None:
        try:
            distilled_items = distiller(
                content=source["content"],
                source_kind=args.source_kind,
                title=args.title or source["title"],
                knowledge_space=args.space,
            )
            distillation_status = "completed"
            distillation_model = _distillation_model_name(config)
        except Exception:
            distilled_items = []
            distillation_status = "skipped-provider-error"
            distillation_model = _distillation_model_name(config)

    raw_request = _build_corpus_raw_capture_request(
        env=env,
        args=args,
        source=source,
        extra_metadata=extra_metadata,
        distillation_status=distillation_status,
        distillation_model=distillation_model,
        pre_ingest_evaluation=evaluation,
        hierarchical_summary=hierarchical_summary,
    )
    raw_capture = _call_neurocore(repo_root, env, "capture", raw_request)
    raw_document_id = str(
        raw_capture.get("id") or raw_request["metadata"]["raw_document_id"]
    )

    distilled_captures: list[dict[str, object]] = []
    for request in _build_corpus_distilled_capture_requests(
        args=args,
        raw_request=raw_request,
        raw_document_id=raw_document_id,
        distilled_items=distilled_items,
        extra_metadata=extra_metadata,
        distillation_model=distillation_model,
        pre_ingest_evaluation=evaluation,
    ):
        capture = _call_neurocore(repo_root, env, "capture", request)
        distilled_captures.append(
            {
                "id": capture.get("id"),
                "bucket": request["bucket"],
                "title": request.get("title"),
                "source_type": request["source_type"],
            }
        )

    return {
        "blocked": False,
        "distillation_model": distillation_model,
        "distillation_status": distillation_status,
        "distilled_captures": distilled_captures,
        "distilled_count": len(distilled_captures),
        "evaluation": evaluation,
        "shared_corpus_ready": bool(args.space == "shared" and distilled_captures),
        "knowledge_space": args.space,
        "namespace": raw_request["namespace"],
        "raw_capture": {
            "bucket": raw_request["bucket"],
            "id": raw_document_id,
            "source_type": raw_request["source_type"],
        },
        "source": {
            "kind": source["kind"],
            "path": source.get("path"),
            "title": raw_request["metadata"]["canonical_title"],
            "url": source.get("url"),
        },
        "stored": True,
    }


def _build_corpus_raw_capture_request(
    *,
    env: dict[str, str],
    args: argparse.Namespace,
    source: dict[str, object],
    extra_metadata: dict[str, object],
    distillation_status: str,
    distillation_model: str | None,
    pre_ingest_evaluation: dict[str, object] | None,
    hierarchical_summary: dict[str, object] | None,
) -> dict[str, object]:
    namespace = _corpus_namespace(args, env)
    sensitivity = _corpus_sensitivity(args)
    source_type = args.source_kind.replace("-", "_")
    title = str(args.title or source["title"] or "Imported corpus source").strip()
    fingerprint = compute_content_fingerprint(str(source["content"]))
    raw_document_id = generate_stable_id(
        "doc",
        namespace,
        "reports",
        fingerprint,
        source_type,
        sensitivity,
    )
    metadata = {
        "canonical_title": title,
        "distillation_model": distillation_model,
        "distillation_status": distillation_status,
        "knowledge_space": args.space,
        "raw_document_id": raw_document_id,
        "source_kind": args.source_kind,
        "source_origin": _corpus_origin(args.space),
        **extra_metadata,
    }
    if source.get("path"):
        metadata["source_path"] = str(source["path"])
    if source.get("url"):
        metadata["source_url"] = str(source["url"])
    if source.get("original_content_format"):
        metadata["source_content_format"] = str(source["original_content_format"])
    if source.get("sanitized_from_html"):
        metadata["sanitized_from_html"] = True
    if pre_ingest_evaluation is not None:
        metadata["pre_ingest_evaluation"] = pre_ingest_evaluation
    if hierarchical_summary is not None:
        metadata["summary_strategy"] = str(
            hierarchical_summary.get("strategy") or ""
        ).strip()
        metadata["chunk_summary_count"] = len(
            list(hierarchical_summary.get("chunk_summaries") or [])
        )
    request = {
        "namespace": namespace,
        "bucket": "reports",
        "sensitivity": sensitivity,
        "content": str(source["content"]),
        "content_format": str(source["content_format"]),
        "source_type": source_type,
        "title": title,
        "metadata": metadata,
        "tags": _normalize_corpus_tags(
            list(args.tag),
            space=args.space,
            source_kind=args.source_kind,
            artifact="raw-document",
            state="raw-captured",
        ),
        "force_kind": "document",
    }
    if hierarchical_summary is not None:
        document_summary = str(
            hierarchical_summary.get("document_summary") or ""
        ).strip()
        chunk_summaries = list(hierarchical_summary.get("chunk_summaries") or [])
        if document_summary:
            request["summary"] = document_summary
        if chunk_summaries:
            request["chunk_summaries"] = chunk_summaries
    return request


def _build_corpus_distilled_capture_requests(
    *,
    args: argparse.Namespace,
    raw_request: dict[str, object],
    raw_document_id: str,
    distilled_items: list[dict[str, object]],
    extra_metadata: dict[str, object],
    distillation_model: str | None,
    pre_ingest_evaluation: dict[str, object] | None,
) -> list[dict[str, object]]:
    allowed_buckets = set(CORPUS_DISTILLATION_BUCKETS[args.source_kind])
    requests: list[dict[str, object]] = []
    for item in distilled_items:
        bucket = str(item.get("bucket") or "").strip()
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if bucket not in allowed_buckets or not title or not content:
            continue
        source_type = str(
            item.get("source_type")
            or f"{bucket[:-1] if bucket.endswith('s') else bucket}_note"
        )
        item_metadata = item.get("metadata", {})
        if not isinstance(item_metadata, dict):
            item_metadata = {}
        item_tags = item.get("tags", [])
        if not isinstance(item_tags, list):
            item_tags = []
        metadata = {
            "canonical_title": raw_request["metadata"]["canonical_title"],
            "distillation_model": distillation_model,
            "distillation_status": "completed",
            "knowledge_space": args.space,
            "raw_document_id": raw_document_id,
            "related_ids": [raw_document_id],
            "source_kind": args.source_kind,
            "source_origin": _corpus_origin(args.space),
            **extra_metadata,
            **item_metadata,
        }
        if raw_request["metadata"].get("source_path"):
            metadata["source_path"] = raw_request["metadata"]["source_path"]
        if raw_request["metadata"].get("source_url"):
            metadata["source_url"] = raw_request["metadata"]["source_url"]
        if pre_ingest_evaluation is not None:
            metadata["source_evaluation_status"] = str(
                pre_ingest_evaluation.get("decision") or ""
            )
        requests.append(
            {
                "namespace": raw_request["namespace"],
                "bucket": bucket,
                "sensitivity": raw_request["sensitivity"],
                "content": content,
                "content_format": "markdown",
                "source_type": source_type,
                "title": title,
                "metadata": metadata,
                "tags": _normalize_corpus_tags(
                    [*list(args.tag), *[str(tag) for tag in item_tags]],
                    space=args.space,
                    source_kind=args.source_kind,
                    artifact="distilled-record",
                    state="distilled",
                ),
            }
        )
    return requests


def _should_evaluate_shared_article(args: argparse.Namespace) -> bool:
    return bool(args.space == "shared" and args.source_kind == "article")


def _apply_shared_article_override(
    evaluation: dict[str, object],
) -> dict[str, object]:
    overridden = {
        **evaluation,
        "decision": "accepted-with-override",
        "blocked": False,
        "override_applied": True,
    }
    return overridden


def _evaluate_shared_article_pre_ingest(
    *,
    source: dict[str, object],
    config,
) -> dict[str, object]:
    evaluation_text = _article_evaluation_text(source)
    local_result = _evaluate_shared_article_locally(
        source=source,
        evaluation_text=evaluation_text,
    )
    decision = local_result["decision"]
    summary = local_result["summary"]
    warnings: list[str] = []
    provider_result: dict[str, object] = {
        "attempted": False,
        "decision": None,
        "model_name": None,
        "provider_name": None,
        "summary": None,
        "used": False,
    }
    provider_target = _resolve_shared_article_provider(config)
    if decision == "accepted" and provider_target is not None:
        provider_name, provider_config, model_name = provider_target
        provider_result["attempted"] = True
        provider_result["provider_name"] = provider_name
        provider_result["model_name"] = model_name
        try:
            model_result = _evaluate_shared_article_via_provider(
                source=source,
                evaluation_text=evaluation_text,
                model_name=model_name,
                provider_config=provider_config,
                provider_name=provider_name,
            )
            provider_result.update(
                {
                    "decision": model_result["decision"],
                    "model_name": model_result.get("model_name") or model_name,
                    "provider_name": model_result.get("provider_name") or provider_name,
                    "summary": model_result["summary"],
                    "used": True,
                }
            )
            if model_result["decision"] == "blocked":
                decision = "blocked"
                summary = model_result["summary"]
        except Exception as exc:
            warnings.append(f"Provider evaluation failed: {exc}")
    return {
        "blocked": bool(decision == "blocked"),
        "decision": decision,
        "local": local_result,
        "override_applied": False,
        "provider": provider_result,
        "summary": summary,
        "warnings": warnings,
    }


def _evaluate_shared_article_locally(
    *,
    source: dict[str, object],
    evaluation_text: str,
) -> dict[str, object]:
    normalized_text = " ".join(evaluation_text.split())
    word_count = len(normalized_text.split()) if normalized_text else 0
    signals: list[str] = []
    if word_count < ARTICLE_EVAL_MIN_WORDS:
        signals.append("too-little-substantive-text")
    if _looks_like_target_specific_article(normalized_text):
        signals.append("target-specific-or-engagement-bound")
    if _looks_like_markup_or_navigation(
        raw_content=str(source.get("content") or ""),
        content_format=str(source.get("content_format") or ""),
        evaluation_text=normalized_text,
    ):
        signals.append("mostly-markup-or-navigation")
    decision = "blocked" if signals else "accepted"
    if decision == "blocked":
        summary = (
            "Shared article import blocked because the content does not look like "
            f"durable, reusable tradecraft: {', '.join(signals)}."
        )
    else:
        summary = "Local evaluation accepted the article as reusable shared tradecraft."
    return {
        "decision": decision,
        "signals": signals,
        "summary": summary,
        "text_source": (
            "html-stripped"
            if str(source.get("content_format") or "").strip().lower() == "html"
            else "raw"
        ),
        "word_count": word_count,
    }


def _resolve_shared_article_provider(
    config,
) -> tuple[str, ReportingProviderConfig, str] | None:
    if config is None or not getattr(config, "enable_multi_model_consensus", False):
        return None
    plan = resolve_reporting_plan(config)
    provider_name = str(plan.primary_provider_name or "").strip()
    provider = plan.primary_provider
    if not provider_name or provider is None:
        return None
    if provider.provider_type != "openai_compatible":
        return None
    if not provider.base_url or not provider.api_key or not provider.model_names:
        return None
    if _is_local_mock_base_url(provider.base_url):
        return None
    return provider_name, provider, str(provider.model_names[0])


def _evaluate_shared_article_via_provider(
    *,
    source: dict[str, object],
    evaluation_text: str,
    model_name: str,
    provider_config: ReportingProviderConfig,
    provider_name: str,
) -> dict[str, object]:
    request = urllib_request.Request(
        url=f"{str(provider_config.base_url).rstrip('/')}/chat/completions",
        method="POST",
        headers={
            "Authorization": f"Bearer {provider_config.api_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "model": model_name,
                "temperature": 0.0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only JSON shaped as "
                            '{"decision":"accepted|blocked","summary":"..."}.\n'
                            "Approve only reusable red-team tradecraft for shared memory. "
                            "Block target-specific, engagement-bound, low-signal, or mostly "
                            "navigation content."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Title: {str(source.get('title') or '').strip()}\n"
                            f"Content format: {str(source.get('content_format') or '').strip()}\n"
                            "Evaluate whether this article belongs in shared-tradecraft.\n\n"
                            f"{evaluation_text[:12000]}"
                        ),
                    },
                ],
            }
        ).encode("utf-8"),
    )
    with urllib_request.urlopen(request, timeout=20.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise ValueError("provider returned no evaluation choices")
    message = choices[0].get("message", {})
    raw = str(message.get("content") or "").strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("provider evaluation payload must be an object")
    decision = str(parsed.get("decision") or "").strip().lower()
    if decision not in {"accepted", "blocked"}:
        raise ValueError("provider evaluation decision must be accepted or blocked")
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        raise ValueError("provider evaluation summary is required")
    return {
        "decision": decision,
        "model_name": model_name,
        "provider_name": provider_name,
        "summary": summary,
    }


def _article_evaluation_text(source: dict[str, object]) -> str:
    return shared_article_evaluation_text(source)


class _ArticleHTMLMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._ignore_depth = 0
        self._list_depth = 0
        self._in_pre = False
        self._in_inline_code = False
        self._capturing_title = False
        self._title_parts: list[str] = []
        self._current_heading_text: list[str] | None = None
        self.first_heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in ARTICLE_HTML_IGNORED_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return
        if lowered == "title":
            self._capturing_title = True
            return
        if lowered in {"article", "main", "section", "div", "p", "header"}:
            self._ensure_newlines(2)
            return
        if lowered in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_newlines(2)
            level = max(1, min(int(lowered[1]), 6))
            self._append_raw(f"{'#' * level} ")
            self._current_heading_text = []
            return
        if lowered in {"ul", "ol"}:
            self._list_depth += 1
            self._ensure_newlines(1)
            return
        if lowered == "li":
            self._ensure_newlines(1)
            indent = "  " * max(0, self._list_depth - 1)
            self._append_raw(f"{indent}- ")
            return
        if lowered == "br":
            self._ensure_newlines(1)
            return
        if lowered == "pre":
            self._ensure_newlines(2)
            self._append_raw("```text\n")
            self._in_pre = True
            return
        if lowered == "code" and not self._in_pre:
            self._append_raw("`")
            self._in_inline_code = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in ARTICLE_HTML_IGNORED_TAGS:
            if self._ignore_depth:
                self._ignore_depth -= 1
            return
        if self._ignore_depth:
            return
        if lowered == "title":
            self._capturing_title = False
            return
        if lowered in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading = " ".join(
                part for part in self._current_heading_text or [] if part
            )
            if heading and self.first_heading is None:
                self.first_heading = heading
            self._current_heading_text = None
            self._ensure_newlines(2)
            return
        if lowered in {"ul", "ol"}:
            self._list_depth = max(0, self._list_depth - 1)
            self._ensure_newlines(1)
            return
        if lowered == "pre":
            if not self._endswith("\n"):
                self._append_raw("\n")
            self._append_raw("```\n\n")
            self._in_pre = False
            return
        if lowered == "code" and self._in_inline_code:
            self._append_raw("`")
            self._in_inline_code = False
            return
        if lowered in {"article", "main", "section", "div", "p", "header", "li"}:
            self._ensure_newlines(2 if lowered != "li" else 1)

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        if self._capturing_title:
            text = " ".join(unescape(data).split())
            if text:
                self._title_parts.append(text)
            return
        if self._in_pre:
            self._append_raw(unescape(data).replace("\r\n", "\n"))
            return
        text = " ".join(unescape(data).split())
        if not text:
            return
        if self._current_heading_text is not None:
            self._current_heading_text.append(text)
        self._append_text(text)

    @property
    def extracted_title(self) -> str | None:
        title = " ".join(self._title_parts).strip()
        return self.first_heading or title

    def render_markdown(self) -> str:
        rendered = "".join(self._parts)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered)
        rendered = "\n".join(line.rstrip() for line in rendered.splitlines())
        return rendered.strip()

    def _append_text(self, text: str) -> None:
        if not self._parts:
            self._parts.append(text)
            return
        last = self._parts[-1]
        if last.endswith((" ", "\n", "`")):
            self._parts.append(text)
            return
        self._parts.append(f" {text}")

    def _append_raw(self, text: str) -> None:
        if text:
            self._parts.append(text)

    def _endswith(self, suffix: str) -> bool:
        return bool(self._parts and self._parts[-1].endswith(suffix))

    def _ensure_newlines(self, count: int) -> None:
        if count <= 0:
            return
        if not self._parts:
            return
        joined = "".join(self._parts)
        trailing = len(joined) - len(joined.rstrip("\n"))
        needed = max(0, count - trailing)
        if needed:
            self._parts.append("\n" * needed)


def _sanitize_article_html_to_markdown(
    content: str,
    *,
    fallback_title: str | None,
) -> tuple[str, str | None]:
    return shared_sanitize_article_html_to_markdown(
        content,
        fallback_title=fallback_title,
    )


def _preferred_article_html_fragment(content: str) -> str:
    return shared_preferred_article_html_fragment(content)


def _strip_html_to_text(content: str) -> str:
    return shared_strip_html_to_text(content)


def _looks_like_markup_or_navigation(
    *,
    raw_content: str,
    content_format: str,
    evaluation_text: str,
) -> bool:
    return shared_looks_like_markup_or_navigation(
        raw_content=raw_content,
        content_format=content_format,
        evaluation_text=evaluation_text,
    )


def _looks_like_target_specific_article(evaluation_text: str) -> bool:
    return shared_looks_like_target_specific_article(evaluation_text)


def _build_query_request(
    args: argparse.Namespace, env: dict[str, str], *, config
) -> dict[str, object]:
    preset = PRESETS.get(args.preset or "", {})
    request = {
        "query_text": args.query_text,
        "allowed_buckets": _resolve_query_buckets(args, config=config, preset=preset),
        "sensitivity_ceiling": (
            args.sensitivity_ceiling
            or env.get("NEUROCORE_DEFAULT_SENSITIVITY", "restricted")
        ),
        "top_k": args.top_k,
        "return_mode": args.return_mode,
    }
    namespace = getattr(args, "namespace", None)
    brain_id = getattr(args, "brain_id", None)
    if namespace:
        request["namespace"] = args.namespace
    elif brain_id:
        request["brain_id"] = brain_id
    if args.tag:
        request["tags_any"] = args.tag
    if args.source_type:
        request["source_types"] = args.source_type
    return request


def _resolve_capture_bucket(
    args: argparse.Namespace,
    *,
    config,
    preset: dict[str, object],
) -> str:
    explicit_bucket = str(args.bucket or "").strip()
    if explicit_bucket:
        return _validated_bucket(explicit_bucket, config=config)

    candidate_buckets = _default_bucket_candidates(
        [
            str(preset.get("capture_bucket") or "").strip(),
            str(getattr(args, "fallback_bucket", "") or "").strip(),
        ]
    )
    resolved = _first_allowed_bucket(candidate_buckets, config=config)
    if resolved is not None:
        return resolved
    if candidate_buckets:
        allowed = ", ".join(_allowed_buckets(config))
        attempted = ", ".join(candidate_buckets)
        raise SystemExit(
            "No default capture bucket is allowed by the active configuration. "
            f"Tried: {attempted}. Allowed buckets: {allowed}"
        )
    return _allowed_buckets(config)[0]


def _resolve_query_buckets(
    args: argparse.Namespace,
    *,
    config,
    preset: dict[str, object],
) -> list[str]:
    explicit_buckets = [
        str(bucket).strip() for bucket in args.bucket if str(bucket).strip()
    ]
    if explicit_buckets:
        return [_validated_bucket(bucket, config=config) for bucket in explicit_buckets]

    preset_buckets = _default_bucket_candidates(
        [str(bucket).strip() for bucket in preset.get("query_buckets", [])]
    )
    resolved = [
        bucket
        for bucket in _dedupe_strings(preset_buckets)
        if bucket in _allowed_buckets(config)
    ]
    if resolved:
        return resolved
    return list(_allowed_buckets(config))


def _default_bucket_candidates(values: list[str]) -> list[str]:
    candidates: list[str] = []
    for value in values:
        if not value:
            continue
        candidates.append(value)
        candidates.extend(DEFAULT_BUCKET_ALIASES.get(value, ()))
    return _dedupe_strings(candidates)


def _first_allowed_bucket(values: list[str], *, config) -> str | None:
    for value in values:
        if value in _allowed_buckets(config):
            return value
    return None


def _validated_bucket(bucket: str, *, config) -> str:
    allowed_buckets = _allowed_buckets(config)
    if bucket not in allowed_buckets:
        allowed = ", ".join(allowed_buckets)
        raise SystemExit(
            f"Bucket {bucket!r} is not allowed by the active configuration. "
            f"Allowed buckets: {allowed}"
        )
    return bucket


def _allowed_buckets(config) -> tuple[str, ...]:
    values = getattr(config, "allowed_buckets", ()) if config is not None else ()
    return tuple(values) or SECURITY_BUCKETS


def _parse_request_object(raw: str) -> dict[str, object]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit("--request-json must decode to an object")
    return payload


def _merge_metadata(raw_json: str, extra: dict[str, object]) -> dict[str, object]:
    metadata = json.loads(raw_json)
    if not isinstance(metadata, dict):
        raise ValueError("--metadata-json must decode to a JSON object")
    merged = dict(metadata)
    for key, value in extra.items():
        if value not in (None, "", []):
            merged[key] = value
    return merged


def _normalize_tag_token(value: str) -> str:
    token = str(value).strip().lower().replace("_", "-")
    token = "-".join(part for part in token.split())
    while "--" in token:
        token = token.replace("--", "-")
    return token.strip("-")


def _normalize_corpus_tag(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or ":" not in raw:
        return _normalize_tag_token(raw)
    family, token = raw.split(":", 1)
    normalized_family = _normalize_tag_token(family)
    normalized_token = _normalize_tag_token(token)
    alias = CORPUS_TAG_ALIASES.get(normalized_family, {})
    normalized_token = alias.get(normalized_token, normalized_token)
    if not normalized_family or not normalized_token:
        return ""
    return f"{normalized_family}:{normalized_token}"


def _normalize_corpus_tags(
    tags: list[str],
    *,
    space: str,
    source_kind: str,
    artifact: str,
    state: str,
) -> list[str]:
    normalized_extras: list[str] = []
    family_values: dict[str, str] = {}
    for tag in tags:
        normalized = _normalize_corpus_tag(tag)
        if not normalized:
            continue
        if ":" not in normalized:
            normalized_extras.append(normalized)
            continue
        family, token = normalized.split(":", 1)
        family_values.setdefault(family, f"{family}:{token}")

    family_values["space"] = f"space:{_normalize_tag_token(space)}"
    family_values["corpus"] = (
        "corpus:"
        f"{CORPUS_TAG_ALIASES['corpus'].get(_normalize_tag_token(source_kind), _normalize_tag_token(source_kind))}"
    )
    family_values["artifact"] = (
        "artifact:"
        f"{CORPUS_TAG_ALIASES['artifact'].get(_normalize_tag_token(artifact), _normalize_tag_token(artifact))}"
    )
    family_values["workflow"] = f"workflow:{FIXED_CORPUS_TAG_VALUES['workflow']}"
    family_values["state"] = (
        "state:"
        f"{CORPUS_TAG_ALIASES['state'].get(_normalize_tag_token(state), _normalize_tag_token(state))}"
    )
    for family in REQUIRED_CORPUS_TAG_FAMILIES:
        family_values.setdefault(family, f"{family}:unknown")

    ordered = [
        family_values["space"],
        family_values["corpus"],
        family_values["class"],
        family_values["tech"],
        family_values["auth"],
        family_values["artifact"],
        family_values["workflow"],
        family_values["state"],
        *normalized_extras,
    ]
    return _dedupe_strings(ordered)


def _distillation_contract(source_kind: str) -> dict[str, object]:
    return CORPUS_RECORD_KIND_SPECS[source_kind]


def _distillation_schema_description(source_kind: str) -> str:
    contract = _distillation_contract(source_kind)
    record_kinds = contract["record_kinds"]
    lines = ["Allowed records:"]
    for record_kind, bucket in record_kinds.items():
        lines.append(f"- {record_kind} -> {bucket}")
    sections = ", ".join(sorted(contract["source_sections"]))
    return "\n".join(
        [
            *lines,
            f"Allowed source_section values: {sections}",
        ]
    )


def _validate_distillation_record(
    record: dict[str, object], source_kind: str
) -> dict[str, object]:
    contract = _distillation_contract(source_kind)
    record_kinds = contract["record_kinds"]
    title = str(record.get("title") or "").strip()
    bucket = str(record.get("bucket") or "").strip()
    content = str(record.get("content") or "").strip()
    tags = record.get("tags", [])
    metadata = record.get("metadata", {})
    if not title or not bucket or not content:
        raise ValueError("distillation record is missing title, bucket, or content")
    if not isinstance(tags, list):
        raise ValueError("distillation record tags must be a list")
    if not isinstance(metadata, dict):
        raise ValueError("distillation record metadata must be an object")
    record_kind = str(metadata.get("record_kind") or "").strip()
    source_section = str(metadata.get("source_section") or "").strip()
    if not record_kind or not source_section:
        raise ValueError(
            "distillation record metadata must include record_kind and source_section"
        )
    if record_kind not in record_kinds:
        raise ValueError(f"unsupported record_kind for {source_kind}: {record_kind}")
    if source_section not in contract["source_sections"]:
        raise ValueError(
            f"unsupported source_section for {source_kind}: {source_section}"
        )
    expected_bucket = str(record_kinds[record_kind]).strip()
    if bucket != expected_bucket:
        raise ValueError(
            f"distillation record bucket mismatch for {record_kind}: expected {expected_bucket}, got {bucket}"
        )
    return {
        "bucket": bucket,
        "title": title,
        "content": content,
        "tags": [str(tag) for tag in tags],
        "metadata": metadata,
        "source_type": str(record.get("source_type") or "").strip() or None,
    }


def _safe_load_config(env: dict[str, str]):
    try:
        return load_config(env)
    except ConfigError:
        return None


def _build_corpus_hierarchical_summary(
    *,
    source: dict[str, object],
    config,
) -> dict[str, object] | None:
    if config is None or not getattr(config, "enable_background_summarization", False):
        return None
    try:
        summarizer = build_summarizer(config)
        summary = build_hierarchical_summary_from_content(
            str(source.get("content") or ""),
            config=config,
            summarizer=summarizer,
        )
    except Exception:
        return None
    chunk_summaries = [
        {"ordinal": item.ordinal, "summary": item.summary}
        for item in summary.chunk_summaries
        if item.summary
    ]
    if not summary.document_summary and not chunk_summaries:
        return None
    return {
        "document_summary": summary.document_summary,
        "chunk_summaries": chunk_summaries,
        "strategy": summary.strategy,
    }


def _build_corpus_distiller(config) -> object | None:
    if config is None:
        return None
    if not config.enable_multi_model_consensus:
        return None
    plan = resolve_reporting_plan(config)
    primary = plan.primary_provider
    if primary is None:
        return None
    if primary.provider_type != "openai_compatible":
        return None
    if not primary.base_url or not primary.api_key:
        return None
    if len(primary.model_names) < 2:
        return None

    def _distill(
        *,
        content: str,
        source_kind: str,
        title: str,
        knowledge_space: str,
    ) -> list[dict[str, object]]:
        return _distill_corpus_via_provider(
            config=config,
            content=content,
            source_kind=source_kind,
            title=title,
            knowledge_space=knowledge_space,
        )

    return _distill


def _distill_corpus_via_provider(
    *,
    config,
    content: str,
    source_kind: str,
    title: str,
    knowledge_space: str,
) -> list[dict[str, object]]:
    plan = resolve_reporting_plan(config)
    primary = plan.primary_provider
    if primary is None or not primary.base_url or not primary.api_key:
        raise ValueError("distillation provider is not configured")
    schema_description = _distillation_schema_description(source_kind)
    request = urllib_request.Request(
        url=f"{str(primary.base_url).rstrip('/')}/chat/completions",
        method="POST",
        headers={
            "Authorization": f"Bearer {primary.api_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "model": _distillation_model_name(config),
                "temperature": 0.0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only JSON shaped as "
                            '{"records":[{"title":"...","bucket":"...","content":"...",'
                            '"tags":["class:...","tech:...","auth:..."],'
                            '"metadata":{"record_kind":"...","source_section":"..."},'
                            '"source_type":"..."}]}\n'
                            f"{schema_description}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Source kind: {source_kind}\n"
                            f"Knowledge space: {knowledge_space}\n"
                            f"Canonical title: {title}\n"
                            "Create reusable security memory records from the source.\n\n"
                            f"{content}"
                        ),
                    },
                ],
            }
        ).encode("utf-8"),
    )
    with urllib_request.urlopen(request, timeout=20.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise ValueError("distillation provider returned no choices")
    message = choices[0].get("message", {})
    raw = str(message.get("content") or "").strip()
    return _parse_distillation_records(raw, source_kind=source_kind)


def _parse_distillation_records(
    raw: str, *, source_kind: str
) -> list[dict[str, object]]:
    payload = json.loads(raw)
    if isinstance(payload, dict):
        records = payload.get("records", [])
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError("distillation payload must be a list or object")
    if not isinstance(records, list):
        raise ValueError("distillation records must be a list")
    validated: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("distillation records must be objects")
        validated.append(_validate_distillation_record(record, source_kind))
    return validated


def _distillation_model_name(config) -> str | None:
    if config is None:
        return None
    primary = resolve_reporting_plan(config).primary_provider
    if primary is None or not primary.model_names:
        return None
    return str(primary.model_names[0])


def _optional_text(inline: str | None, file_path: str | None) -> str | None:
    parts: list[str] = []
    if inline:
        parts.append(inline.strip())
    if file_path:
        parts.append(
            _read_text_file(
                Path(file_path).expanduser().resolve(),
                description="summary or notes file",
            ).strip()
        )
    joined = "\n\n".join(part for part in parts if part)
    return joined or None


def _read_text_file(path: Path, *, description: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"{description.capitalize()} not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"{description.capitalize()} must be UTF-8 text: {path}"
        ) from exc
    except OSError as exc:
        raise SystemExit(f"Could not read {description}: {path} ({exc})") from exc


def _load_corpus_source(args: argparse.Namespace) -> dict[str, object]:
    path_value = str(args.path_option or args.path or "").strip()
    url_value = str(args.url or "").strip()
    if bool(path_value) == bool(url_value):
        raise SystemExit("import-corpus requires exactly one of --path/<path> or --url")
    if path_value:
        path = Path(path_value).expanduser().resolve()
        source = {
            "content": _read_text_file(path, description="corpus source"),
            "content_format": _detect_content_format(path),
            "kind": "path",
            "path": str(path),
            "title": args.title or path.stem.replace("-", " ").replace("_", " "),
        }
        return _normalize_corpus_source(args, source)
    request = urllib_request.Request(url=url_value, method="GET")
    try:
        with urllib_request.urlopen(request, timeout=20.0) as response:
            body = response.read()
            content_type = str(response.headers.get("Content-Type") or "").lower()
    except urllib_error.URLError as exc:
        raise SystemExit(
            f"Could not fetch corpus URL: {url_value} ({exc.reason})"
        ) from exc
    source = {
        "content": body.decode("utf-8"),
        "content_format": _content_format_from_url(url_value, content_type),
        "kind": "url",
        "title": args.title or _title_from_url(url_value),
        "url": url_value,
    }
    return _normalize_corpus_source(args, source)


def _normalize_corpus_source(
    args: argparse.Namespace, source: dict[str, object]
) -> dict[str, object]:
    if args.source_kind != "article":
        return source
    if str(source.get("content_format") or "").strip().lower() != "html":
        return source
    sanitized_content, extracted_title = _sanitize_article_html_to_markdown(
        str(source.get("content") or ""),
        fallback_title=str(source.get("title") or "").strip() or None,
    )
    normalized = dict(source)
    normalized["content"] = sanitized_content
    normalized["content_format"] = "markdown"
    normalized["original_content_format"] = "html"
    normalized["sanitized_from_html"] = True
    if not args.title and extracted_title:
        normalized["title"] = extracted_title
    return normalized


def _content_format_from_url(url: str, content_type: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".json":
        return "json"
    if suffix in {".html", ".htm"}:
        return "html"
    if "markdown" in content_type:
        return "markdown"
    if "json" in content_type:
        return "json"
    if "html" in content_type:
        return "html"
    return "text"


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    stem = Path(unquote(parsed.path)).stem.replace("-", " ").replace("_", " ").strip()
    return stem or parsed.netloc or "Imported URL"


def _corpus_namespace(args: argparse.Namespace, env: dict[str, str]) -> str:
    if args.namespace:
        return str(args.namespace).strip()
    if args.space == "shared":
        return SHARED_TRADECRAFT_NAMESPACE
    return env.get("NEUROCORE_DEFAULT_NAMESPACE", "security-lab")


def _corpus_sensitivity(args: argparse.Namespace) -> str:
    sensitivity = str(args.sensitivity or "").strip().lower()
    if not sensitivity:
        return "standard" if args.space == "shared" else "restricted"
    if args.space == "shared" and sensitivity == "sealed":
        raise SystemExit("sealed corpus imports must use --space engagement")
    return sensitivity


def _corpus_origin(space: str) -> str:
    return "external-public" if space == "shared" else "engagement-curated"


def _paper_markdown(
    *,
    title: str,
    url: str | None,
    authors: list[str],
    published_at: str | None,
    summary: str | None,
    notes: str | None,
) -> str:
    sections = [f"# {title}"]
    if url:
        sections.append(f"Source: {url}")
    if authors:
        sections.append(f"Authors: {', '.join(authors)}")
    if published_at:
        sections.append(f"Published: {published_at}")
    if summary:
        sections.append(f"## Summary\n\n{summary}")
    if notes:
        sections.append(f"## Operator Notes\n\n{notes}")
    return "\n\n".join(sections)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def _detect_content_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".json":
        return "json"
    if suffix in {".html", ".htm"}:
        return "html"
    return "text"


def _runtime_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    should_load_operator_env = repo_root == REPO_ROOT or bool(
        env.get("NEUROCORE_OPERATOR_HOME", "").strip()
    )
    has_operator_env = False
    if should_load_operator_env:
        loaded_env, env_path, used_legacy = load_operator_env(
            repo_root, base_env=env, stderr=sys.stderr
        )
        env.update(loaded_env)
        has_operator_env = env_path.exists() or used_legacy
    if has_operator_env:
        env.setdefault("NEUROCORE_ALLOWED_BUCKETS", ",".join(SECURITY_BUCKETS))
        env.setdefault("NEUROCORE_DEFAULT_SENSITIVITY", "restricted")
        env.setdefault("NEUROCORE_DEFAULT_NAMESPACE", "security-lab")
    else:
        env["NEUROCORE_ALLOWED_BUCKETS"] = ",".join(SECURITY_BUCKETS)
        env["NEUROCORE_DEFAULT_SENSITIVITY"] = "restricted"
        env["NEUROCORE_DEFAULT_NAMESPACE"] = "security-lab"
    src_path = str(repo_root / "src")
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    if existing_pythonpath:
        parts = existing_pythonpath.split(os.pathsep)
        if src_path not in parts:
            env["PYTHONPATH"] = os.pathsep.join([src_path, *parts])
    else:
        env["PYTHONPATH"] = src_path
    return env


def _capabilities_payload(repo_root: Path, env: dict[str, str]) -> dict[str, object]:
    issues: list[str] = []
    issues_by_surface: dict[str, list[str]] = {
        "runtime": [],
        "default_namespace": [],
        "query": [],
        "semantic": [],
        "report": [],
    }
    default_namespace_ready = bool(env.get("NEUROCORE_DEFAULT_NAMESPACE", "").strip())
    if not default_namespace_ready:
        issues_by_surface["default_namespace"].append(
            "Missing required configuration: NEUROCORE_DEFAULT_NAMESPACE"
        )
    env_path = operator_env_path(env)
    legacy_env_exists = (repo_root / ".env").exists()
    has_explicit_operator_state = any(
        env.get(key, "").strip()
        for key in (OPERATOR_HOME_ENV, "HOME", "XDG_STATE_HOME")
    )
    if not legacy_env_exists and (
        not env_path.exists() or not has_explicit_operator_state
    ):
        issues_by_surface["runtime"].append(
            f"Missing NeuroCore operator env file: {env_path}"
        )

    resolved_python = _resolve_repo_python(repo_root, env)
    if resolved_python is None:
        issues_by_surface["runtime"].append(
            "NeuroCore Python executable is missing. "
            "Set NEUROCORE_PYTHON_EXECUTABLE or run `python scripts/bootstrap.py` first."
        )

    query_ready = False
    semantic_ready = False
    semantic_mode = "not-configured"
    report_ready = False
    briefing_ready = False
    report_provider_mode = "disabled"
    distillation_ready = False
    shared_corpus_ready = False
    reporting_strategy = "single_provider"
    reporting_consensus_mode = "lexical_select"
    reporting_primary_provider = None
    reporting_fallback_provider = None
    reporting_active_provider = None
    reporting_provider_health: dict[str, object] = {}
    report_bootstrap_attempted = False
    report_bootstrap_started = False
    report_bootstrap_healthy = False
    config = None
    try:
        config = load_config(env)
        query_ready = True
        briefing_ready = True
    except ConfigError as exc:
        issues_by_surface["query"].append(str(exc))

    if config is not None:
        report_provider_mode = _report_provider_mode(config)
        if config.semantic_backend == "none":
            semantic_ready = True
            semantic_mode = "metadata-only"
        elif config.semantic_backend == "sentence-transformers":
            semantic_status, semantic_issue = sentence_transformers_status()
            semantic_ready = semantic_status == "ready"
            semantic_mode = "hybrid-ready" if semantic_ready else "dependency-missing"
            if not semantic_ready:
                query_ready = False
                briefing_ready = False
                if semantic_issue:
                    issues_by_surface["semantic"].append(semantic_issue)
        else:
            semantic_mode = "unknown-backend"
            issues_by_surface["semantic"].append(
                f"Semantic backend {config.semantic_backend} is not recognized."
            )
            query_ready = False
            briefing_ready = False
        try:
            build_reporter(config)
            report_health = _check_reporting_health(config)
            reporting_strategy = str(
                report_health.get("strategy") or reporting_strategy
            )
            reporting_consensus_mode = str(
                report_health.get("consensus_mode") or reporting_consensus_mode
            )
            reporting_primary_provider = report_health.get("primary_provider")
            reporting_fallback_provider = report_health.get("fallback_provider")
            reporting_provider_health = dict(report_health.get("provider_health") or {})
            report_ready, report_issue = _check_reporter_health(config)
            if report_ready:
                reporting_active_provider = report_health.get("active_provider")
            if not report_ready and report_provider_mode == "mock_local":
                report_bootstrap_attempted = True
                bootstrap = _bootstrap_reporter(repo_root, env, config)
                report_bootstrap_started = bool(bootstrap.get("started"))
                report_bootstrap_healthy = bool(bootstrap.get("healthy"))
                if report_bootstrap_healthy:
                    report_health = _check_reporting_health(config)
                    reporting_provider_health = dict(
                        report_health.get("provider_health") or {}
                    )
                    report_ready, report_issue = _check_reporter_health(config)
                    if report_ready:
                        reporting_active_provider = report_health.get("active_provider")
            if report_issue:
                issues_by_surface["report"].append(report_issue)
        except PermissionError:
            issues_by_surface["report"].append("Consensus reporting disabled")
        except ValueError as exc:
            issues_by_surface["report"].append(str(exc))
        distillation_ready = bool(
            report_ready
            and config.enable_multi_model_consensus
            and report_provider_mode != "mock_local"
            and _build_corpus_distiller(config) is not None
        )
        shared_corpus_ready = bool(query_ready and distillation_ready)

    for surface_issues in issues_by_surface.values():
        issues.extend(surface_issues)

    return {
        "config_ready": query_ready,
        "default_namespace_ready": default_namespace_ready,
        "consensus_report_ready": report_ready,
        "briefing_ready": briefing_ready,
        "semantic_ready": semantic_ready,
        "semantic_mode": semantic_mode,
        "query_ready": query_ready,
        "report_ready": report_ready,
        "report_provider_mode": report_provider_mode,
        "reporting_strategy": reporting_strategy,
        "reporting_consensus_mode": reporting_consensus_mode,
        "reporting_primary_provider": reporting_primary_provider,
        "reporting_fallback_provider": reporting_fallback_provider,
        "reporting_active_provider": reporting_active_provider,
        "reporting_provider_health": reporting_provider_health,
        "distillation_ready": distillation_ready,
        "shared_corpus_ready": shared_corpus_ready,
        "report_bootstrap_attempted": report_bootstrap_attempted,
        "report_bootstrap_started": report_bootstrap_started,
        "report_bootstrap_healthy": report_bootstrap_healthy,
        "storage_backend": (
            build_storage_backend_status(config).to_dict()
            if config is not None
            else None
        ),
        "resolved_python": str(resolved_python) if resolved_python else None,
        "issues_by_surface": {
            key: value for key, value in issues_by_surface.items() if value
        },
        "issues": _dedupe_strings(issues),
        "recommendations": [
            "Enable sentence-transformers for stronger semantic corpus recall."
        ],
    }


def _report_provider_mode(config) -> str:
    if not config.enable_multi_model_consensus:
        return "disabled"
    plan = resolve_reporting_plan(config)
    primary = plan.primary_provider
    if primary is None:
        return "disabled"
    if _is_local_mock_base_url(primary.base_url):
        return "mock_local"
    return "external_openai_compatible"


def _is_local_mock_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return host in {"127.0.0.1", "localhost"} and path == "/v1"


def _check_provider_health(
    provider_name: str,
    provider_config: ReportingProviderConfig,
) -> tuple[bool, str | None]:
    base_url = (provider_config.base_url or "").rstrip("/")
    if not base_url:
        return False, f"Provider {provider_name} requires a consensus base URL"
    headers = {}
    if provider_config.api_key:
        headers["Authorization"] = f"Bearer {provider_config.api_key}"
    if _is_local_mock_base_url(base_url):
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        health_urls = [f"{origin}/health"]
    else:
        health_urls = [f"{base_url}/models", f"{base_url}/health"]
    last_error = "Consensus reporter health check failed"
    for url in health_urls:
        req = urllib_request.Request(url=url, headers=headers, method="GET")
        try:
            with urllib_request.urlopen(req, timeout=2.0) as response:
                if 200 <= int(response.status) < 300:
                    return True, None
                last_error = f"Provider {provider_name} health check returned HTTP {response.status}"
        except urllib_error.URLError as exc:
            last_error = f"Provider {provider_name} health check failed: {exc.reason}"
        except TimeoutError as exc:
            last_error = f"Provider {provider_name} health check failed: {exc}"
    return False, last_error


def _check_reporting_health(config) -> dict[str, object]:
    plan = resolve_reporting_plan(config)
    primary = plan.primary_provider
    fallback = plan.fallback_provider
    provider_health: dict[str, object] = {}
    primary_healthy = False
    primary_error = None
    fallback_healthy = False
    fallback_error = None

    if primary is not None and plan.primary_provider_name is not None:
        primary_healthy, primary_error = _check_provider_health(
            plan.primary_provider_name,
            primary,
        )
        provider_health[plan.primary_provider_name] = {
            "role": "primary",
            "healthy": primary_healthy,
            "error": primary_error,
            "base_url": primary.base_url,
            "model_names": list(primary.model_names),
        }

    if fallback is not None and plan.fallback_provider_name is not None:
        fallback_healthy, fallback_error = _check_provider_health(
            plan.fallback_provider_name,
            fallback,
        )
        provider_health[plan.fallback_provider_name] = {
            "role": "fallback",
            "healthy": fallback_healthy,
            "error": fallback_error,
            "base_url": fallback.base_url,
            "model_names": list(fallback.model_names),
        }

    active_provider = None
    error = None
    if primary_healthy and plan.primary_provider_name is not None:
        active_provider = plan.primary_provider_name
    elif fallback_healthy and plan.fallback_provider_name is not None:
        active_provider = plan.fallback_provider_name
    else:
        errors = [message for message in (primary_error, fallback_error) if message]
        error = "; ".join(errors) or "Consensus reporter health check failed"

    return {
        "strategy": plan.strategy,
        "consensus_mode": getattr(config, "reporting_consensus_mode", "lexical_select"),
        "primary_provider": plan.primary_provider_name,
        "fallback_provider": plan.fallback_provider_name,
        "active_provider": active_provider,
        "provider_health": provider_health,
        "report_ready": active_provider is not None,
        "error": error,
    }


def _check_reporter_health(config) -> tuple[bool, str | None]:
    payload = _check_reporting_health(config)
    return bool(payload.get("report_ready")), payload.get("error")


def _report_bootstrap_payload(
    repo_root: Path, env: dict[str, str]
) -> dict[str, object]:
    try:
        config = load_config(env)
    except ConfigError as exc:
        return {
            "mode": "disabled",
            "started": False,
            "healthy": False,
            "base_url": "",
            "error": str(exc),
        }
    return _bootstrap_reporter(repo_root, env, config)


def _bootstrap_reporter(
    repo_root: Path,
    env: dict[str, str],
    config,
) -> dict[str, object]:
    mode = _report_provider_mode(config)
    plan = resolve_reporting_plan(config)
    primary = plan.primary_provider
    base_url = str(primary.base_url or "").strip() if primary is not None else ""
    if mode != "mock_local":
        return {
            "mode": mode,
            "started": False,
            "healthy": False,
            "base_url": base_url,
        }

    healthy, _ = _check_reporter_health(config)
    if healthy:
        return {
            "mode": mode,
            "started": False,
            "healthy": True,
            "base_url": base_url,
        }

    python_path = _resolve_repo_python(repo_root, env)
    if python_path is None:
        return {
            "mode": mode,
            "started": False,
            "healthy": False,
            "base_url": base_url,
            "error": (
                "NeuroCore Python executable is missing. "
                "Set NEUROCORE_PYTHON_EXECUTABLE or run `python scripts/bootstrap.py` first."
            ),
        }

    parsed = urlparse(base_url or LOCAL_CONSENSUS_BASE_URL)
    host = (parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
    port = parsed.port or 8787
    command = [
        str(python_path),
        str(repo_root / "scripts" / "mock_openai_compatible.py"),
        "--host",
        host,
        "--port",
        str(port),
    ]

    try:
        subprocess.Popen(
            command,
            cwd=repo_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "mode": mode,
            "started": False,
            "healthy": False,
            "base_url": base_url,
            "error": f"Could not start local mock reporter: {exc}",
        }

    for _ in range(10):
        healthy, _ = _check_reporter_health(config)
        if healthy:
            break
        time.sleep(0.2)

    return {
        "mode": mode,
        "started": True,
        "healthy": healthy,
        "base_url": base_url,
    }


def _run_neurocore(
    repo_root: Path,
    env: dict[str, str],
    command: str | list[str],
    request: dict[str, object],
) -> int:
    payload = _call_neurocore(repo_root, env, command, request)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _call_neurocore(
    repo_root: Path,
    env: dict[str, str],
    command: str | list[str],
    request: dict[str, object],
) -> dict[str, object]:
    python_path = _resolve_repo_python(repo_root, env)
    if python_path is None:
        raise SystemExit(
            "NeuroCore Python executable is missing. "
            "Set NEUROCORE_PYTHON_EXECUTABLE or run `python scripts/bootstrap.py` first."
        )

    command_parts = [command] if isinstance(command, str) else list(command)
    completed = subprocess.run(
        [
            str(python_path),
            "-m",
            "neurocore.adapters.cli",
            *command_parts,
            "--request-json",
            json.dumps(request),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        if completed.stderr:
            raise SystemExit(completed.stderr)
        if completed.stdout:
            raise SystemExit(completed.stdout)
        raise SystemExit("NeuroCore command failed")

    output = completed.stdout.strip()
    if not output:
        return {}
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {"raw_output": output}
    if not isinstance(payload, dict):
        return {"payload": payload}
    return payload


def _resolve_repo_python(repo_root: Path, env: dict[str, str]) -> Path | None:
    override = env.get("NEUROCORE_PYTHON_EXECUTABLE", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend(
        [
            repo_root / ".venv" / "bin" / "python",
            repo_root / ".venv" / "Scripts" / "python.exe",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.expanduser().absolute()
    return None


def _maybe_reexec_into_repo_runtime(argv: list[str], repo_root: Path) -> None:
    repo_python = _resolve_repo_python(repo_root, dict(os.environ))
    if repo_python is None:
        return
    venv_dir = repo_root / ".venv"
    if Path(sys.prefix).resolve() == venv_dir.resolve():
        return
    if os.environ.get("NEUROCORE_SKIP_RUNTIME_REEXEC") == "1":
        return
    env = dict(os.environ)
    env["NEUROCORE_SKIP_RUNTIME_REEXEC"] = "1"
    os.execve(str(repo_python), [str(repo_python), str(Path(__file__)), *argv], env)


def print_readiness_summary(
    *,
    repo_root: Path,
    env: dict[str, str],
    stdout: TextIO,
) -> None:
    payload = _capabilities_payload(repo_root, env)
    print("", file=stdout)
    print(
        "Readiness summary:"
        f" semantic={'ready' if payload['semantic_ready'] else 'not ready'}"
        f" ({payload.get('semantic_mode', 'unknown')});"
        f" query={'ready' if payload['query_ready'] else 'not ready'};"
        f" report={'ready' if payload['report_ready'] else 'not ready'}",
        file=stdout,
    )
    if payload.get("semantic_mode") == "metadata-only":
        print(
            "Semantic retrieval is currently metadata-only. Enable sentence-transformers for stronger corpus recall.",
            file=stdout,
        )
    recommendations = payload.get("recommendations", [])
    if recommendations:
        for recommendation in recommendations:
            print(f"Recommendation: {recommendation}", file=stdout)
    print(
        f"Report provider mode: {payload['report_provider_mode']}",
        file=stdout,
    )
    if payload["report_ready"] and payload["report_provider_mode"] == "mock_local":
        print(
            "Report readiness is currently using the local mock provider for development only.",
            file=stdout,
        )
    report_issues = payload.get("issues_by_surface", {}).get("report", [])
    if report_issues:
        print("Report prerequisites still missing:", file=stdout)
        for issue in report_issues:
            print(f"- {issue}", file=stdout)
        print(
            "Local-only report generation can use the bundled mock provider at "
            f"{LOCAL_CONSENSUS_BASE_URL}.",
            file=stdout,
        )


if __name__ == "__main__":
    raise SystemExit(main())
