# Deterministic Verification

Deterministic verification is the default closeout path for AgentiUX Dev.

## Cross-Surface Hooks

- Design handoffs must record stable screen IDs, routes, or navigation entry points.
- Dynamic zones that cannot be compared deterministically must be named and masked explicitly.
- Verification instructions belong in the external handoff state, not only in chat output.
- Every verification surface should support both one-case reruns and full-suite execution.
- Long-running verification must emit structured progress events and keep stdout, stderr, and Android logcat when configured under the external verification run root.
- Coverage audits should report warning-level gaps when a detected surface has no deterministic verification case yet.
- Canonical baselines should stay project-owned for reproducible CI checks.
- Transient screenshots, diffs, traces, videos, and plugin packets should stay outside the repo.

## Runner Adapters

- `playwright-visual`
- `detox-visual`
- `android-compose-screenshot`
- `ios-simulator-capture`
- `shell-contract`

## Common Determinism Fields

- `surface_type`
- `runner`
- `target`
- `device_or_viewport`
- `locale`
- `timezone`
- `color_scheme`
- `freeze_clock`
- `seed_step`
- `readiness_probe`
- `masks`
- `artifact_expectations`
- `retry_policy`
- `baseline`
- `android_logcat`

## Web

- Prefer Playwright.
- Use fixed viewport matrix.
- Disable animations.
- Hide carets when taking screenshots.
- Mask dynamic zones.
- Persist route IDs and expected states in the design handoff.
- Prefer one route or state per verification case and group them into suites for broader closeout runs.

## React Native / Expo

- Prefer Detox for deterministic emulator and simulator flows.
- Treat Maestro or EAS flows as complementary, not as the only gate.
- Fix locale, clock, seed data, and launch args before capture.
- Persist stable screen names, device targets, and masked dynamic regions in the design handoff.
- Prefer one screen-state pair per verification case and stable suites for broader regression runs.

## Android

- Prefer official Compose Preview Screenshot Testing for Compose UI.
- Use emulator-driven UI capture and probes for non-Compose surfaces.
- Mirror Logcat into external verification state when Android execution is active.
- Prefer package- or pid-scoped Logcat capture when the test runner can resolve the app process deterministically.

## iOS

- Prefer simulator-first capture and stable device state.

## Backend And Infra

- Use deterministic smoke and contract checks.
- Verify Dockerized dependency connectivity for databases, queues, caches, and brokers.
