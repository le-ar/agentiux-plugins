from __future__ import annotations

from pathlib import Path
from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

from agentiux_dev_analytics import write_learning_entry
from agentiux_dev_context import refresh_context_index
from agentiux_dev_e2e_support import make_stale_plugin_fixture, read_json_file, seed_workspace, temporary_env
from agentiux_dev_lib import (
    STATE_SCHEMA_VERSION,
    init_workspace,
    preview_repair_workspace_state,
    preview_reset_workspace_state,
    repair_workspace_state,
    reset_workspace_state,
    workspace_paths,
)
from agentiux_dev_verification import read_verification_recipes


GROUP_KEY = "kernel-core"


def _kernel_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("kernel-core")
    state_root = group_root / "state"
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(state_root),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        reset_workspace = group_root / "reset-workspace"
        reset_workspace.mkdir(exist_ok=True)
        seed_workspace(reset_workspace)
        preview_before_init = preview_reset_workspace_state(reset_workspace)
        initial_reset_init = init_workspace(reset_workspace)
        refresh_context_index(reset_workspace)
        write_learning_entry(
            reset_workspace,
            {
                "entry_id": "kernel-reset-learning",
                "kind": "test-harness",
                "status": "open",
                "symptom": "Reset should clear workspace-scoped analytics and context cache slices.",
                "fix_applied": "Reset removes the external slice before re-init.",
                "source": "wave1-e2e",
            },
        )
        preview_after_init = preview_reset_workspace_state(reset_workspace)
        reset_result = reset_workspace_state(reset_workspace)

        idempotent_workspace = group_root / "idempotent-workspace"
        idempotent_workspace.mkdir(exist_ok=True)
        seed_workspace(idempotent_workspace)
        idempotent_first = init_workspace(idempotent_workspace)
        idempotent_second = init_workspace(idempotent_workspace)

        workspace_a = group_root / "cross-a"
        workspace_b = group_root / "cross-b"
        workspace_a.mkdir(exist_ok=True)
        workspace_b.mkdir(exist_ok=True)
        seed_workspace(workspace_a)
        seed_workspace(workspace_b)
        init_workspace(workspace_a)
        init_workspace(workspace_b)
        refresh_context_index(workspace_a)
        write_learning_entry(
            workspace_a,
            {
                "entry_id": "cross-reset-learning",
                "kind": "test-harness",
                "status": "open",
                "symptom": "Reset should not delete sibling workspace state.",
                "fix_applied": "Reset only removes the targeted workspace slice.",
                "source": "wave1-e2e",
            },
        )
        cross_reset = reset_workspace_state(workspace_a)
        sibling_preview = preview_reset_workspace_state(workspace_b)
        sibling_workspace_state = Path(workspace_paths(workspace_b)["workspace_state"]).exists()

        stale_fixture = make_stale_plugin_fixture(context.plugin_root)
        repair_preview = preview_repair_workspace_state(context.plugin_root)
        repaired_state = repair_workspace_state(context.plugin_root)
        repaired_canonical_register = read_json_file(Path(stale_fixture["paths"]["current_workstream_stage_register"]))
        plugin_recipes = read_verification_recipes(context.plugin_root)
    return {
        "preview_before_init": preview_before_init,
        "initial_reset_init": initial_reset_init,
        "preview_after_init": preview_after_init,
        "reset_result": reset_result,
        "idempotent_first": idempotent_first,
        "idempotent_second": idempotent_second,
        "cross_reset": cross_reset,
        "sibling_preview": sibling_preview,
        "sibling_workspace_state_exists": sibling_workspace_state,
        "stale_fixture": stale_fixture,
        "repair_preview": repair_preview,
        "repaired_state": repaired_state,
        "repaired_canonical_register": repaired_canonical_register,
        "plugin_recipes": plugin_recipes,
    }


def _case_preview_reset_uninitialized(context: ExecutionContext) -> dict[str, Any]:
    payload = context.group(GROUP_KEY, _kernel_group)["preview_before_init"]
    assert payload["workspace_root_exists"] is False
    assert payload["registry_entry_exists"] is False
    return {"workspace_root_exists": payload["workspace_root_exists"], "registry_entry_exists": payload["registry_entry_exists"]}


def _case_reset_after_init(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _kernel_group)
    preview = group["preview_after_init"]
    result = group["reset_result"]
    assert preview["workspace_root_exists"] is True
    assert preview["context_cache_exists"] is True
    assert preview["analytics_cleanup"]["learning_paths"]
    assert result["removed_workspace_root"] is True
    assert result["removed_registry_entry"] is True
    assert result["removed_context_cache_root"] is True
    assert result["analytics_cleanup"]["removed_learning_paths"]
    assert result["post_reset_preview"]["already_initialized"] is False
    return {
        "removed_workspace_root": result["removed_workspace_root"],
        "removed_context_cache_root": result["removed_context_cache_root"],
        "removed_learning_paths": len(result["analytics_cleanup"]["removed_learning_paths"]),
    }


def _case_init_idempotent_repeat(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _kernel_group)
    first = group["idempotent_first"]
    second = group["idempotent_second"]
    assert first["workspace_state"]["schema_version"] == STATE_SCHEMA_VERSION
    assert second["workspace_state"]["schema_version"] == STATE_SCHEMA_VERSION
    assert first["workspace_state"]["workspace_path"] == second["workspace_state"]["workspace_path"]
    assert second["workspace_state"]["current_workstream_id"] is None
    return {"workspace_path": second["workspace_state"]["workspace_path"], "schema_version": second["workspace_state"]["schema_version"]}


def _case_reset_cross_workspace_isolation(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _kernel_group)
    reset_payload = group["cross_reset"]
    sibling_preview = group["sibling_preview"]
    assert reset_payload["removed_workspace_root"] is True
    assert group["sibling_workspace_state_exists"] is True
    assert sibling_preview["workspace_root_exists"] is True
    assert sibling_preview["registry_entry_exists"] is True
    return {
        "removed_workspace_root": reset_payload["removed_workspace_root"],
        "sibling_workspace_root_exists": sibling_preview["workspace_root_exists"],
        "sibling_registry_entry_exists": sibling_preview["registry_entry_exists"],
    }


def _case_repair_legacy_slice(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _kernel_group)
    stale_fixture = group["stale_fixture"]
    repair_preview = group["repair_preview"]
    repaired_state = group["repaired_state"]
    repaired_workstream = next(
        item for item in repaired_state["workstreams"]["items"] if item["workstream_id"] == stale_fixture["workstream_id"]
    )
    assert repair_preview["changes"]["local_dev_policy"]["infra_mode"] == "not_applicable"
    assert repair_preview["changes"]["remove_legacy_docker_policy"] is True
    assert repaired_state["workspace_state"]["local_dev_policy"]["infra_mode"] == "not_applicable"
    assert repaired_state["workspace_state"]["state_repair_status"]["source_schema_version"] == STATE_SCHEMA_VERSION
    assert repaired_workstream["title"] == "plugin-production-readiness"
    assert repaired_workstream["kind"] == "feature"
    assert "is_mirror" not in group["repaired_canonical_register"]
    assert any(case["id"] == "plugin-smoke" for case in group["plugin_recipes"]["cases"])
    return {
        "repaired_workstream_id": repaired_workstream["workstream_id"],
        "repaired_title": repaired_workstream["title"],
        "recipe_case_count": len(group["plugin_recipes"]["cases"]),
    }


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition(
            case_id="kernel-preview-reset-uninitialized",
            fixture_id="source-plugin",
            suite_ids=("kernel-core", "core-full-local"),
            tags=("kernel", "reset", "uninitialized"),
            run=_case_preview_reset_uninitialized,
        ),
        ScenarioDefinition(
            case_id="kernel-reset-after-init",
            fixture_id="source-plugin",
            suite_ids=("kernel-core", "core-full-local"),
            tags=("kernel", "reset"),
            run=_case_reset_after_init,
        ),
        ScenarioDefinition(
            case_id="kernel-init-idempotent-repeat",
            fixture_id="source-plugin",
            suite_ids=("kernel-core", "core-full-local"),
            tags=("kernel", "init", "idempotent"),
            run=_case_init_idempotent_repeat,
        ),
        ScenarioDefinition(
            case_id="kernel-reset-cross-workspace-isolation",
            fixture_id="source-plugin",
            suite_ids=("kernel-core", "core-full-local"),
            tags=("kernel", "reset", "isolation"),
            run=_case_reset_cross_workspace_isolation,
        ),
        ScenarioDefinition(
            case_id="kernel-repair-legacy-slice",
            fixture_id="source-plugin",
            suite_ids=("kernel-core", "core-full-local"),
            tags=("kernel", "repair", "legacy"),
            run=_case_repair_legacy_slice,
        ),
    ]
    return {case.case_id: case for case in cases}
