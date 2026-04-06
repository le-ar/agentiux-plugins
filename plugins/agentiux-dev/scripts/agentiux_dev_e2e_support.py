#!/usr/bin/env python3
from __future__ import annotations

import ast
import copy
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator
import urllib.error
import urllib.parse
import urllib.request

from agentiux_dev_analytics import write_learning_entry
from agentiux_dev_context import refresh_context_index
from agentiux_dev_lib import (
    PLUGIN_NAME,
    STATE_SCHEMA_VERSION,
    create_workstream,
    dashboard_snapshot,
    get_active_brief,
    init_workspace,
    payload_size_bytes,
    preview_reset_workspace_state,
    preview_repair_workspace_state,
    python_script_command,
    reset_workspace_state,
    stage_git_files,
    workspace_paths,
    write_stage_register,
)
from agentiux_dev_verification import (
    active_verification_run,
    audit_verification_coverage,
    read_verification_events,
    show_verification_helper_catalog,
    sync_verification_helpers,
    wait_for_verification_run,
    write_verification_recipes,
)
from install_home_local import install_plugin


SEMANTIC_REQUIRED_CHECKS = [
    "presence_uniqueness",
    "visibility",
    "scroll_reachability",
    "overflow_clipping",
    "computed_styles",
    "interaction_states",
    "layout_relations",
    "occlusion",
]
SEMANTIC_CONTRACT_RUNNER_FILENAME = "semantic_contract_runner.py"
SYNTHETIC_SURFACE_INVENTORY_FILENAME = "synthetic_surface_inventory.json"
SYNTHETIC_SURFACE_TOKEN_PATTERN = re.compile(r"(fake|mock|stub|synthetic)", re.IGNORECASE)
TEST_TOOL_OVERRIDE_ALLOW_FLAG = "AGENTIUX_DEV_ALLOW_TEST_OVERRIDES"

DASHBOARD_BUDGETS = {
    "cold_start_ms": 8_000,
    "payload_bytes": {
        "overview": 16 * 1024,
        "bootstrap": 32 * 1024,
        "plan_panel": 24 * 1024,
    },
    "request_timings_ms": {
        "bootstrap": 1_200,
        "plan_panel": 1_200,
    },
    "render_timings_ms": {
        "first_usable_render": 3_500,
    },
}

REPO_FIXTURES: list[dict[str, Any]] = [
    {
        "fixture_id": "codex-benchmark-workspace",
        "repo_name": "codex-benchmark-workspace",
        "profile_expectations": ["web-platform", "monorepo-platform"],
        "runner": "playwright-visual",
        "surface_type": "web",
        "route_query": "Inspect the storefront checkout verification and readiness owner files",
        "search_query": "storefront checkout readiness playwright owner files",
        "case_id": "storefront-checkout",
        "suite_id": "codex-benchmark-suite",
        "artifact_name": "storefront-checkout.txt",
        "changed_path": "apps/storefront/app/checkout/page.tsx",
        "target": {
            "target_id": "checkout-cta",
            "locator": {"kind": "test_id", "value": "checkout-cta"},
            "expected_attributes": {"label": "Continue"},
        },
        "helper_files": ["core/index.js", "playwright/index.js"],
        "learning_entry": {
            "entry_id": "learning-reset-codex-benchmark",
            "kind": "test-harness",
            "status": "open",
            "symptom": "Codex benchmark fixture must stay realistic and isolated from plugin repo noise.",
            "fix_applied": "Use a dedicated monorepo fixture with real owner, spec, and readiness links.",
            "source": "codex-cli-ab-evidence",
        },
    },
    {
        "fixture_id": "fullstack-workspace",
        "repo_name": "fullstack-workspace",
        "profile_expectations": ["web-platform", "monorepo-platform"],
        "runner": "playwright-visual",
        "surface_type": "web",
        "route_query": "Inspect Playwright semantic verification for the checkout page",
        "search_query": "playwright semantic checkout helper bundle",
        "case_id": "checkout-page",
        "suite_id": "web-suite",
        "artifact_name": "checkout-page.txt",
        "changed_path": "apps/web/app/checkout/page.tsx",
        "target": {
            "target_id": "checkout-main",
            "locator": {"kind": "role", "value": "main"},
            "expected_attributes": {"ariaLabel": "Checkout"},
        },
        "helper_files": ["core/index.js", "playwright/index.js"],
        "learning_entry": {
            "entry_id": "learning-reset-fullstack",
            "kind": "test-harness",
            "status": "open",
            "symptom": "Reset should purge workspace analytics and context cache state.",
            "fix_applied": "External e2e harness reinitializes from a clean baseline.",
            "source": "external-repo-e2e",
        },
    },
    {
        "fixture_id": "mobile-detox-app",
        "repo_name": "mobile-detox-app",
        "profile_expectations": ["mobile-platform"],
        "runner": "detox-visual",
        "surface_type": "mobile",
        "route_query": "Check Detox semantic verification helper sync for the home screen",
        "search_query": "detox semantic helper home screen",
        "case_id": "home-screen",
        "suite_id": "detox-suite",
        "artifact_name": "home-screen.txt",
        "changed_path": "apps/mobile/src/screens/HomeScreen.tsx",
        "target": {
            "target_id": "home-screen",
            "locator": {"kind": "test_id", "value": "home-screen"},
            "expected_attributes": {"label": "Home"},
        },
        "helper_files": ["core/index.js", "detox/index.js", "detox/react-native-probe.js"],
        "learning_entry": {
            "entry_id": "learning-reset-mobile",
            "kind": "test-harness",
            "status": "open",
            "symptom": "Reset should remove mobile auth and verification preparation traces.",
            "fix_applied": "Temp clone plus workspace reset keeps the fixture reusable.",
            "source": "external-repo-e2e",
        },
    },
    {
        "fixture_id": "android-compose-lab",
        "repo_name": "android-compose-lab",
        "profile_expectations": ["mobile-platform"],
        "runner": "android-compose-screenshot",
        "surface_type": "android",
        "route_query": "Audit Android Compose semantic screenshot checks for the home route",
        "search_query": "android compose semantic screenshot helper",
        "case_id": "compose-home",
        "suite_id": "compose-suite",
        "artifact_name": "compose-home.txt",
        "changed_path": "app/src/main/java/com/example/demo/HomeScreen.kt",
        "target": {
            "target_id": "compose-home",
            "locator": {"kind": "semantics_tag", "value": "compose-home"},
            "expected_attributes": {"contentDescription": "Home"},
        },
        "helper_files": ["core/index.js", "android-compose/SemanticChecks.kt"],
        "learning_entry": {
            "entry_id": "learning-reset-compose",
            "kind": "test-harness",
            "status": "open",
            "symptom": "Reset should purge compose semantic and layout audit scratch state.",
            "fix_applied": "Tracked fixtures only run from isolated temp roots.",
            "source": "external-repo-e2e",
        },
    },
]


@dataclass
class IsolatedPluginRun:
    run_root: Path
    state_root: Path
    install_root: Path
    marketplace_path: Path
    env: dict[str, str]
    install_result: dict[str, Any]


def completed_process(
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
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
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


@contextmanager
def temporary_env(overrides: dict[str, str]) -> Iterator[None]:
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


def read_json_url(url: str, *, timeout: int = 5) -> tuple[dict[str, Any], int, float]:
    started_at = time.monotonic()
    with urllib.request.urlopen(url, timeout=timeout) as response_handle:
        raw = response_handle.read()
    elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
    return json.loads(raw.decode("utf-8")), len(raw), elapsed_ms


def sample_json_url(url: str, *, samples: int = 3, timeout: int = 5) -> tuple[dict[str, Any], int, float, list[float]]:
    payload: dict[str, Any] | None = None
    response_bytes = 0
    timings: list[float] = []
    for _ in range(max(1, samples)):
        payload, response_bytes, elapsed_ms = read_json_url(url, timeout=timeout)
        timings.append(elapsed_ms)
    return payload or {}, response_bytes, round(float(statistics.median(timings)), 2), timings


def read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
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


def reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def wait_for_run_started(
    workspace: str | Path,
    run_id: str,
    *,
    workstream_id: str | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    latest_active_run = None
    latest_events: dict[str, Any] = {"events": []}
    while time.time() < deadline:
        latest_active_run = active_verification_run(workspace, workstream_id=workstream_id)
        latest_events = read_verification_events(workspace, run_id, limit=20, workstream_id=workstream_id)
        if latest_active_run is not None or any(event["event_type"] == "run_started" for event in latest_events["events"]):
            return latest_active_run, latest_events
        time.sleep(0.1)
    return latest_active_run, latest_events


def git_commit(repo_root: Path, message: str, body: str | None = None) -> None:
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


def stage_definition(stage_id: str, title: str, objective: str, slices: list[str], **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": stage_id,
        "title": title,
        "objective": objective,
        "canonical_execution_slices": slices,
    }
    payload.update(extra)
    return payload


def confirm_stage_plan(workspace: Path, stages: list[dict[str, Any]], workstream_id: str | None = None) -> dict[str, Any]:
    from agentiux_dev_lib import read_stage_register

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


def seed_workspace(root: Path) -> None:
    write_text_file(
        root / "package.json",
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
        + "\n",
    )
    write_text_file(root / "tsconfig.json", "{\"compilerOptions\":{\"strict\":true}}\n")
    write_text_file(root / "nx.json", "{\"extends\":\"nx/presets/npm.json\"}\n")
    write_text_file(root / "Cargo.toml", "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n")
    write_text_file(
        root / "docker-compose.yml",
        "services:\n"
        "  postgres:\n    image: postgres:16\n"
        "  mongo:\n    image: mongo:8\n"
        "  redis:\n    image: redis:7\n"
        "  nats:\n    image: nats:2\n",
    )
    (root / "android").mkdir(parents=True, exist_ok=True)
    (root / "ios").mkdir(parents=True, exist_ok=True)
    write_text_file(root / "app.json", "{\"expo\":{\"name\":\"demo\"}}\n")
    write_text_file(root / "tailwind.config.ts", "export default {};\n")
    write_text_file(root / "README.md", "# Demo Workspace\n")


def seed_web_only_workspace(root: Path) -> None:
    write_text_file(
        root / "package.json",
        json.dumps({"name": "web-only", "dependencies": {"react": "^19.0.0", "next": "^16.0.0"}}, indent=2) + "\n",
    )
    write_text_file(root / "tsconfig.json", "{\"compilerOptions\":{\"strict\":true}}\n")


def seed_backend_workspace(root: Path, with_infra: bool) -> None:
    write_text_file(
        root / "package.json",
        json.dumps({"name": "backend-only", "dependencies": {"@nestjs/core": "^11.0.0", "pg": "^9.0.0"}}, indent=2) + "\n",
    )
    if with_infra:
        write_text_file(root / "docker-compose.yml", "services:\n  postgres:\n    image: postgres:16\n")


def make_stale_plugin_fixture(repo_root: Path) -> dict[str, Any]:
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
    workspace_state = read_json_file(workspace_state_path)
    workspace_state["docker_policy"] = {"mode": "legacy-docker"}
    write_json_file(workspace_state_path, workspace_state)

    workstreams_index_path = Path(paths["workstreams_index"])
    workstreams_index = read_json_file(workstreams_index_path)
    for item in workstreams_index["items"]:
        if item["workstream_id"] == workstream_id:
            item["title"] = "default"
            item["kind"] = "default"
            item["scope_summary"] = "Primary product workstream."
            item["branch_hint"] = None
    write_json_file(workstreams_index_path, workstreams_index)

    canonical_register_path = Path(paths["current_workstream_stage_register"])
    canonical_register = read_json_file(canonical_register_path)
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
    write_json_file(canonical_register_path, canonical_register)

    canonical_brief_path = Path(paths["current_workstream_active_brief"])
    write_text_file(
        canonical_brief_path,
        "<!-- derived-mirror: true -->\n"
        f"<!-- mirror-of-workstream: {workstream_id} -->\n"
        "# Active Stage Brief\n\n"
        "Ship plugin runtime convergence and readiness hardening.\n",
    )
    return {
        "workstream_id": workstream_id,
        "paths": paths,
    }


def make_legacy_workspace_fixture(workspace: Path) -> dict[str, Any]:
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


def write_fake_bootstrap_tools(bin_dir: Path) -> None:
    npx_script = bin_dir / "npx"
    cargo_script = bin_dir / "cargo"
    npx_script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "cwd = pathlib.Path.cwd()\n"
        "args = sys.argv[1:]\n"
        "def ensure_project(path):\n"
        "    path.mkdir(parents=True, exist_ok=True)\n"
        "    (path / 'package.json').write_text(json.dumps({'name': path.name}, indent=2) + '\\n', encoding='utf-8')\n"
        "    (path / 'README.md').write_text('# Starter\\n', encoding='utf-8')\n"
        "def project_arg(index):\n"
        "    value = args[index] if len(args) > index else '.'\n"
        "    return cwd if value == '.' else cwd / value\n"
        "if args[:1] == ['create-next-app@latest']:\n"
        "    ensure_project(project_arg(1))\n"
        "elif args[:1] == ['create-expo-app@latest']:\n"
        "    ensure_project(project_arg(1))\n"
        "elif args[:1] == ['create-nx-workspace@latest']:\n"
        "    ensure_project(project_arg(1))\n"
        "elif args[:2] == ['@nestjs/cli', 'new']:\n"
        "    ensure_project(project_arg(2))\n"
        "elif args[:2] == ['nx', 'g']:\n"
        "    ensure_project(cwd)\n"
        "else:\n"
        "    ensure_project(cwd)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    cargo_script.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "cwd = pathlib.Path.cwd()\n"
        "args = sys.argv[1:]\n"
        "target = cwd / args[1] if args[:1] == ['new'] and len(args) > 1 else cwd\n"
        "target.mkdir(parents=True, exist_ok=True)\n"
        "(target / 'Cargo.toml').write_text('[package]\\nname = \"demo\"\\nversion = \"0.1.0\"\\n', encoding='utf-8')\n"
        "(target / 'src').mkdir(parents=True, exist_ok=True)\n"
        "(target / 'src' / 'main.rs').write_text('fn main() {}\\n', encoding='utf-8')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    npx_script.chmod(0o755)
    cargo_script.chmod(0o755)


def write_fake_adb(bin_dir: Path) -> None:
    adb_script = bin_dir / "adb"
    adb_script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
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
        "    raise SystemExit('unsupported fake adb invocation: ' + ' '.join(args))\n",
        encoding="utf-8",
    )
    adb_script.chmod(0o755)


def write_fake_host_setup_installer(bin_dir: Path, host_os: str) -> tuple[Path, Path | None]:
    installer_script = bin_dir / ("brew" if host_os == "macos" else "apt-get")
    installer_script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "log_path = pathlib.Path(os.environ['AGENTIUX_DEV_HOST_SETUP_LOG'])\n"
        "log_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "with log_path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "for env_name in ('AGENTIUX_DEV_TOOL_OVERRIDE_NODE', 'AGENTIUX_DEV_TOOL_OVERRIDE_ADB'):\n"
        "    target = os.environ.get(env_name)\n"
        "    if not target:\n"
        "        continue\n"
        "    path = pathlib.Path(target)\n"
        "    path.write_text('#!/bin/sh\\nexit 0\\n', encoding='utf-8')\n"
        "    path.chmod(0o755)\n"
    )
    installer_script.chmod(0o755)
    sudo_script: Path | None = None
    if host_os == "linux":
        sudo_script = bin_dir / "sudo"
        sudo_script.write_text(
            "#!/bin/sh\n"
            "\"$@\"\n",
            encoding="utf-8",
        )
        sudo_script.chmod(0o755)
    return installer_script, sudo_script


def call_mcp(script_path: Path, message: dict[str, Any], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    process = subprocess.Popen(
        [sys.executable, str(script_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env or os.environ.copy(),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    process.stdin.close()
    output = process.stdout.readline().strip()
    process.wait(timeout=5)
    if process.returncode != 0:
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise RuntimeError(stderr)
    if not output:
        raise RuntimeError(f"MCP script produced no output: {script_path}")
    return json.loads(output)


def enable_test_tool_overrides(env: dict[str, str] | None = None) -> dict[str, str]:
    resolved = dict(os.environ if env is None else env)
    resolved[TEST_TOOL_OVERRIDE_ALLOW_FLAG] = "1"
    return resolved


def synthetic_surface_inventory_path(source_plugin_root: Path) -> Path:
    return source_plugin_root / "tests" / "e2e" / SYNTHETIC_SURFACE_INVENTORY_FILENAME


def _synthetic_surface_key(path: str, symbol: str) -> str:
    return f"{path}::{symbol}"


def _discover_synthetic_surface_inventory(source_plugin_root: Path) -> list[dict[str, str]]:
    discoveries: dict[str, dict[str, str]] = {}
    inventory_helper_names = {
        "_synthetic_surface_key",
        "_discover_synthetic_surface_inventory",
        "audit_synthetic_surface_inventory",
        "synthetic_surface_inventory_path",
    }
    scan_roots = [source_plugin_root / "scripts", source_plugin_root / "tests"]
    for root in scan_roots:
        if not root.exists():
            continue
        for file_path in root.rglob("*.py"):
            if "__pycache__" in file_path.parts:
                continue
            relative_path = file_path.relative_to(source_plugin_root).as_posix()
            if file_path.name == SEMANTIC_CONTRACT_RUNNER_FILENAME:
                key = _synthetic_surface_key(relative_path, "__file__")
                discoveries[key] = {"path": relative_path, "symbol": "__file__"}
            if SYNTHETIC_SURFACE_TOKEN_PATTERN.search(file_path.stem):
                key = _synthetic_surface_key(relative_path, "__file__")
                discoveries[key] = {"path": relative_path, "symbol": "__file__"}
            module = ast.parse(file_path.read_text(encoding="utf-8"))
            for node in ast.walk(module):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if node.name in inventory_helper_names:
                    continue
                if not SYNTHETIC_SURFACE_TOKEN_PATTERN.search(node.name):
                    continue
                key = _synthetic_surface_key(relative_path, node.name)
                discoveries[key] = {"path": relative_path, "symbol": node.name}
    return [discoveries[key] for key in sorted(discoveries)]


def audit_synthetic_surface_inventory(source_plugin_root: Path) -> dict[str, Any]:
    inventory_path = synthetic_surface_inventory_path(source_plugin_root)
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    surfaces = payload.get("surfaces") if isinstance(payload, dict) else None
    if not isinstance(surfaces, list):
        raise ValueError(f"Synthetic surface inventory is invalid: {inventory_path}")
    inventory_keys: dict[str, dict[str, Any]] = {}
    for item in surfaces:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        symbol = str(item.get("symbol") or "__file__").strip() or "__file__"
        if not path:
            continue
        inventory_keys[_synthetic_surface_key(path, symbol)] = item
    discovered = _discover_synthetic_surface_inventory(source_plugin_root)
    discovered_keys = {_synthetic_surface_key(item["path"], item["symbol"]) for item in discovered}
    inventory_only = sorted(key for key in inventory_keys if key not in discovered_keys and Path(source_plugin_root / inventory_keys[key]["path"]).exists())
    missing_inventory = sorted(key for key in discovered_keys if key not in inventory_keys)
    missing_paths = sorted(
        key for key, item in inventory_keys.items() if not (source_plugin_root / str(item.get("path") or "")).exists()
    )
    return {
        "status": "passed" if not missing_inventory and not missing_paths else "failed",
        "inventory_path": str(inventory_path),
        "discovered": discovered,
        "inventory_keys": sorted(inventory_keys),
        "missing_inventory": missing_inventory,
        "inventory_only": inventory_only,
        "missing_paths": missing_paths,
    }


class FakeYouTrackHandler(BaseHTTPRequestHandler):
    server_version = "FakeYouTrack/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self) -> None:
        self._send_json({"error": "unauthorized"}, status=401)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.server.token}"  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        fixtures = self.server.fixtures  # type: ignore[attr-defined]
        if not parsed.path.startswith("/api/"):
            page = fixtures.get("external_pages", {}).get(parsed.path)
            if page is None:
                self._send_json({"error": "not found", "path": parsed.path}, status=404)
                return
            body = page["body"].encode("utf-8")
            self.send_response(page.get("status", 200))
            self.send_header("Content-Type", page.get("content_type", "text/html; charset=utf-8"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._authorized():
            self._unauthorized()
            return
        if parsed.path == "/api/users/me":
            self._send_json({"id": "1-1", "login": "alex", "name": "Alex"})
            return
        if parsed.path == "/api/admin/projects":
            self._send_json(fixtures["projects"])
            return
        if parsed.path == "/api/admin/customFieldSettings/customFields":
            self._send_json(fixtures["fields"])
            return
        if parsed.path == "/api/issues":
            raw_query = " ".join(query.get("query", []))
            items = list(fixtures["issues"])
            lowered = raw_query.lower()
            id_match = re.search(r"\bid:\s*([A-Z0-9-]+)", raw_query, re.IGNORECASE)
            if id_match:
                expected = id_match.group(1).upper()
                items = [item for item in items if item["idReadable"].upper() == expected or item["id"].upper() == expected]
            project_match = re.search(r"project:\s*([a-z0-9,\s-]+?)(?=\s+[a-z-]+:|$)", lowered)
            if project_match:
                allowed = {part.strip().upper() for part in project_match.group(1).split(",") if part.strip()}
                items = [item for item in items if item["project"]["shortName"].upper() in allowed]
            if "assignee: me" in lowered or "for: me" in lowered:
                items = [
                    item
                    for item in items
                    if any(
                        field.get("name") == "Assignee" and field.get("value", {}).get("name") == "Alex"
                        for field in item["customFields"]
                    )
                ]
            skip = int(query.get("$skip", ["0"])[0])
            top = int(query.get("$top", ["42"])[0])
            self._send_json(items[skip : skip + top])
            return
        issue_match = re.fullmatch(r"/api/issues/([^/]+)", parsed.path)
        if issue_match:
            issue_reference = urllib.parse.unquote(issue_match.group(1)).upper()
            for item in fixtures["issues"]:
                if item["id"].upper() == issue_reference or item["idReadable"].upper() == issue_reference:
                    self._send_json(item)
                    return
            self._send_json({"error": "not found", "path": parsed.path}, status=404)
            return
        if parsed.path == "/api/workItems":
            raw_query = " ".join(query.get("query", []))
            issue_match = re.search(r"issue id:\s*([A-Z0-9-]+)", raw_query, re.IGNORECASE)
            issue_id = issue_match.group(1).upper() if issue_match else None
            self._send_json(fixtures["work_items"].get(issue_id, []))
            return
        links_match = re.fullmatch(r"/api/issues/([^/]+)/links", parsed.path)
        if links_match:
            issue_resource_id = urllib.parse.unquote(links_match.group(1))
            self._send_json(fixtures["links"].get(issue_resource_id, []))
            return
        comments_match = re.fullmatch(r"/api/issues/([^/]+)/comments", parsed.path)
        if comments_match:
            issue_resource_id = urllib.parse.unquote(comments_match.group(1))
            top = int(query.get("$top", ["42"])[0])
            self._send_json(fixtures["comments"].get(issue_resource_id, [])[:top])
            return
        activities_match = re.fullmatch(r"/api/issues/([^/]+)/activities", parsed.path)
        if activities_match:
            issue_resource_id = urllib.parse.unquote(activities_match.group(1))
            top = int(query.get("$top", ["42"])[0])
            reverse = query.get("reverse", ["false"])[0].lower() == "true"
            items = list(fixtures["activities"].get(issue_resource_id, []))
            if reverse:
                items.reverse()
            self._send_json(items[:top])
            return
        self._send_json({"error": "not found", "path": parsed.path}, status=404)


class FakeYouTrackServer:
    def __init__(self) -> None:
        self.token = "perm:test-token"
        self.fixtures: dict[str, Any] = {
            "projects": [
                {"id": "0-0", "shortName": "SL", "name": "Shop Lab"},
                {"id": "0-1", "shortName": "APP", "name": "App"},
            ],
            "fields": [
                {"id": "10-0", "name": "Priority"},
                {"id": "10-1", "name": "Severity"},
                {"id": "10-2", "name": "Estimation"},
                {"id": "10-3", "name": "Assignee"},
                {"id": "10-4", "name": "State"},
            ],
            "issues": [
                {
                    "id": "2-1",
                    "idReadable": "SL-4591",
                    "summary": "Fix payment retry banner",
                    "description": "Short UI fix",
                    "updated": 1,
                    "created": 1,
                    "resolved": None,
                    "project": {"id": "0-0", "shortName": "SL", "name": "Shop Lab"},
                    "customFields": [
                        {"name": "Priority", "value": {"name": "5"}},
                        {"name": "Severity", "value": {"name": "Major"}},
                        {"name": "Estimation", "value": {"minutes": 30, "presentation": "30m"}},
                        {"name": "Assignee", "value": {"name": "Alex"}},
                        {"name": "State", "value": {"name": "Open"}},
                    ],
                },
                {
                    "id": "2-2",
                    "idReadable": "SL-4592",
                    "summary": "Investigate checkout tax mismatch",
                    "description": "Large backend bug",
                    "updated": 2,
                    "created": 1,
                    "resolved": None,
                    "project": {"id": "0-0", "shortName": "SL", "name": "Shop Lab"},
                    "customFields": [
                        {"name": "Priority", "value": {"name": "8"}},
                        {"name": "Severity", "value": {"name": "Normal"}},
                        {"name": "Estimation", "value": {"minutes": 360, "presentation": "6h"}},
                        {"name": "Assignee", "value": {"name": "Alex"}},
                        {"name": "State", "value": {"name": "Open"}},
                    ],
                },
                {
                    "id": "2-3",
                    "idReadable": "SL-4593",
                    "summary": "Fix small cart icon overlap",
                    "description": "Short UI polish",
                    "updated": 3,
                    "created": 1,
                    "resolved": None,
                    "project": {"id": "0-0", "shortName": "SL", "name": "Shop Lab"},
                    "customFields": [
                        {"name": "Priority", "value": {"name": "5"}},
                        {"name": "Severity", "value": {"name": "Major"}},
                        {"name": "Estimation", "value": {"minutes": 30, "presentation": "30m"}},
                        {"name": "Assignee", "value": {"name": "Alex"}},
                        {"name": "State", "value": {"name": "Open"}},
                    ],
                },
            ],
            "work_items": {
                "SL-4591": [{"id": "w1", "duration": {"minutes": 20, "presentation": "20m"}, "date": 1, "text": "support"}],
                "SL-4592": [{"id": "w2", "duration": {"minutes": 45, "presentation": "45m"}, "date": 1, "text": "investigation"}],
                "SL-4593": [],
            },
            "comments": {
                "2-1": [
                    {
                        "id": "c-1",
                        "text": "Banner should stay hidden when retry succeeds.",
                        "textPreview": "Banner should stay hidden when retry succeeds.",
                        "deleted": False,
                        "created": 11,
                        "updated": 12,
                        "author": {"id": "1-2", "login": "alex", "name": "Alex"},
                    }
                ],
                "2-2": [
                    {
                        "id": "c-2",
                        "text": "Mismatch seems related to stale tax cache on checkout refresh.",
                        "textPreview": "Mismatch seems related to stale tax cache on checkout refresh.",
                        "deleted": False,
                        "created": 21,
                        "updated": 22,
                        "author": {"id": "1-3", "login": "sam", "name": "Sam"},
                    }
                ],
                "2-3": [],
            },
            "activities": {
                "2-1": [
                    {
                        "id": "a-1",
                        "timestamp": 31,
                        "targetMember": "description",
                        "author": {"id": "1-2", "login": "alex", "name": "Alex"},
                        "category": {"id": "cat-1", "name": "DescriptionCategory"},
                        "field": {"name": "Description"},
                    }
                ],
                "2-2": [
                    {
                        "id": "a-2",
                        "timestamp": 41,
                        "targetMember": "State",
                        "author": {"id": "1-2", "login": "alex", "name": "Alex"},
                        "category": {"id": "cat-3", "name": "StateCategory"},
                        "field": {"name": "State"},
                    }
                ],
                "2-3": [],
            },
            "links": {
                "2-1": [
                    {
                        "direction": "OUTWARD",
                        "linkType": {
                            "name": "Depend",
                            "sourceToTarget": "is required for",
                            "targetToSource": "depends on",
                            "localizedSourceToTarget": "is required for",
                            "localizedTargetToSource": "depends on",
                        },
                        "issues": [
                            {
                                "id": "2-2",
                                "idReadable": "SL-4592",
                                "summary": "Investigate checkout tax mismatch",
                                "resolved": None,
                                "updated": 2,
                                "project": {"id": "0-0", "shortName": "SL", "name": "Shop Lab"},
                            }
                        ],
                    }
                ],
                "2-2": [],
                "2-3": [],
            },
            "external_pages": {
                "/docs/payment-retry": {
                    "content_type": "text/html; charset=utf-8",
                    "body": "<html><body><main><h1>Payment Retry Guide</h1></main></body></html>",
                },
                "/admin/payment-retry": {
                    "content_type": "text/html; charset=utf-8",
                    "body": "<html><body><form><input type='text'><input type='password'></form></body></html>",
                },
            },
        }
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.base_url: str | None = None

    def __enter__(self) -> "FakeYouTrackServer":
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeYouTrackHandler)
        self.httpd.token = self.token  # type: ignore[attr-defined]
        self.httpd.fixtures = self.fixtures  # type: ignore[attr-defined]
        host, port = self.httpd.server_address
        self.base_url = f"http://{host}:{port}"
        for issue in self.fixtures["issues"]:
            if issue["idReadable"] == "SL-4591":
                issue["description"] = (
                    f"Short UI fix. Public note: {self.base_url}/docs/payment-retry "
                    f"Admin note: {self.base_url}/admin/payment-retry "
                    f"Related issue mention: {self.base_url}/issue/SL-4592"
                )
            elif issue["idReadable"] == "SL-4592":
                issue["description"] = f"Large backend bug. Cross-reference: {self.base_url}/issue/SL-4591"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)


def launch_dashboard_gui(
    plugin_root: Path,
    workspace: Path,
    env: dict[str, str],
    *,
    health_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    result = completed_process(
        python_script_command(
            plugin_root / "scripts" / "agentiux_dev_gui.py",
            ["launch", "--workspace", str(workspace), "--health-timeout", str(health_timeout_seconds)],
        ),
        cwd=workspace,
        env=env,
    )
    return json.loads(result.stdout)


def stop_dashboard_gui(plugin_root: Path, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    result = completed_process(
        python_script_command(plugin_root / "scripts" / "agentiux_dev_gui.py", ["stop"]),
        cwd=cwd,
        env=env,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {"status": "stopped"}


def wait_for_dashboard_gui_shutdown(
    plugin_root: Path,
    cwd: Path,
    env: dict[str, str],
    *,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest = {"status": "unknown"}
    while time.monotonic() < deadline:
        try:
            result = completed_process(
                python_script_command(plugin_root / "scripts" / "agentiux_dev_gui.py", ["status"]),
                cwd=cwd,
                env=env,
            )
            latest = json.loads(result.stdout) if result.stdout.strip() else {"status": "stopped"}
        except Exception:  # noqa: BLE001
            return {"status": "stopped"}
        if latest.get("status") != "running":
            time.sleep(0.25)
            return latest
        time.sleep(0.25)
    return latest


def run_browser_layout_audit(
    plugin_root: Path,
    repo_root: Path,
    env: dict[str, str],
    output_root: Path,
    target_url: str,
    label: str,
    width: int,
    height: int,
    *,
    screenshot: bool = True,
    interaction_script: str | None = None,
    container_selectors: list[str] | None = None,
) -> dict[str, Any]:
    screenshot_path = output_root / f"{label}.png"
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
    for selector in container_selectors or []:
        audit_command.extend(["--container-selector", selector])
    result = completed_process(audit_command, cwd=repo_root, env=env)
    return json.loads(result.stdout)


def dashboard_budget_results(
    *,
    cold_start_ms: float,
    payload_bytes: dict[str, int],
    request_timings_ms: dict[str, float],
    render_timings_ms: dict[str, dict[str, float | None]],
) -> dict[str, Any]:
    first_usable_budget = DASHBOARD_BUDGETS["render_timings_ms"]["first_usable_render"]
    first_usable_measurements = {
        label: metrics.get("first_usable_render")
        for label, metrics in render_timings_ms.items()
        if metrics.get("first_usable_render") is not None
    }
    max_first_usable = max(first_usable_measurements.values(), default=None)
    failing_first_usable_labels = sorted(
        label
        for label, value in first_usable_measurements.items()
        if value is not None and float(value) > first_usable_budget
    )
    return {
        "cold_start_ms": {
            "actual": cold_start_ms,
            "budget": DASHBOARD_BUDGETS["cold_start_ms"],
            "within_budget": cold_start_ms <= DASHBOARD_BUDGETS["cold_start_ms"],
        },
        "overview_payload_bytes": {
            "actual": int(payload_bytes.get("overview") or 0),
            "budget": DASHBOARD_BUDGETS["payload_bytes"]["overview"],
            "within_budget": int(payload_bytes.get("overview") or 0) <= DASHBOARD_BUDGETS["payload_bytes"]["overview"],
        },
        "bootstrap_payload_bytes": {
            "actual": int(payload_bytes.get("bootstrap") or 0),
            "budget": DASHBOARD_BUDGETS["payload_bytes"]["bootstrap"],
            "within_budget": int(payload_bytes.get("bootstrap") or 0) <= DASHBOARD_BUDGETS["payload_bytes"]["bootstrap"],
        },
        "plan_panel_payload_bytes": {
            "actual": int(payload_bytes.get("plan_panel") or 0),
            "budget": DASHBOARD_BUDGETS["payload_bytes"]["plan_panel"],
            "within_budget": int(payload_bytes.get("plan_panel") or 0) <= DASHBOARD_BUDGETS["payload_bytes"]["plan_panel"],
        },
        "bootstrap_fetch_ms": {
            "actual": float(request_timings_ms.get("bootstrap") or 0),
            "budget": DASHBOARD_BUDGETS["request_timings_ms"]["bootstrap"],
            "within_budget": float(request_timings_ms.get("bootstrap") or 0)
            <= DASHBOARD_BUDGETS["request_timings_ms"]["bootstrap"],
        },
        "plan_panel_fetch_ms": {
            "actual": float(request_timings_ms.get("plan_panel") or 0),
            "budget": DASHBOARD_BUDGETS["request_timings_ms"]["plan_panel"],
            "within_budget": float(request_timings_ms.get("plan_panel") or 0)
            <= DASHBOARD_BUDGETS["request_timings_ms"]["plan_panel"],
        },
        "first_usable_render_ms": {
            "actual": max_first_usable,
            "budget": first_usable_budget,
            "within_budget": not failing_first_usable_labels,
            "audited_view_count": len(first_usable_measurements),
            "failing_labels": failing_first_usable_labels,
        },
    }


def dashboard_check(repo_root: Path, plugin_root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    canonical_repo_root = repo_root.resolve()
    with tempfile.TemporaryDirectory(prefix="agentiux-dashboard-check-") as temp_dir:
        temp_root = Path(temp_dir)
        env["AGENTIUX_DEV_STATE_ROOT"] = str(temp_root / "state")
        env["AGENTIUX_DEV_PLUGIN_ROOT"] = str(plugin_root)
        with temporary_env(
            {
                "AGENTIUX_DEV_STATE_ROOT": env["AGENTIUX_DEV_STATE_ROOT"],
                "AGENTIUX_DEV_PLUGIN_ROOT": env["AGENTIUX_DEV_PLUGIN_ROOT"],
            }
        ):
            init_workspace(canonical_repo_root)
            create_workstream(
                canonical_repo_root,
                "Dashboard Layout Audit Fixture",
                kind="feature",
                scope_summary="Exercise cockpit-first dashboard cards and stage state for browser layout auditing.",
            )
            fixture_snapshot = dashboard_snapshot(canonical_repo_root)
        cold_start_ms = 0.0
        health: dict[str, Any] = {}
        snapshot: dict[str, Any] = {}
        cockpit_snapshot: dict[str, Any] = {}
        bootstrap_snapshot: dict[str, Any] = {}
        plan_panel_snapshot: dict[str, Any] = {}
        auth_payload: dict[str, Any] = {}
        auth_sessions_payload: dict[str, Any] = {}
        notes_payload: dict[str, Any] = {}
        analytics_payload: dict[str, Any] = {}
        learnings_payload: dict[str, Any] = {}
        payload_bytes: dict[str, int] = {}
        request_timings_ms: dict[str, float] = {}
        render_timings_ms: dict[str, dict[str, float | None]] = {}
        audit_results: list[dict[str, Any]] = []
        deep_link_results: list[dict[str, Any]] = []
        history_navigation_audit: dict[str, Any] | None = None
        url = ""
        launch_started_at = time.monotonic()
        try:
            payload = launch_dashboard_gui(
                plugin_root,
                canonical_repo_root,
                env,
                health_timeout_seconds=60.0,
            )
            cold_start_ms = round((time.monotonic() - launch_started_at) * 1000, 2)
            url = payload["url"]
            health, _health_bytes, _health_ms = read_json_url(f"{url}/health")
            encoded_workspace = urllib.parse.quote(str(canonical_repo_root), safe="")
            overview_url = f"{url}/api/dashboard"
            cockpit_url_api = f"{url}/api/workspace-cockpit?workspace={encoded_workspace}"
            bootstrap_url = f"{url}/api/dashboard-bootstrap?workspace={encoded_workspace}&panel=now"
            plan_panel_url = f"{url}/api/workspace-panel?workspace={encoded_workspace}&panel=plan"
            read_json_url(overview_url)
            read_json_url(cockpit_url_api)
            read_json_url(bootstrap_url)
            read_json_url(plan_panel_url)
            snapshot, overview_bytes, overview_fetch_ms, overview_samples = sample_json_url(overview_url)
            cockpit_snapshot, cockpit_bytes, cockpit_fetch_ms, cockpit_samples = sample_json_url(cockpit_url_api)
            bootstrap_snapshot, bootstrap_bytes, bootstrap_fetch_ms, bootstrap_samples = sample_json_url(bootstrap_url)
            plan_panel_snapshot, plan_panel_bytes, plan_panel_fetch_ms, plan_panel_samples = sample_json_url(plan_panel_url)
            auth_payload, _auth_bytes, _auth_ms = read_json_url(f"{url}/api/auth/profiles?workspace={encoded_workspace}")
            auth_sessions_payload, _auth_sessions_bytes, _auth_sessions_ms = read_json_url(
                f"{url}/api/auth/sessions?workspace={encoded_workspace}"
            )
            notes_payload, _notes_bytes, _notes_ms = read_json_url(f"{url}/api/project-notes?workspace={encoded_workspace}")
            analytics_payload, _analytics_bytes, _analytics_ms = read_json_url(f"{url}/api/analytics?workspace={encoded_workspace}")
            learnings_payload, _learnings_bytes, _learnings_ms = read_json_url(f"{url}/api/learnings?workspace={encoded_workspace}")
            cockpit_url = f"{url}/workspaces/{urllib.parse.quote(str(canonical_repo_root), safe='')}?panel=now"
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

            for label, width, height in (("cockpit-now-desktop", 1440, 1800), ("cockpit-now-mobile", 390, 2200)):
                audit_results.append(
                    run_browser_layout_audit(
                        plugin_root,
                        canonical_repo_root,
                        env,
                        temp_root,
                        cockpit_url,
                        label,
                        width,
                        height,
                        container_selectors=audit_container_selectors,
                    )
                )
            history_navigation_audit = run_browser_layout_audit(
                plugin_root,
                canonical_repo_root,
                env,
                temp_root,
                cockpit_url,
                "cockpit-history-navigation",
                1280,
                1600,
                screenshot=False,
                interaction_script=history_interaction_script,
                container_selectors=audit_container_selectors,
            )
            deep_link_results.extend(
                [
                    run_browser_layout_audit(
                        plugin_root,
                        canonical_repo_root,
                        env,
                        temp_root,
                        f"{url}/#overview",
                        "overview-deep-link",
                        1280,
                        1400,
                        screenshot=False,
                        container_selectors=audit_container_selectors,
                    ),
                    run_browser_layout_audit(
                        plugin_root,
                        canonical_repo_root,
                        env,
                        temp_root,
                        f"{url}/workspaces/{urllib.parse.quote(str(canonical_repo_root), safe='')}?panel=plan",
                        "cockpit-plan-deep-link",
                        1280,
                        1600,
                        screenshot=False,
                        container_selectors=audit_container_selectors,
                    ),
                ]
            )
        finally:
            with suppress(Exception):
                stop_dashboard_gui(plugin_root, canonical_repo_root, env)
            with suppress(Exception):
                wait_for_dashboard_gui_shutdown(plugin_root, canonical_repo_root, env)
    if not health.get("ok"):
        raise AssertionError("Dashboard health check failed")
    if snapshot.get("schema_version") != 2:
        raise AssertionError("Unexpected dashboard schema version")
    if snapshot.get("plugin", {}).get("name") != PLUGIN_NAME:
        raise AssertionError("Unexpected dashboard plugin payload")
    if Path(fixture_snapshot.get("workspace_cockpit", {}).get("workspace_path") or "").resolve() != canonical_repo_root:
        raise AssertionError("Dashboard fixture did not initialize the expected workspace cockpit")
    design_state = (fixture_snapshot.get("workspace_cockpit", {}).get("plan") or {}).get("design_state") or {}
    if "design_summary" not in design_state or "testability_summary" not in design_state or "semantic_summary" not in design_state:
        raise AssertionError("Dashboard fixture snapshot did not expose compact design/testability/semantic summaries")
    if "auth" not in (cockpit_snapshot.get("integrations") or {}):
        raise AssertionError("Dashboard cockpit is missing auth integration payload")
    if "memory" not in cockpit_snapshot:
        raise AssertionError("Dashboard cockpit is missing memory payload")
    if not bootstrap_snapshot.get("workspace_shell") or not bootstrap_snapshot.get("panel_payload"):
        raise AssertionError("Dashboard bootstrap payload is incomplete")
    if Path(bootstrap_snapshot.get("selected_workspace_path") or "").resolve() != canonical_repo_root:
        raise AssertionError("Dashboard bootstrap did not resolve the requested workspace")
    if plan_panel_snapshot.get("active_panel") != "plan":
        raise AssertionError("Workspace panel endpoint did not resolve the requested panel")
    plan_panel_design_state = (plan_panel_snapshot.get("panel_payload") or {}).get("design_state") or {}
    if (
        "design_summary" not in plan_panel_design_state
        or "testability_summary" not in plan_panel_design_state
        or "semantic_summary" not in plan_panel_design_state
    ):
        raise AssertionError("Plan panel payload did not expose compact design/testability/semantic summaries")
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
        raise AssertionError("Overview deep link did not render the overview screen.")
    plan_deep_link = next(item for item in deep_link_results if item.get("label") == "cockpit-plan-deep-link")
    if plan_deep_link.get("location", {}).get("search") != "?panel=plan":
        raise AssertionError("Plan deep link did not preserve the panel query parameter.")
    if "plan" not in (plan_deep_link.get("active_panel_ids") or []):
        raise AssertionError("Plan deep link did not render the requested panel.")
    if Path(plan_deep_link.get("selected_workspace_path") or "").resolve() != canonical_repo_root:
        raise AssertionError("Plan deep link did not keep the requested workspace selected.")
    history_result = (history_navigation_audit or {}).get("interaction_result") or {}
    if (history_result.get("after_back") or {}).get("panel") != "now":
        raise AssertionError("History back navigation did not restore the previous panel.")
    if (history_result.get("after_forward") or {}).get("panel") != "plan":
        raise AssertionError("History forward navigation did not restore the requested panel.")
    if Path(history_result.get("selected_workspace_path") or "").resolve() != canonical_repo_root:
        raise AssertionError("History navigation lost the selected workspace.")
    for audit in all_audits:
        debug = audit.get("dashboard_debug") or {}
        request_counts = debug.get("requestCounts") or {}
        if int(request_counts.get("bootstrap") or 0) != 1:
            raise AssertionError(f"Dashboard audit `{audit.get('label')}` did not stay on a single bootstrap request path.")
        if int(request_counts.get("overview") or 0) > 0 or int(request_counts.get("cockpit") or 0) > 0:
            raise AssertionError(
                f"Dashboard audit `{audit.get('label')}` fell back to legacy overview/cockpit requests."
            )
        if (audit.get("timings") or {}).get("first_usable_render_ms") is None:
            raise AssertionError(f"Dashboard audit `{audit.get('label')}` did not expose first usable render timing.")
    if history_navigation_audit:
        history_counts_by_stage = {
            stage: ((history_result.get(stage) or {}).get("requestCounts") or {})
            for stage in ("initial", "after_plan", "after_cached_plan", "after_back", "after_forward")
        }
        initial_counts = history_counts_by_stage["initial"]
        if int(initial_counts.get("bootstrap") or 0) != 1:
            raise AssertionError("Dashboard shell load did not use exactly one bootstrap request.")
        for stage, counts in history_counts_by_stage.items():
            if int(counts.get("bootstrap") or 0) != 1:
                raise AssertionError(f"History navigation stage `{stage}` triggered an extra bootstrap request.")
            if int(counts.get("overview") or 0) != 0 or int(counts.get("cockpit") or 0) != 0:
                raise AssertionError(f"History navigation stage `{stage}` fell back to legacy overview/cockpit requests.")
        initial_panel_count = int(initial_counts.get("panel") or 0)
        after_plan_panel_count = int(history_counts_by_stage["after_plan"].get("panel") or 0)
        if after_plan_panel_count < initial_panel_count:
            raise AssertionError("First plan navigation unexpectedly decreased the panel fetch count.")
        if int(history_counts_by_stage["after_cached_plan"].get("panel") or 0) != after_plan_panel_count:
            raise AssertionError("Cached plan navigation triggered a duplicate panel fetch.")
        if int(history_counts_by_stage["after_back"].get("panel") or 0) != after_plan_panel_count:
            raise AssertionError("History back unexpectedly changed the panel fetch count.")
        if int(history_counts_by_stage["after_forward"].get("panel") or 0) != after_plan_panel_count:
            raise AssertionError("History forward did not preserve the cached panel payload.")
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
    payload_bytes = {
        "overview": overview_bytes,
        "legacy_cockpit": cockpit_bytes,
        "legacy_combined": sequential_payload_bytes,
        "bootstrap": bootstrap_bytes,
        "plan_panel": plan_panel_bytes,
    }
    request_timings_ms = {
        "overview": overview_fetch_ms,
        "legacy_cockpit": cockpit_fetch_ms,
        "legacy_combined": sequential_fetch_ms,
        "bootstrap": bootstrap_fetch_ms,
        "plan_panel": plan_panel_fetch_ms,
    }
    budget_results = dashboard_budget_results(
        cold_start_ms=cold_start_ms,
        payload_bytes=payload_bytes,
        request_timings_ms=request_timings_ms,
        render_timings_ms=render_timings_ms,
    )
    failing_budget_keys = [key for key, result in budget_results.items() if not result["within_budget"]]
    if failing_budget_keys:
        raise AssertionError(
            "Dashboard budgets exceeded: "
            + "; ".join(
                f"{key}={budget_results[key]['actual']} budget={budget_results[key]['budget']}"
                for key in failing_budget_keys
            )
        )
    deep_link_assertions = {
        "overview": {
            "route_hash_preserved": overview_deep_link.get("location", {}).get("hash") == "#overview",
            "overview_screen_rendered": "dashboard-overview" in (overview_deep_link.get("active_screen_ids") or []),
        },
        "workspace_plan": {
            "panel_query_preserved": plan_deep_link.get("location", {}).get("search") == "?panel=plan",
            "requested_panel_rendered": "plan" in (plan_deep_link.get("active_panel_ids") or []),
            "workspace_preserved": Path(plan_deep_link.get("selected_workspace_path") or "").resolve() == canonical_repo_root,
        },
        "history_navigation": {
            "back_restored_previous_panel": (history_result.get("after_back") or {}).get("panel") == "now",
            "forward_restored_requested_panel": (history_result.get("after_forward") or {}).get("panel") == "plan",
            "workspace_preserved": Path(history_result.get("selected_workspace_path") or "").resolve() == canonical_repo_root,
        },
    }
    request_counts_by_audit = {
        item.get("label"): ((item.get("dashboard_debug") or {}).get("requestCounts") or {})
        for item in all_audits
        if item and item.get("label")
    }
    history_request_counts = {
        stage: ((history_result.get(stage) or {}).get("requestCounts") or {})
        for stage in ("initial", "after_plan", "after_cached_plan", "after_back", "after_forward")
    }
    return {
        "check": "dashboard-check",
        "url": url,
        "schema_version": snapshot["schema_version"],
        "workspace_count": snapshot["overview"]["workspace_count"],
        "cold_start_ms": cold_start_ms,
        "render_timings_ms": render_timings_ms,
        "payload_bytes": payload_bytes,
        "design_summary": design_state.get("design_summary") or {},
        "testability_summary": design_state.get("testability_summary") or {},
        "request_timings_ms": request_timings_ms,
        "request_timing_samples_ms": {
            "overview": overview_samples,
            "legacy_cockpit": cockpit_samples,
            "bootstrap": bootstrap_samples,
            "plan_panel": plan_panel_samples,
        },
        "budget_results": budget_results,
        "deep_link_assertions": deep_link_assertions,
        "request_counts": request_counts_by_audit,
        "history_request_counts": history_request_counts,
        "audits": all_audits,
        "history_audit": history_navigation_audit,
        "fixture_snapshot": fixture_snapshot,
        "health": health,
    }


def fixture_definition(fixture_id: str) -> dict[str, Any]:
    for fixture in REPO_FIXTURES:
        if fixture.get("fixture_id") == fixture_id:
            return copy.deepcopy(fixture)
    raise KeyError(f"Unknown e2e fixture: {fixture_id}")


def fixture_project_root(source_plugin_root: Path, fixture: dict[str, Any]) -> Path:
    return source_plugin_root / "tests" / "e2e" / "projects" / fixture["fixture_id"]


def fixture_tool_root(source_plugin_root: Path) -> Path:
    return source_plugin_root / "tests" / "e2e" / "tools"


def isolated_plugin_env(run_root: Path, plugin_root: Path) -> dict[str, str]:
    state_root = run_root / "state"
    install_root = run_root / "install-root"
    marketplace_path = run_root / "marketplace.json"
    state_root.mkdir(parents=True, exist_ok=True)
    install_root.mkdir(parents=True, exist_ok=True)
    env = enable_test_tool_overrides()
    env["AGENTIUX_DEV_STATE_ROOT"] = str(state_root)
    env["AGENTIUX_DEV_PLUGIN_ROOT"] = str(plugin_root)
    env["AGENTIUX_DEV_INSTALL_ROOT"] = str(install_root)
    env["AGENTIUX_DEV_MARKETPLACE_PATH"] = str(marketplace_path)
    return env


def install_isolated_plugin(source_plugin_root: Path, run_root: Path) -> IsolatedPluginRun:
    install_root = run_root / "installed-plugin" / "agentiux-dev"
    marketplace_path = run_root / "marketplace.json"
    state_root = run_root / "state"
    install_result = install_plugin(source_plugin_root, install_root, marketplace_path)
    env = enable_test_tool_overrides()
    env["AGENTIUX_DEV_PLUGIN_ROOT"] = str(install_root)
    env["AGENTIUX_DEV_STATE_ROOT"] = str(state_root)
    env["AGENTIUX_DEV_INSTALL_ROOT"] = str(install_root)
    env["AGENTIUX_DEV_MARKETPLACE_PATH"] = str(marketplace_path)
    return IsolatedPluginRun(
        run_root=run_root,
        state_root=state_root,
        install_root=install_root,
        marketplace_path=marketplace_path,
        env=env,
        install_result=install_result,
    )


def state_script(installed_root: Path) -> Path:
    return installed_root / "scripts" / "agentiux_dev_state.py"


def plugin_command(installed_root: Path, *args: str) -> list[str]:
    return [sys.executable, str(state_script(installed_root)), *args]


def run_command_json(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    result = completed_process(cmd, cwd=cwd, env=env)
    stdout = result.stdout.strip()
    if not stdout:
        raise ValueError(f"Command produced no JSON output: {' '.join(cmd)}")
    return json.loads(stdout)


def git_init_repo(path: Path) -> None:
    completed_process(["git", "init", "-b", "main"], cwd=path)
    completed_process(["git", "config", "user.name", "AgentiUX E2E"], cwd=path)
    completed_process(["git", "config", "user.email", "e2e@example.com"], cwd=path)
    completed_process(["git", "add", "."], cwd=path)
    completed_process(["git", "commit", "-m", "chore: seed fixture"], cwd=path)


def prepare_repo_clone(source_plugin_root: Path, repo_root: Path) -> None:
    contract_runner_source = fixture_tool_root(source_plugin_root) / SEMANTIC_CONTRACT_RUNNER_FILENAME
    if not contract_runner_source.exists():
        raise FileNotFoundError(f"Missing semantic contract runner fixture: {contract_runner_source}")
    contract_runner_target = repo_root / "tools" / SEMANTIC_CONTRACT_RUNNER_FILENAME
    contract_runner_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(contract_runner_source, contract_runner_target)
    contract_runner_target.chmod(0o755)


def create_fixture_repo(run_root: Path, source_plugin_root: Path, fixture: dict[str, Any]) -> Path:
    repo_root = run_root / "repos" / fixture["repo_name"]
    tracked_fixture_root = fixture_project_root(source_plugin_root, fixture)
    if not tracked_fixture_root.exists():
        raise FileNotFoundError(f"Missing tracked fixture project: {tracked_fixture_root}")
    shutil.copytree(tracked_fixture_root, repo_root)
    prepare_repo_clone(source_plugin_root, repo_root)
    git_init_repo(repo_root)
    return repo_root


def create_named_fixture_repo(run_root: Path, source_plugin_root: Path, fixture_id: str) -> Path:
    return create_fixture_repo(run_root, source_plugin_root, fixture_definition(fixture_id))


def verification_recipe(repo_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    helper_args: list[str] = []
    for helper_file in fixture["helper_files"]:
        helper_args.extend(["--helper-file", helper_file])
    return {
        "baseline_policy": {
            "canonical_baselines": "project_owned",
            "transient_artifacts": "external_state_only",
        },
        "cases": [
            {
                "id": fixture["case_id"],
                "title": fixture["case_id"].replace("-", " ").title(),
                "surface_type": fixture["surface_type"],
                "runner": fixture["runner"],
                "changed_path_globs": [fixture["changed_path"]],
                "host_requirements": ["python"],
                "cwd": ".",
                "argv": [
                    sys.executable,
                    f"tools/{SEMANTIC_CONTRACT_RUNNER_FILENAME}",
                    "--runner",
                    fixture["runner"],
                    "--repo-root",
                    str(repo_root),
                    "--artifact-name",
                    fixture["artifact_name"],
                    *helper_args,
                ],
                "target": {"route": "/", "screen_id": fixture["target"]["target_id"]},
                "device_or_viewport": {"viewport": "1280x800"},
                "semantic_assertions": {
                    "enabled": True,
                    "report_path": f"{fixture['case_id']}-semantic.json",
                    "required_checks": SEMANTIC_REQUIRED_CHECKS,
                    "targets": [fixture["target"]],
                    "auto_scan": True,
                    "heuristics": ["interactive_visibility_scan"],
                    "artifacts": {
                        "target_screenshots": True,
                        "report_copy": True,
                    },
                },
            }
        ],
        "suites": [
            {
                "id": fixture["suite_id"],
                "title": fixture["suite_id"].replace("-", " ").title(),
                "case_ids": [fixture["case_id"]],
            },
            {
                "id": "full",
                "title": "Full",
                "case_ids": [fixture["case_id"]],
            },
        ],
    }


def exercise_external_fixture(
    run_root: Path,
    source_plugin_root: Path,
    isolated_run: IsolatedPluginRun,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    env = isolated_run.env
    repo_root = create_fixture_repo(run_root, source_plugin_root, fixture)
    detect = run_command_json(plugin_command(isolated_run.install_root, "detect-workspace", "--workspace", str(repo_root)), env=env)
    preview = run_command_json(plugin_command(isolated_run.install_root, "preview-init", "--workspace", str(repo_root)), env=env)
    init = run_command_json(plugin_command(isolated_run.install_root, "init-workspace", "--workspace", str(repo_root)), env=env)
    refresh_before_reset = run_command_json(
        plugin_command(isolated_run.install_root, "refresh-context-index", "--workspace", str(repo_root)),
        env=env,
    )

    learning_entry_path = run_root / "learning-entries" / f"{fixture['repo_name']}.json"
    write_json_file(learning_entry_path, fixture["learning_entry"])
    learning_entry = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "write-learning-entry",
            "--workspace",
            str(repo_root),
            "--entry-file",
            str(learning_entry_path),
        ),
        env=env,
    )

    reset_preview = run_command_json(
        plugin_command(isolated_run.install_root, "preview-reset-workspace-state", "--workspace", str(repo_root)),
        env=env,
    )
    reset = run_command_json(
        plugin_command(isolated_run.install_root, "reset-workspace-state", "--workspace", str(repo_root)),
        env=env,
    )
    reinit = run_command_json(plugin_command(isolated_run.install_root, "init-workspace", "--workspace", str(repo_root)), env=env)
    workstream = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "create-workstream",
            "--workspace",
            str(repo_root),
            "--title",
            fixture["case_id"].replace("-", " ").title(),
            "--kind",
            "feature",
            "--scope-summary",
            f"Exercise {fixture['runner']} helper contract end to end.",
        ),
        env=env,
    )
    workstream_id = workstream["created_workstream_id"]
    route = run_command_json(
        plugin_command(isolated_run.install_root, "show-intent-route", "--request-text", fixture["route_query"]),
        env=env,
    )
    refresh_one = run_command_json(
        plugin_command(isolated_run.install_root, "refresh-context-index", "--workspace", str(repo_root)),
        env=env,
    )
    refresh_two = run_command_json(
        plugin_command(isolated_run.install_root, "refresh-context-index", "--workspace", str(repo_root)),
        env=env,
    )
    search = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "search-context-index",
            "--workspace",
            str(repo_root),
            "--route-id",
            "verification",
            "--query-text",
            fixture["search_query"],
        ),
        env=env,
    )
    pack_one = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "show-workspace-context-pack",
            "--workspace",
            str(repo_root),
            "--route-id",
            "verification",
            "--request-text",
            fixture["route_query"],
        ),
        env=env,
    )
    pack_two = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "show-workspace-context-pack",
            "--workspace",
            str(repo_root),
            "--route-id",
            "verification",
            "--request-text",
            fixture["route_query"],
        ),
        env=env,
    )
    helper_before = run_command_json(
        plugin_command(isolated_run.install_root, "show-verification-helper-catalog", "--workspace", str(repo_root)),
        env=env,
    )
    sync = run_command_json(
        plugin_command(isolated_run.install_root, "sync-verification-helpers", "--workspace", str(repo_root)),
        env=env,
    )
    helper_after = run_command_json(
        plugin_command(isolated_run.install_root, "show-verification-helper-catalog", "--workspace", str(repo_root)),
        env=env,
    )
    recipe_path = run_root / "recipes" / f"{fixture['repo_name']}.json"
    write_json_file(recipe_path, verification_recipe(repo_root, fixture))
    write_recipes = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "write-verification-recipes",
            "--workspace",
            str(repo_root),
            "--workstream-id",
            workstream_id,
            "--recipe-file",
            str(recipe_path),
        ),
        env=env,
    )
    audit = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "audit-verification-coverage",
            "--workspace",
            str(repo_root),
            "--workstream-id",
            workstream_id,
        ),
        env=env,
    )
    verification_selection = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "resolve-verification",
            "--workspace",
            str(repo_root),
            "--workstream-id",
            workstream_id,
            "--confirm-heuristics",
            "--changed-path",
            fixture["changed_path"],
        ),
        env=env,
    )
    run = run_command_json(
        plugin_command(
            isolated_run.install_root,
            "run-verification-case",
            "--workspace",
            str(repo_root),
            "--workstream-id",
            workstream_id,
            "--case-id",
            fixture["case_id"],
            "--wait",
        ),
        env=env,
    )
    assert any(profile in detect["selected_profiles"] for profile in fixture["profile_expectations"])
    assert preview["planning_policy"]["explicit_stage_plan_required"] is True
    assert init["workspace_state"]["workspace_path"] == str(repo_root.resolve())
    assert refresh_before_reset["status"] in {"refreshed", "fresh"}
    assert learning_entry["entry"]["entry_id"] == fixture["learning_entry"]["entry_id"]
    assert reset_preview["workspace_root_exists"] is True
    assert reset_preview["context_cache_exists"] is True
    assert reset_preview["analytics_cleanup"]["learning_paths"]
    assert reset["removed_workspace_root"] is True
    assert reset["removed_registry_entry"] is True
    assert reset["removed_context_cache_root"] is True
    assert reset["analytics_cleanup"]["removed_learning_paths"]
    assert reset["analytics_cleanup"]["removed_event_paths"]
    assert reset["post_reset_preview"]["already_initialized"] is False
    assert reinit["workspace_state"]["workspace_path"] == str(repo_root.resolve())
    assert route["resolved_route"]["route_id"] == "verification"
    assert route["resolution_status"] in {"matched", "exact"}
    assert refresh_one["status"] == "refreshed"
    assert refresh_two["status"] == "fresh"
    assert search["resolved_route"]["route_id"] == "verification"
    assert search["matches"]
    assert pack_one["cache_status"] == "miss"
    assert pack_two["cache_status"] == "hit"
    assert helper_before["version_status"] == "not_synced"
    assert sync["materialization"]["status"] == "synced"
    assert helper_after["version_status"] == "synced"
    assert write_recipes["cases"][0]["id"] == fixture["case_id"]
    assert not any(gap["gap_id"] == "verification-helper-bundle-not-synced" for gap in audit["gaps"])
    assert verification_selection["selection_status"] == "resolved"
    assert run["status"] == "passed"
    assert run["cases"][0]["semantic_assertions"]["status"] == "passed"
    if fixture["runner"] in {"detox-visual", "android-compose-screenshot"}:
        assert run["cases"][0]["native_layout_audit"]["status"] == "passed"
    return {
        "fixture_id": fixture["fixture_id"],
        "repo_name": fixture["repo_name"],
        "repo_root": str(repo_root),
        "runner": fixture["runner"],
        "workstream_id": workstream_id,
        "reset_removed_context_cache_root": reset["removed_context_cache_root"],
        "reset_removed_learning_paths": len(reset["analytics_cleanup"]["removed_learning_paths"]),
        "reset_removed_event_paths": len(reset["analytics_cleanup"]["removed_event_paths"]),
        "route_status": route["resolution_status"],
        "context_refresh_statuses": [refresh_one["status"], refresh_two["status"]],
        "context_pack_statuses": [pack_one["cache_status"], pack_two["cache_status"]],
        "helper_status_before": helper_before["version_status"],
        "helper_status_after": helper_after["version_status"],
        "verification_status": run["status"],
        "semantic_status": run["cases"][0]["semantic_assertions"]["status"],
        "native_layout_status": (run["cases"][0].get("native_layout_audit") or {}).get("status"),
        "run_id": run["run_id"],
        "audit_gap_ids": [gap["gap_id"] for gap in audit["gaps"]],
    }


def run_external_fixture_suite(
    source_plugin_root: Path,
    run_root: Path,
    fixtures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_fixtures = fixtures or REPO_FIXTURES
    isolated_run = install_isolated_plugin(source_plugin_root, run_root)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for fixture in selected_fixtures:
        try:
            results.append(exercise_external_fixture(run_root, source_plugin_root, isolated_run, fixture))
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "repo_name": fixture["repo_name"],
                    "runner": fixture["runner"],
                    "error": str(exc),
                }
            )
    return {
        "status": "passed" if not failures else "failed",
        "source_plugin_root": str(source_plugin_root),
        "run_root": str(run_root),
        "installed_plugin_root": str(isolated_run.install_root),
        "state_root": str(isolated_run.state_root),
        "marketplace_path": str(isolated_run.marketplace_path),
        "install_result": isolated_run.install_result,
        "results": results,
        "failures": failures,
    }


def timestamp_slug(prefix: str = "run") -> str:
    return datetime.now(timezone.utc).strftime(f"{prefix}-%Y%m%dT%H%M%SZ")
