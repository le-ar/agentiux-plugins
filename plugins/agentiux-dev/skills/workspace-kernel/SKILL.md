---
name: workspace-kernel
description: Use when working in any repository through AgentiUX Dev. This skill owns workspace initialization prompts, command-surface routing, explicit intent classification, external state paths, and handoff to the stage and stack profile skills.
---

# Workspace Kernel

## Read First

- `../../references/workflow-kernel.md`
- `../../references/command-surface.md`
- `../../references/stack-profiles.md`
- `../../references/design-workflow.md`
- `../../references/dashboard.md`
- `../../README.md`

## Required Workflow

1. Treat AgentiUX Dev as home-local state only. Do not add `.codex`, stage files, or Codex-only docs into the repository.
2. At the beginning of a stage-aware session, inspect routing advice first:
   - `python3 ../../scripts/agentiux_dev_state.py workflow-advice --workspace <repo> --request-text "<user request>"`
3. Before reading large docs or Python entrypoints, walk the low-token retrieval ladder:
   - `python3 ../../scripts/agentiux_dev_state.py show-intent-route --request-text "<user request>"`
   - `python3 ../../scripts/agentiux_dev_state.py show-capability-catalog --route-id <route>`
   - `python3 ../../scripts/agentiux_dev_state.py show-workspace-context-pack --workspace <repo> --request-text "<user request>" --route-id <route>`
   - `python3 ../../scripts/agentiux_dev_state.py search-context-index --workspace <repo> --query-text "<focused query>"`
4. Before any stage-aware workflow, check whether the workspace is initialized:
   - `python3 ../../scripts/agentiux_dev_state.py preview-init --workspace <repo>`
5. If the workspace is not initialized, do not initialize it silently.
   - show detected stacks
   - show selected profiles
   - show absolute external state paths
   - ask whether to initialize
6. Initialize only after explicit confirmation:
   - `python3 ../../scripts/agentiux_dev_state.py init-workspace --workspace <repo>`
7. Classify the request into:
   - `answer_only`
   - `plan_only`
   - `workflow_change`
   - `execute_stage`
   - `closeout_only`
8. If the user only describes work, infer the right container automatically:
   - propose a workstream for large feature or greenfield work
   - auto-create or reuse a task for small targeted fixes in initialized repositories
   - propose a starter when the request is greenfield
9. Route large feature and project work to named workstreams:
   - `python3 ../../scripts/agentiux_dev_state.py current-workstream --workspace <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py list-workstreams --workspace <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py create-workstream --workspace <repo> --title "<title>"`
   - a newly created workstream is only a planning container until a concrete stage register is confirmed
10. Route point fixes and narrow corrections to lightweight tasks:
   - `python3 ../../scripts/agentiux_dev_state.py current-task --workspace <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py list-tasks --workspace <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py create-task --workspace <repo> --title "<title>" --objective "<objective>"`
   - `workflow-advice` may already have created or reused the current point task
11. Route stage-aware work inside the current workstream to:
   - `stage-planning`
   - `stage-execution`
   - `stage-closeout`
12. Route implementation detail work to the relevant stack skills after the kernel has locked the intent and current external state.
13. Route design discovery and handoff work to:
   - `design-orchestrator`
   - `web-product-designer`
   - `expo-product-designer`
14. Route local dashboard requests through:
   - `python3 ../../scripts/agentiux_dev_gui.py launch`
   - `python3 ../../scripts/agentiux_dev_gui.py stop`
   - `python3 ../../scripts/agentiux_dev_gui.py status`
15. Route existing repositories through:
   - `python3 ../../scripts/agentiux_dev_state.py audit-repository --workspace <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py show-upgrade-plan --workspace <repo>`
16. Route greenfield requests through:
   - `python3 ../../scripts/agentiux_dev_state.py starter-presets`
   - `python3 ../../scripts/agentiux_dev_state.py create-starter --preset-id <preset> --destination-root <dir> --project-name <name>`
   - starter creation uses the upstream CLI only and does not initialize AgentiUX Dev state automatically
17. Route commit requests through commit-style inspection before creating a message:
   - `python3 ../../scripts/agentiux_dev_state.py inspect-git-state --repo-root <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py plan-git-change --repo-root <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py detect-commit-style --repo-root <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py suggest-commit-message --repo-root <repo> --summary "<change summary>"`
   - `python3 ../../scripts/agentiux_dev_state.py create-git-branch --repo-root <repo> --branch-name <branch>`
   - `python3 ../../scripts/agentiux_dev_state.py stage-git-files --repo-root <repo> --file <path>`
   - `python3 ../../scripts/agentiux_dev_state.py create-git-commit --repo-root <repo> --message "<message>"`
18. Reply to the user in the language used by the user's latest message unless the user asks to switch.

## Canonical Phrases

- `initialize workspace`
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
- `approve verification baseline`
- `update verification baseline`
- `create workstream`
- `list workstreams`
- `switch workstream`
- `show current workstream`
- `close current workstream`
- `create task`
- `list tasks`
- `show current task`
- `close current task`
- `audit repository`
- `show upgrade plan`
- `apply upgrade plan`
- `create starter`
- `show starter presets`
- `show git state`
- `plan git change`
- `create git branch`
- `stage git files`
- `create git commit`

## Guardrails

- Do not create workspace state without confirmation.
- Do not jump straight to large docs or Python entrypoints when the low-token catalogs or context pack can answer the routing question first.
- Do not treat workflow advice as permission to auto-create workspace, workstream, or starter state.
- Do not treat automatic point-task routing as permission to create broader workstreams or stage plans.
- Do not wait for the user to guess that initialization, a starter, a workstream, or a task is needed. Propose the right route first.
- Do not treat ordinary repo questions, commits, reviews, explanations, or workflow edits as stage execution.
- Do not let Codex-only state become the source of truth for app behavior or project docs inside the repo.
- Do not keep design boards or handoffs only in chat when the design workflow is active.
- Do not let English-only skill text force the reply language away from the user's language.
