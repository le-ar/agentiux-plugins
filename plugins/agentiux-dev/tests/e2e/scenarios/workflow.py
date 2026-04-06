from __future__ import annotations

from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

from agentiux_dev_e2e_support import confirm_stage_plan, seed_workspace, stage_definition, temporary_env
from agentiux_dev_lib import (
    close_task,
    create_workstream,
    current_task,
    current_workstream,
    get_active_brief,
    init_workspace,
    read_stage_register,
    read_task,
    set_active_brief,
    switch_workstream,
    workflow_advice,
    write_stage_register,
)


GROUP_KEY = "workflow-core"


def _workflow_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("workflow-core")
    workspace = group_root / "workspace"
    workspace.mkdir(exist_ok=True)
    seed_workspace(workspace)
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        initialized = init_workspace(workspace)
        primary_workstream = create_workstream(
            workspace,
            "Workspace Planning",
            kind="feature",
            scope_summary="Lock the approved workspace implementation and verification scope.",
        )
        workstream_id = primary_workstream["created_workstream_id"]
        confirmed_register = confirm_stage_plan(
            workspace,
            [
                stage_definition(
                    "scope-lock",
                    "Scope Lock",
                    "Lock the approved workspace scope before implementation.",
                    ["scope-lock.1-confirm-approved-scope"],
                ),
                stage_definition(
                    "implementation",
                    "Implementation",
                    "Implement the approved workspace slice.",
                    ["implementation.1-apply-approved-change"],
                ),
                stage_definition(
                    "verification",
                    "Verification",
                    "Run deterministic verification for the approved slice.",
                    ["verification.1-run-deterministic-checks"],
                ),
            ],
        )
        workstream_advice = workflow_advice(workspace, "Implement checkout feature across web and backend", auto_create=True)
        generated_stage_brief = get_active_brief(workspace)
        task_advice = workflow_advice(workspace, "Fix CTA spacing in the hero section", auto_create=True)
        task_id = task_advice["applied_action"]["task_id"]
        task_result = read_task(workspace, task_id=task_id)
        reused_task_advice = workflow_advice(workspace, "Fix CTA spacing in the hero section", auto_create=True)
        set_active_brief(workspace, "# TaskBrief\n\nFix CTA spacing.\n")
        manual_task_brief = get_active_brief(workspace)
        task_after_manual = read_task(workspace, task_id=task_id)
        closed_task = close_task(workspace, verification_summary={"status": "completed", "summary": "Spacing fixed."})
        current_task_after_close = current_task(workspace)
        switch_workstream(workspace, workstream_id)
        current_workstream_payload = current_workstream(workspace)

        draft_change = read_stage_register(workspace)
        draft_change["stages"][1]["title"] = "Changed Future Stage"
        try:
            write_stage_register(workspace, draft_change, confirmed_stage_plan_edit=False)
        except ValueError as exc:
            unconfirmed_error = str(exc)
        else:
            raise AssertionError("Expected unconfirmed stage mutation to fail")
        persisted_draft_change = write_stage_register(workspace, draft_change, confirmed_stage_plan_edit=True)
        completed_change = read_stage_register(workspace)
        completed_change["stages"][0]["status"] = "completed"
        completed_change["stages"][0]["completed_at"] = "2026-03-30T00:00:00Z"
        completed_change["current_stage"] = completed_change["stages"][1]["id"]
        completed_change["stage_status"] = "planned"
        completed_change["current_slice"] = completed_change["stages"][1]["canonical_execution_slices"][0]
        completed_change["remaining_slices"] = completed_change["stages"][1]["canonical_execution_slices"][1:]
        completed_change["last_completed_stage"] = "scope-lock"
        write_stage_register(workspace, completed_change, confirmed_stage_plan_edit=False)
        immutable = read_stage_register(workspace)
        immutable["stages"][0]["title"] = "Changed"
        try:
            write_stage_register(workspace, immutable, confirmed_stage_plan_edit=True)
        except ValueError as exc:
            completed_error = str(exc)
        else:
            raise AssertionError("Expected completed stage mutation to fail")
    return {
        "initialized": initialized,
        "primary_workstream": primary_workstream,
        "confirmed_register": confirmed_register,
        "workstream_advice": workstream_advice,
        "generated_stage_brief": generated_stage_brief,
        "task_advice": task_advice,
        "task_result": task_result,
        "reused_task_advice": reused_task_advice,
        "manual_task_brief": manual_task_brief,
        "task_after_manual": task_after_manual,
        "closed_task": closed_task,
        "current_task_after_close": current_task_after_close,
        "current_workstream": current_workstream_payload,
        "unconfirmed_error": unconfirmed_error,
        "persisted_draft_change": persisted_draft_change,
        "completed_error": completed_error,
    }


def _case_workstream_create_switch_close(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _workflow_group)
    assert group["primary_workstream"]["created_workstream_id"] == "workspace-planning"
    assert group["confirmed_register"]["plan_status"] == "confirmed"
    assert group["current_workstream"]["workstream_id"] == group["primary_workstream"]["created_workstream_id"]
    return {
        "workstream_id": group["primary_workstream"]["created_workstream_id"],
        "plan_status": group["confirmed_register"]["plan_status"],
    }


def _case_task_create_switch_close(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _workflow_group)
    task_id = group["task_advice"]["applied_action"]["task_id"]
    assert group["task_advice"]["applied_action"]["action"] == "create_task"
    assert task_id == group["task_result"]["task_id"]
    assert group["closed_task"]["status"] == "completed"
    assert group["current_task_after_close"] is None
    return {
        "task_id": task_id,
        "closed_status": group["closed_task"]["status"],
    }


def _case_stage_register_confirmation(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _workflow_group)
    assert "explicit confirmation" in group["unconfirmed_error"]
    assert "Completed stage cannot be modified" in group["completed_error"]
    return {
        "unconfirmed_error": group["unconfirmed_error"],
        "completed_error": group["completed_error"],
    }


def _case_active_brief_generated_vs_manual(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _workflow_group)
    assert group["generated_stage_brief"]["brief_generation_status"] == "generated"
    assert group["task_result"]["brief_generation_status"] == "generated"
    assert group["manual_task_brief"]["brief_generation_status"] == "manual"
    assert group["task_after_manual"]["brief_generation_status"] == "manual"
    return {
        "stage_brief_status": group["generated_stage_brief"]["brief_generation_status"],
        "task_brief_status": group["task_result"]["brief_generation_status"],
        "manual_task_brief_status": group["manual_task_brief"]["brief_generation_status"],
    }


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition(
            case_id="workstream-create-switch-close",
            fixture_id="source-plugin",
            suite_ids=("workflow-core", "core-full-local"),
            tags=("workflow", "workstream"),
            run=_case_workstream_create_switch_close,
        ),
        ScenarioDefinition(
            case_id="task-create-switch-close",
            fixture_id="source-plugin",
            suite_ids=("workflow-core", "core-full-local"),
            tags=("workflow", "task"),
            run=_case_task_create_switch_close,
        ),
        ScenarioDefinition(
            case_id="stage-register-confirmation",
            fixture_id="source-plugin",
            suite_ids=("workflow-core", "core-full-local"),
            tags=("workflow", "stage-register"),
            run=_case_stage_register_confirmation,
        ),
        ScenarioDefinition(
            case_id="active-brief-generated-vs-manual",
            fixture_id="source-plugin",
            suite_ids=("workflow-core", "core-full-local"),
            tags=("workflow", "brief"),
            run=_case_active_brief_generated_vs_manual,
        ),
    ]
    return {case.case_id: case for case in cases}
