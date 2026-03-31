---
name: web-platform
description: Use for React, Next.js, NestJS-adjacent web surfaces, TypeScript, Tailwind, and route-level deterministic verification policy inside AgentiUX Dev stage workflows.
---

# Web Platform

## Read First

- `../../references/stack-profiles.md`
- `../../references/visual-verification.md`

## Required Workflow

1. Treat React, Next.js, Tailwind, and TypeScript surfaces as first-class web inputs to the stage plan.
2. When the task is visual direction, product UI exploration, or implementation-ready web design handoff, route to `design-orchestrator` and then `web-product-designer`.
3. When the web surface changed, make Playwright route and viewport checks part of closeout.
4. When the local-dev path or route contract changed, sync the real repo docs in the same work cycle.
5. Coordinate with `deterministic-verification` for final gate behavior.

## Guardrails

- Do not treat frontend changes as exempt from stage gating.
- Do not leave dynamic or masked zones undefined when visual checks depend on them.
