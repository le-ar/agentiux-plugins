---
name: stage-execution
description: Use when the user explicitly asks to execute or continue the active stage in the current workstream. This skill requires explicit execution intent, external brief generation, and stage completion through verification and docs sync unless a blocker is reached.
---

# Stage Execution

## Read First

- `../../references/workflow-kernel.md`
- `../../references/visual-verification.md`
- `../../README.md`

## Required Workflow

1. Confirm the request is explicit execution intent.
2. Read the current external state:
   - `python3 ../../scripts/agentiux_dev_state.py workspace-state --workspace <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py current-workstream --workspace <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py stage-register --workspace <repo>`
3. Refuse execution if the current register has no confirmed concrete stage plan yet.
4. Before the first implementation action, synthesize and persist a `StageExecutionBrief`:
   - `python3 ../../scripts/agentiux_dev_state.py set-brief --workspace <repo> --stdin`
5. Show a short visible execution summary to the user before implementation starts.
6. Execute only the active stage and active slice boundaries from the current workstream register.
7. Continue through implementation, docs sync, verification, and closeout unless the workspace becomes blocked or awaits user input.

## Guardrails

- Do not execute on questions, status checks, commit requests, or workflow discussion.
- Do not leave a stage implicitly half-done inside the current workstream.
- Do not create Codex workflow files inside the repository to compensate for missing external state.
