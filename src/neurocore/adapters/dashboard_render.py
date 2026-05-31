"""Server-rendered dashboard presentation for the NeuroCore reference app."""

from __future__ import annotations

from html import escape

from neurocore.core.config import NeuroCoreConfig
from neurocore.interfaces.protocols import list_protocols

DASHBOARD_STYLESHEET = """
:root {
  --bg: #f4f1e8;
  --panel: #fffdf8;
  --panel-strong: #f7f3e8;
  --panel-muted: #f0eadb;
  --border: #d7ccb3;
  --border-strong: #b7aa8c;
  --text: #1f241d;
  --text-muted: #586053;
  --brand: #204a36;
  --brand-soft: #dfeee6;
  --accent: #a65b2b;
  --accent-soft: #fbe8dc;
  --danger: #8d2f2f;
  --danger-soft: #f7e3e3;
  --success: #2e6f44;
  --success-soft: #e2f3e8;
  --warning: #8b5a11;
  --warning-soft: #f8edcf;
  --shadow: 0 18px 40px rgba(43, 32, 18, 0.08);
  --radius-xl: 28px;
  --radius-lg: 20px;
  --radius-md: 14px;
  --radius-sm: 10px;
  --space-1: 0.35rem;
  --space-2: 0.55rem;
  --space-3: 0.85rem;
  --space-4: 1.1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  --space-7: 2.8rem;
  --content-width: 1380px;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

html {
  color-scheme: light;
}

body {
  margin: 0;
  background:
    radial-gradient(circle at top left, rgba(242, 227, 193, 0.55), transparent 24rem),
    linear-gradient(180deg, #f7f4ec 0%, #f0ebdf 100%);
  color: var(--text);
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  line-height: 1.45;
}

body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(103, 93, 73, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(103, 93, 73, 0.04) 1px, transparent 1px);
  background-size: 28px 28px;
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.28), transparent 80%);
}

a {
  color: inherit;
}

button,
input,
select,
textarea {
  font: inherit;
}

button,
.button {
  appearance: none;
  border: 0;
  border-radius: 999px;
  background: var(--brand);
  color: #f8fbf9;
  padding: 0.8rem 1.25rem;
  font-weight: 650;
  cursor: pointer;
  transition: transform 160ms ease, box-shadow 160ms ease, opacity 160ms ease;
}

button:hover,
button:focus-visible,
.button:hover,
.button:focus-visible {
  transform: translateY(-1px);
  box-shadow: 0 10px 24px rgba(32, 74, 54, 0.18);
}

button.secondary {
  background: var(--panel-muted);
  color: var(--text);
}

button.danger {
  background: var(--danger);
}

textarea,
input[type="text"],
input[type="search"],
select {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #fffefb;
  color: var(--text);
  padding: 0.85rem 0.95rem;
  min-height: 2.9rem;
}

textarea {
  min-height: 8rem;
  resize: vertical;
}

input:focus,
select:focus,
textarea:focus,
details summary:focus-visible {
  outline: 2px solid rgba(32, 74, 54, 0.18);
  outline-offset: 2px;
  border-color: var(--brand);
}

.dashboard-shell {
  width: min(var(--content-width), calc(100vw - 2rem));
  margin: 0 auto;
  padding: var(--space-6) 0 var(--space-7);
  position: relative;
  z-index: 1;
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(21rem, 1fr);
  gap: var(--space-5);
  background: linear-gradient(145deg, rgba(32, 74, 54, 0.97), rgba(20, 44, 33, 0.96));
  color: #f7faf8;
  border-radius: var(--radius-xl);
  padding: var(--space-6);
  box-shadow: var(--shadow);
}

.eyebrow {
  margin: 0 0 var(--space-2);
  font-size: 0.82rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #c6dfd1;
}

.hero h1 {
  margin: 0 0 var(--space-3);
  font-family: "IBM Plex Serif", "Georgia", serif;
  font-size: clamp(2rem, 4vw, 3.1rem);
  line-height: 1.05;
}

.hero-copy,
.hero-note {
  margin: 0;
  color: #d7e8df;
  max-width: 48rem;
}

.hero-main {
  display: grid;
  gap: var(--space-4);
}

.badge-row,
.flow-row,
.metrics-grid,
.workflow-grid,
.intel-grid,
.tools-grid,
.status-grid {
  display: grid;
  gap: var(--space-4);
}

.badge-row {
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
}

.badge-card,
.metric-card,
.panel,
.workflow-card,
.tool-card,
.context-card,
.result-card,
.subcard {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}

.badge-card,
.metric-card,
.panel,
.workflow-card,
.tool-card,
.context-card,
.result-card,
.subcard {
  padding: var(--space-5);
}

.badge-card {
  background: rgba(247, 250, 248, 0.09);
  border-color: rgba(247, 250, 248, 0.16);
  padding: var(--space-4);
}

.context-card {
  background: rgba(248, 251, 249, 0.92);
  color: var(--text);
}

.context-card h2,
.workflow-card h3,
.panel h3,
.tool-card h3,
.subcard h4,
.result-card h4 {
  margin: 0;
}

.context-card p,
.workflow-card p,
.panel p,
.tool-card p,
.subcard p,
.result-card p,
.empty-state,
.control-hint,
.section-copy,
.metric-label,
.stat-label,
.detail-meta {
  color: var(--text-muted);
}

.context-card form,
.workflow-card form,
.tool-card form,
.subcard form {
  display: grid;
  gap: var(--space-3);
}

.flow-row {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-top: var(--space-2);
}

.flow-step {
  background: rgba(247, 250, 248, 0.08);
  border: 1px solid rgba(247, 250, 248, 0.12);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
}

.flow-step strong {
  display: block;
  margin-bottom: var(--space-1);
}

.status-grid {
  grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
  margin-top: var(--space-4);
}

.status-chip,
.pill {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  width: fit-content;
  border-radius: 999px;
  padding: 0.38rem 0.8rem;
  font-size: 0.88rem;
  font-weight: 600;
  background: var(--panel-muted);
  color: var(--text);
}

.status-chip::before,
.pill::before {
  content: "";
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 999px;
  background: currentColor;
}

.tone-good {
  background: var(--success-soft);
  color: var(--success);
}

.tone-warn {
  background: var(--warning-soft);
  color: var(--warning);
}

.tone-danger {
  background: var(--danger-soft);
  color: var(--danger);
}

.tone-neutral {
  background: var(--panel-muted);
  color: var(--text);
}

.metrics-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-top: var(--space-5);
}

.metric-card {
  box-shadow: var(--shadow);
}

.metric-value {
  margin: var(--space-3) 0 0;
  font-family: "IBM Plex Serif", "Georgia", serif;
  font-size: clamp(2rem, 3vw, 2.8rem);
  line-height: 1;
}

.metric-label {
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.78rem;
}

.metric-note {
  margin: var(--space-2) 0 0;
  font-size: 0.92rem;
}

.dashboard-main {
  display: grid;
  gap: var(--space-6);
  margin-top: var(--space-6);
}

.section-header {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.section-header h2 {
  margin: 0;
  font-family: "IBM Plex Serif", "Georgia", serif;
  font-size: 1.65rem;
}

.section-copy {
  margin: 0;
  max-width: 42rem;
}

.workflow-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.workflow-card,
.tool-card {
  box-shadow: var(--shadow);
}

.workflow-card header,
.panel header,
.tool-card > header,
.subcard > header {
  display: grid;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.card-kicker {
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.8rem;
  color: var(--accent);
  font-weight: 700;
}

.control-grid {
  display: grid;
  gap: var(--space-3);
}

.control {
  display: grid;
  gap: var(--space-2);
}

.control-label {
  font-size: 0.94rem;
  font-weight: 620;
}

.control-hint {
  margin: 0;
  font-size: 0.84rem;
}

.details-block {
  margin-top: var(--space-3);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--panel-strong);
  overflow: hidden;
}

.details-block[open] {
  border-style: solid;
}

.details-block summary {
  list-style: none;
  cursor: pointer;
  padding: var(--space-3) var(--space-4);
  font-weight: 620;
}

.details-block summary::-webkit-details-marker {
  display: none;
}

.details-body {
  padding: 0 var(--space-4) var(--space-4);
  display: grid;
  gap: var(--space-3);
}

.details-meta {
  margin: 0;
  font-size: 0.88rem;
}

.result-card {
  margin-top: var(--space-4);
  background: #fffefa;
}

.result-card pre {
  margin: 0;
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: #f6f1e6;
  border: 1px solid var(--border);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.result-list,
.panel-list,
.detail-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: var(--space-3);
}

.result-list li,
.panel-list li,
.detail-list li {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--panel-strong);
  padding: var(--space-3) var(--space-4);
}

.intel-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.panel {
  min-height: 100%;
}

.panel-list strong,
.panel-list .pill {
  margin-right: var(--space-2);
}

.panel-meta {
  display: block;
  margin-top: var(--space-2);
  font-size: 0.9rem;
}

.panel-copy {
  display: block;
  margin-top: var(--space-2);
  color: var(--text);
}

.tools-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.tool-stack,
.admin-stack {
  display: grid;
  gap: var(--space-4);
}

.subcard.danger-zone,
.tool-card.danger-zone {
  background: #fff9f7;
  border-color: #e4b7b7;
}

.empty-state {
  margin: 0;
  padding: var(--space-4);
  border-radius: var(--radius-md);
  border: 1px dashed var(--border-strong);
  background: var(--panel-strong);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 1120px) {
  .hero,
  .workflow-grid,
  .tools-grid,
  .intel-grid {
    grid-template-columns: 1fr;
  }

  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 960px) {
  .dashboard-shell {
    width: min(var(--content-width), calc(100vw - 1rem));
    padding-top: 0.5rem;
  }

  .hero {
    padding: var(--space-5);
  }

  .flow-row,
  .status-grid,
  .badge-row {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .metrics-grid {
    grid-template-columns: 1fr;
  }

  .badge-card,
  .metric-card,
  .panel,
  .workflow-card,
  .tool-card,
  .context-card,
  .result-card,
  .subcard {
    padding: var(--space-4);
  }

  button {
    width: 100%;
  }
}
"""


def render_dashboard_stylesheet() -> str:
    """Return the dedicated dashboard stylesheet."""
    return DASHBOARD_STYLESHEET


def render_reference_app(
    *,
    data: dict[str, object],
    config: NeuroCoreConfig,
    capture_result: dict[str, object] | None,
    query_result: dict[str, object] | None,
    briefing_result: dict[str, object] | None,
    report_result: dict[str, object] | None,
    brain_result: dict[str, object] | None,
    session_result: dict[str, object] | None,
    protocol_result: dict[str, object] | None,
    admin_result: dict[str, object] | None,
    active_brain_id: str,
) -> str:
    """Render the reference dashboard page."""
    stats = data["stats"]
    production = data["production_backend"]
    storage = data.get("storage_backend", {})
    reporting_status = data.get("reporting_status", {})
    available_buckets = list(data.get("available_buckets", []))
    active_bucket = str(data.get("active_bucket_filter") or "")
    brain_id = str(
        data.get("active_brain_id") or active_brain_id or config.default_namespace
    )
    active_namespace = str(
        data.get("active_namespace") or active_brain_id or config.default_namespace
    )
    default_bucket = active_bucket or (
        str(available_buckets[0]) if available_buckets else "research"
    )
    default_sensitivity = config.default_sensitivity
    allowed_bucket_values = ",".join(str(bucket) for bucket in available_buckets)
    brains = list(data.get("brains", []))
    brain_meta = (
        data.get("brain_metadata")
        if isinstance(data.get("brain_metadata"), dict)
        else {}
    )
    brain_display = str(brain_meta.get("display_name") or brain_id)
    brain_description = str(brain_meta.get("description") or "")
    current_scope = (
        f"Showing the {escape(active_bucket)} bucket for {brain_display}."
        if active_bucket
        else f"Showing all buckets for {brain_display}."
    )

    return f"""
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>NeuroCore Reference App</title>
        <link rel="icon" href="data:," />
        <link rel="stylesheet" href="/dashboard/assets/reference.css" />
      </head>
      <body>
        <div class="dashboard-shell">
          <header class="hero" id="dashboard-header">
            <div class="hero-main">
              <div>
                <p class="eyebrow">NeuroCore Reference App</p>
                <h1>Durable memory, shaped for operators and demos.</h1>
                <p class="hero-copy">{current_scope}</p>
                <p class="hero-note">
                  Use the primary workflow cards to capture context, retrieve signal, generate briefings,
                  and produce a report without leaving the page.
                </p>
              </div>
              <div class="flow-row" aria-label="Recommended flow">
                {_render_flow_step("Capture", "Write the key note, event, or finding.")}
                {_render_flow_step("Search", "Pull back the most relevant stored context.")}
                {_render_flow_step("Briefing", "Generate a quick handoff or operator summary.")}
                {_render_flow_step("Report", "Turn durable memory into a shareable deliverable.")}
              </div>
              <div class="badge-row">
                {_render_badge_card("Active brain", brain_display, _status_tone("neutral"))}
                {_render_badge_card("Active namespace", active_namespace, _status_tone("neutral"))}
                {_render_badge_card("Current bucket", active_bucket or "all buckets", _status_tone("neutral"))}
              </div>
            </div>
            <aside class="context-card">
              <header>
                <h2>View context</h2>
                <p>{escape(brain_description or "Switch the active brain or focus on a single bucket without changing dashboard behavior.")}</p>
              </header>
              <form method="get" action="/dashboard">
                {_render_control(
                    "Brain ID",
                    _render_text_input(
                        "brain_id",
                        value=brain_id,
                        attributes='list="dashboard-brain-options"',
                    ),
                    hint="Existing brains are suggested, but you can type an id directly.",
                )}
                <datalist id="dashboard-brain-options">
                  {_render_brain_datalist(brains, brain_id)}
                </datalist>
                {_render_control(
                    "Bucket filter",
                    _render_bucket_select("bucket", available_buckets, active_bucket),
                    hint="This changes what the dashboard surfaces and what new captures default to.",
                )}
                <button type="submit">Update view</button>
              </form>
              <div class="status-grid">
                {_render_status_card(
                    "Storage backend",
                    str(storage.get("mode", "in_memory")),
                    f"read={storage.get('read_preference', 'local')} · local_degraded={storage.get('local_degraded', False)}",
                    _status_tone(
                        "degraded" if storage.get("local_degraded") else str(storage.get("mode", "in_memory"))
                    ),
                )}
                {_render_status_card(
                    "Production backend",
                    str(production.get("provider", "none")),
                    str(production.get("status", "unknown")),
                    _status_tone(str(production.get("status", "unknown"))),
                )}
                {_render_status_card(
                    "Reporting",
                    str(reporting_status.get("provider", "none")),
                    str(reporting_status.get("status", "fallback-only")),
                    _status_tone(str(reporting_status.get("status", "fallback-only"))),
                )}
              </div>
            </aside>
          </header>

          <section aria-label="Dashboard metrics" class="metrics-grid">
            {_render_metric_card("Records", stats["record_count"], "Non-sealed memory records in scope.")}
            {_render_metric_card("Documents", stats["document_count"], "Durable documents available for review.")}
            {_render_metric_card("Summarized", stats["summarized_document_count"], "Documents that already include a summary.")}
            {_render_metric_card("Archived", stats["archived_document_count"], "Documents kept for history but archived.")}
          </section>

          <main class="dashboard-main">
            <section id="dashboard-workflows">
              <div class="section-header">
                <div>
                  <h2>Primary workflows</h2>
                  <p class="section-copy">
                    These cards keep the common operator loop up front. Advanced runtime fields are still available,
                    but they are collapsed until needed.
                  </p>
                </div>
              </div>
              <div class="workflow-grid">
                {_render_capture_card(
                    brain_id=brain_id,
                    active_namespace=active_namespace,
                    default_bucket=default_bucket,
                    default_sensitivity=default_sensitivity,
                    bucket_filter=active_bucket,
                    result=capture_result,
                )}
                {_render_search_card(
                    brain_id=brain_id,
                    active_namespace=active_namespace,
                    allowed_bucket_values=allowed_bucket_values,
                    default_sensitivity=default_sensitivity,
                    bucket_filter=active_bucket,
                    result=query_result,
                )}
                {_render_briefing_card(
                    brain_id=brain_id,
                    active_namespace=active_namespace,
                    allowed_bucket_values=allowed_bucket_values,
                    default_sensitivity=default_sensitivity,
                    bucket_filter=active_bucket,
                    result=briefing_result,
                )}
                {_render_report_card(
                    brain_id=brain_id,
                    active_namespace=active_namespace,
                    allowed_bucket_values=allowed_bucket_values,
                    default_sensitivity=default_sensitivity,
                    bucket_filter=active_bucket,
                    result=report_result,
                )}
              </div>
            </section>

            <section id="dashboard-intelligence">
              <div class="section-header">
                <div>
                  <h2>Shared memory snapshot</h2>
                  <p class="section-copy">
                    Read-only panels surface what matters now, what changed recently, and how the current workspace is wired.
                  </p>
                </div>
              </div>
              <div class="intel-grid">
                {_render_panel(
                    "What Matters Now",
                    "Prioritized feed for the current brain and bucket scope.",
                    _render_prioritized_feed(list(data.get("prioritized_feed", []))),
                )}
                {_render_panel(
                    "Recent Memory",
                    "Newest durable memory records in scope.",
                    _render_record_list(list(data.get("recent_records", []))),
                )}
                {_render_panel(
                    "Recent Documents",
                    "Documents with the most recent durable updates.",
                    _render_document_list(list(data.get("recent_documents", []))),
                )}
                {_render_panel(
                    "Brains",
                    "Known brain workspaces and which one is active.",
                    _render_brain_list(brains, brain_id),
                )}
                {_render_panel(
                    "Connector Status",
                    "Available integration surfaces and their operating state.",
                    _render_connector_list(list(data.get("connectors", []))),
                )}
                {_render_panel(
                    "Recent Audit Activity",
                    "The latest audit trail entries recorded by the store.",
                    _render_audit_list(list(data.get("recent_audit_events", []))),
                )}
              </div>
            </section>

            <section id="dashboard-tools">
              <div class="section-header">
                <div>
                  <h2>Advanced tools</h2>
                  <p class="section-copy">
                    Less frequent operations stay available here without crowding the primary workflow loop.
                  </p>
                </div>
              </div>
              <div class="tools-grid">
                {_render_brain_management_card(
                    brain_id=brain_id,
                    allowed_bucket_values=allowed_bucket_values,
                    result=brain_result,
                )}
                {_render_protocol_card(
                    brain_id=brain_id,
                    active_namespace=active_namespace,
                    allowed_bucket_values=allowed_bucket_values,
                    default_sensitivity=default_sensitivity,
                    bucket_filter=active_bucket,
                    result=protocol_result,
                )}
                {_render_session_resume_card(
                    brain_id=brain_id,
                    active_namespace=active_namespace,
                    allowed_bucket_values=allowed_bucket_values,
                    default_sensitivity=default_sensitivity,
                    bucket_filter=active_bucket,
                    result=session_result,
                )}
                {_render_admin_card(
                    enabled=config.enable_admin_surface,
                    brain_id=brain_id,
                    active_namespace=active_namespace,
                    allowed_bucket_values=allowed_bucket_values,
                    bucket_filter=active_bucket,
                    result=admin_result,
                )}
              </div>
            </section>
          </main>
        </div>
      </body>
    </html>
    """


def _render_flow_step(title: str, copy: str) -> str:
    return f"""
      <div class="flow-step">
        <strong>{escape(title)}</strong>
        <span>{escape(copy)}</span>
      </div>
    """


def _render_badge_card(label: str, value: str, tone: str) -> str:
    return f"""
      <div class="badge-card">
        <p class="metric-label">{escape(label)}</p>
        <div class="status-chip {tone}">{escape(value)}</div>
      </div>
    """


def _render_status_card(label: str, value: str, detail: str, tone: str) -> str:
    return f"""
      <div class="badge-card">
        <p class="metric-label">{escape(label)}</p>
        <div class="status-chip {tone}">{escape(value)}</div>
        <p class="metric-note">{escape(detail)}</p>
      </div>
    """


def _render_metric_card(label: str, value: object, note: str) -> str:
    return f"""
      <article class="metric-card">
        <p class="metric-label">{escape(label)}</p>
        <p class="metric-value">{escape(str(value))}</p>
        <p class="metric-note">{escape(note)}</p>
      </article>
    """


def _render_capture_card(
    *,
    brain_id: str,
    active_namespace: str,
    default_bucket: str,
    default_sensitivity: str,
    bucket_filter: str,
    result: dict[str, object] | None,
) -> str:
    return _render_workflow_card(
        title="Capture",
        kicker="Step 1",
        copy="Capture a durable note quickly, then open advanced options only if the defaults need to change.",
        action="/dashboard/capture",
        button_label="Capture Memory",
        essential_fields="".join(
            (
                _render_control("Title", _render_text_input("title")),
                _render_control(
                    "Content",
                    _render_textarea(
                        "content",
                        placeholder="Summarize the key observation, evidence, or next action.",
                    ),
                ),
            )
        ),
        advanced_fields=_render_context_fields(
            brain_id=brain_id,
            active_namespace=active_namespace,
            bucket_value=default_bucket,
            default_sensitivity=default_sensitivity,
            extra_fields="".join(
                (
                    _render_control(
                        "Content format",
                        _render_text_input("content_format", value="markdown"),
                    ),
                    _render_control(
                        "Source type",
                        _render_text_input("source_type", value="note"),
                    ),
                )
            ),
        ),
        hidden_fields=_render_hidden_input("bucket_filter", bucket_filter),
        result_title="Capture Result",
        result=result,
    )


def _render_search_card(
    *,
    brain_id: str,
    active_namespace: str,
    allowed_bucket_values: str,
    default_sensitivity: str,
    bucket_filter: str,
    result: dict[str, object] | None,
) -> str:
    return _render_workflow_card(
        title="Search",
        kicker="Step 2",
        copy="Search durable memory using the active context defaults, then refine buckets or sensitivity only when the result set needs it.",
        action="/dashboard/query",
        button_label="Search Memory",
        essential_fields=_render_control(
            "Query text",
            _render_text_input(
                "query_text",
                attributes='placeholder="Find the memory I need to act on."',
            ),
        ),
        advanced_fields=_render_query_context_fields(
            brain_id=brain_id,
            active_namespace=active_namespace,
            allowed_bucket_values=allowed_bucket_values,
            default_sensitivity=default_sensitivity,
        ),
        hidden_fields=_render_hidden_input("bucket_filter", bucket_filter),
        result_title="Search Result",
        result=result,
    )


def _render_briefing_card(
    *,
    brain_id: str,
    active_namespace: str,
    allowed_bucket_values: str,
    default_sensitivity: str,
    bucket_filter: str,
    result: dict[str, object] | None,
) -> str:
    return _render_workflow_card(
        title="Briefing",
        kicker="Step 3",
        copy="Generate an operator-friendly summary from the same memory context without building a full report.",
        action="/dashboard/briefing",
        button_label="Generate Briefing",
        essential_fields=_render_control(
            "Query text",
            _render_text_input(
                "query_text",
                attributes='placeholder="Focus the briefing on the question or topic that matters now."',
            ),
        ),
        advanced_fields=_render_query_context_fields(
            brain_id=brain_id,
            active_namespace=active_namespace,
            allowed_bucket_values=allowed_bucket_values,
            default_sensitivity=default_sensitivity,
        ),
        hidden_fields=_render_hidden_input("bucket_filter", bucket_filter),
        result_title="Briefing Result",
        result=result,
    )


def _render_report_card(
    *,
    brain_id: str,
    active_namespace: str,
    allowed_bucket_values: str,
    default_sensitivity: str,
    bucket_filter: str,
    result: dict[str, object] | None,
) -> str:
    return _render_workflow_card(
        title="Report",
        kicker="Step 4",
        copy="Generate a report from durable memory. If consensus reporting is unavailable, the flow falls back to a synthesized briefing.",
        action="/dashboard/report",
        button_label="Generate Report",
        essential_fields="".join(
            (
                _render_control(
                    "Objective",
                    _render_text_input(
                        "objective",
                        value="Generate a durable memory report.",
                    ),
                ),
                _render_control(
                    "Query text",
                    _render_text_input(
                        "query_text",
                        attributes='placeholder="Optional: narrow the report to a topic or incident."',
                    ),
                ),
            )
        ),
        advanced_fields=_render_query_context_fields(
            brain_id=brain_id,
            active_namespace=active_namespace,
            allowed_bucket_values=allowed_bucket_values,
            default_sensitivity=default_sensitivity,
        ),
        hidden_fields=_render_hidden_input("bucket_filter", bucket_filter),
        result_title="Report Result",
        result=result,
    )


def _render_brain_management_card(
    *,
    brain_id: str,
    allowed_bucket_values: str,
    result: dict[str, object] | None,
) -> str:
    return f"""
      <article class="tool-card">
        <header>
          <p class="card-kicker">Workspace</p>
          <h3>Brain management</h3>
          <p>Create, refresh, or archive a brain without leaving the dashboard.</p>
        </header>
        <div class="tool-stack">
          <section class="subcard">
            <header>
              <h4>Create or refresh brain</h4>
            </header>
            <form method="post" action="/dashboard/brain/create">
              {_render_control("Brain ID", _render_text_input("brain_id", value=brain_id))}
              {_render_control("Display name", _render_text_input("display_name", value=brain_id))}
              {_render_details(
                  "Advanced options",
                  "".join(
                      (
                          _render_control("Namespace", _render_text_input("namespace", value=brain_id)),
                          _render_control(
                              "Description",
                              _render_text_input("description", value="OpenBrain workspace"),
                          ),
                          _render_control("Owner", _render_text_input("owner", value="dashboard")),
                          _render_control(
                              "Tags",
                              _render_text_input("tags", value="openbrain,reference-app"),
                          ),
                          _render_control(
                              "Default buckets",
                              _render_text_input(
                                  "default_allowed_buckets",
                                  value=allowed_bucket_values,
                              ),
                          ),
                      )
                  ),
                  meta="The default values match the current reference-app workflow.",
              )}
              <button type="submit">Create / Refresh Brain</button>
            </form>
          </section>
          <section class="subcard">
            <header>
              <h4>Archive brain</h4>
            </header>
            <form method="post" action="/dashboard/brain/archive">
              {_render_control("Brain ID", _render_text_input("brain_id", value=brain_id))}
              {_render_control(
                  "Reason",
                  _render_text_input("reason", value="dashboard archive"),
              )}
              <button type="submit" class="secondary">Archive Brain</button>
            </form>
          </section>
        </div>
        {_render_result_block("Brain Result", result)}
      </article>
    """


def _render_protocol_card(
    *,
    brain_id: str,
    active_namespace: str,
    allowed_bucket_values: str,
    default_sensitivity: str,
    bucket_filter: str,
    result: dict[str, object] | None,
) -> str:
    protocol_options = "".join(
        _render_protocol_option(protocol) for protocol in list_protocols()
    )
    return f"""
      <article class="tool-card">
        <header>
          <p class="card-kicker">Operator flow</p>
          <h3>Protocol launcher</h3>
          <p>Run a reusable protocol against the active brain when the standard cards are too narrow.</p>
        </header>
        <form method="post" action="/dashboard/protocol/run">
          {_render_control("Protocol", _render_select("name", protocol_options))}
          {_render_control(
              "Query text",
              _render_text_input("query_text", value="critical memory and next actions"),
          )}
          {_render_control(
              "Objective",
              _render_text_input(
                  "objective",
                  value="Summarize the most relevant memory and next actions.",
              ),
          )}
          {_render_details(
              "Advanced options",
              "".join(
                  (
                      _render_control("Brain ID", _render_text_input("brain_id", value=brain_id)),
                      _render_control("Namespace", _render_text_input("namespace", value=active_namespace)),
                      _render_control("Session ID", _render_text_input("session_id", value="default-session")),
                      _render_control(
                          "Allowed buckets",
                          _render_text_input("allowed_buckets", value=allowed_bucket_values),
                      ),
                      _render_control(
                          "Sensitivity ceiling",
                          _render_text_input("sensitivity_ceiling", value=default_sensitivity),
                      ),
                  )
              ),
              meta="Protocols still use the same underlying query request and report semantics.",
          )}
          {_render_hidden_input("bucket_filter", bucket_filter)}
          <button type="submit">Run Protocol</button>
        </form>
        {_render_result_block("Protocol Result", result)}
      </article>
    """


def _render_session_resume_card(
    *,
    brain_id: str,
    active_namespace: str,
    allowed_bucket_values: str,
    default_sensitivity: str,
    bucket_filter: str,
    result: dict[str, object] | None,
) -> str:
    return f"""
      <article class="tool-card">
        <header>
          <p class="card-kicker">Continuity</p>
          <h3>Session resume</h3>
          <p>Resume a saved session with a focused query instead of reconstructing context manually.</p>
        </header>
        <form method="post" action="/dashboard/session/resume">
          {_render_control("Session ID", _render_text_input("session_id", value="default-session"))}
          {_render_control(
              "Query text",
              _render_text_input("query_text", value="session checkpoint summary"),
          )}
          {_render_details(
              "Advanced options",
              "".join(
                  (
                      _render_control("Brain ID", _render_text_input("brain_id", value=brain_id)),
                      _render_control("Namespace", _render_text_input("namespace", value=active_namespace)),
                      _render_control(
                          "Source client",
                          _render_text_input("source_client", value="dashboard"),
                      ),
                      _render_control(
                          "Allowed buckets",
                          _render_text_input("allowed_buckets", value=allowed_bucket_values),
                      ),
                      _render_control(
                          "Sensitivity ceiling",
                          _render_text_input("sensitivity_ceiling", value=default_sensitivity),
                      ),
                  )
              ),
              meta="Use this when another client or earlier session already wrote checkpoints into shared memory.",
          )}
          {_render_hidden_input("bucket_filter", bucket_filter)}
          <button type="submit">Resume Session</button>
        </form>
        {_render_result_block("Session Result", result)}
      </article>
    """


def _render_admin_card(
    *,
    enabled: bool,
    brain_id: str,
    active_namespace: str,
    allowed_bucket_values: str,
    bucket_filter: str,
    result: dict[str, object] | None,
) -> str:
    if not enabled:
        return """
      <article class="tool-card">
        <header>
          <p class="card-kicker">Admin</p>
          <h3>Admin tools</h3>
          <p>Admin surface is disabled for this runtime, so destructive or maintenance actions are intentionally hidden.</p>
        </header>
      </article>
      """
    return f"""
      <article class="tool-card danger-zone">
        <header>
          <p class="card-kicker">Admin</p>
          <h3>Admin tools</h3>
          <p>Maintenance and destructive actions are isolated here so they do not compete with the normal operator flow.</p>
        </header>
        <div class="admin-stack">
          <section class="subcard">
            <header>
              <h4>Supersede or update memory</h4>
            </header>
            <form method="post" action="/dashboard/admin/update">
              {_render_control("ID", _render_text_input("id"))}
              {_render_control("Title", _render_text_input("title"))}
              {_render_control("Content", _render_textarea("content"))}
              {_render_details(
                  "Advanced options",
                  "".join(
                      (
                          _render_control(
                              "Mode",
                              _render_select(
                                  "mode",
                                  (
                                      '<option value="replace_content">supersede content</option>'
                                      '<option value="in_place">in place</option>'
                                  ),
                              ),
                          ),
                          _render_control("Actor", _render_text_input("actor", value="dashboard")),
                          _render_hidden_input("brain_id", brain_id),
                      )
                  ),
              )}
              {_render_hidden_input("bucket_filter", bucket_filter)}
              <button type="submit">Supersede / Update</button>
            </form>
          </section>
          <section class="subcard">
            <header>
              <h4>Reindex memory</h4>
            </header>
            <form method="post" action="/dashboard/admin/reindex">
              {_render_control("IDs", _render_text_input("ids"))}
              {_render_control(
                  "Scope",
                  _render_select(
                      "scope",
                      '<option value="records">records</option><option value="documents">documents</option>',
                  ),
              )}
              {_render_hidden_input("bucket_filter", bucket_filter)}
              <button type="submit" class="secondary">Reindex</button>
            </form>
          </section>
          <section class="subcard">
            <header>
              <h4>Audit current scope</h4>
            </header>
            <form method="post" action="/dashboard/admin/audit">
              {_render_hidden_input("brain_id", brain_id)}
              {_render_hidden_input("namespace", active_namespace)}
              {_render_hidden_input("allowed_buckets", allowed_bucket_values)}
              {_render_hidden_input("bucket_filter", bucket_filter)}
              <button type="submit" class="secondary">Audit Memory</button>
            </form>
          </section>
          <section class="subcard danger-zone">
            <header>
              <h4>Delete memory</h4>
            </header>
            <form method="post" action="/dashboard/admin/delete">
              {_render_control("IDs", _render_text_input("ids"))}
              {_render_control(
                  "Reason",
                  _render_text_input("reason", value="dashboard cleanup"),
              )}
              {_render_control(
                  "Mode",
                  _render_select(
                      "mode",
                      '<option value="soft">soft</option><option value="hard">hard</option>',
                  ),
              )}
              {_render_hidden_input("bucket_filter", bucket_filter)}
              <button type="submit" class="danger">Delete</button>
            </form>
          </section>
        </div>
        {_render_result_block("Admin Result", result)}
      </article>
    """


def _render_workflow_card(
    *,
    title: str,
    kicker: str,
    copy: str,
    action: str,
    button_label: str,
    essential_fields: str,
    advanced_fields: str,
    hidden_fields: str,
    result_title: str,
    result: dict[str, object] | None,
) -> str:
    return f"""
      <article class="workflow-card">
        <header>
          <p class="card-kicker">{escape(kicker)}</p>
          <h3>{escape(title)}</h3>
          <p>{escape(copy)}</p>
        </header>
        <form method="post" action="{escape(action)}">
          <div class="control-grid">
            {essential_fields}
          </div>
          {_render_details("Advanced options", advanced_fields, meta="Defaults follow the current dashboard context and can be overridden here.")}
          {hidden_fields}
          <button type="submit">{escape(button_label)}</button>
        </form>
        {_render_result_block(result_title, result)}
      </article>
    """


def _render_panel(title: str, copy: str, body: str) -> str:
    return f"""
      <section class="panel">
        <header>
          <h3>{escape(title)}</h3>
          <p>{escape(copy)}</p>
        </header>
        {body}
      </section>
    """


def _render_context_fields(
    *,
    brain_id: str,
    active_namespace: str,
    bucket_value: str,
    default_sensitivity: str,
    extra_fields: str = "",
) -> str:
    return "".join(
        (
            _render_control("Brain ID", _render_text_input("brain_id", value=brain_id)),
            _render_control(
                "Namespace",
                _render_text_input("namespace", value=active_namespace),
            ),
            _render_control("Bucket", _render_text_input("bucket", value=bucket_value)),
            _render_control(
                "Sensitivity",
                _render_text_input("sensitivity", value=default_sensitivity),
            ),
            extra_fields,
        )
    )


def _render_query_context_fields(
    *,
    brain_id: str,
    active_namespace: str,
    allowed_bucket_values: str,
    default_sensitivity: str,
) -> str:
    return "".join(
        (
            _render_control("Brain ID", _render_text_input("brain_id", value=brain_id)),
            _render_control(
                "Namespace",
                _render_text_input("namespace", value=active_namespace),
            ),
            _render_control(
                "Allowed buckets",
                _render_text_input("allowed_buckets", value=allowed_bucket_values),
            ),
            _render_control(
                "Sensitivity ceiling",
                _render_text_input("sensitivity_ceiling", value=default_sensitivity),
            ),
        )
    )


def _render_details(title: str, body: str, *, meta: str | None = None) -> str:
    detail_meta = f'<p class="details-meta">{escape(meta)}</p>' if meta else ""
    return f"""
      <details class="details-block">
        <summary>{escape(title)}</summary>
        <div class="details-body">
          {detail_meta}
          {body}
        </div>
      </details>
    """


def _render_control(label: str, control: str, *, hint: str | None = None) -> str:
    hint_html = f'<p class="control-hint">{escape(hint)}</p>' if hint else ""
    return f"""
      <label class="control">
        <span class="control-label">{escape(label)}</span>
        {control}
        {hint_html}
      </label>
    """


def _render_text_input(name: str, *, value: str = "", attributes: str = "") -> str:
    return f'<input type="text" name="{escape(name)}" value="{escape(value)}" {attributes} />'


def _render_textarea(name: str, *, placeholder: str = "", value: str = "") -> str:
    placeholder_attr = f' placeholder="{escape(placeholder)}"' if placeholder else ""
    return (
        f'<textarea name="{escape(name)}"{placeholder_attr}>{escape(value)}</textarea>'
    )


def _render_select(name: str, options: str) -> str:
    return f'<select name="{escape(name)}">{options}</select>'


def _render_hidden_input(name: str, value: str) -> str:
    return f'<input type="hidden" name="{escape(name)}" value="{escape(value)}" />'


def _render_bucket_select(
    name: str, available_buckets: list[object], active_bucket: str
) -> str:
    options = ['<option value="">all buckets</option>']
    options.extend(
        _render_bucket_option(bucket, active_bucket) for bucket in available_buckets
    )
    return _render_select(name, "".join(options))


def _render_result_block(title: str, payload: dict[str, object] | None) -> str:
    if payload is None:
        return ""
    if title == "Briefing Result" and "briefing" in payload:
        return _render_result_card(
            title,
            f"<pre>{escape(str(payload['briefing']))}</pre>",
        )
    if title == "Report Result" and "report" in payload:
        mode = escape(str(payload.get("mode", "report")))
        return _render_result_card(
            title,
            f'<p><strong>Mode:</strong> {mode}</p><pre>{escape(str(payload["report"]))}</pre>',
        )
    if title == "Search Result":
        results = payload.get("results", [])
        if isinstance(results, list) and results:
            items = "".join(
                (
                    f"<li><strong>{escape(str(result.get('id', 'unknown')))}</strong>"
                    f"<span class=\"panel-copy\">{escape(str(result.get('content', result.get('title', ''))))}</span></li>"
                )
                for result in results
                if isinstance(result, dict)
            )
            return _render_result_card(title, f'<ul class="result-list">{items}</ul>')
    items = "".join(
        f'<li><strong>{escape(str(key))}</strong><span class="panel-copy">{escape(str(value))}</span></li>'
        for key, value in payload.items()
    )
    return _render_result_card(title, f'<ul class="result-list">{items}</ul>')


def _render_result_card(title: str, body: str) -> str:
    return f"""
      <section class="result-card">
        <h4>{escape(title)}</h4>
        {body}
      </section>
    """


def _render_document_list(documents: list[dict[str, object]]) -> str:
    if not documents:
        return _render_empty_state(
            "No documents in scope yet. Capture a document or ingest an article to build a review trail."
        )
    items = "".join(
        (
            f"<li><strong>{escape(str(item.get('title') or 'Untitled document'))}</strong>"
            f"{_render_optional_pill('archived', item.get('archived'))}"
            f"<span class=\"panel-meta\">{escape(str(item['namespace']))}/{escape(str(item['bucket']))}</span>"
            f"<span class=\"panel-copy\">{escape(str(item.get('summary') or 'No summary has been generated for this document yet.'))}</span></li>"
        )
        for item in documents
    )
    return f'<ul class="panel-list">{items}</ul>'


def _render_record_list(records: list[dict[str, object]]) -> str:
    if not records:
        return _render_empty_state(
            "No recent memory yet. Capture a note to start building operator context."
        )
    items = "".join(
        (
            f"<li><strong>{escape(str(item.get('title') or item['id']))}</strong>"
            f"{_render_optional_pill('archived', item.get('archived'))}"
            f"<span class=\"panel-meta\">{escape(str(item['namespace']))}/{escape(str(item['bucket']))}</span>"
            f"<span class=\"panel-copy\">{escape(str(item.get('content', ''))[:220])}</span></li>"
        )
        for item in records
    )
    return f'<ul class="panel-list">{items}</ul>'


def _render_prioritized_feed(items: list[dict[str, object]]) -> str:
    if not items:
        return _render_empty_state(
            "No prioritized memory yet. Run a search, capture a note, or launch a protocol to seed this queue."
        )
    rendered = "".join(
        (
            f"<li><strong>{escape(str(item.get('bucket') or 'memory'))}</strong>"
            f"<span class=\"panel-copy\">{escape(str(item.get('content_preview') or item.get('id') or 'Untitled memory'))}</span></li>"
        )
        for item in items
    )
    return f'<ul class="panel-list">{rendered}</ul>'


def _render_brain_list(brains: list[dict[str, object]], active_brain_id: str) -> str:
    if not brains:
        return _render_empty_state(
            "No brains are registered yet. Create one in the advanced tools section when you need a named workspace."
        )
    items = "".join(
        (
            f"<li><strong>{escape(str(item.get('brain_id', 'unknown')))}</strong>"
            f"{_render_optional_pill('active', str(item.get('brain_id')) == active_brain_id)}"
            f"{_render_optional_pill(str(item.get('status', 'active')), True)}"
            f"<span class=\"panel-meta\">{escape(str(item.get('namespace', 'unknown')))}</span>"
            f"<span class=\"panel-copy\">{escape(str(item.get('description') or 'No description yet.'))}</span></li>"
        )
        for item in brains
    )
    return f'<ul class="panel-list">{items}</ul>'


def _render_connector_list(connectors: list[dict[str, object]]) -> str:
    if not connectors:
        return _render_empty_state(
            "No connector metadata is available yet. Add integrations when you want external systems feeding this brain."
        )
    items = []
    for item in connectors:
        status = (
            "healthy"
            if item.get("healthy")
            else ("runnable" if item.get("runnable") else "metadata-only")
        )
        items.append(
            "<li>"
            f"<strong>{escape(str(item.get('name', item.get('slug', 'unknown'))))}</strong>"
            f'<span class="pill {_status_tone(status)}">{escape(status)}</span>'
            f"<span class=\"panel-copy\">{escape(str(item.get('description') or ''))}</span>"
            f"<span class=\"panel-meta\">Capabilities: {escape(', '.join(str(cap) for cap in item.get('capabilities', [])) or 'n/a')}</span>"
            f"<span class=\"panel-meta\">Verbs: {escape(', '.join(str(verb) for verb in item.get('supported_verbs', [])) or 'n/a')}</span>"
            f"<span class=\"panel-meta\">Setup: {escape(str(item.get('setup_instructions') or 'n/a'))}</span>"
            "</li>"
        )
    return f'<ul class="panel-list">{"".join(items)}</ul>'


def _render_protocol_option(protocol: dict[str, object]) -> str:
    name = escape(str(protocol.get("name") or "unknown"))
    purpose = escape(str(protocol.get("purpose") or ""))
    return f'<option value="{name}">{name} - {purpose}</option>'


def _render_bucket_option(bucket: object, active_bucket: object) -> str:
    selected = " selected" if str(bucket) == str(active_bucket) else ""
    escaped_bucket = escape(str(bucket))
    return f'<option value="{escaped_bucket}"{selected}>{escaped_bucket}</option>'


def _render_brain_datalist(
    brains: list[dict[str, object]], active_brain_id: str
) -> str:
    values = {str(item.get("brain_id")) for item in brains if item.get("brain_id")}
    values.add(active_brain_id)
    return "".join(
        f'<option value="{escape(value)}"></option>' for value in sorted(values)
    )


def _render_audit_list(events: list[dict[str, object]]) -> str:
    if not events:
        return _render_empty_state(
            "No audit activity yet. Audit entries will appear here after capture, delete, or admin actions."
        )
    items = "".join(
        (
            f"<li><strong>{escape(str(event.get('operation', 'unknown')))}</strong>"
            f"<span class=\"pill {_status_tone(str(event.get('outcome', 'unknown')))}\">{escape(str(event.get('outcome', 'unknown')))}</span>"
            f"<span class=\"panel-meta\">Actor: {escape(str(event.get('actor', 'system')))}</span></li>"
        )
        for event in events
    )
    return f'<ul class="panel-list">{items}</ul>'


def _render_empty_state(message: str) -> str:
    return f'<p class="empty-state">{escape(message)}</p>'


def _render_optional_pill(label: str, enabled: object) -> str:
    if not enabled:
        return ""
    return f'<span class="pill tone-neutral">{escape(label)}</span>'


def _status_tone(raw_status: str) -> str:
    status = raw_status.lower()
    if any(
        keyword in status
        for keyword in ("healthy", "configured", "active", "success", "runnable")
    ):
        return "tone-good"
    if any(
        keyword in status
        for keyword in (
            "fallback",
            "metadata",
            "unknown",
            "local",
            "in_memory",
            "mirror",
        )
    ):
        return "tone-warn"
    if any(
        keyword in status
        for keyword in ("disabled", "rejected", "error", "hard", "degraded")
    ):
        return "tone-danger"
    return "tone-neutral"
