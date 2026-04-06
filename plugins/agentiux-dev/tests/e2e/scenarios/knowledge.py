from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

from agentiux_dev_analytics import get_analytics_snapshot, list_learning_entries, update_learning_entry, write_learning_entry
from agentiux_dev_auth import (
    get_auth_session,
    invalidate_auth_session,
    list_auth_sessions,
    remove_auth_profile,
    remove_auth_session,
    resolve_auth_profile,
    show_auth_profiles,
    write_auth_profile,
    write_auth_session,
)
from agentiux_dev_context import (
    refresh_context_index,
    run_analysis_audit,
    search_context_index,
    show_capability_catalog,
    show_context_structure,
    show_intent_route,
    show_workspace_context_pack,
)
from agentiux_dev_e2e_support import call_mcp, create_named_fixture_repo, isolated_plugin_env, seed_workspace, temporary_env
from agentiux_dev_memory import (
    archive_project_note,
    get_project_note,
    list_generated_memory_snapshots,
    list_project_notes,
    persist_generated_memory_snapshot,
    search_project_notes,
    write_project_note,
)
from agentiux_dev_request_intent import analyze_request_text, command_aliases, resolve_command_phrase
from agentiux_dev_lib import close_task, create_task, create_workstream, dashboard_snapshot, init_workspace
from agentiux_dev_retrieval import surface_budget_result
from agentiux_dev_verification import (
    approve_verification_baseline,
    audit_verification_coverage,
    list_verification_runs,
    read_verification_events,
    read_verification_log_tail,
    read_verification_run,
    resolve_verification_selection,
    start_verification_case,
    start_verification_suite,
    update_verification_baseline,
    wait_for_verification_run,
    write_verification_recipes,
)


COMMAND_GROUP = "wave2-knowledge/command"
ANALYSIS_GROUP = "wave2-knowledge/analysis"
MEMORY_GROUP = "wave2-knowledge/memory"
VERIFICATION_COVERAGE_GROUP = "wave2-knowledge/verification-coverage"
VERIFICATION_SELECTION_GROUP = "wave2-knowledge/verification-selection"
VERIFICATION_EXECUTION_GROUP = "wave2-knowledge/verification-execution"
AUTH_PROFILE_GROUP = "wave2-knowledge/auth-profile"
AUTH_SESSION_GROUP = "wave2-knowledge/auth-session"
ANALYSIS_STRESS_GROUP = "wave2-knowledge/analysis-stress"


def _budget_result(payload: dict[str, Any]) -> dict[str, Any]:
    surface_name = ((payload.get("payload") or {}).get("surface")) or payload.get("check")
    if not surface_name:
        raise AssertionError("Payload did not expose a surface identifier for budget checks")
    result = surface_budget_result(surface_name, payload.get("payload"))
    assert result["within_ceiling"] is True
    assert result["within_budget"] is True
    return result


def _assert_no_compact_surface_leakage(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in ("source_text", "\"embedding\"", "\"raw_units\"", "\"semantic_units\"", "\"full_artifact\""):
        assert forbidden not in serialized


def _clone_fixture(context: ExecutionContext, group_root: Path, name: str, fixture_id: str) -> Path:
    return create_named_fixture_repo(group_root / name, context.plugin_root, fixture_id)


def _artifact_script(message: str, artifact_name: str | None = None) -> list[str]:
    statements = ["import os", "from pathlib import Path"]
    if artifact_name:
        statements.extend(
            [
                "artifact_dir = Path(os.environ['VERIFICATION_ARTIFACT_DIR'])",
                "artifact_dir.mkdir(parents=True, exist_ok=True)",
                f"(artifact_dir / {artifact_name!r}).write_text({message!r} + '\\n', encoding='utf-8')",
            ]
        )
    statements.append(f"print({message!r})")
    return [sys.executable, "-c", "; ".join(statements)]


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


def _command_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-command")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        init_workspace(context.plugin_root, force=True)
        aliases = command_aliases()
        resolved_init = resolve_command_phrase("please initialize workspace for this repo")
        resolved_context = resolve_command_phrase("show capability catalog for plugin tools")
        unresolved = resolve_command_phrase("invent a nonexistent command phrase")
        analysis = analyze_request_text("Build a new Expo mobile app from scratch")
        route = show_intent_route(route_id="plugin-dev", request_text="Inspect dashboard release readiness and plugin tools")
        catalog = show_capability_catalog(route_id="plugin-dev", query_text="dashboard release readiness plugin", limit=8)
        context_pack = show_workspace_context_pack(
            context.plugin_root,
            request_text="Inspect dashboard release readiness and plugin tools",
            route_id="plugin-dev",
            force_refresh=True,
            limit=6,
        )
    return {
        "aliases": aliases,
        "resolved_init": resolved_init,
        "resolved_context": resolved_context,
        "unresolved": unresolved,
        "analysis": analysis,
        "route": route,
        "catalog": catalog,
        "context_pack": context_pack,
    }


def _analysis_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-analysis")
    workspace = group_root / "workspace"
    workspace.mkdir(exist_ok=True)
    seed_workspace(workspace)
    src_root = workspace / "packages" / "analysis-core" / "src"
    src_root.mkdir(parents=True, exist_ok=True)
    docs_root = workspace / "docs"
    docs_root.mkdir(parents=True, exist_ok=True)
    (src_root / "alpha.py").write_text(
        "import json\nfrom pathlib import Path\n\nclass FeatureGate:\n    pass\n\n\ndef start_app():\n    return Path('ready')\n",
        encoding="utf-8",
    )
    (src_root / "helper.ts").write_text(
        "export function loadHelper() {\n  return 'helper';\n}\n",
        encoding="utf-8",
    )
    (src_root / "index.ts").write_text(
        "import { loadHelper } from './helper';\n\nexport function startApp() {\n  return loadHelper();\n}\n",
        encoding="utf-8",
    )
    (src_root / "worker.rs").write_text("pub struct Worker;\n\npub fn run_worker() {}\n", encoding="utf-8")
    (src_root / "large.py").write_text("def large_fixture():\n    return 'x'\n\n" + ("# filler\n" * 9000), encoding="utf-8")
    (docs_root / "guide.md").write_text("# Setup Guide\n\nStart the app and verify the boundary hotspots.\n", encoding="utf-8")
    if shutil.which("node"):
        ts_module_root = workspace / "node_modules" / "typescript"
        ts_module_root.mkdir(parents=True, exist_ok=True)
        (ts_module_root / "package.json").write_text(
            json.dumps({"name": "typescript", "main": "shim.js"}, indent=2) + "\n",
            encoding="utf-8",
        )
        (ts_module_root / "shim.js").write_text(
            "exports.agentiuxExtract = function(filePath, sourceText) {\n"
            "  return {\n"
            "    status: 'ok',\n"
            "    backend: 'typescript_compiler',\n"
            "    symbols: [{ title: 'startApp', kind: 'function', line_start: 2, line_end: 4 }],\n"
            "    dependencies: sourceText.includes('./helper') ? ['./helper'] : []\n"
            "  };\n"
            "};\n",
            encoding="utf-8",
        )
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        init_workspace(workspace)
        refresh_one = refresh_context_index(workspace)
        refresh_two = refresh_context_index(workspace)
        structure_view = show_context_structure(
            workspace,
            query_text="startApp FeatureGate run_worker guide setup",
            route_id="analysis",
            module_path="packages/analysis-core",
            limit=8,
        )
        symbolic_search = search_context_index(workspace, "startApp FeatureGate run_worker guide setup", route_id="analysis", limit=8)
        semantic_snapshot = persist_generated_memory_snapshot(
            workspace,
            {
                "title": "Cross-cutting analysis memory",
                "source_audit_mode": "architecture",
                "source_query_text": "boundary coupling broadread",
                "source_module_path": "packages/analysis-core",
                "confidence": 0.81,
                "body_markdown": "- Boundary pressure between entrypoints and helpers.\n- Broadread risk around large module coordination.\n",
                "provenance": {"source": "wave2-e2e"},
            },
        )
        snapshot_listing = list_generated_memory_snapshots(workspace)
        project_notes_listing = list_project_notes(workspace)
        semantic_search = search_context_index(
            workspace,
            "crosscutting broadread memory snapshot",
            route_id="analysis",
            limit=8,
            semantic_mode="enabled",
        )
        semantic_context_pack = show_workspace_context_pack(
            workspace,
            request_text="crosscutting broadread memory snapshot",
            route_id="analysis",
            limit=4,
            semantic_mode="enabled",
        )
        architecture_audit = run_analysis_audit(
            workspace,
            "architecture",
            query_text="crosscutting broadread memory snapshot",
            module_path="packages/analysis-core",
            limit=4,
            semantic_mode="enabled",
        )

        (src_root / "alpha.py").write_text(
            "import json\nfrom pathlib import Path\n\nclass FeatureGate:\n    pass\n\n\ndef run_job():\n    return Path('updated')\n",
            encoding="utf-8",
        )
        incremental_refresh = refresh_context_index(workspace)
        (src_root / "worker.rs").unlink()
        removal_refresh = refresh_context_index(workspace)

        fallback_workspace = group_root / "fallback-workspace"
        fallback_workspace.mkdir(exist_ok=True)
        fallback_src = fallback_workspace / "src"
        fallback_src.mkdir(parents=True, exist_ok=True)
        (fallback_src / "fallback.ts").write_text(
            "export function fallbackEntry() {\n  return 'fallback';\n}\n",
            encoding="utf-8",
        )
        fallback_refresh = refresh_context_index(fallback_workspace)
        fallback_search = search_context_index(fallback_workspace, "fallbackEntry", route_id="analysis", limit=4)
        fallback_structure = show_context_structure(
            fallback_workspace,
            query_text="fallbackEntry",
            route_id="analysis",
            module_path="src",
            limit=4,
            semantic_mode="enabled",
        )
        fallback_audit = run_analysis_audit(
            fallback_workspace,
            "architecture",
            query_text="fallbackEntry",
            module_path="src",
            limit=4,
            semantic_mode="auto",
        )
    return {
        "refresh_one": refresh_one,
        "refresh_two": refresh_two,
        "structure_view": structure_view,
        "symbolic_search": symbolic_search,
        "semantic_snapshot": semantic_snapshot,
        "snapshot_listing": snapshot_listing,
        "project_notes_listing": project_notes_listing,
        "semantic_search": semantic_search,
        "semantic_context_pack": semantic_context_pack,
        "architecture_audit": architecture_audit,
        "incremental_refresh": incremental_refresh,
        "removal_refresh": removal_refresh,
        "fallback_refresh": fallback_refresh,
        "fallback_search": fallback_search,
        "fallback_structure": fallback_structure,
        "fallback_audit": fallback_audit,
    }


def _analysis_stress_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-analysis-stress")
    workspace = group_root / "workspace"
    workspace.mkdir(exist_ok=True)
    seed_workspace(workspace)
    src_root = workspace / "packages" / "stress-core" / "src"
    docs_root = workspace / "docs"
    src_root.mkdir(parents=True, exist_ok=True)
    docs_root.mkdir(parents=True, exist_ok=True)
    repeated_terms = "context budget trimming hotspot semantic retrieval benchmark dashboard telemetry"
    for index in range(1, 19):
        (src_root / f"module_{index:02d}.py").write_text(
            "\n".join(
                [
                    f"def module_{index:02d}_entry():",
                    f"    return {repeated_terms!r}",
                    "",
                    f"class Module{index:02d}Audit:",
                    "    pass",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    (src_root / "large_fixture.py").write_text(
        "def load_large_fixture():\n    return 'stress'\n\n" + ("# " + repeated_terms + "\n") * 7000,
        encoding="utf-8",
    )
    (docs_root / "oversized-notes.txt").write_text(
        ("stress-note " + repeated_terms + "\n") * 9000,
        encoding="utf-8",
    )
    for index in range(1, 9):
        (docs_root / f"guide_{index:02d}.md").write_text(
            f"# Stress Guide {index:02d}\n\n{repeated_terms}\n\nUse compact summaries only.\n",
            encoding="utf-8",
        )
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        init_workspace(workspace)
        refresh_one = refresh_context_index(workspace)
        pinned_note = write_project_note(
            workspace,
            {
                "note_id": "stress-note",
                "title": "Stress retrieval note",
                "tags": ["analysis", "budget", "telemetry"],
                "pin_state": "pinned",
                "source": "system",
                "body_markdown": "Compact summaries must stay within budget even under retrieval pressure.",
            },
        )
        snapshot = persist_generated_memory_snapshot(
            workspace,
            {
                "title": "Stress retrieval snapshot",
                "source_audit_mode": "performance",
                "source_query_text": repeated_terms,
                "source_module_path": "packages/stress-core",
                "confidence": 0.83,
                "body_markdown": "- Retrieval pressure remains high.\n- Cheap surfaces must trim aggressively.\n",
                "provenance": {"source": "wave2-e2e"},
            },
        )
        search_payload = search_context_index(
            workspace,
            repeated_terms,
            route_id="analysis",
            limit=99,
            semantic_mode="enabled",
        )
        context_pack_miss = show_workspace_context_pack(
            workspace,
            request_text=repeated_terms,
            route_id="analysis",
            limit=99,
            semantic_mode="enabled",
        )
        context_pack_hit = show_workspace_context_pack(
            workspace,
            request_text=repeated_terms,
            route_id="analysis",
            limit=99,
            semantic_mode="enabled",
        )
        structure_payload = show_context_structure(
            workspace,
            query_text=repeated_terms,
            route_id="analysis",
            module_path="packages/stress-core",
            limit=99,
            semantic_mode="enabled",
        )
        audit_payload = run_analysis_audit(
            workspace,
            "performance",
            query_text=repeated_terms,
            module_path="packages/stress-core",
            limit=99,
            semantic_mode="enabled",
        )
        (src_root / "module_03.py").write_text(
            "def module_03_entry():\n    return 'edited stress retrieval hotspot telemetry'\n",
            encoding="utf-8",
        )
        edit_refresh = refresh_context_index(workspace)
        context_pack_after_edit = show_workspace_context_pack(
            workspace,
            request_text=repeated_terms,
            route_id="analysis",
            limit=99,
            semantic_mode="enabled",
        )
    return {
        "refresh_one": refresh_one,
        "pinned_note": pinned_note,
        "snapshot": snapshot,
        "search_payload": search_payload,
        "context_pack_miss": context_pack_miss,
        "context_pack_hit": context_pack_hit,
        "structure_payload": structure_payload,
        "audit_payload": audit_payload,
        "edit_refresh": edit_refresh,
        "context_pack_after_edit": context_pack_after_edit,
    }


def _memory_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-memory")
    workspace = group_root / "workspace"
    workspace.mkdir(exist_ok=True)
    seed_workspace(workspace)
    sibling_workspace = group_root / "sibling-workspace"
    sibling_workspace.mkdir(exist_ok=True)
    seed_workspace(sibling_workspace)
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        init_workspace(workspace)
        init_workspace(sibling_workspace)
        active_note = write_project_note(
            workspace,
            {
                "note_id": "bootstrap-auth-note",
                "title": "Bootstrap auth note",
                "pin_state": "pinned",
                "status": "active",
                "tags": ["auth", "memory", "bootstrap"],
                "source": "web",
                "body_markdown": "Temporary bootstrap URL is required for auth smoke and dashboard memory checks.",
            },
        )
        archived_note = write_project_note(
            workspace,
            {
                "note_id": "archived-visual-note",
                "title": "Archived visual note",
                "status": "active",
                "tags": ["visual", "history"],
                "source": "web",
                "body_markdown": "Visual review used to require repeated manual rechecks.",
            },
        )
        archive_payload = archive_project_note(workspace, "archived-visual-note")
        note_listing = list_project_notes(workspace)
        note_search = search_project_notes(workspace, "temporary bootstrap url", limit=8)
        note_record = get_project_note(workspace, "archived-visual-note")
        write_learning_entry(
            workspace,
            {
                "entry_id": "visual-review-learning",
                "kind": "visual-review",
                "status": "open",
                "symptom": "Visual review needed repeated manual rechecks.",
                "root_cause": "The first semantic pass lacked enough signals.",
                "missing_signal": "No stored reason for why the first pass was weak.",
                "fix_applied": "Added stronger visual checks and context.",
                "prevention": "Persist the failure mode as a learning entry.",
                "source": "web",
            },
        )
        sibling_learning = write_learning_entry(
            sibling_workspace,
            {
                "entry_id": "sibling-learning",
                "kind": "verification",
                "status": "open",
                "symptom": "Sibling workspace should not leak into scoped analytics.",
                "fix_applied": "Scope analytics by workspace hash.",
                "source": "wave2-e2e",
            },
        )
        updated_learning = update_learning_entry(
            workspace,
            "visual-review-learning",
            {"status": "resolved", "fix_applied": "Added stronger visual checks and reran verification."},
        )
        workspace_learning_listing = list_learning_entries(workspace=workspace)
        global_learning_listing = list_learning_entries(workspace=None)
        workspace_analytics = get_analytics_snapshot(workspace)
        global_analytics = get_analytics_snapshot()
        refresh_context_index(workspace)
        packed_context = show_workspace_context_pack(
            workspace,
            request_text="temporary bootstrap url auth memory",
            route_id="plugin-dev",
            force_refresh=True,
        )
        direct_context_search = search_context_index(workspace, "temporary bootstrap url auth", route_id="plugin-dev")
        archived_context_search = search_context_index(workspace, "repeated manual rechecks visual history", route_id="plugin-dev")
        memory_dashboard = dashboard_snapshot(workspace)
    return {
        "active_note": active_note,
        "archived_note": archived_note,
        "archive_payload": archive_payload,
        "note_listing": note_listing,
        "note_search": note_search,
        "note_record": note_record,
        "sibling_learning": sibling_learning,
        "updated_learning": updated_learning,
        "workspace_learning_listing": workspace_learning_listing,
        "global_learning_listing": global_learning_listing,
        "workspace_analytics": workspace_analytics,
        "global_analytics": global_analytics,
        "packed_context": packed_context,
        "direct_context_search": direct_context_search,
        "archived_context_search": archived_context_search,
        "memory_dashboard": memory_dashboard,
    }


def _verification_coverage_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-verification-coverage")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        visual_gap_workspace = _clone_fixture(context, group_root, "visual-gap", "fullstack-workspace")
        init_workspace(visual_gap_workspace)
        visual_gap_workstream_id = create_workstream(
            visual_gap_workspace,
            "Verification Coverage Visual Gap",
            kind="feature",
            scope_summary="Exercise warning-only verification coverage gaps without mutating unrelated state.",
        )["created_workstream_id"]
        write_verification_recipes(
            visual_gap_workspace,
            {
                "baseline_policy": {
                    "canonical_baselines": "project_owned",
                    "transient_artifacts": "external_state_only",
                },
                "cases": [
                    {
                        "id": "web-contract-only",
                        "title": "Web contract only",
                        "surface_type": "web",
                        "runner": "shell-contract",
                        "changed_path_globs": ["apps/web/**"],
                        "host_requirements": ["python"],
                        "argv": _artifact_script("web contract only", "web-contract-only.txt"),
                    }
                ],
                "suites": [{"id": "full", "title": "Full", "case_ids": ["web-contract-only"]}],
            },
            workstream_id=visual_gap_workstream_id,
        )
        visual_gap_audit = audit_verification_coverage(visual_gap_workspace, workstream_id=visual_gap_workstream_id)
        visual_gap_mcp = _mcp_tool(
            context,
            env,
            610,
            "audit_verification_coverage",
            {
                "workspacePath": str(visual_gap_workspace.resolve()),
                "workstreamId": visual_gap_workstream_id,
            },
        )

        baseline_gap_workspace = _clone_fixture(context, group_root, "baseline-gap", "fullstack-workspace")
        init_workspace(baseline_gap_workspace)
        baseline_gap_workstream_id = create_workstream(
            baseline_gap_workspace,
            "Verification Coverage Baseline Gap",
            kind="feature",
            scope_summary="Exercise host requirement and baseline-source coverage gaps for visual verification.",
        )["created_workstream_id"]
        write_verification_recipes(
            baseline_gap_workspace,
            {
                "baseline_policy": {
                    "canonical_baselines": "project_owned",
                    "transient_artifacts": "external_state_only",
                },
                "cases": [
                    {
                        "id": "web-visual-missing-baseline",
                        "title": "Web visual missing baseline",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "changed_path_globs": ["apps/web/**"],
                        "routes_or_screens": ["/checkout"],
                        "argv": _artifact_script("web visual missing baseline", "web-visual-missing-baseline.txt"),
                        "target": {"route": "/checkout"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-visual-gap.json",
                            "required_checks": ["visibility", "layout_relations"],
                            "targets": [{"target_id": "checkout-main", "locator": {"kind": "role", "value": "main"}}],
                        },
                    }
                ],
                "suites": [{"id": "full", "title": "Full", "case_ids": ["web-visual-missing-baseline"]}],
            },
            workstream_id=baseline_gap_workstream_id,
        )
        baseline_gap_audit = audit_verification_coverage(baseline_gap_workspace, workstream_id=baseline_gap_workstream_id)
        baseline_gap_mcp = _mcp_tool(
            context,
            env,
            611,
            "audit_verification_coverage",
            {
                "workspacePath": str(baseline_gap_workspace.resolve()),
                "workstreamId": baseline_gap_workstream_id,
            },
        )
    return {
        "visual_gap_audit": visual_gap_audit,
        "visual_gap_mcp": visual_gap_mcp,
        "baseline_gap_audit": baseline_gap_audit,
        "baseline_gap_mcp": baseline_gap_mcp,
    }


def _verification_selection_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-verification-selection")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        workspace = _clone_fixture(context, group_root, "selection", "fullstack-workspace")
        init_workspace(workspace)
        workstream_id = create_workstream(
            workspace,
            "Verification Selection",
            kind="feature",
            scope_summary="Exercise targeted verification resolution and heuristic suggestion flows.",
        )["created_workstream_id"]
        baseline_root = workspace / "tests" / "visual" / "baselines"
        baseline_root.mkdir(parents=True, exist_ok=True)
        (baseline_root / "web-home.txt").write_text("baseline\n", encoding="utf-8")
        write_verification_recipes(
            workspace,
            {
                "baseline_policy": {
                    "canonical_baselines": "project_owned",
                    "transient_artifacts": "external_state_only",
                },
                "cases": [
                    {
                        "id": "web-home",
                        "title": "Web home",
                        "surface_type": "web",
                        "runner": "shell-contract",
                        "surface_ids": ["dashboard-home"],
                        "changed_path_globs": ["apps/web/**"],
                        "host_requirements": ["python"],
                        "argv": _artifact_script("web-home done", "web-home.txt"),
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/web-home.txt"},
                    },
                    {
                        "id": "expo-home",
                        "title": "Expo home",
                        "surface_type": "mobile",
                        "runner": "shell-contract",
                        "surface_ids": ["expo-home"],
                        "changed_path_globs": ["apps/mobile/**"],
                        "host_requirements": ["python"],
                        "argv": _artifact_script("expo-home done", "expo-home.txt"),
                    },
                ],
                "suites": [{"id": "full", "title": "Full", "case_ids": ["web-home", "expo-home"]}],
            },
            workstream_id=workstream_id,
        )
        targeted_task = create_task(
            workspace,
            title="Verify dashboard home only",
            objective="Resolve targeted verification only for the dashboard surface.",
            verification_selectors={"surface_ids": ["dashboard-home"]},
            verification_mode_default="targeted",
            make_current=True,
        )
        resolved_selection = resolve_verification_selection(workspace)
        resolved_mcp = _mcp_tool(
            context,
            env,
            620,
            "resolve_verification",
            {
                "workspacePath": str(workspace.resolve()),
                "workstreamId": workstream_id,
            },
        )
        close_task(workspace, task_id=targeted_task["created_task_id"], verification_summary={"status": "completed"})

        unresolved_task = create_task(
            workspace,
            title="Inspect verification heuristics",
            objective="Inspect changed paths without explicit selectors.",
            make_current=True,
        )
        unresolved_selection = resolve_verification_selection(workspace, changed_paths=["apps/web/app/checkout/page.tsx"])
        heuristic_selection = resolve_verification_selection(
            workspace,
            changed_paths=["apps/web/app/checkout/page.tsx"],
            confirm_heuristics=True,
        )
        close_task(workspace, task_id=unresolved_task["created_task_id"], verification_summary={"status": "completed"})
    return {
        "workstream_id": workstream_id,
        "resolved_selection": resolved_selection,
        "resolved_mcp": resolved_mcp,
        "unresolved_selection": unresolved_selection,
        "heuristic_selection": heuristic_selection,
    }


def _verification_execution_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-verification-execution")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        workspace = _clone_fixture(context, group_root, "execution", "fullstack-workspace")
        init_workspace(workspace)
        workstream_id = create_workstream(
            workspace,
            "Verification Execution",
            kind="feature",
            scope_summary="Exercise suite execution, run readback, and baseline lifecycle on temp-cloned fixtures.",
        )["created_workstream_id"]
        sibling_workspace = _clone_fixture(context, group_root, "execution-sibling", "fullstack-workspace")
        init_workspace(sibling_workspace)
        sibling_workstream_id = create_workstream(
            sibling_workspace,
            "Sibling Verification",
            kind="feature",
            scope_summary="Remain isolated from the primary verification run inventory.",
        )["created_workstream_id"]
        baseline_target = workspace / "tests" / "visual" / "baselines" / "web-home.txt"
        baseline_target.parent.mkdir(parents=True, exist_ok=True)
        baseline_target.write_text("stale baseline\n", encoding="utf-8")
        write_verification_recipes(
            workspace,
            {
                "baseline_policy": {
                    "canonical_baselines": "project_owned",
                    "transient_artifacts": "external_state_only",
                },
                "cases": [
                    {
                        "id": "web-home",
                        "title": "Web home",
                        "surface_type": "web",
                        "runner": "shell-contract",
                        "surface_ids": ["dashboard-home"],
                        "changed_path_globs": ["apps/web/**"],
                        "host_requirements": ["python"],
                        "argv": _artifact_script("web home ok", "web-home.txt"),
                        "baseline": {"policy": "project-owned", "source_path": str(baseline_target.relative_to(workspace))},
                    },
                    {
                        "id": "expo-home",
                        "title": "Expo home",
                        "surface_type": "mobile",
                        "runner": "shell-contract",
                        "surface_ids": ["expo-home"],
                        "changed_path_globs": ["apps/mobile/**"],
                        "host_requirements": ["python"],
                        "argv": _artifact_script("expo home ok", "expo-home.txt"),
                    },
                ],
                "suites": [{"id": "full", "title": "Full", "case_ids": ["web-home", "expo-home"]}],
            },
            workstream_id=workstream_id,
        )
        case_run = start_verification_case(workspace, "web-home", workstream_id=workstream_id)
        case_run = wait_for_verification_run(workspace, case_run["run_id"], timeout_seconds=20, workstream_id=workstream_id)
        approved = approve_verification_baseline(workspace, "web-home", run_id=case_run["run_id"], workstream_id=workstream_id)
        updated = update_verification_baseline(
            workspace,
            "web-home",
            run_id=case_run["run_id"],
            artifact_path=str(Path(case_run["artifacts_dir"]) / "web-home.txt"),
            workstream_id=workstream_id,
        )
        suite_run = start_verification_suite(workspace, "full", workstream_id=workstream_id)
        suite_run = wait_for_verification_run(workspace, suite_run["run_id"], timeout_seconds=20, workstream_id=workstream_id)
        listed_runs = list_verification_runs(workspace, workstream_id=workstream_id)
        listed_runs_mcp = _mcp_tool(
            context,
            env,
            630,
            "list_verification_runs",
            {
                "workspacePath": str(workspace.resolve()),
                "workstreamId": workstream_id,
            },
        )
        run_detail = read_verification_run(workspace, suite_run["run_id"], workstream_id=workstream_id)
        run_detail_mcp = _mcp_tool(
            context,
            env,
            631,
            "get_verification_run",
            {
                "workspacePath": str(workspace.resolve()),
                "workstreamId": workstream_id,
                "runId": suite_run["run_id"],
            },
        )
        event_log = read_verification_events(workspace, suite_run["run_id"], limit=50, workstream_id=workstream_id)
        event_log_mcp = _mcp_tool(
            context,
            env,
            632,
            "get_verification_events",
            {
                "workspacePath": str(workspace.resolve()),
                "workstreamId": workstream_id,
                "runId": suite_run["run_id"],
                "limit": 50,
            },
        )
        stdout_log = read_verification_log_tail(workspace, suite_run["run_id"], "stdout", 20, workstream_id=workstream_id)
        stdout_log_mcp = _mcp_tool(
            context,
            env,
            633,
            "get_verification_log",
            {
                "workspacePath": str(workspace.resolve()),
                "workstreamId": workstream_id,
                "runId": suite_run["run_id"],
                "stream": "stdout",
                "lines": 20,
            },
        )
        sibling_runs = list_verification_runs(sibling_workspace, workstream_id=sibling_workstream_id)
    return {
        "case_run": case_run,
        "approved": approved,
        "updated": updated,
        "suite_run": suite_run,
        "listed_runs": listed_runs,
        "listed_runs_mcp": listed_runs_mcp,
        "run_detail": run_detail,
        "run_detail_mcp": run_detail_mcp,
        "event_log": event_log,
        "event_log_mcp": event_log_mcp,
        "stdout_log": stdout_log,
        "stdout_log_mcp": stdout_log_mcp,
        "baseline_target": str(baseline_target),
        "sibling_runs": sibling_runs,
    }


def _auth_profile_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-auth-profile")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        workspace = _clone_fixture(context, group_root, "profiles", "mobile-detox-app")
        init_workspace(workspace)
        sibling_workspace = _clone_fixture(context, group_root, "profiles-sibling", "mobile-detox-app")
        init_workspace(sibling_workspace)
        primary_profile = write_auth_profile(
            workspace,
            {
                "profile_id": "dashboard-auth",
                "label": "Dashboard auth",
                "scope_type": "workspace",
                "is_default": True,
                "usage_policy": {
                    "default_request_mode": "read_only",
                    "allowed_request_modes": ["read_only"],
                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                    "action_tags": [],
                    "allow_session_persistence": True,
                    "allow_session_refresh": True,
                },
            },
            secret_payload={"login": "qa@example.com", "password": "qa-password"},
        )
        updated_primary = write_auth_profile(
            workspace,
            {
                "profile_id": "dashboard-auth",
                "label": "Dashboard auth updated",
                "scope_type": "workspace",
                "is_default": True,
                "usage_policy": {
                    "default_request_mode": "read_only",
                    "allowed_request_modes": ["read_only"],
                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                    "action_tags": [],
                    "allow_session_persistence": True,
                    "allow_session_refresh": True,
                },
            },
        )
        secondary_profile = write_auth_profile(
            workspace,
            {
                "profile_id": "secondary-auth",
                "label": "Secondary auth",
                "scope_type": "workspace",
                "is_default": False,
                "usage_policy": {
                    "default_request_mode": "read_only",
                    "allowed_request_modes": ["read_only"],
                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                    "action_tags": ["tag.read"],
                    "allow_session_persistence": True,
                    "allow_session_refresh": True,
                },
            },
            secret_payload={"access_token": "secondary-token"},
        )
        default_resolution = resolve_auth_profile(workspace, request_mode="read_only", surface_mode="dashboard")
        explicit_resolution = resolve_auth_profile(
            workspace,
            profile_id="secondary-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            surface_mode="cli",
        )
        listing = show_auth_profiles(workspace)
        listing_mcp = _mcp_tool(
            context,
            env,
            640,
            "show_auth_profiles",
            {"workspacePath": str(workspace.resolve())},
        )
        dashboard = dashboard_snapshot(workspace)
        removed = remove_auth_profile(workspace, "secondary-auth")
        post_remove_listing = show_auth_profiles(workspace)
        sibling_listing = show_auth_profiles(sibling_workspace)
    return {
        "primary_profile": primary_profile,
        "updated_primary": updated_primary,
        "secondary_profile": secondary_profile,
        "default_resolution": default_resolution,
        "explicit_resolution": explicit_resolution,
        "listing": listing,
        "listing_mcp": listing_mcp,
        "dashboard": dashboard,
        "removed": removed,
        "post_remove_listing": post_remove_listing,
        "sibling_listing": sibling_listing,
    }


def _auth_session_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("wave2-knowledge-auth-session")
    env = isolated_plugin_env(group_root, context.plugin_root)
    with temporary_env(env):
        workspace = _clone_fixture(context, group_root, "sessions", "mobile-detox-app")
        init_workspace(workspace)
        sibling_workspace = _clone_fixture(context, group_root, "sessions-sibling", "mobile-detox-app")
        init_workspace(sibling_workspace)
        write_auth_profile(
            workspace,
            {
                "profile_id": "session-auth",
                "label": "Session auth",
                "scope_type": "workspace",
                "is_default": True,
                "usage_policy": {
                    "default_request_mode": "read_only",
                    "allowed_request_modes": ["read_only", "mutating"],
                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                    "action_tags": [],
                    "allow_session_persistence": True,
                    "allow_session_refresh": True,
                },
            },
            secret_payload={"login": "qa@example.com", "password": "qa-password"},
        )
        primary_session = write_auth_session(
            workspace,
            {"profile_id": "session-auth", "source_kind": "manual", "request_mode": "read_only"},
            secret_payload={"access_token": "access-one"},
        )
        updated_primary_session = write_auth_session(
            workspace,
            {
                "session_id": primary_session["session"]["session_id"],
                "profile_id": "session-auth",
                "source_kind": "manual",
                "request_mode": "read_only",
            },
            secret_payload={"access_token": "access-one-updated"},
        )
        invalidated_session = write_auth_session(
            workspace,
            {"profile_id": "session-auth", "source_kind": "manual", "request_mode": "mutating"},
            secret_payload={"access_token": "access-two"},
        )
        removed_session = write_auth_session(
            workspace,
            {"profile_id": "session-auth", "source_kind": "manual", "request_mode": "read_only"},
            secret_payload={"access_token": "access-three"},
        )
        listing = list_auth_sessions(workspace)
        listing_mcp = _mcp_tool(
            context,
            env,
            650,
            "list_auth_sessions",
            {"workspacePath": str(workspace.resolve())},
        )
        detail = get_auth_session(workspace, primary_session["session"]["session_id"])
        detail_mcp = _mcp_tool(
            context,
            env,
            651,
            "get_auth_session",
            {
                "workspacePath": str(workspace.resolve()),
                "sessionId": primary_session["session"]["session_id"],
            },
        )
        invalidated = invalidate_auth_session(workspace, invalidated_session["session"]["session_id"])
        removed = remove_auth_session(workspace, removed_session["session"]["session_id"])
        post_mutation_listing = list_auth_sessions(workspace)
        dashboard = dashboard_snapshot(workspace)
        sibling_listing = list_auth_sessions(sibling_workspace)
        removed_lookup_error = None
        try:
            get_auth_session(workspace, removed_session["session"]["session_id"])
        except FileNotFoundError as exc:
            removed_lookup_error = str(exc)
    return {
        "primary_session": primary_session,
        "updated_primary_session": updated_primary_session,
        "invalidated_session": invalidated_session,
        "removed_session": removed_session,
        "listing": listing,
        "listing_mcp": listing_mcp,
        "detail": detail,
        "detail_mcp": detail_mcp,
        "invalidated": invalidated,
        "removed": removed,
        "post_mutation_listing": post_mutation_listing,
        "dashboard": dashboard,
        "sibling_listing": sibling_listing,
        "removed_lookup_error": removed_lookup_error,
    }


def _case_kernel_command_aliases_and_phrase_resolution(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(COMMAND_GROUP, _command_group)
    aliases = group["aliases"]
    assert "initialize workspace" in aliases
    assert "init workspace" in aliases["initialize workspace"]
    assert group["resolved_init"] == "initialize workspace"
    assert group["resolved_context"] == "show capability catalog"
    assert group["unresolved"] is None
    return {
        "initialize_alias_count": len(aliases["initialize workspace"]),
        "greenfield_request_kind": group["analysis"]["request_kind"],
    }


def _case_capability_catalog_route_pack(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(COMMAND_GROUP, _command_group)
    route = group["route"]
    catalog = group["catalog"]
    context_pack = group["context_pack"]
    assert route["resolved_route"]["route_id"] == "plugin-dev"
    assert catalog["entries"]
    assert "plugin-platform" in context_pack["workspace_context"]["selected_profiles"]
    route_budget = _budget_result(route)
    catalog_budget = _budget_result(catalog)
    context_pack_budget = _budget_result(context_pack)
    assert len(context_pack["context_pack"]["selected_chunks"]) <= int(context_pack["retrieval"]["max_selected_chunk_limit"])
    assert len(context_pack["context_pack"]["selected_tools"]) <= int(context_pack["retrieval"]["max_selected_tool_limit"])
    _assert_no_compact_surface_leakage(catalog)
    _assert_no_compact_surface_leakage(context_pack)
    return {
        "resolved_route_id": route["resolved_route"]["route_id"],
        "catalog_match_count": catalog["total_matches"],
        "selected_chunk_count": len(context_pack["context_pack"]["selected_chunks"]),
        "budget_results": {
            "show_intent_route": route_budget,
            "show_capability_catalog": catalog_budget,
            "show_workspace_context_pack": context_pack_budget,
        },
    }


def _case_analysis_audit_structure(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(ANALYSIS_GROUP, _analysis_group)
    structure_view = group["structure_view"]
    audit = group["architecture_audit"]
    assert group["refresh_one"]["status"] == "refreshed"
    assert group["refresh_two"]["status"] == "fresh"
    assert structure_view["modules"]
    assert structure_view["hotspots"]
    assert any(match["match_source"] == "symbolic" for match in group["symbolic_search"]["matches"])
    assert audit["findings"]
    assert group["incremental_refresh"]["rebuilt_file_count"] >= 1
    assert group["removal_refresh"]["removed_file_count"] >= 1
    assert group["refresh_one"]["bounded_read_count"] >= 1
    assert group["refresh_one"]["large_file_count"] >= 1
    structure_budget = _budget_result(structure_view)
    search_budget = _budget_result(group["symbolic_search"])
    audit_budget = _budget_result(audit)
    _assert_no_compact_surface_leakage(structure_view)
    _assert_no_compact_surface_leakage(audit)
    return {
        "module_count": len(structure_view["modules"]),
        "finding_count": len(audit["findings"]),
        "removed_file_count": group["removal_refresh"]["removed_file_count"],
        "budget_results": {
            "show_context_structure": structure_budget,
            "search_context_index": search_budget,
            "run_analysis_audit": audit_budget,
        },
    }


def _case_analysis_memory_snapshot_separation(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(ANALYSIS_GROUP, _analysis_group)
    snapshot = group["semantic_snapshot"]["snapshot"]
    assert group["snapshot_listing"]["counts"]["active"] >= 1
    assert group["project_notes_listing"]["counts"]["total"] == 0
    assert any(match["match_source"] == "semantic_assisted" for match in group["semantic_search"]["matches"])
    assert not any(
        chunk.get("path", "").startswith("external/project-memory/")
        for chunk in group["semantic_context_pack"]["context_pack"]["selected_chunks"]
    )
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "active_snapshot_count": group["snapshot_listing"]["counts"]["active"],
    }


def _case_analysis_parser_fallback_js_ts(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(ANALYSIS_GROUP, _analysis_group)
    fallback_refresh = group["fallback_refresh"]
    fallback_search = group["fallback_search"]
    fallback_structure = group["fallback_structure"]
    fallback_audit = group["fallback_audit"]
    assert fallback_refresh["parser_backend_status"]["typescript_compiler"]["status"] == "unavailable"
    assert fallback_search["matches"]
    assert any(match["match_source"] == "symbolic" for match in fallback_search["matches"])
    assert fallback_structure["parser_backends"]["typescript_compiler"]["status"] == "unavailable"
    _budget_result(fallback_search)
    _budget_result(fallback_structure)
    _budget_result(fallback_audit)
    return {
        "typescript_backend_status": fallback_refresh["parser_backend_status"]["typescript_compiler"]["status"],
        "match_count": len(fallback_search["matches"]),
    }


def _case_retrieval_stress_trimming_hard_budgets(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(ANALYSIS_STRESS_GROUP, _analysis_stress_group)
    search_payload = group["search_payload"]
    context_pack_miss = group["context_pack_miss"]
    context_pack_hit = group["context_pack_hit"]
    structure_payload = group["structure_payload"]
    audit_payload = group["audit_payload"]
    edit_refresh = group["edit_refresh"]
    context_pack_after_edit = group["context_pack_after_edit"]
    search_budget = _budget_result(search_payload)
    context_pack_budget = _budget_result(context_pack_miss)
    structure_budget = _budget_result(structure_payload)
    audit_budget = _budget_result(audit_payload)
    assert context_pack_miss["cache_status"] == "miss"
    assert context_pack_hit["cache_status"] == "hit"
    assert context_pack_after_edit["cache_status"] == "miss"
    assert edit_refresh["pruned_semantic_cache_reason"] == "source-hash-drift"
    assert edit_refresh["pruned_semantic_cache_entries"] >= 1
    assert len(search_payload["matches"]) <= int(search_payload["retrieval"]["max_match_limit"])
    assert len(search_payload["recommended_capabilities"]) <= int(search_payload["retrieval"]["max_selected_tool_limit"])
    assert len(context_pack_miss["context_pack"]["selected_chunks"]) <= int(context_pack_miss["retrieval"]["max_selected_chunk_limit"])
    assert len(context_pack_miss["context_pack"]["selected_tools"]) <= int(context_pack_miss["retrieval"]["max_selected_tool_limit"])
    assert len(structure_payload["matches"]) <= int(structure_payload["retrieval"]["max_match_limit"])
    assert len(audit_payload["evidence"]) <= int(audit_payload["retrieval"]["max_match_limit"])
    _assert_no_compact_surface_leakage(search_payload)
    _assert_no_compact_surface_leakage(context_pack_miss)
    _assert_no_compact_surface_leakage(structure_payload)
    _assert_no_compact_surface_leakage(audit_payload)
    return {
        "search_match_count": len(search_payload["matches"]),
        "selected_chunk_count": len(context_pack_miss["context_pack"]["selected_chunks"]),
        "selected_tool_count": len(context_pack_miss["context_pack"]["selected_tools"]),
        "budget_results": {
            "search_context_index": search_budget,
            "show_workspace_context_pack": context_pack_budget,
            "show_context_structure": structure_budget,
            "run_analysis_audit": audit_budget,
        },
    }


def _case_project_note_crud_archive_search(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(MEMORY_GROUP, _memory_group)
    listing = group["note_listing"]
    assert group["active_note"]["note"]["note_id"] == "bootstrap-auth-note"
    assert group["archive_payload"]["note"]["status"] == "archived"
    assert listing["counts"]["active"] >= 1
    assert listing["counts"]["archived"] >= 1
    assert listing["counts"]["pinned"] >= 1
    assert group["note_record"]["status"] == "archived"
    assert any(item["note_id"] == "bootstrap-auth-note" for item in group["note_search"]["matches"])
    assert group["memory_dashboard"]["workspace_cockpit"]["memory"]["project_notes"]["counts"]["pinned"] >= 1
    return {
        "active_note_id": group["active_note"]["note"]["note_id"],
        "archived_note_status": group["note_record"]["status"],
    }


def _case_learning_entry_crud_and_analytics_scope(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(MEMORY_GROUP, _memory_group)
    workspace_learning_listing = group["workspace_learning_listing"]
    global_learning_listing = group["global_learning_listing"]
    workspace_analytics = group["workspace_analytics"]
    global_analytics = group["global_analytics"]
    assert group["updated_learning"]["entry"]["status"] == "resolved"
    assert any(item["entry_id"] == "visual-review-learning" for item in workspace_learning_listing["items"])
    assert any(item["entry_id"] == "sibling-learning" for item in global_learning_listing["items"])
    assert workspace_analytics["learning_counts"]["resolved"] >= 1
    assert global_analytics["learning_counts"]["total"] >= 2
    assert any(
        item["path"] == "external/project-memory/bootstrap-auth-note.md"
        for item in group["packed_context"]["context_pack"]["selected_chunks"]
    )
    assert any(match["path"] == "external/project-memory/bootstrap-auth-note.md" for match in group["direct_context_search"]["matches"])
    assert any(match["path"] == "external/project-memory/archived-visual-note.md" for match in group["archived_context_search"]["matches"])
    assert group["memory_dashboard"]["workspace_cockpit"]["memory"]["learnings"]["counts"]["resolved"] >= 1
    return {
        "workspace_learning_total": workspace_learning_listing["counts"]["total"],
        "global_learning_total": global_learning_listing["counts"]["total"],
    }


def _case_verification_coverage_gap_matrix(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_COVERAGE_GROUP, _verification_coverage_group)
    visual_gap_ids = {gap["gap_id"] for gap in group["visual_gap_audit"]["gaps"]}
    baseline_gap_ids = {gap["gap_id"] for gap in group["baseline_gap_audit"]["gaps"]}
    visual_gap_mcp = group["visual_gap_mcp"]["result"]["structuredContent"]
    baseline_gap_mcp = group["baseline_gap_mcp"]["result"]["structuredContent"]
    assert "missing-web-visual-verification" in visual_gap_ids
    assert "missing-web-browser-layout-audit" in visual_gap_ids
    assert "missing-backend-verification" in visual_gap_ids
    assert "web-visual-missing-baseline-missing-host-requirements" in baseline_gap_ids
    assert "web-visual-missing-baseline-missing-baseline-source" in baseline_gap_ids
    assert "missing-web-visual-verification" not in baseline_gap_ids
    assert visual_gap_mcp["warning_count"] == group["visual_gap_audit"]["warning_count"]
    assert baseline_gap_mcp["warning_count"] == group["baseline_gap_audit"]["warning_count"]
    return {
        "visual_gap_warning_count": group["visual_gap_audit"]["warning_count"],
        "baseline_gap_warning_count": group["baseline_gap_audit"]["warning_count"],
    }


def _case_verification_resolve_targeted(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_SELECTION_GROUP, _verification_selection_group)
    resolved = group["resolved_selection"]
    resolved_mcp = group["resolved_mcp"]["result"]["structuredContent"]
    unresolved = group["unresolved_selection"]
    heuristic = group["heuristic_selection"]
    assert resolved["selection_status"] == "resolved"
    assert [case["case_id"] for case in resolved["selected_cases"]] == ["web-home"]
    assert resolved["baseline_sources"]
    assert resolved_mcp["selection_status"] == "resolved"
    assert [case["case_id"] for case in resolved_mcp["selected_cases"]] == ["web-home"]
    assert unresolved["selection_status"] == "unresolved"
    assert unresolved["selected_cases"] == []
    assert [case["case_id"] for case in unresolved["heuristic_suggestions"]] == ["web-home"]
    assert heuristic["selection_status"] == "resolved"
    assert heuristic["source"] == "confirmed_heuristic_suggestion"
    assert [case["case_id"] for case in heuristic["selected_cases"]] == ["web-home"]
    return {
        "resolved_case_ids": [case["case_id"] for case in resolved["selected_cases"]],
        "heuristic_case_ids": [case["case_id"] for case in heuristic["selected_cases"]],
    }


def _case_verification_suite_pass(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_EXECUTION_GROUP, _verification_execution_group)
    suite_run = group["suite_run"]
    assert suite_run["mode"] == "suite"
    assert suite_run["status"] == "passed"
    assert suite_run["case_ids"] == ["web-home", "expo-home"]
    return {"run_id": suite_run["run_id"], "case_ids": suite_run["case_ids"]}


def _case_verification_run_readback_inventory(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_EXECUTION_GROUP, _verification_execution_group)
    listed_runs = group["listed_runs"]
    listed_runs_mcp = group["listed_runs_mcp"]["result"]["structuredContent"]
    run_detail = group["run_detail"]
    run_detail_mcp = group["run_detail_mcp"]["result"]["structuredContent"]
    event_log = group["event_log"]
    event_log_mcp = group["event_log_mcp"]["result"]["structuredContent"]
    stdout_log = group["stdout_log"]
    stdout_log_mcp = group["stdout_log_mcp"]["result"]["structuredContent"]
    assert listed_runs["latest_run"]["run_id"] == group["suite_run"]["run_id"]
    assert listed_runs_mcp["latest_run"]["run_id"] == group["suite_run"]["run_id"]
    assert run_detail["run_id"] == group["suite_run"]["run_id"]
    assert run_detail_mcp["run_id"] == group["suite_run"]["run_id"]
    assert {event["event_type"] for event in event_log["events"]} == {event["event_type"] for event in event_log_mcp["events"]}
    assert any(event["event_type"] == "run_finished" for event in event_log["events"])
    assert stdout_log["path"].endswith("stdout.log")
    assert stdout_log_mcp["path"].endswith("stdout.log")
    assert any("web home ok" in line for line in stdout_log["lines"])
    assert any("expo home ok" in line for line in stdout_log["lines"])
    assert group["sibling_runs"]["run_count"] == 0
    return {
        "run_count": listed_runs["run_count"],
        "event_count": len(event_log["events"]),
        "stdout_line_count": len(stdout_log["lines"]),
    }


def _case_verification_baseline_lifecycle(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_EXECUTION_GROUP, _verification_execution_group)
    assert group["case_run"]["status"] == "passed"
    assert group["approved"]["status"] == "approved"
    assert group["updated"]["status"] == "updated"
    assert Path(group["baseline_target"]).read_text(encoding="utf-8") == "web home ok\n"
    return {
        "case_run_id": group["case_run"]["run_id"],
        "approved_status": group["approved"]["status"],
        "updated_status": group["updated"]["status"],
    }


def _case_auth_profile_crud_readback(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(AUTH_PROFILE_GROUP, _auth_profile_group)
    listing = group["listing"]
    listing_mcp = group["listing_mcp"]["result"]["structuredContent"]
    dashboard = group["dashboard"]
    serialized = json.dumps({"listing": listing, "mcp": listing_mcp, "dashboard": dashboard}).lower()
    assert group["primary_profile"]["profile"]["profile_id"] == "dashboard-auth"
    assert group["updated_primary"]["profile"]["label"] == "Dashboard auth updated"
    assert group["secondary_profile"]["profile"]["profile_id"] == "secondary-auth"
    assert group["default_resolution"]["session"]["profile_id"] == "dashboard-auth"
    assert group["explicit_resolution"]["session"]["profile_id"] == "secondary-auth"
    assert listing["counts"]["total"] == 2
    assert len(listing_mcp["items"]) == 2
    assert dashboard["workspace_cockpit"]["integrations"]["auth"]["summary"]["profile_count"] == 2
    assert group["removed"]["removed_profile_id"] == "secondary-auth"
    assert group["post_remove_listing"]["counts"]["total"] == 1
    assert group["sibling_listing"]["counts"]["total"] == 0
    assert "qa-password" not in serialized
    assert "secondary-token" not in serialized
    return {
        "profile_count_before_remove": listing["counts"]["total"],
        "profile_count_after_remove": group["post_remove_listing"]["counts"]["total"],
    }


def _case_auth_session_crud_readback(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(AUTH_SESSION_GROUP, _auth_session_group)
    listing = group["listing"]
    listing_mcp = group["listing_mcp"]["result"]["structuredContent"]
    detail = group["detail"]
    detail_mcp = group["detail_mcp"]["result"]["structuredContent"]
    serialized = json.dumps({"listing": listing, "detail": detail, "mcp": listing_mcp}).lower()
    assert listing["counts"]["total"] == 3
    assert listing_mcp["counts"]["total"] == 3
    assert detail["session"]["session_id"] == group["primary_session"]["session"]["session_id"]
    assert detail_mcp["session"]["session_id"] == group["primary_session"]["session"]["session_id"]
    assert detail["revision_count"] >= 1
    assert group["invalidated"]["session"]["status"] == "invalidated"
    assert group["post_mutation_listing"]["counts"]["invalidated"] >= 1
    assert all(item["session_id"] != group["removed_session"]["session"]["session_id"] for item in group["post_mutation_listing"]["items"])
    assert group["removed_lookup_error"]
    assert group["dashboard"]["workspace_cockpit"]["integrations"]["auth"]["summary"]["active_session_count"] >= 1
    assert group["sibling_listing"]["counts"]["total"] == 0
    assert "access-one" not in serialized
    assert "access-two" not in serialized
    return {
        "session_count_before_remove": listing["counts"]["total"],
        "session_count_after_remove": group["post_mutation_listing"]["counts"]["total"],
        "revision_count": detail["revision_count"],
    }


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition("kernel-command-aliases-and-phrase-resolution", "source-plugin", ("wave2-knowledge", "wave2-full-local"), ("kernel", "aliases"), _case_kernel_command_aliases_and_phrase_resolution),
        ScenarioDefinition("capability-catalog-route-pack", "source-plugin", ("wave2-knowledge", "wave2-full-local"), ("retrieval", "catalog"), _case_capability_catalog_route_pack),
        ScenarioDefinition("analysis-audit-structure", "source-plugin", ("wave2-knowledge", "wave2-full-local"), ("analysis", "structure"), _case_analysis_audit_structure),
        ScenarioDefinition("analysis-memory-snapshot-separation", "source-plugin", ("wave2-knowledge", "wave2-full-local"), ("analysis", "memory"), _case_analysis_memory_snapshot_separation),
        ScenarioDefinition("analysis-parser-fallback-js-ts", "source-plugin", ("wave2-knowledge", "wave2-full-local"), ("analysis", "fallback"), _case_analysis_parser_fallback_js_ts),
        ScenarioDefinition("retrieval-stress-trimming-hard-budgets", "source-plugin", ("wave2-knowledge", "wave2-full-local"), ("analysis", "stress", "budget"), _case_retrieval_stress_trimming_hard_budgets),
        ScenarioDefinition("verification-coverage-gap-matrix", "fullstack-workspace", ("wave2-knowledge", "wave2-full-local"), ("verification", "coverage"), _case_verification_coverage_gap_matrix),
        ScenarioDefinition("verification-resolve-targeted", "fullstack-workspace", ("wave2-knowledge", "wave2-full-local"), ("verification", "selection"), _case_verification_resolve_targeted),
        ScenarioDefinition("verification-suite-pass", "fullstack-workspace", ("wave2-knowledge", "wave2-full-local"), ("verification", "suite"), _case_verification_suite_pass),
        ScenarioDefinition("verification-run-readback-inventory", "fullstack-workspace", ("wave2-knowledge", "wave2-full-local"), ("verification", "readback"), _case_verification_run_readback_inventory),
        ScenarioDefinition("verification-baseline-lifecycle", "fullstack-workspace", ("wave2-knowledge", "wave2-full-local"), ("verification", "baseline"), _case_verification_baseline_lifecycle),
        ScenarioDefinition("auth-profile-crud-readback", "mobile-detox-app", ("wave2-knowledge", "wave2-full-local"), ("auth", "profiles"), _case_auth_profile_crud_readback),
        ScenarioDefinition("auth-session-crud-readback", "mobile-detox-app", ("wave2-knowledge", "wave2-full-local"), ("auth", "sessions"), _case_auth_session_crud_readback),
        ScenarioDefinition("project-note-crud-archive-search", "source-plugin", ("wave2-knowledge", "wave2-full-local"), ("memory", "notes"), _case_project_note_crud_archive_search),
        ScenarioDefinition("learning-entry-crud-and-analytics-scope", "source-plugin", ("wave2-knowledge", "wave2-full-local"), ("analytics", "learning"), _case_learning_entry_crud_and_analytics_scope),
    ]
    return {case.case_id: case for case in cases}
