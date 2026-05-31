"""Command-line interface adapter for NeuroCore."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TextIO

from neurocore.adapters.http_api import create_app
from neurocore.adapters.mcp_server import create_mcp_server
from neurocore.core.config import NeuroCoreConfig, load_config
from neurocore.interfaces.admin import (
    audit_memory,
    delete_memory,
    reindex_memory,
    sync_storage,
    update_memory,
)
from neurocore.interfaces.brains import (
    archive_brain,
    create_brain,
    get_brain,
    list_brains,
    update_brain as update_brain_interface,
)
from neurocore.interfaces.briefing import generate_briefing
from neurocore.interfaces.capture import capture_many, capture_memory
from neurocore.interfaces.diagnostics import diagnose_runtime
from neurocore.interfaces.ingest import ingest_discord_event, ingest_slack_event
from neurocore.interfaces.protocols import list_protocols, run_protocol
from neurocore.interfaces.query import query_memory
from neurocore.interfaces.reporting import generate_consensus_report
from neurocore.interfaces.sessions import (
    capture_session_event,
    checkpoint_session,
    resume_session,
)
from neurocore.interfaces.summaries import run_background_summaries
from neurocore.runtime import build_semantic_ranker, build_store
from neurocore.storage.base import BaseStore


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser."""
    parser = argparse.ArgumentParser(prog="neurocore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--request-json", required=True)

    capture_batch_parser = subparsers.add_parser("capture-batch")
    capture_batch_parser.add_argument("--request-json", required=True)

    query_parser = subparsers.add_parser("query")
    query_parser.add_argument("--request-json", required=True)

    subparsers.add_parser("diagnose")

    briefing_parser = subparsers.add_parser("briefing")
    briefing_parser.add_argument("--request-json", required=True)

    report_parser = subparsers.add_parser("report")
    report_subparsers = report_parser.add_subparsers(
        dest="report_command", required=True
    )
    consensus_parser = report_subparsers.add_parser("consensus")
    consensus_parser.add_argument("--request-json", required=True)

    protocol_parser = subparsers.add_parser("protocol")
    protocol_subparsers = protocol_parser.add_subparsers(
        dest="protocol_command", required=True
    )
    protocol_run_parser = protocol_subparsers.add_parser("run")
    protocol_run_parser.add_argument("--request-json", required=True)
    protocol_subparsers.add_parser("list")

    session_parser = subparsers.add_parser("session")
    session_subparsers = session_parser.add_subparsers(
        dest="session_command", required=True
    )
    for name in ("capture-event", "checkpoint", "resume"):
        child = session_subparsers.add_parser(name)
        child.add_argument("--request-json", required=True)

    brain_parser = subparsers.add_parser("brain")
    brain_subparsers = brain_parser.add_subparsers(dest="brain_command", required=True)
    for name in ("create", "get", "update", "archive"):
        child = brain_subparsers.add_parser(name)
        child.add_argument("--request-json", required=True)
    brain_list_parser = brain_subparsers.add_parser("list")
    brain_list_parser.add_argument("--request-json", default="{}")

    ingest_parser = subparsers.add_parser("ingest")
    ingest_subparsers = ingest_parser.add_subparsers(
        dest="ingest_command", required=True
    )
    for name in ("slack", "discord"):
        command_parser = ingest_subparsers.add_parser(name)
        command_parser.add_argument("--request-json", required=True)

    summaries_parser = subparsers.add_parser("summaries")
    summaries_subparsers = summaries_parser.add_subparsers(
        dest="summaries_command", required=True
    )
    run_parser = summaries_subparsers.add_parser("run")
    run_parser.add_argument("--request-json", required=True)

    admin_parser = subparsers.add_parser("admin")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command", required=True)
    for name in ("update", "delete", "reindex", "audit", "sync"):
        command_parser = admin_subparsers.add_parser(name)
        command_parser.add_argument("--request-json", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_subparsers = serve_parser.add_subparsers(dest="serve_command", required=True)

    http_parser = serve_subparsers.add_parser("http")
    http_parser.add_argument("--host", default="127.0.0.1")
    http_parser.add_argument("--port", type=int, default=8000)

    mcp_parser = serve_subparsers.add_parser("mcp")
    mcp_parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
    )
    mcp_parser.add_argument("--mount-path", default=None)

    return parser


def main(
    argv: list[str] | None = None,
    *,
    store: BaseStore | None = None,
    config: NeuroCoreConfig | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run the NeuroCore CLI and write a JSON response to stdout."""
    parser = build_parser()
    args = parser.parse_args(argv)
    stdout = stdout or sys.stdout

    if args.command == "diagnose":
        response = diagnose_runtime(
            env=dict(os.environ),
            config=config,
            store=store,
        )
        stdout.write(json.dumps(response))
        stdout.write("\n")
        return 0

    config = config or load_config()
    store = store or build_store(config)

    if args.command == "capture":
        response = capture_memory(
            _parse_request(args.request_json), store=store, config=config
        )
    elif args.command == "capture-batch":
        request = _parse_request(args.request_json)
        response = capture_many(
            list(request.get("requests") or []),
            store=store,
            config=config,
        )
    elif args.command == "query":
        response = query_memory(
            _parse_request(args.request_json),
            store=store,
            config=config,
            semantic_ranker=build_semantic_ranker(config),
        )
    elif args.command == "briefing":
        response = generate_briefing(
            _parse_request(args.request_json),
            store=store,
            config=config,
            semantic_ranker=build_semantic_ranker(config),
        )
    elif args.command == "report":
        response = generate_consensus_report(
            _parse_request(args.request_json),
            store=store,
            config=config,
            semantic_ranker=build_semantic_ranker(config),
        )
    elif args.command == "protocol":
        if args.protocol_command == "list":
            response = {"protocols": list_protocols()}
        else:
            response = run_protocol(
                _parse_request(args.request_json),
                store=store,
                config=config,
                semantic_ranker=build_semantic_ranker(config),
            )
    elif args.command == "session":
        request = _parse_request(args.request_json)
        if args.session_command == "capture-event":
            response = capture_session_event(request, store=store, config=config)
        elif args.session_command == "checkpoint":
            response = checkpoint_session(request, store=store, config=config)
        else:
            response = resume_session(request, store=store, config=config)
    elif args.command == "brain":
        request = _parse_request(args.request_json)
        if args.brain_command == "create":
            response = create_brain(
                request, store=store, default_allowed_buckets=config.allowed_buckets
            )
        elif args.brain_command == "get":
            response = get_brain(request, store=store)
        elif args.brain_command == "list":
            response = list_brains(request, store=store)
        elif args.brain_command == "archive":
            response = archive_brain(request, store=store)
        else:
            response = update_brain_interface(request, store=store)
    elif args.command == "ingest":
        request = _parse_request(args.request_json)
        if args.ingest_command == "slack":
            response = ingest_slack_event(request, store=store, config=config)
        else:
            response = ingest_discord_event(request, store=store, config=config)
    elif args.command == "summaries":
        response = run_background_summaries(
            _parse_request(args.request_json),
            store=store,
            config=config,
        )
    elif args.command == "serve":
        if args.serve_command == "http":
            run_http_server(
                store=store,
                config=config,
                host=args.host,
                port=args.port,
            )
        else:
            run_mcp_server(
                store=store,
                config=config,
                transport=args.transport,
                mount_path=args.mount_path,
            )
        return 0
    else:
        if not config.enable_admin_surface:
            raise PermissionError("Admin surface is disabled")
        request = _parse_request(args.request_json)
        if args.admin_command == "update":
            response = update_memory(request, store=store, config=config)
        elif args.admin_command == "delete":
            response = delete_memory(request, store=store, config=config)
        elif args.admin_command == "audit":
            response = audit_memory(request, store=store, config=config)
        elif args.admin_command == "sync":
            response = sync_storage(request, store=store, config=config)
        else:
            response = reindex_memory(request, store=store, config=config)

    stdout.write(json.dumps(response))
    stdout.write("\n")
    return 0


def run_http_server(
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    host: str,
    port: int,
) -> None:
    """Run the FastAPI adapter with the current store and config."""
    if not config.enable_http_adapter:
        raise PermissionError("HTTP adapter is disabled")
    import uvicorn

    app = create_app(store=store, config=config)
    uvicorn.run(app, host=host, port=port)


def run_mcp_server(
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    transport: str,
    mount_path: str | None,
) -> None:
    """Run the MCP adapter with the current store and config."""
    if not config.enable_mcp_adapter:
        raise PermissionError("MCP adapter is disabled")

    server = create_mcp_server(store=store, config=config)
    server.run(transport=transport, mount_path=mount_path)


def _parse_request(raw: str) -> dict[str, object]:
    """Parse a JSON object supplied to a CLI command."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("request-json must decode to an object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
