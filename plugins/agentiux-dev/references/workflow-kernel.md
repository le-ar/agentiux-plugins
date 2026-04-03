# Workflow Kernel

AgentiUX Dev combines the strongest workflow rules from the user's reference repositories into a home-local kernel.

## Intent Dispositions

- `answer_only`
- `plan_only`
- `workflow_change`
- `execute_stage`
- `closeout_only`

## Core Rules

- Codex replies in the user's language unless the user asks to switch languages.
- Stage 1 retrieval guarantees are Unicode-safe and mixed-script-safe around English canonical literals; localized semantic alias packs stay external to tracked source.
- If the workspace is not initialized, Codex proposes initialization at the beginning of the workflow instead of waiting for a dedicated command.
- Implementation starts only for explicit execution intent.
- Workstream execution requires a confirmed concrete stage plan; empty workstreams are planning containers only.
- Before the first implementation action, Codex must persist an external brief.
- Codex-specific workflow state stays outside the repo.
- If the user simply describes work, Codex must classify it and choose starter, workstream, task, or read-only audit mode automatically.
- Large feature work belongs to named workstreams with independent stage registers.
- Small targeted fixes belong to lightweight tasks by default.
- In initialized repositories, narrow fixes may auto-create or reuse the active point task through workflow advice.
- Greenfield requests should trigger starter recommendations before manual scaffolding commands are requested.
- Workflow advice may recommend the next state mutation, but it must not create workspace, workstream, or starter state without explicit confirmation.
- Verification coverage gaps should surface as warnings when recipes are incomplete; they are not automatic blockers by default.
- Design briefs, reference boards, handoffs, and cached previews stay outside the repo.
- Verification recipes, runs, progress events, baseline status, and logs stay outside the repo.
- Canonical visual baselines remain project-owned for reproducible CI checks.
- Approved decisions that change documented truth must update real project docs in the same work cycle.
- Closeout must update external stage docs and `stage-register.yaml`.
- Stage description changes require explicit user confirmation and cannot touch completed stages.
- Commit requests should inspect existing commit history or repo commit rules first so generated messages match local conventions.
- The GUI is read-only and must never mutate repo code or external state implicitly.

## Retrieval Ladder

- Codex should prefer existing cheap summaries before opening large tracked files.
- Resolve the intent route through the compact route catalog before reading long docs or Python entrypoints.
- Use the capability catalog to choose skills, MCP tools, scripts, and reference docs instead of scanning the repo blindly.
- Load the global workspace context pack and search the context index before broad `rg` or manual exploration.
- Use `show context structure` for compact module, symbol, doc-section, hotspot, or incremental-index drill-down before opening large files.
- Use `run analysis audit` only for explicit architecture, performance, or docs-style analysis when compact findings are more useful than raw matches.
- Project-derived context indexes, query-pack cache packets, semantic units, semantic sqlite state, and generated memory snapshots stay under `~/.agentiux/agentiux-dev/` roots and never inside repositories.
- Structural and semantic cheap surfaces stay compact: `workspace_summary`, dashboard snapshots, workspace context packs, and retrieval surfaces expose only `structure_summary`, `hotspot_summary`, and `semantic_summary` projections instead of full structural or semantic artifacts.
- Python AST and Markdown section parsing are always available. JS/TS parser depth is optional and must fall back cleanly to heuristic extraction when no local TypeScript backend exists.
- Files above the large-file threshold use bounded structural extraction and hotspot signals rather than full-body summary generation.
- Semantic recall is optional, symbolic hits remain first-class, and semantic shortlist expansion is allowed only on explicit `analysis` paths or `run analysis audit`.
