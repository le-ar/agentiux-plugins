from __future__ import annotations

from pathlib import Path
from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

from agentiux_dev_e2e_support import (
    call_mcp,
    confirm_stage_plan,
    read_json_file,
    seed_workspace,
    stage_definition,
    temporary_env,
    write_json_file,
)
from agentiux_dev_lib import (
    create_task,
    create_workstream,
    cache_reference_preview,
    current_task,
    current_workstream,
    dashboard_snapshot,
    detect_workspace,
    get_active_brief,
    get_state_paths,
    init_workspace,
    list_design_handoffs,
    list_reference_boards,
    list_stages,
    list_tasks,
    list_workspaces,
    list_workstreams,
    plugin_stats,
    preview_workspace_init,
    read_design_brief,
    read_design_handoff,
    read_reference_board,
    read_stage_register,
    read_task,
    read_workspace_detail,
    read_workspace_state,
    set_active_brief,
    switch_task,
    switch_workstream,
    workflow_advice,
    write_design_brief,
    write_design_handoff,
    write_reference_board,
)


CORE_GROUP = "wave2-foundation/core"
DESIGN_GROUP = "wave2-foundation/design"
PNG_PIXEL = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
    "0000000A49444154789C6360000002000154A24F5D0000000049454E44AE426082"
)


def _core_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-foundation-core")
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        uninitialized_workspace = group_root / "uninitialized-workspace"
        uninitialized_workspace.mkdir(exist_ok=True)
        seed_workspace(uninitialized_workspace)
        detect_uninitialized = detect_workspace(uninitialized_workspace)
        preview_init = preview_workspace_init(uninitialized_workspace)
        workflow_large = workflow_advice(uninitialized_workspace, "Implement a checkout feature across web and backend")
        workflow_greenfield = workflow_advice(uninitialized_workspace, "Build a new Expo mobile app from scratch")

        state_workspace = group_root / "state-workspace"
        state_workspace.mkdir(exist_ok=True)
        seed_workspace(state_workspace)
        init_result = init_workspace(state_workspace)
        state_paths = get_state_paths(state_workspace)
        workspace_state = read_workspace_state(state_workspace)

        secondary_workspace = group_root / "secondary-workspace"
        secondary_workspace.mkdir(exist_ok=True)
        seed_workspace(secondary_workspace)
        secondary_init = init_workspace(secondary_workspace)
        workspaces_listing = list_workspaces()
        plugin_stats_payload = plugin_stats()
        detail_payload = read_workspace_detail(state_workspace)
        dashboard_payload = dashboard_snapshot(state_workspace)
        mcp_detail = call_mcp(
            context.plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 201,
                "method": "tools/call",
                "params": {
                    "name": "get_workspace_detail",
                    "arguments": {"workspacePath": str(state_workspace.resolve())},
                },
            },
            env=env,
        )
        mcp_dashboard = call_mcp(
            context.plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 202,
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_snapshot",
                    "arguments": {"workspacePath": str(state_workspace.resolve())},
                },
            },
            env=env,
        )

        task_advice_workspace = group_root / "task-advice-workspace"
        task_advice_workspace.mkdir(exist_ok=True)
        seed_workspace(task_advice_workspace)
        init_workspace(task_advice_workspace)
        small_first = workflow_advice(task_advice_workspace, "Fix CTA spacing in the hero section", auto_create=True)
        small_reuse = workflow_advice(task_advice_workspace, "Fix CTA spacing in the hero section", auto_create=True)
        task_advice_paths = get_state_paths(task_advice_workspace)
        task_advice_state_path = Path(task_advice_paths["paths"]["workspace_state"])
        stale_state_payload = read_json_file(task_advice_state_path)
        stale_state_payload["current_task_id"] = "missing-task"
        write_json_file(task_advice_state_path, stale_state_payload)
        small_stale_recovery = workflow_advice(task_advice_workspace, "Fix CTA spacing in the hero section", auto_create=True)
        task_advice_current = current_task(task_advice_workspace)
        task_advice_tasks = list_tasks(task_advice_workspace)

        readback_workspace = group_root / "readback-workspace"
        readback_workspace.mkdir(exist_ok=True)
        seed_workspace(readback_workspace)
        init_workspace(readback_workspace)
        alpha_workstream = create_workstream(
            readback_workspace,
            "Delivery Alpha",
            kind="feature",
            scope_summary="Track the first delivery slice and its stage state.",
        )
        beta_workstream = create_workstream(
            readback_workspace,
            "Delivery Beta",
            kind="feature",
            scope_summary="Track the second delivery slice for pointer switching.",
        )
        alpha_workstream_id = alpha_workstream["created_workstream_id"]
        beta_workstream_id = beta_workstream["created_workstream_id"]
        switch_workstream(readback_workspace, alpha_workstream_id)
        confirmed_register = confirm_stage_plan(
            readback_workspace,
            [
                stage_definition(
                    "scope-lock",
                    "Scope Lock",
                    "Lock the current slice before implementation.",
                    ["scope-lock.1-confirm-scope"],
                ),
                stage_definition(
                    "implementation",
                    "Implementation",
                    "Implement the approved slice.",
                    ["implementation.1-apply-change"],
                ),
            ],
            workstream_id=alpha_workstream_id,
        )
        generated_workstream_brief = get_active_brief(readback_workspace)
        linked_task = create_task(
            readback_workspace,
            title="Verify delivery alpha",
            objective="Run delivery alpha verification and update the brief.",
            linked_workstream_id=alpha_workstream_id,
            make_current=True,
            hydrate_task=False,
            include_task_listing=False,
        )
        background_task = create_task(
            readback_workspace,
            title="Document delivery beta",
            objective="Prepare the follow-up note for beta.",
            linked_workstream_id=beta_workstream_id,
            make_current=False,
            hydrate_task=False,
            include_task_listing=False,
        )
        active_task_id = linked_task["created_task_id"]
        read_task_payload = read_task(readback_workspace, task_id=active_task_id)
        set_active_brief(readback_workspace, "# TaskBrief\n\nTrack the delivery alpha follow-up.\n")
        pre_close_current_task = current_task(readback_workspace)
        pre_close_task_listing = list_tasks(readback_workspace)
        pre_close_workstream_listing = list_workstreams(readback_workspace)
        pre_close_current_workstream = current_workstream(readback_workspace)
        readback_dashboard = dashboard_snapshot(readback_workspace)
        stages_payload = list_stages(readback_workspace, workstream_id=alpha_workstream_id)
        register_payload = read_stage_register(readback_workspace, workstream_id=alpha_workstream_id)
        manual_task_brief = get_active_brief(readback_workspace)
        closed_task = create_task(
            readback_workspace,
            title="Close delivery alpha",
            objective="Persist a verification summary on closeout.",
            linked_workstream_id=alpha_workstream_id,
            make_current=True,
            hydrate_task=False,
            include_task_listing=False,
        )
        set_active_brief(readback_workspace, "# TaskBrief\n\nClose delivery alpha after verification.\n")
        close_summary = {
            "status": "completed",
            "summary": "Deterministic verification passed and rollout notes were captured.",
        }
        from agentiux_dev_lib import close_task

        closed_task_payload = close_task(readback_workspace, verification_summary=close_summary)
        current_task_after_close = current_task(readback_workspace)
        switch_workstream(readback_workspace, beta_workstream_id)
        switched_workstream = current_workstream(readback_workspace)
    return {
        "detect_uninitialized": detect_uninitialized,
        "preview_init": preview_init,
        "workflow_large": workflow_large,
        "workflow_greenfield": workflow_greenfield,
        "init_result": init_result,
        "state_paths": state_paths,
        "workspace_state": workspace_state,
        "secondary_init": secondary_init,
        "workspaces_listing": workspaces_listing,
        "plugin_stats": plugin_stats_payload,
        "detail": detail_payload,
        "dashboard": dashboard_payload,
        "mcp_detail": mcp_detail,
        "mcp_dashboard": mcp_dashboard,
        "small_first": small_first,
        "small_reuse": small_reuse,
        "small_stale_recovery": small_stale_recovery,
        "task_advice_current": task_advice_current,
        "task_advice_tasks": task_advice_tasks,
        "alpha_workstream": alpha_workstream,
        "beta_workstream": beta_workstream,
        "confirmed_register": confirmed_register,
        "generated_workstream_brief": generated_workstream_brief,
        "linked_task": linked_task,
        "background_task": background_task,
        "read_task_payload": read_task_payload,
        "pre_close_current_task": pre_close_current_task,
        "pre_close_task_listing": pre_close_task_listing,
        "pre_close_workstream_listing": pre_close_workstream_listing,
        "pre_close_current_workstream": pre_close_current_workstream,
        "readback_dashboard": readback_dashboard,
        "stages_payload": stages_payload,
        "register_payload": register_payload,
        "manual_task_brief": manual_task_brief,
        "closed_task_payload": closed_task_payload,
        "current_task_after_close": current_task_after_close,
        "switched_workstream": switched_workstream,
    }


def _design_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-foundation-design")
    workspace = group_root / "workspace"
    workspace.mkdir(exist_ok=True)
    seed_workspace(workspace)
    preview_source = group_root / "board-preview.png"
    preview_source.write_bytes(PNG_PIXEL)
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        init_workspace(workspace)
        design_workstream = create_workstream(
            workspace,
            "Design System Refresh",
            kind="feature",
            scope_summary="Persist design brief, boards, handoffs, and readback pointers.",
        )
        workstream_id = design_workstream["created_workstream_id"]
        brief_payload = write_design_brief(
            workspace,
            {
                "summary": "Refresh the component system and cockpit boards.",
                "platform": "web",
                "status": "approved",
                "semantic_summary": {"status": "ready"},
                "testability_summary": {"status": "ready"},
            },
            workstream_id=workstream_id,
        )
        read_brief = read_design_brief(workspace, workstream_id=workstream_id)
        cached_preview = cache_reference_preview(workspace, str(preview_source), candidate_id="hero-board", workstream_id=workstream_id)
        board_one = write_reference_board(
            workspace,
            {
                "title": "Hero Board",
                "platform": "web",
                "candidates": [
                    {
                        "id": "hero-board",
                        "title": "Hero board",
                        "cached_preview_source_path": str(preview_source),
                    }
                ],
            },
            board_id="hero-board",
            make_current=False,
            workstream_id=workstream_id,
        )
        board_two = write_reference_board(
            workspace,
            {
                "title": "Cockpit Board",
                "platform": "web",
                "candidates": [{"id": "cockpit-board", "title": "Cockpit board"}],
            },
            board_id="cockpit-board",
            make_current=True,
            workstream_id=workstream_id,
        )
        boards_listing = list_reference_boards(workspace, workstream_id=workstream_id)
        current_board = read_reference_board(workspace, workstream_id=workstream_id)
        explicit_board = read_reference_board(workspace, board_id="hero-board", workstream_id=workstream_id)

        handoff_one = write_design_handoff(
            workspace,
            {
                "platform": "web",
                "status": "draft",
                "summary": "Initial component migration handoff.",
            },
            handoff_id="handoff-alpha",
            make_current=False,
            workstream_id=workstream_id,
        )
        handoff_two = write_design_handoff(
            workspace,
            {
                "platform": "web",
                "status": "ready",
                "summary": "Final cockpit migration handoff.",
            },
            handoff_id="handoff-beta",
            make_current=True,
            workstream_id=workstream_id,
        )
        handoffs_listing = list_design_handoffs(workspace, workstream_id=workstream_id)
        current_handoff = read_design_handoff(workspace, workstream_id=workstream_id)
        explicit_handoff = read_design_handoff(workspace, handoff_id="handoff-alpha", workstream_id=workstream_id)
        design_dashboard = dashboard_snapshot(workspace)
    return {
        "brief_payload": brief_payload,
        "read_brief": read_brief,
        "cached_preview": cached_preview,
        "board_one": board_one,
        "board_two": board_two,
        "boards_listing": boards_listing,
        "current_board": current_board,
        "explicit_board": explicit_board,
        "handoff_one": handoff_one,
        "handoff_two": handoff_two,
        "handoffs_listing": handoffs_listing,
        "current_handoff": current_handoff,
        "explicit_handoff": explicit_handoff,
        "design_dashboard": design_dashboard,
    }


def _case_kernel_detect_uninitialized(context: ExecutionContext) -> dict[str, Any]:
    payload = context.group(CORE_GROUP, _core_group)["detect_uninitialized"]
    assert "workspace-kernel" in payload["selected_profiles"]
    assert "mobile-platform" in payload["selected_profiles"]
    assert "backend-platform" in payload["selected_profiles"]
    assert payload["repo_maturity"]["mode"] == "scaffold"
    assert payload["plugin_platform"]["enabled"] is False
    return {
        "workspace_path": payload["workspace_path"],
        "profile_count": len(payload["selected_profiles"]),
        "detected_stack_count": len(payload["detected_stacks"]),
    }


def _case_kernel_preview_init(context: ExecutionContext) -> dict[str, Any]:
    payload = context.group(CORE_GROUP, _core_group)["preview_init"]
    assert payload["already_initialized"] is False
    assert payload["must_confirm_before_write"] is True
    assert payload["repo_maturity"]["mode"] == "scaffold"
    assert payload["paths"]["workstreams_index"].endswith("workstreams/index.json")
    return {
        "already_initialized": payload["already_initialized"],
        "selected_profiles": payload["selected_profiles"],
    }


def _case_kernel_init_workspace(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CORE_GROUP, _core_group)
    payload = group["init_result"]
    assert payload["workspace_state"]["schema_version"] == group["workspace_state"]["schema_version"]
    assert payload["workspace_state"]["workspace_mode"] == "workspace"
    assert Path(group["state_paths"]["paths"]["workspace_state"]).exists()
    assert Path(group["state_paths"]["paths"]["workstreams_index"]).exists()
    assert Path(group["state_paths"]["paths"]["tasks_index"]).exists()
    return {
        "workspace_path": payload["workspace_state"]["workspace_path"],
        "schema_version": payload["workspace_state"]["schema_version"],
    }


def _case_workspace_list_and_detail(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CORE_GROUP, _core_group)
    listing = group["workspaces_listing"]
    detail = group["detail"]
    stats = group["plugin_stats"]
    assert listing["workspace_count"] >= 2
    assert detail["summary"]["workspace_path"] == group["workspace_state"]["workspace_path"]
    assert stats["workspace_count"] == listing["workspace_count"]
    assert stats["plugin_platform_workspaces"] >= 0
    return {
        "workspace_count": listing["workspace_count"],
        "detail_workspace_path": detail["summary"]["workspace_path"],
    }


def _case_kernel_paths_state_dashboard_readback(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CORE_GROUP, _core_group)
    paths = group["state_paths"]["paths"]
    state = group["workspace_state"]
    detail = group["detail"]
    dashboard = group["dashboard"]
    mcp_detail = group["mcp_detail"]["result"]["structuredContent"]
    mcp_dashboard = group["mcp_dashboard"]["result"]["structuredContent"]
    assert detail["workspace_state"]["workspace_path"] == state["workspace_path"]
    assert dashboard["workspace_cockpit"]["workspace_path"] == state["workspace_path"]
    assert mcp_detail["workspace_state"]["workspace_path"] == state["workspace_path"]
    assert mcp_dashboard["workspace_cockpit"]["workspace_path"] == state["workspace_path"]
    assert paths["workspace_state"].startswith(str(Path(paths["workspace_state"]).parents[3]))
    return {
        "workspace_state_path": paths["workspace_state"],
        "dashboard_workspace_path": dashboard["workspace_cockpit"]["workspace_path"],
    }


def _case_workflow_advice_uninitialized_large(context: ExecutionContext) -> dict[str, Any]:
    payload = context.group(CORE_GROUP, _core_group)["workflow_large"]
    assert payload["workspace_initialized"] is False
    assert payload["repo_maturity"]["mode"] == "scaffold"
    assert payload["initialization_advice"]["should_propose"] is True
    assert payload["track_recommendation"]["recommended_mode"] == "workstream"
    return {
        "workspace_initialized": payload["workspace_initialized"],
        "recommended_mode": payload["track_recommendation"]["recommended_mode"],
    }


def _case_workflow_advice_uninitialized_greenfield(context: ExecutionContext) -> dict[str, Any]:
    payload = context.group(CORE_GROUP, _core_group)["workflow_greenfield"]
    assert payload["starter_recommendation"]["recommended_preset_id"] == "expo-mobile"
    assert payload["workspace_initialized"] is False
    assert payload["repo_maturity"]["mode"] == "scaffold"
    return {
        "recommended_preset_id": payload["starter_recommendation"]["recommended_preset_id"],
        "request_kind": payload["request_analysis"]["request_kind"],
    }


def _case_workflow_advice_initialized_small(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CORE_GROUP, _core_group)
    first = group["small_first"]
    reuse = group["small_reuse"]
    stale = group["small_stale_recovery"]
    current = group["task_advice_current"]
    tasks = group["task_advice_tasks"]
    assert first["applied_action"]["action"] == "create_task"
    assert reuse["applied_action"]["action"] == "reuse_current_task"
    assert stale["applied_action"]["action"] == "reuse_current_task"
    assert current is not None
    assert current["task_id"] == stale["applied_action"]["task_id"]
    assert len(tasks["items"]) >= 1
    return {
        "initial_task_id": first["applied_action"]["task_id"],
        "reused_task_id": reuse["applied_action"]["task_id"],
        "recovered_task_id": stale["applied_action"]["task_id"],
    }


def _case_workflow_readback_surfaces_current_pointers(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CORE_GROUP, _core_group)
    current_task_payload = group["pre_close_current_task"]
    current_workstream_payload = group["pre_close_current_workstream"]
    task_listing = group["pre_close_task_listing"]
    workstream_listing = group["pre_close_workstream_listing"]
    dashboard = group["readback_dashboard"]
    assert current_task_payload is not None
    assert current_task_payload["task_id"] == group["read_task_payload"]["task_id"]
    assert current_workstream_payload["workstream_id"] == group["alpha_workstream"]["created_workstream_id"]
    assert any(item["task_id"] == current_task_payload["task_id"] for item in task_listing["items"])
    assert any(item["workstream_id"] == current_workstream_payload["workstream_id"] for item in workstream_listing["items"])
    assert dashboard["workspace_cockpit"]["workspace_path"] == current_workstream_payload["workspace_path"]
    return {
        "current_task_id": current_task_payload["task_id"],
        "current_workstream_id": current_workstream_payload["workstream_id"],
    }


def _case_workflow_stage_and_brief_read_surfaces(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CORE_GROUP, _core_group)
    register_payload = group["register_payload"]
    stages_payload = group["stages_payload"]
    generated_brief = group["generated_workstream_brief"]
    manual_brief = group["manual_task_brief"]
    assert register_payload["plan_status"] == "confirmed"
    assert len(stages_payload["stages"]) == len(register_payload["stages"])
    assert generated_brief["brief_generation_status"] == "generated"
    assert manual_brief["brief_generation_status"] == "manual"
    return {
        "stage_count": len(stages_payload["stages"]),
        "generated_status": generated_brief["brief_generation_status"],
        "manual_status": manual_brief["brief_generation_status"],
    }


def _case_task_close_verification_summary(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CORE_GROUP, _core_group)
    closed = group["closed_task_payload"]
    assert closed["status"] == "completed"
    assert closed["verification_summary"]["status"] == "completed"
    assert group["current_task_after_close"] is None
    assert group["switched_workstream"]["workstream_id"] == group["beta_workstream"]["created_workstream_id"]
    return {
        "closed_task_id": closed["task_id"],
        "verification_status": closed["verification_summary"]["status"],
    }


def _case_design_brief_write_read(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(DESIGN_GROUP, _design_group)
    brief = group["brief_payload"]
    read_brief = group["read_brief"]
    assert read_brief["summary"] == brief["summary"]
    assert read_brief["platform"] == "web"
    assert group["design_dashboard"]["workspace_cockpit"]["plan"]["design_state"]["design_summary"]
    return {
        "platform": read_brief["platform"],
        "status": read_brief["status"],
    }


def _case_reference_board_write_cache_preview(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(DESIGN_GROUP, _design_group)
    current_board = group["current_board"]
    explicit_board = group["explicit_board"]
    boards_listing = group["boards_listing"]
    cached_preview = group["cached_preview"]
    assert Path(cached_preview["cached_preview_path"]).exists()
    assert current_board["board_id"] == "cockpit-board"
    assert explicit_board["board_id"] == "hero-board"
    assert len(boards_listing["boards"]) >= 2
    return {
        "current_board_id": current_board["board_id"],
        "explicit_board_id": explicit_board["board_id"],
        "cached_preview_path": cached_preview["cached_preview_path"],
    }


def _case_design_handoff_write_list(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(DESIGN_GROUP, _design_group)
    listing = group["handoffs_listing"]
    current_handoff = group["current_handoff"]
    assert len(listing["handoffs"]) >= 2
    assert any(item["handoff_id"] == "handoff-beta" for item in listing["handoffs"])
    assert current_handoff["handoff_id"] == "current"
    assert current_handoff["status"] == "ready"
    return {
        "handoff_count": len(listing["handoffs"]),
        "current_handoff_id": current_handoff["handoff_id"],
    }


def _case_design_readback_current_vs_explicit(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(DESIGN_GROUP, _design_group)
    assert group["current_board"]["board_id"] == "cockpit-board"
    assert group["explicit_board"]["board_id"] == "hero-board"
    assert group["current_handoff"]["handoff_id"] == "current"
    assert group["current_handoff"]["summary"] == group["handoff_two"]["summary"]
    assert group["explicit_handoff"]["handoff_id"] == "handoff-alpha"
    return {
        "current_board_id": group["current_board"]["board_id"],
        "explicit_board_id": group["explicit_board"]["board_id"],
        "current_handoff_id": group["current_handoff"]["handoff_id"],
        "explicit_handoff_id": group["explicit_handoff"]["handoff_id"],
    }


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition("kernel-detect-uninitialized", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("kernel", "detect"), _case_kernel_detect_uninitialized),
        ScenarioDefinition("kernel-preview-init", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("kernel", "preview"), _case_kernel_preview_init),
        ScenarioDefinition("kernel-init-workspace", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("kernel", "init"), _case_kernel_init_workspace),
        ScenarioDefinition("workspace-list-and-detail", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("kernel", "readback"), _case_workspace_list_and_detail),
        ScenarioDefinition("kernel-paths-state-dashboard-readback", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("kernel", "dashboard", "readback"), _case_kernel_paths_state_dashboard_readback),
        ScenarioDefinition("workflow-advice-uninitialized-large", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("workflow", "advice"), _case_workflow_advice_uninitialized_large),
        ScenarioDefinition("workflow-advice-uninitialized-greenfield", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("workflow", "advice", "greenfield"), _case_workflow_advice_uninitialized_greenfield),
        ScenarioDefinition("workflow-advice-initialized-small", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("workflow", "advice", "task"), _case_workflow_advice_initialized_small),
        ScenarioDefinition("workflow-readback-surfaces-current-pointers", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("workflow", "readback"), _case_workflow_readback_surfaces_current_pointers),
        ScenarioDefinition("workflow-stage-and-brief-read-surfaces", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("workflow", "brief"), _case_workflow_stage_and_brief_read_surfaces),
        ScenarioDefinition("task-close-verification-summary", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("workflow", "task", "closeout"), _case_task_close_verification_summary),
        ScenarioDefinition("design-brief-write-read", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("design", "brief"), _case_design_brief_write_read),
        ScenarioDefinition("reference-board-write-cache-preview", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("design", "board"), _case_reference_board_write_cache_preview),
        ScenarioDefinition("design-handoff-write-list", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("design", "handoff"), _case_design_handoff_write_list),
        ScenarioDefinition("design-readback-current-vs-explicit", "source-plugin", ("wave2-foundation", "wave2-full-local"), ("design", "readback"), _case_design_readback_current_vs_explicit),
    ]
    return {case.case_id: case for case in cases}
