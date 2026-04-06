from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition
from tools.codex_benchmark_adapter import build_codex_benchmark_bootstrap

from agentiux_dev_context import refresh_context_index
from agentiux_dev_context_cache import context_cache_paths, load_usage
from agentiux_dev_e2e_support import create_fixture_repo, fixture_definition, isolated_plugin_env, temporary_env, write_json_file
from agentiux_dev_lib import init_workspace
from install_home_local import install_plugin_into_codex_home


GROUP_KEY = "codex-cli-ab-evidence"
RUNTIME_WARM_GROUP_KEY = "codex-cli-runtime-warm-evidence"
BOOTSTRAP_ASSISTED_CONDITION = "bootstrap-assisted"
ASSISTED_CONDITION = BOOTSTRAP_ASSISTED_CONDITION
RUNTIME_WARM_CONDITION = "runtime-warm"
RAW_CONDITION = "raw"
BENCHMARK_FIXTURE_ID = "codex-benchmark-workspace"
BENCHMARK_TASK_FILTER_ENV = "AGENTIUX_DEV_BENCHMARK_TASKS"
BOOTSTRAP_AB_RUN_PLAN = [
    BOOTSTRAP_ASSISTED_CONDITION,
    RAW_CONDITION,
    RAW_CONDITION,
    BOOTSTRAP_ASSISTED_CONDITION,
]
BENCHMARK_RUNTIME_MODES = (
    RAW_CONDITION,
    RUNTIME_WARM_CONDITION,
    BOOTSTRAP_ASSISTED_CONDITION,
)


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    title: str
    scenario: str
    benchmark_query: str
    benchmark_prompt: str
    expected_core_files: tuple[str, ...]
    expected_optional_files: tuple[str, ...] = ()
    expected_command_patterns: tuple[str, ...] = ()
    expected_command_examples: tuple[str, ...] = ()
    expected_primary_files: tuple[str, ...] = ()
    distractor_files: tuple[str, ...] = ()
    max_candidate_files: int | None = None
    max_candidate_commands: int | None = None


BENCHMARK_TASKS = (
    BenchmarkTask(
        task_id="owner-file-routing",
        title="Owner-file routing",
        scenario="Find the smallest real owner set for storefront checkout CTA copy, the checkout entrypoint, readiness contract, and the checkout Playwright spec without drifting into admin checkout.",
        benchmark_query=(
            "Find the smallest owner file set for the storefront checkout entrypoint, the shared checkout CTA label, "
            "the backend /ready contract, and the Playwright checkout spec."
        ),
        benchmark_prompt=(
            "Read-only benchmark. Inspect this repository and identify the smallest set of files "
            "you would inspect if asked to change the storefront checkout CTA copy, confirm the storefront checkout entrypoint, "
            "inspect the backend /ready contract, and inspect the Playwright spec that verifies checkout CTA text. "
            "Ignore admin checkout unless the requested storefront flow imports it. "
            "This task is about owner files, so return candidate_commands=[] unless a command source file is itself essential evidence. "
            "Do not edit files. Do not run formatters, installers, package managers, or git write operations. "
            "Return only JSON that matches the provided schema."
        ),
        expected_core_files=(
            "apps/storefront/app/checkout/page.tsx",
            "packages/checkout-cta/src/label.ts",
            "apps/server/src/health/health.controller.ts",
            "apps/server/src/health/health.service.ts",
            "tests/storefront-checkout.spec.ts",
        ),
        expected_primary_files=(
            "apps/storefront/app/checkout/page.tsx",
            "packages/checkout-cta/src/label.ts",
            "apps/server/src/health/health.controller.ts",
        ),
        distractor_files=(
            "README.md",
            "apps/admin/app/checkout/page.tsx",
            "apps/storefront/app/checkout/CheckoutShell.tsx",
            "playwright.config.ts",
        ),
        max_candidate_files=5,
        max_candidate_commands=0,
    ),
    BenchmarkTask(
        task_id="verification-command-discovery",
        title="Verification command discovery",
        scenario="Find the minimal package-level verification commands and their real owner package/config/spec files for storefront checkout and server readiness checks.",
        benchmark_query=(
            "Find the minimal package-level verification commands and owner files for the storefront checkout CTA "
            "and the backend /ready contract."
        ),
        benchmark_prompt=(
            "Read-only benchmark. Inspect this repository and identify the smallest set of files and "
            "package-level shell commands you would use to verify a small change to the storefront checkout CTA and backend /ready contract. "
            "Do not execute commands. Do not edit files. Do not run formatters, installers, package managers, or git write operations. "
            "Return only JSON that matches the provided schema."
        ),
        expected_core_files=(
            "apps/storefront/package.json",
            "apps/server/package.json",
            "playwright.config.ts",
            "tests/storefront-checkout.spec.ts",
            "apps/server/test/health.e2e-spec.ts",
        ),
        expected_command_patterns=(
            r"\bpnpm\s+--filter\s+@bench/storefront\s+(?:run\s+)?test:checkout\b",
            r"\bpnpm\s+--filter\s+@bench/server\s+(?:run\s+)?test:readiness\b",
        ),
        expected_command_examples=(
            "pnpm --filter @bench/storefront test:checkout",
            "pnpm --filter @bench/server test:readiness",
        ),
        expected_primary_files=(
            "apps/storefront/package.json",
            "apps/server/package.json",
        ),
        distractor_files=(
            "package.json",
            "README.md",
            "apps/admin/package.json",
        ),
        max_candidate_files=5,
        max_candidate_commands=2,
    ),
    BenchmarkTask(
        task_id="cross-app-disambiguation",
        title="Cross-app disambiguation",
        scenario="Route a customer checkout CTA request to storefront owners while avoiding the sibling admin checkout surface.",
        benchmark_query=(
            "Find the storefront checkout route file and shared package file that own the customer checkout CTA copy."
        ),
        benchmark_prompt=(
            "Read-only benchmark. Inspect this repository and identify the smallest set of files "
            "that own the customer storefront checkout entrypoint and shared CTA label across app/package boundaries. "
            "Do not include admin checkout unless the storefront files explicitly depend on it. "
            "This task is about app/package routing, so return candidate_commands=[] unless a command source file is itself essential evidence. "
            "Do not edit files. Do not run formatters, installers, package managers, or git write operations. "
            "Return only JSON that matches the provided schema."
        ),
        expected_core_files=(
            "apps/storefront/app/checkout/page.tsx",
            "packages/checkout-cta/src/label.ts",
        ),
        expected_primary_files=(
            "apps/storefront/app/checkout/page.tsx",
            "packages/checkout-cta/src/label.ts",
        ),
        distractor_files=(
            "apps/admin/app/checkout/page.tsx",
            "apps/storefront/app/checkout/CheckoutShell.tsx",
            "README.md",
        ),
        max_candidate_files=2,
        max_candidate_commands=0,
    ),
    BenchmarkTask(
        task_id="symptom-to-owner-triage",
        title="Symptom-to-owner triage",
        scenario="Start from a failing readiness log snippet and return the smallest owner set plus the minimal next verification command.",
        benchmark_query=(
            "Given a readiness failure that returned admin-console metadata instead of the storefront checkout readiness contract, "
            "find the smallest owner files and minimal package-level command to inspect next."
        ),
        benchmark_prompt=(
            "Read-only benchmark. A verification log ended with:\n"
            "AssertionError: GET /ready expected {\"status\":\"ok\",\"source\":\"storefront-checkout\"}\n"
            "Received: {\"status\":\"booting\",\"source\":\"admin-console\"}\n"
            "Inspect this repository and return the smallest set of files plus the minimal package-level command "
            "you would inspect next to triage the owner of this failure. Do not execute commands. "
            "Do not edit files. Do not run formatters, installers, package managers, or git write operations. "
            "Return only JSON that matches the provided schema."
        ),
        expected_core_files=(
            "apps/server/package.json",
            "apps/server/test/health.e2e-spec.ts",
            "apps/server/src/health/health.controller.ts",
            "apps/server/src/health/health.service.ts",
        ),
        expected_command_patterns=(r"\bpnpm\s+--filter\s+@bench/server\s+(?:run\s+)?test:readiness\b",),
        expected_command_examples=("pnpm --filter @bench/server test:readiness",),
        expected_primary_files=(
            "apps/server/src/health/health.controller.ts",
            "apps/server/src/health/health.service.ts",
        ),
        distractor_files=(
            "apps/server/src/admin/admin.controller.ts",
            "apps/admin/app/checkout/page.tsx",
            "README.md",
        ),
        max_candidate_files=4,
        max_candidate_commands=1,
    ),
)
BENCHMARK_TASK_INDEX = {task.task_id: task for task in BENCHMARK_TASKS}

USAGE_DELTA_KEYS = (
    "refresh_count",
    "fresh_hit_count",
    "search_count",
    "context_pack_hit_count",
    "context_pack_miss_count",
)
USAGE_LAST_KEYS = (
    "last_refresh_duration_ms",
    "last_context_pack_selected_tool_count",
)
CODEX_RUN_TIMEOUT_SECONDS = 300
CODEX_PROGRESS_HEARTBEAT_SECONDS = 15
CODEX_HOME_SEED_FILES = (
    "auth.json",
    "config.toml",
    "version.json",
    "models_cache.json",
)
CODEX_HOME_SEED_DIRS: tuple[str, ...] = ()
PLUGIN_HELPER_COMMAND_PATTERN = re.compile(
    r"(?:^|['\"\s])(?:(?:\S*/)?python(?:3)?\s+(?:\S*/)?agentiux_dev_state\.py|(?:\S*/)?agentiux)\b"
)
RG_INVOCATION_PATTERN = re.compile(r"(?<![\w-])rg(?=\s|$)")
READ_OPERATION_PATTERNS = (
    re.compile(r"(?<![\w-])sed\s+-n\b"),
    re.compile(r"(?<![\w-])cat(?=\s|$)"),
    re.compile(r"(?<![\w-])head(?=\s|$)"),
    re.compile(r"(?<![\w-])tail(?=\s|$)"),
    re.compile(r"(?<![\w-])awk(?=\s|$)"),
)
BROAD_SCAN_PATTERNS = (
    re.compile(r"(?<![\w-])rg\s+--files\b"),
    re.compile(r"(?<![\w-])find(?=\s|$)"),
    re.compile(r"(?<![\w-])tree(?=\s|$)"),
    re.compile(r"(?<![\w-])ls(?=\s|$)"),
)


def _progress_log(run_label: str, message: str) -> None:
    print(f"[codex-cli:{run_label}] {message}", file=sys.stderr, flush=True)


def _compact_text(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _replace_toml_section(config_text: str, section_header: str, section_lines: list[str] | None = None) -> str:
    lines = config_text.splitlines()
    filtered: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == section_header:
            skipping = True
            continue
        if skipping and stripped.startswith("[") and stripped.endswith("]"):
            skipping = False
        if not skipping:
            filtered.append(line)
    while filtered and not filtered[-1].strip():
        filtered.pop()
    if section_lines:
        if filtered:
            filtered.append("")
        filtered.extend([section_header, *section_lines])
    rendered = "\n".join(filtered).strip()
    return f"{rendered}\n" if rendered else ""


def _set_agentiux_plugin_enabled(config_text: str, *, enabled: bool) -> str:
    section_header = '[plugins."agentiux-dev@local-plugins"]'
    return _replace_toml_section(config_text, section_header, ["enabled = true"] if enabled else None)


def _set_agentiux_mcp_server(
    config_text: str,
    *,
    plugin_root: Path | None,
    state_root: Path | None,
    marketplace_path: Path | None,
    benchmark_log_path: Path | None = None,
) -> str:
    section_header = '[mcp_servers."agentiux-dev-state"]'
    if plugin_root is None:
        return _replace_toml_section(config_text, section_header, None)
    env_items = {
        "AGENTIUX_DEV_PLUGIN_ROOT": str(plugin_root.resolve()),
        "AGENTIUX_DEV_INSTALL_ROOT": str(plugin_root.resolve()),
    }
    if state_root is not None:
        env_items["AGENTIUX_DEV_STATE_ROOT"] = str(state_root.resolve())
    if marketplace_path is not None:
        env_items["AGENTIUX_DEV_MARKETPLACE_PATH"] = str(marketplace_path.resolve())
    if benchmark_log_path is not None:
        env_items["AGENTIUX_DEV_BENCHMARK_LOG"] = str(benchmark_log_path.resolve())
    env_inline = ", ".join(f"{key} = {json.dumps(value)}" for key, value in env_items.items())
    section_lines = [
        "enabled = true",
        f'command = {json.dumps("python3")}',
        f'args = [{json.dumps(str((plugin_root / "scripts" / "agentiux_dev_mcp.py").resolve()))}]',
        f"env = {{ {env_inline} }}",
        "startup_timeout_sec = 30",
        "tool_timeout_sec = 120",
    ]
    return _replace_toml_section(config_text, section_header, section_lines)


def _seed_codex_home(target_root: Path, *, enable_agentiux_plugin: bool) -> Path:
    source_root = Path.home() / ".codex"
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "shell_snapshots").mkdir(parents=True, exist_ok=True)
    for name in CODEX_HOME_SEED_FILES:
        source_path = source_root / name
        if source_path.exists():
            shutil.copy2(source_path, target_root / name)
    source_config_path = source_root / "config.toml"
    if source_config_path.exists() or enable_agentiux_plugin:
        target_config_path = target_root / "config.toml"
        source_config = source_config_path.read_text(encoding="utf-8") if source_config_path.exists() else ""
        rendered_config = _set_agentiux_plugin_enabled(source_config, enabled=enable_agentiux_plugin)
        if rendered_config:
            target_config_path.write_text(rendered_config, encoding="utf-8")
        elif target_config_path.exists():
            target_config_path.unlink()
    for name in CODEX_HOME_SEED_DIRS:
        source_path = source_root / name
        target_path = target_root / name
        if source_path.is_dir() and not target_path.exists():
            shutil.copytree(source_path, target_path)
    return target_root


def _prepare_benchmark_runtime_env(
    run_root: Path,
    source_plugin_root: Path,
    condition: str,
    *,
    benchmark_log_path: Path | None = None,
) -> dict[str, Any]:
    env = isolated_plugin_env(run_root, source_plugin_root)
    product_home = run_root / "product-home"
    product_home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(product_home)
    env["CODEX_HOME"] = str(
        _seed_codex_home(
            product_home / ".codex",
            enable_agentiux_plugin=_condition_uses_runtime_prewarm(condition),
        )
    )
    setup: dict[str, Any] = {
        "env": env,
        "product_home": str(product_home),
        "codex_home": env["CODEX_HOME"],
        "plugin_runtime_root": None,
        "plugin_registration_mode": "disabled",
        "install_result": None,
    }
    if not _condition_uses_runtime_prewarm(condition):
        env.pop("AGENTIUX_DEV_PLUGIN_ROOT", None)
        env.pop("AGENTIUX_DEV_INSTALL_ROOT", None)
        env.pop("AGENTIUX_DEV_MARKETPLACE_PATH", None)
        if benchmark_log_path is not None:
            env["AGENTIUX_DEV_BENCHMARK_LOG"] = str(benchmark_log_path.resolve())
        return setup
    codex_home = Path(env["CODEX_HOME"]).resolve()
    install_result = install_plugin_into_codex_home(
        source_plugin_root,
        codex_home,
    )
    cache_root = Path(str(install_result["cache_install_root"])).resolve()
    env["AGENTIUX_DEV_PLUGIN_ROOT"] = str(cache_root)
    env["AGENTIUX_DEV_INSTALL_ROOT"] = str(cache_root)
    env["AGENTIUX_DEV_MARKETPLACE_PATH"] = str(install_result["marketplace_path"])
    if benchmark_log_path is not None:
        env["AGENTIUX_DEV_BENCHMARK_LOG"] = str(benchmark_log_path.resolve())
    config_path = codex_home / "config.toml"
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config_text = _set_agentiux_mcp_server(
        config_text,
        plugin_root=cache_root,
        state_root=Path(env["AGENTIUX_DEV_STATE_ROOT"]),
        marketplace_path=Path(str(install_result["marketplace_path"])),
        benchmark_log_path=benchmark_log_path,
    )
    config_path.write_text(config_text, encoding="utf-8")
    setup.update(
        {
            "plugin_runtime_root": str(cache_root),
            "plugin_registration_mode": "codex-cache-local-plugin-copy+direct-mcp-config",
            "install_result": install_result,
        }
    )
    return setup


def _prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _selected_benchmark_tasks() -> tuple[BenchmarkTask, ...]:
    raw_filter = str(os.environ.get(BENCHMARK_TASK_FILTER_ENV) or "").strip()
    if not raw_filter:
        return BENCHMARK_TASKS
    requested_ids: list[str] = []
    for task_id in raw_filter.split(","):
        normalized = task_id.strip()
        if normalized and normalized not in requested_ids:
            requested_ids.append(normalized)
    if not requested_ids:
        return BENCHMARK_TASKS
    unknown_ids = [task_id for task_id in requested_ids if task_id not in BENCHMARK_TASK_INDEX]
    if unknown_ids:
        raise ValueError(f"Unknown benchmark task id(s): {', '.join(unknown_ids)}")
    requested_set = set(requested_ids)
    return tuple(task for task in BENCHMARK_TASKS if task.task_id in requested_set)


def _write_codex_bootstrap_overlay(run_root: Path, workspace: Path, bootstrap_payload: dict[str, Any]) -> dict[str, Any]:
    markdown = ((bootstrap_payload.get("bootstrap") or {}).get("markdown") or "").strip()
    if not markdown:
        raise ValueError("Codex bootstrap payload did not include markdown guidance.")
    bootstrap_path = run_root / "codex-bootstrap.md"
    bootstrap_path.write_text(markdown + "\n", encoding="utf-8")
    bootstrap = bootstrap_payload.get("bootstrap") or {}
    agents_paths = _agents_files_in_tree(workspace)
    return {
        "target_path": str(bootstrap_path),
        "delivery_config_key": bootstrap.get("delivery_config_key") or "model_instructions_file",
        "workspace_agents_file_exists": bool(agents_paths),
        "workspace_agents_paths": agents_paths,
        "outside_workspace_tree": not _path_is_within(bootstrap_path, workspace),
        "route_id": bootstrap.get("route_id"),
        "candidate_paths": [
            item.get("path")
            for item in bootstrap.get("candidate_paths") or []
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ],
        "command_hints": [
            item.get("command")
            for item in bootstrap.get("command_hints") or []
            if isinstance(item, dict) and isinstance(item.get("command"), str)
        ],
        "command_count": len(bootstrap.get("commands") or []),
        "markdown_bytes": len(markdown.encode("utf-8")),
        "payload_bytes": int((bootstrap_payload.get("payload") or {}).get("bytes") or 0),
        "within_ceiling": bool((bootstrap_payload.get("payload") or {}).get("within_ceiling")),
        "bootstrap_cache_status": bootstrap_payload.get("bootstrap_cache_status"),
        "bootstrap_store_path": bootstrap_payload.get("bootstrap_store_path"),
    }


def _schema_payload() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_files", "candidate_commands", "why", "risks", "confidence"],
        "properties": {
            "candidate_files": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1},
            },
            "candidate_commands": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1},
            },
            "why": {"type": "string", "minLength": 8},
            "risks": {
                "type": "array",
                "maxItems": 6,
                "items": {"type": "string", "minLength": 1},
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def _usage_metrics(workspace: Path) -> dict[str, Any]:
    usage = load_usage(context_cache_paths(workspace))
    payload: dict[str, Any] = {}
    for key in [*USAGE_DELTA_KEYS, *USAGE_LAST_KEYS]:
        payload[key] = int(usage.get(key) or 0)
    return payload


def _usage_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    delta = {key: int(after.get(key) or 0) - int(before.get(key) or 0) for key in USAGE_DELTA_KEYS}
    for key in USAGE_LAST_KEYS:
        delta[key] = int(after.get(key) or 0)
    return delta


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _validate_output_schema(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if set(payload) != {"candidate_files", "candidate_commands", "why", "risks", "confidence"}:
        return False
    if not isinstance(payload["candidate_files"], list) or not payload["candidate_files"]:
        return False
    if any(not isinstance(item, str) or not item for item in payload["candidate_files"]):
        return False
    if not isinstance(payload["candidate_commands"], list):
        return False
    if any(not isinstance(item, str) or not item for item in payload["candidate_commands"]):
        return False
    if not isinstance(payload["why"], str) or not payload["why"].strip():
        return False
    if not isinstance(payload["risks"], list) or any(not isinstance(item, str) or not item for item in payload["risks"]):
        return False
    if not isinstance(payload["confidence"], (float, int)):
        return False
    return 0 <= float(payload["confidence"]) <= 1


def _normalize_candidate_path(candidate: Any, workspace: Path) -> str | None:
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    text = candidate.strip()
    normalized = Path(text)
    if normalized.is_absolute():
        try:
            return normalized.resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            return normalized.as_posix()
    return normalized.as_posix()


def _normalize_candidate_command(candidate: Any) -> str | None:
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    return " ".join(candidate.split())


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _agents_files_in_tree(workspace: Path) -> list[str]:
    agents_paths: list[str] = []
    for path in workspace.rglob("AGENTS.md"):
        if path.is_file():
            agents_paths.append(path.relative_to(workspace).as_posix())
    return sorted(agents_paths)


def _extract_shell_script(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command
    if len(tokens) >= 3 and tokens[1] == "-lc":
        return tokens[2]
    return command


def _extract_repo_paths_from_text(text: str, workspace: Path) -> list[str]:
    candidates: list[str] = []
    for raw_token in re.split(r"[\s\"'`;|()]+", text):
        token = raw_token.strip().strip(",:")
        if not token or "/" not in token and "." not in token:
            continue
        normalized = _normalize_candidate_path(token, workspace)
        if not normalized:
            continue
        candidate_path = workspace / normalized
        if candidate_path.exists() and candidate_path.is_file():
            candidates.append(normalized)
    return list(dict.fromkeys(candidates))


def _quality_summary(task: BenchmarkTask, output_payload: dict[str, Any] | None, workspace: Path) -> dict[str, Any]:
    normalized_candidates = [
        normalized
        for normalized in (
            _normalize_candidate_path(candidate, workspace)
            for candidate in ((output_payload or {}).get("candidate_files") or [])
        )
        if normalized
    ]
    unique_candidates = list(dict.fromkeys(normalized_candidates))
    candidate_set = set(unique_candidates)
    core_files = set(task.expected_core_files)
    optional_files = set(task.expected_optional_files)
    primary_files = set(task.expected_primary_files or task.expected_core_files)
    distractor_files = set(task.distractor_files)
    core_matches = sorted(candidate_set.intersection(core_files))
    optional_matches = sorted(candidate_set.intersection(optional_files))
    unexpected_files = sorted(candidate_set.difference(core_files.union(optional_files)))
    valid_match_count = len(core_matches) + len(optional_matches)
    primary_rank = next((index for index, path in enumerate(unique_candidates) if path in primary_files), len(unique_candidates) + 1)
    primary_matches = sorted(candidate_set.intersection(primary_files))
    distractor_hits = sorted(candidate_set.intersection(distractor_files))

    normalized_commands = [
        normalized
        for normalized in (
            _normalize_candidate_command(candidate)
            for candidate in ((output_payload or {}).get("candidate_commands") or [])
        )
        if normalized
    ]
    unique_commands = list(dict.fromkeys(normalized_commands))
    command_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in task.expected_command_patterns]
    command_matches = [
        command
        for command in unique_commands
        if command_patterns and any(pattern.search(command) for pattern in command_patterns)
    ]
    expected_command_match_count = sum(
        1
        for pattern in command_patterns
        if any(pattern.search(command) for command in unique_commands)
    )
    unexpected_commands = [
        command
        for command in unique_commands
        if not command_patterns or not any(pattern.search(command) for pattern in command_patterns)
    ]
    allowed_file_count = len(unique_candidates) if task.max_candidate_files is None else task.max_candidate_files
    allowed_command_count = len(unique_commands) if task.max_candidate_commands is None else task.max_candidate_commands
    file_minimality_penalty = len(unexpected_files) + max(len(unique_candidates) - allowed_file_count, 0)
    command_minimality_penalty = len(unexpected_commands) + max(len(unique_commands) - allowed_command_count, 0)
    command_recall = (
        round(expected_command_match_count / len(command_patterns), 2)
        if command_patterns
        else 1.0
    )
    command_precision = (
        round(len(command_matches) / len(unique_commands), 2)
        if unique_commands
        else (1.0 if not command_patterns else 0.0)
    )
    return {
        "task_id": task.task_id,
        "normalized_candidate_files": unique_candidates,
        "core_match_count": len(core_matches),
        "core_matches": core_matches,
        "core_file_recall": round(len(core_matches) / len(core_files), 2) if core_files else 1.0,
        "valid_file_precision": round(valid_match_count / len(candidate_set), 2) if candidate_set else 0.0,
        "optional_match_count": len(optional_matches),
        "optional_matches": optional_matches,
        "primary_match_count": len(primary_matches),
        "primary_matches": primary_matches,
        "primary_file_rank": primary_rank,
        "distractor_file_hit_count": len(distractor_hits),
        "distractor_file_hits": distractor_hits,
        "unexpected_file_count": len(unexpected_files),
        "unexpected_files": unexpected_files,
        "file_minimality_penalty": file_minimality_penalty,
        "normalized_candidate_commands": unique_commands,
        "command_match_count": len(command_matches),
        "command_matches": command_matches,
        "command_recall": command_recall,
        "command_precision": command_precision,
        "unexpected_command_count": len(unexpected_commands),
        "unexpected_commands": unexpected_commands,
        "command_minimality_penalty": command_minimality_penalty,
        "total_minimality_penalty": file_minimality_penalty + command_minimality_penalty,
        "expected_command_examples": list(task.expected_command_examples),
    }


def _command_metrics(events: list[dict[str, Any]], *, workspace: Path, task: BenchmarkTask) -> dict[str, Any]:
    command_count = 0
    plugin_helper_command_count = 0
    direct_mcp_tool_count = 0
    direct_mcp_tool_names: list[str] = []
    rg_count = 0
    read_operation_count = 0
    failed_command_count = 0
    broad_scan_count = 0
    touched_paths: list[str] = []
    for event in events:
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if event.get("type") == "item.completed" and item.get("type") == "mcp_tool_call":
            tool_name = str(item.get("tool") or "").strip()
            server_name = str(item.get("server") or "").strip()
            if tool_name and (server_name == "agentiux-dev-state" or tool_name.startswith("show_") or tool_name == "triage_repo_request"):
                direct_mcp_tool_count += 1
                if tool_name not in direct_mcp_tool_names:
                    direct_mcp_tool_names.append(tool_name)
            continue
        if event.get("type") != "item.completed" or item.get("type") != "command_execution":
            continue
        command_count += 1
        command = " ".join(str(item.get("command") or "").split())
        shell_script = _extract_shell_script(command)
        exit_code = item.get("exit_code")
        aggregated_output = str(item.get("aggregated_output") or "")
        if exit_code not in (0, None):
            failed_command_count += 1
        is_plugin_helper = bool(PLUGIN_HELPER_COMMAND_PATTERN.search(command))
        if is_plugin_helper:
            plugin_helper_command_count += 1
        rg_count += len(RG_INVOCATION_PATTERN.findall(shell_script))
        read_operation_count += sum(len(pattern.findall(shell_script)) for pattern in READ_OPERATION_PATTERNS)
        broad_scan_count += sum(len(pattern.findall(shell_script)) for pattern in BROAD_SCAN_PATTERNS)
        if not is_plugin_helper:
            touched_paths.extend(_extract_repo_paths_from_text(shell_script, workspace))
            touched_paths.extend(_extract_repo_paths_from_text(aggregated_output, workspace))
    unique_paths = list(dict.fromkeys(touched_paths))
    distractor_touches = [path for path in unique_paths if path in set(task.distractor_files)]
    return {
        "command_count": command_count,
        "plugin_helper_command_count": plugin_helper_command_count,
        "plugin_helper_invocation_count": plugin_helper_command_count + direct_mcp_tool_count,
        "plugin_helper_mcp_invocation_count": direct_mcp_tool_count,
        "plugin_helper_tool_names": direct_mcp_tool_names,
        "manual_command_count": command_count - plugin_helper_command_count,
        "manual_shell_invocation_count": command_count - plugin_helper_command_count,
        "manual_read_operation_count": read_operation_count,
        "rg_count": rg_count,
        "rg_invocation_count": rg_count,
        "broad_scan_count": broad_scan_count,
        "unique_repo_path_count": len(unique_paths),
        "unique_repo_paths_touched": unique_paths,
        "distractor_path_touch_count": len(distractor_touches),
        "distractor_paths_touched": distractor_touches,
        "failed_command_count": failed_command_count,
    }


def _todo_progress_message(item: dict[str, Any]) -> str:
    todo_items = item.get("items")
    if not isinstance(todo_items, list) or not todo_items:
        return "todo updated"
    completed = sum(1 for todo_item in todo_items if isinstance(todo_item, dict) and todo_item.get("completed") is True)
    pending = next(
        (
            todo_item.get("text")
            for todo_item in todo_items
            if isinstance(todo_item, dict) and todo_item.get("completed") is not True and isinstance(todo_item.get("text"), str)
        ),
        None,
    )
    progress = f"todo {completed}/{len(todo_items)}"
    if isinstance(pending, str) and pending.strip():
        return f"{progress}: {_compact_text(pending, limit=96)}"
    return progress


def _agent_message_progress(item: dict[str, Any]) -> str:
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return "assistant emitted a final message"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return f"assistant message: {_compact_text(text)}"
    if isinstance(payload, dict):
        details = []
        candidate_files = payload.get("candidate_files")
        if isinstance(candidate_files, list):
            details.append(f"candidate_files={len(candidate_files)}")
        candidate_commands = payload.get("candidate_commands")
        if isinstance(candidate_commands, list):
            details.append(f"candidate_commands={len(candidate_commands)}")
        confidence = payload.get("confidence")
        if isinstance(confidence, (float, int)):
            details.append(f"confidence={float(confidence):.2f}")
        if details:
            return f"assistant emitted final JSON ({', '.join(details)})"
    return "assistant emitted final JSON"


def _event_progress_message(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    if event_type == "thread.started":
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return f"thread started ({thread_id})"
        return "thread started"
    if event_type == "turn.started":
        return "turn started"
    if event_type == "turn.completed":
        usage = event.get("usage")
        usage_bits: list[str] = []
        if isinstance(usage, dict):
            for label, key in (("in", "input_tokens"), ("cached", "cached_input_tokens"), ("out", "output_tokens")):
                value = usage.get(key)
                if isinstance(value, int):
                    usage_bits.append(f"{label}={value}")
        if usage_bits:
            return f"turn completed ({', '.join(usage_bits)})"
        return "turn completed"
    if event_type == "error":
        message = event.get("message")
        if isinstance(message, str) and message.strip():
            return f"error: {_compact_text(message)}"
        return "error"
    if event_type == "turn.failed":
        error = event.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return f"turn failed: {_compact_text(error['message'])}"
        return "turn failed"
    if not isinstance(event_type, str) or not event_type.startswith("item."):
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    if item_type == "command_execution":
        command = item.get("command")
        command_text = _compact_text(command, limit=120) if isinstance(command, str) else "command"
        if event_type == "item.started":
            return f"running {command_text}"
        if event_type == "item.completed":
            exit_code = item.get("exit_code")
            if exit_code not in (0, None):
                return f"command failed exit={exit_code}: {command_text}"
            return None
        return None
    if item_type == "todo_list" and event_type in {"item.updated", "item.completed"}:
        return _todo_progress_message(item)
    if item_type == "agent_message" and event_type == "item.completed":
        return _agent_message_progress(item)
    return None


def _run_codex_exec(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    run_label: str,
) -> tuple[subprocess.CompletedProcess[str], subprocess.TimeoutExpired | None]:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    activity_lock = threading.Lock()
    started_at = time.monotonic()
    last_activity_at = started_at
    last_summary = "process started"

    def note_activity(summary: str | None = None) -> None:
        nonlocal last_activity_at, last_summary
        with activity_lock:
            last_activity_at = time.monotonic()
            if summary:
                last_summary = summary

    def drain_stream(stream: Any, chunks: list[str], *, is_stdout: bool) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                chunks.append(raw_line)
                note_activity()
                line = raw_line.strip()
                if not line:
                    continue
                if is_stdout:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        summary = f"stdout: {_compact_text(line)}"
                    else:
                        summary = _event_progress_message(event)
                else:
                    summary = f"stderr: {_compact_text(line)}"
                if summary:
                    note_activity(summary)
                    _progress_log(run_label, summary)
        finally:
            stream.close()

    readers = [
        threading.Thread(target=drain_stream, args=(process.stdout, stdout_chunks), kwargs={"is_stdout": True}, daemon=True),
        threading.Thread(target=drain_stream, args=(process.stderr, stderr_chunks), kwargs={"is_stdout": False}, daemon=True),
    ]
    for reader in readers:
        reader.start()

    timeout_error: subprocess.TimeoutExpired | None = None
    last_heartbeat_at = started_at
    deadline = started_at + CODEX_RUN_TIMEOUT_SECONDS
    while process.poll() is None:
        now = time.monotonic()
        if now >= deadline:
            timeout_error = subprocess.TimeoutExpired(command, CODEX_RUN_TIMEOUT_SECONDS)
            _progress_log(run_label, f"timed out after {CODEX_RUN_TIMEOUT_SECONDS}s; terminating child process")
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            break
        with activity_lock:
            idle_seconds = now - last_activity_at
            last_summary_snapshot = last_summary
        if (
            idle_seconds >= CODEX_PROGRESS_HEARTBEAT_SECONDS
            and now - last_heartbeat_at >= CODEX_PROGRESS_HEARTBEAT_SECONDS
        ):
            elapsed = round(now - started_at, 1)
            _progress_log(run_label, f"still running after {elapsed}s; last event: {last_summary_snapshot}")
            last_heartbeat_at = now
        time.sleep(0.25)
    if process.returncode is None:
        process.wait()
    for reader in readers:
        reader.join(timeout=1)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    return (
        subprocess.CompletedProcess(
            command,
            process.returncode if process.returncode is not None else 124,
            stdout,
            stderr,
        ),
        timeout_error,
    )


def _telemetry_summary(records: list[dict[str, Any]], *, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if records:
        tool_counts: dict[str, int] = {}
        for record in records:
            surface = str(record.get("surface") or "").strip()
            if surface:
                tool_counts[surface] = tool_counts.get(surface, 0) + 1
        return {
            "record_count": len(records),
            "surface_count": len(tool_counts),
            "surfaces": sorted(tool_counts),
            "payload_bytes_total": sum(int(record.get("payload_bytes") or 0) for record in records),
            "all_within_ceiling": all(bool(record.get("within_ceiling")) for record in records),
            "tool_invocation_counts": tool_counts,
            "source": "benchmark-log",
        }
    tool_counts: dict[str, int] = {}
    for event in events or []:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
            continue
        tool_name = str(item.get("tool") or "").strip()
        if not tool_name:
            continue
        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
    return {
        "record_count": sum(tool_counts.values()),
        "surface_count": len(tool_counts),
        "surfaces": sorted(tool_counts),
        "payload_bytes_total": 0,
        "all_within_ceiling": True,
        "tool_invocation_counts": tool_counts,
        "source": "codex-events-fallback" if tool_counts else "none",
    }


def _codex_available() -> bool:
    return shutil.which("codex") is not None


def _codex_error_message(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("type") == "error" and isinstance(event.get("message"), str):
            return event["message"].strip()
        if event.get("type") == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"].strip()
    return None


def _condition_uses_runtime_prewarm(condition: str) -> bool:
    return condition in {RUNTIME_WARM_CONDITION, BOOTSTRAP_ASSISTED_CONDITION}


def _condition_uses_bootstrap(condition: str) -> bool:
    return condition == BOOTSTRAP_ASSISTED_CONDITION


def _condition_mounts_plugin_runtime(condition: str) -> bool:
    return condition in {RUNTIME_WARM_CONDITION, BOOTSTRAP_ASSISTED_CONDITION}


def _build_codex_command(
    *,
    condition: str,
    state_root: str | None,
    run_root: Path,
    plugin_runtime_root: Path | None,
    schema_path: Path,
    output_path: Path,
    benchmark_prompt: str,
    bootstrap_path: Path | None,
) -> list[str]:
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
    ]
    if state_root:
        command.extend(["--add-dir", state_root])
    command.extend(
        [
            "--add-dir",
            str(run_root),
        ]
    )
    if _condition_mounts_plugin_runtime(condition):
        if plugin_runtime_root is None:
            raise AssertionError("Warm benchmark run is missing the installed plugin runtime root.")
        command.extend(["--add-dir", str(plugin_runtime_root)])
    if _condition_uses_bootstrap(condition):
        if bootstrap_path is None:
            raise AssertionError("Bootstrap-assisted benchmark run is missing external bootstrap path.")
        command.extend(["-c", f"model_instructions_file={json.dumps(str(bootstrap_path))}"])
    command.extend(
        [
            "--output-schema",
            str(schema_path),
            "--json",
            "-o",
            str(output_path),
            benchmark_prompt,
        ]
    )
    return command


def _run_condition(
    run_root: Path,
    source_plugin_root: Path,
    condition: str,
    replica_index: int,
    task: BenchmarkTask,
) -> dict[str, Any]:
    run_label = f"replica-{replica_index:02d}-{condition}-{task.task_id}"
    fixture = fixture_definition(BENCHMARK_FIXTURE_ID)
    _progress_log(run_label, f"copying fixture workspace {fixture['fixture_id']} for {task.task_id}")
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = create_fixture_repo(run_root, source_plugin_root, fixture)
    agents_paths = _agents_files_in_tree(workspace)
    if agents_paths:
        raise AssertionError(f"Benchmark fixture clone must not contain AGENTS.md: {agents_paths}")
    schema_path = run_root / "output-schema.json"
    output_path = run_root / "codex-last-message.json"
    raw_jsonl_path = run_root / "codex-events.jsonl"
    stderr_path = run_root / "codex-stderr.log"
    benchmark_log_path = run_root / "benchmark-log.jsonl"
    runtime_setup = _prepare_benchmark_runtime_env(
        run_root,
        source_plugin_root,
        condition,
        benchmark_log_path=benchmark_log_path,
    )
    env = dict(runtime_setup["env"])
    env["OTEL_SDK_DISABLED"] = "true"
    env["OTEL_TRACES_EXPORTER"] = "none"
    env["OTEL_METRICS_EXPORTER"] = "none"
    env["OTEL_LOGS_EXPORTER"] = "none"
    plugin_runtime_root = (
        Path(str(runtime_setup["plugin_runtime_root"])).resolve()
        if runtime_setup.get("plugin_runtime_root")
        else None
    )
    write_json_file(schema_path, _schema_payload())
    prep_summary: dict[str, Any] = {
        "task_id": task.task_id,
        "task_title": task.title,
        "benchmark_query": task.benchmark_query,
        "benchmark_prompt_sha256": _prompt_digest(task.benchmark_prompt),
        "condition": condition,
        "replica_index": replica_index,
        "fixture_id": fixture["fixture_id"],
        "shared_prompt": True,
        "user_prompt_transport": "direct benchmark prompt",
        "workspace_agents_paths": agents_paths,
        "product_home": runtime_setup["product_home"],
        "codex_home": runtime_setup["codex_home"],
        "plugin_registration_mode": runtime_setup["plugin_registration_mode"],
        "plugin_runtime_root": str(plugin_runtime_root) if plugin_runtime_root else None,
    }
    if runtime_setup.get("install_result"):
        prep_summary["runtime_install_root"] = runtime_setup["install_result"].get("install_root")
        prep_summary["runtime_marketplace_path"] = runtime_setup["install_result"].get("marketplace_path")
    bootstrap_path: Path | None = None
    with temporary_env(env):
        if _condition_uses_runtime_prewarm(condition):
            _progress_log(run_label, "warming initialized workspace state and context cache through the installed home-local plugin runtime")
            init_workspace(workspace)
            refresh_payload = refresh_context_index(workspace)
            prep_summary.update(
                {
                    "prewarm_steps": [
                        "init-workspace",
                        "refresh-context-index",
                    ],
                    "refresh_status": refresh_payload.get("status"),
                    "refresh_reason": refresh_payload.get("refresh_reason"),
                    "bootstrap": None,
                    "bootstrap_delivery": "none",
                }
            )
            if _condition_uses_bootstrap(condition):
                bootstrap_payload = build_codex_benchmark_bootstrap(
                    workspace,
                    request_text=task.benchmark_query,
                    limit=8,
                    semantic_mode="disabled",
                )
                bootstrap_summary = _write_codex_bootstrap_overlay(run_root, workspace, bootstrap_payload)
                if bootstrap_summary["workspace_agents_file_exists"]:
                    raise AssertionError("Bootstrap-assisted benchmark bootstrap must stay outside the fixture clone tree.")
                if not bootstrap_summary["outside_workspace_tree"]:
                    raise AssertionError("Bootstrap-assisted benchmark bootstrap must stay outside the fixture clone subtree.")
                bootstrap_path = Path(bootstrap_summary["target_path"])
                prep_summary.update(
                    {
                        "prewarm_steps": [
                            "init-workspace",
                            "refresh-context-index",
                            "internal benchmark bootstrap projection",
                        ],
                        "warm_context_pack_cache_status": bootstrap_payload.get("cache_status"),
                        "warm_bootstrap_cache_status": bootstrap_payload.get("bootstrap_cache_status"),
                        "bootstrap": bootstrap_summary,
                        "bootstrap_delivery": "external model_instructions_file",
                    }
                )
        else:
            prep_summary.update(
                {
                    "prewarm_steps": [],
                    "bootstrap": None,
                    "bootstrap_delivery": "none",
                }
            )
    with temporary_env(env):
        usage_before = _usage_metrics(workspace)
    env["AGENTIUX_DEV_BENCHMARK_LOG"] = str(benchmark_log_path)
    command = _build_codex_command(
        condition=condition,
        state_root=env.get("AGENTIUX_DEV_STATE_ROOT"),
        run_root=run_root,
        plugin_runtime_root=plugin_runtime_root,
        schema_path=schema_path,
        output_path=output_path,
        benchmark_prompt=task.benchmark_prompt,
        bootstrap_path=bootstrap_path,
    )
    _progress_log(run_label, "starting codex exec benchmark run")
    started_at = time.monotonic()
    result, timeout_error = _run_codex_exec(command, cwd=workspace, env=env, run_label=run_label)
    elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
    raw_jsonl_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    codex_events = _read_jsonl(raw_jsonl_path)
    command_metrics = _command_metrics(codex_events, workspace=workspace, task=task)
    with temporary_env(env):
        usage_after = _usage_metrics(workspace)
    output_payload: dict[str, Any] | None = None
    schema_valid = False
    if output_path.exists():
        try:
            output_payload = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            output_payload = None
        schema_valid = _validate_output_schema(output_payload)
    records = _read_jsonl(benchmark_log_path)
    telemetry = _telemetry_summary(records, events=codex_events)
    quality = _quality_summary(task, output_payload, workspace)
    status = "completed"
    error = None
    if timeout_error is not None:
        status = "unavailable"
        error = f"codex exec timed out after {CODEX_RUN_TIMEOUT_SECONDS}s"
    elif result.returncode != 0:
        status = "unavailable"
        error = (_codex_error_message(codex_events) or result.stderr or result.stdout or "codex exec failed").strip()
    elif not schema_valid:
        status = "warning"
        error = "Final Codex output did not satisfy the expected JSON schema."
    _progress_log(run_label, f"finished status={status} exit={result.returncode} elapsed_ms={elapsed_ms}")
    return {
        "task_id": task.task_id,
        "task_title": task.title,
        "benchmark_query": task.benchmark_query,
        "benchmark_prompt": task.benchmark_prompt,
        "benchmark_prompt_sha256": _prompt_digest(task.benchmark_prompt),
        "condition": condition,
        "replica_index": replica_index,
        "status": status,
        "error": error,
        "elapsed_ms": elapsed_ms,
        "prewarm_bootstrap_payload_bytes": int((((prep_summary.get("bootstrap") or {}).get("payload_bytes")) or 0)),
        "prewarm_bootstrap_markdown_bytes": int((((prep_summary.get("bootstrap") or {}).get("markdown_bytes")) or 0)),
        "exit_code": result.returncode,
        "schema_valid": schema_valid,
        "final_message_bytes": len(output_path.read_bytes()) if output_path.exists() else 0,
        "usage_before": usage_before,
        "usage_after": usage_after,
        "usage_delta": _usage_delta(usage_before, usage_after),
        "telemetry": telemetry,
        "command_metrics": command_metrics,
        "quality": quality,
        "output": output_payload,
        "prep_summary": prep_summary,
        "artifacts": {
            "workspace": str(workspace),
            "schema": str(schema_path),
            "last_message": str(output_path),
            "events_jsonl": str(raw_jsonl_path),
            "stderr_log": str(stderr_path),
            "benchmark_log": str(benchmark_log_path),
        },
    }


def _median_of(items: list[dict[str, Any]], key: str) -> float:
    return round(float(statistics.median(float(item[key]) for item in items)), 2)


def _median_nested(items: list[dict[str, Any]], outer: str, inner: str) -> float:
    return round(float(statistics.median(float(item[outer][inner]) for item in items)), 2)


def _median_payload(items: list[dict[str, Any]]) -> float:
    return round(float(statistics.median(float(item["telemetry"]["payload_bytes_total"]) for item in items)), 2)


def _prompt_parity_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    task_digests: dict[str, list[str]] = {}
    for run in runs:
        task_digests.setdefault(run["task_id"], []).append(str(run.get("benchmark_prompt_sha256") or ""))
    by_task = {
        task_id: {
            "digest": digests[0] if digests else "",
            "unique_digest_count": len(set(digests)),
            "same_user_prompt": len(set(digests)) == 1,
        }
        for task_id, digests in sorted(task_digests.items())
    }
    return {
        "same_user_prompt_for_all_conditions": all(item["same_user_prompt"] for item in by_task.values()),
        "by_task": by_task,
    }


def _comparison_summary_for_task(task_runs: list[dict[str, Any]]) -> dict[str, Any]:
    task_id = str(task_runs[0]["task_id"]) if task_runs else "unknown"
    completed_runs = [run for run in task_runs if run["status"] in {"completed", "warning"}]
    assisted = [run for run in completed_runs if run["condition"] == ASSISTED_CONDITION]
    raw = [run for run in completed_runs if run["condition"] == RAW_CONDITION]
    planned_assisted_count = sum(1 for run in task_runs if run.get("condition") == ASSISTED_CONDITION)
    planned_raw_count = sum(1 for run in task_runs if run.get("condition") == RAW_CONDITION)
    completed_assisted_count = len(assisted)
    completed_raw_count = len(raw)
    unavailable_run_count = sum(1 for run in task_runs if run.get("status") == "unavailable")
    all_expected_replicas_available = (
        planned_assisted_count > 0
        and planned_raw_count > 0
        and completed_assisted_count == planned_assisted_count
        and completed_raw_count == planned_raw_count
        and unavailable_run_count == 0
    )
    if not assisted or not raw:
        return {
            "task_id": task_id,
            "status": "unavailable",
            "reason": "plugin-assisted or raw benchmark replicas were not available",
            "planned_assisted_count": planned_assisted_count,
            "planned_raw_count": planned_raw_count,
            "completed_assisted_count": completed_assisted_count,
            "completed_raw_count": completed_raw_count,
            "unavailable_run_count": unavailable_run_count,
            "all_expected_replicas_available": all_expected_replicas_available,
            "provider_token_usage": "unavailable",
            "provider_cost": "unavailable",
        }
    assisted_elapsed = _median_of(assisted, "elapsed_ms")
    raw_elapsed = _median_of(raw, "elapsed_ms")
    assisted_payload = _median_payload(assisted)
    assisted_manual_commands = _median_nested(assisted, "command_metrics", "manual_shell_invocation_count")
    raw_manual_commands = _median_nested(raw, "command_metrics", "manual_shell_invocation_count")
    assisted_read_ops = _median_nested(assisted, "command_metrics", "manual_read_operation_count")
    raw_read_ops = _median_nested(raw, "command_metrics", "manual_read_operation_count")
    assisted_rg = _median_nested(assisted, "command_metrics", "rg_count")
    raw_rg = _median_nested(raw, "command_metrics", "rg_count")
    assisted_broad_scans = _median_nested(assisted, "command_metrics", "broad_scan_count")
    raw_broad_scans = _median_nested(raw, "command_metrics", "broad_scan_count")
    assisted_unique_paths = _median_nested(assisted, "command_metrics", "unique_repo_path_count")
    raw_unique_paths = _median_nested(raw, "command_metrics", "unique_repo_path_count")
    assisted_distractor_touches = _median_nested(assisted, "command_metrics", "distractor_path_touch_count")
    raw_distractor_touches = _median_nested(raw, "command_metrics", "distractor_path_touch_count")
    assisted_file_recall = _median_nested(assisted, "quality", "core_file_recall")
    raw_file_recall = _median_nested(raw, "quality", "core_file_recall")
    assisted_file_precision = _median_nested(assisted, "quality", "valid_file_precision")
    raw_file_precision = _median_nested(raw, "quality", "valid_file_precision")
    assisted_primary_rank = _median_nested(assisted, "quality", "primary_file_rank")
    raw_primary_rank = _median_nested(raw, "quality", "primary_file_rank")
    assisted_command_recall = _median_nested(assisted, "quality", "command_recall")
    raw_command_recall = _median_nested(raw, "quality", "command_recall")
    assisted_command_precision = _median_nested(assisted, "quality", "command_precision")
    raw_command_precision = _median_nested(raw, "quality", "command_precision")
    assisted_unexpected_files = _median_nested(assisted, "quality", "unexpected_file_count")
    raw_unexpected_files = _median_nested(raw, "quality", "unexpected_file_count")
    assisted_unexpected_commands = _median_nested(assisted, "quality", "unexpected_command_count")
    raw_unexpected_commands = _median_nested(raw, "quality", "unexpected_command_count")
    assisted_minimality_penalty = _median_nested(assisted, "quality", "total_minimality_penalty")
    raw_minimality_penalty = _median_nested(raw, "quality", "total_minimality_penalty")
    assisted_distractor_hits = _median_nested(assisted, "quality", "distractor_file_hit_count")
    raw_distractor_hits = _median_nested(raw, "quality", "distractor_file_hit_count")
    raw_helper_leakage = _median_nested(raw, "command_metrics", "plugin_helper_command_count")
    assisted_within_ceiling = all(item["telemetry"]["all_within_ceiling"] for item in assisted)
    assisted_prewarm_bootstrap_payload = _median_of(assisted, "prewarm_bootstrap_payload_bytes")
    bootstrap_outside_clone = all(
        (
            bool((((item.get("prep_summary") or {}).get("bootstrap") or {}).get("outside_workspace_tree")))
            and not bool((((item.get("prep_summary") or {}).get("bootstrap") or {}).get("workspace_agents_file_exists")))
        )
        for item in assisted
    )
    wall_clock_not_worse = assisted_elapsed <= raw_elapsed
    manual_command_not_worse = assisted_manual_commands <= raw_manual_commands
    read_ops_not_worse = assisted_read_ops <= raw_read_ops
    rg_not_worse = assisted_rg <= raw_rg
    broad_scan_not_worse = assisted_broad_scans <= raw_broad_scans
    unique_paths_not_worse = assisted_unique_paths <= raw_unique_paths
    distractor_touch_not_worse = assisted_distractor_touches <= raw_distractor_touches
    file_recall_not_worse = assisted_file_recall >= raw_file_recall
    file_precision_not_worse = assisted_file_precision >= raw_file_precision
    primary_rank_not_worse = assisted_primary_rank <= raw_primary_rank
    command_recall_not_worse = assisted_command_recall >= raw_command_recall
    command_precision_not_worse = assisted_command_precision >= raw_command_precision
    unexpected_files_not_worse = assisted_unexpected_files <= raw_unexpected_files
    unexpected_commands_not_worse = assisted_unexpected_commands <= raw_unexpected_commands
    minimality_not_worse = assisted_minimality_penalty <= raw_minimality_penalty
    distractor_hits_not_worse = assisted_distractor_hits <= raw_distractor_hits
    raw_baseline_clean = raw_helper_leakage == 0
    wall_clock_improvement_pct = round(((raw_elapsed - assisted_elapsed) / raw_elapsed) * 100, 2) if raw_elapsed else 0.0
    manual_command_reduction_pct = (
        round(((raw_manual_commands - assisted_manual_commands) / raw_manual_commands) * 100, 2)
        if raw_manual_commands
        else 0.0
    )
    read_operation_reduction_pct = (
        round(((raw_read_ops - assisted_read_ops) / raw_read_ops) * 100, 2)
        if raw_read_ops
        else 0.0
    )
    rg_reduction_pct = round(((raw_rg - assisted_rg) / raw_rg) * 100, 2) if raw_rg else 0.0
    broad_scan_reduction_pct = (
        round(((raw_broad_scans - assisted_broad_scans) / raw_broad_scans) * 100, 2)
        if raw_broad_scans
        else 0.0
    )
    significant_win = any(
        (
            wall_clock_improvement_pct >= 10.0,
            manual_command_reduction_pct >= 20.0,
            read_operation_reduction_pct >= 20.0,
            rg_reduction_pct >= 25.0,
            broad_scan_reduction_pct >= 25.0,
        )
    )
    base_constraints_met = all(
        [
            wall_clock_not_worse,
            manual_command_not_worse,
            read_ops_not_worse,
            rg_not_worse,
            broad_scan_not_worse,
            unique_paths_not_worse,
            distractor_touch_not_worse,
            file_recall_not_worse,
            file_precision_not_worse,
            primary_rank_not_worse,
            command_recall_not_worse,
            command_precision_not_worse,
            unexpected_files_not_worse,
            unexpected_commands_not_worse,
            minimality_not_worse,
            distractor_hits_not_worse,
            raw_baseline_clean,
            assisted_within_ceiling,
            bootstrap_outside_clone,
            all_expected_replicas_available,
        ]
    )
    status = "ok" if base_constraints_met and significant_win else "warning"
    return {
        "comparison_kind": "bootstrap-assisted-vs-raw",
        "task_id": task_id,
        "status": status,
        "base_constraints_met": base_constraints_met,
        "significant_win": significant_win,
        "assisted_median_wall_clock_ms": assisted_elapsed,
        "raw_median_wall_clock_ms": raw_elapsed,
        "wall_clock_not_worse": wall_clock_not_worse,
        "wall_clock_improvement_pct": wall_clock_improvement_pct,
        "assisted_median_manual_command_count": assisted_manual_commands,
        "assisted_median_manual_shell_invocation_count": assisted_manual_commands,
        "assisted_median_manual_read_operation_count": assisted_read_ops,
        "raw_median_manual_command_count": raw_manual_commands,
        "raw_median_manual_shell_invocation_count": raw_manual_commands,
        "raw_median_manual_read_operation_count": raw_read_ops,
        "manual_command_not_worse": manual_command_not_worse,
        "manual_command_reduction_pct": manual_command_reduction_pct,
        "read_operation_not_worse": read_ops_not_worse,
        "read_operation_reduction_pct": read_operation_reduction_pct,
        "assisted_median_rg_count": assisted_rg,
        "raw_median_rg_count": raw_rg,
        "rg_not_worse": rg_not_worse,
        "rg_reduction_pct": rg_reduction_pct,
        "assisted_median_broad_scan_count": assisted_broad_scans,
        "raw_median_broad_scan_count": raw_broad_scans,
        "broad_scan_not_worse": broad_scan_not_worse,
        "broad_scan_reduction_pct": broad_scan_reduction_pct,
        "assisted_median_unique_repo_path_count": assisted_unique_paths,
        "raw_median_unique_repo_path_count": raw_unique_paths,
        "unique_repo_path_count_not_worse": unique_paths_not_worse,
        "assisted_median_distractor_path_touch_count": assisted_distractor_touches,
        "raw_median_distractor_path_touch_count": raw_distractor_touches,
        "distractor_path_touch_count_not_worse": distractor_touch_not_worse,
        "assisted_median_core_file_recall": assisted_file_recall,
        "raw_median_core_file_recall": raw_file_recall,
        "core_file_recall_not_worse": file_recall_not_worse,
        "assisted_median_valid_file_precision": assisted_file_precision,
        "raw_median_valid_file_precision": raw_file_precision,
        "valid_file_precision_not_worse": file_precision_not_worse,
        "assisted_median_primary_file_rank": assisted_primary_rank,
        "raw_median_primary_file_rank": raw_primary_rank,
        "primary_file_rank_not_worse": primary_rank_not_worse,
        "assisted_median_command_recall": assisted_command_recall,
        "raw_median_command_recall": raw_command_recall,
        "command_recall_not_worse": command_recall_not_worse,
        "assisted_median_command_precision": assisted_command_precision,
        "raw_median_command_precision": raw_command_precision,
        "command_precision_not_worse": command_precision_not_worse,
        "assisted_median_unexpected_file_count": assisted_unexpected_files,
        "raw_median_unexpected_file_count": raw_unexpected_files,
        "unexpected_file_count_not_worse": unexpected_files_not_worse,
        "assisted_median_unexpected_command_count": assisted_unexpected_commands,
        "raw_median_unexpected_command_count": raw_unexpected_commands,
        "unexpected_command_count_not_worse": unexpected_commands_not_worse,
        "assisted_median_total_minimality_penalty": assisted_minimality_penalty,
        "raw_median_total_minimality_penalty": raw_minimality_penalty,
        "total_minimality_penalty_not_worse": minimality_not_worse,
        "assisted_median_distractor_file_hit_count": assisted_distractor_hits,
        "raw_median_distractor_file_hit_count": raw_distractor_hits,
        "distractor_file_hit_count_not_worse": distractor_hits_not_worse,
        "raw_median_plugin_helper_command_count": raw_helper_leakage,
        "raw_median_plugin_helper_invocation_count": raw_helper_leakage,
        "raw_baseline_clean": raw_baseline_clean,
        "assisted_median_payload_bytes": assisted_payload,
        "assisted_median_prewarm_bootstrap_payload_bytes": assisted_prewarm_bootstrap_payload,
        "assisted_all_within_ceiling": assisted_within_ceiling,
        "bootstrap_outside_clone": bootstrap_outside_clone,
        "planned_assisted_count": planned_assisted_count,
        "planned_raw_count": planned_raw_count,
        "completed_assisted_count": completed_assisted_count,
        "completed_raw_count": completed_raw_count,
        "unavailable_run_count": unavailable_run_count,
        "all_expected_replicas_available": all_expected_replicas_available,
        "provider_token_usage": "unavailable",
        "provider_cost": "unavailable",
    }


def _comparison_summary(runs: list[dict[str, Any]], tasks: tuple[BenchmarkTask, ...]) -> dict[str, Any]:
    per_task = {
        task.task_id: _comparison_summary_for_task(
            [run for run in runs if run.get("task_id") == task.task_id]
        )
        for task in tasks
    }
    aggregate = _comparison_summary_for_task(runs)
    prompt_parity = _prompt_parity_summary(runs)
    aggregate["all_per_task_status_ok"] = all(item.get("status") == "ok" for item in per_task.values())
    aggregate["task_id"] = "all-tasks"
    aggregate["per_task"] = per_task
    aggregate["prompt_parity"] = prompt_parity
    aggregate["prompt_parity_guard"] = bool(prompt_parity["same_user_prompt_for_all_conditions"])
    if not aggregate["prompt_parity_guard"] or not aggregate["all_per_task_status_ok"]:
        aggregate["status"] = "warning"
        aggregate["base_constraints_met"] = False
    return aggregate


def _single_mode_summary_for_task(task_runs: list[dict[str, Any]], *, condition: str) -> dict[str, Any]:
    task_id = str(task_runs[0]["task_id"]) if task_runs else "unknown"
    planned_run_count = sum(1 for run in task_runs if run.get("condition") == condition)
    completed_runs = [
        run
        for run in task_runs
        if run.get("condition") == condition and run["status"] in {"completed", "warning"}
    ]
    completed_run_count = len(completed_runs)
    unavailable_run_count = sum(
        1 for run in task_runs if run.get("condition") == condition and run.get("status") == "unavailable"
    )
    all_expected_runs_available = (
        planned_run_count > 0
        and completed_run_count == planned_run_count
        and unavailable_run_count == 0
    )
    if not completed_runs:
        return {
            "task_id": task_id,
            "mode": condition,
            "status": "unavailable",
            "reason": f"{condition} benchmark runs were not available",
            "planned_run_count": planned_run_count,
            "completed_run_count": completed_run_count,
            "unavailable_run_count": unavailable_run_count,
            "all_expected_runs_available": all_expected_runs_available,
            "provider_token_usage": "unavailable",
            "provider_cost": "unavailable",
        }

    aggregate_status = "completed"
    if not all_expected_runs_available or any(run.get("status") != "completed" for run in task_runs if run.get("condition") == condition):
        aggregate_status = "warning"

    return {
        "task_id": task_id,
        "mode": condition,
        "status": aggregate_status,
        "planned_run_count": planned_run_count,
        "completed_run_count": completed_run_count,
        "unavailable_run_count": unavailable_run_count,
        "all_expected_runs_available": all_expected_runs_available,
        "median_wall_clock_ms": _median_of(completed_runs, "elapsed_ms"),
        "median_payload_bytes": _median_payload(completed_runs),
        "median_manual_command_count": _median_nested(completed_runs, "command_metrics", "manual_command_count"),
        "median_manual_shell_invocation_count": _median_nested(completed_runs, "command_metrics", "manual_shell_invocation_count"),
        "median_manual_read_operation_count": _median_nested(completed_runs, "command_metrics", "manual_read_operation_count"),
        "median_plugin_helper_invocation_count": _median_nested(completed_runs, "command_metrics", "plugin_helper_invocation_count"),
        "median_rg_count": _median_nested(completed_runs, "command_metrics", "rg_count"),
        "median_broad_scan_count": _median_nested(completed_runs, "command_metrics", "broad_scan_count"),
        "median_unique_repo_path_count": _median_nested(completed_runs, "command_metrics", "unique_repo_path_count"),
        "median_distractor_path_touch_count": _median_nested(completed_runs, "command_metrics", "distractor_path_touch_count"),
        "median_core_file_recall": _median_nested(completed_runs, "quality", "core_file_recall"),
        "median_valid_file_precision": _median_nested(completed_runs, "quality", "valid_file_precision"),
        "median_primary_file_rank": _median_nested(completed_runs, "quality", "primary_file_rank"),
        "median_command_recall": _median_nested(completed_runs, "quality", "command_recall"),
        "median_command_precision": _median_nested(completed_runs, "quality", "command_precision"),
        "median_unexpected_file_count": _median_nested(completed_runs, "quality", "unexpected_file_count"),
        "median_unexpected_command_count": _median_nested(completed_runs, "quality", "unexpected_command_count"),
        "median_total_minimality_penalty": _median_nested(completed_runs, "quality", "total_minimality_penalty"),
        "median_distractor_file_hit_count": _median_nested(completed_runs, "quality", "distractor_file_hit_count"),
        "all_within_ceiling": all(item["telemetry"]["all_within_ceiling"] for item in completed_runs),
        "median_prewarm_bootstrap_payload_bytes": _median_of(completed_runs, "prewarm_bootstrap_payload_bytes"),
        "bootstrap_delivery": str(((completed_runs[0].get("prep_summary") or {}).get("bootstrap_delivery")) or "none"),
        "provider_token_usage": "unavailable",
        "provider_cost": "unavailable",
    }


def _single_mode_summary(runs: list[dict[str, Any]], tasks: tuple[BenchmarkTask, ...], *, condition: str) -> dict[str, Any]:
    per_task = {
        task.task_id: _single_mode_summary_for_task(
            [run for run in runs if run.get("task_id") == task.task_id],
            condition=condition,
        )
        for task in tasks
    }
    aggregate = _single_mode_summary_for_task(runs, condition=condition)
    aggregate["task_id"] = "all-tasks"
    aggregate["per_task"] = per_task
    aggregate["all_per_task_status_completed"] = all(item.get("status") == "completed" for item in per_task.values())
    if not aggregate["all_per_task_status_completed"]:
        aggregate["status"] = "warning"
    return aggregate


def _benchmark_mode_descriptions() -> dict[str, str]:
    return {
        RAW_CONDITION: "fresh fixture clone only; no plugin prewarm, no plugin registration in benchmark CODEX_HOME, no installed plugin runtime mount, no external bootstrap",
        RUNTIME_WARM_CONDITION: "fresh fixture clone with init-workspace + refresh-context-index before timed codex exec; the benchmark seeds an isolated home-local installed plugin copy and product-like registration path, but no external model_instructions_file is provided",
        BOOTSTRAP_ASSISTED_CONDITION: "runtime-warm plus external benchmark bootstrap projection passed through model_instructions_file",
    }


def _benchmark_group(context: ExecutionContext) -> dict[str, Any]:
    tasks = _selected_benchmark_tasks()
    group_root = context.path("codex-cli-ab-evidence")
    if not _codex_available():
        result = {
            "status": "unavailable",
            "reason": "codex CLI is not installed in the current environment",
            "runs": [],
            "comparison": {
                "status": "unavailable",
                "provider_token_usage": "unavailable",
                "provider_cost": "unavailable",
            },
        }
        write_json_file(group_root / "summary.json", result)
        return result
    run_plan = BOOTSTRAP_AB_RUN_PLAN
    task_order = [task.task_id for task in tasks]
    _progress_log(
        GROUP_KEY,
        f"starting sequential benchmark plan: conditions={', '.join(run_plan)} tasks={', '.join(task_order)}",
    )
    runs = []
    for index, condition in enumerate(run_plan, start=1):
        for task in tasks:
            run_root = group_root / f"replica-{index:02d}-{condition}" / task.task_id
            runs.append(_run_condition(run_root, context.plugin_root, condition, index, task))
    result = {
        "status": "completed",
        "scenario": {
            "fixture_id": BENCHMARK_FIXTURE_ID,
            "selected_task_ids": task_order,
            "benchmark_tasks": [
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "scenario": task.scenario,
                    "benchmark_query": task.benchmark_query,
                    "benchmark_prompt": task.benchmark_prompt,
                    "benchmark_prompt_sha256": _prompt_digest(task.benchmark_prompt),
                    "expected_core_files": list(task.expected_core_files),
                    "expected_optional_files": list(task.expected_optional_files),
                    "expected_primary_files": list(task.expected_primary_files or task.expected_core_files),
                    "expected_command_patterns": list(task.expected_command_patterns),
                    "expected_command_examples": list(task.expected_command_examples),
                    "distractor_files": list(task.distractor_files),
                    "max_candidate_files": task.max_candidate_files,
                    "max_candidate_commands": task.max_candidate_commands,
                }
                for task in tasks
            ],
            "prompt_policy": (
                "same benchmark prompt for bootstrap-assisted and raw within each task; "
                "bootstrap-assisted differs from runtime-warm only by an internal benchmark bootstrap projection passed through "
                "`-c model_instructions_file=<run_root>/codex-bootstrap.md`, after warm init-workspace + "
                "refresh-context-index + show-workspace-context-pack-based projection through an installed home-local plugin runtime; "
                "raw runs stay cold with the plugin disabled in benchmark CODEX_HOME."
            ),
            "benchmark_modes": _benchmark_mode_descriptions(),
            "assisted_bootstrap_delivery": "external model_instructions_file",
            "raw_bootstrap_delivery": "none",
            "workspace_agents_policy": "no AGENTS.md in fixture templates, no AGENTS.md inside temp fixture clones",
            "prewarm_accounting": "bootstrap-assisted workspace init/index/bootstrap runs before the measured codex exec wall clock",
            "run_plan": run_plan,
        },
        "runs": runs,
        "comparison": _comparison_summary(runs, tasks),
    }
    write_json_file(group_root / "summary.json", result)
    _progress_log(GROUP_KEY, f"benchmark group finished with comparison_status={result['comparison']['status']}")
    return result


def _runtime_warm_group(context: ExecutionContext) -> dict[str, Any]:
    tasks = _selected_benchmark_tasks()
    group_root = context.path(RUNTIME_WARM_GROUP_KEY)
    if not _codex_available():
        result = {
            "status": "unavailable",
            "reason": "codex CLI is not installed in the current environment",
            "runs": [],
            "mode_summary": {
                "status": "unavailable",
                "provider_token_usage": "unavailable",
                "provider_cost": "unavailable",
            },
        }
        write_json_file(group_root / "summary.json", result)
        return result
    run_plan = [RUNTIME_WARM_CONDITION]
    task_order = [task.task_id for task in tasks]
    _progress_log(
        RUNTIME_WARM_GROUP_KEY,
        f"starting runtime-warm benchmark plan: conditions={', '.join(run_plan)} tasks={', '.join(task_order)}",
    )
    runs = []
    for index, condition in enumerate(run_plan, start=1):
        for task in tasks:
            run_root = group_root / f"replica-{index:02d}-{condition}" / task.task_id
            runs.append(_run_condition(run_root, context.plugin_root, condition, index, task))
    result = {
        "status": "completed",
        "scenario": {
            "fixture_id": BENCHMARK_FIXTURE_ID,
            "selected_task_ids": task_order,
            "benchmark_tasks": [
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "scenario": task.scenario,
                    "benchmark_query": task.benchmark_query,
                    "benchmark_prompt": task.benchmark_prompt,
                    "benchmark_prompt_sha256": _prompt_digest(task.benchmark_prompt),
                    "expected_core_files": list(task.expected_core_files),
                    "expected_optional_files": list(task.expected_optional_files),
                    "expected_primary_files": list(task.expected_primary_files or task.expected_core_files),
                    "expected_command_patterns": list(task.expected_command_patterns),
                    "expected_command_examples": list(task.expected_command_examples),
                    "distractor_files": list(task.distractor_files),
                    "max_candidate_files": task.max_candidate_files,
                    "max_candidate_commands": task.max_candidate_commands,
                }
                for task in tasks
            ],
            "prompt_policy": (
                "same benchmark prompt for runtime-warm runs within each task; runtime-warm prewarms the plugin "
                "with init-workspace + refresh-context-index before the measured codex exec wall clock, seeds an "
                "isolated home-local install plus benchmark CODEX_HOME and marketplace state, mounts the installed "
                "plugin runtime into the Codex sandbox, and does not pass any external model_instructions_file."
            ),
            "benchmark_modes": _benchmark_mode_descriptions(),
            "selected_mode": RUNTIME_WARM_CONDITION,
            "bootstrap_delivery": "none",
            "workspace_agents_policy": "no AGENTS.md in fixture templates, no AGENTS.md inside temp fixture clones",
            "prewarm_accounting": "runtime-warm workspace init/index runs happen before the measured codex exec wall clock; no benchmark bootstrap is generated",
            "run_plan": run_plan,
        },
        "runs": runs,
        "mode_summary": _single_mode_summary(runs, tasks, condition=RUNTIME_WARM_CONDITION),
    }
    write_json_file(group_root / "summary.json", result)
    _progress_log(
        RUNTIME_WARM_GROUP_KEY,
        f"runtime-warm benchmark group finished with status={result['mode_summary']['status']}",
    )
    return result


def _case_codex_cli_assisted_vs_raw_evidence(context: ExecutionContext) -> dict[str, Any]:
    tasks = _selected_benchmark_tasks()
    group = context.group(GROUP_KEY, _benchmark_group)
    comparison = group["comparison"]
    runs = group["runs"]
    if group["status"] == "unavailable":
        return {
            "status": "unavailable",
            "reason": group["reason"],
            "provider_metrics": {
                "token_usage": "unavailable",
                "cost": "unavailable",
            },
        }
    assert len(runs) == len(tasks) * 4
    expected_conditions = [
        condition
        for condition in BOOTSTRAP_AB_RUN_PLAN
        for _ in tasks
    ]
    assert [run["condition"] for run in runs] == expected_conditions
    for task in tasks:
        task_runs = [run for run in runs if run["task_id"] == task.task_id]
        assert len(task_runs) == 4
        assert len({run["benchmark_prompt_sha256"] for run in task_runs}) == 1
        assisted_runs = [run for run in task_runs if run["condition"] == ASSISTED_CONDITION]
        assert assisted_runs
        assert all(
            not (((run.get("prep_summary") or {}).get("bootstrap") or {}).get("workspace_agents_file_exists"))
            for run in assisted_runs
        )
        assert all(
            ((run.get("prep_summary") or {}).get("bootstrap") or {}).get("outside_workspace_tree") is True
            for run in assisted_runs
        )
    for run in runs:
        if run["status"] == "completed":
            assert run["exit_code"] == 0
            assert run["schema_valid"] is True
    return {
        "status": comparison["status"],
        "scenario": group["scenario"],
        "comparison": comparison,
        "provider_metrics": {
            "token_usage": "unavailable",
            "cost": "unavailable",
        },
        "run_statuses": [run["status"] for run in runs],
    }


def _case_codex_cli_runtime_warm_evidence(context: ExecutionContext) -> dict[str, Any]:
    tasks = _selected_benchmark_tasks()
    group = context.group(RUNTIME_WARM_GROUP_KEY, _runtime_warm_group)
    mode_summary = group["mode_summary"]
    runs = group["runs"]
    if group["status"] == "unavailable":
        return {
            "status": "unavailable",
            "reason": group["reason"],
            "provider_metrics": {
                "token_usage": "unavailable",
                "cost": "unavailable",
            },
        }
    assert len(runs) == len(tasks)
    assert all(run["condition"] == RUNTIME_WARM_CONDITION for run in runs)
    for run in runs:
        prep_summary = run.get("prep_summary") or {}
        assert prep_summary.get("bootstrap_delivery") == "none"
        assert "internal benchmark bootstrap projection" not in (prep_summary.get("prewarm_steps") or [])
        if run["status"] == "completed":
            assert run["exit_code"] == 0
            assert run["schema_valid"] is True
    return {
        "status": mode_summary["status"],
        "scenario": group["scenario"],
        "mode_summary": mode_summary,
        "provider_metrics": {
            "token_usage": "unavailable",
            "cost": "unavailable",
        },
        "run_statuses": [run["status"] for run in runs],
    }


def register() -> dict[str, ScenarioDefinition]:
    cases = [
        ScenarioDefinition(
            "codex-cli-assisted-vs-raw-evidence",
            BENCHMARK_FIXTURE_ID,
            ("codex-cli-ab-evidence",),
            ("benchmark", "codex-cli", "supplementary"),
            _case_codex_cli_assisted_vs_raw_evidence,
        ),
        ScenarioDefinition(
            "codex-cli-runtime-warm-evidence",
            BENCHMARK_FIXTURE_ID,
            ("codex-cli-runtime-warm-evidence",),
            ("benchmark", "codex-cli", "supplementary"),
            _case_codex_cli_runtime_warm_evidence,
        ),
    ]
    return {case.case_id: case for case in cases}
