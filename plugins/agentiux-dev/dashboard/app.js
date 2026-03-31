const appRoot = document.getElementById("app");

const state = {
  snapshot: null,
  selectedWorkspace: null,
  loading: true,
  error: null,
};

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

async function fetchSnapshot(workspacePath) {
  state.loading = true;
  render();
  const query = workspacePath ? `?workspace=${encodeURIComponent(workspacePath)}` : "";
  try {
    const response = await fetch(`/api/dashboard${query}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Dashboard request failed with ${response.status}`);
    const snapshot = await response.json();
    state.snapshot = snapshot;
    state.selectedWorkspace =
      snapshot.workspace_detail?.summary?.workspace_path ||
      snapshot.overview?.workspaces?.[0]?.workspace_path ||
      null;
    state.error = null;
  } catch (error) {
    state.error = error.message;
  } finally {
    state.loading = false;
    render();
  }
}

function selectWorkspace(workspacePath) {
  state.selectedWorkspace = workspacePath;
  fetchSnapshot(workspacePath);
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
            return `
              <div class="workspace-card ${active}" onclick='window.__agentiux.selectWorkspace(${workspaceArg})'>
                <h3>${escapeHtml(workspace.workspace_label)}</h3>
                <div class="muted">${escapeHtml(workspace.workspace_path)}</div>
                <div class="workspace-meta">
                  <span class="chip ${statusChip(workspace.stage_status)}">${escapeHtml(workspace.stage_status)}</span>
                  <span class="chip">${escapeHtml(workspace.current_workstream_id || "default")}</span>
                  <span class="chip">${escapeHtml(workspace.summary_counts.workstreams)} workstreams</span>
                  <span class="chip">${escapeHtml(workspace.summary_counts.tasks)} tasks</span>
                </div>
              </div>
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
              (detail.verification_coverage_audit?.gaps || [])
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
        ${renderDetail(snapshot.workspace_detail)}
      </main>
    </div>
  `;
}

window.__agentiux = {
  refresh: () => fetchSnapshot(state.selectedWorkspace),
  clearSelection: () => fetchSnapshot(null),
  selectWorkspace,
};

fetchSnapshot();
