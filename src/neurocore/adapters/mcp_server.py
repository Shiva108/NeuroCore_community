"""MCP server adapter for NeuroCore tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

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
    update_brain,
)
from neurocore.interfaces.briefing import generate_briefing
from neurocore.interfaces.capture import capture_memory
from neurocore.interfaces.dashboard import build_dashboard_data
from neurocore.interfaces.ingest import ingest_discord_event, ingest_slack_event
from neurocore.interfaces.protocols import list_protocols, run_protocol
from neurocore.interfaces.query import query_memory
from neurocore.interfaces.runtime_support import attach_runtime_metadata
from neurocore.interfaces.reporting import generate_consensus_report
from neurocore.interfaces.sessions import (
    capture_session_event,
    checkpoint_session,
    resume_session,
)
from neurocore.interfaces.summaries import run_background_summaries
from neurocore.runtime import build_semantic_ranker, build_store
from neurocore.storage.base import BaseStore


def create_mcp_server(
    *,
    store: BaseStore | None = None,
    config: NeuroCoreConfig | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """Create the MCP server and register enabled NeuroCore tools."""
    config = config or load_config()
    store = store or build_store(config)
    semantic_ranker = build_semantic_ranker(config)
    server = FastMCP("NeuroCore", host=host, port=port)

    server.add_tool(
        lambda request: capture_memory(request, store=store, config=config),
        name="capture_memory",
        description="Capture a record or document into NeuroCore.",
    )
    server.add_tool(
        lambda request: create_brain(
            request, store=store, default_allowed_buckets=config.allowed_buckets
        ),
        name="create_brain",
        description="Create or refresh a first-class NeuroCore brain manifest.",
    )
    server.add_tool(
        lambda request: get_brain(request, store=store),
        name="get_brain",
        description="Get a NeuroCore brain manifest by brain_id.",
    )
    server.add_tool(
        lambda request: list_brains(request, store=store),
        name="list_brains",
        description="List NeuroCore brain manifests.",
    )
    server.add_tool(
        lambda request: update_brain(request, store=store),
        name="update_brain",
        description="Update a NeuroCore brain manifest.",
    )
    server.add_tool(
        lambda request: archive_brain(request, store=store),
        name="archive_brain",
        description="Archive a NeuroCore brain manifest.",
    )
    server.add_tool(
        lambda request: query_memory(
            request,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        ),
        name="query_memory",
        description="Query NeuroCore records or chunks.",
    )
    server.add_tool(
        lambda request: generate_briefing(
            request,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        ),
        name="generate_briefing",
        description="Generate a compact markdown briefing from NeuroCore memory.",
    )
    server.add_tool(
        lambda request: run_protocol(
            request,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        ),
        name="run_protocol",
        description="Run a named NeuroCore protocol such as cti-review-v1.",
    )
    server.add_tool(
        lambda: list_protocols(),
        name="list_protocols",
        description="List supported NeuroCore named protocols.",
    )
    server.add_tool(
        lambda request: capture_session_event(request, store=store, config=config),
        name="capture_session_event",
        description="Capture a high-signal AI/client session event into NeuroCore.",
    )
    server.add_tool(
        lambda request: checkpoint_session(request, store=store, config=config),
        name="checkpoint_session",
        description="Store a high-signal session checkpoint into NeuroCore.",
    )
    server.add_tool(
        lambda request: resume_session(request, store=store, config=config),
        name="resume_session",
        description="Resume a prior session from NeuroCore session memory.",
    )
    server.add_tool(
        lambda request: ingest_slack_event(request, store=store, config=config),
        name="ingest_slack_event",
        description="Ingest a Slack event into NeuroCore.",
    )
    server.add_tool(
        lambda request: ingest_discord_event(request, store=store, config=config),
        name="ingest_discord_event",
        description="Ingest a Discord event into NeuroCore.",
    )
    if config.enable_background_summarization:
        server.add_tool(
            lambda request: run_background_summaries(
                attach_runtime_metadata(
                    request,
                    source_surface="mcp",
                    action="run_background_summaries",
                ),
                store=store,
                config=config,
            ),
            name="run_background_summaries",
            description="Run background document summarization.",
        )
    server.add_tool(
        lambda request: generate_consensus_report(
            request,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        ),
        name="generate_consensus_report",
        description="Generate a report from NeuroCore context, falling back to a synthesized briefing when needed.",
    )
    if config.enable_dashboard:
        server.add_tool(
            lambda request: build_dashboard_data(
                store=store,
                config=config,
                bucket_filter=str(request.get("bucket_filter") or "").strip() or None,
                brain_id=str(request.get("brain_id") or "").strip() or None,
            ),
            name="dashboard_data",
            description="Return dashboard data for NeuroCore.",
        )
    if config.enable_admin_surface:
        server.add_tool(
            lambda request: update_memory(
                attach_runtime_metadata(
                    request,
                    source_surface="mcp",
                    action="update_memory",
                ),
                store=store,
                config=config,
            ),
            name="update_memory",
            description="Update a NeuroCore record or document.",
        )
        server.add_tool(
            lambda request: delete_memory(
                attach_runtime_metadata(
                    request,
                    source_surface="mcp",
                    action="delete_memory",
                ),
                store=store,
                config=config,
            ),
            name="delete_memory",
            description="Delete a NeuroCore record or document.",
        )
        server.add_tool(
            lambda request: reindex_memory(
                attach_runtime_metadata(
                    request,
                    source_surface="mcp",
                    action="reindex_memory",
                ),
                store=store,
                config=config,
            ),
            name="reindex_memory",
            description="Reindex NeuroCore retrieval artifacts.",
        )
        server.add_tool(
            lambda request: audit_memory(
                attach_runtime_metadata(
                    request,
                    source_surface="mcp",
                    action="audit_memory",
                ),
                store=store,
                config=config,
            ),
            name="audit_memory",
            description="Audit NeuroCore memory for secret-like values.",
        )
        server.add_tool(
            lambda request: sync_storage(
                attach_runtime_metadata(
                    request,
                    source_surface="mcp",
                    action="sync_storage",
                ),
                store=store,
                config=config,
            ),
            name="sync_storage",
            description="Inspect or repair mirrored NeuroCore storage state.",
        )

    return server
