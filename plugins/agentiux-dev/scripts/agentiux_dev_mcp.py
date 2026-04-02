#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import traceback
from typing import Any

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
    close_current_workstream,
    close_task,
    command_aliases,
    create_git_branch,
    create_git_commit,
    create_git_worktree,
    create_starter,
    create_task,
    create_workstream,
    current_task,
    current_workstream,
    dashboard_snapshot,
    detect_commit_style,
    detect_workspace,
    get_active_brief,
    get_state_paths,
    init_workspace,
    inspect_git_state,
    list_git_worktrees,
    list_design_handoffs,
    list_reference_boards,
    list_stages,
    list_starter_presets,
    list_starter_runs,
    list_tasks,
    list_workspaces,
    list_workstreams,
    migrate_workspace_state,
    plugin_stats,
    plan_git_change,
    preview_repair_workspace_state,
    preview_workspace_init,
    read_current_audit,
    read_design_brief,
    read_design_handoff,
    read_reference_board,
    read_stage_register,
    read_task,
    read_upgrade_plan,
    read_workspace_detail,
    read_workspace_state,
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


PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {
    "name": "agentiux-dev-state",
    "version": "0.8.0",
}


def _emit(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _tool_result(payload: Any, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, sort_keys=True),
            }
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


def _read_tool(name: str, description: str, handler: Any, extra_properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    properties = {"workspacePath": {"type": "string"}}
    if extra_properties:
        properties.update(extra_properties)
    required_keys = ["workspacePath"] if required is None else required
    return {
        "title": name.replace("_", " ").title(),
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required_keys,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": handler,
    }


def _read_tool_no_workspace(
    name: str,
    description: str,
    handler: Any,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "title": name.replace("_", " ").title(),
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": handler,
    }


def _write_tool(name: str, description: str, handler: Any, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "title": name.replace("_", " ").title(),
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
        "handler": handler,
    }


TOOLS = {
    "detect_workspace": _read_tool(
        "detect_workspace",
        "Detect supported stacks, signals, and selected profile packs for a workspace path.",
        lambda args: detect_workspace(args["workspacePath"]),
    ),
    "preview_workspace_init": _read_tool(
        "preview_workspace_init",
        "Show the external paths and detected stacks that would be created for workspace initialization.",
        lambda args: preview_workspace_init(args["workspacePath"]),
    ),
    "init_workspace": _write_tool(
        "init_workspace",
        "Initialize external AgentiUX Dev state for a workspace after explicit confirmation.",
        lambda args: init_workspace(args["workspacePath"], force=args.get("force", False)),
        {
            "workspacePath": {"type": "string"},
            "force": {"type": "boolean"},
        },
        ["workspacePath"],
    ),
    "preview_repair_workspace_state": _read_tool(
        "preview_repair_workspace_state",
        "Preview how state repair would normalize external workspace state and remove stale infra assumptions without synthesizing new stage plans.",
        lambda args: preview_repair_workspace_state(args["workspacePath"]),
    ),
    "repair_workspace_state": _write_tool(
        "repair_workspace_state",
        "Repair external workspace state using the current host support model and local-dev policy without auto-generating stage plans.",
        lambda args: repair_workspace_state(args["workspacePath"]),
        {"workspacePath": {"type": "string"}},
        ["workspacePath"],
    ),
    "migrate_workspace_state": _write_tool(
        "migrate_workspace_state",
        "Migrate a workspace from the legacy single-register layout to workstreams and tasks.",
        lambda args: migrate_workspace_state(args["workspacePath"]),
        {"workspacePath": {"type": "string"}},
        ["workspacePath"],
    ),
    "get_state_paths": _read_tool(
        "get_state_paths",
        "Return the absolute external state paths for a workspace.",
        lambda args: get_state_paths(args["workspacePath"]),
    ),
    "get_workspace_state": _read_tool(
        "get_workspace_state",
        "Read the external WorkspaceState document for a workspace.",
        lambda args: read_workspace_state(args["workspacePath"]),
    ),
    "show_host_support": _read_tool(
        "show_host_support",
        "Read host support, toolchain capabilities, and support warnings for a workspace.",
        lambda args: show_host_support(args["workspacePath"]),
    ),
    "show_host_setup_plan": _read_tool(
        "show_host_setup_plan",
        "Preview the host-specific install or manual-repair steps required to satisfy missing toolchains for a workspace.",
        lambda args: show_host_setup_plan(args["workspacePath"], requirement_ids=args.get("requirementIds")),
        {"requirementIds": {"type": "array", "items": {"type": "string"}}},
    ),
    "show_auth_profiles": _read_tool(
        "show_auth_profiles",
        "List configured E2E auth profiles for the workspace.",
        lambda args: show_auth_profiles(args["workspacePath"]),
    ),
    "list_auth_sessions": _read_tool(
        "list_auth_sessions",
        "List persisted auth sessions for the workspace.",
        lambda args: list_auth_sessions(args["workspacePath"], profile_id=args.get("profileId")),
        {
            "profileId": {"type": "string"},
        },
    ),
    "get_auth_session": _read_tool(
        "get_auth_session",
        "Read one persisted auth session with a redacted summary.",
        lambda args: get_auth_session(args["workspacePath"], args["sessionId"]),
        {
            "sessionId": {"type": "string"},
        },
        ["workspacePath", "sessionId"],
    ),
    "write_auth_profile": _write_tool(
        "write_auth_profile",
        "Create or update an E2E auth profile and its secret payload.",
        lambda args: write_auth_profile(
            args["workspacePath"],
            args["profile"],
            secret_payload=args.get("secretPayload"),
        ),
        {
            "workspacePath": {"type": "string"},
            "profile": {"type": "object"},
            "secretPayload": {"type": "object"},
        },
        ["workspacePath", "profile"],
    ),
    "write_auth_session": _write_tool(
        "write_auth_session",
        "Create or update a persisted auth session and its secret payload.",
        lambda args: write_auth_session(
            args["workspacePath"],
            args["session"],
            secret_payload=args.get("secretPayload"),
        ),
        {
            "workspacePath": {"type": "string"},
            "session": {"type": "object"},
            "secretPayload": {},
        },
        ["workspacePath", "session"],
    ),
    "remove_auth_profile": _write_tool(
        "remove_auth_profile",
        "Remove an E2E auth profile and its persisted secret payload.",
        lambda args: remove_auth_profile(args["workspacePath"], args["profileId"]),
        {
            "workspacePath": {"type": "string"},
            "profileId": {"type": "string"},
        },
        ["workspacePath", "profileId"],
    ),
    "invalidate_auth_session": _write_tool(
        "invalidate_auth_session",
        "Invalidate one persisted auth session without deleting its revision history.",
        lambda args: invalidate_auth_session(args["workspacePath"], args["sessionId"]),
        {
            "workspacePath": {"type": "string"},
            "sessionId": {"type": "string"},
        },
        ["workspacePath", "sessionId"],
    ),
    "remove_auth_session": _write_tool(
        "remove_auth_session",
        "Remove a persisted auth session, its secret payload, and revisions.",
        lambda args: remove_auth_session(args["workspacePath"], args["sessionId"]),
        {
            "workspacePath": {"type": "string"},
            "sessionId": {"type": "string"},
        },
        ["workspacePath", "sessionId"],
    ),
    "resolve_auth_profile": _read_tool(
        "resolve_auth_profile",
        "Resolve the matching auth profile for a workspace/task/issue/case context and return a redacted artifact summary.",
        lambda args: resolve_auth_profile(
            args["workspacePath"],
            profile_id=args.get("profileId"),
            task_id=args.get("taskId"),
            external_issue=args.get("externalIssue"),
            case=args.get("case"),
            workstream_id=args.get("workstreamId"),
            request_mode=args.get("requestMode"),
            action_tags=args.get("actionTags"),
            session_binding=args.get("sessionBinding"),
            context_overrides=args.get("contextOverrides"),
            prefer_cached=args.get("preferCached", True),
            force_refresh=args.get("forceRefresh", False),
            surface_mode="mcp",
        ),
        {
            "profileId": {"type": "string"},
            "taskId": {"type": "string"},
            "externalIssue": {"type": "object"},
            "case": {"type": "object"},
            "workstreamId": {"type": "string"},
            "requestMode": {"type": "string", "enum": ["read_only", "mutating"]},
            "actionTags": {"type": "array", "items": {"type": "string"}},
            "sessionBinding": {},
            "contextOverrides": {},
            "preferCached": {"type": "boolean"},
            "forceRefresh": {"type": "boolean"},
        },
    ),
    "install_host_requirements": _write_tool(
        "install_host_requirements",
        "Install missing host requirements for a workspace after explicit confirmation, then refresh host support state.",
        lambda args: install_host_requirements(
            args["workspacePath"],
            requirement_ids=args.get("requirementIds"),
            confirmed=args.get("confirmed", False),
        ),
        {
            "workspacePath": {"type": "string"},
            "requirementIds": {"type": "array", "items": {"type": "string"}},
            "confirmed": {"type": "boolean"},
        },
        ["workspacePath"],
    ),
    "repair_host_requirements": _write_tool(
        "repair_host_requirements",
        "Re-run the host requirement plan for a workspace after explicit confirmation and refresh host support state.",
        lambda args: repair_host_requirements(
            args["workspacePath"],
            requirement_ids=args.get("requirementIds"),
            confirmed=args.get("confirmed", False),
        ),
        {
            "workspacePath": {"type": "string"},
            "requirementIds": {"type": "array", "items": {"type": "string"}},
            "confirmed": {"type": "boolean"},
        },
        ["workspacePath"],
    ),
    "get_stage_register": _read_tool(
        "get_stage_register",
        "Read the external StageRegister document for a workspace or explicit workstream.",
        lambda args: read_stage_register(args["workspacePath"], workstream_id=args.get("workstreamId")),
        {"workstreamId": {"type": "string"}},
    ),
    "list_stages": _read_tool(
        "list_stages",
        "List stage ids, titles, statuses, and paths for the workspace or explicit workstream.",
        lambda args: list_stages(args["workspacePath"], workstream_id=args.get("workstreamId")),
        {"workstreamId": {"type": "string"}},
    ),
    "get_active_brief": _read_tool(
        "get_active_brief",
        "Read the current external StageExecutionBrief or TaskBrief markdown for a workspace.",
        lambda args: get_active_brief(args["workspacePath"]),
    ),
    "set_active_brief": _write_tool(
        "set_active_brief",
        "Persist the active StageExecutionBrief or TaskBrief markdown document for the selected workspace mode.",
        lambda args: set_active_brief(args["workspacePath"], args["markdown"]),
        {
            "workspacePath": {"type": "string"},
            "markdown": {"type": "string"},
        },
        ["workspacePath", "markdown"],
    ),
    "write_stage_register": _write_tool(
        "write_stage_register",
        "Persist a StageRegister update. Stage definition changes require explicit confirmation and completed stages are immutable.",
        lambda args: write_stage_register(
            args["workspacePath"],
            args["register"],
            confirmed_stage_plan_edit=args.get("confirmedStagePlanEdit", False),
            workstream_id=args.get("workstreamId"),
        ),
        {
            "workspacePath": {"type": "string"},
            "register": {"type": "object"},
            "confirmedStagePlanEdit": {"type": "boolean"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "register"],
    ),
    "list_workstreams": _read_tool(
        "list_workstreams",
        "List workstreams for a workspace.",
        lambda args: list_workstreams(args["workspacePath"]),
    ),
    "create_workstream": _write_tool(
        "create_workstream",
        "Create a named workstream container with its own external state and an empty stage register that still requires explicit stage-plan confirmation.",
        lambda args: create_workstream(
            args["workspacePath"],
            title=args["title"],
            kind=args.get("kind", "feature"),
            branch_hint=args.get("branchHint"),
            scope_summary=args.get("scopeSummary"),
            workstream_id=args.get("workstreamId"),
            make_current=args.get("makeCurrent", True),
        ),
        {
            "workspacePath": {"type": "string"},
            "title": {"type": "string"},
            "kind": {"type": "string"},
            "branchHint": {"type": "string"},
            "scopeSummary": {"type": "string"},
            "workstreamId": {"type": "string"},
            "makeCurrent": {"type": "boolean"},
        },
        ["workspacePath", "title"],
    ),
    "switch_workstream": _write_tool(
        "switch_workstream",
        "Switch the workspace to a different current workstream.",
        lambda args: switch_workstream(args["workspacePath"], args["workstreamId"]),
        {
            "workspacePath": {"type": "string"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "workstreamId"],
    ),
    "get_current_workstream": _read_tool(
        "get_current_workstream",
        "Read the current workstream summary and register.",
        lambda args: current_workstream(args["workspacePath"]),
    ),
    "close_current_workstream": _write_tool(
        "close_current_workstream",
        "Close the current workstream and mark it completed or archived.",
        lambda args: close_current_workstream(args["workspacePath"]),
        {"workspacePath": {"type": "string"}},
        ["workspacePath"],
    ),
    "list_tasks": _read_tool(
        "list_tasks",
        "List lightweight tasks for a workspace.",
        lambda args: list_tasks(args["workspacePath"]),
    ),
    "create_task": _write_tool(
        "create_task",
        "Create a lightweight task for targeted work without starting a full workstream.",
        lambda args: create_task(
            args["workspacePath"],
            title=args["title"],
            objective=args["objective"],
            scope=args.get("scope"),
            verification_target=args.get("verificationTarget"),
            verification_selectors=args.get("verificationSelectors"),
            verification_mode_default=args.get("verificationModeDefault"),
            branch_hint=args.get("branchHint"),
            linked_workstream_id=args.get("linkedWorkstreamId"),
            stage_id=args.get("stageId"),
            external_issue=args.get("externalIssue"),
            codex_estimate_minutes=args.get("codexEstimateMinutes"),
            task_id=args.get("taskId"),
            make_current=args.get("makeCurrent", True),
        ),
        {
            "workspacePath": {"type": "string"},
            "title": {"type": "string"},
            "objective": {"type": "string"},
            "scope": {"type": "array", "items": {"type": "string"}},
            "verificationTarget": {"type": "string"},
            "verificationSelectors": {"type": "object"},
            "verificationModeDefault": {"type": "string", "enum": ["targeted", "full"]},
            "branchHint": {"type": "string"},
            "linkedWorkstreamId": {"type": "string"},
            "stageId": {"type": "string"},
            "externalIssue": {"type": "object"},
            "codexEstimateMinutes": {"type": "integer"},
            "taskId": {"type": "string"},
            "makeCurrent": {"type": "boolean"},
        },
        ["workspacePath", "title", "objective"],
    ),
    "get_current_task": _read_tool(
        "get_current_task",
        "Read the current task if task mode is active.",
        lambda args: current_task(args["workspacePath"]),
    ),
    "list_project_notes": _read_tool(
        "list_project_notes",
        "List project memory notes for a workspace.",
        lambda args: list_project_notes(args["workspacePath"], status=args.get("status")),
        {"status": {"type": "string", "enum": ["active", "archived"]}},
    ),
    "get_project_note": _read_tool(
        "get_project_note",
        "Read one project memory note and its revision metadata.",
        lambda args: get_project_note(args["workspacePath"], args["noteId"]),
        {"noteId": {"type": "string"}},
        ["workspacePath", "noteId"],
    ),
    "write_project_note": _write_tool(
        "write_project_note",
        "Create or update a project memory note.",
        lambda args: write_project_note(args["workspacePath"], args["note"]),
        {
            "workspacePath": {"type": "string"},
            "note": {"type": "object"},
        },
        ["workspacePath", "note"],
    ),
    "archive_project_note": _write_tool(
        "archive_project_note",
        "Archive a project memory note.",
        lambda args: archive_project_note(args["workspacePath"], args["noteId"]),
        {
            "workspacePath": {"type": "string"},
            "noteId": {"type": "string"},
        },
        ["workspacePath", "noteId"],
    ),
    "search_project_notes": _read_tool(
        "search_project_notes",
        "Search project memory notes by free-text query.",
        lambda args: search_project_notes(args["workspacePath"], args["queryText"], limit=args.get("limit", 8)),
        {
            "queryText": {"type": "string"},
            "limit": {"type": "integer"},
        },
        ["workspacePath", "queryText"],
    ),
    "get_task": _read_tool(
        "get_task",
        "Read a task by id.",
        lambda args: read_task(args["workspacePath"], task_id=args["taskId"]),
        {"taskId": {"type": "string"}},
        ["workspacePath", "taskId"],
    ),
    "switch_task": _write_tool(
        "switch_task",
        "Make an existing task current and start its active task session.",
        lambda args: switch_task(args["workspacePath"], args["taskId"]),
        {
            "workspacePath": {"type": "string"},
            "taskId": {"type": "string"},
        },
        ["workspacePath", "taskId"],
    ),
    "close_task": _write_tool(
        "close_task",
        "Close a task and optionally persist a verification summary.",
        lambda args: close_task(args["workspacePath"], task_id=args.get("taskId"), verification_summary=args.get("verificationSummary")),
        {
            "workspacePath": {"type": "string"},
            "taskId": {"type": "string"},
            "verificationSummary": {"type": "object"},
        },
        ["workspacePath"],
    ),
    "list_workspaces": {
        "title": "List Workspaces",
        "description": "List all initialized workspaces with dashboard-oriented summaries.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: list_workspaces(),
    },
    "get_workspace_detail": _read_tool(
        "get_workspace_detail",
        "Return full detail for a workspace, including workstreams, tasks, stage state, briefs, design state, audits, starters, and paths.",
        lambda args: read_workspace_detail(args["workspacePath"]),
    ),
    "get_plugin_stats": {
        "title": "Get Plugin Stats",
        "description": "Return aggregate plugin statistics across initialized workspaces.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: plugin_stats(),
    },
    "get_analytics_snapshot": _read_tool_no_workspace(
        "get_analytics_snapshot",
        "Return global or workspace-scoped analytics and learning-entry summary data.",
        lambda args: get_analytics_snapshot(args.get("workspacePath")),
        {
            "workspacePath": {"type": "string"},
        },
    ),
    "list_learning_entries": _read_tool_no_workspace(
        "list_learning_entries",
        "List global or workspace-scoped learning entries.",
        lambda args: list_learning_entries(
            workspace=args.get("workspacePath"),
            status=args.get("status"),
            limit=args.get("limit"),
        ),
        {
            "workspacePath": {"type": "string"},
            "status": {"type": "string", "enum": ["open", "resolved", "archived"]},
            "limit": {"type": "integer"},
        },
    ),
    "write_learning_entry": _write_tool(
        "write_learning_entry",
        "Create a learning entry for plugin or Codex retrospective tracking.",
        lambda args: write_learning_entry(args.get("workspacePath"), args["entry"]),
        {
            "workspacePath": {"type": "string"},
            "entry": {"type": "object"},
        },
        ["entry"],
    ),
    "update_learning_entry": _write_tool(
        "update_learning_entry",
        "Update or resolve an existing learning entry.",
        lambda args: update_learning_entry(args.get("workspacePath"), args["entryId"], args["updates"]),
        {
            "workspacePath": {"type": "string"},
            "entryId": {"type": "string"},
            "updates": {"type": "object"},
        },
        ["entryId", "updates"],
    ),
    "get_dashboard_snapshot": {
        "title": "Get Dashboard Snapshot",
        "description": "Return the overview and optional workspace detail payload used by the local GUI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspacePath": {"type": "string"}
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: dashboard_snapshot(args.get("workspacePath")),
    },
    "advise_workflow": _write_tool(
        "advise_workflow",
        "Inspect the current request context and recommend workspace init, starter, workstream, or task actions before execution starts. This tool does not write state automatically.",
        lambda args: workflow_advice(
            args["workspacePath"],
            request_text=args.get("requestText"),
            auto_create=args.get("autoCreate", False),
        ),
        {
            "workspacePath": {"type": "string"},
            "requestText": {"type": "string"},
            "autoCreate": {"type": "boolean"},
        },
        ["workspacePath"],
    ),
    "get_command_aliases": {
        "title": "Get Command Aliases",
        "description": "Return canonical command surface aliases used for runtime language matching.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: {"command_aliases": command_aliases()},
    },
    "show_capability_catalog": _read_tool_no_workspace(
        "show_capability_catalog",
        "Show the compact low-token catalog for skills, MCP tools, scripts, and references.",
        lambda args: show_capability_catalog(
            kind=args.get("kind"),
            route_id=args.get("routeId"),
            query_text=args.get("queryText"),
            limit=args.get("limit"),
        ),
        {
            "kind": {"type": "string", "enum": ["skill", "mcp_tool", "script", "reference"]},
            "routeId": {"type": "string"},
            "queryText": {"type": "string"},
            "limit": {"type": "integer"},
        },
    ),
    "show_intent_route": _read_tool_no_workspace(
        "show_intent_route",
        "Resolve the canonical low-token route for a request before reading large docs or scripts.",
        lambda args: show_intent_route(route_id=args.get("routeId"), request_text=args.get("requestText")),
        {
            "routeId": {"type": "string"},
            "requestText": {"type": "string"},
        },
    ),
    "audit_repository": _read_tool(
        "audit_repository",
        "Audit an existing repository for missing local infra, verification, docs, design hooks, and workflow readiness.",
        lambda args: audit_repository(args["workspacePath"]),
    ),
    "get_current_audit": _read_tool(
        "get_current_audit",
        "Read the latest persisted repository audit for a workspace.",
        lambda args: read_current_audit(args["workspacePath"]),
    ),
    "show_upgrade_plan": _read_tool(
        "show_upgrade_plan",
        "Build or read the current upgrade plan from the latest repository audit.",
        lambda args: show_upgrade_plan(args["workspacePath"]),
    ),
    "get_current_upgrade_plan": _read_tool(
        "get_current_upgrade_plan",
        "Read the latest persisted upgrade plan for a workspace.",
        lambda args: read_upgrade_plan(args["workspacePath"]),
    ),
    "apply_upgrade_plan": _write_tool(
        "apply_upgrade_plan",
        "Apply an approved upgrade plan by creating workstreams and tasks for the identified gaps.",
        lambda args: apply_upgrade_plan(args["workspacePath"], confirmed=args.get("confirmed", False)),
        {
            "workspacePath": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        ["workspacePath"],
    ),
    "get_design_brief": _read_tool(
        "get_design_brief",
        "Read the persisted DesignBrief for a workspace or explicit workstream.",
        lambda args: read_design_brief(args["workspacePath"], workstream_id=args.get("workstreamId")),
        {"workstreamId": {"type": "string"}},
    ),
    "write_design_brief": _write_tool(
        "write_design_brief",
        "Persist a DesignBrief for a workspace.",
        lambda args: write_design_brief(args["workspacePath"], args["brief"], workstream_id=args.get("workstreamId")),
        {
            "workspacePath": {"type": "string"},
            "brief": {"type": "object"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "brief"],
    ),
    "list_reference_boards": _read_tool(
        "list_reference_boards",
        "List persisted reference boards for a workspace or explicit workstream.",
        lambda args: list_reference_boards(args["workspacePath"], workstream_id=args.get("workstreamId")),
        {"workstreamId": {"type": "string"}},
    ),
    "get_reference_board": _read_tool(
        "get_reference_board",
        "Read a persisted reference board for a workspace or explicit workstream.",
        lambda args: read_reference_board(args["workspacePath"], board_id=args.get("boardId", "current"), workstream_id=args.get("workstreamId")),
        {"boardId": {"type": "string"}, "workstreamId": {"type": "string"}},
    ),
    "write_reference_board": _write_tool(
        "write_reference_board",
        "Persist a reference board and optionally mark it as current.",
        lambda args: write_reference_board(
            args["workspacePath"],
            args["board"],
            board_id=args.get("boardId", "current"),
            make_current=args.get("makeCurrent", True),
            workstream_id=args.get("workstreamId"),
        ),
        {
            "workspacePath": {"type": "string"},
            "board": {"type": "object"},
            "boardId": {"type": "string"},
            "makeCurrent": {"type": "boolean"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "board"],
    ),
    "cache_reference_preview": _write_tool(
        "cache_reference_preview",
        "Copy a local preview asset into the external design cache for stable boards and GUI rendering.",
        lambda args: cache_reference_preview(
            args["workspacePath"],
            args["sourcePath"],
            args.get("candidateId"),
            workstream_id=args.get("workstreamId"),
        ),
        {
            "workspacePath": {"type": "string"},
            "sourcePath": {"type": "string"},
            "candidateId": {"type": "string"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "sourcePath"],
    ),
    "list_design_handoffs": _read_tool(
        "list_design_handoffs",
        "List persisted design handoffs for a workspace or explicit workstream.",
        lambda args: list_design_handoffs(args["workspacePath"], workstream_id=args.get("workstreamId")),
        {"workstreamId": {"type": "string"}},
    ),
    "get_design_handoff": _read_tool(
        "get_design_handoff",
        "Read a persisted design handoff for a workspace or explicit workstream.",
        lambda args: read_design_handoff(args["workspacePath"], handoff_id=args.get("handoffId", "current"), workstream_id=args.get("workstreamId")),
        {"handoffId": {"type": "string"}, "workstreamId": {"type": "string"}},
    ),
    "write_design_handoff": _write_tool(
        "write_design_handoff",
        "Persist a design handoff and optionally mark it as current.",
        lambda args: write_design_handoff(
            args["workspacePath"],
            args["handoff"],
            handoff_id=args.get("handoffId", "current"),
            make_current=args.get("makeCurrent", True),
            workstream_id=args.get("workstreamId"),
        ),
        {
            "workspacePath": {"type": "string"},
            "handoff": {"type": "object"},
            "handoffId": {"type": "string"},
            "makeCurrent": {"type": "boolean"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "handoff"],
    ),
    "get_verification_recipes": _read_tool(
        "get_verification_recipes",
        "Read the verification recipe document for a workspace or explicit workstream.",
        lambda args: read_verification_recipes(args["workspacePath"], workstream_id=args.get("workstreamId")),
        {"workstreamId": {"type": "string"}},
    ),
    "audit_verification_coverage": _read_tool(
        "audit_verification_coverage",
        "Audit warning-level deterministic verification coverage gaps, including missing web or Android visual cases, without mutating workspace state.",
        lambda args: audit_verification_coverage(args["workspacePath"], workstream_id=args.get("workstreamId")),
        {"workstreamId": {"type": "string"}},
    ),
    "show_verification_helper_catalog": _read_tool(
        "show_verification_helper_catalog",
        "Show the plugin-owned visual helper bundle catalog, supported runners, entrypoints, and materialization status for a workspace.",
        lambda args: show_verification_helper_catalog(args["workspacePath"]),
    ),
    "show_workspace_context_pack": _read_tool(
        "show_workspace_context_pack",
        "Load the current workspace context pack and optional semantic retrieval pack for a request.",
        lambda args: show_workspace_context_pack(
            args["workspacePath"],
            request_text=args.get("requestText"),
            route_id=args.get("routeId"),
            limit=args.get("limit"),
            force_refresh=args.get("forceRefresh", False),
        ),
        {
            "requestText": {"type": "string"},
            "routeId": {"type": "string"},
            "limit": {"type": "integer"},
            "forceRefresh": {"type": "boolean"},
        },
    ),
    "search_context_index": _read_tool(
        "search_context_index",
        "Search the global low-token context index for relevant repo chunks and recommended capabilities.",
        lambda args: search_context_index(
            args["workspacePath"],
            args["queryText"],
            route_id=args.get("routeId"),
            limit=args.get("limit"),
        ),
        {
            "queryText": {"type": "string"},
            "routeId": {"type": "string"},
            "limit": {"type": "integer"},
        },
        ["workspacePath", "queryText"],
    ),
    "refresh_context_index": _read_tool(
        "refresh_context_index",
        "Refresh the global project context index and semantic cache metadata for a workspace.",
        lambda args: refresh_context_index(args["workspacePath"], force=args.get("force", False)),
        {
            "force": {"type": "boolean"},
        },
    ),
    "show_youtrack_connections": _read_tool(
        "show_youtrack_connections",
        "List sanitized YouTrack connections for a workspace, including default status and cached field-catalog paths.",
        lambda args: list_youtrack_connections(args["workspacePath"]),
    ),
    "connect_youtrack": _write_tool(
        "connect_youtrack",
        "Create or validate a workspace-scoped YouTrack connection using a permanent token.",
        lambda args: connect_youtrack(
            args["workspacePath"],
            base_url=args["baseUrl"],
            token=args["token"],
            label=args.get("label"),
            connection_id=args.get("connectionId"),
            project_scope=args.get("projectScope"),
            default=args.get("default", False),
            test_connection=args.get("testConnection", True),
        ),
        {
            "workspacePath": {"type": "string"},
            "baseUrl": {"type": "string"},
            "token": {"type": "string"},
            "label": {"type": "string"},
            "connectionId": {"type": "string"},
            "projectScope": {"type": "array", "items": {"type": "string"}},
            "default": {"type": "boolean"},
            "testConnection": {"type": "boolean"},
        },
        ["workspacePath", "baseUrl", "token"],
    ),
    "update_youtrack_connection": _write_tool(
        "update_youtrack_connection",
        "Update a YouTrack connection, optionally rotate its token, and refresh cached metadata.",
        lambda args: update_youtrack_connection(
            args["workspacePath"],
            args["connectionId"],
            base_url=args.get("baseUrl"),
            token=args.get("token"),
            label=args.get("label"),
            project_scope=args.get("projectScope"),
            default=args.get("default"),
            test_connection=args.get("testConnection", True),
        ),
        {
            "workspacePath": {"type": "string"},
            "connectionId": {"type": "string"},
            "baseUrl": {"type": "string"},
            "token": {"type": "string"},
            "label": {"type": "string"},
            "projectScope": {"type": "array", "items": {"type": "string"}},
            "default": {"type": "boolean"},
            "testConnection": {"type": "boolean"},
        },
        ["workspacePath", "connectionId"],
    ),
    "remove_youtrack_connection": _write_tool(
        "remove_youtrack_connection",
        "Remove a YouTrack connection and its stored token metadata from a workspace.",
        lambda args: remove_youtrack_connection(args["workspacePath"], args["connectionId"]),
        {
            "workspacePath": {"type": "string"},
            "connectionId": {"type": "string"},
        },
        ["workspacePath", "connectionId"],
    ),
    "test_youtrack_connection": _write_tool(
        "test_youtrack_connection",
        "Re-run connection validation and refresh the field catalog for a YouTrack connection.",
        lambda args: test_youtrack_connection(args["workspacePath"], args["connectionId"]),
        {
            "workspacePath": {"type": "string"},
            "connectionId": {"type": "string"},
        },
        ["workspacePath", "connectionId"],
    ),
    "search_youtrack_issues": _write_tool(
        "search_youtrack_issues",
        "Run a YouTrack issue search, persist the search session, and return a ranked shortlist.",
        lambda args: search_youtrack_issues(
            args["workspacePath"],
            query_text=args["queryText"],
            connection_id=args.get("connectionId"),
            page_size=args.get("pageSize", 25),
            skip=args.get("skip", 0),
            shortlist_size=args.get("shortlistSize", 8),
        ),
        {
            "workspacePath": {"type": "string"},
            "queryText": {"type": "string"},
            "connectionId": {"type": "string"},
            "pageSize": {"type": "integer"},
            "skip": {"type": "integer"},
            "shortlistSize": {"type": "integer"},
        },
        ["workspacePath", "queryText"],
    ),
    "show_youtrack_issue_queue": _read_tool(
        "show_youtrack_issue_queue",
        "Read the current or explicit persisted YouTrack search session and shortlist queue.",
        lambda args: show_youtrack_issue_queue(args["workspacePath"], search_session_id=args.get("searchSessionId")),
        {"searchSessionId": {"type": "string"}},
    ),
    "propose_youtrack_workstream_plan": _write_tool(
        "propose_youtrack_workstream_plan",
        "Persist a draft workstream plan from a YouTrack search session by selecting issue ids and batching them into stages.",
        lambda args: propose_youtrack_workstream_plan(
            args["workspacePath"],
            search_session_id=args.get("searchSessionId"),
            selected_issue_ids=args.get("selectedIssueIds"),
            rejected_issue_ids=args.get("rejectedIssueIds"),
            workstream_title=args.get("workstreamTitle"),
        ),
        {
            "workspacePath": {"type": "string"},
            "searchSessionId": {"type": "string"},
            "selectedIssueIds": {"type": "array", "items": {"type": "string"}},
            "rejectedIssueIds": {"type": "array", "items": {"type": "string"}},
            "workstreamTitle": {"type": "string"},
        },
        ["workspacePath"],
    ),
    "apply_youtrack_workstream_plan": _write_tool(
        "apply_youtrack_workstream_plan",
        "Apply a confirmed YouTrack plan draft by creating a workstream, stage register, linked tasks, and an execution brief.",
        lambda args: apply_youtrack_workstream_plan(
            args["workspacePath"],
            plan_id=args.get("planId"),
            confirmed=args.get("confirmed", False),
            activate_first_task=args.get("activateFirstTask", False),
        ),
        {
            "workspacePath": {"type": "string"},
            "planId": {"type": "string"},
            "confirmed": {"type": "boolean"},
            "activateFirstTask": {"type": "boolean"},
        },
        ["workspacePath"],
    ),
    "sync_verification_helpers": _write_tool(
        "sync_verification_helpers",
        "Materialize the versioned plugin-owned visual helper bundle into the repository-local generated helper directory.",
        lambda args: sync_verification_helpers(args["workspacePath"], force=args.get("force", False)),
        {
            "workspacePath": {"type": "string"},
            "force": {"type": "boolean"},
        },
        ["workspacePath"],
    ),
    "resolve_verification": _read_tool(
        "resolve_verification",
        "Resolve the targeted verification plan for the current task or workstream without starting a run.",
        lambda args: resolve_verification_selection(
            args["workspacePath"],
            workstream_id=args.get("workstreamId"),
            changed_paths=args.get("changedPaths"),
            confirm_heuristics=args.get("confirmHeuristics", False),
        ),
        {
            "workstreamId": {"type": "string"},
            "changedPaths": {"type": "array", "items": {"type": "string"}},
            "confirmHeuristics": {"type": "boolean"},
        },
    ),
    "write_verification_recipes": _write_tool(
        "write_verification_recipes",
        "Persist verification cases and suites for a workspace or explicit workstream.",
        lambda args: write_verification_recipes(args["workspacePath"], args["recipes"], workstream_id=args.get("workstreamId")),
        {
            "workspacePath": {"type": "string"},
            "recipes": {"type": "object"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "recipes"],
    ),
    "approve_verification_baseline": _write_tool(
        "approve_verification_baseline",
        "Approve a project-owned baseline from a completed verification run.",
        lambda args: approve_verification_baseline(args["workspacePath"], args["caseId"], run_id=args.get("runId"), workstream_id=args.get("workstreamId")),
        {
            "workspacePath": {"type": "string"},
            "caseId": {"type": "string"},
            "runId": {"type": "string"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "caseId"],
    ),
    "update_verification_baseline": _write_tool(
        "update_verification_baseline",
        "Copy a verification artifact into a project-owned canonical baseline path.",
        lambda args: update_verification_baseline(
            args["workspacePath"],
            args["caseId"],
            artifact_path=args.get("artifactPath"),
            run_id=args.get("runId"),
            workstream_id=args.get("workstreamId"),
        ),
        {
            "workspacePath": {"type": "string"},
            "caseId": {"type": "string"},
            "artifactPath": {"type": "string"},
            "runId": {"type": "string"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "caseId"],
    ),
    "run_verification_case": _write_tool(
        "run_verification_case",
        "Start one deterministic verification case for a workspace or explicit workstream.",
        lambda args: start_verification_case(args["workspacePath"], args["caseId"], wait=args.get("wait", False), workstream_id=args.get("workstreamId")),
        {
            "workspacePath": {"type": "string"},
            "caseId": {"type": "string"},
            "wait": {"type": "boolean"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "caseId"],
    ),
    "run_verification_suite": _write_tool(
        "run_verification_suite",
        "Start one named deterministic verification suite for a workspace or explicit workstream.",
        lambda args: start_verification_suite(args["workspacePath"], args["suiteId"], wait=args.get("wait", False), workstream_id=args.get("workstreamId")),
        {
            "workspacePath": {"type": "string"},
            "suiteId": {"type": "string"},
            "wait": {"type": "boolean"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "suiteId"],
    ),
    "list_verification_runs": _read_tool(
        "list_verification_runs",
        "List verification runs with explicit latest and active run metadata.",
        lambda args: list_verification_runs(args["workspacePath"], limit=args.get("limit"), workstream_id=args.get("workstreamId")),
        {"limit": {"type": "integer"}, "workstreamId": {"type": "string"}},
    ),
    "get_verification_run": _read_tool(
        "get_verification_run",
        "Read a single verification run with health metadata.",
        lambda args: read_verification_run(args["workspacePath"], args["runId"], workstream_id=args.get("workstreamId")),
        {"runId": {"type": "string"}, "workstreamId": {"type": "string"}},
        ["workspacePath", "runId"],
    ),
    "get_verification_events": _read_tool(
        "get_verification_events",
        "Read recent structured verification events for a run.",
        lambda args: read_verification_events(args["workspacePath"], args["runId"], limit=args.get("limit", 50), workstream_id=args.get("workstreamId")),
        {"runId": {"type": "string"}, "limit": {"type": "integer"}, "workstreamId": {"type": "string"}},
        ["workspacePath", "runId"],
    ),
    "get_verification_log": _read_tool(
        "get_verification_log",
        "Read the tail of stdout, stderr, or Android logcat for a verification run.",
        lambda args: read_verification_log_tail(
            args["workspacePath"],
            args["runId"],
            stream=args.get("stream", "stdout"),
            lines=args.get("lines", 50),
            workstream_id=args.get("workstreamId"),
        ),
        {"runId": {"type": "string"}, "stream": {"type": "string"}, "lines": {"type": "integer"}, "workstreamId": {"type": "string"}},
        ["workspacePath", "runId"],
    ),
    "wait_for_verification_run": _write_tool(
        "wait_for_verification_run",
        "Wait for a verification run to finish.",
        lambda args: wait_for_verification_run(
            args["workspacePath"],
            args["runId"],
            timeout_seconds=args.get("timeoutSeconds", 60.0),
            workstream_id=args.get("workstreamId"),
        ),
        {
            "workspacePath": {"type": "string"},
            "runId": {"type": "string"},
            "timeoutSeconds": {"type": "number"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "runId"],
    ),
    "cancel_verification_run": _write_tool(
        "cancel_verification_run",
        "Cancel a running verification run.",
        lambda args: cancel_verification_run(args["workspacePath"], args["runId"], workstream_id=args.get("workstreamId")),
        {
            "workspacePath": {"type": "string"},
            "runId": {"type": "string"},
            "workstreamId": {"type": "string"},
        },
        ["workspacePath", "runId"],
    ),
    "list_starter_presets": {
        "title": "List Starter Presets",
        "description": "List curated starter presets and advisory metadata for thin-wrapper greenfield creation.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: list_starter_presets(),
    },
    "create_starter": {
        "title": "Create Starter",
        "description": "Create a greenfield project from a curated starter preset using the official upstream CLI only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "presetId": {"type": "string"},
                "destinationRoot": {"type": "string"},
                "projectName": {"type": "string"},
                "force": {"type": "boolean"},
            },
            "required": ["presetId", "destinationRoot", "projectName"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
        "handler": lambda args: create_starter(args["presetId"], args["destinationRoot"], args["projectName"], force=args.get("force", False)),
    },
    "list_starter_runs": {
        "title": "List Starter Runs",
        "description": "List recent starter creation runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: list_starter_runs(limit=args.get("limit", 10)),
    },
    "detect_commit_style": {
        "title": "Detect Commit Style",
        "description": "Inspect git history and commitlint-style config to determine the repository commit message style.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["repoRoot"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: detect_commit_style(args["repoRoot"], limit=args.get("limit", 30)),
    },
    "suggest_commit_message": {
        "title": "Suggest Commit Message",
        "description": "Suggest a commit message that follows the detected repository commit style.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "summary": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repoRoot", "summary"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: suggest_commit_message(args["repoRoot"], args["summary"], files=args.get("files")),
    },
    "suggest_branch_name": {
        "title": "Suggest Branch Name",
        "description": "Suggest a branch name aligned with repository history or fallback policy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "summary": {"type": "string"},
                "mode": {"type": "string", "enum": ["task", "workstream"]},
            },
            "required": ["repoRoot", "summary"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: suggest_branch_name(args["repoRoot"], args["summary"], mode=args.get("mode", "task")),
    },
    "suggest_pr_title": {
        "title": "Suggest PR Title",
        "description": "Suggest a pull request title aligned with repository conventions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "summary": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repoRoot", "summary"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: suggest_pr_title(args["repoRoot"], args["summary"], files=args.get("files")),
    },
    "suggest_pr_body": {
        "title": "Suggest PR Body",
        "description": "Suggest a pull request body skeleton aligned with repository workflow guidance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "summary": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repoRoot", "summary"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: suggest_pr_body(args["repoRoot"], args["summary"], files=args.get("files")),
    },
    "show_git_workflow_advice": {
        "title": "Show Git Workflow Advice",
        "description": "Inspect repository history and return repo-aware Git workflow advice.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"}
            },
            "required": ["repoRoot"],
            "additionalProperties": False
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True
        },
        "handler": lambda args: show_git_workflow_advice(args["repoRoot"]),
    },
    "inspect_git_state": {
        "title": "Inspect Git State",
        "description": "Inspect branch, divergence, changed files, and staged state for a local repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"}
            },
            "required": ["repoRoot"],
            "additionalProperties": False
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True
        },
        "handler": lambda args: inspect_git_state(args["repoRoot"]),
    },
    "list_git_worktrees": {
        "title": "List Git Worktrees",
        "description": "Inspect linked git worktrees for a local repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"}
            },
            "required": ["repoRoot"],
            "additionalProperties": False
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True
        },
        "handler": lambda args: list_git_worktrees(args["repoRoot"]),
    },
    "plan_git_change": {
        "title": "Plan Git Change",
        "description": "Plan the next safe local git actions from repository state plus current task or workstream context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "summary": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repoRoot"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
        },
        "handler": lambda args: plan_git_change(args["repoRoot"], summary=args.get("summary"), files=args.get("files")),
    },
    "create_git_worktree": {
        "title": "Create Git Worktree",
        "description": "Create a linked git worktree on a new branch after explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "path": {"type": "string"},
                "branchName": {"type": "string"},
                "startPoint": {"type": "string"},
            },
            "required": ["repoRoot", "path", "branchName"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
        "handler": lambda args: create_git_worktree(
            args["repoRoot"],
            args["path"],
            args["branchName"],
            start_point=args.get("startPoint", "HEAD"),
        ),
    },
    "create_git_branch": {
        "title": "Create Git Branch",
        "description": "Create or switch to a local branch after explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "branchName": {"type": "string"},
            },
            "required": ["repoRoot", "branchName"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
        "handler": lambda args: create_git_branch(args["repoRoot"], args["branchName"]),
    },
    "stage_git_files": {
        "title": "Stage Git Files",
        "description": "Stage an explicit set of files with git add.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repoRoot", "files"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
        "handler": lambda args: stage_git_files(args["repoRoot"], args["files"]),
    },
    "create_git_commit": {
        "title": "Create Git Commit",
        "description": "Create a local git commit from the currently staged changes after explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoRoot": {"type": "string"},
                "message": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["repoRoot", "message"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
        "handler": lambda args: create_git_commit(args["repoRoot"], args["message"], body=args.get("body")),
    },
}


def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": SERVER_INFO,
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        tools = []
        for name, definition in TOOLS.items():
            tool_entry = {
                "name": name,
                "title": definition["title"],
                "description": definition["description"],
                "inputSchema": definition["inputSchema"],
                "annotations": definition.get("annotations", {}),
            }
            tools.append(tool_entry)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": tools},
        }

    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_result({"error": f"Unknown tool: {name}"}, is_error=True),
            }
        try:
            payload = TOOLS[name]["handler"](arguments)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_result(payload),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_result(
                    {
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                    is_error=True,
                ),
            }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = _handle_request(request)
            if response is not None:
                _emit(response)
        except Exception:  # noqa: BLE001
            _emit(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": traceback.format_exc(),
                    },
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
