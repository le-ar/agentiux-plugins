# Local Dashboard

AgentiUX Dev includes a read-only local dashboard launched through `scripts/agentiux_dev_gui.py`.

## Scope

- global workspace overview
- current workspace detail
- stage register summary
- active brief preview
- design brief, board, and handoff visibility
- artifact and blocker counts
- absolute external state paths

## Guardrails

- The dashboard must not mutate stage or design state.
- The dashboard shows external/plugin state only, not hidden repo edits.
- The dashboard URL is local-only and should be returned to the user after launch.
