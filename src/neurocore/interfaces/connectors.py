"""Connector metadata helpers for the reference app."""

from __future__ import annotations

import json
import os
from pathlib import Path

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.reporting import build_reporting_status

OPENBRAIN_CONNECTOR_VERBS = (
    "describe_capabilities",
    "select_brain",
    "capture_session_event",
    "resume_session",
    "run_protocol",
    "generate_report",
    "health_check",
    "setup_instructions",
)


def list_connector_statuses(
    repo_root: Path | None = None,
    *,
    config: NeuroCoreConfig | None = None,
) -> list[dict[str, object]]:
    root = repo_root or Path(__file__).resolve().parents[3]
    integrations_dir = root / "integrations"
    connectors: list[dict[str, object]] = []
    for metadata_path in sorted(integrations_dir.glob("*/metadata.json")):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        slug = metadata_path.parent.name
        connectors.append(
            {
                "slug": slug,
                "name": str(payload.get("name") or slug),
                "description": str(payload.get("description") or ""),
                "capabilities": list(payload.get("capabilities") or []),
                "runnable": bool((metadata_path.parent / "connector.py").exists()),
                "configured": _connector_is_configured(slug),
                "healthy": bool((metadata_path.parent / "connector.py").exists()),
                "last_health_state": (
                    "healthy"
                    if (metadata_path.parent / "connector.py").exists()
                    else "missing"
                ),
                "supported_verbs": list(_supported_verbs(slug)),
                "setup_instructions": _setup_instructions(slug),
                "health_check_command": _health_check_command(slug),
                "reporting_status": build_reporting_status(config) if config else None,
            }
        )
    return connectors


def _supported_verbs(slug: str) -> tuple[str, ...]:
    if slug == "claude-desktop-mcp":
        return OPENBRAIN_CONNECTOR_VERBS
    return (*OPENBRAIN_CONNECTOR_VERBS, "ingest_event")


def _connector_is_configured(slug: str) -> bool:
    if slug == "claude-desktop-mcp":
        return bool(os.getenv("NEUROCORE_DEFAULT_NAMESPACE") or os.getenv("HOME"))
    return bool(os.getenv("NEUROCORE_DEFAULT_NAMESPACE"))


def _setup_instructions(slug: str) -> str:
    if slug == "claude-desktop-mcp":
        return (
            "Run the connector health command, then use claude-config and "
            "paste the output into Claude Desktop MCP settings."
        )
    if slug == "slack-connector":
        return (
            "Run health, create or select a brain, ingest a sample Slack "
            "event, then query or run a protocol."
        )
    if slug == "discord-connector":
        return (
            "Run health, create or select a brain, ingest a sample Discord "
            "message payload, then query or run a protocol."
        )
    return "Run the connector health command and follow its setup guidance."


def _health_check_command(slug: str) -> str:
    if slug == "claude-desktop-mcp":
        return "python integrations/claude-desktop-mcp/connector.py health"
    return f"python integrations/{slug}/connector.py health"
