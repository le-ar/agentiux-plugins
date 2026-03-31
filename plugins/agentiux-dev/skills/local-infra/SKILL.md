---
name: local-infra
description: Use for Docker-first local development infrastructure, service inventories, Docker Compose baselines, local env contracts, and infra smoke checks across Postgres, MongoDB, Redis, NATS, and similar supporting services.
---

# Local Infra

## Read First

- `../../references/stack-profiles.md`
- `../../README.md`

## Required Workflow

1. Inventory the local infrastructure requirements from repo manifests and local-dev docs.
2. Keep supporting services in Docker for local development.
3. Prefer Docker Compose as the local baseline even when Docker Swarm is also a supported ops profile.
4. Record the real boot and smoke-check path in project docs whenever the infra contract changes.
5. Verify the resulting stack through deterministic smoke checks.

## Guardrails

- Do not require host-installed databases or brokers for the standard local workflow.
- Do not silently change local ports, env contracts, or service topology without syncing docs.
- Do not confuse local Compose workflow with Swarm deployment planning.
