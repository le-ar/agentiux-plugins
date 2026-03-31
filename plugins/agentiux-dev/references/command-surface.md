# Command Surface

AgentiUX Dev exposes a small chat-first command surface.

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
- `audit verification coverage`
- `sync verification helpers`
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

## Behavior

- If the workspace is not initialized, the plugin must propose initialization before creating state.
- The initialization proposal should appear proactively when stage-aware work starts, even if the user did not explicitly ask for initialization.
- The initialization proposal must show detected stacks plus the absolute external paths it will create.
- The tracked source keeps canonical command phrases in English.
- Localized aliases are resolved at runtime only and must not move the tracked source away from English.
- If the user describes work without naming `create workstream` or `create task`, the kernel should infer the right mode first.
- `continue work` is an execution-intent trigger only after the workspace is initialized and either a current task exists or the current workstream has a confirmed stage plan.
- `propose stage plan changes` is a planning action. It must not mutate state.
- `apply stage plan changes` can mutate unfinished stage definitions only after explicit user confirmation.
- `launch gui` launches the read-only local dashboard through `scripts/agentiux_dev_gui.py`.
- `show gui url` returns the current dashboard URL without opening the browser automatically.
- `run verification case` starts one deterministic verification case.
- `run verification suite` starts a deterministic suite in stable case order.
- `show verification log` reads stdout, stderr, or Android logcat from the active or selected verification run in external state.
- `show verification helper catalog` returns the versioned plugin-owned helper bundle catalog plus sync status for the current workspace.
- `audit verification coverage` reports warning-level QA coverage gaps without mutating workspace state.
- `sync verification helpers` materializes the generated helper bundle into `.verification/helpers/` for local imports.
- `approve verification baseline` records approval for a project-owned baseline source path.
- `update verification baseline` copies a selected verification artifact into a project-owned baseline path.
- `create workstream` creates a named workstream container with its own external state and an empty stage register that still requires explicit stage-plan confirmation.
- `create task` creates a lightweight task for point fixes without requiring a full workstream.
- In initialized repositories, `workflow-advice` may auto-create or reuse the active point task for narrow fixes.
- Greenfield requests should trigger starter recommendations automatically instead of requiring `show starter presets` first.
- `audit repository` is read-only for repo code and produces a structured gap report.
- `apply upgrade plan` requires explicit confirmation before creating remediation workstreams and tasks.
- `create starter` uses the official upstream CLI only and does not initialize AgentiUX Dev state automatically.
- Commit requests should inspect commit history or config before a message is suggested or a commit is created.
- `show git state` and `plan git change` are read-only.
- `create git branch`, `stage git files`, and `create git commit` are local-only write actions and must not push or publish.
