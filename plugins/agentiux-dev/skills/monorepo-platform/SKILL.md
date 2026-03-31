---
name: monorepo-platform
description: Use for Nx-driven monorepos, affected-task scoping, graph-aware verification, Docker Compose coordination, and Docker Swarm deployment planning inside AgentiUX Dev workflows.
---

# Monorepo Platform

## Read First

- `../../references/stack-profiles.md`
- `../../README.md`

## Required Workflow

1. Use Nx graph and affected-task awareness when the workspace exposes `nx.json` or Nx packages.
2. Scope checks and stage work to the real affected surfaces when possible.
3. Keep local orchestration aligned with Docker Compose.
4. Treat Docker Swarm as a supported ops/deploy profile without replacing the local Compose baseline.

## Guardrails

- Do not treat monorepo orchestration as purely a repo-local implementation detail.
- Do not let Swarm planning override the Docker Compose local-development contract.
