---
name: stage-closeout
description: Use when closing the active stage or handling closeout-only work after implementation. This skill verifies docs sync and deterministic checks, updates external state, rerenders external stage docs, and advances to the next stage without starting it.
---

# Stage Closeout

## Read First

- `../../references/workflow-kernel.md`
- `../../references/visual-verification.md`
- `../../README.md`

## Required Workflow

1. Read the external stage register and current brief.
2. Verify the completed work against:
   - exit criteria
   - required doc updates
   - deterministic verification requirements
3. Refuse closeout if the current register has no confirmed concrete stage plan.
4. Update repo docs first when runtime behavior, architecture, local-dev commands, or verification contracts changed.
5. Update the external register and derived mirrors only after the closeout facts are true.
6. Advance `current_stage` to the next planned stage if one exists, but do not start it during closeout-only or commit-only work.

## Guardrails

- Do not skip closeout after explicit stage execution.
- Do not advance the register without updating the corresponding external stage docs.
- Do not mutate completed stage definitions during closeout.
