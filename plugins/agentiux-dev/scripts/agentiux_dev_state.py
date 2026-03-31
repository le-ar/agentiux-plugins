#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
    create_starter,
    create_task,
    create_workstream,
    dashboard_snapshot,
    detect_commit_style,
    detect_workspace,
    get_active_brief,
    get_state_paths,
    init_workspace,
    inspect_git_state,
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
    show_host_support,
    show_upgrade_plan,
    stage_git_files,
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


def _read_json(path: str) -> dict:
    with Path(path).open() as handle:
        return json.load(handle)


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
    cmd.add_argument("--task-id")
    cmd.add_argument("--no-make-current", action="store_true")

    cmd = subparsers.add_parser("current-task")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("task")
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

    cmd = subparsers.add_parser("verification-recipes")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

    cmd = subparsers.add_parser("audit-verification-coverage")
    add_workspace_arg(cmd)
    cmd.add_argument("--workstream-id")

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

    cmd = subparsers.add_parser("plan-git-change")
    cmd.add_argument("--repo-root", required=True)
    cmd.add_argument("--summary")
    cmd.add_argument("--file", dest="files", action="append")

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
                task_id=args.task_id,
                make_current=not args.no_make_current,
            )
        elif args.command == "current-task":
            payload = current_task(args.workspace)
        elif args.command == "task":
            payload = read_task(args.workspace, task_id=args.task_id)
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
        elif args.command == "verification-recipes":
            payload = read_verification_recipes(args.workspace, workstream_id=args.workstream_id)
        elif args.command == "audit-verification-coverage":
            payload = audit_verification_coverage(args.workspace, workstream_id=args.workstream_id)
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
        elif args.command == "plan-git-change":
            payload = plan_git_change(args.repo_root, summary=args.summary, files=args.files)
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
