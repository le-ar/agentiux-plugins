from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


CURRENT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = CURRENT_DIR.parents[1]
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from agentiux_dev_context import (  # noqa: E402
    refresh_context_index,
    run_analysis_audit,
    search_context_index,
    show_context_structure,
    show_runtime_preflight,
    show_workspace_context_pack,
    triage_repo_request,
)
from agentiux_dev_context_query import _rank_runtime_owner_candidates, _resolve_intent_candidates  # noqa: E402
from agentiux_dev_context_cache import context_cache_paths  # noqa: E402
from agentiux_dev_retrieval import SURFACE_WORKING_BUDGETS  # noqa: E402
from agentiux_dev_context_store import (  # noqa: E402
    QUERY_CACHE_OWNERSHIP_GRAPH_KIND,
    QUERY_CACHE_ROUTE_SHORTLIST_KIND,
    load_chunks as load_context_store_chunks,
    load_modules as load_context_store_modules,
    read_query_cache,
)
from agentiux_dev_e2e_support import create_fixture_repo, fixture_definition, isolated_plugin_env, temporary_env  # noqa: E402
from install_home_local import install_plugin, sync_plugin_into_codex_home  # noqa: E402
from agentiux_dev_lib import create_task, create_workstream, detect_workspace, init_workspace  # noqa: E402
from agentiux_dev_memory import persist_generated_memory_snapshot  # noqa: E402


FIXTURE_ROOT = PLUGIN_ROOT / "tests" / "e2e" / "projects" / "codex-benchmark-workspace"
REPO_ROOT = PLUGIN_ROOT.parents[1]


def _seed_minimal_plugin(root: Path, *, version: str) -> None:
    shutil.rmtree(root, ignore_errors=True)
    (root / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "agentiux-dev",
                "version": version,
                "description": "Synthetic plugin fixture",
                "interface": {"category": "Coding"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "scripts" / "agentiux.py").write_text(
        "#!/usr/bin/env python3\nprint('launcher fixture')\n",
        encoding="utf-8",
    )
    (root / "scripts" / "agentiux_dev_mcp.py").write_text(
        "#!/usr/bin/env python3\nprint('mcp fixture')\n",
        encoding="utf-8",
    )


class RuntimeBoundaryTests(unittest.TestCase):
    def test_home_local_install_refreshes_active_codex_cache_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="install-home-local-codex-cache-") as tmp_dir:
            root = Path(tmp_dir)
            source = root / "source-plugin"
            install_root = root / "home" / "plugins" / "agentiux-dev"
            marketplace = root / "home" / ".agents" / "plugins" / "marketplace.json"
            codex_home = root / "home" / ".codex"
            stale_cache = codex_home / "plugins" / "cache" / "local-plugins" / "agentiux-dev" / "local"

            _seed_minimal_plugin(source, version="9.9.9")
            _seed_minimal_plugin(stale_cache, version="0.1.0")
            codex_home.mkdir(parents=True, exist_ok=True)

            install_plugin(source, install_root, marketplace, install_global_command=False)
            payload = sync_plugin_into_codex_home(source, codex_home, marketplace)

            cache_manifest = json.loads((stale_cache / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            stage_manifest = json.loads(
                (codex_home / ".tmp" / "plugins" / "plugins" / "agentiux-dev" / ".codex-plugin" / "plugin.json").read_text(
                    encoding="utf-8"
                )
            )
            cache_metadata = json.loads((stale_cache / "install-metadata.json").read_text(encoding="utf-8"))
            cache_mcp = json.loads((stale_cache / ".mcp.json").read_text(encoding="utf-8"))
            temp_marketplace = json.loads(
                (codex_home / ".tmp" / "plugins" / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
            )

        self.assertEqual(payload["codex_cache_sync_status"], "synced")
        self.assertEqual(payload["codex_cache_install_root"], str(stale_cache.resolve()))
        self.assertEqual(payload["codex_stage_install_root"], str((codex_home / ".tmp" / "plugins" / "plugins" / "agentiux-dev").resolve()))
        self.assertEqual(cache_manifest["version"], "9.9.9")
        self.assertEqual(stage_manifest["version"], "9.9.9")
        self.assertEqual(cache_metadata["marketplace_path"], str(marketplace.resolve()))
        self.assertEqual(
            cache_mcp["mcpServers"]["agentiux-dev-state"]["env"]["AGENTIUX_DEV_PLUGIN_ROOT"],
            str(stale_cache.resolve()),
        )
        self.assertEqual(temp_marketplace["name"], "local-plugins")
        self.assertEqual(temp_marketplace["plugins"][0]["name"], "agentiux-dev")

    def test_runtime_surface_does_not_expose_benchmark_codex_transport(self) -> None:
        forbidden_markers = (
            "show_codex_bootstrap",
            "show-codex-bootstrap",
            "codex-bootstrap.md",
            "model_instructions_file",
            "codex exec",
        )
        for path in PLUGIN_ROOT.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(PLUGIN_ROOT).as_posix()
            if relative.startswith("tests/e2e/"):
                continue
            if any(part in {"__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in forbidden_markers:
                self.assertNotIn(marker, text, msg=f"Found forbidden benchmark-only marker `{marker}` in runtime path {relative}")

    def test_context_store_migrates_legacy_chunk_cache_without_full_jsonl_hot_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="context-store-migration-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                initial_refresh = refresh_context_index(workspace)
                cache_paths = context_cache_paths(workspace)
                modules = load_context_store_modules(cache_paths["context_store"])
                chunks = load_context_store_chunks(cache_paths["context_store"])
                cache_paths["chunk_summaries"].write_text(
                    "".join(json.dumps(chunk, sort_keys=True) + "\n" for chunk in chunks),
                    encoding="utf-8",
                )
                cache_paths["context_store"].unlink()

                migrated_refresh = refresh_context_index(workspace)
                migrated_search = search_context_index(workspace, "checkout readiness owner files", route_id="verification", limit=5)
                context_store_exists = Path(migrated_refresh["context_store_path"]).exists()

        self.assertEqual(initial_refresh["storage_backend"], "sqlite")
        self.assertEqual(migrated_refresh["storage_backend"], "sqlite")
        self.assertIn(migrated_refresh["storage_backend_status"], {"sqlite", "sqlite-migrated"})
        self.assertTrue(context_store_exists)
        self.assertTrue(modules)
        self.assertTrue(chunks)
        self.assertEqual(migrated_search["storage_backend"], "sqlite")
        self.assertTrue(migrated_search["matches"])

    def test_show_workspace_context_pack_exposes_shared_owner_candidates_and_commands(self) -> None:
        query = "Find the minimal package-level verification commands and owner files for the storefront checkout CTA and the backend /ready contract."
        with tempfile.TemporaryDirectory(prefix="shared-retrieval-pack-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = show_workspace_context_pack(
                    workspace,
                    request_text=query,
                    limit=8,
                    semantic_mode="disabled",
                )

        owner_paths = [item["path"] for item in payload["context_pack"]["owner_candidates"]]
        commands = [item["command"] for item in payload["context_pack"]["command_suggestions"]]
        self.assertEqual(payload["storage_backend"], "sqlite")
        self.assertEqual(payload["workspace_context"]["repo_maturity"]["mode"], "existing")
        self.assertIn("apps/storefront/package.json", owner_paths[:3])
        self.assertIn("apps/server/package.json", owner_paths[:3])
        self.assertTrue(payload["context_pack"]["next_read_paths"])
        self.assertTrue(payload["context_pack"]["exact_candidate_commands_only"])
        self.assertIn("apps/admin/", payload["context_pack"]["do_not_scan_paths"])
        self.assertTrue(payload["context_pack"]["why_these_files"]["summary"])
        self.assertTrue(payload["context_pack"]["why_these_files"]["focus_paths"])
        self.assertCountEqual(
            commands,
            [
                "pnpm --filter @bench/storefront test:checkout",
                "pnpm --filter @bench/server test:readiness",
            ],
        )

    def test_show_runtime_preflight_returns_bounded_first_move_guidance(self) -> None:
        query = "Given a readiness failure that returned admin-console metadata instead of the storefront checkout readiness contract, find the smallest owner files and minimal package-level command to inspect next."
        with tempfile.TemporaryDirectory(prefix="runtime-preflight-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = show_runtime_preflight(
                    workspace,
                    request_text=query,
                    route_id="verification",
                    limit=4,
                )

        preflight = payload["preflight"]
        self.assertEqual(payload["storage_backend"], "sqlite")
        self.assertEqual(preflight["repo_maturity"]["mode"], "existing")
        self.assertEqual(payload["resolved_route"]["route_id"], "verification")
        self.assertIn("apps/server/src/health/health.controller.ts", preflight["next_read_paths"])
        self.assertIn("apps/admin/", preflight["do_not_scan_paths"])
        self.assertTrue(preflight["why_these_files"]["summary"])
        self.assertCountEqual(
            preflight["primary_owner_files"],
            [
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
            ],
        )
        self.assertIn("apps/server/package.json", preflight["supporting_evidence_files"])
        self.assertEqual(
            preflight["exact_candidate_commands_only"],
            ["pnpm --filter @bench/server test:readiness"],
        )
        self.assertTrue(preflight["stop_if_enough"])
        self.assertIn("bounded triage", preflight["confidence_reason"].lower())
        self.assertEqual(preflight["selected_tools"], [])
        self.assertTrue(preflight["proof_assertions"])
        self.assertIn(
            {"from": "apps/server/src/health/health.controller.ts", "to": "apps/server/src/health/health.service.ts"},
            preflight["dependency_edges"],
        )
        self.assertEqual(
            preflight["follow_up_policy"],
            {
                "answer_now": True,
                "allow_search_context_index": False,
                "allow_show_context_structure": False,
                "tool_budget": 0,
                "shell_read_budget": 0,
            },
        )
        self.assertEqual(
            preflight["applied_constraints"],
            {
                "owner_files_only": True,
                "suppress_commands": False,
                "excluded_families_unless_imported": [],
            },
        )
        self.assertTrue(preflight["answer_ready_reason"])

    def test_triage_repo_request_prefers_small_owner_set_before_broad_scan(self) -> None:
        query = "Given a readiness failure that returned admin-console metadata instead of the storefront checkout readiness contract, find the smallest owner files and minimal package-level command to inspect next."
        with tempfile.TemporaryDirectory(prefix="repo-triage-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = triage_repo_request(
                    workspace,
                    request_text=query,
                    route_id="verification",
                    limit=4,
                )

        self.assertEqual(payload["storage_backend"], "sqlite")
        self.assertEqual((payload["resolved_route"] or {}).get("route_id"), "verification")
        self.assertCountEqual(
            payload["candidate_files"],
            [
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
            ],
        )
        self.assertEqual(payload["primary_owner_files"], payload["candidate_files"])
        self.assertIn("apps/server/package.json", payload["supporting_evidence_files"])
        self.assertNotIn("apps/server/src/admin/admin.controller.ts", payload["candidate_files"])
        self.assertNotIn("apps/storefront/package.json", payload["candidate_files"])
        self.assertNotIn("tests/storefront-checkout.spec.ts", payload["candidate_files"])
        self.assertIn("apps/admin/", payload["do_not_scan_paths"])
        self.assertIn("apps/server/src/admin/", payload["do_not_scan_paths"])
        self.assertEqual(payload["candidate_commands"], ["pnpm --filter @bench/server test:readiness"])
        self.assertTrue(payload["why"])
        self.assertTrue(payload["manual_shell_scan_discouraged"])
        self.assertTrue(payload["answer_ready"])
        self.assertTrue(payload["additional_retrieval_discouraged"])
        self.assertEqual(payload["selected_tools"], [])
        self.assertTrue(payload["proof_assertions"])
        self.assertIn(
            {"from": "apps/server/src/health/health.controller.ts", "to": "apps/server/src/health/health.service.ts"},
            payload["dependency_edges"],
        )
        self.assertEqual(
            payload["follow_up_policy"],
            {
                "answer_now": True,
                "allow_search_context_index": False,
                "allow_show_context_structure": False,
                "tool_budget": 0,
                "shell_read_budget": 0,
            },
        )
        self.assertEqual(
            payload["next_read_paths"],
            [
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
                "apps/server/package.json",
                "apps/server/test/health.e2e-spec.ts",
            ],
        )
        self.assertEqual(
            payload["applied_constraints"],
            {
                "owner_files_only": True,
                "suppress_commands": False,
                "excluded_families_unless_imported": [],
            },
        )
        self.assertTrue(payload["answer_ready_reason"])
        self.assertTrue(payload["payload"]["within_ceiling"])

    def test_triage_repo_request_accepts_balanced_semantic_mode_alias(self) -> None:
        query = "Given a readiness failure that returned admin-console metadata instead of the storefront checkout readiness contract, find the smallest owner files and minimal package-level command to inspect next."
        with tempfile.TemporaryDirectory(prefix="repo-triage-balanced-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = triage_repo_request(
                    workspace,
                    request_text=query,
                    route_id="verification",
                    limit=4,
                    semantic_mode="balanced",
                )

        self.assertEqual(payload["semantic_mode"], "auto")
        self.assertEqual(payload["candidate_commands"], ["pnpm --filter @bench/server test:readiness"])
        self.assertIn("apps/server/package.json", payload["supporting_evidence_files"])
        self.assertNotIn("apps/server/src/admin/admin.controller.ts", payload["candidate_files"])
        self.assertTrue(payload["answer_ready"])

    def test_triage_repo_request_closes_composite_owner_query_with_negative_intent(self) -> None:
        query = (
            "Identify the smallest set of files to inspect for changing the storefront checkout CTA copy, "
            "confirming the storefront checkout entrypoint, inspecting the backend /ready contract, and "
            "inspecting the Playwright spec that verifies checkout CTA text. Ignore admin checkout unless "
            "the requested storefront flow imports it. Return owner files only; candidate_commands should be empty unless "
            "a command source file is essential evidence."
        )
        with tempfile.TemporaryDirectory(prefix="repo-triage-composite-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = triage_repo_request(
                    workspace,
                    request_text=query,
                    limit=5,
                )

        self.assertEqual(payload["candidate_commands"], [])
        self.assertEqual(len(payload["candidate_files"]), 4)
        self.assertCountEqual(
            payload["candidate_files"],
            [
                "apps/storefront/app/checkout/page.tsx",
                "packages/checkout-cta/src/label.ts",
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
            ],
        )
        self.assertEqual(payload["primary_owner_files"], payload["candidate_files"])
        self.assertIn("tests/storefront-checkout.spec.ts", payload["supporting_evidence_files"])
        self.assertNotIn("apps/server/test/health.e2e-spec.ts", payload["supporting_evidence_files"])
        self.assertNotIn("apps/storefront/package.json", payload["supporting_evidence_files"])
        self.assertNotIn("apps/admin/app/checkout/page.tsx", payload["candidate_files"])
        self.assertNotIn("apps/storefront/app/checkout/CheckoutShell.tsx", payload["candidate_files"])
        self.assertNotIn("README.md", payload["candidate_files"])
        self.assertNotIn("playwright.config.ts", payload["candidate_files"])
        self.assertTrue(payload["answer_ready"])
        self.assertTrue(payload["additional_retrieval_discouraged"])
        self.assertEqual(payload["selected_tools"], [])
        self.assertTrue(payload["proof_assertions"])
        self.assertTrue(any("excluded families" in item.lower() for item in payload["proof_assertions"]))
        self.assertTrue(any("wrapper" in item.lower() for item in payload["proof_assertions"]))
        self.assertIn(
            {"from": "apps/storefront/app/checkout/page.tsx", "to": "apps/storefront/app/checkout/CheckoutShell.tsx"},
            payload["dependency_edges"],
        )
        self.assertIn(
            {"from": "apps/storefront/app/checkout/CheckoutShell.tsx", "to": "packages/checkout-cta/src/label.ts"},
            payload["dependency_edges"],
        )
        self.assertEqual(
            payload["follow_up_policy"],
            {
                "answer_now": True,
                "allow_search_context_index": False,
                "allow_show_context_structure": False,
                "tool_budget": 0,
                "shell_read_budget": 0,
            },
        )
        self.assertEqual(
            payload["next_read_paths"],
            [
                "packages/checkout-cta/src/label.ts",
                "apps/storefront/app/checkout/page.tsx",
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
                "tests/storefront-checkout.spec.ts",
            ],
        )
        self.assertEqual(
            payload["applied_constraints"],
            {
                "owner_files_only": True,
                "suppress_commands": True,
                "excluded_families_unless_imported": ["admin"],
            },
        )
        self.assertIn("retained route", payload["answer_ready_reason"].lower())

    def test_show_runtime_preflight_reports_constraints_when_composite_owner_query_stays_constraint_aware(self) -> None:
        query = (
            "Identify the smallest set of files to inspect for changing the storefront checkout CTA copy, "
            "confirming the storefront checkout entrypoint, inspecting the backend /ready contract, and "
            "inspecting the Playwright spec that verifies checkout CTA text. Ignore admin checkout unless "
            "the requested storefront flow imports it. Return owner files only; candidate_commands should be empty unless "
            "a command source file is essential evidence."
        )
        with tempfile.TemporaryDirectory(prefix="runtime-preflight-composite-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = show_runtime_preflight(
                    workspace,
                    request_text=query,
                    limit=4,
                )

        preflight = payload["preflight"]
        self.assertEqual(
            preflight["applied_constraints"],
            {
                "owner_files_only": True,
                "suppress_commands": True,
                "excluded_families_unless_imported": ["admin"],
            },
        )
        self.assertEqual(preflight["exact_candidate_commands_only"], [])
        self.assertIn("apps/admin/", preflight["do_not_scan_paths"])
        self.assertTrue(preflight["answer_ready_reason"])
        self.assertTrue(preflight["stop_if_enough"])
        self.assertTrue(preflight["follow_up_policy"]["answer_now"])
        self.assertEqual(preflight["selected_tools"], [])
        self.assertTrue(any("excluded families" in item.lower() for item in preflight["proof_assertions"]))

    def test_resolve_intent_candidates_prefers_verification_for_symptom_triage(self) -> None:
        query = "Given a readiness failure that returned admin-console metadata instead of the storefront checkout readiness contract, find the smallest owner files and minimal package-level command to inspect next."
        resolved, candidates, status = _resolve_intent_candidates(query, None)

        self.assertEqual(status, "matched")
        self.assertEqual((resolved or {}).get("route_id"), "verification")
        self.assertNotIn("git", [candidate["route_id"] for candidate in candidates[:2]])

    def test_resolve_intent_candidates_prefers_analysis_for_cross_app_owner_query(self) -> None:
        query = "Find the storefront checkout route file and shared package file that own the customer checkout CTA copy."
        resolved, candidates, status = _resolve_intent_candidates(query, None)

        self.assertEqual(status, "matched")
        self.assertEqual((resolved or {}).get("route_id"), "analysis")
        self.assertNotIn("git", [candidate["route_id"] for candidate in candidates[:2]])

    def test_triage_repo_request_infers_owner_only_for_owner_file_routing_benchmark_query(self) -> None:
        query = (
            "Find the smallest owner file set for the storefront checkout entrypoint, the shared checkout CTA label, "
            "the backend /ready contract, and the Playwright checkout spec."
        )
        with tempfile.TemporaryDirectory(prefix="repo-triage-owner-routing-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = triage_repo_request(
                    workspace,
                    request_text=query,
                    limit=5,
                )

        self.assertEqual(payload["candidate_commands"], [])
        self.assertEqual(len(payload["candidate_files"]), 4)
        self.assertCountEqual(
            payload["candidate_files"],
            [
                "apps/storefront/app/checkout/page.tsx",
                "packages/checkout-cta/src/label.ts",
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
            ],
        )
        self.assertEqual(
            payload["supporting_evidence_files"],
            ["tests/storefront-checkout.spec.ts"],
        )
        self.assertEqual(
            payload["applied_constraints"],
            {
                "owner_files_only": True,
                "suppress_commands": True,
                "excluded_families_unless_imported": [],
            },
        )
        self.assertTrue(payload["answer_ready"])
        self.assertEqual(payload["selected_tools"], [])
        self.assertTrue(any("wrapper" in item.lower() for item in payload["proof_assertions"]))
        self.assertIn(
            {"from": "apps/storefront/app/checkout/page.tsx", "to": "apps/storefront/app/checkout/CheckoutShell.tsx"},
            payload["dependency_edges"],
        )
        self.assertNotIn("apps/storefront/package.json", payload["candidate_files"])
        self.assertNotIn("apps/server/test/health.e2e-spec.ts", payload["supporting_evidence_files"])

    def test_show_runtime_preflight_internal_fast_path_skips_context_pack_surface_logging(self) -> None:
        query = "Find the smallest owner file set for the storefront checkout entrypoint, the shared checkout CTA label, the backend /ready contract, and the Playwright checkout spec."
        with tempfile.TemporaryDirectory(prefix="runtime-preflight-fast-path-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            benchmark_log = run_root / "benchmark-log.jsonl"
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            env["AGENTIUX_DEV_BENCHMARK_LOG"] = str(benchmark_log)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                show_runtime_preflight(
                    workspace,
                    request_text=query,
                    limit=4,
                )

            records = [
                json.loads(line)
                for line in benchmark_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual([record["surface"] for record in records], ["show_runtime_preflight"])

    def test_triage_repo_request_infers_owner_only_for_cross_app_benchmark_query(self) -> None:
        query = "Find the storefront checkout route file and shared package file that own the customer checkout CTA copy."
        with tempfile.TemporaryDirectory(prefix="repo-triage-cross-app-benchmark-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = triage_repo_request(
                    workspace,
                    request_text=query,
                    limit=5,
                )

        self.assertEqual(payload["candidate_commands"], [])
        self.assertEqual(
            payload["candidate_files"],
            [
                "apps/storefront/app/checkout/page.tsx",
                "packages/checkout-cta/src/label.ts",
            ],
        )
        self.assertEqual(payload["supporting_evidence_files"], [])
        self.assertEqual(
            payload["applied_constraints"],
            {
                "owner_files_only": True,
                "suppress_commands": True,
                "excluded_families_unless_imported": [],
            },
        )
        self.assertTrue(payload["answer_ready"])
        self.assertEqual(payload["selected_tools"], [])
        self.assertTrue(any("wrapper" in item.lower() for item in payload["proof_assertions"]))
        self.assertEqual(
            payload["follow_up_policy"],
            {
                "answer_now": True,
                "allow_search_context_index": False,
                "allow_show_context_structure": False,
                "tool_budget": 0,
                "shell_read_budget": 0,
            },
        )
        self.assertIn(
            {"from": "apps/storefront/app/checkout/page.tsx", "to": "apps/storefront/app/checkout/CheckoutShell.tsx"},
            payload["dependency_edges"],
        )
        self.assertIn(
            {"from": "apps/storefront/app/checkout/CheckoutShell.tsx", "to": "packages/checkout-cta/src/label.ts"},
            payload["dependency_edges"],
        )

    def test_triage_repo_request_separates_primary_owners_from_command_evidence(self) -> None:
        query = "Find the minimal package-level verification commands and owner files for the storefront checkout CTA and the backend /ready contract."
        with tempfile.TemporaryDirectory(prefix="repo-triage-command-minimal-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = triage_repo_request(
                    workspace,
                    request_text=query,
                    limit=5,
                )

        self.assertEqual((payload["resolved_route"] or {}).get("route_id"), "verification")
        self.assertEqual(
            payload["candidate_files"],
            [
                "apps/server/package.json",
                "apps/storefront/package.json",
            ],
        )
        self.assertEqual(payload["primary_owner_files"], payload["candidate_files"])
        self.assertIn("apps/server/test/health.e2e-spec.ts", payload["supporting_evidence_files"])
        self.assertIn("tests/storefront-checkout.spec.ts", payload["supporting_evidence_files"])
        self.assertNotIn("apps/storefront/app/checkout/page.tsx", payload["supporting_evidence_files"])
        self.assertNotIn("apps/server/src/health/health.controller.ts", payload["candidate_files"])
        self.assertTrue(payload["answer_ready"])
        self.assertEqual(payload["selected_tools"], [])
        self.assertTrue(any("exact package-level commands" in item.lower() for item in payload["proof_assertions"]))
        self.assertEqual(
            payload["follow_up_policy"],
            {
                "answer_now": True,
                "allow_search_context_index": False,
                "allow_show_context_structure": False,
                "tool_budget": 0,
                "shell_read_budget": 0,
            },
        )
        self.assertEqual(
            payload["next_read_paths"],
            [
                "apps/server/package.json",
                "apps/storefront/package.json",
                "apps/server/test/health.e2e-spec.ts",
                "tests/storefront-checkout.spec.ts",
            ],
        )

    def test_runtime_preflight_and_triage_share_ready_contract_for_owner_query(self) -> None:
        query = "Find the storefront checkout route file and shared package file that own the customer checkout CTA copy."
        with tempfile.TemporaryDirectory(prefix="runtime-ready-contract-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                preflight_payload = show_runtime_preflight(
                    workspace,
                    request_text=query,
                    limit=5,
                )
                triage_payload = triage_repo_request(
                    workspace,
                    request_text=query,
                    limit=5,
                )

        self.assertEqual(
            preflight_payload["preflight"]["primary_owner_files"],
            triage_payload["primary_owner_files"],
        )
        self.assertEqual(
            preflight_payload["preflight"]["follow_up_policy"],
            triage_payload["follow_up_policy"],
        )
        self.assertEqual(preflight_payload["preflight"]["selected_tools"], [])
        self.assertEqual(triage_payload["selected_tools"], [])

    def test_runtime_owner_rerank_uses_exact_package_ownership_and_do_not_scan_paths(self) -> None:
        ranked = _rank_runtime_owner_candidates(
            owner_candidates=[
                {"path": "apps/other/src/job.ts", "why": "This path ranked highly for the current owner query."},
                {"path": "apps/server/src/health/health.controller.ts", "why": "This controller owns the `/ready` HTTP contract."},
                {"path": "apps/server/package.json", "why": "Package-owned verification commands live here."},
                {"path": "apps/server/src/admin/admin.controller.ts", "why": "This path ranked highly for the current owner query."},
            ],
            command_suggestions=[
                {
                    "command": "pnpm --filter @bench/server test:readiness",
                    "source_path": "apps/server/package.json",
                }
            ],
            do_not_scan_paths=["apps/server/src/admin/"],
        )

        self.assertEqual(
            [item["path"] for item in ranked],
            [
                "apps/server/package.json",
                "apps/server/src/health/health.controller.ts",
                "apps/other/src/job.ts",
                "apps/server/src/admin/admin.controller.ts",
            ],
        )

    def test_refresh_context_index_precomputes_route_shortlists_and_ownership_graph(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-projections-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_payload = refresh_context_index(workspace)
                cache_paths = context_cache_paths(workspace)
                ownership_graph = read_query_cache(
                    cache_paths["context_store"],
                    cache_kind=QUERY_CACHE_OWNERSHIP_GRAPH_KIND,
                    cache_key=refresh_payload["workspace_fingerprint"],
                )
                verification_projection = read_query_cache(
                    cache_paths["context_store"],
                    cache_kind=QUERY_CACHE_ROUTE_SHORTLIST_KIND,
                    cache_key="verification",
                )

        self.assertEqual(refresh_payload["runtime_projection_status"], "rebuilt")
        self.assertGreaterEqual(refresh_payload["route_projection_count"], 4)
        self.assertIsNotNone(ownership_graph)
        self.assertGreater(int((ownership_graph or {}).get("payload", {}).get("path_count") or 0), 0)
        page_entry = ((ownership_graph or {}).get("payload", {}).get("by_path", {}).get("apps/storefront/app/checkout/page.tsx", {}) or {})
        self.assertEqual(page_entry.get("package_manifest"), "apps/storefront/package.json")
        self.assertIn("route-entrypoint", page_entry.get("signals", []))
        self.assertIsNotNone(verification_projection)
        self.assertTrue((verification_projection or {}).get("payload", {}).get("priority_paths"))
        self.assertTrue((verification_projection or {}).get("payload", {}).get("why_these_files", {}).get("summary"))

    def test_show_workspace_context_pack_reuses_task_scoped_cache_for_reordered_query(self) -> None:
        first_query = "Inspect checkout CTA owner files and readiness package command triage"
        follow_up_query = "Readiness package command triage and checkout CTA owner files inspect"
        with tempfile.TemporaryDirectory(prefix="task-cache-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                workstream = create_workstream(
                    workspace,
                    title="Checkout readiness triage",
                    scope_summary="Investigate checkout owner files and readiness commands.",
                )
                create_task(
                    workspace,
                    title="Checkout readiness owner triage",
                    objective="Find the owner files and package-level commands for the checkout readiness slice.",
                    linked_workstream_id=workstream["created_workstream_id"],
                    make_current=True,
                )
                refresh_context_index(workspace)
                first_payload = show_workspace_context_pack(
                    workspace,
                    request_text=first_query,
                    route_id="verification",
                    limit=4,
                )
                follow_up_payload = show_workspace_context_pack(
                    workspace,
                    request_text=follow_up_query,
                    route_id="verification",
                    limit=4,
                )

        self.assertEqual(first_payload["cache_status"], "miss")
        self.assertEqual(follow_up_payload["cache_status"], "task-hit")
        self.assertEqual(
            first_payload["context_pack"]["scope_signature"],
            follow_up_payload["context_pack"]["scope_signature"],
        )
        self.assertTrue(follow_up_payload["workspace_context"]["current_task"]["task_id"])
        self.assertEqual(
            [item["path"] for item in first_payload["context_pack"]["owner_candidates"][:2]],
            [item["path"] for item in follow_up_payload["context_pack"]["owner_candidates"][:2]],
        )

    def test_show_workspace_context_pack_separates_task_cache_by_limit_bucket(self) -> None:
        query = "Inspect checkout CTA owner files and readiness package command triage"
        with tempfile.TemporaryDirectory(prefix="task-cache-limit-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                workstream = create_workstream(
                    workspace,
                    title="Checkout readiness triage",
                    scope_summary="Investigate checkout owner files and readiness commands.",
                )
                create_task(
                    workspace,
                    title="Checkout readiness owner triage",
                    objective="Find the owner files and package-level commands for the checkout readiness slice.",
                    linked_workstream_id=workstream["created_workstream_id"],
                    make_current=True,
                )
                refresh_context_index(workspace)
                wide_payload = show_workspace_context_pack(
                    workspace,
                    request_text=query,
                    route_id="verification",
                    limit=8,
                )
                narrow_payload = show_workspace_context_pack(
                    workspace,
                    request_text=query,
                    route_id="verification",
                    limit=4,
                )

        self.assertEqual(wide_payload["cache_status"], "miss")
        self.assertEqual(narrow_payload["cache_status"], "miss")
        self.assertLessEqual(len(narrow_payload["context_pack"]["selected_chunks"]), 4)

    def test_show_runtime_preflight_keeps_secondary_analysis_route_for_mixed_owner_query(self) -> None:
        query = (
            "Identify the smallest set of files to inspect for changing the storefront checkout CTA copy, "
            "confirming the storefront checkout entrypoint, inspecting the backend /ready contract, and "
            "inspecting the Playwright spec that verifies checkout CTA text."
        )
        with tempfile.TemporaryDirectory(prefix="runtime-preflight-secondary-route-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                payload = show_runtime_preflight(
                    workspace,
                    request_text=query,
                    limit=5,
                )

        route_ids = [item["route_id"] for item in payload["route_candidates"]]
        self.assertIn("verification", route_ids)
        self.assertIn("analysis", route_ids)

    def test_show_runtime_preflight_reuses_last_high_confidence_request_when_follow_up_is_blank(self) -> None:
        query = "Find the minimal package-level verification commands and owner files for the storefront checkout CTA and the backend /ready contract."
        with tempfile.TemporaryDirectory(prefix="runtime-preflight-memory-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace)
                seeded_payload = show_runtime_preflight(
                    workspace,
                    request_text=query,
                    route_id="verification",
                    limit=4,
                )
                follow_up_payload = show_runtime_preflight(
                    workspace,
                    request_text=None,
                    limit=4,
                )

        self.assertEqual(seeded_payload["resolved_route"]["route_id"], "verification")
        self.assertEqual(follow_up_payload["preflight"]["request_text_source"], "last_high_confidence_request")
        self.assertEqual(follow_up_payload["effective_request_text"], query)
        self.assertEqual(follow_up_payload["resolved_route"]["route_id"], "verification")
        self.assertTrue(follow_up_payload["preflight"]["next_read_paths"])

    def test_repo_maturity_detects_empty_scaffold_and_existing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="repo-maturity-") as tmp_dir:
            root = Path(tmp_dir)
            empty = root / "empty"
            scaffold = root / "scaffold"
            existing = root / "existing"
            empty.mkdir()
            scaffold.mkdir()
            existing.mkdir()
            (scaffold / "package.json").write_text("{\"name\":\"scaffold\"}\n", encoding="utf-8")
            (existing / "package.json").write_text("{\"name\":\"existing\"}\n", encoding="utf-8")
            (existing / "src").mkdir()
            (existing / "src" / "app.ts").write_text("export const ready = true;\n", encoding="utf-8")

            empty_detection = detect_workspace(empty)
            scaffold_detection = detect_workspace(scaffold)
            existing_detection = detect_workspace(existing)

        self.assertEqual(empty_detection["repo_maturity"]["mode"], "empty")
        self.assertEqual(scaffold_detection["repo_maturity"]["mode"], "scaffold")
        self.assertEqual(existing_detection["repo_maturity"]["mode"], "existing")

    def test_query_surfaces_do_not_full_hydrate_context_store_on_hot_path(self) -> None:
        query = "Inspect the structural owner files for the storefront checkout CTA and backend readiness path."
        with tempfile.TemporaryDirectory(prefix="context-hot-path-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = create_fixture_repo(run_root, PLUGIN_ROOT, fixture_definition("codex-benchmark-workspace"))
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                refresh_context_index(workspace, query_text=query, route_id="analysis")
                with mock.patch(
                    "agentiux_dev_context_cache.load_context_store_chunks",
                    side_effect=AssertionError("full chunk hydration is forbidden on the query hot path"),
                ), mock.patch(
                    "agentiux_dev_context_cache.load_context_store_modules",
                    side_effect=AssertionError("full module hydration is forbidden on the query hot path"),
                ):
                    search_payload = search_context_index(workspace, query, route_id="analysis", limit=4)
                    pack_payload = show_workspace_context_pack(workspace, request_text=query, route_id="analysis", limit=4)
                    preflight_payload = show_runtime_preflight(workspace, request_text=query, route_id="analysis", limit=4)
                    structure_payload = show_context_structure(workspace, query_text=query, route_id="analysis", limit=4)
                    audit_payload = run_analysis_audit(workspace, "architecture", query_text=query, limit=4)

        self.assertTrue(search_payload["matches"])
        self.assertTrue(pack_payload["context_pack"]["selected_chunks"])
        self.assertTrue(preflight_payload["preflight"]["next_read_paths"])
        self.assertTrue(structure_payload["matches"])
        self.assertTrue(audit_payload["evidence"])

    def test_show_workspace_context_pack_keeps_selected_chunks_identified_and_within_working_budget(self) -> None:
        query = "inspect structural hotspots and modules"
        with tempfile.TemporaryDirectory(prefix="context-pack-budget-") as tmp_dir:
            run_root = Path(tmp_dir)
            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(REPO_ROOT)
                refresh_context_index(REPO_ROOT, query_text=query, route_id="analysis", force=True)
                payload = show_workspace_context_pack(
                    REPO_ROOT,
                    request_text=query,
                    route_id="analysis",
                    limit=4,
                    semantic_mode="enabled",
                )

        selected_chunks = payload["context_pack"]["selected_chunks"]
        self.assertTrue(selected_chunks)
        self.assertTrue(all(item.get("chunk_id") for item in selected_chunks))
        self.assertLessEqual(
            int((payload.get("payload") or {}).get("bytes") or 0),
            SURFACE_WORKING_BUDGETS["show_workspace_context_pack"],
        )

    def test_search_context_index_auto_mode_without_explicit_analysis_stays_symbolic(self) -> None:
        query = "crosscutting broadread memory snapshot"
        with tempfile.TemporaryDirectory(prefix="context-auto-symbolic-") as tmp_dir:
            run_root = Path(tmp_dir)
            workspace = run_root / "workspace"
            module_root = workspace / "packages" / "analysis-core"
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
                "# Analysis Core\n\nIntro paragraph.\n\n## Overview\n\nDetails.\n",
                encoding="utf-8",
            )
            (docs_root / "architecture.md").write_text(
                "# Architecture\n\nCrosscutting boundary notes and broadread memory snapshot guidance.\n",
                encoding="utf-8",
            )
            (src_root / "app.ts").write_text(
                'export function loadSnapshotMemory() { return helperBoundary(); }\nimport { helperBoundary } from "./helper";\n',
                encoding="utf-8",
            )
            (src_root / "helper.ts").write_text(
                'export function helperBoundary() { return "broadread"; }\n',
                encoding="utf-8",
            )

            env = isolated_plugin_env(run_root, PLUGIN_ROOT)
            with temporary_env(env):
                init_workspace(workspace)
                persist_generated_memory_snapshot(
                    workspace,
                    {
                        "title": "Cross-cutting analysis memory",
                        "source_audit_mode": "architecture",
                        "source_query_text": "boundary coupling broadread",
                        "source_module_path": "packages/analysis-core",
                        "confidence": 0.81,
                        "body_markdown": (
                            "- Boundary pressure between entrypoints and helpers.\n"
                            "- Broadread risk around large module coordination.\n"
                        ),
                        "provenance": {"source": "runtime-boundary-test"},
                    },
                )
                refresh_context_index(workspace)
                payload = search_context_index(
                    workspace,
                    query,
                    limit=8,
                    semantic_mode="auto",
                )

        self.assertTrue(payload["matches"])
        self.assertTrue(all(match["match_source"] == "symbolic" for match in payload["matches"]))


if __name__ == "__main__":
    unittest.main()
