from __future__ import annotations

import os
import urllib.parse
from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

from agentiux_dev_e2e_support import (
    FakeSentryServer,
    FakeYouTrackServer,
    dashboard_check,
    http_json,
    launch_dashboard_gui,
    seed_workspace,
    stop_dashboard_gui,
    temporary_env,
)
from agentiux_dev_lib import create_workstream, init_workspace


GROUP_KEY = "dashboard-core"


def _dashboard_group(context: ExecutionContext) -> dict[str, Any]:
    group_root = context.path("dashboard-core")
    workspace = group_root / "workspace"
    workspace.mkdir(exist_ok=True)
    seed_workspace(workspace)
    env = {
        **os.environ,
        "AGENTIUX_DEV_STATE_ROOT": str(group_root / "state"),
        "AGENTIUX_DEV_PLUGIN_ROOT": str(context.plugin_root),
    }
    with temporary_env(env):
        init_workspace(workspace)
        create_workstream(
            workspace,
            "Dashboard CRUD Fixture",
            kind="feature",
            scope_summary="Exercise dashboard bootstrap, singleton runtime, and CRUD endpoints.",
        )
        audit_payload = dashboard_check(workspace, context.plugin_root)
        with FakeYouTrackServer() as fake_youtrack, FakeSentryServer() as fake_sentry:
            launch_one = launch_dashboard_gui(context.plugin_root, workspace, env)
            launch_two = launch_dashboard_gui(context.plugin_root, workspace, env)
            try:
                url = launch_one["url"]
                health = http_json(f"{url}/health")
                encoded_workspace = urllib.parse.quote(str(workspace.resolve()), safe="")
                auth_profile = http_json(
                    f"{url}/api/auth/profiles",
                    method="POST",
                    payload={
                        "workspacePath": str(workspace.resolve()),
                        "profile": {
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
                        "secretPayload": {"login": "qa@example.com", "password": "qa-password"},
                    },
                )
                auth_listing = http_json(f"{url}/api/auth/profiles?workspace={encoded_workspace}")
                note_create = http_json(
                    f"{url}/api/project-notes",
                    method="POST",
                    payload={
                        "workspacePath": str(workspace.resolve()),
                        "note": {
                            "note_id": "dashboard-note",
                            "title": "Dashboard note",
                            "tags": ["dashboard", "memory"],
                            "pin_state": "pinned",
                            "source": "web",
                            "body_markdown": "Temporary bootstrap URL is required for dashboard smoke.",
                        },
                    },
                )
                note_search = http_json(
                    f"{url}/api/project-notes/search?workspace={encoded_workspace}&query={urllib.parse.quote('temporary bootstrap url')}"
                )
                learning_create = http_json(
                    f"{url}/api/learnings",
                    method="POST",
                    payload={
                        "workspacePath": str(workspace.resolve()),
                        "entry": {
                            "entry_id": "dashboard-learning",
                            "kind": "visual-review",
                            "status": "open",
                            "symptom": "Dashboard review needed repeated manual rechecks.",
                            "root_cause": "First pass lacked stored failure context.",
                            "missing_signal": "No persisted reminder for the weak first pass.",
                            "fix_applied": "Add stronger layout audits and memory entries.",
                            "prevention": "Persist the failure mode as a learning entry.",
                            "source": "web",
                        },
                    },
                )
                learning_update = http_json(
                    f"{url}/api/learnings/dashboard-learning",
                    method="PATCH",
                    payload={"workspacePath": str(workspace.resolve()), "updates": {"status": "resolved"}},
                )
                analytics_snapshot = http_json(f"{url}/api/analytics?workspace={encoded_workspace}")
                connection_create = http_json(
                    f"{url}/api/youtrack/connections",
                    method="POST",
                    payload={
                        "workspacePath": str(workspace.resolve()),
                        "label": "Dashboard tracker",
                        "connectionId": "dashboard-tracker",
                        "baseUrl": fake_youtrack.base_url,
                        "token": fake_youtrack.token,
                        "projectScope": ["SL"],
                    },
                )
                connection_test = http_json(
                    f"{url}/api/youtrack/connections/dashboard-tracker/test",
                    method="POST",
                    payload={"workspacePath": str(workspace.resolve())},
                )
                connection_update = http_json(
                    f"{url}/api/youtrack/connections",
                    method="PATCH",
                    payload={
                        "workspacePath": str(workspace.resolve()),
                        "connectionId": "dashboard-tracker",
                        "label": "Dashboard tracker updated",
                        "default": True,
                        "testConnection": False,
                    },
                )
                connection_listing = http_json(f"{url}/api/youtrack/connections?workspace={encoded_workspace}")
                connection_delete = http_json(
                    f"{url}/api/youtrack/connections",
                    method="DELETE",
                    payload={"workspacePath": str(workspace.resolve()), "connectionId": "dashboard-tracker"},
                )
                sentry_create = http_json(
                    f"{url}/api/sentry/connections",
                    method="POST",
                    payload={
                        "workspacePath": str(workspace.resolve()),
                        "label": "Dashboard Sentry",
                        "connectionId": "dashboard-sentry",
                        "baseUrl": fake_sentry.base_url,
                        "token": fake_sentry.token,
                    },
                )
                sentry_test = http_json(
                    f"{url}/api/sentry/connections/dashboard-sentry/test",
                    method="POST",
                    payload={"workspacePath": str(workspace.resolve())},
                )
                sentry_update = http_json(
                    f"{url}/api/sentry/connections",
                    method="PATCH",
                    payload={
                        "workspacePath": str(workspace.resolve()),
                        "connectionId": "dashboard-sentry",
                        "label": "Dashboard Sentry updated",
                        "default": True,
                        "testConnection": False,
                    },
                )
                sentry_listing = http_json(f"{url}/api/sentry/connections?workspace={encoded_workspace}")
                sentry_delete = http_json(
                    f"{url}/api/sentry/connections",
                    method="DELETE",
                    payload={"workspacePath": str(workspace.resolve()), "connectionId": "dashboard-sentry"},
                )
            finally:
                stop_payload = stop_dashboard_gui(context.plugin_root, workspace, env)
    return {
        "audit": audit_payload,
        "launch_one": launch_one,
        "launch_two": launch_two,
        "health": health,
        "stop_payload": stop_payload,
        "auth_profile": auth_profile,
        "auth_listing": auth_listing,
        "note_create": note_create,
        "note_search": note_search,
        "learning_create": learning_create,
        "learning_update": learning_update,
        "analytics_snapshot": analytics_snapshot,
        "connection_create": connection_create,
        "connection_test": connection_test,
        "connection_update": connection_update,
        "connection_listing": connection_listing,
        "connection_delete": connection_delete,
        "sentry_create": sentry_create,
        "sentry_test": sentry_test,
        "sentry_update": sentry_update,
        "sentry_listing": sentry_listing,
        "sentry_delete": sentry_delete,
    }


def _case_dashboard_overview_bootstrap_panel(context: ExecutionContext) -> dict[str, Any]:
    audit = context.group(GROUP_KEY, _dashboard_group)["audit"]
    assert audit["health"]["ok"] is True
    assert audit["schema_version"] == 2
    assert audit["payload_bytes"]["bootstrap"] < audit["payload_bytes"]["legacy_combined"]
    return {
        "schema_version": audit["schema_version"],
        "bootstrap_bytes": audit["payload_bytes"]["bootstrap"],
        "legacy_combined_bytes": audit["payload_bytes"]["legacy_combined"],
    }


def _case_dashboard_deeplink_history_singleton(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _dashboard_group)
    audit = group["audit"]
    assert audit["deep_link_assertions"]["overview"]["route_hash_preserved"] is True
    assert audit["deep_link_assertions"]["workspace_plan"]["panel_query_preserved"] is True
    assert audit["deep_link_assertions"]["history_navigation"]["back_restored_previous_panel"] is True
    assert group["launch_one"]["url"] == group["launch_two"]["url"]
    return {
        "singleton_url": group["launch_one"]["url"],
        "overview_hash_preserved": audit["deep_link_assertions"]["overview"]["route_hash_preserved"],
    }


def _case_dashboard_crud_integrations_memory(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _dashboard_group)
    assert group["auth_profile"]["profile"]["profile_id"] == "dashboard-auth"
    assert group["auth_listing"]["counts"]["total"] >= 1
    assert "qa-password" not in str(group["auth_listing"])
    assert group["note_create"]["note"]["note_id"] == "dashboard-note"
    assert any(item["note_id"] == "dashboard-note" for item in group["note_search"]["matches"])
    assert group["learning_create"]["entry"]["entry_id"] == "dashboard-learning"
    assert group["learning_update"]["entry"]["status"] == "resolved"
    assert group["analytics_snapshot"]["learning_counts"]["resolved"] >= 1
    assert group["connection_create"]["created_connection_id"] == "dashboard-tracker"
    assert group["connection_test"]["connection"]["status"] == "connected"
    assert group["connection_listing"]["default_connection_id"] == "dashboard-tracker"
    assert group["connection_delete"]["removed_connection_id"] == "dashboard-tracker"
    assert group["sentry_create"]["created_connection_id"] == "dashboard-sentry"
    assert group["sentry_test"]["connection"]["status"] == "connected"
    assert group["sentry_update"]["connection"]["label"] == "Dashboard Sentry updated"
    assert group["sentry_listing"]["default_connection_id"] == "dashboard-sentry"
    assert group["sentry_delete"]["removed_connection_id"] == "dashboard-sentry"
    return {
        "auth_profile_id": group["auth_profile"]["profile"]["profile_id"],
        "note_id": group["note_create"]["note"]["note_id"],
        "learning_id": group["learning_create"]["entry"]["entry_id"],
        "connection_id": group["connection_create"]["created_connection_id"],
        "sentry_connection_id": group["sentry_create"]["created_connection_id"],
    }


def _case_dashboard_runtime_launch_stop_url(context: ExecutionContext) -> dict[str, Any]:
    group = context.group(GROUP_KEY, _dashboard_group)
    assert group["launch_one"]["url"].startswith("http://127.0.0.1:")
    assert group["health"]["ok"] is True
    assert group["stop_payload"]["status"] in {"stopped", "ok"}
    return {"url": group["launch_one"]["url"], "stop_status": group["stop_payload"]["status"]}


def _case_dashboard_performance_audit(context: ExecutionContext) -> dict[str, Any]:
    audit = context.group(GROUP_KEY, _dashboard_group)["audit"]
    assert audit["request_timings_ms"]["bootstrap"] < audit["request_timings_ms"]["legacy_combined"]
    assert audit["payload_bytes"]["bootstrap"] < audit["payload_bytes"]["legacy_combined"]
    assert all(metrics["first_usable_render"] is not None for metrics in audit["render_timings_ms"].values())
    assert all(result["within_budget"] for result in audit["budget_results"].values())
    return {
        "bootstrap_ms": audit["request_timings_ms"]["bootstrap"],
        "legacy_combined_ms": audit["request_timings_ms"]["legacy_combined"],
        "budget_results": audit["budget_results"],
    }


def _case_dashboard_request_path_telemetry(context: ExecutionContext) -> dict[str, Any]:
    audit = context.group(GROUP_KEY, _dashboard_group)["audit"]
    request_counts = audit["request_counts"]
    history_counts = audit["history_request_counts"]
    assert request_counts
    for counts in request_counts.values():
        assert int(counts.get("bootstrap") or 0) == 1
        assert int(counts.get("overview") or 0) == 0
        assert int(counts.get("cockpit") or 0) == 0
    assert int((history_counts.get("initial") or {}).get("bootstrap") or 0) == 1
    initial_panel_count = int((history_counts.get("initial") or {}).get("panel") or 0)
    after_plan_panel_count = int((history_counts.get("after_plan") or {}).get("panel") or 0)
    assert after_plan_panel_count >= initial_panel_count
    assert int((history_counts.get("after_cached_plan") or {}).get("panel") or 0) == after_plan_panel_count
    assert int((history_counts.get("after_back") or {}).get("panel") or 0) == after_plan_panel_count
    assert int((history_counts.get("after_forward") or {}).get("panel") or 0) == after_plan_panel_count
    return {"request_counts": request_counts, "history_request_counts": history_counts}


def _case_dashboard_responsive_layout_audit(context: ExecutionContext) -> dict[str, Any]:
    audits = context.group(GROUP_KEY, _dashboard_group)["audit"]["audits"]
    desktop = next(item for item in audits if item.get("label") == "cockpit-now-desktop")
    mobile = next(item for item in audits if item.get("label") == "cockpit-now-mobile")
    assert int(desktop.get("issue_count") or 0) == 0
    assert int(mobile.get("issue_count") or 0) == 0
    assert str(desktop.get("status") or "").lower() != "failed"
    assert str(mobile.get("status") or "").lower() != "failed"
    return {"desktop_status": desktop.get("status"), "mobile_status": mobile.get("status")}


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition("dashboard-overview-bootstrap-panel", "source-plugin", ("dashboard-core", "core-full-local"), ("dashboard", "bootstrap"), _case_dashboard_overview_bootstrap_panel),
        ScenarioDefinition("dashboard-deeplink-history-singleton", "source-plugin", ("dashboard-core", "core-full-local"), ("dashboard", "deeplink", "singleton"), _case_dashboard_deeplink_history_singleton),
        ScenarioDefinition("dashboard-crud-integrations-memory", "source-plugin", ("dashboard-core", "core-full-local"), ("dashboard", "crud"), _case_dashboard_crud_integrations_memory),
        ScenarioDefinition("dashboard-runtime-launch-stop-url", "source-plugin", ("dashboard-core", "core-full-local"), ("dashboard", "runtime"), _case_dashboard_runtime_launch_stop_url),
        ScenarioDefinition("dashboard-performance-audit", "source-plugin", ("dashboard-core", "core-full-local"), ("dashboard", "performance"), _case_dashboard_performance_audit),
        ScenarioDefinition("dashboard-request-path-telemetry", "source-plugin", ("dashboard-core", "core-full-local"), ("dashboard", "telemetry"), _case_dashboard_request_path_telemetry),
        ScenarioDefinition("dashboard-responsive-layout-audit", "source-plugin", ("dashboard-core", "core-full-local"), ("dashboard", "layout"), _case_dashboard_responsive_layout_audit),
    ]
    return {case.case_id: case for case in cases}
