#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agentiux_dev_analytics import append_analytics_event
from agentiux_dev_auth import resolve_auth_profile_artifact
from agentiux_dev_lib import (
    PLUGIN_VERSION,
    _resolve_verification_fragments,
    _tool_available,
    current_task,
    detect_workspace,
    now_iso,
    plugin_root,
    process_running,
    python_script_command,
    read_stage_register,
    read_workspace_state,
    sanitize_identifier,
    start_logged_process,
    start_logged_python_process,
    stop_process,
    text_result,
    workspace_paths,
)


TERMINAL_RUN_STATUSES = {"passed", "failed", "cancelled"}
RUNNER_TYPES = {
    "browser-layout-audit",
    "playwright-visual",
    "detox-visual",
    "android-compose-screenshot",
    "ios-simulator-capture",
    "shell-contract",
}
VISUAL_RUNNERS = {
    "playwright-visual",
    "detox-visual",
    "android-compose-screenshot",
    "ios-simulator-capture",
}
NATIVE_LAYOUT_AUDIT_RUNNERS = {
    "detox-visual",
    "android-compose-screenshot",
}
LOGCAT_CRASH_PATTERNS = [
    re.compile(r"fatal exception", re.IGNORECASE),
    re.compile(r"androidruntime", re.IGNORECASE),
    re.compile(r"\banr in\b", re.IGNORECASE),
    re.compile(r"\bcrash\b", re.IGNORECASE),
    re.compile(r"abort message:", re.IGNORECASE),
]
SEMANTIC_RESULT_STATUSES = {
    "passed",
    "failed",
    "warning",
    "skipped",
    "not_applicable",
    "unknown",
}
SEMANTIC_ASSERTION_CHECKS = {
    "presence_uniqueness",
    "visibility",
    "scroll_reachability",
    "overflow_clipping",
    "occlusion",
    "interaction_states",
    "computed_styles",
    "layout_relations",
    "text_overflow",
    "accessibility_state",
    "screenshot_baseline",
}
SEMANTIC_LOCATOR_KINDS = {
    "selector",
    "role",
    "test_id",
    "semantics_tag",
    "text",
}
DEFAULT_SEMANTIC_HEURISTICS = [
    "interactive_visibility_scan",
    "interactive_overflow_scan",
    "interactive_occlusion_scan",
]
MANDATORY_WEB_VISUAL_SEMANTIC_CHECKS = [
    "presence_uniqueness",
    "visibility",
    "overflow_clipping",
    "computed_styles",
    "interaction_states",
    "scroll_reachability",
    "occlusion",
]
MANDATORY_NATIVE_VISUAL_SEMANTIC_CHECKS = [
    "presence_uniqueness",
    "visibility",
    "overflow_clipping",
    "computed_styles",
    "layout_relations",
    "occlusion",
]
DEFAULT_NATIVE_LAYOUT_AUDIT_CHECKS = [
    "visibility",
    "overflow_clipping",
    "computed_styles",
    "layout_relations",
    "occlusion",
]
RUNNER_CHECK_SUPPORT = {
    "browser-layout-audit": set(),
    "playwright-visual": set(SEMANTIC_ASSERTION_CHECKS),
    "detox-visual": set(SEMANTIC_ASSERTION_CHECKS),
    "android-compose-screenshot": set(SEMANTIC_ASSERTION_CHECKS),
    "ios-simulator-capture": set(SEMANTIC_ASSERTION_CHECKS),
    "shell-contract": set(),
}
RUNNER_LOCATOR_SUPPORT = {
    "browser-layout-audit": set(),
    "playwright-visual": {"selector", "role", "test_id", "text"},
    "detox-visual": {"test_id", "text"},
    "android-compose-screenshot": {"test_id", "semantics_tag", "text"},
    "ios-simulator-capture": {"test_id", "text"},
    "shell-contract": set(),
}
RUNNER_REQUIRED_HOST_TOOLS = {
    "browser-layout-audit": ["node", "browser-runtime"],
    "playwright-visual": ["node", "browser-runtime"],
    "detox-visual": ["node", "detox"],
    "android-compose-screenshot": ["android", "gradle"],
    "ios-simulator-capture": ["xcode", "simulator"],
}
VERIFICATION_HELPER_RELATIVE_ROOT = Path(".verification") / "helpers"
LEGACY_VERIFICATION_HELPER_RELATIVE_ROOT = Path(".agentiux") / "verification-helpers"
VERIFICATION_HELPER_MARKER_FILENAME = "bundle.json"
LAYOUT_AUDIT_RULE_CATALOG_FILENAME = "layout_audit_rules.json"
_LAYOUT_AUDIT_RULE_CATALOG_CACHE: dict[str, Any] | None = None


def _verification_file_error(path: Path, exc: Exception, purpose: str | None = None) -> ValueError:
    label = purpose or "verification JSON file"
    return ValueError(f"Unable to read {label} at {path}: {exc}")


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_json(path: Path, default: Any | None = None, *, strict: bool = False, purpose: str | None = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open() as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        if strict:
            raise _verification_file_error(path, exc, purpose) from exc
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    with temp_path.open("w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temp_path, path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _tail_lines(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    with path.open() as handle:
        lines = handle.readlines()
    return [line.rstrip("\n") for line in lines[-limit:]]


def _process_running(pid: int | None) -> bool:
    return process_running(pid)


def _ensure_workspace_paths(workspace: str | Path, workstream_id: str | None = None, require_initialized: bool = True) -> dict[str, str]:
    paths = workspace_paths(workspace, workstream_id=workstream_id)
    state_path = Path(paths["workspace_state"])
    if require_initialized and not state_path.exists():
        raise FileNotFoundError(f"Workspace is not initialized in AgentiUX Dev state: {paths['workspace_root']}")
    if not paths["current_workstream_id"] or not paths["verification_dir"] or not paths["verification_runs_dir"]:
        raise FileNotFoundError("No current workstream selected.")
    Path(paths["verification_dir"]).mkdir(parents=True, exist_ok=True)
    Path(paths["verification_runs_dir"]).mkdir(parents=True, exist_ok=True)
    Path(paths["verification_baselines_dir"]).mkdir(parents=True, exist_ok=True)
    return paths


def _verification_helper_version() -> str:
    return PLUGIN_VERSION


def _verification_helper_bundle_root() -> Path:
    return plugin_root() / "bundles" / "verification-helpers" / _verification_helper_version()


def _verification_helper_catalog_path() -> Path:
    return _verification_helper_bundle_root() / "catalog.json"


def _layout_audit_rule_catalog_path() -> Path:
    return plugin_root() / "catalogs" / LAYOUT_AUDIT_RULE_CATALOG_FILENAME


def _load_layout_audit_rule_catalog() -> dict[str, Any]:
    global _LAYOUT_AUDIT_RULE_CATALOG_CACHE
    if _LAYOUT_AUDIT_RULE_CATALOG_CACHE is not None:
        return _LAYOUT_AUDIT_RULE_CATALOG_CACHE
    payload = _load_json(_layout_audit_rule_catalog_path(), strict=True, purpose="layout audit rule catalog")
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        raise ValueError(f"Layout audit rule catalog is invalid: {_layout_audit_rule_catalog_path()}")
    _LAYOUT_AUDIT_RULE_CATALOG_CACHE = payload
    return payload


def _layout_audit_rule(rule_id: str) -> dict[str, Any]:
    for rule in _load_layout_audit_rule_catalog().get("rules") or []:
        if str((rule or {}).get("id") or "").strip() == rule_id:
            return dict(rule or {})
    return {}


def _layout_audit_threshold(rule_id: str, key: str, default: float) -> float:
    rule = _layout_audit_rule(rule_id)
    thresholds = rule.get("thresholds") if isinstance(rule.get("thresholds"), dict) else {}
    value = _coerce_float((thresholds or {}).get(key))
    return default if value is None else float(value)


def _verification_helper_sync_root(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / VERIFICATION_HELPER_RELATIVE_ROOT


def _legacy_verification_helper_sync_root(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / LEGACY_VERIFICATION_HELPER_RELATIVE_ROOT


def _verification_helper_version_root(workspace: str | Path) -> Path:
    return _verification_helper_sync_root(workspace)


def _verification_helper_marker_path(workspace: str | Path) -> Path:
    return _verification_helper_sync_root(workspace) / VERIFICATION_HELPER_MARKER_FILENAME


def _load_verification_helper_catalog() -> dict[str, Any]:
    payload = _load_json(_verification_helper_catalog_path(), strict=True, purpose="verification helper catalog")
    if not isinstance(payload, dict) or not payload.get("runners"):
        raise ValueError(f"Verification helper catalog is invalid: {_verification_helper_catalog_path()}")
    return payload


def _materialized_helper_entrypoints(catalog: dict[str, Any] | None = None) -> list[str]:
    payload = catalog if catalog is not None else _load_verification_helper_catalog()
    entrypoints = {"core/index.js"}
    for runner in (payload.get("runners") or {}).values():
        primary = str(runner.get("entrypoint") or "").strip()
        if primary:
            entrypoints.add(primary)
        for item in runner.get("entrypoints") or []:
            entrypoint_path = str((item or {}).get("path") or "").strip()
            if entrypoint_path:
                entrypoints.add(entrypoint_path)
    return sorted(entrypoints)


def _legacy_materialized_helper_versions(workspace: str | Path) -> list[str]:
    root = _legacy_verification_helper_sync_root(workspace)
    if not root.exists():
        return []
    return sorted(candidate.name for candidate in root.iterdir() if candidate.is_dir())


def _verification_helper_materialization_status(workspace: str | Path) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    sync_root = _verification_helper_sync_root(resolved_workspace)
    version_root = _verification_helper_version_root(resolved_workspace)
    marker_path = _verification_helper_marker_path(resolved_workspace)
    marker_payload = _load_json(marker_path, default={}) or {}
    marker_version = marker_payload.get("bundle_version") or marker_payload.get("version")
    catalog_error = None
    try:
        expected_entrypoints = _materialized_helper_entrypoints()
    except Exception as exc:  # noqa: BLE001
        expected_entrypoints = []
        catalog_error = str(exc)
    missing_entrypoints = [
        entrypoint
        for entrypoint in expected_entrypoints
        if not (version_root / entrypoint).exists()
    ]
    legacy_versions = _legacy_materialized_helper_versions(resolved_workspace)
    legacy_root = _legacy_verification_helper_sync_root(resolved_workspace)
    legacy_exists = legacy_root.exists()
    if version_root.exists() and marker_version == _verification_helper_version() and not missing_entrypoints:
        status = "synced"
    elif version_root.exists():
        status = "version_drift"
    elif legacy_exists:
        status = "legacy_location"
    else:
        status = "not_synced"
    return {
        "workspace_path": str(resolved_workspace),
        "bundle_version": _verification_helper_version(),
        "sync_root": str(sync_root),
        "current_version_root": str(version_root),
        "current_marker_path": str(marker_path),
        "available_versions": [str(marker_version)] if marker_version else [],
        "current_marker_version": marker_version,
        "marker_payload": copy.deepcopy(marker_payload),
        "legacy_sync_root": str(legacy_root),
        "legacy_versions": legacy_versions,
        "legacy_detected": legacy_exists,
        "missing_entrypoints": missing_entrypoints,
        "catalog_error": catalog_error,
        "status": status,
        "synced": status == "synced",
    }


def _runner_catalog_entry(runner: str, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = catalog if catalog is not None else _load_verification_helper_catalog()
    runner_entry = (payload.get("runners") or {}).get(runner) or {}
    return copy.deepcopy(runner_entry) if isinstance(runner_entry, dict) else {}


def _runner_supported_checks(runner: str, catalog: dict[str, Any] | None = None) -> set[str]:
    runner_entry = _runner_catalog_entry(runner, catalog)
    declared = runner_entry.get("supported_checks") or runner_entry.get("check_families") or []
    if declared:
        return {str(item).strip().lower() for item in declared if str(item or "").strip()}
    return set(RUNNER_CHECK_SUPPORT.get(runner, set()))


def _runner_supported_locator_kinds(runner: str, catalog: dict[str, Any] | None = None) -> set[str]:
    runner_entry = _runner_catalog_entry(runner, catalog)
    declared = runner_entry.get("supported_locator_kinds") or runner_entry.get("locator_kinds") or []
    if declared:
        return {str(item).strip().lower() for item in declared if str(item or "").strip()}
    return set(RUNNER_LOCATOR_SUPPORT.get(runner, set()))


def _runner_required_host_tools(runner: str, catalog: dict[str, Any] | None = None) -> list[str]:
    runner_entry = _runner_catalog_entry(runner, catalog)
    declared = runner_entry.get("required_host_tools") or runner_entry.get("required_tools") or []
    if declared:
        return [str(item) for item in declared if str(item or "").strip()]
    return list(RUNNER_REQUIRED_HOST_TOOLS.get(runner, []))


def _runner_capability_matrix(runner: str, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    runner_entry = _runner_catalog_entry(runner, catalog)
    return {
        "runner": runner,
        "supported_checks": sorted(_runner_supported_checks(runner, catalog)),
        "supported_locator_kinds": sorted(_runner_supported_locator_kinds(runner, catalog)),
        "required_host_tools": _runner_required_host_tools(runner, catalog),
        "supports_auto_scan": bool(runner_entry.get("supports_auto_scan", runner in VISUAL_RUNNERS)),
        "supports_platform_hooks": bool(runner_entry.get("supports_platform_hooks", runner in VISUAL_RUNNERS)),
        "entrypoint": runner_entry.get("entrypoint"),
        "entrypoints": copy.deepcopy(runner_entry.get("entrypoints") or []),
    }


def _render_import_examples(
    workspace: str | Path,
    entrypoint: str | Path,
    runner_entry: dict[str, Any],
) -> list[str]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    version_root = _verification_helper_version_root(resolved_workspace)
    relative_helper_root = VERIFICATION_HELPER_RELATIVE_ROOT
    entrypoint_path = Path(str(entrypoint))
    relative_entrypoint = (relative_helper_root / entrypoint_path).as_posix()
    absolute_entrypoint = str((version_root / entrypoint_path).resolve())
    templates = list(runner_entry.get("import_examples") or [])
    if not templates:
        export_name = str(runner_entry.get("export_name") or "runSemanticChecks")
        language = str(runner_entry.get("language") or "").lower()
        if language == "kotlin":
            package_name = str(runner_entry.get("package") or "generated.verification.helpers")
            templates = [f"import {package_name}.{export_name}"]
        else:
            templates = [f"import {{ {export_name} }} from './{relative_entrypoint}';"]
    rendered: list[str] = []
    for template in templates:
        snippet = str(template)
        snippet = snippet.replace("__HELPER_VERSION__", _verification_helper_version())
        snippet = snippet.replace("__RELATIVE_VERSION_ROOT__", relative_helper_root.as_posix())
        snippet = snippet.replace("__ABSOLUTE_VERSION_ROOT__", str(version_root))
        snippet = snippet.replace("__RELATIVE_HELPER_ROOT__", relative_helper_root.as_posix())
        snippet = snippet.replace("__ABSOLUTE_HELPER_ROOT__", str(version_root))
        snippet = snippet.replace("__RELATIVE_ENTRYPOINT__", relative_entrypoint)
        snippet = snippet.replace("__ABSOLUTE_ENTRYPOINT__", absolute_entrypoint)
        rendered.append(snippet)
    return rendered


def _verification_helper_import_snippets(workspace: str | Path, catalog: dict[str, Any]) -> dict[str, Any]:
    snippets: dict[str, Any] = {}
    for runner_id, runner in (catalog.get("runners") or {}).items():
        entrypoint = Path(str(runner.get("entrypoint") or ""))
        relative_path = (VERIFICATION_HELPER_RELATIVE_ROOT / entrypoint).as_posix()
        snippets[runner_id] = {
            "relative_path": relative_path,
            "absolute_path": str((Path(workspace).expanduser().resolve() / relative_path).resolve()),
            "import_examples": _render_import_examples(workspace, entrypoint, runner),
        }
    return snippets


def show_verification_helper_catalog(workspace: str | Path) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    catalog = copy.deepcopy(_load_verification_helper_catalog())
    materialization = _verification_helper_materialization_status(resolved_workspace)
    runners: dict[str, Any] = {}
    for runner_id, runner in (catalog.get("runners") or {}).items():
        runners[runner_id] = {
            **runner,
            "capability_matrix": _runner_capability_matrix(runner_id, catalog),
            "version_status": materialization.get("status"),
        }
    return {
        "workspace_path": str(resolved_workspace),
        "bundle_version": _verification_helper_version(),
        "bundle_root": str(_verification_helper_bundle_root()),
        "catalog_path": str(_verification_helper_catalog_path()),
        "runners": runners,
        "available_runners": sorted(runners),
        "check_families": catalog.get("check_families") or sorted(SEMANTIC_ASSERTION_CHECKS),
        "heuristics": catalog.get("heuristics") or DEFAULT_SEMANTIC_HEURISTICS,
        "project_helper_root": str((resolved_workspace / VERIFICATION_HELPER_RELATIVE_ROOT).resolve()),
        "project_helper_relative_root": VERIFICATION_HELPER_RELATIVE_ROOT.as_posix(),
        "materialization": materialization,
        "version_status": materialization.get("status"),
        "import_snippets": _verification_helper_import_snippets(resolved_workspace, catalog),
    }


def _materialized_helper_runtime_directories(catalog: dict[str, Any] | None = None) -> list[str]:
    roots = {Path(entrypoint).parts[0] for entrypoint in _materialized_helper_entrypoints(catalog)}
    return sorted(root for root in roots if root)


def _copy_verification_helper_bundle(source_root: Path, destination_root: Path, catalog: dict[str, Any]) -> None:
    for directory in _materialized_helper_runtime_directories(catalog):
        shutil.copytree(source_root / directory, destination_root / directory)


def sync_verification_helpers(workspace: str | Path, force: bool = False) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    if not resolved_workspace.exists():
        raise FileNotFoundError(f"Workspace path does not exist: {resolved_workspace}")
    source_root = _verification_helper_bundle_root()
    if not source_root.exists():
        raise FileNotFoundError(f"Verification helper bundle is missing: {source_root}")
    catalog = _load_verification_helper_catalog()
    destination_root = _verification_helper_version_root(resolved_workspace)
    legacy_root = _legacy_verification_helper_sync_root(resolved_workspace)
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    materialization_before = _verification_helper_materialization_status(resolved_workspace)
    removed_legacy_root = False
    if legacy_root.exists():
        shutil.rmtree(legacy_root)
        try:
            legacy_root.parent.rmdir()
        except OSError:
            pass
        removed_legacy_root = True
    if force and destination_root.exists():
        shutil.rmtree(destination_root)
    if destination_root.exists() and materialization_before.get("status") == "synced" and not force:
        status = "already_synced"
    else:
        if destination_root.exists():
            shutil.rmtree(destination_root)
        destination_root.mkdir(parents=True, exist_ok=True)
        _copy_verification_helper_bundle(source_root, destination_root, catalog)
        status = "synced"
    marker_payload = {
        "schema_version": 1,
        "bundle_version": _verification_helper_version(),
        "entrypoints": {
            runner_id: str(runner.get("entrypoint") or "")
            for runner_id, runner in (catalog.get("runners") or {}).items()
        },
        "materialized_relative_root": VERIFICATION_HELPER_RELATIVE_ROOT.as_posix(),
        "synced_at": now_iso(),
    }
    _write_json(
        _verification_helper_marker_path(resolved_workspace),
        marker_payload,
    )
    files = sorted(str(candidate) for candidate in destination_root.rglob("*") if candidate.is_file())
    return {
        "workspace_path": str(resolved_workspace),
        "status": status,
        "bundle_version": _verification_helper_version(),
        "source_root": str(source_root),
        "destination_root": str(destination_root),
        "marker_path": str(_verification_helper_marker_path(resolved_workspace)),
        "version_marker": marker_payload,
        "removed_legacy_root": removed_legacy_root,
        "file_count": len(files),
        "files": files,
        "materialization": _verification_helper_materialization_status(resolved_workspace),
        "import_snippets": _verification_helper_import_snippets(resolved_workspace, catalog),
        "catalog": show_verification_helper_catalog(resolved_workspace),
    }


def _runner_from_legacy(value: str | None) -> str | None:
    mapping = {
        "browser-audit": "browser-layout-audit",
        "browser-layout": "browser-layout-audit",
        "playwright": "playwright-visual",
        "detox": "detox-visual",
        "compose-screenshot": "android-compose-screenshot",
        "ios-simulator": "ios-simulator-capture",
        "shell": "shell-contract",
        "python": "shell-contract",
        "mcp": "shell-contract",
        "dashboard": "shell-contract",
        "smoke": "shell-contract",
    }
    if value is None:
        return None
    return mapping.get(value, value)


def _normalize_browser_layout_audit(payload: Any, case: dict[str, Any]) -> dict[str, Any]:
    config = dict(payload or {})
    return {
        "url": str(config.get("url") or "").strip() or None,
        "base_url": str(config.get("base_url") or "").strip() or None,
        "wait_for": str(config.get("wait_for") or "").strip() or None,
        "settle_ms": int(config.get("settle_ms", 1200)),
        "wait_timeout_ms": int(config.get("wait_timeout_ms", 15000)),
        "selector": [str(item) for item in config.get("selector") or [] if str(item or "").strip()],
        "container_selector": [
            str(item)
            for item in config.get("container_selector") or []
            if str(item or "").strip()
        ],
        "text_selector": [str(item) for item in config.get("text_selector") or [] if str(item or "").strip()],
        "allow_selector": [str(item) for item in config.get("allow_selector") or [] if str(item or "").strip()],
        "chrome_path": str(config.get("chrome_path") or "").strip() or None,
        "report_path": str(config.get("report_path") or f"{case['id']}-browser-layout-audit.json"),
        "screenshot_path": str(config.get("screenshot_path") or f"{case['id']}-browser-layout-audit.png"),
    }


def _normalize_native_layout_audit(payload: Any, case: dict[str, Any]) -> dict[str, Any]:
    config = dict(payload or {})
    semantic_assertions = case.get("semantic_assertions") or {}
    enabled_default = case.get("runner") in NATIVE_LAYOUT_AUDIT_RUNNERS and bool(semantic_assertions.get("enabled"))
    enabled = bool(config.get("enabled", enabled_default))
    requested_checks = config.get("required_checks")
    if requested_checks:
        required_checks: list[str] = []
        for item in requested_checks:
            check_id = str(item or "").strip().lower()
            if check_id and check_id not in required_checks:
                required_checks.append(check_id)
    elif enabled:
        required_checks = list(DEFAULT_NATIVE_LAYOUT_AUDIT_CHECKS)
    else:
        required_checks = []
    unsupported = [check_id for check_id in required_checks if check_id not in SEMANTIC_ASSERTION_CHECKS]
    if unsupported:
        raise ValueError(f"Unsupported native layout audit checks: {', '.join(unsupported)}")
    unsupported_by_runner = [check_id for check_id in required_checks if check_id not in _runner_supported_checks(case.get("runner"))]
    return {
        "enabled": enabled,
        "report_path": str(config.get("report_path") or f"{case['id']}-native-layout-audit.json"),
        "required_checks": required_checks,
        "unsupported_required_checks": unsupported_by_runner,
        "check_pair_overlap": bool(config.get("check_pair_overlap", True)),
        "pair_overlap_tolerance_px": float(
            config.get("pair_overlap_tolerance_px", _layout_audit_threshold("pair-overlap", "epsilon_px", 2.0))
        ),
        "pair_overlap_ratio_threshold": float(
            config.get("pair_overlap_ratio_threshold", _layout_audit_threshold("pair-overlap", "ratio_threshold", 0.04))
        ),
        "check_edge_gutter_balance": bool(config.get("check_edge_gutter_balance", True)),
        "edge_gutter_balance_tolerance_px": float(
            config.get(
                "edge_gutter_balance_tolerance_px",
                _layout_audit_threshold("edge-gutter-imbalance", "difference_px", 8.0),
            )
        ),
        "edge_gutter_ratio_threshold": float(
            config.get(
                "edge_gutter_ratio_threshold",
                _layout_audit_threshold("edge-gutter-imbalance", "ratio_threshold", 0.25),
            )
        ),
        "check_sibling_gap_consistency": bool(config.get("check_sibling_gap_consistency", True)),
        "sibling_gap_tolerance_px": float(
            config.get(
                "sibling_gap_tolerance_px",
                _layout_audit_threshold("sibling-gap-inconsistency", "difference_px", 8.0),
            )
        ),
        "sibling_gap_ratio_threshold": float(
            config.get(
                "sibling_gap_ratio_threshold",
                _layout_audit_threshold("sibling-gap-inconsistency", "ratio_threshold", 0.3),
            )
        ),
        "check_alignment_drift": bool(config.get("check_alignment_drift", True)),
        "alignment_drift_tolerance_px": float(
            config.get(
                "alignment_drift_tolerance_px",
                _layout_audit_threshold("alignment-drift", "drift_px", 8.0),
            )
        ),
        "check_vertical_rhythm": bool(config.get("check_vertical_rhythm", True)),
        "vertical_rhythm_tolerance_px": float(
            config.get(
                "vertical_rhythm_tolerance_px",
                _layout_audit_threshold("vertical-rhythm-drift", "difference_px", 12.0),
            )
        ),
        "vertical_rhythm_ratio_threshold": float(
            config.get(
                "vertical_rhythm_ratio_threshold",
                _layout_audit_threshold("vertical-rhythm-drift", "ratio_threshold", 0.3),
            )
        ),
        "check_touch_targets": bool(config.get("check_touch_targets", True)),
        "touch_target_min_width_px": float(
            config.get(
                "touch_target_min_width_px",
                _layout_audit_threshold("touch-target-too-small", "min_width", 44.0),
            )
        ),
        "touch_target_min_height_px": float(
            config.get(
                "touch_target_min_height_px",
                _layout_audit_threshold("touch-target-too-small", "min_height", 44.0),
            )
        ),
        "check_flex_distribution": bool(config.get("check_flex_distribution", True)),
        "flex_distribution_ratio_threshold": float(
            config.get(
                "flex_distribution_ratio_threshold",
                _layout_audit_threshold("unexpected-flex-distribution", "ratio_threshold", 1.8),
            )
        ),
        "flex_cross_axis_tolerance_px": float(
            config.get(
                "flex_cross_axis_tolerance_px",
                _layout_audit_threshold("unexpected-flex-distribution", "cross_axis_tolerance_px", 16.0),
            )
        ),
        "fail_on_missing_bounds": bool(config.get("fail_on_missing_bounds", True)),
        "require_style_tokens": bool(config.get("require_style_tokens", True)),
    }


def _normalize_android_logcat(payload: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(payload or {})
    return {
        "enabled": bool(config.get("enabled", False)),
        "serial": config.get("serial"),
        "package": config.get("package"),
        "pid_mode": config.get("pid_mode") or "none",
        "buffers": list(config.get("buffers") or ["main", "crash"]),
        "filter_specs": list(config.get("filter_specs") or []),
        "clear_on_start": bool(config.get("clear_on_start", False)),
        "tail_lines_on_failure": int(config.get("tail_lines_on_failure", 80)),
    }


def _default_locator_kind_for_runner(runner: str) -> str:
    if runner == "playwright-visual":
        return "selector"
    if runner == "android-compose-screenshot":
        return "semantics_tag"
    return "test_id"


def _normalize_semantic_locator(payload: Any, *, runner: str) -> dict[str, Any] | None:
    if payload is None or payload == "":
        return None
    if isinstance(payload, str):
        normalized = {
            "kind": _default_locator_kind_for_runner(runner),
            "value": payload,
            "name": None,
            "exact": False,
        }
    elif isinstance(payload, dict):
        normalized = {
            "kind": str(payload.get("kind") or _default_locator_kind_for_runner(runner)).strip().lower(),
            "value": payload.get("value"),
            "name": payload.get("name"),
            "exact": bool(payload.get("exact", False)),
        }
    else:
        raise ValueError(f"Unsupported semantic locator payload: {payload}")
    if normalized["kind"] not in SEMANTIC_LOCATOR_KINDS:
        raise ValueError(f"Unsupported semantic locator kind: {normalized['kind']}")
    if normalized["kind"] not in _runner_supported_locator_kinds(runner):
        raise ValueError(f"Locator kind `{normalized['kind']}` is not supported by runner `{runner}`")
    if normalized["value"] in {None, ""}:
        raise ValueError(f"Semantic locator for runner `{runner}` requires a non-empty value")
    return normalized


def _normalize_semantic_target(payload: dict[str, Any], *, runner: str, index: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"Semantic target must be an object: {payload}")
    target_id = str(payload.get("target_id") or payload.get("id") or f"target-{index}").strip()
    if not target_id:
        raise ValueError("Semantic target requires target_id")
    interactions: list[str] = []
    for item in payload.get("interactions") or []:
        interaction = str(item or "").strip().lower()
        if interaction and interaction not in interactions:
            interactions.append(interaction)
    normalized = {
        "target_id": target_id,
        "locator": _normalize_semantic_locator(payload.get("locator"), runner=runner),
        "container_locator": _normalize_semantic_locator(payload.get("container_locator"), runner=runner),
        "scroll_container_locator": _normalize_semantic_locator(payload.get("scroll_container_locator"), runner=runner),
        "interactions": interactions,
        "expected_attributes": copy.deepcopy(payload.get("expected_attributes") or {}),
        "expected_styles": copy.deepcopy(payload.get("expected_styles") or {}),
        "expected_layout": copy.deepcopy(payload.get("expected_layout") or {}),
        "allow_clipping": bool(payload.get("allow_clipping", False)),
        "allow_occlusion": bool(payload.get("allow_occlusion", False)),
        "allow_text_truncation": bool(payload.get("allow_text_truncation", False)),
    }
    if normalized["locator"] is None:
        raise ValueError(f"Semantic target `{target_id}` requires locator")
    return normalized


def _default_semantic_checks(case: dict[str, Any]) -> list[str]:
    required = [
        "presence_uniqueness",
        "visibility",
        "scroll_reachability",
        "overflow_clipping",
        "layout_relations",
        "text_overflow",
        "accessibility_state",
    ]
    if case.get("runner") == "playwright-visual" or str(case.get("surface_type") or "").lower() == "web":
        required.extend(["computed_styles", "interaction_states", "occlusion"])
    elif _case_prefers_android(case):
        required.extend(["computed_styles", "interaction_states", "occlusion"])
    else:
        required.extend(["interaction_states", "occlusion"])
    if case.get("baseline", {}).get("source_path") and case.get("runner") in VISUAL_RUNNERS:
        required.append("screenshot_baseline")
    deduped: list[str] = []
    for item in required:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _normalize_semantic_artifacts(payload: Any) -> dict[str, Any]:
    config = dict(payload or {})
    return {
        "target_screenshots": bool(config.get("target_screenshots", False)),
        "debug_snapshots": bool(config.get("debug_snapshots", False)),
        "dom_snapshot": bool(config.get("dom_snapshot", False)),
        "report_copy": bool(config.get("report_copy", True)),
        "debug_layout_overlay": bool(config.get("debug_layout_overlay", False)),
        "capture_on_failure_only": bool(config.get("capture_on_failure_only", False)),
    }


def _normalize_semantic_platform_hooks(payload: Any) -> dict[str, Any]:
    config = dict(payload or {})
    return {
        "module": config.get("module"),
        "entrypoint": config.get("entrypoint"),
        "function": config.get("function"),
        "probe": config.get("probe"),
        "required": bool(config.get("required", False)),
        "args": copy.deepcopy(config.get("args") or {}),
        "metadata_keys": list(config.get("metadata_keys") or []),
    }


def _normalize_semantic_assertions(payload: dict[str, Any] | None, case: dict[str, Any]) -> dict[str, Any]:
    config = dict(payload or {})
    enabled = bool(config.get("enabled", False))
    if enabled and case.get("runner") not in VISUAL_RUNNERS:
        raise ValueError(f"Semantic assertions require a visual runner: {case['id']}")
    requested = config.get("required_checks")
    if requested:
        required_checks = []
        for item in requested:
            check_id = str(item or "").strip().lower()
            if not check_id or check_id in required_checks:
                continue
            required_checks.append(check_id)
    elif enabled:
        required_checks = _default_semantic_checks(case)
    else:
        required_checks = []
    unsupported = [check_id for check_id in required_checks if check_id not in SEMANTIC_ASSERTION_CHECKS]
    if unsupported:
        raise ValueError(f"Unsupported semantic assertion checks: {', '.join(unsupported)}")
    unsupported_by_runner = [check_id for check_id in required_checks if check_id not in _runner_supported_checks(case.get("runner"))]
    targets = [
        _normalize_semantic_target(target, runner=case.get("runner"), index=index)
        for index, target in enumerate(config.get("targets") or [], start=1)
    ]
    auto_scan = bool(config.get("auto_scan", enabled))
    heuristics: list[str] = []
    for item in config.get("heuristics") or (DEFAULT_SEMANTIC_HEURISTICS if auto_scan else []):
        heuristic = str(item or "").strip().lower()
        if heuristic and heuristic not in heuristics:
            heuristics.append(heuristic)
    return {
        "enabled": enabled,
        "report_path": config.get("report_path") or f"{case['id']}-semantic.json",
        "required_checks": required_checks,
        "unsupported_required_checks": unsupported_by_runner,
        "targets": targets,
        "auto_scan": auto_scan,
        "heuristics": heuristics,
        "artifacts": _normalize_semantic_artifacts(config.get("artifacts")),
        "platform_hooks": _normalize_semantic_platform_hooks(config.get("platform_hooks")),
        "runner_capabilities": _runner_capability_matrix(case.get("runner")),
    }


def _normalize_case(case: dict[str, Any]) -> dict[str, Any]:
    payload = dict(case or {})
    runner = payload.get("runner") or _runner_from_legacy(payload.get("runner_type")) or "shell-contract"
    target = dict(payload.get("target") or {})
    if payload.get("route") and "route" not in target:
        target["route"] = payload["route"]
    if payload.get("screen_id") and "screen_id" not in target:
        target["screen_id"] = payload["screen_id"]
    if payload.get("service") and "service" not in target:
        target["service"] = payload["service"]
    device_or_viewport = dict(payload.get("device_or_viewport") or {})
    if payload.get("viewport") and "viewport" not in device_or_viewport:
        device_or_viewport["viewport"] = payload["viewport"]
    if payload.get("device") and "device" not in device_or_viewport:
        device_or_viewport["device"] = payload["device"]
    routes_or_screens = list(payload.get("routes_or_screens") or [])
    for candidate in (
        payload.get("route"),
        payload.get("screen_id"),
        target.get("route"),
        target.get("screen_id"),
    ):
        if candidate and candidate not in routes_or_screens:
            routes_or_screens.append(candidate)
    baseline = dict(payload.get("baseline") or {})
    baseline_source = payload.get("baseline_source") or baseline.get("source_path")
    if baseline_source and "source_path" not in baseline:
        baseline["source_path"] = baseline_source
    normalized = {
        "id": payload["id"],
        "title": payload.get("title") or payload["id"],
        "surface_type": payload.get("surface_type") or payload.get("platform") or "service",
        "runner": runner,
        "tags": payload.get("tags", []),
        "feature_ids": payload.get("feature_ids", []),
        "surface_ids": payload.get("surface_ids", []),
        "routes_or_screens": routes_or_screens,
        "changed_path_globs": payload.get("changed_path_globs", []),
        "host_requirements": payload.get("host_requirements", []),
        "baseline_source": baseline_source,
        "target": target,
        "cwd": payload.get("cwd", "."),
        "argv": payload.get("argv"),
        "shell_command": payload.get("shell_command"),
        "env": payload.get("env", {}),
        "device_or_viewport": device_or_viewport,
        "locale": payload.get("locale"),
        "timezone": payload.get("timezone"),
        "color_scheme": payload.get("color_scheme"),
        "freeze_clock": payload.get("freeze_clock", False),
        "seed_step": payload.get("seed_step"),
        "readiness_probe": payload.get("readiness_probe"),
        "masks": payload.get("masks", []),
        "artifact_expectations": payload.get("artifact_expectations", []),
        "retry_policy": payload.get("retry_policy", {"attempts": 1}),
        "baseline": baseline,
        "auth_profile_ref": payload.get("auth_profile_ref"),
        "auth_request_mode": payload.get("auth_request_mode") or "read_only",
        "auth_action_tags": payload.get("auth_action_tags", []),
        "auth_session_binding": copy.deepcopy(payload.get("auth_session_binding")),
        "auth_context": payload.get("auth_context"),
        "android_logcat": _normalize_android_logcat(payload.get("android_logcat")),
    }
    normalized["semantic_assertions"] = _normalize_semantic_assertions(payload.get("semantic_assertions"), normalized)
    normalized["native_layout_audit"] = _normalize_native_layout_audit(
        payload.get("native_layout_audit") or payload.get("native_audit"),
        normalized,
    )
    normalized["browser_layout_audit"] = _normalize_browser_layout_audit(
        payload.get("browser_layout_audit") or payload.get("browser_audit"),
        normalized,
    )
    if runner not in RUNNER_TYPES:
        raise ValueError(f"Unsupported verification runner: {runner}")
    return normalized


def _normalize_recipes(recipes: dict[str, Any], workspace: str | Path) -> dict[str, Any]:
    payload = dict(recipes or {})
    payload["schema_version"] = 2
    payload["workspace_path"] = str(Path(workspace).expanduser().resolve())
    payload["updated_at"] = now_iso()
    payload["baseline_policy"] = payload.get("baseline_policy") or {
        "canonical_baselines": "project_owned",
        "transient_artifacts": "external_state_only",
    }
    payload["cases"] = [_normalize_case(case) for case in payload.get("cases", [])]
    payload["suites"] = payload.get("suites", [])
    return payload


def _default_recipes(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    paths = workspace_paths(workspace, workstream_id=workstream_id)
    detection = detect_workspace(workspace)
    payload = {
        "schema_version": 2,
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": paths["current_workstream_id"],
        "updated_at": now_iso(),
        "baseline_policy": {
            "canonical_baselines": "project_owned",
            "transient_artifacts": "external_state_only",
        },
        "cases": [],
        "suites": [],
        "log_policy": {
            "events_path_root": str(Path(paths["verification_runs_dir"])),
            "stdout_file": "stdout.log",
            "stderr_file": "stderr.log",
            "logcat_file": "logcat.log",
        },
    }
    fragment_payload = _resolve_verification_fragments(workspace, detection)
    payload.update(fragment_payload["verification"])
    payload["verification_fragment_resolution"] = fragment_payload["verification_fragment_resolution"]
    return _normalize_recipes(payload, workspace)


def ensure_verification_recipes(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    paths = _ensure_workspace_paths(workspace, workstream_id=workstream_id, require_initialized=False)
    recipes_path = Path(paths["verification_recipes"])
    if recipes_path.exists():
        return _normalize_recipes(_load_json(recipes_path, default={}) or {}, workspace)
    payload = _default_recipes(workspace, workstream_id=workstream_id)
    _write_json(recipes_path, payload)
    return payload


def read_verification_recipes(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    return ensure_verification_recipes(workspace, workstream_id=workstream_id)


def write_verification_recipes(workspace: str | Path, recipes: dict[str, Any], workstream_id: str | None = None) -> dict[str, Any]:
    paths = _ensure_workspace_paths(workspace, workstream_id=workstream_id)
    payload = _default_recipes(workspace, workstream_id=workstream_id)
    payload.update(recipes or {})
    payload = _normalize_recipes(payload, workspace)
    payload["workstream_id"] = paths["current_workstream_id"]
    _write_json(Path(paths["verification_recipes"]), payload)
    return payload


def _verification_run_paths(workspace: str | Path, run_id: str, workstream_id: str | None = None) -> dict[str, Path]:
    paths = workspace_paths(workspace, workstream_id=workstream_id)
    if not paths["current_workstream_id"] or not paths["verification_runs_dir"] or not paths["artifacts_dir"]:
        raise FileNotFoundError("No current workstream selected.")
    run_root = Path(paths["verification_runs_dir"]) / run_id
    return {
        "run_root": run_root,
        "run_json": run_root / "run.json",
        "events_jsonl": run_root / "events.jsonl",
        "stdout_log": run_root / "stdout.log",
        "stderr_log": run_root / "stderr.log",
        "logcat_log": run_root / "logcat.log",
        "transient_auth_dir": run_root / "auth",
        "artifacts_dir": Path(paths["artifacts_dir"]) / "verification" / run_id,
    }


def _case_prefers_android(case: dict[str, Any]) -> bool:
    device = str((case.get("device_or_viewport") or {}).get("device") or "").lower()
    surface_type = str(case.get("surface_type") or "").lower()
    runner = case.get("runner")
    return runner == "android-compose-screenshot" or surface_type == "android" or "android" in device


def _case_should_capture_logcat(case: dict[str, Any], workspace: str | Path) -> tuple[bool, str | None]:
    config = dict(case.get("android_logcat") or {})
    if config.get("enabled") is False and not _case_prefers_android(case):
        return False, None
    if not _case_prefers_android(case) and case.get("runner") != "detox-visual":
        return False, None
    state = read_workspace_state(workspace)
    android_tooling = state.get("toolchain_capabilities", {}).get("android_tooling", {"available": _tool_available("adb")})
    if not android_tooling.get("available"):
        return False, android_tooling.get("reason") or "adb is not available on the host."
    return True, None


def _adb_command_prefix(config: dict[str, Any]) -> list[str]:
    argv = ["adb"]
    if config.get("serial"):
        argv.extend(["-s", str(config["serial"])])
    return argv


def _resolve_logcat_pid(config: dict[str, Any]) -> str | None:
    if config.get("pid_mode") != "package" or not config.get("package"):
        return None
    result = subprocess.run(  # noqa: S603
        [*_adb_command_prefix(config), "shell", "pidof", "-s", str(config["package"])],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    pid = result.stdout.strip().split()
    return pid[0] if pid else None


def _append_logcat_banner(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(message.rstrip() + "\n")


def _start_android_logcat_capture(workspace: str | Path, run: dict[str, Any], case: dict[str, Any]) -> dict[str, Any] | None:
    enabled, reason = _case_should_capture_logcat(case, workspace)
    if not enabled:
        if reason:
            _append_event(
                workspace,
                run["run_id"],
                "logcat_skipped",
                f"Android logcat capture was skipped for {case['id']}.",
                workstream_id=run.get("workstream_id"),
                case_id=case["id"],
                reason=reason,
            )
        return None
    config = dict(case.get("android_logcat") or {})
    run_paths = _verification_run_paths(workspace, run["run_id"], workstream_id=run.get("workstream_id"))
    if config.get("clear_on_start"):
        subprocess.run([*_adb_command_prefix(config), "logcat", "-c"], capture_output=True, text=True, check=False)  # noqa: S603
    command = [*_adb_command_prefix(config), "logcat"]
    for buffer_name in config.get("buffers") or []:
        command.extend(["-b", str(buffer_name)])
    resolved_pid = _resolve_logcat_pid(config)
    if resolved_pid:
        command.extend(["--pid", resolved_pid])
    filters = list(config.get("filter_specs") or [])
    if filters:
        command.extend(filters)
    _append_logcat_banner(run_paths["logcat_log"], f"\n=== LOGCAT {case['id']} START {now_iso()} ===")
    process = start_logged_process(
        command,
        run_paths["logcat_log"],
        run_paths["logcat_log"],
        cwd=Path(workspace).expanduser().resolve(),
        env=os.environ.copy(),
        shell=False,
    )
    run["logcat_pid"] = process.pid
    run["logcat_case_id"] = case["id"]
    _write_run(workspace, run, workstream_id=run.get("workstream_id"))
    _append_event(
        workspace,
        run["run_id"],
        "logcat_started",
        f"Started Android logcat capture for {case['id']}.",
        workstream_id=run.get("workstream_id"),
        case_id=case["id"],
        pid=process.pid,
        serial=config.get("serial"),
        package=config.get("package"),
        pid_mode=config.get("pid_mode"),
    )
    return {
        "pid": process.pid,
        "case_id": case["id"],
        "config": config,
        "started_at": time.time(),
    }


def _stop_android_logcat_capture(workspace: str | Path, run: dict[str, Any], session: dict[str, Any] | None, status: str = "stopped") -> dict[str, Any] | None:
    if not session:
        return None
    pid = session.get("pid")
    stopped = stop_process(pid) if isinstance(pid, int) and _process_running(pid) else {"pid": pid, "status": "not_running", "stopped": False}
    run["logcat_pid"] = None
    run["logcat_case_id"] = None
    _write_run(workspace, run, workstream_id=run.get("workstream_id"))
    _append_event(
        workspace,
        run["run_id"],
        "logcat_stopped",
        f"Stopped Android logcat capture for {session.get('case_id')}.",
        workstream_id=run.get("workstream_id"),
        case_id=session.get("case_id"),
        pid=pid,
        stop_status=status,
        stopped=stopped.get("stopped"),
    )
    return stopped


def _summarize_logcat_crash(log_path: Path, tail_lines: int) -> dict[str, Any] | None:
    lines = _tail_lines(log_path, tail_lines)
    if not lines:
        return None
    matches = [line for line in lines if any(pattern.search(line) for pattern in LOGCAT_CRASH_PATTERNS)]
    if not matches:
        return None
    return {
        "status": "signals_detected",
        "signals": matches[-5:],
        "tail": lines[-min(tail_lines, 20):],
    }


def _read_appended_lines(path: Path, offset: int) -> tuple[int, list[str]]:
    if not path.exists():
        return offset, []
    with path.open() as handle:
        handle.seek(offset)
        text = handle.read()
        new_offset = handle.tell()
    return new_offset, [line for line in text.splitlines() if line]


def _case_by_id(recipes: dict[str, Any], case_id: str) -> dict[str, Any]:
    for case in recipes.get("cases", []):
        if case.get("id") == case_id:
            return case
    raise ValueError(f"Unknown verification case: {case_id}")


def _suite_by_id(recipes: dict[str, Any], suite_id: str) -> dict[str, Any]:
    for suite in recipes.get("suites", []):
        if suite.get("id") == suite_id:
            return suite
    raise ValueError(f"Unknown verification suite: {suite_id}")


def _resolve_case_ids(recipes: dict[str, Any], mode: str, target_id: str) -> list[str]:
    if mode == "case":
        _case_by_id(recipes, target_id)
        return [target_id]
    suite = _suite_by_id(recipes, target_id)
    case_ids = suite.get("case_ids", [])
    if not isinstance(case_ids, list) or not case_ids:
        raise ValueError(f"Verification suite has no cases: {target_id}")
    for case_id in case_ids:
        _case_by_id(recipes, case_id)
    return case_ids


def _match_changed_paths(case: dict[str, Any], changed_paths: list[str]) -> bool:
    globs = case.get("changed_path_globs") or []
    if not globs or not changed_paths:
        return False
    for candidate in changed_paths:
        candidate_path = Path(candidate).as_posix().lstrip("./")
        for glob in globs:
            normalized_glob = str(glob).lstrip("./")
            if normalized_glob.endswith("/**"):
                prefix = normalized_glob[:-3].rstrip("/")
                if candidate_path == prefix or candidate_path.startswith(f"{prefix}/"):
                    return True
            if fnmatch.fnmatch(candidate_path, normalized_glob):
                return True
    return False


def _case_matches_selectors(case: dict[str, Any], selectors: dict[str, Any]) -> bool:
    if not selectors:
        return False
    explicit_targets = set(selectors.get("explicit_targets") or [])
    if explicit_targets and case.get("id") in explicit_targets:
        return True
    for field in ("tags", "feature_ids", "surface_ids", "routes_or_screens"):
        requested = set(selectors.get(field) or [])
        if requested and requested.intersection(set(case.get(field) or [])):
            return True
    return False


def _selection_case_ids(
    recipes: dict[str, Any],
    selectors: dict[str, Any],
    changed_paths: list[str] | None = None,
    confirm_heuristics: bool = False,
) -> tuple[list[str], list[str], bool]:
    selected_case_ids = [case["id"] for case in recipes.get("cases", []) if _case_matches_selectors(case, selectors)]
    heuristic_case_ids = [
        case["id"]
        for case in recipes.get("cases", [])
        if _match_changed_paths(case, changed_paths or []) and case["id"] not in selected_case_ids
    ]
    used_confirmed_heuristics = False
    if not selected_case_ids and confirm_heuristics:
        selected_case_ids = heuristic_case_ids[:]
        used_confirmed_heuristics = bool(heuristic_case_ids)
    return selected_case_ids, heuristic_case_ids, used_confirmed_heuristics


def _host_requirement_status(state: dict[str, Any], requirement: str) -> dict[str, Any]:
    mapping = {
        "python": state.get("toolchain_capabilities", {}).get("python", {"supported": True, "available": True, "reason": None}),
        "docker": state.get("toolchain_capabilities", {}).get("docker", {"supported": True, "available": True, "reason": None}),
        "web": state.get("toolchain_capabilities", {}).get("web_verification", {"supported": True, "available": True, "reason": None}),
        "browser-runtime": state.get(
            "toolchain_capabilities",
            {},
        ).get("browser_runtime", {"supported": True, "available": True, "reason": None}),
        "android": state.get("toolchain_capabilities", {}).get("mobile_verification_android", {"supported": True, "available": True, "reason": None}),
        "ios": state.get("toolchain_capabilities", {}).get("mobile_verification_ios", {"supported": True, "available": True, "reason": None}),
    }
    status = mapping.get(requirement, {"supported": True, "available": True, "reason": None})
    return {"requirement": requirement, **status}


def _case_selection_summary(workspace: str | Path, case: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    requirements = [_host_requirement_status(state, requirement) for requirement in case.get("host_requirements") or []]
    semantic_assertions = case.get("semantic_assertions") or {}
    native_layout_audit = case.get("native_layout_audit") or {}
    browser_layout_audit = case.get("browser_layout_audit") or {}
    return {
        "case_id": case["id"],
        "title": case.get("title"),
        "surface_type": case.get("surface_type"),
        "runner": case.get("runner"),
        "auth_profile_ref": case.get("auth_profile_ref"),
        "auth_request_mode": case.get("auth_request_mode") or "read_only",
        "auth_action_tags": copy.deepcopy(case.get("auth_action_tags") or []),
        "auth_session_binding": copy.deepcopy(case.get("auth_session_binding")),
        "baseline_source": _case_baseline_source(workspace, case),
        "host_requirements": case.get("host_requirements") or [],
        "host_compatibility": requirements,
        "tags": case.get("tags") or [],
        "surface_ids": case.get("surface_ids") or [],
        "feature_ids": case.get("feature_ids") or [],
        "routes_or_screens": case.get("routes_or_screens") or [],
        "changed_path_globs": case.get("changed_path_globs") or [],
        "semantic_assertions_enabled": bool(semantic_assertions.get("enabled")),
        "semantic_target_count": len(semantic_assertions.get("targets") or []),
        "semantic_auto_scan": bool(semantic_assertions.get("auto_scan", False)),
        "native_layout_audit_enabled": bool(native_layout_audit.get("enabled")),
        "native_layout_audit_report_path": native_layout_audit.get("report_path"),
        "browser_layout_audit_enabled": case.get("runner") == "browser-layout-audit",
        "browser_layout_audit_report_path": browser_layout_audit.get("report_path"),
    }


def _selector_map(payload: Any) -> dict[str, Any]:
    return copy.deepcopy(payload) if isinstance(payload, dict) else {}


def _policy_map(payload: Any) -> dict[str, Any]:
    return copy.deepcopy(payload) if isinstance(payload, dict) else {}


def _current_stage_entry(register: dict[str, Any]) -> dict[str, Any] | None:
    current_stage_id = register.get("current_stage")
    if not current_stage_id:
        return None
    for stage in register.get("stages", []):
        if stage.get("id") == current_stage_id:
            return stage
    return None


def _selection_host_compatibility(cases: list[dict[str, Any]], host_os: str | None) -> dict[str, Any]:
    requirements: list[dict[str, Any]] = []
    for case in cases:
        for requirement in case.get("host_compatibility") or []:
            requirement_name = requirement.get("requirement")
            if requirement_name and not any(item.get("requirement") == requirement_name for item in requirements):
                requirements.append(requirement)
    blocking = [
        requirement["requirement"]
        for requirement in requirements
        if not requirement.get("supported", True) or not requirement.get("available", True)
    ]
    return {
        "host_os": host_os,
        "requirements": requirements,
        "supported": not any(not requirement.get("supported", True) for requirement in requirements),
        "available": not any(not requirement.get("available", True) for requirement in requirements),
        "blocking_requirements": blocking,
    }


def _selection_baseline_sources(selected_cases: list[dict[str, Any]], heuristic_suggestions: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    for case in [*selected_cases, *heuristic_suggestions]:
        source = case.get("baseline_source")
        if source and source not in sources:
            sources.append(source)
    return sources


def _selection_helper_guidance(workspace: str | Path, selected_cases: list[dict[str, Any]], heuristic_suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    relevant_cases = [*selected_cases, *heuristic_suggestions]
    materialization = _verification_helper_materialization_status(workspace)
    needs_semantic = any(case.get("semantic_assertions_enabled") for case in relevant_cases)
    missing_targets = [
        case["case_id"]
        for case in relevant_cases
        if case.get("semantic_assertions_enabled") and int(case.get("semantic_target_count") or 0) == 0
    ]
    next_actions: list[str] = []
    if needs_semantic and materialization.get("status") == "legacy_location":
        next_actions.append("Re-run `sync verification helpers` to replace the legacy `.agentiux/verification-helpers` helper layout.")
    elif needs_semantic and not materialization.get("synced"):
        next_actions.append("Run `sync verification helpers` so the project can import the neutral verification helpers.")
    if missing_targets:
        next_actions.append(f"Add semantic targets for visual cases: {', '.join(missing_targets)}.")
    if needs_semantic:
        next_actions.append("Run targeted verification while wiring semantic targets, then run the `full` suite for closeout.")
    return {
        "needs_semantic_helpers": needs_semantic,
        "materialization": materialization,
        "cases_missing_targets": missing_targets,
        "next_actions": next_actions,
    }


def resolve_verification_selection(
    workspace: str | Path,
    workstream_id: str | None = None,
    changed_paths: list[str] | None = None,
    confirm_heuristics: bool = False,
    request_mode: str | None = None,
) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    state = read_workspace_state(resolved_workspace)
    task = current_task(resolved_workspace) if state.get("workspace_mode") == "task" else None
    target_workstream_id = workstream_id or state.get("current_workstream_id") or (task.get("linked_workstream_id") if task else None)
    task_selectors = _selector_map(task.get("verification_selectors")) if task else {}
    task_default_mode = task.get("verification_mode_default") if task else None
    if not target_workstream_id:
        requested_mode = request_mode or task_default_mode or "targeted"
        requested_mode_source = (
            "explicit_request"
            if request_mode
            else ("task_default" if task_default_mode else "workstream_default")
        )
        source = "explicit_request" if request_mode else (f"task:{task['task_id']}" if task else "workspace")
        return {
            "workspace_path": str(resolved_workspace),
            "workstream_id": None,
            "workspace_mode": state.get("workspace_mode"),
            "selection_status": "unresolved",
            "source": source,
            "requested_mode": requested_mode,
            "requested_mode_source": requested_mode_source,
            "resolved_mode": "none",
            "targeted": requested_mode != "full",
            "full_suite": False,
            "selectors": task_selectors,
            "selected_cases": [],
            "selected_suite": None,
            "heuristic_suggestions": [],
            "used_confirmed_heuristics": False,
            "baseline_sources": [],
            "helper_guidance": {
                "needs_semantic_helpers": False,
                "materialization": _verification_helper_materialization_status(resolved_workspace),
                "cases_missing_targets": [],
                "next_actions": [],
            },
            "host_compatibility": {
                "host_os": state.get("host_os"),
                "requirements": [],
                "supported": True,
                "available": True,
                "blocking_requirements": [],
            },
            "reason": "No current workstream is selected, so verification cases cannot be resolved yet.",
        }

    register = read_stage_register(resolved_workspace, workstream_id=target_workstream_id)
    current_stage = _current_stage_entry(register)
    recipes = read_verification_recipes(resolved_workspace, workstream_id=target_workstream_id)
    selectors: dict[str, Any] = {}
    stage_selectors = _selector_map(current_stage.get("verification_selectors")) if current_stage else {}
    workstream_selectors = _selector_map(register.get("verification_selectors"))
    stage_policy = _policy_map(current_stage.get("verification_policy")) if current_stage else {}
    workstream_policy = _policy_map(register.get("verification_policy"))
    workstream_default_mode = workstream_policy.get("default_mode", "targeted")
    source = f"task:{task['task_id']}" if task else f"workstream:{register['workstream_id']}"
    requested_mode_source = "workstream_default"
    if task_selectors:
        selectors = task_selectors
    elif stage_selectors:
        selectors = stage_selectors
        source = f"stage:{current_stage['id']}"
    elif workstream_selectors:
        selectors = workstream_selectors
        source = f"workstream:{register['workstream_id']}"
    if request_mode:
        requested_mode = request_mode
        source = "explicit_request"
        requested_mode_source = "explicit_request"
    elif task_default_mode:
        requested_mode = task_default_mode
        requested_mode_source = "task_default"
    elif register.get("stage_status") == "ready_for_closeout" and stage_policy.get("closeout_default_mode") == "full":
        requested_mode = "full"
        requested_mode_source = "stage_closeout_policy"
        source = f"stage:{current_stage['id']}" if current_stage else source
    elif stage_policy.get("default_mode"):
        requested_mode = stage_policy["default_mode"]
        requested_mode_source = "stage_default"
        source = f"stage:{current_stage['id']}" if current_stage else source
    elif register.get("stage_status") == "ready_for_closeout" and workstream_policy.get("closeout_default_mode") == "full":
        requested_mode = "full"
        requested_mode_source = "workstream_closeout_policy"
    else:
        requested_mode = workstream_default_mode
    selected_case_ids, heuristic_case_ids, used_confirmed_heuristics = _selection_case_ids(
        recipes,
        selectors,
        changed_paths=changed_paths,
        confirm_heuristics=confirm_heuristics,
    )
    suite_ids = {suite["id"] for suite in recipes.get("suites", [])}
    selected_suite = None
    selection_status = "resolved"
    reason = "Explicit selectors matched verification cases." if selected_case_ids else "No explicit selectors matched."
    if requested_mode == "full":
        if "full" in suite_ids:
            selected_suite = _suite_by_id(recipes, "full")
            selected_case_ids = _resolve_case_ids(recipes, "suite", "full")
            reason = "Full verification was selected explicitly or by closeout policy."
        elif recipes.get("cases"):
            selected_case_ids = [case["id"] for case in recipes.get("cases", [])]
            reason = "Full verification was requested, so every known case was selected."
        else:
            selection_status = "unresolved"
            reason = "Full verification was requested, but no full-capable cases or suites are defined yet."
    elif used_confirmed_heuristics:
        source = "confirmed_heuristic_suggestion"
        reason = (
            "Explicit selectors did not match, so confirmed heuristic suggestions were used."
            if selectors
            else "Explicit selectors were empty, so confirmed heuristic suggestions were used."
        )
    elif not selected_case_ids:
        selection_status = "unresolved"
        if selectors:
            reason = "No verification cases matched the explicit selectors for this task or workstream."
        else:
            reason = "No explicit verification selectors are recorded for this task or workstream yet."
        if heuristic_case_ids and not confirm_heuristics:
            reason = f"{reason} Heuristic suggestions are available but were not auto-selected."
    selected_cases = [_case_selection_summary(resolved_workspace, _case_by_id(recipes, case_id), state) for case_id in selected_case_ids]
    heuristic_suggestions = [_case_selection_summary(resolved_workspace, _case_by_id(recipes, case_id), state) for case_id in heuristic_case_ids]
    resolved_mode = "full" if requested_mode == "full" and selection_status == "resolved" else ("targeted" if selected_case_ids else "none")
    return {
        "workspace_path": str(resolved_workspace),
        "workstream_id": target_workstream_id,
        "workspace_mode": state.get("workspace_mode"),
        "selection_status": selection_status,
        "source": source,
        "requested_mode": requested_mode,
        "requested_mode_source": requested_mode_source,
        "resolved_mode": resolved_mode,
        "targeted": requested_mode != "full",
        "full_suite": requested_mode == "full" and selection_status == "resolved",
        "selectors": selectors,
        "selected_cases": selected_cases,
        "selected_suite": selected_suite,
        "heuristic_suggestions": heuristic_suggestions,
        "baseline_sources": _selection_baseline_sources(selected_cases, heuristic_suggestions),
        "helper_guidance": _selection_helper_guidance(resolved_workspace, selected_cases, heuristic_suggestions),
        "host_compatibility": _selection_host_compatibility(selected_cases or heuristic_suggestions, state.get("host_os")),
        "changed_paths": changed_paths or [],
        "reason": reason,
    }


def _worker_command() -> list[str]:
    return python_script_command(plugin_root() / "scripts" / "agentiux_dev_verification.py")


def _run_order_key(run: dict[str, Any]) -> tuple[int, int, str]:
    created_ns = int(run.get("created_at_ns") or 0)
    if created_ns <= 0:
        parsed = _parse_iso_timestamp(run.get("created_at"))
        created_ns = int(parsed.timestamp() * 1_000_000_000) if parsed else 0
    terminal_ns = int(run.get("completed_at_ns") or run.get("started_at_ns") or created_ns or 0)
    if terminal_ns <= 0:
        parsed = _parse_iso_timestamp(run.get("completed_at")) or _parse_iso_timestamp(run.get("started_at")) or _parse_iso_timestamp(run.get("created_at"))
        terminal_ns = int(parsed.timestamp() * 1_000_000_000) if parsed else 0
    return (created_ns, terminal_ns, str(run.get("run_id") or ""))


def _terminal_run_key(run: dict[str, Any]) -> tuple[int, int, str]:
    completed_ns = int(run.get("completed_at_ns") or 0)
    if completed_ns <= 0:
        parsed = _parse_iso_timestamp(run.get("completed_at")) or _parse_iso_timestamp(run.get("started_at")) or _parse_iso_timestamp(run.get("created_at"))
        completed_ns = int(parsed.timestamp() * 1_000_000_000) if parsed else 0
    created_ns = int(run.get("created_at_ns") or completed_ns or 0)
    return (completed_ns, created_ns, str(run.get("run_id") or ""))


def _sort_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(runs, key=_run_order_key, reverse=True)


def _read_events(run_paths: dict[str, Path], limit: int | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if run_paths["events_jsonl"].exists():
        with run_paths["events_jsonl"].open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))
    return events[-limit:] if limit is not None else events


def _load_baseline_status(paths: dict[str, str]) -> dict[str, Any]:
    status_path = Path(paths["verification_baselines_dir"]) / "status.json"
    return _load_json(status_path, default={"schema_version": 1, "cases": {}, "updated_at": now_iso()}) or {
        "schema_version": 1,
        "cases": {},
        "updated_at": now_iso(),
    }


def _write_baseline_status(paths: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    payload["updated_at"] = now_iso()
    _write_json(Path(paths["verification_baselines_dir"]) / "status.json", payload)
    return payload


def _case_baseline_source(workspace: str | Path, case: dict[str, Any]) -> str | None:
    baseline = case.get("baseline") or {}
    source = baseline.get("source_path")
    if not source:
        return None
    path = Path(source)
    if path.is_absolute():
        return str(path)
    return str((Path(workspace).expanduser().resolve() / path).resolve())


def _slow_after_seconds(case: dict[str, Any]) -> int:
    retry_policy = case.get("retry_policy") or {}
    return int(retry_policy.get("slow_after_seconds", 10))


def _decorate_run_health(workspace: str | Path, run: dict[str, Any], workstream_id: str | None = None) -> dict[str, Any]:
    if not run:
        return run
    if run.get("status") in TERMINAL_RUN_STATUSES:
        run["health"] = "terminal"
        return run
    run_paths = _verification_run_paths(workspace, run["run_id"], workstream_id=workstream_id)
    events = _read_events(run_paths, limit=20)
    last_event_at = run.get("last_event_at")
    if events:
        last_event_at = events[-1].get("timestamp")
    started_at = _parse_iso_timestamp(run.get("started_at") or run.get("created_at"))
    last_event_dt = _parse_iso_timestamp(last_event_at)
    now = datetime.now(started_at.tzinfo if started_at else None)
    if last_event_dt and (now - last_event_dt).total_seconds() > 8:
        run["health"] = "hung"
    elif started_at and (now - started_at).total_seconds() > int(run.get("slow_after_seconds") or 10):
        run["health"] = "slow"
    else:
        run["health"] = "running"
    run["last_event_at"] = last_event_at
    return run


def _read_run(workspace: str | Path, run_id: str, workstream_id: str | None = None) -> dict[str, Any]:
    run = _load_json(_verification_run_paths(workspace, run_id, workstream_id=workstream_id)["run_json"], default={}) or {}
    if not run:
        raise FileNotFoundError(f"Verification run does not exist: {run_id}")
    if run.get("status") in {"queued", "running"} and not _process_running(run.get("pid")) and not run.get("completed_at"):
        run["status"] = "failed"
        run["completed_at"] = now_iso()
        run["completed_at_ns"] = time.time_ns()
        run["summary"] = {
            **(run.get("summary") or {}),
            "message": "Verification worker exited unexpectedly.",
        }
        if isinstance(run.get("logcat_pid"), int) and _process_running(run.get("logcat_pid")):
            run["logcat_stop"] = stop_process(run["logcat_pid"])
            run["logcat_pid"] = None
        _write_json(_verification_run_paths(workspace, run_id, workstream_id=workstream_id)["run_json"], run)
    return _decorate_run_health(workspace, run, workstream_id=workstream_id)


def _write_run(workspace: str | Path, run: dict[str, Any], workstream_id: str | None = None) -> dict[str, Any]:
    paths = _verification_run_paths(workspace, run["run_id"], workstream_id=workstream_id)
    _write_json(paths["run_json"], run)
    return run


def _append_event(workspace: str | Path, run_id: str, event_type: str, message: str, workstream_id: str | None = None, **extra: Any) -> dict[str, Any]:
    event = {
        "timestamp": now_iso(),
        "event_type": event_type,
        "message": message,
    }
    event.update(extra)
    run_paths = _verification_run_paths(workspace, run_id, workstream_id=workstream_id)
    _append_jsonl(run_paths["events_jsonl"], event)
    run = _load_json(run_paths["run_json"], default={}) or {}
    if run:
        run["last_event_at"] = event["timestamp"]
        run["event_count"] = int(run.get("event_count") or 0) + 1
        _write_json(run_paths["run_json"], run)
    return event


def _run_summary_payload(workspace: str | Path, runs: list[dict[str, Any]], limit: int | None, workstream_id: str | None = None) -> dict[str, Any]:
    sorted_runs = _sort_runs([_decorate_run_health(workspace, run, workstream_id=workstream_id) for run in runs])
    recent_runs = sorted_runs[:limit] if limit is not None else sorted_runs
    active_run = next((run for run in sorted_runs if run.get("status") in {"queued", "running"}), None)
    latest_run = sorted_runs[0] if sorted_runs else None
    completed_runs = [run for run in sorted_runs if run.get("status") in TERMINAL_RUN_STATUSES]
    latest_completed_run = sorted(completed_runs, key=_terminal_run_key, reverse=True)[0] if completed_runs else None
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": workspace_paths(workspace, workstream_id=workstream_id)["current_workstream_id"],
        "run_count": len(sorted_runs),
        "runs": recent_runs,
        "recent_runs": recent_runs,
        "active_run": active_run,
        "latest_run": latest_run,
        "latest_completed_run": latest_completed_run,
    }


def _resolve_case_cwd(workspace: str | Path, case: dict[str, Any]) -> str:
    case_cwd = case.get("cwd")
    if not case_cwd:
        return str(Path(workspace).expanduser().resolve())
    candidate = Path(case_cwd)
    if candidate.is_absolute():
        return str(candidate.resolve())
    return str((Path(workspace).expanduser().resolve() / candidate).resolve())


def _resolve_browser_layout_audit_report_path(run_paths: dict[str, Path], case: dict[str, Any]) -> Path:
    config = case.get("browser_layout_audit") or {}
    candidate = config.get("report_path") or f"{case['id']}-browser-layout-audit.json"
    path = Path(str(candidate))
    if path.is_absolute():
        return path
    return run_paths["artifacts_dir"] / path


def _resolve_native_layout_audit_report_path(run_paths: dict[str, Path], case: dict[str, Any]) -> Path:
    config = case.get("native_layout_audit") or {}
    candidate = config.get("report_path") or f"{case['id']}-native-layout-audit.json"
    path = Path(str(candidate))
    if path.is_absolute():
        return path
    return run_paths["artifacts_dir"] / path


def _resolve_browser_layout_audit_screenshot_path(run_paths: dict[str, Path], case: dict[str, Any]) -> Path:
    config = case.get("browser_layout_audit") or {}
    candidate = config.get("screenshot_path") or f"{case['id']}-browser-layout-audit.png"
    path = Path(str(candidate))
    if path.is_absolute():
        return path
    return run_paths["artifacts_dir"] / path


def _browser_layout_audit_script() -> Path:
    return plugin_root() / "scripts" / "browser_layout_audit.mjs"


def _browser_layout_audit_viewport(case: dict[str, Any]) -> tuple[int, int]:
    config = case.get("device_or_viewport") or {}
    viewport = config.get("viewport")
    if isinstance(viewport, str):
        match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", viewport)
        if match:
            return int(match.group(1)), int(match.group(2))
    if isinstance(viewport, dict):
        width = int(viewport.get("width") or 0)
        height = int(viewport.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
    device = str(config.get("device") or "").lower()
    if any(signal in device for signal in {"iphone", "android", "mobile"}):
        return 390, 844
    if "tablet" in device or "ipad" in device:
        return 820, 1180
    return 1440, 1600


def _browser_layout_audit_url(case: dict[str, Any]) -> str:
    config = case.get("browser_layout_audit") or {}
    explicit_url = str(config.get("url") or "").strip()
    if explicit_url:
        return explicit_url
    route = str((case.get("target") or {}).get("route") or "").strip()
    base_url = str(config.get("base_url") or "").strip()
    if base_url:
        if route:
            return urllib.parse.urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))
        return base_url
    probe = case.get("readiness_probe") or {}
    if probe.get("type") == "http":
        probe_url = str(probe.get("url") or "").strip()
        if probe_url:
            if route:
                parsed = urllib.parse.urlsplit(probe_url)
                return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, route, "", ""))
            return probe_url
    raise ValueError(
        f"Browser layout audit case `{case['id']}` requires `browser_layout_audit.url`, "
        "`browser_layout_audit.base_url`, or an HTTP readiness probe."
    )


def _browser_layout_audit_command(case: dict[str, Any], run_paths: dict[str, Path]) -> list[str]:
    width, height = _browser_layout_audit_viewport(case)
    config = case.get("browser_layout_audit") or {}
    argv = [
        "node",
        str(_browser_layout_audit_script()),
        "--url",
        _browser_layout_audit_url(case),
        "--width",
        str(width),
        "--height",
        str(height),
        "--settle-ms",
        str(int(config.get("settle_ms") or 1200)),
        "--wait-timeout-ms",
        str(int(config.get("wait_timeout_ms") or 15000)),
        "--screenshot-path",
        str(_resolve_browser_layout_audit_screenshot_path(run_paths, case)),
        "--label",
        case["id"],
    ]
    if config.get("wait_for"):
        argv.extend(["--wait-for", str(config["wait_for"])])
    if config.get("chrome_path"):
        argv.extend(["--chrome-path", str(config["chrome_path"])])
    for field, flag in (
        ("selector", "--selector"),
        ("container_selector", "--container-selector"),
        ("text_selector", "--text-selector"),
        ("allow_selector", "--allow-selector"),
    ):
        for value in config.get(field) or []:
            argv.extend([flag, str(value)])
    return argv


def _stop_managed_case_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _wait_for_readiness_probe(
    workspace: str | Path,
    case: dict[str, Any],
    *,
    startup_process: subprocess.Popen[str] | None = None,
) -> None:
    probe = case.get("readiness_probe")
    if not probe:
        return
    probe_type = probe.get("type")
    timeout_seconds = float(probe.get("timeout_seconds", 10))
    deadline = time.time() + timeout_seconds
    if probe_type == "http":
        url = probe["url"]
        expected_status = int(probe.get("status", 200))
        while time.time() < deadline:
            if startup_process is not None and startup_process.poll() is not None:
                raise RuntimeError(f"Startup command for case `{case['id']}` exited before readiness probe succeeded.")
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    if response.status == expected_status:
                        return
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        raise TimeoutError(f"Readiness probe timed out for {url}")
    if probe_type == "file_exists":
        candidate = Path(probe["path"])
        if not candidate.is_absolute():
            candidate = Path(workspace).expanduser().resolve() / candidate
        while time.time() < deadline:
            if startup_process is not None and startup_process.poll() is not None:
                raise RuntimeError(f"Startup command for case `{case['id']}` exited before readiness probe succeeded.")
            if candidate.exists():
                return
            time.sleep(0.2)
        raise TimeoutError(f"Readiness probe timed out for file {candidate}")
    if probe_type == "shell_command":
        argv = probe.get("argv")
        if not argv:
            raise ValueError("shell_command readiness probe requires argv")
        while time.time() < deadline:
            if startup_process is not None and startup_process.poll() is not None:
                raise RuntimeError(f"Startup command for case `{case['id']}` exited before readiness probe succeeded.")
            result = subprocess.run(argv, cwd=str(Path(workspace).expanduser().resolve()), capture_output=True, text=True, check=False)  # noqa: S603
            if result.returncode == 0:
                return
            time.sleep(0.2)
        raise TimeoutError(f"Readiness probe timed out for command {' '.join(argv)}")
    raise ValueError(f"Unsupported readiness probe type: {probe_type}")


def _effective_command(case: dict[str, Any]) -> tuple[list[str] | None, str | None]:
    runner = case.get("runner")
    if case.get("shell_command"):
        return None, case["shell_command"]
    argv = case.get("argv")
    if isinstance(argv, list) and argv:
        return [str(part) for part in argv], None
    if runner in RUNNER_TYPES:
        raise ValueError(f"Verification case requires argv or shell_command: {case['id']}")
    raise ValueError(f"Unsupported verification runner: {runner}")


def _run_browser_layout_audit_case(
    workspace: str | Path,
    run: dict[str, Any],
    case: dict[str, Any],
    cwd: str,
    env: dict[str, str],
) -> int:
    run_paths = _verification_run_paths(workspace, run["run_id"], workstream_id=run.get("workstream_id"))
    startup_process: subprocess.Popen[str] | None = None
    startup_label = None
    if case.get("shell_command"):
        startup_label = case["shell_command"]
        startup_process = start_logged_process(
            case["shell_command"],
            run_paths["stdout_log"],
            run_paths["stderr_log"],
            cwd=cwd,
            env=env,
            shell=True,
        )
    elif isinstance(case.get("argv"), list) and case.get("argv"):
        startup_label = " ".join(str(part) for part in case["argv"])
        startup_process = start_logged_process(
            [str(part) for part in case["argv"]],
            run_paths["stdout_log"],
            run_paths["stderr_log"],
            cwd=cwd,
            env=env,
            shell=False,
        )
    try:
        if startup_process is not None:
            _append_event(
                workspace,
                run["run_id"],
                "browser_layout_audit_startup",
                f"Started browser layout audit server command for {case['id']}.",
                workstream_id=run.get("workstream_id"),
                case_id=case["id"],
                pid=startup_process.pid,
                command=startup_label,
            )
        _wait_for_readiness_probe(workspace, case, startup_process=startup_process)
        audit_command = _browser_layout_audit_command(case, run_paths)
        completed = subprocess.run(  # noqa: S603
            audit_command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        with run_paths["stdout_log"].open("a") as stdout_handle:
            if completed.stdout:
                stdout_handle.write(completed.stdout)
                if not completed.stdout.endswith("\n"):
                    stdout_handle.write("\n")
        with run_paths["stderr_log"].open("a") as stderr_handle:
            if completed.stderr:
                stderr_handle.write(completed.stderr)
                if not completed.stderr.endswith("\n"):
                    stderr_handle.write("\n")
        if completed.returncode != 0:
            return int(completed.returncode)
        report_path = _resolve_browser_layout_audit_report_path(run_paths, case)
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Browser layout audit produced invalid JSON for case `{case['id']}`: {exc}") from exc
        _write_json(report_path, payload)
        return 0 if payload.get("ok") else 1
    finally:
        if startup_process is not None:
            _stop_managed_case_process(startup_process)
            _append_event(
                workspace,
                run["run_id"],
                "browser_layout_audit_startup_stopped",
                f"Stopped browser layout audit server command for {case['id']}.",
                workstream_id=run.get("workstream_id"),
                case_id=case["id"],
                pid=startup_process.pid,
            )


def _auth_runtime_summary(resolved_auth: dict[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(resolved_auth.get("artifact") or {})
    return {
        "profile_id": ((resolved_auth.get("profile") or {}).get("profile_id")),
        "label": ((resolved_auth.get("profile") or {}).get("label")),
        "session_id": ((resolved_auth.get("session") or {}).get("session_id")),
        "resolution_reason": resolved_auth.get("resolution_reason"),
        "request_mode": resolved_auth.get("request_mode"),
        "action_tags": copy.deepcopy(resolved_auth.get("action_tags") or []),
        "artifact_type": artifact.get("artifact_type"),
        "expires_at": artifact.get("expires_at"),
        "summary": copy.deepcopy(resolved_auth.get("artifact_summary") or {}),
    }


def _write_case_auth_runtime(
    workspace: str | Path,
    run: dict[str, Any],
    case: dict[str, Any],
    resolved_auth: dict[str, Any],
) -> dict[str, Any]:
    run_paths = _verification_run_paths(workspace, run["run_id"], workstream_id=run.get("workstream_id"))
    auth_dir = run_paths["transient_auth_dir"]
    auth_dir.mkdir(parents=True, exist_ok=True)
    case_key = sanitize_identifier(case["id"], "case")
    artifact_path = auth_dir / f"{case_key}-artifact.json"
    summary_path = auth_dir / f"{case_key}-summary.json"
    artifact_payload = copy.deepcopy(resolved_auth.get("artifact") or {})
    summary_payload = _auth_runtime_summary(resolved_auth)
    _write_json(artifact_path, artifact_payload)
    _write_json(summary_path, summary_payload)
    return {
        "profile_id": summary_payload.get("profile_id"),
        "artifact_path": str(artifact_path),
        "summary_path": str(summary_path),
        "summary": summary_payload,
    }


def _cleanup_case_auth_runtime(auth_runtime: dict[str, Any] | None) -> None:
    if not auth_runtime:
        return
    for key in ("artifact_path", "summary_path"):
        target = auth_runtime.get(key)
        if target:
            Path(target).unlink(missing_ok=True)
    auth_dir = Path(auth_runtime.get("summary_path") or "").parent if auth_runtime.get("summary_path") else None
    if auth_dir:
        try:
            auth_dir.rmdir()
        except OSError:
            pass


def _resolve_case_auth_runtime(
    workspace: str | Path,
    run: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        resolved_auth = resolve_auth_profile_artifact(
            workspace,
            profile_id=case.get("auth_profile_ref"),
            task_id=run.get("task_id"),
            external_issue=copy.deepcopy(run.get("external_issue")),
            case=case,
            workstream_id=run.get("workstream_id"),
            request_mode=case.get("auth_request_mode"),
            action_tags=case.get("auth_action_tags"),
            session_binding=case.get("auth_session_binding"),
            context_overrides=case.get("auth_context"),
            surface_mode="verification",
        )
    except FileNotFoundError:
        if case.get("auth_profile_ref"):
            append_analytics_event(
                "auth_resolver_failed",
                workspace,
                source="verification",
                status="failed",
                workstream_id=run.get("workstream_id"),
                task_id=run.get("task_id"),
                run_id=run.get("run_id"),
                external_issue=copy.deepcopy(run.get("external_issue")),
                payload={"case_id": case.get("id"), "reason": "explicit_profile_not_found", "profile_id": case.get("auth_profile_ref")},
            )
            raise
        return None
    except Exception as exc:  # noqa: BLE001
        append_analytics_event(
            "auth_resolver_failed",
            workspace,
            source="verification",
            status="failed",
            workstream_id=run.get("workstream_id"),
            task_id=run.get("task_id"),
            run_id=run.get("run_id"),
            external_issue=copy.deepcopy(run.get("external_issue")),
            payload={"case_id": case.get("id"), "error": str(exc)},
        )
        raise
    runtime_auth = _write_case_auth_runtime(workspace, run, case, resolved_auth)
    _append_event(
        workspace,
        run["run_id"],
        "auth_resolved",
        f"Resolved auth profile for case {case['id']}.",
        workstream_id=run.get("workstream_id"),
        case_id=case["id"],
        profile_id=runtime_auth.get("profile_id"),
        auth_summary=copy.deepcopy(runtime_auth.get("summary") or {}),
    )
    append_analytics_event(
        "auth_resolver_succeeded",
        workspace,
        source="verification",
        status="passed",
        workstream_id=run.get("workstream_id"),
        task_id=run.get("task_id"),
        run_id=run.get("run_id"),
        external_issue=copy.deepcopy(run.get("external_issue")),
        payload={"case_id": case.get("id"), "auth": copy.deepcopy(runtime_auth.get("summary") or {})},
    )
    return runtime_auth


def _run_case_attempt(
    workspace: str | Path,
    run: dict[str, Any],
    case: dict[str, Any],
    attempt: int,
    semantic_runtime: dict[str, Any] | None = None,
    auth_runtime: dict[str, Any] | None = None,
) -> int:
    run_paths = _verification_run_paths(workspace, run["run_id"], workstream_id=run.get("workstream_id"))
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in (case.get("env") or {}).items()})
    env["VERIFICATION_RUN_ID"] = run["run_id"]
    env["VERIFICATION_CASE_ID"] = case["id"]
    env["VERIFICATION_ARTIFACT_DIR"] = str(run_paths["artifacts_dir"])
    preferred_helper_root, _ = _preferred_verification_helper_root(workspace)
    runtime_payload = semantic_runtime or {}
    env["VERIFICATION_HELPER_ROOT"] = str(runtime_payload.get("helper_root") or preferred_helper_root)
    env["VERIFICATION_HELPER_VERSION"] = _verification_helper_version()
    semantic_report_path = _resolve_semantic_report_path(run_paths, case)
    semantic_spec_path = _write_resolved_semantic_spec(run_paths, case, semantic_runtime=runtime_payload)
    env["VERIFICATION_SEMANTIC_REPORT_PATH"] = str(semantic_report_path)
    if semantic_spec_path is not None:
        env["VERIFICATION_SEMANTIC_SPEC_PATH"] = str(semantic_spec_path)
    if auth_runtime:
        env["VERIFICATION_AUTH_ARTIFACT_PATH"] = str(auth_runtime["artifact_path"])
        env["VERIFICATION_AUTH_PROFILE_ID"] = str(auth_runtime["profile_id"])
        env["VERIFICATION_AUTH_SUMMARY_PATH"] = str(auth_runtime["summary_path"])
        env["VERIFICATION_AUTH_REQUEST_MODE"] = str((auth_runtime.get("summary") or {}).get("request_mode") or "read_only")
        env["VERIFICATION_AUTH_ACTION_TAGS"] = json.dumps((auth_runtime.get("summary") or {}).get("action_tags") or [])
        env["VERIFICATION_AUTH_SESSION_ID"] = str((auth_runtime.get("summary") or {}).get("session_id") or "")
        env["VERIFICATION_AUTH_RESOLUTION_REASON"] = str((auth_runtime.get("summary") or {}).get("resolution_reason") or "")
    cwd = _resolve_case_cwd(workspace, case)
    if case.get("runner") == "browser-layout-audit":
        return _run_browser_layout_audit_case(workspace, run, case, cwd, env)
    argv, shell_command = _effective_command(case)

    _wait_for_readiness_probe(workspace, case)
    with run_paths["stdout_log"].open("a") as stdout_handle, run_paths["stderr_log"].open("a") as stderr_handle:
        stdout_handle.write(f"\n=== CASE {case['id']} ATTEMPT {attempt} START {now_iso()} ===\n")
        stderr_handle.write(f"\n=== CASE {case['id']} ATTEMPT {attempt} START {now_iso()} ===\n")
    if semantic_spec_path is not None:
        _append_event(
            workspace,
            run["run_id"],
            "semantic_spec_ready",
            f"Semantic spec is ready for {case['id']}.",
            workstream_id=run.get("workstream_id"),
            case_id=case["id"],
            spec_path=str(semantic_spec_path),
            report_path=str(semantic_report_path),
        )
    logcat_session = _start_android_logcat_capture(workspace, run, case)
    try:
        if shell_command:
            process = start_logged_process(
                shell_command,
                run_paths["stdout_log"],
                run_paths["stderr_log"],
                cwd=cwd,
                env=env,
                shell=True,
            )
        else:
            process = start_logged_process(
                argv,
                run_paths["stdout_log"],
                run_paths["stderr_log"],
                cwd=cwd,
                env=env,
                shell=False,
            )
    except Exception:  # noqa: BLE001
        _stop_android_logcat_capture(workspace, run, logcat_session, status="startup_failed")
        raise

    heartbeat_deadline = time.time() + 1.0
    logcat_heartbeat_deadline = time.time() + 1.0
    slow_after_seconds = _slow_after_seconds(case)
    started = time.time()
    slow_emitted = False
    try:
        while process.poll() is None:
            if time.time() >= heartbeat_deadline:
                _append_event(
                    workspace,
                    run["run_id"],
                    "case_heartbeat",
                    f"Verification case {case['id']} is still running.",
                    workstream_id=run.get("workstream_id"),
                    case_id=case["id"],
                    attempt=attempt,
                )
                heartbeat_deadline = time.time() + 1.0
            if logcat_session and time.time() >= logcat_heartbeat_deadline:
                _append_event(
                    workspace,
                    run["run_id"],
                    "logcat_heartbeat",
                    f"Android logcat capture is still running for {case['id']}.",
                    workstream_id=run.get("workstream_id"),
                    case_id=case["id"],
                    pid=logcat_session["pid"],
                )
                logcat_heartbeat_deadline = time.time() + 1.0
            if not slow_emitted and time.time() - started >= slow_after_seconds:
                _append_event(
                    workspace,
                    run["run_id"],
                    "case_slow",
                    f"Verification case {case['id']} is running slower than expected.",
                    workstream_id=run.get("workstream_id"),
                    case_id=case["id"],
                    threshold_seconds=slow_after_seconds,
                )
                slow_emitted = True
            time.sleep(0.2)
    finally:
        _stop_android_logcat_capture(workspace, run, logcat_session)
    return int(process.returncode or 0)


def _case_baseline_result(workspace: str | Path, case: dict[str, Any], exit_code: int) -> dict[str, Any]:
    source_path = _case_baseline_source(workspace, case)
    if not source_path:
        return {"policy": "external-or-none", "source_path": None, "status": "not_applicable"}
    source_exists = Path(source_path).exists()
    if exit_code == 0 and source_exists:
        status = "matched"
    elif exit_code == 0 and not source_exists:
        status = "baseline_missing"
    else:
        status = "diff_or_failure"
    return {
        "policy": "project-owned",
        "source_path": source_path,
        "status": status,
    }


def _resolve_semantic_report_path(run_paths: dict[str, Path], case: dict[str, Any]) -> Path:
    config = case.get("semantic_assertions") or {}
    candidate = config.get("report_path") or f"{case['id']}-semantic.json"
    path = Path(str(candidate))
    if path.is_absolute():
        return path
    return run_paths["artifacts_dir"] / path


def _resolve_semantic_spec_path(run_paths: dict[str, Path], case: dict[str, Any]) -> Path:
    return run_paths["run_root"] / "semantic-specs" / f"{case['id']}.json"


def _preferred_verification_helper_root(workspace: str | Path) -> tuple[Path, dict[str, Any]]:
    materialization = _verification_helper_materialization_status(workspace)
    if materialization.get("synced"):
        return Path(materialization["current_version_root"]).resolve(), materialization
    return _verification_helper_bundle_root(), materialization


def _semantic_preflight_failure(
    case: dict[str, Any],
    report_path: Path,
    helper_root: Path | None,
    materialization: dict[str, Any],
    reason: str,
    message: str,
    *,
    capability_matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = case.get("semantic_assertions") or {}
    return {
        "enabled": True,
        "status": "failed",
        "reason": reason,
        "message": message,
        "report_path": str(report_path),
        "required_checks": list(config.get("required_checks") or []),
        "unsupported_required_checks": list(config.get("unsupported_required_checks") or []),
        "missing_targets": [],
        "missing_checks": [],
        "failed_checks": [],
        "optional_failed_checks": [],
        "target_ids": [target.get("target_id") for target in config.get("targets") or [] if target.get("target_id")],
        "helper_root": str(helper_root) if helper_root is not None else None,
        "helper_bundle_version": _verification_helper_version(),
        "helper_materialization": materialization,
        "runner_capabilities": copy.deepcopy(capability_matrix or config.get("runner_capabilities") or _runner_capability_matrix(case.get("runner"))),
    }


def _semantic_runtime_preflight(workspace: str | Path, run_paths: dict[str, Path], case: dict[str, Any]) -> dict[str, Any]:
    config = case.get("semantic_assertions") or {}
    report_path = _resolve_semantic_report_path(run_paths, case)
    helper_root, materialization = _preferred_verification_helper_root(workspace)
    capability_matrix = copy.deepcopy(config.get("runner_capabilities") or _runner_capability_matrix(case.get("runner")))
    if not config.get("enabled"):
        return {
            "enabled": False,
            "status": "not_enabled",
            "report_path": None,
            "helper_root": str(helper_root),
            "helper_bundle_version": _verification_helper_version(),
            "helper_materialization": materialization,
            "runner_capabilities": capability_matrix,
        }
    try:
        catalog = _load_verification_helper_catalog()
    except Exception as exc:  # noqa: BLE001
        return _semantic_preflight_failure(
            case,
            report_path,
            None,
            materialization,
            "helper_bundle_missing",
            str(exc),
            capability_matrix=capability_matrix,
        )
    if not _runner_catalog_entry(case.get("runner"), catalog):
        return _semantic_preflight_failure(
            case,
            report_path,
            helper_root,
            materialization,
            "runner_not_cataloged",
            f"Verification helper catalog has no semantic entrypoint for runner `{case.get('runner')}`.",
            capability_matrix=capability_matrix,
        )
    if materialization.get("status") == "not_synced":
        return _semantic_preflight_failure(
            case,
            report_path,
            helper_root,
            materialization,
            "helper_bundle_not_synced",
            "Verification helper bundle is not materialized in `.verification/helpers`. Run `sync verification helpers` first.",
            capability_matrix=capability_matrix,
        )
    if materialization.get("status") == "legacy_location":
        return _semantic_preflight_failure(
            case,
            report_path,
            helper_root,
            materialization,
            "helper_bundle_legacy_location",
            "A legacy `.agentiux/verification-helpers` helper layout was detected. Re-run `sync verification helpers` to materialize `.verification/helpers`.",
            capability_matrix=capability_matrix,
        )
    if materialization.get("status") == "version_drift":
        return _semantic_preflight_failure(
            case,
            report_path,
            helper_root,
            materialization,
            "helper_bundle_version_drift",
            "Materialized verification helpers do not match the current bundle version. Re-run `sync verification helpers` before this case.",
            capability_matrix=capability_matrix,
        )
    unsupported_required_checks = list(config.get("unsupported_required_checks") or [])
    if unsupported_required_checks:
        return _semantic_preflight_failure(
            case,
            report_path,
            helper_root,
            materialization,
            "unsupported_required_checks",
            (
                f"Semantic assertion checks {', '.join(unsupported_required_checks)} are not supported by runner "
                f"`{case.get('runner')}`."
            ),
            capability_matrix=capability_matrix,
        )
    if not helper_root.exists():
        return _semantic_preflight_failure(
            case,
            report_path,
            helper_root,
            materialization,
            "helper_bundle_missing",
            f"Verification helper bundle is missing at {helper_root}.",
            capability_matrix=capability_matrix,
        )
    return {
        "enabled": True,
        "status": "ready",
        "report_path": str(report_path),
        "helper_root": str(helper_root),
        "helper_bundle_version": _verification_helper_version(),
        "helper_materialization": materialization,
        "runner_capabilities": capability_matrix,
    }


def _write_resolved_semantic_spec(
    run_paths: dict[str, Path],
    case: dict[str, Any],
    semantic_runtime: dict[str, Any] | None = None,
) -> Path | None:
    config = case.get("semantic_assertions") or {}
    if not config.get("enabled"):
        return None
    spec_path = _resolve_semantic_spec_path(run_paths, case)
    runtime_payload = semantic_runtime or {}
    payload = {
        "schema_version": 2,
        "helper_bundle_version": _verification_helper_version(),
        "helper_root": runtime_payload.get("helper_root"),
        "helper_materialization": copy.deepcopy(runtime_payload.get("helper_materialization") or {}),
        "runner": case.get("runner"),
        "case_id": case.get("id"),
        "surface_type": case.get("surface_type"),
        "report_path": str(_resolve_semantic_report_path(run_paths, case)),
        "required_checks": list(config.get("required_checks") or []),
        "unsupported_required_checks": list(config.get("unsupported_required_checks") or []),
        "targets": copy.deepcopy(config.get("targets") or []),
        "auto_scan": bool(config.get("auto_scan", False)),
        "heuristics": list(config.get("heuristics") or []),
        "artifacts": copy.deepcopy(config.get("artifacts") or {}),
        "platform_hooks": copy.deepcopy(config.get("platform_hooks") or {}),
        "runner_capabilities": copy.deepcopy(runtime_payload.get("runner_capabilities") or config.get("runner_capabilities") or {}),
        "locale": case.get("locale"),
        "timezone": case.get("timezone"),
        "color_scheme": case.get("color_scheme"),
        "freeze_clock": bool(case.get("freeze_clock", False)),
        "masks": list(case.get("masks") or []),
        "target": copy.deepcopy(case.get("target") or {}),
    }
    _write_json(spec_path, payload)
    return spec_path


def _normalize_semantic_result_status(value: Any) -> str:
    status = str(value or "").strip().lower() or "unknown"
    return status if status in SEMANTIC_RESULT_STATUSES else "unknown"


def _normalize_semantic_artifact_paths(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if item not in {None, ""}]


def _normalize_semantic_report_check(item: dict[str, Any], *, runner: str, target_id: str) -> dict[str, Any] | None:
    check_id = str(item.get("check_id") or item.get("id") or "").strip().lower()
    if not check_id:
        return None
    return {
        "check_id": check_id,
        "target_id": target_id,
        "status": _normalize_semantic_result_status(item.get("status")),
        "runner": str(item.get("runner") or runner),
        "diagnostics": copy.deepcopy(item.get("diagnostics") or item.get("details") or {}),
        "artifact_paths": _normalize_semantic_artifact_paths(item.get("artifact_paths") or item.get("artifacts")),
    }


def _parse_semantic_report_targets(payload: Any) -> dict[str, dict[str, Any]]:
    parsed_targets: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return parsed_targets
    runner = str(payload.get("runner") or "")
    for target in payload.get("targets") or []:
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("target_id") or target.get("id") or "").strip()
        if not target_id:
            continue
        parsed = {
            "target_id": target_id,
            "status": _normalize_semantic_result_status(target.get("status")),
            "diagnostics": copy.deepcopy(target.get("diagnostics") or {}),
            "artifact_paths": _normalize_semantic_artifact_paths(target.get("artifact_paths") or target.get("artifacts")),
            "checks": {},
        }
        checks_payload = target.get("checks") or []
        if isinstance(checks_payload, dict):
            checks_iterable = [{"check_id": check_id, **(value if isinstance(value, dict) else {"status": value})} for check_id, value in checks_payload.items()]
        else:
            checks_iterable = checks_payload
        for check in checks_iterable:
            if not isinstance(check, dict):
                continue
            normalized = _normalize_semantic_report_check(check, runner=runner, target_id=target_id)
            if normalized is not None:
                parsed["checks"][normalized["check_id"]] = normalized
        parsed_targets[target_id] = parsed
    if parsed_targets:
        return parsed_targets
    top_level_checks = payload.get("checks")
    global_target = {
        "target_id": "_global",
        "status": _normalize_semantic_result_status((payload.get("summary") or {}).get("status") or payload.get("status")),
        "diagnostics": {},
        "artifact_paths": [],
        "checks": {},
    }
    if isinstance(top_level_checks, dict):
        for check_id, value in top_level_checks.items():
            item = value if isinstance(value, dict) else {"status": value}
            normalized = _normalize_semantic_report_check(
                {"check_id": check_id, **(item if isinstance(item, dict) else {})},
                runner=runner,
                target_id="_global",
            )
            if normalized is not None:
                global_target["checks"][normalized["check_id"]] = normalized
    elif isinstance(top_level_checks, list):
        for item in top_level_checks:
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("target_id") or "_global")
            target = parsed_targets.setdefault(
                target_id,
                {
                    "target_id": target_id,
                    "status": "unknown",
                    "diagnostics": {},
                    "artifact_paths": [],
                    "checks": {},
                },
            )
            normalized = _normalize_semantic_report_check(item, runner=runner, target_id=target_id)
            if normalized is not None:
                target["checks"][normalized["check_id"]] = normalized
    if parsed_targets:
        return parsed_targets
    if global_target["checks"]:
        parsed_targets["_global"] = global_target
    return parsed_targets


def _semantic_report_schema_errors(payload: Any, case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["Semantic assertions report must be a JSON object."]
    report_runner = payload.get("runner")
    if report_runner and str(report_runner) != str(case.get("runner")):
        errors.append(
            f"Semantic assertions report runner `{report_runner}` does not match case runner `{case.get('runner')}`."
        )
    helper_version = payload.get("helper_bundle_version") or payload.get("spec_version")
    if helper_version and str(helper_version) != _verification_helper_version():
        errors.append(
            f"Semantic assertions report helper version `{helper_version}` does not match `{_verification_helper_version()}`."
        )
    summary = payload.get("summary") or {}
    if summary and not isinstance(summary, dict):
        errors.append("Semantic assertions report summary must be an object when present.")
    elif summary and _normalize_semantic_result_status(summary.get("status")) == "unknown" and summary.get("status") not in {None, ""}:
        errors.append(f"Semantic assertions report summary status `{summary.get('status')}` is unsupported.")
    checks_payload = payload.get("checks")
    targets_payload = payload.get("targets")
    if checks_payload is not None and not isinstance(checks_payload, (list, dict)):
        errors.append("Semantic assertions report checks must be a list or object.")
    if targets_payload is not None and not isinstance(targets_payload, list):
        errors.append("Semantic assertions report targets must be a list.")

    def validate_check_entry(item: Any, *, label: str) -> None:
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object.")
            return
        check_id = item.get("check_id") or item.get("id")
        if not check_id:
            errors.append(f"{label} is missing `check_id`.")
        status = item.get("status")
        if status is not None and _normalize_semantic_result_status(status) == "unknown" and str(status).strip().lower() != "unknown":
            errors.append(f"{label} has unsupported status `{status}`.")
        artifact_paths = item.get("artifact_paths") or item.get("artifacts")
        if artifact_paths is not None and not isinstance(artifact_paths, list):
            errors.append(f"{label} artifact paths must be a list.")

    if isinstance(checks_payload, list):
        for index, item in enumerate(checks_payload, start=1):
            validate_check_entry(item, label=f"checks[{index}]")
    elif isinstance(checks_payload, dict):
        for check_id, item in checks_payload.items():
            validate_check_entry(
                item if isinstance(item, dict) else {"status": item, "check_id": check_id},
                label=f"checks.{check_id}",
            )
    if isinstance(targets_payload, list):
        for index, target in enumerate(targets_payload, start=1):
            if not isinstance(target, dict):
                errors.append(f"targets[{index}] must be an object.")
                continue
            target_id = target.get("target_id") or target.get("id")
            if not target_id:
                errors.append(f"targets[{index}] is missing `target_id`.")
            checks = target.get("checks")
            if checks is not None and not isinstance(checks, (list, dict)):
                errors.append(f"targets[{index}].checks must be a list or object.")
                continue
            if isinstance(checks, list):
                for check_index, item in enumerate(checks, start=1):
                    validate_check_entry(item, label=f"targets[{index}].checks[{check_index}]")
            elif isinstance(checks, dict):
                for check_id, item in checks.items():
                    validate_check_entry(
                        item if isinstance(item, dict) else {"status": item, "check_id": check_id},
                        label=f"targets[{index}].checks.{check_id}",
                    )
    return errors


def _validate_semantic_assertions(run_paths: dict[str, Path], case: dict[str, Any]) -> dict[str, Any]:
    config = case.get("semantic_assertions") or {}
    if not config.get("enabled"):
        return {
            "enabled": False,
            "status": "not_enabled",
            "required_checks": [],
            "report_path": None,
        }
    unsupported_required_checks = list(config.get("unsupported_required_checks") or [])
    if unsupported_required_checks:
        return {
            "enabled": True,
            "status": "failed",
            "report_path": str(_resolve_semantic_report_path(run_paths, case)),
            "required_checks": list(config.get("required_checks") or []),
            "unsupported_required_checks": unsupported_required_checks,
            "missing_checks": [],
            "failed_checks": [],
            "optional_failed_checks": [],
            "reason": "unsupported_required_checks",
            "message": (
                f"Semantic assertion checks {', '.join(unsupported_required_checks)} are not supported by "
                f"runner `{case.get('runner')}`."
            ),
        }
    report_path = _resolve_semantic_report_path(run_paths, case)
    if not report_path.exists():
        return {
            "enabled": True,
            "status": "failed",
            "report_path": str(report_path),
            "required_checks": list(config.get("required_checks") or []),
            "unsupported_required_checks": unsupported_required_checks,
            "missing_checks": list(config.get("required_checks") or []),
            "failed_checks": [],
            "optional_failed_checks": [],
            "reason": "missing_report",
            "message": f"Semantic assertions report is missing for case {case['id']}.",
        }
    try:
        payload = _load_json(report_path, strict=True, purpose="semantic assertions report")
    except ValueError as exc:
        return {
            "enabled": True,
            "status": "failed",
            "report_path": str(report_path),
            "required_checks": list(config.get("required_checks") or []),
            "unsupported_required_checks": unsupported_required_checks,
            "missing_checks": [],
            "failed_checks": [],
            "optional_failed_checks": [],
            "reason": "invalid_report",
            "message": str(exc),
        }
    schema_errors = _semantic_report_schema_errors(payload, case)
    if schema_errors:
        return {
            "enabled": True,
            "status": "failed",
            "report_path": str(report_path),
            "required_checks": list(config.get("required_checks") or []),
            "unsupported_required_checks": unsupported_required_checks,
            "missing_checks": [],
            "failed_checks": [],
            "optional_failed_checks": [],
            "reason": "invalid_report_schema",
            "message": "Semantic assertions report does not match the shared schema.",
            "schema_errors": schema_errors,
        }
    targets = _parse_semantic_report_targets(payload)
    required_checks = list(config.get("required_checks") or [])
    expected_target_ids = [target.get("target_id") for target in config.get("targets") or [] if target.get("target_id")]
    validation_target_ids = expected_target_ids or list(targets.keys()) or ["_global"]
    missing_targets = [target_id for target_id in expected_target_ids if target_id not in targets]
    missing_checks: list[str] = []
    failed_checks: list[str] = []
    optional_failed_checks: list[str] = []
    for target_id in validation_target_ids:
        target_checks = (targets.get(target_id) or {}).get("checks", {})
        for check_id in required_checks:
            composite_id = f"{target_id}/{check_id}" if target_id != "_global" else check_id
            if check_id not in target_checks:
                missing_checks.append(composite_id)
            elif target_checks[check_id].get("status") != "passed":
                failed_checks.append(composite_id)
        for check_id, check in target_checks.items():
            if check_id in required_checks or check.get("status") == "passed":
                continue
            composite_id = f"{target_id}/{check_id}" if target_id != "_global" else check_id
            optional_failed_checks.append(composite_id)
    summary_status = str((payload.get("summary") or {}).get("status") or payload.get("status") or "").lower()
    report_status = "passed"
    reason = None
    if missing_targets:
        report_status = "failed"
        reason = "missing_targets"
    elif missing_checks:
        report_status = "failed"
        reason = "missing_checks"
    elif failed_checks:
        report_status = "failed"
        reason = "failed_checks"
    elif not required_checks and summary_status == "failed":
        report_status = "failed"
        reason = "summary_failed"
    return {
        "enabled": True,
        "status": report_status,
        "report_path": str(report_path),
        "required_checks": required_checks,
        "unsupported_required_checks": unsupported_required_checks,
        "target_ids": validation_target_ids,
        "missing_targets": missing_targets,
        "missing_checks": missing_checks,
        "failed_checks": failed_checks,
        "optional_failed_checks": optional_failed_checks,
        "targets": targets,
        "helper_bundle_version": payload.get("helper_bundle_version") or payload.get("spec_version"),
        "summary_status": summary_status or ("passed" if report_status == "passed" else "failed"),
        "reason": reason,
        "message": (payload.get("summary") or {}).get("message") or payload.get("message"),
    }


def _validate_browser_layout_audit(run_paths: dict[str, Path], case: dict[str, Any]) -> dict[str, Any]:
    if case.get("runner") != "browser-layout-audit":
        return {
            "enabled": False,
            "status": "not_enabled",
            "report_path": None,
        }
    report_path = _resolve_browser_layout_audit_report_path(run_paths, case)
    if not report_path.exists():
        return {
            "enabled": True,
            "status": "failed",
            "report_path": str(report_path),
            "reason": "missing_report",
            "message": f"Browser layout audit report is missing for case {case['id']}.",
            "issue_count": None,
            "issues": [],
            "warning_count": None,
            "warnings": [],
            "screenshot_path": None,
        }
    try:
        payload = _load_json(report_path, strict=True, purpose="browser layout audit report")
    except ValueError as exc:
        return {
            "enabled": True,
            "status": "failed",
            "report_path": str(report_path),
            "reason": "invalid_report",
            "message": str(exc),
            "issue_count": None,
            "issues": [],
            "warning_count": None,
            "warnings": [],
            "screenshot_path": None,
        }
    if not isinstance(payload, dict):
        return {
            "enabled": True,
            "status": "failed",
            "report_path": str(report_path),
            "reason": "invalid_report",
            "message": f"Browser layout audit report is invalid for case {case['id']}.",
            "issue_count": None,
            "issues": [],
            "warning_count": None,
            "warnings": [],
            "screenshot_path": None,
        }
    issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"passed", "warning", "failed"}:
        if issues:
            status = "failed"
        elif warnings:
            status = "warning"
        else:
            status = "passed" if bool(payload.get("ok")) else "failed"
    return {
        "enabled": True,
        "status": status,
        "report_path": str(report_path),
        "reason": (
            None
            if status == "passed"
            else "layout_issues_detected"
            if status == "failed"
            else "layout_warnings_detected"
        ),
        "message": (
            None
            if status == "passed"
            else f"Browser layout audit detected {len(issues)} issues for case {case['id']}."
            if status == "failed"
            else f"Browser layout audit detected {len(warnings)} warnings for case {case['id']}."
        ),
        "issue_count": int(payload.get("issue_count") or len(issues)),
        "issues": issues,
        "warning_count": int(payload.get("warning_count") or len(warnings)),
        "warnings": warnings,
        "screenshot_path": payload.get("screenshot_path"),
        "viewport": copy.deepcopy(payload.get("viewport") or {}),
        "url": payload.get("url"),
        "label": payload.get("label"),
    }


def _coerce_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _rect_from_payload(payload: Any) -> dict[str, float] | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("present") is False:
        return None
    left = _coerce_float(payload.get("left"))
    top = _coerce_float(payload.get("top"))
    right = _coerce_float(payload.get("right"))
    bottom = _coerce_float(payload.get("bottom"))
    width = _coerce_float(payload.get("width"))
    height = _coerce_float(payload.get("height"))
    if left is None:
        left = _coerce_float(payload.get("x"))
    if top is None:
        top = _coerce_float(payload.get("y"))
    if width is None and left is not None and right is not None:
        width = right - left
    if height is None and top is not None and bottom is not None:
        height = bottom - top
    if right is None and left is not None and width is not None:
        right = left + width
    if bottom is None and top is not None and height is not None:
        bottom = top + height
    if None in {left, top, right, bottom, width, height}:
        return None
    return {
        "left": float(left),
        "top": float(top),
        "right": float(right),
        "bottom": float(bottom),
        "width": float(width),
        "height": float(height),
    }


def _extract_rect_from_candidates(candidates: list[Any], nested_keys: tuple[str, ...]) -> dict[str, float] | None:
    for candidate in candidates:
        rect = _rect_from_payload(candidate)
        if rect is not None:
            return rect
        if not isinstance(candidate, dict):
            continue
        for key in nested_keys:
            rect = _rect_from_payload(candidate.get(key))
            if rect is not None:
                return rect
    return None


def _extract_semantic_target_bounds(target: dict[str, Any]) -> dict[str, float] | None:
    checks = target.get("checks") or {}
    layout_diag = (checks.get("layout_relations") or {}).get("diagnostics") or {}
    clipping_diag = (checks.get("overflow_clipping") or {}).get("diagnostics") or {}
    return _extract_rect_from_candidates(
        [
            target.get("diagnostics") or {},
            layout_diag,
            layout_diag.get("layout") if isinstance(layout_diag, dict) else {},
            clipping_diag,
            clipping_diag.get("clipping") if isinstance(clipping_diag, dict) else {},
        ],
        ("bounds_in_root", "target_bounds", "bounds", "rect"),
    )


def _extract_semantic_root_bounds(target: dict[str, Any]) -> dict[str, float] | None:
    checks = target.get("checks") or {}
    layout_diag = (checks.get("layout_relations") or {}).get("diagnostics") or {}
    clipping_diag = (checks.get("overflow_clipping") or {}).get("diagnostics") or {}
    return _extract_rect_from_candidates(
        [
            target.get("diagnostics") or {},
            layout_diag,
            layout_diag.get("layout") if isinstance(layout_diag, dict) else {},
            clipping_diag,
            clipping_diag.get("clipping") if isinstance(clipping_diag, dict) else {},
        ],
        ("root_bounds", "container_bounds", "viewport_bounds"),
    )


def _extract_semantic_style_tokens(target: dict[str, Any]) -> dict[str, Any]:
    checks = target.get("checks") or {}
    computed_diag = (checks.get("computed_styles") or {}).get("diagnostics") or {}
    candidates = [
        computed_diag,
        computed_diag.get("probe") if isinstance(computed_diag, dict) else {},
        target.get("diagnostics") or {},
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("style_tokens", "styles", "computed_styles"):
            value = candidate.get(key)
            if isinstance(value, dict) and value:
                return copy.deepcopy(value)
    return {}


def _extract_semantic_clipping(target: dict[str, Any]) -> dict[str, Any]:
    checks = target.get("checks") or {}
    diagnostics = (checks.get("overflow_clipping") or {}).get("diagnostics") or {}
    if isinstance(diagnostics.get("clipping"), dict):
        return copy.deepcopy(diagnostics.get("clipping") or {})
    return copy.deepcopy(diagnostics) if isinstance(diagnostics, dict) else {}


def _extract_semantic_metadata(target: dict[str, Any]) -> dict[str, Any]:
    checks = target.get("checks") or {}
    diagnostics = (checks.get("occlusion") or {}).get("diagnostics") or {}
    if isinstance(diagnostics.get("metadata"), dict):
        return copy.deepcopy(diagnostics.get("metadata") or {})
    return copy.deepcopy(diagnostics) if isinstance(diagnostics, dict) else {}


def _extract_text_overflow(target: dict[str, Any]) -> dict[str, Any]:
    checks = target.get("checks") or {}
    diagnostics = (checks.get("text_overflow") or {}).get("diagnostics") or {}
    if isinstance(diagnostics.get("text_overflow"), dict):
        return copy.deepcopy(diagnostics.get("text_overflow") or {})
    return copy.deepcopy(diagnostics) if isinstance(diagnostics, dict) else {}


def _extract_check_mismatches(target: dict[str, Any], check_id: str) -> list[Any]:
    checks = target.get("checks") or {}
    diagnostics = (checks.get(check_id) or {}).get("diagnostics") or {}
    mismatches = diagnostics.get("mismatches")
    return copy.deepcopy(mismatches) if isinstance(mismatches, list) else []


def _rect_area(rect: dict[str, float] | None) -> float:
    if rect is None:
        return 0.0
    return max(0.0, float(rect.get("width") or 0.0)) * max(0.0, float(rect.get("height") or 0.0))


def _rect_contains(outer: dict[str, float], inner: dict[str, float], tolerance: float = 0.0) -> bool:
    return (
        inner["left"] >= outer["left"] - tolerance
        and inner["top"] >= outer["top"] - tolerance
        and inner["right"] <= outer["right"] + tolerance
        and inner["bottom"] <= outer["bottom"] + tolerance
    )


def _rect_intersection(a: dict[str, float], b: dict[str, float]) -> dict[str, float] | None:
    left = max(a["left"], b["left"])
    top = max(a["top"], b["top"])
    right = min(a["right"], b["right"])
    bottom = min(a["bottom"], b["bottom"])
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": width,
        "height": height,
    }


def _rect_center_x(rect: dict[str, float]) -> float:
    return float(rect["left"] + rect["width"] / 2.0)


def _rect_center_y(rect: dict[str, float]) -> float:
    return float(rect["top"] + rect["height"] / 2.0)


def _cluster_layout_targets(
    targets: list[dict[str, Any]],
    *,
    axis: str,
    band_tolerance: float = 24.0,
) -> list[list[dict[str, Any]]]:
    if axis not in {"row", "column"}:
        return []
    keyed: list[dict[str, Any]] = []
    for target in targets:
        bounds = target.get("bounds")
        if not isinstance(bounds, dict):
            continue
        center = _rect_center_y(bounds) if axis == "row" else _rect_center_x(bounds)
        keyed.append({**target, "_cluster_center": center})
    if not keyed:
        return []
    keyed.sort(key=lambda item: float(item["_cluster_center"]))
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_center = 0.0
    for item in keyed:
        if not current:
            current = [item]
            current_center = float(item["_cluster_center"])
            continue
        if abs(float(item["_cluster_center"]) - current_center) <= band_tolerance:
            current.append(item)
            current_center = sum(float(entry["_cluster_center"]) for entry in current) / len(current)
            continue
        clusters.append(current)
        current = [item]
        current_center = float(item["_cluster_center"])
    if current:
        clusters.append(current)
    if axis == "row":
        for cluster in clusters:
            cluster.sort(key=lambda item: float((item.get("bounds") or {}).get("left") or 0.0))
    else:
        for cluster in clusters:
            cluster.sort(key=lambda item: float((item.get("bounds") or {}).get("top") or 0.0))
    return clusters


def _neighbor_gaps(cluster: list[dict[str, Any]], *, axis: str) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for left_item, right_item in zip(cluster, cluster[1:]):
        left_bounds = left_item.get("bounds") or {}
        right_bounds = right_item.get("bounds") or {}
        if axis == "row":
            gap_value = float(right_bounds["left"] - left_bounds["right"])
        else:
            gap_value = float(right_bounds["top"] - left_bounds["bottom"])
        gaps.append(
            {
                "axis": axis,
                "first_target_id": left_item["target_id"],
                "second_target_id": right_item["target_id"],
                "gap": gap_value,
            }
        )
    return gaps


def _gap_warning_from_cluster(
    cluster: list[dict[str, Any]],
    *,
    axis: str,
    tolerance_px: float,
    ratio_threshold: float,
) -> dict[str, Any] | None:
    gaps = [gap for gap in _neighbor_gaps(cluster, axis=axis) if gap["gap"] >= 0]
    if len(gaps) < 2:
        return None
    values = [gap["gap"] for gap in gaps]
    max_gap = max(values)
    min_gap = min(values)
    difference = max_gap - min_gap
    dominant_gap = max_gap if max_gap > 0 else 0.0
    difference_ratio = 0.0 if dominant_gap <= 0 else difference / dominant_gap
    if difference <= tolerance_px or difference_ratio < ratio_threshold:
        return None
    return {
        "type": "sibling-gap-inconsistency",
        "severity": "warning",
        "axis": "horizontal" if axis == "row" else "vertical",
        "target_ids": [item["target_id"] for item in cluster],
        "gaps": gaps,
        "difference": difference,
        "difference_ratio": difference_ratio,
    }


def _alignment_warning_from_cluster(
    cluster: list[dict[str, Any]],
    *,
    axis: str,
    tolerance_px: float,
) -> dict[str, Any] | None:
    if len(cluster) < 2:
        return None
    bounds_list = [item.get("bounds") or {} for item in cluster]
    if axis == "row":
        candidates = {
            "top": max(float(bounds["top"]) for bounds in bounds_list) - min(float(bounds["top"]) for bounds in bounds_list),
            "bottom": max(float(bounds["bottom"]) for bounds in bounds_list) - min(float(bounds["bottom"]) for bounds in bounds_list),
            "center": max(_rect_center_y(bounds) for bounds in bounds_list) - min(_rect_center_y(bounds) for bounds in bounds_list),
        }
        layout_axis = "horizontal"
    else:
        candidates = {
            "left": max(float(bounds["left"]) for bounds in bounds_list) - min(float(bounds["left"]) for bounds in bounds_list),
            "right": max(float(bounds["right"]) for bounds in bounds_list) - min(float(bounds["right"]) for bounds in bounds_list),
            "center": max(_rect_center_x(bounds) for bounds in bounds_list) - min(_rect_center_x(bounds) for bounds in bounds_list),
        }
        layout_axis = "vertical"
    anchor, drift = min(candidates.items(), key=lambda item: item[1])
    if drift <= tolerance_px:
        return None
    return {
        "type": "alignment-drift",
        "severity": "warning",
        "axis": layout_axis,
        "anchor": anchor,
        "target_ids": [item["target_id"] for item in cluster],
        "drift": drift,
    }


def _flex_distribution_warning_from_cluster(
    cluster: list[dict[str, Any]],
    *,
    axis: str,
    ratio_threshold: float,
    cross_axis_tolerance_px: float,
) -> dict[str, Any] | None:
    if len(cluster) < 2:
        return None
    bounds_list = [item.get("bounds") or {} for item in cluster]
    primary_sizes = [float(bounds["width"] if axis == "row" else bounds["height"]) for bounds in bounds_list]
    secondary_sizes = [float(bounds["height"] if axis == "row" else bounds["width"]) for bounds in bounds_list]
    min_primary = min(primary_sizes)
    max_primary = max(primary_sizes)
    if min_primary <= 0:
        return None
    if (max(primary_sizes) / min_primary) < ratio_threshold:
        return None
    if (max(secondary_sizes) - min(secondary_sizes)) > cross_axis_tolerance_px:
        return None
    return {
        "type": "unexpected-flex-distribution",
        "severity": "warning",
        "axis": "horizontal" if axis == "row" else "vertical",
        "target_ids": [item["target_id"] for item in cluster],
        "sizes": primary_sizes,
        "ratio": max_primary / min_primary,
    }


def _vertical_rhythm_warning(
    targets: list[dict[str, Any]],
    *,
    tolerance_px: float,
    ratio_threshold: float,
) -> dict[str, Any] | None:
    ordered = [
        item
        for item in sorted(
            targets,
            key=lambda entry: float(((entry.get("bounds") or {}).get("top") or 0.0)),
        )
        if isinstance(item.get("bounds"), dict)
    ]
    gaps = [gap for gap in _neighbor_gaps(ordered, axis="column") if gap["gap"] >= 0]
    if len(gaps) < 2:
        return None
    values = [gap["gap"] for gap in gaps]
    max_gap = max(values)
    min_gap = min(values)
    difference = max_gap - min_gap
    dominant_gap = max_gap if max_gap > 0 else 0.0
    difference_ratio = 0.0 if dominant_gap <= 0 else difference / dominant_gap
    if difference <= tolerance_px or difference_ratio < ratio_threshold:
        return None
    return {
        "type": "vertical-rhythm-drift",
        "severity": "warning",
        "target_ids": [item["target_id"] for item in ordered],
        "gaps": gaps,
        "difference": difference,
        "difference_ratio": difference_ratio,
    }


def _layout_expectation_mismatches(rect: dict[str, float] | None, expected_layout: Any) -> list[dict[str, Any]]:
    if rect is None or not isinstance(expected_layout, dict):
        return []
    mismatches: list[dict[str, Any]] = []
    direct_fields = {
        "width": rect["width"],
        "height": rect["height"],
        "left": rect["left"],
        "top": rect["top"],
        "right": rect["right"],
        "bottom": rect["bottom"],
    }
    for key, actual in direct_fields.items():
        expected = _coerce_float(expected_layout.get(key))
        if expected is not None and abs(actual - expected) > 0.5:
            mismatches.append({"field": key, "expected": expected, "actual": actual})
    threshold_fields = {
        "min_width": ("width", lambda actual, expected: actual >= expected),
        "max_width": ("width", lambda actual, expected: actual <= expected),
        "min_height": ("height", lambda actual, expected: actual >= expected),
        "max_height": ("height", lambda actual, expected: actual <= expected),
    }
    for key, (field, comparator) in threshold_fields.items():
        expected = _coerce_float(expected_layout.get(key))
        actual = direct_fields[field]
        if expected is not None and not comparator(actual, expected):
            mismatches.append({"field": key, "expected": expected, "actual": actual})
    return mismatches


def _rects_match(a: dict[str, float] | None, b: dict[str, float] | None, tolerance: float = 1.0) -> bool:
    if a is None or b is None:
        return False
    return all(abs(float(a[key]) - float(b[key])) <= tolerance for key in ("left", "top", "right", "bottom"))


def _union_rect(rects: list[dict[str, float]]) -> dict[str, float] | None:
    if not rects:
        return None
    left = min(rect["left"] for rect in rects)
    top = min(rect["top"] for rect in rects)
    right = max(rect["right"] for rect in rects)
    bottom = max(rect["bottom"] for rect in rects)
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
    }


def _detect_edge_gutter_warnings(
    target_summaries: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not bool(config.get("check_edge_gutter_balance", True)):
        return []
    candidates = [
        target
        for target in target_summaries
        if isinstance(target.get("bounds"), dict)
        and isinstance(target.get("root_bounds"), dict)
        and (target.get("check_statuses") or {}).get("visibility") == "passed"
    ]
    if not candidates:
        return []
    reference_root = candidates[0]["root_bounds"]
    if any(not _rects_match(reference_root, target["root_bounds"]) for target in candidates[1:]):
        return []
    content_bounds = _union_rect([target["bounds"] for target in candidates])
    if content_bounds is None:
        return []
    left_gutter = max(0.0, float(content_bounds["left"] - reference_root["left"]))
    right_gutter = max(0.0, float(reference_root["right"] - content_bounds["right"]))
    difference = abs(left_gutter - right_gutter)
    dominant_gutter = max(left_gutter, right_gutter)
    difference_ratio = 0.0 if dominant_gutter <= 0 else difference / dominant_gutter
    if difference <= float(config.get("edge_gutter_balance_tolerance_px") or 0.0):
        return []
    if difference_ratio < float(config.get("edge_gutter_ratio_threshold") or 0.0):
        return []
    return [
        {
            "type": "edge-gutter-imbalance",
            "severity": "warning",
            "axis": "horizontal",
            "target_ids": [target["target_id"] for target in candidates],
            "root_bounds": copy.deepcopy(reference_root),
            "content_bounds": copy.deepcopy(content_bounds),
            "left_gutter": left_gutter,
            "right_gutter": right_gutter,
            "difference": difference,
            "difference_ratio": difference_ratio,
            "message": (
                "Visible content gutters are not balanced across the horizontal axis. "
                "Review whether the asymmetry is intentional."
            ),
        }
    ]


def _detect_native_group_warnings(
    target_summaries: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = [
        target
        for target in target_summaries
        if isinstance(target.get("bounds"), dict)
        and isinstance(target.get("root_bounds"), dict)
        and target.get("status") == "passed"
        and (target.get("check_statuses") or {}).get("visibility") == "passed"
    ]
    if len(candidates) < 2:
        return []
    warnings: list[dict[str, Any]] = []
    row_clusters = _cluster_layout_targets(candidates, axis="row")
    column_clusters = _cluster_layout_targets(candidates, axis="column")
    if bool(config.get("check_sibling_gap_consistency", True)):
        gap_tolerance = float(config.get("sibling_gap_tolerance_px") or 0.0)
        gap_ratio = float(config.get("sibling_gap_ratio_threshold") or 0.0)
        for cluster in row_clusters:
            warning = _gap_warning_from_cluster(
                cluster,
                axis="row",
                tolerance_px=gap_tolerance,
                ratio_threshold=gap_ratio,
            )
            if warning:
                warnings.append(warning)
        for cluster in column_clusters:
            warning = _gap_warning_from_cluster(
                cluster,
                axis="column",
                tolerance_px=gap_tolerance,
                ratio_threshold=gap_ratio,
            )
            if warning:
                warnings.append(warning)
    if bool(config.get("check_alignment_drift", True)):
        alignment_tolerance = float(config.get("alignment_drift_tolerance_px") or 0.0)
        for cluster in row_clusters:
            warning = _alignment_warning_from_cluster(cluster, axis="row", tolerance_px=alignment_tolerance)
            if warning:
                warnings.append(warning)
        for cluster in column_clusters:
            warning = _alignment_warning_from_cluster(cluster, axis="column", tolerance_px=alignment_tolerance)
            if warning:
                warnings.append(warning)
    if bool(config.get("check_vertical_rhythm", True)):
        for cluster in column_clusters:
            if len(cluster) < 3:
                continue
            warning = _vertical_rhythm_warning(
                cluster,
                tolerance_px=float(config.get("vertical_rhythm_tolerance_px") or 0.0),
                ratio_threshold=float(config.get("vertical_rhythm_ratio_threshold") or 0.0),
            )
            if warning:
                warnings.append(warning)
    if bool(config.get("check_flex_distribution", True)):
        ratio_threshold = float(config.get("flex_distribution_ratio_threshold") or 0.0)
        cross_axis_tolerance = float(config.get("flex_cross_axis_tolerance_px") or 0.0)
        for cluster in row_clusters:
            warning = _flex_distribution_warning_from_cluster(
                cluster,
                axis="row",
                ratio_threshold=ratio_threshold,
                cross_axis_tolerance_px=cross_axis_tolerance,
            )
            if warning:
                warnings.append(warning)
        for cluster in column_clusters:
            warning = _flex_distribution_warning_from_cluster(
                cluster,
                axis="column",
                ratio_threshold=ratio_threshold,
                cross_axis_tolerance_px=cross_axis_tolerance,
            )
            if warning:
                warnings.append(warning)
    return warnings


def _detect_touch_target_warnings(
    target_summaries: list[dict[str, Any]],
    validation_targets: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not bool(config.get("check_touch_targets", True)):
        return []
    target_specs = {target["target_id"]: target for target in validation_targets}
    min_width = float(config.get("touch_target_min_width_px") or 0.0)
    min_height = float(config.get("touch_target_min_height_px") or 0.0)
    warnings: list[dict[str, Any]] = []
    for target in target_summaries:
        bounds = target.get("bounds")
        if not isinstance(bounds, dict):
            continue
        target_spec = target_specs.get(target["target_id"]) or {}
        interactions = list(target_spec.get("interactions") or [])
        if not interactions:
            continue
        if float(bounds["width"]) >= min_width and float(bounds["height"]) >= min_height:
            continue
        warnings.append(
            {
                "type": "touch-target-too-small",
                "severity": "warning",
                "target_id": target["target_id"],
                "bounds": copy.deepcopy(bounds),
                "min_width": min_width,
                "min_height": min_height,
                "interactions": interactions,
            }
        )
    return warnings


def _finalize_native_layout_audit(
    run_paths: dict[str, Path],
    case: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    report_path = _resolve_native_layout_audit_report_path(run_paths, case)
    payload = {
        "schema_version": 1,
        "audit_type": "native-layout-audit",
        "case_id": case.get("id"),
        "runner": case.get("runner"),
        "generated_at": now_iso(),
        "ok": result.get("status") == "passed",
        "status": result.get("status"),
        "reason": result.get("reason"),
        "message": result.get("message"),
        "issue_count": int(result.get("issue_count") or 0),
        "issues": copy.deepcopy(result.get("issues") or []),
        "warning_count": int(result.get("warning_count") or 0),
        "warnings": copy.deepcopy(result.get("warnings") or []),
        "target_count": int(result.get("target_count") or 0),
        "validated_target_count": int(result.get("validated_target_count") or 0),
        "checked_pairs": int(result.get("checked_pairs") or 0),
        "source_report_path": result.get("source_report_path"),
        "targets": copy.deepcopy(result.get("targets") or []),
        "config": {
            "required_checks": list((case.get("native_layout_audit") or {}).get("required_checks") or []),
            "check_pair_overlap": bool((case.get("native_layout_audit") or {}).get("check_pair_overlap", True)),
            "pair_overlap_tolerance_px": float((case.get("native_layout_audit") or {}).get("pair_overlap_tolerance_px", 2.0)),
            "pair_overlap_ratio_threshold": float((case.get("native_layout_audit") or {}).get("pair_overlap_ratio_threshold", 0.04)),
            "check_edge_gutter_balance": bool((case.get("native_layout_audit") or {}).get("check_edge_gutter_balance", True)),
            "edge_gutter_balance_tolerance_px": float((case.get("native_layout_audit") or {}).get("edge_gutter_balance_tolerance_px", 8.0)),
            "edge_gutter_ratio_threshold": float((case.get("native_layout_audit") or {}).get("edge_gutter_ratio_threshold", 0.25)),
            "check_sibling_gap_consistency": bool((case.get("native_layout_audit") or {}).get("check_sibling_gap_consistency", True)),
            "sibling_gap_tolerance_px": float((case.get("native_layout_audit") or {}).get("sibling_gap_tolerance_px", 8.0)),
            "sibling_gap_ratio_threshold": float((case.get("native_layout_audit") or {}).get("sibling_gap_ratio_threshold", 0.3)),
            "check_alignment_drift": bool((case.get("native_layout_audit") or {}).get("check_alignment_drift", True)),
            "alignment_drift_tolerance_px": float((case.get("native_layout_audit") or {}).get("alignment_drift_tolerance_px", 8.0)),
            "check_vertical_rhythm": bool((case.get("native_layout_audit") or {}).get("check_vertical_rhythm", True)),
            "vertical_rhythm_tolerance_px": float((case.get("native_layout_audit") or {}).get("vertical_rhythm_tolerance_px", 12.0)),
            "vertical_rhythm_ratio_threshold": float((case.get("native_layout_audit") or {}).get("vertical_rhythm_ratio_threshold", 0.3)),
            "check_touch_targets": bool((case.get("native_layout_audit") or {}).get("check_touch_targets", True)),
            "touch_target_min_width_px": float((case.get("native_layout_audit") or {}).get("touch_target_min_width_px", 44.0)),
            "touch_target_min_height_px": float((case.get("native_layout_audit") or {}).get("touch_target_min_height_px", 44.0)),
            "check_flex_distribution": bool((case.get("native_layout_audit") or {}).get("check_flex_distribution", True)),
            "flex_distribution_ratio_threshold": float((case.get("native_layout_audit") or {}).get("flex_distribution_ratio_threshold", 1.8)),
            "flex_cross_axis_tolerance_px": float((case.get("native_layout_audit") or {}).get("flex_cross_axis_tolerance_px", 16.0)),
            "fail_on_missing_bounds": bool((case.get("native_layout_audit") or {}).get("fail_on_missing_bounds", True)),
            "require_style_tokens": bool((case.get("native_layout_audit") or {}).get("require_style_tokens", True)),
        },
    }
    _write_json(report_path, payload)
    result["report_path"] = str(report_path)
    return result


def _validate_native_layout_audit(run_paths: dict[str, Path], case: dict[str, Any]) -> dict[str, Any]:
    config = case.get("native_layout_audit") or {}
    if case.get("runner") not in NATIVE_LAYOUT_AUDIT_RUNNERS or not config.get("enabled"):
        return {
            "enabled": False,
            "status": "not_enabled",
            "report_path": None,
        }
    source_report_path = _resolve_semantic_report_path(run_paths, case)
    base_result = {
        "enabled": True,
        "source_report_path": str(source_report_path),
        "issues": [],
        "issue_count": 0,
        "warnings": [],
        "warning_count": 0,
        "target_count": 0,
        "validated_target_count": 0,
        "checked_pairs": 0,
        "targets": [],
    }
    unsupported_required_checks = list(config.get("unsupported_required_checks") or [])
    if unsupported_required_checks:
        return _finalize_native_layout_audit(
            run_paths,
            case,
            {
                **base_result,
                "status": "failed",
                "reason": "unsupported_required_checks",
                "message": (
                    f"Native layout audit checks {', '.join(unsupported_required_checks)} are not supported by "
                    f"runner `{case.get('runner')}`."
                ),
            },
        )
    if not (case.get("semantic_assertions") or {}).get("enabled"):
        return _finalize_native_layout_audit(
            run_paths,
            case,
            {
                **base_result,
                "status": "failed",
                "reason": "semantic_assertions_not_enabled",
                "message": f"Native layout audit requires `semantic_assertions` for case {case['id']}.",
            },
        )
    if not source_report_path.exists():
        return _finalize_native_layout_audit(
            run_paths,
            case,
            {
                **base_result,
                "status": "failed",
                "reason": "missing_semantic_report",
                "message": f"Semantic assertions report is missing for native layout audit case {case['id']}.",
            },
        )
    try:
        payload = _load_json(source_report_path, strict=True, purpose="native layout audit semantic report")
    except ValueError as exc:
        return _finalize_native_layout_audit(
            run_paths,
            case,
            {
                **base_result,
                "status": "failed",
                "reason": "invalid_semantic_report",
                "message": str(exc),
            },
        )
    schema_errors = _semantic_report_schema_errors(payload, case)
    if schema_errors:
        return _finalize_native_layout_audit(
            run_paths,
            case,
            {
                **base_result,
                "status": "failed",
                "reason": "invalid_semantic_report_schema",
                "message": "Semantic assertions report does not match the shared schema.",
                "issues": [{"type": "invalid-semantic-report-schema", "schema_errors": schema_errors}],
                "issue_count": 1,
            },
        )
    targets = _parse_semantic_report_targets(payload)
    semantic_targets = {
        target.get("target_id"): target
        for target in (case.get("semantic_assertions") or {}).get("targets") or []
        if target.get("target_id")
    }
    validation_target_ids = list(semantic_targets.keys()) or list(targets.keys())
    issues: list[dict[str, Any]] = []
    target_summaries: list[dict[str, Any]] = []
    overlap_candidates: list[dict[str, Any]] = []
    checked_pairs = 0
    required_checks = list(config.get("required_checks") or [])
    for target_id in validation_target_ids:
        target_spec = semantic_targets.get(target_id) or {}
        target_payload = targets.get(target_id)
        target_summary = {
            "target_id": target_id,
            "status": target_payload.get("status") if target_payload else "missing",
            "bounds": None,
            "root_bounds": None,
            "style_tokens": {},
            "check_statuses": {},
        }
        if target_payload is None:
            issues.append(
                {
                    "type": "missing-target",
                    "target_id": target_id,
                    "message": f"Semantic report does not include target `{target_id}`.",
                }
            )
            target_summaries.append(target_summary)
            continue
        checks = target_payload.get("checks") or {}
        target_summary["check_statuses"] = {
            check_id: (check.get("status") or "unknown")
            for check_id, check in checks.items()
        }
        for check_id in required_checks:
            if check_id not in checks:
                issues.append(
                    {
                        "type": "missing-required-check",
                        "target_id": target_id,
                        "check_id": check_id,
                        "message": f"Native layout audit expected check `{check_id}` for target `{target_id}`.",
                    }
                )
        bounds = _extract_semantic_target_bounds(target_payload)
        root_bounds = _extract_semantic_root_bounds(target_payload)
        style_tokens = _extract_semantic_style_tokens(target_payload)
        target_summary["bounds"] = copy.deepcopy(bounds)
        target_summary["root_bounds"] = copy.deepcopy(root_bounds)
        target_summary["style_tokens"] = copy.deepcopy(style_tokens)
        if bounds is None and config.get("fail_on_missing_bounds"):
            issues.append(
                {
                    "type": "missing-layout-bounds",
                    "target_id": target_id,
                    "message": f"Target `{target_id}` has no usable layout bounds in the semantic report.",
                }
            )
        elif bounds is not None and (bounds["width"] <= 0 or bounds["height"] <= 0):
            issues.append(
                {
                    "type": "invalid-layout-bounds",
                    "target_id": target_id,
                    "bounds": copy.deepcopy(bounds),
                    "message": f"Target `{target_id}` has non-positive layout bounds.",
                }
            )
        layout_mismatches = _layout_expectation_mismatches(bounds, target_spec.get("expected_layout"))
        if layout_mismatches:
            issues.append(
                {
                    "type": "layout-expectation-mismatch",
                    "target_id": target_id,
                    "mismatches": layout_mismatches,
                }
            )
        visibility_status = (checks.get("visibility") or {}).get("status") or "unknown"
        if "visibility" in required_checks and visibility_status != "passed":
            issues.append(
                {
                    "type": "visibility",
                    "target_id": target_id,
                    "status": visibility_status,
                }
            )
        clipping = _extract_semantic_clipping(target_payload)
        clipped = bool(clipping.get("clipped") is True)
        if "overflow_clipping" in required_checks and not bool(target_spec.get("allow_clipping")):
            if clipped or (checks.get("overflow_clipping") or {}).get("status") not in {None, "passed"}:
                issues.append(
                    {
                        "type": "clipping",
                        "target_id": target_id,
                        "clipping": clipping,
                    }
                )
            if bounds is not None and root_bounds is not None and not _rect_contains(
                root_bounds,
                bounds,
                tolerance=float(config.get("pair_overlap_tolerance_px") or 0.0),
            ):
                issues.append(
                    {
                        "type": "root-overflow",
                        "target_id": target_id,
                        "bounds": copy.deepcopy(bounds),
                        "root_bounds": copy.deepcopy(root_bounds),
                    }
                )
        metadata = _extract_semantic_metadata(target_payload)
        occluded = bool(metadata.get("occluded") is True)
        if "occlusion" in required_checks and not bool(target_spec.get("allow_occlusion")):
            if occluded or (checks.get("occlusion") or {}).get("status") not in {None, "passed"}:
                issues.append(
                    {
                        "type": "occlusion",
                        "target_id": target_id,
                        "metadata": metadata,
                    }
                )
        if "computed_styles" in required_checks:
            mismatches = _extract_check_mismatches(target_payload, "computed_styles")
            if mismatches:
                issues.append(
                    {
                        "type": "style-mismatch",
                        "target_id": target_id,
                        "mismatches": mismatches,
                    }
                )
            elif not style_tokens and bool(config.get("require_style_tokens")):
                issues.append(
                    {
                        "type": "missing-style-data",
                        "target_id": target_id,
                        "message": f"Target `{target_id}` does not expose computed style tokens for native layout audit.",
                    }
                )
            elif (checks.get("computed_styles") or {}).get("status") not in {None, "passed"}:
                issues.append(
                    {
                        "type": "computed-styles",
                        "target_id": target_id,
                        "status": (checks.get("computed_styles") or {}).get("status"),
                    }
                )
        if "text_overflow" in required_checks:
            overflow = _extract_text_overflow(target_payload)
            truncated = bool(overflow.get("truncated") is True)
            if truncated or (
                (checks.get("text_overflow") or {}).get("status") not in {None, "passed"}
                and not bool(target_spec.get("allow_text_truncation"))
            ):
                issues.append(
                    {
                        "type": "text-overflow",
                        "target_id": target_id,
                        "text_overflow": overflow,
                    }
                )
        if bounds is not None and visibility_status == "passed":
            overlap_candidates.append(
                {
                    "target_id": target_id,
                    "bounds": bounds,
                    "allow_occlusion": bool(target_spec.get("allow_occlusion")),
                }
            )
        target_summaries.append(target_summary)
    if bool(config.get("check_pair_overlap")):
        tolerance = float(config.get("pair_overlap_tolerance_px") or 0.0)
        ratio_threshold = float(config.get("pair_overlap_ratio_threshold") or 0.0)
        for index, left_target in enumerate(overlap_candidates):
            for right_target in overlap_candidates[index + 1 :]:
                if left_target["allow_occlusion"] or right_target["allow_occlusion"]:
                    continue
                checked_pairs += 1
                intersection = _rect_intersection(left_target["bounds"], right_target["bounds"])
                if intersection is None:
                    continue
                if intersection["width"] <= tolerance or intersection["height"] <= tolerance:
                    continue
                smaller_area = min(_rect_area(left_target["bounds"]), _rect_area(right_target["bounds"]))
                overlap_ratio = 0.0 if smaller_area <= 0 else _rect_area(intersection) / smaller_area
                if overlap_ratio < ratio_threshold:
                    continue
                issues.append(
                    {
                        "type": "pair-overlap",
                        "target_ids": [left_target["target_id"], right_target["target_id"]],
                        "intersection": intersection,
                        "overlap_ratio": overlap_ratio,
                    }
                )
    warnings = _detect_edge_gutter_warnings(target_summaries, config)
    warnings.extend(_detect_native_group_warnings(target_summaries, config))
    warnings.extend(_detect_touch_target_warnings(target_summaries, list(semantic_targets.values()), config))
    status = "failed" if issues else "warning" if warnings else "passed"
    return _finalize_native_layout_audit(
        run_paths,
        case,
        {
            **base_result,
            "status": status,
            "reason": "layout_issues_detected" if issues else "layout_warnings_detected" if warnings else None,
            "message": (
                f"Native layout audit detected {len(issues)} issues for case {case['id']}."
                if issues
                else f"Native layout audit detected {len(warnings)} warnings for case {case['id']}."
                if warnings
                else None
            ),
            "issues": issues,
            "issue_count": len(issues),
            "warnings": warnings,
            "warning_count": len(warnings),
            "target_count": len(validation_target_ids),
            "validated_target_count": len(targets),
            "checked_pairs": checked_pairs,
            "targets": target_summaries,
        },
    )


def _start_run(workspace: str | Path, mode: str, target_id: str, workstream_id: str | None = None) -> dict[str, Any]:
    paths = _ensure_workspace_paths(workspace, workstream_id=workstream_id)
    recipes = read_verification_recipes(workspace, workstream_id=workstream_id)
    task_payload = current_task(workspace)
    case_ids = _resolve_case_ids(recipes, mode, target_id)
    slow_after = max((_slow_after_seconds(_case_by_id(recipes, case_id)) for case_id in case_ids), default=10)
    created_at_ns = time.time_ns()
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_paths = _verification_run_paths(workspace, run_id, workstream_id=workstream_id)
    run_paths["run_root"].mkdir(parents=True, exist_ok=True)
    run_paths["transient_auth_dir"].mkdir(parents=True, exist_ok=True)
    run_paths["artifacts_dir"].mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "run_id": run_id,
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": paths["current_workstream_id"],
        "task_id": task_payload.get("task_id") if task_payload else None,
        "external_issue": copy.deepcopy(task_payload.get("external_issue")) if task_payload else None,
        "mode": mode,
        "target_id": target_id,
        "case_ids": case_ids,
        "status": "queued",
        "health": "queued",
        "slow_after_seconds": slow_after,
        "created_at": now_iso(),
        "created_at_ns": created_at_ns,
        "started_at": None,
        "started_at_ns": None,
        "completed_at": None,
        "completed_at_ns": None,
        "last_event_at": None,
        "event_count": 0,
        "pid": None,
        "recipes_path": str(Path(paths["verification_recipes"])),
        "run_root": str(run_paths["run_root"]),
        "events_path": str(run_paths["events_jsonl"]),
        "stdout_log_path": str(run_paths["stdout_log"]),
        "stderr_log_path": str(run_paths["stderr_log"]),
        "logcat_log_path": str(run_paths["logcat_log"]),
        "transient_auth_dir": str(run_paths["transient_auth_dir"]),
        "artifacts_dir": str(run_paths["artifacts_dir"]),
        "logcat_pid": None,
        "logcat_case_id": None,
        "summary": {
            "total_cases": len(case_ids),
            "passed_cases": 0,
            "failed_cases": 0,
            "message": "Run queued.",
        },
        "cases": [
            {
                "case_id": case_id,
                "status": "queued",
                "started_at": None,
                "completed_at": None,
                "exit_code": None,
                "runner": _case_by_id(recipes, case_id).get("runner"),
                "baseline": _case_baseline_result(workspace, _case_by_id(recipes, case_id), 0),
                "semantic_summary": {
                    "enabled": bool((_case_by_id(recipes, case_id).get("semantic_assertions") or {}).get("enabled")),
                    "status": "queued",
                },
            }
            for case_id in case_ids
        ],
    }
    _write_run(workspace, payload, workstream_id=workstream_id)
    _append_event(workspace, run_id, "run_queued", f"Queued {mode} run for {target_id}.", workstream_id=workstream_id, target_id=target_id, case_ids=case_ids)
    append_analytics_event(
        "verification_run_queued",
        workspace,
        source="verification",
        status="queued",
        workstream_id=paths["current_workstream_id"],
        task_id=payload.get("task_id"),
        run_id=run_id,
        external_issue=copy.deepcopy(payload.get("external_issue")),
        payload={"mode": mode, "target_id": target_id, "case_ids": case_ids},
    )

    env = os.environ.copy()
    env.setdefault("AGENTIUX_DEV_PLUGIN_ROOT", str(plugin_root()))
    script_args = ["worker", "--workspace", str(Path(workspace).expanduser().resolve()), "--run-id", run_id]
    if workstream_id:
        script_args.extend(["--workstream-id", workstream_id])
    process = start_logged_python_process(
        plugin_root() / "scripts" / "agentiux_dev_verification.py",
        run_paths["stdout_log"],
        run_paths["stderr_log"],
        script_args=script_args,
        env=env,
        start_new_session=True,
    )
    payload["pid"] = process.pid
    payload["status"] = "running"
    payload["started_at"] = now_iso()
    payload["started_at_ns"] = time.time_ns()
    payload["summary"]["message"] = "Run started."
    _write_run(workspace, payload, workstream_id=workstream_id)
    _append_event(workspace, run_id, "run_started", f"Started {mode} run for {target_id}.", workstream_id=workstream_id, pid=process.pid)
    append_analytics_event(
        "verification_run_started",
        workspace,
        source="verification",
        status="running",
        workstream_id=paths["current_workstream_id"],
        task_id=payload.get("task_id"),
        run_id=run_id,
        external_issue=copy.deepcopy(payload.get("external_issue")),
        payload={"mode": mode, "target_id": target_id, "pid": process.pid},
    )
    return payload


def start_verification_case(workspace: str | Path, case_id: str, wait: bool = False, workstream_id: str | None = None) -> dict[str, Any]:
    run = _start_run(workspace, "case", case_id, workstream_id=workstream_id)
    return wait_for_verification_run(workspace, run["run_id"], workstream_id=workstream_id) if wait else run


def start_verification_suite(workspace: str | Path, suite_id: str, wait: bool = False, workstream_id: str | None = None) -> dict[str, Any]:
    run = _start_run(workspace, "suite", suite_id, workstream_id=workstream_id)
    return wait_for_verification_run(workspace, run["run_id"], workstream_id=workstream_id) if wait else run


def read_verification_run(workspace: str | Path, run_id: str, workstream_id: str | None = None) -> dict[str, Any]:
    return _read_run(workspace, run_id, workstream_id=workstream_id)


def list_verification_runs(workspace: str | Path, limit: int | None = None, workstream_id: str | None = None) -> dict[str, Any]:
    paths = _ensure_workspace_paths(workspace, workstream_id=workstream_id)
    runs = []
    for run_path in Path(paths["verification_runs_dir"]).glob("*/run.json"):
        run = _load_json(run_path, default={}) or {}
        if not run:
            continue
        try:
            run = _read_run(workspace, run["run_id"], workstream_id=workstream_id)
        except Exception:  # noqa: BLE001
            continue
        runs.append(run)
    return _run_summary_payload(workspace, runs, limit, workstream_id=workstream_id)


def read_verification_events(workspace: str | Path, run_id: str, limit: int = 50, workstream_id: str | None = None) -> dict[str, Any]:
    run_paths = _verification_run_paths(workspace, run_id, workstream_id=workstream_id)
    events = _read_events(run_paths, limit=limit)
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": workspace_paths(workspace, workstream_id=workstream_id)["current_workstream_id"],
        "run_id": run_id,
        "events": events,
    }


def read_verification_log_tail(workspace: str | Path, run_id: str, stream: str = "stdout", lines: int = 50, workstream_id: str | None = None) -> dict[str, Any]:
    run_paths = _verification_run_paths(workspace, run_id, workstream_id=workstream_id)
    if stream not in {"stdout", "stderr", "logcat"}:
        raise ValueError(f"Unsupported verification log stream: {stream}")
    if stream == "stdout":
        target = run_paths["stdout_log"]
    elif stream == "stderr":
        target = run_paths["stderr_log"]
    else:
        target = run_paths["logcat_log"]
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": workspace_paths(workspace, workstream_id=workstream_id)["current_workstream_id"],
        "run_id": run_id,
        "stream": stream,
        "path": str(target),
        "lines": _tail_lines(target, lines),
    }


def wait_for_verification_run(workspace: str | Path, run_id: str, timeout_seconds: float = 60.0, workstream_id: str | None = None) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    missing_error: FileNotFoundError | None = None
    while time.time() < deadline:
        try:
            run = read_verification_run(workspace, run_id, workstream_id=workstream_id)
        except FileNotFoundError as exc:
            missing_error = exc
            time.sleep(0.2)
            continue
        if run.get("status") in TERMINAL_RUN_STATUSES:
            return run
        time.sleep(0.2)
    if missing_error is not None:
        raise TimeoutError(f"Verification run did not materialize before timeout: {run_id}") from missing_error
    raise TimeoutError(f"Verification run did not finish before timeout: {run_id}")


def cancel_verification_run(workspace: str | Path, run_id: str, workstream_id: str | None = None) -> dict[str, Any]:
    run = read_verification_run(workspace, run_id, workstream_id=workstream_id)
    if run.get("status") in TERMINAL_RUN_STATUSES:
        return run
    pid = run.get("pid")
    if isinstance(pid, int) and _process_running(pid):
        run["process_stop"] = stop_process(pid)
    logcat_pid = run.get("logcat_pid")
    if isinstance(logcat_pid, int) and _process_running(logcat_pid):
        run["logcat_stop"] = stop_process(logcat_pid)
        run["logcat_pid"] = None
    run["status"] = "cancelled"
    run["completed_at"] = now_iso()
    run["completed_at_ns"] = time.time_ns()
    run["summary"] = {
        **(run.get("summary") or {}),
        "message": "Run cancelled.",
    }
    _write_run(workspace, run, workstream_id=workstream_id)
    _append_event(workspace, run_id, "run_cancelled", "Verification run cancelled.", workstream_id=workstream_id)
    return run


def active_verification_run(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any] | None:
    return list_verification_runs(workspace, workstream_id=workstream_id)["active_run"]


def latest_verification_run(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any] | None:
    return list_verification_runs(workspace, workstream_id=workstream_id)["latest_run"]


def latest_completed_verification_run(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any] | None:
    return list_verification_runs(workspace, workstream_id=workstream_id)["latest_completed_run"]


def verification_summary_counts(workspace: str | Path, workstream_id: str | None = None) -> dict[str, int]:
    all_runs = list_verification_runs(workspace, limit=None, workstream_id=workstream_id)
    run_set = all_runs["recent_runs"]
    return {
        "verification_runs": all_runs["run_count"],
        "active_verification_runs": 1 if all_runs["active_run"] else 0,
        "failed_verification_runs": sum(1 for run in run_set if run.get("status") == "failed"),
        "passed_verification_runs": sum(1 for run in run_set if run.get("status") == "passed"),
    }


def recent_verification_events(workspace: str | Path, limit: int = 10, workstream_id: str | None = None) -> dict[str, Any]:
    runs = list_verification_runs(workspace, limit=1, workstream_id=workstream_id)
    run = runs["active_run"] or runs["latest_run"]
    if run is None:
        return {
            "workspace_path": str(Path(workspace).expanduser().resolve()),
            "workstream_id": workspace_paths(workspace, workstream_id=workstream_id)["current_workstream_id"],
            "run_id": None,
            "events": [],
        }
    return read_verification_events(workspace, run["run_id"], limit=limit, workstream_id=workstream_id)


def _coverage_gap(gap_id: str, title: str, recommendation: str, *, category: str = "verification") -> dict[str, Any]:
    return {
        "gap_id": gap_id,
        "severity": "warning",
        "category": category,
        "title": title,
        "recommendation": recommendation,
    }


def _merge_named_items(base: list[dict[str, Any]], override: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {str(item.get("id")): copy.deepcopy(item) for item in base if item.get("id")}
    ordered_ids = [str(item.get("id")) for item in base if item.get("id")]
    preserve_default_when_blank = {
        "changed_path_globs",
        "host_requirements",
        "feature_ids",
        "surface_ids",
        "routes_or_screens",
    }
    for item in override:
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        if item_id not in merged:
            ordered_ids.append(item_id)
            merged[item_id] = {}
        for key, value in copy.deepcopy(item).items():
            if key in preserve_default_when_blank and (value is None or value == []):
                continue
            merged[item_id][key] = value
    return [merged[item_id] for item_id in ordered_ids]


def _merge_recipe_defaults(default_recipes: dict[str, Any], saved_recipes: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(default_recipes)
    for key, value in saved_recipes.items():
        if key in {"cases", "suites"}:
            continue
        merged[key] = copy.deepcopy(value)
    merged["cases"] = _merge_named_items(default_recipes.get("cases", []), saved_recipes.get("cases", []))
    merged["suites"] = _merge_named_items(default_recipes.get("suites", []), saved_recipes.get("suites", []))
    return merged


def _case_targets_dashboard_surface(case: dict[str, Any]) -> bool:
    signals: list[str] = []
    signals.extend(str(item or "").lower() for item in case.get("surface_ids", []))
    signals.extend(str(item or "").lower() for item in case.get("feature_ids", []))
    signals.extend(str(item or "").lower() for item in case.get("tags", []))
    signals.extend(str(item or "").lower() for item in case.get("routes_or_screens", []))
    signals.extend(str(item or "").lower() for item in case.get("changed_path_globs", []))
    signals.append(str(case.get("id") or "").lower())
    return any("dashboard" in signal or "gui" in signal for signal in signals)


def _case_requires_web_visual_semantics(case: dict[str, Any]) -> bool:
    runner = str(case.get("runner") or "").lower()
    surface_type = str(case.get("surface_type") or "").lower()
    return runner == "playwright-visual" or (surface_type == "web" and runner in VISUAL_RUNNERS)


def _case_command_tokens(case: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for part in case.get("argv") or []:
        tokens.append(str(part).strip().lower())
    shell_command = str(case.get("shell_command") or "").strip().lower()
    if shell_command:
        tokens.extend(shell_command.split())
    return tokens


def _case_runs_release_readiness_dashboard_check(case: dict[str, Any]) -> bool:
    tokens = _case_command_tokens(case)
    if not tokens:
        return False
    return "dashboard-check" in tokens and any("release_readiness.py" in token for token in tokens)


def _case_has_browser_layout_audit(case: dict[str, Any]) -> bool:
    return str(case.get("runner") or "").lower() == "browser-layout-audit"


def _case_has_native_layout_audit(case: dict[str, Any]) -> bool:
    return str(case.get("runner") or "").lower() in NATIVE_LAYOUT_AUDIT_RUNNERS and bool(
        (case.get("native_layout_audit") or {}).get("enabled")
    )


def _case_counts_as_visual_web_coverage(case: dict[str, Any]) -> bool:
    return (
        case.get("runner") in VISUAL_RUNNERS
        or _case_has_browser_layout_audit(case)
        or _case_runs_release_readiness_dashboard_check(case)
    )


def audit_verification_coverage(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    detection = detect_workspace(resolved_workspace)
    paths = workspace_paths(resolved_workspace, workstream_id=workstream_id)
    recipes_path = Path(paths["verification_recipes"])
    catalog_runners = set((_load_verification_helper_catalog().get("runners") or {}).keys())
    default_recipes = _default_recipes(resolved_workspace, workstream_id=workstream_id)
    if recipes_path.exists():
        saved_recipes = _normalize_recipes(_load_json(recipes_path, default={}) or {}, resolved_workspace)
        recipes = _normalize_recipes(_merge_recipe_defaults(default_recipes, saved_recipes), resolved_workspace)
        recipe_source = "saved"
    else:
        recipes = default_recipes
        recipe_source = "detected_fragments"
    cases = recipes.get("cases", [])
    suites = recipes.get("suites", [])
    suite_ids = {suite.get("id") for suite in suites}
    gaps: list[dict[str, Any]] = []
    if not cases:
        gaps.append(
            _coverage_gap(
                "missing-verification-cases",
                "No deterministic verification cases are defined",
                "Add at least one named verification case for the detected workspace surfaces.",
            )
        )
    if cases and "full" not in suite_ids:
        gaps.append(
            _coverage_gap(
                "missing-full-suite",
                "A full verification suite is not defined",
                "Add a `full` suite so the workspace has a stable full-run contract.",
            )
        )
    baseline_policy = recipes.get("baseline_policy") or {}
    if cases and baseline_policy.get("canonical_baselines") != "project_owned":
        gaps.append(
            _coverage_gap(
                "baseline-policy-not-project-owned",
                "Canonical baseline policy is missing or non-project-owned",
                "Set `baseline_policy.canonical_baselines=project_owned` for reproducible CI checks.",
                category="baseline",
            )
        )
    coverage = {
        "plugin": False,
        "dashboard": False,
        "dashboard_visual": False,
        "dashboard_browser_layout_audit": False,
        "web": False,
        "web_visual": False,
        "web_browser_layout_audit": False,
        "mobile": False,
        "mobile_native_layout_audit": False,
        "android": False,
        "android_visual": False,
        "android_native_layout_audit": False,
        "ios": False,
        "backend": False,
    }
    helper_status = _verification_helper_materialization_status(resolved_workspace)
    semantic_case_ids: list[str] = []
    for case in cases:
        case_id = case["id"]
        targeting_signals = bool(case.get("changed_path_globs") or case.get("feature_ids") or case.get("surface_ids") or case.get("routes_or_screens"))
        if not targeting_signals:
            gaps.append(
                _coverage_gap(
                    f"{case_id}-missing-targeting-signals",
                    f"Verification case `{case_id}` cannot be targeted reliably",
                    "Add `changed_path_globs`, `surface_ids`, `feature_ids`, or route/screen selectors.",
                )
            )
        if not case.get("host_requirements"):
            gaps.append(
                _coverage_gap(
                    f"{case_id}-missing-host-requirements",
                    f"Verification case `{case_id}` does not declare host requirements",
                    "Add `host_requirements` so unsupported hosts fail early and deterministically.",
                )
            )
        if case.get("runner") in VISUAL_RUNNERS and not case.get("baseline", {}).get("source_path"):
            gaps.append(
                _coverage_gap(
                    f"{case_id}-missing-baseline-source",
                    f"Visual verification case `{case_id}` has no baseline source path",
                    "Declare a project-owned baseline source path for visual checks.",
                    category="baseline",
                )
            )
        semantic_assertions = case.get("semantic_assertions") or {}
        native_layout_audit = case.get("native_layout_audit") or {}
        if _case_requires_web_visual_semantics(case) and not semantic_assertions.get("enabled"):
            gaps.append(
                _coverage_gap(
                    f"{case_id}-missing-web-semantic-assertions",
                    f"Web visual case `{case_id}` does not enable semantic assertions",
                    (
                        "Enable `semantic_assertions` for Playwright-backed web coverage so clipping, overlap, "
                        "visibility, and computed-style regressions are checked deterministically."
                    ),
                    category="visual",
                )
            )
        if semantic_assertions.get("enabled"):
            semantic_case_ids.append(case_id)
            if not semantic_assertions.get("targets"):
                gaps.append(
                    _coverage_gap(
                        f"{case_id}-missing-semantic-targets",
                        f"Semantic visual case `{case_id}` does not declare targets",
                        "Add `semantic_assertions.targets[]` so deterministic helper checks can resolve stable UI targets.",
                        category="visual",
                    )
                )
            if case.get("runner") not in catalog_runners:
                gaps.append(
                    _coverage_gap(
                        f"{case_id}-semantic-runner-not-cataloged",
                        f"Semantic visual case `{case_id}` uses a runner without a helper bundle entrypoint",
                        f"Use a supported runner or extend the helper bundle catalog for `{case.get('runner')}`.",
                        category="visual",
                    )
                )
            unsupported_required_checks = semantic_assertions.get("unsupported_required_checks") or []
            if unsupported_required_checks:
                gaps.append(
                    _coverage_gap(
                        f"{case_id}-unsupported-semantic-checks",
                        f"Semantic visual case `{case_id}` requires checks the runner cannot produce",
                        (
                            f"Remove or replace unsupported checks for `{case.get('runner')}`: "
                            f"{', '.join(unsupported_required_checks)}."
                        ),
                        category="visual",
                    )
                )
            if _case_requires_web_visual_semantics(case):
                missing_web_checks = [
                    check_id
                    for check_id in MANDATORY_WEB_VISUAL_SEMANTIC_CHECKS
                    if check_id not in set(semantic_assertions.get("required_checks") or [])
                ]
                if missing_web_checks:
                    gaps.append(
                        _coverage_gap(
                            f"{case_id}-missing-core-web-semantic-checks",
                            f"Web visual case `{case_id}` is missing mandatory layout semantic checks",
                            (
                                "Require the full web semantic guardrail set for visual checks: "
                                f"{', '.join(missing_web_checks)}."
                            ),
                            category="visual",
                        )
                    )
            if case.get("runner") in NATIVE_LAYOUT_AUDIT_RUNNERS:
                missing_native_checks = [
                    check_id
                    for check_id in MANDATORY_NATIVE_VISUAL_SEMANTIC_CHECKS
                    if check_id not in set(semantic_assertions.get("required_checks") or [])
                ]
                if missing_native_checks:
                    gaps.append(
                        _coverage_gap(
                            f"{case_id}-missing-core-native-semantic-checks",
                            f"Native visual case `{case_id}` is missing mandatory mobile semantic checks",
                            (
                                "Require the shared native semantic guardrail set for layout audit coverage: "
                                f"{', '.join(missing_native_checks)}."
                            ),
                            category="visual",
                        )
                    )
        if case.get("runner") in NATIVE_LAYOUT_AUDIT_RUNNERS and not native_layout_audit.get("enabled"):
            gaps.append(
                _coverage_gap(
                    f"{case_id}-missing-native-layout-audit",
                    f"Native visual case `{case_id}` does not enable native layout audit",
                    "Enable `native_layout_audit` so overlap, clipping, computed-style, and bounds regressions are checked from semantic reports.",
                    category="visual",
                )
            )
        if native_layout_audit.get("enabled") and not semantic_assertions.get("enabled"):
            gaps.append(
                _coverage_gap(
                    f"{case_id}-native-layout-audit-missing-semantic-assertions",
                    f"Native layout audit case `{case_id}` has no semantic assertions input",
                    "Enable `semantic_assertions` so the native layout audit has deterministic layout data to validate.",
                    category="visual",
                )
            )
        if native_layout_audit.get("enabled") and native_layout_audit.get("unsupported_required_checks"):
            gaps.append(
                _coverage_gap(
                    f"{case_id}-native-layout-audit-unsupported-checks",
                    f"Native layout audit case `{case_id}` requires checks the runner cannot produce",
                    (
                        f"Remove or replace unsupported native audit checks for `{case.get('runner')}`: "
                        f"{', '.join(native_layout_audit.get('unsupported_required_checks') or [])}."
                    ),
                    category="visual",
                )
            )
        surface_type = str(case.get("surface_type") or "").lower()
        device = str((case.get("device_or_viewport") or {}).get("device") or "").lower()
        tags = {str(tag).lower() for tag in case.get("tags", [])}
        feature_ids = {str(tag).lower() for tag in case.get("feature_ids", [])}
        if surface_type == "plugin" or "plugin-runtime" in tags or "plugin-runtime" in feature_ids:
            coverage["plugin"] = True
        if _case_targets_dashboard_surface(case):
            coverage["dashboard"] = True
            if _case_counts_as_visual_web_coverage(case):
                coverage["dashboard_visual"] = True
            if _case_has_browser_layout_audit(case) or _case_runs_release_readiness_dashboard_check(case):
                coverage["dashboard_browser_layout_audit"] = True
        if surface_type == "web":
            coverage["web"] = True
            if _case_counts_as_visual_web_coverage(case):
                coverage["web_visual"] = True
            if _case_has_browser_layout_audit(case):
                coverage["web_browser_layout_audit"] = True
        if surface_type == "mobile":
            coverage["mobile"] = True
            if _case_has_native_layout_audit(case):
                coverage["mobile_native_layout_audit"] = True
        if case.get("runner") == "android-compose-screenshot" or "android" in device or surface_type == "android":
            coverage["android"] = True
            coverage["mobile"] = True
            if case.get("runner") in VISUAL_RUNNERS:
                coverage["android_visual"] = True
            if _case_has_native_layout_audit(case):
                coverage["android_native_layout_audit"] = True
                coverage["mobile_native_layout_audit"] = True
        if case.get("runner") == "ios-simulator-capture" or "ios" in device or surface_type == "ios":
            coverage["ios"] = True
            coverage["mobile"] = True
            if _case_has_native_layout_audit(case):
                coverage["mobile_native_layout_audit"] = True
        if surface_type in {"service", "backend", "infra"}:
            coverage["backend"] = True
    profiles = set(detection.get("selected_profiles", []))
    stacks = set(detection.get("detected_stacks", []))
    if "plugin-platform" in profiles and not coverage["plugin"]:
        gaps.append(
            _coverage_gap(
                "missing-plugin-verification",
                "Plugin workspace has no plugin-runtime verification case",
                "Add at least one plugin verification case or fragment for self-hosted plugin behavior.",
            )
        )
    if "local-dashboard" in stacks and not coverage["dashboard_visual"]:
        gaps.append(
            _coverage_gap(
                "missing-dashboard-visual-verification",
                "Local dashboard surface has no visual verification case",
                "Add at least one `playwright-visual` or `browser-layout-audit` case for the dashboard so overlap, clipping, and occlusion regressions are checked deterministically.",
                category="visual",
            )
        )
    if "local-dashboard" in stacks and not coverage["dashboard_browser_layout_audit"]:
        gaps.append(
            _coverage_gap(
                "missing-dashboard-browser-layout-audit",
                "Local dashboard surface has no live browser layout audit case",
                "Add at least one `browser-layout-audit` case for the dashboard so DOM-level overlap and occlusion issues are checked on a real rendered page.",
                category="visual",
            )
        )
    if "web-platform" in profiles and not coverage["web"]:
        gaps.append(
            _coverage_gap(
                "missing-web-verification",
                "Web workspace has no web verification case",
                "Add at least one deterministic web verification case.",
            )
        )
    if "web-platform" in profiles and not coverage["web_visual"]:
        gaps.append(
            _coverage_gap(
                "missing-web-visual-verification",
                "Web workspace has no visual web verification case",
                "Add at least one `playwright-visual` or `browser-layout-audit` web case so deterministic UI regressions are covered.",
                category="visual",
            )
        )
    if "web-platform" in profiles and not coverage["web_browser_layout_audit"]:
        gaps.append(
            _coverage_gap(
                "missing-web-browser-layout-audit",
                "Web workspace has no live browser layout audit case",
                "Add at least one `browser-layout-audit` web case so computed layout regressions are caught on a real rendered page.",
                category="visual",
            )
        )
    if "mobile-platform" in profiles and not coverage["mobile"]:
        gaps.append(
            _coverage_gap(
                "missing-mobile-verification",
                "Mobile workspace has no mobile verification case",
                "Add at least one deterministic mobile verification case.",
            )
        )
    if "mobile-platform" in profiles and not coverage["mobile_native_layout_audit"]:
        gaps.append(
            _coverage_gap(
                "missing-mobile-native-layout-audit",
                "Mobile workspace has no native layout audit coverage",
                "Add at least one `detox-visual` or `android-compose-screenshot` case with `native_layout_audit` enabled so native layout regressions are checked deterministically.",
                category="visual",
            )
        )
    if "android" in stacks and not coverage["android"]:
        gaps.append(
            _coverage_gap(
                "missing-android-verification",
                "Android signals were detected but no Android verification case is defined",
                "Add Android-targeted deterministic verification for emulator, Compose screenshots, or both.",
            )
        )
    if "android" in stacks and not coverage["android_visual"]:
        gaps.append(
            _coverage_gap(
                "missing-android-visual-verification",
                "Android signals were detected but no Android visual verification case is defined",
                "Add `android-compose-screenshot` or Android-targeted `detox-visual` coverage for deterministic UI closeout.",
                category="visual",
            )
        )
    if "android" in stacks and not coverage["android_native_layout_audit"]:
        gaps.append(
            _coverage_gap(
                "missing-android-native-layout-audit",
                "Android signals were detected but no Android native layout audit case is defined",
                "Enable `native_layout_audit` on an Android-targeted `detox-visual` or `android-compose-screenshot` case so overlap, clipping, and style regressions fail deterministically.",
                category="visual",
            )
        )
    if "ios" in stacks and not coverage["ios"]:
        gaps.append(
            _coverage_gap(
                "missing-ios-verification",
                "iOS signals were detected but no iOS verification case is defined",
                "Add simulator-first deterministic verification for iOS surfaces.",
            )
        )
    if any(profile in profiles for profile in {"backend-platform", "local-infra"}) and not coverage["backend"]:
        gaps.append(
            _coverage_gap(
                "missing-backend-verification",
                "Backend or local infra signals were detected but no service-side verification case is defined",
                "Add deterministic smoke or contract verification for backend and infra surfaces.",
            )
        )
    if semantic_case_ids and helper_status["status"] == "not_synced":
        gaps.append(
            _coverage_gap(
                "verification-helper-bundle-not-synced",
                "Semantic visual cases are defined but the helper bundle is not materialized in the repository",
                "Run `sync verification helpers` so projects can import `.verification/helpers`.",
                category="visual",
            )
        )
    if semantic_case_ids and helper_status["status"] == "legacy_location":
        gaps.append(
            _coverage_gap(
                "verification-helper-bundle-legacy-location",
                "A legacy `.agentiux/verification-helpers` helper layout is materialized in the repository",
                "Re-run `sync verification helpers` to replace it with `.verification/helpers`.",
                category="visual",
            )
        )
    if semantic_case_ids and helper_status["status"] == "version_drift":
        gaps.append(
            _coverage_gap(
                "verification-helper-bundle-version-drift",
                "A stale verification helper bundle version is materialized in the repository",
                "Re-run `sync verification helpers` to materialize the current neutral helper bundle version.",
                category="visual",
            )
        )
    return {
        "workspace_path": str(resolved_workspace),
        "workstream_id": paths["current_workstream_id"],
        "recipe_source": recipe_source,
        "detected_stacks": detection.get("detected_stacks", []),
        "selected_profiles": detection.get("selected_profiles", []),
        "case_count": len(cases),
        "suite_count": len(suites),
        "coverage": coverage,
        "helper_bundle": helper_status,
        "gaps": gaps,
        "warning_count": len(gaps),
        "status": "warning" if gaps else "clean",
    }


def follow_verification_run(
    workspace: str | Path,
    run_id: str,
    *,
    timeout_seconds: float = 60.0,
    workstream_id: str | None = None,
    emit: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    callback = emit or (lambda _: None)
    run_paths = _verification_run_paths(workspace, run_id, workstream_id=workstream_id)
    offsets = {
        "stdout": 0,
        "stderr": 0,
        "logcat": 0,
    }
    seen_events = 0
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        events = _read_events(run_paths)
        if len(events) > seen_events:
            for event in events[seen_events:]:
                callback(f"[event] {event.get('event_type')}: {event.get('message')}")
            seen_events = len(events)
        for stream, path in {
            "stdout": run_paths["stdout_log"],
            "stderr": run_paths["stderr_log"],
            "logcat": run_paths["logcat_log"],
        }.items():
            offsets[stream], lines = _read_appended_lines(path, offsets[stream])
            for line in lines:
                callback(f"[{stream}] {line}")
        run = read_verification_run(workspace, run_id, workstream_id=workstream_id)
        if run.get("status") in TERMINAL_RUN_STATUSES:
            return run
        time.sleep(0.2)
    raise TimeoutError(f"Verification run did not finish before timeout: {run_id}")


def approve_verification_baseline(workspace: str | Path, case_id: str, run_id: str | None = None, workstream_id: str | None = None) -> dict[str, Any]:
    paths = _ensure_workspace_paths(workspace, workstream_id=workstream_id)
    recipes = read_verification_recipes(workspace, workstream_id=workstream_id)
    case = _case_by_id(recipes, case_id)
    run = read_verification_run(workspace, run_id, workstream_id=workstream_id) if run_id else latest_completed_verification_run(workspace, workstream_id=workstream_id)
    if not run:
        raise FileNotFoundError("No completed verification run is available for baseline approval.")
    status = _load_baseline_status(paths)
    status["cases"][case_id] = {
        "case_id": case_id,
        "runner": case.get("runner"),
        "source_path": _case_baseline_source(workspace, case),
        "approved_from_run_id": run["run_id"],
        "approved_at": now_iso(),
        "status": "approved",
    }
    _write_baseline_status(paths, status)
    return status["cases"][case_id]


def update_verification_baseline(
    workspace: str | Path,
    case_id: str,
    artifact_path: str | None = None,
    run_id: str | None = None,
    workstream_id: str | None = None,
) -> dict[str, Any]:
    paths = _ensure_workspace_paths(workspace, workstream_id=workstream_id)
    recipes = read_verification_recipes(workspace, workstream_id=workstream_id)
    case = _case_by_id(recipes, case_id)
    source_path = _case_baseline_source(workspace, case)
    if not source_path:
        raise ValueError(f"Verification case does not declare a project-owned baseline source path: {case_id}")
    run = read_verification_run(workspace, run_id, workstream_id=workstream_id) if run_id else latest_completed_verification_run(workspace, workstream_id=workstream_id)
    if not run:
        raise FileNotFoundError("No completed verification run is available for baseline update.")
    artifact_candidate = Path(artifact_path).expanduser().resolve() if artifact_path else None
    if artifact_candidate is None:
        run_artifact_dir = Path(run["artifacts_dir"])
        files = sorted(candidate for candidate in run_artifact_dir.rglob("*") if candidate.is_file())
        if not files:
            raise FileNotFoundError(f"No artifacts found in run directory: {run_artifact_dir}")
        artifact_candidate = files[0]
    target_path = Path(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact_candidate, target_path)
    status = _load_baseline_status(paths)
    status["cases"][case_id] = {
        "case_id": case_id,
        "runner": case.get("runner"),
        "source_path": str(target_path),
        "updated_from_run_id": run["run_id"],
        "artifact_source_path": str(artifact_candidate),
        "updated_at": now_iso(),
        "status": "updated",
    }
    _write_baseline_status(paths, status)
    return status["cases"][case_id]


def execute_verification_run(workspace: str | Path, run_id: str, workstream_id: str | None = None) -> dict[str, Any]:
    workspace_path = str(Path(workspace).expanduser().resolve())
    run = read_verification_run(workspace_path, run_id, workstream_id=workstream_id)
    recipes = read_verification_recipes(workspace_path, workstream_id=workstream_id)
    run["status"] = "running"
    run["started_at"] = run.get("started_at") or now_iso()
    _write_run(workspace_path, run, workstream_id=workstream_id)

    passed_cases = 0
    failed_cases = 0
    for case_state in run.get("cases", []):
        case = _case_by_id(recipes, case_state["case_id"])
        run_paths = _verification_run_paths(workspace_path, run_id, workstream_id=workstream_id)
        semantic_runtime = _semantic_runtime_preflight(workspace_path, run_paths, case)
        auth_runtime = None
        case_state["status"] = "running"
        case_state["started_at"] = now_iso()
        if semantic_runtime.get("enabled"):
            case_state["semantic_runtime"] = semantic_runtime
        _write_run(workspace_path, run, workstream_id=workstream_id)
        _append_event(
            workspace_path,
            run_id,
            "case_started",
            f"Starting verification case {case['id']}.",
            workstream_id=workstream_id,
            case_id=case["id"],
            surface_type=case.get("surface_type"),
            runner=case.get("runner"),
        )
        attempts = max(1, int((case.get("retry_policy") or {}).get("attempts", 1)))
        delay_seconds = float((case.get("retry_policy") or {}).get("delay_seconds", 0))
        exit_code = 1
        last_error: Exception | None = None
        attempts_performed = 0
        if semantic_runtime.get("enabled") and semantic_runtime.get("status") == "failed":
            last_error = RuntimeError(str(semantic_runtime.get("message") or "Semantic helper preflight failed."))
            exit_code = 1
            _append_event(
                workspace_path,
                run_id,
                "semantic_assertions_preflight_failed",
                str(semantic_runtime.get("message") or "Semantic helper preflight failed."),
                workstream_id=workstream_id,
                case_id=case["id"],
                reason=semantic_runtime.get("reason"),
                report_path=semantic_runtime.get("report_path"),
                helper_root=semantic_runtime.get("helper_root"),
            )
        else:
            try:
                auth_runtime = _resolve_case_auth_runtime(workspace_path, run, case)
                if auth_runtime:
                    case_state["auth"] = copy.deepcopy(auth_runtime.get("summary") or {})
                    _write_run(workspace_path, run, workstream_id=workstream_id)
                for attempt in range(1, attempts + 1):
                    attempts_performed = attempt
                    try:
                        exit_code = _run_case_attempt(
                            workspace_path,
                            run,
                            case,
                            attempt,
                            semantic_runtime=semantic_runtime,
                            auth_runtime=auth_runtime,
                        )
                        last_error = None
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        exit_code = 1
                        with run_paths["stderr_log"].open("a") as handle:
                            handle.write(f"\n[verification-error] {exc}\n")
                    if exit_code == 0:
                        break
                    if attempt < attempts and delay_seconds > 0:
                        time.sleep(delay_seconds)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                exit_code = 1
                with run_paths["stderr_log"].open("a") as handle:
                    handle.write(f"\n[auth-resolution-error] {exc}\n")
            finally:
                _cleanup_case_auth_runtime(auth_runtime)
        semantic_assertions = (
            semantic_runtime
            if semantic_runtime.get("enabled") and semantic_runtime.get("status") == "failed"
            else _validate_semantic_assertions(run_paths, case)
        )
        case_state["semantic_assertions"] = semantic_assertions
        case_state["semantic_summary"] = semantic_assertions
        native_layout_audit = _validate_native_layout_audit(run_paths, case)
        case_state["native_layout_audit"] = native_layout_audit
        browser_layout_audit = _validate_browser_layout_audit(run_paths, case)
        case_state["browser_layout_audit"] = browser_layout_audit
        if exit_code == 0 and semantic_assertions.get("enabled") and semantic_assertions.get("status") != "passed":
            exit_code = 1
            message = semantic_assertions.get("message") or (
                f"Semantic assertions failed for case {case['id']}: "
                f"missing_targets={semantic_assertions.get('missing_targets', [])}, "
                f"missing={semantic_assertions.get('missing_checks', [])}, failed={semantic_assertions.get('failed_checks', [])}"
            )
            with _verification_run_paths(workspace_path, run_id, workstream_id=workstream_id)["stderr_log"].open("a") as handle:
                handle.write(f"\n[semantic-assertions] {message}\n")
            _append_event(
                workspace_path,
                run_id,
                "semantic_assertions_failed",
                message,
                workstream_id=workstream_id,
                case_id=case["id"],
                missing_targets=semantic_assertions.get("missing_targets"),
                missing_checks=semantic_assertions.get("missing_checks"),
                failed_checks=semantic_assertions.get("failed_checks"),
                report_path=semantic_assertions.get("report_path"),
            )
        if semantic_assertions.get("enabled"):
            _append_event(
                workspace_path,
                run_id,
                "semantic_assertions_validated",
                f"Semantic assertions {semantic_assertions.get('status')} for case {case['id']}.",
                workstream_id=workstream_id,
                case_id=case["id"],
                status=semantic_assertions.get("status"),
                reason=semantic_assertions.get("reason"),
                missing_targets=semantic_assertions.get("missing_targets"),
                missing_checks=semantic_assertions.get("missing_checks"),
                failed_checks=semantic_assertions.get("failed_checks"),
                optional_failed_checks=semantic_assertions.get("optional_failed_checks"),
                report_path=semantic_assertions.get("report_path"),
            )
        if native_layout_audit.get("enabled") and native_layout_audit.get("status") != "passed":
            if exit_code == 0:
                exit_code = 1
            native_layout_status = native_layout_audit.get("status")
            message = native_layout_audit.get("message") or (
                f"Native layout audit {native_layout_status} for case {case['id']}."
            )
            log_prefix = "native-layout-audit-warning" if native_layout_status == "warning" else "native-layout-audit"
            with _verification_run_paths(workspace_path, run_id, workstream_id=workstream_id)["stderr_log"].open("a") as handle:
                handle.write(f"\n[{log_prefix}] {message}\n")
            _append_event(
                workspace_path,
                run_id,
                "native_layout_audit_warning" if native_layout_status == "warning" else "native_layout_audit_failed",
                message,
                workstream_id=workstream_id,
                case_id=case["id"],
                issue_count=native_layout_audit.get("issue_count"),
                warning_count=native_layout_audit.get("warning_count"),
                report_path=native_layout_audit.get("report_path"),
                source_report_path=native_layout_audit.get("source_report_path"),
            )
        if browser_layout_audit.get("enabled") and browser_layout_audit.get("status") != "passed":
            if exit_code == 0:
                exit_code = 1
            browser_layout_status = browser_layout_audit.get("status")
            message = browser_layout_audit.get("message") or (
                f"Browser layout audit {browser_layout_status} for case {case['id']}."
            )
            log_prefix = "browser-layout-audit-warning" if browser_layout_status == "warning" else "browser-layout-audit"
            with _verification_run_paths(workspace_path, run_id, workstream_id=workstream_id)["stderr_log"].open("a") as handle:
                handle.write(f"\n[{log_prefix}] {message}\n")
            _append_event(
                workspace_path,
                run_id,
                "browser_layout_audit_warning" if browser_layout_status == "warning" else "browser_layout_audit_failed",
                message,
                workstream_id=workstream_id,
                case_id=case["id"],
                issue_count=browser_layout_audit.get("issue_count"),
                warning_count=browser_layout_audit.get("warning_count"),
                report_path=browser_layout_audit.get("report_path"),
                screenshot_path=browser_layout_audit.get("screenshot_path"),
            )
        case_state["attempts"] = attempts_performed
        case_state["exit_code"] = exit_code
        case_state["completed_at"] = now_iso()
        case_state["status"] = "passed" if exit_code == 0 else "failed"
        case_state["baseline"] = _case_baseline_result(workspace_path, case, exit_code)
        logcat_summary = None
        if _case_prefers_android(case):
            logcat_summary = _summarize_logcat_crash(
                _verification_run_paths(workspace_path, run_id, workstream_id=workstream_id)["logcat_log"],
                int((case.get("android_logcat") or {}).get("tail_lines_on_failure", 80)),
            )
            if logcat_summary:
                case_state["logcat_summary"] = logcat_summary
        if last_error is not None:
            case_state["error"] = str(last_error)
        if exit_code == 0:
            passed_cases += 1
        else:
            failed_cases += 1
        if logcat_summary:
            run.setdefault("summary", {})
            run["summary"]["logcat_crash_summary"] = {
                "case_id": case["id"],
                "signals": logcat_summary["signals"],
            }
        if auth_runtime:
            run.setdefault("summary", {})
            run["summary"]["auth_summary"] = copy.deepcopy(auth_runtime.get("summary") or {})
        if semantic_assertions.get("enabled"):
            run.setdefault("summary", {})
            run["summary"]["semantic_summary"] = {
                "case_id": case["id"],
                "status": semantic_assertions.get("status"),
                "reason": semantic_assertions.get("reason"),
                "missing_targets": semantic_assertions.get("missing_targets", []),
                "missing_checks": semantic_assertions.get("missing_checks", []),
                "failed_checks": semantic_assertions.get("failed_checks", []),
                "optional_failed_checks": semantic_assertions.get("optional_failed_checks", []),
            }
        if native_layout_audit.get("enabled"):
            _append_event(
                workspace_path,
                run_id,
                "native_layout_audit_validated",
                f"Native layout audit {native_layout_audit.get('status')} for case {case['id']}.",
                workstream_id=workstream_id,
                case_id=case["id"],
                status=native_layout_audit.get("status"),
                issue_count=native_layout_audit.get("issue_count"),
                report_path=native_layout_audit.get("report_path"),
                source_report_path=native_layout_audit.get("source_report_path"),
            )
            run.setdefault("summary", {})
            run["summary"]["native_layout_audit"] = {
                "case_id": case["id"],
                "status": native_layout_audit.get("status"),
                "issue_count": native_layout_audit.get("issue_count"),
                "warning_count": native_layout_audit.get("warning_count"),
                "report_path": native_layout_audit.get("report_path"),
                "source_report_path": native_layout_audit.get("source_report_path"),
            }
        if browser_layout_audit.get("enabled"):
            _append_event(
                workspace_path,
                run_id,
                "browser_layout_audit_validated",
                f"Browser layout audit {browser_layout_audit.get('status')} for case {case['id']}.",
                workstream_id=workstream_id,
                case_id=case["id"],
                status=browser_layout_audit.get("status"),
                issue_count=browser_layout_audit.get("issue_count"),
                report_path=browser_layout_audit.get("report_path"),
                screenshot_path=browser_layout_audit.get("screenshot_path"),
            )
            run.setdefault("summary", {})
            run["summary"]["browser_layout_audit"] = {
                "case_id": case["id"],
                "status": browser_layout_audit.get("status"),
                "issue_count": browser_layout_audit.get("issue_count"),
                "report_path": browser_layout_audit.get("report_path"),
                "screenshot_path": browser_layout_audit.get("screenshot_path"),
            }
        _write_run(workspace_path, run, workstream_id=workstream_id)
        _append_event(
            workspace_path,
            run_id,
            "case_finished",
            f"Finished verification case {case['id']} with exit code {exit_code}.",
            workstream_id=workstream_id,
            case_id=case["id"],
            exit_code=exit_code,
            status=case_state["status"],
            baseline_status=case_state["baseline"]["status"],
            native_layout_status=native_layout_audit.get("status"),
            browser_layout_status=browser_layout_audit.get("status"),
            logcat_signals=(logcat_summary or {}).get("signals"),
            auth_summary=copy.deepcopy((auth_runtime or {}).get("summary") or {}),
        )
        if native_layout_audit.get("status") == "warning" or browser_layout_audit.get("status") == "warning":
            append_analytics_event(
                "verification_case_warning",
                workspace_path,
                source="verification",
                status="warning",
                workstream_id=workstream_id,
                task_id=run.get("task_id"),
                run_id=run_id,
                external_issue=copy.deepcopy(run.get("external_issue")),
                payload={
                    "case_id": case.get("id"),
                    "native_layout_status": native_layout_audit.get("status"),
                    "browser_layout_status": browser_layout_audit.get("status"),
                    "auth": copy.deepcopy((auth_runtime or {}).get("summary") or {}),
                },
            )
        if exit_code != 0:
            run["status"] = "failed"
            run["completed_at"] = now_iso()
            run["completed_at_ns"] = time.time_ns()
            preserved_summary = {
                key: value
                for key, value in (run.get("summary") or {}).items()
                if key in {"logcat_crash_summary", "semantic_summary", "browser_layout_audit", "native_layout_audit"}
            }
            run["summary"] = {
                **preserved_summary,
                "total_cases": len(run["case_ids"]),
                "passed_cases": passed_cases,
                "failed_cases": failed_cases,
                "message": f"Verification failed on case {case['id']}.",
            }
            _write_run(workspace_path, run, workstream_id=workstream_id)
            _append_event(workspace_path, run_id, "run_failed", f"Verification run failed on case {case['id']}.", workstream_id=workstream_id)
            append_analytics_event(
                "verification_run_failed",
                workspace_path,
                source="verification",
                status="failed",
                workstream_id=workstream_id,
                task_id=run.get("task_id"),
                run_id=run_id,
                external_issue=copy.deepcopy(run.get("external_issue")),
                payload={
                    "case_id": case.get("id"),
                    "summary": copy.deepcopy(run.get("summary") or {}),
                },
            )
            return run

    run["status"] = "passed"
    run["completed_at"] = now_iso()
    run["completed_at_ns"] = time.time_ns()
    preserved_summary = {
        key: value
        for key, value in (run.get("summary") or {}).items()
        if key in {"logcat_crash_summary", "semantic_summary", "browser_layout_audit", "native_layout_audit"}
    }
    run["summary"] = {
        **preserved_summary,
        "total_cases": len(run["case_ids"]),
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "message": "Verification run passed.",
    }
    _write_run(workspace_path, run, workstream_id=workstream_id)
    _append_event(workspace_path, run_id, "run_finished", "Verification run passed.", workstream_id=workstream_id)
    append_analytics_event(
        "verification_run_finished",
        workspace_path,
        source="verification",
        status="passed",
        workstream_id=workstream_id,
        task_id=run.get("task_id"),
        run_id=run_id,
        external_issue=copy.deepcopy(run.get("external_issue")),
        payload={"summary": copy.deepcopy(run.get("summary") or {})},
    )
    return run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AgentiUX Dev verification runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_workspace_arg(command: argparse.ArgumentParser) -> None:
        command.add_argument("--workspace", required=True, help="Workspace path")
        command.add_argument("--workstream-id", help="Optional workstream id")

    cmd = subparsers.add_parser("recipes")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("audit-coverage")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("show-helper-catalog")
    add_workspace_arg(cmd)

    cmd = subparsers.add_parser("sync-helpers")
    add_workspace_arg(cmd)
    cmd.add_argument("--force", action="store_true")

    cmd = subparsers.add_parser("write-recipes")
    add_workspace_arg(cmd)
    cmd.add_argument("--recipe-file", required=True)

    cmd = subparsers.add_parser("approve-baseline")
    add_workspace_arg(cmd)
    cmd.add_argument("--case-id", required=True)
    cmd.add_argument("--run-id")

    cmd = subparsers.add_parser("update-baseline")
    add_workspace_arg(cmd)
    cmd.add_argument("--case-id", required=True)
    cmd.add_argument("--run-id")
    cmd.add_argument("--artifact-path")

    cmd = subparsers.add_parser("run-case")
    add_workspace_arg(cmd)
    cmd.add_argument("--case-id", required=True)
    cmd.add_argument("--wait", action="store_true")
    cmd.add_argument("--follow", action="store_true")

    cmd = subparsers.add_parser("run-suite")
    add_workspace_arg(cmd)
    cmd.add_argument("--suite-id", required=True)
    cmd.add_argument("--wait", action="store_true")
    cmd.add_argument("--follow", action="store_true")

    cmd = subparsers.add_parser("runs")
    add_workspace_arg(cmd)
    cmd.add_argument("--limit", type=int)

    cmd = subparsers.add_parser("run-status")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)

    cmd = subparsers.add_parser("run-events")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)
    cmd.add_argument("--limit", type=int, default=50)

    cmd = subparsers.add_parser("run-log")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)
    cmd.add_argument("--stream", choices=["stdout", "stderr", "logcat"], default="stdout")
    cmd.add_argument("--lines", type=int, default=50)

    cmd = subparsers.add_parser("wait-run")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)
    cmd.add_argument("--timeout-seconds", type=float, default=60.0)
    cmd.add_argument("--follow", action="store_true")

    cmd = subparsers.add_parser("cancel-run")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)

    cmd = subparsers.add_parser("worker")
    add_workspace_arg(cmd)
    cmd.add_argument("--run-id", required=True)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "recipes":
        payload = read_verification_recipes(args.workspace, workstream_id=args.workstream_id)
    elif args.command == "audit-coverage":
        payload = audit_verification_coverage(args.workspace, workstream_id=args.workstream_id)
    elif args.command == "show-helper-catalog":
        payload = show_verification_helper_catalog(args.workspace)
    elif args.command == "sync-helpers":
        payload = sync_verification_helpers(args.workspace, force=args.force)
    elif args.command == "write-recipes":
        payload = write_verification_recipes(args.workspace, _load_json(Path(args.recipe_file), default={}) or {}, workstream_id=args.workstream_id)
    elif args.command == "approve-baseline":
        payload = approve_verification_baseline(args.workspace, args.case_id, run_id=args.run_id, workstream_id=args.workstream_id)
    elif args.command == "update-baseline":
        payload = update_verification_baseline(args.workspace, args.case_id, artifact_path=args.artifact_path, run_id=args.run_id, workstream_id=args.workstream_id)
    elif args.command == "run-case":
        if args.follow:
            run = start_verification_case(args.workspace, args.case_id, wait=False, workstream_id=args.workstream_id)
            payload = follow_verification_run(args.workspace, run["run_id"], workstream_id=args.workstream_id, emit=lambda line: print(line, flush=True))
        else:
            payload = start_verification_case(args.workspace, args.case_id, wait=args.wait, workstream_id=args.workstream_id)
    elif args.command == "run-suite":
        if args.follow:
            run = start_verification_suite(args.workspace, args.suite_id, wait=False, workstream_id=args.workstream_id)
            payload = follow_verification_run(args.workspace, run["run_id"], workstream_id=args.workstream_id, emit=lambda line: print(line, flush=True))
        else:
            payload = start_verification_suite(args.workspace, args.suite_id, wait=args.wait, workstream_id=args.workstream_id)
    elif args.command == "runs":
        payload = list_verification_runs(args.workspace, limit=args.limit, workstream_id=args.workstream_id)
    elif args.command == "run-status":
        payload = read_verification_run(args.workspace, args.run_id, workstream_id=args.workstream_id)
    elif args.command == "run-events":
        payload = read_verification_events(args.workspace, args.run_id, limit=args.limit, workstream_id=args.workstream_id)
    elif args.command == "run-log":
        payload = read_verification_log_tail(args.workspace, args.run_id, stream=args.stream, lines=args.lines, workstream_id=args.workstream_id)
    elif args.command == "wait-run":
        if args.follow:
            payload = follow_verification_run(
                args.workspace,
                args.run_id,
                timeout_seconds=args.timeout_seconds,
                workstream_id=args.workstream_id,
                emit=lambda line: print(line, flush=True),
            )
        else:
            payload = wait_for_verification_run(args.workspace, args.run_id, timeout_seconds=args.timeout_seconds, workstream_id=args.workstream_id)
    elif args.command == "cancel-run":
        payload = cancel_verification_run(args.workspace, args.run_id, workstream_id=args.workstream_id)
    elif args.command == "worker":
        execute_verification_run(args.workspace, args.run_id, workstream_id=args.workstream_id)
        return 0
    else:
        raise ValueError(f"Unsupported command: {args.command}")

    print(text_result(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
