# AgentiUX Dev

AgentiUX Dev is a home-local Codex plugin for development workflows that should not leak Codex-only state into project repositories. It owns external workspace state, task and workstream routing, deterministic verification, design orchestration, local Git guidance, and a read-only dashboard for Codex-driven work.

## Quick Start

Initialize an existing repository in external state and let the plugin route the next action:

```bash
python3 scripts/agentiux_dev_state.py preview-init --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py init-workspace --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py workflow-advice --workspace /path/to/repo --request-text "Fix the CTA spacing"
python3 scripts/agentiux_dev_state.py run-verification-suite --workspace /path/to/repo --suite-id full
```

Sync the source plugin into the home-local installed copy:

```bash
python3 scripts/install_home_local.py
```

If the installer finds a writable user directory that is already in `PATH`, it also installs a short `agentiux` launcher there. Otherwise re-run with an explicit bin directory:

```bash
python3 scripts/install_home_local.py --bin-dir /path/already/in/PATH
```

The source repo remains the place where you edit and verify the plugin. The installed copy under `~/plugins/agentiux-dev` is the runtime snapshot that Codex loads for day-to-day use.

## Core Rules

- Keep all Codex-specific workflow state outside project repositories.
- `workflow-advice` may auto-create or reuse point tasks for narrow fixes in initialized repositories, but workstreams, starter bootstrapping, stage plan changes, and upgrade application stay confirmation-driven.
- Verification events, stdout, stderr, Android logcat, and artifacts stay in external plugin state so the repo remains clean.
- Local Git helpers inspect and write locally only. They do not push branches or publish pull requests.

## Recommended Flows

- Small targeted fix: initialize the repo, let `workflow-advice` create or reuse a task, then run targeted verification.
- Large feature or epic: create a workstream, confirm the stage plan, execute, and close stages explicitly.
- YouTrack triage: connect a token-scoped tracker, inspect shortlisted issues with description, work items, comments, recent activity, and linked-issue context, draft a workstream plan, and apply it only after explicit confirmation.
- Existing repo hardening: initialize, audit the repository, inspect the upgrade plan, then apply only confirmed items.
- Greenfield work: choose a starter preset, run the upstream CLI through the plugin, then initialize the new workspace explicitly.
- Self-hosting this plugin: run the same workflow on this repository and let the `plugin-platform` profile route the work.

## Core Capabilities

- external workspace state under `~/.agentiux/agentiux-dev/`
- explicit workspace initialization before state creation
- automatic workflow advice that proposes initialization, starters, workstreams, or tasks from plain user requests without writing state automatically
- named workstreams with independent stage registers, briefs, design state, and verification state
- lightweight tasks for point fixes that should not require a full workstream
- design briefs, reference boards, handoffs, and cached previews outside project repos
- verification recipes, runs, progress events, baseline status, and logs outside project repos
- explicit stage planning where template fragments stay advisory and concrete stage definitions are user-approved
- deterministic verification guidance for web, mobile, backend, monorepo, and plugin-runtime work
- curated greenfield starters as thin wrappers around official CLIs
- repository audits and upgrade plans for existing repos
- state repair for stale or profile-inaccurate workspace state
- host-aware support reporting across Windows, Linux, and macOS
- repo-aware Git workflow advice plus safe local branch, staging, and commit actions
- workspace-scoped YouTrack integration with permanent-token connections, persisted search sessions, richer issue context plus linked-issue analysis for planning, idempotent plan apply, and issue-ledger aggregation
- repo-tracked low-token catalogs for skills, MCP tools, scripts, references, and intent routes
- global project context indexing and semantic cache under `~/.agentiux/agentiux-dev/cache/context/`
- a local-only dashboard launched from chat, with dashboard writes limited to YouTrack integration management

## Low-Token Retrieval

Codex should prefer the low-token retrieval ladder before reading large docs or Python entrypoints:

1. existing cheap summaries such as plugin stats, dashboard snapshot, and workspace detail
2. `show intent route`
3. `show capability catalog`
4. `show workspace context pack`
5. `search context index`
6. targeted file reads
7. broad manual exploration only when the earlier layers are insufficient

The plugin keeps canonical versioned catalogs in `catalogs/` and stores project-derived context indexes globally under `~/.agentiux/agentiux-dev/cache/context/<workspace-fingerprint>/`. No project-derived context cache is written into repositories.

## Public Command Surface

- `initialize workspace`
- `preview repair workspace state`
- `repair workspace state`
- `show state paths`
- `show stages`
- `show active brief`
- `propose stage plan changes`
- `apply stage plan changes`
- `continue work`
- `close current stage`
- `launch gui`
- `stop gui`
- `show gui url`
- `run verification case`
- `run verification suite`
- `show verification log`
- `show verification recipes`
- `show verification helper catalog`
- `sync verification helpers`
- `show capability catalog`
- `show intent route`
- `show workspace context pack`
- `search context index`
- `refresh context index`
- `audit verification coverage`
- `resolve verification`
- `approve verification baseline`
- `update verification baseline`
- `show host support`
- `show host setup plan`
- `install host requirements`
- `repair host requirements`
- `create workstream`
- `list workstreams`
- `switch workstream`
- `show current workstream`
- `close current workstream`
- `create task`
- `switch task`
- `list tasks`
- `show current task`
- `close current task`
- `show youtrack connections`
- `connect youtrack`
- `update youtrack connection`
- `remove youtrack connection`
- `search youtrack issues`
- `show youtrack issue queue`
- `propose youtrack workstream plan`
- `apply youtrack workstream plan`
- `audit repository`
- `show upgrade plan`
- `apply upgrade plan`
- `create starter`
- `show starter presets`
- `suggest branch name`
- `suggest commit message`
- `suggest pr title`
- `suggest pr body`
- `show git workflow advice`
- `show git state`
- `plan git change`
- `create git branch`
- `stage git files`
- `create git commit`

Localized aliases are matched at runtime. The tracked source remains English-only, and Codex should reply to the user in the user's language unless the user asks to switch.

## Agents

- `workspace-kernel`: initializes external state, routes intent, and enforces language and workflow guardrails.
- `stage-planning`: handles proposed or confirmed updates to unfinished stage definitions inside the current workstream.
- `stage-execution`: owns explicit execution intent, brief creation, and active workstream stage progression.
- `stage-closeout`: closes the active stage, updates external state, and prepares the next stage safely.
- `web-platform`: routes React, Next.js, TypeScript, and Tailwind work into web-aware verification and design behavior.
- `mobile-platform`: routes Expo, React Native, Android, and iOS work into mobile-aware verification and design behavior.
- `backend-platform`: handles backend workflow decisions for NestJS, Rust, and service contracts.
- `monorepo-platform`: scopes Nx-aware work and verification inside multi-surface repositories.
- `plugin-platform`: handles self-hosted plugin development across Python scripts, MCP tooling, installer flow, dashboard health, and release-readiness gates.
- `git-ops`: inspects commit history or commitlint-style rules before suggesting or creating commit messages.
- `docs-sync`: keeps real project docs aligned with approved changes.
- `deterministic-verification`: defines or reviews deterministic checks, scoped reruns, baseline lifecycle, and closeout evidence.
- `design-orchestrator`: runs the design brief, reference search, and persisted board workflow.
- `web-product-designer`: turns a chosen design direction into an implementation-ready web handoff.
- `expo-product-designer`: turns a chosen design direction into an implementation-ready Expo and React Native handoff.

## CLI Reference

- `python3 scripts/agentiux_dev_state.py preview-init --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py init-workspace --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py preview-repair-workspace-state --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py repair-workspace-state --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py migrate-workspace-state --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py paths --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py show-host-support --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py workflow-advice --workspace /path/to/repo --request-text "Fix the CTA spacing"`
- `python3 scripts/agentiux_dev_state.py show-capability-catalog --route-id git --query-text "commit worktree branch"`
- `python3 scripts/agentiux_dev_state.py show-intent-route --request-text "Inspect plugin dashboard and MCP tool catalogs"`
- `python3 scripts/agentiux_dev_state.py show-workspace-context-pack --workspace /path/to/repo --request-text "Inspect checkout verification" --route-id verification`
- `python3 scripts/agentiux_dev_state.py search-context-index --workspace /path/to/repo --query-text "Detox helper bundle drift"`
- `python3 scripts/agentiux_dev_state.py refresh-context-index --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py current-workstream --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py create-workstream --workspace /path/to/repo --title "Checkout Feature"`
- `python3 scripts/agentiux_dev_state.py create-task --workspace /path/to/repo --title "Fix CTA" --objective "Tighten spacing"`
- `python3 scripts/agentiux_dev_state.py switch-task --workspace /path/to/repo --task-id task-123`
- `python3 scripts/agentiux_dev_state.py show-youtrack-connections --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py connect-youtrack --workspace /path/to/repo --base-url https://tracker.example.com --token perm:xxxx --project-scope SL`
- `python3 scripts/agentiux_dev_state.py search-youtrack-issues --workspace /path/to/repo --query-text "assignee: me"`
- `python3 scripts/agentiux_dev_state.py propose-youtrack-workstream-plan --workspace /path/to/repo --search-session-id yt-search-123 --selected-issue-id SL-100`
- `python3 scripts/agentiux_dev_state.py apply-youtrack-workstream-plan --workspace /path/to/repo --plan-id yt-plan-123 --confirmed`
- `python3 scripts/agentiux_dev_state.py detect-commit-style --repo-root /path/to/repo`
- `python3 scripts/agentiux_dev_state.py suggest-commit-message --repo-root /path/to/repo --summary "Improve dashboard log view"`
- `python3 scripts/agentiux_dev_state.py audit-repository --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py show-upgrade-plan --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py verification-recipes --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py resolve-verification --workspace /path/to/repo`
- `python3 scripts/agentiux_dev_state.py run-verification-case --workspace /path/to/repo --case-id home-route`
- `python3 scripts/agentiux_dev_state.py approve-verification-baseline --workspace /path/to/repo --case-id home-route`
- `python3 scripts/agentiux_dev_state.py create-starter --preset-id next-web --destination-root /tmp --project-name demo`
- `python3 scripts/agentiux_dev_state.py suggest-branch-name --repo-root /path/to/repo --summary "Improve dashboard log view"`
- `python3 scripts/agentiux_dev_state.py suggest-pr-title --repo-root /path/to/repo --summary "Improve dashboard log view"`
- `python3 scripts/agentiux_dev_state.py suggest-pr-body --repo-root /path/to/repo --summary "Improve dashboard log view"`
- `python3 scripts/agentiux_dev_state.py show-git-workflow-advice --repo-root /path/to/repo`
- `python3 scripts/release_readiness.py run --repo-root /path/to/repo`
- `python3 scripts/agentiux_dev_gui.py launch`
- `python3 scripts/agentiux_dev_gui.py stop`
- `python3 scripts/agentiux.py web`
- `python3 scripts/install_home_local.py`
- `python3 scripts/smoke_test.py`

## Host Support

- Windows, Linux, and macOS share the same core plugin runtime for state, MCP, GUI, workflow advice, audits, tasks, workstreams, and Git guidance.
- iOS execution remains macOS-only.
- Android, web, backend, and starter execution remain host-aware and only run when the required toolchain is present.
- `show host setup plan` previews host-specific install or manual repair steps without mutating the machine.
- `install host requirements` and `repair host requirements` run only after explicit confirmation and refresh `show host support` state after execution.
- Automatic host setup currently covers the deterministic package-manager path the plugin can defend on the current host: Homebrew on macOS, APT on Linux, and WinGet or Chocolatey on Windows for supported tools such as Node.js and Android platform tools.
- Interactive or high-risk flows such as Xcode command-line tools and Docker remain manual.
- Installed copies normalize `.mcp.json` to a host-appropriate Python launcher during install or sync.

## Launching the GUI

After `install_home_local.py` installs the global launcher, you can start or reuse the dashboard singleton from any directory:

```bash
agentiux web
agentiux web demo-workspace
agentiux web status
agentiux web url
agentiux web stop
```

`agentiux web <workspace-selector>` accepts an initialized workspace path, a path inside the workspace, or a short selector such as the workspace slug or name. Repeated launch commands reuse the existing server process and only update the default workspace selection when needed.

Start the local dashboard from the plugin root:

```bash
python3 scripts/agentiux_dev_gui.py launch
```

Start the dashboard with a default workspace selected:

```bash
python3 scripts/agentiux_dev_gui.py launch --workspace /path/to/repo
```

Check the current GUI status and URL:

```bash
python3 scripts/agentiux_dev_gui.py status
```

Stop the dashboard:

```bash
python3 scripts/agentiux_dev_gui.py stop
```

The `launch` command returns the local URL, process id, and log file paths. Runtime state is stored under `~/.agentiux/agentiux-dev/runtime/dashboard.json`.

The dashboard now uses browser routing:

- `/` shows the global overview without forcing a workspace detail panel
- `/workspaces/<url-encoded-workspace-path>` opens a specific workspace detail view and survives browser refresh or direct open
- legacy `?workspace=/path/to/repo` links remain accepted and are rewritten to the canonical workspace route after load

## Automatic Routing

If the user starts with a plain request instead of a canonical phrase, the plugin should route it automatically:

- propose workspace initialization immediately when the repo is unmanaged
- resolve the low-token intent route before opening large docs or scripts
- propose a starter when the request is greenfield
- propose a workstream for large feature work
- auto-create or reuse a task for small targeted fixes in initialized repositories
- inspect commit history or commit rules before suggesting a commit message

The runtime helper for this is:

```bash
python3 scripts/agentiux_dev_state.py workflow-advice --workspace /path/to/repo --request-text "Implement checkout flow across web and backend"
```

The low-token retrieval helpers for the same request are:

```bash
python3 scripts/agentiux_dev_state.py show-intent-route --request-text "Implement checkout flow across web and backend"
python3 scripts/agentiux_dev_state.py show-capability-catalog --route-id workstream
python3 scripts/agentiux_dev_state.py show-workspace-context-pack --workspace /path/to/repo --request-text "Implement checkout flow across web and backend" --route-id workstream
python3 scripts/agentiux_dev_state.py search-context-index --workspace /path/to/repo --query-text "checkout flow verification selectors"
```

## Working With Workstreams And Tasks

Use workstreams for large features, epics, and greenfield development. Codex should propose this automatically for large requests, but the explicit commands remain available:

```bash
python3 scripts/agentiux_dev_state.py create-workstream --workspace /path/to/repo --title "Checkout Feature" --branch-hint feature/checkout
python3 scripts/agentiux_dev_state.py switch-workstream --workspace /path/to/repo --workstream-id checkout-feature
python3 scripts/agentiux_dev_state.py stage-register --workspace /path/to/repo --workstream-id checkout-feature
```

New workstreams start with an empty stage register and `plan_status=needs_user_confirmation`. Persist the first concrete stage plan only after explicit confirmation:

```bash
python3 scripts/agentiux_dev_state.py write-stage-register --workspace /path/to/repo --register-file /tmp/register.json --confirmed-stage-plan-edit
```

Use tasks for small targeted changes. In initialized repositories, `workflow-advice` auto-creates or reuses the active point task for narrow fixes; the explicit commands remain available:

```bash
python3 scripts/agentiux_dev_state.py create-task --workspace /path/to/repo --title "Fix CTA spacing" --objective "Tighten hero CTA spacing"
python3 scripts/agentiux_dev_state.py current-task --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py close-task --workspace /path/to/repo
```

`continue work` resolves to the current task when task mode is active. In workstream mode it is valid only after a concrete stage plan has been confirmed.

## Running Verification

Read the current verification recipe document:

```bash
python3 scripts/agentiux_dev_state.py verification-recipes --workspace /path/to/repo
```

Run one deterministic case:

```bash
python3 scripts/agentiux_dev_state.py run-verification-case --workspace /path/to/repo --case-id home-route
```

Run one deterministic case and follow live progress in the terminal:

```bash
python3 scripts/agentiux_dev_state.py run-verification-case --workspace /path/to/repo --case-id home-route --follow
```

Run one deterministic suite:

```bash
python3 scripts/agentiux_dev_state.py run-verification-suite --workspace /path/to/repo --suite-id full
```

Inspect run status and recent logs:

```bash
python3 scripts/agentiux_dev_state.py verification-run --workspace /path/to/repo --run-id <run-id>
python3 scripts/agentiux_dev_state.py verification-events --workspace /path/to/repo --run-id <run-id>
python3 scripts/agentiux_dev_state.py verification-log --workspace /path/to/repo --run-id <run-id> --stream stdout
python3 scripts/agentiux_dev_state.py verification-log --workspace /path/to/repo --run-id <run-id> --stream logcat
python3 scripts/agentiux_dev_state.py audit-verification-coverage --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py show-verification-helper-catalog --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py sync-verification-helpers --workspace /path/to/repo
```

Approve or update a project-owned baseline:

```bash
python3 scripts/agentiux_dev_state.py approve-verification-baseline --workspace /path/to/repo --case-id home-route --run-id <run-id>
python3 scripts/agentiux_dev_state.py update-verification-baseline --workspace /path/to/repo --case-id home-route --run-id <run-id>
```

Verification runs write structured events, stdout, stderr, Android logcat when configured, and linked artifacts under the external workspace verification root, so Codex and the GUI can show progress without assuming a hang. Canonical baselines remain project-owned for reproducible CI checks. Coverage audits report warning-level gaps without failing the workspace automatically.

Visual cases may also declare optional `semantic_assertions`. The plugin now owns a versioned helper bundle under `bundles/verification-helpers/<plugin-version>/`, and projects materialize the current neutral runtime snapshot into `.verification/helpers/` with `sync verification helpers`.

The semantic spec supports `enabled`, `report_path`, `required_checks`, `targets`, `auto_scan`, `heuristics`, `artifacts`, and `platform_hooks`. Targets are platform-neutral and use locator kinds such as `selector`, `role`, `test_id`, `semantics_tag`, or `text`, plus expected attributes, styles, layout invariants, and clipping or occlusion allowances. At runtime AgentiUX Dev writes the resolved spec into the run root, passes helper and report env vars to the runner, validates helper sync and capability compatibility, and records `semantic_summary` in case and run state.

The shared deterministic check families are `presence_uniqueness`, `visibility`, `scroll_reachability`, `overflow_clipping`, `occlusion`, `interaction_states`, `computed_styles`, `layout_relations`, `text_overflow`, `accessibility_state`, and `screenshot_baseline`. Coverage audits now also warn when semantic cases have no explicit targets, when helper bundles are missing or stale, or when required checks do not match the runner capability matrix.

`resolve verification` returns a canonical `VerificationSelection` payload with `selection_status`, `source`, `requested_mode`, `requested_mode_source`, `resolved_mode`, `selected_cases`, `heuristic_suggestions`, `baseline_sources`, and `host_compatibility`. Tasks without explicit selectors remain unresolved and targeted by default; the runtime does not silently fall back to `smoke`.

## Existing Repositories

The existing repo flow is:

```bash
python3 scripts/agentiux_dev_state.py preview-init --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py init-workspace --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py audit-repository --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py show-upgrade-plan --workspace /path/to/repo
python3 scripts/agentiux_dev_state.py apply-upgrade-plan --workspace /path/to/repo --confirmed
```

The audit is read-only for repo code. Applying an upgrade plan creates confirmed workstreams and tasks for the detected gaps after explicit confirmation and does not synthesize a generic umbrella workstream automatically.

## Greenfield Starters

Codex should propose starter presets automatically for greenfield requests. The explicit commands remain available when you want direct control.

List the curated starter presets:

```bash
python3 scripts/agentiux_dev_state.py starter-presets
```

Create a starter:

```bash
python3 scripts/agentiux_dev_state.py create-starter --preset-id next-web --destination-root /tmp/projects --project-name demo-web
```

The curated presets are:

- `next-web`
- `expo-mobile`
- `nestjs-api`
- `rust-service`
- `nx-fullstack`

Each starter uses the official upstream CLI for creation only. It records the starter run in external plugin state and leaves workspace initialization, stage planning, verification setup, and design state creation for explicit follow-up confirmation with the user.

## Commit Style Matching

When the user asks for a commit, inspect local commit style first:

```bash
python3 scripts/agentiux_dev_state.py inspect-git-state --repo-root /path/to/repo
python3 scripts/agentiux_dev_state.py list-git-worktrees --repo-root /path/to/repo
python3 scripts/agentiux_dev_state.py plan-git-change --repo-root /path/to/repo
python3 scripts/agentiux_dev_state.py detect-commit-style --repo-root /path/to/repo
python3 scripts/agentiux_dev_state.py suggest-commit-message --repo-root /path/to/repo --summary "Improve dashboard log view"
python3 scripts/agentiux_dev_state.py create-git-worktree --repo-root /path/to/repo --path ../repo-dashboard-log-view --branch-name feature/dashboard-log-view
python3 scripts/agentiux_dev_state.py create-git-branch --repo-root /path/to/repo --branch-name task/dashboard-log-view
python3 scripts/agentiux_dev_state.py stage-git-files --repo-root /path/to/repo --file src/app.ts
python3 scripts/agentiux_dev_state.py create-git-commit --repo-root /path/to/repo --message "feat: improve dashboard log view"
```

If the repository already has a commit convention, the suggested message should follow it. If history is sparse or there are no explicit rules, the fallback is a conventional-commit style suggestion plus neutral branch prefixes like `task/` and `feature/`. Use linked worktrees for parallel or long-running workstreams; the local git execution helpers stay local-only and do not push or publish PRs.

## Self-Hosting This Plugin

Initialize the source-of-truth repo itself as a workspace from the repo root, not from the installed copy:

```bash
python3 plugins/agentiux-dev/scripts/agentiux_dev_state.py preview-init --workspace /path/to/agentiux-plugins
```

The preview for this repo should include `plugin-platform` in `selected_profiles` and detect `python`, `codex-plugin`, `mcp-server`, and `local-dashboard`.

For this repo, the expected daily verification flow is:

```bash
python3 plugins/agentiux-dev/scripts/agentiux_dev_state.py verification-recipes --workspace /path/to/agentiux-plugins
python3 plugins/agentiux-dev/scripts/agentiux_dev_state.py run-verification-suite --workspace /path/to/agentiux-plugins --suite-id full
```

## Release Readiness

Run the full production gate from the source repo root:

```bash
python3 plugins/agentiux-dev/scripts/release_readiness.py run --repo-root . --smoke-runs 3
```

This gate checks public-safe source auditing, English-only tracked source, Python compile health, self-host detection, MCP handshake, dashboard health, repeated smoke runs, and the integrated v2 workflow surface including workstreams, tasks, audit, starter creation, GUI, and verification baseline lifecycle.

It also validates that repo-tracked low-token catalogs are current and that the context-index surfaces stay available through the MCP server.

## State Layout

- `registry.json`
- `runtime/dashboard.json`
- `starter-runs/<run-id>/run.json`
- `workspaces/<slug>--<hash>/workspace.json`
- `workspaces/<slug>--<hash>/workstreams/index.json`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/stage-register.yaml`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/active-stage-brief.md`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/artifacts/...`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/design/brief.json`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/design/current-board.json`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/design/boards/*.json`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/design/current-handoff.json`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/design/handoffs/*.json`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/design/cache/...`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/verification/recipes.json` when verification recipes have been defined
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/verification/runs/<run-id>/run.json`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/verification/runs/<run-id>/events.jsonl`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/verification/runs/<run-id>/stdout.log`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/verification/runs/<run-id>/stderr.log`
- `workspaces/<slug>--<hash>/workstreams/<workstream-id>/verification/baselines/status.json`
- `workspaces/<slug>--<hash>/tasks/index.json`
- `workspaces/<slug>--<hash>/tasks/<task-id>/task.json`
- `workspaces/<slug>--<hash>/tasks/<task-id>/task-brief.md`
- `workspaces/<slug>--<hash>/tasks/<task-id>/verification-summary.json`
- `workspaces/<slug>--<hash>/audits/*.json`
- `workspaces/<slug>--<hash>/upgrade-plans/*.json`

## Notes

- `stage-register.yaml` is intentionally a machine-owned JSON payload stored in a `.yaml` file for backward compatibility with existing external state paths.
- Root `stage-register.yaml` and root `active-stage-brief.md` are derived compatibility mirrors of the current workstream only when a current workstream exists. Canonical workstream files do not retain mirror markers.
- Completed stages are immutable. Unfinished stage definitions can change only after explicit user confirmation.
- `workspace.json` persists `local_dev_policy` and related host/toolchain capability state. Legacy `docker_policy` is removed during repair and is not part of the canonical workspace contract.
- `show git workflow advice` returns a canonical `GitWorkflowAdvice` object with branch, commit, ticket-prefix, PR, trailer, and safety policies. `inspect git state` and `plan git change` layer local branch and staging state on top of the same repo-first advice object.
- Project docs may still be updated inside the repo when runtime behavior, architecture, local development commands, or verification contracts change.
- The source repo can sync the plugin into `~/plugins/agentiux-dev`, and the installed copy stores `install-metadata.json` so the runtime can distinguish source and install roots.
