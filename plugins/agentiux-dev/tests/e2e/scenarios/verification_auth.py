from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

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
from agentiux_dev_context import refresh_context_index, search_context_index, show_workspace_context_pack
from agentiux_dev_e2e_support import (
    SEMANTIC_CONTRACT_RUNNER_FILENAME,
    read_json_file,
    reserve_local_port,
    seed_backend_workspace,
    seed_web_only_workspace,
    seed_workspace,
    temporary_env,
    wait_for_run_started,
)
from agentiux_dev_lib import close_task, create_task, create_workstream, dashboard_snapshot, init_workspace, read_stage_register, write_stage_register
from agentiux_dev_retrieval import surface_budget_result
from agentiux_dev_verification import (
    approve_verification_baseline,
    audit_verification_coverage,
    cancel_verification_run,
    list_verification_runs,
    read_verification_events,
    read_verification_log_tail,
    read_verification_recipes,
    read_verification_run,
    resolve_verification_selection,
    show_verification_helper_catalog,
    start_verification_case,
    start_verification_suite,
    sync_verification_helpers,
    update_verification_baseline,
    wait_for_verification_run,
    write_verification_recipes,
)


CONTEXT_GROUP = "verification-auth-core/context"
VERIFICATION_GROUP = "verification-auth-core/verification"
AUTH_GROUP = "verification-auth-core/auth"


def _budget_result(payload: dict[str, Any]) -> dict[str, Any]:
    surface_name = ((payload.get("payload") or {}).get("surface")) or payload.get("check")
    if not surface_name:
        raise AssertionError("Payload did not expose a surface identifier for budget checks")
    result = surface_budget_result(surface_name, payload.get("payload"))
    assert result["within_ceiling"] is True
    assert result["within_budget"] is True
    return result


def _rect_payload(left: int, top: int, right: int, bottom: int) -> dict[str, int | bool]:
    return {
        "present": True,
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
    }


def _semantic_check(check_id: str, status: str = "passed", diagnostics: dict[str, object] | None = None) -> dict[str, object]:
    return {"id": check_id, "status": status, "diagnostics": diagnostics or {}}


def _semantic_target(target_id: str, checks: list[dict[str, object]], status: str = "passed") -> dict[str, object]:
    return {"target_id": target_id, "status": status, "checks": checks}


def _semantic_report(runner: str, targets: list[dict[str, object]]) -> dict[str, object]:
    summary_status = "passed" if all(target["status"] == "passed" for target in targets) else "failed"
    return {
        "schema_version": 2,
        "runner": runner,
        "helper_bundle_version": "0.8.0",
        "summary": {"status": summary_status},
        "targets": targets,
    }


def _json_writer_argv(filename: str, payload: dict[str, Any], message: str) -> list[str]:
    script = (
        "import json, os, pathlib; "
        "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
        "artifact_dir.mkdir(parents=True, exist_ok=True); "
        f"(artifact_dir / {filename!r}).write_text({json.dumps(payload)!r}); "
        f"print({message!r})"
    )
    return [sys.executable, "-c", script]


def _write_browser_layout_fixture(path: Path, kind: str) -> None:
    if kind == "broken":
        html = (
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            "<head>\n"
            "  <meta charset=\"utf-8\">\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "  <title>Broken Layout</title>\n"
            "  <style>\n"
            "    body { margin: 0; font: 16px/1.4 sans-serif; background: #f3f1ea; }\n"
            "    [data-testid='layout-shell'] { padding: 12px; }\n"
            "    [data-testid='layout-row'] { display: flex; width: 360px; align-items: flex-start; }\n"
            "    [data-testid='primary-panel'], [data-testid='secondary-panel'] { box-sizing: border-box; height: 150px; padding: 16px; border: 1px solid #111; }\n"
            "    [data-testid='primary-panel'] { width: 220px; background: #ffffff; }\n"
            "    [data-testid='secondary-panel'] { width: 220px; margin-left: -96px; background: rgba(180, 52, 35, 0.82); color: #fff; }\n"
            "    [data-testid='layout-action'] { display: inline-flex; align-items: center; min-height: 44px; margin-top: 56px; padding: 12px 18px; }\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            "  <main data-testid=\"layout-shell\">\n"
            "    <div class=\"content-grid\" data-testid=\"layout-row\">\n"
            "      <section data-testid=\"primary-panel\">Primary panel<button data-testid=\"layout-action\">Ship</button></section>\n"
            "      <aside data-testid=\"secondary-panel\">Secondary panel overlaps the primary content.</aside>\n"
            "    </div>\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n"
        )
    else:
        html = (
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            "<head>\n"
            "  <meta charset=\"utf-8\">\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "  <title>Warning Layout</title>\n"
            "  <style>\n"
            "    body { margin: 0; font: 16px/1.4 sans-serif; background: #f3f1ea; color: #181511; }\n"
            "    [data-testid='layout-shell'] { padding: 12px; }\n"
            "    [data-testid='layout-stack'] { box-sizing: border-box; display: flex; flex-direction: column; width: 320px; padding: 24px 6px 18px 24px; border: 1px solid #111; background: #fbf8ef; }\n"
            "    [data-testid='stack-card'] { box-sizing: border-box; width: 240px; min-height: 72px; padding: 16px; border: 1px solid #111; background: #fffdfa; }\n"
            "    [data-testid='stack-card'] + [data-testid='stack-card'] { margin-top: 12px; }\n"
            "    [data-testid='stack-card'].delayed { margin-top: 38px; }\n"
            "    [data-testid='warning-cta'] { align-self: flex-start; margin-top: 18px; padding: 6px 10px; border: 1px solid #111; background: #181511; color: #fffdfa; }\n"
            "    [data-testid='subtle-copy'] { margin-top: 10px; color: #a5a091; }\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            "  <main data-testid=\"layout-shell\">\n"
            "    <section class=\"content-grid\" data-testid=\"layout-stack\">\n"
            "      <article data-testid=\"stack-card\">Primary summary card</article>\n"
            "      <article data-testid=\"stack-card\">Secondary card with smaller gap above</article>\n"
            "      <article class=\"delayed\" data-testid=\"stack-card\">Tertiary card drifts the vertical rhythm.</article>\n"
            "      <button data-testid=\"warning-cta\">Go</button>\n"
            "      <p data-testid=\"subtle-copy\">Low-contrast helper copy should be reviewed.</p>\n"
            "    </section>\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n"
        )
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text(html, encoding="utf-8")


def _context_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("verification-auth-context")
    workspace = group_root / "workspace"
    workspace.mkdir(exist_ok=True)
    seed_workspace(workspace)
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        init_workspace(workspace)
        refresh_one = refresh_context_index(workspace)
        refresh_two = refresh_context_index(workspace)
        hit_search = search_context_index(workspace, "docker verification setup workspace", route_id="workstream", limit=4)
        miss_search = search_context_index(workspace, "quux frobnicate lattice", route_id="workstream", limit=4)
        pack_miss = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        pack_hit = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        original_fingerprint = pack_miss["workspace_context"]["workspace_fingerprint"]
        cache_root = Path(refresh_one["cache_root"])
        manifest_path = cache_root / "index_manifest.json"
        context_path = cache_root / "workspace_context.json"
        usage_path = cache_root / "usage.json"
        usage_after_hit_miss = json.loads(usage_path.read_text(encoding="utf-8"))

        (workspace / "scratch.log").write_text("unindexed dirty change\n", encoding="utf-8")
        dirty_refresh = refresh_context_index(workspace)

        selected_paths = {chunk["path"] for chunk in pack_hit["context_pack"]["selected_chunks"]}
        refresh_target = "docker-compose.yml" if "docker-compose.yml" in selected_paths else next(iter(selected_paths))
        if refresh_target == "docker-compose.yml":
            (workspace / "docker-compose.yml").write_text(
                "services:\n"
                "  postgres:\n    image: postgres:16\n"
                "  mongo:\n    image: mongo:8\n"
                "  redis:\n    image: redis:7\n"
                "  nats:\n    image: nats:2\n"
                "  mailhog:\n    image: mailhog/mailhog:v1.0.1\n",
                encoding="utf-8",
            )
        else:
            (workspace / refresh_target).write_text("# Demo Workspace\n\nUpdated context for targeted invalidation.\n", encoding="utf-8")
        edit_refresh = refresh_context_index(workspace)
        pack_after_edit = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )

        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payload["catalog_digest"] = "outdated-catalog-digest"
        manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        context_payload = json.loads(context_path.read_text(encoding="utf-8"))
        context_payload["catalog_digest"] = "outdated-catalog-digest"
        context_path.write_text(json.dumps(context_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        catalog_drift_refresh = refresh_context_index(workspace)
        pack_after_catalog_drift = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        usage_payload = json.loads(usage_path.read_text(encoding="utf-8"))
    return {
        "refresh_one": refresh_one,
        "refresh_two": refresh_two,
        "hit_search": hit_search,
        "miss_search": miss_search,
        "pack_miss": pack_miss,
        "pack_hit": pack_hit,
        "usage_after_hit_miss": usage_after_hit_miss,
        "dirty_refresh": dirty_refresh,
        "edit_refresh": edit_refresh,
        "pack_after_edit": pack_after_edit,
        "catalog_drift_refresh": catalog_drift_refresh,
        "pack_after_catalog_drift": pack_after_catalog_drift,
        "usage_payload": usage_payload,
        "original_fingerprint": original_fingerprint,
    }


def _verification_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("verification-auth-verification")
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        preflight_workspace = group_root / "preflight-workspace"
        preflight_workspace.mkdir(exist_ok=True)
        seed_workspace(preflight_workspace)
        init_workspace(preflight_workspace)
        preflight_workstream_id = create_workstream(
            preflight_workspace,
            "Helper Preflight",
            kind="feature",
            scope_summary="Exercise helper sync and helper drift preflight failures.",
        )["created_workstream_id"]
        preflight_baseline = preflight_workspace / "tests" / "visual" / "baselines" / "preflight.txt"
        preflight_baseline.parent.mkdir(parents=True, exist_ok=True)
        preflight_baseline.write_text("baseline\n", encoding="utf-8")
        contract_runner = context.plugin_root / "tests" / "e2e" / "tools" / SEMANTIC_CONTRACT_RUNNER_FILENAME
        write_verification_recipes(
            preflight_workspace,
            {
                "baseline_policy": {"canonical_baselines": "project_owned", "transient_artifacts": "external_state_only"},
                "cases": [
                    {
                        "id": "web-preflight",
                        "title": "Web helper preflight",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "changed_path_globs": ["apps/web/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            str(contract_runner),
                            "--runner",
                            "playwright-visual",
                            "--repo-root",
                            str(preflight_workspace),
                            "--artifact-name",
                            "preflight.txt",
                            "--helper-file",
                            "core/index.js",
                            "--helper-file",
                            "playwright/index.js",
                        ],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "1280x800"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-preflight-semantic.json",
                            "required_checks": ["visibility"],
                            "targets": [{"target_id": "preflight-main", "locator": {"kind": "role", "value": "main"}}],
                        },
                        "baseline": {"policy": "project-owned", "source_path": str(preflight_baseline.relative_to(preflight_workspace))},
                    }
                ],
                "suites": [{"id": "full", "title": "Full", "case_ids": ["web-preflight"]}],
            },
            workstream_id=preflight_workstream_id,
        )
        helper_before = show_verification_helper_catalog(preflight_workspace)
        unsynced_run = start_verification_case(preflight_workspace, "web-preflight", workstream_id=preflight_workstream_id)
        unsynced_run = wait_for_verification_run(preflight_workspace, unsynced_run["run_id"], timeout_seconds=20, workstream_id=preflight_workstream_id)
        helper_sync = sync_verification_helpers(preflight_workspace)
        stale_marker = read_json_file(Path(helper_sync["marker_path"]))
        stale_marker["bundle_version"] = "0.7.0"
        Path(helper_sync["marker_path"]).write_text(json.dumps(stale_marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        drift_run = start_verification_case(preflight_workspace, "web-preflight", workstream_id=preflight_workstream_id)
        drift_run = wait_for_verification_run(preflight_workspace, drift_run["run_id"], timeout_seconds=20, workstream_id=preflight_workstream_id)

        workspace = group_root / "workspace"
        workspace.mkdir(exist_ok=True)
        seed_workspace(workspace)
        init_workspace(workspace)
        workstream_id = create_workstream(
            workspace,
            "Wave 1 Verification",
            kind="feature",
            scope_summary="Exercise semantic, layout, and cancellation verification flows.",
        )["created_workstream_id"]

        baseline_root = workspace / "tests" / "visual" / "baselines"
        baseline_root.mkdir(parents=True, exist_ok=True)
        (baseline_root / "web-pass.txt").write_text("playwright-visual:web-pass\n", encoding="utf-8")
        (baseline_root / "web-pass-secondary.txt").write_text("playwright-visual:web-pass-secondary\n", encoding="utf-8")

        broken_root = workspace / "browser-layout-audit" / "broken"
        warning_root = workspace / "browser-layout-audit" / "warning"
        _write_browser_layout_fixture(broken_root, "broken")
        _write_browser_layout_fixture(warning_root, "warning")
        broken_port = reserve_local_port()
        warning_port = reserve_local_port()

        mobile_root_bounds = _rect_payload(0, 0, 360, 720)
        expo_overlap_report = _semantic_report(
            "detox-visual",
            [
                _semantic_target(
                    "home-card",
                    [
                        _semantic_check("visibility", diagnostics={"visible": True}),
                        _semantic_check(
                            "overflow_clipping",
                            diagnostics={"clipping": {"clipped": False, "target_bounds": _rect_payload(16, 24, 176, 228), "root_bounds": mobile_root_bounds}},
                        ),
                        _semantic_check("computed_styles", diagnostics={"style_tokens": {"width": 160, "height": 204, "background": "#ffffff"}, "mismatches": []}),
                        _semantic_check("layout_relations", diagnostics={"layout": {"bounds_in_root": _rect_payload(16, 24, 176, 228), "root_bounds": mobile_root_bounds}}),
                        _semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
                _semantic_target(
                    "home-overlay",
                    [
                        _semantic_check("visibility", diagnostics={"visible": True}),
                        _semantic_check(
                            "overflow_clipping",
                            diagnostics={"clipping": {"clipped": False, "target_bounds": _rect_payload(132, 80, 316, 284), "root_bounds": mobile_root_bounds}},
                        ),
                        _semantic_check("computed_styles", diagnostics={"style_tokens": {"width": 184, "height": 204, "background": "#f75f49"}, "mismatches": []}),
                        _semantic_check("layout_relations", diagnostics={"layout": {"bounds_in_root": _rect_payload(132, 80, 316, 284), "root_bounds": mobile_root_bounds}}),
                        _semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
            ],
        )
        expo_gutter_warning_report = _semantic_report(
            "detox-visual",
            [
                _semantic_target(
                    "home-left-card",
                    [
                        _semantic_check("visibility", diagnostics={"visible": True}),
                        _semantic_check(
                            "overflow_clipping",
                            diagnostics={"clipping": {"clipped": False, "target_bounds": _rect_payload(24, 24, 192, 228), "root_bounds": mobile_root_bounds}},
                        ),
                        _semantic_check("computed_styles", diagnostics={"style_tokens": {"width": 168, "height": 204, "background": "#ffffff"}, "mismatches": []}),
                        _semantic_check("layout_relations", diagnostics={"layout": {"bounds_in_root": _rect_payload(24, 24, 192, 228), "root_bounds": mobile_root_bounds}}),
                        _semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
                _semantic_target(
                    "home-right-card",
                    [
                        _semantic_check("visibility", diagnostics={"visible": True}),
                        _semantic_check(
                            "overflow_clipping",
                            diagnostics={"clipping": {"clipped": False, "target_bounds": _rect_payload(208, 24, 354, 228), "root_bounds": mobile_root_bounds}},
                        ),
                        _semantic_check("computed_styles", diagnostics={"style_tokens": {"width": 146, "height": 204, "background": "#dfe8db"}, "mismatches": []}),
                        _semantic_check("layout_relations", diagnostics={"layout": {"bounds_in_root": _rect_payload(208, 24, 354, 228), "root_bounds": mobile_root_bounds}}),
                        _semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
            ],
        )
        semantic_fail_report = {
            "schema_version": 2,
            "runner": "playwright-visual",
            "helper_bundle_version": "0.8.0",
            "summary": {"status": "failed", "message": "required semantic checks failed"},
            "targets": [
                {
                    "target_id": "semantic-main",
                    "status": "failed",
                    "checks": [
                        {"id": "visibility", "status": "passed", "diagnostics": {}},
                        {"id": "layout_relations", "status": "failed", "diagnostics": {"layout": {"bounds_in_root": _rect_payload(10, 10, 200, 80), "root_bounds": _rect_payload(0, 0, 1280, 800)}}},
                    ],
                }
            ],
        }
        write_verification_recipes(
            workspace,
            {
                "baseline_policy": {"canonical_baselines": "project_owned", "transient_artifacts": "external_state_only"},
                "cases": [
                    {
                        "id": "web-pass",
                        "title": "Web pass",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "surface_ids": ["dashboard-home"],
                        "changed_path_globs": ["apps/web/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            str(contract_runner),
                            "--runner",
                            "playwright-visual",
                            "--repo-root",
                            str(workspace),
                            "--artifact-name",
                            "web-pass.txt",
                            "--helper-file",
                            "core/index.js",
                            "--helper-file",
                            "playwright/index.js",
                        ],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "1280x800"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-pass-semantic.json",
                            "required_checks": ["visibility", "layout_relations"],
                            "targets": [{"target_id": "web-pass-main", "locator": {"kind": "role", "value": "main"}}],
                        },
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/web-pass.txt"},
                    },
                    {
                        "id": "web-pass-secondary",
                        "title": "Web pass secondary",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "surface_ids": ["checkout-summary"],
                        "changed_path_globs": ["apps/web/routes/checkout.tsx"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            str(contract_runner),
                            "--runner",
                            "playwright-visual",
                            "--repo-root",
                            str(workspace),
                            "--artifact-name",
                            "web-pass-secondary.txt",
                            "--helper-file",
                            "core/index.js",
                            "--helper-file",
                            "playwright/index.js",
                        ],
                        "target": {"route": "/checkout"},
                        "device_or_viewport": {"viewport": "1280x800"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-pass-secondary-semantic.json",
                            "required_checks": ["visibility", "layout_relations"],
                            "targets": [{"target_id": "web-pass-secondary-main", "locator": {"kind": "role", "value": "main"}}],
                        },
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/web-pass-secondary.txt"},
                    },
                    {
                        "id": "web-semantic-fail",
                        "title": "Web semantic fail",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "changed_path_globs": ["apps/web/**"],
                        "host_requirements": ["python"],
                        "argv": _json_writer_argv("web-semantic-fail.json", semantic_fail_report, "web semantic fail"),
                        "target": {"route": "/semantic"},
                        "device_or_viewport": {"viewport": "1280x800"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-semantic-fail.json",
                            "required_checks": ["visibility", "layout_relations"],
                            "targets": [{"target_id": "semantic-main", "locator": {"kind": "role", "value": "main"}}],
                        },
                    },
                    {
                        "id": "browser-layout-overlap",
                        "title": "Browser overlap",
                        "surface_type": "web",
                        "runner": "browser-layout-audit",
                        "changed_path_globs": ["browser-layout-audit/broken/**"],
                        "host_requirements": ["python", "web", "browser-runtime"],
                        "cwd": str(broken_root.relative_to(workspace)),
                        "argv": [sys.executable, "-m", "http.server", str(broken_port), "--bind", "127.0.0.1"],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "360x280"},
                        "readiness_probe": {"type": "http", "url": f"http://127.0.0.1:{broken_port}/", "timeout_seconds": 10},
                        "browser_layout_audit": {
                            "base_url": f"http://127.0.0.1:{broken_port}/",
                            "report_path": "browser-layout-overlap.json",
                            "screenshot_path": "browser-layout-overlap.png",
                            "wait_timeout_ms": 8000,
                            "settle_ms": 300,
                        },
                    },
                    {
                        "id": "browser-layout-warning",
                        "title": "Browser warning",
                        "surface_type": "web",
                        "runner": "browser-layout-audit",
                        "changed_path_globs": ["browser-layout-audit/warning/**"],
                        "host_requirements": ["python", "web", "browser-runtime"],
                        "cwd": str(warning_root.relative_to(workspace)),
                        "argv": [sys.executable, "-m", "http.server", str(warning_port), "--bind", "127.0.0.1"],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "390x420"},
                        "readiness_probe": {"type": "http", "url": f"http://127.0.0.1:{warning_port}/", "timeout_seconds": 10},
                        "browser_layout_audit": {
                            "base_url": f"http://127.0.0.1:{warning_port}/",
                            "report_path": "browser-layout-warning.json",
                            "screenshot_path": "browser-layout-warning.png",
                            "wait_timeout_ms": 8000,
                            "settle_ms": 300,
                        },
                    },
                    {
                        "id": "expo-native-layout-overlap",
                        "title": "Expo native overlap",
                        "surface_type": "mobile",
                        "runner": "detox-visual",
                        "changed_path_globs": ["apps/mobile/**"],
                        "host_requirements": ["python"],
                        "argv": _json_writer_argv("expo-native-layout-overlap.json", expo_overlap_report, "expo native overlap"),
                        "target": {"screen_id": "layout-overlap"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "expo-native-layout-overlap.json",
                            "required_checks": ["overflow_clipping", "computed_styles", "layout_relations", "occlusion"],
                            "targets": [
                                {"target_id": "home-card", "locator": {"kind": "test_id", "value": "home-card"}},
                                {"target_id": "home-overlay", "locator": {"kind": "test_id", "value": "home-overlay"}},
                            ],
                        },
                        "native_layout_audit": {
                            "enabled": True,
                            "report_path": "expo-native-layout-overlap-audit.json",
                            "required_checks": ["visibility", "overflow_clipping", "computed_styles", "layout_relations", "occlusion"],
                        },
                    },
                    {
                        "id": "expo-native-gutter-warning",
                        "title": "Expo native warning",
                        "surface_type": "mobile",
                        "runner": "detox-visual",
                        "changed_path_globs": ["apps/mobile/**"],
                        "host_requirements": ["python"],
                        "argv": _json_writer_argv("expo-native-gutter-warning.json", expo_gutter_warning_report, "expo native warning"),
                        "target": {"screen_id": "layout-warning"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "expo-native-gutter-warning.json",
                            "required_checks": ["overflow_clipping", "computed_styles", "layout_relations", "occlusion"],
                            "targets": [
                                {"target_id": "home-left-card", "locator": {"kind": "test_id", "value": "home-left-card"}},
                                {"target_id": "home-right-card", "locator": {"kind": "test_id", "value": "home-right-card"}},
                            ],
                        },
                        "native_layout_audit": {
                            "enabled": True,
                            "report_path": "expo-native-gutter-warning-audit.json",
                            "required_checks": ["visibility", "overflow_clipping", "computed_styles", "layout_relations", "occlusion"],
                        },
                    },
                    {
                        "id": "slow-shell",
                        "title": "Slow shell case",
                        "surface_type": "service",
                        "runner": "shell-contract",
                        "changed_path_globs": ["scripts/**"],
                        "host_requirements": ["python"],
                        "argv": [sys.executable, "-c", "import sys,time; print('slow case start'); sys.stdout.flush(); time.sleep(30)"],
                    },
                ],
                "suites": [{"id": "full", "title": "Full", "case_ids": ["web-pass", "web-pass-secondary"]}],
            },
            workstream_id=workstream_id,
        )
        recipes = read_verification_recipes(workspace, workstream_id=workstream_id)
        helper_sync_main = sync_verification_helpers(workspace)
        web_pass_run = start_verification_case(workspace, "web-pass", wait=True, workstream_id=workstream_id)
        semantic_fail_run = start_verification_case(workspace, "web-semantic-fail", wait=True, workstream_id=workstream_id)
        browser_fail_run = start_verification_case(workspace, "browser-layout-overlap", wait=True, workstream_id=workstream_id)
        browser_warn_run = start_verification_case(workspace, "browser-layout-warning", wait=True, workstream_id=workstream_id)
        native_fail_run = start_verification_case(workspace, "expo-native-layout-overlap", wait=True, workstream_id=workstream_id)
        native_warn_run = start_verification_case(workspace, "expo-native-gutter-warning", wait=True, workstream_id=workstream_id)
        slow_run = start_verification_case(workspace, "slow-shell", workstream_id=workstream_id)
        active_run, run_started_events = wait_for_run_started(workspace, slow_run["run_id"], workstream_id=workstream_id, timeout_seconds=10)
        time_wait_log = read_verification_log_tail(workspace, slow_run["run_id"], "stdout", 20, workstream_id=workstream_id)
        cancel_payload = cancel_verification_run(workspace, slow_run["run_id"], workstream_id=workstream_id)
        cancelled_run = wait_for_verification_run(workspace, slow_run["run_id"], timeout_seconds=10, workstream_id=workstream_id)
        cancelled_events = read_verification_events(workspace, slow_run["run_id"], limit=20, workstream_id=workstream_id)
    return {
        "helper_before": helper_before,
        "helper_sync": helper_sync,
        "unsynced_run": unsynced_run,
        "drift_run": drift_run,
        "recipes": recipes,
        "helper_sync_main": helper_sync_main,
        "web_pass_run": web_pass_run,
        "semantic_fail_run": semantic_fail_run,
        "browser_fail_run": browser_fail_run,
        "browser_warn_run": browser_warn_run,
        "native_fail_run": native_fail_run,
        "native_warn_run": native_warn_run,
        "active_run": active_run,
        "run_started_events": run_started_events,
        "slow_log": time_wait_log,
        "cancel_payload": cancel_payload,
        "cancelled_run": cancelled_run,
        "cancelled_events": cancelled_events,
    }


def _resolver_script_body() -> str:
    return (
        "from __future__ import annotations\n"
        "import json\n"
        "import sys\n"
        "from datetime import datetime, timedelta, timezone\n"
        "\n"
        "def iso_after(seconds: int) -> str:\n"
        "    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')\n"
        "\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "reason = payload.get('resolution_reason') or 'initial'\n"
        "request_mode = payload.get('request_mode') or 'read_only'\n"
        "action_tags = payload.get('action_tags') or []\n"
        "secret_payload = payload.get('secret_payload') or {}\n"
        "cached_payload = payload.get('cached_session_secret_payload') or {}\n"
        "if not cached_payload:\n"
        "    cached_payload = (payload.get('cached_session_secret_record') or {}).get('payload') or {}\n"
        "context_overrides = payload.get('context_overrides') or {}\n"
        "subject_ref = context_overrides.get('subject_ref') or cached_payload.get('subject_ref') or secret_payload.get('login') or 'neutral-subject'\n"
        "if reason in {'refresh', 'manual_seed'}:\n"
        "    access_token = f'{reason}-access'\n"
        "    refresh_token = cached_payload.get('refresh_token') or secret_payload.get('refresh_token') or 'resolver-refresh'\n"
        "else:\n"
        "    access_token = 'initial-access'\n"
        "    refresh_token = secret_payload.get('refresh_token') or 'initial-refresh'\n"
        "artifact_payload = {\n"
        "    'access_token': access_token,\n"
        "    'refresh_token': refresh_token,\n"
        "    'token_type': 'Bearer',\n"
        "    'access_expires_at': iso_after(900),\n"
        "    'refresh_expires_at': iso_after(3600),\n"
        "    'base_url': 'https://neutral.example.test',\n"
        "    'subject_ref': subject_ref,\n"
        "    'headers': {'X-Resolver-Mode': reason},\n"
        "}\n"
        "print(json.dumps({\n"
        "    'artifact': {\n"
        "        'artifact_type': 'token_bundle',\n"
        "        'expires_at': artifact_payload['access_expires_at'],\n"
        "        'payload': artifact_payload,\n"
        "    },\n"
        "    'session_persistence': {\n"
        "        'persist': True,\n"
        "        'request_mode': request_mode,\n"
        "        'action_tags': action_tags,\n"
        "        'access_expires_at': artifact_payload['access_expires_at'],\n"
        "        'refresh_expires_at': artifact_payload['refresh_expires_at'],\n"
        "        'secret_payload': artifact_payload,\n"
        "    },\n"
        "    'session_summary': {\n"
        "        'resolution_reason': reason,\n"
        "        'subject_ref': subject_ref,\n"
        "    },\n"
        "}))\n"
    )


def _auth_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("verification-auth-auth")
    workspace = group_root / "workspace"
    workspace.mkdir(exist_ok=True)
    seed_workspace(workspace)
    resolver_script = group_root / "auth_resolver_v2.py"
    resolver_script.write_text(_resolver_script_body(), encoding="utf-8")
    env = {
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        init_workspace(workspace)
        write_auth_profile(
            workspace,
            {
                "profile_id": "smoke-auth",
                "label": "Smoke auth",
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
        write_auth_profile(
            workspace,
            {
                "profile_id": "resolver-auth",
                "label": "Resolver auth",
                "scope_type": "workspace",
                "resolver": {"kind": "command_v2", "argv": [sys.executable, str(resolver_script)], "cwd": ".", "timeout_seconds": 10},
                "usage_policy": {
                    "default_request_mode": "read_only",
                    "allowed_request_modes": ["read_only"],
                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                    "action_tags": ["tag.read"],
                    "allow_session_persistence": True,
                    "allow_session_refresh": True,
                },
            },
            secret_payload={"login": "reader@example.com", "password": "reader-password", "refresh_token": "profile-refresh-token"},
        )
        write_auth_profile(
            workspace,
            {
                "profile_id": "mutating-auth",
                "label": "Mutating auth",
                "scope_type": "workspace",
                "resolver": {"kind": "command_v2", "argv": [sys.executable, str(resolver_script)], "cwd": ".", "timeout_seconds": 10},
                "usage_policy": {
                    "default_request_mode": "read_only",
                    "allowed_request_modes": ["read_only", "mutating"],
                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                    "action_tags": ["tag.read", "tag.write"],
                    "allow_session_persistence": True,
                    "allow_session_refresh": True,
                },
            },
            secret_payload={"login": "writer@example.com", "password": "writer-password", "refresh_token": "writer-refresh-token"},
        )
        write_auth_profile(
            workspace,
            {
                "profile_id": "binding-auth",
                "label": "Binding auth",
                "scope_type": "workspace",
                "resolver": {"kind": "command_v2", "argv": [sys.executable, str(resolver_script)], "cwd": ".", "timeout_seconds": 10},
                "usage_policy": {
                    "default_request_mode": "read_only",
                    "allowed_request_modes": ["read_only"],
                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                    "action_tags": ["tag.read"],
                    "allow_session_persistence": True,
                    "allow_session_refresh": True,
                },
            },
            secret_payload={"login": "binding@example.com", "password": "binding-password", "refresh_token": "binding-refresh-token"},
        )
        manual_access_expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        manual_refresh_expires = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        resolver_session = write_auth_session(
            workspace,
            {
                "profile_id": "resolver-auth",
                "source_kind": "manual",
                "request_mode": "read_only",
                "action_tags": ["tag.read"],
                "summary": {"seed_kind": "token_bundle"},
            },
            secret_payload={
                "access_token": "seed-access",
                "refresh_token": "seed-refresh",
                "token_type": "Bearer",
                "access_expires_at": manual_access_expires,
                "refresh_expires_at": manual_refresh_expires,
                "subject_ref": "dashboard-seed",
            },
        )
        resolver_session_id = resolver_session["session"]["session_id"]
        binding_session = write_auth_session(
            workspace,
            {
                "profile_id": "binding-auth",
                "source_kind": "manual",
                "request_mode": "read_only",
                "action_tags": ["tag.read"],
                "session_binding": {
                    "primary_ref": "backend.shared",
                    "refs": ["backend.shared", "https://neutral-a.example.test", "https://neutral-b.example.test"],
                },
                "summary": {"seed_kind": "token_bundle"},
            },
            secret_payload={
                "access_token": "binding-seed-access",
                "refresh_token": "binding-seed-refresh",
                "token_type": "Bearer",
                "access_expires_at": manual_access_expires,
                "refresh_expires_at": manual_refresh_expires,
                "subject_ref": "binding-dashboard-seed",
            },
        )
        binding_session_id = binding_session["session"]["session_id"]
        mutating_session = write_auth_session(
            workspace,
            {
                "profile_id": "mutating-auth",
                "source_kind": "manual",
                "request_mode": "mutating",
                "action_tags": ["tag.read", "tag.write"],
            },
            secret_payload={
                "access_token": "writer-access",
                "refresh_token": "writer-refresh",
                "token_type": "Bearer",
                "access_expires_at": manual_access_expires,
                "refresh_expires_at": manual_refresh_expires,
                "subject_ref": "writer-subject",
            },
        )
        mutating_session_id = mutating_session["session"]["session_id"]
        profiles = show_auth_profiles(workspace)
        sessions = list_auth_sessions(workspace)
        session_detail = get_auth_session(workspace, resolver_session_id)
        auth_preview = resolve_auth_profile(workspace, profile_id="smoke-auth", surface_mode="cli")
        resolver_preview = resolve_auth_profile(
            workspace,
            profile_id="resolver-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            prefer_cached=True,
            surface_mode="cli",
        )
        refreshed_preview = resolve_auth_profile(
            workspace,
            profile_id="resolver-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            context_overrides={"subject_ref": "dashboard-refresh"},
            prefer_cached=False,
            force_refresh=True,
            surface_mode="cli",
        )
        binding_preview = resolve_auth_profile(
            workspace,
            profile_id="binding-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            session_binding={"refs": ["backend.shared", "https://neutral-b.example.test"]},
            prefer_cached=True,
            surface_mode="cli",
        )
        isolated_resolve = resolve_auth_profile(
            workspace,
            profile_id="binding-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            session_binding={"primary_ref": "backend.isolated", "refs": ["backend.isolated", "https://isolated.example.test"]},
            surface_mode="cli",
        )
        isolated_reuse = resolve_auth_profile(
            workspace,
            profile_id="binding-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            session_binding={"refs": ["backend.isolated", "https://isolated.example.test"]},
            surface_mode="cli",
        )
        allowed_mutating_preview = resolve_auth_profile(
            workspace,
            profile_id="mutating-auth",
            request_mode="mutating",
            action_tags=["tag.write"],
            surface_mode="cli",
        )
        try:
            resolve_auth_profile(workspace, profile_id="smoke-auth", request_mode="mutating", surface_mode="cli")
        except (PermissionError, ValueError) as exc:
            mutating_blocked_error = str(exc)
        else:
            raise AssertionError("Expected read-only auth preview to be rejected for mutating requests")
        try:
            resolve_auth_profile(
                workspace,
                profile_id="mutating-auth",
                request_mode="mutating",
                action_tags=["tag.blocked"],
                surface_mode="cli",
            )
        except (PermissionError, ValueError) as exc:
            action_tag_error = str(exc)
        else:
            raise AssertionError("Expected auth action tag policy rejection")
        temporary_session = write_auth_session(
            workspace,
            {"profile_id": "resolver-auth", "source_kind": "manual", "request_mode": "read_only", "action_tags": ["tag.read"]},
            secret_payload={
                "access_token": "temporary-access",
                "refresh_token": "temporary-refresh",
                "token_type": "Bearer",
                "access_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "refresh_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            },
        )
        temporary_session_id = temporary_session["session"]["session_id"]
        invalidated_session = invalidate_auth_session(workspace, temporary_session_id)
        removed_session = remove_auth_session(workspace, temporary_session_id)
    return {
        "profiles": profiles,
        "sessions": sessions,
        "session_detail": session_detail,
        "auth_preview": auth_preview,
        "resolver_preview": resolver_preview,
        "refreshed_preview": refreshed_preview,
        "binding_preview": binding_preview,
        "isolated_resolve": isolated_resolve,
        "isolated_reuse": isolated_reuse,
        "allowed_mutating_preview": allowed_mutating_preview,
        "mutating_blocked_error": mutating_blocked_error,
        "action_tag_error": action_tag_error,
        "resolver_session_id": resolver_session_id,
        "binding_session_id": binding_session_id,
        "mutating_session_id": mutating_session_id,
        "invalidated_session": invalidated_session,
        "removed_session": removed_session,
    }


def _case_context_search_hit_miss(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CONTEXT_GROUP, _context_group)
    hit_scores = [int(match.get("score") or 0) for match in group["hit_search"]["matches"]]
    miss_scores = [int(match.get("score") or 0) for match in group["miss_search"]["matches"]]
    hit_paths = {match.get("path") for match in group["hit_search"]["matches"]}
    usage = group["usage_after_hit_miss"]
    assert group["refresh_one"]["status"] == "refreshed"
    assert group["refresh_two"]["status"] == "fresh"
    assert group["hit_search"]["matches"]
    assert len(group["miss_search"]["matches"]) <= len(group["hit_search"]["matches"])
    assert max(hit_scores) > max(miss_scores, default=0)
    assert "docker-compose.yml" in hit_paths
    assert group["pack_miss"]["cache_status"] == "miss"
    assert group["pack_hit"]["cache_status"] == "hit"
    assert int(usage["context_pack_miss_count"]) == 1
    assert int(usage["context_pack_hit_count"]) == 1
    assert int(usage["last_context_pack_selected_tool_count"]) == len(group["pack_hit"]["context_pack"]["selected_tools"])
    assert int(usage["last_refresh_duration_ms"]) <= 3000
    search_budget = _budget_result(group["hit_search"])
    context_pack_budget = _budget_result(group["pack_miss"])
    return {
        "hit_count": len(group["hit_search"]["matches"]),
        "miss_count": len(group["miss_search"]["matches"]),
        "pack_statuses": [group["pack_miss"]["cache_status"], group["pack_hit"]["cache_status"]],
        "budget_results": {
            "search_context_index": search_budget,
            "show_workspace_context_pack": context_pack_budget,
        },
    }


def _case_context_index_drift_and_pruning(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(CONTEXT_GROUP, _context_group)
    assert group["dirty_refresh"]["status"] in {"fresh", "refreshed"}
    assert int(group["dirty_refresh"]["pruned_semantic_cache_entries"] or 0) == 0
    assert group["edit_refresh"]["pruned_semantic_cache_reason"] == "source-hash-drift"
    assert group["edit_refresh"]["pruned_semantic_cache_entries"] >= 1
    assert group["pack_after_edit"]["cache_status"] == "miss"
    assert group["catalog_drift_refresh"]["pruned_semantic_cache_reason"] == "catalog-digest"
    assert group["catalog_drift_refresh"]["pruned_semantic_cache_entries"] >= 1
    assert group["pack_after_catalog_drift"]["cache_status"] == "miss"
    return {
        "dirty_reason": group["dirty_refresh"]["refresh_reason"],
        "edit_prune_reason": group["edit_refresh"]["pruned_semantic_cache_reason"],
        "catalog_prune_reason": group["catalog_drift_refresh"]["pruned_semantic_cache_reason"],
        "fresh_hit_count": group["usage_payload"]["fresh_hit_count"],
    }


def _case_helper_catalog_sync_preflight(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_GROUP, _verification_group)
    assert group["helper_before"]["version_status"] == "not_synced"
    assert group["unsynced_run"]["status"] == "failed"
    assert group["unsynced_run"]["cases"][0]["semantic_assertions"]["reason"] == "helper_bundle_not_synced"
    assert group["helper_sync"]["materialization"]["status"] == "synced"
    assert group["drift_run"]["status"] == "failed"
    assert group["drift_run"]["cases"][0]["semantic_assertions"]["reason"] == "helper_bundle_version_drift"
    return {
        "helper_status_before": group["helper_before"]["version_status"],
        "helper_status_after_sync": group["helper_sync"]["materialization"]["status"],
        "unsynced_reason": group["unsynced_run"]["cases"][0]["semantic_assertions"]["reason"],
        "drift_reason": group["drift_run"]["cases"][0]["semantic_assertions"]["reason"],
    }


def _case_verification_recipe_write_read(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_GROUP, _verification_group)
    case_ids = {case["id"] for case in group["recipes"]["cases"]}
    assert {"web-pass", "web-semantic-fail", "browser-layout-overlap", "browser-layout-warning", "expo-native-layout-overlap", "expo-native-gutter-warning", "slow-shell"}.issubset(case_ids)
    return {"case_count": len(case_ids), "suite_ids": [suite["id"] for suite in group["recipes"]["suites"]]}


def _case_verification_case_pass_playwright(context: ExecutionContext) -> dict[str, Any]:
    run = context.group(VERIFICATION_GROUP, _verification_group)["web_pass_run"]
    assert run["status"] == "passed"
    assert run["cases"][0]["semantic_assertions"]["status"] == "passed"
    return {"run_id": run["run_id"], "semantic_status": run["cases"][0]["semantic_assertions"]["status"]}


def _case_verification_case_fail_semantic(context: ExecutionContext) -> dict[str, Any]:
    run = context.group(VERIFICATION_GROUP, _verification_group)["semantic_fail_run"]
    assert run["status"] == "failed"
    assert run["cases"][0]["semantic_assertions"]["status"] == "failed"
    return {"run_id": run["run_id"], "semantic_status": run["cases"][0]["semantic_assertions"]["status"]}


def _case_verification_browser_layout_audit_fail_and_warn(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_GROUP, _verification_group)
    failed = group["browser_fail_run"]["cases"][0]["browser_layout_audit"]
    warned = group["browser_warn_run"]["cases"][0]["browser_layout_audit"]
    assert group["browser_fail_run"]["status"] == "failed"
    assert failed["status"] == "failed"
    assert int(failed["issue_count"] or 0) > 0
    assert group["browser_warn_run"]["status"] == "failed"
    assert warned["status"] == "warning"
    assert int(warned["warning_count"] or 0) >= 1
    return {"failed_issue_count": failed["issue_count"], "warning_count": warned["warning_count"]}


def _case_verification_native_layout_audit_fail_and_warn(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_GROUP, _verification_group)
    failed = group["native_fail_run"]["cases"][0]["native_layout_audit"]
    warned = group["native_warn_run"]["cases"][0]["native_layout_audit"]
    assert group["native_fail_run"]["status"] == "failed"
    assert failed["status"] == "failed"
    assert any(issue["type"] == "pair-overlap" for issue in failed["issues"])
    assert group["native_warn_run"]["status"] == "failed"
    assert warned["status"] == "warning"
    assert int(warned["warning_count"] or 0) >= 1
    return {"failed_issue_count": failed["issue_count"], "warning_count": warned["warning_count"]}


def _case_verification_log_events_wait_cancel(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(VERIFICATION_GROUP, _verification_group)
    assert group["active_run"] is not None
    assert any(event["event_type"] == "run_started" for event in group["run_started_events"]["events"])
    assert group["slow_log"]["stream"] == "stdout"
    assert isinstance(group["slow_log"]["lines"], list)
    assert group["cancel_payload"]["status"] == "cancelled"
    assert group["cancelled_run"]["status"] == "cancelled"
    assert any(event["event_type"] == "run_cancelled" for event in group["cancelled_events"]["events"])
    return {"run_id": group["cancelled_run"]["run_id"], "status": group["cancelled_run"]["status"]}


def _case_auth_profile_session_resolve(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(AUTH_GROUP, _auth_group)
    assert group["profiles"]["counts"]["total"] >= 4
    assert group["sessions"]["counts"]["total"] >= 3
    assert group["auth_preview"]["artifact"]["artifact_type"] == "credentials"
    assert group["resolver_preview"]["resolution_reason"] == "reuse"
    assert group["resolver_preview"]["session"]["session_id"] == group["resolver_session_id"]
    assert group["refreshed_preview"]["resolution_reason"] == "manual_seed"
    assert group["session_detail"]["session"]["session_id"] == group["resolver_session_id"]
    return {
        "profile_count": group["profiles"]["counts"]["total"],
        "session_count": group["sessions"]["counts"]["total"],
        "resolver_session_id": group["resolver_session_id"],
    }


def _case_auth_session_binding_reuse(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(AUTH_GROUP, _auth_group)
    assert group["binding_preview"]["resolution_reason"] == "reuse"
    assert group["binding_preview"]["session"]["session_id"] == group["binding_session_id"]
    assert group["isolated_resolve"]["resolution_reason"] == "initial"
    assert group["isolated_resolve"]["session"]["session_id"] != group["binding_session_id"]
    assert group["isolated_reuse"]["resolution_reason"] == "reuse"
    assert group["isolated_reuse"]["session"]["session_id"] == group["isolated_resolve"]["session"]["session_id"]
    return {
        "binding_session_id": group["binding_session_id"],
        "isolated_session_id": group["isolated_resolve"]["session"]["session_id"],
    }


def _case_auth_mutating_blocked(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(AUTH_GROUP, _auth_group)
    assert "not allowed" in group["mutating_blocked_error"]
    return {"error": group["mutating_blocked_error"]}


def _case_auth_action_tag_policy_blocked(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(AUTH_GROUP, _auth_group)
    assert "action_tags" in group["action_tag_error"]
    return {"error": group["action_tag_error"]}


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition("context-search-hit-miss", "source-plugin", ("verification-auth-core", "core-full-local"), ("context", "search"), _case_context_search_hit_miss),
        ScenarioDefinition("context-index-drift-and-pruning", "source-plugin", ("verification-auth-core", "core-full-local"), ("context", "drift"), _case_context_index_drift_and_pruning),
        ScenarioDefinition("helper-catalog-sync-preflight", "source-plugin", ("verification-auth-core", "core-full-local"), ("verification", "helpers"), _case_helper_catalog_sync_preflight),
        ScenarioDefinition("verification-recipe-write-read", "source-plugin", ("verification-auth-core", "core-full-local"), ("verification", "recipes"), _case_verification_recipe_write_read),
        ScenarioDefinition("verification-case-pass-playwright", "source-plugin", ("verification-auth-core", "core-full-local"), ("verification", "playwright"), _case_verification_case_pass_playwright),
        ScenarioDefinition("verification-case-fail-semantic", "source-plugin", ("verification-auth-core", "core-full-local"), ("verification", "semantic"), _case_verification_case_fail_semantic),
        ScenarioDefinition("verification-browser-layout-audit-fail-and-warn", "source-plugin", ("verification-auth-core", "core-full-local"), ("verification", "browser-layout"), _case_verification_browser_layout_audit_fail_and_warn),
        ScenarioDefinition("verification-native-layout-audit-fail-and-warn", "source-plugin", ("verification-auth-core", "core-full-local"), ("verification", "native-layout"), _case_verification_native_layout_audit_fail_and_warn),
        ScenarioDefinition("verification-log-events-wait-cancel", "source-plugin", ("verification-auth-core", "core-full-local"), ("verification", "cancel"), _case_verification_log_events_wait_cancel),
        ScenarioDefinition("auth-profile-session-resolve", "source-plugin", ("verification-auth-core", "core-full-local"), ("auth", "resolve"), _case_auth_profile_session_resolve),
        ScenarioDefinition("auth-session-binding-reuse", "source-plugin", ("verification-auth-core", "core-full-local"), ("auth", "binding"), _case_auth_session_binding_reuse),
        ScenarioDefinition("auth-mutating-blocked", "source-plugin", ("verification-auth-core", "core-full-local"), ("auth", "policy"), _case_auth_mutating_blocked),
        ScenarioDefinition("auth-action-tag-policy-blocked", "source-plugin", ("verification-auth-core", "core-full-local"), ("auth", "policy"), _case_auth_action_tag_policy_blocked),
    ]
    return {case.case_id: case for case in cases}
