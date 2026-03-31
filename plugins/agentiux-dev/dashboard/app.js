const appRoot = document.getElementById("app");

const state = {
  snapshot: null,
  selectedWorkspace: null,
  loading: true,
  error: null,
  editingConnection: null,
};

function workspaceRoute(workspacePath) {
  return workspacePath ? `/workspaces/${encodeURIComponent(workspacePath)}` : "/";
}

function parseRoute() {
  const legacyWorkspace = new URLSearchParams(window.location.search).get("workspace");
  const match = window.location.pathname.match(/^\/workspaces\/(.+)$/);
  if (match) {
    return {
      workspacePath: decodeURIComponent(match[1]),
      source: "path",
    };
  }
  if (legacyWorkspace) {
    return {
      workspacePath: legacyWorkspace,
      source: "legacy-query",
    };
  }
  return {
    workspacePath: null,
    source: "overview",
  };
}

function updateRoute(workspacePath, mode = "push") {
  const target = workspaceRoute(workspacePath);
  const current = `${window.location.pathname}${window.location.search}`;
  if (current === target) {
    return;
  }
  const payload = { workspacePath: workspacePath || null };
  if (mode === "replace") {
    window.history.replaceState(payload, "", target);
    return;
  }
  window.history.pushState(payload, "", target);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function statusChip(status) {
  if (["completed", "running", "passed", "active", "approved", "updated"].includes(status)) return "ok";
  if (["failed", "cancelled", "blocked", "hung"].includes(status)) return "bad";
  if (["ready_for_closeout", "slow", "planned", "queued", "draft"].includes(status)) return "warn";
  return "";
}

function formatLines(lines, fallback) {
  return escapeHtml((lines || []).join("\n") || fallback);
}

async function apiJson(url, options = {}) {
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

function renderVerificationSummary(run) {
  if (!run) {
    return `<pre>No verification run recorded yet.</pre>`;
  }
  const summary = run.summary || {};
  return `
    <pre>${escapeHtml(
      [
        `Run ID: ${run.run_id || "unknown"}`,
        `Workstream: ${run.workstream_id || "unknown"}`,
        `Mode: ${run.mode || "unknown"}`,
        `Target: ${run.target_id || "unknown"}`,
        `Status: ${run.status || "unknown"}`,
        `Health: ${run.health || "unknown"}`,
        `Started: ${run.started_at || "not started"}`,
        `Completed: ${run.completed_at || "not completed"}`,
        `Passed cases: ${summary.passed_cases ?? 0}`,
        `Failed cases: ${summary.failed_cases ?? 0}`,
        `Message: ${summary.message || "No summary recorded."}`,
        `Stdout log: ${run.stdout_log_path || "n/a"}`,
        `Stderr log: ${run.stderr_log_path || "n/a"}`,
        `Logcat log: ${run.logcat_log_path || "n/a"}`,
      ].join("\n"),
    )}</pre>
  `;
}

function renderVerificationCases(recipes) {
  const cases = recipes?.cases || [];
  const suites = recipes?.suites || [];
  return `
    <pre>${escapeHtml(
      [
        `Cases: ${cases.length}`,
        ...cases.map((item) => {
          const baseline = item.baseline_source || item.baseline?.source_path || "no project baseline";
          return `${item.id || "case"} [${item.surface_type || "surface"} / ${item.runner || "runner"}] tags=${(item.tags || []).join(", ") || "none"} -> ${baseline}`;
        }),
        "",
        `Suites: ${suites.length}`,
        ...suites.map(
          (item) => `${item.id || "suite"} -> ${Array.isArray(item.case_ids) ? item.case_ids.join(", ") : ""}`,
        ),
      ]
        .filter(Boolean)
        .join("\n") || "No verification recipe recorded.",
    )}</pre>
  `;
}

function renderVerificationSelection(selection) {
  if (!selection) {
    return `<pre>No verification selection has been resolved yet.</pre>`;
  }
  const selectedSuiteId = selection.selected_suite?.id || "none";
  const selectedCaseIds = (selection.selected_cases || []).map((item) => item.case_id).join(", ") || "none";
  const heuristicCaseIds = (selection.heuristic_suggestions || []).map((item) => item.case_id).join(", ") || "none";
  const blockingRequirements =
    (selection.host_compatibility?.blocking_requirements || []).join(", ") || "none";
  return `
    <pre>${escapeHtml(
      [
        `Status: ${selection.selection_status || "unknown"}`,
        `Source: ${selection.source || "unknown"}`,
        `Requested mode: ${selection.requested_mode || "unknown"} (${selection.requested_mode_source || "unknown"})`,
        `Resolved mode: ${selection.resolved_mode || "unknown"}`,
        `Targeted: ${selection.targeted}`,
        `Full suite: ${selection.full_suite}`,
        `Selected suite: ${selectedSuiteId}`,
        `Selected cases: ${selectedCaseIds}`,
        `Heuristic suggestions: ${heuristicCaseIds}`,
        `Baseline sources: ${(selection.baseline_sources || []).join(", ") || "none"}`,
        `Host compatible: ${selection.host_compatibility?.available !== false}`,
        `Blocking requirements: ${blockingRequirements}`,
        `Reason: ${selection.reason || "No reason recorded."}`,
      ].join("\n"),
    )}</pre>
  `;
}

function renderVerificationEvents(events) {
  return `
    <pre>${escapeHtml(
      (events || [])
        .map((event) => `${event.timestamp || ""} ${event.event_type || "event"}: ${event.message || ""}`)
        .join("\n") || "No verification events recorded.",
    )}</pre>
  `;
}

function renderPluginPlatform(pluginPlatform) {
  if (!pluginPlatform?.enabled) {
    return `<pre>No plugin-platform detection for this workspace.</pre>`;
  }
  return `
    <pre>${escapeHtml(
      [
        `Primary plugin root: ${pluginPlatform.primary_plugin_root || "unknown"}`,
        `Detected features: ${(pluginPlatform.detected_features || []).join(", ") || "none"}`,
        `Plugin roots: ${(pluginPlatform.plugin_roots || []).join(", ") || "none"}`,
        `Release readiness: ${pluginPlatform.release_readiness_command || "not available"}`,
      ].join("\n"),
    )}</pre>
  `;
}

function renderYouTrack(detail) {
  const youtrack = detail.youtrack || {};
  const connections = youtrack.connections?.items || [];
  const summary = detail.summary?.youtrack || {};
  const currentSearch = youtrack.current_search_session || null;
  const currentPlan = youtrack.current_plan || null;
  const workstreamIssues = youtrack.current_workstream_issues?.items || [];
  const editing = state.editingConnection;
  const editingId = editing?.connection_id || "";
  const editingLabel = editing?.label || "";
  const editingBaseUrl = editing?.base_url || "";
  const editingProjectScope = (editing?.project_scope || []).join(", ");
  const submitLabel = editing ? "Update connection" : "Add connection";
  return `
    <section class="panel">
      <h3>YouTrack</h3>
      <div class="section-list">
        <div class="section-row">
          <strong>Workspace summary</strong>
          <pre>${escapeHtml(
            [
              `Connections: ${summary.connection_count ?? 0}`,
              `Default connection: ${summary.default_connection_id || "none"}`,
              `Last search session: ${summary.last_search_session_id || "none"}`,
              `Active plan: ${summary.active_plan_id || "none"}`,
              `Current workstream issues: ${summary.current_workstream_issue_count ?? 0}`,
            ].join("\n"),
          )}</pre>
        </div>
        <div class="section-row">
          <strong>Connections</strong>
          <div class="stage-list">
            ${connections
              .map(
                (connection) => `
                  <div class="stage-item">
                    <div><strong>${escapeHtml(connection.label)}</strong></div>
                    <div class="muted">${escapeHtml(connection.base_url)}</div>
                    <div class="workspace-meta">
                      <span class="chip ${statusChip(connection.status)}">${escapeHtml(connection.status)}</span>
                      <span class="chip">${escapeHtml(connection.connection_id)}</span>
                      <span class="chip">${connection.default ? "default" : "secondary"}</span>
                    </div>
                    <div class="workspace-meta">
                      <button onclick='window.__agentiux.editYouTrackConnection(${JSON.stringify(connection.connection_id)})'>Edit</button>
                      <button class="secondary" onclick='window.__agentiux.testYouTrackConnection(${JSON.stringify(connection.connection_id)})'>Test</button>
                      <button class="secondary" onclick='window.__agentiux.setDefaultYouTrackConnection(${JSON.stringify(connection.connection_id)})'>Make default</button>
                      <button class="secondary" onclick='window.__agentiux.removeYouTrackConnection(${JSON.stringify(connection.connection_id)})'>Remove</button>
                    </div>
                  </div>
                `,
              )
              .join("") || `<pre>No YouTrack connections recorded.</pre>`}
          </div>
        </div>
        <div class="section-row">
          <strong>${editing ? "Edit connection" : "Add connection"}</strong>
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
            <div class="workspace-meta">
              <button onclick="window.__agentiux.submitYouTrackConnection()">${submitLabel}</button>
              ${editing ? `<button class="secondary" onclick="window.__agentiux.clearYouTrackForm()">Cancel</button>` : ""}
            </div>
          </div>
        </div>
        <div class="section-row">
          <strong>Search and plan state</strong>
          <pre>${escapeHtml(
            [
              currentSearch
                ? `Search ${currentSearch.session_id}: ${currentSearch.resolved_query} -> results ${currentSearch.result_count ?? "unknown"}, shortlist ${(currentSearch.shortlist_count ?? currentSearch.shortlist?.length) || 0}, page ${currentSearch.shortlist_page?.skip || 0}/${currentSearch.shortlist_page?.page_size || 0}`
                : "No persisted search session.",
              currentPlan
                ? `Plan ${currentPlan.plan_id}: ${currentPlan.status} / selected issues ${(currentPlan.selected_issue_count ?? currentPlan.selected_issue_ids?.length) || 0}`
                : "No persisted YouTrack plan.",
            ].join("\n"),
          )}</pre>
        </div>
        <div class="section-row">
          <strong>Current workstream issue cards</strong>
          <div class="stage-list">
            ${workstreamIssues
              .map(
                (item) => `
                  <div class="stage-item">
                    <div><strong><a href="${escapeHtml(item.issue_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.issue_key)}</a></strong></div>
                    <div class="muted">${escapeHtml(item.title)}</div>
                    <div class="workspace-meta">
                      <span class="chip ${statusChip(item.task_status)}">${escapeHtml(item.task_status || "planned")}</span>
                      <span class="chip">${escapeHtml(item.stage_id || "no-stage")}</span>
                      <span class="chip">YT est ${escapeHtml(item.user_estimate_minutes ?? "n/a")}</span>
                      <span class="chip">Codex est ${escapeHtml(item.codex_estimate_minutes ?? "n/a")}</span>
                      <span class="chip">YT spent ${escapeHtml(item.youtrack_spent_minutes ?? 0)}</span>
                      <span class="chip">Codex spent ${escapeHtml(item.codex_spent_minutes ?? 0)}</span>
                    </div>
                    <pre>${escapeHtml(
                      item.latest_commit
                        ? `Latest commit: ${item.latest_commit.commit_hash} ${item.latest_commit.message}`
                        : "No linked commit recorded yet.",
                    )}</pre>
                  </div>
                `,
              )
              .join("") || `<pre>No YouTrack-linked tasks in the current workstream.</pre>`}
          </div>
        </div>
      </div>
    </section>
  `;
}

async function loadVerificationCoverage(workspacePath) {
  if (!workspacePath) return;
  try {
    const response = await fetch(`/api/verification-coverage?workspace=${encodeURIComponent(workspacePath)}`, {
      cache: "no-store",
    });
    if (!response.ok) return;
    const payload = await response.json();
    if (!state.snapshot?.workspace_detail || state.selectedWorkspace !== workspacePath) {
      return;
    }
    state.snapshot.workspace_detail.verification_coverage_audit = payload;
    render();
  } catch (error) {
    if (!state.snapshot?.workspace_detail || state.selectedWorkspace !== workspacePath) {
      return;
    }
    state.snapshot.workspace_detail.verification_coverage_audit_error = error.message;
    render();
  }
}

async function fetchSnapshot(workspacePath, options = {}) {
  const requestedWorkspace = workspacePath || null;
  const historyMode = options.historyMode || "skip";
  state.loading = true;
  render();
  try {
    const detailQuery = requestedWorkspace ? `?workspace=${encodeURIComponent(requestedWorkspace)}` : "";
    const [snapshotResponse, detailResponse] = await Promise.all([
      fetch("/api/dashboard", { cache: "no-store" }),
      requestedWorkspace ? fetch(`/api/workspace-detail${detailQuery}`, { cache: "no-store" }) : Promise.resolve(null),
    ]);
    if (!snapshotResponse.ok) throw new Error(`Dashboard request failed with ${snapshotResponse.status}`);
    if (detailResponse && !detailResponse.ok) throw new Error(`Workspace detail request failed with ${detailResponse.status}`);
    const snapshot = await snapshotResponse.json();
    snapshot.workspace_detail = detailResponse ? await detailResponse.json() : null;
    if (snapshot.workspace_detail) {
      snapshot.workspace_detail.verification_coverage_audit = null;
      snapshot.workspace_detail.verification_coverage_audit_error = null;
    }
    state.snapshot = snapshot;
    state.selectedWorkspace = requestedWorkspace
      ? snapshot.workspace_detail?.summary?.workspace_path || requestedWorkspace
      : null;
    const knownConnections = state.selectedWorkspace
      ? snapshot.workspace_detail?.youtrack?.connections?.items || []
      : [];
    if (state.editingConnection) {
      state.editingConnection =
        knownConnections.find((item) => item.connection_id === state.editingConnection.connection_id) || null;
    }
    if (!state.selectedWorkspace) {
      state.editingConnection = null;
    }
    if (historyMode !== "skip") {
      updateRoute(state.selectedWorkspace, historyMode);
    }
    state.error = null;
  } catch (error) {
    state.error = error.message;
  } finally {
    state.loading = false;
    render();
    if (requestedWorkspace && state.selectedWorkspace) {
      loadVerificationCoverage(state.selectedWorkspace);
    }
  }
}

function selectWorkspace(workspacePath) {
  state.selectedWorkspace = workspacePath;
  fetchSnapshot(workspacePath, { historyMode: "push" });
}

function clearSelection() {
  state.selectedWorkspace = null;
  fetchSnapshot(null, { historyMode: "push" });
}

function renderSidebar(snapshot) {
  const overview = snapshot.overview || { workspaces: [] };
  const workspaces = overview.workspaces || [];
  return `
    <aside class="sidebar">
      <div class="brand">
        <p class="muted">AgentiUX Dev</p>
        <h1>Plugin Dashboard</h1>
        <p>${escapeHtml(snapshot.plugin.current_root)}</p>
      </div>
      <div class="stat-strip">
        <div class="pill">
          <strong>${escapeHtml(snapshot.stats.workspace_count)}</strong>
          <span>Workspaces</span>
        </div>
        <div class="pill">
          <strong>${escapeHtml(snapshot.stats.starter_runs)}</strong>
          <span>Starter runs</span>
        </div>
        <div class="pill">
          <strong>${escapeHtml(snapshot.stats.reference_boards)}</strong>
          <span>Boards</span>
        </div>
        <div class="pill">
          <strong>${escapeHtml(snapshot.stats.design_handoffs)}</strong>
          <span>Handoffs</span>
        </div>
        <div class="pill">
          <strong>${escapeHtml(snapshot.stats.active_verification_runs)}</strong>
          <span>Active runs</span>
        </div>
        <div class="pill">
          <strong>${escapeHtml(snapshot.stats.failed_verification_runs)}</strong>
          <span>Failed runs</span>
        </div>
        <div class="pill">
          <strong>${escapeHtml(snapshot.stats.plugin_platform_workspaces)}</strong>
          <span>Plugin workspaces</span>
        </div>
        <div class="pill">
          <strong>${escapeHtml(snapshot.stats.gui_status)}</strong>
          <span>GUI</span>
        </div>
      </div>
      <div class="sidebar-actions">
        <button onclick="window.__agentiux.refresh()">Refresh</button>
        <button class="secondary" onclick="window.__agentiux.clearSelection()">Overview</button>
      </div>
      <div class="workspace-list">
        ${workspaces
          .map((workspace) => {
            const active = workspace.workspace_path === state.selectedWorkspace ? "active" : "";
            const workspaceArg = JSON.stringify(workspace.workspace_path);
            const href = workspaceRoute(workspace.workspace_path);
            return `
              <a class="workspace-card ${active}" href="${escapeHtml(href)}" onclick='window.__agentiux.selectWorkspace(${workspaceArg}); return false;'>
                <h3>${escapeHtml(workspace.workspace_label)}</h3>
                <div class="muted">${escapeHtml(workspace.workspace_path)}</div>
                <div class="workspace-meta">
                  <span class="chip ${statusChip(workspace.stage_status)}">${escapeHtml(workspace.stage_status)}</span>
                  <span class="chip">${escapeHtml(workspace.current_workstream_id || "default")}</span>
                  <span class="chip">${escapeHtml(workspace.summary_counts.workstreams)} workstreams</span>
                  <span class="chip">${escapeHtml(workspace.summary_counts.tasks)} tasks</span>
                </div>
              </a>
            `;
          })
          .join("")}
      </div>
    </aside>
  `;
}

function renderOverview(snapshot) {
  const starterRuns = snapshot.starter_runs?.runs || [];
  return `
    <div class="hero-card">
      <p class="muted">Overview</p>
      <h2>Global plugin state</h2>
      <div class="summary-grid">
        <div class="metric"><strong>${escapeHtml(snapshot.stats.workspace_count)}</strong><span>Initialized workspaces</span></div>
        <div class="metric"><strong>${escapeHtml(snapshot.stats.blocked_workspaces)}</strong><span>Blocked workspaces</span></div>
        <div class="metric"><strong>${escapeHtml(snapshot.stats.artifact_files)}</strong><span>Artifact files</span></div>
        <div class="metric"><strong>${escapeHtml(snapshot.stats.active_verification_runs)}</strong><span>Active verification runs</span></div>
        <div class="metric"><strong>${escapeHtml(snapshot.stats.failed_verification_runs)}</strong><span>Failed verification runs</span></div>
        <div class="metric"><strong>${escapeHtml(snapshot.stats.starter_runs)}</strong><span>Starter runs</span></div>
        <div class="metric"><strong>${escapeHtml(snapshot.stats.plugin_platform_workspaces)}</strong><span>Plugin-platform workspaces</span></div>
        <div class="metric"><strong>${escapeHtml(snapshot.stats.gui_status)}</strong><span>GUI status</span></div>
      </div>
      <div class="section-list">
        <div class="section-row">
          <strong>Recent starter runs</strong>
          <pre>${escapeHtml(
            starterRuns
              .map((run) => `${run.run_id || "run"} [${run.preset_id || "preset"}] ${run.status || "status"} -> ${run.project_root || "n/a"}`)
              .join("\n") || "No starter runs recorded.",
          )}</pre>
        </div>
      </div>
    </div>
  `;
}

function renderDetail(detail) {
  if (!detail) {
    return `
      <div class="panel">
        <h2>No workspace selected</h2>
        <p class="muted">Choose a workspace from the left rail to inspect workstreams, tasks, verification state, design state, audits, and paths.</p>
      </div>
    `;
  }

  const summary = detail.summary;
  const register = detail.stage_register || {};
  const board = detail.current_reference_board || {};
  const handoff = detail.current_design_handoff || {};
  const designBrief = detail.design_brief || {};
  const verificationRunSummary = detail.verification_runs || {};
  const verificationRecipes = detail.verification_recipes || {};
  const verificationRuns = verificationRunSummary.recent_runs || verificationRunSummary.runs || [];
  const latestVerificationRun = detail.latest_verification_run || verificationRunSummary.latest_run || null;
  const latestCompletedRun = detail.latest_completed_verification_run || verificationRunSummary.latest_completed_run || null;
  const recentVerificationEvents = detail.recent_verification_events?.events || [];
  const pluginPlatform = detail.workspace_state?.plugin_platform || summary.plugin_platform || { enabled: false };
  const activeBriefLines = (summary.active_brief_preview || []).join("\n");
  const workstreams = detail.workstreams?.items || [];
  const tasks = detail.tasks?.items || [];
  const currentTask = detail.current_task || null;
  const currentAudit = detail.current_audit || null;
  const currentUpgradePlan = detail.current_upgrade_plan || null;
  const recentStarterRuns = detail.recent_starter_runs || [];
  const coverageAudit = detail.verification_coverage_audit;
  const coverageAuditError = detail.verification_coverage_audit_error;

  return `
    <div class="hero-card">
      <p class="muted">${escapeHtml(summary.workspace_path)}</p>
      <h2>${escapeHtml(summary.workspace_label)}</h2>
      <div class="hero-grid">
        <div class="metric"><strong>${escapeHtml(summary.workspace_mode)}</strong><span>Workspace mode</span></div>
        <div class="metric"><strong>${escapeHtml(summary.current_workstream_id || "default")}</strong><span>Current workstream</span></div>
        <div class="metric"><strong>${escapeHtml(summary.current_task_id || "none")}</strong><span>Current task</span></div>
        <div class="metric"><strong>${escapeHtml(summary.summary_counts.workstreams)}</strong><span>Total workstreams</span></div>
        <div class="metric"><strong>${escapeHtml(summary.summary_counts.tasks)}</strong><span>Total tasks</span></div>
        <div class="metric"><strong>${escapeHtml(summary.summary_counts.active_verification_runs)}</strong><span>Active verification runs</span></div>
        <div class="metric"><strong>${escapeHtml(summary.summary_counts.verification_runs)}</strong><span>Total verification runs</span></div>
      </div>
    </div>
    <div class="detail-grid">
      <section class="panel">
        <h3>Execution state</h3>
        <div class="section-list">
          <div class="section-row">
            <strong>Next objective</strong>
            <pre>${escapeHtml(summary.next_task || "No next task recorded.")}</pre>
          </div>
          <div class="section-row">
            <strong>Active brief preview</strong>
            <pre>${escapeHtml(activeBriefLines || "No brief recorded.")}</pre>
          </div>
          <div class="section-row">
            <strong>Blockers</strong>
            <pre>${escapeHtml((summary.blockers || []).join("\n") || "No blockers recorded.")}</pre>
          </div>
        </div>
      </section>
      <section class="panel">
        <h3>Workstreams</h3>
        <div class="stage-list">
          ${workstreams
            .map(
              (item) => `
                <div class="stage-item">
                  <div><strong>${escapeHtml(item.workstream_id)}</strong></div>
                  <div class="muted">${escapeHtml(item.title || "Untitled workstream")}</div>
                  <div class="workspace-meta">
                    <span class="chip ${statusChip(item.status)}">${escapeHtml(item.status)}</span>
                    <span class="chip">${escapeHtml(item.current_stage || "no-stage")}</span>
                    <span class="chip">${escapeHtml(item.branch_hint || "no-branch")}</span>
                  </div>
                </div>
              `,
            )
            .join("") || `<pre>No workstreams recorded.</pre>`}
        </div>
      </section>
      <section class="panel">
        <h3>Tasks</h3>
        <div class="section-list">
          <div class="section-row">
            <strong>Current task</strong>
            <pre>${escapeHtml(
              currentTask
                ? `${currentTask.task_id}\n${currentTask.title}\n${currentTask.status}\nLinked workstream: ${currentTask.linked_workstream_id || "none"}`
                : "No current task selected.",
            )}</pre>
          </div>
          <div class="section-row">
            <strong>Task list</strong>
            <pre>${escapeHtml(
              tasks
                .map((task) => `${task.task_id} [${task.status}] ${task.title}`)
                .join("\n") || "No tasks recorded.",
            )}</pre>
          </div>
        </div>
      </section>
      <section class="panel">
        <h3>Workspace detection</h3>
        <div class="section-list">
          <div class="section-row">
            <strong>Host support</strong>
            <pre>${escapeHtml(
              [
                `Host OS: ${summary.host_os || "unknown"}`,
                `Infra mode: ${summary.local_dev_policy?.infra_mode || "unknown"}`,
                `Orchestration: ${summary.local_dev_policy?.orchestration || "n/a"}`,
                `Repair status: ${summary.state_repair_status?.status || "unknown"}`,
                ...(summary.support_warnings || []),
              ].join("\n") || "No host support state recorded.",
            )}</pre>
          </div>
          <div class="section-row">
            <strong>Detected stacks</strong>
            <pre>${escapeHtml((summary.detected_stacks || []).join("\n") || "No stack signals recorded.")}</pre>
          </div>
          <div class="section-row">
            <strong>Selected profiles</strong>
            <pre>${escapeHtml((summary.selected_profiles || []).join("\n") || "No profiles selected.")}</pre>
          </div>
          <div class="section-row">
            <strong>Plugin platform</strong>
            ${renderPluginPlatform(pluginPlatform)}
          </div>
        </div>
      </section>
      ${renderYouTrack(detail)}
      <section class="panel">
        <h3>Verification state</h3>
        <div class="section-list">
          <div class="section-row">
            <strong>Current or latest run</strong>
            ${renderVerificationSummary(latestVerificationRun)}
          </div>
          <div class="section-row">
            <strong>Latest completed run</strong>
            ${renderVerificationSummary(latestCompletedRun)}
          </div>
          <div class="section-row">
            <strong>Recipes, runners, baselines</strong>
            ${renderVerificationCases(verificationRecipes)}
          </div>
          <div class="section-row">
            <strong>Resolved verification plan</strong>
            ${renderVerificationSelection(detail.verification_selection || summary.verification?.selection)}
          </div>
          <div class="section-row">
            <strong>Recent events</strong>
            ${renderVerificationEvents(recentVerificationEvents)}
          </div>
        </div>
      </section>
      <section class="panel">
        <h3>Stage timeline</h3>
        <div class="stage-list">
          ${(register.stages || [])
            .map(
              (stage) => `
                <div class="stage-item">
                  <div><strong>${escapeHtml(stage.id)}</strong></div>
                  <div class="muted">${escapeHtml(stage.title)}</div>
                  <div class="workspace-meta">
                    <span class="chip ${statusChip(stage.status)}">${escapeHtml(stage.status)}</span>
                    <span class="chip">${escapeHtml(stage.completed_at || "not completed")}</span>
                  </div>
                </div>
              `,
            )
            .join("")}
        </div>
      </section>
      <section class="panel">
        <h3>Boards and handoffs</h3>
        <div class="section-list">
          <div class="section-row">
            <strong>Design brief</strong>
            <pre>${escapeHtml(`${designBrief.status || "not_started"}\nPlatform: ${designBrief.platform || "n/a"}\nSurface: ${designBrief.surface || "n/a"}`)}</pre>
          </div>
          <div class="section-row">
            <strong>Reference candidates</strong>
            <pre>${escapeHtml(
              (board.candidates || [])
                .map((candidate) => `${candidate.id || "candidate"}: ${candidate.title || candidate.url || "untitled"}`)
                .join("\n") || "No persisted candidates yet.",
            )}</pre>
          </div>
          <div class="section-row">
            <strong>Verification hooks</strong>
            <pre>${escapeHtml((handoff.verification_hooks || []).join("\n") || "No verification hooks recorded.")}</pre>
          </div>
        </div>
      </section>
      <section class="panel">
        <h3>Audit and upgrade</h3>
        <div class="section-list">
          <div class="section-row">
            <strong>Current audit</strong>
            <pre>${escapeHtml(
              currentAudit
                ? [
                    `Audit ID: ${currentAudit.audit_id || "n/a"}`,
                    `Initialized: ${currentAudit.initialized}`,
                    `Gaps: ${(currentAudit.gaps || []).length}`,
                    ...(currentAudit.gaps || []).map((gap) => `${gap.gap_id}: ${gap.title}`),
                  ].join("\n")
                : "No audit recorded.",
            )}</pre>
          </div>
          <div class="section-row">
            <strong>Current upgrade plan</strong>
            <pre>${escapeHtml(
              currentUpgradePlan
                ? [
                    `Plan ID: ${currentUpgradePlan.plan_id || "n/a"}`,
                    `Status: ${currentUpgradePlan.status || "draft"}`,
                    `Created workstream: ${currentUpgradePlan.created_workstream_id || "none"}`,
                    `Tasks: ${(currentUpgradePlan.created_task_ids || []).join(", ") || "none"}`,
                  ].join("\n")
                : "No upgrade plan recorded.",
            )}</pre>
          </div>
        </div>
      </section>
      <section class="panel">
        <h3>Starter history</h3>
        <div class="section-list">
          <div class="section-row">
            <strong>Recent starter runs for this workspace</strong>
            <pre>${escapeHtml(
              recentStarterRuns
                .map((run) => `${run.run_id || "run"} [${run.preset_id || "preset"}] ${run.status || "status"}`)
                .join("\n") || "No starter runs recorded for this workspace.",
            )}</pre>
          </div>
        </div>
      </section>
      <section class="panel">
        <h3>Verification logs</h3>
        <div class="section-list">
          <div class="section-row">
            <strong>Stdout tail</strong>
            <pre>${formatLines(detail.active_verification_stdout?.lines, "No active stdout stream.")}</pre>
          </div>
          <div class="section-row">
            <strong>Stderr tail</strong>
            <pre>${formatLines(detail.active_verification_stderr?.lines, "No active stderr stream.")}</pre>
          </div>
          <div class="section-row">
            <strong>Logcat tail</strong>
            <pre>${formatLines(detail.active_verification_logcat?.lines, "No active logcat stream.")}</pre>
          </div>
          <div class="section-row">
            <strong>Coverage audit</strong>
            <pre>${escapeHtml(
              coverageAuditError
                ? `Coverage audit failed: ${coverageAuditError}`
                : coverageAudit === null
                  ? "Coverage audit is loading..."
                  : (coverageAudit?.gaps || [])
                      .map((gap) => `${gap.gap_id}: ${gap.title}`)
                      .join("\n") || "No verification coverage warnings.",
            )}</pre>
          </div>
          <div class="section-row">
            <strong>Recent runs</strong>
            <pre>${escapeHtml(
              verificationRuns
                .map(
                  (run) =>
                    `${run.run_id || "run"} [${run.mode || "mode"}:${run.target_id || "target"}] ${run.status || "status"} / ${run.health || "health"}`,
                )
                .join("\n") || "No verification runs recorded.",
            )}</pre>
          </div>
        </div>
      </section>
      <section class="panel">
        <h3>State paths</h3>
        <div class="path-block">${escapeHtml(
          Object.entries(detail.paths || {})
            .map(([key, value]) => `${key}: ${value}`)
            .join("\n"),
        )}</div>
      </section>
    </div>
  `;
}

async function submitYouTrackConnection() {
  const workspacePath = state.selectedWorkspace;
  if (!workspacePath) return;
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
  await fetchSnapshot(workspacePath);
}

function clearYouTrackForm() {
  state.editingConnection = null;
  render();
}

async function testYouTrackConnection(connectionId) {
  if (!state.selectedWorkspace) return;
  await apiJson(`/api/youtrack/connections/${encodeURIComponent(connectionId)}/test`, {
    method: "POST",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace }),
  });
  await fetchSnapshot(state.selectedWorkspace);
}

async function removeYouTrackConnection(connectionId) {
  if (!state.selectedWorkspace) return;
  await apiJson("/api/youtrack/connections", {
    method: "DELETE",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace, connectionId }),
  });
  if (state.editingConnection?.connection_id === connectionId) {
    state.editingConnection = null;
  }
  await fetchSnapshot(state.selectedWorkspace);
}

async function setDefaultYouTrackConnection(connectionId) {
  if (!state.selectedWorkspace) return;
  await apiJson("/api/youtrack/connections", {
    method: "PATCH",
    body: JSON.stringify({ workspacePath: state.selectedWorkspace, connectionId, default: true, testConnection: false }),
  });
  await fetchSnapshot(state.selectedWorkspace);
}

function editYouTrackConnection(connectionId) {
  const connections = state.snapshot?.workspace_detail?.youtrack?.connections?.items || [];
  state.editingConnection = connections.find((item) => item.connection_id === connectionId) || null;
  render();
}

function render() {
  if (state.loading) {
    appRoot.innerHTML = `<div class="loading-shell">Loading AgentiUX Dev dashboard...</div>`;
    return;
  }
  if (state.error) {
    appRoot.innerHTML = `<div class="error-state">${escapeHtml(state.error)}</div>`;
    return;
  }
  const snapshot = state.snapshot;
  if (!snapshot) {
    appRoot.innerHTML = `<div class="empty-state">No dashboard snapshot available.</div>`;
    return;
  }

  appRoot.innerHTML = `
    <div class="shell">
      ${renderSidebar(snapshot)}
      <main class="main">
        ${renderOverview(snapshot)}
        ${renderDetail(state.selectedWorkspace ? snapshot.workspace_detail : null)}
      </main>
    </div>
  `;
}

window.__agentiux = {
  refresh: () => fetchSnapshot(state.selectedWorkspace, { historyMode: "replace" }),
  clearSelection,
  selectWorkspace,
  submitYouTrackConnection,
  clearYouTrackForm,
  testYouTrackConnection,
  removeYouTrackConnection,
  setDefaultYouTrackConnection,
  editYouTrackConnection,
};

window.addEventListener("popstate", () => {
  const route = parseRoute();
  fetchSnapshot(route.workspacePath, { historyMode: "skip" });
});

const initialRoute = parseRoute();
fetchSnapshot(initialRoute.workspacePath, {
  historyMode: initialRoute.source === "legacy-query" ? "replace" : "skip",
});
