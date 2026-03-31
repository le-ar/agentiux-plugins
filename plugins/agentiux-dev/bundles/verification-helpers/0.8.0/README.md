# Verification Helper Bundle

This directory is the plugin-owned source of truth for deterministic visual helper code.

- `core/`: shared schema normalization, report assembly, and JSON writing.
- `playwright/`: DOM and browser-semantic helper entrypoint.
- `detox/`: Detox wrapper plus React Native probe contract helpers.
- `android-compose/`: Kotlin helper assets for Compose instrumented and screenshot tests.

Projects should not edit these files in place. Use `sync verification helpers` to materialize the generated bundle into `.verification/helpers/` inside a workspace.
