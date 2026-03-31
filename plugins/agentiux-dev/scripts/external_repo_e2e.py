#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from install_home_local import install_plugin


FAKE_RUNNER_SOURCE = """#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--helper-file", action="append", default=[])
    parser.add_argument("--artifact-name", required=True)
    args = parser.parse_args()

    required_env = [
        "VERIFICATION_RUN_ID",
        "VERIFICATION_CASE_ID",
        "VERIFICATION_ARTIFACT_DIR",
        "VERIFICATION_HELPER_ROOT",
        "VERIFICATION_HELPER_VERSION",
        "VERIFICATION_SEMANTIC_SPEC_PATH",
        "VERIFICATION_SEMANTIC_REPORT_PATH",
    ]
    env = {name: os.environ.get(name) for name in required_env}
    missing = [name for name, value in env.items() if not value]
    if missing:
        _fail("Missing verification env vars: " + ", ".join(missing))

    repo_root = Path(args.repo_root).resolve()
    helper_root = Path(env["VERIFICATION_HELPER_ROOT"]).resolve()
    expected_helper_root = (repo_root / ".verification" / "helpers").resolve()
    if helper_root != expected_helper_root:
        _fail(f"Expected helper root {expected_helper_root}, got {helper_root}")
    if ".verification/helpers" not in helper_root.as_posix():
        _fail(f"Helper root should be neutral project path, got {helper_root}")
    if (repo_root / ".agentiux").exists():
        _fail("Legacy .agentiux helper root should not exist")

    relative_helper_root = helper_root.relative_to(repo_root).as_posix().lower()
    if "agentiux" in relative_helper_root or "codex" in relative_helper_root:
        _fail(f"Project helper path should not contain branding: {relative_helper_root}")

    for relative in args.helper_file:
        candidate = helper_root / relative
        if not candidate.exists():
            _fail(f"Expected helper file is missing: {candidate}")

    for candidate in helper_root.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in {".js", ".json", ".kt"}:
            continue
        text = candidate.read_text(encoding="utf-8", errors="ignore").lower()
        if "agentiux" in text or "codex" in text:
            _fail(f"Branded string leaked into materialized helper file: {candidate}")

    spec_path = Path(env["VERIFICATION_SEMANTIC_SPEC_PATH"]).resolve()
    report_path = Path(env["VERIFICATION_SEMANTIC_REPORT_PATH"]).resolve()
    artifact_dir = Path(env["VERIFICATION_ARTIFACT_DIR"]).resolve()
    if artifact_dir not in report_path.parents:
        _fail(f"Semantic report should live under artifact dir: {report_path}")

    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if payload.get("runner") != args.runner:
        _fail(f"Spec runner mismatch: {payload.get('runner')} != {args.runner}")
    if payload.get("helper_bundle_version") != env["VERIFICATION_HELPER_VERSION"]:
        _fail("Spec helper bundle version does not match env")
    if Path(payload.get("helper_root") or "").resolve() != helper_root:
        _fail("Spec helper root does not match env helper root")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_dir / args.artifact_name
    artifact_file.write_text(f"{args.runner}:{env['VERIFICATION_CASE_ID']}\\n", encoding="utf-8")

    checks = []
    for target in payload.get("targets") or []:
        target_checks = []
        for check_id in payload.get("required_checks") or []:
            target_checks.append(
                {
                    "check_id": check_id,
                    "status": "passed",
                    "runner": args.runner,
                    "diagnostics": {
                        "case_id": env["VERIFICATION_CASE_ID"],
                        "helper_root": helper_root.as_posix(),
                        "target_id": target.get("target_id"),
                    },
                    "artifact_paths": [artifact_file.name],
                }
            )
        checks.append(
            {
                "target_id": target.get("target_id"),
                "status": "passed",
                "diagnostics": {
                    "locator_kind": ((target.get("locator") or {}).get("kind")),
                    "auto_scan": bool(payload.get("auto_scan", False)),
                },
                "artifact_paths": [artifact_file.name],
                "checks": target_checks,
            }
        )

    report = {
        "schema_version": 1,
        "runner": args.runner,
        "helper_bundle_version": env["VERIFICATION_HELPER_VERSION"],
        "summary": {
            "status": "passed",
            "message": f"{args.runner} fake semantic runner completed",
        },
        "targets": checks,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "report_path": str(report_path), "artifact_path": str(artifact_file)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


WEB_PACKAGE_JSON = {
    "name": "shop-web-playwright",
    "private": True,
    "scripts": {
        "test:e2e": "playwright test",
    },
    "dependencies": {
        "next": "^16.0.0",
        "react": "^19.0.0",
        "react-dom": "^19.0.0",
    },
    "devDependencies": {
        "@playwright/test": "^1.54.0",
        "typescript": "^5.8.0",
    },
}

DETOX_PACKAGE_JSON = {
    "name": "mobile-detox-app",
    "private": True,
    "scripts": {
        "test:detox": "detox test",
    },
    "dependencies": {
        "expo": "^54.0.0",
        "react": "^19.0.0",
        "react-native": "^0.82.0",
    },
    "devDependencies": {
        "detox": "^20.0.0",
        "typescript": "^5.8.0",
    },
}

REPO_FIXTURES = [
    {
        "repo_name": "shop-web-playwright",
        "profile_expectation": "web-platform",
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
        "files": {
            "package.json": json.dumps(WEB_PACKAGE_JSON, indent=2) + "\n",
            "README.md": "# Shop Web\n\nCheckout flow with Playwright semantic verification.\n",
            "playwright.config.ts": "export default { testDir: './tests' };\n",
            "apps/web/app/checkout/page.tsx": "export default function CheckoutPage() { return <main data-testid='checkout-main'>Checkout</main>; }\n",
        },
    },
    {
        "repo_name": "mobile-detox-app",
        "profile_expectation": "mobile-platform",
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
        "files": {
            "package.json": json.dumps(DETOX_PACKAGE_JSON, indent=2) + "\n",
            "README.md": "# Mobile Detox App\n\nHome screen semantic verification with Detox.\n",
            ".detoxrc.js": "module.exports = { testRunner: 'jest', apps: {}, devices: {}, configurations: {} };\n",
            "app.json": json.dumps({"expo": {"name": "Mobile Detox App"}}, indent=2) + "\n",
            "android/app/src/main/AndroidManifest.xml": "<manifest package='com.example.detoxapp'></manifest>\n",
            "ios/Podfile": "platform :ios, '15.0'\n",
            "apps/mobile/src/screens/HomeScreen.tsx": "export function HomeScreen() { return null; }\n",
        },
    },
    {
        "repo_name": "android-compose-lab",
        "profile_expectation": "mobile-platform",
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
        "files": {
            "README.md": "# Android Compose Lab\n\nCompose home surface semantic verification.\n",
            "settings.gradle.kts": "rootProject.name = \"android-compose-lab\"\ninclude(\":app\")\n",
            "gradle.properties": "android.useAndroidX=true\n",
            "app/build.gradle.kts": textwrap.dedent(
                """
                plugins {
                    id("com.android.application")
                    kotlin("android")
                }

                android {
                    namespace = "com.example.demo"
                    compileSdk = 35
                }
                """
            ).strip()
            + "\n",
            "app/src/main/AndroidManifest.xml": "<manifest package='com.example.demo'></manifest>\n",
            "app/src/main/java/com/example/demo/HomeScreen.kt": textwrap.dedent(
                """
                package com.example.demo

                fun homeScreenTag(): String = "compose-home"
                """
            ).strip()
            + "\n",
        },
    },
]


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"cwd={cwd or Path.cwd()}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def _run_json(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    result = _run(cmd, cwd=cwd, env=env)
    stdout = result.stdout.strip()
    if not stdout:
        raise ValueError(f"Command produced no JSON output: {' '.join(cmd)}")
    return json.loads(stdout)


def _git_init_repo(path: Path) -> None:
    _run(["git", "init", "-b", "main"], cwd=path)
    _run(["git", "config", "user.name", "AgentiUX E2E"], cwd=path)
    _run(["git", "config", "user.email", "e2e@example.com"], cwd=path)
    _run(["git", "add", "."], cwd=path)
    _run(["git", "commit", "-m", "chore: seed fixture"], cwd=path)


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")


def _state_env(installed_root: Path, state_root: Path, marketplace_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["AGENTIUX_DEV_PLUGIN_ROOT"] = str(installed_root)
    env["AGENTIUX_DEV_STATE_ROOT"] = str(state_root)
    env["AGENTIUX_DEV_INSTALL_ROOT"] = str(installed_root)
    env["AGENTIUX_DEV_MARKETPLACE_PATH"] = str(marketplace_path)
    return env


def _state_script(installed_root: Path) -> Path:
    return installed_root / "scripts" / "agentiux_dev_state.py"


def _plugin_command(installed_root: Path, *args: str) -> list[str]:
    return [sys.executable, str(_state_script(installed_root)), *args]


def _create_repo(run_root: Path, fixture: dict[str, Any]) -> Path:
    repo_root = run_root / "repos" / fixture["repo_name"]
    repo_root.mkdir(parents=True, exist_ok=True)
    for relative_path, content in fixture["files"].items():
        _write_text(repo_root / relative_path, content)
    _write_text(repo_root / "tools" / "fake_semantic_runner.py", FAKE_RUNNER_SOURCE)
    (repo_root / "tools" / "fake_semantic_runner.py").chmod(0o755)
    _git_init_repo(repo_root)
    return repo_root


def _verification_recipe(repo_root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
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
                    "tools/fake_semantic_runner.py",
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
                    "required_checks": [
                        "presence_uniqueness",
                        "visibility",
                        "scroll_reachability",
                        "layout_relations",
                        "interaction_states",
                    ],
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
            }
        ],
    }


def _exercise_repo(run_root: Path, installed_root: Path, state_root: Path, marketplace_path: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    env = _state_env(installed_root, state_root, marketplace_path)
    repo_root = _create_repo(run_root, fixture)

    detect = _run_json(_plugin_command(installed_root, "detect-workspace", "--workspace", str(repo_root)), env=env)
    preview = _run_json(_plugin_command(installed_root, "preview-init", "--workspace", str(repo_root)), env=env)
    init = _run_json(_plugin_command(installed_root, "init-workspace", "--workspace", str(repo_root), "--force"), env=env)
    workstream = _run_json(
        _plugin_command(
            installed_root,
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

    route = _run_json(
        _plugin_command(installed_root, "show-intent-route", "--request-text", fixture["route_query"]),
        env=env,
    )
    refresh_one = _run_json(_plugin_command(installed_root, "refresh-context-index", "--workspace", str(repo_root)), env=env)
    refresh_two = _run_json(_plugin_command(installed_root, "refresh-context-index", "--workspace", str(repo_root)), env=env)
    search = _run_json(
        _plugin_command(
            installed_root,
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
    pack_one = _run_json(
        _plugin_command(
            installed_root,
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
    pack_two = _run_json(
        _plugin_command(
            installed_root,
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

    helper_before = _run_json(
        _plugin_command(installed_root, "show-verification-helper-catalog", "--workspace", str(repo_root)),
        env=env,
    )
    sync = _run_json(
        _plugin_command(installed_root, "sync-verification-helpers", "--workspace", str(repo_root)),
        env=env,
    )
    helper_after = _run_json(
        _plugin_command(installed_root, "show-verification-helper-catalog", "--workspace", str(repo_root)),
        env=env,
    )

    recipe_path = run_root / "recipes" / f"{fixture['repo_name']}.json"
    _write_json(recipe_path, _verification_recipe(repo_root, fixture))
    write_recipes = _run_json(
        _plugin_command(
            installed_root,
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
    audit = _run_json(
        _plugin_command(
            installed_root,
            "audit-verification-coverage",
            "--workspace",
            str(repo_root),
            "--workstream-id",
            workstream_id,
        ),
        env=env,
    )
    verification_selection = _run_json(
        _plugin_command(
            installed_root,
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
    run = _run_json(
        _plugin_command(
            installed_root,
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

    assert fixture["profile_expectation"] in detect["selected_profiles"], detect
    assert preview["planning_policy"]["explicit_stage_plan_required"] is True
    assert init["workspace_state"]["workspace_path"] == str(repo_root.resolve())
    assert route["resolved_route"]["route_id"] == "verification", route
    assert route["resolution_status"] in {"matched", "exact"}, route
    assert refresh_one["status"] == "refreshed", refresh_one
    assert refresh_two["status"] == "fresh", refresh_two
    assert search["resolved_route"]["route_id"] == "verification", search
    assert search["matches"], search
    assert pack_one["cache_status"] == "miss", pack_one
    assert pack_two["cache_status"] == "hit", pack_two
    assert helper_before["version_status"] == "not_synced", helper_before
    assert sync["materialization"]["status"] == "synced", sync
    assert sync["destination_root"].endswith("/.verification/helpers"), sync
    assert "/0.8.0/" not in "".join(sync["import_snippets"][fixture["runner"]]["import_examples"]), sync
    assert not (repo_root / ".agentiux").exists()
    assert helper_after["version_status"] == "synced", helper_after
    assert write_recipes["cases"][0]["id"] == fixture["case_id"], write_recipes
    assert not any(gap["gap_id"] == "verification-helper-bundle-not-synced" for gap in audit["gaps"]), audit
    assert verification_selection["selection_status"] == "resolved", verification_selection
    assert run["status"] == "passed", run
    assert run["cases"][0]["semantic_assertions"]["status"] == "passed", run

    return {
        "repo_name": fixture["repo_name"],
        "repo_root": str(repo_root),
        "runner": fixture["runner"],
        "workstream_id": workstream_id,
        "route_status": route["resolution_status"],
        "context_refresh_statuses": [refresh_one["status"], refresh_two["status"]],
        "context_pack_statuses": [pack_one["cache_status"], pack_two["cache_status"]],
        "helper_status_before": helper_before["version_status"],
        "helper_status_after": helper_after["version_status"],
        "verification_status": run["status"],
        "semantic_status": run["cases"][0]["semantic_assertions"]["status"],
        "run_id": run["run_id"],
        "audit_gap_ids": [gap["gap_id"] for gap in audit["gaps"]],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run external-repository e2e coverage for AgentiUX Dev")
    parser.add_argument("--source-plugin-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--e2e-root", default="/tmp/agentiux-dev-e2e")
    parser.add_argument("--run-slug", default=_timestamp_slug())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_plugin_root = Path(args.source_plugin_root).expanduser().resolve()
    e2e_root = Path(args.e2e_root).expanduser().resolve()
    run_root = e2e_root / args.run_slug
    if run_root.exists():
        raise SystemExit(f"E2E run root already exists: {run_root}")
    run_root.mkdir(parents=True, exist_ok=True)

    installed_root = run_root / "installed-plugin" / "agentiux-dev"
    marketplace_path = run_root / "marketplace.json"
    state_root = run_root / "state"
    install_result = install_plugin(source_plugin_root, installed_root, marketplace_path)

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for fixture in REPO_FIXTURES:
        try:
            results.append(_exercise_repo(run_root, installed_root, state_root, marketplace_path, fixture))
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "repo_name": fixture["repo_name"],
                    "runner": fixture["runner"],
                    "error": str(exc),
                }
            )

    payload = {
        "status": "passed" if not failures else "failed",
        "source_plugin_root": str(source_plugin_root),
        "run_root": str(run_root),
        "installed_plugin_root": str(installed_root),
        "state_root": str(state_root),
        "marketplace_path": str(marketplace_path),
        "install_result": install_result,
        "results": results,
        "failures": failures,
    }
    summary_path = run_root / "e2e-summary.json"
    _write_json(summary_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
