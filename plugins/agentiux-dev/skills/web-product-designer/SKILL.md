---
name: web-product-designer
description: Use for implementation-ready web UI handoffs inside AgentiUX Dev after the user selects a visual direction. This skill covers React, Next.js, Tailwind, route-level verification hooks, and concrete component/layout guidance.
---

# Web Product Designer

## Read First

- `../../references/design-workflow.md`
- `../../references/visual-verification.md`
- `../../references/stack-profiles.md`

## Required Workflow

1. Start from the persisted `DesignBrief` and selected `ReferenceBoard`, not from generic taste-only brainstorming.
2. Produce a handoff that names the layout system, component inventory, typography, color direction, motion rules, accessibility constraints, and copy tone.
3. Include concrete web verification hooks:
   - stable routes
   - viewport matrix
   - masked dynamic zones
   - expected states to capture
4. Keep the handoff specific enough that implementation can start without re-deciding the visual direction.

## Guardrails

- Do not produce generic card-grid or default-SaaS direction unless the brief explicitly calls for it.
- Do not omit verification hooks for the changed routes or states.
