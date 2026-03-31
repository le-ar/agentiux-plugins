# Local Dashboard

AgentiUX Dev includes a local-only dashboard launched through `scripts/agentiux_dev_gui.py`.

## Scope

- global workspace overview
- current workspace detail
- stage register summary
- active brief preview
- design brief, board, and handoff visibility
- YouTrack connection status, latest search session, active plan, and current workstream issue cards
- artifact and blocker counts
- absolute external state paths

## Guardrails

- The dashboard must not mutate stage or design state.
- The only allowed dashboard mutations are YouTrack integration-management flows: add, update, test, remove, or set-default connection.
- The dashboard shows external/plugin state only, not hidden repo edits.
- The dashboard URL is local-only and should be returned to the user after launch.
