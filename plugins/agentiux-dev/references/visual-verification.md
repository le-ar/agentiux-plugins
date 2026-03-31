# Deterministic Verification

Deterministic verification is the default closeout path for AgentiUX Dev.

## Cross-Surface Hooks

- Design handoffs must record stable screen IDs, routes, or navigation entry points.
- Dynamic zones that cannot be compared deterministically must be named and masked explicitly.
- Verification instructions belong in the external handoff state, not only in chat output.
- Every verification surface should support both one-case reruns and full-suite execution.
- Long-running verification must emit structured progress events and keep stdout, stderr, and Android logcat when configured under the external verification run root.
- Coverage audits should report warning-level gaps when a detected surface has no deterministic verification case yet.
- Web workspaces should ship at least one visual web case.
- Android-capable workspaces should ship at least one Android-targeted visual case.
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
- `semantic_assertions`

## Optional Semantic Assertions

- `semantic_assertions` is optional per case.
- Projects should materialize the plugin-owned helper bundle into `.verification/helpers/` before importing runner helpers.
- The helper catalog is read-only and reports bundle version, runner entrypoints, required host tools, and current materialization status.
- When enabled, the runner must emit a JSON report into the verification artifact root.
- The runtime validates helper sync status, runner capability compatibility, the shared report schema, and the declared `required_checks`.
- The semantic spec shape is `enabled`, `report_path`, `required_checks`, `targets`, `auto_scan`, `heuristics`, `artifacts`, and `platform_hooks`.
- Each target uses `target_id`, `locator`, `container_locator`, `scroll_container_locator`, `interactions`, `expected_attributes`, `expected_styles`, `expected_layout`, `allow_clipping`, `allow_occlusion`, and `allow_text_truncation`.
- Shared check families are `presence_uniqueness`, `visibility`, `scroll_reachability`, `overflow_clipping`, `occlusion`, `interaction_states`, `computed_styles`, `layout_relations`, `text_overflow`, `accessibility_state`, and `screenshot_baseline`.
- Recommended checks for web: `visibility`, `overflow_clipping`, `computed_styles`, `interaction_states`, `scroll_reachability`, `occlusion`.
- Recommended checks for Android: `visibility`, `overflow_clipping`, `interaction_states`, `scroll_reachability`, `occlusion`.

## Web

- Prefer Playwright.
- Use fixed viewport matrix.
- Disable animations.
- Hide carets when taking screenshots.
- Mask dynamic zones.
- Persist route IDs and expected states in the design handoff.
- Prefer one route or state per verification case and group them into suites for broader closeout runs.
- If semantic assertions are enabled, make Playwright emit a JSON report for the declared checks alongside screenshots and diffs.

## React Native / Expo

- Prefer Detox for deterministic emulator and simulator flows.
- Treat Maestro or EAS flows as complementary, not as the only gate.
- Fix locale, clock, seed data, and launch args before capture.
- Persist stable screen names, device targets, and masked dynamic regions in the design handoff.
- Prefer one screen-state pair per verification case and stable suites for broader regression runs.
- If semantic assertions are enabled, make Detox emit a JSON report that records required UI-state checks in addition to screenshots.

## Android

- Prefer official Compose Preview Screenshot Testing for Compose UI.
- Use emulator-driven UI capture and probes for non-Compose surfaces.
- Mirror Logcat into external verification state when Android execution is active.
- Prefer package- or pid-scoped Logcat capture when the test runner can resolve the app process deterministically.
- If semantic assertions are enabled, emit a JSON report for visibility, clipping, interaction-state, scroll, and occlusion checks.

## iOS

- Prefer simulator-first capture and stable device state.

## Backend And Infra

- Use deterministic smoke and contract checks.
- Verify Dockerized dependency connectivity for databases, queues, caches, and brokers.
