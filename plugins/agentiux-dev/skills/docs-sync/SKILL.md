---
name: docs-sync
description: Use when approved decisions or implementation work changed documented truth. This skill keeps real project docs in sync with code changes while leaving Codex-specific workflow state outside the repository.
---

# Docs Sync

## Read First

- `../../references/workflow-kernel.md`
- `../../README.md`

## Required Workflow

1. Identify whether the task changed:
   - runtime behavior
   - architecture
   - local development commands
   - verification contracts
   - scope or workflow truth already documented inside the repo
2. Update the relevant real project docs in the same work cycle.
3. If the stage is active, make sure the external register still points to the correct required doc updates.

## Guardrails

- Do not let approved decisions live only in chat.
- Do not treat external AgentiUX Dev state as a substitute for real project docs.
- Do not add Codex-only operational docs into the repo when the truth belongs in the external stage system.
