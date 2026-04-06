from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

from agentiux_dev_e2e_support import FakeYouTrackServer, git_commit, seed_workspace, temporary_env, write_fake_bootstrap_tools
from agentiux_dev_lib import (
    create_git_branch,
    create_git_commit,
    create_git_worktree,
    create_starter,
    create_task,
    init_workspace,
    inspect_git_state,
    list_git_worktrees,
    list_tasks,
    list_workstreams,
    plan_git_change,
    show_git_workflow_advice,
    stage_git_files,
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
    remove_youtrack_connection,
    search_youtrack_issues,
    show_youtrack_issue_queue,
    test_youtrack_connection,
    update_youtrack_connection,
)


GIT_GROUP = "integration-core/git"
STARTER_GROUP = "integration-core/starter"
YOUTRACK_GROUP = "integration-core/youtrack"


def _git_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("integration-git")
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        commit_repo = group_root / "commit-style-repo"
        commit_repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init"], cwd=commit_repo, check=True, capture_output=True, text=True)
        (commit_repo / "README.md").write_text("# Commit Style\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=commit_repo, check=True, capture_output=True, text=True)
        git_commit(commit_repo, "feat(dashboard): add overview panel")
        (commit_repo / "dashboard.txt").write_text("panel\n", encoding="utf-8")
        subprocess.run(["git", "add", "dashboard.txt"], cwd=commit_repo, check=True, capture_output=True, text=True)
        git_commit(commit_repo, "fix(dashboard): align status badge")
        advice = show_git_workflow_advice(commit_repo)
        suggested_commit = suggest_commit_message(commit_repo, "Improve dashboard log view", files=["plugins/agentiux-dev/dashboard/app.js"])
        suggested_branch = suggest_branch_name(commit_repo, "Improve dashboard log view", mode="task")
        suggested_pr_title = suggest_pr_title(commit_repo, "Improve dashboard log view", files=["plugins/agentiux-dev/dashboard/app.js"])
        suggested_pr_body = suggest_pr_body(commit_repo, "Improve dashboard log view", files=["plugins/agentiux-dev/dashboard/app.js"])

        git_flow_repo = group_root / "git-flow-repo"
        git_flow_repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "AgentiUX"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "agentiux@example.com"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        (git_flow_repo / "README.md").write_text("# Git Flow Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "chore: bootstrap repo"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        init_workspace(git_flow_repo)
        create_task(git_flow_repo, title="Update git note", objective="Add an operational note for the repository.")
        (git_flow_repo / "notes.md").write_text("ops note\n", encoding="utf-8")
        git_state_before = inspect_git_state(git_flow_repo)
        git_plan = plan_git_change(git_flow_repo)
        branch_result = create_git_branch(git_flow_repo, git_plan["suggested_branch_name"])
        stage_result = stage_git_files(git_flow_repo, ["notes.md"])
        commit_result = create_git_commit(git_flow_repo, git_plan["suggested_commit_message"])

        worktree_repo = group_root / "worktree-repo"
        worktree_repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init"], cwd=worktree_repo, check=True, capture_output=True, text=True)
        (worktree_repo / "README.md").write_text("# Worktree Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=worktree_repo, check=True, capture_output=True, text=True)
        git_commit(worktree_repo, "chore: bootstrap worktree repo")
        init_workspace(worktree_repo)
        from agentiux_dev_lib import create_workstream

        create_workstream(worktree_repo, title="Dashboard revamp", scope_summary="Ship the dashboard revamp.")
        worktree_plan = plan_git_change(worktree_repo)
        worktree_listing = list_git_worktrees(worktree_repo)
        created_worktree = create_git_worktree(
            worktree_repo,
            worktree_plan["suggested_worktree_path"],
            worktree_plan["suggested_branch_name"],
        )
        linked_state = inspect_git_state(Path(created_worktree["worktree_path"]))
    return {
        "advice": advice,
        "suggested_commit": suggested_commit,
        "suggested_branch": suggested_branch,
        "suggested_pr_title": suggested_pr_title,
        "suggested_pr_body": suggested_pr_body,
        "git_state_before": git_state_before,
        "git_plan": git_plan,
        "branch_result": branch_result,
        "stage_result": stage_result,
        "commit_result": commit_result,
        "worktree_plan": worktree_plan,
        "worktree_listing": worktree_listing,
        "created_worktree": created_worktree,
        "linked_state": linked_state,
    }


def _starter_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("integration-starter")
    previous_path = os.environ.get("PATH", "")
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
        "PATH": previous_path,
    }
    starter_bin = group_root / "starter-bin"
    starter_bin.mkdir(exist_ok=True)
    write_fake_bootstrap_tools(starter_bin)
    starter_root = group_root / "starters"
    starter_root.mkdir(exist_ok=True)
    existing_project = starter_root / "existing-demo"
    existing_project.mkdir(exist_ok=True)
    with temporary_env({**env, "PATH": f"{starter_bin}{os.pathsep}{previous_path}"}):
        try:
            create_starter("next-web", starter_root, "existing-demo")
        except ValueError as exc:
            starter_error = str(exc)
        else:
            raise AssertionError("Expected starter creation to fail for existing destination")
        starter_workspace_paths = workspace_paths(existing_project)
    return {
        "starter_error": starter_error,
        "workspace_state_path": starter_workspace_paths["workspace_state"],
        "verification_recipes_path": starter_workspace_paths["verification_recipes"],
    }


def _youtrack_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("integration-youtrack")
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        workspace = group_root / "workspace"
        workspace.mkdir(exist_ok=True)
        seed_workspace(workspace)
        subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True, text=True)
        git_commit(workspace, "feat: bootstrap youtrack workspace")
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
            tested = test_youtrack_connection(workspace, "primary-tracker")
            updated = update_youtrack_connection(
                workspace,
                "primary-tracker",
                label="Primary tracker updated",
                default=True,
                test_connection=False,
            )
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
            proposed_plan = propose_youtrack_workstream_plan(
                workspace,
                search_session_id=search_session["session_id"],
                selected_issue_ids=[item["issue_id"] for item in search_session["shortlist"][:3]],
                workstream_title="YouTrack checkout queue",
            )["plan"]
            try:
                apply_youtrack_workstream_plan(workspace, plan_id=proposed_plan["plan_id"], confirmed=False)
            except ValueError as exc:
                unconfirmed_error = str(exc)
            else:
                raise AssertionError("Expected YouTrack plan apply without confirmation to fail")
            applied_plan = apply_youtrack_workstream_plan(workspace, plan_id=proposed_plan["plan_id"], confirmed=True)["plan"]
            secondary = connect_youtrack(
                workspace,
                base_url=fake_youtrack.base_url or "",
                token=fake_youtrack.token,
                label="Secondary tracker",
                connection_id="secondary-tracker",
                project_scope="SL",
                default=False,
            )
            secondary_test = test_youtrack_connection(workspace, "secondary-tracker")
            secondary_update = update_youtrack_connection(
                workspace,
                "secondary-tracker",
                label="Secondary tracker updated",
                default=True,
                test_connection=False,
            )
            secondary_remove = remove_youtrack_connection(workspace, "secondary-tracker")
            connections_after = list_youtrack_connections(workspace)
    return {
        "connected": connected,
        "tested": tested,
        "updated": updated,
        "paged_search": paged_search,
        "search_session": search_session,
        "queue": queue,
        "workstream_count_before": workstream_count_before,
        "task_count_before": task_count_before,
        "workstream_count_after_search": workstream_count_after_search,
        "task_count_after_search": task_count_after_search,
        "proposed_plan": proposed_plan,
        "unconfirmed_error": unconfirmed_error,
        "applied_plan": applied_plan,
        "secondary": secondary,
        "secondary_test": secondary_test,
        "secondary_update": secondary_update,
        "secondary_remove": secondary_remove,
        "connections_after": connections_after,
    }


def _case_youtrack_connection_lifecycle_and_test(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(YOUTRACK_GROUP, _youtrack_group)
    assert group["connected"]["connection"]["status"] == "connected"
    assert group["tested"]["connection"]["status"] == "connected"
    assert group["updated"]["connection"]["label"] == "Primary tracker updated"
    assert group["proposed_plan"]["status"] == "needs_user_confirmation"
    assert "confirmed=True" in group["unconfirmed_error"]
    assert group["applied_plan"]["status"] == "applied"
    assert group["secondary"]["connection"]["connection_id"] == "secondary-tracker"
    assert group["secondary_test"]["connection"]["status"] == "connected"
    assert group["secondary_update"]["connection"]["label"] == "Secondary tracker updated"
    assert group["secondary_remove"]["removed_connection_id"] == "secondary-tracker"
    assert group["connections_after"]["default_connection_id"] == "primary-tracker"
    assert len(group["connections_after"]["items"]) == 1
    return {
        "primary_connection_id": group["connected"]["connection"]["connection_id"],
        "applied_plan_id": group["applied_plan"]["plan_id"],
        "remaining_connections": len(group["connections_after"]["items"]),
    }


def _case_youtrack_search_read_only_no_side_effects(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(YOUTRACK_GROUP, _youtrack_group)
    assert group["paged_search"]["result_count"] == 3
    assert group["search_session"]["shortlist"]
    assert group["queue"]["search_session"]["session_id"] == group["search_session"]["session_id"]
    assert group["workstream_count_before"] == group["workstream_count_after_search"]
    assert group["task_count_before"] == group["task_count_after_search"]
    return {
        "result_count": group["search_session"]["result_count"],
        "workstream_count_after_search": group["workstream_count_after_search"],
        "task_count_after_search": group["task_count_after_search"],
    }


def _case_starter_failure_no_implicit_init(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(STARTER_GROUP, _starter_group)
    assert "already exists" in group["starter_error"]
    assert not Path(group["workspace_state_path"]).exists()
    assert group["verification_recipes_path"] == ""
    return {
        "error": group["starter_error"],
        "workspace_state_exists": Path(group["workspace_state_path"]).exists(),
    }


def _case_git_advice_branch_stage_commit(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GIT_GROUP, _git_group)
    assert group["advice"]["commit_policy"]["recommended_style"] == "conventional"
    assert group["suggested_commit"]["suggested_message"].startswith("feat(dashboard):")
    assert group["suggested_branch"]["suggested_branch_name"].startswith("task/")
    assert "## Summary" in group["suggested_pr_body"]["suggested_pr_body"]
    assert group["git_state_before"]["untracked_files"] == ["notes.md"]
    assert group["git_plan"]["branch_action"] == "create_and_switch"
    assert group["branch_result"]["status"] == "created"
    assert "notes.md" in group["stage_result"]["git_state"]["staged_files"]
    assert group["commit_result"]["commit_hash"]
    assert group["worktree_plan"]["worktree_action"] == "create_linked_worktree"
    assert group["worktree_listing"]["worktree_count"] == 1
    assert group["created_worktree"]["worktree_state"]["worktree_count"] == 2
    assert group["linked_state"]["worktree"]["is_linked_worktree"] is True
    return {
        "commit_hash": group["commit_result"]["commit_hash"],
        "branch_name": group["git_plan"]["suggested_branch_name"],
        "worktree_branch_name": group["worktree_plan"]["suggested_branch_name"],
    }


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition("youtrack-connection-lifecycle-and-test", "source-plugin", ("integration-core", "core-full-local"), ("integration", "youtrack"), _case_youtrack_connection_lifecycle_and_test),
        ScenarioDefinition("youtrack-search-read-only-no-side-effects", "source-plugin", ("integration-core", "core-full-local"), ("integration", "youtrack", "search"), _case_youtrack_search_read_only_no_side_effects),
        ScenarioDefinition("starter-failure-no-implicit-init", "source-plugin", ("integration-core", "core-full-local"), ("integration", "starter"), _case_starter_failure_no_implicit_init),
        ScenarioDefinition("git-advice-branch-stage-commit", "source-plugin", ("integration-core", "core-full-local"), ("integration", "git"), _case_git_advice_branch_stage_commit),
    ]
    return {case.case_id: case for case in cases}
