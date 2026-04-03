#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from agentiux_dev_lib import (
    PLUGIN_NAME,
    create_workstream,
    dashboard_snapshot,
    init_workspace,
    preview_workspace_init,
    python_launcher_string,
    python_launcher_tokens,
    python_script_command,
)
from agentiux_dev_context import refresh_context_index, search_context_index, show_context_structure, show_workspace_context_pack
from agentiux_dev_verification import audit_verification_coverage
from build_context_catalogs import check_catalogs


DISCOVERY_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}


def default_repo_root() -> Path:
    script_plugin_root = Path(__file__).resolve().parents[1]
    if script_plugin_root.parent.name == "plugins":
        return script_plugin_root.parent.parent.resolve()
    return script_plugin_root.resolve()


def resolve_plugin_root(repo_root: Path) -> Path:
    candidate = repo_root / "plugins" / PLUGIN_NAME
    if (candidate / ".codex-plugin" / "plugin.json").exists():
        return candidate.resolve()
    if (repo_root / ".codex-plugin" / "plugin.json").exists():
        return repo_root.resolve()
    fallback = Path(__file__).resolve().parents[1]
    if (fallback / ".codex-plugin" / "plugin.json").exists():
        return fallback.resolve()
    raise FileNotFoundError(f"Unable to resolve plugin root from repo root: {repo_root}")


def _completed_process(
    argv: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    *,
    stream_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    if stream_output:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        stdout_chunks: list[str] = []
        for line in process.stdout:
            stdout_chunks.append(line)
            print(line, file=sys.stderr, end="", flush=True)
        process.wait()
        result = subprocess.CompletedProcess(argv, process.returncode, "".join(stdout_chunks), "")
    else:
        result = subprocess.run(argv, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = "\n".join(
            part
            for part in [
                f"Command failed: {' '.join(argv)}",
                result.stdout.strip() or "",
                result.stderr.strip() or "",
            ]
            if part
        )
        raise RuntimeError(message)
    return result


def _call_mcp_session(script_path: Path, messages: list[dict[str, Any]], env: dict[str, str]) -> list[dict[str, Any]]:
    process = subprocess.Popen(
        python_script_command(script_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    responses: list[dict[str, Any]] = []
    try:
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            line = process.stdout.readline().strip()
            if not line:
                raise RuntimeError(process.stderr.read())
            responses.append(json.loads(line))
    finally:
        process.stdin.close()
    process.wait(timeout=5)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.read())
    return responses


@contextmanager
def _temporary_env(overrides: dict[str, str]) -> Any:
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _read_json_url(url: str, *, timeout: int = 5) -> tuple[dict[str, Any], int, float]:
    started_at = time.monotonic()
    with urllib.request.urlopen(url, timeout=timeout) as response_handle:
        raw = response_handle.read()
    elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
    return json.loads(raw.decode("utf-8")), len(raw), elapsed_ms


def _assert_clean_repo_text(repo_root: Path, plugin_root: Path) -> None:
    forbidden_terms = [
        "".join(["/Use", "rs/a", "nd"]),
        "".join(["/Vol", "umes/T", "7"]),
        "".join(["and", "rei", "-local"]),
        "".join(["And", "rei ", "Local ", "Plugins"]),
    ]
    offenders: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or any(part in DISCOVERY_EXCLUDED_DIRS for part in path.parts) or path.suffix == ".pyc":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for term in forbidden_terms:
            if term in text:
                offenders.append(f"{path}: {term}")
    if offenders:
        raise AssertionError("\n".join(offenders))

    non_english: list[str] = []
    for path in plugin_root.rglob("*"):
        if not path.is_file() or any(part in DISCOVERY_EXCLUDED_DIRS for part in path.parts) or path.suffix == ".pyc":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(
            ("\u0410" <= char <= "\u042f")
            or ("\u0430" <= char <= "\u044f")
            or char in {"\u0401", "\u0451"}
            for char in text
        ):
            non_english.append(str(path))
    if non_english:
        raise AssertionError("\n".join(non_english))


def audit(repo_root: Path, plugin_root: Path) -> dict[str, Any]:
    _assert_clean_repo_text(repo_root, plugin_root)
    return {
        "check": "audit",
        "repo_root": str(repo_root),
        "plugin_root": str(plugin_root),
        "public_safe": True,
        "english_only": True,
    }


def python_compile(plugin_root: Path) -> dict[str, Any]:
    script_files = sorted(str(path) for path in (plugin_root / "scripts").glob("*.py"))
    _completed_process([*python_launcher_tokens(), "-m", "py_compile", *script_files], cwd=plugin_root.parents[2] if plugin_root.parent.name == "plugins" else plugin_root)
    return {
        "check": "python-compile",
        "compiled_files": script_files,
    }


def context_catalog_check(plugin_root: Path) -> dict[str, Any]:
    payload = check_catalogs(plugin_root)
    if payload["status"] != "ok":
        raise AssertionError(f"Context catalogs are stale: {payload['issues']}")
    return {
        "check": "context-catalogs",
        "catalog_root": payload["catalog_root"],
        "entry_counts": payload["entry_counts"],
    }


def self_host_check(repo_root: Path, plugin_root: Path) -> dict[str, Any]:
    preview = preview_workspace_init(repo_root)
    expected_primary_root = "." if plugin_root == repo_root else str(plugin_root.relative_to(repo_root))
    launcher = python_launcher_string()
    expected_command = (
        f"{launcher} scripts/release_readiness.py"
        if expected_primary_root == "."
        else f"{launcher} {expected_primary_root}/scripts/release_readiness.py"
    )
    required_stacks = {"python", "codex-plugin", "mcp-server", "local-dashboard"}
    if not required_stacks.issubset(set(preview["detected_stacks"])):
        raise AssertionError(f"Missing self-host stacks: {sorted(required_stacks.difference(preview['detected_stacks']))}")
    if "plugin-platform" not in preview["selected_profiles"]:
        raise AssertionError("plugin-platform profile was not selected for self-host workspace")
    plugin_platform = preview.get("plugin_platform") or {}
    if plugin_platform.get("primary_plugin_root") != expected_primary_root:
        raise AssertionError(f"Unexpected primary plugin root: {plugin_platform.get('primary_plugin_root')}")
    if plugin_platform.get("release_readiness_command") != expected_command:
        raise AssertionError(f"Unexpected release readiness command: {plugin_platform.get('release_readiness_command')}")
    if preview.get("local_dev_policy", {}).get("infra_mode") != "not_applicable":
        raise AssertionError("Plugin self-host workspace should not require local Docker infra by default.")
    return {
        "check": "self-host-detect",
        "detected_stacks": preview["detected_stacks"],
        "selected_profiles": preview["selected_profiles"],
        "plugin_platform": plugin_platform,
        "local_dev_policy": preview.get("local_dev_policy"),
    }


def mcp_check(plugin_root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["AGENTIUX_DEV_PLUGIN_ROOT"] = str(plugin_root)
    responses = _call_mcp_session(
        plugin_root / "scripts" / "agentiux_dev_mcp.py",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "release-readiness",
                        "version": "1.0.0",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        ],
        env,
    )
    init_response, tools_response = responses
    tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
    required_tools = {
        "get_dashboard_snapshot",
        "advise_workflow",
        "preview_repair_workspace_state",
        "repair_workspace_state",
        "show_host_support",
        "show_host_setup_plan",
        "install_host_requirements",
        "repair_host_requirements",
        "show_capability_catalog",
        "show_intent_route",
        "show_workspace_context_pack",
        "search_context_index",
        "show_context_structure",
        "refresh_context_index",
        "show_auth_profiles",
        "list_auth_sessions",
        "get_auth_session",
        "write_auth_profile",
        "write_auth_session",
        "remove_auth_profile",
        "invalidate_auth_session",
        "remove_auth_session",
        "resolve_auth_profile",
        "list_project_notes",
        "get_project_note",
        "write_project_note",
        "archive_project_note",
        "search_project_notes",
        "get_analytics_snapshot",
        "list_learning_entries",
        "write_learning_entry",
        "update_learning_entry",
        "audit_verification_coverage",
        "show_verification_helper_catalog",
        "sync_verification_helpers",
        "resolve_verification",
        "run_verification_case",
        "list_verification_runs",
        "list_workstreams",
        "create_task",
        "switch_task",
        "audit_repository",
        "create_starter",
        "approve_verification_baseline",
        "suggest_commit_message",
        "suggest_branch_name",
        "suggest_pr_title",
        "suggest_pr_body",
        "show_git_workflow_advice",
        "inspect_git_state",
        "list_git_worktrees",
        "plan_git_change",
        "create_git_worktree",
        "create_git_branch",
        "stage_git_files",
        "create_git_commit",
        "show_youtrack_connections",
        "connect_youtrack",
        "update_youtrack_connection",
        "remove_youtrack_connection",
        "test_youtrack_connection",
        "search_youtrack_issues",
        "show_youtrack_issue_queue",
        "propose_youtrack_workstream_plan",
        "apply_youtrack_workstream_plan",
    }
    missing = required_tools.difference(tool_names)
    if init_response["result"]["serverInfo"]["name"] != "agentiux-dev-state":
        raise AssertionError("Unexpected MCP server name")
    if missing:
        raise AssertionError(f"Missing MCP tools: {sorted(missing)}")
    return {
        "check": "mcp-check",
        "server": init_response["result"]["serverInfo"],
        "tool_count": len(tool_names),
        "required_tools": sorted(required_tools),
    }


def verification_coverage_check(repo_root: Path) -> dict[str, Any]:
    payload = audit_verification_coverage(repo_root)
    if "design_summary" not in payload or "testability_summary" not in payload:
        raise AssertionError("Verification coverage audit did not expose design/testability summaries")
    return {
        "check": "verification-coverage",
        "status": payload["status"],
        "warning_count": payload["warning_count"],
        "gaps": payload["gaps"],
        "coverage": payload["coverage"],
        "design_summary": payload["design_summary"],
        "testability_summary": payload["testability_summary"],
    }


def context_structure_check(repo_root: Path) -> dict[str, Any]:
    refresh_payload = refresh_context_index(repo_root, force=True)
    required_refresh_fields = {
        "rebuilt_file_count",
        "reused_file_count",
        "removed_file_count",
        "bounded_read_count",
        "full_read_count",
        "large_file_count",
        "parser_backend_status",
        "structure_summary",
        "hotspot_summary",
    }
    missing_refresh_fields = sorted(field for field in required_refresh_fields if field not in refresh_payload)
    if missing_refresh_fields:
        raise AssertionError(f"refresh_context_index missing fields: {missing_refresh_fields}")

    structure_payload = show_context_structure(
        repo_root,
        query_text="context structure hotspot symbol",
        route_id="analysis",
        module_path="plugins/agentiux-dev",
        limit=6,
    )
    parser_backends = structure_payload.get("parser_backends") or {}
    if (parser_backends.get("python_ast") or {}).get("status") != "active":
        raise AssertionError("python_ast backend should be active")
    if (parser_backends.get("markdown_sections") or {}).get("status") != "active":
        raise AssertionError("markdown_sections backend should be active")
    ts_backend_status = (parser_backends.get("typescript_compiler") or {}).get("status")
    if ts_backend_status not in {"available", "unavailable"}:
        raise AssertionError(f"Unexpected TypeScript backend status: {ts_backend_status}")
    if not structure_payload.get("summary", {}).get("chunk_counts"):
        raise AssertionError("show_context_structure did not expose chunk counts")
    if not structure_payload.get("modules"):
        raise AssertionError("show_context_structure did not expose module summaries")
    if not structure_payload.get("matches"):
        raise AssertionError("show_context_structure did not expose structural matches")

    search_payload = search_context_index(repo_root, "context structure hotspot symbol", route_id="analysis", limit=4)
    if not search_payload.get("matches"):
        raise AssertionError("search_context_index did not return structural matches")
    if any("match_kind" not in match for match in search_payload["matches"]):
        raise AssertionError("search_context_index matches are missing match_kind")

    context_pack_payload = show_workspace_context_pack(
        repo_root,
        request_text="inspect structural hotspots and modules",
        route_id="analysis",
        limit=4,
    )
    workspace_context = context_pack_payload.get("workspace_context") or {}
    if "structure_summary" not in workspace_context or "hotspot_summary" not in workspace_context:
        raise AssertionError("show_workspace_context_pack did not expose structural summaries")

    return {
        "check": "context-structure",
        "refresh": {
            key: refresh_payload[key]
            for key in [
                "status",
                "rebuilt_file_count",
                "reused_file_count",
                "removed_file_count",
                "bounded_read_count",
                "full_read_count",
                "large_file_count",
                "parser_backend_status",
                "structure_summary",
                "hotspot_summary",
            ]
        },
        "structure_summary": structure_payload.get("summary"),
        "parser_backends": parser_backends,
        "match_count": len(structure_payload.get("matches") or []),
        "search_match_count": len(search_payload.get("matches") or []),
        "workspace_context": {
            "structure_summary": workspace_context.get("structure_summary"),
            "hotspot_summary": workspace_context.get("hotspot_summary"),
        },
    }


def dashboard_check(repo_root: Path, plugin_root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        env["AGENTIUX_DEV_STATE_ROOT"] = str(temp_root / "state")
        env["AGENTIUX_DEV_PLUGIN_ROOT"] = str(plugin_root)
        with _temporary_env(
            {
                "AGENTIUX_DEV_STATE_ROOT": env["AGENTIUX_DEV_STATE_ROOT"],
                "AGENTIUX_DEV_PLUGIN_ROOT": env["AGENTIUX_DEV_PLUGIN_ROOT"],
            }
        ):
            init_workspace(repo_root)
            create_workstream(
                repo_root,
                "Dashboard Layout Audit Fixture",
                kind="feature",
                scope_summary="Exercise cockpit-first dashboard cards and stage state for browser layout auditing.",
            )
            fixture_snapshot = dashboard_snapshot(repo_root)
        launch_started_at = time.monotonic()
        launch_output = _completed_process(
            python_script_command(
                plugin_root / "scripts" / "agentiux_dev_gui.py",
                ["launch", "--workspace", str(repo_root)],
            ),
            cwd=repo_root,
            env=env,
        )
        cold_start_ms = round((time.monotonic() - launch_started_at) * 1000, 2)
        payload = json.loads(launch_output.stdout)
        url = payload["url"]
        audit_results: list[dict[str, Any]] = []
        deep_link_results: list[dict[str, Any]] = []
        history_navigation_audit: dict[str, Any] | None = None
        try:
            health, _health_bytes, _health_ms = _read_json_url(f"{url}/health")
            snapshot, overview_bytes, overview_fetch_ms = _read_json_url(f"{url}/api/dashboard")
            encoded_workspace = urllib.parse.quote(str(repo_root), safe="")
            cockpit_snapshot, cockpit_bytes, cockpit_fetch_ms = _read_json_url(
                f"{url}/api/workspace-cockpit?workspace={encoded_workspace}"
            )
            bootstrap_snapshot, bootstrap_bytes, bootstrap_fetch_ms = _read_json_url(
                f"{url}/api/dashboard-bootstrap?workspace={encoded_workspace}&panel=now"
            )
            plan_panel_snapshot, plan_panel_bytes, plan_panel_fetch_ms = _read_json_url(
                f"{url}/api/workspace-panel?workspace={encoded_workspace}&panel=plan"
            )
            auth_payload, _auth_bytes, _auth_ms = _read_json_url(f"{url}/api/auth/profiles?workspace={encoded_workspace}")
            auth_sessions_payload, _auth_sessions_bytes, _auth_sessions_ms = _read_json_url(
                f"{url}/api/auth/sessions?workspace={encoded_workspace}"
            )
            notes_payload, _notes_bytes, _notes_ms = _read_json_url(f"{url}/api/project-notes?workspace={encoded_workspace}")
            analytics_payload, _analytics_bytes, _analytics_ms = _read_json_url(f"{url}/api/analytics?workspace={encoded_workspace}")
            learnings_payload, _learnings_bytes, _learnings_ms = _read_json_url(f"{url}/api/learnings?workspace={encoded_workspace}")
            cockpit_url = f"{url}/workspaces/{urllib.parse.quote(str(repo_root), safe='')}?panel=now"
            audit_container_selectors = [
                "body",
                ".main",
                ".page-shell",
                ".content-grid",
                ".metric-grid",
                ".attention-strip",
                ".workspace-nav",
                ".portfolio-grid",
            ]
            history_interaction_script = """
(async () => {
  const waitFor = async (predicate, timeoutMs = 8000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (predicate()) {
        return true;
      }
      await new Promise((resolve) => setTimeout(resolve, 80));
    }
    return false;
  };
  const snapshot = () => window.__agentiux?.debugSnapshot?.() || null;
  const selectedPanel = () => document.querySelector("[data-selected-panel]")?.getAttribute("data-selected-panel");
  const selectedWorkspace = () => document.querySelector("[data-selected-workspace]")?.getAttribute("data-selected-workspace");
  const initial = snapshot();
  await window.__agentiux.setPanel("plan");
  await waitFor(() => selectedPanel() === "plan" && (snapshot()?.panelCache || []).includes("plan"));
  const after_plan = snapshot();
  await window.__agentiux.setPanel("plan");
  const after_cached_plan = snapshot();
  window.history.back();
  await waitFor(() => selectedPanel() === "now");
  const after_back = snapshot();
  window.history.forward();
  await waitFor(() => selectedPanel() === "plan");
  const after_forward = snapshot();
  return {
    initial,
    after_plan,
    after_cached_plan,
    after_back,
    after_forward,
    selected_workspace_path: selectedWorkspace(),
    selected_panel: selectedPanel(),
    location: {
      href: window.location.href,
      pathname: window.location.pathname,
      search: window.location.search,
      hash: window.location.hash,
    },
  };
})()
""".strip()

            def run_browser_audit(
                target_url: str,
                label: str,
                width: int,
                height: int,
                *,
                screenshot: bool = True,
                interaction_script: str | None = None,
            ) -> dict[str, Any]:
                screenshot_path = temp_root / f"{label}.png"
                audit_command = [
                    "node",
                    str(plugin_root / "scripts" / "browser_layout_audit.mjs"),
                    "--url",
                    target_url,
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--label",
                    label,
                ]
                if screenshot:
                    audit_command.extend(["--screenshot-path", str(screenshot_path)])
                if interaction_script:
                    audit_command.extend(["--interaction-script", interaction_script])
                for selector in audit_container_selectors:
                    audit_command.extend(["--container-selector", selector])
                audit_output = _completed_process(audit_command, cwd=repo_root, env=env)
                return json.loads(audit_output.stdout)

            for label, width, height in (("cockpit-now-desktop", 1440, 1800), ("cockpit-now-mobile", 390, 2200)):
                audit_results.append(run_browser_audit(cockpit_url, label, width, height))
            history_navigation_audit = run_browser_audit(
                cockpit_url,
                "cockpit-history-navigation",
                1280,
                1600,
                screenshot=False,
                interaction_script=history_interaction_script,
            )
            deep_link_results.extend(
                [
                    run_browser_audit(f"{url}/#overview", "overview-deep-link", 1280, 1400, screenshot=False),
                    run_browser_audit(
                        f"{url}/workspaces/{urllib.parse.quote(str(repo_root), safe='')}?panel=plan",
                        "cockpit-plan-deep-link",
                        1280,
                        1600,
                        screenshot=False,
                    ),
                ]
            )
        finally:
            _completed_process(
                python_script_command(plugin_root / "scripts" / "agentiux_dev_gui.py", ["stop"]),
                cwd=repo_root,
                env=env,
            )
    if not health.get("ok"):
        raise AssertionError("Dashboard health check failed")
    if snapshot.get("schema_version") != 2:
        raise AssertionError("Unexpected dashboard schema version")
    if snapshot.get("plugin", {}).get("name") != PLUGIN_NAME:
        raise AssertionError("Unexpected dashboard plugin payload")
    if fixture_snapshot.get("workspace_cockpit", {}).get("workspace_path") != str(repo_root):
        raise AssertionError("Dashboard fixture did not initialize the expected workspace cockpit")
    design_state = (fixture_snapshot.get("workspace_cockpit", {}).get("plan") or {}).get("design_state") or {}
    if "design_summary" not in design_state or "testability_summary" not in design_state:
        raise AssertionError("Dashboard fixture snapshot did not expose compact design/testability summaries")
    if "auth" not in (cockpit_snapshot.get("integrations") or {}):
        raise AssertionError("Dashboard cockpit is missing auth integration payload")
    if "memory" not in cockpit_snapshot:
        raise AssertionError("Dashboard cockpit is missing memory payload")
    if not bootstrap_snapshot.get("workspace_shell") or not bootstrap_snapshot.get("panel_payload"):
        raise AssertionError("Dashboard bootstrap payload is incomplete")
    if bootstrap_snapshot.get("selected_workspace_path") != str(repo_root):
        raise AssertionError("Dashboard bootstrap did not resolve the requested workspace")
    if plan_panel_snapshot.get("active_panel") != "plan":
        raise AssertionError("Workspace panel endpoint did not resolve the requested panel")
    plan_panel_design_state = (plan_panel_snapshot.get("panel_payload") or {}).get("design_state") or {}
    if "design_summary" not in plan_panel_design_state or "testability_summary" not in plan_panel_design_state:
        raise AssertionError("Plan panel payload did not expose compact design/testability summaries")
    if auth_payload.get("counts") is None or auth_sessions_payload.get("counts") is None or notes_payload.get("counts") is None:
        raise AssertionError("Dashboard auth or note APIs returned incomplete payloads")
    if auth_sessions_payload.get("counts", {}).get("total") is None:
        raise AssertionError("Dashboard auth sessions API returned incomplete payloads")
    auth_dump = json.dumps({"profiles": auth_payload, "sessions": auth_sessions_payload}).lower()
    for disallowed in ("access_token", "refresh_token", "password", "cookies", "storage_state"):
        if disallowed in auth_dump:
            raise AssertionError(f"Dashboard auth payload leaked raw secret material: {disallowed}")
    if analytics_payload.get("learning_counts") is None or learnings_payload.get("counts") is None:
        raise AssertionError("Dashboard analytics or learnings APIs returned incomplete payloads")
    sequential_fetch_ms = round(overview_fetch_ms + cockpit_fetch_ms, 2)
    sequential_payload_bytes = overview_bytes + cockpit_bytes
    if bootstrap_fetch_ms >= sequential_fetch_ms:
        raise AssertionError(
            f"Bootstrap request did not improve fetch latency: bootstrap {bootstrap_fetch_ms} ms vs legacy {sequential_fetch_ms} ms."
        )
    if bootstrap_bytes >= sequential_payload_bytes:
        raise AssertionError(
            f"Bootstrap payload did not improve payload size: bootstrap {bootstrap_bytes} B vs legacy {sequential_payload_bytes} B."
        )
    all_audits = [*audit_results, *deep_link_results]
    if history_navigation_audit:
        all_audits.append(history_navigation_audit)
    failing_audits = [
        item
        for item in all_audits
        if item and (int(item.get("issue_count") or 0) > 0 or str(item.get("status") or "").lower() == "failed")
    ]
    if failing_audits:
        raise AssertionError(
            "Dashboard layout audit failed: "
            + "; ".join(
                f"{item.get('label')}: {item.get('issue_count')} issues ({', '.join(issue.get('type') for issue in item.get('issues', [])[:4])})"
                for item in failing_audits
            )
        )
    blocking_warning_types = {
        "contrast-warning",
        "container-padding-imbalance",
        "ragged-grid-warning",
        "touch-target-too-small",
    }
    blocking_warnings = [
        {
            "label": audit.get("label"),
            "type": warning.get("type"),
            "warning_label": warning.get("label"),
            "text": warning.get("text"),
        }
        for audit in all_audits
        for warning in (audit.get("warnings") or [])
        if warning.get("type") in blocking_warning_types
    ]
    if blocking_warnings:
        raise AssertionError(
            "Dashboard layout audit produced blocking warnings: "
            + "; ".join(
                f"{item['label']}: {item['type']} ({item.get('warning_label') or item.get('text') or 'unlabeled'})"
                for item in blocking_warnings[:8]
            )
        )
    overview_deep_link = next(item for item in deep_link_results if item.get("label") == "overview-deep-link")
    if overview_deep_link.get("location", {}).get("hash") != "#overview":
        raise AssertionError("Overview deep link did not preserve the overview hash route.")
    if "dashboard-overview" not in (overview_deep_link.get("active_screen_ids") or []):
        raise AssertionError(
            "Overview deep link did not render the overview screen. "
            f"active_screen_ids={overview_deep_link.get('active_screen_ids')} "
            f"dashboard_debug={overview_deep_link.get('dashboard_debug')}"
        )
    plan_deep_link = next(item for item in deep_link_results if item.get("label") == "cockpit-plan-deep-link")
    if plan_deep_link.get("location", {}).get("search") != "?panel=plan":
        raise AssertionError("Plan deep link did not preserve the panel query parameter.")
    if "plan" not in (plan_deep_link.get("active_panel_ids") or []):
        raise AssertionError("Plan deep link did not render the requested panel.")
    if plan_deep_link.get("selected_workspace_path") != str(repo_root):
        raise AssertionError("Plan deep link did not keep the requested workspace selected.")
    history_result = (history_navigation_audit or {}).get("interaction_result") or {}
    if (history_result.get("after_back") or {}).get("panel") != "now":
        raise AssertionError("History back navigation did not restore the previous panel.")
    if (history_result.get("after_forward") or {}).get("panel") != "plan":
        raise AssertionError("History forward navigation did not restore the requested panel.")
    if history_result.get("selected_workspace_path") != str(repo_root):
        raise AssertionError("History navigation lost the selected workspace.")
    for audit in all_audits:
        debug = audit.get("dashboard_debug") or {}
        request_counts = debug.get("requestCounts") or {}
        if int(request_counts.get("bootstrap") or 0) < 1:
            raise AssertionError(f"Dashboard audit `{audit.get('label')}` did not use the bootstrap request path.")
        if int(request_counts.get("overview") or 0) > 0 or int(request_counts.get("cockpit") or 0) > 0:
            raise AssertionError(
                f"Dashboard audit `{audit.get('label')}` fell back to legacy overview/cockpit requests."
            )
        if (audit.get("timings") or {}).get("first_usable_render_ms") is None:
            raise AssertionError(f"Dashboard audit `{audit.get('label')}` did not expose first usable render timing.")
    if history_navigation_audit:
        history_counts = (((history_navigation_audit.get("interaction_result") or {}).get("after_forward") or {}).get("requestCounts") or {})
        if int(history_counts.get("bootstrap") or 0) != 1:
            raise AssertionError("History navigation triggered an extra bootstrap request instead of reusing the shell state.")
        if int(history_counts.get("panel") or 0) != 1:
            raise AssertionError("History navigation did not preserve the cached panel payload.")
    render_timings_ms = {
        item.get("label"): {
            "first_usable_render": (item.get("timings") or {}).get("first_usable_render_ms"),
            "dom_content_loaded": (item.get("timings") or {}).get("dom_content_loaded_ms"),
            "first_contentful_paint": (item.get("timings") or {}).get("first_contentful_paint_ms"),
            "audit_ready": (item.get("timings") or {}).get("audit_ready_ms"),
        }
        for item in all_audits
        if item
    }
    deep_link_assertions = {
        "overview": {
            "route_hash_preserved": overview_deep_link.get("location", {}).get("hash") == "#overview",
            "overview_screen_rendered": "dashboard-overview" in (overview_deep_link.get("active_screen_ids") or []),
        },
        "workspace_plan": {
            "panel_query_preserved": plan_deep_link.get("location", {}).get("search") == "?panel=plan",
            "requested_panel_rendered": "plan" in (plan_deep_link.get("active_panel_ids") or []),
            "workspace_preserved": plan_deep_link.get("selected_workspace_path") == str(repo_root),
        },
        "history_navigation": {
            "back_restored_previous_panel": (history_result.get("after_back") or {}).get("panel") == "now",
            "forward_restored_requested_panel": (history_result.get("after_forward") or {}).get("panel") == "plan",
            "workspace_preserved": history_result.get("selected_workspace_path") == str(repo_root),
        },
    }
    return {
        "check": "dashboard-check",
        "url": url,
        "schema_version": snapshot["schema_version"],
        "workspace_count": snapshot["overview"]["workspace_count"],
        "cold_start_ms": cold_start_ms,
        "render_timings_ms": render_timings_ms,
        "payload_bytes": {
            "overview": overview_bytes,
            "legacy_cockpit": cockpit_bytes,
            "legacy_combined": sequential_payload_bytes,
            "bootstrap": bootstrap_bytes,
            "plan_panel": plan_panel_bytes,
        },
        "design_summary": design_state.get("design_summary") or {},
        "testability_summary": design_state.get("testability_summary") or {},
        "request_timings_ms": {
            "overview": overview_fetch_ms,
            "legacy_cockpit": cockpit_fetch_ms,
            "legacy_combined": sequential_fetch_ms,
            "bootstrap": bootstrap_fetch_ms,
            "plan_panel": plan_panel_fetch_ms,
        },
        "audits": audit_results,
        "deep_link_audits": deep_link_results,
        "history_navigation_audit": history_navigation_audit,
        "deep_link_assertions": deep_link_assertions,
        "blocking_warning_count": len(blocking_warnings),
        "warning_audits": [
            {
                "label": item.get("label"),
                "warning_count": int(item.get("warning_count") or 0),
                "status": item.get("status"),
            }
            for item in audit_results
            if str(item.get("status") or "").lower() == "warning"
        ],
    }


def smoke(plugin_root: Path, repo_root: Path) -> dict[str, Any]:
    _completed_process(
        python_script_command(plugin_root / "scripts" / "smoke_test.py"),
        cwd=repo_root,
        stream_output=True,
    )
    return {
        "check": "smoke",
        "script": str(plugin_root / "scripts" / "smoke_test.py"),
        "status": "passed",
        "covered_features": [
            "workflow-advice",
            "context-catalogs",
            "context-index",
            "workstreams",
            "tasks",
            "youtrack",
            "commit-style-detection",
            "verification-case",
            "verification-suite",
            "verification-coverage-audit",
            "verification-follow-mode",
            "android-logcat",
            "baseline-lifecycle",
            "repository-audit",
            "upgrade-plan",
            "starter-creation",
            "git-safe-exec",
            "gui",
            "dashboard-management-flows",
            "mcp",
        ],
    }


def run_release_readiness(repo_root: Path, plugin_root: Path, smoke_runs: int) -> dict[str, Any]:
    checks = [
        audit(repo_root, plugin_root),
        python_compile(plugin_root),
        context_catalog_check(plugin_root),
        self_host_check(repo_root, plugin_root),
        verification_coverage_check(repo_root),
        context_structure_check(repo_root),
        mcp_check(plugin_root),
        dashboard_check(repo_root, plugin_root),
    ]
    smoke_results = []
    for index in range(smoke_runs):
        result = smoke(plugin_root, repo_root)
        result["iteration"] = index + 1
        smoke_results.append(result)
    checks.extend(smoke_results)
    return {
        "check": "release-readiness",
        "repo_root": str(repo_root),
        "plugin_root": str(plugin_root),
        "smoke_runs": smoke_runs,
        "checks": checks,
        "status": "passed",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AgentiUX Dev release-readiness checks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_repo_root_argument(command: argparse.ArgumentParser) -> None:
        command.add_argument("--repo-root", default=str(default_repo_root()))

    command = subparsers.add_parser("audit")
    add_repo_root_argument(command)

    command = subparsers.add_parser("python-compile")
    add_repo_root_argument(command)

    command = subparsers.add_parser("self-host-check")
    add_repo_root_argument(command)

    command = subparsers.add_parser("mcp-check")
    add_repo_root_argument(command)

    command = subparsers.add_parser("dashboard-check")
    add_repo_root_argument(command)

    command = subparsers.add_parser("smoke")
    add_repo_root_argument(command)

    run_parser = subparsers.add_parser("run")
    add_repo_root_argument(run_parser)
    run_parser.add_argument("--smoke-runs", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve()
    plugin_root = resolve_plugin_root(repo_root)

    if args.command == "audit":
        payload = audit(repo_root, plugin_root)
    elif args.command == "python-compile":
        payload = python_compile(plugin_root)
    elif args.command == "self-host-check":
        payload = self_host_check(repo_root, plugin_root)
    elif args.command == "mcp-check":
        payload = mcp_check(plugin_root)
    elif args.command == "dashboard-check":
        payload = dashboard_check(repo_root, plugin_root)
    elif args.command == "smoke":
        payload = smoke(plugin_root, repo_root)
    elif args.command == "run":
        payload = run_release_readiness(repo_root, plugin_root, smoke_runs=args.smoke_runs)
    else:
        raise ValueError(f"Unsupported command: {args.command}")

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
