---
name: deterministic-verification
description: Use for deterministic verification and repair loops across web, React Native, Expo, Android, iOS, backend, and infrastructure work. This skill keeps verification artifacts outside the repo and prefers deterministic gates before model-assisted review.
---

# Deterministic Verification

## Read First

- `../../references/visual-verification.md`
- `../../references/stack-profiles.md`

## Required Workflow

1. Choose the platform-specific deterministic gate for the changed surface.
2. Resolve the verification route and low-token context first when the request is broad:
   - `python3 ../../scripts/agentiux_dev_state.py show-intent-route --request-text "<user request>"`
   - `python3 ../../scripts/agentiux_dev_state.py show-capability-catalog --route-id verification`
   - `python3 ../../scripts/agentiux_dev_state.py search-context-index --workspace <repo> --query-text "<verification query>"`
3. Keep all screenshots, diffs, traces, and verification packets under the external artifact root for the workspace.
4. Define verification recipes so one named case can run independently and named suites can run in stable order.
5. Use scoped reruns first after repairs.
6. Record a verification summary before closeout.
7. Keep structured verification progress events plus stdout, stderr, and Android logcat when configured so long-running checks are observable.
8. Use `audit-verification-coverage` to surface warning-level coverage gaps before claiming a surface is ready.

## Default Gates

- Web: Playwright with fixed viewports, masked dynamic zones, hidden default browser mode, disabled animations.
- React Native / Expo: Detox on emulator and simulator with fixed locale, clock, seed data, and launch args.
- Android native: Compose screenshot testing where applicable, emulator-driven checks elsewhere.
- Android execution should mirror Logcat into external verification state when the case uses Android runners or Android-targeted Detox devices.
- iOS native: simulator-first stable capture.
- Backend and infra: deterministic smoke and contract checks.

## Guardrails

- Do not commit raster artifacts into the repo.
- Do not substitute taste review for deterministic verification.
- Do not close the active slice without a verification summary or explicit blocker.
