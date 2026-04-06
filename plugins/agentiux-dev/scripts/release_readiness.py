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
from agentiux_dev_e2e_support import dashboard_check as shared_dashboard_check
from agentiux_dev_context import (
    refresh_context_index,
    run_analysis_audit,
    show_capability_catalog,
    show_intent_route,
    search_context_index,
    show_context_structure,
    show_runtime_preflight,
    show_workspace_context_pack,
    triage_repo_request,
)
from agentiux_dev_retrieval import surface_budget_result
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
        "triage_repo_request",
        "show_runtime_preflight",
        "show_workspace_context_pack",
        "search_context_index",
        "show_context_structure",
        "run_analysis_audit",
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
        "semantic_summary",
        "semantic_rebuilt_unit_count",
        "semantic_reused_unit_count",
    }
    missing_refresh_fields = sorted(field for field in required_refresh_fields if field not in refresh_payload)
    if missing_refresh_fields:
        raise AssertionError(f"refresh_context_index missing fields: {missing_refresh_fields}")
    intent_payload = show_intent_route(request_text="Inspect dashboard release readiness and plugin tools")
    if (intent_payload.get("resolved_route") or {}).get("route_id") != "plugin-dev":
        raise AssertionError("show_intent_route did not resolve the plugin-dev route for plugin inspection")
    capability_payload = show_capability_catalog(
        route_id="plugin-dev",
        query_text="dashboard release readiness plugin",
        limit=12,
    )
    if not capability_payload.get("entries"):
        raise AssertionError("show_capability_catalog did not return plugin capability entries")

    structure_payload = show_context_structure(
        repo_root,
        query_text="context structure hotspot symbol",
        route_id="analysis",
        module_path="plugins/agentiux-dev",
        limit=6,
        semantic_mode="enabled",
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
    if "semantic_summary" not in structure_payload:
        raise AssertionError("show_context_structure did not expose semantic_summary")
    if not structure_payload.get("modules"):
        raise AssertionError("show_context_structure did not expose module summaries")
    if not structure_payload.get("matches"):
        raise AssertionError("show_context_structure did not expose structural matches")

    search_payload = search_context_index(repo_root, "context structure hotspot symbol", route_id="analysis", limit=4, semantic_mode="enabled")
    if not search_payload.get("matches"):
        raise AssertionError("search_context_index did not return structural matches")
    if any("match_kind" not in match or "match_source" not in match for match in search_payload["matches"]):
        raise AssertionError("search_context_index matches are missing match_kind or match_source")
    search_auto_payload = search_context_index(
        repo_root,
        "context structure hotspot symbol semantic memory",
        limit=6,
        semantic_mode="auto",
    )
    if any(match.get("match_source") == "semantic_assisted" for match in search_auto_payload.get("matches") or []):
        raise AssertionError("search_context_index enabled semantic auto mode without an explicit analysis route")

    context_pack_payload = show_workspace_context_pack(
        repo_root,
        request_text="inspect structural hotspots and modules",
        route_id="analysis",
        limit=4,
        semantic_mode="enabled",
    )
    runtime_preflight_payload = show_runtime_preflight(
        repo_root,
        request_text="inspect structural hotspots and modules",
        route_id="analysis",
        limit=4,
        semantic_mode="enabled",
    )
    triage_payload = triage_repo_request(
        repo_root,
        request_text="inspect structural hotspots and modules",
        route_id="analysis",
        limit=4,
        semantic_mode="enabled",
    )
    context_pack_hit_payload = show_workspace_context_pack(
        repo_root,
        request_text="inspect structural hotspots and modules",
        route_id="analysis",
        limit=4,
        semantic_mode="enabled",
    )
    if context_pack_payload.get("cache_status") != "miss" or context_pack_hit_payload.get("cache_status") != "hit":
        raise AssertionError("show_workspace_context_pack did not expose the expected miss -> hit cache sequence")
    workspace_context = context_pack_payload.get("workspace_context") or {}
    if "structure_summary" not in workspace_context or "hotspot_summary" not in workspace_context or "semantic_summary" not in workspace_context:
        raise AssertionError("show_workspace_context_pack did not expose structural/semantic summaries")
    if (runtime_preflight_payload.get("preflight") or {}).get("repo_maturity", {}).get("mode") != "existing":
        raise AssertionError("show_runtime_preflight did not expose repo_maturity")
    if not (runtime_preflight_payload.get("preflight") or {}).get("next_read_paths"):
        raise AssertionError("show_runtime_preflight did not expose next_read_paths")
    if "confidence_reason" not in (runtime_preflight_payload.get("preflight") or {}):
        raise AssertionError("show_runtime_preflight did not expose confidence_reason")
    if not triage_payload.get("candidate_files"):
        raise AssertionError("triage_repo_request did not expose candidate_files")
    if triage_payload.get("manual_shell_scan_discouraged") is not True:
        raise AssertionError("triage_repo_request did not discourage broad shell exploration")
    context_pack_auto_payload = show_workspace_context_pack(
        repo_root,
        request_text="inspect structural hotspots and semantic memory",
        limit=4,
        semantic_mode="auto",
    )
    context_pack_auto_chunks = ((context_pack_auto_payload.get("context_pack") or {}).get("selected_chunks") or [])
    if any(chunk.get("match_source") == "semantic_assisted" for chunk in context_pack_auto_chunks):
        raise AssertionError("show_workspace_context_pack enabled semantic auto mode without an explicit analysis route")

    audit_payload = run_analysis_audit(
        repo_root,
        "docs_style",
        query_text="operator docs command surface semantic summary",
        module_path="plugins/agentiux-dev",
        limit=4,
        semantic_mode="auto",
    )
    required_audit_fields = {
        "mode",
        "semantic_mode",
        "semantic_backend_status",
        "findings",
        "evidence",
        "semantic_matches",
        "memory_snapshot_draft",
        "recommended_follow_ups",
        "payload",
    }
    missing_audit_fields = sorted(field for field in required_audit_fields if field not in audit_payload)
    if missing_audit_fields:
        raise AssertionError(f"run_analysis_audit missing fields: {missing_audit_fields}")
    payloads = {
        "show_intent_route": intent_payload,
        "show_capability_catalog": capability_payload,
        "triage_repo_request": triage_payload,
        "show_runtime_preflight": runtime_preflight_payload,
        "search_context_index": search_payload,
        "show_workspace_context_pack": context_pack_payload,
        "show_context_structure": structure_payload,
        "run_analysis_audit": audit_payload,
    }
    surface_payloads = {surface: payload.get("payload") for surface, payload in payloads.items()}
    budget_results = {surface: surface_budget_result(surface, stats) for surface, stats in surface_payloads.items()}
    failing_budgets = [surface for surface, result in budget_results.items() if not result["within_budget"]]
    if failing_budgets:
        raise AssertionError(
            "Cheap-surface working budgets exceeded: "
            + "; ".join(
                f"{surface}={budget_results[surface]['bytes']} budget={budget_results[surface]['budget_bytes']}"
                for surface in failing_budgets
            )
        )
    failing_ceilings = [surface for surface, result in budget_results.items() if not result["within_ceiling"]]
    if failing_ceilings:
        raise AssertionError(f"Cheap-surface payload ceilings exceeded: {failing_ceilings}")
    usage_path = Path(refresh_payload["cache_root"]) / "usage.json"
    usage_payload = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {}
    cache_metrics = {
        "refresh_count": int(usage_payload.get("refresh_count") or 0),
        "fresh_hit_count": int(usage_payload.get("fresh_hit_count") or 0),
        "search_count": int(usage_payload.get("search_count") or 0),
        "context_pack_hit_count": int(usage_payload.get("context_pack_hit_count") or 0),
        "context_pack_miss_count": int(usage_payload.get("context_pack_miss_count") or 0),
        "last_refresh_duration_ms": int(usage_payload.get("last_refresh_duration_ms") or 0),
        "last_context_pack_selected_tool_count": int(usage_payload.get("last_context_pack_selected_tool_count") or 0),
        "last_refresh_reason": usage_payload.get("last_refresh_reason"),
    }
    if cache_metrics["context_pack_hit_count"] < 1 or cache_metrics["context_pack_miss_count"] < 1:
        raise AssertionError("Context usage metrics did not record context-pack hit and miss counts")

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
                "semantic_summary",
                "semantic_rebuilt_unit_count",
                "semantic_reused_unit_count",
            ]
        },
        "surface_payloads": surface_payloads,
        "budget_results": budget_results,
        "cache_metrics": cache_metrics,
        "structure_summary": structure_payload.get("summary"),
        "parser_backends": parser_backends,
        "match_count": len(structure_payload.get("matches") or []),
        "search_match_count": len(search_payload.get("matches") or []),
        "audit_finding_count": len(audit_payload.get("findings") or []),
        "workspace_context": {
            "structure_summary": workspace_context.get("structure_summary"),
            "hotspot_summary": workspace_context.get("hotspot_summary"),
            "semantic_summary": workspace_context.get("semantic_summary"),
        },
        "route_payload": intent_payload,
        "capability_catalog": {
            "total_matches": capability_payload.get("total_matches"),
            "entry_count": len(capability_payload.get("entries") or []),
        },
    }


def dashboard_check(repo_root: Path, plugin_root: Path) -> dict[str, Any]:
    return shared_dashboard_check(repo_root, plugin_root)


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
