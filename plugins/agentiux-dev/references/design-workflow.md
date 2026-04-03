# Design Workflow

AgentiUX Dev uses a deterministic design workflow for web and Expo surfaces.

## Required Sequence

1. Make sure the workspace is initialized in external state.
2. Persist a `DesignBrief` before collecting references.
3. Use live web and image search to collect 3 to 5 concrete references.
4. Persist a `ReferenceBoard` outside the repo and cache local previews when available.
5. Wait for the user to select a direction or ask for another search pass.
6. Persist a `DesignHandoff` only after the direction is chosen.
7. Include deterministic verification hooks in the handoff.

## Schema Expectations

- `DesignBrief` is schema v2.
- `DesignHandoff` is schema v2.
- Both artifacts should keep the legacy visual fields and also normalize:
  - `affected_surfaces[]` as `{surface_id, platform, route_or_screen, summary}`
  - `user_flows[]` as `{flow_id, title, entry_points, success_state, steps}`
  - `state_coverage[]` as `{state_id, surface_id, status_kind, notes, verified_by}`
  - `ux_rationale[]` as `{decision_id, surface_ids, rationale, tradeoff}`
  - `critical_actions[]` as `{action_id, flow_id, surface_id, interaction_type, priority, verification_path_id}`
  - `testability_guidance` as `{stable_targets, masks, preconditions, limitations}`
- Read paths must stay backward-compatible with v1 payloads and preserve unknown keys.
- Write paths should emit the full normalized v2 payload even when only one Stage 3 field changed.

## Brief Synthesis

- Active task and stage briefs stay markdown-first on the outside.
- Placeholder briefs and previously generated briefs are rebuilt automatically from task or workstream truth, design artifacts, and verification selection.
- Manual edits made through `set_active_brief` are treated as explicit overrides and must not be replaced silently.
- `TaskBrief` should stay useful even when design artifacts are empty. Required compact sections are `Objective`, `Scope`, `Current Truth`, `Target States`, `Critical Actions`, `Verification Notes`, and `Risks`.
- `StageExecutionBrief` should stay useful even when design artifacts are light. Preferred sections are `Goal`, `Current Truth`, `Active Surfaces`, `Flows and States`, `Design/Testability Signals`, `Deterministic Verification Plan`, and `Exit Criteria`.

## Handoff Minimums

- layout system
- component inventory
- motion rules
- typography and colors
- accessibility constraints
- copy tone
- platform deltas
- stable screen IDs or routes
- masked dynamic zones
- capture instructions for web or Expo mobile
- authored critical actions plus verification path IDs when gestures matter
- explicit testability limitations when a runner cannot execute the intended path yet
