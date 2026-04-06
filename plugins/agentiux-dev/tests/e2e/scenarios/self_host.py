from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

from agentiux_dev_e2e_support import (
    FakeYouTrackServer,
    call_mcp,
    create_named_fixture_repo,
    git_commit,
    isolated_plugin_env,
    make_legacy_workspace_fixture,
    temporary_env,
    write_fake_bootstrap_tools,
    write_fake_host_setup_installer,
)
from agentiux_dev_lib import (
    apply_upgrade_plan,
    audit_repository,
    create_git_worktree,
    create_starter,
    create_task,
    create_workstream,
    current_workstream,
    dashboard_snapshot,
    init_workspace,
    inspect_git_state,
    install_host_requirements,
    list_git_worktrees,
    list_starter_presets,
    list_starter_runs,
    list_tasks,
    list_workstreams,
    migrate_workspace_state,
    plan_git_change,
    read_current_audit,
    read_upgrade_plan,
    repair_host_requirements,
    show_git_workflow_advice,
    show_host_setup_plan,
    show_host_support,
    show_upgrade_plan,
    suggest_branch_name,
    suggest_commit_message,
    suggest_pr_body,
    suggest_pr_title,
    workspace_paths,
)
from agentiux_dev_youtrack import (
    apply_youtrack_workstream_plan,
    connect_youtrack,
    list_youtrack_connections,
    propose_youtrack_workstream_plan,
    search_youtrack_issues,
    show_youtrack_issue_queue,
)


LEGACY_GROUP = "wave2-self-host/legacy"
AUDIT_GROUP = "wave2-self-host/audit"
STARTER_GROUP = "wave2-self-host/starter"
GIT_GROUP = "wave2-self-host/git"
YOUTRACK_GROUP = "wave2-self-host/youtrack"
HOST_GROUP = "wave2-self-host/host"


def _clone_fixture(context: ExecutionContext, group_root: Path, name: str, fixture_id: str) -> Path:
    return create_named_fixture_repo(group_root / name, context.plugin_root, fixture_id)


def _mcp_tool(context: ExecutionContext, env: dict[str, str], request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return call_mcp(
        context.plugin_root / "scripts" / "agentiux_dev_mcp.py",
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        },
        env=env,
    )


def _host_override_env(base_env: dict[str, str], group_root: Path, host_os: str) -> tuple[dict[str, str], dict[str, Path]]:
    installer_bin = group_root / "host-setup-bin"
    installer_bin.mkdir(parents=True, exist_ok=True)
    fake_installer, fake_sudo = write_fake_host_setup_installer(installer_bin, host_os)
    log_path = group_root / "host-setup.log"
    fake_node = group_root / "fake-node"
    fake_adb = group_root / "fake-android-adb"
    overrides = dict(base_env)
    overrides["AGENTIUX_DEV_HOST_SETUP_LOG"] = str(log_path)
    overrides["AGENTIUX_DEV_TOOL_OVERRIDE_NODE"] = str(fake_node)
    overrides["AGENTIUX_DEV_TOOL_OVERRIDE_ADB"] = str(fake_adb)
    if host_os == "macos":
        overrides["AGENTIUX_DEV_TOOL_OVERRIDE_BREW"] = str(fake_installer)
        overrides.pop("AGENTIUX_DEV_TOOL_OVERRIDE_APT_GET", None)
        overrides.pop("AGENTIUX_DEV_TOOL_OVERRIDE_SUDO", None)
    else:
        overrides["AGENTIUX_DEV_TOOL_OVERRIDE_APT_GET"] = str(fake_installer)
        if fake_sudo is not None:
            overrides["AGENTIUX_DEV_TOOL_OVERRIDE_SUDO"] = str(fake_sudo)
        overrides.pop("AGENTIUX_DEV_TOOL_OVERRIDE_BREW", None)
    return overrides, {
        "log_path": log_path,
        "fake_node": fake_node,
        "fake_adb": fake_adb,
    }


def _legacy_group(context: ExecutionContext) -> dict[str, Any]:
    migrate_root = context.path("wave2-self-host-legacy-migrate")
    migrate_env = isolated_plugin_env(migrate_root, context.plugin_root)
    with temporary_env(migrate_env):
        migrate_fixture = make_legacy_workspace_fixture(context.plugin_root)
        migrated = migrate_workspace_state(context.plugin_root)
        migrated_mcp = _mcp_tool(
            context,
            migrate_env,
            710,
            "migrate_workspace_state",
            {"workspacePath": str(context.plugin_root.resolve())},
        )

    dashboard_root = context.path("wave2-self-host-legacy-dashboard")
    dashboard_env = isolated_plugin_env(dashboard_root, context.plugin_root)
    with temporary_env(dashboard_env):
        dashboard_fixture = make_legacy_workspace_fixture(context.plugin_root)
        dashboard = dashboard_snapshot(context.plugin_root)
        dashboard_mcp = _mcp_tool(
            context,
            dashboard_env,
            711,
            "get_dashboard_snapshot",
            {"workspacePath": str(context.plugin_root.resolve())},
        )
    return {
        "migrate_fixture": migrate_fixture,
        "migrated": migrated,
        "migrated_mcp": migrated_mcp,
        "dashboard_fixture": dashboard_fixture,
        "dashboard": dashboard,
        "dashboard_mcp": dashboard_mcp,
    }


def _audit_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-self-host-audit")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        workspace = _clone_fixture(context, group_root, "audit", "fullstack-workspace")
        init_workspace(workspace)
        audit = audit_repository(workspace)
        current_audit = read_current_audit(workspace)
        upgrade = show_upgrade_plan(workspace)
        applied = apply_upgrade_plan(workspace, confirmed=True)
        current_upgrade = read_upgrade_plan(workspace)
        dashboard = dashboard_snapshot(workspace)
        dashboard_mcp = _mcp_tool(
            context,
            env,
            720,
            "get_dashboard_snapshot",
            {"workspacePath": str(workspace.resolve())},
        )
    return {
        "audit": audit,
        "current_audit": current_audit,
        "upgrade": upgrade,
        "applied": applied,
        "current_upgrade": current_upgrade,
        "dashboard": dashboard,
        "dashboard_mcp": dashboard_mcp,
    }


def _starter_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-self-host-starter")
    env = isolated_plugin_env(group_root, context.plugin_root)
    starter_bin = group_root / "starter-bin"
    starter_bin.mkdir(parents=True, exist_ok=True)
    write_fake_bootstrap_tools(starter_bin)
    starter_root = group_root / "starters"
    starter_root.mkdir(parents=True, exist_ok=True)
    starter_env = dict(env)
    starter_env["PATH"] = f"{starter_bin}{os.pathsep}{starter_env.get('PATH', os.environ.get('PATH', ''))}"
    with temporary_env(starter_env):
        presets = list_starter_presets()
        created_web = create_starter("next-web", starter_root, "next-web-demo")
        created_mobile = create_starter("expo-mobile", starter_root, "expo-mobile-demo")
        run_history = list_starter_runs(limit=None)
    return {
        "presets": presets,
        "created_web": created_web,
        "created_mobile": created_mobile,
        "run_history": run_history,
    }


def _git_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-self-host-git")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        repo_root = _clone_fixture(context, group_root, "git", "fullstack-workspace")
        (repo_root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        from agentiux_dev_e2e_support import completed_process

        completed_process(["git", "add", "CHANGELOG.md"], cwd=repo_root)
        git_commit(repo_root, "feat(repo): seed git suggestion history")
        init_workspace(repo_root)
        create_task(repo_root, title="Refresh repo notes", objective="Prepare a focused repo change summary.", make_current=True)
        (repo_root / "notes.md").write_text("ops note\n", encoding="utf-8")
        advice = show_git_workflow_advice(repo_root)
        suggested_commit = suggest_commit_message(repo_root, "Improve repo diagnostics", files=["notes.md"])
        suggested_branch = suggest_branch_name(repo_root, "Improve repo diagnostics", mode="task")
        suggested_pr_title = suggest_pr_title(repo_root, "Improve repo diagnostics", files=["notes.md"])
        suggested_pr_body = suggest_pr_body(repo_root, "Improve repo diagnostics", files=["notes.md"])
        create_workstream(repo_root, "Linked Worktree", kind="feature", scope_summary="Exercise linked worktree inventory in isolation.")
        worktree_plan = plan_git_change(repo_root)
        worktree_listing_before = list_git_worktrees(repo_root)
        created_worktree = create_git_worktree(
            repo_root,
            worktree_plan["suggested_worktree_path"],
            worktree_plan["suggested_branch_name"],
        )
        worktree_listing_after = list_git_worktrees(repo_root)
        linked_state = inspect_git_state(Path(created_worktree["worktree_path"]))
    return {
        "advice": advice,
        "suggested_commit": suggested_commit,
        "suggested_branch": suggested_branch,
        "suggested_pr_title": suggested_pr_title,
        "suggested_pr_body": suggested_pr_body,
        "worktree_plan": worktree_plan,
        "worktree_listing_before": worktree_listing_before,
        "created_worktree": created_worktree,
        "worktree_listing_after": worktree_listing_after,
        "linked_state": linked_state,
    }


def _youtrack_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-self-host-youtrack")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        workspace = _clone_fixture(context, group_root, "youtrack", "fullstack-workspace")
        init_workspace(workspace)
        workstream_count_before = len(list_workstreams(workspace)["items"])
        task_count_before = len(list_tasks(workspace)["items"])
        with FakeYouTrackServer() as fake_youtrack:
            connected = connect_youtrack(
                workspace,
                base_url=fake_youtrack.base_url or "",
                token=fake_youtrack.token,
                label="Primary tracker",
                connection_id="primary-tracker",
                project_scope="SL",
                default=True,
            )
            connections = list_youtrack_connections(workspace)
            paged_search = search_youtrack_issues(
                workspace,
                query_text="assignee: me",
                connection_id="primary-tracker",
                page_size=2,
                shortlist_size=2,
            )["search_session"]
            search_session = search_youtrack_issues(
                workspace,
                query_text="assignee: me",
                connection_id="primary-tracker",
                page_size=3,
                shortlist_size=3,
            )["search_session"]
            queue = show_youtrack_issue_queue(workspace, search_session_id=search_session["session_id"])
            workstream_count_after_search = len(list_workstreams(workspace)["items"])
            task_count_after_search = len(list_tasks(workspace)["items"])
            selected_issue_ids = [item["issue_id"] for item in search_session["shortlist"][:2]]
            rejected_issue_ids = [item["issue_id"] for item in search_session["shortlist"][2:]]
            proposed_plan = propose_youtrack_workstream_plan(
                workspace,
                search_session_id=search_session["session_id"],
                selected_issue_ids=selected_issue_ids,
                rejected_issue_ids=rejected_issue_ids,
                workstream_title="Wave 2 triage queue",
            )["plan"]
            applied_plan = apply_youtrack_workstream_plan(
                workspace,
                plan_id=proposed_plan["plan_id"],
                confirmed=True,
            )["plan"]
            workstream_count_before_reapply = len(list_workstreams(workspace)["items"])
            task_count_before_reapply = len(list_tasks(workspace)["items"])
            reapplied_plan = apply_youtrack_workstream_plan(
                workspace,
                plan_id=proposed_plan["plan_id"],
                confirmed=True,
            )["plan"]
            dashboard = dashboard_snapshot(workspace)
            queue_mcp = _mcp_tool(
                context,
                env,
                730,
                "show_youtrack_issue_queue",
                {
                    "workspacePath": str(workspace.resolve()),
                    "searchSessionId": search_session["session_id"],
                },
            )
    return {
        "connected": connected,
        "connections": connections,
        "paged_search": paged_search,
        "search_session": search_session,
        "queue": queue,
        "queue_mcp": queue_mcp,
        "workstream_count_before": workstream_count_before,
        "task_count_before": task_count_before,
        "workstream_count_after_search": workstream_count_after_search,
        "task_count_after_search": task_count_after_search,
        "proposed_plan": proposed_plan,
        "applied_plan": applied_plan,
        "reapplied_plan": reapplied_plan,
        "workstream_count_before_reapply": workstream_count_before_reapply,
        "task_count_before_reapply": task_count_before_reapply,
        "dashboard": dashboard,
    }


def _host_group(context: ExecutionContext) -> dict[str, Any]:
    detect_root = context.path("wave2-self-host-host-detect")
    detect_env = isolated_plugin_env(detect_root, context.plugin_root)
    with temporary_env(detect_env):
        init_workspace(context.plugin_root, force=True)
        plugin_support = show_host_support(context.plugin_root)
        host_os = plugin_support["host_os"]
    if host_os not in {"macos", "linux"}:
        raise AssertionError(f"Unsupported host for deterministic host-support e2e: {host_os}")

    plugin_root = context.path("wave2-self-host-host-plugin")
    plugin_env = isolated_plugin_env(plugin_root, context.plugin_root)
    plugin_overrides, plugin_paths = _host_override_env(plugin_env, plugin_root, host_os)
    with temporary_env(plugin_overrides):
        init_workspace(context.plugin_root, force=True)
        plugin_plan = show_host_setup_plan(context.plugin_root, requirement_ids=["mobile_verification_android"])
        plugin_plan_mcp = _mcp_tool(
            context,
            plugin_overrides,
            740,
            "show_host_setup_plan",
            {
                "workspacePath": str(context.plugin_root.resolve()),
                "requirementIds": ["mobile_verification_android"],
            },
        )
        plugin_repair = repair_host_requirements(
            context.plugin_root,
            requirement_ids=["mobile_verification_android"],
            confirmed=True,
        )
        plugin_support_after = show_host_support(context.plugin_root)

    mobile_root = context.path("wave2-self-host-host-mobile")
    mobile_env = isolated_plugin_env(mobile_root, context.plugin_root)
    mobile_overrides, mobile_paths = _host_override_env(mobile_env, mobile_root, host_os)
    with temporary_env(mobile_overrides):
        workspace = _clone_fixture(context, mobile_root, "mobile", "mobile-detox-app")
        init_workspace(workspace)
        mobile_support_before = show_host_support(workspace)
        mobile_plan = show_host_setup_plan(workspace, requirement_ids=["mobile_verification_android"])
        mobile_install = install_host_requirements(
            workspace,
            requirement_ids=["mobile_verification_android"],
            confirmed=True,
        )
        mobile_support_after_install = show_host_support(workspace)
        mobile_paths["fake_adb"].unlink()
        mobile_repair = repair_host_requirements(
            workspace,
            requirement_ids=["android_tooling"],
            confirmed=True,
        )
        mobile_support_after_repair = show_host_support(workspace)
        mobile_support_mcp = _mcp_tool(
            context,
            mobile_overrides,
            741,
            "show_host_support",
            {"workspacePath": str(workspace.resolve())},
        )
    return {
        "plugin_plan": plugin_plan,
        "plugin_plan_mcp": plugin_plan_mcp,
        "plugin_repair": plugin_repair,
        "plugin_support_after": plugin_support_after,
        "plugin_paths": plugin_paths,
        "mobile_support_before": mobile_support_before,
        "mobile_plan": mobile_plan,
        "mobile_install": mobile_install,
        "mobile_support_after_install": mobile_support_after_install,
        "mobile_repair": mobile_repair,
        "mobile_support_after_repair": mobile_support_after_repair,
        "mobile_support_mcp": mobile_support_mcp,
        "mobile_paths": mobile_paths,
    }


def _case_kernel_migrate_legacy_root(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(LEGACY_GROUP, _legacy_group)
    migrated = group["migrated"]
    migrated_mcp = group["migrated_mcp"]["result"]["structuredContent"]
    assert migrated["workspace_state"]["current_workstream_id"] == group["migrate_fixture"]["workstream_id"]
    assert Path(group["migrate_fixture"]["paths"]["workspace_state"]).exists()
    assert Path(group["migrate_fixture"]["paths"]["workstreams_index"]).exists()
    assert Path(group["migrate_fixture"]["paths"]["tasks_index"]).exists()
    assert migrated_mcp["workspace_state"]["current_workstream_id"] == group["migrate_fixture"]["workstream_id"]
    return {"workstream_id": group["migrate_fixture"]["workstream_id"]}


def _case_audit_repository_upgrade_plan(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(AUDIT_GROUP, _audit_group)
    assert group["audit"]["initialized"] is True
    assert group["audit"]["gaps"]
    assert group["upgrade"]["status"] == "draft"
    assert group["applied"]["status"] == "applied"
    assert group["applied"]["created_task_ids"]
    return {
        "audit_id": group["audit"]["audit_id"],
        "plan_id": group["upgrade"]["plan_id"],
        "created_task_count": len(group["applied"]["created_task_ids"]),
    }


def _case_repository_audit_readback_and_current_snapshots(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(AUDIT_GROUP, _audit_group)
    dashboard = group["dashboard"]["workspace_cockpit"]["diagnostics"]
    dashboard_mcp = group["dashboard_mcp"]["result"]["structuredContent"]["workspace_cockpit"]["diagnostics"]
    assert group["current_audit"]["audit_id"] == group["audit"]["audit_id"]
    assert group["current_upgrade"]["plan_id"] == group["upgrade"]["plan_id"]
    assert dashboard["audit"]["audit_id"] == group["audit"]["audit_id"]
    assert dashboard["upgrade_plan"]["plan_id"] == group["upgrade"]["plan_id"]
    assert dashboard_mcp["audit"]["audit_id"] == group["audit"]["audit_id"]
    assert dashboard_mcp["upgrade_plan"]["plan_id"] == group["upgrade"]["plan_id"]
    return {
        "audit_gap_count": len(group["current_audit"]["gaps"]),
        "created_task_count": len(group["current_upgrade"]["created_task_ids"]),
    }


def _case_starter_presets_create_run(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(STARTER_GROUP, _starter_group)
    web_project_root = Path(group["created_web"]["project_root"])
    starter_paths = workspace_paths(web_project_root)
    assert any(item["preset_id"] == "next-web" for item in group["presets"]["presets"])
    assert group["created_web"]["status"] == "passed"
    assert Path(group["created_web"]["stdout_log_path"]).exists()
    assert not Path(starter_paths["workspace_state"]).exists()
    assert starter_paths["verification_recipes"] == ""
    return {
        "preset_count": len(group["presets"]["presets"]),
        "project_root": group["created_web"]["project_root"],
    }


def _case_starter_presets_readback_and_run_history(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(STARTER_GROUP, _starter_group)
    created_roots = {group["created_web"]["project_root"], group["created_mobile"]["project_root"]}
    recent_roots = {run["project_root"] for run in group["run_history"]["runs"][:2]}
    assert group["run_history"]["run_count"] >= 2
    assert created_roots.issubset({run["project_root"] for run in group["run_history"]["runs"]})
    assert recent_roots
    return {
        "run_count": group["run_history"]["run_count"],
        "recent_project_roots": sorted(recent_roots),
    }


def _case_git_suggestion_surfaces(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GIT_GROUP, _git_group)
    assert group["advice"]["commit_policy"]["recommended_style"] == "conventional"
    assert group["suggested_commit"]["suggested_message"].startswith("feat(")
    assert group["suggested_branch"]["suggested_branch_name"].startswith("task/")
    assert group["suggested_pr_title"]["suggested_pr_title"]
    assert "## Summary" in group["suggested_pr_body"]["suggested_pr_body"]
    return {
        "branch_name": group["suggested_branch"]["suggested_branch_name"],
        "pr_title": group["suggested_pr_title"]["suggested_pr_title"],
    }


def _case_git_worktree_inventory_and_create(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GIT_GROUP, _git_group)
    assert group["worktree_listing_before"]["worktree_count"] == 1
    assert group["worktree_listing_after"]["worktree_count"] == 2
    assert group["created_worktree"]["worktree_state"]["worktree_count"] == 2
    assert group["linked_state"]["worktree"]["is_linked_worktree"] is True
    return {
        "worktree_path": group["created_worktree"]["worktree_path"],
        "branch_name": group["worktree_plan"]["suggested_branch_name"],
    }


def _case_youtrack_connection_search_queue(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(YOUTRACK_GROUP, _youtrack_group)
    serialized = json.dumps(group["connections"])
    assert group["connected"]["connection"]["status"] == "connected"
    assert group["queue"]["search_session"]["session_id"] == group["search_session"]["session_id"]
    assert group["queue_mcp"]["result"]["structuredContent"]["search_session"]["session_id"] == group["search_session"]["session_id"]
    assert group["workstream_count_before"] == group["workstream_count_after_search"]
    assert group["task_count_before"] == group["task_count_after_search"]
    assert "perm:test-token" not in serialized
    return {
        "result_count": group["search_session"]["result_count"],
        "queue_session_id": group["queue"]["search_session"]["session_id"],
    }


def _case_youtrack_search_pagination_and_triage(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(YOUTRACK_GROUP, _youtrack_group)
    proposed = group["proposed_plan"]
    shortlist_issue_ids = [item["issue_id"] for item in group["search_session"]["shortlist"]]
    assert group["paged_search"]["result_count"] == 3
    assert group["paged_search"]["page_cursor"]["has_more"] is True
    assert len(group["paged_search"]["shortlist_page"]["items"]) == 2
    assert shortlist_issue_ids == ["SL-4591", "SL-4593", "SL-4592"]
    assert proposed["selected_issue_ids"] == shortlist_issue_ids[:2]
    assert proposed["selection_analysis"]["ordered_issue_ids"] == shortlist_issue_ids[:2]
    assert proposed["rejected_issue_ids"] == shortlist_issue_ids[2:]
    return {
        "ordered_issue_ids": proposed["selection_analysis"]["ordered_issue_ids"],
        "rejected_issue_ids": proposed["rejected_issue_ids"],
    }


def _case_youtrack_plan_apply_idempotent(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(YOUTRACK_GROUP, _youtrack_group)
    dashboard_youtrack = group["dashboard"]["workspace_cockpit"]["integrations"]["youtrack"]
    assert group["applied_plan"]["status"] == "applied"
    assert group["reapplied_plan"]["applied_workstream_id"] == group["applied_plan"]["applied_workstream_id"]
    assert group["applied_plan"]["created_task_ids"]
    assert dashboard_youtrack["current_plan"]["plan_id"] == group["proposed_plan"]["plan_id"]
    return {
        "plan_id": group["proposed_plan"]["plan_id"],
        "applied_workstream_id": group["applied_plan"]["applied_workstream_id"],
    }


def _case_host_support_plan_repair(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(HOST_GROUP, _host_group)
    plugin_plan_mcp = group["plugin_plan_mcp"]["result"]["structuredContent"]
    assert group["plugin_plan"]["status"] == "needs_confirmation"
    assert plugin_plan_mcp["status"] == "needs_confirmation"
    assert group["plugin_repair"]["status"] == "completed"
    assert group["plugin_paths"]["fake_node"].exists()
    assert group["plugin_paths"]["fake_adb"].exists()
    assert group["plugin_paths"]["log_path"].exists()
    assert group["plugin_support_after"]["toolchain_capabilities"]["mobile_verification_android"]["available"] is True
    return {
        "repair_status": group["plugin_repair"]["status"],
        "host_os": group["plugin_support_after"]["host_os"],
    }


def _case_host_support_install_repair_readback(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(HOST_GROUP, _host_group)
    mobile_support_mcp = group["mobile_support_mcp"]["result"]["structuredContent"]
    assert group["mobile_plan"]["status"] == "needs_confirmation"
    assert group["mobile_install"]["status"] == "completed"
    assert group["mobile_support_after_install"]["toolchain_capabilities"]["mobile_verification_android"]["available"] is True
    assert group["mobile_repair"]["status"] == "completed"
    assert group["mobile_support_after_repair"]["toolchain_capabilities"]["android_tooling"]["available"] is True
    assert mobile_support_mcp["toolchain_capabilities"]["android_tooling"]["available"] is True
    return {
        "install_status": group["mobile_install"]["status"],
        "repair_status": group["mobile_repair"]["status"],
    }


def _case_dashboard_legacy_workspace_bootstrap(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(LEGACY_GROUP, _legacy_group)
    dashboard = group["dashboard"]
    dashboard_mcp = group["dashboard_mcp"]["result"]["structuredContent"]
    assert dashboard["schema_version"] == 2
    assert dashboard["workspace_cockpit"]["workspace_path"] == str(context.plugin_root.resolve())
    assert dashboard["workspace_cockpit"]["plan"]["current_workstream"]["workstream_id"] == group["dashboard_fixture"]["workstream_id"]
    assert dashboard_mcp["workspace_cockpit"]["plan"]["current_workstream"]["workstream_id"] == group["dashboard_fixture"]["workstream_id"]
    return {
        "workstream_id": group["dashboard_fixture"]["workstream_id"],
        "workspace_path": dashboard["workspace_cockpit"]["workspace_path"],
    }


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition("kernel-migrate-legacy-root", "source-plugin", ("wave2-self-host", "wave2-full-local"), ("kernel", "migration"), _case_kernel_migrate_legacy_root),
        ScenarioDefinition("audit-repository-upgrade-plan", "fullstack-workspace", ("wave2-self-host", "wave2-full-local"), ("repository", "audit"), _case_audit_repository_upgrade_plan),
        ScenarioDefinition("repository-audit-readback-and-current-snapshots", "fullstack-workspace", ("wave2-self-host", "wave2-full-local"), ("repository", "readback"), _case_repository_audit_readback_and_current_snapshots),
        ScenarioDefinition("starter-presets-create-run", "none", ("wave2-self-host", "wave2-full-local"), ("starter", "create"), _case_starter_presets_create_run),
        ScenarioDefinition("starter-presets-readback-and-run-history", "none", ("wave2-self-host", "wave2-full-local"), ("starter", "history"), _case_starter_presets_readback_and_run_history),
        ScenarioDefinition("git-suggestion-surfaces", "fullstack-workspace", ("wave2-self-host", "wave2-full-local"), ("git", "suggestions"), _case_git_suggestion_surfaces),
        ScenarioDefinition("git-worktree-inventory-and-create", "fullstack-workspace", ("wave2-self-host", "wave2-full-local"), ("git", "worktree"), _case_git_worktree_inventory_and_create),
        ScenarioDefinition("youtrack-connection-search-queue", "fullstack-workspace", ("wave2-self-host", "wave2-full-local"), ("youtrack", "queue"), _case_youtrack_connection_search_queue),
        ScenarioDefinition("youtrack-search-pagination-and-triage", "fullstack-workspace", ("wave2-self-host", "wave2-full-local"), ("youtrack", "pagination"), _case_youtrack_search_pagination_and_triage),
        ScenarioDefinition("youtrack-plan-apply-idempotent", "fullstack-workspace", ("wave2-self-host", "wave2-full-local"), ("youtrack", "apply"), _case_youtrack_plan_apply_idempotent),
        ScenarioDefinition("host-support-plan-repair", "source-plugin", ("wave2-self-host", "wave2-full-local"), ("host", "repair"), _case_host_support_plan_repair),
        ScenarioDefinition("host-support-install-repair-readback", "mobile-detox-app", ("wave2-self-host", "wave2-full-local"), ("host", "install"), _case_host_support_install_repair_readback),
        ScenarioDefinition("dashboard-legacy-workspace-bootstrap", "source-plugin", ("wave2-self-host", "wave2-full-local"), ("dashboard", "legacy"), _case_dashboard_legacy_workspace_bootstrap),
    ]
    return {case.case_id: case for case in cases}
