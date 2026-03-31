---
name: design-orchestrator
description: Use when the user needs a design brief, a reference search pass, a persisted reference board, or an implementation-ready design handoff for a web or Expo surface inside AgentiUX Dev.
---

# Design Orchestrator

## Read First

- `../../references/design-workflow.md`
- `../../references/visual-verification.md`
- `../../references/dashboard.md`
- `../../README.md`

## Required Workflow

1. Confirm the workspace is initialized in AgentiUX Dev external state before starting the design workflow.
2. Persist a `DesignBrief` before collecting references.
3. Use live web and image search to collect 3 to 5 concrete references with rationale.
4. Persist the reference candidates into a `ReferenceBoard` outside the repo and cache local previews when possible.
5. Wait for the user's selection or request for another search pass before producing the handoff.
6. Route the final handoff to:
   - `web-product-designer` for web surfaces
   - `expo-product-designer` for Expo / React Native surfaces
7. Persist the final `DesignHandoff` outside the repo with deterministic verification hooks.

## Guardrails

- Do not skip the brief and jump straight to implementation.
- Do not leave reference choices only in chat.
- Do not produce an implementation-ready handoff until the user chooses a direction.
