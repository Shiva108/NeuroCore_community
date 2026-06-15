"""FastAPI adapter for exposing NeuroCore over HTTP."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from neurocore.adapters.dashboard_render import (
    render_dashboard_stylesheet,
    render_reference_app as render_dashboard_page,
)
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
from neurocore.interfaces.capture import capture_many, capture_memory
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

ResponseT = TypeVar("ResponseT")
FormValues = dict[str, list[str]]


def create_app(
    *,
    store: BaseStore | None = None,
    config: NeuroCoreConfig | None = None,
) -> FastAPI:
    """Create the FastAPI application for the configured NeuroCore runtime."""
    config = config or load_config()
    store = store or build_store(config)
    semantic_ranker = build_semantic_ranker(config)

    app = FastAPI(title="NeuroCore")
    app.state.config = config
    _register_core_routes(
        app,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
    )
    _register_dashboard_routes(
        app,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
    )
    return app


def _register_core_routes(
    app: FastAPI,
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
) -> None:
    @app.post("/capture")
    def capture_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: capture_memory(request, store=store, config=config)
        )

    @app.post("/capture/batch")
    def capture_batch_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: capture_many(
                list(request.get("requests") or []),
                store=store,
                config=config,
            )
        )

    @app.post("/brains/create")
    def brain_create_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: create_brain(
                request, store=store, default_allowed_buckets=config.allowed_buckets
            )
        )

    @app.post("/brains/get")
    def brain_get_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(lambda: get_brain(request, store=store))

    @app.post("/brains/list")
    def brain_list_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(lambda: list_brains(request, store=store))

    @app.post("/brains/update")
    def brain_update_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(lambda: update_brain(request, store=store))

    @app.post("/brains/archive")
    def brain_archive_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(lambda: archive_brain(request, store=store))

    @app.post("/query")
    def query_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: query_memory(
                request,
                store=store,
                config=config,
                semantic_ranker=semantic_ranker,
            )
        )

    @app.post("/briefings/generate")
    def briefing_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: generate_briefing(
                request,
                store=store,
                config=config,
                semantic_ranker=semantic_ranker,
            )
        )

    @app.post("/reports/consensus")
    def report_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: _guard_reporting(
                lambda: generate_consensus_report(
                    request,
                    store=store,
                    config=config,
                    semantic_ranker=semantic_ranker,
                )
            )
        )

    @app.get("/protocols/list")
    def protocol_list_endpoint() -> dict[str, object]:
        return {"protocols": list_protocols()}

    @app.post("/protocols/run")
    def protocol_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: run_protocol(
                request,
                store=store,
                config=config,
                semantic_ranker=semantic_ranker,
            )
        )

    @app.post("/sessions/capture")
    def session_capture_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: capture_session_event(request, store=store, config=config)
        )

    @app.post("/sessions/checkpoint")
    def session_checkpoint_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: checkpoint_session(request, store=store, config=config)
        )

    @app.post("/sessions/resume")
    def session_resume_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: resume_session(request, store=store, config=config)
        )

    @app.post("/admin/update")
    def update_endpoint(request: dict[str, object]) -> dict[str, object]:
        payload = attach_runtime_metadata(
            request, source_surface="http", action="update_memory"
        )
        return _guard_request_errors(
            lambda: _guard_admin(
                lambda: update_memory(payload, store=store, config=config)
            )
        )

    @app.post("/admin/delete")
    def delete_endpoint(request: dict[str, object]) -> dict[str, object]:
        payload = attach_runtime_metadata(
            request, source_surface="http", action="delete_memory"
        )
        return _guard_request_errors(
            lambda: _guard_admin(
                lambda: delete_memory(payload, store=store, config=config)
            )
        )

    @app.post("/admin/reindex")
    def reindex_endpoint(request: dict[str, object]) -> dict[str, object]:
        payload = attach_runtime_metadata(
            request, source_surface="http", action="reindex_memory"
        )
        return _guard_request_errors(
            lambda: _guard_admin(
                lambda: reindex_memory(payload, store=store, config=config)
            )
        )

    @app.post("/admin/audit")
    def audit_endpoint(request: dict[str, object]) -> dict[str, object]:
        payload = attach_runtime_metadata(
            request, source_surface="http", action="audit_memory"
        )
        return _guard_request_errors(
            lambda: _guard_admin(
                lambda: audit_memory(payload, store=store, config=config)
            )
        )

    @app.post("/admin/sync")
    def sync_endpoint(request: dict[str, object]) -> dict[str, object]:
        payload = attach_runtime_metadata(
            request, source_surface="http", action="sync_storage"
        )
        return _guard_request_errors(
            lambda: _guard_admin(
                lambda: sync_storage(payload, store=store, config=config)
            )
        )

    @app.post("/ingest/slack")
    async def slack_ingest_endpoint(request: Request) -> dict[str, object]:
        payload = await _parse_json_or_form_payload(request)
        return _guard_request_errors(
            lambda: ingest_slack_event(payload, store=store, config=config)
        )

    @app.post("/ingest/discord")
    def discord_ingest_endpoint(request: dict[str, object]) -> dict[str, object]:
        return _guard_request_errors(
            lambda: ingest_discord_event(request, store=store, config=config)
        )

    @app.post("/summaries/run")
    def run_summaries_endpoint(request: dict[str, object]) -> dict[str, object]:
        payload = attach_runtime_metadata(
            request, source_surface="http", action="run_background_summaries"
        )
        return _guard_request_errors(
            lambda: _guard_summaries(
                lambda: _guard_feature(
                    lambda: run_background_summaries(
                        payload,
                        store=store,
                        config=config,
                    ),
                    enabled=config.enable_background_summarization,
                    message="Background summarization is disabled",
                )
            )
        )


def _register_dashboard_routes(
    app: FastAPI,
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
) -> None:
    _register_dashboard_read_routes(
        app,
        store=store,
        config=config,
        semantic_ranker=semantic_ranker,
    )
    _register_dashboard_admin_routes(app, store=store, config=config)


def _register_dashboard_read_routes(
    app: FastAPI,
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
) -> None:
    @app.get("/dashboard/assets/reference.css")
    def dashboard_stylesheet_endpoint() -> Response:
        return _guard_dashboard(
            lambda: Response(
                content=render_dashboard_stylesheet(),
                media_type="text/css",
            ),
            enabled=config.enable_dashboard,
        )

    @app.get("/dashboard/data")
    def dashboard_data_endpoint(
        bucket: str | None = None, brain_id: str | None = None
    ) -> dict[str, object]:
        return _guard_dashboard(
            lambda: build_dashboard_data(
                store=store, config=config, bucket_filter=bucket, brain_id=brain_id
            ),
            enabled=config.enable_dashboard,
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_endpoint(
        bucket: str | None = None, brain_id: str | None = None
    ) -> str:
        data = _guard_dashboard(
            lambda: build_dashboard_data(
                store=store, config=config, bucket_filter=bucket, brain_id=brain_id
            ),
            enabled=config.enable_dashboard,
        )
        return render_dashboard_page(
            data=data,
            config=config,
            capture_result=None,
            query_result=None,
            briefing_result=None,
            report_result=None,
            brain_result=None,
            session_result=None,
            protocol_result=None,
            admin_result=None,
            active_brain_id=str(
                data.get("active_brain_id") or brain_id or config.default_namespace
            ),
        )

    @app.post("/dashboard/capture", response_class=HTMLResponse)
    async def dashboard_capture_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        capture_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: capture_memory(payload, store=store, config=config),
                enabled=config.enable_dashboard,
            )
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            capture_result=capture_result,
        )

    @app.post("/dashboard/query", response_class=HTMLResponse)
    async def dashboard_query_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        query_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: query_memory(
                    payload,
                    store=store,
                    config=config,
                    semantic_ranker=semantic_ranker,
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            query_result=query_result,
        )

    @app.post("/dashboard/briefing", response_class=HTMLResponse)
    async def dashboard_briefing_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        briefing_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: generate_briefing(
                    payload,
                    store=store,
                    config=config,
                    semantic_ranker=semantic_ranker,
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            briefing_result=briefing_result,
        )

    @app.post("/dashboard/report", response_class=HTMLResponse)
    async def dashboard_report_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        report_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: _dashboard_report_result(
                    payload,
                    store=store,
                    config=config,
                    semantic_ranker=semantic_ranker,
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            report_result=report_result,
        )

    @app.post("/dashboard/brain/create", response_class=HTMLResponse)
    async def dashboard_brain_create_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        brain_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: create_brain(
                    payload,
                    store=store,
                    default_allowed_buckets=config.allowed_buckets,
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            brain_result=brain_result,
        )

    @app.post("/dashboard/brain/archive", response_class=HTMLResponse)
    async def dashboard_brain_archive_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        brain_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: archive_brain(payload, store=store),
                enabled=config.enable_dashboard,
            )
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            brain_result=brain_result,
        )

    @app.post("/dashboard/session/resume", response_class=HTMLResponse)
    async def dashboard_session_resume_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        session_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: resume_session(payload, store=store, config=config),
                enabled=config.enable_dashboard,
            )
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            session_result=session_result,
        )

    @app.post("/dashboard/protocol/run", response_class=HTMLResponse)
    async def dashboard_protocol_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        protocol_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: run_protocol(
                    payload,
                    store=store,
                    config=config,
                    semantic_ranker=semantic_ranker,
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            protocol_result=protocol_result,
        )


def _register_dashboard_admin_routes(
    app: FastAPI,
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
) -> None:
    @app.post("/dashboard/admin/update", response_class=HTMLResponse)
    async def dashboard_update_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        admin_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: _guard_admin(
                    lambda: update_memory(payload, store=store, config=config)
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            admin_result=admin_result,
        )

    @app.post("/dashboard/admin/reindex", response_class=HTMLResponse)
    async def dashboard_reindex_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        admin_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: _guard_admin(
                    lambda: reindex_memory(payload, store=store, config=config)
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            admin_result=admin_result,
        )

    @app.post("/dashboard/admin/audit", response_class=HTMLResponse)
    async def dashboard_audit_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        admin_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: _guard_admin(
                    lambda: audit_memory(payload, store=store, config=config)
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            admin_result=admin_result,
        )

    @app.post("/dashboard/admin/delete", response_class=HTMLResponse)
    async def dashboard_delete_endpoint(request: Request) -> str:
        payload = await _parse_form_payload(request)
        admin_result = _guard_request_errors(
            lambda: _guard_dashboard(
                lambda: _guard_admin(
                    lambda: delete_memory(payload, store=store, config=config)
                ),
                enabled=config.enable_dashboard,
            ),
        )
        return _render_dashboard_response(
            payload,
            store=store,
            config=config,
            admin_result=admin_result,
        )


def _guard_admin(fn: Callable[[], ResponseT]) -> ResponseT:
    """Translate admin permission errors into HTTP responses."""
    try:
        return fn()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _guard_request_errors(fn: Callable[[], ResponseT]) -> ResponseT:
    """Translate request validation and lookup failures into HTTP responses."""
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        detail = str(exc.args[0]) if exc.args else "Not found"
        raise HTTPException(status_code=404, detail=detail) from exc


def _guard_dashboard(fn: Callable[[], ResponseT], *, enabled: bool) -> ResponseT:
    """Guard dashboard-only routes behind the configured feature flag."""
    try:
        return _guard_feature(fn, enabled=enabled, message="Dashboard is disabled")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _guard_summaries(fn: Callable[[], ResponseT]) -> ResponseT:
    """Translate summary permission errors into HTTP responses."""
    try:
        return fn()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _guard_reporting(fn: Callable[[], ResponseT]) -> ResponseT:
    """Translate reporting permission errors into HTTP responses."""
    try:
        return fn()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _guard_feature(
    fn: Callable[[], ResponseT], *, enabled: bool, message: str
) -> ResponseT:
    """Run a handler only when the corresponding feature flag is enabled."""
    if not enabled:
        raise PermissionError(message)
    return fn()


def _render_dashboard_response(
    payload: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    capture_result: dict[str, object] | None = None,
    query_result: dict[str, object] | None = None,
    briefing_result: dict[str, object] | None = None,
    report_result: dict[str, object] | None = None,
    brain_result: dict[str, object] | None = None,
    session_result: dict[str, object] | None = None,
    protocol_result: dict[str, object] | None = None,
    admin_result: dict[str, object] | None = None,
) -> str:
    data = _guard_dashboard(
        lambda: build_dashboard_data(
            store=store,
            config=config,
            bucket_filter=_optional_str(payload.get("bucket_filter")),
            brain_id=_optional_str(payload.get("brain_id"))
            or _optional_str(payload.get("namespace")),
        ),
        enabled=config.enable_dashboard,
    )
    return render_dashboard_page(
        data=data,
        config=config,
        capture_result=capture_result,
        query_result=query_result,
        briefing_result=briefing_result,
        report_result=report_result,
        brain_result=brain_result,
        session_result=session_result,
        protocol_result=protocol_result,
        admin_result=admin_result,
        active_brain_id=_resolve_dashboard_brain_id(payload, config),
    )


async def _parse_form_payload(request: Request) -> dict[str, object]:
    raw = (await request.body()).decode("utf-8")
    form_values = parse_qs(raw, keep_blank_values=False)
    payload = _build_dashboard_payload(
        request.url.path, form_values, request.app.state.config
    )
    bucket_filter = _first_value(form_values, "bucket_filter")
    if bucket_filter:
        payload["bucket_filter"] = bucket_filter
    return {key: value for key, value in payload.items() if value is not None}


async def _parse_json_or_form_payload(request: Request) -> dict[str, object]:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400, detail="request body must be an object"
            )
        return payload
    if "application/x-www-form-urlencoded" in content_type:
        raw = (await request.body()).decode("utf-8")
        form_values = parse_qs(raw, keep_blank_values=False)
        return {
            key: _first_value(form_values, key)
            for key, values in form_values.items()
            if values
        }
    raise HTTPException(
        status_code=415,
        detail="unsupported media type for this endpoint",
    )


def _build_dashboard_payload(
    path: str,
    form_values: FormValues,
    config: NeuroCoreConfig,
) -> dict[str, object]:
    for suffix, builder in (
        ("/brain/create", _build_brain_create_form_payload),
        ("/brain/archive", _build_brain_archive_form_payload),
        ("/capture", _build_capture_form_payload),
        ("/query", _build_query_form_payload),
        ("/briefing", _build_briefing_form_payload),
        ("/report", _build_report_form_payload),
        ("/protocol/run", _build_protocol_form_payload),
        ("/session/resume", _build_session_resume_form_payload),
        ("/update", _build_update_form_payload),
        ("/reindex", _build_reindex_form_payload),
        ("/audit", _build_audit_form_payload),
        ("/delete", _build_delete_form_payload),
    ):
        if path.endswith(suffix):
            return builder(form_values, config)
    return _build_query_form_payload(form_values, config)


def _build_brain_create_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    brain_id = _first_value(form_values, "brain_id") or _form_namespace(
        form_values, config
    )
    namespace = _first_value(form_values, "namespace") or brain_id
    owner = _first_value(form_values, "owner")
    tags = _split_csv_values(_first_value(form_values, "tags"))
    return {
        "brain_id": brain_id,
        "namespace": namespace,
        "display_name": _first_value(form_values, "display_name") or brain_id,
        "description": _first_value(form_values, "description") or "",
        "owner": owner,
        "tags": tags,
        "default_allowed_buckets": _split_csv_values(
            _first_value(form_values, "default_allowed_buckets")
        )
        or list(config.allowed_buckets),
    }


def _build_brain_archive_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    del config
    return {
        "brain_id": _first_value(form_values, "brain_id"),
        "reason": _first_value(form_values, "reason") or "dashboard archive",
    }


def _build_capture_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    brain_id = _first_value(form_values, "brain_id") or _form_namespace(
        form_values, config
    )
    return {
        "brain_id": brain_id,
        "namespace": _first_value(form_values, "namespace") or None,
        "bucket": _first_value(form_values, "bucket"),
        "sensitivity": _first_value(form_values, "sensitivity"),
        "content": _first_value(form_values, "content"),
        "content_format": _first_value(form_values, "content_format") or "markdown",
        "source_type": _first_value(form_values, "source_type") or "note",
        "title": _first_value(form_values, "title") or None,
    }


def _build_query_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    return _build_query_request_from_form(form_values, config)


def _build_briefing_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    namespace = _form_namespace(form_values, config)
    return {
        "brain_id": _first_value(form_values, "brain_id") or namespace,
        "query_request": _build_query_request_from_form(form_values, config),
        "include_operator_hints": True,
    }


def _build_report_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    namespace = _form_namespace(form_values, config)
    return {
        "brain_id": _first_value(form_values, "brain_id") or namespace,
        "objective": _first_value(form_values, "objective")
        or "Generate a durable memory report.",
        "query_request": _build_query_request_from_form(form_values, config),
        "max_items": 5,
    }


def _build_update_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    return {
        "id": _first_value(form_values, "id"),
        "mode": _first_value(form_values, "mode") or "replace_content",
        "patch": {
            "content": _first_value(form_values, "content"),
            "title": _first_value(form_values, "title"),
        },
        "actor": _first_value(form_values, "actor") or "dashboard",
    }


def _build_delete_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    ids = _first_value(form_values, "ids") or ""
    return {
        "ids": [value.strip() for value in ids.split(",") if value.strip()],
        "scope": _first_value(form_values, "scope") or "records",
        "mode": _first_value(form_values, "mode") or "soft",
        "reason": _first_value(form_values, "reason") or "dashboard request",
    }


def _build_query_request_from_form(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    allowed_buckets = _form_allowed_buckets(form_values, config)
    return {
        "brain_id": _first_value(form_values, "brain_id")
        or _form_namespace(form_values, config),
        "query_text": _first_value(form_values, "query_text"),
        "namespace": _form_namespace(form_values, config),
        "allowed_buckets": allowed_buckets,
        "sensitivity_ceiling": _first_value(form_values, "sensitivity_ceiling")
        or config.default_sensitivity,
    }


def _form_allowed_buckets(
    form_values: FormValues, config: NeuroCoreConfig
) -> list[str]:
    raw = _first_value(form_values, "allowed_buckets") or ",".join(
        config.allowed_buckets
    )
    return [value.strip() for value in raw.split(",") if value.strip()]


def _form_namespace(form_values: FormValues, config: NeuroCoreConfig) -> str:
    return (
        _first_value(form_values, "namespace")
        or _first_value(form_values, "brain_id")
        or config.default_namespace
    )


def _split_csv_values(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [value.strip() for value in raw.split(",") if value.strip()]


def _build_protocol_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    payload = _build_query_request_from_form(form_values, config)
    payload["name"] = _first_value(form_values, "name") or "resume-brain-v1"
    payload["objective"] = _first_value(form_values, "objective") or None
    payload["max_items"] = int(_first_value(form_values, "max_items") or 8)
    payload["session_id"] = _first_value(form_values, "session_id")
    return {key: value for key, value in payload.items() if value is not None}


def _build_session_resume_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    payload = _build_query_request_from_form(form_values, config)
    payload["session_id"] = _first_value(form_values, "session_id")
    payload["source_client"] = _first_value(form_values, "source_client")
    payload["max_items"] = int(_first_value(form_values, "max_items") or 6)
    return {key: value for key, value in payload.items() if value is not None}


def _build_reindex_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    del config
    ids = _first_value(form_values, "ids") or ""
    return {
        "ids": [value.strip() for value in ids.split(",") if value.strip()],
        "scope": _first_value(form_values, "scope") or "records",
    }


def _build_audit_form_payload(
    form_values: FormValues, config: NeuroCoreConfig
) -> dict[str, object]:
    return {
        "brain_id": _first_value(form_values, "brain_id")
        or _form_namespace(form_values, config),
        "namespace": _form_namespace(form_values, config),
        "allowed_buckets": _form_allowed_buckets(form_values, config),
    }


def _first_value(values: dict[str, list[str]], key: str) -> str | None:
    matches = values.get(key)
    if not matches:
        return None
    value = matches[0].strip()
    return value or None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_dashboard_brain_id(
    payload: dict[str, object], config: NeuroCoreConfig
) -> str:
    return str(
        payload.get("brain_id") or payload.get("namespace") or config.default_namespace
    )


def _dashboard_report_result(
    payload: dict[str, object],
    *,
    store: BaseStore,
    config: NeuroCoreConfig,
    semantic_ranker: object | None,
) -> dict[str, object]:
    try:
        return generate_consensus_report(
            payload,
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        )
    except PermissionError:
        briefing = generate_briefing(
            {
                "brain_id": payload.get("brain_id"),
                "query_request": payload.get("query_request"),
                "include_operator_hints": True,
                "max_items": payload.get("max_items", 5),
            },
            store=store,
            config=config,
            semantic_ranker=semantic_ranker,
        )
        return {
            "mode": "fallback-briefing",
            "report": briefing["briefing"],
            "metadata": briefing.get("metadata", {}),
        }
