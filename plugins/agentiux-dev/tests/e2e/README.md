# AgentiUX Dev E2E Fixtures

This directory is the tracked preparation layer for full end-to-end coverage.

## Layout

```text
tests/e2e/
├── README.md
├── TEST_CATALOG.md
├── synthetic_surface_inventory.json
├── projects/
│   ├── codex-benchmark-workspace/
│   ├── fullstack-workspace/
│   ├── mobile-detox-app/
│   └── android-compose-lab/
└── tools/
    └── semantic_contract_runner.py
```

## Execution Model

- Fixture projects under `projects/` are committed templates only.
- Every scenario copies a fixture into a temp run root before it mutates repo files or AgentiUX Dev state.
- Each scenario isolates:
  - `AGENTIUX_DEV_STATE_ROOT`
  - `AGENTIUX_DEV_INSTALL_ROOT`
  - `AGENTIUX_DEV_MARKETPLACE_PATH`
- No e2e run should write to the real home-local plugin state or mutate the tracked fixture templates in place.

## Budget Contracts

- Cheap retrieval surfaces enforce two layers:
  - working budgets: stable hard gates used by deterministic e2e and `release_readiness.py`
  - absolute ceilings: serialization safety caps that trimming must never exceed, even under stress
- Dashboard performance coverage hard-gates overview, bootstrap, and plan-panel payload budgets, request timing budgets, cold-start budget, first-usable render budget, and exact request-path telemetry invariants.
- Benchmark evidence runs may also opt into `AGENTIUX_DEV_BENCHMARK_LOG`; when set, cheap retrieval surfaces append JSONL telemetry so external A/B runs can prove cache-hit, miss, payload, and trimming behavior with artifacts instead of theory.

## Fixture Roles

- `fullstack-workspace`: primary broad-spectrum fixture for detect/init/repair, workflow routing, workstreams, tasks, design state, context indexing, repo audit, upgrade, git, and dashboard-adjacent flows.
- `codex-benchmark-workspace`: dedicated read-only benchmark fixture for Codex CLI A/B evidence with real storefront checkout ownership, sibling admin distractors, backend readiness contracts, package-level verification commands, and tracked specs.
- `mobile-detox-app`: React Native and Expo fixture for auth-backed verification, helper sync, session reuse and binding, and Detox-native layout-audit branches.
- `android-compose-lab`: Android fixture for Compose screenshot verification, semantic contracts, and Android-native layout-audit branches.

## Reset And Cleanup Contract

- Broken or legacy states are synthesized on top of a clean temp clone. They are never stored as committed external-state snapshots.
- Destructive multi-phase scenarios should use the public `preview-reset-workspace-state` and `reset-workspace-state` commands between phases.
- `reset-workspace-state` is expected to remove:
  - the workspace state slice under `~/.agentiux/agentiux-dev/workspaces/<slug>--<hash>/`
  - the workspace context cache under `~/.agentiux/agentiux-dev/cache/context/<slug>--<hash>/`
  - workspace-scoped analytics event and learning-entry slices
  - the dashboard default workspace pointer when it points at the deleted workspace
- Final cleanup still deletes the entire temp run root after the scenario finishes.

## Fake Services And Deterministic Seeds

- `tools/semantic_contract_runner.py` produces deterministic semantic reports with layout bounds, clipping metadata, occlusion metadata, and style tokens so schema, helper, and native layout audit contracts can run without platform runtimes.
- `tools/semantic_contract_runner.py` is contract-only evidence. It must not be cited as proof of live runner, locator, interaction, or reachability integration.
- Additional fake services, if needed later, should live next to this tool layer and stay reusable across fixtures.
- Seeds must remain static and human-readable. If a test needs a broken state, generate it through a scenario mutator instead of committing a broken fixture.
- `synthetic_surface_inventory.json` is the tracked classification registry for fake, mock, stub, and synthetic helpers. Smoke coverage fails when a new helper appears without inventory classification.

## Current Entry Points

- `python3 scripts/external_repo_e2e.py`
- `python3 scripts/smoke_test.py`
- `python3 scripts/release_readiness.py run --repo-root /path/to/repo`

## Suite IDs

- `core-full-local`: stable Wave 1 acceptance suite. It does not expand for Wave 2.
- `wave2-foundation`: Subwave 2A only; foundation, workflow readback, and design readback coverage.
- `wave2-knowledge`: Subwave 2B only; command routing, analysis, verification, auth, and memory readback coverage.
- `wave2-self-host`: Subwave 2C only; migration, repo audit, starter, git, YouTrack, host-support, and legacy dashboard coverage.
- `wave2-full-local`: aggregate of all Wave 2 executable cases.
- `catalog-implemented-local`: aggregate of `core-full-local` and `wave2-full-local`; this is the top local suite for all catalog rows that already have executable coverage.
- `codex-cli-ab-evidence`: supplementary sequential benchmark suite that compares `bootstrap-assisted` warmed `codex-benchmark-workspace` fixture clones against raw Codex baseline clones through real `codex exec`. It is evidence-only, internal to benchmark coverage, and does not replace deterministic blocking gates.
- `codex-cli-runtime-warm-evidence`: supplementary single-pass benchmark suite that runs the same four tasks after `init-workspace` + `refresh-context-index`, through an isolated home-local installed plugin copy and product-like plugin registration path, but without any external bootstrap injection.

## Execution Notes

- `run.py` still defaults to `core-full-local` when no explicit `--case` or `--suite` filter is provided.
- Wave 2 self-host and aggregate suites start the fake YouTrack server on `127.0.0.1`; restricted sandboxes must allow localhost binds for those suites.
- All new Wave 2 scenarios keep fixture mutations, state roots, starter outputs, worktrees, and self-host artifacts inside temp roots only.
- Live browser layout audit remains the higher-trust web verification signal. Mobile and Android semantic fixture coverage stays partially synthetic until a live runner path is available for those surfaces.
- The Codex benchmark harness supports three explicit modes: `raw`, `runtime-warm`, and `bootstrap-assisted`.
- `raw`: fresh fixture clone only, no plugin prewarm, no plugin runtime registration in the benchmark `CODEX_HOME`, no installed plugin runtime mount, and no external bootstrap.
- `runtime-warm`: fresh fixture clone plus `init-workspace` + `refresh-context-index` before timed `codex exec`; the harness seeds an isolated `HOME`, `.codex`, marketplace file, and installed plugin copy so Codex sees the same registration path as a product session, but no `model_instructions_file` is passed.
- `bootstrap-assisted`: `runtime-warm` plus an external benchmark bootstrap projection passed through `codex exec -c model_instructions_file=<run_root>/codex-bootstrap.md`.
- `codex-cli-ab-evidence` and `codex-cli-runtime-warm-evidence` must run sequentially, never in parallel. Each replica uses a fresh `codex-benchmark-workspace` fixture clone and a fresh isolated `AGENTIUX_DEV_STATE_ROOT`.
- The Codex CLI suite runs four benchmark tasks on the external fixture: owner-file routing, verification command discovery, cross-app disambiguation, and symptom-to-owner triage.
- Warm replicas seed an isolated home-local plugin install plus benchmark-local `HOME`, `CODEX_HOME`, and marketplace state. This keeps raw runs from inheriting the operator's real host plugin enablement while making warm runs closer to real product sessions.
- `bootstrap-assisted` replicas prewarm plugin state with `init-workspace`, `refresh-context-index`, and an internal benchmark bootstrap projection built from `show-workspace-context-pack` before timing starts, then pass the generated transport file through `codex exec -c model_instructions_file=<run_root>/codex-bootstrap.md`. No `AGENTS.md` is written into the temp fixture clone or the tracked fixture template, and the bootstrap file must stay outside the workspace subtree.
- The benchmark projection persists compact query-scoped records in the shared `context_store.sqlite` query cache and only materializes markdown on demand; the tracked fixture clone never becomes the source of truth for warmed bootstrap state.
- Command-oriented bootstraps now publish exact package-owned `command_hints`. `bootstrap-assisted` replicas should use those exact commands when they return `candidate_commands`, not expanded script bodies or helper-command fallbacks.
- The internal benchmark projection always uses the task's exact `benchmark_query` as `request_text`, with `limit=8` and `semantic_mode="disabled"`. The four concrete queries are encoded in the benchmark task definitions so the bootstrap-assisted prewarm stays reproducible inside the harness without exposing a public runtime bootstrap command.
- `run.py --benchmark-task <task-id>` can narrow either benchmark suite to one or more benchmark tasks without changing the underlying harness mode. This is intended for regression debugging of one task without paying for all four.
- Raw replicas stay cold: fresh fixture clone only, no plugin bootstrap, no plugin registration in the benchmark `CODEX_HOME`, no installed plugin runtime mounted into the Codex sandbox, and no seeded user `rules/` or `vendor_imports/` carried into benchmark `CODEX_HOME`.
- The suite enforces prompt parity by hashing the per-task user prompt and asserting that bootstrap-assisted/raw replicas receive the same prompt text. The only bootstrap-assisted-vs-raw difference is the external plugin-generated bootstrap and access to the installed plugin runtime.
- The suite records output-schema constrained final JSON, raw `--json` event logs, benchmark JSONL telemetry when helper commands run, command-path metrics, usage-counter deltas, file and command recall/precision, `primary_file_rank`, minimality penalties, prompt parity, bootstrap-outside-workspace checks, and concise in-flight progress logs. Provider token usage and provider cost remain explicitly unavailable.
- Benchmark summaries now expose `prewarm_bootstrap_payload_bytes`, `manual_shell_invocation_count`, `manual_read_operation_count`, `unique_repo_path_count`, `distractor_path_touch_count`, `plugin_helper_invocation_count`, and `all_expected_replicas_available` so the evidence report can show whether bootstrap-assisted routing stayed narrow instead of merely being faster.
- The comparison summary now also reports `planned_*_count`, `completed_*_count`, `unavailable_run_count`, and `all_expected_replicas_available`. If Codex quota or provider failures drop planned replicas, the suite keeps artifacts but downgrades benchmark status to `warning`.
- Latest bootstrap-assisted A/B rerun on `2026-04-05`: `comparison.status=warning` with `all_expected_replicas_available=true`. Aggregate medians were `84.9s` bootstrap-assisted vs `117.4s` raw, with bootstrap-assisted reducing manual shell invocations from `15.0` to `7.0`, manual reads from `10.5` to `6.5`, unique repo paths from `22.0` to `6.5`, and distractor-path touches from `3.0` to `1.0`. `cross-app-disambiguation` and `verification-command-discovery` were full bootstrap-assisted wins; the remaining warning is driven by an owner-file-routing read-operation regression and by `symptom-to-owner-triage` still trailing raw on core-file recall and file precision.
- Latest `runtime-warm` vs `raw` A/B rerun on `2026-04-06` used a one-off evidence driver that reused the existing `_run_condition` harness for only those two modes and spent exactly `8` total `codex exec` calls (`4` raw + `4` runtime-warm, one run per benchmark task, with balanced condition order across tasks). `runtime-warm` completed all `4/4` tasks. Aggregate medians were `29.9s` runtime-warm vs `74.9s` raw, `0.0` vs `8.5` manual shell invocations, `0.0` vs `6.5` manual reads, `0.0` vs `22.0` unique repo paths, and `0.0` vs `3.0` distractor-path touches. The trade-off is answer quality: runtime-warm improved valid-file precision (`1.00` vs `0.71`) and minimality (`0.0` vs `1.5`), but raw still held higher median core-file recall (`0.88` vs `0.65`) because `owner-file-routing` and `symptom-to-owner-triage` each dropped one expected supporting/backend file in warm mode. `cross-app-disambiguation` was a clear runtime-warm win; `verification-command-discovery` remains weak in both modes, though runtime-warm at least returned one useful exact command while raw returned none.
- The runtime surface now exposes `show_runtime_preflight` for that first warm-repo move. It reuses the shared retrieval backend, returns `repo_maturity`, top owner candidates, exact package-owned commands, `next_read_paths`, `do_not_scan_paths`, and stop/go guidance, can reuse the last high-confidence runtime request when a follow-up call omits text, and stays separate from benchmark-only bootstrap transport.

### Latest Bootstrap-Assisted A/B Snapshot

| Metric | Bootstrap-assisted | Raw | Delta |
| --- | ---: | ---: | ---: |
| Median wall clock | `84.9s` | `117.4s` | `-27.7%` |
| Manual shell invocations | `7.0` | `15.0` | `-53.3%` |
| Manual read operations | `6.5` | `10.5` | `-38.1%` |
| Unique repo paths touched | `6.5` | `22.0` | `-70.5%` |
| Distractor-path touches | `1.0` | `3.0` | `-66.7%` |

| Benchmark task | Scenario | Status | Bootstrap-assisted | Raw | Notes |
| --- | --- | --- | ---: | ---: | --- |
| `cross-app-disambiguation` | Route storefront checkout CTA ownership without drifting into admin checkout. | `ok` | `35.2s` | `77.6s` | Full bootstrap-assisted win; same core-file recall/precision, much narrower path spread. |
| `verification-command-discovery` | Find exact package-owned verification commands and owner files for checkout and `/ready`. | `ok` | `149.0s` | `236.7s` | Full bootstrap-assisted win; command precision/recall stayed `1.0` vs raw median `0.5`. |
| `owner-file-routing` | Find the smallest owner set for checkout CTA, `/ready`, and checkout spec. | `warning` | `89.0s` | `101.2s` | Faster and narrower, but bootstrap-assisted median reads regressed from `7.0` to `8.5`. |
| `symptom-to-owner-triage` | Start from readiness failure output and return smallest owner set plus next command. | `warning` | `84.9s` | `145.4s` | Faster and narrower, but bootstrap-assisted trailed raw on core-file recall `0.50` vs `0.62` and valid-file precision `0.58` vs `0.62`. |

### Latest Runtime-Warm vs Raw A/B Snapshot

Fresh A/B evidence on `2026-04-06` used the same benchmark tasks and prompt contracts as the cataloged suites, but only compared `raw` and `runtime-warm`. The run spent exactly `8` total `codex exec` calls and completed every task in both modes.

| Metric | Runtime-warm | Raw | Delta |
| --- | ---: | ---: | ---: |
| Median wall clock | `29.9s` | `74.9s` | `-60.1%` |
| Manual shell invocations | `0.0` | `8.5` | `-100.0%` |
| Manual read operations | `0.0` | `6.5` | `-100.0%` |
| Unique repo paths touched | `0.0` | `22.0` | `-100.0%` |
| Distractor-path touches | `0.0` | `3.0` | `-100.0%` |
| Plugin helper invocations | `1.0` | `0.0` | `runtime-warm only` |
| Median core-file recall | `0.65` | `0.88` | `raw +0.23` |
| Median valid-file precision | `1.00` | `0.71` | `runtime-warm +0.29` |
| Median total minimality penalty | `0.0` | `1.5` | `runtime-warm -1.5` |

| Benchmark task | Outcome | Runtime-warm | Raw | Notes |
| --- | --- | ---: | ---: | --- |
| `owner-file-routing` | `mixed` | `27.3s` | `77.1s` | Runtime-warm stayed plugin-first with `0` shell reads and no distractors, but it returned `4` files and missed one expected backend/supporting file (`core recall 0.80` vs raw `1.00`). |
| `verification-command-discovery` | `warning` | `85.2s` | `27.1s` | Raw finished faster but returned no useful owner files or commands (`0.00` file precision, `0.00` command recall/precision). Runtime-warm returned one exact verification command with better quality, but still under-recalled the full package/config/spec owner set and paid a large latency cost. |
| `cross-app-disambiguation` | `ok` | `32.1s` | `72.7s` | Clear runtime-warm win: it returned exactly the `2` owner files with `1.00` recall/precision and no distractor touches, while raw kept an extra wrapper file and drifted into admin-adjacent reads. |
| `symptom-to-owner-triage` | `mixed` | `27.6s` | `150.5s` | Runtime-warm was much faster and perfectly precise, but it narrowed too aggressively and dropped one expected supporting/backend file (`core recall 0.50` vs raw `0.75`). |

| Benchmark task | `benchmark_query` used for bootstrap-assisted prewarm | Exact prompt sent to `codex exec` |
| --- | --- | --- |
| `owner-file-routing` | `Find the smallest owner file set for the storefront checkout entrypoint, the shared checkout CTA label, the backend /ready contract, and the Playwright checkout spec.` | `Read-only benchmark. Inspect this repository and identify the smallest set of files you would inspect if asked to change the storefront checkout CTA copy, confirm the storefront checkout entrypoint, inspect the backend /ready contract, and inspect the Playwright spec that verifies checkout CTA text. Ignore admin checkout unless the requested storefront flow imports it. This task is about owner files, so return candidate_commands=[] unless a command source file is itself essential evidence. Do not edit files. Do not run formatters, installers, package managers, or git write operations. Return only JSON that matches the provided schema.` |
| `verification-command-discovery` | `Find the minimal package-level verification commands and owner files for the storefront checkout CTA and the backend /ready contract.` | `Read-only benchmark. Inspect this repository and identify the smallest set of files and package-level shell commands you would use to verify a small change to the storefront checkout CTA and backend /ready contract. Do not execute commands. Do not edit files. Do not run formatters, installers, package managers, or git write operations. Return only JSON that matches the provided schema.` |
| `cross-app-disambiguation` | `Find the storefront checkout route file and shared package file that own the customer checkout CTA copy.` | `Read-only benchmark. Inspect this repository and identify the smallest set of files that own the customer storefront checkout entrypoint and shared CTA label across app/package boundaries. Do not include admin checkout unless the storefront files explicitly depend on it. This task is about app/package routing, so return candidate_commands=[] unless a command source file is itself essential evidence. Do not edit files. Do not run formatters, installers, package managers, or git write operations. Return only JSON that matches the provided schema.` |
| `symptom-to-owner-triage` | `Given a readiness failure that returned admin-console metadata instead of the storefront checkout readiness contract, find the smallest owner files and minimal package-level command to inspect next.` | `Read-only benchmark. A verification log ended with:<br>AssertionError: GET /ready expected {"status":"ok","source":"storefront-checkout"}<br>Received: {"status":"booting","source":"admin-console"}<br>Inspect this repository and return the smallest set of files plus the minimal package-level command you would inspect next to triage the owner of this failure. Do not execute commands. Do not edit files. Do not run formatters, installers, package managers, or git write operations. Return only JSON that matches the provided schema.` |

## Adding New Coverage

- Reuse the smallest fixture that still represents the branch you need.
- Prefer adding a scenario mutator over creating a new committed broken fixture.
- Update `TEST_CATALOG.md` in the same change whenever a public surface, branch, or runner contract changes.
