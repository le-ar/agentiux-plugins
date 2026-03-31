---
name: stage-planning
description: Use when proposing, reviewing, or applying changes to the external stage plan inside the current workstream. This skill enforces explicit confirmation for unfinished stage definition changes and forbids edits to completed stages.
---

# Stage Planning

## Read First

- `../../references/workflow-kernel.md`
- `../../references/command-surface.md`
- `../../README.md`

## Required Workflow

1. Read the current workstream register:
   - `python3 ../../scripts/agentiux_dev_state.py current-workstream --workspace <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py stage-register --workspace <repo>`
2. For `propose stage plan changes`, draft the proposed changes first and do not mutate state.
3. For `apply stage plan changes`, update the register only after explicit user confirmation.
4. Persist stage-plan changes through:
   - `python3 ../../scripts/agentiux_dev_state.py write-stage-register --workspace <repo> --register-file <json> --confirmed-stage-plan-edit`
5. Let the state writer persist the confirmed register and derived mirrors after the confirmed change.

## Guardrails

- Completed stages are immutable.
- Current and future unfinished stages may change only after explicit confirmation.
- Do not start implementation while applying a stage-plan change unless the user separately asks to execute the stage.
