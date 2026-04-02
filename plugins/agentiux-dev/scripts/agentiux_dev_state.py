#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentiux_dev_analytics import (
    get_analytics_snapshot,
    list_learning_entries,
    update_learning_entry,
    write_learning_entry,
)
from agentiux_dev_auth import (
    get_auth_session,
    invalidate_auth_session,
    list_auth_sessions,
    remove_auth_profile,
    remove_auth_session,
    resolve_auth_profile,
    show_auth_profiles,
    write_auth_session,
    write_auth_profile,
)
from agentiux_dev_memory import (
    archive_project_note,
    get_project_note,
    list_project_notes,
    search_project_notes,
    write_project_note,
)
from agentiux_dev_verification import (
    audit_verification_coverage,
    approve_verification_baseline,
    cancel_verification_run,
    follow_verification_run,
    list_verification_runs,
    read_verification_events,
    read_verification_log_tail,
    read_verification_recipes,
    read_verification_run,
    resolve_verification_selection,
    show_verification_helper_catalog,
    sync_verification_helpers,
    start_verification_case,
    start_verification_suite,
    update_verification_baseline,
    wait_for_verification_run,
    write_verification_recipes,
)
from agentiux_dev_lib import (
    apply_upgrade_plan,
    audit_repository,
    cache_reference_preview,
    command_aliases,
    close_current_workstream,
    close_task,
    create_git_branch,
    create_git_commit,
    create_git_worktree,
    create_starter,
    create_task,
    create_workstream,
    dashboard_snapshot,
    detect_commit_style,
    detect_workspace,
    get_active_brief,
    get_state_paths,
    HOST_SETUP_REQUIREMENT_METADATA,
    init_workspace,
    inspect_git_state,
    list_git_worktrees,
    list_starter_presets,
    list_starter_runs,
    list_design_handoffs,
    list_reference_boards,
    list_stages,
    list_tasks,
    list_workstreams,
    list_workspaces,
    migrate_workspace_state,
    current_task,
    current_workstream,
    plugin_stats,
    plan_git_change,
    preview_repair_workspace_state,
    preview_workspace_init,
    read_current_audit,
    read_design_brief,
    read_design_handoff,
    read_reference_board,
    read_task,
    read_upgrade_plan,
    read_stage_register,
    read_workspace_state,
    read_workspace_detail,
    set_active_brief,
    show_git_workflow_advice,
    show_host_setup_plan,
    show_host_support,
    show_upgrade_plan,
    install_host_requirements,
    repair_host_requirements,
    stage_git_files,
    switch_task,
    suggest_branch_name,
    suggest_commit_message,
    suggest_pr_body,
    suggest_pr_title,
    switch_workstream,
    text_result,
    workflow_advice,
    write_design_brief,
    write_design_handoff,
    write_reference_board,
    write_stage_register,
    repair_workspace_state,
)
from agentiux_dev_context import (
    refresh_context_index,
    search_context_index,
    show_capability_catalog,
    show_intent_route,
    show_workspace_context_pack,
)
from agentiux_dev_youtrack import (
    apply_youtrack_workstream_plan,
    connect_youtrack,
    list_youtrack_connections,
    propose_youtrack_workstream_plan,
    remove_youtrack_connection,
    search_youtrack_issues,
    show_youtrack_issue_queue,
    test_youtrack_connection,
    update_youtrack_connection,
)


def _read_json(path: str) -> dict:
    with Path(path).open() as handle:
        return json.load(handle)


def _read_json_input(*, path: str | None = None, stdin: bool = False, label: str = "JSON payload") -> dict[str, object]:
    if stdin:
        raw = sys.stdin.read()
        if not raw.strip():
            raise ValueError(f"{label} stdin is empty")
        return json.loads(raw)
    if path:
        return _read_json(path)
    raise ValueError(f"{label} requires --stdin or a file path")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AgentiUX Dev workspace state CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_workspace_arg(command: argparse.ArgumentParser, required: bool = True) -> None:
        command.add_argument("--workspace", required=required, help="Workspace path")

    cmd = subparsers.add_parser("detect-workspace")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("preview-init")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("init-workspace")
    add_workspace_arg(cmd)
    cmd.add_argument("--force", action="store_true")

    cmd = subparsers.add_parser("preview-repair-workspace-state")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("repair-workspace-state")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("migrate-workspace-state")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("paths")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("workspace-state")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("show-host-support")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("show-host-setup-plan")
    add_workspace_arg(cmd)
    cmd.add_argument("--requirement-id", action="append", choices=list(HOST_SETUP_REQUIREMENT_METADATA))

    cmd = subparsers.add_parser("install-host-requirements")
    add_workspace_arg(cmd)
    cmd.add_argument("--requirement-id", action="append", choices=list(HOST_SETUP_REQUIREMENT_METADATA))
    cmd.add_argument("--confirmed", action="store_true")

    cmd = subparsers.add_parser("repair-host-requirements")
    add_workspace_arg(cmd)
    cmd.add_argument("--requirement-id", action="append", choices=list(HOST_SETUP_REQUIREMENT_METADATA))
    cmd.add_argument("--confirmed", action="store_true")

    cmd = subparsers.add_parser("stage-register")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("stages")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("get-brief")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("set-brief")
    add_workspace_arg(cmd)
    cmd.add_argument("--file", help="Markdown file to load")
    cmd.add_argument("--stdin", action="store_true", help="Read markdown from stdin")

    cmd = subparsers.add_parser("write-stage-register")
    add_workspace_arg(cmd)
    cmd.add_argument("--register-file", required=True, help="Path to JSON register file")
    cmd.add_argument("--confirmed-stage-plan-edit", action="store_true")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("list-workstreams")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("create-workstream")
    add_workspace_arg(cmd)
    cmd.add_argument("--title", required=True)
    cmd.add_argument("--kind", default="feature")
    cmd.add_argument("--branch-hint")
    cmd.add_argument("--scope-summary")
    cmd.add_argument("--workstream-id")
    cmd.add_argument("--no-make-current", action="store_true")

    cmd = subparsers.add_parser("switch-workstream")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id", required=True)

    cmd = subparsers.add_parser("current-workstream")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("close-current-workstream")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("list-tasks")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("create-task")
    add_workspace_arg(cmd)
    cmd.add_argument("--title", required=True)
    cmd.add_argument("--objective", required=True)
    cmd.add_argument("--scope", nargs="*")
    cmd.add_argument("--verification-target")
    cmd.add_argument("--verification-selectors-file")
    cmd.add_argument("--verification-mode-default", choices=["targeted", "full"])
    cmd.add_argument("--branch-hint")
    cmd.add_argument("--linked-workstream-id")
    cmd.add_argument("--stage-id")
    cmd.add_argument("--external-issue-file")
    cmd.add_argument("--codex-estimate-minutes", type=int)
    cmd.add_argument("--task-id")
    cmd.add_argument("--no-make-current", action="store_true")

    cmd = subparsers.add_parser("current-task")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("task")
    add_workspace_arg(cmd)
    cmd.add_argument("--task-id", required=True)

    cmd = subparsers.add_parser("switch-task")
    add_workspace_arg(cmd)
    cmd.add_argument("--task-id", required=True)

    cmd = subparsers.add_parser("close-task")
    add_workspace_arg(cmd)
    cmd.add_argument("--task-id")
    cmd.add_argument("--verification-summary-file")

    cmd = subparsers.add_parser("list-workspaces")

    cmd = subparsers.add_parser("workspace-detail")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("plugin-stats")

    cmd = subparsers.add_parser("dashboard-snapshot")
    add_workspace_arg(cmd, required=False)

    cmd = subparsers.add_parser("workflow-advice")
    add_workspace_arg(cmd)
    cmd.add_argument("--request-text")
    cmd.add_argument("--auto-create", action="store_true")

    cmd = subparsers.add_parser("audit-repository")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("current-audit")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("show-upgrade-plan")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("current-upgrade-plan")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("apply-upgrade-plan")
    add_workspace_arg(cmd)
    cmd.add_argument("--confirmed", action="store_true")

    cmd = subparsers.add_parser("design-brief")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("write-design-brief")
    add_workspace_arg(cmd)
    cmd.add_argument("--brief-file", required=True, help="Path to JSON brief file")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("reference-board")
    add_workspace_arg(cmd)
    cmd.add_argument("--board-id", default="current")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("write-reference-board")
    add_workspace_arg(cmd)
    cmd.add_argument("--board-file", required=True, help="Path to JSON board file")
    cmd.add_argument("--board-id", default="current")
    cmd.add_argument("--no-make-current", action="store_true")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("list-reference-boards")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("design-handoff")
    add_workspace_arg(cmd)
    cmd.add_argument("--handoff-id", default="current")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("write-design-handoff")
    add_workspace_arg(cmd)
    cmd.add_argument("--handoff-file", required=True, help="Path to JSON handoff file")
    cmd.add_argument("--handoff-id", default="current")
    cmd.add_argument("--no-make-current", action="store_true")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("list-design-handoffs")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("cache-reference-preview")
    add_workspace_arg(cmd)
    cmd.add_argument("--source-path", required=True)
    cmd.add_argument("--candidate-id")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("command-aliases")

    cmd = subparsers.add_parser("show-capability-catalog")
    cmd.add_argument("--kind", choices=["skill", "mcp_tool", "script", "reference"])
    cmd.add_argument("--route-id")
    cmd.add_argument("--query-text")
    cmd.add_argument("--limit", type=int)

    cmd = subparsers.add_parser("show-intent-route")
    cmd.add_argument("--route-id")
    cmd.add_argument("--request-text")

    cmd = subparsers.add_parser("verification-recipes")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("audit-verification-coverage")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("show-verification-helper-catalog")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("show-workspace-context-pack")
    add_workspace_arg(cmd)
    cmd.add_argument("--request-text")
    cmd.add_argument("--route-id")
    cmd.add_argument("--limit", type=int)
    cmd.add_argument("--force-refresh", action="store_true")

    cmd = subparsers.add_parser("search-context-index")
    add_workspace_arg(cmd)
    cmd.add_argument("--query-text", required=True)
    cmd.add_argument("--route-id")
    cmd.add_argument("--limit", type=int)

    cmd = subparsers.add_parser("refresh-context-index")
    add_workspace_arg(cmd)
    cmd.add_argument("--force", action="store_true")

    cmd = subparsers.add_parser("show-youtrack-connections")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("show-auth-profiles")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("write-auth-profile")
    add_workspace_arg(cmd)
    cmd.add_argument("--profile-file", help="Path to JSON auth profile payload")
    cmd.add_argument("--secret-file", help="Optional path to JSON secret payload")
    cmd.add_argument("--stdin", action="store_true", help="Read JSON payload from stdin")

    cmd = subparsers.add_parser("remove-auth-profile")
    add_workspace_arg(cmd)
    cmd.add_argument("--profile-id", required=True)

    cmd = subparsers.add_parser("resolve-auth-profile")
    add_workspace_arg(cmd)
    cmd.add_argument("--profile-id")
    cmd.add_argument("--task-id")
    cmd.add_argument("--external-issue-file")
    cmd.add_argument("--case-file")
    cmd.add_argument("--workstream-id")
    cmd.add_argument("--request-mode", choices=["read_only", "mutating"])
    cmd.add_argument("--action-tag", action="append", default=[])
    cmd.add_argument("--session-binding-file")
    cmd.add_argument("--context-overrides-file")
    cmd.add_argument("--prefer-cached", action=argparse.BooleanOptionalAction, default=True)
    cmd.add_argument("--force-refresh", action="store_true")

    cmd = subparsers.add_parser("list-auth-sessions")
    add_workspace_arg(cmd)
    cmd.add_argument("--profile-id")

    cmd = subparsers.add_parser("get-auth-session")
    add_workspace_arg(cmd)
    cmd.add_argument("--session-id", required=True)

    cmd = subparsers.add_parser("write-auth-session")
    add_workspace_arg(cmd)
    cmd.add_argument("--session-file", help="Path to JSON auth session payload")
    cmd.add_argument("--secret-file", help="Optional path to JSON session secret payload")
    cmd.add_argument("--stdin", action="store_true", help="Read JSON payload from stdin")

    cmd = subparsers.add_parser("invalidate-auth-session")
    add_workspace_arg(cmd)
    cmd.add_argument("--session-id", required=True)

    cmd = subparsers.add_parser("remove-auth-session")
    add_workspace_arg(cmd)
    cmd.add_argument("--session-id", required=True)

    cmd = subparsers.add_parser("list-project-notes")
    add_workspace_arg(cmd)
    cmd.add_argument("--status", choices=["active", "archived"])

    cmd = subparsers.add_parser("get-project-note")
    add_workspace_arg(cmd)
    cmd.add_argument("--note-id", required=True)

    cmd = subparsers.add_parser("write-project-note")
    add_workspace_arg(cmd)
    cmd.add_argument("--note-file", help="Path to JSON note payload")
    cmd.add_argument("--stdin", action="store_true", help="Read JSON payload from stdin")

    cmd = subparsers.add_parser("archive-project-note")
    add_workspace_arg(cmd)
    cmd.add_argument("--note-id", required=True)

    cmd = subparsers.add_parser("search-project-notes")
    add_workspace_arg(cmd)
    cmd.add_argument("--query-text", required=True)
    cmd.add_argument("--limit", type=int, default=8)

    cmd = subparsers.add_parser("analytics-snapshot")
    add_workspace_arg(cmd, required=False)

    cmd = subparsers.add_parser("list-learning-entries")
    add_workspace_arg(cmd, required=False)
    cmd.add_argument("--status", choices=["open", "resolved", "archived"])
    cmd.add_argument("--limit", type=int, default=50)

    cmd = subparsers.add_parser("write-learning-entry")
    add_workspace_arg(cmd, required=False)
    cmd.add_argument("--entry-file", help="Path to JSON learning entry payload")
    cmd.add_argument("--stdin", action="store_true", help="Read JSON payload from stdin")

    cmd = subparsers.add_parser("update-learning-entry")
    add_workspace_arg(cmd, required=False)
    cmd.add_argument("--entry-id", required=True)
    cmd.add_argument("--updates-file", help="Path to JSON update payload")
    cmd.add_argument("--stdin", action="store_true", help="Read JSON payload from stdin")

    cmd = subparsers.add_parser("connect-youtrack")
    add_workspace_arg(cmd)
    cmd.add_argument("--base-url", required=True)
    cmd.add_argument("--token", required=True)
    cmd.add_argument("--label")
    cmd.add_argument("--connection-id")
    cmd.add_argument("--project-scope")
    cmd.add_argument("--default", action="store_true")
    cmd.add_argument("--no-test", action="store_true")

    cmd = subparsers.add_parser("update-youtrack-connection")
    add_workspace_arg(cmd)
    cmd.add_argument("--connection-id", required=True)
    cmd.add_argument("--base-url")
    cmd.add_argument("--token")
    cmd.add_argument("--label")
    cmd.add_argument("--project-scope")
    cmd.add_argument("--default", action="store_true")
    cmd.add_argument("--no-test", action="store_true")

    cmd = subparsers.add_parser("remove-youtrack-connection")
    add_workspace_arg(cmd)
    cmd.add_argument("--connection-id", required=True)

    cmd = subparsers.add_parser("test-youtrack-connection")
    add_workspace_arg(cmd)
    cmd.add_argument("--connection-id", required=True)

    cmd = subparsers.add_parser("search-youtrack-issues")
    add_workspace_arg(cmd)
    cmd.add_argument("--query-text", required=True)
    cmd.add_argument("--connection-id")
    cmd.add_argument("--page-size", type=int, default=25)
    cmd.add_argument("--skip", type=int, default=0)
    cmd.add_argument("--shortlist-size", type=int, default=8)

    cmd = subparsers.add_parser("show-youtrack-issue-queue")
    add_workspace_arg(cmd)
    cmd.add_argument("--search-session-id")

    cmd = subparsers.add_parser("propose-youtrack-workstream-plan")
    add_workspace_arg(cmd)
    cmd.add_argument("--search-session-id")
    cmd.add_argument("--selected-issue-id", dest="selected_issue_ids", action="append")
    cmd.add_argument("--rejected-issue-id", dest="rejected_issue_ids", action="append")
    cmd.add_argument("--workstream-title")

    cmd = subparsers.add_parser("apply-youtrack-workstream-plan")
    add_workspace_arg(cmd)
    cmd.add_argument("--plan-id")
    cmd.add_argument("--confirmed", action="store_true")
    cmd.add_argument("--activate-first-task", action="store_true")
    cmd.add_argument("--reuse-current-workstream", action="store_true")

    cmd = subparsers.add_parser("sync-verification-helpers")
    add_workspace_arg(cmd)
    cmd.add_argument("--force", action="store_true")

    cmd = subparsers.add_parser("resolve-verification")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")
    cmd.add_argument("--changed-path", dest="changed_paths", action="append")
    cmd.add_argument("--confirm-heuristics", action="store_true")

    cmd = subparsers.add_parser("write-verification-recipes")
    add_workspace_arg(cmd)
    cmd.add_argument("--recipe-file", required=True, help="Path to JSON recipe file")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("approve-verification-baseline")
    add_workspace_arg(cmd)
    cmd.add_argument("--case-id", required=True)
    cmd.add_argument("--run-id")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("update-verification-baseline")
    add_workspace_arg(cmd)
    cmd.add_argument("--case-id", required=True)
    cmd.add_argument("--artifact-path")
    cmd.add_argument("--run-id")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("run-verification-case")
    add_workspace_arg(cmd)
    cmd.add_argument("--case-id", required=True)
    cmd.add_argument("--wait", action="store_true")
    cmd.add_argument("--follow", action="store_true")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("run-verification-suite")
    add_workspace_arg(cmd)
    cmd.add_argument("--suite-id", required=True)
    cmd.add_argument("--wait", action="store_true")
    cmd.add_argument("--follow", action="store_true")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("verification-runs")
    add_workspace_arg(cmd)
    cmd.add_argument("--limit", type=int)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("verification-run")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("verification-events")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)
    cmd.add_argument("--limit", type=int, default=50)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("verification-log")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)
    cmd.add_argument("--stream", choices=["stdout", "stderr", "logcat"], default="stdout")
    cmd.add_argument("--lines", type=int, default=50)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("wait-verification-run")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)
    cmd.add_argument("--timeout-seconds", type=float, default=60.0)
    cmd.add_argument("--follow", action="store_true")
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("cancel-verification-run")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("starter-presets")

    cmd = subparsers.add_parser("create-starter")
    cmd.add_argument("--preset-id", required=True)
    cmd.add_argument("--destination-root", required=True)
    cmd.add_argument("--project-name", required=True)
    cmd.add_argument("--force", action="store_true")

    cmd = subparsers.add_parser("starter-runs")
    cmd.add_argument("--limit", type=int)

    cmd = subparsers.add_parser("detect-commit-style")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--limit", type=int, default=30)

    cmd = subparsers.add_parser("suggest-commit-message")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--summary", required=True)
    cmd.add_argument("--file", dest="files", action="append")

    cmd = subparsers.add_parser("suggest-branch-name")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--summary", required=True)
    cmd.add_argument("--mode", choices=["task", "workstream"], default="task")

    cmd = subparsers.add_parser("suggest-pr-title")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--summary", required=True)
    cmd.add_argument("--file", dest="files", action="append")

    cmd = subparsers.add_parser("suggest-pr-body")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--summary", required=True)
    cmd.add_argument("--file", dest="files", action="append")

    cmd = subparsers.add_parser("show-git-workflow-advice")
    cmd.add_argument("--repo-root", required=True)

    cmd = subparsers.add_parser("inspect-git-state")
    cmd.add_argument("--repo-root", required=True)

    cmd = subparsers.add_parser("list-git-worktrees")
    cmd.add_argument("--repo-root", required=True)

    cmd = subparsers.add_parser("plan-git-change")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--summary")
    cmd.add_argument("--file", dest="files", action="append")

    cmd = subparsers.add_parser("create-git-worktree")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--path", required=True)
    cmd.add_argument("--branch-name", required=True)
    cmd.add_argument("--start-point", default="HEAD")

    cmd = subparsers.add_parser("create-git-branch")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--branch-name", required=True)

    cmd = subparsers.add_parser("stage-git-files")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--file", dest="files", action="append", required=True)

    cmd = subparsers.add_parser("create-git-commit")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--message", required=True)
    cmd.add_argument("--body")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "detect-workspace":
            payload = detect_workspace(args.workspace)
        elif args.command == "preview-init":
            payload = preview_workspace_init(args.workspace)
        elif args.command == "init-workspace":
            payload = init_workspace(args.workspace, force=args.force)
        elif args.command == "preview-repair-workspace-state":
            payload = preview_repair_workspace_state(args.workspace)
        elif args.command == "repair-workspace-state":
            payload = repair_workspace_state(args.workspace)
        elif args.command == "migrate-workspace-state":
            payload = migrate_workspace_state(args.workspace)
        elif args.command == "paths":
            payload = get_state_paths(args.workspace)
        elif args.command == "workspace-state":
            payload = read_workspace_state(args.workspace)
        elif args.command == "show-host-support":
            payload = show_host_support(args.workspace)
        elif args.command == "show-host-setup-plan":
            payload = show_host_setup_plan(args.workspace, requirement_ids=args.requirement_id)
        elif args.command == "install-host-requirements":
            payload = install_host_requirements(args.workspace, requirement_ids=args.requirement_id, confirmed=args.confirmed)
        elif args.command == "repair-host-requirements":
            payload = repair_host_requirements(args.workspace, requirement_ids=args.requirement_id, confirmed=args.confirmed)
        elif args.command == "stage-register":
            payload = read_stage_register(args.workspace, workstream_id=args.workstream_id)
        elif args.command == "stages":
            payload = list_stages(args.workspace, workstream_id=args.workstream_id)
        elif args.command == "get-brief":
            payload = get_active_brief(args.workspace)
        elif args.command == "set-brief":
            if args.stdin:
                markdown = sys.stdin.read()
            elif args.file:
                markdown = Path(args.file).read_text()
            else:
                raise ValueError("set-brief requires --file or --stdin")
            payload = set_active_brief(args.workspace, markdown)
        elif args.command == "write-stage-register":
            payload = write_stage_register(
                args.workspace,
                _read_json(args.register_file),
                confirmed_stage_plan_edit=args.confirmed_stage_plan_edit,
                workstream_id=args.workstream_id,
            )
        elif args.command == "list-workstreams":
            payload = list_workstreams(args.workspace)
        elif args.command == "create-workstream":
            payload = create_workstream(
                args.workspace,
                title=args.title,
                kind=args.kind,
                branch_hint=args.branch_hint,
                scope_summary=args.scope_summary,
                workstream_id=args.workstream_id,
                make_current=not args.no_make_current,
            )
        elif args.command == "switch-workstream":
            payload = switch_workstream(args.workspace, args.workstream_id)
        elif args.command == "current-workstream":
            payload = current_workstream(args.workspace)
        elif args.command == "close-current-workstream":
            payload = close_current_workstream(args.workspace)
        elif args.command == "list-tasks":
            payload = list_tasks(args.workspace)
        elif args.command == "create-task":
            payload = create_task(
                args.workspace,
                title=args.title,
                objective=args.objective,
                scope=args.scope,
                verification_target=args.verification_target,
                verification_selectors=_read_json(args.verification_selectors_file) if args.verification_selectors_file else None,
                verification_mode_default=args.verification_mode_default,
                branch_hint=args.branch_hint,
                linked_workstream_id=args.linked_workstream_id,
                stage_id=args.stage_id,
                external_issue=_read_json(args.external_issue_file) if args.external_issue_file else None,
                codex_estimate_minutes=args.codex_estimate_minutes,
                task_id=args.task_id,
                make_current=not args.no_make_current,
            )
        elif args.command == "current-task":
            payload = current_task(args.workspace)
        elif args.command == "task":
            payload = read_task(args.workspace, task_id=args.task_id)
        elif args.command == "switch-task":
            payload = switch_task(args.workspace, args.task_id)
        elif args.command == "close-task":
            summary = _read_json(args.verification_summary_file) if args.verification_summary_file else None
            payload = close_task(args.workspace, task_id=args.task_id, verification_summary=summary)
        elif args.command == "list-workspaces":
            payload = list_workspaces()
        elif args.command == "workspace-detail":
            payload = read_workspace_detail(args.workspace)
        elif args.command == "plugin-stats":
            payload = plugin_stats()
        elif args.command == "dashboard-snapshot":
            payload = dashboard_snapshot(args.workspace)
        elif args.command == "workflow-advice":
            payload = workflow_advice(args.workspace, request_text=args.request_text, auto_create=args.auto_create)
        elif args.command == "audit-repository":
            payload = audit_repository(args.workspace)
        elif args.command == "current-audit":
            payload = read_current_audit(args.workspace)
        elif args.command == "show-upgrade-plan":
            payload = show_upgrade_plan(args.workspace)
        elif args.command == "current-upgrade-plan":
            payload = read_upgrade_plan(args.workspace)
        elif args.command == "apply-upgrade-plan":
            payload = apply_upgrade_plan(args.workspace, confirmed=args.confirmed)
        elif args.command == "design-brief":
            payload = read_design_brief(args.workspace, workstream_id=args.workstream_id)
        elif args.command == "write-design-brief":
            payload = write_design_brief(args.workspace, _read_json(args.brief_file), workstream_id=args.workstream_id)
        elif args.command == "reference-board":
            payload = read_reference_board(args.workspace, board_id=args.board_id, workstream_id=args.workstream_id)
        elif args.command == "write-reference-board":
            payload = write_reference_board(
                args.workspace,
                _read_json(args.board_file),
                board_id=args.board_id,
                make_current=not args.no_make_current,
                workstream_id=args.workstream_id,
            )
        elif args.command == "list-reference-boards":
            payload = list_reference_boards(args.workspace, workstream_id=args.workstream_id)
        elif args.command == "design-handoff":
            payload = read_design_handoff(args.workspace, handoff_id=args.handoff_id, workstream_id=args.workstream_id)
        elif args.command == "write-design-handoff":
            payload = write_design_handoff(
                args.workspace,
                _read_json(args.handoff_file),
                handoff_id=args.handoff_id,
                make_current=not args.no_make_current,
                workstream_id=args.workstream_id,
            )
        elif args.command == "list-design-handoffs":
            payload = list_design_handoffs(args.workspace, workstream_id=args.workstream_id)
        elif args.command == "cache-reference-preview":
            payload = cache_reference_preview(args.workspace, args.source_path, args.candidate_id, workstream_id=args.workstream_id)
        elif args.command == "command-aliases":
            payload = {"command_aliases": command_aliases()}
        elif args.command == "show-capability-catalog":
            payload = show_capability_catalog(kind=args.kind, route_id=args.route_id, query_text=args.query_text, limit=args.limit)
        elif args.command == "show-intent-route":
            payload = show_intent_route(route_id=args.route_id, request_text=args.request_text)
        elif args.command == "verification-recipes":
            payload = read_verification_recipes(args.workspace, workstream_id=args.workstream_id)
        elif args.command == "audit-verification-coverage":
            payload = audit_verification_coverage(args.workspace, workstream_id=args.workstream_id)
        elif args.command == "show-verification-helper-catalog":
            payload = show_verification_helper_catalog(args.workspace)
        elif args.command == "show-workspace-context-pack":
            payload = show_workspace_context_pack(
                args.workspace,
                request_text=args.request_text,
                route_id=args.route_id,
                limit=args.limit,
                force_refresh=args.force_refresh,
            )
        elif args.command == "search-context-index":
            payload = search_context_index(args.workspace, args.query_text, route_id=args.route_id, limit=args.limit)
        elif args.command == "refresh-context-index":
            payload = refresh_context_index(args.workspace, force=args.force)
        elif args.command == "show-youtrack-connections":
            payload = list_youtrack_connections(args.workspace)
        elif args.command == "show-auth-profiles":
            payload = show_auth_profiles(args.workspace)
        elif args.command == "write-auth-profile":
            incoming = _read_json_input(path=args.profile_file, stdin=args.stdin, label="auth profile payload")
            profile = incoming.get("profile") if isinstance(incoming.get("profile"), dict) else incoming
            secret_payload = incoming.get("secret_payload") if isinstance(incoming.get("secret_payload"), dict) else None
            if secret_payload is None and args.secret_file:
                secret_payload = _read_json(args.secret_file)
            payload = write_auth_profile(args.workspace, profile, secret_payload=secret_payload)
        elif args.command == "remove-auth-profile":
            payload = remove_auth_profile(args.workspace, args.profile_id)
        elif args.command == "resolve-auth-profile":
            payload = resolve_auth_profile(
                args.workspace,
                profile_id=args.profile_id,
                task_id=args.task_id,
                external_issue=_read_json(args.external_issue_file) if args.external_issue_file else None,
                case=_read_json(args.case_file) if args.case_file else None,
                workstream_id=args.workstream_id,
                request_mode=args.request_mode,
                action_tags=args.action_tag,
                session_binding=_read_json(args.session_binding_file) if args.session_binding_file else None,
                context_overrides=_read_json(args.context_overrides_file) if args.context_overrides_file else None,
                prefer_cached=args.prefer_cached,
                force_refresh=args.force_refresh,
                surface_mode="cli",
            )
        elif args.command == "list-auth-sessions":
            payload = list_auth_sessions(args.workspace, profile_id=args.profile_id)
        elif args.command == "get-auth-session":
            payload = get_auth_session(args.workspace, args.session_id)
        elif args.command == "write-auth-session":
            incoming = _read_json_input(path=args.session_file, stdin=args.stdin, label="auth session payload")
            session = incoming.get("session") if isinstance(incoming.get("session"), dict) else incoming
            secret_payload = incoming.get("secret_payload")
            if secret_payload is None and args.secret_file:
                secret_payload = _read_json(args.secret_file)
            payload = write_auth_session(args.workspace, session, secret_payload=secret_payload)
        elif args.command == "invalidate-auth-session":
            payload = invalidate_auth_session(args.workspace, args.session_id)
        elif args.command == "remove-auth-session":
            payload = remove_auth_session(args.workspace, args.session_id)
        elif args.command == "list-project-notes":
            payload = list_project_notes(args.workspace, status=args.status)
        elif args.command == "get-project-note":
            payload = get_project_note(args.workspace, args.note_id)
        elif args.command == "write-project-note":
            incoming = _read_json_input(path=args.note_file, stdin=args.stdin, label="project note payload")
            note = incoming.get("note") if isinstance(incoming.get("note"), dict) else incoming
            payload = write_project_note(args.workspace, note)
        elif args.command == "archive-project-note":
            payload = archive_project_note(args.workspace, args.note_id)
        elif args.command == "search-project-notes":
            payload = search_project_notes(args.workspace, args.query_text, limit=args.limit)
        elif args.command == "analytics-snapshot":
            payload = get_analytics_snapshot(args.workspace)
        elif args.command == "list-learning-entries":
            payload = list_learning_entries(workspace=args.workspace, status=args.status, limit=args.limit)
        elif args.command == "write-learning-entry":
            incoming = _read_json_input(path=args.entry_file, stdin=args.stdin, label="learning entry payload")
            entry = incoming.get("entry") if isinstance(incoming.get("entry"), dict) else incoming
            payload = write_learning_entry(args.workspace, entry)
        elif args.command == "update-learning-entry":
            updates = _read_json_input(path=args.updates_file, stdin=args.stdin, label="learning entry update payload")
            if isinstance(updates.get("updates"), dict):
                updates = updates["updates"]
            payload = update_learning_entry(args.workspace, args.entry_id, updates)
        elif args.command == "connect-youtrack":
            payload = connect_youtrack(
                args.workspace,
                base_url=args.base_url,
                token=args.token,
                label=args.label,
                connection_id=args.connection_id,
                project_scope=args.project_scope,
                default=args.default,
                test_connection=not args.no_test,
            )
        elif args.command == "update-youtrack-connection":
            payload = update_youtrack_connection(
                args.workspace,
                args.connection_id,
                base_url=args.base_url,
                token=args.token,
                label=args.label,
                project_scope=args.project_scope,
                default=True if args.default else None,
                test_connection=not args.no_test,
            )
        elif args.command == "remove-youtrack-connection":
            payload = remove_youtrack_connection(args.workspace, args.connection_id)
        elif args.command == "test-youtrack-connection":
            payload = test_youtrack_connection(args.workspace, args.connection_id)
        elif args.command == "search-youtrack-issues":
            payload = search_youtrack_issues(
                args.workspace,
                query_text=args.query_text,
                connection_id=args.connection_id,
                page_size=args.page_size,
                skip=args.skip,
                shortlist_size=args.shortlist_size,
            )
        elif args.command == "show-youtrack-issue-queue":
            payload = show_youtrack_issue_queue(args.workspace, search_session_id=args.search_session_id)
        elif args.command == "propose-youtrack-workstream-plan":
            payload = propose_youtrack_workstream_plan(
                args.workspace,
                search_session_id=args.search_session_id,
                selected_issue_ids=args.selected_issue_ids,
                rejected_issue_ids=args.rejected_issue_ids,
                workstream_title=args.workstream_title,
            )
        elif args.command == "apply-youtrack-workstream-plan":
            payload = apply_youtrack_workstream_plan(
                args.workspace,
                plan_id=args.plan_id,
                confirmed=args.confirmed,
                activate_first_task=args.activate_first_task,
                reuse_current_workstream=args.reuse_current_workstream,
            )
        elif args.command == "sync-verification-helpers":
            payload = sync_verification_helpers(args.workspace, force=args.force)
        elif args.command == "resolve-verification":
            payload = resolve_verification_selection(
                args.workspace,
                workstream_id=args.workstream_id,
                changed_paths=args.changed_paths,
                confirm_heuristics=args.confirm_heuristics,
            )
        elif args.command == "write-verification-recipes":
            payload = write_verification_recipes(args.workspace, _read_json(args.recipe_file), workstream_id=args.workstream_id)
        elif args.command == "approve-verification-baseline":
            payload = approve_verification_baseline(args.workspace, args.case_id, run_id=args.run_id, workstream_id=args.workstream_id)
        elif args.command == "update-verification-baseline":
            payload = update_verification_baseline(
                args.workspace,
                args.case_id,
                artifact_path=args.artifact_path,
                run_id=args.run_id,
                workstream_id=args.workstream_id,
            )
        elif args.command == "run-verification-case":
            if args.follow:
                run = start_verification_case(args.workspace, args.case_id, wait=False, workstream_id=args.workstream_id)
                payload = follow_verification_run(args.workspace, run["run_id"], workstream_id=args.workstream_id, emit=lambda line: print(line, flush=True))
            else:
                payload = start_verification_case(args.workspace, args.case_id, wait=args.wait, workstream_id=args.workstream_id)
        elif args.command == "run-verification-suite":
            if args.follow:
                run = start_verification_suite(args.workspace, args.suite_id, wait=False, workstream_id=args.workstream_id)
                payload = follow_verification_run(args.workspace, run["run_id"], workstream_id=args.workstream_id, emit=lambda line: print(line, flush=True))
            else:
                payload = start_verification_suite(args.workspace, args.suite_id, wait=args.wait, workstream_id=args.workstream_id)
        elif args.command == "verification-runs":
            payload = list_verification_runs(args.workspace, limit=args.limit, workstream_id=args.workstream_id)
        elif args.command == "verification-run":
            payload = read_verification_run(args.workspace, args.run_id, workstream_id=args.workstream_id)
        elif args.command == "verification-events":
            payload = read_verification_events(args.workspace, args.run_id, limit=args.limit, workstream_id=args.workstream_id)
        elif args.command == "verification-log":
            payload = read_verification_log_tail(args.workspace, args.run_id, stream=args.stream, lines=args.lines, workstream_id=args.workstream_id)
        elif args.command == "wait-verification-run":
            if args.follow:
                payload = follow_verification_run(
                    args.workspace,
                    args.run_id,
                    timeout_seconds=args.timeout_seconds,
                    workstream_id=args.workstream_id,
                    emit=lambda line: print(line, flush=True),
                )
            else:
                payload = wait_for_verification_run(args.workspace, args.run_id, timeout_seconds=args.timeout_seconds, workstream_id=args.workstream_id)
        elif args.command == "cancel-verification-run":
            payload = cancel_verification_run(args.workspace, args.run_id, workstream_id=args.workstream_id)
        elif args.command == "starter-presets":
            payload = list_starter_presets()
        elif args.command == "create-starter":
            payload = create_starter(args.preset_id, args.destination_root, args.project_name, force=args.force)
        elif args.command == "starter-runs":
            payload = list_starter_runs(limit=args.limit)
        elif args.command == "detect-commit-style":
            payload = detect_commit_style(args.repo_root, limit=args.limit)
        elif args.command == "suggest-commit-message":
            payload = suggest_commit_message(args.repo_root, args.summary, files=args.files)
        elif args.command == "suggest-branch-name":
            payload = suggest_branch_name(args.repo_root, args.summary, mode=args.mode)
        elif args.command == "suggest-pr-title":
            payload = suggest_pr_title(args.repo_root, args.summary, files=args.files)
        elif args.command == "suggest-pr-body":
            payload = suggest_pr_body(args.repo_root, args.summary, files=args.files)
        elif args.command == "show-git-workflow-advice":
            payload = show_git_workflow_advice(args.repo_root)
        elif args.command == "inspect-git-state":
            payload = inspect_git_state(args.repo_root)
        elif args.command == "list-git-worktrees":
            payload = list_git_worktrees(args.repo_root)
        elif args.command == "plan-git-change":
            payload = plan_git_change(args.repo_root, summary=args.summary, files=args.files)
        elif args.command == "create-git-worktree":
            payload = create_git_worktree(args.repo_root, args.path, args.branch_name, start_point=args.start_point)
        elif args.command == "create-git-branch":
            payload = create_git_branch(args.repo_root, args.branch_name)
        elif args.command == "stage-git-files":
            payload = stage_git_files(args.repo_root, args.files)
        elif args.command == "create-git-commit":
            payload = create_git_commit(args.repo_root, args.message, body=args.body)
        else:
            raise ValueError(f"Unsupported command: {args.command}")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1

    print(text_result(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
