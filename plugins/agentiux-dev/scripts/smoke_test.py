#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from agentiux_dev_gui import stop as stop_gui
from agentiux_dev_lib import (
    apply_upgrade_plan,
    audit_repository,
    cache_reference_preview,
    close_task,
    command_aliases,
    create_git_branch,
    create_git_commit,
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
    list_reference_boards,
    list_starter_runs,
    list_tasks,
    list_workspaces,
    list_workstreams,
    migrate_workspace_state,
    plugin_stats,
    plan_git_change,
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
    repair_workspace_state,
    resolve_command_phrase,
    set_active_brief,
    show_git_workflow_advice,
    show_host_support,
    show_upgrade_plan,
    stage_git_files,
    suggest_branch_name,
    suggest_commit_message,
    suggest_pr_body,
    suggest_pr_title,
    switch_workstream,
    workflow_advice,
    workspace_paths,
    write_design_brief,
    write_design_handoff,
    write_reference_board,
    write_stage_register,
)
from agentiux_dev_verification import (
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
from install_home_local import install_plugin


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


def _write_json_file(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


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
    allowed_exceptions = {"smoke_test.py"}
    for path in plugin_root.rglob("*"):
        if not path.is_file() or path.name in allowed_exceptions or "__pycache__" in path.parts or path.suffix == ".pyc":
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


def _write_fake_bootstrap_tools(bin_dir: Path) -> None:
    npx_script = bin_dir / "npx"
    cargo_script = bin_dir / "cargo"
    npx_script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "cwd = pathlib.Path.cwd()\n"
        "args = sys.argv[1:]\n"
        "def ensure_project(path):\n"
        "    path.mkdir(parents=True, exist_ok=True)\n"
        "    (path / 'package.json').write_text(json.dumps({'name': path.name, 'dependencies': {'react': '^19.0.0', 'next': '^16.0.0'}}, indent=2) + '\\n')\n"
        "    (path / 'README.md').write_text(f'# {path.name}\\n')\n"
        "    (path / 'tsconfig.json').write_text('{\"compilerOptions\":{\"strict\":true}}\\n')\n"
        "if 'create-next-app@latest' in args:\n"
        "    ensure_project(cwd / args[args.index('create-next-app@latest') + 1])\n"
        "elif 'create-expo-app@latest' in args:\n"
        "    project = cwd / args[args.index('create-expo-app@latest') + 1]\n"
        "    ensure_project(project)\n"
        "    (project / 'app.json').write_text('{\"expo\":{\"name\":\"demo\"}}\\n')\n"
        "elif '@nestjs/cli' in args and 'new' in args:\n"
        "    ensure_project(cwd / args[args.index('new') + 1])\n"
        "    (cwd / args[args.index('new') + 1] / 'nest-cli.json').write_text('{}\\n')\n"
        "elif 'create-nx-workspace@latest' in args:\n"
        "    project = cwd / args[args.index('create-nx-workspace@latest') + 1]\n"
        "    ensure_project(project)\n"
        "    (project / 'nx.json').write_text('{\"extends\":\"nx/presets/npm.json\"}\\n')\n"
        "elif args[:2] == ['nx', 'g']:\n"
        "    marker = cwd / 'generated.txt'\n"
        "    marker.write_text(marker.read_text() + ' '.join(args) + '\\n' if marker.exists() else ' '.join(args) + '\\n')\n"
        "else:\n"
        "    raise SystemExit('unsupported fake npx invocation: ' + ' '.join(args))\n"
    )
    cargo_script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "cwd = pathlib.Path.cwd()\n"
        "args = sys.argv[1:]\n"
        "if args[:1] == ['new']:\n"
        "    project = cwd / args[1]\n"
        "    project.mkdir(parents=True, exist_ok=True)\n"
        "    (project / 'Cargo.toml').write_text('[package]\\nname = \"demo\"\\nversion = \"0.1.0\"\\n')\n"
        "    (project / 'README.md').write_text(f'# {project.name}\\n')\n"
        "    (project / 'src').mkdir(exist_ok=True)\n"
        "    (project / 'src' / 'main.rs').write_text('fn main() {}\\n')\n"
        "else:\n"
        "    raise SystemExit('unsupported fake cargo invocation: ' + ' '.join(args))\n"
    )
    npx_script.chmod(0o755)
    cargo_script.chmod(0o755)


def _write_fake_adb(bin_dir: Path) -> None:
    adb_script = bin_dir / "adb"
    adb_script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys, time\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] == '-s':\n"
        "    args = args[2:]\n"
        "if args[:3] == ['shell', 'pidof', '-s']:\n"
        "    print('4242')\n"
        "elif args[:2] == ['logcat', '-c']:\n"
        "    raise SystemExit(0)\n"
        "elif args[:1] == ['logcat']:\n"
        "    for index in range(5):\n"
        "        print(f'03-31 00:00:0{index} I/FakeTag(4242): heartbeat {index}', flush=True)\n"
        "        time.sleep(0.2)\n"
        "    print('03-31 00:00:05 E/AndroidRuntime(4242): FATAL EXCEPTION: main', flush=True)\n"
        "    time.sleep(1)\n"
        "else:\n"
        "    raise SystemExit('unsupported fake adb invocation: ' + ' '.join(args))\n"
    )
    adb_script.chmod(0o755)


def main() -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    repo_root = plugin_root.parents[1]
    with tempfile.TemporaryDirectory() as temp_dir:
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
        tool_bin = temp_root / "tool-bin"
        tool_bin.mkdir()
        _write_fake_adb(tool_bin)
        os.environ["PATH"] = f"{tool_bin}{os.pathsep}{os.environ['PATH']}"

        _assert_clean_repo_text(repo_root, plugin_root)

        aliases = command_aliases()
        assert "initialize workspace" in aliases
        assert "create workstream" in aliases
        assert resolve_command_phrase("\u0438\u043d\u0438\u0446\u0438\u0430\u043b\u0438\u0437\u0438\u0440\u0443\u0439 workspace") == "initialize workspace"
        assert resolve_command_phrase("\u0441\u043e\u0437\u0434\u0430\u0439 workstream") == "create workstream"

        preview = preview_workspace_init(workspace)
        assert preview["must_confirm_before_write"] is True
        assert preview["paths"]["workstreams_index"].endswith("workstreams/index.json")
        assert "mobile-platform" in preview["selected_profiles"]
        assert "backend-platform" in preview["selected_profiles"]
        assert preview["planning_policy"]["explicit_stage_plan_required"] is True

        pre_init_advice = workflow_advice(workspace, "Implement a checkout feature across web and backend")
        assert pre_init_advice["workspace_initialized"] is False
        assert pre_init_advice["initialization_advice"]["should_propose"] is True
        assert pre_init_advice["requires_confirmation"] is True
        assert pre_init_advice["track_recommendation"]["recommended_mode"] == "workstream"

        greenfield_advice = workflow_advice(workspace, "Build a new Expo mobile app from scratch")
        assert greenfield_advice["starter_recommendation"]["recommended_preset_id"] == "expo-mobile"

        self_host_preview = preview_workspace_init(repo_root)
        assert "plugin-platform" in self_host_preview["selected_profiles"]
        assert {"python", "codex-plugin", "mcp-server", "local-dashboard"}.issubset(set(self_host_preview["detected_stacks"]))
        assert self_host_preview["plugin_platform"]["enabled"] is True
        assert self_host_preview["plugin_platform"]["primary_plugin_root"] == "plugins/agentiux-dev"
        assert self_host_preview["plugin_platform"]["release_readiness_command"] == f"{python_launcher_string()} plugins/agentiux-dev/scripts/release_readiness.py"
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
        assert repaired_plugin_state["workspace_state"]["state_repair_status"]["source_schema_version"] == 7
        assert repaired_plugin_state["workspace_state"]["state_repair_status"]["target_schema_version"] == 7
        assert repaired_plugin_state["workspace_state"]["state_repair_status"]["source_workstream_schema_versions"][stale_plugin_fixture["workstream_id"]] == 4
        assert "docker_policy" not in repaired_plugin_state["workspace_state"]
        repaired_workstream = next(
            item for item in repaired_plugin_state["workstreams"]["items"] if item["workstream_id"] == stale_plugin_fixture["workstream_id"]
        )
        assert repaired_workstream["title"] == "plugin-production-readiness"
        assert repaired_workstream["kind"] == "feature"
        assert repaired_workstream["scope_summary"] == "Lock plugin runtime convergence, verification, and release-readiness contracts."
        assert repaired_workstream["branch_hint"] == "codex/plugin-production-readiness"
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
        finally:
            shutil.rmtree(plugin_helper_root, ignore_errors=True)
        _assert_no_default_origin(repaired_plugin_state["stage_register"])
        _assert_no_default_origin(plugin_verification_recipes)

        backend_workspace = temp_root / "backend-workspace"
        backend_workspace.mkdir()
        _seed_backend_workspace(backend_workspace, with_infra=True)
        init_workspace(backend_workspace)
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
        assert "missing-android-visual-verification" not in visual_gap_ids
        assert visual_gap_audit["coverage"]["android_visual"] is True

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
        assert initialized["workspace_state"]["schema_version"] == 7
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
        assert workstream_advice["track_recommendation"]["recommended_mode"] == "workstream"
        assert len(list_workstreams(workspace)["items"]) == 1

        task_advice = workflow_advice(workspace, "Fix CTA spacing in the hero section", auto_create=True)
        assert task_advice["auto_create_supported"] is True
        assert task_advice["applied_action"]["action"] == "create_task"
        assert task_advice["requires_confirmation"] is False
        task_id = task_advice["applied_action"]["task_id"]
        task_result = read_task(workspace, task_id=task_id)
        assert task_id
        assert "cta-spacing" in task_id
        assert task_result["linked_workstream_id"] == primary_workstream["created_workstream_id"]
        assert current_task(workspace)["task_id"] == task_id
        reused_task_advice = workflow_advice(workspace, "Fix CTA spacing in the hero section", auto_create=True)
        assert reused_task_advice["applied_action"]["action"] == "reuse_current_task"
        assert reused_task_advice["applied_action"]["task_id"] == task_id
        assert read_stage_register(workspace)["workstream_id"] == primary_workstream["created_workstream_id"]

        set_active_brief(workspace, "# TaskBrief\n\nFix CTA spacing.\n")
        assert "Fix CTA spacing" in get_active_brief(workspace)["markdown"]
        closed_task = close_task(workspace, verification_summary={"status": "completed", "summary": "Spacing fixed."})
        assert closed_task["status"] == "completed"
        assert current_task(workspace) is None

        switch_workstream(workspace, primary_workstream["created_workstream_id"])
        assert current_workstream(workspace)["workstream_id"] == primary_workstream["created_workstream_id"]

        host_support = show_host_support(workspace)
        assert host_support["host_os"] in {"macos", "linux", "windows"}
        assert "core_runtime" in host_support["host_capabilities"]

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
                                "(artifact_dir / 'expo-home-semantic.json').write_text(json.dumps({"
                                "'schema_version': 2, "
                                "'runner': 'detox-visual', "
                                "'helper_bundle_version': '0.8.0', "
                                "'summary': {'status': 'passed'}, "
                                "'targets': [{"
                                "'target_id': 'home-screen', "
                                "'status': 'passed', "
                                "'checks': ["
                                "{'id': 'presence_uniqueness', 'status': 'passed'}, "
                                "{'id': 'visibility', 'status': 'passed'}, "
                                "{'id': 'overflow_clipping', 'status': 'passed'}, "
                                "{'id': 'interaction_states', 'status': 'passed'}, "
                                "{'id': 'scroll_reachability', 'status': 'passed'}, "
                                "{'id': 'occlusion', 'status': 'passed'}"
                                "]"
                                "}]"
                                "})); "
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
                                "interaction_states",
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
                ],
            },
        )
        assert verification_recipes["schema_version"] == 2
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

        case_run = start_verification_case(workspace, "web-home", workstream_id=verification_workstream_id)
        case_run = wait_for_verification_run(workspace, case_run["run_id"], timeout_seconds=20, workstream_id=verification_workstream_id)
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

        suite_run = start_verification_suite(workspace, "full", workstream_id=verification_workstream_id)
        time.sleep(0.5)
        active_run = active_verification_run(workspace, workstream_id=verification_workstream_id)
        assert active_run is not None
        assert active_run["run_id"] == suite_run["run_id"]
        mid_events = read_verification_events(workspace, suite_run["run_id"], limit=20, workstream_id=verification_workstream_id)
        assert any(event["event_type"] == "run_started" for event in mid_events["events"])

        suite_run = wait_for_verification_run(workspace, suite_run["run_id"], timeout_seconds=20, workstream_id=verification_workstream_id)
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

        commit_repo = temp_root / "commit-style-repo"
        commit_repo.mkdir()
        subprocess.run(["git", "init"], cwd=commit_repo, check=True, capture_output=True, text=True)
        (commit_repo / "README.md").write_text("# Commit Style\n")
        subprocess.run(["git", "add", "README.md"], cwd=commit_repo, check=True, capture_output=True, text=True)
        _git_commit(commit_repo, "feat(dashboard): add overview panel")
        (commit_repo / "dashboard.txt").write_text("panel\n")
        subprocess.run(["git", "add", "dashboard.txt"], cwd=commit_repo, check=True, capture_output=True, text=True)
        _git_commit(commit_repo, "fix(dashboard): align status badge")
        commit_style = detect_commit_style(commit_repo)
        assert commit_style["style"] == "conventional"
        assert commit_style["uses_scope"] is True
        assert commit_style["preferred_branch_prefix"]
        git_advice = show_git_workflow_advice(commit_repo)
        assert git_advice["inspection"]["style"] == "conventional"
        assert git_advice["commit_policy"]["recommended_style"] == "conventional"
        assert git_advice["branch_policy"]["pattern"].startswith("codex/")
        assert "best_practices" not in git_advice
        commit_message = suggest_commit_message(
            commit_repo,
            "Improve dashboard log view",
            files=["plugins/agentiux-dev/dashboard/app.js"],
        )
        assert commit_message["suggested_message"].startswith("feat(dashboard):")
        assert commit_message["advice"] == git_advice
        branch_name = suggest_branch_name(commit_repo, "Improve dashboard log view", mode="task")
        assert branch_name["suggested_branch_name"].startswith("codex/")
        assert branch_name["advice"] == git_advice
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

        empty_repo = temp_root / "empty-repo"
        empty_repo.mkdir()
        subprocess.run(["git", "init"], cwd=empty_repo, check=True, capture_output=True, text=True)
        empty_advice = show_git_workflow_advice(empty_repo)
        assert empty_advice["inspection"]["source"] == "fallback"
        assert empty_advice["commit_policy"]["recommended_style"] == "conventional"

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
        branch_result = create_git_branch(git_flow_repo, git_plan["suggested_branch_name"])
        assert branch_result["status"] == "created"
        stage_result = stage_git_files(git_flow_repo, ["notes.md"])
        assert "notes.md" in stage_result["git_state"]["staged_files"]
        commit_result = create_git_commit(git_flow_repo, git_plan["suggested_commit_message"])
        assert commit_result["commit_hash"]
        assert inspect_git_state(git_flow_repo)["summary_counts"]["changed_files"] == 0

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
        _write_fake_bootstrap_tools(starter_bin)
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
        assert snapshot["workspace_detail"]["current_design_handoff"]["status"] == "ready"
        assert snapshot["workspace_detail"]["verification_runs"]["latest_run"]["run_id"] == suite_run["run_id"]
        assert snapshot["workspace_detail"]["latest_verification_run"]["run_id"] == suite_run["run_id"]
        assert snapshot["workspace_detail"]["verification_selection"]["requested_mode_source"] == "workstream_closeout_policy"
        assert snapshot["workspace_detail"]["verification_selection"]["selected_suite"]["id"] == "full"
        assert snapshot["workspace_detail"]["recent_verification_events"]["events"]
        assert snapshot["workspace_detail"]["workstreams"]["items"]
        assert snapshot["workspace_detail"]["tasks"]["items"]

        legacy_workspace = temp_root / "legacy-dashboard-workspace"
        legacy_workspace.mkdir()
        legacy_fixture = _make_legacy_workspace_fixture(legacy_workspace)
        legacy_snapshot = dashboard_snapshot(legacy_workspace)
        assert legacy_snapshot["schema_version"] == 2
        assert legacy_snapshot["workspace_detail"]["summary"]["workspace_path"] == str(legacy_workspace.resolve())
        assert legacy_snapshot["workspace_detail"]["stage_register"]["workstream_id"] == legacy_fixture["workstream_id"]
        assert Path(legacy_fixture["paths"]["workspace_state"]).exists()
        assert Path(legacy_fixture["paths"]["workstreams_index"]).exists()

        install_result = install_plugin(plugin_root, install_root, marketplace)
        assert Path(install_result["install_root"]).exists()
        installed_mcp = json.loads((install_root / ".mcp.json").read_text())
        mcp_args = installed_mcp["mcpServers"]["agentiux-dev-state"]["args"]
        assert str((install_root / "scripts" / "agentiux_dev_mcp.py").resolve()) in mcp_args
        assert marketplace.exists()
        marketplace.write_text(
            json.dumps(
                {
                    "name": "legacy-owner-local",
                    "interface": {"displayName": "Legacy Owner Local Plugins"},
                    "plugins": [],
                },
                indent=2,
            )
            + "\n"
        )
        install_result = install_plugin(plugin_root, install_root, marketplace)
        marketplace_payload = json.loads(marketplace.read_text())
        assert marketplace_payload["name"] == "local-plugins"
        assert marketplace_payload["interface"]["displayName"] == "Local Plugins"

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
        assert response["result"]["structuredContent"]["workspace_detail"]["summary"]["workspace_path"] == str(workspace.resolve())
        assert response["result"]["structuredContent"]["workspace_detail"]["verification_runs"]["latest_run"]["run_id"] == suite_run["run_id"]

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "list_workstreams",
                    "arguments": {
                        "workspacePath": str(workspace)
                    }
                },
            },
        )
        assert response["result"]["structuredContent"]["current_workstream_id"]

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "advise_workflow",
                    "arguments": {
                        "workspacePath": str(workspace),
                        "requestText": "Fix button spacing",
                    },
                },
            },
        )
        assert response["result"]["structuredContent"]["track_recommendation"]["recommended_mode"] == "task"
        assert response["result"]["structuredContent"]["applied_action"]["action"] in {"create_task", "reuse_current_task"}

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 4,
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

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "show_verification_helper_catalog",
                    "arguments": {
                        "workspacePath": str(workspace),
                    },
                },
            },
        )
        assert response["result"]["structuredContent"]["version_status"] == "synced"

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "sync_verification_helpers",
                    "arguments": {
                        "workspacePath": str(workspace),
                    },
                },
            },
        )
        assert response["result"]["structuredContent"]["status"] in {"synced", "already_synced"}

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "suggest_commit_message",
                    "arguments": {
                        "repoRoot": str(commit_repo),
                        "summary": "Improve dashboard log view",
                        "files": ["plugins/agentiux-dev/dashboard/app.js"],
                    },
                },
            },
        )
        assert response["result"]["structuredContent"]["suggested_message"].startswith("feat(dashboard):")

        response = _call_mcp(
            plugin_root / "scripts" / "agentiux_dev_mcp.py",
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "plan_git_change",
                    "arguments": {
                        "repoRoot": str(git_flow_repo),
                    },
                },
            },
        )
        assert response["result"]["structuredContent"]["suggested_branch_name"].startswith("codex/")

        gui_launch_process = subprocess.run(
            ["python3", str(plugin_root / "scripts" / "agentiux_dev_gui.py"), "launch", "--workspace", str(workspace)],
            text=True,
            capture_output=True,
            env=os.environ.copy(),
            check=False,
        )
        if gui_launch_process.returncode == 0:
            gui_launch = json.loads(gui_launch_process.stdout)
            try:
                encoded_workspace = urllib.parse.quote(str(workspace.resolve()), safe="")
                with urllib.request.urlopen(f"{gui_launch['url']}/api/dashboard?workspace={encoded_workspace}", timeout=20) as response_handle:
                    payload = json.loads(response_handle.read().decode("utf-8"))
                assert payload["workspace_detail"]["summary"]["workspace_label"] == "demo-workspace"
                assert payload["workspace_detail"]["verification_runs"]["latest_run"]["run_id"] == suite_run["run_id"]
                assert payload["workspace_detail"]["workstreams"]["items"]
                assert payload["stats"]["active_verification_runs"] == 0
            finally:
                stop_gui()
        else:
            assert "Operation not permitted" in gui_launch_process.stderr or "PermissionError" in gui_launch_process.stderr

    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
