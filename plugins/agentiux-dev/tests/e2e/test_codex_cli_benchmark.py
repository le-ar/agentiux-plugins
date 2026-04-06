from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parents[1]
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from agentiux_dev_context import refresh_context_index, search_context_index  # noqa: E402
from agentiux_dev_e2e_support import create_fixture_repo, fixture_definition, isolated_plugin_env, temporary_env  # noqa: E402
from agentiux_dev_lib import init_workspace  # noqa: E402
from agentiux_dev_mcp import _handle_request  # noqa: E402
from scenarios import codex_cli  # noqa: E402
from tools.codex_benchmark_adapter import build_codex_benchmark_bootstrap  # noqa: E402


FIXTURE_ROOT = PLUGIN_ROOT / "tests" / "e2e" / "projects" / "codex-benchmark-workspace"


class CodexCliBenchmarkTests(unittest.TestCase):
    def test_benchmark_fixture_has_real_owner_links(self) -> None:
        storefront_page = (FIXTURE_ROOT / "apps" / "storefront" / "app" / "checkout" / "page.tsx").read_text(encoding="utf-8")
        checkout_shell = (FIXTURE_ROOT / "apps" / "storefront" / "app" / "checkout" / "CheckoutShell.tsx").read_text(encoding="utf-8")
        admin_checkout = FIXTURE_ROOT / "apps" / "admin" / "app" / "checkout" / "page.tsx"
        health_controller = (FIXTURE_ROOT / "apps" / "server" / "src" / "health" / "health.controller.ts").read_text(encoding="utf-8")
        health_service = (FIXTURE_ROOT / "apps" / "server" / "src" / "health" / "health.service.ts").read_text(encoding="utf-8")
        health_spec = (FIXTURE_ROOT / "apps" / "server" / "test" / "health.e2e-spec.ts").read_text(encoding="utf-8")
        storefront_package = json.loads((FIXTURE_ROOT / "apps" / "storefront" / "package.json").read_text(encoding="utf-8"))
        server_package = json.loads((FIXTURE_ROOT / "apps" / "server" / "package.json").read_text(encoding="utf-8"))
        playwright_config = (FIXTURE_ROOT / "playwright.config.ts").read_text(encoding="utf-8")

        self.assertIn('import { CheckoutShell } from "./CheckoutShell";', storefront_page)
        self.assertIn('packages/checkout-cta/src/label', checkout_shell)
        self.assertTrue(admin_checkout.exists())
        self.assertIn('import { readinessPayload } from "./health.service";', health_controller)
        self.assertIn('source: "storefront-checkout"', health_service)
        self.assertIn('handleReadinessRequest', health_spec)
        self.assertIn('"/ready"', health_spec)
        self.assertEqual(
            storefront_package["scripts"]["test:checkout"],
            "playwright test ../../tests/storefront-checkout.spec.ts --config ../../playwright.config.ts",
        )
        self.assertEqual(server_package["scripts"]["test:readiness"], "tsx --test test/health.e2e-spec.ts")
        self.assertIn("storefront-checkout\\.spec\\.ts", playwright_config)
        self.assertIn("@storefront", playwright_config)

    def test_command_metrics_count_rg_reads_helpers_and_distractors(self) -> None:
        task = next(task for task in codex_cli.BENCHMARK_TASKS if task.task_id == "cross-app-disambiguation")
        events = [
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "agentiux-dev-state",
                    "tool": "triage_repo_request",
                    "arguments": {"workspacePath": str(FIXTURE_ROOT)},
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "/bin/zsh -lc \"rg --files . | rg 'checkout|ready'\"",
                    "aggregated_output": (
                        "apps/storefront/app/checkout/page.tsx\n"
                        "apps/admin/app/checkout/page.tsx\n"
                        "apps/server/src/health/health.controller.ts\n"
                    ),
                    "exit_code": 0,
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": (
                        "/bin/zsh -lc \"sed -n '1,120p' apps/storefront/app/checkout/page.tsx; "
                        "sed -n '1,120p' apps/admin/app/checkout/page.tsx; "
                        "cat tests/storefront-checkout.spec.ts\""
                    ),
                    "aggregated_output": "",
                    "exit_code": 0,
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "agentiux show-workspace-context-pack --workspace . --route-id verification --request-text 'checkout cta'",
                    "aggregated_output": (
                        "apps/storefront/app/checkout/page.tsx\n"
                        "packages/checkout-cta/src/label.ts\n"
                    ),
                    "exit_code": 0,
                },
            },
        ]

        metrics = codex_cli._command_metrics(events, workspace=FIXTURE_ROOT, task=task)

        self.assertEqual(metrics["command_count"], 3)
        self.assertEqual(metrics["manual_shell_invocation_count"], 2)
        self.assertEqual(metrics["plugin_helper_invocation_count"], 2)
        self.assertEqual(metrics["plugin_helper_mcp_invocation_count"], 1)
        self.assertIn("triage_repo_request", metrics["plugin_helper_tool_names"])
        self.assertEqual(metrics["rg_invocation_count"], 2)
        self.assertEqual(metrics["manual_read_operation_count"], 3)
        self.assertEqual(metrics["broad_scan_count"], 1)
        self.assertEqual(metrics["unique_repo_path_count"], 4)
        self.assertEqual(metrics["distractor_path_touch_count"], 1)
        self.assertEqual(metrics["distractor_paths_touched"], ["apps/admin/app/checkout/page.tsx"])

    def test_quality_summary_penalizes_overfetch_and_tracks_primary_rank(self) -> None:
        task = next(task for task in codex_cli.BENCHMARK_TASKS if task.task_id == "cross-app-disambiguation")
        quality = codex_cli._quality_summary(
            task,
            {
                "candidate_files": [
                    "apps/storefront/app/checkout/CheckoutShell.tsx",
                    "apps/storefront/app/checkout/page.tsx",
                    "packages/checkout-cta/src/label.ts",
                    "README.md",
                ],
                "candidate_commands": [],
                "why": "Overfetches a wrapper and docs.",
                "risks": [],
                "confidence": 0.7,
            },
            FIXTURE_ROOT,
        )

        self.assertEqual(quality["primary_file_rank"], 1)
        self.assertEqual(quality["distractor_file_hit_count"], 2)
        self.assertLess(quality["valid_file_precision"], 1.0)
        self.assertGreater(quality["total_minimality_penalty"], 0)

    def test_comparison_summary_warns_when_expected_replicas_are_unavailable(self) -> None:
        task = next(task for task in codex_cli.BENCHMARK_TASKS if task.task_id == "owner-file-routing")

        def build_run(condition: str, status: str, replica_index: int) -> dict[str, object]:
            return {
                "task_id": task.task_id,
                "condition": condition,
                "replica_index": replica_index,
                "status": status,
                "elapsed_ms": 10_000 if condition == codex_cli.ASSISTED_CONDITION else 12_000,
                "prewarm_bootstrap_payload_bytes": 1_000 if condition == codex_cli.ASSISTED_CONDITION else 0,
                "telemetry": {"payload_bytes_total": 0, "all_within_ceiling": True},
                "command_metrics": {
                    "manual_shell_invocation_count": 1,
                    "manual_read_operation_count": 1,
                    "manual_command_count": 1,
                    "rg_count": 0,
                    "broad_scan_count": 0,
                    "unique_repo_path_count": 2,
                    "distractor_path_touch_count": 0,
                    "plugin_helper_command_count": 0,
                },
                "quality": {
                    "core_file_recall": 1.0,
                    "valid_file_precision": 1.0,
                    "primary_file_rank": 0,
                    "command_recall": 1.0,
                    "command_precision": 1.0,
                    "unexpected_file_count": 0,
                    "unexpected_command_count": 0,
                    "total_minimality_penalty": 0,
                    "distractor_file_hit_count": 0,
                },
                "prep_summary": {
                    "bootstrap": {
                        "outside_workspace_tree": True,
                        "workspace_agents_file_exists": False,
                    }
                },
            }

        comparison = codex_cli._comparison_summary_for_task(
            [
                build_run(codex_cli.ASSISTED_CONDITION, "completed", 1),
                build_run(codex_cli.RAW_CONDITION, "completed", 1),
                build_run(codex_cli.ASSISTED_CONDITION, "unavailable", 2),
                build_run(codex_cli.RAW_CONDITION, "unavailable", 2),
            ]
        )

        self.assertEqual(comparison["status"], "warning")
        self.assertFalse(comparison["all_expected_replicas_available"])
        self.assertEqual(comparison["unavailable_run_count"], 2)

    def test_build_codex_command_separates_raw_runtime_warm_and_bootstrap_assisted(self) -> None:
        run_root = Path("/tmp/agentiux-benchmark-run")
        plugin_root = Path("/tmp/agentiux-plugin-root")
        schema_path = run_root / "output-schema.json"
        output_path = run_root / "codex-last-message.json"
        bootstrap_path = run_root / "codex-bootstrap.md"

        raw_command = codex_cli._build_codex_command(
            condition=codex_cli.RAW_CONDITION,
            state_root="/tmp/agentiux-state",
            run_root=run_root,
            plugin_runtime_root=None,
            schema_path=schema_path,
            output_path=output_path,
            benchmark_prompt="raw prompt",
            bootstrap_path=None,
        )
        runtime_warm_command = codex_cli._build_codex_command(
            condition=codex_cli.RUNTIME_WARM_CONDITION,
            state_root="/tmp/agentiux-state",
            run_root=run_root,
            plugin_runtime_root=plugin_root,
            schema_path=schema_path,
            output_path=output_path,
            benchmark_prompt="warm prompt",
            bootstrap_path=None,
        )
        bootstrap_command = codex_cli._build_codex_command(
            condition=codex_cli.BOOTSTRAP_ASSISTED_CONDITION,
            state_root="/tmp/agentiux-state",
            run_root=run_root,
            plugin_runtime_root=plugin_root,
            schema_path=schema_path,
            output_path=output_path,
            benchmark_prompt="bootstrap prompt",
            bootstrap_path=bootstrap_path,
        )

        self.assertNotIn(str(plugin_root), raw_command)
        self.assertIn(str(plugin_root), runtime_warm_command)
        self.assertIn(str(plugin_root), bootstrap_command)
        self.assertFalse(any("model_instructions_file" in token for token in runtime_warm_command))
        self.assertTrue(any("model_instructions_file" in token for token in bootstrap_command))

    def test_prepare_benchmark_runtime_env_isolates_raw_and_uses_installed_copy_for_warm(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-benchmark-runtime-env-") as tmp_dir:
            root = Path(tmp_dir)
            warm_setup = codex_cli._prepare_benchmark_runtime_env(
                root / "warm",
                PLUGIN_ROOT,
                codex_cli.RUNTIME_WARM_CONDITION,
                benchmark_log_path=root / "warm" / "benchmark-log.jsonl",
            )
            raw_setup = codex_cli._prepare_benchmark_runtime_env(
                root / "raw",
                PLUGIN_ROOT,
                codex_cli.RAW_CONDITION,
                benchmark_log_path=root / "raw" / "benchmark-log.jsonl",
            )
            warm_env = warm_setup["env"]
            raw_env = raw_setup["env"]
            warm_config = Path(warm_setup["codex_home"]) / "config.toml"
            raw_config = Path(raw_setup["codex_home"]) / "config.toml"
            warm_marketplace = json.loads(Path(warm_env["AGENTIUX_DEV_MARKETPLACE_PATH"]).read_text(encoding="utf-8"))
            warm_entry = next(plugin for plugin in warm_marketplace["plugins"] if plugin["name"] == "agentiux-dev")

            warm_runtime_root = Path(str(warm_setup["plugin_runtime_root"]))
            self.assertEqual(warm_setup["plugin_registration_mode"], "codex-cache-local-plugin-copy+direct-mcp-config")
            self.assertTrue(warm_runtime_root.exists())
            self.assertEqual(
                warm_runtime_root,
                (Path(warm_setup["codex_home"]) / "plugins" / "cache" / "local-plugins" / "agentiux-dev" / "local").resolve(),
            )
            self.assertTrue((warm_runtime_root / ".mcp.json").exists())
            self.assertTrue(Path(warm_env["AGENTIUX_DEV_MARKETPLACE_PATH"]).exists())
            self.assertEqual(
                Path(warm_env["AGENTIUX_DEV_MARKETPLACE_PATH"]).resolve(),
                (Path(warm_setup["codex_home"]) / ".tmp" / "plugins" / ".agents" / "plugins" / "marketplace.json").resolve(),
            )
            self.assertTrue(
                (
                    Path(warm_setup["codex_home"]) / ".tmp" / "plugins" / "plugins" / "agentiux-dev" / ".mcp.json"
                ).exists()
            )
            self.assertEqual(Path(warm_env["HOME"]).name, "product-home")
            self.assertIn('[plugins."agentiux-dev@local-plugins"]', warm_config.read_text(encoding="utf-8"))
            self.assertIn("enabled = true", warm_config.read_text(encoding="utf-8"))
            self.assertIn('[mcp_servers."agentiux-dev-state"]', warm_config.read_text(encoding="utf-8"))
            self.assertIn("command = \"python3\"", warm_config.read_text(encoding="utf-8"))
            self.assertIn("AGENTIUX_DEV_BENCHMARK_LOG", warm_config.read_text(encoding="utf-8"))
            self.assertIn("triage_repo_request", " ".join((warm_entry.get("interface") or {}).get("defaultPrompt") or []))
            self.assertIn("triage", str(warm_entry.get("description") or "").lower())
            self.assertTrue((warm_entry.get("keywords") or []))

            self.assertEqual(raw_setup["plugin_registration_mode"], "disabled")
            self.assertIsNone(raw_setup["plugin_runtime_root"])
            self.assertNotIn("AGENTIUX_DEV_PLUGIN_ROOT", raw_env)
            self.assertNotIn("AGENTIUX_DEV_INSTALL_ROOT", raw_env)
            self.assertNotIn("AGENTIUX_DEV_MARKETPLACE_PATH", raw_env)
            self.assertNotIn('[plugins."agentiux-dev@local-plugins"]', raw_config.read_text(encoding="utf-8"))
            self.assertIn("AGENTIUX_DEV_BENCHMARK_LOG", raw_env)

    def test_single_mode_summary_for_runtime_warm_tracks_single_replica(self) -> None:
        task = next(task for task in codex_cli.BENCHMARK_TASKS if task.task_id == "verification-command-discovery")
        summary = codex_cli._single_mode_summary_for_task(
            [
                {
                    "task_id": task.task_id,
                    "condition": codex_cli.RUNTIME_WARM_CONDITION,
                    "status": "completed",
                    "elapsed_ms": 42_000,
                    "prewarm_bootstrap_payload_bytes": 0,
                    "telemetry": {"payload_bytes_total": 0, "all_within_ceiling": True},
                    "command_metrics": {
                        "manual_command_count": 3,
                        "manual_shell_invocation_count": 3,
                        "manual_read_operation_count": 2,
                        "plugin_helper_invocation_count": 1,
                        "rg_count": 0,
                        "broad_scan_count": 0,
                        "unique_repo_path_count": 4,
                        "distractor_path_touch_count": 0,
                    },
                    "quality": {
                        "core_file_recall": 1.0,
                        "valid_file_precision": 1.0,
                        "primary_file_rank": 0,
                        "command_recall": 1.0,
                        "command_precision": 1.0,
                        "unexpected_file_count": 0,
                        "unexpected_command_count": 0,
                        "total_minimality_penalty": 0,
                        "distractor_file_hit_count": 0,
                    },
                    "prep_summary": {
                        "bootstrap_delivery": "none",
                    },
                }
            ],
            condition=codex_cli.RUNTIME_WARM_CONDITION,
        )

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["mode"], codex_cli.RUNTIME_WARM_CONDITION)
        self.assertEqual(summary["planned_run_count"], 1)
        self.assertEqual(summary["completed_run_count"], 1)
        self.assertEqual(summary["bootstrap_delivery"], "none")
        self.assertEqual(summary["median_plugin_helper_invocation_count"], 1.0)
        self.assertEqual(summary["median_wall_clock_ms"], 42000.0)

    def test_internal_benchmark_bootstrap_prefers_storefront_over_admin_and_generic_root(self) -> None:
        owner_task = next(task for task in codex_cli.BENCHMARK_TASKS if task.task_id == "owner-file-routing")
        cross_app_task = next(task for task in codex_cli.BENCHMARK_TASKS if task.task_id == "cross-app-disambiguation")
        verification_task = next(task for task in codex_cli.BENCHMARK_TASKS if task.task_id == "verification-command-discovery")
        symptom_task = next(task for task in codex_cli.BENCHMARK_TASKS if task.task_id == "symptom-to-owner-triage")

        with tempfile.TemporaryDirectory(prefix="codex-benchmark-test-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                owner_payload = build_codex_benchmark_bootstrap(
                    workspace,
                    request_text=owner_task.benchmark_query,
                    limit=8,
                    semantic_mode="disabled",
                )
                cross_app_payload = build_codex_benchmark_bootstrap(
                    workspace,
                    request_text=cross_app_task.benchmark_query,
                    limit=8,
                    semantic_mode="disabled",
                )
                verification_payload = build_codex_benchmark_bootstrap(
                    workspace,
                    request_text=verification_task.benchmark_query,
                    limit=8,
                    semantic_mode="disabled",
                )
                verification_cached_payload = build_codex_benchmark_bootstrap(
                    workspace,
                    request_text=verification_task.benchmark_query,
                    limit=8,
                    semantic_mode="disabled",
                )
                symptom_payload = build_codex_benchmark_bootstrap(
                    workspace,
                    request_text=symptom_task.benchmark_query,
                    limit=8,
                    semantic_mode="disabled",
                )

        owner_paths = [item["path"] for item in owner_payload["bootstrap"]["candidate_paths"]]
        owner_commands = [item["command"] for item in owner_payload["bootstrap"].get("command_hints", [])]
        cross_app_paths = [item["path"] for item in cross_app_payload["bootstrap"]["candidate_paths"]]
        verification_paths = [item["path"] for item in verification_payload["bootstrap"]["candidate_paths"]]
        verification_commands = [item["command"] for item in verification_payload["bootstrap"].get("command_hints", [])]
        symptom_paths = [item["path"] for item in symptom_payload["bootstrap"]["candidate_paths"]]
        symptom_commands = [item["command"] for item in symptom_payload["bootstrap"].get("command_hints", [])]

        self.assertCountEqual(
            owner_paths[:4],
            [
                "apps/storefront/app/checkout/page.tsx",
                "packages/checkout-cta/src/label.ts",
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
            ],
        )
        self.assertEqual(owner_commands, [])
        self.assertEqual(owner_paths[4:], ["tests/storefront-checkout.spec.ts"])
        self.assertNotIn("apps/server/test/health.e2e-spec.ts", owner_paths)
        self.assertNotIn("apps/admin/app/checkout/page.tsx", owner_paths)
        self.assertNotIn("tools/semantic_contract_runner.py", owner_paths)
        self.assertIn("apps/storefront/app/checkout/page.tsx", cross_app_paths[:2])
        self.assertIn("packages/checkout-cta/src/label.ts", cross_app_paths[:2])
        self.assertEqual(cross_app_paths, ["apps/storefront/app/checkout/page.tsx", "packages/checkout-cta/src/label.ts"])
        self.assertNotIn("apps/admin/app/checkout/page.tsx", cross_app_paths[:2])
        self.assertIn("apps/storefront/package.json", verification_paths[:3])
        self.assertIn("apps/server/package.json", verification_paths[:3])
        self.assertIn("tests/storefront-checkout.spec.ts", verification_paths[:4])
        self.assertIn("apps/server/test/health.e2e-spec.ts", verification_paths[:4])
        self.assertNotIn("apps/storefront/app/checkout/page.tsx", verification_paths[:4])
        self.assertNotIn("package.json", verification_paths[:3])
        self.assertCountEqual(
            verification_commands,
            [
                "pnpm --filter @bench/storefront test:checkout",
                "pnpm --filter @bench/server test:readiness",
            ],
        )
        self.assertEqual(verification_payload["bootstrap_cache_status"], "miss")
        self.assertEqual(verification_cached_payload["bootstrap_cache_status"], "hit")
        self.assertTrue(str(verification_payload["bootstrap_store_path"]).endswith("context_store.sqlite"))
        self.assertIn("Do not rewrite them into `cd <dir> && ...`", verification_payload["bootstrap"]["markdown"])
        self.assertIn("triage-repo-request", verification_payload["bootstrap"]["markdown"])
        self.assertEqual(symptom_payload["resolved_route"]["route_id"], "verification")
        self.assertFalse(symptom_payload["bootstrap_route_override_applied"])
        self.assertIsNone(symptom_payload["bootstrap_route_override_from"])
        self.assertEqual(
            symptom_paths[:4],
            [
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
                "apps/server/package.json",
                "apps/server/test/health.e2e-spec.ts",
            ],
        )
        self.assertNotIn("apps/admin/app/checkout/page.tsx", symptom_paths)
        self.assertEqual(symptom_commands, ["pnpm --filter @bench/server test:readiness"])

    def test_telemetry_summary_falls_back_to_direct_mcp_events(self) -> None:
        events = [
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "agentiux-dev-state", "tool": "triage_repo_request"}},
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "agentiux-dev-state", "tool": "show_context_structure"}},
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "agentiux-dev-state", "tool": "triage_repo_request"}},
        ]

        telemetry = codex_cli._telemetry_summary([], events=events)

        self.assertEqual(telemetry["source"], "codex-events-fallback")
        self.assertEqual(telemetry["record_count"], 3)
        self.assertEqual(telemetry["surface_count"], 2)
        self.assertEqual(
            telemetry["tool_invocation_counts"],
            {
                "show_context_structure": 1,
                "triage_repo_request": 2,
            },
        )
        self.assertEqual(telemetry["payload_bytes_total"], 0)

    def test_mcp_server_returns_empty_resources_lists(self) -> None:
        resources_response = _handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
        templates_response = _handle_request({"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list"})

        self.assertEqual(resources_response["result"], {"resources": []})
        self.assertEqual(templates_response["result"], {"resourceTemplates": []})

    def test_search_context_index_hides_capability_hints_for_exact_route_only(self) -> None:
        query = "Inspect MCP tool catalogs and the dashboard runtime for plugin development Sigma"

        with tempfile.TemporaryDirectory(prefix="context-contract-test-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = {"AGENTIUX_DEV_STATE_ROOT": str(run_root / "state")}
            with temporary_env(env):
                refresh_context_index(workspace)
                exact_route_payload = search_context_index(workspace, query, route_id="plugin-dev", limit=5)
                inferred_route_payload = search_context_index(workspace, query, route_id=None, limit=5)

        self.assertEqual(exact_route_payload["resolved_route"]["route_id"], "plugin-dev")
        self.assertEqual(exact_route_payload["route_resolution_status"], "exact")
        self.assertEqual(exact_route_payload["recommended_capabilities"], [])

        self.assertEqual(inferred_route_payload["resolved_route"]["route_id"], "plugin-dev")
        self.assertEqual(inferred_route_payload["route_resolution_status"], "matched")
        self.assertTrue(inferred_route_payload["recommended_capabilities"])
        self.assertIn(
            "show_capability_catalog",
            [entry["id"] for entry in inferred_route_payload["recommended_capabilities"]],
        )

    def test_selected_benchmark_tasks_supports_task_filter(self) -> None:
        with temporary_env({"AGENTIUX_DEV_BENCHMARK_TASKS": "symptom-to-owner-triage,owner-file-routing"}):
            selected = codex_cli._selected_benchmark_tasks()
        self.assertEqual(
            [task.task_id for task in selected],
            ["owner-file-routing", "symptom-to-owner-triage"],
        )

        with temporary_env({"AGENTIUX_DEV_BENCHMARK_TASKS": "unknown-task"}):
            with self.assertRaisesRegex(ValueError, "Unknown benchmark task id"):
                codex_cli._selected_benchmark_tasks()


if __name__ == "__main__":
    unittest.main()
