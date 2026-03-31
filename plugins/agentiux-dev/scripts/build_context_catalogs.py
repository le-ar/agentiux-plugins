#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from agentiux_dev_lib import PLUGIN_NAME, PLUGIN_VERSION, plugin_root
from agentiux_dev_mcp import TOOLS


CATALOG_SCHEMA_VERSION = 1
CATALOG_DIRNAME = "catalogs"
CATALOG_FILENAMES = ("skills", "mcp_tools", "scripts", "references", "intent_routes")
SUPPORTED_ROUTE_IDS = (
    "design",
    "git",
    "plugin-dev",
    "release",
    "youtrack",
    "verification",
    "workstream",
)

ROUTE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "git": {
        "title": "Git Workflow",
        "summary": "Inspect repository state, worktrees, commit conventions, and safe local git mutations.",
        "tags": ["branch", "commit", "git", "pr", "staging", "worktree"],
        "triggers": ["branch", "commit", "git", "pull request", "stage files", "worktree"],
        "recommended_skills": ["workspace-kernel", "git-ops"],
        "recommended_tools": [
            "inspect_git_state",
            "list_git_worktrees",
            "plan_git_change",
            "detect_commit_style",
            "suggest_branch_name",
            "suggest_commit_message",
            "suggest_pr_title",
            "suggest_pr_body",
        ],
        "summary_surfaces": ["inspect_git_state", "list_git_worktrees", "show_git_workflow_advice"],
        "primary_paths": [
            "skills/git-ops/SKILL.md",
            "references/command-surface.md",
            "README.md",
        ],
        "cost_hint": "low",
    },
    "verification": {
        "title": "Deterministic Verification",
        "summary": "Resolve semantic checks, helper bundle sync, verification plans, runs, baselines, and artifacts.",
        "tags": ["baseline", "helper bundle", "semantic", "verification", "visual"],
        "triggers": ["baseline", "playwright", "semantic", "verification", "visual"],
        "recommended_skills": ["workspace-kernel", "deterministic-verification", "stage-closeout"],
        "recommended_tools": [
            "resolve_verification",
            "show_host_support",
            "show_host_setup_plan",
            "show_verification_helper_catalog",
            "sync_verification_helpers",
            "audit_verification_coverage",
            "run_verification_case",
            "run_verification_suite",
            "list_verification_runs",
            "approve_verification_baseline",
            "update_verification_baseline",
        ],
        "summary_surfaces": [
            "get_workspace_detail",
            "show_verification_helper_catalog",
            "audit_verification_coverage",
        ],
        "primary_paths": [
            "skills/deterministic-verification/SKILL.md",
            "references/visual-verification.md",
            "README.md",
        ],
        "cost_hint": "medium",
    },
    "design": {
        "title": "Design Workflow",
        "summary": "Handle briefs, reference boards, handoffs, and verification hooks for design-driven work.",
        "tags": ["brief", "design", "handoff", "reference board", "ux", "visual"],
        "triggers": ["brief", "design", "handoff", "reference", "ux", "visual design"],
        "recommended_skills": ["workspace-kernel", "design-orchestrator", "web-product-designer", "expo-product-designer"],
        "recommended_tools": [
            "get_design_brief",
            "write_design_brief",
            "list_reference_boards",
            "get_reference_board",
            "write_reference_board",
            "list_design_handoffs",
            "get_design_handoff",
            "write_design_handoff",
            "cache_reference_preview",
        ],
        "summary_surfaces": ["get_workspace_detail", "list_reference_boards", "list_design_handoffs"],
        "primary_paths": [
            "skills/design-orchestrator/SKILL.md",
            "references/design-workflow.md",
            "README.md",
        ],
        "cost_hint": "medium",
    },
    "release": {
        "title": "Release Readiness",
        "summary": "Check self-host readiness, dashboard health, smoke coverage, and public-safe packaging.",
        "tags": ["dashboard", "installer", "release", "self-host", "smoke"],
        "triggers": ["dashboard", "install", "release", "smoke", "self-host"],
        "recommended_skills": ["plugin-platform", "docs-sync"],
        "recommended_tools": [
            "get_dashboard_snapshot",
            "get_plugin_stats",
            "show_host_support",
            "show_host_setup_plan",
            "install_host_requirements",
            "repair_host_requirements",
            "audit_repository",
        ],
        "summary_surfaces": ["get_dashboard_snapshot", "get_plugin_stats", "show_host_support"],
        "primary_paths": [
            "skills/plugin-platform/SKILL.md",
            "references/dashboard.md",
            "README.md",
        ],
        "cost_hint": "low",
    },
    "youtrack": {
        "title": "YouTrack Workflow",
        "summary": "Handle connection management, persisted issue search and triage sessions, plan drafts, and linked-task execution rules.",
        "tags": ["connection", "issue", "planning", "search", "triage", "youtrack"],
        "triggers": ["issue queue", "search tickets", "triage backlog", "youtrack", "youtrack plan", "youtrack search"],
        "recommended_skills": ["plugin-platform", "workspace-kernel"],
        "recommended_tools": [
            "show_youtrack_connections",
            "connect_youtrack",
            "update_youtrack_connection",
            "remove_youtrack_connection",
            "test_youtrack_connection",
            "search_youtrack_issues",
            "show_youtrack_issue_queue",
            "propose_youtrack_workstream_plan",
            "apply_youtrack_workstream_plan",
            "plan_git_change",
            "suggest_commit_message",
        ],
        "summary_surfaces": ["show_youtrack_connections", "show_youtrack_issue_queue", "get_workspace_detail"],
        "primary_paths": [
            "README.md",
            "references/command-surface.md",
            "references/dashboard.md",
        ],
        "cost_hint": "medium",
    },
    "workstream": {
        "title": "Workspace Kernel",
        "summary": "Route requests into workspace initialization, workstreams, tasks, stage plans, and audits.",
        "tags": ["brief", "stage", "task", "workflow", "workstream", "workspace"],
        "triggers": ["task", "workflow", "workstream", "workspace", "stage plan"],
        "recommended_skills": ["workspace-kernel", "stage-planning", "stage-execution", "stage-closeout"],
        "recommended_tools": [
            "preview_workspace_init",
            "init_workspace",
            "advise_workflow",
            "get_workspace_detail",
            "list_workstreams",
            "create_workstream",
            "list_tasks",
            "create_task",
            "get_stage_register",
            "write_stage_register",
            "audit_repository",
            "show_upgrade_plan",
        ],
        "summary_surfaces": ["get_workspace_detail", "get_dashboard_snapshot", "list_workspaces"],
        "primary_paths": [
            "skills/workspace-kernel/SKILL.md",
            "references/workflow-kernel.md",
            "references/command-surface.md",
        ],
        "cost_hint": "low",
    },
    "plugin-dev": {
        "title": "Plugin Development",
        "summary": "Orient on plugin source, MCP interfaces, scripts, catalogs, and self-host development surfaces.",
        "tags": ["catalog", "dashboard", "mcp", "plugin", "python", "self-host"],
        "triggers": ["catalog", "mcp", "plugin", "python scripts", "self-host"],
        "recommended_skills": ["workspace-kernel", "plugin-platform", "docs-sync"],
        "recommended_tools": [
            "get_plugin_stats",
            "get_dashboard_snapshot",
            "show_capability_catalog",
            "show_intent_route",
            "show_workspace_context_pack",
            "search_context_index",
            "refresh_context_index",
        ],
        "summary_surfaces": ["get_plugin_stats", "get_dashboard_snapshot", "show_capability_catalog"],
        "primary_paths": [
            "skills/plugin-platform/SKILL.md",
            "references/workflow-kernel.md",
            "README.md",
        ],
        "cost_hint": "low",
    },
}

SKILL_OVERRIDES: dict[str, dict[str, Any]] = {
    "workspace-kernel": {
        "tags": ["kernel", "routing", "workflow", "workspace"],
        "triggers": ["workflow advice", "workspace init", "workstream", "task routing"],
        "related_routes": ["workstream", "plugin-dev"],
    },
    "git-ops": {
        "tags": ["branch", "commit", "git", "worktree"],
        "triggers": ["commit message", "inspect git", "stage files", "worktree"],
        "related_routes": ["git"],
    },
    "deterministic-verification": {
        "tags": ["baseline", "semantic", "verification", "visual"],
        "triggers": ["baseline", "helper bundle", "semantic checks", "verification"],
        "related_routes": ["verification"],
    },
    "design-orchestrator": {
        "tags": ["brief", "design", "handoff", "reference board"],
        "triggers": ["design brief", "reference board", "handoff"],
        "related_routes": ["design"],
    },
    "web-product-designer": {
        "tags": ["design", "product", "ui", "web"],
        "triggers": ["landing page", "web design", "ui direction"],
        "related_routes": ["design"],
    },
    "expo-product-designer": {
        "tags": ["design", "expo", "mobile", "product"],
        "triggers": ["expo design", "mobile visual", "rn product design"],
        "related_routes": ["design"],
    },
    "plugin-platform": {
        "tags": ["dashboard", "mcp", "plugin", "release"],
        "triggers": ["dashboard", "mcp tool", "plugin runtime", "release readiness"],
        "related_routes": ["plugin-dev", "release", "youtrack"],
    },
    "docs-sync": {
        "tags": ["docs", "readme", "truth maintenance"],
        "triggers": ["docs update", "readme sync"],
        "related_routes": ["plugin-dev", "workstream"],
    },
    "stage-planning": {
        "tags": ["planning", "stage plan", "workstream"],
        "triggers": ["plan stages", "stage planning"],
        "related_routes": ["workstream"],
    },
    "stage-execution": {
        "tags": ["execution", "implementation", "stage"],
        "triggers": ["continue work", "execute stage"],
        "related_routes": ["workstream"],
    },
    "stage-closeout": {
        "tags": ["closeout", "verification", "stage"],
        "triggers": ["close stage", "closeout", "verification summary"],
        "related_routes": ["workstream", "verification"],
    },
}

REFERENCE_OVERRIDES: dict[str, dict[str, Any]] = {
    "workflow-kernel.md": {
        "tags": ["routing", "workflow", "workspace"],
        "triggers": ["intent disposition", "workflow kernel", "workspace rules"],
        "related_routes": ["workstream", "plugin-dev"],
    },
    "command-surface.md": {
        "tags": ["aliases", "commands", "surface"],
        "triggers": ["command phrase", "command surface", "tool routing"],
        "related_routes": ["workstream", "git", "verification", "plugin-dev", "youtrack"],
    },
    "visual-verification.md": {
        "tags": ["helper bundle", "semantic", "verification", "visual"],
        "triggers": ["helper bundle", "semantic assertions", "visual verification"],
        "related_routes": ["verification"],
    },
    "design-workflow.md": {
        "tags": ["brief", "design", "handoff", "reference board"],
        "triggers": ["design workflow", "reference board", "design handoff"],
        "related_routes": ["design"],
    },
    "dashboard.md": {
        "tags": ["dashboard", "gui", "monitoring"],
        "triggers": ["dashboard", "gui", "workspace overview"],
        "related_routes": ["plugin-dev", "release", "youtrack"],
    },
    "stack-profiles.md": {
        "tags": ["profiles", "stacks", "workspace detection"],
        "triggers": ["stack profile", "profile pack", "workspace detection"],
        "related_routes": ["workstream", "plugin-dev"],
    },
}

SCRIPT_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "agentiux_dev_state",
        "path": "scripts/agentiux_dev_state.py",
        "title": "Workspace State CLI",
        "summary": "Command-line entrypoint for workspace state, git helpers, verification, YouTrack flows, catalogs, and context indexing.",
        "tags": ["cli", "state", "workspace"],
        "triggers": ["agentiux_dev_state.py", "workspace cli", "state command"],
        "primary_surface": "cli",
        "follow_up_paths": ["references/command-surface.md", "README.md"],
        "cost_hint": "low",
        "related_routes": ["workstream", "verification", "git", "plugin-dev", "youtrack"],
        "safe_usage": "Safe for local inspection commands; write subcommands mutate only external state or local git after explicit confirmation.",
        "common_flags": ["--workspace", "--repo-root", "--workstream-id", "--task-id"],
        "produced_artifacts": ["external workspace state", "global context cache"],
    },
    {
        "id": "agentiux_dev_mcp",
        "path": "scripts/agentiux_dev_mcp.py",
        "title": "MCP Server",
        "summary": "Stdio MCP server exposing read and write tools for workspace state, git, verification, YouTrack flows, and context retrieval.",
        "tags": ["mcp", "server", "stdio"],
        "triggers": ["mcp", "tools/list", "tools/call"],
        "primary_surface": "mcp",
        "follow_up_paths": ["README.md", "references/command-surface.md"],
        "cost_hint": "low",
        "related_routes": ["plugin-dev", "workstream", "youtrack"],
        "safe_usage": "Run through the plugin manifest or local Python launcher; it serves JSON-RPC over stdio.",
        "common_flags": [],
        "produced_artifacts": [],
    },
    {
        "id": "agentiux_dev_verification",
        "path": "scripts/agentiux_dev_verification.py",
        "title": "Verification Runtime",
        "summary": "Verification runner, recipe resolution, semantic helper sync, log/event streaming, and baseline lifecycle logic.",
        "tags": ["baseline", "runner", "semantic", "verification"],
        "triggers": ["semantic checks", "verification runtime", "verification case"],
        "primary_surface": "verification-runtime",
        "follow_up_paths": ["references/visual-verification.md", "README.md"],
        "cost_hint": "medium",
        "related_routes": ["verification", "plugin-dev"],
        "safe_usage": "Use through CLI or MCP helpers; transient artifacts stay outside repositories.",
        "common_flags": ["--workspace", "--workstream-id", "--case-id", "--suite-id"],
        "produced_artifacts": ["verification runs", "events", "logs", "helper materialization metadata"],
    },
    {
        "id": "agentiux_dev_gui",
        "path": "scripts/agentiux_dev_gui.py",
        "title": "Dashboard Runtime",
        "summary": "Launches and stops the local-only dashboard backed by dashboard snapshot and workspace detail payloads.",
        "tags": ["dashboard", "gui", "http"],
        "triggers": ["dashboard", "launch gui", "show gui url"],
        "primary_surface": "dashboard",
        "follow_up_paths": ["references/dashboard.md", "README.md"],
        "cost_hint": "low",
        "related_routes": ["plugin-dev", "release", "youtrack"],
        "safe_usage": "Local-only UI runtime; dashboard mutations are limited to YouTrack integration-management flows.",
        "common_flags": ["launch", "stop", "status", "--workspace"],
        "produced_artifacts": ["runtime/dashboard.json"],
    },
    {
        "id": "agentiux_cli",
        "path": "scripts/agentiux.py",
        "title": "Public Shell CLI",
        "summary": "Installs and exposes a short `agentiux` command for launching or reusing the dashboard singleton from any directory.",
        "tags": ["cli", "dashboard", "launcher"],
        "triggers": ["agentiux web", "dashboard singleton", "global launcher"],
        "primary_surface": "shell-cli",
        "follow_up_paths": ["README.md", "references/dashboard.md"],
        "cost_hint": "low",
        "related_routes": ["plugin-dev", "release"],
        "safe_usage": "Matches initialized workspaces by path or slug and reuses the existing dashboard process instead of spawning duplicates.",
        "common_flags": ["web", "gui", "status", "stop", "url", "--json"],
        "produced_artifacts": ["runtime/dashboard.json", "global command launcher"],
    },
    {
        "id": "agentiux_dev_youtrack",
        "path": "scripts/agentiux_dev_youtrack.py",
        "title": "YouTrack Integration Runtime",
        "summary": "Workspace-scoped YouTrack connections, field catalogs, persisted search sessions, plan drafts, and issue-ledger helpers.",
        "tags": ["integration", "issues", "planning", "youtrack"],
        "triggers": ["issue triage", "search backlog", "youtrack", "youtrack planning"],
        "primary_surface": "integration-runtime",
        "follow_up_paths": ["references/command-surface.md", "references/dashboard.md", "README.md"],
        "cost_hint": "medium",
        "related_routes": ["plugin-dev", "workstream", "youtrack"],
        "safe_usage": "Reads YouTrack remotely and stores plugin-owned state locally; v1 does not write back to YouTrack.",
        "common_flags": [],
        "produced_artifacts": ["workspace integration state", "field catalogs", "search sessions", "plan drafts", "issue ledger"],
    },
    {
        "id": "release_readiness",
        "path": "scripts/release_readiness.py",
        "title": "Release Readiness Checks",
        "summary": "Runs audit, compile, self-host, MCP, dashboard, catalog, and smoke validations for the plugin.",
        "tags": ["readiness", "release", "smoke"],
        "triggers": ["release readiness", "ship plugin", "smoke checks"],
        "primary_surface": "release-check",
        "follow_up_paths": ["skills/plugin-platform/SKILL.md", "README.md"],
        "cost_hint": "medium",
        "related_routes": ["release", "plugin-dev"],
        "safe_usage": "Read-only for repo-tracked source; writes only to temp dirs and external state during checks.",
        "common_flags": ["run", "audit", "python-compile", "mcp-check", "--repo-root", "--smoke-runs"],
        "produced_artifacts": ["stdout JSON report", "temporary self-host fixtures"],
    },
    {
        "id": "smoke_test",
        "path": "scripts/smoke_test.py",
        "title": "Synthetic Smoke Test",
        "summary": "Synthetic integration coverage for workspace, verification, git, installer, dashboard, MCP, and context indexing flows.",
        "tags": ["integration", "regression", "smoke", "synthetic"],
        "triggers": ["smoke", "synthetic test", "plugin regression"],
        "primary_surface": "test",
        "follow_up_paths": ["scripts/release_readiness.py", "README.md"],
        "cost_hint": "high",
        "related_routes": ["release", "plugin-dev"],
        "safe_usage": "Runs in temp directories and isolated external state roots.",
        "common_flags": [],
        "produced_artifacts": ["temporary repos", "temporary external state"],
    },
    {
        "id": "install_home_local",
        "path": "scripts/install_home_local.py",
        "title": "Home-Local Installer",
        "summary": "Installs the plugin into a home-local plugins directory, updates marketplace metadata, and can place a global `agentiux` launcher into PATH.",
        "tags": ["install", "marketplace", "plugin"],
        "triggers": ["install plugin", "home-local install", "marketplace entry"],
        "primary_surface": "installer",
        "follow_up_paths": ["README.md", ".codex-plugin/plugin.json"],
        "cost_hint": "low",
        "related_routes": ["plugin-dev", "release"],
        "safe_usage": "Copies tracked plugin files to the install root, updates local marketplace metadata, and optionally writes a user-local command shim.",
        "common_flags": ["--source-plugin-root", "--install-root", "--marketplace-path", "--bin-dir", "--skip-global-command"],
        "produced_artifacts": ["installed plugin copy", "install-metadata.json", "marketplace.json", "agentiux launcher"],
    },
]


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _tokenize(value: str | None) -> list[str]:
    text = _normalize_text(value).lower()
    if not text:
        return []
    tokens = re.split(r"[^a-z0-9]+", text)
    return sorted({token for token in tokens if len(token) >= 2})


def _cost_hint_for_text(text: str) -> str:
    line_count = len(text.splitlines())
    if line_count <= 80:
        return "low"
    if line_count <= 220:
        return "medium"
    return "high"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    _, _, rest = text.partition("---\n")
    frontmatter, separator, body = rest.partition("\n---\n")
    if not separator:
        return {}, text
    payload: dict[str, str] = {}
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        payload[key.strip()] = value.strip().strip('"')
    return payload, body


def _markdown_heading_and_summary(text: str) -> tuple[str, str]:
    title = ""
    summary = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            continue
        if stripped and not stripped.startswith("#") and not stripped.startswith("- ") and not stripped.startswith("```") and not summary:
            summary = stripped
            break
    return title, summary


def _read_first_paths(text: str, base_dir: Path, root: Path) -> list[str]:
    lines = text.splitlines()
    in_section = False
    results: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "## Read First":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- `") and stripped.endswith("`"):
            raw_path = stripped[3:-1]
            resolved = (base_dir / raw_path).resolve()
            try:
                results.append(str(resolved.relative_to(root)))
            except ValueError:
                continue
    return sorted(dict.fromkeys(results))


def _route_ids_for_hint(*values: str) -> list[str]:
    tokens = set()
    for value in values:
        tokens.update(_tokenize(value))
    route_ids: set[str] = set()
    if tokens.intersection({"branch", "commit", "git", "pr", "staging", "worktree"}):
        route_ids.add("git")
    if tokens.intersection({"baseline", "detox", "playwright", "semantic", "verification", "visual"}):
        route_ids.add("verification")
    if tokens.intersection({"brief", "design", "handoff", "reference", "ux"}):
        route_ids.add("design")
    if tokens.intersection({"dashboard", "install", "mcp", "plugin", "release", "self", "smoke"}):
        route_ids.add("plugin-dev")
    if tokens.intersection({"dashboard", "release", "smoke"}):
        route_ids.add("release")
    if tokens.intersection({"backlog", "tickets", "triage", "youtrack"}):
        route_ids.add("youtrack")
    if tokens.intersection({"stage", "task", "workflow", "workstream", "workspace"}):
        route_ids.add("workstream")
    return sorted(route_ids)


def _follow_up_paths_for_routes(route_ids: list[str]) -> list[str]:
    results: list[str] = []
    for route_id in route_ids:
        route = ROUTE_DEFINITIONS[route_id]
        results.extend(route["primary_paths"])
    return sorted(dict.fromkeys(results))


def _tool_return_summary(name: str, read_only: bool) -> str:
    if name.startswith(("list_", "get_", "show_", "read_")) or read_only:
        return "Structured JSON payload for inspection and routing."
    if name.startswith(("suggest_", "detect_", "resolve_", "preview_", "plan_")):
        return "Structured recommendation or resolution payload."
    return "Structured mutation result payload."


def _tool_cost_hint(name: str, description: str) -> str:
    if any(token in name for token in ("dashboard", "workspace_detail", "verification_run")):
        return "medium"
    if any(token in description.lower() for token in ("full detail", "latest", "dashboard", "coverage")):
        return "medium"
    return "low"


def _entry_payload(
    *,
    entry_id: str,
    kind: str,
    title: str,
    summary: str,
    path: str,
    tags: list[str],
    triggers: list[str],
    primary_surface: str,
    follow_up_paths: list[str],
    cost_hint: str,
    related_routes: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "id": entry_id,
        "kind": kind,
        "title": title,
        "summary": summary,
        "path": path,
        "tags": sorted(dict.fromkeys(tags)),
        "triggers": sorted(dict.fromkeys(triggers)),
        "primary_surface": primary_surface,
        "follow_up_paths": sorted(dict.fromkeys(follow_up_paths)),
        "cost_hint": cost_hint,
        "related_routes": sorted(dict.fromkeys(route for route in related_routes if route in SUPPORTED_ROUTE_IDS)),
    }
    if extra:
        payload.update(extra)
    return payload


def _build_skill_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for skill_path in sorted((root / "skills").glob("*/SKILL.md")):
        text = _read_text(skill_path)
        frontmatter, body = _parse_frontmatter(text)
        title, summary_line = _markdown_heading_and_summary(body)
        skill_id = frontmatter.get("name") or skill_path.parent.name
        overrides = SKILL_OVERRIDES.get(skill_id, {})
        related_routes = overrides.get("related_routes") or _route_ids_for_hint(skill_id, frontmatter.get("description", ""), summary_line)
        entries.append(
            _entry_payload(
                entry_id=skill_id,
                kind="skill",
                title=title or skill_id.replace("-", " ").title(),
                summary=frontmatter.get("description") or summary_line,
                path=str(skill_path.relative_to(root)),
                tags=[skill_id, *skill_id.split("-"), *(overrides.get("tags") or [])],
                triggers=[skill_id.replace("-", " "), *(overrides.get("triggers") or [])],
                primary_surface="skill",
                follow_up_paths=_read_first_paths(body, skill_path.parent, root),
                cost_hint=_cost_hint_for_text(text),
                related_routes=related_routes,
            )
        )
    return entries


def _build_reference_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for reference_path in sorted((root / "references").glob("*.md")):
        text = _read_text(reference_path)
        title, summary = _markdown_heading_and_summary(text)
        overrides = REFERENCE_OVERRIDES.get(reference_path.name, {})
        related_routes = overrides.get("related_routes") or _route_ids_for_hint(reference_path.stem, title, summary)
        entries.append(
            _entry_payload(
                entry_id=reference_path.stem,
                kind="reference",
                title=title or reference_path.stem.replace("-", " ").title(),
                summary=summary,
                path=str(reference_path.relative_to(root)),
                tags=[reference_path.stem, *reference_path.stem.split("-"), *(overrides.get("tags") or [])],
                triggers=[reference_path.stem.replace("-", " "), *(overrides.get("triggers") or [])],
                primary_surface="reference",
                follow_up_paths=[],
                cost_hint=_cost_hint_for_text(text),
                related_routes=related_routes,
            )
        )
    return entries


def _build_script_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for script in SCRIPT_DEFINITIONS:
        entries.append(
            _entry_payload(
                entry_id=script["id"],
                kind="script",
                title=script["title"],
                summary=script["summary"],
                path=script["path"],
                tags=script["tags"],
                triggers=script["triggers"],
                primary_surface=script["primary_surface"],
                follow_up_paths=script["follow_up_paths"],
                cost_hint=script["cost_hint"],
                related_routes=script["related_routes"],
                extra={
                    "safe_usage": script["safe_usage"],
                    "common_flags": script["common_flags"],
                    "produced_artifacts": script["produced_artifacts"],
                },
            )
        )
    return entries


def _build_mcp_tool_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for tool_name, tool in sorted(TOOLS.items()):
        annotations = tool.get("annotations", {})
        description = _normalize_text(tool.get("description", ""))
        related_routes = _route_ids_for_hint(tool_name, description)
        follow_up_paths = _follow_up_paths_for_routes(related_routes)
        entries.append(
            _entry_payload(
                entry_id=tool_name,
                kind="mcp_tool",
                title=tool["title"],
                summary=description,
                path="scripts/agentiux_dev_mcp.py",
                tags=[tool_name, *tool_name.split("_"), *related_routes],
                triggers=[tool_name.replace("_", " "), *related_routes],
                primary_surface="mcp",
                follow_up_paths=follow_up_paths[:3],
                cost_hint=_tool_cost_hint(tool_name, description),
                related_routes=related_routes,
                extra={
                    "tool_name": tool_name,
                    "read_only": bool(annotations.get("readOnlyHint")),
                    "required_inputs": tool.get("inputSchema", {}).get("required", []),
                    "returns": _tool_return_summary(tool_name, bool(annotations.get("readOnlyHint"))),
                },
            )
        )
    return entries


def _build_intent_routes(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for route_id in SUPPORTED_ROUTE_IDS:
        route = ROUTE_DEFINITIONS[route_id]
        entries.append(
            {
                "route_id": route_id,
                "title": route["title"],
                "summary": route["summary"],
                "tags": sorted(route["tags"]),
                "triggers": sorted(route["triggers"]),
                "recommended_skills": sorted(route["recommended_skills"]),
                "recommended_tools": sorted(route["recommended_tools"]),
                "summary_surfaces": sorted(route["summary_surfaces"]),
                "primary_paths": sorted(route["primary_paths"])[:3],
                "cost_hint": route["cost_hint"],
            }
        )
    return entries


def _catalog_payload(name: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "plugin": {
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
        },
        "catalog": name,
        "entry_count": len(entries),
        "entries": entries,
    }


def generate_catalog_payloads(root: Path | None = None) -> dict[str, dict[str, Any]]:
    resolved_root = (root or plugin_root()).resolve()
    return {
        "skills": _catalog_payload("skills", _build_skill_entries(resolved_root)),
        "mcp_tools": _catalog_payload("mcp_tools", _build_mcp_tool_entries(resolved_root)),
        "scripts": _catalog_payload("scripts", _build_script_entries(resolved_root)),
        "references": _catalog_payload("references", _build_reference_entries(resolved_root)),
        "intent_routes": _catalog_payload("intent_routes", _build_intent_routes(resolved_root)),
    }


def _catalog_root(root: Path | None = None) -> Path:
    return (root or plugin_root()).resolve() / CATALOG_DIRNAME


def _validate_payloads(root: Path, payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    issues: list[str] = []
    route_ids = {entry["route_id"] for entry in payloads["intent_routes"]["entries"]}
    for catalog_name, payload in payloads.items():
        for entry in payload["entries"]:
            for path_field in ("path",):
                target = entry.get(path_field)
                if target and not (root / target).exists():
                    issues.append(f"{catalog_name}:{entry.get('id')}: missing path {target}")
            for path_field in ("follow_up_paths", "primary_paths"):
                for target in entry.get(path_field, []):
                    if not (root / target).exists():
                        issues.append(f"{catalog_name}:{entry.get('id')}: missing path {target}")
            for route_id in entry.get("related_routes", []):
                if route_id not in route_ids:
                    issues.append(f"{catalog_name}:{entry.get('id')}: unknown route {route_id}")

    tool_names = set(TOOLS.keys())
    catalog_tool_names = {entry["tool_name"] for entry in payloads["mcp_tools"]["entries"]}
    missing_tools = sorted(tool_names.difference(catalog_tool_names))
    extra_tools = sorted(catalog_tool_names.difference(tool_names))
    if missing_tools:
        issues.append(f"missing_mcp_tools:{','.join(missing_tools)}")
    if extra_tools:
        issues.append(f"unexpected_mcp_tools:{','.join(extra_tools)}")
    return {
        "valid": not issues,
        "issues": issues,
    }


def write_catalogs(root: Path | None = None) -> dict[str, Any]:
    resolved_root = (root or plugin_root()).resolve()
    catalog_root = _catalog_root(resolved_root)
    payloads = generate_catalog_payloads(resolved_root)
    validation = _validate_payloads(resolved_root, payloads)
    if not validation["valid"]:
        raise ValueError("\n".join(validation["issues"]))
    catalog_root.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        path = catalog_root / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "written",
        "catalog_root": str(catalog_root),
        "catalogs": sorted(f"{name}.json" for name in payloads),
        "entry_counts": {name: payload["entry_count"] for name, payload in payloads.items()},
    }


def check_catalogs(root: Path | None = None) -> dict[str, Any]:
    resolved_root = (root or plugin_root()).resolve()
    payloads = generate_catalog_payloads(resolved_root)
    validation = _validate_payloads(resolved_root, payloads)
    if not validation["valid"]:
        return {
            "status": "invalid",
            "catalog_root": str(_catalog_root(resolved_root)),
            "issues": validation["issues"],
        }

    mismatches: list[str] = []
    for name, payload in payloads.items():
        path = _catalog_root(resolved_root) / f"{name}.json"
        if not path.exists():
            mismatches.append(f"missing:{path}")
            continue
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            mismatches.append(f"stale:{path}")
    return {
        "status": "ok" if not mismatches else "stale",
        "catalog_root": str(_catalog_root(resolved_root)),
        "issues": mismatches,
        "entry_counts": {name: payload["entry_count"] for name, payload in payloads.items()},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and validate low-token context catalogs for AgentiUX Dev.")
    parser.add_argument("--plugin-root", default=str(plugin_root()))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--print", dest="print_payload", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.plugin_root).expanduser().resolve()
    if args.write:
        payload = write_catalogs(root)
    elif args.check:
        payload = check_catalogs(root)
        if payload["status"] != "ok":
            print(json.dumps(payload, indent=2))
            return 1
    else:
        payload = generate_catalog_payloads(root)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
