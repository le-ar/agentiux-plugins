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
- Web workspaces should also ship at least one live `browser-layout-audit` case so computed overlap, occlusion, clipping, and viewport regressions are exercised against a real rendered page.
- Android-capable workspaces should ship at least one Android-targeted visual case.
- Mobile workspaces should also ship at least one `native_layout_audit`-enabled native visual case so overlap, clipping, bounds, spacing, tap-target, and computed-style regressions fail from runner-emitted semantic reports.
- Canonical baselines should stay project-owned for reproducible CI checks.
- Transient screenshots, diffs, traces, videos, and plugin packets should stay outside the repo.

## Runner Adapters

- `playwright-visual`
- `browser-layout-audit`
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
- `native_layout_audit`
- `browser_layout_audit`

## Optional Semantic Assertions

- `semantic_assertions` is optional for non-visual and non-web cases, but web visual cases should always enable it.
- `native_layout_audit` is the post-run native geometry/style audit for `detox-visual` and `android-compose-screenshot` cases. It reads the semantic JSON report, validates target bounds and style tokens, detects pair overlap and root overflow, warns on suspicious spacing, rhythm, tap-target, and flex anomalies, and writes a separate audit report under the verification artifact root.
- `browser_layout_audit` is the live browser DOM audit config for cases that must verify a rendered page directly instead of relying only on runner-emitted semantic JSON.
- The shared rule catalog lives in `catalogs/layout_audit_rules.json`. Browser and native audit adapters should read thresholds from that catalog instead of hardcoding drift and gap tolerances separately.
- `warning` is a non-green verification state for both browser and native layout audits. Use it when the layout looks suspicious enough to review even if there is no hard overlap or clipping failure yet.
- Projects should materialize the plugin-owned helper bundle into `.verification/helpers/` before importing runner helpers.
- The helper catalog is read-only and reports bundle version, runner entrypoints, required host tools, and current materialization status.
- When enabled, the runner must emit a JSON report into the verification artifact root.
- The runtime validates helper sync status, runner capability compatibility, the shared report schema, and the declared `required_checks`.
- Contract-only semantic fixture reports may validate helper sync, shared report shape, and downstream audit contracts, but they do not replace live browser or live runner evidence.
- Verification recipes are schema v3.
- The semantic spec shape is `enabled`, `report_path`, `required_checks`, `targets`, `reachability_paths`, `limitation_entries`, `auto_scan`, `heuristics`, `artifacts`, and `platform_hooks`.
- Each target uses `target_id`, `locator`, `container_locator`, `scroll_container_locator`, `interactions`, `expected_attributes`, `expected_styles`, `expected_layout`, `allow_clipping`, `allow_occlusion`, and `allow_text_truncation`.
- Each `reachability_paths[]` entry uses `{path_id, title, target_id, required_for_action_ids, steps[]}`.
- Reachability steps are limited to `ensure_visible`, `scroll_to`, `tap`, `long_press`, `swipe`, `drag`, `type_text`, and `wait_for`.
- Each `limitation_entries[]` entry uses `{limitation_id, action_id, kind, reason, runner_scope}`.
- Shared check families are `presence_uniqueness`, `visibility`, `scroll_reachability`, `overflow_clipping`, `occlusion`, `interaction_states`, `computed_styles`, `layout_relations`, `text_overflow`, `accessibility_state`, and `screenshot_baseline`.
- Required checks for web visual cases: `presence_uniqueness`, `visibility`, `overflow_clipping`, `computed_styles`, `interaction_states`, `scroll_reachability`, `occlusion`.
- Recommended checks for Android: `visibility`, `overflow_clipping`, `interaction_states`, `scroll_reachability`, `occlusion`.
- `scroll_reachability` alone is only enough for scroll-only critical actions. Gesture-led actions such as `tap`, `long_press`, `swipe`, and `drag` should have either an authored reachability path on a supported runner or a persisted limitation entry.
- Canonical reachability paths execute only on `playwright-visual` and `detox-visual`.
- `android-compose-screenshot`, `shell-contract`, and other non-path runners must not pretend to execute gestures. They should use limitation entries and warning-level coverage gaps instead.
- Coverage audits should keep declared limitations visible as known gaps instead of treating them as green coverage.

## Shared Layout Rules

- Cross-surface layout rules currently include `pair-overlap`, `sibling-gap-inconsistency`, `alignment-drift`, `vertical-rhythm-drift`, `touch-target-too-small`, and `unexpected-flex-distribution`.
- Cross-surface warning rules should be treated as review gates, not as informational logs only.
- Web-specific rules currently include `viewport-overflow`, `container-padding-imbalance`, `contrast-warning`, and `ragged-grid-warning`.
- Native-first rules may extend the shared set when helper payloads expose more metadata, but thresholds should still live in the shared catalog.

## Web

- Prefer Playwright.
- Add at least one `browser-layout-audit` case per web workspace.
- Use fixed viewport matrix.
- Disable animations.
- Hide carets when taking screenshots.
- Mask dynamic zones.
- Persist route IDs and expected states in the design handoff.
- Prefer one route or state per verification case and group them into suites for broader closeout runs.
- If semantic assertions are enabled, make Playwright emit a JSON report for the declared checks alongside screenshots and diffs.
- If critical actions depend on gestures, wire authored reachability paths into the Playwright helper so the semantic report reflects the executed path contract.
- `browser-layout-audit` cases may start a local server via `argv` or `shell_command`, wait on `readiness_probe`, and then record a JSON audit report plus screenshot under the external artifact root.

## React Native / Expo

- Prefer Detox for deterministic emulator and simulator flows.
- Treat Maestro or EAS flows as complementary, not as the only gate.
- Fix locale, clock, seed data, and launch args before capture.
- Persist stable screen names, device targets, and masked dynamic regions in the design handoff.
- Prefer one screen-state pair per verification case and stable suites for broader regression runs.
- If semantic assertions are enabled, make Detox emit a JSON report that records required UI-state checks in addition to screenshots.
- If critical actions depend on gestures, wire authored reachability paths into the Detox helper so taps, long presses, swipes, drags, waits, and text entry happen before semantic validation.
- Prefer enabling `native_layout_audit` on Detox visual cases so React Native layouts are checked for overlap, clipping, occlusion, missing style data, suspicious spacing drift, and undersized tap targets even when screenshot diffs are noisy.

## Android

- Prefer official Compose Preview Screenshot Testing for Compose UI.
- Use emulator-driven UI capture and probes for non-Compose surfaces.
- Mirror Logcat into external verification state when Android execution is active.
- Prefer package- or pid-scoped Logcat capture when the test runner can resolve the app process deterministically.
- If semantic assertions are enabled, emit a JSON report for visibility, clipping, interaction-state, scroll, and occlusion checks.
- Prefer wiring Compose semantic helpers so `computed_styles`, `layout_relations`, `overflow_clipping`, and `occlusion` diagnostics are present for `native_layout_audit`.

## iOS

- Prefer simulator-first capture and stable device state.

## Backend And Infra

- Use deterministic smoke and contract checks.
- Verify Dockerized dependency connectivity for databases, queues, caches, and brokers.
