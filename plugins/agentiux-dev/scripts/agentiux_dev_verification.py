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
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agentiux_dev_lib import (
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
    start_logged_process,
    start_logged_python_process,
    stop_process,
    text_result,
    workspace_paths,
)


TERMINAL_RUN_STATUSES = {"passed", "failed", "cancelled"}
RUNNER_TYPES = {
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
LOGCAT_CRASH_PATTERNS = [
    re.compile(r"fatal exception", re.IGNORECASE),
    re.compile(r"androidruntime", re.IGNORECASE),
    re.compile(r"\banr in\b", re.IGNORECASE),
    re.compile(r"\bcrash\b", re.IGNORECASE),
    re.compile(r"abort message:", re.IGNORECASE),
]


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
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


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
    Path(paths["verification_dir"]).mkdir(parents=True, exist_ok=True)
    Path(paths["verification_runs_dir"]).mkdir(parents=True, exist_ok=True)
    Path(paths["verification_baselines_dir"]).mkdir(parents=True, exist_ok=True)
    return paths


def _runner_from_legacy(value: str | None) -> str | None:
    mapping = {
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
        "android_logcat": _normalize_android_logcat(payload.get("android_logcat")),
    }
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
    run_root = Path(paths["verification_runs_dir"]) / run_id
    return {
        "run_root": run_root,
        "run_json": run_root / "run.json",
        "events_jsonl": run_root / "events.jsonl",
        "stdout_log": run_root / "stdout.log",
        "stderr_log": run_root / "stderr.log",
        "logcat_log": run_root / "logcat.log",
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
        "android": state.get("toolchain_capabilities", {}).get("mobile_verification_android", {"supported": True, "available": True, "reason": None}),
        "ios": state.get("toolchain_capabilities", {}).get("mobile_verification_ios", {"supported": True, "available": True, "reason": None}),
    }
    status = mapping.get(requirement, {"supported": True, "available": True, "reason": None})
    return {"requirement": requirement, **status}


def _case_selection_summary(workspace: str | Path, case: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    requirements = [_host_requirement_status(state, requirement) for requirement in case.get("host_requirements") or []]
    return {
        "case_id": case["id"],
        "title": case.get("title"),
        "surface_type": case.get("surface_type"),
        "runner": case.get("runner"),
        "baseline_source": _case_baseline_source(workspace, case),
        "host_requirements": case.get("host_requirements") or [],
        "host_compatibility": requirements,
        "tags": case.get("tags") or [],
        "surface_ids": case.get("surface_ids") or [],
        "feature_ids": case.get("feature_ids") or [],
        "routes_or_screens": case.get("routes_or_screens") or [],
        "changed_path_globs": case.get("changed_path_globs") or [],
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


def _wait_for_readiness_probe(workspace: str | Path, case: dict[str, Any]) -> None:
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
            if candidate.exists():
                return
            time.sleep(0.2)
        raise TimeoutError(f"Readiness probe timed out for file {candidate}")
    if probe_type == "shell_command":
        argv = probe.get("argv")
        if not argv:
            raise ValueError("shell_command readiness probe requires argv")
        while time.time() < deadline:
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


def _run_case_attempt(workspace: str | Path, run: dict[str, Any], case: dict[str, Any], attempt: int) -> int:
    run_paths = _verification_run_paths(workspace, run["run_id"], workstream_id=run.get("workstream_id"))
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in (case.get("env") or {}).items()})
    env["AGENTIUX_VERIFICATION_RUN_ID"] = run["run_id"]
    env["AGENTIUX_VERIFICATION_CASE_ID"] = case["id"]
    env["AGENTIUX_VERIFICATION_ARTIFACT_DIR"] = str(run_paths["artifacts_dir"])
    cwd = _resolve_case_cwd(workspace, case)
    argv, shell_command = _effective_command(case)

    _wait_for_readiness_probe(workspace, case)
    with run_paths["stdout_log"].open("a") as stdout_handle, run_paths["stderr_log"].open("a") as stderr_handle:
        stdout_handle.write(f"\n=== CASE {case['id']} ATTEMPT {attempt} START {now_iso()} ===\n")
        stderr_handle.write(f"\n=== CASE {case['id']} ATTEMPT {attempt} START {now_iso()} ===\n")
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


def _start_run(workspace: str | Path, mode: str, target_id: str, workstream_id: str | None = None) -> dict[str, Any]:
    paths = _ensure_workspace_paths(workspace, workstream_id=workstream_id)
    recipes = read_verification_recipes(workspace, workstream_id=workstream_id)
    case_ids = _resolve_case_ids(recipes, mode, target_id)
    slow_after = max((_slow_after_seconds(_case_by_id(recipes, case_id)) for case_id in case_ids), default=10)
    created_at_ns = time.time_ns()
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_paths = _verification_run_paths(workspace, run_id, workstream_id=workstream_id)
    run_paths["run_root"].mkdir(parents=True, exist_ok=True)
    run_paths["artifacts_dir"].mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "run_id": run_id,
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": paths["current_workstream_id"],
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
            }
            for case_id in case_ids
        ],
    }
    _write_run(workspace, payload, workstream_id=workstream_id)
    _append_event(workspace, run_id, "run_queued", f"Queued {mode} run for {target_id}.", workstream_id=workstream_id, target_id=target_id, case_ids=case_ids)

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
    while time.time() < deadline:
        run = read_verification_run(workspace, run_id, workstream_id=workstream_id)
        if run.get("status") in TERMINAL_RUN_STATUSES:
            return run
        time.sleep(0.2)
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


def audit_verification_coverage(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    detection = detect_workspace(resolved_workspace)
    paths = workspace_paths(resolved_workspace, workstream_id=workstream_id)
    recipes_path = Path(paths["verification_recipes"])
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
        "web": False,
        "mobile": False,
        "android": False,
        "ios": False,
        "backend": False,
    }
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
        surface_type = str(case.get("surface_type") or "").lower()
        device = str((case.get("device_or_viewport") or {}).get("device") or "").lower()
        tags = {str(tag).lower() for tag in case.get("tags", [])}
        feature_ids = {str(tag).lower() for tag in case.get("feature_ids", [])}
        if surface_type == "plugin" or "plugin-runtime" in tags or "plugin-runtime" in feature_ids:
            coverage["plugin"] = True
        if surface_type == "web":
            coverage["web"] = True
        if surface_type == "mobile":
            coverage["mobile"] = True
        if case.get("runner") == "android-compose-screenshot" or "android" in device or surface_type == "android":
            coverage["android"] = True
            coverage["mobile"] = True
        if case.get("runner") == "ios-simulator-capture" or "ios" in device or surface_type == "ios":
            coverage["ios"] = True
            coverage["mobile"] = True
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
    if "web-platform" in profiles and not coverage["web"]:
        gaps.append(
            _coverage_gap(
                "missing-web-verification",
                "Web workspace has no web verification case",
                "Add at least one deterministic web verification case.",
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
    if "android" in stacks and not coverage["android"]:
        gaps.append(
            _coverage_gap(
                "missing-android-verification",
                "Android signals were detected but no Android verification case is defined",
                "Add Android-targeted deterministic verification for emulator, Compose screenshots, or both.",
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
    return {
        "workspace_path": str(resolved_workspace),
        "workstream_id": paths["current_workstream_id"],
        "recipe_source": recipe_source,
        "detected_stacks": detection.get("detected_stacks", []),
        "selected_profiles": detection.get("selected_profiles", []),
        "case_count": len(cases),
        "suite_count": len(suites),
        "coverage": coverage,
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
        case_state["status"] = "running"
        case_state["started_at"] = now_iso()
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
        for attempt in range(1, attempts + 1):
            try:
                exit_code = _run_case_attempt(workspace_path, run, case, attempt)
                last_error = None
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                exit_code = 1
                with _verification_run_paths(workspace_path, run_id, workstream_id=workstream_id)["stderr_log"].open("a") as handle:
                    handle.write(f"\n[verification-error] {exc}\n")
            if exit_code == 0:
                break
            if attempt < attempts and delay_seconds > 0:
                time.sleep(delay_seconds)
        case_state["attempts"] = attempts
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
            logcat_signals=(logcat_summary or {}).get("signals"),
        )
        if exit_code != 0:
            run["status"] = "failed"
            run["completed_at"] = now_iso()
            run["completed_at_ns"] = time.time_ns()
            run["summary"] = {
                **({key: value for key, value in (run.get("summary") or {}).items() if key == "logcat_crash_summary"}),
                "total_cases": len(run["case_ids"]),
                "passed_cases": passed_cases,
                "failed_cases": failed_cases,
                "message": f"Verification failed on case {case['id']}.",
            }
            _write_run(workspace_path, run, workstream_id=workstream_id)
            _append_event(workspace_path, run_id, "run_failed", f"Verification run failed on case {case['id']}.", workstream_id=workstream_id)
            return run

    run["status"] = "passed"
    run["completed_at"] = now_iso()
    run["completed_at_ns"] = time.time_ns()
    run["summary"] = {
        **({key: value for key, value in (run.get("summary") or {}).items() if key == "logcat_crash_summary"}),
        "total_cases": len(run["case_ids"]),
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "message": "Verification run passed.",
    }
    _write_run(workspace_path, run, workstream_id=workstream_id)
    _append_event(workspace_path, run_id, "run_finished", "Verification run passed.", workstream_id=workstream_id)
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
