---
name: backend-platform
description: Use for NestJS, Rust, Postgres, MongoDB, Redis, and NATS work inside AgentiUX Dev. This skill keeps backend stage planning, Dockerized dependencies, contract checks, and repo docs synchronized.
---

# Backend Platform

## Read First

- `../../references/stack-profiles.md`
- `../../README.md`

## Required Workflow

1. Treat NestJS and Rust as first-class backend runtimes.
2. Treat Postgres, MongoDB, Redis, and NATS as first-class supporting services.
3. Keep backend dependency services in Docker for local development.
4. Run deterministic smoke or contract checks before closeout.
5. Sync repo docs whenever backend contracts, migrations, or local-dev commands change.

## Guardrails

- Do not let backend changes bypass the external stage system.
- Do not require manual host installs for databases or brokers in the standard local path.
