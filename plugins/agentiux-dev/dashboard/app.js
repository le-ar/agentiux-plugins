const appRoot = document.getElementById("app");

const WORKSPACE_PANELS = ["now", "plan", "quality", "integrations", "memory", "diagnostics"];

function createTelemetry() {
  return {
    requestCounts: {
      bootstrap: 0,
      panel: 0,
      overview: 0,
      cockpit: 0,
      other: 0,
    },
    requestLog: [],
    firstUsableRenderMs: null,
    lastLoadMode: null,
    lastBootstrapAt: null,
    activePanelLoadedAt: null,
  };
}

const state = {
  overviewPayload: null,
  cockpitShell: null,
  panelPayloads: {},
  selectedWorkspace: null,
  panel: "now",
  forceOverview: false,
  loading: true,
  refreshing: false,
  panelLoading: false,
  error: null,
  panelError: null,
  editingConnection: null,
  editingAuthProfile: null,
  editingAuthSession: null,
  authResolvePreview: null,
  editingNote: null,
  editingLearning: null,
  bootstrapped: false,
  requestToken: 0,
  telemetry: createTelemetry(),
};

function workspaceRoute(workspacePath, panel = "now") {
  if (!workspacePath) {
    return "/#overview";
  }
  const query = new URLSearchParams();
  const normalizedPanel = normalizePanel(panel);
  if (normalizedPanel !== "now") {
    query.set("panel", normalizedPanel);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return `/workspaces/${encodeURIComponent(workspacePath)}${suffix}`;
}

function parseRoute() {
  const legacyWorkspace = new URLSearchParams(window.location.search).get("workspace");
  const panel = normalizePanel(new URLSearchParams(window.location.search).get("panel") || "now");
  const match = window.location.pathname.match(/^\/workspaces\/(.+)$/);
  if (match) {
    return {
      workspacePath: decodeURIComponent(match[1]),
      panel,
      forceOverview: false,
      source: "path",
    };
  }
  if (legacyWorkspace) {
    return {
      workspacePath: legacyWorkspace,
      panel,
      forceOverview: false,
      source: "legacy-query",
    };
  }
  return {
    workspacePath: null,
    panel: "now",
    forceOverview: window.location.hash === "#overview",
    source: "overview",
  };
}

function updateRoute(workspacePath, options = {}) {
  const panel = normalizePanel(options.panel || state.panel || "now");
  const mode = options.mode || "push";
  const forceOverview = Boolean(options.forceOverview);
  const target = workspacePath ? workspaceRoute(workspacePath, panel) : forceOverview ? "/#overview" : "/";
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (current === target) {
    return;
  }
  const payload = {
    workspacePath: workspacePath || null,
    panel,
    forceOverview,
  };
  if (mode === "replace") {
    window.history.replaceState(payload, "", target);
    return;
  }
  window.history.pushState(payload, "", target);
}

function normalizePanel(panel) {
  return WORKSPACE_PANELS.includes(panel) ? panel : "now";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function toneClass(tone) {
  if (tone === "ok") return "ok";
  if (tone === "warn") return "warn";
  if (tone === "bad") return "bad";
  return "";
}

function formatLines(lines, fallback) {
  return escapeHtml((lines || []).join("\n") || fallback);
}

function compactText(value, fallback = "n/a") {
  return escapeHtml(value || fallback);
}

function issueSummarySourceLabel(source) {
  switch (source) {
    case "description":
      return "Description";
    case "external_reference":
      return "Doc preview";
    case "comment":
      return "Comment";
    case "related_issue":
      return "Related issue";
    case "summary":
      return "Issue summary";
    default:
      return "";
  }
}

function renderIssueHoverSummary(item) {
  const preview = item?.hover_summary || null;
  if (!preview) {
    return "";
  }
  const sourceLabel = issueSummarySourceLabel(preview.source);
  const stats = [
    preview.comment_count > 0 ? `${preview.comment_count} comments` : null,
    preview.linked_issue_count > 0 ? `${preview.linked_issue_count} links` : null,
    preview.openable_external_reference_count > 0
      ? `${preview.openable_external_reference_count} docs`
      : preview.external_reference_count > 0
        ? `${preview.external_reference_count} refs`
        : null,
    preview.related_issue_count > 0 ? `${preview.related_issue_count} related` : null,
    preview.warning_count > 0 ? `${preview.warning_count} warnings` : null,
  ].filter(Boolean);
  const referenceTitles = (preview.reference_titles || []).filter(Boolean);
  const relatedIssueKeys = (preview.related_issue_keys || []).filter(Boolean);
  return `
    <div class="issue-popover" role="tooltip">
      <div class="issue-popover-header">
        <strong>${escapeHtml(item.issue_key || "issue")}</strong>
        ${sourceLabel ? `<span class="pill-chip ${toneClass("warn")}">${escapeHtml(sourceLabel)}</span>` : ""}
      </div>
      <div class="issue-popover-title">${escapeHtml(item.title || "Untitled issue")}</div>
      <p class="issue-popover-text">${escapeHtml(preview.excerpt || "No extra context collected yet.")}</p>
      ${
        stats.length
          ? `<div class="chip-row">${stats.map((stat) => `<span class="pill-chip">${escapeHtml(stat)}</span>`).join("")}</div>`
          : ""
      }
      ${
        referenceTitles.length
          ? `<div class="issue-popover-note">Docs: ${escapeHtml(referenceTitles.join(", "))}</div>`
          : ""
      }
      ${
        relatedIssueKeys.length
          ? `<div class="issue-popover-note">Related: ${escapeHtml(relatedIssueKeys.join(", "))}</div>`
          : ""
      }
    </div>
  `;
}

async function apiJson(url, options = {}) {
  const path = new URL(url, window.location.origin).pathname;
  let requestKind = "other";
  if (path === "/api/dashboard-bootstrap") requestKind = "bootstrap";
  else if (path === "/api/workspace-panel") requestKind = "panel";
  else if (path === "/api/dashboard") requestKind = "overview";
  else if (path === "/api/workspace-cockpit" || path === "/api/workspace-detail") requestKind = "cockpit";
  state.telemetry.requestCounts[requestKind] = (state.telemetry.requestCounts[requestKind] || 0) + 1;
  state.telemetry.requestLog = [
    {
      kind: requestKind,
      path,
      at: new Date().toISOString(),
    },
    ...state.telemetry.requestLog,
  ].slice(0, 12);
  const response = await fetch(url, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `Request failed with ${response.status}`);
  }
  return payload;
}

function currentPanelPayload() {
  return state.panelPayloads[state.panel] || null;
}

function composeCockpitModel() {
  if (!state.cockpitShell) {
    return null;
  }
  return {
    ...state.cockpitShell,
    [state.panel]: currentPanelPayload() || {},
  };
}

function setOverviewPayload(payload) {
  if (!payload) {
    state.overviewPayload = null;
    return;
  }
  state.overviewPayload = {
    schema_version: payload.schema_version,
    generated_at: payload.generated_at,
    plugin: payload.plugin,
    stats: payload.stats,
    gui: payload.gui,
    overview: payload.overview,
    starter_runs: payload.starter_runs,
  };
}

function clearWorkspaceEditingState() {
  state.editingConnection = null;
  state.editingAuthProfile = null;
  state.editingAuthSession = null;
  state.authResolvePreview = null;
  state.editingNote = null;
  state.editingLearning = null;
}

function syncEditingState(panelId, panelPayload) {
  if (panelId === "integrations") {
    const connections = panelPayload?.youtrack?.connections?.items || [];
    const authProfiles = panelPayload?.auth?.items || [];
    const authSessions = panelPayload?.auth?.sessions?.items || [];
    if (state.editingConnection) {
      state.editingConnection =
        connections.find((item) => item.connection_id === state.editingConnection.connection_id) || null;
    }
    if (state.editingAuthProfile) {
      state.editingAuthProfile =
        authProfiles.find((item) => item.profile_id === state.editingAuthProfile.profile_id) || null;
    }
    if (state.editingAuthSession) {
      state.editingAuthSession =
        authSessions.find((item) => item.session_id === state.editingAuthSession.session_id) || null;
    }
  }
  if (panelId === "memory") {
    const notes = panelPayload?.project_notes?.items || [];
    const learningEntries = panelPayload?.learnings?.items || [];
    if (state.editingNote) {
      state.editingNote = notes.find((item) => item.note_id === state.editingNote.note_id) || null;
    }
    if (state.editingLearning) {
      state.editingLearning =
        learningEntries.find((item) => item.entry_id === state.editingLearning.entry_id) || null;
    }
  }
}

function applyBootstrapPayload(payload, options = {}) {
  setOverviewPayload(payload);
  const resolvedWorkspace = payload.selected_workspace_path || payload.workspace_shell?.workspace_path || null;
  state.selectedWorkspace = resolvedWorkspace;
  state.cockpitShell = payload.workspace_shell || null;
  state.panel = resolvedWorkspace ? normalizePanel(payload.active_panel || options.panel || state.panel || "now") : "now";
  state.forceOverview = !resolvedWorkspace && Boolean(payload.force_overview ?? options.forceOverview);
  state.panelPayloads = resolvedWorkspace && payload.panel_payload ? { [state.panel]: payload.panel_payload } : {};
  state.panelError = null;
  if (resolvedWorkspace) {
    syncEditingState(state.panel, payload.panel_payload || {});
  } else {
    clearWorkspaceEditingState();
  }
  state.telemetry.lastLoadMode = "bootstrap";
  state.telemetry.lastBootstrapAt = new Date().toISOString();
}

function applyPanelPayload(payload, panelId) {
  const normalizedPanel = normalizePanel(payload.active_panel || panelId || state.panel);
  state.selectedWorkspace = payload.selected_workspace_path || state.selectedWorkspace;
  state.cockpitShell = payload.workspace_shell || state.cockpitShell;
  state.panel = normalizedPanel;
  state.panelPayloads = {
    ...state.panelPayloads,
    [normalizedPanel]: payload.panel_payload || {},
  };
  state.panelError = null;
  syncEditingState(normalizedPanel, payload.panel_payload || {});
  state.telemetry.lastLoadMode = "panel";
  state.telemetry.activePanelLoadedAt = new Date().toISOString();
}

async function loadDashboardBootstrap(workspacePath, options = {}) {
  const requestedWorkspace = workspacePath || null;
  const historyMode = options.historyMode || "skip";
  const requestedPanel = normalizePanel(options.panel || state.panel || "now");
  const forceOverview = Boolean(options.forceOverview);
  const requestToken = state.requestToken + 1;
  state.requestToken = requestToken;
  state.panelError = null;
  state.panelLoading = false;
  if (!state.overviewPayload) {
    state.loading = true;
  } else {
    state.refreshing = true;
  }
  render();
  try {
    const query = new URLSearchParams();
    if (requestedWorkspace) {
      query.set("workspace", requestedWorkspace);
    }
    query.set("panel", requestedPanel);
    if (forceOverview) {
      query.set("forceOverview", "1");
    }
    const payload = await apiJson(`/api/dashboard-bootstrap?${query.toString()}`);
    if (requestToken !== state.requestToken) {
      return;
    }
    applyBootstrapPayload(payload, { panel: requestedPanel, forceOverview });
    if (historyMode !== "skip") {
      updateRoute(state.selectedWorkspace, {
        panel: state.panel,
        forceOverview: state.forceOverview,
        mode: historyMode,
      });
    }
    state.error = null;
    state.bootstrapped = true;
  } catch (error) {
    if (requestToken !== state.requestToken) {
      return;
    }
    state.error = error.message;
  } finally {
    if (requestToken !== state.requestToken) {
      return;
    }
    state.loading = false;
    state.refreshing = false;
    render();
  }
}

async function loadWorkspacePanel(panel, options = {}) {
  if (!state.selectedWorkspace) return;
  const normalizedPanel = normalizePanel(panel);
  const historyMode = options.historyMode || "push";
  state.panel = normalizedPanel;
  if (historyMode !== "skip") {
    updateRoute(state.selectedWorkspace, { panel: normalizedPanel, mode: historyMode });
  }
  if (state.panelPayloads[normalizedPanel] && !options.force) {
    state.panelError = null;
    render();
    return;
  }
  const requestToken = state.requestToken + 1;
  state.requestToken = requestToken;
  state.panelLoading = true;
  state.panelError = null;
  render();
  try {
    const payload = await apiJson(
      `/api/workspace-panel?workspace=${encodeURIComponent(state.selectedWorkspace)}&panel=${encodeURIComponent(normalizedPanel)}`,
    );
    if (requestToken !== state.requestToken) {
      return;
    }
    applyPanelPayload(payload, normalizedPanel);
    state.error = null;
  } catch (error) {
    if (requestToken !== state.requestToken) {
      return;
    }
    state.panelError = error.message;
  } finally {
    if (requestToken !== state.requestToken) {
      return;
    }
    state.panelLoading = false;
    render();
  }
}

function setPanel(panel) {
  if (!state.selectedWorkspace) return;
  return loadWorkspacePanel(panel, { historyMode: "push" });
}

function reloadActiveWorkspacePanel(panel) {
  if (!state.selectedWorkspace) return;
  return loadWorkspacePanel(panel, { historyMode: "replace", force: true });
}

function refresh() {
  return loadDashboardBootstrap(state.selectedWorkspace, {
    historyMode: "replace",
    panel: state.panel,
    forceOverview: !state.selectedWorkspace && state.forceOverview,
  });
}

function clearSelection() {
  return loadDashboardBootstrap(null, { historyMode: "push", forceOverview: true, panel: "now" });
}

function selectWorkspace(workspacePath) {
  return loadDashboardBootstrap(workspacePath, { historyMode: "push", panel: "now" });
}

function debugSnapshot() {
  return {
    selectedWorkspace: state.selectedWorkspace,
    panel: state.panel,
    forceOverview: state.forceOverview,
    requestCounts: state.telemetry.requestCounts,
    requestLog: state.telemetry.requestLog,
    panelCache: Object.keys(state.panelPayloads),
    timings: {
      firstUsableRenderMs: state.telemetry.firstUsableRenderMs,
    },
    loading: state.loading,
    refreshing: state.refreshing,
    panelLoading: state.panelLoading,
    lastLoadMode: state.telemetry.lastLoadMode,
  };
}

function fallbackPortfolioCardFromShell() {
  if (!state.selectedWorkspace || !state.cockpitShell) {
    return null;
  }
  return {
    workspace_path: state.selectedWorkspace,
    workspace_label: state.cockpitShell.workspace_label || "Pending workspace",
    workspace_slug: null,
    status_badge: state.cockpitShell.hero?.status_badge || { label: state.cockpitShell.state_kind, tone: "warn" },
    next_action: state.cockpitShell.hero?.headline || "Open workspace cockpit.",
    verification_status: state.cockpitShell.verification_status || { label: "Not indexed", tone: "warn" },
    youtrack_status: state.cockpitShell.youtrack_status || { label: "Unavailable", tone: "warn" },
    metrics: state.cockpitShell.portfolio_card?.metrics || [],
  };
}

function currentWorkspaceNavCard() {
  return state.cockpitShell?.portfolio_card || fallbackPortfolioCardFromShell();
}

function renderMetric(metric) {
  return `
    <div class="metric-card ${toneClass(metric.tone)}">
      <span class="metric-label">${escapeHtml(metric.label)}</span>
      <strong>${escapeHtml(metric.value)}</strong>
      ${metric.hint ? `<span class="metric-hint">${escapeHtml(metric.hint)}</span>` : ""}
    </div>
  `;
}

function renderMetricGrid(metrics) {
  if (!metrics?.length) return "";
  const classNames = ["metric-grid"];
  const useBalancedGrid = metrics.length === 4 || (metrics.length > 4 && metrics.length % 3 !== 0);
  if (useBalancedGrid) {
    classNames.push("metric-grid-balanced");
  }
  if (useBalancedGrid && metrics.length % 2 === 1) {
    classNames.push("metric-grid-last-span");
  }
  return `<div class="${classNames.join(" ")}">${metrics.map(renderMetric).join("")}</div>`;
}

function renderAttentionList(items) {
  if (!items?.length) {
    return `<div class="empty-note">No immediate attention items.</div>`;
  }
  return `
    <div class="attention-list">
      ${items
        .map(
          (item) => `
            <article class="attention-card ${toneClass(item.tone)}">
              <div class="attention-head">
                <span class="pill-chip ${toneClass(item.tone)}">${escapeHtml(item.section || "note")}</span>
                <strong>${escapeHtml(item.title)}</strong>
              </div>
              <p>${escapeHtml(item.body)}</p>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderSidebar(overviewPayload) {
  const overview = overviewPayload?.overview || { workspaces: [], stat_cards: [] };
  const plugin = overviewPayload?.plugin || {};
  const gui = overviewPayload?.gui || {};
  const cards = overview.workspaces || [];
  const selectedCard = currentWorkspaceNavCard();
  const mergedCards = cards.map((item) => {
    if (!selectedCard || item.workspace_path !== state.selectedWorkspace) {
      return item;
    }
    return {
      ...item,
      ...selectedCard,
      workspace_path: item.workspace_path,
    };
  });
  const hasSelectedCard = mergedCards.some((item) => item.workspace_path === state.selectedWorkspace);
  const visibleCards = !hasSelectedCard && selectedCard ? [selectedCard, ...mergedCards] : mergedCards;
  return `
    <aside class="sidebar">
      <section class="sidebar-section sidebar-intro">
        <div class="brand-block">
          <p class="brand-kicker">AgentiUX Dev</p>
          <h1>Dashboard</h1>
          <p class="brand-path">${escapeHtml(plugin.current_root || "plugin root unavailable")}</p>
        </div>
        <div class="sidebar-actions">
          <button ${state.refreshing ? "disabled" : ""} onclick="window.__agentiux.refresh()">${state.refreshing ? "Refreshing..." : "Refresh"}</button>
          <button class="secondary" onclick="window.__agentiux.clearSelection()">Overview</button>
        </div>
        ${renderMetricGrid((overview.stat_cards || []).slice(0, 3))}
      </section>
      <section class="sidebar-section">
        <div class="section-heading">
          <h2>Portfolio</h2>
          <span class="pill-chip ${toneClass(overviewPayload?.stats?.gui_status === "running" ? "ok" : "warn")}">${escapeHtml(
            gui.status || "stopped",
          )}</span>
        </div>
        <div class="workspace-nav">
          ${visibleCards.map((item) => renderWorkspaceNavCard(item, item.workspace_path === state.selectedWorkspace)).join("")}
          ${!visibleCards.length ? `<div class="empty-note">${escapeHtml(overview.empty_message || "No initialized workspaces yet.")}</div>` : ""}
        </div>
      </section>
    </aside>
  `;
}

function renderWorkspaceNavCard(item, active) {
  const workspaceArg = JSON.stringify(item.workspace_path);
  return `
    <a class="workspace-nav-card ${active ? "active" : ""}" href="${escapeHtml(
      workspaceRoute(item.workspace_path),
    )}" onclick='window.__agentiux.selectWorkspace(${workspaceArg}); return false;'>
      <div class="workspace-nav-head">
        <div>
          <strong>${escapeHtml(item.workspace_label || item.workspace_path)}</strong>
          <div class="workspace-nav-path">${escapeHtml(item.workspace_path)}</div>
        </div>
        <span class="pill-chip ${toneClass(item.status_badge?.tone)}">${escapeHtml(item.status_badge?.label || "idle")}</span>
      </div>
      <p class="workspace-nav-copy">${escapeHtml(item.next_action || "Open workspace cockpit.")}</p>
      <div class="chip-row">
        <span class="pill-chip ${toneClass(item.verification_status?.tone)}">${escapeHtml(item.verification_status?.label || "No verification")}</span>
        <span class="pill-chip ${toneClass(item.youtrack_status?.tone)}">${escapeHtml(item.youtrack_status?.label || "No integration")}</span>
      </div>
    </a>
  `;
}

function renderOverviewPage(overviewPayload) {
  const overview = overviewPayload?.overview || {};
  const workspaces = overview.workspaces || [];
  const starterRuns = overview.recent_starter_runs || [];
  const attentionSummary = overview.attention_summary || {};
  return `
    <section class="page-shell" data-screen-id="dashboard-overview" data-testid="dashboard-overview">
      <div class="hero-card overview-hero">
        <div class="hero-copy">
          <p class="eyebrow">Global overview</p>
          <h2>Portfolio state across initialized workspaces</h2>
          <p class="hero-text">
            Workspace cockpit is the primary operating view. This overview stays focused on portfolio risk, verification pressure, and fast workspace entry.
          </p>
        </div>
        ${renderMetricGrid(overview.stat_cards || [])}
      </div>
      <div class="attention-strip">
        <div class="attention-summary-card bad">
          <span>Critical</span>
          <strong>${escapeHtml(attentionSummary.critical_count || 0)}</strong>
          <p>Blocked work or failed verification across the portfolio.</p>
        </div>
        <div class="attention-summary-card warn">
          <span>Warnings</span>
          <strong>${escapeHtml(attentionSummary.warning_count || 0)}</strong>
          <p>Planned or closeout-ready work that still needs operator attention.</p>
        </div>
        <div class="attention-summary-card">
          <span>Active runs</span>
          <strong>${escapeHtml(attentionSummary.active_verification_runs || 0)}</strong>
          <p>Verification currently executing in external state.</p>
        </div>
        <div class="attention-summary-card">
          <span>Failed runs</span>
          <strong>${escapeHtml(attentionSummary.failed_verification_runs || 0)}</strong>
          <p>Recent deterministic checks that need review before closeout.</p>
        </div>
      </div>
      <div class="content-grid two-up">
        <section class="surface-card">
          <div class="section-heading">
            <h3>Workspace portfolio</h3>
            <span class="muted-copy">${escapeHtml(workspaces.length)} initialized</span>
          </div>
          <div class="portfolio-grid">
            ${
              workspaces.length
                ? workspaces
                    .map(
                      (item) => `
                        <article class="portfolio-card">
                          <div class="portfolio-head">
                            <div>
                              <h4>${escapeHtml(item.workspace_label)}</h4>
                              <p>${escapeHtml(item.workspace_path)}</p>
                            </div>
                            <span class="pill-chip ${toneClass(item.status_badge?.tone)}">${escapeHtml(item.status_badge?.label || "idle")}</span>
                          </div>
                          <p class="portfolio-copy">${escapeHtml(item.next_action || "Open cockpit.")}</p>
                          ${renderMetricGrid(item.metrics || [])}
                          <div class="chip-row">
                            <span class="pill-chip ${toneClass(item.verification_status?.tone)}">${escapeHtml(item.verification_status?.label || "No verification")}</span>
                            <span class="pill-chip ${toneClass(item.youtrack_status?.tone)}">${escapeHtml(item.youtrack_status?.label || "No integration")}</span>
                          </div>
                          <div class="portfolio-actions">
                            <button onclick='window.__agentiux.selectWorkspace(${JSON.stringify(item.workspace_path)})'>Open cockpit</button>
                          </div>
                        </article>
                      `,
                    )
                    .join("")
                : `<div class="empty-note">${escapeHtml(overview.empty_message || "No initialized workspaces yet.")}</div>`
            }
          </div>
        </section>
        <section class="surface-card">
          <div class="section-heading">
            <h3>Recent starter runs</h3>
            <span class="muted-copy">${escapeHtml(starterRuns.length)}</span>
          </div>
          ${
            starterRuns.length
              ? `<div class="stack-list">
                  ${starterRuns
                    .map(
                      (run) => `
                        <article class="stack-item">
                          <div class="stack-item-head">
                            <strong>${escapeHtml(run.run_id || "run")}</strong>
                            <span class="pill-chip ${toneClass(run.status === "failed" ? "bad" : "neutral")}">${escapeHtml(run.status || "unknown")}</span>
                          </div>
                          <p>${escapeHtml(run.project_root || "n/a")}</p>
                          <div class="chip-row">
                            <span class="pill-chip">${escapeHtml(run.preset_id || "preset")}</span>
                          </div>
                        </article>
                      `,
                    )
                    .join("")}
                </div>`
              : `<div class="empty-note">No starter runs recorded.</div>`
          }
        </section>
      </div>
    </section>
  `;
}

function renderHero(cockpit) {
  return `
    <section class="hero-card cockpit-hero ${toneClass(cockpit.hero?.status_badge?.tone)}" data-screen-id="workspace-cockpit-hero" data-testid="cockpit-hero">
      <div class="hero-copy">
        <p class="eyebrow">${escapeHtml(cockpit.state_kind === "initialized" ? "Workspace cockpit" : "Initialization preview")}</p>
        <h2>${escapeHtml(cockpit.hero?.title || cockpit.workspace_label || "Workspace")}</h2>
        <p class="hero-subtitle">${escapeHtml(cockpit.hero?.subtitle || cockpit.workspace_path || "")}</p>
        <div class="chip-row">
          <span class="pill-chip ${toneClass(cockpit.hero?.status_badge?.tone)}">${escapeHtml(cockpit.hero?.status_badge?.label || "idle")}</span>
          ${
            cockpit.hero?.status_badge?.hint
              ? `<span class="pill-chip">${escapeHtml(cockpit.hero.status_badge.hint)}</span>`
              : ""
          }
        </div>
        <p class="hero-text">${escapeHtml(cockpit.hero?.headline || "")}</p>
        <p class="hero-caption">${escapeHtml(cockpit.hero?.supporting_text || "")}</p>
      </div>
      ${renderMetricGrid(cockpit.hero?.metrics || [])}
    </section>
  `;
}

function renderTabs(cockpit) {
  const panelCounts = cockpit.panel_counts || {};
  const labels = {
    now: "Now",
    plan: "Plan",
    quality: "Quality",
    integrations: "Integrations",
    memory: "Memory",
    diagnostics: "Diagnostics",
  };
  return `
    <nav class="panel-tabs" aria-label="Workspace cockpit panels" data-active-panel="${escapeHtml(state.panel)}">
      ${WORKSPACE_PANELS.map(
        (panelId) => `
          <button class="tab-button ${state.panel === panelId ? "active" : ""}" data-panel-id="${escapeHtml(panelId)}" aria-pressed="${state.panel === panelId ? "true" : "false"}" onclick='window.__agentiux.setPanel(${JSON.stringify(
            panelId,
          )})'>
            <span>${labels[panelId]}</span>
            <span class="tab-count">${escapeHtml(panelCounts[panelId] || 0)}</span>
          </button>
        `,
      ).join("")}
    </nav>
  `;
}

function renderFocusSummary(now) {
  const cards = [];
  if (now.current_workstream) {
    cards.push(`
      <article class="stack-item">
        <div class="stack-item-head">
          <strong>${escapeHtml(now.current_workstream.workstream_id || "workstream")}</strong>
          <span class="pill-chip ${toneClass(now.current_workstream.tone)}">${escapeHtml(now.current_workstream.status || "status")}</span>
        </div>
        <p>${escapeHtml(now.current_workstream.title || "Untitled workstream")}</p>
      </article>
    `);
  }
  if (now.current_task) {
    cards.push(`
      <article class="stack-item">
        <div class="stack-item-head">
          <strong>${escapeHtml(now.current_task.task_id || "task")}</strong>
          <span class="pill-chip ${toneClass(now.current_task.tone)}">${escapeHtml(now.current_task.status || "status")}</span>
        </div>
        <p>${escapeHtml(now.current_task.title || "Untitled task")}</p>
      </article>
    `);
  }
  if (!cards.length) {
    return `<div class="empty-note">No current task or workstream focus recorded.</div>`;
  }
  return `<div class="stack-list">${cards.join("")}</div>`;
}

function renderNowPanel(cockpit) {
  const now = cockpit.now || {};
  return `
    <div class="content-grid" data-screen-id="cockpit-now-panel" data-panel="now" data-testid="cockpit-now-panel">
      <section class="surface-card emphasis-card">
        <div class="section-heading">
          <h3>Current objective</h3>
          <span class="pill-chip ${toneClass(now.verification_status?.tone)}">${escapeHtml(now.verification_status?.label || "No verification")}</span>
        </div>
        <p class="lead-copy">${escapeHtml(now.objective || "No explicit objective recorded.")}</p>
        ${renderMetricGrid(now.focus_cards || [])}
        <div class="stack-list">
          ${(now.guidance || []).map((item) => `<article class="stack-item"><p>${escapeHtml(item)}</p></article>`).join("")}
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Immediate attention</h3>
          <span class="muted-copy">${escapeHtml((cockpit.attention?.items || []).length)}</span>
        </div>
        ${renderAttentionList(cockpit.attention?.items || [])}
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Active brief</h3>
          <span class="muted-copy">${escapeHtml((now.brief_preview || []).length)} lines</span>
        </div>
        ${
          now.brief_preview?.length
            ? `<pre class="code-block">${escapeHtml(now.brief_preview.join("\n"))}</pre>`
            : `<div class="empty-note">No active brief recorded.</div>`
        }
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Current focus</h3>
          <span class="pill-chip ${toneClass(now.youtrack_status?.tone)}">${escapeHtml(now.youtrack_status?.label || "No integration")}</span>
        </div>
        ${renderFocusSummary(now)}
        ${
          now.blockers?.length
            ? `<div class="stack-list">
                ${now.blockers
                  .map(
                    (blocker) => `
                      <article class="stack-item danger">
                        <div class="stack-item-head">
                          <strong>Blocker</strong>
                          <span class="pill-chip bad">attention</span>
                        </div>
                        <p>${escapeHtml(blocker)}</p>
                      </article>
                    `,
                  )
                  .join("")}
              </div>`
            : `<div class="empty-note">No blockers recorded.</div>`
        }
      </section>
    </div>
  `;
}

function renderPlanPanel(cockpit) {
  const plan = cockpit.plan || {};
  const designState = plan.design_state || {};
  const designSummary = designState.design_summary || {};
  const testabilitySummary = designState.testability_summary || {};
  return `
    <div class="content-grid" data-screen-id="cockpit-plan-panel" data-panel="plan" data-testid="cockpit-plan-panel">
      <section class="surface-card">
        <div class="section-heading">
          <h3>Plan summary</h3>
          <span class="muted-copy">${escapeHtml(plan.stages?.length || 0)} stages</span>
        </div>
        ${renderMetricGrid(plan.summary_cards || [])}
        <div class="chip-row">
          ${Object.entries(plan.stage_summary || {})
            .map(([key, value]) => `<span class="pill-chip">${escapeHtml(`${key.replaceAll("_", " ")}: ${value}`)}</span>`)
            .join("")}
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Stage timeline</h3>
          <span class="muted-copy">${escapeHtml(plan.current_workstream?.workstream_id || "no workstream")}</span>
        </div>
        ${
          plan.stages?.length
            ? `<div class="stack-list">
                ${plan.stages
                  .map(
                    (stage) => `
                      <article class="stack-item">
                        <div class="stack-item-head">
                          <strong>${escapeHtml(stage.id || "stage")}</strong>
                          <span class="pill-chip ${toneClass(stage.tone)}">${escapeHtml(stage.status || "planned")}</span>
                        </div>
                        <p>${escapeHtml(stage.title || "Untitled stage")}</p>
                        <div class="chip-row">
                          <span class="pill-chip">${escapeHtml(`${stage.task_count || 0} tasks`)}</span>
                          <span class="pill-chip">${escapeHtml(stage.completed_at || "not completed")}</span>
                        </div>
                      </article>
                    `,
                  )
                  .join("")}
              </div>`
            : `<div class="empty-note">No stage register has been confirmed yet.</div>`
        }
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Task buckets</h3>
          <span class="muted-copy">${escapeHtml(plan.task_buckets?.length || 0)} groups</span>
        </div>
        ${
          plan.task_buckets?.length
            ? `<div class="stack-list">
                ${plan.task_buckets
                  .map(
                    (bucket) => `
                      <article class="stack-item">
                        <div class="stack-item-head">
                          <strong>${escapeHtml(bucket.label)}</strong>
                          <span class="pill-chip ${toneClass(bucket.tone)}">${escapeHtml(bucket.count || 0)}</span>
                        </div>
                        <div class="sub-list">
                          ${(bucket.items || [])
                            .map(
                              (task) => `
                                <div class="sub-row">
                                  <span>${escapeHtml(task.task_id || "task")}</span>
                                  <span>${escapeHtml(task.stage_id || "no-stage")}</span>
                                  <span>${escapeHtml(task.external_issue?.issue_key || task.title || "Untitled")}</span>
                                </div>
                              `,
                            )
                            .join("")}
                        </div>
                      </article>
                    `,
                  )
                  .join("")}
              </div>`
            : `<div class="empty-note">No tasks recorded for this workspace.</div>`
        }
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Design and testability</h3>
          <span class="muted-copy">${escapeHtml(designState.design_readiness || "sparse")}</span>
        </div>
        ${renderMetricGrid([
          { label: "Brief", value: designState.brief_status || "not started", tone: "neutral" },
          { label: "Brief mode", value: designState.brief_generation_status || "unknown", tone: "neutral" },
          { label: "Handoff", value: designState.current_handoff_status || "not started", tone: "neutral" },
          { label: "Surfaces", value: designSummary.affected_surface_count || 0, tone: "neutral" },
          { label: "Flows", value: designSummary.user_flow_count || 0, tone: "neutral" },
          { label: "States", value: designSummary.state_coverage_count || 0, tone: "neutral" },
          { label: "Critical actions", value: designSummary.critical_action_count || 0, tone: "neutral" },
          { label: "Covered actions", value: testabilitySummary.covered_action_count || 0, tone: "ok" },
          { label: "Limitations", value: testabilitySummary.limitation_count || 0, tone: testabilitySummary.limitation_count ? "warn" : "ok" },
          { label: "Hooks", value: designState.verification_hooks || 0, tone: "neutral" },
        ])}
        <div class="chip-row">
          <span class="pill-chip">${escapeHtml(`board candidates: ${designState.current_board_candidates || 0}`)}</span>
          <span class="pill-chip">${escapeHtml(`verified states: ${designSummary.verified_state_count || 0}`)}</span>
          <span class="pill-chip ${toneClass(testabilitySummary.unresolved_action_count ? "warn" : "ok")}">${escapeHtml(
            `unresolved actions: ${testabilitySummary.unresolved_action_count || 0}`,
          )}</span>
        </div>
      </section>
    </div>
  `;
}

function renderRunCard(title, run) {
  if (!run) {
    return `
      <article class="stack-item">
        <div class="stack-item-head">
          <strong>${escapeHtml(title)}</strong>
          <span class="pill-chip">none</span>
        </div>
        <p>No run recorded.</p>
      </article>
    `;
  }
  return `
    <article class="stack-item">
      <div class="stack-item-head">
        <strong>${escapeHtml(title)}</strong>
        <span class="pill-chip ${toneClass(run.status === "failed" ? "bad" : run.status === "passed" ? "ok" : "warn")}">${escapeHtml(
          run.status || "unknown",
        )}</span>
      </div>
      <p>${escapeHtml(run.run_id || "run")} · ${escapeHtml(run.mode || "mode")} · ${escapeHtml(run.target_id || "target")}</p>
      <div class="chip-row">
        <span class="pill-chip">${escapeHtml(`health: ${run.health || "unknown"}`)}</span>
        <span class="pill-chip">${escapeHtml(`passed: ${run.passed_cases ?? 0}`)}</span>
        <span class="pill-chip">${escapeHtml(`failed: ${run.failed_cases ?? 0}`)}</span>
      </div>
      ${run.message ? `<p>${escapeHtml(run.message)}</p>` : ""}
    </article>
  `;
}

function renderQualityPanel(cockpit) {
  const quality = cockpit.quality || {};
  const logs = quality.logs || {};
  const coverage = quality.coverage || {};
  const testabilitySummary = quality.testability_summary || {};
  return `
    <div class="content-grid" data-screen-id="cockpit-quality-panel" data-panel="quality" data-testid="cockpit-quality-panel">
      <section class="surface-card emphasis-card">
        <div class="section-heading">
          <h3>Verification health</h3>
          <span class="pill-chip ${toneClass(quality.health?.tone)}">${escapeHtml(quality.health?.label || "Unknown")}</span>
        </div>
        ${renderMetricGrid(quality.summary_cards || [])}
        ${
          quality.health?.detail
            ? `<p class="hero-caption">${escapeHtml(quality.health.detail)}</p>`
            : ""
        }
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Runs</h3>
          <span class="muted-copy">${escapeHtml(quality.recent_runs?.length || 0)} recent</span>
        </div>
        <div class="stack-list">
          ${renderRunCard("Latest run", quality.latest_run)}
          ${renderRunCard("Latest completed", quality.latest_completed_run)}
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Coverage and helpers</h3>
          <span class="muted-copy">${escapeHtml(coverage.warning_count || 0)} gaps</span>
        </div>
        <div class="stack-list">
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Coverage</strong>
              <span class="pill-chip ${toneClass(coverage.status === "clean" ? "ok" : coverage.status === "warning" ? "warn" : "neutral")}">${escapeHtml(
                coverage.status || "unknown",
              )}</span>
            </div>
            <div class="chip-row">
              <span class="pill-chip">${escapeHtml(`authored paths: ${testabilitySummary.authored_path_count || 0}`)}</span>
              <span class="pill-chip ${toneClass(testabilitySummary.limitation_count ? "warn" : "ok")}">${escapeHtml(
                `limitations: ${testabilitySummary.limitation_count || 0}`,
              )}</span>
              <span class="pill-chip ${toneClass(testabilitySummary.unresolved_action_count ? "warn" : "ok")}">${escapeHtml(
                `unresolved actions: ${testabilitySummary.unresolved_action_count || 0}`,
              )}</span>
            </div>
            <div class="sub-list">
              ${(coverage.gaps || [])
                .map(
                  (gap) => `<div class="sub-row"><span>${escapeHtml(gap.gap_id || "gap")}</span><span>${escapeHtml(
                    gap.category || "warning",
                  )}</span><span>${escapeHtml(gap.title || "Untitled gap")}</span></div>`,
                )
                .join("") || `<div class="empty-note">No coverage warnings.</div>`}
            </div>
          </article>
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Helper sync</strong>
              <span class="pill-chip ${toneClass(quality.helper_sync?.status === "synced" ? "ok" : quality.helper_sync?.status ? "warn" : "neutral")}">${escapeHtml(
                quality.helper_sync?.status || "unknown",
              )}</span>
            </div>
            <p>${escapeHtml(quality.helper_sync?.sync_root || "No helper sync root recorded.")}</p>
            ${
              quality.helper_sync?.missing_entrypoints?.length
                ? `<details class="disclosure">
                    <summary>Missing entrypoints (${escapeHtml(quality.helper_sync.missing_entrypoints.length)})</summary>
                    <pre class="code-block">${escapeHtml(quality.helper_sync.missing_entrypoints.join("\n"))}</pre>
                  </details>`
                : ""
            }
          </article>
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Selection and events</h3>
          <span class="muted-copy">${escapeHtml(quality.events?.length || 0)} events</span>
        </div>
        <div class="stack-list">
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Action coverage</strong>
              <span class="pill-chip ${toneClass(testabilitySummary.unresolved_action_count ? "warn" : "ok")}">${escapeHtml(
                testabilitySummary.unresolved_action_count ? "needs follow-up" : "covered",
              )}</span>
            </div>
            <div class="chip-row">
              <span class="pill-chip">${escapeHtml(`covered ${testabilitySummary.covered_action_count || 0}`)}</span>
              <span class="pill-chip">${escapeHtml(`limited ${testabilitySummary.limited_action_count || 0}`)}</span>
              <span class="pill-chip">${escapeHtml(`unsupported paths ${testabilitySummary.unsupported_path_count || 0}`)}</span>
            </div>
            ${
              testabilitySummary.unresolved_action_ids?.length
                ? `<pre class="code-block">${escapeHtml(testabilitySummary.unresolved_action_ids.join("\n"))}</pre>`
                : `<div class="empty-note">No unresolved critical actions.</div>`
            }
          </article>
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Resolved verification plan</strong>
              <span class="pill-chip ${toneClass(quality.selection?.selection_status === "resolved" ? "ok" : "warn")}">${escapeHtml(
                quality.selection?.selection_status || "unresolved",
              )}</span>
            </div>
            <div class="chip-row">
              <span class="pill-chip">${escapeHtml(`requested: ${quality.selection?.requested_mode || "n/a"}`)}</span>
              <span class="pill-chip">${escapeHtml(`resolved: ${quality.selection?.resolved_mode || "n/a"}`)}</span>
              <span class="pill-chip">${escapeHtml(`suite: ${quality.selection?.selected_suite || "none"}`)}</span>
            </div>
            ${quality.selection?.reason ? `<p>${escapeHtml(quality.selection.reason)}</p>` : ""}
            ${
              quality.auth_resolution?.issues?.length
                ? `<pre class="code-block">${escapeHtml(
                    quality.auth_resolution.issues.map((item) => `${item.case_id || "case"}: ${item.reason || "Auth resolution issue"}`).join("\n"),
                  )}</pre>`
                : quality.auth_resolution?.resolved_cases?.length
                  ? `<div class="chip-row">${quality.auth_resolution.resolved_cases
                      .map((item) => `<span class="pill-chip">${escapeHtml(`${item.case_id}: ${item.profile_id || "profile"}`)}</span>`)
                      .join("")}</div>`
                  : ""
            }
          </article>
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Recent events</strong>
              <span class="pill-chip">${escapeHtml(quality.events?.length || 0)}</span>
            </div>
            ${
              quality.events?.length
                ? `<pre class="code-block">${escapeHtml(
                    quality.events.map((event) => `${event.timestamp || ""} ${event.event_type || "event"}: ${event.message || ""}`).join("\n"),
                  )}</pre>`
                : `<div class="empty-note">No recent verification events.</div>`
            }
          </article>
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Log summaries</h3>
          <span class="muted-copy">debug</span>
        </div>
        <details class="disclosure">
          <summary>Stdout</summary>
          <pre class="code-block">${formatLines(logs.stdout, "No active stdout stream.")}</pre>
        </details>
        <details class="disclosure">
          <summary>Stderr</summary>
          <pre class="code-block">${formatLines(logs.stderr, "No active stderr stream.")}</pre>
        </details>
        <details class="disclosure">
          <summary>Logcat</summary>
          <pre class="code-block">${formatLines(logs.logcat, "No active logcat stream.")}</pre>
        </details>
      </section>
    </div>
  `;
}

function renderConnectionList(connections) {
  if (!connections?.length) {
    return `<div class="empty-note">No YouTrack connections recorded.</div>`;
  }
  return `
    <div class="stack-list">
      ${connections
        .map(
          (connection) => `
            <article class="stack-item">
              <div class="stack-item-head">
                <strong>${escapeHtml(connection.label)}</strong>
                <span class="pill-chip ${toneClass(connection.status === "connected" ? "ok" : connection.status === "error" ? "bad" : "warn")}">${escapeHtml(
                  connection.status || "unknown",
                )}</span>
              </div>
              <p>${escapeHtml(connection.base_url)}</p>
              <div class="chip-row">
                <span class="pill-chip">${escapeHtml(connection.connection_id)}</span>
                <span class="pill-chip">${connection.default ? "default" : "secondary"}</span>
              </div>
              <div class="action-row">
                <button onclick='window.__agentiux.editYouTrackConnection(${JSON.stringify(connection.connection_id)})'>Edit</button>
                <button class="secondary" onclick='window.__agentiux.testYouTrackConnection(${JSON.stringify(connection.connection_id)})'>Test</button>
                <button class="secondary" onclick='window.__agentiux.setDefaultYouTrackConnection(${JSON.stringify(connection.connection_id)})'>Make default</button>
                <button class="secondary" onclick='window.__agentiux.removeYouTrackConnection(${JSON.stringify(connection.connection_id)})'>Remove</button>
              </div>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderYouTrackIssues(items) {
  if (!items?.length) {
    return `<div class="empty-note">No workstream issues recorded for the current workspace.</div>`;
  }
  return `
    <div class="stack-list">
      ${items
        .map(
          (item) => `
            <article class="stack-item">
              <div class="stack-item-head">
                <span class="issue-link-wrap">
                  <a class="issue-link" href="${escapeHtml(item.issue_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.issue_key)}</a>
                  ${renderIssueHoverSummary(item)}
                </span>
                <span class="pill-chip ${toneClass(item.task_status === "blocked" ? "bad" : item.task_status === "planned" ? "warn" : "neutral")}">${escapeHtml(
                  item.task_status || "planned",
                )}</span>
              </div>
              <p>${escapeHtml(item.title || "Untitled issue")}</p>
              <div class="chip-row">
                <span class="pill-chip">${escapeHtml(item.stage_id || "no-stage")}</span>
                <span class="pill-chip">${escapeHtml(`YT est ${item.user_estimate_minutes ?? "n/a"}`)}</span>
                <span class="pill-chip">${escapeHtml(`Codex est ${item.codex_estimate_minutes ?? "n/a"}`)}</span>
                <span class="pill-chip">${escapeHtml(`YT spent ${item.youtrack_spent_minutes ?? 0}`)}</span>
              </div>
              ${
                item.latest_commit
                  ? `<pre class="code-block">${escapeHtml(`Latest commit: ${item.latest_commit.commit_hash} ${item.latest_commit.message}`)}</pre>`
                  : ""
              }
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderYouTrackForm(connections, isEnabled) {
  if (!isEnabled) {
    return `
      <section class="surface-card">
        <div class="section-heading">
          <h3>Connection management</h3>
          <span class="muted-copy">unavailable</span>
        </div>
        <div class="empty-note">YouTrack connection management becomes available after workspace initialization.</div>
      </section>
    `;
  }
  const editing = state.editingConnection;
  const editingId = editing?.connection_id || "";
  const editingLabel = editing?.label || "";
  const editingBaseUrl = editing?.base_url || "";
  const editingProjectScope = (editing?.project_scope || []).join(", ");
  const submitLabel = editing ? "Update connection" : "Add connection";
  return `
    <section class="surface-card">
      <div class="section-heading">
        <h3>${editing ? "Edit connection" : "Add connection"}</h3>
        <span class="muted-copy">${escapeHtml(connections?.length || 0)} total</span>
      </div>
      <div class="form-grid">
        <input id="yt-connection-id" type="hidden" value="${escapeHtml(editingId)}" />
        <label>
          <span>Label</span>
          <input id="yt-label" value="${escapeHtml(editingLabel)}" placeholder="Primary tracker" />
        </label>
        <label>
          <span>Base URL</span>
          <input id="yt-base-url" value="${escapeHtml(editingBaseUrl)}" placeholder="https://tracker.example.com" />
        </label>
        <label>
          <span>Permanent token</span>
          <input id="yt-token" type="password" placeholder="${editing ? "Leave empty to keep current token" : "perm:xxxx"}" />
        </label>
        <label>
          <span>Project scope</span>
          <input id="yt-project-scope" value="${escapeHtml(editingProjectScope)}" placeholder="SL, APP" />
        </label>
        <label class="checkbox-row">
          <input id="yt-default" type="checkbox" ${editing?.default ? "checked" : ""} />
          <span>Use as default connection</span>
        </label>
        <div class="action-row">
          <button onclick="window.__agentiux.submitYouTrackConnection()">${submitLabel}</button>
          ${editing ? `<button class="secondary" onclick="window.__agentiux.clearYouTrackForm()">Cancel</button>` : ""}
        </div>
      </div>
    </section>
  `;
}

function prettyJson(value, fallback = {}) {
  return JSON.stringify(value ?? fallback, null, 2);
}

function renderAuthProfileList(items) {
  if (!items?.length) {
    return `<div class="empty-note">No auth profiles configured for this workspace.</div>`;
  }
  return `
    <div class="stack-list">
      ${items
        .map(
          (item) => `
            <article class="stack-item">
              <div class="stack-item-head">
                <strong>${escapeHtml(item.label || item.profile_id || "profile")}</strong>
                <span class="pill-chip ${toneClass(item.is_default ? "ok" : "neutral")}">${escapeHtml(item.scope_type || "workspace")}</span>
              </div>
              <p>${escapeHtml(item.profile_id || "profile")} · ${escapeHtml(item.scope_ref || "default workspace")}</p>
              <div class="chip-row">
                <span class="pill-chip">${escapeHtml(item.has_secret ? "secret stored" : "no secret")}</span>
                <span class="pill-chip">${escapeHtml(item.is_default ? "default" : "non-default")}</span>
                <span class="pill-chip">${escapeHtml(item.resolver?.kind || "resolver")}</span>
                <span class="pill-chip ${toneClass(item.usage_policy?.default_request_mode === "mutating" ? "warn" : "ok")}">${escapeHtml(
                  item.usage_policy?.default_request_mode || "read_only",
                )}</span>
              </div>
              <div class="chip-row">
                <span class="pill-chip">${escapeHtml(`allowed modes: ${(item.usage_policy?.allowed_request_modes || []).join(", ") || "read_only"}`)}</span>
                <span class="pill-chip">${escapeHtml(`action tags: ${(item.usage_policy?.action_tags || []).join(", ") || "none"}`)}</span>
              </div>
              <div class="action-row">
                <button onclick='window.__agentiux.editAuthProfile(${JSON.stringify(item.profile_id)})'>Edit</button>
                <button class="secondary" onclick='window.__agentiux.resolveAuthProfilePreview(${JSON.stringify(item.profile_id)})'>Resolve</button>
                <button class="secondary" onclick='window.__agentiux.removeAuthProfile(${JSON.stringify(item.profile_id)})'>Remove</button>
              </div>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderAuthProfileForm(auth, isEnabled) {
  if (!isEnabled) {
    return `
      <section class="surface-card">
        <div class="section-heading">
          <h3>Auth profile management</h3>
          <span class="muted-copy">unavailable</span>
        </div>
        <div class="empty-note">Auth profile management becomes available after workspace initialization.</div>
      </section>
    `;
  }
  const editing = state.editingAuthProfile;
  const profileJson = editing
    ? prettyJson(
        {
          profile_id: editing.profile_id,
          label: editing.label,
          scope_type: editing.scope_type,
          scope_ref: editing.scope_ref,
          is_default: editing.is_default,
          resolver: editing.resolver,
          artifact_policy: editing.artifact_policy,
          usage_policy: editing.usage_policy,
          notes: editing.notes,
        },
        {},
      )
    : prettyJson(
        {
          label: "",
          scope_type: "workspace",
          scope_ref: null,
          is_default: false,
          usage_policy: {
            default_request_mode: "read_only",
            allowed_request_modes: ["read_only"],
            allowed_surface_modes: [],
            action_tags: [],
            allow_session_persistence: true,
            allow_session_refresh: true,
            notes: null,
          },
        },
        {},
      );
  const preview = state.authResolvePreview;
  return `
    <section class="surface-card">
      <div class="section-heading">
        <h3>${editing ? "Edit auth profile" : "Add auth profile"}</h3>
        <span class="muted-copy">${escapeHtml(auth?.items?.length || 0)} total</span>
      </div>
      <div class="form-grid">
        <label>
          <span>Profile JSON</span>
          <textarea id="auth-profile-json" rows="16" placeholder='{"label":"Read-only API user","scope_type":"workspace","is_default":true,"usage_policy":{"default_request_mode":"read_only","allowed_request_modes":["read_only"]}}'>${escapeHtml(profileJson)}</textarea>
        </label>
        <label>
          <span>Secret JSON</span>
          <textarea id="auth-secret-json" rows="12" placeholder='{"login":"reader@example.com","password":"secret"}'></textarea>
        </label>
        <label>
          <span>Resolve request mode</span>
          <select id="auth-resolve-request-mode">
            <option value="read_only">read_only</option>
            <option value="mutating">mutating</option>
          </select>
        </label>
        <label>
          <span>Resolve action tags</span>
          <input id="auth-resolve-action-tags" placeholder="tag.read, tag.write" />
        </label>
        <label>
          <span>Resolve context JSON</span>
          <textarea id="auth-resolve-context-json" rows="8" placeholder='{"opaque_ref":"value"}'></textarea>
        </label>
        <label>
          <span>Resolve session binding JSON</span>
          <textarea id="auth-resolve-binding-json" rows="6" placeholder='{"primary_ref":"backend.shared","refs":["backend.shared","https://api-a.example","https://api-b.example"]}'></textarea>
        </label>
        <div class="chip-row">
          <label class="checkbox-row">
            <input id="auth-resolve-prefer-cached" type="checkbox" checked />
            <span>Prefer cached session</span>
          </label>
          <label class="checkbox-row">
            <input id="auth-resolve-force-refresh" type="checkbox" />
            <span>Force refresh</span>
          </label>
        </div>
        <div class="action-row">
          <button onclick="window.__agentiux.submitAuthProfile()">${editing ? "Update profile" : "Add profile"}</button>
          ${editing ? `<button class="secondary" onclick="window.__agentiux.clearAuthProfileForm()">Cancel</button>` : ""}
        </div>
      </div>
      ${
        preview
          ? `<details class="disclosure" open>
              <summary>Resolve preview</summary>
              <pre class="code-block">${escapeHtml(prettyJson(preview, {}))}</pre>
            </details>`
          : ""
      }
    </section>
  `;
}

function renderAuthSessionList(items) {
  if (!items?.length) {
    return `<div class="empty-note">No persisted auth sessions for this workspace.</div>`;
  }
  return `
    <div class="stack-list">
      ${items
        .map(
          (item) => `
            <article class="stack-item">
              <div class="stack-item-head">
                <strong>${escapeHtml(item.session_id || "session")}</strong>
                <span class="pill-chip ${toneClass(item.status === "active" ? "ok" : item.status === "expired" ? "warn" : "bad")}">${escapeHtml(
                  item.status || "active",
                )}</span>
              </div>
              <p>${escapeHtml(item.profile_id || "profile")} · ${escapeHtml(item.source_kind || "manual")} · ${escapeHtml(item.artifact_type || "artifact")}</p>
              <div class="chip-row">
                <span class="pill-chip ${toneClass(item.request_mode === "mutating" ? "warn" : "ok")}">${escapeHtml(item.request_mode || "read_only")}</span>
                <span class="pill-chip">${escapeHtml(`action tags: ${(item.action_tags || []).join(", ") || "none"}`)}</span>
                <span class="pill-chip">${escapeHtml(`expires: ${item.expires_state || "unknown"}`)}</span>
              </div>
              <div class="chip-row">
                <span class="pill-chip">${escapeHtml(item.has_secret ? "secret stored" : "no secret")}</span>
                <span class="pill-chip">${escapeHtml(`refresh: ${item.refresh_expires_state || "unknown"}`)}</span>
                ${
                  item.session_binding?.refs?.length
                    ? `<span class="pill-chip">${escapeHtml(`binding refs: ${item.session_binding.refs.join(", ")}`)}</span>`
                    : ""
                }
              </div>
              <div class="action-row">
                <button onclick='window.__agentiux.editAuthSession(${JSON.stringify(item.session_id)})'>Edit</button>
                <button class="secondary" onclick='window.__agentiux.invalidateAuthSession(${JSON.stringify(item.session_id)})'>Invalidate</button>
                <button class="secondary" onclick='window.__agentiux.removeAuthSession(${JSON.stringify(item.session_id)})'>Remove</button>
              </div>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderAuthSessionForm(auth, isEnabled) {
  if (!isEnabled) {
    return "";
  }
  const editing = state.editingAuthSession;
  const sessionJson = editing
    ? prettyJson(
        {
          session_id: editing.session_id,
          profile_id: editing.profile_id,
          source_kind: editing.source_kind,
          request_mode: editing.request_mode,
          action_tags: editing.action_tags,
          session_binding: editing.session_binding,
          status: editing.status,
          artifact_type: editing.artifact_type,
          access_expires_at: editing.access_expires_at,
          refresh_expires_at: editing.refresh_expires_at,
          summary: editing.summary,
        },
        {},
      )
    : prettyJson(
        {
          profile_id: auth?.items?.[0]?.profile_id || "",
          source_kind: "manual",
          request_mode: "read_only",
          action_tags: [],
          session_binding: {
            primary_ref: "backend.shared",
            refs: ["backend.shared", "https://api-a.example", "https://api-b.example"],
          },
        },
        {},
      );
  return `
    <section class="surface-card">
      <div class="section-heading">
        <h3>${editing ? "Edit auth session" : "Add auth session"}</h3>
        <span class="muted-copy">${escapeHtml(auth?.sessions?.items?.length || 0)} total</span>
      </div>
      <div class="form-grid">
        <label>
          <span>Session JSON</span>
          <textarea id="auth-session-json" rows="12" placeholder='{"profile_id":"reader","source_kind":"manual","request_mode":"read_only","action_tags":["tag.read"],"session_binding":{"primary_ref":"backend.shared","refs":["backend.shared","https://api-a.example"]}}'>${escapeHtml(sessionJson)}</textarea>
        </label>
        <label>
          <span>Secret JSON</span>
          <textarea id="auth-session-secret-json" rows="12" placeholder='{"access_token":"token","refresh_token":"refresh"}'></textarea>
        </label>
        <p class="muted-copy">Manual intake supports login/password, direct token bundles, cookies, and storage_state through the secret JSON payload.</p>
        <div class="action-row">
          <button onclick="window.__agentiux.submitAuthSession()">${editing ? "Update session" : "Add session"}</button>
          ${editing ? `<button class="secondary" onclick="window.__agentiux.clearAuthSessionForm()">Cancel</button>` : ""}
        </div>
      </div>
    </section>
  `;
}

function renderIntegrationsPanel(cockpit) {
  const integrations = cockpit.integrations || {};
  const auth = integrations.auth || {};
  const authSummary = auth.summary || {};
  const youtrack = integrations.youtrack || {};
  const summary = youtrack.summary || {};
  const currentSearch = youtrack.current_search_session || null;
  const currentPlan = youtrack.current_plan || null;
  const connections = youtrack.connections?.items || [];
  return `
    <div class="content-grid" data-screen-id="cockpit-integrations-panel" data-panel="integrations" data-testid="cockpit-integrations-panel">
      <section class="surface-card emphasis-card">
        <div class="section-heading">
          <h3>Integration state</h3>
          <span class="pill-chip ${toneClass(connections.length ? "ok" : "warn")}">${escapeHtml(connections.length ? "connected" : "needs setup")}</span>
        </div>
        ${renderMetricGrid(integrations.summary_cards || [])}
        <div class="chip-row">
          <span class="pill-chip">${escapeHtml(`auth profiles: ${auth.summary?.profile_count || 0}`)}</span>
          <span class="pill-chip">${escapeHtml(`active sessions: ${authSummary.active_session_count || 0}`)}</span>
          <span class="pill-chip">${escapeHtml(`mutating sessions: ${authSummary.mutating_session_count || 0}`)}</span>
          <span class="pill-chip">${escapeHtml(`policy alerts: ${authSummary.policy_mismatch_count || 0}`)}</span>
          <span class="pill-chip">${escapeHtml(`workstream issues: ${summary.current_workstream_issue_count || 0}`)}</span>
          <span class="pill-chip">${escapeHtml(`default: ${summary.default_connection_id || "none"}`)}</span>
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Auth profiles</h3>
          <span class="muted-copy">${escapeHtml(auth.items?.length || 0)}</span>
        </div>
        ${renderAuthProfileList(auth.items || [])}
      </section>
      ${renderAuthProfileForm(auth, cockpit.state_kind === "initialized")}
      <section class="surface-card">
        <div class="section-heading">
          <h3>Auth sessions</h3>
          <span class="muted-copy">${escapeHtml(auth.sessions?.items?.length || 0)}</span>
        </div>
        ${renderAuthSessionList(auth.sessions?.items || [])}
      </section>
      ${renderAuthSessionForm(auth, cockpit.state_kind === "initialized")}
      <section class="surface-card">
        <div class="section-heading">
          <h3>Connections</h3>
          <span class="muted-copy">${escapeHtml(connections.length)}</span>
        </div>
        ${renderConnectionList(connections)}
      </section>
      ${renderYouTrackForm(connections, cockpit.state_kind === "initialized")}
      <section class="surface-card">
        <div class="section-heading">
          <h3>Search and plan state</h3>
          <span class="muted-copy">YouTrack</span>
        </div>
        <div class="stack-list">
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Current search session</strong>
              <span class="pill-chip">${escapeHtml(currentSearch?.session_id || "none")}</span>
            </div>
            <p>${escapeHtml(currentSearch?.resolved_query || "No persisted search session.")}</p>
            <div class="chip-row">
              <span class="pill-chip">${escapeHtml(`results ${currentSearch?.result_count ?? 0}`)}</span>
              <span class="pill-chip">${escapeHtml(`shortlist ${currentSearch?.shortlist_count ?? 0}`)}</span>
            </div>
          </article>
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Current plan</strong>
              <span class="pill-chip ${toneClass(currentPlan?.status === "applied" ? "ok" : "warn")}">${escapeHtml(
                currentPlan?.status || "none",
              )}</span>
            </div>
            <p>${escapeHtml(currentPlan?.plan_id || "No persisted YouTrack plan.")}</p>
            <div class="chip-row">
              <span class="pill-chip">${escapeHtml(`issues ${currentPlan?.selected_issue_count ?? 0}`)}</span>
              <span class="pill-chip">${escapeHtml(`stages ${currentPlan?.stage_count ?? 0}`)}</span>
            </div>
          </article>
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Current workstream issue cards</h3>
          <span class="muted-copy">${escapeHtml(youtrack.current_workstream_issues?.items?.length || 0)}</span>
        </div>
        ${renderYouTrackIssues(youtrack.current_workstream_issues?.items || [])}
      </section>
    </div>
  `;
}

function parseCommaList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function renderProjectNoteList(items) {
  if (!items?.length) {
    return `<div class="empty-note">No project memory notes recorded yet.</div>`;
  }
  return `
    <div class="stack-list">
      ${items
        .map(
          (item) => `
            <article class="stack-item">
              <div class="stack-item-head">
                <strong>${escapeHtml(item.title || item.note_id || "note")}</strong>
                <span class="pill-chip ${toneClass(item.pin_state === "pinned" ? "ok" : item.status === "archived" ? "warn" : "neutral")}">${escapeHtml(
                  item.pin_state === "pinned" ? "pinned" : item.status || "active",
                )}</span>
              </div>
              <p>${escapeHtml(item.preview || "No preview available.")}</p>
              <div class="chip-row">
                <span class="pill-chip">${escapeHtml(item.note_id || "note")}</span>
                <span class="pill-chip">${escapeHtml((item.tags || []).join(", ") || "no-tags")}</span>
              </div>
              <div class="action-row">
                <button onclick='window.__agentiux.editProjectNote(${JSON.stringify(item.note_id)})'>Edit</button>
                <button class="secondary" onclick='window.__agentiux.archiveProjectNote(${JSON.stringify(item.note_id)})'>Archive</button>
              </div>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderProjectNoteForm(cockpit) {
  if (cockpit.state_kind !== "initialized") {
    return `
      <section class="surface-card">
        <div class="section-heading">
          <h3>Project note editor</h3>
          <span class="muted-copy">unavailable</span>
        </div>
        <div class="empty-note">Project memory editing becomes available after workspace initialization.</div>
      </section>
    `;
  }
  const note = state.editingNote || {};
  return `
    <section class="surface-card">
      <div class="section-heading">
        <h3>${state.editingNote ? "Edit project note" : "Add project note"}</h3>
        <span class="muted-copy">${escapeHtml(cockpit.memory?.project_notes?.items?.length || 0)} total</span>
      </div>
      <div class="form-grid">
        <input id="note-id" type="hidden" value="${escapeHtml(note.note_id || "")}" />
        <label>
          <span>Title</span>
          <input id="note-title" value="${escapeHtml(note.title || "")}" placeholder="Checkout test notes" />
        </label>
        <label>
          <span>Tags</span>
          <input id="note-tags" value="${escapeHtml((note.tags || []).join(", "))}" placeholder="checkout, e2e" />
        </label>
        <label>
          <span>Status</span>
          <select id="note-status">
            <option value="active" ${note.status === "archived" ? "" : "selected"}>active</option>
            <option value="archived" ${note.status === "archived" ? "selected" : ""}>archived</option>
          </select>
        </label>
        <label>
          <span>Pin state</span>
          <select id="note-pin-state">
            <option value="normal" ${note.pin_state === "pinned" ? "" : "selected"}>normal</option>
            <option value="pinned" ${note.pin_state === "pinned" ? "selected" : ""}>pinned</option>
          </select>
        </label>
        <label>
          <span>Source</span>
          <select id="note-source">
            <option value="chat" ${note.source === "web" || note.source === "system" ? "" : "selected"}>chat</option>
            <option value="web" ${note.source === "web" ? "selected" : ""}>web</option>
            <option value="system" ${note.source === "system" ? "selected" : ""}>system</option>
          </select>
        </label>
        <label>
          <span>Body</span>
          <textarea id="note-body" rows="10" placeholder="Markdown note body">${escapeHtml(note.body_markdown || "")}</textarea>
        </label>
        <div class="action-row">
          <button onclick="window.__agentiux.submitProjectNote()">${state.editingNote ? "Update note" : "Add note"}</button>
          ${state.editingNote ? `<button class="secondary" onclick="window.__agentiux.clearProjectNoteForm()">Cancel</button>` : ""}
        </div>
      </div>
    </section>
  `;
}

function renderLearningList(items) {
  if (!items?.length) {
    return `<div class="empty-note">No learning entries recorded yet.</div>`;
  }
  return `
    <div class="stack-list">
      ${items
        .map(
          (item) => `
            <article class="stack-item">
              <div class="stack-item-head">
                <strong>${escapeHtml(item.entry_id || "learning")}</strong>
                <span class="pill-chip ${toneClass(item.status === "open" ? "warn" : item.status === "resolved" ? "ok" : "neutral")}">${escapeHtml(
                  item.status || "open",
                )}</span>
              </div>
              <p>${escapeHtml(item.symptom || "No symptom recorded.")}</p>
              <div class="chip-row">
                <span class="pill-chip">${escapeHtml(item.kind || "general")}</span>
                <span class="pill-chip">${escapeHtml(item.run_id || "no-run")}</span>
                <span class="pill-chip">${escapeHtml(item.task_id || "no-task")}</span>
              </div>
              <div class="action-row">
                <button onclick='window.__agentiux.editLearningEntry(${JSON.stringify(item.entry_id)})'>Edit</button>
              </div>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderLearningForm(cockpit) {
  if (cockpit.state_kind !== "initialized") {
    return `
      <section class="surface-card">
        <div class="section-heading">
          <h3>Learning entry editor</h3>
          <span class="muted-copy">unavailable</span>
        </div>
        <div class="empty-note">Learning entry editing becomes available after workspace initialization.</div>
      </section>
    `;
  }
  const entry = state.editingLearning || {};
  return `
    <section class="surface-card">
      <div class="section-heading">
        <h3>${state.editingLearning ? "Edit learning entry" : "Add learning entry"}</h3>
        <span class="muted-copy">${escapeHtml(cockpit.memory?.learnings?.items?.length || 0)} total</span>
      </div>
      <div class="form-grid">
        <input id="learning-entry-id" type="hidden" value="${escapeHtml(entry.entry_id || "")}" />
        <label>
          <span>Kind</span>
          <input id="learning-kind" value="${escapeHtml(entry.kind || "")}" placeholder="visual-review" />
        </label>
        <label>
          <span>Status</span>
          <select id="learning-status">
            <option value="open" ${entry.status === "resolved" || entry.status === "archived" ? "" : "selected"}>open</option>
            <option value="resolved" ${entry.status === "resolved" ? "selected" : ""}>resolved</option>
            <option value="archived" ${entry.status === "archived" ? "selected" : ""}>archived</option>
          </select>
        </label>
        <label>
          <span>Symptom</span>
          <textarea id="learning-symptom" rows="3" placeholder="What went wrong?">${escapeHtml(entry.symptom || "")}</textarea>
        </label>
        <label>
          <span>Root cause</span>
          <textarea id="learning-root-cause" rows="3" placeholder="Root cause">${escapeHtml(entry.root_cause || "")}</textarea>
        </label>
        <label>
          <span>Missing signal</span>
          <textarea id="learning-missing-signal" rows="3" placeholder="What signal was missing?">${escapeHtml(entry.missing_signal || "")}</textarea>
        </label>
        <label>
          <span>Fix applied</span>
          <textarea id="learning-fix-applied" rows="3" placeholder="What fixed it?">${escapeHtml(entry.fix_applied || "")}</textarea>
        </label>
        <label>
          <span>Prevention</span>
          <textarea id="learning-prevention" rows="3" placeholder="How to prevent it next time">${escapeHtml(entry.prevention || "")}</textarea>
        </label>
        <div class="action-row">
          <button onclick="window.__agentiux.submitLearningEntry()">${state.editingLearning ? "Update learning" : "Add learning"}</button>
          ${state.editingLearning ? `<button class="secondary" onclick="window.__agentiux.clearLearningEntryForm()">Cancel</button>` : ""}
        </div>
      </div>
    </section>
  `;
}

function renderMemoryPanel(cockpit) {
  const memory = cockpit.memory || {};
  const projectNotes = memory.project_notes || {};
  const analytics = memory.analytics || {};
  return `
    <div class="content-grid" data-screen-id="cockpit-memory-panel" data-panel="memory" data-testid="cockpit-memory-panel">
      <section class="surface-card emphasis-card">
        <div class="section-heading">
          <h3>Memory and learnings</h3>
          <span class="pill-chip ${toneClass((memory.learnings?.counts?.open || 0) > 0 ? "warn" : "ok")}">${escapeHtml(
            (memory.learnings?.counts?.open || 0) > 0 ? "needs review" : "stable",
          )}</span>
        </div>
        ${renderMetricGrid(memory.summary_cards || [])}
        <div class="chip-row">
          <span class="pill-chip">${escapeHtml(`workspace events: ${analytics.event_counts?.workspace_total || 0}`)}</span>
          <span class="pill-chip">${escapeHtml(`recent learnings: ${analytics.recent_learning_entries?.length || 0}`)}</span>
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Project memory</h3>
          <span class="muted-copy">${escapeHtml(projectNotes.items?.length || 0)}</span>
        </div>
        ${renderProjectNoteList(projectNotes.items || [])}
      </section>
      ${renderProjectNoteForm(cockpit)}
      <section class="surface-card">
        <div class="section-heading">
          <h3>Learnings</h3>
          <span class="muted-copy">${escapeHtml(memory.learnings?.items?.length || 0)}</span>
        </div>
        ${renderLearningList(memory.learnings?.items || [])}
      </section>
      ${renderLearningForm(cockpit)}
    </div>
  `;
}

function renderDiagnosticsPanel(cockpit) {
  const diagnostics = cockpit.diagnostics || {};
  const audit = diagnostics.audit;
  const upgradePlan = diagnostics.upgrade_plan;
  const design = diagnostics.design || {};
  return `
    <div class="content-grid" data-screen-id="cockpit-diagnostics-panel" data-panel="diagnostics" data-testid="cockpit-diagnostics-panel">
      <section class="surface-card">
        <div class="section-heading">
          <h3>Workspace detection</h3>
          <span class="muted-copy">${escapeHtml(diagnostics.host_support?.host_os || "unknown")}</span>
        </div>
        ${renderMetricGrid([
          { label: "Host OS", value: diagnostics.host_support?.host_os || "unknown" },
          { label: "Infra mode", value: diagnostics.host_support?.infra_mode || "unknown" },
          { label: "Orchestration", value: diagnostics.host_support?.orchestration || "n/a" },
          { label: "Plugin platform", value: diagnostics.plugin_platform?.enabled ? "enabled" : "disabled", tone: diagnostics.plugin_platform?.enabled ? "ok" : "neutral" },
        ])}
        <details class="disclosure">
          <summary>Detected stacks</summary>
          <pre class="code-block">${escapeHtml((diagnostics.detected_stacks || []).join("\n") || "No stack signals recorded.")}</pre>
        </details>
        <details class="disclosure">
          <summary>Selected profiles</summary>
          <pre class="code-block">${escapeHtml((diagnostics.selected_profiles || []).join("\n") || "No selected profiles recorded.")}</pre>
        </details>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Audit and upgrade</h3>
          <span class="muted-copy">secondary</span>
        </div>
        <div class="stack-list">
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Current audit</strong>
              <span class="pill-chip ${toneClass(audit?.gaps?.length ? "warn" : "neutral")}">${escapeHtml(audit?.audit_id || "none")}</span>
            </div>
            ${
              audit
                ? `<pre class="code-block">${escapeHtml(
                    [`Initialized: ${audit.initialized}`, `Gaps: ${(audit.gaps || []).length}`, ...(audit.gaps || []).map((gap) => `${gap.gap_id}: ${gap.title}`)].join(
                      "\n",
                    ),
                  )}</pre>`
                : `<div class="empty-note">No audit recorded.</div>`
            }
          </article>
          <article class="stack-item">
            <div class="stack-item-head">
              <strong>Upgrade plan</strong>
              <span class="pill-chip ${toneClass(upgradePlan?.status === "applied" ? "ok" : "neutral")}">${escapeHtml(
                upgradePlan?.status || "none",
              )}</span>
            </div>
            ${
              upgradePlan
                ? `<pre class="code-block">${escapeHtml(
                    [
                      `Plan ID: ${upgradePlan.plan_id || "n/a"}`,
                      `Created workstream: ${upgradePlan.created_workstream_id || "none"}`,
                      `Tasks: ${(upgradePlan.created_task_ids || []).join(", ") || "none"}`,
                    ].join("\n"),
                  )}</pre>`
                : `<div class="empty-note">No upgrade plan recorded.</div>`
            }
          </article>
        </div>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>Design traces</h3>
          <span class="muted-copy">debug</span>
        </div>
        <details class="disclosure">
          <summary>Brief</summary>
          <pre class="code-block">${escapeHtml(JSON.stringify(design.brief || {}, null, 2) || "{}")}</pre>
        </details>
        <details class="disclosure">
          <summary>Reference board</summary>
          <pre class="code-block">${escapeHtml(JSON.stringify(design.reference_board || {}, null, 2) || "{}")}</pre>
        </details>
        <details class="disclosure">
          <summary>Handoff</summary>
          <pre class="code-block">${escapeHtml(JSON.stringify(design.handoff || {}, null, 2) || "{}")}</pre>
        </details>
      </section>
      <section class="surface-card">
        <div class="section-heading">
          <h3>State paths</h3>
          <span class="muted-copy">${escapeHtml(diagnostics.paths?.length || 0)}</span>
        </div>
        <details class="disclosure" open>
          <summary>External paths</summary>
          <pre class="code-block">${escapeHtml(
            (diagnostics.paths || []).map((item) => `${item.key}: ${item.value}`).join("\n") || "No paths recorded.",
          )}</pre>
        </details>
      </section>
    </div>
  `;
}

function renderActivePanel(cockpit) {
  if (state.panelError && !currentPanelPayload()) {
    return `<section class="surface-card"><div class="error-state compact-error">${escapeHtml(state.panelError)}</div></section>`;
  }
  if (state.panelLoading && !currentPanelPayload()) {
    return `<section class="surface-card"><div class="empty-note panel-loading">Loading ${escapeHtml(state.panel)} panel...</div></section>`;
  }
  switch (state.panel) {
    case "plan":
      return renderPlanPanel(cockpit);
    case "quality":
      return renderQualityPanel(cockpit);
    case "integrations":
      return renderIntegrationsPanel(cockpit);
    case "memory":
      return renderMemoryPanel(cockpit);
    case "diagnostics":
      return renderDiagnosticsPanel(cockpit);
    case "now":
    default:
      return renderNowPanel(cockpit);
  }
}

function renderCockpit(cockpit) {
  return `
    <section class="page-shell" data-screen-id="workspace-cockpit" data-testid="workspace-cockpit" data-selected-workspace="${escapeHtml(
      cockpit.workspace_path || "",
    )}" data-selected-panel="${escapeHtml(state.panel)}">
      ${renderHero(cockpit)}
      ${renderTabs(cockpit)}
      ${renderActivePanel(cockpit)}
    </section>
  `;
}

async function submitYouTrackConnection() {
  const workspacePath = state.selectedWorkspace;
  if (!workspacePath || state.cockpitShell?.state_kind !== "initialized") return;
  const connectionId = document.getElementById("yt-connection-id")?.value || "";
  const label = document.getElementById("yt-label")?.value || "";
  const baseUrl = document.getElementById("yt-base-url")?.value || "";
  const token = document.getElementById("yt-token")?.value || "";
  const projectScope = (document.getElementById("yt-project-scope")?.value || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const isDefault = Boolean(document.getElementById("yt-default")?.checked);
  const body = {
    workspacePath,
    label,
    baseUrl,
    token: token || undefined,
    projectScope,
    default: isDefault,
  };
  if (connectionId) {
    body.connectionId = connectionId;
    await apiJson("/api/youtrack/connections", { method: "PATCH", body: JSON.stringify(body) });
  } else {
    await apiJson("/api/youtrack/connections", { method: "POST", body: JSON.stringify(body) });
  }
  state.editingConnection = null;
  await reloadActiveWorkspacePanel("integrations");
}

function clearYouTrackForm() {
  state.editingConnection = null;
  render();
}

async function testYouTrackConnection(connectionId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  await apiJson(`/api/youtrack/connections/${encodeURIComponent(connectionId)}/test`, {
    method: "POST",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace }),
  });
  await reloadActiveWorkspacePanel("integrations");
}

async function removeYouTrackConnection(connectionId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  await apiJson("/api/youtrack/connections", {
    method: "DELETE",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace, connectionId }),
  });
  if (state.editingConnection?.connection_id === connectionId) {
    state.editingConnection = null;
  }
  await reloadActiveWorkspacePanel("integrations");
}

async function setDefaultYouTrackConnection(connectionId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  await apiJson("/api/youtrack/connections", {
    method: "PATCH",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace, connectionId, default: true, testConnection: false }),
  });
  await reloadActiveWorkspacePanel("integrations");
}

function editYouTrackConnection(connectionId) {
  const integrationsPanel = state.panelPayloads.integrations || currentPanelPayload() || {};
  const connections = integrationsPanel?.youtrack?.connections?.items || [];
  state.editingConnection = connections.find((item) => item.connection_id === connectionId) || null;
  state.panel = "integrations";
  render();
}

function parseJsonTextarea(elementId, fallback = null) {
  const raw = document.getElementById(elementId)?.value || "";
  if (!raw.trim()) {
    return fallback;
  }
  return JSON.parse(raw);
}

async function submitAuthProfile() {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  const profile = parseJsonTextarea("auth-profile-json", {});
  const secretPayload = parseJsonTextarea("auth-secret-json", null);
  await apiJson("/api/auth/profiles", {
    method: state.editingAuthProfile ? "PATCH" : "POST",
    body: JSON.stringify({
      workspacePath: state.selectedWorkspace,
      profile,
      secretPayload,
    }),
  });
  state.editingAuthProfile = null;
  state.authResolvePreview = null;
  await reloadActiveWorkspacePanel("integrations");
}

function clearAuthProfileForm() {
  state.editingAuthProfile = null;
  state.authResolvePreview = null;
  render();
}

function editAuthProfile(profileId) {
  const integrationsPanel = state.panelPayloads.integrations || currentPanelPayload() || {};
  const authProfiles = integrationsPanel?.auth?.items || [];
  state.editingAuthProfile = authProfiles.find((item) => item.profile_id === profileId) || null;
  state.authResolvePreview = null;
  state.panel = "integrations";
  render();
}

async function resolveAuthProfilePreview(profileId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  const requestMode = document.getElementById("auth-resolve-request-mode")?.value || "read_only";
  const actionTags = parseCommaList(document.getElementById("auth-resolve-action-tags")?.value || "");
  const sessionBinding = parseJsonTextarea("auth-resolve-binding-json", null);
  const contextOverrides = parseJsonTextarea("auth-resolve-context-json", null);
  const preferCached = Boolean(document.getElementById("auth-resolve-prefer-cached")?.checked);
  const forceRefresh = Boolean(document.getElementById("auth-resolve-force-refresh")?.checked);
  state.authResolvePreview = await apiJson("/api/auth/profiles/resolve", {
    method: "POST",
    body: JSON.stringify({
      workspacePath: state.selectedWorkspace,
      profileId,
      requestMode,
      actionTags,
      sessionBinding,
      contextOverrides,
      preferCached,
      forceRefresh,
    }),
  });
  state.panel = "integrations";
  render();
}

async function removeAuthProfile(profileId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  await apiJson("/api/auth/profiles", {
    method: "DELETE",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace, profileId }),
  });
  if (state.editingAuthProfile?.profile_id === profileId) {
    state.editingAuthProfile = null;
  }
  state.authResolvePreview = null;
  await reloadActiveWorkspacePanel("integrations");
}

async function submitAuthSession() {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  const session = parseJsonTextarea("auth-session-json", {});
  const secretPayload = parseJsonTextarea("auth-session-secret-json", null);
  await apiJson("/api/auth/sessions", {
    method: state.editingAuthSession ? "PATCH" : "POST",
    body: JSON.stringify({
      workspacePath: state.selectedWorkspace,
      session,
      secretPayload,
    }),
  });
  state.editingAuthSession = null;
  await reloadActiveWorkspacePanel("integrations");
}

function clearAuthSessionForm() {
  state.editingAuthSession = null;
  render();
}

function editAuthSession(sessionId) {
  const integrationsPanel = state.panelPayloads.integrations || currentPanelPayload() || {};
  const authSessions = integrationsPanel?.auth?.sessions?.items || [];
  state.editingAuthSession = authSessions.find((item) => item.session_id === sessionId) || null;
  state.panel = "integrations";
  render();
}

async function invalidateAuthSession(sessionId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  await apiJson(`/api/auth/sessions/${encodeURIComponent(sessionId)}/invalidate`, {
    method: "POST",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace }),
  });
  if (state.editingAuthSession?.session_id === sessionId) {
    state.editingAuthSession = null;
  }
  await reloadActiveWorkspacePanel("integrations");
}

async function removeAuthSession(sessionId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  await apiJson(`/api/auth/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace }),
  });
  if (state.editingAuthSession?.session_id === sessionId) {
    state.editingAuthSession = null;
  }
  await reloadActiveWorkspacePanel("integrations");
}

async function submitProjectNote() {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  const noteId = document.getElementById("note-id")?.value || "";
  const note = {
    note_id: noteId || undefined,
    title: document.getElementById("note-title")?.value || "",
    tags: parseCommaList(document.getElementById("note-tags")?.value || ""),
    status: document.getElementById("note-status")?.value || "active",
    pin_state: document.getElementById("note-pin-state")?.value || "normal",
    source: document.getElementById("note-source")?.value || "web",
    body_markdown: document.getElementById("note-body")?.value || "",
  };
  if (state.editingNote?.note_id) {
    await apiJson(`/api/project-notes/${encodeURIComponent(state.editingNote.note_id)}`, {
      method: "PATCH",
      body: JSON.stringify({ workspacePath: state.selectedWorkspace, note }),
    });
  } else {
    await apiJson("/api/project-notes", {
      method: "POST",
      body: JSON.stringify({ workspacePath: state.selectedWorkspace, note }),
    });
  }
  state.editingNote = null;
  await reloadActiveWorkspacePanel("memory");
}

function clearProjectNoteForm() {
  state.editingNote = null;
  render();
}

async function editProjectNote(noteId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  state.editingNote = await apiJson(`/api/project-notes/${encodeURIComponent(noteId)}?workspace=${encodeURIComponent(state.selectedWorkspace)}`);
  state.panel = "memory";
  render();
}

async function archiveProjectNote(noteId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  await apiJson(`/api/project-notes/${encodeURIComponent(noteId)}/archive`, {
    method: "POST",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace }),
  });
  if (state.editingNote?.note_id === noteId) {
    state.editingNote = null;
  }
  await reloadActiveWorkspacePanel("memory");
}

async function submitLearningEntry() {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  const entryId = document.getElementById("learning-entry-id")?.value || "";
  const entry = {
    entry_id: entryId || undefined,
    kind: document.getElementById("learning-kind")?.value || "general",
    status: document.getElementById("learning-status")?.value || "open",
    symptom: document.getElementById("learning-symptom")?.value || "",
    root_cause: document.getElementById("learning-root-cause")?.value || "",
    missing_signal: document.getElementById("learning-missing-signal")?.value || "",
    fix_applied: document.getElementById("learning-fix-applied")?.value || "",
    prevention: document.getElementById("learning-prevention")?.value || "",
    source: "web",
  };
  if (state.editingLearning?.entry_id) {
    await apiJson(`/api/learnings/${encodeURIComponent(state.editingLearning.entry_id)}`, {
      method: "PATCH",
      body: JSON.stringify({ workspacePath: state.selectedWorkspace, updates: entry }),
    });
  } else {
    await apiJson("/api/learnings", {
      method: "POST",
      body: JSON.stringify({ workspacePath: state.selectedWorkspace, entry }),
    });
  }
  state.editingLearning = null;
  await reloadActiveWorkspacePanel("memory");
}

function clearLearningEntryForm() {
  state.editingLearning = null;
  render();
}

async function editLearningEntry(entryId) {
  if (!state.selectedWorkspace || state.cockpitShell?.state_kind !== "initialized") return;
  state.editingLearning = await apiJson(`/api/learnings/${encodeURIComponent(entryId)}?workspace=${encodeURIComponent(state.selectedWorkspace)}`);
  state.panel = "memory";
  render();
}

function render() {
  if (state.loading) {
    appRoot.innerHTML = `<div class="loading-shell">Loading AgentiUX Dev dashboard...</div>`;
    return;
  }
  if (state.error && !state.overviewPayload) {
    appRoot.innerHTML = `<div class="error-state">${escapeHtml(state.error)}</div>`;
    return;
  }
  if (!state.overviewPayload) {
    appRoot.innerHTML = `<div class="empty-state">No dashboard snapshot available.</div>`;
    return;
  }
  const cockpitModel = composeCockpitModel();
  const mainContent = state.selectedWorkspace && cockpitModel ? renderCockpit(cockpitModel) : renderOverviewPage(state.overviewPayload);
  if (state.telemetry.firstUsableRenderMs == null) {
    const readyForOverview = Boolean(state.overviewPayload) && !state.selectedWorkspace;
    const readyForCockpit = Boolean(state.selectedWorkspace && state.cockpitShell && currentPanelPayload());
    if (readyForOverview || readyForCockpit) {
      state.telemetry.firstUsableRenderMs = Math.round(performance.now());
    }
  }
  appRoot.innerHTML = `
    <div class="shell" data-screen-id="dashboard-shell" data-testid="dashboard-shell" data-selected-workspace="${escapeHtml(
      state.selectedWorkspace || "",
    )}" data-selected-panel="${escapeHtml(state.panel)}">
      ${renderSidebar(state.overviewPayload)}
      <main class="main" role="main">
        ${
          state.error
            ? `<div class="error-banner">${escapeHtml(state.error)}</div>`
            : state.panelError && currentPanelPayload()
              ? `<div class="error-banner">${escapeHtml(state.panelError)}</div>`
              : state.refreshing
                ? `<div class="status-banner">Refreshing dashboard snapshot...</div>`
                : state.panelLoading && currentPanelPayload()
                  ? `<div class="status-banner">Loading ${escapeHtml(state.panel)} panel...</div>`
                  : ""
        }
        ${mainContent}
      </main>
    </div>
  `;
}

window.__agentiux = {
  refresh,
  clearSelection,
  selectWorkspace,
  setPanel,
  submitYouTrackConnection,
  clearYouTrackForm,
  testYouTrackConnection,
  removeYouTrackConnection,
  setDefaultYouTrackConnection,
  editYouTrackConnection,
  submitAuthProfile,
  clearAuthProfileForm,
  editAuthProfile,
  resolveAuthProfilePreview,
  removeAuthProfile,
  submitAuthSession,
  clearAuthSessionForm,
  editAuthSession,
  invalidateAuthSession,
  removeAuthSession,
  submitProjectNote,
  clearProjectNoteForm,
  editProjectNote,
  archiveProjectNote,
  submitLearningEntry,
  clearLearningEntryForm,
  editLearningEntry,
  debugSnapshot,
};

function loadRoute(route) {
  const normalizedPanel = normalizePanel(route.panel || "now");
  const sameWorkspace = Boolean(route.workspacePath) && route.workspacePath === state.selectedWorkspace;
  if (sameWorkspace && !route.forceOverview && state.bootstrapped) {
    return loadWorkspacePanel(normalizedPanel, { historyMode: "skip" });
  }
  return loadDashboardBootstrap(route.workspacePath, {
    historyMode: "skip",
    panel: normalizedPanel,
    forceOverview: route.forceOverview,
  });
}

window.addEventListener("popstate", () => {
  loadRoute(parseRoute());
});

const initialRoute = parseRoute();
loadDashboardBootstrap(initialRoute.workspacePath, {
  historyMode: initialRoute.source === "legacy-query" ? "replace" : "skip",
  panel: initialRoute.panel,
  forceOverview: initialRoute.forceOverview,
});
