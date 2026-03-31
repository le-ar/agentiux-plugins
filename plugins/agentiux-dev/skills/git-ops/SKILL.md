---
name: git-ops
description: Use when the user asks to commit changes, prepare a commit message, or inspect repository commit conventions. This skill reads local commit history or commitlint-style config first, then suggests or creates commit messages that fit the repo.
---

# Git Ops

## Read First

- `../../references/workflow-kernel.md`
- `../../README.md`

## Required Workflow

1. Treat commit requests as a separate workflow from stage execution.
2. Resolve the git route before opening larger docs or scripts:
   - `python3 ../../scripts/agentiux_dev_state.py show-intent-route --request-text "<user request>"`
   - `python3 ../../scripts/agentiux_dev_state.py show-capability-catalog --route-id git`
3. Inspect the repository commit style before writing a message:
   - `python3 ../../scripts/agentiux_dev_state.py detect-commit-style --repo-root <repo>`
4. Inspect current branch and staging state before proposing local git actions:
   - `python3 ../../scripts/agentiux_dev_state.py inspect-git-state --repo-root <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py list-git-worktrees --repo-root <repo>`
   - `python3 ../../scripts/agentiux_dev_state.py plan-git-change --repo-root <repo>`
5. If the user asks for a commit message or asks Codex to commit, derive a message from the actual change summary:
   - `python3 ../../scripts/agentiux_dev_state.py suggest-commit-message --repo-root <repo> --summary "<change summary>"`
6. Prefer matching existing repo history or explicit commitlint-style rules.
7. If the repository has no commits and no rules, fall back to a clear imperative message.
8. Use the local-only execution helpers only after explicit confirmation:
   - `python3 ../../scripts/agentiux_dev_state.py create-git-worktree --repo-root <repo> --path <path> --branch-name <branch>`
   - `python3 ../../scripts/agentiux_dev_state.py create-git-branch --repo-root <repo> --branch-name <branch>`
   - `python3 ../../scripts/agentiux_dev_state.py stage-git-files --repo-root <repo> --file <path>`
   - `python3 ../../scripts/agentiux_dev_state.py create-git-commit --repo-root <repo> --message "<message>"`

## Guardrails

- Do not invent a commit convention when the repo already has one.
- If the history is sparse, treat it as a weak signal and prefer a neutral fallback over pretending the repo has a strong convention.
- Prefer linked worktrees for parallel or long-running workstreams instead of overloading one dirty checkout.
- Do not create a commit unless the user explicitly asks for it.
- Do not push or publish from this workflow automatically.
- Do not treat commit requests as permission to start a new stage, task, or workstream.
