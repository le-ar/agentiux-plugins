# Local Dashboard

AgentiUX Dev includes a local-only dashboard launched through `scripts/agentiux_dev_gui.py` or the installed shell launcher `agentiux web`.

## Scope

- cockpit-first workspace operations view with explicit `Now`, `Plan`, `Quality`, `Integrations`, and `Diagnostics` panels
- secondary global portfolio overview for initialized workspaces
- first-class empty state for uninitialized workspace paths, including detection signals and external state paths
- action-oriented summaries for current stage, current task, blockers, verification health, and YouTrack status
- low-priority diagnostics such as host support, plugin-platform detection, audits, upgrade data, and absolute external state paths

## Guardrails

- The dashboard must not mutate stage or design state.
- The only allowed dashboard mutations are YouTrack integration-management flows: add, update, test, remove, or set-default connection.
- The dashboard shows external/plugin state only, not hidden repo edits.
- Release-readiness must verify the cockpit with a live browser layout audit, not only health and JSON payload checks.
- The dashboard browser audit should exercise at least one initialized cockpit state and fail on overlap, clipping, viewport overflow, or occlusion regressions.
- The dashboard URL is local-only and should be returned to the user after launch.
- The dashboard runtime is singleton-scoped. Repeated launch commands must reuse the existing server process and may only update the default workspace selection.
- The dashboard supports deep-link routes: `/workspaces/<url-encoded-workspace-path>` for a workspace cockpit, optional `?panel=<panel-id>` for panel selection, and `/#overview` for the portfolio overview.
