# Local Dashboard

AgentiUX Dev includes a local-only dashboard launched through `scripts/agentiux_dev_gui.py` or the installed shell launcher `agentiux web`.

## Scope

- cockpit-first workspace operations view with explicit `Now`, `Plan`, `Quality`, `Integrations`, `Memory`, and `Diagnostics` panels
- secondary global portfolio overview for initialized workspaces
- bootstrap-first load model with three dashboard data layers:
  - `GET /api/dashboard` for the global overview snapshot
  - `GET /api/dashboard-bootstrap?workspace=<path>&panel=<id>` for overview plus one workspace shell plus one active panel payload
  - `GET /api/workspace-panel?workspace=<path>&panel=<id>` for shell metadata plus one lazy panel payload
- first-class empty state for uninitialized workspace paths, including detection signals and external state paths
- action-oriented summaries for current stage, current task, blockers, verification health, and YouTrack status
- first-class workspace memory summaries for pinned project notes, learning-entry status, and auth coverage attention items
- low-priority diagnostics such as host support, plugin-platform detection, audits, upgrade data, and absolute external state paths

## Guardrails

- The dashboard must not mutate stage or design state.
- The allowed dashboard mutations are limited to external-state management flows: YouTrack integration management plus workspace-scoped auth profile, auth session, project note, and learning-entry CRUD.
- The dashboard shows external/plugin state only, not hidden repo edits.
- Release-readiness must verify the cockpit with a live browser layout audit, not only health and JSON payload checks.
- The dashboard browser audit should exercise at least one initialized cockpit state and fail on overlap, clipping, viewport overflow, or occlusion regressions.
- The operator shell stays visible after the first workspace bootstrap. Refreshes or workspace-local CRUD flows should invalidate only the affected panel or workspace shell data instead of dropping back to a full-page loading state.
- The dashboard URL is local-only and should be returned to the user after launch.
- The dashboard runtime is singleton-scoped. Repeated launch commands must reuse the existing server process and may only update the default workspace selection.
- The dashboard supports deep-link routes: `/workspaces/<url-encoded-workspace-path>` for a workspace cockpit, optional `?panel=<panel-id>` for panel selection, and `/#overview` for the portfolio overview.
- Deep links must deterministically restore the selected workspace and panel, and browser history navigation must preserve that state without reintroducing the legacy `overview -> cockpit` waterfall path.
- Auth resolve preview may pass opaque `session_binding` refs so operators can separate or reuse cached sessions across arbitrary project-defined backend targets without adding project semantics to core plugin code.
- `release_readiness.py dashboard-check` is the tracked evidence surface for dashboard hardening. It records cold-start timing, first-usable render timing, payload byte sizes, and deep-link or history assertions from the live audited cockpit.
