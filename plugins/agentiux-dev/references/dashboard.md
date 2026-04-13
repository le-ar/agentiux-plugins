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
- action-oriented summaries for current stage, current task, blockers, verification health, and tracker status such as YouTrack and Sentry
- compact design and testability summaries on cheap surfaces instead of full artifact hydration
- compact structural indexing summaries on cheap surfaces, including module, chunk, hotspot, and large-file counts without exposing full structural artifacts
- compact semantic summaries on cheap surfaces, including backend status, unit counts, snapshot counts, and semantic refresh reuse or rebuild stats without exposing semantic unit bodies
- first-class workspace memory summaries for pinned project notes, learning-entry status, and auth coverage attention items
- low-priority diagnostics such as host support, plugin-platform detection, audits, upgrade data, and absolute external state paths

## Guardrails

- The dashboard must not mutate stage or design state.
- The allowed dashboard mutations are limited to external-state management flows: YouTrack and Sentry integration management plus workspace-scoped auth profile, auth session, project note, and learning-entry CRUD.
- The dashboard shows external/plugin state only, not hidden repo edits.
- `Plan` should show design readiness, active surfaces, flow or state coverage, critical-action counts, and brief generation state from compact summaries.
- `Plan` may also show semantic readiness hints such as semantic unit counts or active snapshot counts, but it must use compact `semantic_summary` only.
- `Quality` should show authored path counts, limitation counts, unresolved critical actions, warning-level coverage gaps, and compact semantic backend or unit-count signals from summaries.
- `Diagnostics` remains the place for raw artifact and detail views such as the full design brief, current handoff, and audit payloads.
- `workspace_summary`, dashboard overview snapshots, and cockpit shell payloads should carry only compact `structure_summary`, `hotspot_summary`, and `semantic_summary` projections from the context cache, not the full structural index, semantic units, or generated snapshot bodies.
- Release-readiness must verify the cockpit with a live browser layout audit, not only health and JSON payload checks.
- The dashboard browser audit should exercise at least one initialized cockpit state and fail on overlap, clipping, viewport overflow, or occlusion regressions.
- The operator shell stays visible after the first workspace bootstrap. Refreshes or workspace-local CRUD flows should invalidate only the affected panel or workspace shell data instead of dropping back to a full-page loading state.
- The dashboard URL is local-only and should be returned to the user after launch.
- The dashboard runtime is singleton-scoped. Repeated launch commands must reuse the existing server process and may only update the default workspace selection.
- The dashboard supports deep-link routes: `/workspaces/<url-encoded-workspace-path>` for a workspace cockpit, optional `?panel=<panel-id>` for panel selection, and `/#overview` for the portfolio overview.
- Deep links must deterministically restore the selected workspace and panel, and browser history navigation must preserve that state without reintroducing the legacy `overview -> cockpit` waterfall path.
- Auth resolve preview may pass opaque `session_binding` refs so operators can separate or reuse cached sessions across arbitrary project-defined backend targets without adding project semantics to core plugin code.
- `release_readiness.py dashboard-check` is the tracked evidence surface for dashboard hardening. It records cold-start timing, first-usable render timing, payload byte sizes, and deep-link or history assertions from the live audited cockpit.
