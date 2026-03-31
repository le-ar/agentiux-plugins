---
name: plugin-platform
description: Use for AgentiUX Dev self-hosting work on Python scripts, MCP tooling, installer flow, release-readiness gates, and the local dashboard.
---

# Plugin Platform

## Read First

- `../../references/stack-profiles.md`
- `../../references/workflow-kernel.md`
- `../../references/dashboard.md`

## Required Workflow

1. Treat Codex plugin source, Python scripts, MCP interfaces, install flow, and dashboard assets as one product surface.
2. When the plugin itself changes, keep `plugin-platform` selected in the workspace state and make release-readiness checks part of closeout.
3. Prefer the low-token plugin route before reading large source files:
   - `python3 ../../scripts/agentiux_dev_state.py show-intent-route --request-text "<user request>"`
   - `python3 ../../scripts/agentiux_dev_state.py show-capability-catalog --route-id plugin-dev`
   - `python3 ../../scripts/agentiux_dev_state.py show-workspace-context-pack --workspace <repo> --request-text "<user request>" --route-id plugin-dev`
4. Validate Python compile health, MCP tool discovery, dashboard health, and smoke coverage before claiming the plugin is ready.
5. Keep source and installed-copy paths distinct; repo-tracked files stay public-safe while home-local install behavior remains external.
6. When operator UX, commands, or verification behavior changes, update the plugin README in the same work cycle.

## Guardrails

- Do not rely on filesystem ordering for verification or dashboard recency logic.
- Do not treat the plugin repo as a generic workspace when plugin-platform signals are present.
- Do not add machine-specific or non-English text to tracked source while preserving reply-in-user-language behavior at runtime.
