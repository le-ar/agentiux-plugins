#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agentiux_dev_e2e_support import (
    FakeYouTrackServer,
    audit_synthetic_surface_inventory,
    enable_test_tool_overrides,
    write_fake_adb,
    write_fake_bootstrap_tools,
    write_fake_host_setup_installer,
)
from agentiux_dev_analytics import (
    get_analytics_snapshot,
    list_learning_entries,
    write_learning_entry,
)
from agentiux_dev_auth import (
    get_auth_session,
    invalidate_auth_session,
    list_auth_sessions,
    remove_auth_session,
    resolve_auth_profile,
    show_auth_profiles,
    write_auth_profile,
    write_auth_session,
)
from agentiux_dev_gui import stop as stop_gui
from agentiux_dev_lib import (
    SURFACE_PAYLOAD_CEILINGS,
    STATE_SCHEMA_VERSION,
    _host_setup_recipe_for_tool,
    _safe_rglob,
    apply_upgrade_plan,
    audit_repository,
    cache_reference_preview,
    close_task,
    command_aliases,
    create_git_branch,
    create_git_commit,
    create_git_worktree,
    create_starter,
    create_workstream,
    create_task,
    current_task,
    current_workstream,
    dashboard_snapshot,
    detect_commit_style,
    get_active_brief,
    init_workspace,
    inspect_git_state,
    install_host_requirements,
    list_git_worktrees,
    list_reference_boards,
    list_starter_runs,
    list_tasks,
    list_workspaces,
    list_workstreams,
    migrate_workspace_state,
    normalize_command_phrase,
    plugin_stats,
    plan_git_change,
    payload_size_bytes,
    preview_reset_workspace_state,
    preview_repair_workspace_state,
    preview_workspace_init,
    python_script_command,
    python_launcher_string,
    read_current_audit,
    read_design_brief,
    read_design_handoff,
    read_reference_board,
    read_task,
    read_stage_register,
    read_upgrade_plan,
    repair_host_requirements,
    repair_workspace_state,
    reset_workspace_state,
    resolve_command_phrase,
    set_active_brief,
    show_git_workflow_advice,
    show_host_setup_plan,
    show_host_support,
    show_upgrade_plan,
    stage_git_files,
    switch_task,
    suggest_branch_name,
    suggest_commit_message,
    suggest_pr_body,
    suggest_pr_title,
    switch_workstream,
    workflow_advice,
    workspace_summary,
    workspace_paths,
    write_design_brief,
    write_design_handoff,
    write_reference_board,
    write_stage_register,
)
from agentiux_dev_memory import (
    archive_project_note,
    get_project_note,
    list_generated_memory_snapshots,
    list_project_notes,
    persist_generated_memory_snapshot,
    search_project_notes,
)
from agentiux_dev_verification import (
    SEMANTIC_REPORT_SCHEMA_VERSION,
    _validate_semantic_assertions,
    active_verification_run,
    audit_verification_coverage,
    approve_verification_baseline,
    follow_verification_run,
    list_verification_runs,
    read_verification_events,
    read_verification_log_tail,
    read_verification_recipes,
    resolve_verification_selection,
    show_verification_helper_catalog,
    sync_verification_helpers,
    start_verification_case,
    start_verification_suite,
    update_verification_baseline,
    wait_for_verification_run,
    write_verification_recipes,
)
from agentiux_dev_youtrack import (
    apply_youtrack_workstream_plan,
    connect_youtrack,
    list_youtrack_connections,
    propose_youtrack_workstream_plan,
    search_youtrack_issues,
    show_youtrack_issue_queue,
)
from install_home_local import install_plugin
from build_context_catalogs import check_catalogs
from agentiux_dev_context import (
    refresh_context_index,
    run_analysis_audit,
    search_context_index,
    show_capability_catalog,
    show_context_structure,
    show_intent_route,
    show_runtime_preflight,
    show_workspace_context_pack,
    triage_repo_request,
)
from agentiux_dev_text import tokenize_text


def _seed_workspace(root: Path) -> None:
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "demo-workspace",
                "dependencies": {
                    "react": "^19.0.0",
                    "next": "^16.0.0",
                    "@nestjs/core": "^11.0.0",
                    "expo": "^54.0.0",
                    "nativewind": "^4.0.0",
                    "tailwindcss": "^4.0.0",
                    "react-native": "^0.82.0",
                    "nx": "^22.0.0",
                    "pg": "^9.0.0",
                    "mongodb": "^6.0.0",
                    "redis": "^5.0.0",
                    "nats": "^2.0.0",
                },
            },
            indent=2,
        )
        + "\n"
    )
    (root / "tsconfig.json").write_text("{\"compilerOptions\":{\"strict\":true}}\n")
    (root / "nx.json").write_text("{\"extends\":\"nx/presets/npm.json\"}\n")
    (root / "Cargo.toml").write_text("[package]\nname = \"demo\"\nversion = \"0.1.0\"\n")
    (root / "docker-compose.yml").write_text(
        "services:\n"
        "  postgres:\n    image: postgres:16\n"
        "  mongo:\n    image: mongo:8\n"
        "  redis:\n    image: redis:7\n"
        "  nats:\n    image: nats:2\n"
    )
    (root / "android").mkdir()
    (root / "ios").mkdir()
    (root / "app.json").write_text("{\"expo\":{\"name\":\"demo\"}}\n")
    (root / "tailwind.config.ts").write_text("export default {};\n")
    (root / "README.md").write_text("# Demo Workspace\n")


def _call_mcp(script_path: Path, message: dict) -> dict:
    process = subprocess.Popen(
        ["python3", str(script_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    process.stdin.close()
    output = process.stdout.readline().strip()
    process.wait(timeout=5)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.read())
    return json.loads(output)


def _read_json_file(path: Path) -> dict:
    return json.loads(path.read_text())


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _write_json_file(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _http_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 10.0) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method=method,
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AssertionError(f"HTTP {exc.code} for {method} {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(0.5)
    raise AssertionError(f"Timed out reading JSON from {url}: {last_error}")


def _http_text(url: str, *, timeout: float = 10.0) -> str:
    request = urllib.request.Request(url, method="GET")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AssertionError(f"HTTP {exc.code} for GET {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(0.5)
    raise AssertionError(f"Timed out reading text from {url}: {last_error}")


def _wait_for_run_started(
    workspace,
    run_id: str,
    *,
    workstream_id: str | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[dict | None, dict]:
    deadline = time.time() + timeout_seconds
    latest_active_run = None
    latest_events = {"events": []}
    while time.time() < deadline:
        latest_active_run = active_verification_run(workspace, workstream_id=workstream_id)
        latest_events = read_verification_events(workspace, run_id, limit=20, workstream_id=workstream_id)
        if latest_active_run is not None or any(
            event["event_type"] == "run_started" for event in latest_events["events"]
        ):
            return latest_active_run, latest_events
        time.sleep(0.1)
    return latest_active_run, latest_events


def _assert_no_branded_strings_in_tree(root: Path) -> None:
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        text = candidate.read_text()
        lowered = text.lower()
        assert "agentiux" not in lowered, f"unexpected brand leak in {candidate}"
        assert "codex" not in lowered, f"unexpected brand leak in {candidate}"


def _git_commit(repo_root: Path, message: str, body: str | None = None) -> None:
    argv = [
        "git",
        "-c",
        "user.name=AgentiUX",
        "-c",
        "user.email=agentiux@example.com",
        "commit",
        "-m",
        message,
    ]
    if body:
        argv.extend(["-m", body])
    subprocess.run(argv, cwd=repo_root, check=True, capture_output=True, text=True)


def _assert_stage_ids(register: dict, expected_present: list[str], expected_absent: list[str]) -> None:
    stage_ids = [stage["id"] for stage in register["stages"]]
    for stage_id in expected_present:
        assert stage_id in stage_ids, f"missing stage {stage_id}: {stage_ids}"
    for stage_id in expected_absent:
        assert stage_id not in stage_ids, f"unexpected stage {stage_id}: {stage_ids}"


def _assert_no_default_origin(payload: object) -> None:
    if isinstance(payload, dict):
        if payload.get("origin") is not None:
            assert payload["origin"] in {"custom", "template", "mixed"}, payload
        for value in payload.values():
            _assert_no_default_origin(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_default_origin(item)


def _semantic_validation_case(report_path: str = "semantic-report.json") -> dict[str, object]:
    return {
        "id": "validator-case",
        "runner": "playwright-visual",
        "semantic_assertions": {
            "enabled": True,
            "report_path": report_path,
            "required_checks": ["visibility", "computed_styles"],
            "targets": [
                {
                    "target_id": "hero-main",
                    "locator": {"kind": "role", "value": "main"},
                }
            ],
        },
    }


def _write_semantic_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _assert_test_support_deduped(plugin_root: Path) -> None:
    smoke_source = (plugin_root / "scripts" / "smoke_test.py").read_text(encoding="utf-8")
    assert "from agentiux_dev_e2e_support import (" in smoke_source
    for symbol in (
        "FakeYouTrackServer",
        "write_fake_adb",
        "write_fake_bootstrap_tools",
        "write_fake_host_setup_installer",
    ):
        assert symbol in smoke_source
    for legacy_marker in (
        "class " + "_FakeYouTrackHandler",
        "class " + "_FakeYouTrackServer",
        "def " + "_write_fake_adb",
        "def " + "_write_fake_bootstrap_tools",
        "def " + "_write_fake_host_setup_installer",
    ):
        assert legacy_marker not in smoke_source


def _assert_contract_only_docs(plugin_root: Path) -> None:
    root_readme = (plugin_root / "README.md").read_text(encoding="utf-8")
    e2e_readme = (plugin_root / "tests" / "e2e" / "README.md").read_text(encoding="utf-8")
    test_catalog = (plugin_root / "tests" / "e2e" / "TEST_CATALOG.md").read_text(encoding="utf-8")
    assert "semantic_contract_runner.py" in root_readme
    assert "contract-only" in root_readme
    assert "semantic_contract_runner.py" in e2e_readme
    assert "contract-only" in e2e_readme
    for case_id in (
        "verification-case-pass-playwright",
        "external-fixture-playwright-reset",
        "external-fixture-detox-native-audit",
        "external-fixture-compose-native-audit",
    ):
        line = next(line for line in test_catalog.splitlines() if line.startswith(f"| {case_id} |"))
        assert "contract-only" in line


def _stage_definition(stage_id: str, title: str, objective: str, slices: list[str], **extra: object) -> dict:
    payload = {
        "id": stage_id,
        "title": title,
        "objective": objective,
        "canonical_execution_slices": slices,
    }
    payload.update(extra)
    return payload


def _confirm_stage_plan(workspace: Path, stages: list[dict], workstream_id: str | None = None) -> dict:
    register = read_stage_register(workspace, workstream_id=workstream_id)
    register["stages"] = stages
    if stages:
        register["current_stage"] = stages[0]["id"]
        register["stage_status"] = "planned"
        register["current_slice"] = stages[0]["canonical_execution_slices"][0]
        register["remaining_slices"] = stages[0]["canonical_execution_slices"][1:]
        register["slice_status"] = "planned"
        register["active_goal"] = stages[0]["objective"]
        register["next_task"] = stages[0]["objective"]
    else:
        register["current_stage"] = None
        register["stage_status"] = None
        register["current_slice"] = None
        register["remaining_slices"] = []
        register["slice_status"] = None
        register["active_goal"] = None
        register["next_task"] = None
    return write_stage_register(workspace, register, confirmed_stage_plan_edit=True, workstream_id=workstream_id)


def _seed_web_only_workspace(root: Path) -> None:
    (root / "package.json").write_text(
        json.dumps({"name": "web-only", "dependencies": {"react": "^19.0.0", "next": "^16.0.0"}}, indent=2) + "\n"
    )
    (root / "tsconfig.json").write_text("{\"compilerOptions\":{\"strict\":true}}\n")


def _seed_backend_workspace(root: Path, with_infra: bool) -> None:
    (root / "package.json").write_text(
        json.dumps({"name": "backend-only", "dependencies": {"@nestjs/core": "^11.0.0", "pg": "^9.0.0"}}, indent=2) + "\n"
    )
    if with_infra:
        (root / "docker-compose.yml").write_text("services:\n  postgres:\n    image: postgres:16\n")


def _make_stale_plugin_fixture(repo_root: Path) -> dict:
    init_workspace(repo_root, force=True)
    created = create_workstream(
        repo_root,
        "Plugin Production Readiness",
        kind="feature",
        scope_summary="Lock plugin runtime convergence, verification, and release-readiness contracts.",
    )
    workstream_id = created["created_workstream_id"]
    paths = workspace_paths(repo_root, workstream_id=workstream_id)

    workspace_state_path = Path(paths["workspace_state"])
    workspace_state = _read_json_file(workspace_state_path)
    workspace_state["docker_policy"] = {"mode": "legacy-docker"}
    _write_json_file(workspace_state_path, workspace_state)

    workstreams_index_path = Path(paths["workstreams_index"])
    workstreams_index = _read_json_file(workstreams_index_path)
    for item in workstreams_index["items"]:
        if item["workstream_id"] == workstream_id:
            item["title"] = "default"
            item["kind"] = "default"
            item["scope_summary"] = "Primary product workstream."
            item["branch_hint"] = None
    _write_json_file(workstreams_index_path, workstreams_index)

    canonical_register_path = Path(paths["current_workstream_stage_register"])
    canonical_register = _read_json_file(canonical_register_path)
    canonical_register["schema_version"] = 4
    canonical_register["workstream_title"] = "default"
    canonical_register["workstream_kind"] = "default"
    canonical_register["scope_summary"] = "Lock plugin runtime convergence, verification, and release-readiness contracts."
    canonical_register["branch_hint"] = None
    canonical_register["is_mirror"] = True
    canonical_register["mirror_of_workstream_id"] = workstream_id
    docker_stage = {
        "id": "01-local-dev-infra-and-boot",
        "title": "Local Dev Infra And Boot",
        "objective": "Legacy dockerized plugin stage that should be removed by repair.",
        "path": str((Path(paths["current_workstream_stages_dir"]) / "01-local-dev-infra-and-boot.md").resolve()),
        "status": "planned",
        "canonical_execution_slices": ["01.1-infra-inventory-and-container-boundary"],
    }
    if all(stage["id"] != docker_stage["id"] for stage in canonical_register["stages"]):
        canonical_register["stages"].insert(1, docker_stage)
    canonical_register["current_stage"] = docker_stage["id"]
    canonical_register["stage_status"] = "planned"
    canonical_register["current_slice"] = docker_stage["canonical_execution_slices"][0]
    canonical_register["remaining_slices"] = []
    _write_json_file(canonical_register_path, canonical_register)

    canonical_brief_path = Path(paths["current_workstream_active_brief"])
    canonical_brief_path.write_text(
        "<!-- derived-mirror: true -->\n"
        f"<!-- mirror-of-workstream: {workstream_id} -->\n"
        "# Active Stage Brief\n\n"
        "Ship plugin runtime convergence and readiness hardening.\n"
    )

    return {
        "workstream_id": workstream_id,
        "paths": paths,
    }


def _make_legacy_workspace_fixture(workspace: Path) -> dict:
    init_workspace(workspace, force=True)
    created = create_workstream(
        workspace,
        "Legacy Dashboard Workspace",
        kind="feature",
        scope_summary="Exercise dashboard migration from root-only legacy workspace state.",
    )
    workstream_id = created["created_workstream_id"]
    root_paths = workspace_paths(workspace)
    canonical_paths = workspace_paths(workspace, workstream_id=workstream_id)
    workspace_state_path = Path(root_paths["workspace_state"])
    if workspace_state_path.exists():
        workspace_state_path.unlink()
    for candidate in (root_paths["workstreams_index"], root_paths["tasks_index"]):
        candidate_path = Path(candidate)
        if candidate_path.exists():
            candidate_path.unlink()
    return {
        "workstream_id": workstream_id,
        "paths": root_paths,
        "canonical_paths": canonical_paths,
    }


def _assert_clean_repo_text(repo_root: Path, plugin_root: Path) -> None:
    forbidden_terms = [
        "".join(["/Use", "rs/a", "nd"]),
        "".join(["/Vol", "umes/T", "7"]),
        "".join(["and", "rei", "-local"]),
        "".join(["And", "rei ", "Local ", "Plugins"]),
    ]
    offenders: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for term in forbidden_terms:
            if term in text:
                offenders.append(f"{path}: {term}")
    assert not offenders, "\n".join(offenders)

    non_english: list[str] = []
    for path in plugin_root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(
            ("\u0410" <= char <= "\u042f")
            or ("\u0430" <= char <= "\u044f")
            or char in {"\u0401", "\u0451"}
            for char in text
        ):
            non_english.append(str(path))
    assert not non_english, "\n".join(non_english)


def main() -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    repo_root = plugin_root.parents[1]
    with tempfile.TemporaryDirectory() as temp_dir:
        smoke_started_at = time.monotonic()
        last_progress_at = smoke_started_at
        progress_step = 0

        def progress(label: str) -> None:
            nonlocal last_progress_at, progress_step
            progress_step += 1
            now = time.monotonic()
            total_seconds = now - smoke_started_at
            delta_seconds = now - last_progress_at
            print(
                f"[smoke {progress_step:02d}] +{total_seconds:6.1f}s (+{delta_seconds:5.1f}s) {label}",
                flush=True,
            )
            last_progress_at = now

        temp_root = Path(temp_dir)
        workspace = temp_root / "workspace"
        workspace.mkdir()
        _seed_workspace(workspace)

        install_root = temp_root / "installed-plugin"
        marketplace = temp_root / "marketplace.json"
        state_root = temp_root / "state"

        os.environ["AGENTIUX_DEV_STATE_ROOT"] = str(state_root)
        os.environ["AGENTIUX_DEV_PLUGIN_ROOT"] = str(plugin_root)
        os.environ["AGENTIUX_DEV_INSTALL_ROOT"] = str(install_root)
        os.environ["AGENTIUX_DEV_MARKETPLACE_PATH"] = str(marketplace)
        os.environ.update(enable_test_tool_overrides())
        tool_bin = temp_root / "tool-bin"
        tool_bin.mkdir()
        write_fake_adb(tool_bin)
        os.environ["PATH"] = f"{tool_bin}{os.pathsep}{os.environ['PATH']}"

        subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "smoke@example.com"], cwd=workspace, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Smoke Test"], cwd=workspace, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "seed workspace"], cwd=workspace, check=True, capture_output=True, text=True)

        _assert_clean_repo_text(repo_root, plugin_root)
        synthetic_inventory = audit_synthetic_surface_inventory(plugin_root)
        assert synthetic_inventory["status"] == "passed", synthetic_inventory
        assert not synthetic_inventory["missing_inventory"], synthetic_inventory
        assert not synthetic_inventory["missing_paths"], synthetic_inventory
        _assert_test_support_deduped(plugin_root)
        _assert_contract_only_docs(plugin_root)
        progress("bootstrap fixtures, repo seed, and repository hygiene checks")

        aliases = command_aliases()
        assert "initialize workspace" in aliases
        assert "create workstream" in aliases
        assert all(all(ord(char) < 128 for char in alias) for values in aliases.values() for alias in values)
        assert resolve_command_phrase("init workspace") == "initialize workspace"
        assert resolve_command_phrase("please show workspace context pack for this repo") == "show workspace context pack"
        assert resolve_command_phrase("show context structure for this repo") == "show context structure"
        mixed_script_request = "Inspect \uff2d\uff23\uff30 tool catalogs and the dashboard runtime for plugin development \u03a3"
        assert normalize_command_phrase(mixed_script_request).startswith("inspect mcp")
        assert "mcp" in tokenize_text(mixed_script_request)

        context_catalogs = check_catalogs(plugin_root)
        assert context_catalogs["status"] == "ok"
        assert context_catalogs["entry_counts"]["mcp_tools"] >= 1
        assert context_catalogs["entry_counts"]["skills"] >= 1

        scan_workspace = temp_root / "scan-workspace"
        (scan_workspace / "src").mkdir(parents=True)
        (scan_workspace / "src" / "main.py").write_text("print('ok')\n")
        (scan_workspace / "node_modules" / "vendor").mkdir(parents=True)
        (scan_workspace / "node_modules" / "vendor" / "ignored.py").write_text("print('skip')\n")
        scanned_roots: list[str] = []
        real_scandir = os.scandir

        def _recording_scandir(path: str | os.PathLike[str] = "."):
            scanned_roots.append(str(Path(path).resolve()))
            return real_scandir(path)

        with mock.patch("os.scandir", side_effect=_recording_scandir):
            safe_rglob_matches = _safe_rglob(scan_workspace, "*.py")
        resolved_scan_workspace = scan_workspace.resolve()
        assert [path.relative_to(resolved_scan_workspace).as_posix() for path in safe_rglob_matches] == ["src/main.py"]
        assert str((resolved_scan_workspace / "node_modules").resolve()) not in scanned_roots

        git_route = show_intent_route(request_text="Inspect git worktree and propose a commit message")
        assert git_route["resolved_route"]["route_id"] == "git"
        assert git_route["resolution_status"] == "matched"
        assert git_route["payload"]["within_ceiling"] is True
        verification_route = show_intent_route(request_text="Check semantic verification helper bundle drift")
        assert verification_route["resolved_route"]["route_id"] == "verification"
        assert verification_route["resolution_status"] == "matched"
        mixed_plugin_route = show_intent_route(request_text=mixed_script_request)
        assert mixed_plugin_route["resolved_route"]["route_id"] == "plugin-dev"
        assert mixed_plugin_route["resolution_status"] == "matched"
        benchmark_plugin_route = show_intent_route(
            request_text=(
                "Find the smallest file set in this plugin that controls low-token retrieval payload ceilings, "
                "benchmark telemetry, context-pack cache behavior, and dashboard performance budgets."
            )
        )
        assert benchmark_plugin_route["resolved_route"]["route_id"] == "plugin-dev"
        assert benchmark_plugin_route["resolution_status"] == "matched"
        unresolved_route = show_intent_route(request_text="frobnicate lattice quux")
        assert unresolved_route["resolved_route"] is None
        assert unresolved_route["resolution_status"] == "unresolved"
        git_capabilities = show_capability_catalog(route_id="git", query_text="commit branch worktree", limit=12)
        assert git_capabilities["entries"]
        assert any(entry["id"] == "git-ops" for entry in git_capabilities["entries"])
        assert any(entry["id"] == "inspect_git_state" for entry in git_capabilities["entries"])
        assert all("why" in entry for entry in git_capabilities["entries"])
        assert git_capabilities["payload"]["within_ceiling"] is True
        assert payload_size_bytes(git_capabilities) <= SURFACE_PAYLOAD_CEILINGS["show_capability_catalog"]

        repo_context_refresh = refresh_context_index(repo_root)
        assert repo_context_refresh["status"] == "refreshed"
        assert Path(repo_context_refresh["cache_root"]).resolve().is_relative_to((state_root / "cache" / "context").resolve())
        assert Path(repo_context_refresh["workspace_context_path"]).exists()
        assert repo_context_refresh["storage_backend"] == "sqlite"
        assert Path(repo_context_refresh["context_store_path"]).exists()
        repo_context_refresh_again = refresh_context_index(repo_root)
        assert repo_context_refresh_again["status"] == "fresh"
        assert repo_context_refresh_again["refresh_reason"] == "manifest-match"
        repo_context_search = search_context_index(
            repo_root,
            mixed_script_request,
            route_id="plugin-dev",
            limit=5,
        )
        assert repo_context_search["resolved_route"]["route_id"] == "plugin-dev"
        assert repo_context_search["route_resolution_status"] == "exact"
        assert repo_context_search["index_status"] in {"fresh", "refreshed", "context-refreshed"}
        assert repo_context_search["storage_backend"] == "sqlite"
        assert repo_context_search["matches"]
        assert repo_context_search["retrieval"]["mode"] == "orientation"
        assert repo_context_search["payload"]["within_ceiling"] is True
        assert payload_size_bytes(repo_context_search) <= SURFACE_PAYLOAD_CEILINGS["search_context_index"]
        assert repo_context_search["recommended_capabilities"] == []
        capability_catalog = show_capability_catalog(route_id="plugin-dev", query_text=mixed_script_request, limit=5)
        assert capability_catalog["entries"]
        assert any(entry["id"] == "show_capability_catalog" for entry in capability_catalog["entries"])
        repo_context_pack = show_workspace_context_pack(
            repo_root,
            request_text=mixed_script_request,
            route_id="plugin-dev",
            limit=5,
        )
        assert repo_context_pack["cache_status"] == "miss"
        assert repo_context_pack["index_status"] in {"fresh", "refreshed", "context-refreshed"}
        assert repo_context_pack["storage_backend"] == "sqlite"
        assert repo_context_pack["retrieval"]["mode"] == "orientation"
        assert repo_context_pack["payload"]["within_ceiling"] is True
        assert payload_size_bytes(repo_context_pack) <= SURFACE_PAYLOAD_CEILINGS["show_workspace_context_pack"]
        assert repo_context_pack["context_pack"]["selected_chunks"]
        assert "get_dashboard_snapshot" in (repo_context_pack["context_pack"].get("selected_tools") or [])
        assert repo_context_pack["context_pack"]["owner_candidates"]
        assert repo_context_pack["context_pack"]["next_read_paths"]
        assert "confidence_reason" in repo_context_pack["context_pack"]
        repo_runtime_preflight = show_runtime_preflight(
            repo_root,
            request_text=mixed_script_request,
            route_id="plugin-dev",
            limit=5,
        )
        assert repo_runtime_preflight["storage_backend"] == "sqlite"
        assert repo_runtime_preflight["payload"]["within_ceiling"] is True
        assert payload_size_bytes(repo_runtime_preflight) <= SURFACE_PAYLOAD_CEILINGS["show_runtime_preflight"]
        assert repo_runtime_preflight["preflight"]["next_read_paths"]
        assert repo_runtime_preflight["preflight"]["repo_maturity"]["mode"] == "existing"
        repo_triage = triage_repo_request(
            repo_root,
            request_text=mixed_script_request,
            route_id="plugin-dev",
            limit=5,
        )
        assert repo_triage["storage_backend"] == "sqlite"
        assert repo_triage["payload"]["within_ceiling"] is True
        assert payload_size_bytes(repo_triage) <= SURFACE_PAYLOAD_CEILINGS["triage_repo_request"]
        assert repo_triage["candidate_files"]
        assert repo_triage["manual_shell_scan_discouraged"] is True
        cached_repo_context_pack = show_workspace_context_pack(
            repo_root,
            request_text=mixed_script_request,
            route_id="plugin-dev",
            limit=5,
        )
        assert cached_repo_context_pack["cache_status"] == "hit"
        assert cached_repo_context_pack["payload"]["within_ceiling"] is True
        assert cached_repo_context_pack["context_pack"]["catalog_digest"] == repo_context_pack["workspace_context"]["catalog_digest"]

        workspace_context_refresh = refresh_context_index(workspace)
        assert workspace_context_refresh["status"] == "refreshed"
        assert Path(workspace_context_refresh["cache_root"]).resolve().is_relative_to((state_root / "cache" / "context").resolve())
        assert not (workspace / ".agentiux").exists()
        assert not (workspace / ".verification" / "helpers").exists()
        workspace_context_refresh_again = refresh_context_index(workspace)
        assert workspace_context_refresh_again["status"] == "fresh"
        workspace_context_pack = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        assert workspace_context_pack["cache_status"] == "miss"
        assert workspace_context_pack["index_status"] in {"fresh", "refreshed", "context-refreshed"}
        assert workspace_context_pack["route_resolution_status"] == "exact"
        assert workspace_context_pack["workspace_context"]["repo_maturity"]["mode"] in {"existing", "scaffold"}
        workspace_runtime_preflight = show_runtime_preflight(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        assert workspace_runtime_preflight["preflight"]["next_read_paths"]
        assert workspace_runtime_preflight["preflight"]["request_text_source"] == "request_text"
        assert workspace_runtime_preflight["preflight"]["repo_maturity"]["mode"] in {"existing", "scaffold"}
        cached_workspace_context_pack = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        assert cached_workspace_context_pack["cache_status"] == "hit"
        original_workspace_fingerprint = workspace_context_pack["workspace_context"]["workspace_fingerprint"]
        workspace_cache_root = Path(workspace_context_refresh["cache_root"])
        workspace_manifest_path = workspace_cache_root / "index_manifest.json"
        workspace_context_path = workspace_cache_root / "workspace_context.json"
        workspace_usage_path = workspace_cache_root / "usage.json"
        (workspace / "scratch.log").write_text("unindexed dirty change\n")
        workspace_context_refresh_after_dirty = refresh_context_index(workspace)
        assert workspace_context_refresh_after_dirty["status"] == "context-refreshed"
        assert workspace_context_refresh_after_dirty["refresh_reason"] == "dirty-digest"
        assert workspace_context_refresh_after_dirty["workspace_fingerprint"] == original_workspace_fingerprint
        assert workspace_context_refresh_after_dirty["rebuilt_chunk_count"] == 0
        assert workspace_context_refresh_after_dirty["pruned_semantic_cache_entries"] == 0
        dirty_workspace_context_pack = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        assert dirty_workspace_context_pack["cache_status"] == "hit"
        (workspace / "notes-unrelated.md").write_text("# Notes\n\nThis file should not invalidate unrelated context packs.\n")
        workspace_context_refresh_after_unrelated = refresh_context_index(workspace)
        assert workspace_context_refresh_after_unrelated["status"] == "refreshed"
        assert workspace_context_refresh_after_unrelated["rebuilt_chunk_count"] >= 1
        assert workspace_context_refresh_after_unrelated["pruned_semantic_cache_entries"] == 0
        unrelated_workspace_context_pack = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        assert unrelated_workspace_context_pack["cache_status"] == "hit"
        selected_paths = {chunk["path"] for chunk in cached_workspace_context_pack["context_pack"]["selected_chunks"]}
        assert selected_paths
        refresh_target = "docker-compose.yml" if "docker-compose.yml" in selected_paths else next(iter(selected_paths))
        if refresh_target == "docker-compose.yml":
            (workspace / "docker-compose.yml").write_text(
                "services:\n"
                "  postgres:\n    image: postgres:16\n"
                "  mongo:\n    image: mongo:8\n"
                "  redis:\n    image: redis:7\n"
                "  nats:\n    image: nats:2\n"
                "  mailhog:\n    image: mailhog/mailhog:v1.0.1\n"
            )
        else:
            (workspace / refresh_target).write_text("# Demo Workspace\n\nUpdated context for targeted invalidation.\n")
        workspace_context_refresh_after_edit = refresh_context_index(workspace)
        assert workspace_context_refresh_after_edit["workspace_fingerprint"] != original_workspace_fingerprint
        assert workspace_context_refresh_after_edit["rebuilt_chunk_count"] >= 1
        assert workspace_context_refresh_after_edit["pruned_semantic_cache_reason"] == "source-hash-drift"
        assert workspace_context_refresh_after_edit["pruned_semantic_cache_entries"] >= 1
        refreshed_workspace_context_pack = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        assert refreshed_workspace_context_pack["cache_status"] == "miss"
        manifest_payload = json.loads(workspace_manifest_path.read_text(encoding="utf-8"))
        manifest_payload["catalog_digest"] = "outdated-catalog-digest"
        workspace_manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        workspace_context_payload = json.loads(workspace_context_path.read_text(encoding="utf-8"))
        workspace_context_payload["catalog_digest"] = "outdated-catalog-digest"
        workspace_context_path.write_text(json.dumps(workspace_context_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        workspace_context_refresh_after_catalog_drift = refresh_context_index(workspace)
        assert workspace_context_refresh_after_catalog_drift["status"] == "refreshed"
        assert workspace_context_refresh_after_catalog_drift["refresh_reason"] == "catalog-digest"
        assert workspace_context_refresh_after_catalog_drift["pruned_semantic_cache_reason"] == "catalog-digest"
        assert workspace_context_refresh_after_catalog_drift["pruned_semantic_cache_entries"] >= 1
        post_drift_workspace_context_pack = show_workspace_context_pack(
            workspace,
            request_text="Inspect docker verification setup for the workspace",
            route_id="workstream",
            limit=4,
        )
        assert post_drift_workspace_context_pack["cache_status"] == "miss"
        usage_payload = json.loads(workspace_usage_path.read_text(encoding="utf-8"))
        assert usage_payload["fresh_hit_count"] >= 1
        assert usage_payload["refresh_reason_counts"]["catalog-digest"] >= 1
        assert usage_payload["route_resolution_counts"]["exact"] >= 1
        assert usage_payload["last_refresh_reason"] in {"catalog-digest", "manifest-match", "indexed-file-snapshot"}

        with tempfile.TemporaryDirectory() as structure_fixture_dir:
            structure_workspace = Path(structure_fixture_dir)
            module_root = structure_workspace / "packages" / "analysis-core"
            docs_root = module_root / "docs"
            src_root = module_root / "src"
            docs_root.mkdir(parents=True)
            src_root.mkdir(parents=True)
            (module_root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "analysis-core",
                        "scripts": {"smoke": "node src/app.ts"},
                        "dependencies": {"left-pad": "^1.3.0"},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (module_root / "README.md").write_text(
                "# Analysis Core\n\nIntro paragraph.\n\n## Overview\n\nDetails.\n\n## Troubleshooting\n\nFallback notes.\n",
                encoding="utf-8",
            )
            (docs_root / "guide.md").write_text(
                "# Guide\n\nOverview.\n\n## Setup\n\nSteps.\n\n## Recovery\n\nMore steps.\n",
                encoding="utf-8",
            )
            (src_root / "alpha.py").write_text(
                "import json\nfrom pathlib import Path\n\nclass FeatureGate:\n    pass\n\n\ndef run_job():\n    return Path('ok')\n",
                encoding="utf-8",
            )
            (src_root / "app.ts").write_text(
                "import { helper } from './helper';\nexport function startApp() {\n  return helper();\n}\n",
                encoding="utf-8",
            )
            (src_root / "helper.ts").write_text("export const helper = () => 'ok';\n", encoding="utf-8")
            (src_root / "feature.kt").write_text("package demo\n\nclass FeatureGate\n\nfun renderScreen() = Unit\n", encoding="utf-8")
            (src_root / "worker.rs").write_text("pub struct Worker;\n\npub fn run_worker() {}\n", encoding="utf-8")
            (src_root / "large.py").write_text("def large_fixture():\n    return 'x'\n\n" + ("# filler\n" * 9000), encoding="utf-8")

            if shutil.which("node"):
                ts_module_root = structure_workspace / "node_modules" / "typescript"
                ts_module_root.mkdir(parents=True)
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

            structure_refresh = refresh_context_index(structure_workspace)
            assert structure_refresh["status"] == "refreshed"
            assert Path(structure_refresh["structure_index_path"]).exists()
            assert structure_refresh["structure_summary"]["module_count"] >= 1
            assert structure_refresh["structure_summary"]["large_file_count"] >= 1
            assert structure_refresh["bounded_read_count"] >= 1
            assert structure_refresh["large_file_count"] >= 1
            assert structure_refresh["parser_backend_status"]["python_ast"]["status"] == "active"
            assert structure_refresh["parser_backend_status"]["markdown_sections"]["status"] == "active"
            if shutil.which("node"):
                assert structure_refresh["parser_backend_status"]["typescript_compiler"]["status"] == "available"
            else:
                assert structure_refresh["parser_backend_status"]["typescript_compiler"]["status"] == "unavailable"

            structure_refresh_again = refresh_context_index(structure_workspace)
            assert structure_refresh_again["status"] == "fresh"
            assert structure_refresh_again["refresh_reason"] == "manifest-match"

            structure_view = show_context_structure(
                structure_workspace,
                query_text="startApp FeatureGate run_worker guide setup",
                route_id="analysis",
                module_path="packages/analysis-core",
                limit=8,
            )
            assert structure_view["payload"]["within_ceiling"] is True
            assert payload_size_bytes(structure_view) <= SURFACE_PAYLOAD_CEILINGS["show_context_structure"]
            assert structure_view["summary"]["chunk_counts"]["symbol"] >= 3
            assert structure_view["modules"]
            assert structure_view["hotspots"]
            assert structure_view["matches"]
            assert any(match["match_kind"] in {"file", "symbol"} for match in structure_view["matches"])
            assert any(match["match_kind"] == "doc_section" for match in structure_view["matches"])

            structure_search = search_context_index(
                structure_workspace,
                "startApp FeatureGate run_worker guide setup",
                route_id="analysis",
                limit=8,
            )
            assert structure_search["resolved_route"]["route_id"] == "analysis"
            assert structure_search["matches"]
            assert any(match["match_kind"] in {"file", "symbol"} for match in structure_search["matches"])
            assert all("anchor_title" in match for match in structure_search["matches"])

            structure_pack = show_workspace_context_pack(
                structure_workspace,
                request_text="inspect structural hotspots and modules",
                route_id="analysis",
                limit=4,
            )
            assert structure_pack["workspace_context"]["structure_summary"]["large_file_count"] >= 1
            assert "hotspot_summary" in structure_pack["workspace_context"]
            assert "semantic_summary" in structure_pack["workspace_context"]
            init_workspace(structure_workspace)

            semantic_snapshot = persist_generated_memory_snapshot(
                structure_workspace,
                {
                    "title": "Cross-cutting analysis memory",
                    "source_audit_mode": "architecture",
                    "source_query_text": "boundary coupling broadread",
                    "source_module_path": "packages/analysis-core",
                    "confidence": 0.81,
                    "body_markdown": "- Boundary pressure between entrypoints and helpers.\n- Broadread risk around large module coordination.\n",
                    "provenance": {"source": "smoke-test"},
                },
            )
            assert semantic_snapshot["snapshot"]["snapshot_id"]
            assert list_generated_memory_snapshots(structure_workspace)["counts"]["active"] >= 1
            semantic_refresh = refresh_context_index(structure_workspace)
            assert semantic_refresh["semantic_summary"]["unit_count"] >= 1
            assert semantic_refresh["semantic_summary"]["snapshot_count"] >= 1
            assert semantic_refresh["semantic_rebuilt_unit_count"] >= 1
            semantic_refresh_again = refresh_context_index(structure_workspace)
            assert semantic_refresh_again["semantic_reused_unit_count"] >= 1

            semantic_disabled_search = search_context_index(
                structure_workspace,
                "crosscutting broadread memory snapshot",
                route_id="analysis",
                limit=4,
                semantic_mode="disabled",
            )
            semantic_enabled_search = search_context_index(
                structure_workspace,
                "crosscutting broadread memory snapshot",
                route_id="analysis",
                limit=8,
                semantic_mode="enabled",
            )
            assert all(match["match_source"] == "symbolic" for match in semantic_disabled_search["matches"])
            assert any(match["match_source"] == "semantic_assisted" for match in semantic_enabled_search["matches"])
            semantic_auto_without_explicit_analysis = search_context_index(
                structure_workspace,
                "crosscutting broadread memory snapshot",
                limit=8,
                semantic_mode="auto",
            )
            assert all(
                match["match_source"] == "symbolic"
                for match in semantic_auto_without_explicit_analysis["matches"]
            )

            semantic_structure_view = show_context_structure(
                structure_workspace,
                query_text="crosscutting broadread memory snapshot",
                route_id="analysis",
                limit=8,
                semantic_mode="enabled",
            )
            assert semantic_structure_view["semantic_summary"]["unit_count"] >= 1
            assert any(match["match_source"] == "semantic_assisted" for match in semantic_structure_view["matches"])

            semantic_context_pack = show_workspace_context_pack(
                structure_workspace,
                request_text="crosscutting broadread memory snapshot",
                route_id="analysis",
                limit=4,
                semantic_mode="enabled",
            )
            assert semantic_context_pack["semantic_mode"] == "enabled"
            assert semantic_context_pack["workspace_context"]["semantic_summary"]["snapshot_count"] >= 1
            assert any(chunk.get("match_source") == "semantic_assisted" for chunk in semantic_context_pack["context_pack"]["selected_chunks"])
            semantic_context_pack_auto = show_workspace_context_pack(
                structure_workspace,
                request_text="crosscutting broadread memory snapshot",
                limit=4,
                semantic_mode="auto",
            )
            assert not any(
                chunk.get("match_source") == "semantic_assisted"
                for chunk in semantic_context_pack_auto["context_pack"]["selected_chunks"]
            )

            architecture_audit = run_analysis_audit(
                structure_workspace,
                "architecture",
                query_text="crosscutting broadread memory snapshot",
                module_path="packages/analysis-core",
                limit=4,
                semantic_mode="enabled",
            )
            assert architecture_audit["mode"] == "architecture"
            assert architecture_audit["findings"]
            assert architecture_audit["evidence"]
            assert architecture_audit["semantic_matches"]
            assert architecture_audit["memory_snapshot_draft"]["source_audit_mode"] == "architecture"

            performance_audit = run_analysis_audit(
                structure_workspace,
                "performance",
                query_text="large file bounded read hotspot",
                limit=4,
                semantic_mode="auto",
            )
            assert performance_audit["mode"] == "performance"
            assert performance_audit["findings"]

            docs_style_audit = run_analysis_audit(
                structure_workspace,
                "docs_style",
                query_text="operator docs command surface",
                limit=4,
                semantic_mode="disabled",
            )
            assert docs_style_audit["mode"] == "docs_style"
            assert docs_style_audit["memory_snapshot_draft"]["source_audit_mode"] == "docs_style"

            (src_root / "alpha.py").write_text(
                "import json\nfrom pathlib import Path\n\nclass FeatureGate:\n    pass\n\n\ndef run_job():\n    return Path('updated')\n",
                encoding="utf-8",
            )
            incremental_refresh = refresh_context_index(structure_workspace)
            assert incremental_refresh["status"] == "refreshed"
            assert incremental_refresh["rebuilt_file_count"] >= 1
            assert incremental_refresh["reused_file_count"] >= 1

            (src_root / "worker.rs").unlink()
            removal_refresh = refresh_context_index(structure_workspace)
            assert removal_refresh["status"] == "refreshed"
            assert removal_refresh["removed_file_count"] >= 1
            updated_snapshot = persist_generated_memory_snapshot(
                structure_workspace,
                {
                    "snapshot_id": semantic_snapshot["snapshot"]["snapshot_id"],
                    "title": "Cross-cutting analysis memory",
                    "source_audit_mode": "architecture",
                    "source_query_text": "boundary coupling broadread",
                    "source_module_path": "packages/analysis-core",
                    "confidence": 0.86,
                    "body_markdown": "- Boundary pressure changed after worker removal.\n- Rebuild cost shifted to remaining symbols.\n",
                    "provenance": {"source": "smoke-test-update"},
                },
            )
            assert updated_snapshot["snapshot"]["confidence"] == 0.86
            semantic_after_snapshot_edit = refresh_context_index(structure_workspace)
            assert semantic_after_snapshot_edit["semantic_rebuilt_unit_count"] >= 1

        with tempfile.TemporaryDirectory() as fallback_fixture_dir:
            fallback_workspace = Path(fallback_fixture_dir)
            fallback_src = fallback_workspace / "src"
            fallback_src.mkdir(parents=True)
            (fallback_src / "fallback.ts").write_text(
                "export function fallbackEntry() {\n  return 'fallback';\n}\n",
                encoding="utf-8",
            )
            fallback_refresh = refresh_context_index(fallback_workspace)
            assert fallback_refresh["parser_backend_status"]["typescript_compiler"]["status"] == "unavailable"
            fallback_search = search_context_index(fallback_workspace, "fallbackEntry", route_id="analysis", limit=4)
            assert fallback_search["matches"]
            assert any(match["match_kind"] in {"file", "symbol"} for match in fallback_search["matches"])

        preview = preview_workspace_init(workspace)
        assert preview["must_confirm_before_write"] is True
        assert preview["paths"]["workstreams_index"].endswith("workstreams/index.json")
        assert preview["repo_maturity"]["mode"] == "scaffold"
        assert "mobile-platform" in preview["selected_profiles"]
        assert "backend-platform" in preview["selected_profiles"]
        assert preview["planning_policy"]["explicit_stage_plan_required"] is True

        pre_init_advice = workflow_advice(workspace, "Implement a checkout feature across web and backend")
        assert pre_init_advice["workspace_initialized"] is False
        assert pre_init_advice["repo_maturity"]["mode"] == "scaffold"
        assert pre_init_advice["initialization_advice"]["should_propose"] is True
        assert pre_init_advice["requires_confirmation"] is True
        assert pre_init_advice["track_recommendation"]["recommended_mode"] == "workstream"

        greenfield_advice = workflow_advice(workspace, "Build a new Expo mobile app from scratch")
        assert greenfield_advice["starter_recommendation"]["recommended_preset_id"] == "expo-mobile"
        assert "starter first" in greenfield_advice["initialization_advice"]["reason"].lower() or greenfield_advice["repo_maturity"]["mode"] == "scaffold"

        self_host_preview = preview_workspace_init(repo_root)
        assert self_host_preview["repo_maturity"]["mode"] == "existing"
        assert "plugin-platform" in self_host_preview["selected_profiles"]
        assert {"python", "codex-plugin", "mcp-server", "local-dashboard"}.issubset(set(self_host_preview["detected_stacks"]))
        assert self_host_preview["plugin_platform"]["enabled"] is True
        assert self_host_preview["plugin_platform"]["primary_plugin_root"] == "plugins/agentiux-dev"
        assert self_host_preview["plugin_platform"]["release_readiness_command"] == f"{python_launcher_string()} plugins/agentiux-dev/scripts/release_readiness.py"

        empty_workspace = temp_root / "empty-workspace"
        empty_workspace.mkdir()
        empty_preview = preview_workspace_init(empty_workspace)
        empty_advice = workflow_advice(empty_workspace, "Build a new Expo mobile app from scratch")
        assert empty_preview["repo_maturity"]["mode"] == "empty"
        assert empty_advice["repo_maturity"]["mode"] == "empty"
        assert "starter first" in empty_advice["initialization_advice"]["reason"].lower()

        autoflow_workspace = temp_root / "autoflow-workspace"
        autoflow_workspace.mkdir()
        _seed_workspace(autoflow_workspace)
        autoflow_advice = workflow_advice(
            autoflow_workspace,
            "Operator request already normalized by Codex",
            canonical_request_text="Fix CTA spacing in the hero section",
            auto_create=True,
        )
        assert autoflow_advice["workspace_initialized"] is True
        assert autoflow_advice["request_analysis"]["analysis_source"] == "canonical_request_text"
        assert autoflow_advice["request_analysis"]["request_kind"] == "point_task"
        assert autoflow_advice["initialization_advice"]["auto_applied"] is True
        assert autoflow_advice["applied_actions"][0]["action"] == "initialize_workspace"
        assert autoflow_advice["applied_action"]["action"] == "create_task"
        assert autoflow_advice["requires_confirmation"] is False
        assert current_task(autoflow_workspace)["task_id"] == autoflow_advice["applied_action"]["task_id"]

        auto_workstream_workspace = temp_root / "auto-workstream-workspace"
        auto_workstream_workspace.mkdir()
        _seed_workspace(auto_workstream_workspace)
        auto_workstream_advice = workflow_advice(
            auto_workstream_workspace,
            "Implement checkout feature across web and backend",
            auto_create=True,
        )
        assert auto_workstream_advice["workspace_initialized"] is True
        assert auto_workstream_advice["applied_action"]["action"] == "initialize_workspace"
        assert auto_workstream_advice["track_recommendation"]["recommended_mode"] == "workstream"
        assert auto_workstream_advice["requires_confirmation"] is True

        reset_workspace = temp_root / "reset-workspace"
        reset_workspace.mkdir()
        _seed_workspace(reset_workspace)
        reset_preview_before_init = preview_reset_workspace_state(reset_workspace)
        assert reset_preview_before_init["workspace_root_exists"] is False
        assert reset_preview_before_init["registry_entry_exists"] is False
        init_workspace(reset_workspace)
        refresh_context_index(reset_workspace)
        reset_learning = write_learning_entry(
            reset_workspace,
            {
                "entry_id": "reset-workspace-learning",
                "kind": "test-harness",
                "status": "open",
                "symptom": "Reset should clear workspace-scoped analytics and context cache slices.",
                "fix_applied": "Reset removes the external slice before re-init.",
                "source": "smoke-test",
            },
        )
        assert reset_learning["entry"]["entry_id"] == "reset-workspace-learning"
        reset_preview = preview_reset_workspace_state(reset_workspace)
        assert reset_preview["workspace_root_exists"] is True
        assert reset_preview["context_cache_exists"] is True
        assert reset_preview["analytics_cleanup"]["learning_paths"]
        reset_result = reset_workspace_state(reset_workspace)
        assert reset_result["removed_workspace_root"] is True
        assert reset_result["removed_registry_entry"] is True
        assert reset_result["removed_context_cache_root"] is True
        assert reset_result["analytics_cleanup"]["removed_learning_paths"]
        assert reset_result["analytics_cleanup"]["removed_event_paths"]
        assert reset_result["post_reset_preview"]["already_initialized"] is False
        progress("capability catalogs, context indexing, and self-host detection")
        stale_plugin_fixture = _make_stale_plugin_fixture(repo_root)
        repair_preview = preview_repair_workspace_state(repo_root)
        assert repair_preview["changes"]["local_dev_policy"]["infra_mode"] == "not_applicable"
        assert repair_preview["changes"]["remove_legacy_docker_policy"] is True
        repaired_preview_workstream = next(
            item for item in repair_preview["changes"]["workstreams"] if item["workstream_id"] == stale_plugin_fixture["workstream_id"]
        )
        assert repaired_preview_workstream["title_after"] == "plugin-production-readiness"
        assert repaired_preview_workstream["kind_after"] == "feature"
        assert repaired_preview_workstream["planner_context"]["needs_plugin_runtime"] is True
        assert repaired_preview_workstream["plan_status_after"] == "needs_user_confirmation"
        assert repaired_preview_workstream["removed_stage_ids"] == []
        repaired_plugin_state = repair_workspace_state(repo_root)
        assert repaired_plugin_state["workspace_state"]["local_dev_policy"]["infra_mode"] == "not_applicable"
        assert repaired_plugin_state["workspace_state"]["state_repair_status"]["source_schema_version"] == STATE_SCHEMA_VERSION
        assert repaired_plugin_state["workspace_state"]["state_repair_status"]["target_schema_version"] == STATE_SCHEMA_VERSION
        assert repaired_plugin_state["workspace_state"]["state_repair_status"]["source_workstream_schema_versions"][stale_plugin_fixture["workstream_id"]] == 4
        assert "docker_policy" not in repaired_plugin_state["workspace_state"]
        repaired_workstream = next(
            item for item in repaired_plugin_state["workstreams"]["items"] if item["workstream_id"] == stale_plugin_fixture["workstream_id"]
        )
        assert repaired_workstream["title"] == "plugin-production-readiness"
        assert repaired_workstream["kind"] == "feature"
        assert repaired_workstream["scope_summary"] == "Lock plugin runtime convergence, verification, and release-readiness contracts."
        assert repaired_workstream["branch_hint"] == "feature/plugin-production-readiness"
        assert repaired_workstream["plan_status"] == "confirmed"
        _assert_stage_ids(
            repaired_plugin_state["stage_register"],
            expected_present=["01-local-dev-infra-and-boot"],
            expected_absent=[],
        )
        canonical_plugin_paths = stale_plugin_fixture["paths"]
        repaired_canonical_register = _read_json_file(Path(canonical_plugin_paths["current_workstream_stage_register"]))
        assert "is_mirror" not in repaired_canonical_register
        repaired_root_register = _read_json_file(Path(workspace_paths(repo_root)["stage_register"]))
        assert repaired_root_register["is_mirror"] is True
        assert repaired_root_register["mirror_of_workstream_id"] == stale_plugin_fixture["workstream_id"]
        canonical_brief = Path(canonical_plugin_paths["current_workstream_active_brief"]).read_text()
        assert "<!-- derived-mirror: true -->" not in canonical_brief
        root_brief = Path(workspace_paths(repo_root)["active_brief"]).read_text()
        assert "<!-- derived-mirror: true -->" in root_brief
        plugin_verification_recipes = read_verification_recipes(repo_root)
        assert plugin_verification_recipes["verification_fragment_resolution"]["source_module_ids"]
        assert any(case["id"] == "plugin-smoke" for case in plugin_verification_recipes["cases"])
        plugin_helper_root = repo_root / ".verification"
        shutil.rmtree(plugin_helper_root, ignore_errors=True)
        try:
            sync_verification_helpers(repo_root)
            plugin_coverage = audit_verification_coverage(repo_root)
            assert plugin_coverage["status"] == "clean"
            assert plugin_coverage["coverage"]["plugin"] is True
            assert plugin_coverage["coverage"]["dashboard"] is True
            assert plugin_coverage["coverage"]["dashboard_visual"] is True
            assert plugin_coverage["coverage"]["dashboard_browser_layout_audit"] is True
            assert plugin_coverage["warning_count"] == 0
        finally:
            shutil.rmtree(plugin_helper_root, ignore_errors=True)
        _assert_no_default_origin(repaired_plugin_state["stage_register"])
        _assert_no_default_origin(plugin_verification_recipes)
        progress("plugin self-host repair flow and plugin verification fragments")

        backend_workspace = temp_root / "backend-workspace"
        backend_workspace.mkdir()
        _seed_backend_workspace(backend_workspace, with_infra=True)
        init_workspace(backend_workspace)
        backend_overview = list_workspaces()
        assert any(item["workspace_path"] == str(backend_workspace.resolve()) for item in backend_overview["workspaces"])
        backend_snapshot = dashboard_snapshot(backend_workspace)
        assert backend_snapshot["workspace_cockpit"]["workspace_path"] == str(backend_workspace.resolve())
        assert backend_snapshot["workspace_cockpit"]["state_kind"] == "initialized"
        assert backend_snapshot["workspace_cockpit"]["quality"]["recent_runs"] == []
        assert backend_snapshot["workspace_cockpit"]["quality"]["events"] == []
        backend_coverage = audit_verification_coverage(backend_workspace)
        assert backend_coverage["status"] == "warning"
        assert backend_coverage["warning_count"] >= 1
        backend_register = create_workstream(backend_workspace, "Backend Infra Improvements")["current_workstream"]["register"]
        assert backend_register["plan_status"] == "needs_user_confirmation"
        assert backend_register["stages"] == []
        _assert_no_default_origin(backend_register)

        visual_gap_workspace = temp_root / "visual-gap-workspace"
        visual_gap_workspace.mkdir()
        _seed_workspace(visual_gap_workspace)
        init_workspace(visual_gap_workspace)
        visual_gap_workstream = create_workstream(visual_gap_workspace, "Visual Coverage Audit", kind="feature")["created_workstream_id"]
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
                        "argv": [sys.executable, "-c", "print('web contract ok')"],
                    },
                    {
                        "id": "android-contract-only",
                        "title": "Android contract only",
                        "surface_type": "android",
                        "runner": "shell-contract",
                        "changed_path_globs": ["apps/mobile/android/**"],
                        "host_requirements": ["python"],
                        "argv": [sys.executable, "-c", "print('android contract ok')"],
                    },
                ],
                "suites": [
                    {
                        "id": "full",
                        "title": "Full Suite",
                        "case_ids": ["web-contract-only", "android-contract-only"],
                    }
                ],
            },
            workstream_id=visual_gap_workstream,
        )
        visual_gap_audit = audit_verification_coverage(visual_gap_workspace, workstream_id=visual_gap_workstream)
        visual_gap_ids = {gap["gap_id"] for gap in visual_gap_audit["gaps"]}
        assert "missing-web-visual-verification" in visual_gap_ids
        assert "missing-web-browser-layout-audit" in visual_gap_ids
        assert "missing-android-visual-verification" not in visual_gap_ids
        assert "missing-mobile-native-layout-audit" not in visual_gap_ids
        assert "missing-android-native-layout-audit" not in visual_gap_ids
        assert visual_gap_audit["coverage"]["android_visual"] is True
        assert visual_gap_audit["coverage"]["android_native_layout_audit"] is True
        web_semantic_gap_workspace = temp_root / "web-semantic-gap-workspace"
        web_semantic_gap_workspace.mkdir()
        _seed_web_only_workspace(web_semantic_gap_workspace)
        init_workspace(web_semantic_gap_workspace)
        web_semantic_gap_workstream = create_workstream(
            web_semantic_gap_workspace,
            "Web Semantic Coverage Audit",
            kind="feature",
        )["created_workstream_id"]
        write_verification_recipes(
            web_semantic_gap_workspace,
            {
                "baseline_policy": {
                    "canonical_baselines": "project_owned",
                    "transient_artifacts": "external_state_only",
                },
                "cases": [
                    {
                        "id": "web-visual-no-semantic",
                        "title": "Web visual without semantic assertions",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "changed_path_globs": ["app/**"],
                        "routes_or_screens": ["/"],
                        "host_requirements": ["python"],
                        "argv": [sys.executable, "-c", "print('web visual no semantic')"],
                        "target": {"route": "/"},
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/__screenshots__/home.png"},
                    },
                    {
                        "id": "web-visual-narrow-semantic",
                        "title": "Web visual with incomplete semantic checks",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "changed_path_globs": ["app/**"],
                        "routes_or_screens": ["/checkout"],
                        "host_requirements": ["python"],
                        "argv": [sys.executable, "-c", "print('web visual narrow semantic')"],
                        "target": {"route": "/checkout"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-visual-narrow-semantic.json",
                            "required_checks": ["visibility"],
                            "targets": [
                                {
                                    "target_id": "checkout-main",
                                    "locator": {"kind": "role", "value": "main"},
                                }
                            ],
                        },
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/__screenshots__/checkout.png"},
                    },
                    {
                        "id": "web-browser-layout",
                        "title": "Web browser layout audit",
                        "surface_type": "web",
                        "runner": "browser-layout-audit",
                        "changed_path_globs": ["app/**"],
                        "routes_or_screens": ["/"],
                        "host_requirements": ["web", "browser-runtime"],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "1440x1024"},
                        "browser_layout_audit": {
                            "url": "http://127.0.0.1:3000/",
                            "report_path": "web-browser-layout.json",
                            "screenshot_path": "web-browser-layout.png",
                        },
                    },
                ],
                "suites": [
                    {
                        "id": "full",
                        "title": "Full Suite",
                        "case_ids": ["web-visual-no-semantic", "web-visual-narrow-semantic", "web-browser-layout"],
                    }
                ],
            },
            workstream_id=web_semantic_gap_workstream,
        )
        web_semantic_gap_audit = audit_verification_coverage(
            web_semantic_gap_workspace,
            workstream_id=web_semantic_gap_workstream,
        )
        web_semantic_gap_ids = {gap["gap_id"] for gap in web_semantic_gap_audit["gaps"]}
        assert web_semantic_gap_audit["coverage"]["web_visual"] is True
        assert "missing-web-visual-verification" not in web_semantic_gap_ids
        assert "missing-web-browser-layout-audit" not in web_semantic_gap_ids
        assert "web-visual-no-semantic-missing-web-semantic-assertions" in web_semantic_gap_ids
        assert "web-visual-narrow-semantic-missing-core-web-semantic-checks" in web_semantic_gap_ids
        progress("coverage audit scenarios for backend, web, and visual gap detection")

        web_workspace = temp_root / "web-workspace"
        web_workspace.mkdir()
        _seed_web_only_workspace(web_workspace)
        init_workspace(web_workspace)
        web_register = create_workstream(
            web_workspace,
            "Fix Hero CTA Spacing",
            kind="fix",
            scope_summary="Tighten the homepage hero CTA spacing without broad layout work.",
        )["current_workstream"]["register"]
        assert web_register["plan_status"] == "needs_user_confirmation"
        assert web_register["stages"] == []
        _assert_no_default_origin(web_register)
        web_custom_register = read_stage_register(web_workspace)
        web_custom_register["stages"] = [
            _stage_definition(
                "implementation-scope-lock",
                "Implementation Scope Lock",
                "Lock the approved UI scope before targeted implementation.",
                ["implementation-scope-lock.1-confirm-scope"],
            ),
            _stage_definition(
                "ui-polish-validation",
                "UI Polish Validation",
                "Capture focused design polish and regression notes before deterministic verification.",
                ["ui-polish-validation.1-review-polish-scope"],
                allowed_scope=["hero spacing regression checks", "targeted polish notes"],
                deliverables=["Custom polish findings", "Updated implementation notes"],
                verification_selectors={"surface_ids": ["dashboard-home"]},
                verification_policy={"default_mode": "targeted"},
                planner_notes=["User-approved custom polish stage for this focused web workstream."],
            ),
            _stage_definition(
                "deterministic-verification",
                "Deterministic Verification",
                "Verify the changed slice with deterministic checks.",
                ["deterministic-verification.1-run-checks"],
            ),
        ]
        web_custom_register["current_stage"] = "implementation-scope-lock"
        web_custom_register["stage_status"] = "planned"
        web_custom_register["current_slice"] = "implementation-scope-lock.1-confirm-scope"
        web_custom_register["remaining_slices"] = []
        web_custom_register["slice_status"] = "planned"
        web_custom_register["active_goal"] = "Lock the approved UI scope before targeted implementation."
        web_custom_register["next_task"] = "Lock the approved UI scope before targeted implementation."
        _assert_stage_ids(
            web_custom_register,
            expected_present=[
                "implementation-scope-lock",
                "ui-polish-validation",
                "deterministic-verification",
            ],
            expected_absent=[],
        )
        custom_stage = next(stage for stage in web_custom_register["stages"] if stage["id"] == "ui-polish-validation")
        assert custom_stage["verification_selectors"] == {"surface_ids": ["dashboard-home"]}
        assert custom_stage["verification_policy"] == {"default_mode": "targeted"}
        _assert_no_default_origin(web_custom_register)
        try:
            write_stage_register(web_workspace, web_custom_register, confirmed_stage_plan_edit=False)
        except ValueError as exc:
            assert "explicit confirmation" in str(exc)
        else:
            raise AssertionError("Custom stage replanning should require explicit confirmation")
        persisted_web_custom_register = write_stage_register(web_workspace, web_custom_register, confirmed_stage_plan_edit=True)
        persisted_custom_stage = next(stage for stage in persisted_web_custom_register["stages"] if stage["id"] == "ui-polish-validation")
        assert persisted_custom_stage["origin"] == "custom"
        assert persisted_custom_stage["planner_notes"] == [
            "User-approved custom polish stage for this focused web workstream."
        ]
        custom_completed_register = read_stage_register(web_workspace)
        custom_stage_index = next(
            index for index, stage in enumerate(custom_completed_register["stages"]) if stage["id"] == "ui-polish-validation"
        )
        custom_completed_register["stages"][custom_stage_index]["status"] = "completed"
        custom_completed_register["stages"][custom_stage_index]["completed_at"] = "2026-03-30T00:00:00Z"
        next_web_stage = custom_completed_register["stages"][custom_stage_index + 1]
        custom_completed_register["current_stage"] = next_web_stage["id"]
        custom_completed_register["stage_status"] = "planned"
        custom_completed_register["current_slice"] = next_web_stage["canonical_execution_slices"][0]
        custom_completed_register["remaining_slices"] = next_web_stage["canonical_execution_slices"][1:]
        custom_completed_register["last_completed_stage"] = "ui-polish-validation"
        write_stage_register(web_workspace, custom_completed_register, confirmed_stage_plan_edit=False)
        custom_immutable = read_stage_register(web_workspace)
        for stage in custom_immutable["stages"]:
            if stage["id"] == "ui-polish-validation":
                stage["title"] = "Changed Custom Stage"
        try:
            write_stage_register(web_workspace, custom_immutable, confirmed_stage_plan_edit=True)
        except ValueError as exc:
            assert "Completed stage cannot be modified" in str(exc)
        else:
            raise AssertionError("Completed custom stage mutation should have failed")

        initialized = init_workspace(workspace)
        assert initialized["workspace_state"]["schema_version"] == STATE_SCHEMA_VERSION
        assert initialized["workspace_state"]["current_workstream_id"] is None
        assert initialized["workspace_state"]["workspace_mode"] == "workspace"
        assert initialized["workspace_state"]["local_dev_policy"]["infra_mode"] == "docker_required"
        paths = workspace_paths(workspace)
        assert Path(paths["workstreams_index"]).exists()
        assert Path(paths["tasks_index"]).exists()
        assert paths["current_workstream_stage_register"] == ""

        migrated = migrate_workspace_state(workspace)
        assert migrated["workspace_state"]["current_workstream_id"] is None

        primary_workstream = create_workstream(
            workspace,
            "Workspace Planning",
            kind="feature",
            scope_summary="Lock the approved workspace implementation and verification scope.",
        )
        verification_workstream_id = primary_workstream["created_workstream_id"]
        assert primary_workstream["created_workstream_id"] == "workspace-planning"
        assert primary_workstream["current_workstream"]["register"]["plan_status"] == "needs_user_confirmation"
        assert primary_workstream["current_workstream"]["register"]["stages"] == []
        assert Path(workspace_paths(workspace)["current_workstream_stage_register"]).exists()
        assert not Path(workspace_paths(workspace)["verification_recipes"]).exists()

        confirmed_register = _confirm_stage_plan(
            workspace,
            [
                _stage_definition(
                    "scope-lock",
                    "Scope Lock",
                    "Lock the approved workspace scope before implementation.",
                    ["scope-lock.1-confirm-approved-scope"],
                ),
                _stage_definition(
                    "implementation",
                    "Implementation",
                    "Implement the approved workspace slice.",
                    ["implementation.1-apply-approved-change"],
                ),
                _stage_definition(
                    "verification",
                    "Verification",
                    "Run deterministic verification for the approved slice.",
                    ["verification.1-run-deterministic-checks"],
                ),
            ],
        )
        assert confirmed_register["plan_status"] == "confirmed"

        workstream_advice = workflow_advice(workspace, "Implement checkout feature across web and backend", auto_create=True)
        assert workstream_advice["applied_action"] is None
        assert workstream_advice["requires_confirmation"] is True
        assert workstream_advice["retrieval"]["mode"] == "execution"
        assert workstream_advice["payload"]["within_ceiling"] is True
        assert workstream_advice["track_recommendation"]["recommended_mode"] == "workstream"
        assert len(list_workstreams(workspace)["items"]) == 1
        generated_stage_brief = get_active_brief(workspace)
        assert generated_stage_brief["brief_generation_status"] == "generated"
        assert "## Goal" in generated_stage_brief["markdown"]
        assert "## Current Truth" in generated_stage_brief["markdown"]
        assert "## Deterministic Verification Plan" in generated_stage_brief["markdown"]

        task_advice = workflow_advice(workspace, "Fix CTA spacing in the hero section", auto_create=True)
        assert task_advice["auto_create_supported"] is True
        assert task_advice["retrieval"]["mode"] == "fix"
        assert task_advice["payload"]["within_ceiling"] is True
        assert payload_size_bytes(task_advice) <= SURFACE_PAYLOAD_CEILINGS["workflow_advice"]
        assert task_advice["applied_action"]["action"] == "create_task"
        assert task_advice["requires_confirmation"] is False
        task_id = task_advice["applied_action"]["task_id"]
        task_result = read_task(workspace, task_id=task_id)
        assert task_id
        assert "cta-spacing" in task_id
        assert task_result["linked_workstream_id"] == primary_workstream["created_workstream_id"]
        assert current_task(workspace)["task_id"] == task_id
        assert task_result["brief_generation_status"] == "generated"
        assert "## Objective" in task_result["brief_markdown"]
        assert "## Current Truth" in task_result["brief_markdown"]
        assert "## Verification Notes" in task_result["brief_markdown"]
        reused_task_advice = workflow_advice(workspace, "Fix CTA spacing in the hero section", auto_create=True)
        assert reused_task_advice["applied_action"]["action"] == "reuse_current_task"
        assert reused_task_advice["applied_action"]["task_id"] == task_id
        assert read_stage_register(workspace)["workstream_id"] == primary_workstream["created_workstream_id"]

        set_active_brief(workspace, "# TaskBrief\n\nFix CTA spacing.\n")
        manual_task_brief = get_active_brief(workspace)
        assert "Fix CTA spacing" in manual_task_brief["markdown"]
        assert manual_task_brief["brief_generation_status"] == "manual"
        assert read_task(workspace, task_id=task_id)["brief_generation_status"] == "manual"
        closed_task = close_task(workspace, verification_summary={"status": "completed", "summary": "Spacing fixed."})
        assert closed_task["status"] == "completed"
        assert current_task(workspace) is None

        switch_workstream(workspace, primary_workstream["created_workstream_id"])
        assert current_workstream(workspace)["workstream_id"] == primary_workstream["created_workstream_id"]

        host_support = show_host_support(workspace)
        assert host_support["host_os"] in {"macos", "linux", "windows"}
        assert "core_runtime" in host_support["host_capabilities"]
        assert "host_setup" in host_support

        host_setup_host = host_support["host_os"]
        if host_setup_host in {"macos", "linux"}:
            fake_host_setup_log = temp_root / "host-setup.log"
            fake_node = temp_root / "fake-node"
            fake_android_adb = temp_root / "fake-android-adb"
            fake_installer_bin = temp_root / "host-setup-bin"
            fake_installer_bin.mkdir()
            fake_installer, fake_sudo = write_fake_host_setup_installer(fake_installer_bin, host_setup_host)
            override_keys = [
                "AGENTIUX_DEV_HOST_SETUP_LOG",
                "AGENTIUX_DEV_TOOL_OVERRIDE_NODE",
                "AGENTIUX_DEV_TOOL_OVERRIDE_ADB",
                "AGENTIUX_DEV_TOOL_OVERRIDE_BREW",
                "AGENTIUX_DEV_TOOL_OVERRIDE_APT_GET",
                "AGENTIUX_DEV_TOOL_OVERRIDE_SUDO",
            ]
            override_backup = {key: os.environ.get(key) for key in override_keys}
            os.environ["AGENTIUX_DEV_HOST_SETUP_LOG"] = str(fake_host_setup_log)
            os.environ["AGENTIUX_DEV_TOOL_OVERRIDE_NODE"] = str(fake_node)
            os.environ["AGENTIUX_DEV_TOOL_OVERRIDE_ADB"] = str(fake_android_adb)
            if host_setup_host == "macos":
                os.environ["AGENTIUX_DEV_TOOL_OVERRIDE_BREW"] = str(fake_installer)
                os.environ.pop("AGENTIUX_DEV_TOOL_OVERRIDE_APT_GET", None)
                os.environ.pop("AGENTIUX_DEV_TOOL_OVERRIDE_SUDO", None)
            else:
                os.environ["AGENTIUX_DEV_TOOL_OVERRIDE_APT_GET"] = str(fake_installer)
                if fake_sudo is not None:
                    os.environ["AGENTIUX_DEV_TOOL_OVERRIDE_SUDO"] = str(fake_sudo)
                os.environ.pop("AGENTIUX_DEV_TOOL_OVERRIDE_BREW", None)
            try:
                repair_workspace_state(workspace)
                host_setup_plan = show_host_setup_plan(workspace, requirement_ids=["mobile_verification_android"])
                assert host_setup_plan["status"] == "needs_confirmation"
                assert host_setup_plan["requires_confirmation"] is True
                assert {step["tool_id"] for step in host_setup_plan["steps"] if step["mode"] == "automatic"} == {"adb", "node"}

                try:
                    install_host_requirements(workspace, requirement_ids=["mobile_verification_android"])
                except ValueError as exc:
                    assert "explicit confirmation" in str(exc)
                else:
                    raise AssertionError("install_host_requirements should require confirmation")

                install_host_result = install_host_requirements(
                    workspace,
                    requirement_ids=["mobile_verification_android"],
                    confirmed=True,
                )
                assert install_host_result["status"] == "completed"
                assert fake_node.exists()
                assert fake_android_adb.exists()
                assert fake_host_setup_log.exists()

                host_support_after_install = show_host_support(workspace)
                assert host_support_after_install["toolchain_capabilities"]["mobile_verification_android"]["available"] is True
                assert host_support_after_install["host_setup"]["last_operation"]["status"] == "completed"

                fake_android_adb.unlink()
                repair_host_result = repair_host_requirements(
                    workspace,
                    requirement_ids=["android_tooling"],
                    confirmed=True,
                )
                assert repair_host_result["status"] == "completed"
                assert fake_android_adb.exists()
                host_support_after_repair = show_host_support(workspace)
                assert host_support_after_repair["toolchain_capabilities"]["android_tooling"]["available"] is True
                assert host_support_after_repair["host_setup"]["last_operation"]["operation"] == "repair_host_requirements"
            finally:
                for key, value in override_backup.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        windows_override_keys = [
            "AGENTIUX_DEV_ALLOW_TEST_OVERRIDES",
            "AGENTIUX_DEV_TOOL_OVERRIDE_WINGET",
            "AGENTIUX_DEV_TOOL_OVERRIDE_CHOCO",
        ]
        windows_override_backup = {key: os.environ.get(key) for key in windows_override_keys}
        os.environ["AGENTIUX_DEV_TOOL_OVERRIDE_WINGET"] = "available"
        os.environ["AGENTIUX_DEV_TOOL_OVERRIDE_CHOCO"] = "available"
        try:
            os.environ.pop("AGENTIUX_DEV_ALLOW_TEST_OVERRIDES", None)
            windows_node_recipe_without_flag = _host_setup_recipe_for_tool("node", "windows")
            assert windows_node_recipe_without_flag["installer_available"] is False
            os.environ["AGENTIUX_DEV_ALLOW_TEST_OVERRIDES"] = "1"
            windows_node_recipe = _host_setup_recipe_for_tool("node", "windows")
            assert windows_node_recipe["mode"] == "automatic"
            assert windows_node_recipe["installer_available"] is True
            assert windows_node_recipe["installer_id"] in {"winget", "choco"}
            assert windows_node_recipe["commands"]
            windows_adb_recipe = _host_setup_recipe_for_tool("adb", "windows")
            assert windows_adb_recipe["mode"] == "automatic"
            assert windows_adb_recipe["installer_available"] is True
            assert windows_adb_recipe["installer_id"] in {"winget", "choco"}
            assert windows_adb_recipe["commands"]
            assert windows_adb_recipe["available_installers"]
        finally:
            for key, value in windows_override_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        design_brief = write_design_brief(
            workspace,
            {
                "status": "briefed",
                "platform": "web",
                "surface": "marketing-home",
                "style_goals": ["editorial", "precise"],
            },
            workstream_id=verification_workstream_id,
        )
        assert design_brief["status"] == "briefed"
        assert read_design_brief(workspace)["surface"] == "marketing-home"

        preview_asset = temp_root / "reference-preview.txt"
        preview_asset.write_text("preview")
        cached = cache_reference_preview(workspace, str(preview_asset), "hero-ref")
        assert Path(cached["cached_preview_path"]).exists()

        board = write_reference_board(
            workspace,
            {
                "title": "Current Reference Board",
                "platform": "web",
                "iteration_count": 1,
                "search_notes": ["look for editorial dashboards"],
                "candidates": [
                    {
                        "id": "ref-1",
                        "title": "Reference One",
                        "url": "https://example.com/ref-1",
                        "rationale": "strong editorial layout",
                    },
                    {
                        "id": "ref-2",
                        "title": "Reference Two",
                        "url": "https://example.com/ref-2",
                        "cached_preview_source_path": str(preview_asset),
                        "rationale": "strong hierarchy",
                    },
                ],
                "selected_candidate_ids": ["ref-1"],
                "rejected_candidate_ids": ["ref-2"],
            },
        )
        assert len(board["candidates"]) == 2
        assert read_reference_board(workspace)["selected_candidate_ids"] == ["ref-1"]
        assert list_reference_boards(workspace)["boards"]

        handoff = write_design_handoff(
            workspace,
            {
                "status": "ready",
                "platform": "web",
                "layout_system": ["12-col editorial grid"],
                "component_inventory": ["hero", "feature rail", "proof band"],
                "verification_hooks": [
                    "route:/",
                    "viewport:1440x1024",
                    "mask:.clock,.live-counter",
                ],
            },
        )
        assert handoff["status"] == "ready"
        assert read_design_handoff(workspace)["verification_hooks"][0] == "route:/"

        legacy_design_workspace = temp_root / "legacy-design-workspace"
        legacy_design_workspace.mkdir()
        _seed_workspace(legacy_design_workspace)
        init_workspace(legacy_design_workspace)
        legacy_design_workstream_id = create_workstream(
            legacy_design_workspace,
            "Legacy Design Compatibility",
            kind="feature",
            scope_summary="Exercise design schema v1 read compatibility and v2 write normalization.",
        )["created_workstream_id"]
        legacy_design_paths = workspace_paths(legacy_design_workspace, workstream_id=legacy_design_workstream_id)
        _write_json_file(
            Path(legacy_design_paths["design_brief"]),
            {
                "schema_version": 1,
                "workspace_path": str(legacy_design_workspace.resolve()),
                "workstream_id": legacy_design_workstream_id,
                "status": "briefed",
                "platform": "web",
                "surface": "legacy-home",
                "style_goals": ["calm"],
                "legacy_extra": {"preserve": True},
            },
        )
        legacy_brief = read_design_brief(legacy_design_workspace, workstream_id=legacy_design_workstream_id)
        assert legacy_brief["schema_version"] == 2
        assert legacy_brief["surface"] == "legacy-home"
        assert legacy_brief["legacy_extra"]["preserve"] is True
        normalized_brief = write_design_brief(
            legacy_design_workspace,
            {
                "status": "ready_for_execution",
                "platform": "web",
                "affected_surfaces": [
                    {
                        "surface_id": "legacy-home",
                        "platform": "web",
                        "route_or_screen": "/legacy",
                        "summary": "Legacy home hero and CTA strip",
                    }
                ],
                "user_flows": [
                    {
                        "flow_id": "legacy-signup",
                        "title": "Legacy signup flow",
                        "entry_points": ["hero-cta"],
                        "success_state": "Signup modal visible",
                        "steps": ["Tap CTA", "Confirm modal content"],
                    }
                ],
                "state_coverage": [
                    {
                        "state_id": "legacy-default",
                        "surface_id": "legacy-home",
                        "status_kind": "covered",
                        "notes": "Default hero state covered",
                        "verified_by": ["playwright-visual"],
                    }
                ],
                "ux_rationale": [
                    {
                        "decision_id": "legacy-cta-prominence",
                        "surface_ids": ["legacy-home"],
                        "rationale": "Keep the primary CTA visible above the fold.",
                        "tradeoff": "Slightly denser hero layout.",
                    }
                ],
                "critical_actions": [
                    {
                        "action_id": "legacy-hero-cta",
                        "flow_id": "legacy-signup",
                        "surface_id": "legacy-home",
                        "interaction_type": "tap",
                        "priority": "high",
                        "verification_path_id": "legacy-hero-cta-path",
                    }
                ],
                "testability_guidance": {
                    "stable_targets": ["hero-cta"],
                    "masks": [".clock"],
                    "preconditions": ["seeded-session"],
                    "limitations": ["none"],
                },
            },
            workstream_id=legacy_design_workstream_id,
        )
        assert normalized_brief["schema_version"] == 2
        assert normalized_brief["affected_surfaces"][0]["surface_id"] == "legacy-home"
        assert normalized_brief["legacy_extra"]["preserve"] is True
        _write_json_file(
            Path(legacy_design_paths["design_handoff"]),
            {
                "schema_version": 1,
                "workspace_path": str(legacy_design_workspace.resolve()),
                "workstream_id": legacy_design_workstream_id,
                "status": "ready",
                "platform": "web",
                "layout_system": ["legacy-grid"],
                "verification_hooks": ["route:/legacy"],
                "legacy_notes": {"preserve": True},
            },
        )
        legacy_handoff = read_design_handoff(legacy_design_workspace, workstream_id=legacy_design_workstream_id)
        assert legacy_handoff["schema_version"] == 2
        assert legacy_handoff["layout_system"] == ["legacy-grid"]
        assert legacy_handoff["legacy_notes"]["preserve"] is True
        normalized_handoff = write_design_handoff(
            legacy_design_workspace,
            {
                "status": "ready",
                "platform": "web",
                "layout_system": ["legacy-grid"],
                "component_inventory": ["hero", "modal"],
                "verification_hooks": ["route:/legacy", "viewport:1440x900"],
                "affected_surfaces": normalized_brief["affected_surfaces"],
                "user_flows": normalized_brief["user_flows"],
                "state_coverage": normalized_brief["state_coverage"],
                "ux_rationale": normalized_brief["ux_rationale"],
                "critical_actions": normalized_brief["critical_actions"],
                "testability_guidance": normalized_brief["testability_guidance"],
            },
            workstream_id=legacy_design_workstream_id,
        )
        assert normalized_handoff["schema_version"] == 2
        assert normalized_handoff["critical_actions"][0]["action_id"] == "legacy-hero-cta"
        assert normalized_handoff["legacy_notes"]["preserve"] is True
        progress("workspace init, stage planning, host setup, and design-state persistence")

        updated = read_stage_register(workspace)
        updated["stages"][0]["status"] = "completed"
        updated["stages"][0]["completed_at"] = "2026-03-30T00:00:00Z"
        updated["current_stage"] = updated["stages"][1]["id"]
        updated["stage_status"] = "planned"
        updated["current_slice"] = updated["stages"][1]["canonical_execution_slices"][0]
        updated["remaining_slices"] = updated["stages"][1]["canonical_execution_slices"][1:]
        updated["last_completed_stage"] = "scope-lock"
        write_stage_register(workspace, updated, confirmed_stage_plan_edit=False)

        immutable = read_stage_register(workspace)
        immutable["stages"][0]["title"] = "Changed"
        try:
            write_stage_register(workspace, immutable, confirmed_stage_plan_edit=True)
        except ValueError as exc:
            assert "Completed stage cannot be modified" in str(exc)
        else:
            raise AssertionError("Completed stage mutation should have failed")

        draft_change = read_stage_register(workspace)
        draft_change["stages"][1]["title"] = "Changed Future Stage"
        try:
            write_stage_register(workspace, draft_change, confirmed_stage_plan_edit=False)
        except ValueError as exc:
            assert "require explicit confirmation" in str(exc)
        else:
            raise AssertionError("Unconfirmed stage definition mutation should have failed")
        write_stage_register(workspace, draft_change, confirmed_stage_plan_edit=True)

        baseline_target = workspace / "tests" / "visual" / "baselines" / "web-home.txt"
        baseline_target.parent.mkdir(parents=True, exist_ok=True)
        baseline_target.write_text("previous baseline")
        broken_layout_root = workspace / "browser-layout-audit" / "broken"
        broken_layout_root.mkdir(parents=True, exist_ok=True)
        broken_layout_root.joinpath("index.html").write_text(
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
            "    [data-testid='primary-panel'], [data-testid='secondary-panel'] {\n"
            "      box-sizing: border-box; height: 150px; padding: 16px; border: 1px solid #111;\n"
            "    }\n"
            "    [data-testid='primary-panel'] { width: 220px; background: #ffffff; }\n"
            "    [data-testid='secondary-panel'] { width: 220px; margin-left: -96px; background: rgba(180, 52, 35, 0.82); color: #fff; }\n"
            "    [data-testid='layout-action'] { display: inline-flex; align-items: center; min-height: 44px; margin-top: 56px; padding: 12px 18px; }\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            "  <main data-testid=\"layout-shell\">\n"
            "    <div class=\"content-grid\" data-testid=\"layout-row\">\n"
            "      <section data-testid=\"primary-panel\">\n"
            "        Primary panel\n"
            "        <button data-testid=\"layout-action\">Ship</button>\n"
            "      </section>\n"
            "      <aside data-testid=\"secondary-panel\">Secondary panel overlaps the primary content.</aside>\n"
            "    </div>\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n"
        )
        warning_layout_root = workspace / "browser-layout-audit" / "warning"
        warning_layout_root.mkdir(parents=True, exist_ok=True)
        warning_layout_root.joinpath("index.html").write_text(
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            "<head>\n"
            "  <meta charset=\"utf-8\">\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "  <title>Warning Layout</title>\n"
            "  <style>\n"
            "    body { margin: 0; font: 16px/1.4 sans-serif; background: #f3f1ea; color: #181511; }\n"
            "    [data-testid='layout-shell'] { padding: 12px; }\n"
            "    [data-testid='layout-stack'] {\n"
            "      box-sizing: border-box; display: flex; flex-direction: column; width: 320px; padding: 24px 6px 18px 24px;\n"
            "      border: 1px solid #111; background: #fbf8ef;\n"
            "    }\n"
            "    [data-testid='stack-card'] {\n"
            "      box-sizing: border-box; width: 240px; min-height: 72px; padding: 16px; border: 1px solid #111; background: #fffdfa;\n"
            "    }\n"
            "    [data-testid='stack-card'] + [data-testid='stack-card'] { margin-top: 12px; }\n"
            "    [data-testid='stack-card'].delayed { margin-top: 38px; }\n"
            "    [data-testid='warning-cta'] {\n"
            "      align-self: flex-start; margin-top: 18px; padding: 6px 10px; border: 1px solid #111; background: #181511; color: #fffdfa;\n"
            "    }\n"
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
        fixed_layout_root = workspace / "browser-layout-audit" / "fixed"
        fixed_layout_root.mkdir(parents=True, exist_ok=True)
        fixed_layout_root.joinpath("index.html").write_text(
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            "<head>\n"
            "  <meta charset=\"utf-8\">\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "  <title>Fixed Layout</title>\n"
            "  <style>\n"
            "    body { margin: 0; font: 16px/1.4 sans-serif; background: #f3f1ea; }\n"
            "    [data-testid='layout-shell'] { padding: 12px; }\n"
            "    [data-testid='layout-row'] { display: flex; gap: 12px; width: 336px; align-items: flex-start; flex-wrap: wrap; }\n"
            "    [data-testid='primary-panel'], [data-testid='secondary-panel'] {\n"
            "      box-sizing: border-box; min-width: 0; flex: 1 1 160px; height: 150px; padding: 16px; border: 1px solid #111;\n"
            "    }\n"
            "    [data-testid='primary-panel'] { background: #ffffff; }\n"
            "    [data-testid='secondary-panel'] { background: #dfe8db; }\n"
            "    [data-testid='layout-action'] { display: inline-flex; align-items: center; min-height: 44px; margin-top: 56px; padding: 12px 18px; }\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            "  <main data-testid=\"layout-shell\">\n"
            "    <div class=\"content-grid\" data-testid=\"layout-row\">\n"
            "      <section data-testid=\"primary-panel\">\n"
            "        Primary panel\n"
            "        <button data-testid=\"layout-action\">Ship</button>\n"
            "      </section>\n"
            "      <aside data-testid=\"secondary-panel\">Secondary panel stays in its own lane.</aside>\n"
            "    </div>\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n"
        )
        broken_layout_port = _reserve_local_port()
        warning_layout_port = _reserve_local_port()
        fixed_layout_port = _reserve_local_port()

        def rect_payload(left: int, top: int, right: int, bottom: int) -> dict[str, int | bool]:
            return {
                "present": True,
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "width": right - left,
                "height": bottom - top,
            }

        def semantic_check(check_id: str, status: str = "passed", diagnostics: dict[str, object] | None = None) -> dict[str, object]:
            return {
                "id": check_id,
                "status": status,
                "diagnostics": diagnostics or {},
            }

        def semantic_target(target_id: str, checks: list[dict[str, object]], status: str = "passed") -> dict[str, object]:
            return {
                "target_id": target_id,
                "status": status,
                "checks": checks,
            }

        def semantic_report(runner: str, targets: list[dict[str, object]]) -> dict[str, object]:
            summary_status = "passed" if all(target["status"] == "passed" for target in targets) else "failed"
            return {
                "schema_version": 2,
                "runner": runner,
                "helper_bundle_version": "0.8.0",
                "summary": {"status": summary_status},
                "targets": targets,
            }

        mobile_root_bounds = rect_payload(0, 0, 360, 720)
        expo_home_report = semantic_report(
            "detox-visual",
            [
                semantic_target(
                    "home-screen",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(12, 24, 348, 696),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {
                                    "width": 336,
                                    "height": 672,
                                    "enabled": True,
                                    "selected": False,
                                    "textLength": 18,
                                },
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(12, 24, 348, 696),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                        semantic_check("text_overflow", diagnostics={"text_overflow": {"truncated": False}}),
                    ],
                )
            ],
        )
        expo_layout_overlap_report = semantic_report(
            "detox-visual",
            [
                semantic_target(
                    "home-card",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(16, 24, 176, 228),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 160, "height": 204, "background": "#ffffff"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(16, 24, 176, 228),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
                semantic_target(
                    "home-overlay",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(132, 80, 316, 284),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 184, "height": 204, "background": "#f75f49"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(132, 80, 316, 284),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
            ],
        )
        expo_gutter_warning_report = semantic_report(
            "detox-visual",
            [
                semantic_target(
                    "home-left-card",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(24, 24, 192, 228),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 168, "height": 204, "background": "#ffffff"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(24, 24, 192, 228),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
                semantic_target(
                    "home-right-card",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(208, 24, 354, 228),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 146, "height": 204, "background": "#dfe8db"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(208, 24, 354, 228),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
            ],
        )
        expo_spacing_warning_report = semantic_report(
            "detox-visual",
            [
                semantic_target(
                    "stack-card-a",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(16, 24, 344, 156),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 328, "height": 132, "background": "#ffffff"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(16, 24, 344, 156),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
                semantic_target(
                    "stack-card-b",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(16, 168, 344, 300),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 328, "height": 132, "background": "#f7f3e9"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(16, 168, 344, 300),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
                semantic_target(
                    "stack-card-c",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(16, 344, 344, 476),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 328, "height": 132, "background": "#dfe8db"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(16, 344, 344, 476),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
                semantic_target(
                    "tiny-cta",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(16, 520, 44, 548),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 28, "height": 28, "background": "#101820"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(16, 520, 44, 548),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
            ],
        )
        android_style_mismatch_report = semantic_report(
            "android-compose-screenshot",
            [
                semantic_target(
                    "android-card",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(16, 24, 344, 212),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            status="failed",
                            diagnostics={
                                "style_tokens": {"width": 328, "height": 188, "background": "#ffffff"},
                                "mismatches": [
                                    {"field": "background", "expected": "#101820", "actual": "#ffffff"}
                                ],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(16, 24, 344, 212),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                )
            ],
        )
        android_fixed_report = semantic_report(
            "android-compose-screenshot",
            [
                semantic_target(
                    "android-left-card",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(16, 24, 168, 220),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 152, "height": 196, "background": "#f7f4ed"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(16, 24, 168, 220),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
                semantic_target(
                    "android-right-card",
                    [
                        semantic_check("presence_uniqueness"),
                        semantic_check("visibility"),
                        semantic_check(
                            "overflow_clipping",
                            diagnostics={
                                "clipping": {
                                    "clipped": False,
                                    "target_bounds": rect_payload(184, 24, 344, 220),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check(
                            "computed_styles",
                            diagnostics={
                                "style_tokens": {"width": 160, "height": 196, "background": "#dde9df"},
                                "mismatches": [],
                            },
                        ),
                        semantic_check("interaction_states"),
                        semantic_check(
                            "layout_relations",
                            diagnostics={
                                "layout": {
                                    "bounds_in_root": rect_payload(184, 24, 344, 220),
                                    "root_bounds": mobile_root_bounds,
                                }
                            },
                        ),
                        semantic_check("scroll_reachability"),
                        semantic_check("occlusion", diagnostics={"metadata": {"occluded": False}}),
                    ],
                ),
            ],
        )
        verification_recipes = write_verification_recipes(
            workspace,
            {
                "cases": [
                    {
                        "id": "web-home",
                        "title": "Web home deterministic check",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "tags": ["hero", "web"],
                        "feature_ids": ["marketing-home"],
                        "surface_ids": ["dashboard-home"],
                        "routes_or_screens": ["/"],
                        "changed_path_globs": ["apps/web/**", "plugins/agentiux-dev/dashboard/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import os, pathlib, sys, time; "
                                "import json; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                "(artifact_dir / 'web-home.txt').write_text('web home ok\\n'); "
                                "(artifact_dir / 'web-home-semantic.json').write_text(json.dumps({"
                                "'schema_version': 2, "
                                "'runner': 'playwright-visual', "
                                "'helper_bundle_version': '0.8.0', "
                                "'summary': {'status': 'passed'}, "
                                "'targets': [{"
                                "'target_id': 'home-main', "
                                "'status': 'passed', "
                                "'checks': ["
                                "{'id': 'presence_uniqueness', 'status': 'passed'}, "
                                "{'id': 'visibility', 'status': 'passed'}, "
                                "{'id': 'overflow_clipping', 'status': 'passed'}, "
                                "{'id': 'computed_styles', 'status': 'passed'}, "
                                "{'id': 'interaction_states', 'status': 'passed'}, "
                                "{'id': 'scroll_reachability', 'status': 'passed'}, "
                                "{'id': 'occlusion', 'status': 'passed'}"
                                "]"
                                "}]"
                                "})); "
                                "print('web-home start'); sys.stdout.flush(); "
                                "time.sleep(0.25); "
                                "print('web-home done')"
                            ),
                        ],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "1440x1024"},
                        "locale": "en-US",
                        "timezone": "UTC",
                        "color_scheme": "light",
                        "freeze_clock": True,
                        "masks": [".clock", ".live-counter"],
                        "artifact_expectations": ["screenshots", "diffs"],
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-home-semantic.json",
                            "required_checks": [
                                "presence_uniqueness",
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "interaction_states",
                                "scroll_reachability",
                                "occlusion",
                            ],
                            "targets": [
                                {
                                    "target_id": "home-main",
                                    "locator": {"kind": "role", "value": "main"},
                                    "interactions": ["hover", "focus"],
                                }
                            ],
                            "auto_scan": True,
                            "heuristics": [
                                "interactive_visibility_scan",
                                "interactive_overflow_scan",
                                "interactive_occlusion_scan",
                            ],
                            "artifacts": {
                                "target_screenshots": True,
                                "debug_snapshots": False,
                            },
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "baseline": {"policy": "project-owned", "source_path": str(baseline_target.relative_to(workspace))},
                    },
                    {
                        "id": "expo-home",
                        "title": "Expo home deterministic check",
                        "surface_type": "mobile",
                        "runner": "detox-visual",
                        "tags": ["mobile", "expo"],
                        "feature_ids": ["mobile-home"],
                        "surface_ids": ["expo-home"],
                        "routes_or_screens": ["home"],
                        "changed_path_globs": ["apps/mobile/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import os, pathlib, sys, time; "
                                "import json; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                "(artifact_dir / 'expo-home.txt').write_text('expo home ok\\n'); "
                                f"(artifact_dir / 'expo-home-semantic.json').write_text({repr(json.dumps(expo_home_report))}); "
                                "print('expo-home start'); sys.stdout.flush(); "
                                "time.sleep(1.6); "
                                "print('expo-home done')"
                            ),
                        ],
                        "target": {"screen_id": "home"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "locale": "en-US",
                        "timezone": "UTC",
                        "color_scheme": "light",
                        "freeze_clock": True,
                        "masks": ["LiveClock", "RemoteCounter"],
                        "artifact_expectations": ["screenshots", "diffs"],
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "expo-home-semantic.json",
                            "required_checks": [
                                "presence_uniqueness",
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "interaction_states",
                                "layout_relations",
                                "scroll_reachability",
                                "occlusion",
                            ],
                            "targets": [
                                {
                                    "target_id": "home-screen",
                                    "locator": {"kind": "test_id", "value": "home-screen"},
                                    "scroll_container_locator": {"kind": "test_id", "value": "home-scroll"},
                                    "interactions": ["tap"],
                                }
                            ],
                            "auto_scan": True,
                            "heuristics": [
                                "interactive_visibility_scan",
                                "interactive_overflow_scan",
                            ],
                            "artifacts": {
                                "target_screenshots": True,
                                "debug_snapshots": False,
                            },
                        },
                        "native_layout_audit": {
                            "enabled": True,
                            "report_path": "expo-home-native-layout-audit.json",
                            "required_checks": [
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "layout_relations",
                                "occlusion",
                            ],
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "host_requirements": ["python", "adb"],
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/expo-home.txt"},
                        "android_logcat": {
                            "enabled": True,
                            "package": "com.example.demo",
                            "pid_mode": "package",
                            "clear_on_start": True,
                            "buffers": ["main", "crash"],
                            "filter_specs": ["*:I"],
                            "tail_lines_on_failure": 20,
                        },
                    },
                    {
                        "id": "expo-native-layout-overlap",
                        "title": "Expo native layout overlap detection",
                        "surface_type": "mobile",
                        "runner": "detox-visual",
                        "tags": ["mobile", "expo", "layout"],
                        "feature_ids": ["native-layout-audit"],
                        "surface_ids": ["expo-layout-overlap"],
                        "routes_or_screens": ["layout-overlap"],
                        "changed_path_globs": ["apps/mobile/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import os, pathlib; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                f"(artifact_dir / 'expo-native-layout-overlap.json').write_text({repr(json.dumps(expo_layout_overlap_report))}); "
                                "print('expo-native-layout-overlap done')"
                            ),
                        ],
                        "target": {"screen_id": "layout-overlap"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "expo-native-layout-overlap.json",
                            "required_checks": [
                                "presence_uniqueness",
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "interaction_states",
                                "layout_relations",
                                "scroll_reachability",
                                "occlusion",
                            ],
                            "targets": [
                                {
                                    "target_id": "home-card",
                                    "locator": {"kind": "test_id", "value": "home-card"},
                                },
                                {
                                    "target_id": "home-overlay",
                                    "locator": {"kind": "test_id", "value": "home-overlay"},
                                },
                            ],
                        },
                        "native_layout_audit": {
                            "enabled": True,
                            "report_path": "expo-native-layout-overlap-audit.json",
                            "required_checks": [
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "layout_relations",
                                "occlusion",
                            ],
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/expo-layout-overlap.txt"},
                    },
                    {
                        "id": "expo-native-gutter-warning",
                        "title": "Expo native gutter imbalance warning",
                        "surface_type": "mobile",
                        "runner": "detox-visual",
                        "tags": ["mobile", "expo", "layout", "warning"],
                        "feature_ids": ["native-layout-audit"],
                        "surface_ids": ["expo-gutter-warning"],
                        "routes_or_screens": ["layout-gutter-warning"],
                        "changed_path_globs": ["apps/mobile/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import os, pathlib; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                f"(artifact_dir / 'expo-native-gutter-warning.json').write_text({repr(json.dumps(expo_gutter_warning_report))}); "
                                "print('expo-native-gutter-warning done')"
                            ),
                        ],
                        "target": {"screen_id": "layout-gutter-warning"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "expo-native-gutter-warning.json",
                            "required_checks": [
                                "presence_uniqueness",
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "interaction_states",
                                "layout_relations",
                                "scroll_reachability",
                                "occlusion",
                            ],
                            "targets": [
                                {
                                    "target_id": "home-left-card",
                                    "locator": {"kind": "test_id", "value": "home-left-card"},
                                },
                                {
                                    "target_id": "home-right-card",
                                    "locator": {"kind": "test_id", "value": "home-right-card"},
                                },
                            ],
                        },
                        "native_layout_audit": {
                            "enabled": True,
                            "report_path": "expo-native-gutter-warning-audit.json",
                            "required_checks": [
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "layout_relations",
                                "occlusion",
                            ],
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/expo-gutter-warning.txt"},
                    },
                    {
                        "id": "expo-native-spacing-warning",
                        "title": "Expo native spacing and tap-target warnings",
                        "surface_type": "mobile",
                        "runner": "detox-visual",
                        "tags": ["mobile", "expo", "layout", "warning"],
                        "feature_ids": ["native-layout-audit"],
                        "surface_ids": ["expo-spacing-warning"],
                        "routes_or_screens": ["layout-spacing-warning"],
                        "changed_path_globs": ["apps/mobile/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import os, pathlib; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                f"(artifact_dir / 'expo-native-spacing-warning.json').write_text({repr(json.dumps(expo_spacing_warning_report))}); "
                                "print('expo-native-spacing-warning done')"
                            ),
                        ],
                        "target": {"screen_id": "layout-spacing-warning"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "expo-native-spacing-warning.json",
                            "required_checks": [
                                "presence_uniqueness",
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "interaction_states",
                                "layout_relations",
                                "scroll_reachability",
                                "occlusion",
                            ],
                            "targets": [
                                {
                                    "target_id": "stack-card-a",
                                    "locator": {"kind": "test_id", "value": "stack-card-a"},
                                },
                                {
                                    "target_id": "stack-card-b",
                                    "locator": {"kind": "test_id", "value": "stack-card-b"},
                                },
                                {
                                    "target_id": "stack-card-c",
                                    "locator": {"kind": "test_id", "value": "stack-card-c"},
                                },
                                {
                                    "target_id": "tiny-cta",
                                    "locator": {"kind": "test_id", "value": "tiny-cta"},
                                    "interactions": ["tap"],
                                },
                            ],
                        },
                        "native_layout_audit": {
                            "enabled": True,
                            "report_path": "expo-native-spacing-warning-audit.json",
                            "required_checks": [
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "layout_relations",
                                "occlusion",
                            ],
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/expo-spacing-warning.txt"},
                    },
                    {
                        "id": "android-native-style-mismatch",
                        "title": "Android native style mismatch detection",
                        "surface_type": "android",
                        "runner": "android-compose-screenshot",
                        "tags": ["android", "layout", "style"],
                        "feature_ids": ["native-layout-audit"],
                        "surface_ids": ["android-style-mismatch"],
                        "routes_or_screens": ["android-style"],
                        "changed_path_globs": ["apps/mobile/android/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import os, pathlib; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                f"(artifact_dir / 'android-native-style-mismatch.json').write_text({repr(json.dumps(android_style_mismatch_report))}); "
                                "print('android-native-style-mismatch done')"
                            ),
                        ],
                        "target": {"screen_id": "android-style"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "android-native-style-mismatch.json",
                            "required_checks": [
                                "presence_uniqueness",
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "interaction_states",
                                "layout_relations",
                                "scroll_reachability",
                                "occlusion",
                            ],
                            "targets": [
                                {
                                    "target_id": "android-card",
                                    "locator": {"kind": "semantics_tag", "value": "android-card"},
                                }
                            ],
                        },
                        "native_layout_audit": {
                            "enabled": True,
                            "report_path": "android-native-style-mismatch-audit.json",
                            "required_checks": [
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "layout_relations",
                                "occlusion",
                            ],
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/android-style-mismatch.txt"},
                    },
                    {
                        "id": "android-native-layout-fixed",
                        "title": "Android native layout fixed state",
                        "surface_type": "android",
                        "runner": "android-compose-screenshot",
                        "tags": ["android", "layout"],
                        "feature_ids": ["native-layout-audit"],
                        "surface_ids": ["android-layout-fixed"],
                        "routes_or_screens": ["android-fixed"],
                        "changed_path_globs": ["apps/mobile/android/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import os, pathlib; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                f"(artifact_dir / 'android-native-layout-fixed.json').write_text({repr(json.dumps(android_fixed_report))}); "
                                "print('android-native-layout-fixed done')"
                            ),
                        ],
                        "target": {"screen_id": "android-fixed"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "android-native-layout-fixed.json",
                            "required_checks": [
                                "presence_uniqueness",
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "interaction_states",
                                "layout_relations",
                                "scroll_reachability",
                                "occlusion",
                            ],
                            "targets": [
                                {
                                    "target_id": "android-left-card",
                                    "locator": {"kind": "semantics_tag", "value": "android-left-card"},
                                },
                                {
                                    "target_id": "android-right-card",
                                    "locator": {"kind": "semantics_tag", "value": "android-right-card"},
                                },
                            ],
                        },
                        "native_layout_audit": {
                            "enabled": True,
                            "report_path": "android-native-layout-fixed-audit.json",
                            "required_checks": [
                                "visibility",
                                "overflow_clipping",
                                "computed_styles",
                                "layout_relations",
                                "occlusion",
                            ],
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "baseline": {"policy": "project-owned", "source_path": "tests/visual/baselines/android-layout-fixed.txt"},
                    },
                    {
                        "id": "web-semantic-missing",
                        "title": "Web semantic report required",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "tags": ["web", "semantic"],
                        "feature_ids": ["semantic-coverage"],
                        "surface_ids": ["semantic-missing"],
                        "routes_or_screens": ["/semantic"],
                        "changed_path_globs": ["apps/web/semantic/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import os, pathlib, sys; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                "(artifact_dir / 'web-semantic-missing.txt').write_text('semantic missing\\n'); "
                                "print('web-semantic-missing done')"
                            ),
                        ],
                        "target": {"route": "/semantic"},
                        "device_or_viewport": {"viewport": "1440x1024"},
                        "locale": "en-US",
                        "timezone": "UTC",
                        "color_scheme": "light",
                        "freeze_clock": True,
                        "masks": [],
                        "artifact_expectations": ["screenshots", "diffs"],
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-semantic-missing.json",
                            "required_checks": [
                                "visibility",
                                "computed_styles",
                            ],
                            "targets": [
                                {
                                    "target_id": "semantic-main",
                                    "locator": {"kind": "selector", "value": "[data-testid='semantic-main']"},
                                }
                            ],
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "baseline": {"policy": "project-owned", "source_path": str(baseline_target.relative_to(workspace))},
                    },
                    {
                        "id": "web-optional-semantic-warning",
                        "title": "Web semantic optional warning",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "tags": ["web", "semantic"],
                        "feature_ids": ["semantic-optional"],
                        "surface_ids": ["semantic-optional"],
                        "routes_or_screens": ["/semantic-optional"],
                        "changed_path_globs": ["apps/web/semantic/**"],
                        "host_requirements": ["python"],
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "import json, os, pathlib; "
                                "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                                "artifact_dir.mkdir(parents=True, exist_ok=True); "
                                "(artifact_dir / 'web-optional-semantic-warning.json').write_text(json.dumps({"
                                "'schema_version': 2, "
                                "'runner': 'playwright-visual', "
                                "'helper_bundle_version': '0.8.0', "
                                "'summary': {'status': 'failed', 'message': 'optional layout warning'}, "
                                "'targets': [{"
                                "'target_id': 'optional-main', "
                                "'status': 'failed', "
                                "'checks': ["
                                "{'id': 'visibility', 'status': 'passed'}, "
                                "{'id': 'layout_relations', 'status': 'failed'}"
                                "]"
                                "}]"
                                "})); "
                                "print('web-optional-semantic-warning done')"
                            ),
                        ],
                        "target": {"route": "/semantic-optional"},
                        "device_or_viewport": {"viewport": "1440x1024"},
                        "locale": "en-US",
                        "timezone": "UTC",
                        "color_scheme": "light",
                        "freeze_clock": True,
                        "masks": [],
                        "artifact_expectations": ["screenshots", "diffs"],
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-optional-semantic-warning.json",
                            "required_checks": ["visibility"],
                            "targets": [
                                {
                                    "target_id": "optional-main",
                                    "locator": {"kind": "role", "value": "main"},
                                }
                            ],
                        },
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                        "baseline": {"policy": "project-owned", "source_path": str(baseline_target.relative_to(workspace))},
                    },
                    {
                        "id": "browser-layout-overlap",
                        "title": "Browser layout overlap detection",
                        "surface_type": "web",
                        "runner": "browser-layout-audit",
                        "tags": ["web", "layout"],
                        "feature_ids": ["browser-layout-audit"],
                        "surface_ids": ["browser-layout-broken"],
                        "routes_or_screens": ["/"],
                        "changed_path_globs": ["browser-layout-audit/broken/**"],
                        "host_requirements": ["python", "web", "browser-runtime"],
                        "cwd": str(broken_layout_root.relative_to(workspace)),
                        "argv": [sys.executable, "-m", "http.server", str(broken_layout_port), "--bind", "127.0.0.1"],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "360x280"},
                        "readiness_probe": {
                            "type": "http",
                            "url": f"http://127.0.0.1:{broken_layout_port}/",
                            "timeout_seconds": 10,
                        },
                        "browser_layout_audit": {
                            "base_url": f"http://127.0.0.1:{broken_layout_port}/",
                            "report_path": "browser-layout-overlap.json",
                            "screenshot_path": "browser-layout-overlap.png",
                            "wait_timeout_ms": 8000,
                            "settle_ms": 300,
                        },
                        "artifact_expectations": ["screenshots", "reports"],
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                    },
                    {
                        "id": "browser-layout-warning",
                        "title": "Browser layout warning detection",
                        "surface_type": "web",
                        "runner": "browser-layout-audit",
                        "tags": ["web", "layout", "warning"],
                        "feature_ids": ["browser-layout-audit"],
                        "surface_ids": ["browser-layout-warning"],
                        "routes_or_screens": ["/"],
                        "changed_path_globs": ["browser-layout-audit/warning/**"],
                        "host_requirements": ["python", "web", "browser-runtime"],
                        "cwd": str(warning_layout_root.relative_to(workspace)),
                        "argv": [sys.executable, "-m", "http.server", str(warning_layout_port), "--bind", "127.0.0.1"],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "390x420"},
                        "readiness_probe": {
                            "type": "http",
                            "url": f"http://127.0.0.1:{warning_layout_port}/",
                            "timeout_seconds": 10,
                        },
                        "browser_layout_audit": {
                            "base_url": f"http://127.0.0.1:{warning_layout_port}/",
                            "report_path": "browser-layout-warning.json",
                            "screenshot_path": "browser-layout-warning.png",
                            "wait_timeout_ms": 8000,
                            "settle_ms": 300,
                        },
                        "artifact_expectations": ["screenshots", "reports"],
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                    },
                    {
                        "id": "browser-layout-fixed",
                        "title": "Browser layout fixed state",
                        "surface_type": "web",
                        "runner": "browser-layout-audit",
                        "tags": ["web", "layout"],
                        "feature_ids": ["browser-layout-audit"],
                        "surface_ids": ["browser-layout-fixed"],
                        "routes_or_screens": ["/"],
                        "changed_path_globs": ["browser-layout-audit/fixed/**"],
                        "host_requirements": ["python", "web", "browser-runtime"],
                        "cwd": str(fixed_layout_root.relative_to(workspace)),
                        "argv": [sys.executable, "-m", "http.server", str(fixed_layout_port), "--bind", "127.0.0.1"],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "360x280"},
                        "readiness_probe": {
                            "type": "http",
                            "url": f"http://127.0.0.1:{fixed_layout_port}/",
                            "timeout_seconds": 10,
                        },
                        "browser_layout_audit": {
                            "base_url": f"http://127.0.0.1:{fixed_layout_port}/",
                            "report_path": "browser-layout-fixed.json",
                            "screenshot_path": "browser-layout-fixed.png",
                            "wait_timeout_ms": 8000,
                            "settle_ms": 300,
                        },
                        "artifact_expectations": ["screenshots", "reports"],
                        "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                    },
                ],
                "suites": [
                    {
                        "id": "smoke",
                        "title": "Smoke Suite",
                        "case_ids": ["web-home"],
                    },
                    {
                        "id": "full",
                        "title": "Full Suite",
                        "case_ids": ["web-home", "expo-home"],
                    },
                    {
                        "id": "browser-layout-fixed",
                        "title": "Browser Layout Fixed",
                        "case_ids": ["browser-layout-fixed"],
                    },
                ],
            },
        )
        assert verification_recipes["schema_version"] == 3
        assert verification_recipes["cases"][0]["runner"] == "playwright-visual"
        assert read_verification_recipes(workspace, workstream_id=verification_workstream_id)["suites"][1]["id"] == "full"

        targeted_task = create_task(
            workspace,
            title="Verify dashboard home only",
            objective="Run targeted verification for the dashboard home surface.",
            verification_selectors={"surface_ids": ["dashboard-home"]},
            verification_mode_default="targeted",
        )
        selection = resolve_verification_selection(workspace)
        assert selection["selection_status"] == "resolved"
        assert selection["source"] == f"task:{targeted_task['created_task_id']}"
        assert selection["requested_mode"] == "targeted"
        assert selection["requested_mode_source"] == "task_default"
        assert selection["resolved_mode"] == "targeted"
        assert selection["selected_suite"] is None
        assert [case["case_id"] for case in selection["selected_cases"]] == ["web-home"]
        assert selection["heuristic_suggestions"] == []
        assert selection["baseline_sources"] == [str(baseline_target.resolve())]
        assert selection["helper_guidance"]["needs_semantic_helpers"] is True
        assert selection["helper_guidance"]["materialization"]["status"] == "not_synced"
        assert any("sync verification helpers" in item.lower() for item in selection["helper_guidance"]["next_actions"])
        close_task(workspace, task_id=targeted_task["created_task_id"], verification_summary={"status": "completed"})

        unresolved_task = create_task(
            workspace,
            title="Review verification heuristics only",
            objective="Inspect changed paths without explicit selectors.",
        )
        unresolved_selection = resolve_verification_selection(workspace, changed_paths=["apps/web/routes/home.tsx"])
        assert unresolved_selection["selection_status"] == "unresolved"
        assert unresolved_selection["targeted"] is True
        assert unresolved_selection["source"] == f"task:{unresolved_task['created_task_id']}"
        assert unresolved_selection["requested_mode_source"] == "task_default"
        assert unresolved_selection["selected_cases"] == []
        assert [case["case_id"] for case in unresolved_selection["heuristic_suggestions"]] == ["web-home"]
        assert "Heuristic suggestions are available" in unresolved_selection["reason"]
        close_task(workspace, task_id=unresolved_task["created_task_id"], verification_summary={"status": "completed"})

        workstream_default_selection = resolve_verification_selection(workspace)
        assert workstream_default_selection["selection_status"] == "unresolved"
        assert workstream_default_selection["source"] == f"workstream:{primary_workstream['created_workstream_id']}"
        assert workstream_default_selection["requested_mode_source"] == "workstream_default"
        assert workstream_default_selection["selected_cases"] == []

        stage_level_register = read_stage_register(workspace)
        for stage in stage_level_register["stages"]:
            if stage["id"] == stage_level_register["current_stage"]:
                stage["verification_selectors"] = {"surface_ids": ["expo-home"]}
                stage["verification_policy"] = {"default_mode": "targeted"}
        write_stage_register(workspace, stage_level_register, confirmed_stage_plan_edit=True)
        stage_default_selection = resolve_verification_selection(workspace)
        assert stage_default_selection["selection_status"] == "resolved"
        assert stage_default_selection["source"] == f"stage:{stage_level_register['current_stage']}"
        assert stage_default_selection["requested_mode_source"] == "stage_default"
        assert [case["case_id"] for case in stage_default_selection["selected_cases"]] == ["expo-home"]

        explicit_request_selection = resolve_verification_selection(workspace, request_mode="full")
        assert explicit_request_selection["selection_status"] == "resolved"
        assert explicit_request_selection["source"] == "explicit_request"
        assert explicit_request_selection["requested_mode_source"] == "explicit_request"
        assert explicit_request_selection["resolved_mode"] == "full"
        assert explicit_request_selection["selected_suite"]["id"] == "full"

        stage_closeout_register = read_stage_register(workspace)
        stage_closeout_register["stage_status"] = "ready_for_closeout"
        for stage in stage_closeout_register["stages"]:
            if stage["id"] == stage_closeout_register["current_stage"]:
                stage["verification_policy"] = {
                    "default_mode": "targeted",
                    "closeout_default_mode": "full",
                }
        write_stage_register(workspace, stage_closeout_register, confirmed_stage_plan_edit=True)
        stage_closeout_selection = resolve_verification_selection(workspace)
        assert stage_closeout_selection["selection_status"] == "resolved"
        assert stage_closeout_selection["source"] == f"stage:{stage_closeout_register['current_stage']}"
        assert stage_closeout_selection["requested_mode_source"] == "stage_closeout_policy"
        assert stage_closeout_selection["selected_suite"]["id"] == "full"

        heuristic_register = read_stage_register(workspace)
        heuristic_register["stage_status"] = "planned"
        for stage in heuristic_register["stages"]:
            if stage["id"] == heuristic_register["current_stage"]:
                stage["verification_selectors"] = {}
                stage["verification_policy"] = {}
        write_stage_register(workspace, heuristic_register, confirmed_stage_plan_edit=True)
        heuristic_selection = resolve_verification_selection(
            workspace,
            changed_paths=["apps/web/routes/home.tsx"],
            confirm_heuristics=True,
        )
        assert heuristic_selection["selection_status"] == "resolved"
        assert heuristic_selection["source"] == "confirmed_heuristic_suggestion"
        assert heuristic_selection["requested_mode_source"] == "workstream_default"
        assert [case["case_id"] for case in heuristic_selection["selected_cases"]] == ["web-home"]

        helper_catalog_before_sync = show_verification_helper_catalog(workspace)
        assert helper_catalog_before_sync["version_status"] == "not_synced"
        assert "playwright-visual" in helper_catalog_before_sync["available_runners"]
        legacy_helper_root = workspace / ".agentiux" / "verification-helpers" / "0.7.0"
        legacy_helper_root.mkdir(parents=True, exist_ok=True)
        legacy_catalog = show_verification_helper_catalog(workspace)
        assert legacy_catalog["version_status"] == "legacy_location"
        assert legacy_catalog["materialization"]["legacy_detected"] is True
        legacy_audit = audit_verification_coverage(workspace, workstream_id=verification_workstream_id)
        assert "verification-helper-bundle-legacy-location" in {gap["gap_id"] for gap in legacy_audit["gaps"]}
        helper_sync = sync_verification_helpers(workspace)
        assert helper_sync["status"] == "synced"
        assert helper_sync["removed_legacy_root"] is True
        assert helper_sync["materialization"]["status"] == "synced"
        assert helper_sync["file_count"] > 0
        assert helper_sync["destination_root"].endswith("/.verification/helpers")
        assert helper_sync["marker_path"].endswith("/.verification/helpers/bundle.json")
        assert helper_sync["import_snippets"]["playwright-visual"]["import_examples"]
        assert helper_sync["import_snippets"]["playwright-visual"]["relative_path"] == ".verification/helpers/playwright/index.js"
        assert helper_sync["import_snippets"]["detox-visual"]["relative_path"] == ".verification/helpers/detox/index.js"
        assert helper_sync["import_snippets"]["android-compose-screenshot"]["relative_path"] == ".verification/helpers/android-compose/SemanticChecks.kt"
        assert "/0.8.0/" not in "".join(helper_sync["import_snippets"]["playwright-visual"]["import_examples"])
        assert not (workspace / ".agentiux").exists()
        _assert_no_branded_strings_in_tree(Path(helper_sync["destination_root"]))
        helper_catalog_after_sync = show_verification_helper_catalog(workspace)
        assert helper_catalog_after_sync["version_status"] == "synced"
        assert helper_catalog_after_sync["materialization"]["synced"] is True
        assert helper_catalog_after_sync["runners"]["android-compose-screenshot"]["capability_matrix"]["entrypoint"] == "android-compose/SemanticChecks.kt"
        helper_catalog_cli = subprocess.run(
            python_script_command(
                plugin_root / "scripts" / "agentiux_dev_state.py",
                ["show-verification-helper-catalog", "--workspace", str(workspace)],
            ),
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        ).stdout
        assert "playwright-visual" in helper_catalog_cli
        validator_artifacts = temp_root / "semantic-validator"
        validator_artifacts.mkdir(exist_ok=True)
        validator_case = _semantic_validation_case()
        validator_report_path = validator_artifacts / "semantic-report.json"
        valid_report = {
            "schema_version": SEMANTIC_REPORT_SCHEMA_VERSION,
            "runner": "playwright-visual",
            "helper_bundle_version": helper_sync["bundle_version"],
            "summary": {"status": "passed"},
            "targets": [
                {
                    "target_id": "hero-main",
                    "status": "passed",
                    "checks": [
                        {"check_id": "visibility", "status": "passed"},
                        {"check_id": "computed_styles", "status": "passed"},
                    ],
                }
            ],
        }
        stale_schema_report = dict(valid_report)
        stale_schema_report["schema_version"] = SEMANTIC_REPORT_SCHEMA_VERSION - 1
        _write_semantic_report(validator_report_path, stale_schema_report)
        stale_schema_result = _validate_semantic_assertions({"artifacts_dir": validator_artifacts}, validator_case)
        assert stale_schema_result["status"] == "failed"
        assert stale_schema_result["reason"] == "invalid_report_schema"
        assert any("schema_version" in error for error in stale_schema_result["schema_errors"])

        missing_target_report = dict(valid_report)
        missing_target_report["targets"] = [
            {
                "target_id": "hero-secondary",
                "status": "passed",
                "checks": [
                    {"check_id": "visibility", "status": "passed"},
                    {"check_id": "computed_styles", "status": "passed"},
                ],
            }
        ]
        _write_semantic_report(validator_report_path, missing_target_report)
        missing_target_result = _validate_semantic_assertions({"artifacts_dir": validator_artifacts}, validator_case)
        assert missing_target_result["status"] == "failed"
        assert missing_target_result["reason"] == "missing_targets"
        assert missing_target_result["missing_targets"] == ["hero-main"]

        missing_check_report = dict(valid_report)
        missing_check_report["targets"] = [
            {
                "target_id": "hero-main",
                "status": "passed",
                "checks": [
                    {"check_id": "visibility", "status": "passed"},
                ],
            }
        ]
        _write_semantic_report(validator_report_path, missing_check_report)
        missing_check_result = _validate_semantic_assertions({"artifacts_dir": validator_artifacts}, validator_case)
        assert missing_check_result["status"] == "failed"
        assert missing_check_result["reason"] == "missing_checks"
        assert missing_check_result["missing_checks"] == ["hero-main/computed_styles"]

        mismatched_helper_report = dict(valid_report)
        mismatched_helper_report["helper_bundle_version"] = "0.0.0"
        _write_semantic_report(validator_report_path, mismatched_helper_report)
        mismatched_helper_result = _validate_semantic_assertions({"artifacts_dir": validator_artifacts}, validator_case)
        assert mismatched_helper_result["status"] == "failed"
        assert mismatched_helper_result["reason"] == "invalid_report_schema"
        assert any("helper version" in error for error in mismatched_helper_result["schema_errors"])

        partial_shape_report = dict(valid_report)
        partial_shape_report["targets"] = [
            {
                "target_id": "hero-main",
                "checks": [
                    {"check_id": "visibility"},
                ],
            }
        ]
        _write_semantic_report(validator_report_path, partial_shape_report)
        partial_shape_result = _validate_semantic_assertions({"artifacts_dir": validator_artifacts}, validator_case)
        assert partial_shape_result["status"] == "failed"
        assert partial_shape_result["reason"] == "invalid_report_schema"
        assert any("missing `status`" in error for error in partial_shape_result["schema_errors"])
        playwright_helper_uri = (Path(helper_sync["destination_root"]) / "playwright" / "index.js").resolve().as_uri()
        detox_helper_uri = (Path(helper_sync["destination_root"]) / "detox" / "index.js").resolve().as_uri()
        playwright_helper_report = temp_root / "playwright-helper-report.json"
        detox_helper_report = temp_root / "detox-helper-report.json"
        playwright_helper_script = temp_root / "playwright-helper-check.mjs"
        detox_helper_script = temp_root / "detox-helper-check.mjs"
        playwright_helper_script.write_text(
            (
                f"""
import {{ runSemanticChecks }} from {json.dumps(playwright_helper_uri)};

const ops = [];

function makeLocator(id, rect) {{
  const locator = {{
    first() {{
      return locator;
    }},
    async count() {{
      return 1;
    }},
    async scrollIntoViewIfNeeded() {{
      ops.push(`scroll:${{id}}`);
    }},
    async boundingBox() {{
      return rect;
    }},
    async evaluate(fn, arg) {{
      const source = String(fn);
      if (source.includes("computedStyle")) {{
        return {{
          style: {{
            color: "#111111",
            backgroundColor: "#ffffff",
            fontSize: "16px",
            fontWeight: "600",
            opacity: "1",
            display: "block",
            visibility: "visible",
          }},
          attributes: {{
            role: null,
            checked: null,
            disabled: false,
            expanded: null,
            pressed: null,
            text: id,
          }},
          textOverflow: {{
            horizontal: false,
            vertical: false,
            scrollWidth: 120,
            clientWidth: 120,
            scrollHeight: 40,
            clientHeight: 40,
          }},
        }};
      }}
      if (source.includes("elementFromPoint")) {{
        return {{ occupiedBySelf: true, occupantHtml: null }};
      }}
      if (source.includes("document.activeElement")) {{
        return false;
      }}
      return null;
    }},
    async isVisible() {{
      return true;
    }},
    async hover() {{
      ops.push(`hover:${{id}}`);
    }},
    async focus() {{
      ops.push(`focus:${{id}}`);
    }},
    async press(key) {{
      ops.push(`press:${{id}}:${{key}}`);
    }},
    async click(options = {{}}) {{
      ops.push(`click:${{id}}:${{options.delay ?? 0}}`);
    }},
    async fill(value) {{
      ops.push(`fill:${{id}}:${{value}}`);
    }},
    async type(value) {{
      ops.push(`type:${{id}}:${{value}}`);
    }},
    async waitFor(options = {{}}) {{
      ops.push(`waitFor:${{id}}:${{options.state ?? "visible"}}:${{options.timeout ?? 0}}`);
    }},
    async screenshot(options = {{}}) {{
      ops.push(`screenshot:${{id}}:${{options.path ?? ""}}`);
    }},
  }};
  return locator;
}}

const locators = new Map([
  ["test:card", makeLocator("card", {{ left: 40, top: 60, width: 220, height: 96, right: 260, bottom: 156 }})],
  ["test:input", makeLocator("input", {{ left: 60, top: 220, width: 280, height: 44, right: 340, bottom: 264 }})],
]);

const page = {{
  locator(selector) {{
    return locators.get(selector) ?? null;
  }},
  getByRole(role, options = {{}}) {{
    return locators.get(`role:${{role}}:${{options.name ?? ""}}`) ?? locators.get(`role:${{role}}`) ?? null;
  }},
  getByTestId(testId) {{
    return locators.get(`test:${{testId}}`) ?? null;
  }},
  getByText(text) {{
    return locators.get(`text:${{text}}`) ?? null;
  }},
  viewportSize() {{
    return {{ width: 1280, height: 800 }};
  }},
  mouse: {{
    async move(x, y, options = {{}}) {{
      ops.push(`mouse:move:${{Math.round(x)}}:${{Math.round(y)}}:${{options.steps ?? 0}}`);
    }},
    async down() {{
      ops.push("mouse:down");
    }},
    async up() {{
      ops.push("mouse:up");
    }},
  }},
  async waitForTimeout(ms) {{
    ops.push(`waitTimeout:${{ms}}`);
  }},
  async content() {{
    return "<main></main>";
  }},
}};

const report = await runSemanticChecks(page, {{
  runner: "playwright-visual",
  case_id: "playwright-path-helper",
  report_path: {json.dumps(str(playwright_helper_report))},
  required_checks: ["presence_uniqueness", "visibility", "scroll_reachability"],
  targets: [
    {{ target_id: "card", locator: {{ kind: "test_id", value: "card" }} }},
    {{ target_id: "input", locator: {{ kind: "test_id", value: "input" }} }},
  ],
  reachability_paths: [
    {{
      path_id: "card-gesture-path",
      target_id: "card",
      required_for_action_ids: ["hero-card-open"],
      steps: [
        {{ action: "scroll_to", target_id: "card" }},
        {{ action: "tap", target_id: "card" }},
        {{ action: "long_press", target_id: "card", duration_ms: 900 }},
        {{ action: "swipe", target_id: "card", direction: "left", distance_px: 120 }},
        {{ action: "type_text", target_id: "input", value: "hello" }},
        {{ action: "wait_for", target_id: "input", timeout_ms: 2500 }},
      ],
    }},
  ],
}});

console.log(JSON.stringify({{ ops, summary: report.summary, reachability_paths: report.reachability_paths }}));
""".strip()
                + "\n"
            ),
            encoding="utf-8",
        )
        detox_helper_script.write_text(
            (
                f"""
import {{ runSemanticChecks }} from {json.dumps(detox_helper_uri)};

const ops = [];
const visibility = {{
  card: false,
  input: true,
}};

function makeElement(id) {{
  return {{
    id,
    async tap() {{
      ops.push(`tap:${{id}}`);
    }},
    async longPress(duration) {{
      ops.push(`longPress:${{id}}:${{duration}}`);
    }},
    async swipe(direction, speed, percentage) {{
      ops.push(`swipe:${{id}}:${{direction}}:${{speed}}:${{percentage}}`);
    }},
    async replaceText(value) {{
      ops.push(`replaceText:${{id}}:${{value}}`);
    }},
    async typeText(value) {{
      ops.push(`typeText:${{id}}:${{value}}`);
    }},
    async scroll(distance, direction) {{
      ops.push(`scroll:${{id}}:${{distance}}:${{direction}}`);
      if (id === "list") {{
        visibility.card = true;
      }}
    }},
    async getAttributes() {{
      return {{
        enabled: true,
        focused: false,
        label: id,
        value: id === "input" ? "hello" : null,
        visible: visibility[id] ?? true,
        alpha: 1,
        elevation: 0,
        textSize: 16,
      }};
    }},
  }};
}}

const elements = new Map([
  ["card", makeElement("card")],
  ["input", makeElement("input")],
  ["list", makeElement("list")],
]);

const runtime = {{
  device: {{}},
  by: {{
    id(value) {{
      return {{ kind: "id", value }};
    }},
    text(value) {{
      return {{ kind: "text", value }};
    }},
  }},
  element(matcher) {{
    return elements.get(matcher.value);
  }},
  expect(targetElement) {{
    return {{
      async toExist() {{
        ops.push(`expect:exist:${{targetElement.id}}`);
      }},
      async toBeVisible() {{
        ops.push(`expect:visible:${{targetElement.id}}`);
        if (!(visibility[targetElement.id] ?? true)) {{
          throw new Error(`not visible:${{targetElement.id}}`);
        }}
      }},
    }};
  }},
  waitFor(targetElement) {{
    return {{
      toBeVisible() {{
        return {{
          async withTimeout(timeoutMs) {{
            ops.push(`waitFor:${{targetElement.id}}:${{timeoutMs}}`);
            visibility[targetElement.id] = true;
          }},
        }};
      }},
    }};
  }},
}};

const report = await runSemanticChecks(runtime, {{
  runner: "detox-visual",
  case_id: "detox-path-helper",
  report_path: {json.dumps(str(detox_helper_report))},
  required_checks: ["presence_uniqueness", "visibility", "scroll_reachability"],
  targets: [
    {{
      target_id: "card",
      locator: {{ kind: "test_id", value: "card" }},
      scroll_container_locator: {{ kind: "test_id", value: "list" }},
    }},
    {{
      target_id: "input",
      locator: {{ kind: "test_id", value: "input" }},
    }},
  ],
  reachability_paths: [
    {{
      path_id: "card-mobile-path",
      target_id: "card",
      required_for_action_ids: ["hero-card-mobile"],
      steps: [
        {{ action: "scroll_to", target_id: "card" }},
        {{ action: "tap", target_id: "card" }},
        {{ action: "long_press", target_id: "card", duration_ms: 750 }},
        {{ action: "swipe", target_id: "card", direction: "right" }},
        {{ action: "type_text", target_id: "input", value: "hello" }},
        {{ action: "wait_for", target_id: "input", timeout_ms: 2500 }},
      ],
    }},
  ],
}});

console.log(JSON.stringify({{ ops, summary: report.summary, reachability_paths: report.reachability_paths }}));
""".strip()
                + "\n"
            ),
            encoding="utf-8",
        )
        playwright_helper_result = json.loads(
            subprocess.run(
                ["node", str(playwright_helper_script)],
                check=True,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            ).stdout
        )
        assert playwright_helper_result["summary"]["reachability_path_count"] == 1
        assert playwright_helper_result["reachability_paths"][0]["status"] == "passed"
        assert "scroll:card" in playwright_helper_result["ops"]
        assert "click:card:0" in playwright_helper_result["ops"]
        assert "click:card:900" in playwright_helper_result["ops"]
        assert "fill:input:hello" in playwright_helper_result["ops"]
        assert "waitFor:input:visible:2500" in playwright_helper_result["ops"]
        assert "mouse:down" in playwright_helper_result["ops"]
        assert "mouse:up" in playwright_helper_result["ops"]
        detox_helper_result = json.loads(
            subprocess.run(
                ["node", str(detox_helper_script)],
                check=True,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            ).stdout
        )
        assert detox_helper_result["summary"]["reachability_path_count"] == 1
        assert detox_helper_result["reachability_paths"][0]["status"] == "passed"
        assert "scroll:list:180:down" in detox_helper_result["ops"]
        assert "tap:card" in detox_helper_result["ops"]
        assert "longPress:card:750" in detox_helper_result["ops"]
        assert "swipe:card:right:fast:0.75" in detox_helper_result["ops"]
        assert "replaceText:input:hello" in detox_helper_result["ops"]
        assert "waitFor:input:2500" in detox_helper_result["ops"]
        playwright_module_check = subprocess.run(
            [
                "node",
                "-e",
                "try { require.resolve('playwright'); process.stdout.write('ok'); } catch (error) { process.exit(1); }",
            ],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        chrome_path = next(
            (
                candidate
                for candidate in [
                    os.environ.get("CHROME_BINARY"),
                    shutil.which("google-chrome"),
                    shutil.which("google-chrome-stable"),
                    shutil.which("chromium"),
                    shutil.which("chromium-browser"),
                    shutil.which("chrome"),
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ]
                if candidate and Path(candidate).expanduser().exists()
            ),
            None,
        )
        live_playwright_status = "gap"
        if playwright_module_check.returncode == 0 and chrome_path:
            live_playwright_report = temp_root / "playwright-live-helper-report.json"
            live_playwright_script = temp_root / "playwright-live-helper-check.mjs"
            live_playwright_script.write_text(
                (
                    f"""
import {{ chromium }} from "playwright";
import {{ runSemanticChecks }} from {json.dumps(playwright_helper_uri)};

const browser = await chromium.launch({{
  headless: true,
  executablePath: {json.dumps(str(chrome_path))},
}});
const page = await browser.newPage({{ viewport: {{ width: 1280, height: 800 }} }});
await page.setContent(
  `<main role="main" style="padding: 48px;">
     <button data-testid="hero-cta" style="display: block; padding: 16px 24px;">Continue</button>
   </main>`,
  {{ waitUntil: "load" }},
);
const report = await runSemanticChecks(page, {{
  runner: "playwright-visual",
  case_id: "playwright-live-helper",
  report_path: {json.dumps(str(live_playwright_report))},
  required_checks: [
    "presence_uniqueness",
    "visibility",
    "scroll_reachability",
    "overflow_clipping",
    "computed_styles",
    "interaction_states",
    "layout_relations",
    "occlusion"
  ],
  targets: [
    {{
      target_id: "hero-cta",
      locator: {{ kind: "test_id", value: "hero-cta" }},
      interactions: ["hover", "focus"],
    }},
  ],
}});
await browser.close();
console.log(JSON.stringify(report));
""".strip()
                    + "\n"
                ),
                encoding="utf-8",
            )
            live_playwright_result = json.loads(
                subprocess.run(
                    ["node", str(live_playwright_script)],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=os.environ.copy(),
                ).stdout
            )
            live_playwright_payload = _read_json_file(live_playwright_report)
            live_checks = {
                item["check_id"]: item["status"]
                for item in live_playwright_payload["targets"][0]["checks"]
            }
            assert live_playwright_result["summary"]["status"] == "passed"
            assert live_playwright_payload["schema_version"] == SEMANTIC_REPORT_SCHEMA_VERSION
            assert live_playwright_payload["helper_bundle_version"] == helper_sync["bundle_version"]
            assert live_playwright_payload["targets"][0]["target_id"] == "hero-cta"
            for check_id in (
                "presence_uniqueness",
                "visibility",
                "scroll_reachability",
                "overflow_clipping",
                "computed_styles",
                "interaction_states",
                "layout_relations",
                "occlusion",
            ):
                assert live_checks[check_id] == "passed"
            live_playwright_status = "passed"
        else:
            assert playwright_module_check.returncode != 0 or chrome_path is None
        progress("helper sync, semantic validator guardrails, and helper-bundle contract checks")

        gesture_contract_workspace = temp_root / "gesture-contract-workspace"
        gesture_contract_workspace.mkdir()
        _seed_workspace(gesture_contract_workspace)
        init_workspace(gesture_contract_workspace)
        gesture_contract_workstream_id = create_workstream(
            gesture_contract_workspace,
            "Gesture Contract Coverage",
            kind="feature",
            scope_summary="Exercise reachability path, limitation, and compact design summary contracts.",
        )["created_workstream_id"]
        gesture_baseline = gesture_contract_workspace / "tests" / "visual" / "baselines" / "gesture-home.txt"
        gesture_baseline.parent.mkdir(parents=True, exist_ok=True)
        gesture_baseline.write_text("gesture baseline\n")
        write_design_handoff(
            gesture_contract_workspace,
            {
                "status": "ready",
                "platform": "mobile",
                "verification_hooks": ["screen:gesture-home"],
                "affected_surfaces": [
                    {
                        "surface_id": "gesture-home",
                        "platform": "mobile",
                        "route_or_screen": "GestureHome",
                        "summary": "Gesture-heavy feed with swipe and drag affordances.",
                    }
                ],
                "user_flows": [
                    {
                        "flow_id": "gesture-open",
                        "title": "Open gesture card",
                        "entry_points": ["feed-card"],
                        "success_state": "Card details open",
                        "steps": ["Reveal card", "Perform gesture", "Observe details"],
                    }
                ],
                "state_coverage": [
                    {
                        "state_id": "gesture-default",
                        "surface_id": "gesture-home",
                        "status_kind": "covered",
                        "notes": "Default feed state is covered.",
                        "verified_by": ["playwright-visual"],
                    }
                ],
                "ux_rationale": [
                    {
                        "decision_id": "gesture-priority",
                        "surface_ids": ["gesture-home"],
                        "rationale": "Keep direct manipulation visible for high-value feed actions.",
                        "tradeoff": "Gesture coverage needs authored paths or explicit limitations.",
                    }
                ],
                "critical_actions": [
                    {
                        "action_id": "swipe-card",
                        "flow_id": "gesture-open",
                        "surface_id": "gesture-home",
                        "interaction_type": "swipe",
                        "priority": "high",
                        "verification_path_id": "swipe-card-path",
                    },
                    {
                        "action_id": "scroll-feed",
                        "flow_id": "gesture-open",
                        "surface_id": "gesture-home",
                        "interaction_type": "scroll_to",
                        "priority": "medium",
                        "verification_path_id": None,
                    },
                    {
                        "action_id": "drag-card",
                        "flow_id": "gesture-open",
                        "surface_id": "gesture-home",
                        "interaction_type": "drag",
                        "priority": "high",
                        "verification_path_id": "drag-card-path",
                    },
                    {
                        "action_id": "limited-long-press",
                        "flow_id": "gesture-open",
                        "surface_id": "gesture-home",
                        "interaction_type": "long_press",
                        "priority": "medium",
                        "verification_path_id": "limited-long-press-path",
                    },
                ],
                "testability_guidance": {
                    "stable_targets": ["feed-card", "feed-list"],
                    "masks": [".clock"],
                    "preconditions": ["seeded-feed"],
                    "limitations": ["android-compose cannot execute authored gesture paths"],
                },
            },
            workstream_id=gesture_contract_workstream_id,
        )
        write_verification_recipes(
            gesture_contract_workspace,
            {
                "baseline_policy": {
                    "canonical_baselines": "project_owned",
                    "transient_artifacts": "external_state_only",
                },
                "cases": [
                    {
                        "id": "scroll-only-web",
                        "title": "Scroll-only semantic coverage",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "changed_path_globs": ["apps/web/**"],
                        "host_requirements": ["python"],
                        "argv": [sys.executable, "-c", "print('scroll only')"],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "1280x800"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "scroll-only-web.json",
                            "required_checks": ["visibility", "scroll_reachability"],
                            "targets": [
                                {
                                    "target_id": "feed-card",
                                    "locator": {"kind": "test_id", "value": "feed-card"},
                                }
                            ],
                        },
                        "baseline": {"policy": "project-owned", "source_path": str(gesture_baseline.relative_to(gesture_contract_workspace))},
                    },
                    {
                        "id": "android-gesture-path",
                        "title": "Unsupported Android authored path",
                        "surface_type": "android",
                        "runner": "android-compose-screenshot",
                        "changed_path_globs": ["apps/mobile/android/**"],
                        "host_requirements": ["python"],
                        "argv": [sys.executable, "-c", "print('should not execute')"],
                        "target": {"screen_id": "gesture-home"},
                        "device_or_viewport": {"device": "android-emulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "android-gesture-path.json",
                            "required_checks": ["visibility"],
                            "targets": [
                                {
                                    "target_id": "feed-card",
                                    "locator": {"kind": "test_id", "value": "feed-card"},
                                }
                            ],
                            "reachability_paths": [
                                {
                                    "path_id": "swipe-card-path",
                                    "title": "Swipe feed card",
                                    "target_id": "feed-card",
                                    "required_for_action_ids": ["swipe-card"],
                                    "steps": [
                                        {
                                            "action": "swipe",
                                            "target_id": "feed-card",
                                            "direction": "left",
                                        }
                                    ],
                                }
                            ],
                            "limitation_entries": [
                                {
                                    "limitation_id": "limited-long-press-gap",
                                    "action_id": "limited-long-press",
                                    "kind": "runner_gap",
                                    "reason": "Compose screenshot runner does not execute long-press gestures.",
                                    "runner_scope": ["android-compose-screenshot"],
                                }
                            ],
                        },
                        "baseline": {"policy": "project-owned", "source_path": str(gesture_baseline.relative_to(gesture_contract_workspace))},
                    },
                ],
                "suites": [{"id": "full", "title": "Full Suite", "case_ids": ["scroll-only-web", "android-gesture-path"]}],
            },
            workstream_id=gesture_contract_workstream_id,
        )
        invalid_action_error = None
        try:
            write_verification_recipes(
                gesture_contract_workspace,
                {
                    "cases": [
                        {
                            "id": "invalid-action",
                            "title": "Invalid action path",
                            "surface_type": "web",
                            "runner": "playwright-visual",
                            "changed_path_globs": ["apps/web/**"],
                            "host_requirements": ["python"],
                            "argv": [sys.executable, "-c", "print('invalid')"],
                            "semantic_assertions": {
                                "enabled": True,
                                "targets": [
                                    {
                                        "target_id": "feed-card",
                                        "locator": {"kind": "test_id", "value": "feed-card"},
                                    }
                                ],
                                "reachability_paths": [
                                    {
                                        "path_id": "invalid-action-path",
                                        "steps": [{"action": "pinch", "target_id": "feed-card"}],
                                    }
                                ],
                            },
                        }
                    ],
                    "suites": [{"id": "full", "title": "Full", "case_ids": ["invalid-action"]}],
                },
                workstream_id=gesture_contract_workstream_id,
            )
        except ValueError as exc:
            invalid_action_error = str(exc)
        assert invalid_action_error and "Unsupported reachability step action" in invalid_action_error
        invalid_target_error = None
        try:
            write_verification_recipes(
                gesture_contract_workspace,
                {
                    "cases": [
                        {
                            "id": "invalid-target",
                            "title": "Invalid target path",
                            "surface_type": "web",
                            "runner": "playwright-visual",
                            "changed_path_globs": ["apps/web/**"],
                            "host_requirements": ["python"],
                            "argv": [sys.executable, "-c", "print('invalid')"],
                            "semantic_assertions": {
                                "enabled": True,
                                "targets": [
                                    {
                                        "target_id": "feed-card",
                                        "locator": {"kind": "test_id", "value": "feed-card"},
                                    }
                                ],
                                "reachability_paths": [
                                    {
                                        "path_id": "invalid-target-path",
                                        "steps": [{"action": "tap", "target_id": "missing-target"}],
                                    }
                                ],
                            },
                        }
                    ],
                    "suites": [{"id": "full", "title": "Full", "case_ids": ["invalid-target"]}],
                },
                workstream_id=gesture_contract_workstream_id,
            )
        except ValueError as exc:
            invalid_target_error = str(exc)
        assert invalid_target_error and "unknown semantic target" in invalid_target_error
        gesture_summary = workspace_summary(gesture_contract_workspace)
        assert gesture_summary["design_summary"]["critical_action_count"] == 4
        assert gesture_summary["testability_summary"]["limitation_count"] == 1
        assert gesture_summary["testability_summary"]["unresolved_action_count"] == 2
        gesture_snapshot = dashboard_snapshot(gesture_contract_workspace)
        assert gesture_snapshot["workspace_cockpit"]["plan"]["design_state"]["design_summary"]["critical_action_count"] == 4
        assert gesture_snapshot["workspace_cockpit"]["plan"]["design_state"]["testability_summary"]["limitation_count"] == 1
        gesture_context = show_workspace_context_pack(
            gesture_contract_workspace,
            request_text="gesture limitation coverage",
            route_id="plugin-dev",
            force_refresh=True,
        )
        assert gesture_context["workspace_context"]["design_summary"]["critical_action_count"] == 4
        assert gesture_context["workspace_context"]["testability_summary"]["limitation_count"] == 1
        assert "affected_surfaces" not in json.dumps(gesture_context["workspace_context"])
        gesture_coverage_audit = audit_verification_coverage(
            gesture_contract_workspace,
            workstream_id=gesture_contract_workstream_id,
        )
        gesture_gap_ids = {gap["gap_id"] for gap in gesture_coverage_audit["gaps"]}
        assert gesture_coverage_audit["design_summary"]["critical_action_count"] == 4
        assert gesture_coverage_audit["testability_summary"]["limitation_count"] == 1
        assert "android-gesture-path-unsupported-reachability-runner" in gesture_gap_ids
        assert "swipe-card-unsupported-authored-path" in gesture_gap_ids
        assert "drag-card-missing-critical-action-path" in gesture_gap_ids
        assert "limited-long-press-declared-limitation" in gesture_gap_ids
        assert "scroll-feed-missing-critical-action-path" not in gesture_gap_ids
        gesture_helper_sync = sync_verification_helpers(gesture_contract_workspace)
        assert gesture_helper_sync["materialization"]["status"] == "synced"
        unsupported_gesture_run = start_verification_case(
            gesture_contract_workspace,
            "android-gesture-path",
            workstream_id=gesture_contract_workstream_id,
        )
        unsupported_gesture_run = wait_for_verification_run(
            gesture_contract_workspace,
            unsupported_gesture_run["run_id"],
            timeout_seconds=20,
            workstream_id=gesture_contract_workstream_id,
        )
        assert unsupported_gesture_run["status"] == "failed"
        assert unsupported_gesture_run["cases"][0]["attempts"] == 0
        assert unsupported_gesture_run["cases"][0]["semantic_assertions"]["reason"] == "reachability_contract_invalid"
        progress("verification recipes, helper sync, and CLI verification entrypoints")

        cli_case_output = subprocess.run(
            python_script_command(
                plugin_root / "scripts" / "agentiux_dev_state.py",
                [
                    "run-verification-case",
                    "--workspace",
                    str(workspace),
                    "--case-id",
                    "web-home",
                    "--wait",
                    "--workstream-id",
                    verification_workstream_id,
                ],
            ),
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        ).stdout
        assert "\"status\": \"passed\"" in cli_case_output

        verification_case_timeout_seconds = 20
        browser_layout_timeout_seconds = 45
        verification_suite_timeout_seconds = 45

        case_run = start_verification_case(workspace, "web-home", workstream_id=verification_workstream_id)
        case_run = wait_for_verification_run(
            workspace,
            case_run["run_id"],
            timeout_seconds=verification_case_timeout_seconds,
            workstream_id=verification_workstream_id,
        )
        assert case_run["mode"] == "case"
        assert case_run["status"] == "passed"
        assert case_run["case_ids"] == ["web-home"]
        assert case_run["cases"][0]["baseline"]["status"] == "matched"
        assert case_run["cases"][0]["semantic_assertions"]["status"] == "passed"
        approved = approve_verification_baseline(workspace, "web-home", run_id=case_run["run_id"], workstream_id=verification_workstream_id)
        assert approved["status"] == "approved"
        updated_baseline = update_verification_baseline(
            workspace,
            "web-home",
            run_id=case_run["run_id"],
            artifact_path=str(Path(case_run["artifacts_dir"]) / "web-home.txt"),
            workstream_id=verification_workstream_id,
        )
        assert updated_baseline["status"] == "updated"
        assert baseline_target.read_text() == "web home ok\n"

        semantic_failure_run = start_verification_case(workspace, "web-semantic-missing", workstream_id=verification_workstream_id)
        semantic_failure_run = wait_for_verification_run(
            workspace,
            semantic_failure_run["run_id"],
            timeout_seconds=20,
            workstream_id=verification_workstream_id,
        )
        assert semantic_failure_run["status"] == "failed"
        assert semantic_failure_run["cases"][0]["semantic_assertions"]["status"] == "failed"
        semantic_failure_events = read_verification_events(
            workspace,
            semantic_failure_run["run_id"],
            limit=20,
            workstream_id=verification_workstream_id,
        )
        assert any(event["event_type"] == "semantic_assertions_failed" for event in semantic_failure_events["events"])

        optional_warning_run = start_verification_case(
            workspace,
            "web-optional-semantic-warning",
            workstream_id=verification_workstream_id,
        )
        optional_warning_run = wait_for_verification_run(
            workspace,
            optional_warning_run["run_id"],
            timeout_seconds=20,
            workstream_id=verification_workstream_id,
        )
        assert optional_warning_run["status"] == "passed"
        optional_summary = optional_warning_run["cases"][0]["semantic_assertions"]
        assert optional_summary["status"] == "passed"
        assert optional_summary["optional_failed_checks"] == ["optional-main/layout_relations"]

        expo_overlap_run = start_verification_case(
            workspace,
            "expo-native-layout-overlap",
            workstream_id=verification_workstream_id,
        )
        expo_overlap_run = wait_for_verification_run(
            workspace,
            expo_overlap_run["run_id"],
            timeout_seconds=20,
            workstream_id=verification_workstream_id,
        )
        assert expo_overlap_run["status"] == "failed"
        expo_overlap_summary = expo_overlap_run["cases"][0]["native_layout_audit"]
        assert expo_overlap_summary["status"] == "failed"
        assert any(issue["type"] == "pair-overlap" for issue in expo_overlap_summary["issues"])
        expo_overlap_events = read_verification_events(
            workspace,
            expo_overlap_run["run_id"],
            limit=20,
            workstream_id=verification_workstream_id,
        )
        assert any(event["event_type"] == "native_layout_audit_failed" for event in expo_overlap_events["events"])

        expo_gutter_warning_run = start_verification_case(
            workspace,
            "expo-native-gutter-warning",
            workstream_id=verification_workstream_id,
        )
        expo_gutter_warning_run = wait_for_verification_run(
            workspace,
            expo_gutter_warning_run["run_id"],
            timeout_seconds=20,
            workstream_id=verification_workstream_id,
        )
        assert expo_gutter_warning_run["status"] == "failed"
        expo_gutter_warning_summary = expo_gutter_warning_run["cases"][0]["native_layout_audit"]
        assert expo_gutter_warning_summary["status"] == "warning"
        assert expo_gutter_warning_summary["issue_count"] == 0
        assert expo_gutter_warning_summary["warning_count"] == 1
        assert any(issue["type"] == "edge-gutter-imbalance" for issue in expo_gutter_warning_summary["warnings"])
        expo_gutter_events = read_verification_events(
            workspace,
            expo_gutter_warning_run["run_id"],
            limit=20,
            workstream_id=verification_workstream_id,
        )
        assert any(event["event_type"] == "native_layout_audit_warning" for event in expo_gutter_events["events"])

        expo_spacing_warning_run = start_verification_case(
            workspace,
            "expo-native-spacing-warning",
            workstream_id=verification_workstream_id,
        )
        expo_spacing_warning_run = wait_for_verification_run(
            workspace,
            expo_spacing_warning_run["run_id"],
            timeout_seconds=20,
            workstream_id=verification_workstream_id,
        )
        assert expo_spacing_warning_run["status"] == "failed"
        expo_spacing_warning_summary = expo_spacing_warning_run["cases"][0]["native_layout_audit"]
        assert expo_spacing_warning_summary["status"] == "warning"
        assert expo_spacing_warning_summary["issue_count"] == 0
        assert expo_spacing_warning_summary["warning_count"] >= 2
        assert any(
            issue["type"] == "vertical-rhythm-drift" for issue in expo_spacing_warning_summary["warnings"]
        )
        assert any(
            issue["type"] == "touch-target-too-small" for issue in expo_spacing_warning_summary["warnings"]
        )
        expo_spacing_events = read_verification_events(
            workspace,
            expo_spacing_warning_run["run_id"],
            limit=20,
            workstream_id=verification_workstream_id,
        )
        assert any(event["event_type"] == "native_layout_audit_warning" for event in expo_spacing_events["events"])

        android_style_mismatch_run = start_verification_case(
            workspace,
            "android-native-style-mismatch",
            workstream_id=verification_workstream_id,
        )
        android_style_mismatch_run = wait_for_verification_run(
            workspace,
            android_style_mismatch_run["run_id"],
            timeout_seconds=20,
            workstream_id=verification_workstream_id,
        )
        assert android_style_mismatch_run["status"] == "failed"
        android_style_summary = android_style_mismatch_run["cases"][0]["native_layout_audit"]
        assert android_style_summary["status"] == "failed"
        assert any(issue["type"] == "style-mismatch" for issue in android_style_summary["issues"])

        android_fixed_run = start_verification_case(
            workspace,
            "android-native-layout-fixed",
            workstream_id=verification_workstream_id,
        )
        android_fixed_run = wait_for_verification_run(
            workspace,
            android_fixed_run["run_id"],
            timeout_seconds=20,
            workstream_id=verification_workstream_id,
        )
        assert android_fixed_run["status"] == "passed"
        android_fixed_summary = android_fixed_run["cases"][0]["native_layout_audit"]
        assert android_fixed_summary["status"] == "passed"
        assert android_fixed_summary["issue_count"] == 0
        assert Path(android_fixed_summary["report_path"]).exists()

        overlap_layout_run = start_verification_case(
            workspace,
            "browser-layout-overlap",
            workstream_id=verification_workstream_id,
        )
        overlap_layout_run = wait_for_verification_run(
            workspace,
            overlap_layout_run["run_id"],
            timeout_seconds=browser_layout_timeout_seconds,
            workstream_id=verification_workstream_id,
        )
        assert overlap_layout_run["status"] == "failed"
        overlap_layout_summary = overlap_layout_run["cases"][0]["browser_layout_audit"]
        assert overlap_layout_summary["status"] == "failed"
        assert int(overlap_layout_summary["issue_count"] or 0) > 0
        assert any(issue["type"] in {"pair-overlap", "occlusion", "viewport-overflow"} for issue in overlap_layout_summary["issues"])
        overlap_events = read_verification_events(
            workspace,
            overlap_layout_run["run_id"],
            limit=20,
            workstream_id=verification_workstream_id,
        )
        assert any(event["event_type"] == "browser_layout_audit_failed" for event in overlap_events["events"])

        warning_layout_run = start_verification_case(
            workspace,
            "browser-layout-warning",
            workstream_id=verification_workstream_id,
        )
        warning_layout_run = wait_for_verification_run(
            workspace,
            warning_layout_run["run_id"],
            timeout_seconds=browser_layout_timeout_seconds,
            workstream_id=verification_workstream_id,
        )
        assert warning_layout_run["status"] == "failed"
        warning_layout_summary = warning_layout_run["cases"][0]["browser_layout_audit"]
        assert warning_layout_summary["status"] == "warning"
        assert warning_layout_summary["issue_count"] == 0
        assert int(warning_layout_summary["warning_count"] or 0) >= 2
        assert any(
            issue["type"] in {"container-padding-imbalance", "vertical-rhythm-drift", "touch-target-too-small", "contrast-warning"}
            for issue in warning_layout_summary["warnings"]
        )
        warning_layout_events = read_verification_events(
            workspace,
            warning_layout_run["run_id"],
            limit=20,
            workstream_id=verification_workstream_id,
        )
        assert any(event["event_type"] == "browser_layout_audit_warning" for event in warning_layout_events["events"])

        fixed_layout_run = start_verification_case(
            workspace,
            "browser-layout-fixed",
            workstream_id=verification_workstream_id,
        )
        fixed_layout_run = wait_for_verification_run(
            workspace,
            fixed_layout_run["run_id"],
            timeout_seconds=browser_layout_timeout_seconds,
            workstream_id=verification_workstream_id,
        )
        assert fixed_layout_run["status"] == "passed"
        fixed_layout_summary = fixed_layout_run["cases"][0]["browser_layout_audit"]
        assert fixed_layout_summary["status"] == "passed"
        assert fixed_layout_summary["issue_count"] == 0
        assert Path(fixed_layout_summary["report_path"]).exists()
        assert Path(fixed_layout_summary["screenshot_path"]).exists()
        progress("single-case verification runs, semantic failures, native mobile layout audits, and live browser layout audit")

        suite_run = start_verification_suite(workspace, "full", workstream_id=verification_workstream_id)
        active_run, mid_events = _wait_for_run_started(
            workspace,
            suite_run["run_id"],
            workstream_id=verification_workstream_id,
        )
        if active_run is not None:
            assert active_run["run_id"] == suite_run["run_id"]
        assert any(event["event_type"] == "run_started" for event in mid_events["events"])

        suite_run = wait_for_verification_run(
            workspace,
            suite_run["run_id"],
            timeout_seconds=verification_suite_timeout_seconds,
            workstream_id=verification_workstream_id,
        )
        assert suite_run["mode"] == "suite"
        assert suite_run["status"] == "passed"
        assert suite_run["case_ids"] == ["web-home", "expo-home"]
        assert active_verification_run(workspace, workstream_id=verification_workstream_id) is None

        all_runs = list_verification_runs(workspace, workstream_id=verification_workstream_id)
        assert len(all_runs["runs"]) >= 2
        assert all_runs["latest_run"]["run_id"] == suite_run["run_id"]
        assert all_runs["latest_completed_run"]["run_id"] == suite_run["run_id"]
        event_log = read_verification_events(workspace, suite_run["run_id"], limit=50, workstream_id=verification_workstream_id)
        event_types = {event["event_type"] for event in event_log["events"]}
        assert "case_heartbeat" in event_types
        assert "case_slow" in event_types
        assert "logcat_started" in event_types
        assert "logcat_heartbeat" in event_types
        assert "logcat_stopped" in event_types
        assert "native_layout_audit_validated" in event_types
        assert "run_finished" in event_types
        stdout_log = read_verification_log_tail(workspace, suite_run["run_id"], "stdout", 50, workstream_id=verification_workstream_id)
        stderr_log = read_verification_log_tail(workspace, suite_run["run_id"], "stderr", 20, workstream_id=verification_workstream_id)
        logcat_log = read_verification_log_tail(workspace, suite_run["run_id"], "logcat", 50, workstream_id=verification_workstream_id)
        assert any("web-home done" in line for line in stdout_log["lines"])
        assert any("expo-home done" in line for line in stdout_log["lines"])
        assert stderr_log["path"].endswith("stderr.log")
        assert logcat_log["path"].endswith("logcat.log")
        assert any("FATAL EXCEPTION" in line for line in logcat_log["lines"])
        assert suite_run["summary"]["logcat_crash_summary"]["case_id"] == "expo-home"
        assert suite_run["summary"]["native_layout_audit"]["case_id"] == "expo-home"
        assert suite_run["summary"]["native_layout_audit"]["status"] == "passed"
        expo_suite_case = next(case for case in suite_run["cases"] if case["case_id"] == "expo-home")
        assert expo_suite_case["native_layout_audit"]["status"] == "passed"
        progress("full-suite verification execution, heartbeat events, and log capture")

        closeout_register = read_stage_register(workspace)
        closeout_register["stage_status"] = "ready_for_closeout"
        for stage in closeout_register["stages"]:
            if stage["id"] == closeout_register["current_stage"]:
                stage["verification_selectors"] = {}
                stage["verification_policy"] = {}
        closeout_register["verification_policy"]["closeout_default_mode"] = "full"
        write_stage_register(workspace, closeout_register, confirmed_stage_plan_edit=True)
        closeout_selection = resolve_verification_selection(workspace)
        assert closeout_selection["selection_status"] == "resolved"
        assert closeout_selection["requested_mode"] == "full"
        assert closeout_selection["requested_mode_source"] == "workstream_closeout_policy"
        assert closeout_selection["resolved_mode"] == "full"
        assert closeout_selection["full_suite"] is True
        assert closeout_selection["selected_suite"]["id"] == "full"
        assert [case["case_id"] for case in closeout_selection["selected_cases"]] == ["web-home", "expo-home"]

        verification_paths = workspace_paths(workspace, workstream_id=verification_workstream_id)
        corrupt_run_path = Path(verification_paths["verification_runs_dir"]) / "corrupt-run" / "run.json"
        corrupt_run_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt_run_path.write_text("{\n")
        corrupt_starter_run = state_root / "starter-runs" / "corrupt-run" / "run.json"
        corrupt_starter_run.parent.mkdir(parents=True, exist_ok=True)
        corrupt_starter_run.write_text("")
        runs_after_corruption = list_verification_runs(workspace, workstream_id=verification_workstream_id)
        assert runs_after_corruption["latest_run"]["run_id"] == suite_run["run_id"]

        helper_preflight_workspace = temp_root / "helper-preflight-workspace"
        helper_preflight_workspace.mkdir()
        _seed_workspace(helper_preflight_workspace)
        init_workspace(helper_preflight_workspace)
        helper_preflight_workstream_id = create_workstream(
            helper_preflight_workspace,
            "Helper Preflight",
            kind="feature",
            scope_summary="Exercise helper sync and preflight runtime failures.",
        )["created_workstream_id"]
        preflight_baseline = helper_preflight_workspace / "tests" / "visual" / "baselines" / "preflight.txt"
        preflight_baseline.parent.mkdir(parents=True, exist_ok=True)
        preflight_baseline.write_text("baseline\n")
        write_verification_recipes(
            helper_preflight_workspace,
            {
                "baseline_policy": {
                    "canonical_baselines": "project_owned",
                    "transient_artifacts": "external_state_only",
                },
                "cases": [
                    {
                        "id": "web-preflight",
                        "title": "Web helper preflight",
                        "surface_type": "web",
                        "runner": "playwright-visual",
                        "changed_path_globs": ["apps/web/**"],
                        "host_requirements": ["python"],
                        "argv": [sys.executable, "-c", "print('should not execute')"],
                        "target": {"route": "/"},
                        "device_or_viewport": {"viewport": "1280x800"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "web-preflight-semantic.json",
                            "required_checks": ["visibility"],
                            "targets": [
                                {
                                    "target_id": "preflight-main",
                                    "locator": {"kind": "role", "value": "main"},
                                }
                            ],
                        },
                        "baseline": {"policy": "project-owned", "source_path": str(preflight_baseline.relative_to(helper_preflight_workspace))},
                    },
                    {
                        "id": "ios-semantic-case",
                        "title": "iOS semantic helper gap",
                        "surface_type": "ios",
                        "runner": "ios-simulator-capture",
                        "changed_path_globs": ["apps/mobile/ios/**"],
                        "host_requirements": ["python"],
                        "argv": [sys.executable, "-c", "print('should not execute')"],
                        "target": {"screen_id": "ios-home"},
                        "device_or_viewport": {"device": "ios-simulator"},
                        "semantic_assertions": {
                            "enabled": True,
                            "report_path": "ios-semantic-case.json",
                            "required_checks": ["visibility"],
                            "targets": [
                                {
                                    "target_id": "ios-home",
                                    "locator": {"kind": "test_id", "value": "ios-home"},
                                }
                            ],
                        },
                        "baseline": {"policy": "project-owned", "source_path": str(preflight_baseline.relative_to(helper_preflight_workspace))},
                    },
                ],
                "suites": [{"id": "full", "title": "Full Suite", "case_ids": ["web-preflight"]}],
            },
            workstream_id=helper_preflight_workstream_id,
        )
        unsynced_preflight_run = start_verification_case(
            helper_preflight_workspace,
            "web-preflight",
            workstream_id=helper_preflight_workstream_id,
        )
        unsynced_preflight_run = wait_for_verification_run(
            helper_preflight_workspace,
            unsynced_preflight_run["run_id"],
            timeout_seconds=20,
            workstream_id=helper_preflight_workstream_id,
        )
        assert unsynced_preflight_run["status"] == "failed"
        assert unsynced_preflight_run["cases"][0]["attempts"] == 0
        assert unsynced_preflight_run["cases"][0]["semantic_assertions"]["reason"] == "helper_bundle_not_synced"
        helper_preflight_sync = sync_verification_helpers(helper_preflight_workspace)
        assert helper_preflight_sync["materialization"]["status"] == "synced"
        ios_helper_run = start_verification_case(
            helper_preflight_workspace,
            "ios-semantic-case",
            workstream_id=helper_preflight_workstream_id,
        )
        ios_helper_run = wait_for_verification_run(
            helper_preflight_workspace,
            ios_helper_run["run_id"],
            timeout_seconds=20,
            workstream_id=helper_preflight_workstream_id,
        )
        assert ios_helper_run["status"] == "failed"
        assert ios_helper_run["cases"][0]["semantic_assertions"]["reason"] == "runner_not_cataloged"
        stale_marker = _read_json_file(Path(helper_preflight_sync["marker_path"]))
        stale_marker["bundle_version"] = "0.7.0"
        _write_json_file(Path(helper_preflight_sync["marker_path"]), stale_marker)
        drift_run = start_verification_case(
            helper_preflight_workspace,
            "web-preflight",
            workstream_id=helper_preflight_workstream_id,
        )
        drift_run = wait_for_verification_run(
            helper_preflight_workspace,
            drift_run["run_id"],
            timeout_seconds=20,
            workstream_id=helper_preflight_workstream_id,
        )
        assert drift_run["status"] == "failed"
        assert drift_run["cases"][0]["semantic_assertions"]["reason"] == "helper_bundle_version_drift"
        helper_preflight_audit = audit_verification_coverage(
            helper_preflight_workspace,
            workstream_id=helper_preflight_workstream_id,
        )
        helper_preflight_gap_ids = {gap["gap_id"] for gap in helper_preflight_audit["gaps"]}
        assert "verification-helper-bundle-version-drift" in helper_preflight_gap_ids
        assert "ios-semantic-case-semantic-runner-not-cataloged" in helper_preflight_gap_ids
        progress("helper preflight failures for unsynced, drifted, and unsupported runners")

        commit_repo = temp_root / "commit-style-repo"
        commit_repo.mkdir()
        subprocess.run(["git", "init"], cwd=commit_repo, check=True, capture_output=True, text=True)
        (commit_repo / "README.md").write_text("# Commit Style\n")
        subprocess.run(["git", "add", "README.md"], cwd=commit_repo, check=True, capture_output=True, text=True)
        _git_commit(commit_repo, "feat(dashboard): add overview panel")
        (commit_repo / "dashboard.txt").write_text("panel\n")
        subprocess.run(["git", "add", "dashboard.txt"], cwd=commit_repo, check=True, capture_output=True, text=True)
        _git_commit(commit_repo, "fix(dashboard): align status badge")
        (commit_repo / "notes.md").write_text("dashboard notes\n")
        subprocess.run(["git", "add", "notes.md"], cwd=commit_repo, check=True, capture_output=True, text=True)
        _git_commit(commit_repo, "test(dashboard): add semantic smoke notes")
        commit_style = detect_commit_style(commit_repo)
        assert commit_style["style"] == "conventional"
        assert commit_style["source"] == "history"
        assert commit_style["uses_scope"] is True
        assert commit_style["preferred_branch_prefix"]
        git_advice = show_git_workflow_advice(commit_repo)
        assert git_advice["inspection"]["style"] == "conventional"
        assert git_advice["commit_policy"]["recommended_style"] == "conventional"
        assert git_advice["branch_policy"]["pattern"].startswith("task/")
        assert "best_practices" not in git_advice
        commit_message = suggest_commit_message(
            commit_repo,
            "Improve dashboard log view",
            files=["plugins/agentiux-dev/dashboard/app.js"],
        )
        assert commit_message["suggested_message"].startswith("feat(dashboard):")
        assert commit_message["advice"] == git_advice
        branch_name = suggest_branch_name(commit_repo, "Improve dashboard log view", mode="task")
        assert branch_name["suggested_branch_name"].startswith("task/")
        assert branch_name["advice"] == git_advice
        workstream_branch_name = suggest_branch_name(commit_repo, "Improve dashboard log view", mode="workstream")
        assert workstream_branch_name["suggested_branch_name"].startswith("feature/")
        pr_title = suggest_pr_title(commit_repo, "Improve dashboard log view", files=["plugins/agentiux-dev/dashboard/app.js"])
        assert pr_title["suggested_pr_title"]
        assert pr_title["advice"] == git_advice
        pr_body = suggest_pr_body(commit_repo, "Improve dashboard log view", files=["plugins/agentiux-dev/dashboard/app.js"])
        assert "## Summary" in pr_body["suggested_pr_body"]
        assert pr_body["advice"] == git_advice

        config_repo = temp_root / "config-style-repo"
        config_repo.mkdir()
        subprocess.run(["git", "init"], cwd=config_repo, check=True, capture_output=True, text=True)
        (config_repo / "commitlint.config.cjs").write_text("module.exports = { extends: ['@commitlint/config-conventional'] };\n")
        config_advice = show_git_workflow_advice(config_repo)
        assert config_advice["inspection"]["source"] == "config"
        assert config_advice["commit_policy"]["recommended_style"] == "conventional"

        trailer_repo = temp_root / "trailer-repo"
        trailer_repo.mkdir()
        subprocess.run(["git", "init"], cwd=trailer_repo, check=True, capture_output=True, text=True)
        (trailer_repo / "README.md").write_text("# Trailer Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=trailer_repo, check=True, capture_output=True, text=True)
        _git_commit(trailer_repo, "Add release notes", "Signed-off-by: AgentiUX <agentiux@example.com>")
        trailer_advice = show_git_workflow_advice(trailer_repo)
        assert trailer_advice["trailer_policy"]["uses_trailers"] is True
        assert trailer_advice["trailer_policy"]["signoff_required"] is True

        ticket_repo = temp_root / "ticket-repo"
        ticket_repo.mkdir()
        subprocess.run(["git", "init"], cwd=ticket_repo, check=True, capture_output=True, text=True)
        (ticket_repo / "README.md").write_text("# Ticket Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=ticket_repo, check=True, capture_output=True, text=True)
        _git_commit(ticket_repo, "PROJECT-123 add dashboard filters")
        ticket_advice = show_git_workflow_advice(ticket_repo)
        assert ticket_advice["ticket_prefix_policy"]["examples"] == ["PROJECT-123"]
        assert ticket_advice["ticket_prefix_policy"]["usage"] == "follow_repo_history"
        assert ticket_advice["inspection"]["source"] == "limited-history"
        assert ticket_advice["commit_policy"]["recommended_style"] == "conventional"

        empty_repo = temp_root / "empty-repo"
        empty_repo.mkdir()
        subprocess.run(["git", "init"], cwd=empty_repo, check=True, capture_output=True, text=True)
        empty_advice = show_git_workflow_advice(empty_repo)
        assert empty_advice["inspection"]["source"] == "fallback"
        assert empty_advice["commit_policy"]["recommended_style"] == "conventional"
        assert empty_advice["branch_policy"]["pattern"].startswith("task/")

        sparse_repo = temp_root / "sparse-repo"
        sparse_repo.mkdir()
        subprocess.run(["git", "init"], cwd=sparse_repo, check=True, capture_output=True, text=True)
        (sparse_repo / "README.md").write_text("# Sparse Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=sparse_repo, check=True, capture_output=True, text=True)
        _git_commit(sparse_repo, "Initial sparse repo setup")
        sparse_style = detect_commit_style(sparse_repo)
        assert sparse_style["source"] == "limited-history"
        assert sparse_style["history_sufficient"] is False
        sparse_advice = show_git_workflow_advice(sparse_repo)
        assert sparse_advice["commit_policy"]["recommended_style"] == "conventional"
        assert sparse_advice["branch_policy"]["pattern"].startswith("task/")
        progress("git workflow, commit style, branch policy, and worktree flows")

        porcelain_repo = temp_root / "porcelain-repo"
        porcelain_repo.mkdir()
        subprocess.run(["git", "init"], cwd=porcelain_repo, check=True, capture_output=True, text=True)
        (porcelain_repo / ".gitignore").write_text("node_modules/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=porcelain_repo, check=True, capture_output=True, text=True)
        _git_commit(porcelain_repo, "Add ignore rules")
        (porcelain_repo / ".gitignore").write_text("node_modules/\ncoverage/\n")
        porcelain_state = inspect_git_state(porcelain_repo)
        assert porcelain_state["changed_files"][0]["path"] == ".gitignore"
        assert ".gitignore" in porcelain_state["unstaged_files"]

        git_flow_repo = temp_root / "git-flow-repo"
        git_flow_repo.mkdir()
        subprocess.run(["git", "init"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "AgentiUX"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "agentiux@example.com"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        (git_flow_repo / "README.md").write_text("# Git Flow Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "chore: bootstrap repo"], cwd=git_flow_repo, check=True, capture_output=True, text=True)
        init_workspace(git_flow_repo)
        create_task(git_flow_repo, title="Update git note", objective="Add an operational note for the repository.")
        (git_flow_repo / "notes.md").write_text("ops note\n")
        git_state = inspect_git_state(git_flow_repo)
        assert "notes.md" in git_state["untracked_files"]
        git_plan = plan_git_change(git_flow_repo)
        assert git_plan["workspace_context"]["context_type"] == "task"
        assert git_plan["resolved_summary"] == "Add an operational note for the repository."
        assert git_plan["branch_action"] == "create_and_switch"
        assert git_plan["worktree_action"] == "current_checkout_ok"
        assert git_plan["suggested_branch_name"].startswith("task/")
        branch_result = create_git_branch(git_flow_repo, git_plan["suggested_branch_name"])
        assert branch_result["status"] == "created"
        stage_result = stage_git_files(git_flow_repo, ["notes.md"])
        assert "notes.md" in stage_result["git_state"]["staged_files"]
        commit_result = create_git_commit(git_flow_repo, git_plan["suggested_commit_message"])
        assert commit_result["commit_hash"]
        assert inspect_git_state(git_flow_repo)["summary_counts"]["changed_files"] == 0

        worktree_repo = temp_root / "worktree-repo"
        worktree_repo.mkdir()
        subprocess.run(["git", "init"], cwd=worktree_repo, check=True, capture_output=True, text=True)
        (worktree_repo / "README.md").write_text("# Worktree Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=worktree_repo, check=True, capture_output=True, text=True)
        _git_commit(worktree_repo, "chore: bootstrap worktree repo")
        init_workspace(worktree_repo)
        create_workstream(worktree_repo, title="Dashboard revamp", scope_summary="Ship the dashboard revamp.")
        worktree_plan = plan_git_change(worktree_repo)
        assert worktree_plan["workspace_context"]["context_type"] == "workstream"
        assert worktree_plan["worktree_action"] == "create_linked_worktree"
        assert worktree_plan["suggested_branch_name"].startswith("feature/")
        worktree_listing = list_git_worktrees(worktree_repo)
        assert worktree_listing["worktree_count"] == 1
        created_worktree = create_git_worktree(
            worktree_repo,
            worktree_plan["suggested_worktree_path"],
            worktree_plan["suggested_branch_name"],
        )
        assert created_worktree["branch_name"] == worktree_plan["suggested_branch_name"]
        assert created_worktree["worktree_path"] == worktree_plan["suggested_worktree_path"]
        assert created_worktree["worktree_state"]["worktree_count"] == 2
        linked_state = inspect_git_state(created_worktree["worktree_path"])
        assert linked_state["worktree"]["is_linked_worktree"] is True
        assert linked_state["current_branch"] == worktree_plan["suggested_branch_name"]
        assert linked_state["summary_counts"]["changed_files"] == 0

        audit_target = temp_root / "audit-target"
        audit_target.mkdir()
        (audit_target / "package.json").write_text(
            json.dumps(
                {
                    "name": "audit-target",
                    "dependencies": {
                        "@nestjs/core": "^11.0.0",
                        "pg": "^9.0.0",
                    },
                },
                indent=2,
            )
            + "\n"
        )
        init_workspace(audit_target)
        audit = audit_repository(audit_target)
        assert audit["initialized"] is True
        assert audit["gaps"]
        assert read_current_audit(audit_target)["audit_id"] == audit["audit_id"]
        upgrade = show_upgrade_plan(audit_target)
        assert upgrade["status"] == "draft"
        applied = apply_upgrade_plan(audit_target, confirmed=True)
        assert applied["status"] == "applied"
        assert applied["created_workstream_ids"]
        assert applied["created_task_ids"]
        assert read_upgrade_plan(audit_target)["plan_id"] == applied["plan_id"]

        starter_bin = temp_root / "starter-bin"
        starter_bin.mkdir()
        write_fake_bootstrap_tools(starter_bin)
        os.environ["PATH"] = f"{starter_bin}{os.pathsep}{os.environ['PATH']}"
        starter_root = temp_root / "starters"
        starter_root.mkdir()
        starter_presets = ["next-web", "expo-mobile", "nestjs-api", "rust-service", "nx-fullstack"]
        created_starters = []
        for preset in starter_presets:
            run = create_starter(preset, starter_root, f"{preset}-demo")
            created_starters.append(run)
            assert run["status"] == "passed"
            project_root = Path(run["project_root"])
            assert project_root.exists()
            starter_workspace_paths = workspace_paths(project_root)
            assert not Path(starter_workspace_paths["workspace_state"]).exists()
            assert starter_workspace_paths["verification_recipes"] == ""
            _assert_no_default_origin(run["summary"])
        assert list_starter_runs(limit=None)["run_count"] >= len(starter_presets)
        progress("starter creation and starter-run bookkeeping")

        youtrack_workspace = temp_root / "youtrack-workspace"
        youtrack_workspace.mkdir()
        _seed_workspace(youtrack_workspace)
        subprocess.run(["git", "init"], cwd=youtrack_workspace, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=youtrack_workspace, check=True, capture_output=True, text=True)
        _git_commit(youtrack_workspace, "feat: bootstrap youtrack workspace")
        init_workspace(youtrack_workspace)

        with FakeYouTrackServer() as fake_youtrack:
            connected = connect_youtrack(
                youtrack_workspace,
                base_url=fake_youtrack.base_url or "",
                token=fake_youtrack.token,
                label="Primary tracker",
                connection_id="primary-tracker",
                project_scope="SL",
                default=True,
            )
            assert connected["connection"]["status"] == "connected"
            assert connected["field_catalog"]["field_mapping"]["priority"]
            redacted_connections = list_youtrack_connections(youtrack_workspace)
            serialized_connections = json.dumps(redacted_connections)
            assert fake_youtrack.token not in serialized_connections
            assert '"token":' not in serialized_connections
            assert redacted_connections["items"][0]["auth_mode"] == "permanent_token"
            secret_path = Path(workspace_paths(youtrack_workspace)["youtrack_secrets_dir"]) / "primary-tracker.json"
            assert secret_path.exists()
            if os.name != "nt":
                assert (secret_path.stat().st_mode & 0o777) == 0o600

            paged_search = search_youtrack_issues(
                youtrack_workspace,
                query_text="assignee: me",
                connection_id="primary-tracker",
                page_size=2,
                shortlist_size=2,
            )["search_session"]
            assert paged_search["result_count"] == 3
            assert paged_search["result_count_exact"] is True
            assert paged_search["page_cursor"]["has_more"] is True
            assert len(paged_search["shortlist_page"]["items"]) == 2

            search_session = search_youtrack_issues(
                youtrack_workspace,
                query_text="assignee: me",
                connection_id="primary-tracker",
                page_size=3,
                shortlist_size=3,
            )["search_session"]
            assert search_session["shortlist"]
            assert search_session["result_count"] == 3
            assert search_session["result_count_exact"] is True
            assert all(item["issue_key"].startswith("SL-") for item in search_session["shortlist"])
            assert search_session["shortlist"][0]["issue_entity_id"].startswith("2-")
            assert isinstance(search_session["shortlist"][0]["comments"], list)
            assert isinstance(search_session["shortlist"][0]["recent_activities"], list)
            assert isinstance(search_session["shortlist"][0]["issue_links"], list)
            assert search_session["shortlist"][0]["link_summary"]["linked_issue_count"] >= 1
            rich_context_issue = next(item for item in search_session["shortlist"] if item["issue_key"] == "SL-4591")
            assert isinstance(rich_context_issue["external_references"], list)
            assert rich_context_issue["external_reference_overview"]["link_count"] >= 2
            assert any(item["classification"] == "openable_text" for item in rich_context_issue["external_references"])
            assert any(item["classification"] == "admin_or_auth_like" for item in rich_context_issue["external_references"])
            assert any(item.get("tracker_issue_key") == "SL-4592" for item in rich_context_issue["external_references"])
            assert isinstance(rich_context_issue["related_issue_summaries"], list)
            assert any(item.get("issue_key") == "SL-4592" for item in rich_context_issue["related_issue_summaries"])
            assert rich_context_issue["ticket_overview"]["external_reference_count"] >= 2
            assert rich_context_issue["ticket_overview"]["related_issue_count"] >= 1
            assert rich_context_issue["ticket_overview"]["comment_count"] >= 0
            queue = show_youtrack_issue_queue(youtrack_workspace, search_session_id=search_session["session_id"])
            assert queue["search_session"]["session_id"] == search_session["session_id"]
            assert queue["connection"]["connection_id"] == "primary-tracker"
            youtrack_mcp = _call_mcp(
                plugin_root / "scripts" / "agentiux_dev_mcp.py",
                {
                    "jsonrpc": "2.0",
                    "id": 140,
                    "method": "tools/call",
                    "params": {
                        "name": "show_youtrack_connections",
                        "arguments": {"workspacePath": str(youtrack_workspace)},
                    },
                },
            )
            assert youtrack_mcp["result"]["structuredContent"]["items"][0]["connection_id"] == "primary-tracker"

            selected_issue_ids = [item["issue_id"] for item in search_session["shortlist"][:3]]
            proposed_plan = propose_youtrack_workstream_plan(
                youtrack_workspace,
                search_session_id=search_session["session_id"],
                selected_issue_ids=selected_issue_ids,
                workstream_title="YouTrack checkout queue",
            )["plan"]
            ordered_issue_ids = proposed_plan["selection_analysis"]["ordered_issue_ids"]
            task_proposals_by_issue_key = {
                item["issue_key"]: item
                for item in proposed_plan["task_proposals"]
                if item.get("issue_key")
            }
            assert proposed_plan["task_proposals"]
            assert len(proposed_plan["stages"]) >= 2
            assert proposed_plan["status"] == "needs_user_confirmation"
            assert set(ordered_issue_ids) == {"SL-4591", "SL-4592", "SL-4593"}
            assert ordered_issue_ids.index("SL-4591") < ordered_issue_ids.index("SL-4592")
            assert any(
                edge["from_issue_id"] == "SL-4591" and edge["to_issue_id"] == "SL-4592"
                for edge in proposed_plan["selection_analysis"]["dependency_edges"]
            )
            assert proposed_plan["task_proposals"][0]["description"]
            assert isinstance(proposed_plan["task_proposals"][0]["work_items"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["comments"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["recent_activities"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["issue_links"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["link_summary"], dict)
            assert isinstance(proposed_plan["task_proposals"][0]["external_references"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["external_reference_overview"], dict)
            assert isinstance(proposed_plan["task_proposals"][0]["related_issue_summaries"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["related_issue_overview"], dict)
            assert isinstance(proposed_plan["task_proposals"][0]["plan_link_analysis"], dict)
            assert proposed_plan["task_proposals"][0]["ticket_overview"]["work_item_count"] >= 0
            assert proposed_plan["task_proposals"][0]["external_issue"]["description"]
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["work_items"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["comments"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["recent_activities"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["issue_links"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["external_references"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["external_reference_overview"], dict)
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["related_issue_summaries"], list)
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["related_issue_overview"], dict)
            assert isinstance(proposed_plan["task_proposals"][0]["external_issue"]["plan_link_analysis"], dict)
            assert all(isinstance(stage.get("planning_signals"), dict) for stage in proposed_plan["stages"])
            assert isinstance(task_proposals_by_issue_key["SL-4593"]["plan_link_analysis"]["selected_duplicate_of_issue_ids"], list)
            assert not [
                item for item in list_tasks(youtrack_workspace)["items"] if (item.get("external_issue") or {}).get("issue_key")
            ]
            proposed_plan_mcp = _call_mcp(
                plugin_root / "scripts" / "agentiux_dev_mcp.py",
                {
                    "jsonrpc": "2.0",
                    "id": 141,
                    "method": "tools/call",
                    "params": {
                        "name": "show_youtrack_issue_queue",
                        "arguments": {
                            "workspacePath": str(youtrack_workspace),
                            "searchSessionId": search_session["session_id"],
                        },
                    },
                },
            )
            assert proposed_plan_mcp["result"]["structuredContent"]["search_session"]["session_id"] == search_session["session_id"]
            try:
                apply_youtrack_workstream_plan(
                    youtrack_workspace,
                    plan_id=proposed_plan["plan_id"],
                    confirmed=False,
                )
            except ValueError as exc:
                assert "confirmed=True" in str(exc)
            else:
                raise AssertionError("Expected YouTrack plan apply without confirmation to fail.")

            applied_plan = apply_youtrack_workstream_plan(
                youtrack_workspace,
                plan_id=proposed_plan["plan_id"],
                confirmed=True,
            )["plan"]
            assert applied_plan["status"] == "applied"
            assert applied_plan["created_task_ids"]
            assert current_workstream(youtrack_workspace)["source_context"]["plan_id"] == proposed_plan["plan_id"]
            workstream_count_before_reapply = len(list_workstreams(youtrack_workspace)["items"])
            task_count_before_reapply = len(_read_json_file(Path(workspace_paths(youtrack_workspace)["tasks_index"]))["items"])
            plan_path = Path(workspace_paths(youtrack_workspace)["youtrack_plans_dir"]) / f"{proposed_plan['plan_id']}.json"
            plan_payload = _read_json_file(plan_path)
            plan_payload["status"] = "needs_user_confirmation"
            plan_payload["applied_workstream_id"] = None
            plan_payload["created_task_ids"] = []
            _write_json_file(plan_path, plan_payload)
            recovered_apply = apply_youtrack_workstream_plan(
                youtrack_workspace,
                plan_id=proposed_plan["plan_id"],
                confirmed=True,
            )
            assert recovered_apply["plan"]["status"] == "applied"
            assert recovered_apply["plan"]["applied_workstream_id"] == applied_plan["applied_workstream_id"]
            assert len(list_workstreams(youtrack_workspace)["items"]) == workstream_count_before_reapply
            assert len(_read_json_file(Path(workspace_paths(youtrack_workspace)["tasks_index"]))["items"]) == task_count_before_reapply

            for stale_task in [
                item for item in list_tasks(youtrack_workspace)["items"] if (item.get("external_issue") or {}).get("issue_key")
            ]:
                stale_task_path = Path(workspace_paths(youtrack_workspace, task_id=stale_task["task_id"])["current_task_record"])
                stale_task_payload = _read_json_file(stale_task_path)
                thin_external_issue = {
                    "connection_id": stale_task_payload["external_issue"]["connection_id"],
                    "issue_id": stale_task_payload["external_issue"]["issue_id"],
                    "issue_key": stale_task_payload["external_issue"]["issue_key"],
                    "issue_url": stale_task_payload["external_issue"]["issue_url"],
                    "summary": stale_task_payload["external_issue"]["summary"],
                    "youtrack_estimate_minutes": stale_task_payload["external_issue"].get("youtrack_estimate_minutes"),
                    "youtrack_spent_minutes": stale_task_payload["external_issue"].get("youtrack_spent_minutes"),
                }
                stale_task_payload["external_issue"] = thin_external_issue
                _write_json_file(stale_task_path, stale_task_payload)
            stale_tasks_index = _read_json_file(Path(workspace_paths(youtrack_workspace)["tasks_index"]))
            for item in stale_tasks_index["items"]:
                external_issue = item.get("external_issue") or {}
                if not external_issue.get("issue_key"):
                    continue
                item["external_issue"] = {
                    "connection_id": external_issue["connection_id"],
                    "issue_id": external_issue["issue_id"],
                    "issue_key": external_issue["issue_key"],
                    "issue_url": external_issue["issue_url"],
                    "summary": external_issue["summary"],
                    "youtrack_estimate_minutes": external_issue.get("youtrack_estimate_minutes"),
                    "youtrack_spent_minutes": external_issue.get("youtrack_spent_minutes"),
                }
            _write_json_file(Path(workspace_paths(youtrack_workspace)["tasks_index"]), stale_tasks_index)

            current_workstream_payload = current_workstream(youtrack_workspace)
            workstreams_index_path = Path(workspace_paths(youtrack_workspace)["workstreams_index"])
            workstreams_index_payload = _read_json_file(workstreams_index_path)
            for item in workstreams_index_payload["items"]:
                if item["workstream_id"] == current_workstream_payload["workstream_id"]:
                    item["source_context"] = {
                        "provider": "youtrack",
                        "connection_id": proposed_plan["connection_id"],
                        "search_session_id": proposed_plan["search_session_id"],
                        "plan_id": proposed_plan["plan_id"],
                    }
                    break
            _write_json_file(workstreams_index_path, workstreams_index_payload)

            refreshed_plan = propose_youtrack_workstream_plan(
                youtrack_workspace,
                search_session_id=search_session["session_id"],
                selected_issue_ids=selected_issue_ids,
                workstream_title="YouTrack checkout queue",
            )["plan"]
            refreshed_apply = apply_youtrack_workstream_plan(
                youtrack_workspace,
                plan_id=refreshed_plan["plan_id"],
                confirmed=True,
                reuse_current_workstream=True,
            )
            assert refreshed_apply["plan"]["applied_workstream_id"] == applied_plan["applied_workstream_id"]
            assert len(list_workstreams(youtrack_workspace)["items"]) == workstream_count_before_reapply
            assert len(_read_json_file(Path(workspace_paths(youtrack_workspace)["tasks_index"]))["items"]) == task_count_before_reapply
            assert current_workstream(youtrack_workspace)["source_context"]["plan_id"] == refreshed_plan["plan_id"]
            refreshed_tasks = [
                item for item in list_tasks(youtrack_workspace)["items"] if (item.get("external_issue") or {}).get("issue_key")
            ]
            assert refreshed_tasks[0]["external_issue"]["description"]
            assert isinstance(refreshed_tasks[0]["external_issue"]["comments"], list)
            assert isinstance(refreshed_tasks[0]["external_issue"]["recent_activities"], list)
            assert isinstance(refreshed_tasks[0]["external_issue"]["issue_links"], list)
            assert isinstance(refreshed_tasks[0]["external_issue"]["external_references"], list)
            assert isinstance(refreshed_tasks[0]["external_issue"]["related_issue_summaries"], list)
            assert isinstance(refreshed_tasks[0]["external_issue"]["plan_link_analysis"], dict)

            yt_snapshot = dashboard_snapshot(youtrack_workspace)
            assert yt_snapshot["workspace_cockpit"]["integrations"]["youtrack"]["summary"]["connection_count"] == 1
            assert yt_snapshot["workspace_cockpit"]["integrations"]["youtrack"]["current_plan"]["plan_id"] == refreshed_plan["plan_id"]
            assert yt_snapshot["workspace_cockpit"]["integrations"]["youtrack"]["current_workstream_issues"]["items"]
            issue_card = next(
                item
                for item in yt_snapshot["workspace_cockpit"]["integrations"]["youtrack"]["current_workstream_issues"]["items"]
                if item["issue_key"] == "SL-4591"
            )
            assert isinstance(issue_card["hover_summary"], dict)
            assert issue_card["hover_summary"]["excerpt"]
            assert issue_card["hover_summary"]["source"] in {
                "description",
                "external_reference",
                "comment",
                "related_issue",
                "summary",
            }
            assert "SL-4592" in issue_card["hover_summary"]["related_issue_keys"]

            imported_tasks = [
                item for item in list_tasks(youtrack_workspace)["items"] if (item.get("external_issue") or {}).get("issue_key")
            ]
            assert imported_tasks
            assert imported_tasks[0]["external_issue"]["description"]
            assert isinstance(imported_tasks[0]["external_issue"]["work_items"], list)
            assert isinstance(imported_tasks[0]["external_issue"]["comments"], list)
            assert isinstance(imported_tasks[0]["external_issue"]["recent_activities"], list)
            assert isinstance(imported_tasks[0]["external_issue"]["issue_links"], list)
            assert isinstance(imported_tasks[0]["external_issue"]["external_references"], list)
            assert isinstance(imported_tasks[0]["external_issue"]["related_issue_summaries"], list)
            assert isinstance(imported_tasks[0]["external_issue"]["plan_link_analysis"], dict)
            activated_task = switch_task(youtrack_workspace, imported_tasks[0]["task_id"])
            assert activated_task["status"] == "active"
            issue_key = activated_task["external_issue"]["issue_key"]
            activated_task_path = Path(workspace_paths(youtrack_workspace, task_id=activated_task["task_id"])["current_task_record"])
            activated_task_payload = _read_json_file(activated_task_path)
            activated_started_at = (datetime.now(timezone.utc) - timedelta(minutes=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            activated_task_payload["time_tracking"]["active_session_started_at"] = activated_started_at
            activated_task_payload["time_tracking"]["active_session_id"] = "session-a"
            _write_json_file(activated_task_path, activated_task_payload)
            tasks_index_path = Path(workspace_paths(youtrack_workspace)["tasks_index"])
            tasks_index_payload = _read_json_file(tasks_index_path)
            for item in tasks_index_payload.get("items", []):
                if item.get("task_id") == activated_task["task_id"]:
                    item.setdefault("time_tracking", {})["active_session_started_at"] = activated_started_at
                    item["time_tracking"]["active_session_id"] = "session-a"
                    break
            _write_json_file(tasks_index_path, tasks_index_payload)

            (youtrack_workspace / "issue-change.txt").write_text("linked issue change\n")
            subprocess.run(["git", "add", "issue-change.txt"], cwd=youtrack_workspace, check=True, capture_output=True, text=True)
            try:
                create_git_commit(youtrack_workspace, "fix: missing linked issue prefix")
            except ValueError as exc:
                assert issue_key in str(exc)
            else:
                raise AssertionError("Expected linked task commit without issue prefix to fail.")
            linked_commit_message = suggest_commit_message(
                youtrack_workspace,
                "Fix linked issue flow",
                files=["issue-change.txt"],
            )["suggested_message"]
            assert linked_commit_message.startswith(f"{issue_key} ")
            linked_commit = create_git_commit(youtrack_workspace, linked_commit_message)
            assert linked_commit["workspace_context"]["issue_key"] == issue_key
            linked_task = read_task(youtrack_workspace, task_id=activated_task["task_id"])
            assert linked_task["latest_commit"]["commit_hash"] == linked_commit["commit_hash"]
            closed_linked_task = close_task(youtrack_workspace, task_id=activated_task["task_id"])
            assert closed_linked_task["issue_ledger"]["linked_task_ids"]
            assert closed_linked_task["issue_ledger"]["latest_snapshot"]["issue_key"] == issue_key
            assert closed_linked_task["time_summary"]["aggregate_issue_minutes"] == closed_linked_task["issue_ledger"]["codex_total_minutes"]

            rerun_task = create_task(
                youtrack_workspace,
                title=f"{issue_key} rerun verification",
                objective="Re-open linked issue for regression follow-up",
                linked_workstream_id=current_workstream(youtrack_workspace)["workstream_id"],
                stage_id=closed_linked_task["stage_id"],
                external_issue=closed_linked_task["external_issue"],
                codex_estimate_minutes=closed_linked_task["time_summary"]["codex_estimate_minutes"],
                task_id=f"{issue_key.lower()}-rerun",
                make_current=True,
            )["task"]
            rerun_task_path = Path(workspace_paths(youtrack_workspace, task_id=rerun_task["task_id"])["current_task_record"])
            rerun_task_payload = _read_json_file(rerun_task_path)
            rerun_started_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            rerun_task_payload["time_tracking"]["active_session_started_at"] = rerun_started_at
            rerun_task_payload["time_tracking"]["active_session_id"] = "session-b"
            _write_json_file(rerun_task_path, rerun_task_payload)
            tasks_index_payload = _read_json_file(tasks_index_path)
            for item in tasks_index_payload.get("items", []):
                if item.get("task_id") == rerun_task["task_id"]:
                    item.setdefault("time_tracking", {})["active_session_started_at"] = rerun_started_at
                    item["time_tracking"]["active_session_id"] = "session-b"
                    break
            _write_json_file(tasks_index_path, tasks_index_payload)
            rerun_closed_task = close_task(youtrack_workspace, task_id=rerun_task["task_id"])
            assert set(entry["session_id"] for entry in rerun_closed_task["issue_ledger"]["time_entries"]) >= {"session-a", "session-b"}
            assert set(rerun_closed_task["issue_ledger"]["linked_task_ids"]) >= {
                activated_task["task_id"],
                rerun_task["task_id"],
            }
            assert rerun_closed_task["issue_ledger"]["codex_total_minutes"] >= 12

            gui_launch_process = subprocess.run(
                ["python3", str(plugin_root / "scripts" / "agentiux_dev_gui.py"), "launch", "--workspace", str(youtrack_workspace)],
                text=True,
                capture_output=True,
                env=os.environ.copy(),
                check=False,
            )
            if gui_launch_process.returncode == 0:
                gui_launch = json.loads(gui_launch_process.stdout)
                try:
                    encoded_workspace = urllib.parse.quote(str(youtrack_workspace.resolve()), safe="")
                    encoded_memory_workspace = urllib.parse.quote(str(workspace.resolve()), safe="")
                    connections_payload = _http_json(f"{gui_launch['url']}/api/youtrack/connections?workspace={encoded_workspace}")
                    assert connections_payload["default_connection_id"] == "primary-tracker"
                    serialized_gui_connections = json.dumps(connections_payload)
                    assert fake_youtrack.token not in serialized_gui_connections
                    assert '"token":' not in serialized_gui_connections
                    created_secondary = _http_json(
                        f"{gui_launch['url']}/api/youtrack/connections",
                        method="POST",
                        payload={
                            "workspacePath": str(youtrack_workspace.resolve()),
                            "label": "Secondary tracker",
                            "connectionId": "secondary-tracker",
                            "baseUrl": fake_youtrack.base_url,
                            "token": fake_youtrack.token,
                            "projectScope": ["SL"],
                        },
                    )
                    assert created_secondary["created_connection_id"] == "secondary-tracker"
                    tested_secondary = _http_json(
                        f"{gui_launch['url']}/api/youtrack/connections/secondary-tracker/test",
                        method="POST",
                        payload={"workspacePath": str(youtrack_workspace.resolve())},
                    )
                    assert tested_secondary["connection"]["status"] == "connected"
                    _http_json(
                        f"{gui_launch['url']}/api/youtrack/connections",
                        method="PATCH",
                        payload={
                            "workspacePath": str(youtrack_workspace.resolve()),
                            "connectionId": "secondary-tracker",
                            "label": "Secondary tracker updated",
                            "default": True,
                            "testConnection": False,
                        },
                    )
                    updated_connections = _http_json(f"{gui_launch['url']}/api/youtrack/connections?workspace={encoded_workspace}")
                    assert updated_connections["default_connection_id"] == "secondary-tracker"
                    _http_json(
                        f"{gui_launch['url']}/api/youtrack/connections",
                        method="DELETE",
                        payload={"workspacePath": str(youtrack_workspace.resolve()), "connectionId": "secondary-tracker"},
                    )
                    final_connections = _http_json(f"{gui_launch['url']}/api/youtrack/connections?workspace={encoded_workspace}")
                    assert len(final_connections["items"]) == 1
                    resolver_script = temp_root / "auth_resolver_v2.py"
                    resolver_script.write_text(
                        (
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
                        ),
                        encoding="utf-8",
                    )
                    auth_profiles = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profile": {
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
                            "secretPayload": {
                                "login": "qa@example.com",
                                "password": "qa-password",
                            },
                        },
                    )
                    assert auth_profiles["profile"]["profile_id"] == "smoke-auth"
                    resolver_profile = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profile": {
                                "profile_id": "resolver-auth",
                                "label": "Resolver auth",
                                "scope_type": "workspace",
                                "resolver": {
                                    "kind": "command_v2",
                                    "argv": [sys.executable, str(resolver_script)],
                                    "cwd": ".",
                                    "timeout_seconds": 10,
                                },
                                "usage_policy": {
                                    "default_request_mode": "read_only",
                                    "allowed_request_modes": ["read_only"],
                                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                                    "action_tags": ["tag.read"],
                                    "allow_session_persistence": True,
                                    "allow_session_refresh": True,
                                },
                            },
                            "secretPayload": {
                                "login": "reader@example.com",
                                "password": "reader-password",
                                "refresh_token": "profile-refresh-token",
                            },
                        },
                    )
                    assert resolver_profile["profile"]["resolver"]["kind"] == "command_v2"
                    mutating_profile = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profile": {
                                "profile_id": "mutating-auth",
                                "label": "Mutating auth",
                                "scope_type": "workspace",
                                "resolver": {
                                    "kind": "command_v2",
                                    "argv": [sys.executable, str(resolver_script)],
                                    "cwd": ".",
                                    "timeout_seconds": 10,
                                },
                                "usage_policy": {
                                    "default_request_mode": "read_only",
                                    "allowed_request_modes": ["read_only", "mutating"],
                                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                                    "action_tags": ["tag.read", "tag.write"],
                                    "allow_session_persistence": True,
                                    "allow_session_refresh": True,
                                },
                            },
                            "secretPayload": {
                                "login": "writer@example.com",
                                "password": "writer-password",
                                "refresh_token": "writer-refresh-token",
                            },
                        },
                    )
                    assert mutating_profile["profile"]["usage_policy"]["allowed_request_modes"] == ["read_only", "mutating"]
                    binding_profile = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profile": {
                                "profile_id": "binding-auth",
                                "label": "Binding auth",
                                "scope_type": "workspace",
                                "resolver": {
                                    "kind": "command_v2",
                                    "argv": [sys.executable, str(resolver_script)],
                                    "cwd": ".",
                                    "timeout_seconds": 10,
                                },
                                "usage_policy": {
                                    "default_request_mode": "read_only",
                                    "allowed_request_modes": ["read_only"],
                                    "allowed_surface_modes": ["dashboard", "verification", "mcp", "cli", "resolver_only"],
                                    "action_tags": ["tag.read"],
                                    "allow_session_persistence": True,
                                    "allow_session_refresh": True,
                                },
                            },
                            "secretPayload": {
                                "login": "binding@example.com",
                                "password": "binding-password",
                                "refresh_token": "binding-refresh-token",
                            },
                        },
                    )
                    assert binding_profile["profile"]["profile_id"] == "binding-auth"
                    auth_listing = _http_json(f"{gui_launch['url']}/api/auth/profiles?workspace={encoded_memory_workspace}")
                    assert auth_listing["counts"]["total"] >= 4
                    assert "qa-password" not in json.dumps(auth_listing)
                    auth_preview = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles/resolve",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profileId": "smoke-auth",
                        },
                    )
                    assert auth_preview["artifact"]["artifact_type"] == "credentials"
                    auth_sessions_after_profile_resolve = _http_json(
                        f"{gui_launch['url']}/api/auth/sessions?workspace={encoded_memory_workspace}"
                    )
                    assert auth_sessions_after_profile_resolve["counts"]["total"] >= 1
                    assert "qa-password" not in json.dumps(auth_sessions_after_profile_resolve)
                    assert "initial-access" not in json.dumps(auth_sessions_after_profile_resolve)
                    manual_access_expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    manual_refresh_expires = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    resolver_session = _http_json(
                        f"{gui_launch['url']}/api/auth/sessions",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "session": {
                                "profile_id": "resolver-auth",
                                "source_kind": "manual",
                                "request_mode": "read_only",
                                "action_tags": ["tag.read"],
                                "summary": {"seed_kind": "token_bundle"},
                            },
                            "secretPayload": {
                                "access_token": "seed-access",
                                "refresh_token": "seed-refresh",
                                "token_type": "Bearer",
                                "access_expires_at": manual_access_expires,
                                "refresh_expires_at": manual_refresh_expires,
                                "subject_ref": "dashboard-seed",
                            },
                        },
                    )
                    resolver_session_id = resolver_session["session"]["session_id"]
                    binding_session = _http_json(
                        f"{gui_launch['url']}/api/auth/sessions",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "session": {
                                "profile_id": "binding-auth",
                                "source_kind": "manual",
                                "request_mode": "read_only",
                                "action_tags": ["tag.read"],
                                "session_binding": {
                                    "primary_ref": "backend.shared",
                                    "refs": [
                                        "backend.shared",
                                        "https://neutral-a.example.test",
                                        "https://neutral-b.example.test",
                                    ],
                                },
                                "summary": {"seed_kind": "token_bundle"},
                            },
                            "secretPayload": {
                                "access_token": "binding-seed-access",
                                "refresh_token": "binding-seed-refresh",
                                "token_type": "Bearer",
                                "access_expires_at": manual_access_expires,
                                "refresh_expires_at": manual_refresh_expires,
                                "subject_ref": "binding-dashboard-seed",
                            },
                        },
                    )
                    binding_session_id = binding_session["session"]["session_id"]
                    session_listing = _http_json(f"{gui_launch['url']}/api/auth/sessions?workspace={encoded_memory_workspace}")
                    assert session_listing["counts"]["total"] >= 3
                    assert "seed-access" not in json.dumps(session_listing)
                    assert "seed-refresh" not in json.dumps(session_listing)
                    resolver_session_detail = _http_json(
                        f"{gui_launch['url']}/api/auth/sessions/{urllib.parse.quote(resolver_session_id)}?workspace={encoded_memory_workspace}"
                    )
                    assert resolver_session_detail["session"]["session_id"] == resolver_session_id
                    assert "seed-access" not in json.dumps(resolver_session_detail)
                    resolver_preview = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles/resolve",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profileId": "resolver-auth",
                            "requestMode": "read_only",
                            "actionTags": ["tag.read"],
                            "preferCached": True,
                        },
                    )
                    assert resolver_preview["artifact"]["artifact_type"] == "token_bundle"
                    assert resolver_preview["resolution_reason"] == "reuse"
                    assert resolver_preview["session"]["session_id"] == resolver_session_id
                    refreshed_preview = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles/resolve",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profileId": "resolver-auth",
                            "requestMode": "read_only",
                            "actionTags": ["tag.read"],
                            "contextOverrides": {"subject_ref": "dashboard-refresh"},
                            "preferCached": False,
                            "forceRefresh": True,
                        },
                    )
                    assert refreshed_preview["artifact"]["artifact_type"] == "token_bundle"
                    assert refreshed_preview["resolution_reason"] == "manual_seed"
                    assert refreshed_preview["session"]["session_id"] == resolver_session_id
                    binding_preview = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles/resolve",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profileId": "binding-auth",
                            "requestMode": "read_only",
                            "actionTags": ["tag.read"],
                            "sessionBinding": {
                                "refs": [
                                    "backend.shared",
                                    "https://neutral-b.example.test",
                                ],
                            },
                            "preferCached": True,
                        },
                    )
                    assert binding_preview["artifact"]["artifact_type"] == "token_bundle"
                    assert binding_preview["resolution_reason"] == "reuse"
                    assert binding_preview["session"]["session_id"] == binding_session_id
                    mutating_session = _http_json(
                        f"{gui_launch['url']}/api/auth/sessions",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "session": {
                                "profile_id": "mutating-auth",
                                "source_kind": "manual",
                                "request_mode": "mutating",
                                "action_tags": ["tag.read", "tag.write"],
                            },
                            "secretPayload": {
                                "access_token": "writer-access",
                                "refresh_token": "writer-refresh",
                                "token_type": "Bearer",
                                "access_expires_at": manual_access_expires,
                                "refresh_expires_at": manual_refresh_expires,
                                "subject_ref": "writer-subject",
                            },
                        },
                    )
                    mutating_session_id = mutating_session["session"]["session_id"]
                    allowed_mutating_preview = _http_json(
                        f"{gui_launch['url']}/api/auth/profiles/resolve",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "profileId": "mutating-auth",
                            "requestMode": "mutating",
                            "actionTags": ["tag.write"],
                        },
                    )
                    assert allowed_mutating_preview["resolution_reason"] == "reuse"
                    assert allowed_mutating_preview["session"]["session_id"] == mutating_session_id
                    try:
                        _http_json(
                            f"{gui_launch['url']}/api/auth/profiles/resolve",
                            method="POST",
                            payload={
                                "workspacePath": str(workspace.resolve()),
                                "profileId": "smoke-auth",
                                "requestMode": "mutating",
                            },
                        )
                        raise AssertionError("Expected mutating auth preview to be rejected for read-only profile")
                    except AssertionError as exc:
                        assert "not allowed" in str(exc)
                    try:
                        _http_json(
                            f"{gui_launch['url']}/api/auth/profiles/resolve",
                            method="POST",
                            payload={
                                "workspacePath": str(workspace.resolve()),
                                "profileId": "mutating-auth",
                                "requestMode": "mutating",
                                "actionTags": ["tag.blocked"],
                            },
                        )
                        raise AssertionError("Expected auth action tag policy rejection")
                    except AssertionError as exc:
                        assert "action_tags" in str(exc)
                    invalidated_session = _http_json(
                        f"{gui_launch['url']}/api/auth/sessions/{urllib.parse.quote(mutating_session_id)}/invalidate",
                        method="POST",
                        payload={"workspacePath": str(workspace.resolve())},
                    )
                    assert invalidated_session["session"]["status"] == "invalidated"
                    removed_session = _http_json(
                        f"{gui_launch['url']}/api/auth/sessions/{urllib.parse.quote(mutating_session_id)}",
                        method="DELETE",
                        payload={"workspacePath": str(workspace.resolve())},
                    )
                    assert removed_session["removed_session_id"] == mutating_session_id
                    _http_json(
                        f"{gui_launch['url']}/api/project-notes",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "note": {
                                "note_id": "bootstrap-auth-note",
                                "title": "Bootstrap auth note",
                                "tags": ["bootstrap", "auth"],
                                "pin_state": "pinned",
                                "source": "web",
                                "body_markdown": "Temporary bootstrap URL is required for auth smoke.",
                            },
                        },
                    )
                    _http_json(
                        f"{gui_launch['url']}/api/project-notes",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "note": {
                                "note_id": "archived-visual-note",
                                "title": "Archived visual note",
                                "tags": ["visual", "history"],
                                "source": "web",
                                "body_markdown": "Visual review used to require repeated manual rechecks.",
                            },
                        },
                    )
                    _http_json(
                        f"{gui_launch['url']}/api/project-notes/archived-visual-note/archive",
                        method="POST",
                        payload={"workspacePath": str(workspace.resolve())},
                    )
                    note_listing = _http_json(f"{gui_launch['url']}/api/project-notes?workspace={encoded_memory_workspace}")
                    assert note_listing["counts"]["pinned"] >= 1
                    searched_notes = _http_json(
                        f"{gui_launch['url']}/api/project-notes/search?workspace={encoded_memory_workspace}&query={urllib.parse.quote('temporary bootstrap url')}"
                    )
                    assert any(item["note_id"] == "bootstrap-auth-note" for item in searched_notes["matches"])
                    created_learning = _http_json(
                        f"{gui_launch['url']}/api/learnings",
                        method="POST",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "entry": {
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
                        },
                    )
                    assert created_learning["entry"]["entry_id"] == "visual-review-learning"
                    updated_learning = _http_json(
                        f"{gui_launch['url']}/api/learnings/visual-review-learning",
                        method="PATCH",
                        payload={
                            "workspacePath": str(workspace.resolve()),
                            "updates": {
                                "status": "resolved",
                                "fix_applied": "Added stronger visual checks and context, then reran verification.",
                            },
                        },
                    )
                    assert updated_learning["entry"]["status"] == "resolved"
                    analytics_snapshot = _http_json(f"{gui_launch['url']}/api/analytics?workspace={encoded_memory_workspace}")
                    assert analytics_snapshot["learning_counts"]["resolved"] >= 1
                    learning_listing = _http_json(f"{gui_launch['url']}/api/learnings?workspace={encoded_memory_workspace}")
                    assert any(item["entry_id"] == "visual-review-learning" for item in learning_listing["items"])
                finally:
                    stop_gui()
            else:
                assert "Operation not permitted" in gui_launch_process.stderr or "PermissionError" in gui_launch_process.stderr
        progress("YouTrack connection management, import, planning, and GUI mutation flows")

        overview = list_workspaces()
        assert overview["workspace_count"] >= 1
        stats = plugin_stats()
        assert stats["reference_boards"] >= 1
        assert stats["active_verification_runs"] == 0
        assert stats["plugin_platform_workspaces"] >= 1
        assert stats["starter_runs"] >= len(starter_presets)
        snapshot = dashboard_snapshot(workspace)
        assert snapshot["schema_version"] == 2
        assert snapshot["starter_runs"]["run_count"] >= len(starter_presets)
        assert snapshot["workspace_cockpit"]["state_kind"] == "initialized"
        assert snapshot["overview"]["preferred_workspace_path"] == str(workspace.resolve())
        assert snapshot["workspace_cockpit"]["plan"]["design_state"]["current_handoff_status"] == "ready"
        assert snapshot["workspace_cockpit"]["quality"]["latest_run"]["run_id"] == suite_run["run_id"]
        assert snapshot["workspace_cockpit"]["quality"]["selection"]["selected_suite"] == "full"
        assert snapshot["workspace_cockpit"]["quality"]["events"]
        assert snapshot["workspace_cockpit"]["plan"]["workstreams"]
        assert snapshot["workspace_cockpit"]["plan"]["task_buckets"]

        workspace_auth_profiles = show_auth_profiles(workspace)
        assert workspace_auth_profiles["counts"]["total"] >= 1
        assert "qa-password" not in json.dumps(workspace_auth_profiles)
        workspace_auth_sessions = list_auth_sessions(workspace)
        assert workspace_auth_sessions["counts"]["total"] >= 2
        assert workspace_auth_sessions["counts"]["read_only"] >= 1
        assert "seed-access" not in json.dumps(workspace_auth_sessions)
        resolver_session_record = get_auth_session(workspace, resolver_session_id)
        assert resolver_session_record["session"]["session_id"] == resolver_session_id
        assert "refresh_token" not in json.dumps(resolver_session_record)
        refreshed_resolve = resolve_auth_profile(
            workspace,
            profile_id="resolver-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            context_overrides={"subject_ref": "direct-refresh"},
            prefer_cached=False,
            force_refresh=True,
            surface_mode="cli",
        )
        assert refreshed_resolve["artifact"]["artifact_type"] == "token_bundle"
        assert refreshed_resolve["resolution_reason"] == "manual_seed"
        bound_resolve = resolve_auth_profile(
            workspace,
            profile_id="binding-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            session_binding={
                "refs": [
                    "backend.shared",
                    "https://neutral-a.example.test",
                ]
            },
            surface_mode="cli",
        )
        assert bound_resolve["resolution_reason"] == "reuse"
        assert bound_resolve["session"]["session_id"] == binding_session_id
        isolated_resolve = resolve_auth_profile(
            workspace,
            profile_id="binding-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            session_binding={
                "primary_ref": "backend.isolated",
                "refs": [
                    "backend.isolated",
                    "https://isolated.example.test",
                ],
            },
            surface_mode="cli",
        )
        assert isolated_resolve["artifact"]["artifact_type"] == "token_bundle"
        assert isolated_resolve["resolution_reason"] == "initial"
        assert isolated_resolve["session"]["session_id"] != binding_session_id
        assert isolated_resolve["session"]["session_binding"]["primary_ref"] == "backend.isolated"
        isolated_reuse = resolve_auth_profile(
            workspace,
            profile_id="binding-auth",
            request_mode="read_only",
            action_tags=["tag.read"],
            session_binding={
                "refs": [
                    "backend.isolated",
                    "https://isolated.example.test",
                ]
            },
            surface_mode="cli",
        )
        assert isolated_reuse["resolution_reason"] == "reuse"
        assert isolated_reuse["session"]["session_id"] == isolated_resolve["session"]["session_id"]
        binding_sessions = list_auth_sessions(workspace, profile_id="binding-auth")
        assert binding_sessions["counts"]["total"] >= 2
        temporary_session = write_auth_session(
            workspace,
            {
                "profile_id": "resolver-auth",
                "source_kind": "manual",
                "request_mode": "read_only",
                "action_tags": ["tag.read"],
            },
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
        assert invalidated_session["session"]["status"] == "invalidated"
        removed_session = remove_auth_session(workspace, temporary_session_id)
        assert removed_session["removed_session_id"] == temporary_session_id
        workspace_notes = list_project_notes(workspace)
        assert workspace_notes["counts"]["active"] >= 1
        assert workspace_notes["counts"]["archived"] >= 1
        assert workspace_notes["counts"]["pinned"] >= 1
        assert get_project_note(workspace, "archived-visual-note")["status"] == "archived"
        direct_note_search = search_project_notes(workspace, "temporary bootstrap url", limit=8)
        assert any(item["note_id"] == "bootstrap-auth-note" for item in direct_note_search["matches"])
        analytics_snapshot = get_analytics_snapshot(workspace)
        assert analytics_snapshot["learning_counts"]["resolved"] >= 1
        workspace_learning_entries = list_learning_entries(workspace=workspace)
        assert any(item["entry_id"] == "visual-review-learning" for item in workspace_learning_entries["items"])
        packed_context = show_workspace_context_pack(
            workspace,
            request_text="temporary bootstrap url auth memory",
            route_id="plugin-dev",
            force_refresh=True,
        )
        assert any(
            item["path"] == "external/project-memory/bootstrap-auth-note.md"
            for item in packed_context["context_pack"]["selected_chunks"]
        )
        searched_context = search_context_index(workspace, "temporary bootstrap url auth", route_id="plugin-dev")
        assert any(match["path"] == "external/project-memory/bootstrap-auth-note.md" for match in searched_context["matches"])
        archived_context = search_context_index(workspace, "repeated manual rechecks visual history", route_id="plugin-dev")
        assert any(match["path"] == "external/project-memory/archived-visual-note.md" for match in archived_context["matches"])

        recipes_with_auth_case = read_verification_recipes(workspace, workstream_id=verification_workstream_id)
        existing_case_ids = {case["id"] for case in recipes_with_auth_case["cases"]}
        missing_auth_cases = []
        if "auth-smoke" not in existing_case_ids:
            missing_auth_cases.append(
                {
                    "id": "auth-smoke",
                    "title": "Auth artifact smoke check",
                    "surface_type": "service",
                    "runner": "shell-contract",
                    "tags": ["auth", "smoke"],
                    "host_requirements": ["python"],
                    "auth_profile_ref": "smoke-auth",
                    "auth_request_mode": "read_only",
                    "argv": [
                        sys.executable,
                        "-c",
                        (
                            "import json, os, pathlib; "
                            "artifact_path = pathlib.Path(os.environ['VERIFICATION_AUTH_ARTIFACT_PATH']); "
                            "summary_path = pathlib.Path(os.environ['VERIFICATION_AUTH_SUMMARY_PATH']); "
                            "assert artifact_path.exists(), artifact_path; "
                            "assert summary_path.exists(), summary_path; "
                            "artifact = json.loads(artifact_path.read_text()); "
                            "summary = json.loads(summary_path.read_text()); "
                            "assert os.environ['VERIFICATION_AUTH_PROFILE_ID'] == 'smoke-auth'; "
                            "assert os.environ['VERIFICATION_AUTH_REQUEST_MODE'] == 'read_only'; "
                            "assert json.loads(os.environ['VERIFICATION_AUTH_ACTION_TAGS']) == []; "
                            "assert artifact['artifact_type'] == 'credentials'; "
                            "assert artifact['payload']['login'] == 'qa@example.com'; "
                            "assert summary['profile_id'] == 'smoke-auth'; "
                            "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                            "artifact_dir.mkdir(parents=True, exist_ok=True); "
                            "(artifact_dir / 'auth-smoke.txt').write_text('auth ok\\n'); "
                            "print('auth smoke ok')"
                        ),
                    ],
                    "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                }
            )
        if "auth-session-smoke" not in existing_case_ids:
            missing_auth_cases.append(
                {
                    "id": "auth-session-smoke",
                    "title": "Auth session reuse smoke check",
                    "surface_type": "service",
                    "runner": "shell-contract",
                    "tags": ["auth", "session", "smoke"],
                    "host_requirements": ["python"],
                    "auth_profile_ref": "resolver-auth",
                    "auth_request_mode": "read_only",
                    "auth_action_tags": ["tag.read"],
                    "auth_context": {"subject_ref": "verification-neutral"},
                    "argv": [
                        sys.executable,
                        "-c",
                        (
                            "import json, os, pathlib; "
                            "artifact_path = pathlib.Path(os.environ['VERIFICATION_AUTH_ARTIFACT_PATH']); "
                            "summary_path = pathlib.Path(os.environ['VERIFICATION_AUTH_SUMMARY_PATH']); "
                            "artifact = json.loads(artifact_path.read_text()); "
                            "summary = json.loads(summary_path.read_text()); "
                            "assert artifact['artifact_type'] == 'token_bundle'; "
                            "assert summary['request_mode'] == 'read_only'; "
                            "assert json.loads(os.environ['VERIFICATION_AUTH_ACTION_TAGS']) == ['tag.read']; "
                            "assert os.environ['VERIFICATION_AUTH_REQUEST_MODE'] == 'read_only'; "
                            "assert summary['session_id']; "
                            "assert os.environ['VERIFICATION_AUTH_SESSION_ID'] == summary['session_id']; "
                            "assert os.environ['VERIFICATION_AUTH_RESOLUTION_REASON'] in {'reuse', 'manual_seed', 'refresh', 'initial'}; "
                            "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                            "artifact_dir.mkdir(parents=True, exist_ok=True); "
                            "(artifact_dir / 'auth-session-smoke.txt').write_text(summary['session_id']); "
                            "print('auth session smoke ok')"
                        ),
                    ],
                    "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                }
            )
        if "auth-session-binding-smoke" not in existing_case_ids:
            missing_auth_cases.append(
                {
                    "id": "auth-session-binding-smoke",
                    "title": "Auth session binding reuse smoke check",
                    "surface_type": "service",
                    "runner": "shell-contract",
                    "tags": ["auth", "session", "binding"],
                    "host_requirements": ["python"],
                    "auth_profile_ref": "binding-auth",
                    "auth_request_mode": "read_only",
                    "auth_action_tags": ["tag.read"],
                    "auth_session_binding": {
                        "refs": [
                            "backend.shared",
                            "https://neutral-b.example.test",
                        ]
                    },
                    "argv": [
                        sys.executable,
                        "-c",
                        (
                            "import json, os, pathlib; "
                            "artifact_path = pathlib.Path(os.environ['VERIFICATION_AUTH_ARTIFACT_PATH']); "
                            "summary_path = pathlib.Path(os.environ['VERIFICATION_AUTH_SUMMARY_PATH']); "
                            "artifact = json.loads(artifact_path.read_text()); "
                            "summary = json.loads(summary_path.read_text()); "
                            "assert artifact['artifact_type'] == 'token_bundle'; "
                            "assert summary['request_mode'] == 'read_only'; "
                            "assert json.loads(os.environ['VERIFICATION_AUTH_ACTION_TAGS']) == ['tag.read']; "
                            "assert os.environ['VERIFICATION_AUTH_REQUEST_MODE'] == 'read_only'; "
                            "assert summary['session_id']; "
                            "artifact_dir = pathlib.Path(os.environ['VERIFICATION_ARTIFACT_DIR']); "
                            "artifact_dir.mkdir(parents=True, exist_ok=True); "
                            "(artifact_dir / 'auth-session-binding-smoke.txt').write_text(summary['session_id']); "
                            "print('auth session binding smoke ok')"
                        ),
                    ],
                    "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                }
            )
        if "auth-mutating-blocked" not in existing_case_ids:
            missing_auth_cases.append(
                {
                    "id": "auth-mutating-blocked",
                    "title": "Read-only auth rejects mutating verification",
                    "surface_type": "service",
                    "runner": "shell-contract",
                    "tags": ["auth", "policy"],
                    "host_requirements": ["python"],
                    "auth_profile_ref": "smoke-auth",
                    "auth_request_mode": "mutating",
                    "argv": [sys.executable, "-c", "raise SystemExit('runner should not execute')"],
                    "retry_policy": {"attempts": 1, "slow_after_seconds": 1},
                }
            )
        if missing_auth_cases:
            write_verification_recipes(
                workspace,
                {
                    **recipes_with_auth_case,
                    "cases": [
                        *recipes_with_auth_case["cases"],
                        *missing_auth_cases,
                    ],
                },
                workstream_id=verification_workstream_id,
            )
        auth_run = start_verification_case(workspace, "auth-smoke", wait=True, workstream_id=verification_workstream_id)
        assert auth_run["status"] == "passed"
        assert any(event["event_type"] == "auth_resolved" for event in read_verification_events(workspace, auth_run["run_id"], workstream_id=verification_workstream_id)["events"])
        auth_case_state = next(case for case in auth_run["cases"] if case["case_id"] == "auth-smoke")
        assert auth_case_state["auth"]["profile_id"] == "smoke-auth"
        assert auth_case_state["auth"]["request_mode"] == "read_only"
        transient_auth_dir = Path(auth_run["transient_auth_dir"])
        if transient_auth_dir.exists():
            assert not any(transient_auth_dir.iterdir())
        auth_session_run = start_verification_case(workspace, "auth-session-smoke", wait=True, workstream_id=verification_workstream_id)
        assert auth_session_run["status"] == "passed"
        auth_session_case_state = next(case for case in auth_session_run["cases"] if case["case_id"] == "auth-session-smoke")
        assert auth_session_case_state["auth"]["profile_id"] == "resolver-auth"
        assert auth_session_case_state["auth"]["request_mode"] == "read_only"
        assert auth_session_case_state["auth"]["session_id"] == resolver_session_id
        auth_session_run_repeat = start_verification_case(workspace, "auth-session-smoke", wait=True, workstream_id=verification_workstream_id)
        assert auth_session_run_repeat["status"] == "passed"
        repeat_case_state = next(case for case in auth_session_run_repeat["cases"] if case["case_id"] == "auth-session-smoke")
        assert repeat_case_state["auth"]["session_id"] == resolver_session_id
        binding_auth_run = start_verification_case(workspace, "auth-session-binding-smoke", wait=True, workstream_id=verification_workstream_id)
        assert binding_auth_run["status"] == "passed"
        binding_case_state = next(case for case in binding_auth_run["cases"] if case["case_id"] == "auth-session-binding-smoke")
        assert binding_case_state["auth"]["profile_id"] == "binding-auth"
        assert binding_case_state["auth"]["session_id"] == binding_session_id
        binding_auth_run_repeat = start_verification_case(
            workspace,
            "auth-session-binding-smoke",
            wait=True,
            workstream_id=verification_workstream_id,
        )
        assert binding_auth_run_repeat["status"] == "passed"
        binding_repeat_case_state = next(
            case for case in binding_auth_run_repeat["cases"] if case["case_id"] == "auth-session-binding-smoke"
        )
        assert binding_repeat_case_state["auth"]["session_id"] == binding_session_id
        blocked_auth_run = start_verification_case(workspace, "auth-mutating-blocked", wait=True, workstream_id=verification_workstream_id)
        assert blocked_auth_run["status"] == "failed"
        auth_snapshot = dashboard_snapshot(workspace)
        assert auth_snapshot["workspace_cockpit"]["integrations"]["auth"]["summary"]["profile_count"] >= 1
        assert auth_snapshot["workspace_cockpit"]["integrations"]["auth"]["summary"]["active_session_count"] >= 1
        assert auth_snapshot["workspace_cockpit"]["memory"]["project_notes"]["counts"]["pinned"] >= 1
        assert auth_snapshot["workspace_cockpit"]["memory"]["learnings"]["counts"]["resolved"] >= 1
        assert auth_snapshot["workspace_cockpit"]["quality"]["auth_resolution"]["status"] in {"ok", "not_configured", "warning"}

        legacy_workspace = temp_root / "legacy-dashboard-workspace"
        legacy_workspace.mkdir()
        legacy_fixture = _make_legacy_workspace_fixture(legacy_workspace)
        legacy_snapshot = dashboard_snapshot(legacy_workspace)
        assert legacy_snapshot["schema_version"] == 2
        assert legacy_snapshot["workspace_cockpit"]["workspace_path"] == str(legacy_workspace.resolve())
        assert legacy_snapshot["workspace_cockpit"]["plan"]["current_workstream"]["workstream_id"] == legacy_fixture["workstream_id"]
        assert Path(legacy_fixture["paths"]["workspace_state"]).exists()
        assert Path(legacy_fixture["paths"]["workstreams_index"]).exists()

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_dashboard_snapshot",
                    "arguments": {
                        "workspacePath": str(workspace)
                    }
                },
            },
        )
        assert response["result"]["isError"] is False
        assert response["result"]["structuredContent"]["workspace_cockpit"]["workspace_path"] == str(workspace.resolve())
        assert response["result"]["structuredContent"]["workspace_cockpit"]["quality"]["latest_run"]["run_id"] == blocked_auth_run["run_id"]

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "audit_verification_coverage",
                    "arguments": {
                        "workspacePath": str(workspace),
                    },
                },
            },
        )
        assert "warning_count" in response["result"]["structuredContent"]
        assert "design_summary" in response["result"]["structuredContent"]
        assert "testability_summary" in response["result"]["structuredContent"]

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "show_workspace_context_pack",
                    "arguments": {
                        "workspacePath": str(repo_root),
                        "requestText": "Inspect MCP tool catalogs and dashboard runtime",
                        "routeId": "plugin-dev",
                        "limit": 5,
                    },
                },
            },
        )
        assert response["result"]["structuredContent"]["context_pack"]["selected_chunks"]
        progress("dashboard snapshot, installer CLI, MCP handshake, and GUI smoke assertions")

    total_elapsed = time.monotonic() - smoke_started_at
    print(f"smoke test passed in {total_elapsed:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
