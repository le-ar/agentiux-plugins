from __future__ import annotations

import json
import os
import posixpath
from pathlib import Path
import re
import shlex
from typing import Any

from agentiux_dev_lib import now_iso, plugin_info
from agentiux_dev_retrieval import (
    SURFACE_PAYLOAD_CEILINGS,
    SURFACE_WORKING_BUDGETS,
    infer_retrieval_mode,
    payload_size_bytes,
    retrieval_mode_profile,
    retrieval_policy_payload,
)
from agentiux_dev_text import (
    expand_token_set,
    score_preexpanded_query_match,
    score_preexpanded_set_match,
    score_token_match,
    short_hash,
    tokenize_text,
)

from agentiux_dev_context_cache import (
    ROUTE_PROFILES,
    all_capability_entries,
    compact_workspace_context,
    context_cache_paths,
    intent_routes,
    load_usage,
    load_workspace_context_bundle,
    plugin_catalog_root,
    record_context_pack_stats,
    record_high_confidence_runtime_context,
    record_search_stats,
    record_usage,
    refresh_context_index,
    route_index,
)
from agentiux_dev_context_projection import (
    build_command_suggestions,
    build_owner_candidates,
    build_why_these_files_summary,
    build_runtime_action_hints,
    resolve_route_hint_override,
)
from agentiux_dev_context_store import (
    QUERY_CACHE_CONTEXT_PACK_KIND,
    QUERY_CACHE_OWNERSHIP_GRAPH_KIND,
    QUERY_CACHE_ROUTE_SHORTLIST_KIND,
    QUERY_CACHE_RUNTIME_PREFLIGHT_KIND,
    QUERY_CACHE_TASK_RETRIEVAL_KIND,
    load_module_chunks,
    load_module_summaries,
    load_pinned_project_memory_chunks,
    load_source_hashes,
    read_query_cache,
    search_chunks,
    upsert_query_cache_entry,
)
from agentiux_dev_context_semantic import (
    ANALYSIS_AUDIT_MODES,
    load_semantic_manifest,
    search_semantic_units,
    semantic_mode_enabled,
    semantic_summary_from_manifest,
)
from agentiux_dev_request_intent import parse_runtime_triage_constraints


ROUTE_SCORE_MIN = 8
ROUTE_SCORE_AMBIGUOUS_DELTA = 3
TOKEN_SYNONYMS = {
    "analysis": ["hotspot", "incremental", "module", "section", "structural", "symbol"],
    "a11y": ["accessibility", "semantic"],
    "accessibility": ["a11y", "semantic"],
    "baseline": ["snapshot", "verification", "visual"],
    "benchmark": ["budget", "payload", "performance", "telemetry"],
    "budget": ["benchmark", "payload", "performance"],
    "branch": ["checkout", "worktree"],
    "cache": ["context", "refresh"],
    "catalog": ["index", "mcp", "tools"],
    "checkout": ["branch", "worktree"],
    "cockpit": ["dashboard"],
    "commit": ["git", "message"],
    "context": ["catalog", "workspace"],
    "dashboard": ["cockpit", "gui"],
    "design": ["brief", "handoff", "ui", "ux", "visual"],
    "git": ["branch", "commit", "worktree"],
    "gui": ["dashboard"],
    "handoff": ["brief", "design"],
    "helper": ["bundle", "semantic", "verification"],
    "hotspot": ["analysis", "drift", "module", "structural"],
    "index": ["catalog", "context"],
    "incremental": ["analysis", "index", "refresh", "structural"],
    "mcp": ["catalog", "plugin", "tool", "tools"],
    "memory": ["note", "project"],
    "module": ["analysis", "hotspot", "structural", "symbol"],
    "payload": ["benchmark", "budget", "ceiling", "telemetry"],
    "performance": ["benchmark", "budget", "latency", "payload", "telemetry"],
    "plugin": ["catalog", "dashboard", "mcp", "self", "host"],
    "pr": ["pull", "request"],
    "pull": ["pr"],
    "release": ["dashboard", "readiness", "ship", "smoke"],
    "retrieval": ["catalog", "context", "plugin"],
    "semantic": ["a11y", "helper", "verification", "visual"],
    "section": ["analysis", "doc", "module", "structural"],
    "ship": ["readiness", "release", "smoke"],
    "smoke": ["readiness", "release", "verification"],
    "structural": ["analysis", "hotspot", "module", "symbol"],
    "symbol": ["analysis", "module", "section", "structural"],
    "task": ["stage", "workstream", "workspace"],
    "telemetry": ["benchmark", "budget", "payload", "performance"],
    "tool": ["catalog", "mcp"],
    "tools": ["catalog", "mcp"],
    "verification": ["baseline", "checks", "helper", "semantic", "test", "visual"],
    "visual": ["baseline", "design", "semantic", "verification"],
    "workflow": ["stage", "task", "workstream", "workspace"],
    "workstream": ["stage", "task", "workspace", "workflow"],
    "worktree": ["branch", "checkout"],
    "workspace": ["task", "workstream", "workflow"],
}
RETRIEVAL_LADDER = [
    {
        "step": 1,
        "surface": "existing_summaries",
        "description": "Use existing cheap summaries first: plugin stats, dashboard snapshot, and workspace detail summaries.",
        "tools": ["get_plugin_stats", "get_dashboard_snapshot", "get_workspace_detail"],
    },
    {
        "step": 2,
        "surface": "show_intent_route",
        "description": "Resolve the intent family before opening large docs or entrypoints.",
        "tools": ["show_intent_route"],
    },
    {
        "step": 3,
        "surface": "show_capability_catalog",
        "description": "Inspect compact skill, MCP, script, and reference catalogs for the selected route.",
        "tools": ["show_capability_catalog"],
    },
    {
        "step": 4,
        "surface": "triage_repo_request",
        "description": "Use one front-door triage call to resolve smallest owner files, exact package commands, and no-scan guidance before manual exploration.",
        "tools": ["triage_repo_request"],
    },
    {
        "step": 5,
        "surface": "show_runtime_preflight",
        "description": "Resolve route, owner candidates, exact commands, and stop/go guidance in one cheap preflight call.",
        "tools": ["show_runtime_preflight"],
    },
    {
        "step": 6,
        "surface": "show_workspace_context_pack",
        "description": "Load the current workspace context pack for the request if repo context is needed.",
        "tools": ["show_workspace_context_pack"],
    },
    {
        "step": 7,
        "surface": "search_context_index",
        "description": "Search the global context index for relevant chunks instead of broad manual scans.",
        "tools": ["search_context_index"],
    },
    {
        "step": 8,
        "surface": "show_context_structure",
        "description": "Inspect compact structural module, symbol, doc-section, hotspot, and incremental index summaries.",
        "tools": ["show_context_structure"],
    },
    {
        "step": 9,
        "surface": "run_analysis_audit",
        "description": "Run a read-only architecture, performance, or docs-style audit with optional semantic shortlist expansion.",
        "tools": ["run_analysis_audit"],
    },
    {
        "step": 10,
        "surface": "targeted_file_reads",
        "description": "Open only the specific files referenced by the selected route and search hits.",
        "tools": [],
    },
    {
        "step": 11,
        "surface": "manual_exploration",
        "description": "Use broad rg/manual exploration only if the earlier layers are insufficient.",
        "tools": [],
    },
]
BENCHMARK_LOG_ENV = "AGENTIUX_DEV_BENCHMARK_LOG"
GIT_ROUTE_CHECKOUT_UI_HINTS = {
    "button",
    "component",
    "copy",
    "label",
    "markup",
    "page",
    "playwright",
    "screen",
    "semantic",
    "ui",
    "view",
}
GIT_ROUTE_COMMAND_HINTS = {"branch", "commit", "git", "merge", "pr", "rebase", "staging", "worktree"}
TRIAGE_OWNER_ROUTE_HINTS = {
    "app",
    "button",
    "candidate",
    "copy",
    "cta",
    "entrypoint",
    "file",
    "files",
    "label",
    "owner",
    "owners",
    "package",
    "page",
    "route",
    "shared",
    "smallest",
}
TRIAGE_VERIFICATION_ROUTE_HINTS = {
    "assertion",
    "command",
    "commands",
    "contract",
    "failure",
    "failing",
    "health",
    "playwright",
    "readiness",
    "ready",
    "spec",
    "test",
    "tests",
    "triage",
    "verification",
    "verify",
}
TRIAGE_RELEASE_ROUTE_HINTS = {"deploy", "launch", "production", "release", "ship", "smoke"}
TRIAGE_ANALYSIS_ROUTE_HINTS = {
    "app",
    "copy",
    "cta",
    "entrypoint",
    "file",
    "files",
    "label",
    "owner",
    "owners",
    "package",
    "packages",
    "page",
    "route",
    "shared",
}
TRIAGE_COMMAND_DISCOVERY_HINTS = {"command", "commands", "manifest", "verification", "verify"}
TRIAGE_TEST_EVIDENCE_HINTS = {"playwright", "spec", "test", "tests", "verification", "verify"}
TRIAGE_SECONDARY_ROUTE_CONFIDENCE_DELTA = 0.12
TRIAGE_SECONDARY_ROUTE_SCORE_DELTA = 18
TRIAGE_MAX_ROUTE_COUNT = 2
TRIAGE_MAX_FILES = 5
TRIAGE_MAX_FILES_PER_FAMILY = 2
TRIAGE_MAX_PROOF_ASSERTIONS = 4
TRIAGE_MAX_DEPENDENCY_EDGES = 4
TRIAGE_CONFIG_FILENAMES = {
    "docker-compose.yml",
    "nest-cli.json",
    "nx.json",
    "playwright.config.ts",
    "pnpm-workspace.yaml",
    "tsconfig.base.json",
}
TRIAGE_DOC_PREFIXES = ("docs/", "references/")
TRIAGE_EXCLUDED_FAMILY_PREFIXES = {
    "admin": ["apps/admin/", "apps/server/src/admin/"],
    "docs": ["README.md", "docs/", "references/"],
    "server": ["apps/server/"],
    "storefront": ["apps/storefront/"],
    "tests": ["tests/", "apps/server/test/"],
}


def _limit(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(value, maximum))


def _surface_payload_stats(surface_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = SURFACE_PAYLOAD_CEILINGS[surface_name]
    payload_stats = {"surface": surface_name, "ceiling_bytes": ceiling}
    payload["payload"] = payload_stats
    actual_bytes = payload_size_bytes(payload)
    payload_stats.update(
        {
            "bytes": actual_bytes,
            "within_ceiling": actual_bytes <= ceiling,
            "overage_bytes": max(actual_bytes - ceiling, 0),
        }
    )
    return payload_stats


def _trim_search_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = SURFACE_PAYLOAD_CEILINGS["search_context_index"]
    while payload_size_bytes(payload) > ceiling and payload.get("recommended_capabilities"):
        payload["recommended_capabilities"] = payload["recommended_capabilities"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("matches"):
        payload["matches"] = payload["matches"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("modules"):
        payload["modules"] = payload["modules"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("recommended_capabilities"):
        payload["recommended_capabilities"] = payload["recommended_capabilities"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("matches"):
        payload["matches"] = payload["matches"][:-1]
    return payload


def _trim_capability_catalog_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = SURFACE_PAYLOAD_CEILINGS["show_capability_catalog"]
    while payload_size_bytes(payload) > ceiling and payload.get("entries"):
        payload["entries"] = payload["entries"][:-1]
    while payload_size_bytes(payload) > ceiling and any("why" in entry for entry in payload.get("entries", [])):
        trimmed = False
        for entry in reversed(payload.get("entries", [])):
            if entry.pop("why", None) is not None:
                trimmed = True
                break
        if not trimmed:
            break
    while payload_size_bytes(payload) > ceiling and any(entry.get("follow_up_paths") for entry in payload.get("entries", [])):
        trimmed = False
        for entry in reversed(payload.get("entries", [])):
            if entry.get("follow_up_paths"):
                entry["follow_up_paths"] = []
                trimmed = True
                break
        if not trimmed:
            break
    while payload_size_bytes(payload) > ceiling and payload.get("entries"):
        payload["entries"] = payload["entries"][:-1]
    return payload


def _trim_context_pack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = min(
        SURFACE_PAYLOAD_CEILINGS["show_workspace_context_pack"],
        SURFACE_WORKING_BUDGETS["show_workspace_context_pack"],
    )
    # `_surface_payload_stats` appends a small `payload` block after trimming.
    ceiling = max(ceiling - 256, 1024)
    context_pack = payload.get("context_pack") or {}
    resolved_route = payload.get("resolved_route") or {}
    route_candidates = payload.get("route_candidates") or []
    workspace_context = payload.get("workspace_context") or {}
    while payload_size_bytes(payload) > ceiling and context_pack.get("route_projection"):
        context_pack["route_projection"] = {}
    while payload_size_bytes(payload) > ceiling and resolved_route.get("why"):
        resolved_route.pop("why", None)
    while payload_size_bytes(payload) > ceiling and resolved_route.get("recommended_tools"):
        resolved_route["recommended_tools"] = resolved_route["recommended_tools"][:-1]
    while payload_size_bytes(payload) > ceiling and any(item.get("why") for item in route_candidates if isinstance(item, dict)):
        trimmed = False
        for item in reversed(route_candidates):
            if isinstance(item, dict) and item.get("why"):
                item.pop("why", None)
                trimmed = True
                break
        if not trimmed:
            break
    for route_key in (
        "title",
        "summary",
        "cost_hint",
        "summary_surfaces",
        "triggers",
        "tags",
        "recommended_skills",
        "primary_paths",
        "recommended_tools",
    ):
        while payload_size_bytes(payload) > ceiling and route_key in resolved_route:
            resolved_route.pop(route_key, None)
        while payload_size_bytes(payload) > ceiling and any(route_key in item for item in route_candidates if isinstance(item, dict)):
            trimmed = False
            for item in reversed(route_candidates):
                if isinstance(item, dict) and route_key in item:
                    item.pop(route_key, None)
                    trimmed = True
                    break
            if not trimmed:
                break
    while payload_size_bytes(payload) > ceiling and len(route_candidates) > 2:
        route_candidates.pop()
    for optional_key in (
        "changed_paths",
        "usage_stats",
        "last_used_routes",
        "last_used_tools",
        "project_memory",
        "design_summary",
        "testability_summary",
    ):
        while payload_size_bytes(payload) > ceiling and optional_key in workspace_context:
            workspace_context.pop(optional_key, None)
    while payload_size_bytes(payload) > ceiling and context_pack.get("command_suggestions"):
        context_pack["command_suggestions"] = context_pack["command_suggestions"][:-1]
    while payload_size_bytes(payload) > ceiling and context_pack.get("exact_candidate_commands_only"):
        context_pack["exact_candidate_commands_only"] = context_pack["exact_candidate_commands_only"][:-1]
    while payload_size_bytes(payload) > ceiling and context_pack.get("owner_candidates"):
        context_pack["owner_candidates"] = context_pack["owner_candidates"][:-1]
    while payload_size_bytes(payload) > ceiling and context_pack.get("next_read_paths"):
        context_pack["next_read_paths"] = context_pack["next_read_paths"][:-1]
    while payload_size_bytes(payload) > ceiling and context_pack.get("do_not_scan_paths"):
        context_pack["do_not_scan_paths"] = context_pack["do_not_scan_paths"][:-1]
    while payload_size_bytes(payload) > ceiling and context_pack.get("selected_tools"):
        context_pack["selected_tools"] = context_pack["selected_tools"][:-1]
    while payload_size_bytes(payload) > ceiling and context_pack.get("selected_chunks"):
        context_pack["selected_chunks"] = context_pack["selected_chunks"][:-1]
    return payload


def _trim_runtime_preflight_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = SURFACE_PAYLOAD_CEILINGS["show_runtime_preflight"]
    preflight = payload.get("preflight") or {}
    while payload_size_bytes(payload) > ceiling and preflight.get("owner_candidates"):
        preflight["owner_candidates"] = preflight["owner_candidates"][:-1]
    while payload_size_bytes(payload) > ceiling and preflight.get("command_suggestions"):
        preflight["command_suggestions"] = preflight["command_suggestions"][:-1]
    while payload_size_bytes(payload) > ceiling and preflight.get("selected_tools"):
        preflight["selected_tools"] = preflight["selected_tools"][:-1]
    while payload_size_bytes(payload) > ceiling and preflight.get("next_read_paths"):
        preflight["next_read_paths"] = preflight["next_read_paths"][:-1]
    while payload_size_bytes(payload) > ceiling and preflight.get("do_not_scan_paths"):
        preflight["do_not_scan_paths"] = preflight["do_not_scan_paths"][:-1]
    while payload_size_bytes(payload) > ceiling and preflight.get("proof_assertions"):
        preflight["proof_assertions"] = preflight["proof_assertions"][:-1]
    while payload_size_bytes(payload) > ceiling and preflight.get("dependency_edges"):
        preflight["dependency_edges"] = preflight["dependency_edges"][:-1]
    while payload_size_bytes(payload) > ceiling and preflight.get("supporting_evidence_files"):
        preflight["supporting_evidence_files"] = preflight["supporting_evidence_files"][:-1]
    return payload


def _trim_triage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = SURFACE_PAYLOAD_CEILINGS["triage_repo_request"]
    while payload_size_bytes(payload) > ceiling and payload.get("owner_candidates"):
        payload["owner_candidates"] = payload["owner_candidates"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("command_suggestions"):
        payload["command_suggestions"] = payload["command_suggestions"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("supporting_evidence_files"):
        payload["supporting_evidence_files"] = payload["supporting_evidence_files"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("proof_assertions"):
        payload["proof_assertions"] = payload["proof_assertions"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("dependency_edges"):
        payload["dependency_edges"] = payload["dependency_edges"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("candidate_files"):
        payload["candidate_files"] = payload["candidate_files"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("next_read_paths"):
        payload["next_read_paths"] = payload["next_read_paths"][:-1]
    return payload


def _trim_structure_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = SURFACE_PAYLOAD_CEILINGS["show_context_structure"]
    while payload_size_bytes(payload) > ceiling and payload.get("matches"):
        payload["matches"] = payload["matches"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("hotspots"):
        payload["hotspots"] = payload["hotspots"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("modules"):
        payload["modules"] = payload["modules"][:-1]
    return payload


def _trim_analysis_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = SURFACE_PAYLOAD_CEILINGS["run_analysis_audit"]
    while payload_size_bytes(payload) > ceiling and payload.get("recommended_follow_ups"):
        payload["recommended_follow_ups"] = payload["recommended_follow_ups"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("semantic_matches"):
        payload["semantic_matches"] = payload["semantic_matches"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("evidence"):
        payload["evidence"] = payload["evidence"][:-1]
    while payload_size_bytes(payload) > ceiling and payload.get("findings"):
        payload["findings"] = payload["findings"][:-1]
    return payload


def _benchmark_log_path() -> Path | None:
    raw_path = os.environ.get(BENCHMARK_LOG_ENV)
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _append_benchmark_record(record: dict[str, Any]) -> None:
    log_path = _benchmark_log_path()
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _record_surface_benchmark(
    surface_name: str,
    payload: dict[str, Any],
    *,
    route_id: str | None = None,
    retrieval_mode: str | None = None,
    semantic_mode: str | None = None,
    cache_status: str | None = None,
    selected_chunk_count: int | None = None,
    selected_tool_count: int | None = None,
    refresh_reason: str | None = None,
) -> None:
    payload_stats = payload.get("payload") or {}
    if not payload_stats:
        return
    _append_benchmark_record(
        {
            "surface": surface_name,
            "timestamp": now_iso(),
            "route_id": route_id,
            "retrieval_mode": retrieval_mode or ((payload.get("retrieval") or {}).get("mode")),
            "semantic_mode": semantic_mode or payload.get("semantic_mode"),
            "cache_status": cache_status,
            "payload_bytes": int(payload_stats.get("bytes") or 0),
            "ceiling_bytes": int(payload_stats.get("ceiling_bytes") or 0),
            "within_ceiling": bool(payload_stats.get("within_ceiling")),
            "selected_chunk_count": int(selected_chunk_count or 0),
            "selected_tool_count": int(selected_tool_count or 0),
            "refresh_reason": refresh_reason,
        }
    )


def _serialize_module(module: dict[str, Any]) -> dict[str, Any]:
    return {
        "module_id": module.get("module_id"),
        "path": module.get("path"),
        "kind": module.get("kind"),
        "manifest_path": module.get("manifest_path"),
        "file_count": module.get("file_count", 0),
        "indexed_file_count": module.get("indexed_file_count", 0),
        "language_counts": module.get("language_counts", {}),
        "local_fan_in": module.get("local_fan_in", 0),
        "local_fan_out": module.get("local_fan_out", 0),
        "hotspot_score": module.get("hotspot_score", 0),
        "hotspot_labels": module.get("hotspot_labels", []),
        "entrypoint_hints": module.get("entrypoint_hints", []),
        "large_file_count": module.get("large_file_count", 0),
    }


def _serialize_hotspot(hotspot: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_kind": hotspot.get("target_kind"),
        "path": hotspot.get("path"),
        "module_id": hotspot.get("module_id"),
        "language": hotspot.get("language"),
        "hotspot_score": hotspot.get("hotspot_score", 0),
        "hotspot_labels": hotspot.get("hotspot_labels", []),
        "local_fan_in": hotspot.get("local_fan_in", 0),
        "local_fan_out": hotspot.get("local_fan_out", 0),
        "large_file": hotspot.get("large_file"),
        "large_file_count": hotspot.get("large_file_count"),
    }


def _serialize_match(match: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "chunk_id": match.get("chunk_id"),
        "path": match.get("path"),
        "match_kind": match.get("chunk_kind", "file"),
        "anchor_title": match.get("anchor_title"),
        "anchor_kind": match.get("anchor_kind"),
        "summary": match.get("summary"),
        "module_id": match.get("module_id"),
        "language": match.get("language"),
        "line_start": match.get("line_start"),
        "line_end": match.get("line_end"),
        "section_level": match.get("section_level"),
        "hotspot_labels": match.get("hotspot_labels", []),
        "dependency_targets": match.get("dependency_targets", []),
        "score": match.get("score"),
        "match_source": match.get("match_source", "symbolic"),
        "why": match.get("why", {}),
    }
    if match.get("note_id"):
        payload["note_id"] = match.get("note_id")
    if match.get("snapshot_id"):
        payload["snapshot_id"] = match.get("snapshot_id")
    return payload


def _semantic_match_kind(match: dict[str, Any]) -> str:
    source_kind = str(match.get("source_kind") or "")
    if source_kind == "module_summary":
        return "module"
    if source_kind == "hotspot_cluster":
        return "hotspot"
    if source_kind == "project_note":
        return "project_memory"
    if source_kind == "generated_snapshot":
        return "project_memory"
    if source_kind.startswith("symbol"):
        return "symbol"
    if source_kind.startswith("doc_section"):
        return "doc_section"
    return "semantic_unit"


def _semantic_match_record(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": match.get("unit_id"),
        "chunk_kind": _semantic_match_kind(match),
        "path": match.get("path"),
        "module_id": match.get("module_id"),
        "language": None,
        "anchor_title": match.get("anchor_title"),
        "anchor_kind": match.get("anchor_kind"),
        "summary": match.get("summary_text"),
        "line_start": match.get("line_start"),
        "line_end": match.get("line_end"),
        "section_level": None,
        "hotspot_labels": match.get("hotspot_labels", []),
        "dependency_targets": match.get("dependency_targets", []),
        "score": match.get("score"),
        "match_source": "semantic_assisted",
        "why": match.get("why", {}),
        "note_id": match.get("note_id"),
        "snapshot_id": match.get("snapshot_id"),
    }


def _match_identity(match: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    return (
        match.get("path"),
        match.get("anchor_title"),
        match.get("chunk_kind"),
        match.get("note_id") or match.get("snapshot_id"),
    )


def _merge_semantic_matches(
    symbolic_matches: list[dict[str, Any]],
    semantic_matches: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen = set()
    semantic_quota = 0
    if semantic_matches and limit > 4:
        semantic_quota = min(len(semantic_matches), max(1, min(2, limit // 3)))
    symbolic_budget = limit if not semantic_quota else max(limit - semantic_quota, min(4, limit))
    for match in symbolic_matches:
        candidate = {**match, "match_source": match.get("match_source", "symbolic")}
        identity = _match_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(candidate)
        if len(merged) >= symbolic_budget:
            break
    for match in semantic_matches:
        candidate = _semantic_match_record(match)
        identity = _match_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(candidate)
        if len(merged) >= limit:
            break
    if len(merged) < limit:
        for match in symbolic_matches:
            candidate = {**match, "match_source": match.get("match_source", "symbolic")}
            identity = _match_identity(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(candidate)
            if len(merged) >= limit:
                break
    return merged[:limit]


def _dedupe_matches_by_path(matches: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    fallback_seen: set[tuple[Any, ...]] = set()
    for match in matches:
        path = match.get("path")
        if isinstance(path, str) and path:
            if path in seen_paths:
                continue
            seen_paths.add(path)
        else:
            identity = _match_identity(match)
            if identity in fallback_seen:
                continue
            fallback_seen.add(identity)
        deduped.append(match)
        if len(deduped) >= limit:
            break
    return deduped


def _normalize_module_path(workspace_path: Path, module_path: str | None) -> str | None:
    if not module_path:
        return None
    normalized = str(module_path).strip()
    if not normalized:
        return None
    candidate = Path(normalized).expanduser()
    if candidate.is_absolute():
        try:
            relative = candidate.resolve().relative_to(workspace_path)
        except ValueError:
            return normalized
        return "." if str(relative) == "." else relative.as_posix()
    return "." if normalized == "." else Path(normalized).as_posix()


def _analysis_explicit(route_id: str | None, route: dict[str, Any] | None) -> bool:
    del route
    return route_id == "analysis"


def _semantic_support(
    cache_paths: dict[str, Path],
    *,
    semantic_mode: str | None,
    analysis_explicit: bool,
    query_text: str | None = None,
    limit: int = 8,
    module_path: str | None = None,
) -> dict[str, Any]:
    normalized_mode, enabled = semantic_mode_enabled(semantic_mode, analysis_explicit=analysis_explicit)
    manifest = load_semantic_manifest(cache_paths)
    summary = semantic_summary_from_manifest(manifest)
    if not enabled or not query_text:
        return {
            "semantic_mode": normalized_mode,
            "enabled": False,
            "backend_status": summary.get("backend_status"),
            "semantic_summary": summary,
            "matches": [],
        }
    semantic_payload = search_semantic_units(
        cache_paths,
        query_text=query_text,
        limit=limit,
        module_path=module_path,
    )
    return {
        "semantic_mode": normalized_mode,
        "enabled": True,
        "backend_status": semantic_payload.get("backend_status"),
        "semantic_summary": semantic_payload.get("semantic_summary") or summary,
        "matches": semantic_payload.get("matches") or [],
    }


def _semantic_manifest_fingerprint(cache_paths: dict[str, Path]) -> str:
    manifest = load_semantic_manifest(cache_paths)
    return short_hash(
        "|".join(
            [
                str(manifest.get("embedder_version") or ""),
                str(manifest.get("structure_fingerprint") or ""),
                str(manifest.get("note_fingerprint") or ""),
                str(manifest.get("snapshot_fingerprint") or ""),
            ]
        ),
        length=16,
    )


def _path_within_module(path_value: str | None, module_path: str | None) -> bool:
    if not path_value or not module_path or module_path == ".":
        return bool(path_value)
    path_obj = Path(path_value)
    module_obj = Path(module_path)
    return path_obj == module_obj or module_obj in path_obj.parents


def _select_modules_for_path(modules: list[dict[str, Any]], module_path: str | None) -> list[dict[str, Any]]:
    if not module_path:
        return modules
    matched = [
        module
        for module in modules
        if _path_within_module(module.get("path"), module_path) or _path_within_module(module_path, module.get("path"))
    ]
    if not matched:
        return []
    best_depth = max(len(Path(str(module.get("path") or ".")).parts) for module in matched)
    return [module for module in matched if len(Path(str(module.get("path") or ".")).parts) == best_depth]


def _catalog_entry_score(
    entry: dict[str, Any],
    query_tokens: list[str],
    query_expanded: set[str] | None = None,
    route_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    score = 0
    why: dict[str, Any] = {}
    query_set = query_expanded if query_expanded is not None else expand_token_set(query_tokens, TOKEN_SYNONYMS)
    title_score, title_matches = score_preexpanded_query_match(
        query_set,
        tokenize_text(entry.get("title")),
        5,
        synonyms=TOKEN_SYNONYMS,
    )
    if title_matches:
        why["matched_title"] = title_matches
    score += title_score
    tag_score, tag_matches = score_preexpanded_query_match(query_set, entry.get("tags", []), 6, synonyms=TOKEN_SYNONYMS)
    if tag_matches:
        why["matched_tags"] = tag_matches
    score += tag_score
    summary_score, summary_matches = score_preexpanded_query_match(
        query_set,
        tokenize_text(entry.get("summary")),
        3,
        synonyms=TOKEN_SYNONYMS,
    )
    if summary_matches:
        why["matched_summary"] = summary_matches
    score += summary_score
    path_score, path_matches = score_preexpanded_query_match(
        query_set,
        tokenize_text(entry.get("path")),
        4,
        synonyms=TOKEN_SYNONYMS,
    )
    if path_matches:
        why["matched_path"] = path_matches
    score += path_score
    trigger_score, trigger_matches = score_preexpanded_query_match(
        query_set,
        entry.get("triggers", []),
        5,
        synonyms=TOKEN_SYNONYMS,
    )
    if trigger_matches:
        why["matched_triggers"] = trigger_matches
    score += trigger_score
    if route_id and route_id in entry.get("related_routes", []):
        score += 6
        why["route_match"] = route_id
    return score, why


def _route_score_adjustment(route_id: str, query_tokens: list[str], query_expanded: set[str]) -> tuple[int, dict[str, Any]]:
    owner_hints = sorted(query_expanded.intersection(TRIAGE_OWNER_ROUTE_HINTS))
    verification_hints = sorted(query_expanded.intersection(TRIAGE_VERIFICATION_ROUTE_HINTS))
    release_hints = sorted(query_expanded.intersection(TRIAGE_RELEASE_ROUTE_HINTS))
    analysis_hints = sorted(query_expanded.intersection(TRIAGE_ANALYSIS_ROUTE_HINTS))
    command_hints = sorted(query_expanded.intersection(TRIAGE_COMMAND_DISCOVERY_HINTS))
    git_command_hints = sorted(set(query_tokens).intersection(GIT_ROUTE_COMMAND_HINTS))
    checkout_ui_hints = sorted(query_expanded.intersection(GIT_ROUTE_CHECKOUT_UI_HINTS))
    if route_id == "git":
        if git_command_hints:
            return 0, {}
        false_positive_hints = sorted(dict.fromkeys([*checkout_ui_hints, *owner_hints, *verification_hints]))
        if false_positive_hints:
            return (
                -(36 + min(len(false_positive_hints) * 5, 28)),
                {"deprioritized_non_git_repo_triage": false_positive_hints[:6]},
            )
        if "checkout" in query_tokens:
            return -24, {"deprioritized_checkout_without_git_intent": ["checkout"]}
        return 0, {}
    if route_id == "release":
        if release_hints:
            return 0, {}
        false_positive_hints = sorted(dict.fromkeys([*owner_hints, *verification_hints]))
        if false_positive_hints:
            return (
                -(24 + min(len(false_positive_hints) * 4, 20)),
                {"deprioritized_non_release_repo_triage": false_positive_hints[:6]},
            )
        return 0, {}
    if route_id == "verification":
        score = 0
        why: dict[str, Any] = {}
        if verification_hints:
            score += 24 + min(len(verification_hints) * 6, 24)
            why["boosted_verification_triage"] = verification_hints[:6]
        if command_hints and verification_hints:
            score += 10
            why["boosted_command_evidence"] = command_hints[:6]
        if owner_hints and verification_hints:
            score += 8
            why["boosted_owner_verification_overlap"] = owner_hints[:4]
        return score, why
    if route_id == "analysis":
        score = 0
        why: dict[str, Any] = {}
        if analysis_hints:
            score += 22 + min(len(analysis_hints) * 5, 20)
            why["boosted_owner_file_triage"] = analysis_hints[:6]
        if owner_hints and not verification_hints:
            score += 8
            why["boosted_owner_focus"] = owner_hints[:4]
        elif owner_hints and verification_hints:
            score += 6
            why["boosted_mixed_owner_triage"] = owner_hints[:4]
        return score, why
    return 0, {}


def _resolve_intent_candidates(
    request_text: str | None,
    route_id: str | None,
    usage: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    routes = intent_routes()
    if route_id:
        exact = next((route for route in routes if route["route_id"] == route_id), None)
        if exact is None:
            raise ValueError(f"Unknown route id: {route_id}")
        return exact, [dict(exact, score=100, confidence=1.0, why={"explicit_route_id": route_id})], "exact"

    query_tokens = tokenize_text(request_text)
    if not query_tokens:
        ordered = [dict(route, score=0, confidence=0.0) for route in routes]
        return None, ordered, "unresolved"

    query_expanded = expand_token_set(query_tokens, TOKEN_SYNONYMS)
    recent_routes = set((usage or {}).get("recent_route_ids", []))
    recent_tools = set((usage or {}).get("recent_tool_ids", []))
    scored: list[dict[str, Any]] = []
    for route in routes:
        score = 0
        why: dict[str, Any] = {}
        tag_score, tag_matches = score_preexpanded_query_match(
            query_expanded,
            route.get("tags", []),
            6,
            synonyms=TOKEN_SYNONYMS,
        )
        if tag_matches:
            why["matched_tags"] = tag_matches
        score += tag_score
        trigger_score, trigger_matches = score_preexpanded_query_match(
            query_expanded,
            route.get("triggers", []),
            7,
            synonyms=TOKEN_SYNONYMS,
        )
        if trigger_matches:
            why["matched_triggers"] = trigger_matches
        score += trigger_score
        title_score, title_matches = score_preexpanded_query_match(
            query_expanded,
            tokenize_text(route.get("title")),
            4,
            synonyms=TOKEN_SYNONYMS,
        )
        if title_matches:
            why["matched_title"] = title_matches
        score += title_score
        summary_score, summary_matches = score_preexpanded_query_match(
            query_expanded,
            tokenize_text(route.get("summary")),
            3,
            synonyms=TOKEN_SYNONYMS,
        )
        if summary_matches:
            why["matched_summary"] = summary_matches
        score += summary_score
        if route["route_id"] in recent_routes:
            score += 2
            why["recent_route"] = route["route_id"]
        recent_tool_matches = sorted(recent_tools.intersection(route.get("recommended_tools", [])))[:4]
        if recent_tool_matches:
            score += 2
            why["recent_tools"] = recent_tool_matches
        score_adjustment, adjustment_why = _route_score_adjustment(route["route_id"], query_tokens, query_expanded)
        if score_adjustment:
            score += score_adjustment
            why.update(adjustment_why)
        confidence = max(min(score / 24, 1.0), 0.0)
        scored.append(dict(route, score=score, confidence=round(confidence, 2), why=why))
    scored.sort(key=lambda item: (-item["score"], item["route_id"]))
    resolved = scored[0] if scored and scored[0]["score"] >= ROUTE_SCORE_MIN else None
    if resolved is None:
        return None, scored, "unresolved"
    second_score = scored[1]["score"] if len(scored) > 1 else 0
    if second_score >= ROUTE_SCORE_MIN and resolved["score"] - second_score <= ROUTE_SCORE_AMBIGUOUS_DELTA:
        return resolved, scored, "ambiguous"
    return resolved, scored, "matched"


def show_intent_route(route_id: str | None = None, request_text: str | None = None) -> dict[str, Any]:
    resolved, candidates, resolution_status = _resolve_intent_candidates(request_text, route_id)
    retrieval_mode = infer_retrieval_mode(request_text)
    payload = {
        "plugin": plugin_info(),
        "catalog_root": str(plugin_catalog_root()),
        "request_text": request_text,
        "requested_route_id": route_id,
        "resolved_route": resolved,
        "resolution_status": resolution_status,
        "requires_confirmation": resolution_status == "ambiguous",
        "retrieval": retrieval_policy_payload("show_intent_route", retrieval_mode),
        "route_candidates": candidates[:3],
        "retrieval_ladder": RETRIEVAL_LADDER,
    }
    _surface_payload_stats("show_intent_route", payload)
    _record_surface_benchmark(
        "show_intent_route",
        payload,
        route_id=(resolved or {}).get("route_id") or route_id,
        retrieval_mode=retrieval_mode,
        selected_tool_count=len((resolved or {}).get("recommended_tools") or []),
    )
    return payload


def show_capability_catalog(
    kind: str | None = None,
    route_id: str | None = None,
    query_text: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    if kind and kind not in {"skill", "mcp_tool", "script", "reference"}:
        raise ValueError(f"Unsupported capability kind: {kind}")
    all_entries = all_capability_entries()
    entries = list(all_entries)
    if kind:
        entries = [entry for entry in entries if entry["kind"] == kind]
    if route_id:
        route_map = route_index()
        if route_id not in route_map:
            raise ValueError(f"Unknown route id: {route_id}")
        entries = [entry for entry in entries if route_id in entry.get("related_routes", [])]

    query_tokens = tokenize_text(query_text)
    query_expanded = expand_token_set(query_tokens, TOKEN_SYNONYMS)
    scored_entries = []
    for entry in entries:
        score, why = _catalog_entry_score(entry, query_tokens, query_expanded=query_expanded, route_id=route_id)
        scored_entries.append(dict(entry, score=score, why=why))
    if query_tokens:
        scored_entries = [entry for entry in scored_entries if entry["score"] > 0]
        scored_entries.sort(key=lambda item: (-item["score"], item["kind"], item["title"]))
    else:
        scored_entries.sort(key=lambda item: (item["kind"], item["title"]))
    selected = scored_entries[: _limit(limit, 20, 100)]
    retrieval_mode = infer_retrieval_mode(query_text or "show capability catalog")
    payload = {
        "plugin": plugin_info(),
        "catalog_root": str(plugin_catalog_root()),
        "filter": {
            "kind": kind,
            "route_id": route_id,
            "query_text": query_text,
            "limit": _limit(limit, 20, 100),
        },
        "retrieval": retrieval_policy_payload("show_capability_catalog", retrieval_mode),
        "catalog_counts": {
            "skills": len([entry for entry in all_entries if entry["kind"] == "skill"]),
            "mcp_tools": len([entry for entry in all_entries if entry["kind"] == "mcp_tool"]),
            "scripts": len([entry for entry in all_entries if entry["kind"] == "script"]),
            "references": len([entry for entry in all_entries if entry["kind"] == "reference"]),
        },
        "total_matches": len(scored_entries),
        "entries": selected,
    }
    _trim_capability_catalog_payload(payload)
    _surface_payload_stats("show_capability_catalog", payload)
    _record_surface_benchmark(
        "show_capability_catalog",
        payload,
        route_id=route_id,
        retrieval_mode=retrieval_mode,
        selected_tool_count=len(payload.get("entries") or []),
    )
    return payload


def _chunk_score(
    chunk: dict[str, Any],
    query_tokens: list[str],
    route: dict[str, Any] | None,
    workspace_context: dict[str, Any],
    *,
    query_expanded: set[str] | None = None,
    changed_path_set: set[str] | None = None,
    current_token_set: set[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    score = 0
    why: dict[str, Any] = {}
    query_set = query_expanded if query_expanded is not None else expand_token_set(query_tokens, TOKEN_SYNONYMS)
    tag_score, tag_matches = score_preexpanded_query_match(query_set, chunk.get("tags", []), 6, synonyms=TOKEN_SYNONYMS)
    if tag_matches:
        why["matched_tags"] = tag_matches
    score += tag_score
    summary_score, summary_matches = score_preexpanded_query_match(
        query_set,
        tokenize_text(chunk.get("summary")),
        4,
        synonyms=TOKEN_SYNONYMS,
    )
    if summary_matches:
        why["matched_summary"] = summary_matches
    score += summary_score
    path_score, path_matches = score_preexpanded_query_match(
        query_set,
        tokenize_text(chunk.get("path")),
        5,
        synonyms=TOKEN_SYNONYMS,
    )
    if path_matches:
        why["matched_path"] = path_matches
    score += path_score
    symbol_score, symbol_matches = score_preexpanded_query_match(
        query_set,
        tokenize_text(" ".join(chunk.get("symbols", []))),
        5,
        synonyms=TOKEN_SYNONYMS,
    )
    if symbol_matches:
        why["matched_symbols"] = symbol_matches
    score += symbol_score
    if route:
        route_tokens = list(route.get("tags", [])) + list(route.get("triggers", []))
        route_score, route_matches = score_preexpanded_query_match(query_set, route_tokens, 1, synonyms=TOKEN_SYNONYMS)
        if route_matches:
            why["route_context_tokens"] = route_matches
        score += route_score
        if route["route_id"] in chunk.get("route_hints", []):
            score += 3
            why["route_hint"] = route["route_id"]
        profile = ROUTE_PROFILES.get(route["route_id"], {})
        relative_path = Path(chunk.get("path", ""))
        if relative_path.name in profile.get("priority_files", set()):
            score += 5
            why["profile_file"] = relative_path.name
        profile_dirs = sorted(set(relative_path.parts).intersection(set(profile.get("priority_dirs", set()))))
        if profile_dirs:
            score += len(profile_dirs) * 4
            why["profile_dirs"] = profile_dirs
        profile_path_tokens = sorted(set(tokenize_text(" ".join(relative_path.parts))).intersection(set(profile.get("path_tokens", set()))))
        if profile_path_tokens:
            score += len(profile_path_tokens) * 3
            why["profile_path_tokens"] = profile_path_tokens
        if route["route_id"] == "plugin-dev":
            dashboard_budget_query = "dashboard" in query_set and bool(
                {"benchmark", "budget", "latency", "payload", "performance", "telemetry"}.intersection(query_set)
            )
            if "scripts" in relative_path.parts:
                score += 4
                why["plugin_runtime_path"] = True
            if relative_path.name == "agentiux_dev_e2e_support.py" and dashboard_budget_query:
                score += 48
                why["dashboard_budget_owner"] = True
            if "tests" in relative_path.parts and not {"smoke", "test", "verification"}.intersection(query_set):
                test_penalty = 8 + (24 if dashboard_budget_query else 0)
                score -= test_penalty
                why["deprioritized_test_path"] = test_penalty
            if (
                relative_path.name in {"agentiux_dev_gui.py", "agentiux_dev_lib.py"}
                and dashboard_budget_query
                and not {"client", "render", "ui"}.intersection(query_set)
            ):
                score -= 8
                why["deprioritized_dashboard_wrapper"] = True
    if chunk.get("path") in (changed_path_set if changed_path_set is not None else set(workspace_context.get("changed_paths", []))):
        score += 4
        why["changed_path"] = True
    if chunk.get("source_kind") == "project_memory" and chunk.get("pin_state") == "pinned" and chunk.get("note_status") == "active":
        score += 6
        why["pinned_project_memory"] = True
    current_tokens = current_token_set
    if current_tokens is None:
        current_tokens = expand_token_set(
            tokenize_text(
                " ".join(
                    value
                    for value in [
                        (workspace_context.get("current_workstream") or {}).get("title", ""),
                        (workspace_context.get("current_task") or {}).get("title", ""),
                    ]
                )
            ),
            TOKEN_SYNONYMS,
        )
    current_score, current_matches = score_preexpanded_set_match(query_set, current_tokens, 2)
    if current_matches:
        why["matched_current_scope"] = current_matches
    score += current_score
    return score, why


def _promote_plugin_dev_runtime_owner(
    scored_chunks: list[dict[str, Any]],
    route: dict[str, Any] | None,
    query_expanded: set[str],
) -> list[dict[str, Any]]:
    if not route or route.get("route_id") != "plugin-dev":
        return scored_chunks
    dashboard_budget_query = "dashboard" in query_expanded and bool(
        {"benchmark", "budget", "latency", "payload", "performance", "telemetry"}.intersection(query_expanded)
    )
    if not dashboard_budget_query:
        return scored_chunks
    owner_path = "plugins/agentiux-dev/scripts/agentiux_dev_e2e_support.py"
    owner_index = next((index for index, item in enumerate(scored_chunks) if item.get("path") == owner_path), None)
    if owner_index is None or owner_index <= 3:
        return scored_chunks
    promoted = list(scored_chunks)
    owner = dict(promoted.pop(owner_index))
    owner_why = dict(owner.get("why") or {})
    owner_why["promoted_runtime_owner"] = True
    owner["why"] = owner_why
    promoted.insert(3, owner)
    return promoted


def _search_bundle_matches(
    *,
    query_text: str,
    route_id: str | None,
    workspace_context: dict[str, Any],
    store_path: Path,
    usage: dict[str, Any],
    match_limit: int,
    capability_limit: int,
    include_recommended_capabilities: bool = True,
    module_path: str | None = None,
    chunk_kinds: list[str] | None = None,
) -> dict[str, Any]:
    route, route_candidates, route_status = _resolve_intent_candidates(query_text, route_id, usage=usage)
    projected_route_id = resolve_route_hint_override(
        requested_route_id=route_id,
        selected_route_id=route["route_id"] if route else None,
        request_text=query_text,
    )
    route_projection = _load_route_shortlist_projection(
        store_path=store_path,
        workspace_context=workspace_context,
        route_id=projected_route_id,
    )
    query_tokens = tokenize_text(query_text)
    query_expanded = expand_token_set(query_tokens, TOKEN_SYNONYMS)
    changed_path_set = set(workspace_context.get("changed_paths", []))
    current_token_set = expand_token_set(
        tokenize_text(
            " ".join(
                value
                for value in [
                    (workspace_context.get("current_workstream") or {}).get("title", ""),
                    (workspace_context.get("current_task") or {}).get("title", ""),
                ]
            )
        ),
        TOKEN_SYNONYMS,
    )
    candidate_chunks = search_chunks(
        store_path,
        query_tokens=sorted(query_expanded) or query_tokens,
        route_id=route["route_id"] if route else None,
        changed_paths=list(changed_path_set),
        module_path=module_path,
        chunk_kinds=chunk_kinds,
        limit=max(match_limit * 8, 48),
    )
    scored_chunks = []
    for chunk in candidate_chunks:
        score, why = _chunk_score(
            chunk,
            query_tokens,
            route,
            workspace_context,
            query_expanded=query_expanded,
            changed_path_set=changed_path_set,
            current_token_set=current_token_set,
        )
        if score <= 0:
            continue
        scored_chunks.append({**chunk, "score": score, "why": why})
    scored_chunks.sort(key=lambda item: (-item["score"], item["path"]))
    scored_chunks = _augment_with_route_projection(
        scored_chunks,
        route_projection=route_projection,
    )
    scored_chunks = _promote_plugin_dev_runtime_owner(scored_chunks, route, query_expanded)
    module_summaries = load_module_summaries(
        store_path,
        limit=8,
        module_path=module_path,
        preferred_paths=[item.get("path") for item in scored_chunks[:match_limit] if item.get("path")],
    )
    recommended_capabilities: list[dict[str, Any]] = []
    if include_recommended_capabilities and capability_limit > 0 and not (route_id and route and route_status == "exact"):
        capability_catalog = show_capability_catalog(
            route_id=route["route_id"] if route else None,
            query_text=query_text,
            limit=capability_limit,
        )
        recommended_capabilities = capability_catalog["entries"][:capability_limit]
    return {
        "route": route,
        "route_candidates": route_candidates[:3],
        "route_status": route_status,
        "matches": scored_chunks[:match_limit],
        "match_count": len(scored_chunks),
        "recommended_capabilities": recommended_capabilities,
        "modules": module_summaries[:8],
        "route_projection": route_projection,
        "projected_route_id": projected_route_id,
    }


def search_context_index(
    workspace: str | Path,
    query_text: str,
    route_id: str | None = None,
    limit: int | None = 8,
    semantic_mode: str | None = "disabled",
) -> dict[str, Any]:
    if not tokenize_text(query_text):
        raise ValueError("query_text is required")
    retrieval_mode = infer_retrieval_mode(query_text)
    profile = retrieval_mode_profile(retrieval_mode)
    match_limit = _limit(limit, 8, profile["max_match_limit"])
    refresh_result, cache_paths, workspace_context, usage, _structure_index = load_workspace_context_bundle(
        workspace,
        query_text=query_text,
        route_id=route_id,
        retrieval_mode=retrieval_mode,
    )
    search_bundle = _search_bundle_matches(
        query_text=query_text,
        route_id=route_id,
        workspace_context=workspace_context,
        store_path=cache_paths["context_store"],
        usage=usage,
        match_limit=match_limit,
        capability_limit=profile["max_selected_tool_limit"],
    )
    semantic_support = _semantic_support(
        cache_paths,
        semantic_mode=semantic_mode,
        analysis_explicit=_analysis_explicit(route_id, search_bundle["route"]),
        query_text=query_text,
        limit=match_limit,
    )
    merged_matches = _dedupe_matches_by_path(
        _merge_semantic_matches(
            search_bundle["matches"],
            semantic_support["matches"],
            limit=match_limit,
        ),
        limit=match_limit,
    )
    if semantic_support["semantic_mode"] == "auto" and not _analysis_explicit(route_id, search_bundle["route"]):
        merged_matches = [
            match
            for match in merged_matches
            if str(match.get("match_source") or "symbolic") == "symbolic"
        ]
    record_search_stats(
        cache_paths,
        query_text=query_text,
        match_count=search_bundle["match_count"],
        route_status=search_bundle["route_status"],
        route_id=search_bundle["route"]["route_id"] if search_bundle["route"] else None,
    )
    payload = {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "cache_root": str(cache_paths["root"]),
        "index_status": refresh_result["status"],
        "index_refresh_reason": refresh_result.get("refresh_reason"),
        "storage_backend": refresh_result.get("storage_backend"),
        "storage_summary": refresh_result.get("storage_summary"),
        "context_store_path": refresh_result.get("context_store_path"),
        "query_text": query_text,
        "resolved_route": search_bundle["route"],
        "route_resolution_status": search_bundle["route_status"],
        "requires_route_confirmation": search_bundle["route_status"] == "ambiguous",
        "retrieval": retrieval_policy_payload("search_context_index", retrieval_mode),
        "semantic_mode": semantic_support["semantic_mode"],
        "semantic_backend_status": semantic_support["backend_status"],
        "route_candidates": search_bundle["route_candidates"],
        "workspace_context": compact_workspace_context(workspace_context),
        "modules": [_serialize_module(module) for module in search_bundle["modules"]],
        "matches": [_serialize_match(match) for match in merged_matches],
        "recommended_capabilities": search_bundle["recommended_capabilities"],
    }
    _trim_search_payload(payload)
    _surface_payload_stats("search_context_index", payload)
    _record_surface_benchmark(
        "search_context_index",
        payload,
        route_id=search_bundle["route"]["route_id"] if search_bundle["route"] else None,
        retrieval_mode=retrieval_mode,
        semantic_mode=semantic_support["semantic_mode"],
        selected_chunk_count=len(payload.get("matches") or []),
        selected_tool_count=len(payload.get("recommended_capabilities") or []),
        refresh_reason=refresh_result.get("refresh_reason"),
    )
    return payload


def _semantic_cache_key(
    query_text: str,
    catalog_digest: str,
    route_id: str | None,
    semantic_mode: str,
    *,
    semantic_enabled: bool,
    limit_hint: int | None = None,
) -> str:
    normalized_tokens = " ".join(sorted(dict.fromkeys(tokenize_text(query_text))))
    return short_hash(
        f"{catalog_digest}:{route_id or 'none'}:{semantic_mode}:{int(semantic_enabled)}:{limit_hint or 0}:{normalized_tokens}",
        length=16,
    )


def _pinned_project_memory_selected_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [
        {"chunk_id": chunk["chunk_id"], "path": chunk["path"], "summary": chunk["summary"], "score": 999}
        for chunk in chunks
        if chunk.get("source_kind") == "project_memory"
        and chunk.get("pin_state") == "pinned"
        and chunk.get("note_status") == "active"
    ]
    items.sort(key=lambda item: item["path"])
    return items[:12]


def _selected_chunk_payload_from_match(match: dict[str, Any]) -> dict[str, Any] | None:
    path_text = str(match.get("path") or "").strip()
    if not path_text:
        return None
    chunk_id = str(match.get("chunk_id") or "").strip()
    if not chunk_id:
        chunk_id = short_hash(
            f"selected-chunk:{path_text}:{match.get('anchor_title') or ''}:{match.get('match_source') or 'symbolic'}",
            length=16,
        )
    return {
        "chunk_id": chunk_id,
        "path": path_text,
        "summary": match.get("summary"),
        "score": match.get("score"),
        "match_source": match.get("match_source", "symbolic"),
    }


def _scope_signature(workspace_context: dict[str, Any]) -> str | None:
    current_workstream = workspace_context.get("current_workstream") or {}
    current_task = workspace_context.get("current_task") or {}
    workstream_id = str(current_workstream.get("workstream_id") or "").strip()
    task_id = str(current_task.get("task_id") or "").strip()
    workstream_title = str(current_workstream.get("title") or "").strip().lower()
    task_title = str(current_task.get("title") or "").strip().lower()
    if not any((workstream_id, task_id, workstream_title, task_title)):
        return None
    return short_hash(
        f"scope:{workstream_id}:{task_id}:{workstream_title}:{task_title}",
        length=16,
    )


def _task_intent_signature(request_text: str | None, route_id: str | None, *, limit_hint: int | None = None) -> str:
    retrieval_mode = infer_retrieval_mode(request_text or "")
    normalized_tokens = " ".join(sorted(dict.fromkeys(tokenize_text(request_text or ""))))
    return short_hash(
        f"task-intent:{route_id or 'none'}:{retrieval_mode}:{limit_hint or 0}:{normalized_tokens}",
        length=16,
    )


def _task_retrieval_cache_key(
    request_text: str | None,
    *,
    route_id: str | None,
    semantic_mode: str,
    workspace_context: dict[str, Any],
    limit_hint: int | None = None,
) -> str | None:
    scope_signature = _scope_signature(workspace_context)
    if not scope_signature or not request_text:
        return None
    intent_signature = _task_intent_signature(request_text, route_id, limit_hint=limit_hint)
    return short_hash(
        f"task-retrieval:{scope_signature}:{route_id or 'none'}:{semantic_mode}:{intent_signature}",
        length=16,
    )


def _load_ownership_graph(
    *,
    store_path: Path,
    workspace_context: dict[str, Any],
) -> dict[str, Any] | None:
    workspace_fingerprint = str(workspace_context.get("workspace_fingerprint") or "").strip()
    cached = read_query_cache(
        store_path,
        cache_kind=QUERY_CACHE_OWNERSHIP_GRAPH_KIND,
        cache_key=workspace_fingerprint or "current",
    )
    if not cached:
        return None
    payload = cached.get("payload") or {}
    if (
        cached.get("workspace_fingerprint") != workspace_context.get("workspace_fingerprint")
        or cached.get("catalog_digest") != workspace_context.get("catalog_digest")
        or int(payload.get("schema_version") or 0) < 1
    ):
        return None
    return payload


def _load_route_shortlist_projection(
    *,
    store_path: Path,
    workspace_context: dict[str, Any],
    route_id: str | None,
) -> dict[str, Any] | None:
    if not route_id:
        return None
    cached = read_query_cache(
        store_path,
        cache_kind=QUERY_CACHE_ROUTE_SHORTLIST_KIND,
        cache_key=route_id,
    )
    if not cached:
        return None
    payload = cached.get("payload") or {}
    if (
        cached.get("workspace_fingerprint") != workspace_context.get("workspace_fingerprint")
        or cached.get("catalog_digest") != workspace_context.get("catalog_digest")
        or int(payload.get("schema_version") or 0) < 1
    ):
        return None
    source_hashes = cached.get("source_hashes") or {}
    if source_hashes and load_source_hashes(store_path, list(source_hashes)) != {
        path: value for path, value in source_hashes.items() if isinstance(path, str)
    }:
        return None
    return payload


def _augment_with_route_projection(
    scored_chunks: list[dict[str, Any]],
    *,
    route_projection: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not route_projection:
        return scored_chunks
    projected_owners = [
        item
        for item in (route_projection.get("owner_candidates") or [])
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item.get("path")
    ]
    priority_index = {
        str(path): rank
        for rank, path in enumerate(route_projection.get("priority_paths") or [])
        if isinstance(path, str) and path
    }
    promoted: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for item in scored_chunks:
        candidate = dict(item)
        path_text = str(candidate.get("path") or "").strip()
        if path_text in priority_index:
            candidate["score"] = float(candidate.get("score") or 0.0) + max(8.0 - priority_index[path_text], 2.5)
            why = dict(candidate.get("why") or {})
            why["matched_route_projection"] = route_projection.get("route_id")
            why["route_projection_rank"] = priority_index[path_text] + 1
            candidate["why"] = why
        promoted.append(candidate)
        if path_text:
            seen_paths.add(path_text)
    for rank, owner in enumerate(projected_owners):
        path_text = str(owner.get("path") or "").strip()
        if not path_text or path_text in seen_paths:
            continue
        promoted.append(
            {
                "chunk_id": short_hash(f"route-projection:{path_text}", length=16),
                "chunk_kind": "file",
                "path": path_text,
                "summary": owner.get("summary"),
                "score": max(7.0 - rank, 2.0),
                "why": {
                    "route_projection_only": True,
                    "route_projection_rank": rank + 1,
                },
                "match_source": "route_projection",
            }
        )
        seen_paths.add(path_text)
    promoted.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("path") or "")))
    return promoted


def _effective_preflight_request_text(
    request_text: str | None,
    compact_context: dict[str, Any],
    usage: dict[str, Any],
) -> tuple[str | None, str]:
    normalized_request = str(request_text or "").strip()
    if normalized_request:
        return normalized_request, "request_text"
    persisted_request = str(usage.get("last_high_confidence_request_text") or "").strip()
    if persisted_request:
        return persisted_request, "last_high_confidence_request"
    current_task = compact_context.get("current_task") or {}
    task_title = str(current_task.get("title") or "").strip()
    if task_title:
        return task_title, "current_task"
    current_workstream = compact_context.get("current_workstream") or {}
    workstream_title = str(current_workstream.get("title") or "").strip()
    if workstream_title:
        return workstream_title, "current_workstream"
    return None, "none"


def _build_runtime_preflight_payload(
    *,
    workspace_root: Path | None,
    effective_request_text: str | None,
    request_text_source: str,
    route: dict[str, Any] | None,
    route_status: str,
    compact_context: dict[str, Any],
    retrieval_mode: str,
    context_pack_payload: dict[str, Any],
    ownership_graph: dict[str, Any] | None,
) -> dict[str, Any]:
    context_pack = context_pack_payload.get("context_pack") or {}
    owner_candidates = [
        dict(item)
        for item in (context_pack.get("owner_candidates") or [])
        if isinstance(item, dict)
    ]
    command_suggestions = [
        dict(item)
        for item in (context_pack.get("command_suggestions") or [])
        if isinstance(item, dict)
    ]
    confidence = float(context_pack.get("confidence") or 0.0)
    constraints = parse_runtime_triage_constraints(effective_request_text)
    scoring_command_suggestions = [] if constraints.get("suppress_commands") else command_suggestions
    owner_route_id = str(context_pack.get("owner_route_id") or route["route_id"] if route else "").strip() or None
    action_hints = build_runtime_action_hints(
        request_text=effective_request_text,
        route_id=owner_route_id,
        route_status=route_status,
        retrieval_mode=retrieval_mode,
        confidence=confidence,
        owner_candidates=owner_candidates,
        command_suggestions=command_suggestions,
    )
    do_not_scan_paths = list(
        dict.fromkeys(
            [
                str(path)
                for path in (action_hints.get("do_not_scan_paths") or [])
                if isinstance(path, str) and path
            ]
            + _excluded_family_prefixes(list(constraints.get("excluded_families_unless_imported") or []))
        )
    )[:8]
    owner_candidates = _rank_runtime_owner_candidates(
        owner_candidates=owner_candidates,
        command_suggestions=scoring_command_suggestions,
        do_not_scan_paths=do_not_scan_paths,
        excluded_families=list(constraints.get("excluded_families_unless_imported") or []),
        ownership_graph=ownership_graph,
    )
    retained_route_ids = _retained_triage_route_ids(
        request_text=effective_request_text,
        resolved_route=route,
        route_candidates=[
            dict(item)
            for item in (context_pack_payload.get("route_candidates") or [])
            if isinstance(item, dict)
        ],
        owner_route_id=owner_route_id,
    )
    route_projections = _load_retained_route_projections(
        store_path=Path(context_pack_payload.get("context_store_path") or "")
        if context_pack_payload.get("context_store_path")
        else None,
        workspace_context=compact_context,
        route_ids=retained_route_ids,
    )
    action_hints = build_runtime_action_hints(
        request_text=effective_request_text,
        route_id=owner_route_id,
        route_status=route_status,
        retrieval_mode=retrieval_mode,
        confidence=confidence,
        owner_candidates=owner_candidates,
        command_suggestions=command_suggestions,
    )
    visible_commands = [] if constraints.get("suppress_commands") else [
        str(command)
        for command in (action_hints.get("exact_candidate_commands_only") or [])
        if isinstance(command, str) and str(command).strip()
    ]
    response_mode = _triage_response_mode(
        request_text=effective_request_text,
        constraints=constraints,
        candidate_commands=visible_commands,
    )
    preflight_entries: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(owner_candidates[:6]):
        _add_triage_entry(
            preflight_entries,
            path_text=candidate.get("path"),
            base_score=108 - (index * 4),
            route_id=owner_route_id,
            source="preflight_owner",
            summary=str(candidate.get("summary") or "").strip() or None,
            why=str(candidate.get("why") or "").strip() or None,
        )
    for index, path_text in enumerate(action_hints.get("next_read_paths") or []):
        _add_triage_entry(
            preflight_entries,
            path_text=str(path_text),
            base_score=96 - (index * 3),
            route_id=owner_route_id,
            source="next_read",
        )
    if not constraints.get("suppress_commands"):
        for index, manifest_path in enumerate(_exact_package_manifest_paths(command_suggestions, ownership_graph=ownership_graph)):
            _add_triage_entry(
                preflight_entries,
                path_text=manifest_path,
                base_score=120 - index,
                route_id=owner_route_id,
                source="exact_manifest",
            )
        for index, source_path in enumerate(_exact_package_source_paths(command_suggestions)):
            _add_triage_entry(
                preflight_entries,
                path_text=source_path,
                base_score=116 - index,
                route_id=owner_route_id,
                source="exact_source",
            )
    for projection in route_projections:
        projection_route_id = str(projection.get("route_id") or "").strip() or None
        for index, candidate in enumerate((projection.get("owner_candidates") or [])[:2]):
            if not isinstance(candidate, dict):
                continue
            _add_triage_entry(
                preflight_entries,
                path_text=candidate.get("path"),
                base_score=92 - (index * 4),
                route_id=projection_route_id,
                source="route_projection_owner",
                summary=str(candidate.get("summary") or "").strip() or None,
                why=str(candidate.get("why") or "").strip() or None,
            )
        for index, path_text in enumerate((projection.get("priority_paths") or [])[:2]):
            _add_triage_entry(
                preflight_entries,
                path_text=str(path_text),
                base_score=80 - (index * 3),
                route_id=projection_route_id,
                source="route_projection_priority",
            )
    _add_controller_service_pair_entries(
        preflight_entries,
        ownership_graph=ownership_graph,
        route_id=owner_route_id,
        base_score=90.0,
    )
    preflight_graph_seed_paths = list(preflight_entries)[:6]
    for path_text in preflight_graph_seed_paths:
        related_paths = sorted(_ownership_related_paths(path_text, ownership_graph))
        for index, related_path in enumerate(related_paths[:3]):
            _add_triage_entry(
                preflight_entries,
                path_text=related_path,
                base_score=66 - (index * 3),
                route_id=owner_route_id,
                source="graph_neighbor",
            )
    preflight_supporting_paths = {
        str(candidate.get("path") or "").strip()
        for candidate in owner_candidates[:4]
        if str(candidate.get("path") or "").strip()
    }
    preflight_entries = _restrict_entries_to_exact_package_slice(
        preflight_entries,
        command_suggestions=command_suggestions,
        supporting_paths=preflight_supporting_paths,
        ownership_graph=ownership_graph,
        response_mode=response_mode,
    )
    scored_entries = _score_triage_entries(
        preflight_entries,
        request_text=effective_request_text,
        constraints=constraints,
        response_mode=response_mode,
        command_suggestions=scoring_command_suggestions,
        do_not_scan_paths=do_not_scan_paths,
        excluded_families=list(constraints.get("excluded_families_unless_imported") or []),
        ownership_graph=ownership_graph,
    )
    required_manifest_paths = (
        _exact_package_manifest_paths(command_suggestions, ownership_graph=ownership_graph)
        if not constraints.get("suppress_commands")
        else []
    )
    selection_limit = min(4, TRIAGE_MAX_FILES)
    selected_entries = (
        _select_exact_command_minimal_entries(
            scored_entries,
            limit=selection_limit,
            required_manifest_paths=required_manifest_paths,
            ownership_graph=ownership_graph,
            request_text=effective_request_text,
        )
        if response_mode == "command_discovery"
        else []
    )
    if not selected_entries:
        selected_entries = _select_triage_entries(
            scored_entries,
            limit=selection_limit,
            required_paths=required_manifest_paths,
        )
    selected_entries = _refine_owner_focus_entries(
        selected_entries,
        scored_entries=scored_entries,
        request_text=effective_request_text,
        response_mode=response_mode,
        ownership_graph=ownership_graph,
    )
    primary_owner_files, supporting_evidence_files, _response_mode = _partition_triage_entries(
        selected_entries,
        request_text=effective_request_text,
        constraints=constraints,
        candidate_commands=visible_commands,
        ownership_graph=ownership_graph,
    )
    primary_owner_files = _stabilize_readiness_owner_order(
        primary_owner_files,
        request_text=effective_request_text,
        ownership_graph=ownership_graph,
    )
    supporting_evidence_files = _prune_supporting_evidence_files(
        primary_owner_files=primary_owner_files,
        supporting_evidence_files=supporting_evidence_files,
        request_text=effective_request_text,
        candidate_commands=visible_commands,
    )
    answer_ready, answer_ready_reason = _runtime_answer_readiness(
        selected_entries=selected_entries,
        scored_entries=scored_entries,
        retained_route_ids=retained_route_ids,
        route_candidates=[
            dict(item)
            for item in (context_pack_payload.get("route_candidates") or [])
            if isinstance(item, dict)
        ],
        dominant_route_id=owner_route_id,
        request_text=effective_request_text,
        constraints=constraints,
        candidate_commands=visible_commands,
        required_manifest_paths=required_manifest_paths,
    )
    dependency_edges = _build_dependency_edges(
        primary_owner_files=primary_owner_files,
        supporting_evidence_files=supporting_evidence_files,
        ownership_graph=ownership_graph,
        workspace_root=workspace_root,
    )
    proof_assertions = _build_proof_assertions(
        primary_owner_files=primary_owner_files,
        supporting_evidence_files=supporting_evidence_files,
        dependency_edges=dependency_edges,
        request_text=effective_request_text,
        constraints=constraints,
        candidate_commands=visible_commands,
        ownership_graph=ownership_graph,
    )
    next_read_paths = [*primary_owner_files, *supporting_evidence_files][:selection_limit]
    selected_tools = [] if answer_ready else list(context_pack.get("selected_tools") or [])[:6]
    confidence_reason = (
        _ready_confidence_reason(
            constraints=constraints,
            candidate_commands=visible_commands,
            retained_route_ids=retained_route_ids,
        )
        if answer_ready
        else action_hints["confidence_reason"]
    )
    follow_up_policy = _follow_up_policy(
        answer_ready=answer_ready,
        retrieval_mode=retrieval_mode,
        selected_tools=selected_tools,
        next_read_paths=next_read_paths,
    )
    stop_if_enough_guidance = (
        "The bounded preflight already covers the owner slice; answer directly from the ranked owner files and exact package command."
        if answer_ready and visible_commands
        else "The bounded preflight already covers the owner slice; answer directly from the ranked owner files."
        if answer_ready
        else "Read only the listed owner files first; use `search_context_index` next if they still do not close the owner set."
        if next_read_paths
        else "Resolve the route first, then re-run preflight or `search_context_index`."
    )
    return {
        "schema_version": 4,
        "request_text_source": request_text_source,
        "effective_request_text": effective_request_text,
        "repo_maturity": compact_context.get("repo_maturity") or {},
        "route_id": route["route_id"] if route else None,
        "owner_route_id": owner_route_id,
        "owner_route_override_applied": bool(owner_route_id and owner_route_id != (route["route_id"] if route else None)),
        "route_resolution_status": route_status,
        "selected_tools": selected_tools,
        "owner_candidates": owner_candidates[:4],
        "primary_owner_files": primary_owner_files[:selection_limit],
        "supporting_evidence_files": supporting_evidence_files[:selection_limit],
        "command_suggestions": command_suggestions[:4],
        "why_these_files": context_pack.get("why_these_files") or {},
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "next_read_paths": next_read_paths[:4],
        "do_not_scan_paths": do_not_scan_paths[:6],
        "exact_candidate_commands_only": visible_commands[:4],
        "stop_if_enough": answer_ready,
        "stop_if_enough_guidance": stop_if_enough_guidance,
        "request_kind_hint": action_hints["request_kind_hint"],
        "proof_assertions": proof_assertions[:TRIAGE_MAX_PROOF_ASSERTIONS],
        "dependency_edges": dependency_edges[:TRIAGE_MAX_DEPENDENCY_EDGES],
        "follow_up_policy": follow_up_policy,
        "cache_status": context_pack_payload.get("cache_status"),
        "applied_constraints": {
            "owner_files_only": bool(constraints.get("owner_files_only")),
            "suppress_commands": bool(constraints.get("suppress_commands")),
            "excluded_families_unless_imported": list(constraints.get("excluded_families_unless_imported") or []),
        },
        "answer_ready_reason": answer_ready_reason,
    }


def show_workspace_context_pack(
    workspace: str | Path,
    request_text: str | None = None,
    route_id: str | None = None,
    limit: int | None = 6,
    force_refresh: bool = False,
    semantic_mode: str | None = "disabled",
    _internal_include_recommended_capabilities: bool = True,
    _internal_record_surface: bool = True,
) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    retrieval_mode = infer_retrieval_mode(request_text)
    profile = retrieval_mode_profile(retrieval_mode)
    chunk_limit = _limit(limit, 6, profile["max_selected_chunk_limit"])
    if force_refresh:
        refresh_context_index(
            resolved_workspace,
            force=True,
            query_text=request_text,
            route_id=route_id,
            retrieval_mode=retrieval_mode,
        )
    refresh_result, cache_paths, workspace_context, usage, _structure_index = load_workspace_context_bundle(
        resolved_workspace,
        query_text=request_text,
        route_id=route_id,
        retrieval_mode=retrieval_mode,
    )
    route, route_candidates, route_status = _resolve_intent_candidates(request_text, route_id, usage=usage)
    compact_context = compact_workspace_context(workspace_context)
    semantic_support = _semantic_support(
        cache_paths,
        semantic_mode=semantic_mode,
        analysis_explicit=_analysis_explicit(route_id, route),
        query_text=request_text,
        limit=chunk_limit,
    )
    task_cache_key = _task_retrieval_cache_key(
        request_text,
        route_id=route["route_id"] if route else None,
        semantic_mode=semantic_support["semantic_mode"],
        workspace_context=compact_context,
        limit_hint=chunk_limit,
    )

    if request_text:
        semantic_fingerprint = (
            _semantic_manifest_fingerprint(cache_paths) if semantic_support["enabled"] else None
        )
        cache_key = _semantic_cache_key(
            request_text,
            workspace_context.get("catalog_digest", ""),
            route["route_id"] if route else None,
            semantic_support["semantic_mode"],
            semantic_enabled=bool(semantic_support["enabled"]),
            limit_hint=chunk_limit,
        )
        if task_cache_key:
            cached = read_query_cache(
                cache_paths["context_store"],
                cache_kind=QUERY_CACHE_TASK_RETRIEVAL_KIND,
                cache_key=task_cache_key,
            )
            if cached:
                task_cached_payload = cached.get("payload") or {}
                cached_context_pack = task_cached_payload.get("context_pack") or {}
                if (
                    int(task_cached_payload.get("schema_version") or 0) == 1
                    and task_cached_payload.get("scope_signature") == _scope_signature(compact_context)
                    and task_cached_payload.get("route_id") == (route["route_id"] if route else None)
                    and int(cached_context_pack.get("schema_version") or 0) == 8
                    and cached.get("catalog_digest") == workspace_context.get("catalog_digest")
                    and cached.get("semantic_mode") == semantic_support["semantic_mode"]
                    and isinstance(cached.get("source_hashes"), dict)
                    and (
                        not cached.get("source_hashes")
                        or load_source_hashes(
                            cache_paths["context_store"],
                            list(cached["source_hashes"]),
                        )
                        == {path: value for path, value in cached["source_hashes"].items() if isinstance(path, str)}
                    )
                ):
                    record_context_pack_stats(
                        cache_paths,
                        cache_status="task-hit",
                        route_status=route_status,
                        route_id=route["route_id"] if route else None,
                        selected_tool_count=len(cached_context_pack.get("selected_tools", [])),
                    )
                    payload = {
                        "workspace_path": str(resolved_workspace),
                        "cache_root": str(cache_paths["root"]),
                        "cache_status": "task-hit",
                        "index_status": refresh_result["status"],
                        "index_refresh_reason": refresh_result.get("refresh_reason"),
                        "storage_backend": refresh_result.get("storage_backend"),
                        "storage_summary": refresh_result.get("storage_summary"),
                        "context_store_path": refresh_result.get("context_store_path"),
                        "retrieval": retrieval_policy_payload("show_workspace_context_pack", retrieval_mode),
                        "semantic_mode": semantic_support["semantic_mode"],
                        "semantic_backend_status": semantic_support["backend_status"],
                        "workspace_context": compact_context,
                        "resolved_route": route,
                        "route_resolution_status": route_status,
                        "route_candidates": route_candidates[:3],
                        "context_pack": cached_context_pack,
                    }
                    _trim_context_pack_payload(payload)
                    if _internal_record_surface:
                        _surface_payload_stats("show_workspace_context_pack", payload)
                        cached_context_pack = payload.get("context_pack") or {}
                        _record_surface_benchmark(
                            "show_workspace_context_pack",
                            payload,
                            route_id=route["route_id"] if route else None,
                            retrieval_mode=retrieval_mode,
                            semantic_mode=semantic_support["semantic_mode"],
                            cache_status="task-hit",
                            selected_chunk_count=len(cached_context_pack.get("selected_chunks") or []),
                            selected_tool_count=len(cached_context_pack.get("selected_tools") or []),
                            refresh_reason=refresh_result.get("refresh_reason"),
                        )
                    record_high_confidence_runtime_context(
                        cache_paths,
                        request_text=request_text,
                        route_id=route.get("route_id") if isinstance(route, dict) else None,
                        route_status=route_status,
                        confidence=cached_context_pack.get("confidence"),
                    )
                    return payload
        cached = read_query_cache(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_CONTEXT_PACK_KIND,
            cache_key=cache_key,
        )
        if cached:
            cached_context_pack = cached.get("payload") or {}
            if (
                int(cached_context_pack.get("schema_version") or 0) == 8
                and cached.get("catalog_digest") == workspace_context.get("catalog_digest")
                and cached.get("semantic_mode") == semantic_support["semantic_mode"]
                and bool(cached_context_pack.get("semantic_enabled")) == bool(semantic_support["enabled"])
                and (
                    semantic_fingerprint is None
                    or cached_context_pack.get("semantic_fingerprint") == semantic_fingerprint
                )
                and isinstance(cached.get("source_hashes"), dict)
                and (
                    not cached.get("source_hashes")
                    or load_source_hashes(
                        cache_paths["context_store"],
                        list(cached["source_hashes"]),
                    )
                    == {path: value for path, value in cached["source_hashes"].items() if isinstance(path, str)}
                )
            ):
                record_context_pack_stats(
                    cache_paths,
                    cache_status="hit",
                    route_status=route_status,
                    route_id=route["route_id"] if route else None,
                    selected_tool_count=len(cached_context_pack.get("selected_tools", [])),
                )
                payload = {
                    "workspace_path": str(resolved_workspace),
                    "cache_root": str(cache_paths["root"]),
                    "cache_status": "hit",
                    "index_status": refresh_result["status"],
                    "index_refresh_reason": refresh_result.get("refresh_reason"),
                    "storage_backend": refresh_result.get("storage_backend"),
                    "storage_summary": refresh_result.get("storage_summary"),
                    "context_store_path": refresh_result.get("context_store_path"),
                    "retrieval": retrieval_policy_payload("show_workspace_context_pack", retrieval_mode),
                    "semantic_mode": semantic_support["semantic_mode"],
                    "semantic_backend_status": semantic_support["backend_status"],
                    "workspace_context": compact_context,
                    "resolved_route": route,
                    "route_resolution_status": route_status,
                    "route_candidates": route_candidates[:3],
                    "context_pack": cached_context_pack,
                }
                _trim_context_pack_payload(payload)
                if _internal_record_surface:
                    _surface_payload_stats("show_workspace_context_pack", payload)
                    cached_context_pack = payload.get("context_pack") or {}
                    _record_surface_benchmark(
                        "show_workspace_context_pack",
                        payload,
                        route_id=route["route_id"] if route else None,
                        retrieval_mode=retrieval_mode,
                        semantic_mode=semantic_support["semantic_mode"],
                        cache_status="hit",
                        selected_chunk_count=len(cached_context_pack.get("selected_chunks") or []),
                        selected_tool_count=len(cached_context_pack.get("selected_tools") or []),
                        refresh_reason=refresh_result.get("refresh_reason"),
                    )
                record_high_confidence_runtime_context(
                    cache_paths,
                    request_text=request_text,
                    route_id=route.get("route_id") if isinstance(route, dict) else None,
                    route_status=route_status,
                    confidence=cached_context_pack.get("confidence"),
                )
                return payload
        search_bundle = _search_bundle_matches(
            query_text=request_text,
            route_id=route_id,
            workspace_context=workspace_context,
            store_path=cache_paths["context_store"],
            usage=usage,
            match_limit=chunk_limit,
            capability_limit=profile["max_selected_tool_limit"],
            include_recommended_capabilities=_internal_include_recommended_capabilities,
        )
        semantic_support = _semantic_support(
            cache_paths,
            semantic_mode=semantic_mode,
            analysis_explicit=_analysis_explicit(route_id, search_bundle["route"]),
            query_text=request_text,
            limit=chunk_limit,
        )
        record_search_stats(
            cache_paths,
            query_text=request_text,
            match_count=search_bundle["match_count"],
            route_status=search_bundle["route_status"],
            route_id=search_bundle["route"]["route_id"] if search_bundle["route"] else None,
        )
        selected_chunks = _pinned_project_memory_selected_chunks(
            load_pinned_project_memory_chunks(
                cache_paths["context_store"],
                limit=profile["max_selected_chunk_limit"],
            )
        )
        selected_chunks.extend(
            [
                item
                for match in search_bundle["matches"]
                for item in [_selected_chunk_payload_from_match(match)]
                if item is not None
            ]
        )
        selected_chunks.extend(
            [
                {
                    "chunk_id": match.get("unit_id"),
                    "path": match.get("path"),
                    "summary": match.get("summary_text"),
                    "score": match.get("score"),
                    "match_source": "semantic_assisted",
                }
                for match in semantic_support["matches"]
                if match.get("path") and match.get("summary_text")
            ]
        )
        deduped_chunks: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for item in selected_chunks:
            if item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            deduped_chunks.append(item)
        selected_chunks = deduped_chunks[:chunk_limit]
        selected_tools = []
        if route:
            selected_tools.extend(route.get("recommended_tools", []))
        selected_tools.extend(
            entry["tool_name"]
            for entry in search_bundle["recommended_capabilities"]
            if entry["kind"] == "mcp_tool"
        )
        selected_tools = list(dict.fromkeys(selected_tools))[: profile["max_selected_tool_limit"]]
        source_hashes = load_source_hashes(
            cache_paths["context_store"],
            [str(match["path"]) for match in selected_chunks if isinstance(match.get("path"), str)],
        )
        ownership_graph = _load_ownership_graph(
            store_path=cache_paths["context_store"],
            workspace_context=workspace_context,
        )
        owner_route_id = resolve_route_hint_override(
            requested_route_id=route_id,
            selected_route_id=route["route_id"] if route else None,
            request_text=request_text,
        )
        owner_candidates = build_owner_candidates(
            [chunk for chunk in selected_chunks if isinstance(chunk, dict)],
            workspace=resolved_workspace,
            request_text=request_text,
            route_id=owner_route_id,
            ownership_graph=ownership_graph,
        )
        command_suggestions = build_command_suggestions(
            workspace=resolved_workspace,
            selected_chunks=owner_candidates,
            request_text=request_text,
            route_id=owner_route_id,
            workspace_context=workspace_context,
        )
        why_these_files = build_why_these_files_summary(
            owner_candidates=owner_candidates,
            command_suggestions=command_suggestions,
            ownership_graph=ownership_graph,
            request_text=request_text,
            route_id=owner_route_id,
        )
        confidence = round(min(sum(match["score"] for match in selected_chunks[:3]) / 36, 1.0), 2)
        action_hints = build_runtime_action_hints(
            request_text=request_text,
            route_id=owner_route_id,
            route_status=route_status,
            retrieval_mode=retrieval_mode,
            confidence=confidence,
            owner_candidates=owner_candidates,
            command_suggestions=command_suggestions,
        )
        context_pack = {
            "schema_version": 8,
            "query_fingerprint": cache_key,
            "workspace_fingerprint": workspace_context.get("workspace_fingerprint"),
            "catalog_digest": workspace_context.get("catalog_digest"),
            "route_id": route["route_id"] if route else None,
            "semantic_mode": semantic_support["semantic_mode"],
            "semantic_enabled": bool(semantic_support["enabled"]),
            "semantic_fingerprint": semantic_fingerprint,
            "selected_chunks": selected_chunks,
            "selected_tools": selected_tools,
            "owner_route_id": owner_route_id,
            "owner_route_override_applied": bool(owner_route_id and owner_route_id != (route["route_id"] if route else None)),
            "owner_candidates": [
                {
                    "path": item.get("path"),
                    "summary": item.get("summary"),
                    "score": item.get("owner_score", item.get("score")),
                    "match_source": item.get("match_source", "symbolic"),
                    "why": item.get("why"),
                }
                for item in owner_candidates
                if isinstance(item.get("path"), str) and item.get("path")
            ],
            "command_suggestions": command_suggestions,
            "why_these_files": why_these_files,
            "route_projection": search_bundle.get("route_projection") or {},
            "confidence": confidence,
            "next_read_paths": action_hints["next_read_paths"],
            "do_not_scan_paths": action_hints["do_not_scan_paths"],
            "exact_candidate_commands_only": action_hints["exact_candidate_commands_only"],
            "confidence_reason": action_hints["confidence_reason"],
            "stop_if_enough": action_hints["stop_if_enough"],
            "stop_if_enough_guidance": action_hints["stop_if_enough_guidance"],
            "request_kind_hint": action_hints["request_kind_hint"],
            "scope_signature": _scope_signature(compact_context),
            "source_hashes": source_hashes,
            "created_at": now_iso(),
        }
        upsert_query_cache_entry(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_CONTEXT_PACK_KIND,
            cache_key=cache_key,
            payload=context_pack,
            route_id=route["route_id"] if route else None,
            workspace_fingerprint=workspace_context.get("workspace_fingerprint"),
            catalog_digest=workspace_context.get("catalog_digest"),
            semantic_mode=semantic_support["semantic_mode"],
            created_at=context_pack["created_at"],
            source_hashes=source_hashes,
            limit_per_kind=40,
        )
        if task_cache_key:
            upsert_query_cache_entry(
                cache_paths["context_store"],
                cache_kind=QUERY_CACHE_TASK_RETRIEVAL_KIND,
                cache_key=task_cache_key,
                payload={
                    "schema_version": 1,
                    "scope_signature": _scope_signature(compact_context),
                    "route_id": route["route_id"] if route else None,
                    "intent_signature": _task_intent_signature(request_text, route["route_id"] if route else None),
                    "context_pack": context_pack,
                },
                route_id=route["route_id"] if route else None,
                workspace_fingerprint=workspace_context.get("workspace_fingerprint"),
                catalog_digest=workspace_context.get("catalog_digest"),
                semantic_mode=semantic_support["semantic_mode"],
                created_at=context_pack["created_at"],
                source_hashes=source_hashes,
                limit_per_kind=24,
            )
        record_usage(cache_paths, route["route_id"] if route else None, selected_tools)
        record_context_pack_stats(
            cache_paths,
            cache_status="miss",
            route_status=route_status,
            route_id=route["route_id"] if route else None,
            selected_tool_count=len(selected_tools),
        )
        payload = {
            "workspace_path": str(resolved_workspace),
            "cache_root": str(cache_paths["root"]),
            "cache_status": "miss",
            "index_status": refresh_result["status"],
            "index_refresh_reason": refresh_result.get("refresh_reason"),
            "storage_backend": refresh_result.get("storage_backend"),
            "storage_summary": refresh_result.get("storage_summary"),
            "context_store_path": refresh_result.get("context_store_path"),
            "retrieval": retrieval_policy_payload("show_workspace_context_pack", retrieval_mode),
            "semantic_mode": semantic_support["semantic_mode"],
            "semantic_backend_status": semantic_support["backend_status"],
            "workspace_context": compact_context,
            "resolved_route": route,
            "route_resolution_status": route_status,
            "route_candidates": route_candidates[:3],
            "context_pack": context_pack,
        }
        _trim_context_pack_payload(payload)
        recorded_context_pack = payload.get("context_pack") or {}
        if _internal_record_surface:
            _surface_payload_stats("show_workspace_context_pack", payload)
            recorded_context_pack = payload.get("context_pack") or {}
            _record_surface_benchmark(
                "show_workspace_context_pack",
                payload,
                route_id=route["route_id"] if route else None,
                retrieval_mode=retrieval_mode,
                semantic_mode=semantic_support["semantic_mode"],
                cache_status="miss",
                selected_chunk_count=len(recorded_context_pack.get("selected_chunks") or []),
                selected_tool_count=len(recorded_context_pack.get("selected_tools") or []),
                refresh_reason=refresh_result.get("refresh_reason"),
            )
        record_high_confidence_runtime_context(
            cache_paths,
            request_text=request_text,
            route_id=route.get("route_id") if isinstance(route, dict) else None,
            route_status=route_status,
            confidence=recorded_context_pack.get("confidence"),
        )
        return payload

    record_context_pack_stats(
        cache_paths,
        cache_status="workspace_only",
        route_status=route_status,
        route_id=route["route_id"] if route else None,
        selected_tool_count=0,
    )
    payload = {
        "workspace_path": str(resolved_workspace),
        "cache_root": str(cache_paths["root"]),
        "cache_status": "workspace_only",
        "index_status": refresh_result["status"],
        "index_refresh_reason": refresh_result.get("refresh_reason"),
        "storage_backend": refresh_result.get("storage_backend"),
        "storage_summary": refresh_result.get("storage_summary"),
        "context_store_path": refresh_result.get("context_store_path"),
        "retrieval": retrieval_policy_payload("show_workspace_context_pack", retrieval_mode),
        "semantic_mode": semantic_support["semantic_mode"],
        "semantic_backend_status": semantic_support["backend_status"],
        "workspace_context": compact_context,
        "resolved_route": route,
        "route_resolution_status": route_status,
        "route_candidates": route_candidates[:3],
        "modules": load_module_summaries(cache_paths["context_store"], limit=8),
        "chunk_count": workspace_context.get("chunk_count", 0),
        "pinned_project_memory": _pinned_project_memory_selected_chunks(
            load_pinned_project_memory_chunks(
                cache_paths["context_store"],
                limit=profile["max_selected_chunk_limit"],
            )
        )[: profile["max_selected_chunk_limit"]],
        "retrieval_ladder": RETRIEVAL_LADDER,
    }
    if _internal_record_surface:
        _surface_payload_stats("show_workspace_context_pack", payload)
        _record_surface_benchmark(
            "show_workspace_context_pack",
            payload,
            route_id=route["route_id"] if route else None,
            retrieval_mode=retrieval_mode,
            semantic_mode=semantic_support["semantic_mode"],
            cache_status="workspace_only",
            selected_chunk_count=len(payload.get("pinned_project_memory") or []),
            selected_tool_count=0,
            refresh_reason=refresh_result.get("refresh_reason"),
        )
    return payload


def show_runtime_preflight(
    workspace: str | Path,
    request_text: str | None = None,
    route_id: str | None = None,
    limit: int | None = 4,
    force_refresh: bool = False,
    semantic_mode: str | None = "disabled",
) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    cache_paths = context_cache_paths(resolved_workspace)
    usage = load_usage(cache_paths)
    preview_payload: dict[str, Any] | None = None
    effective_request_text = str(request_text or "").strip() or None
    request_text_source = "request_text" if effective_request_text else "none"
    if not effective_request_text:
        preview_payload = show_workspace_context_pack(
            resolved_workspace,
            request_text=None,
            route_id=route_id,
            limit=limit,
            force_refresh=force_refresh,
            semantic_mode=semantic_mode,
            _internal_include_recommended_capabilities=False,
            _internal_record_surface=False,
        )
        effective_request_text, request_text_source = _effective_preflight_request_text(
            None,
            preview_payload.get("workspace_context") or {},
            usage,
        )
    context_pack_payload = (
        show_workspace_context_pack(
            resolved_workspace,
            request_text=effective_request_text,
            route_id=route_id,
            limit=limit,
            force_refresh=force_refresh if preview_payload is None else False,
            semantic_mode=semantic_mode,
            _internal_include_recommended_capabilities=False,
            _internal_record_surface=False,
        )
        if effective_request_text
        else (preview_payload or {})
    )
    compact_context = context_pack_payload.get("workspace_context") or {}
    route = context_pack_payload.get("resolved_route")
    route_status = str(context_pack_payload.get("route_resolution_status") or "unresolved")
    retrieval_mode = infer_retrieval_mode(effective_request_text or request_text or "runtime preflight")
    context_pack = context_pack_payload.get("context_pack") or {}
    ownership_graph = (
        _load_ownership_graph(
            store_path=Path(context_pack_payload.get("context_store_path") or ""),
            workspace_context=compact_context,
        )
        if context_pack_payload.get("context_store_path")
        else None
    )
    preflight_cache_key = None
    preflight_payload = None
    context_fingerprint = str(context_pack.get("query_fingerprint") or "").strip()
    if context_fingerprint:
        preflight_cache_key = short_hash(f"runtime-preflight:{context_fingerprint}:{route_status}", length=16)
        cached = read_query_cache(
            Path(context_pack_payload.get("context_store_path") or ""),
            cache_kind=QUERY_CACHE_RUNTIME_PREFLIGHT_KIND,
            cache_key=preflight_cache_key,
        )
        if cached:
            cached_preflight = cached.get("payload") or {}
            if (
                int(cached_preflight.get("schema_version") or 0) == 4
                and cached.get("catalog_digest") == compact_context.get("catalog_digest")
                and isinstance(cached.get("source_hashes"), dict)
                and (
                    not cached.get("source_hashes")
                    or load_source_hashes(
                        Path(context_pack_payload.get("context_store_path") or ""),
                        list(cached["source_hashes"]),
                    )
                    == {path: value for path, value in cached["source_hashes"].items() if isinstance(path, str)}
                )
            ):
                preflight_payload = dict(cached_preflight)
                preflight_payload["cache_status"] = "hit"
    if preflight_payload is None:
        preflight_payload = _build_runtime_preflight_payload(
            workspace_root=resolved_workspace,
            effective_request_text=effective_request_text,
            request_text_source=request_text_source,
            route=route if isinstance(route, dict) else None,
            route_status=route_status,
            compact_context=compact_context,
            retrieval_mode=retrieval_mode,
            context_pack_payload=context_pack_payload,
            ownership_graph=ownership_graph,
        )
        preflight_payload["cache_status"] = "miss"
        if preflight_cache_key:
            upsert_query_cache_entry(
                Path(context_pack_payload.get("context_store_path") or ""),
                cache_kind=QUERY_CACHE_RUNTIME_PREFLIGHT_KIND,
                cache_key=preflight_cache_key,
                payload=preflight_payload,
                route_id=route.get("route_id") if isinstance(route, dict) else None,
                workspace_fingerprint=compact_context.get("workspace_fingerprint"),
                catalog_digest=compact_context.get("catalog_digest"),
                semantic_mode=context_pack_payload.get("semantic_mode"),
                created_at=now_iso(),
                source_hashes=context_pack.get("source_hashes") or {},
                limit_per_kind=40,
            )
    payload = {
        "workspace_path": str(resolved_workspace),
        "cache_root": context_pack_payload.get("cache_root"),
        "index_status": context_pack_payload.get("index_status"),
        "index_refresh_reason": context_pack_payload.get("index_refresh_reason"),
        "storage_backend": context_pack_payload.get("storage_backend"),
        "storage_summary": context_pack_payload.get("storage_summary"),
        "context_store_path": context_pack_payload.get("context_store_path"),
        "retrieval": retrieval_policy_payload("show_runtime_preflight", retrieval_mode),
        "semantic_mode": context_pack_payload.get("semantic_mode"),
        "semantic_backend_status": context_pack_payload.get("semantic_backend_status"),
        "workspace_context": compact_context,
        "resolved_route": route,
        "route_resolution_status": route_status,
        "route_candidates": (context_pack_payload.get("route_candidates") or [])[:3],
        "effective_request_text": effective_request_text,
        "preflight": preflight_payload,
    }
    _trim_runtime_preflight_payload(payload)
    _surface_payload_stats("show_runtime_preflight", payload)
    _record_surface_benchmark(
        "show_runtime_preflight",
        payload,
        route_id=route.get("route_id") if isinstance(route, dict) else None,
        retrieval_mode=retrieval_mode,
        semantic_mode=context_pack_payload.get("semantic_mode"),
        cache_status=preflight_payload.get("cache_status"),
        selected_chunk_count=len(preflight_payload.get("owner_candidates") or []),
        selected_tool_count=len(preflight_payload.get("selected_tools") or []),
        refresh_reason=context_pack_payload.get("index_refresh_reason"),
    )
    record_high_confidence_runtime_context(
        cache_paths,
        request_text=effective_request_text,
        route_id=route.get("route_id") if isinstance(route, dict) else None,
        route_status=route_status,
        confidence=preflight_payload.get("confidence"),
    )
    return payload


def _path_matches_prefix(path_text: str, prefix: str) -> bool:
    normalized_path = str(path_text or "").strip().rstrip("/")
    normalized_prefix = str(prefix or "").strip().rstrip("/")
    if not normalized_path or not normalized_prefix:
        return False
    return normalized_path == normalized_prefix or normalized_path.startswith(normalized_prefix + "/")


def _exact_package_source_paths(command_suggestions: list[dict[str, Any]]) -> list[str]:
    source_paths: list[str] = []
    for suggestion in command_suggestions[:4]:
        source_path = str(suggestion.get("source_path") or "").strip()
        if source_path and source_path not in source_paths:
            source_paths.append(source_path)
    return source_paths


TRIAGE_SCRIPT_REFERENCE_SUFFIXES = {
    ".cjs",
    ".cts",
    ".js",
    ".json",
    ".jsx",
    ".mjs",
    ".mts",
    ".ts",
    ".tsx",
}


def _normalize_script_reference_token(token: str, *, base_dir: str) -> str | None:
    normalized = str(token or "").strip().strip("\"'")
    if (
        not normalized
        or normalized.startswith("-")
        or normalized in {"&&", "||", "|", ";", "(", ")"}
        or "://" in normalized
        or normalized.startswith("$")
        or any(marker in normalized for marker in {"${", "*", "?"})
    ):
        return None
    if "." not in posixpath.basename(normalized):
        return None
    if Path(posixpath.basename(normalized)).suffix.lower() not in TRIAGE_SCRIPT_REFERENCE_SUFFIXES:
        return None
    joined = posixpath.normpath(posixpath.join(base_dir or ".", normalized))
    if joined in {".", ""} or joined.startswith("../") or posixpath.isabs(joined):
        return None
    return joined


def _exact_command_reference_paths(command_suggestions: list[dict[str, Any]]) -> list[str]:
    reference_paths: list[str] = []
    for suggestion in command_suggestions[:4]:
        script_command = str(suggestion.get("script_command") or "").strip()
        source_path = str(suggestion.get("source_path") or "").strip()
        if not script_command or not source_path:
            continue
        try:
            tokens = shlex.split(script_command)
        except ValueError:
            tokens = script_command.split()
        base_dir = posixpath.dirname(source_path) or "."
        for token in tokens:
            reference_path = _normalize_script_reference_token(token, base_dir=base_dir)
            if (
                not reference_path
                or reference_path == source_path
                or reference_path in reference_paths
            ):
                continue
            reference_paths.append(reference_path)
    return reference_paths


def _ownership_graph_entry(ownership_graph: dict[str, Any] | None, path_text: str) -> dict[str, Any]:
    if not isinstance(ownership_graph, dict):
        return {}
    by_path = ownership_graph.get("by_path") or {}
    if not isinstance(by_path, dict):
        return {}
    entry = by_path.get(path_text) or {}
    return entry if isinstance(entry, dict) else {}


def _package_manifest_for_path(path_text: str, ownership_graph: dict[str, Any] | None) -> str | None:
    normalized = str(path_text or "").strip()
    if not normalized:
        return None
    if Path(normalized).name == "package.json":
        return normalized
    entry = _ownership_graph_entry(ownership_graph, normalized)
    package_manifest = str(entry.get("package_manifest") or "").strip()
    return package_manifest or None


def _exact_package_manifest_paths(
    command_suggestions: list[dict[str, Any]],
    *,
    ownership_graph: dict[str, Any] | None,
) -> list[str]:
    manifests: list[str] = []
    for path_text in _exact_package_source_paths(command_suggestions):
        manifest_path = _package_manifest_for_path(path_text, ownership_graph)
        if manifest_path and manifest_path not in manifests:
            manifests.append(manifest_path)
    return manifests


def _exact_package_roots(command_suggestions: list[dict[str, Any]]) -> list[str]:
    roots: list[str] = []
    for source_path in _exact_package_source_paths(command_suggestions):
        source = Path(source_path)
        root_text = source.parent.as_posix()
        if root_text == ".":
            root_text = ""
        if root_text not in roots:
            roots.append(root_text)
    return roots


def _path_conflicts_with_exact_package(
    path_text: str,
    *,
    exact_package_roots: list[str],
    exact_source_paths: list[str],
) -> bool:
    normalized = str(path_text or "").strip()
    if not normalized or not exact_package_roots:
        return False
    if normalized in exact_source_paths:
        return False
    if "" in exact_package_roots:
        return False
    return not any(_path_matches_prefix(normalized, root) for root in exact_package_roots if root)


def _path_family(path_text: str) -> str:
    parts = Path(str(path_text or "").strip()).parts
    if not parts:
        return ""
    if parts[0] in {"apps", "packages"} and len(parts) >= 2:
        return "/".join(parts[:2])
    if parts[0] in {"docs", "references", "tests"}:
        return parts[0]
    return parts[0]


def _is_doc_or_config_path(path_text: str) -> bool:
    normalized = str(path_text or "").strip()
    if not normalized:
        return False
    path = Path(normalized)
    if path.name == "package.json":
        return False
    if path.name in {"README.md", "AGENTS.md"} or path.suffix == ".md":
        return True
    if any(part in {"docs", "references"} for part in path.parts):
        return True
    return path.name in TRIAGE_CONFIG_FILENAMES


def _is_test_like_path(path_text: str) -> bool:
    normalized = str(path_text or "").strip().lower()
    return (
        "/test/" in normalized
        or normalized.startswith("tests/")
        or normalized.endswith(".spec.ts")
        or normalized.endswith(".spec.tsx")
        or normalized.endswith(".test.ts")
        or normalized.endswith(".test.tsx")
    )


def _is_wrapper_like_path(path_text: str, ownership_graph: dict[str, Any] | None = None) -> bool:
    normalized = str(path_text or "").strip()
    if not normalized or _is_test_like_path(normalized) or _is_doc_or_config_path(normalized):
        return False
    path = Path(normalized)
    name = path.name.lower()
    if "shell" in name or "wrapper" in name:
        return True
    if name in {"index.ts", "index.tsx", "index.js", "index.jsx"}:
        return True
    entry = _ownership_graph_entry(ownership_graph, normalized)
    if not entry:
        return False
    signals = set(entry.get("signals") or [])
    if signals.intersection({"http-controller", "service-owner"}):
        return False
    imports = [value for value in entry.get("imports") or [] if isinstance(value, str) and value]
    imported_by = [value for value in entry.get("imported_by") or [] if isinstance(value, str) and value]
    return (
        path.suffix.lower() in {".ts", ".tsx", ".js", ".jsx"}
        and len(imports) == 1
        and len(imported_by) <= 2
        and not signals.intersection({"route-entry", "route-owner", "test-owner"})
    )


def _excluded_family_prefixes(excluded_families: list[str] | tuple[str, ...]) -> list[str]:
    prefixes: list[str] = []
    for family in excluded_families:
        for prefix in TRIAGE_EXCLUDED_FAMILY_PREFIXES.get(str(family), []):
            if prefix not in prefixes:
                prefixes.append(prefix)
    return prefixes


def _matched_excluded_family(path_text: str, excluded_families: list[str] | tuple[str, ...]) -> str | None:
    normalized = str(path_text or "").strip()
    if not normalized:
        return None
    for family in excluded_families:
        if any(_path_matches_prefix(normalized, prefix) for prefix in TRIAGE_EXCLUDED_FAMILY_PREFIXES.get(str(family), [])):
            return str(family)
    return None


def _ownership_related_paths(path_text: str, ownership_graph: dict[str, Any] | None) -> set[str]:
    entry = _ownership_graph_entry(ownership_graph, path_text)
    related: set[str] = set()
    for key in ("imports", "imported_by", "tests"):
        for value in entry.get(key) or []:
            if isinstance(value, str) and value:
                related.add(value)
    package_manifest = str(entry.get("package_manifest") or "").strip()
    if package_manifest:
        related.add(package_manifest)
    return related


def _ownership_direct_edges(path_text: str, ownership_graph: dict[str, Any] | None) -> set[str]:
    entry = _ownership_graph_entry(ownership_graph, path_text)
    related: set[str] = set()
    for key in ("imports", "imported_by", "tests"):
        for value in entry.get(key) or []:
            if isinstance(value, str) and value:
                related.add(value)
    return related


def _resolve_import_specifier(
    workspace_root: Path | None,
    *,
    source_path: str,
    specifier: str,
) -> str | None:
    if workspace_root is None:
        return None
    normalized_source = str(source_path or "").strip()
    normalized_specifier = str(specifier or "").strip()
    if not normalized_source or not normalized_specifier:
        return None
    source_dir = posixpath.dirname(normalized_source)
    if normalized_specifier.startswith("."):
        base = posixpath.normpath(posixpath.join(source_dir, normalized_specifier))
    else:
        base = normalized_specifier.lstrip("/")
    candidates = [
        base,
        f"{base}.ts",
        f"{base}.tsx",
        f"{base}.js",
        f"{base}.jsx",
        posixpath.join(base, "index.ts"),
        posixpath.join(base, "index.tsx"),
        posixpath.join(base, "index.js"),
        posixpath.join(base, "index.jsx"),
    ]
    for candidate in candidates:
        if candidate and (workspace_root / candidate).exists():
            return candidate
    return None


def _source_text_import_paths(workspace_root: Path | None, path_text: str) -> set[str]:
    if workspace_root is None:
        return set()
    normalized = str(path_text or "").strip()
    if not normalized or _is_doc_or_config_path(normalized) or _is_test_like_path(normalized):
        return set()
    source_path = workspace_root / normalized
    if not source_path.exists() or not source_path.is_file():
        return set()
    try:
        source_text = source_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    imports: set[str] = set()
    for match in re.finditer(
        r"""(?:
            from\s+["']([^"']+)["']
            |import\s+["']([^"']+)["']
            |require\(\s*["']([^"']+)["']\s*\)
            |export\s+[^;]*?\sfrom\s+["']([^"']+)["']
        )""",
        source_text,
        flags=re.VERBOSE,
    ):
        specifier = next((group for group in match.groups() if group), "")
        resolved = _resolve_import_specifier(
            workspace_root,
            source_path=normalized,
            specifier=specifier,
        )
        if resolved:
            imports.add(resolved)
    return imports


def _direct_forward_paths(
    path_text: str,
    *,
    ownership_graph: dict[str, Any] | None,
    workspace_root: Path | None = None,
) -> set[str]:
    entry = _ownership_graph_entry(ownership_graph, path_text)
    imports = {
        value
        for value in entry.get("imports") or []
        if isinstance(value, str) and value
    }
    if imports:
        return imports
    return _source_text_import_paths(workspace_root, path_text)


def _intermediate_wrapper_path(
    source_path: str,
    target_path: str,
    *,
    ownership_graph: dict[str, Any] | None,
    selected_paths: set[str],
    workspace_root: Path | None = None,
) -> str | None:
    source = str(source_path or "").strip()
    target = str(target_path or "").strip()
    if not source or not target or source == target:
        return None
    source_forward_paths = _direct_forward_paths(
        source,
        ownership_graph=ownership_graph,
        workspace_root=workspace_root,
    )
    for candidate in sorted(source_forward_paths):
        if candidate in selected_paths or not _is_wrapper_like_path(candidate, ownership_graph):
            continue
        candidate_forward_paths = _direct_forward_paths(
            candidate,
            ownership_graph=ownership_graph,
            workspace_root=workspace_root,
        )
        if target in candidate_forward_paths:
            return candidate
    source_edges = _ownership_direct_edges(source, ownership_graph)
    target_edges = _ownership_direct_edges(target, ownership_graph)
    for candidate in sorted(source_edges.intersection(target_edges)):
        if candidate in selected_paths or not _is_wrapper_like_path(candidate, ownership_graph):
            continue
        return candidate
    return None


def _paired_service_owner_paths(path_text: str, ownership_graph: dict[str, Any] | None) -> list[str]:
    normalized = str(path_text or "").strip()
    if not normalized or not isinstance(ownership_graph, dict):
        return []
    entry = _ownership_graph_entry(ownership_graph, normalized)
    signals = set(entry.get("signals") or [])
    if "http-controller" not in signals and "controller" not in Path(normalized).stem.lower():
        return []
    direct_edges = _ownership_direct_edges(normalized, ownership_graph)
    package_manifest = str(entry.get("package_manifest") or "").strip()
    parent_dir = posixpath.dirname(normalized)
    controller_tokens = {
        token
        for token in tokenize_text(Path(normalized).stem)
        if token not in {"api", "controller", "http"}
    }
    by_path = ownership_graph.get("by_path") or {}
    candidates: list[tuple[int, str]] = []
    for candidate_path, candidate_entry in by_path.items():
        if not isinstance(candidate_path, str) or candidate_path == normalized or not isinstance(candidate_entry, dict):
            continue
        candidate_signals = set(candidate_entry.get("signals") or [])
        if "service-owner" not in candidate_signals and "service" not in Path(candidate_path).stem.lower():
            continue
        score = 0
        if candidate_path in direct_edges:
            score += 12
        candidate_manifest = str(candidate_entry.get("package_manifest") or "").strip()
        if package_manifest and candidate_manifest == package_manifest:
            score += 4
        candidate_dir = posixpath.dirname(candidate_path)
        if parent_dir and candidate_dir == parent_dir:
            score += 3
        candidate_tokens = {
            token
            for token in tokenize_text(Path(candidate_path).stem)
            if token not in {"service"}
        }
        score += len(controller_tokens.intersection(candidate_tokens)) * 2
        if score <= 0:
            continue
        candidates.append((score, candidate_path))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [path for _score, path in candidates[:3]]


def _paired_controller_owner_paths(path_text: str, ownership_graph: dict[str, Any] | None) -> list[str]:
    normalized = str(path_text or "").strip()
    if not normalized or not isinstance(ownership_graph, dict):
        return []
    entry = _ownership_graph_entry(ownership_graph, normalized)
    signals = set(entry.get("signals") or [])
    if "service-owner" not in signals and "service" not in Path(normalized).stem.lower():
        return []
    direct_edges = _ownership_direct_edges(normalized, ownership_graph)
    package_manifest = str(entry.get("package_manifest") or "").strip()
    parent_dir = posixpath.dirname(normalized)
    service_tokens = {
        token
        for token in tokenize_text(Path(normalized).stem)
        if token not in {"service"}
    }
    by_path = ownership_graph.get("by_path") or {}
    candidates: list[tuple[int, str]] = []
    for candidate_path, candidate_entry in by_path.items():
        if not isinstance(candidate_path, str) or candidate_path == normalized or not isinstance(candidate_entry, dict):
            continue
        candidate_signals = set(candidate_entry.get("signals") or [])
        if "http-controller" not in candidate_signals and "controller" not in Path(candidate_path).stem.lower():
            continue
        score = 0
        if candidate_path in direct_edges:
            score += 12
        candidate_manifest = str(candidate_entry.get("package_manifest") or "").strip()
        if package_manifest and candidate_manifest == package_manifest:
            score += 4
        candidate_dir = posixpath.dirname(candidate_path)
        if parent_dir and candidate_dir == parent_dir:
            score += 3
        candidate_tokens = {
            token
            for token in tokenize_text(Path(candidate_path).stem)
            if token not in {"api", "controller", "http"}
        }
        score += len(service_tokens.intersection(candidate_tokens)) * 2
        if score <= 0:
            continue
        candidates.append((score, candidate_path))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [path for _score, path in candidates[:3]]


def _add_controller_service_pair_entries(
    entries: dict[str, dict[str, Any]],
    *,
    ownership_graph: dict[str, Any] | None,
    route_id: str | None,
    base_score: float,
) -> None:
    for controller_path in list(entries):
        for index, service_path in enumerate(_paired_service_owner_paths(controller_path, ownership_graph)[:2]):
            _add_triage_entry(
                entries,
                path_text=service_path,
                base_score=base_score - (index * 4),
                route_id=route_id,
                source="controller_service_pair",
            )


def _stabilize_readiness_owner_order(
    primary_owner_files: list[str],
    *,
    request_text: str | None,
    ownership_graph: dict[str, Any] | None,
) -> list[str]:
    if not primary_owner_files or not ownership_graph:
        return primary_owner_files
    query_tokens = set(tokenize_text(request_text))
    if not {"backend", "contract", "failure", "health", "readiness", "ready"}.intersection(query_tokens):
        return primary_owner_files
    ordered = list(primary_owner_files)
    for index, path_text in enumerate(list(ordered)):
        service_signals = set(_ownership_graph_entry(ownership_graph, path_text).get("signals") or [])
        if "service-owner" not in service_signals:
            continue
        controller_paths = _paired_controller_owner_paths(path_text, ownership_graph)
        controller_index = next(
            (
                candidate_index
                for candidate_index, candidate_path in enumerate(ordered)
                if candidate_path in controller_paths
            ),
            None,
        )
        if controller_index is None or controller_index < index:
            continue
        controller_path = ordered.pop(controller_index)
        ordered.insert(index, controller_path)
    return ordered


def _has_direct_ownership_support(
    path_text: str,
    *,
    supporting_paths: set[str],
    ownership_graph: dict[str, Any] | None,
) -> bool:
    normalized = str(path_text or "").strip()
    if not normalized or not supporting_paths:
        return False
    related = _ownership_direct_edges(normalized, ownership_graph)
    if related.intersection(supporting_paths):
        return True
    for support_path in supporting_paths:
        if normalized in _ownership_direct_edges(support_path, ownership_graph):
            return True
    return False


def _runtime_candidate_flags(
    *,
    path_text: str,
    why_text: str | None,
    do_not_scan_paths: list[str],
    exact_package_roots: list[str],
    exact_source_paths: list[str],
    excluded_families: list[str] | None = None,
    ownership_graph: dict[str, Any] | None = None,
    supporting_paths: set[str] | None = None,
) -> dict[str, bool]:
    normalized = str(path_text or "").strip()
    blocked = any(_path_matches_prefix(normalized, prefix) for prefix in do_not_scan_paths)
    conflict = _path_conflicts_with_exact_package(
        normalized,
        exact_package_roots=exact_package_roots,
        exact_source_paths=exact_source_paths,
    )
    excluded_family = _matched_excluded_family(normalized, excluded_families or [])
    excluded_without_support = bool(excluded_family) and not _has_direct_ownership_support(
        normalized,
        supporting_paths=supporting_paths or set(),
        ownership_graph=ownership_graph,
    )
    exact_manifest = normalized in _exact_package_manifest_paths(
        [{"source_path": path_text} for path_text in exact_source_paths],
        ownership_graph=ownership_graph,
    )
    return {
        "exact_source": normalized in exact_source_paths,
        "exact_manifest": exact_manifest,
        "blocked": blocked,
        "conflict": conflict,
        "reason_distractor": "distractor" in str(why_text or "").lower(),
        "doc_or_config": _is_doc_or_config_path(normalized),
        "excluded_without_support": excluded_without_support,
    }


def _seed_supporting_paths(
    *,
    owner_candidates: list[dict[str, Any]],
    do_not_scan_paths: list[str],
    exact_source_paths: list[str],
    excluded_families: list[str],
) -> set[str]:
    supporting_paths = set(exact_source_paths)
    for candidate in owner_candidates:
        path_text = str(candidate.get("path") or "").strip()
        if not path_text:
            continue
        if any(_path_matches_prefix(path_text, prefix) for prefix in do_not_scan_paths):
            continue
        if _matched_excluded_family(path_text, excluded_families):
            continue
        supporting_paths.add(path_text)
        if len(supporting_paths) >= 8:
            break
    return supporting_paths


def _rank_runtime_owner_candidates(
    *,
    owner_candidates: list[dict[str, Any]],
    command_suggestions: list[dict[str, Any]],
    do_not_scan_paths: list[str],
    excluded_families: list[str] | None = None,
    ownership_graph: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    exact_source_paths = _exact_package_source_paths(command_suggestions)
    exact_package_roots = _exact_package_roots(command_suggestions)
    supporting_paths = _seed_supporting_paths(
        owner_candidates=owner_candidates,
        do_not_scan_paths=do_not_scan_paths,
        exact_source_paths=exact_source_paths,
        excluded_families=list(excluded_families or []),
    )

    def _sort_key(indexed_item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
        index, candidate = indexed_item
        path_text = str(candidate.get("path") or "").strip()
        flags = _runtime_candidate_flags(
            path_text=path_text,
            why_text=candidate.get("why"),
            do_not_scan_paths=do_not_scan_paths,
            exact_package_roots=exact_package_roots,
            exact_source_paths=exact_source_paths,
            excluded_families=list(excluded_families or []),
            ownership_graph=ownership_graph,
            supporting_paths=supporting_paths,
        )
        return (
            0 if (flags["exact_source"] or flags["exact_manifest"]) else 1,
            1 if flags["blocked"] else 0,
            1 if flags["conflict"] else 0,
            1 if flags["excluded_without_support"] else 0,
            1 if flags["doc_or_config"] else 0,
            index,
        )

    indexed_candidates = [
        (index, dict(candidate))
        for index, candidate in enumerate(owner_candidates)
        if isinstance(candidate, dict)
    ]
    indexed_candidates.sort(key=_sort_key)
    return [candidate for _index, candidate in indexed_candidates]


def _triage_candidate_files(
    *,
    owner_candidates: list[dict[str, Any]],
    command_suggestions: list[dict[str, Any]],
    next_read_paths: list[str],
    do_not_scan_paths: list[str],
    excluded_families: list[str],
    ownership_graph: dict[str, Any] | None,
    limit: int,
) -> list[str]:
    blocked_prefixes = [str(path).strip() for path in do_not_scan_paths if isinstance(path, str) and path.strip()]
    exact_source_paths = _exact_package_source_paths(command_suggestions)
    exact_package_roots = _exact_package_roots(command_suggestions)
    exact_manifest_paths = _exact_package_manifest_paths(command_suggestions, ownership_graph=ownership_graph)
    supporting_paths = _seed_supporting_paths(
        owner_candidates=owner_candidates,
        do_not_scan_paths=blocked_prefixes,
        exact_source_paths=exact_source_paths,
        excluded_families=excluded_families,
    )
    selected_paths: list[str] = []
    deferred_paths: list[str] = []

    def _defer_path(path_text: str, why_text: str | None = None) -> bool:
        flags = _runtime_candidate_flags(
            path_text=path_text,
            why_text=why_text,
            do_not_scan_paths=blocked_prefixes,
            exact_package_roots=exact_package_roots,
            exact_source_paths=exact_source_paths,
            excluded_families=excluded_families,
            ownership_graph=ownership_graph,
            supporting_paths=supporting_paths,
        )
        return bool(
            flags["blocked"]
            or flags["conflict"]
            or flags["reason_distractor"]
            or flags["excluded_without_support"]
            or (flags["doc_or_config"] and not flags["exact_manifest"])
        )

    def _append_path(path_text: str) -> None:
        normalized = str(path_text or "").strip()
        if normalized and normalized not in selected_paths:
            selected_paths.append(normalized)

    for source_path in [*exact_manifest_paths[:2], *exact_source_paths[:2]]:
        _append_path(source_path)

    for candidate in owner_candidates:
        path_text = str(candidate.get("path") or "").strip()
        if not path_text:
            continue
        if _defer_path(path_text, candidate.get("why")):
            if path_text not in deferred_paths:
                deferred_paths.append(path_text)
            continue
        _append_path(path_text)

    for path_text in next_read_paths:
        normalized = str(path_text or "").strip()
        if not normalized:
            continue
        if _defer_path(normalized):
            if normalized not in deferred_paths:
                deferred_paths.append(normalized)
            continue
        _append_path(normalized)

    for path_text in deferred_paths:
        if len(selected_paths) >= limit:
            break
        _append_path(path_text)

    return selected_paths[:limit]


def _retained_triage_route_ids(
    *,
    request_text: str | None,
    resolved_route: dict[str, Any] | None,
    route_candidates: list[dict[str, Any]],
    owner_route_id: str | None,
) -> list[str]:
    query_tokens = set(tokenize_text(request_text))
    owner_focus_tokens = {"copy", "cta", "entrypoint", "label", "page", "route", "shared"}
    retained: list[str] = []

    def _append(route_text: str | None) -> None:
        normalized = str(route_text or "").strip()
        if normalized and normalized not in retained:
            retained.append(normalized)

    _append(owner_route_id)
    resolved_route_id = str((resolved_route or {}).get("route_id") or "").strip() or None
    if not owner_route_id or owner_route_id == resolved_route_id:
        _append(resolved_route_id)
    dominant_confidence = 0.0
    dominant_score = 0.0
    for candidate in route_candidates:
        if str(candidate.get("route_id") or "").strip() in retained:
            dominant_confidence = max(dominant_confidence, float(candidate.get("confidence") or 0.0))
            dominant_score = max(dominant_score, float(candidate.get("score") or 0.0))
    if not dominant_confidence and isinstance(resolved_route, dict):
        dominant_confidence = float(resolved_route.get("confidence") or 0.0)
        dominant_score = float(resolved_route.get("score") or 0.0)
    for candidate in route_candidates:
        route_text = str(candidate.get("route_id") or "").strip()
        if not route_text or route_text in retained:
            continue
        if route_text == "git" and not GIT_ROUTE_COMMAND_HINTS.intersection(query_tokens):
            continue
        confidence = float(candidate.get("confidence") or 0.0)
        score = float(candidate.get("score") or 0.0)
        allow_secondary_analysis = (
            route_text == "analysis"
            and bool(owner_focus_tokens.intersection(query_tokens))
            and score >= max(dominant_score - (TRIAGE_SECONDARY_ROUTE_SCORE_DELTA + 6), ROUTE_SCORE_MIN)
        )
        if (
            confidence >= max(dominant_confidence - TRIAGE_SECONDARY_ROUTE_CONFIDENCE_DELTA, 0.0)
            and score >= max(dominant_score - TRIAGE_SECONDARY_ROUTE_SCORE_DELTA, ROUTE_SCORE_MIN)
        ) or allow_secondary_analysis:
            retained.append(route_text)
        if len(retained) >= TRIAGE_MAX_ROUTE_COUNT:
            break
    return retained[:TRIAGE_MAX_ROUTE_COUNT]


def _add_triage_entry(
    entries: dict[str, dict[str, Any]],
    *,
    path_text: str | None,
    base_score: float,
    route_id: str | None,
    source: str,
    summary: str | None = None,
    why: str | None = None,
) -> None:
    normalized = str(path_text or "").strip()
    if not normalized:
        return
    entry = entries.setdefault(
        normalized,
        {
            "path": normalized,
            "base_score": float(base_score),
            "route_ids": set(),
            "sources": set(),
            "summary": summary,
            "why": why,
        },
    )
    entry["base_score"] = max(float(entry.get("base_score") or 0.0), float(base_score))
    if route_id:
        entry["route_ids"].add(str(route_id))
    entry["sources"].add(source)
    if summary and not entry.get("summary"):
        entry["summary"] = summary
    if why and not entry.get("why"):
        entry["why"] = why


def _load_retained_route_projections(
    *,
    store_path: Path | None,
    workspace_context: dict[str, Any],
    route_ids: list[str],
) -> list[dict[str, Any]]:
    if store_path is None:
        return []
    projections: list[dict[str, Any]] = []
    for route_id in route_ids:
        projection = _load_route_shortlist_projection(
            store_path=store_path,
            workspace_context=workspace_context,
            route_id=route_id,
        )
        if projection:
            projections.append(projection)
    return projections


def _merged_command_suggestions(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for group in groups:
        for suggestion in group:
            if not isinstance(suggestion, dict):
                continue
            command = str(suggestion.get("command") or "").strip()
            source_path = str(suggestion.get("source_path") or "").strip()
            key = (command, source_path)
            if not command or key in seen_keys:
                continue
            seen_keys.add(key)
            suggestions.append(dict(suggestion))
    return suggestions


def _seed_triage_entries(
    *,
    preflight: dict[str, Any],
    retained_route_ids: list[str],
    workspace_context: dict[str, Any],
    store_path: Path | None,
    ownership_graph: dict[str, Any] | None,
    include_script_references: bool,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    dominant_route_id = str(preflight.get("owner_route_id") or preflight.get("route_id") or "").strip() or None
    preflight_owner_candidates = [
        dict(item)
        for item in (preflight.get("owner_candidates") or [])
        if isinstance(item, dict)
    ]
    preflight_command_suggestions = [
        dict(item)
        for item in (preflight.get("command_suggestions") or [])
        if isinstance(item, dict)
    ]
    preflight_support_paths = {
        str(item.get("path") or "").strip()
        for item in preflight_owner_candidates[:6]
        if str(item.get("path") or "").strip()
    }
    preflight_support_families = {
        _path_family(path_text)
        for path_text in preflight_support_paths
        if _path_family(path_text)
    }
    route_projections = _load_retained_route_projections(
        store_path=store_path,
        workspace_context=workspace_context,
        route_ids=retained_route_ids,
    )
    merged_command_suggestions = _merged_command_suggestions(
        preflight_command_suggestions,
        *[
            [
                dict(item)
                for item in (projection.get("command_suggestions") or [])
                if isinstance(item, dict)
                and (
                    _path_family(str(item.get("source_path") or "").strip()) in preflight_support_families
                    or str(item.get("source_path") or "").strip() in preflight_support_paths
                    or _has_direct_ownership_support(
                        str(item.get("source_path") or "").strip(),
                        supporting_paths=preflight_support_paths,
                        ownership_graph=ownership_graph,
                    )
                )
            ]
            for projection in route_projections
        ],
    )
    entries: dict[str, dict[str, Any]] = {}
    for index, path_text in enumerate(
        _exact_package_manifest_paths(merged_command_suggestions, ownership_graph=ownership_graph)
    ):
        _add_triage_entry(entries, path_text=path_text, base_score=120 - index, route_id=dominant_route_id, source="exact_manifest")
    for index, path_text in enumerate(_exact_package_source_paths(merged_command_suggestions)):
        _add_triage_entry(entries, path_text=path_text, base_score=116 - index, route_id=dominant_route_id, source="exact_source")
    if include_script_references:
        for index, path_text in enumerate(_exact_command_reference_paths(merged_command_suggestions)):
            _add_triage_entry(
                entries,
                path_text=path_text,
                base_score=112 - index,
                route_id=dominant_route_id,
                source="script_reference",
            )
    for index, candidate in enumerate(preflight_owner_candidates[:6]):
        _add_triage_entry(
            entries,
            path_text=candidate.get("path"),
            base_score=108 - (index * 4),
            route_id=dominant_route_id,
            source="preflight_owner",
            summary=str(candidate.get("summary") or "").strip() or None,
            why=str(candidate.get("why") or "").strip() or None,
        )
    for index, path_text in enumerate(preflight.get("next_read_paths") or []):
        _add_triage_entry(
            entries,
            path_text=str(path_text),
            base_score=96 - (index * 3),
            route_id=dominant_route_id,
            source="next_read",
        )
    for projection in route_projections:
        projection_route_id = str(projection.get("route_id") or "").strip() or None
        for index, candidate in enumerate((projection.get("owner_candidates") or [])[:2]):
            if not isinstance(candidate, dict):
                continue
            _add_triage_entry(
                entries,
                path_text=candidate.get("path"),
                base_score=92 - (index * 4),
                route_id=projection_route_id,
                source="route_projection_owner",
                summary=str(candidate.get("summary") or "").strip() or None,
                why=str(candidate.get("why") or "").strip() or None,
            )
        for index, path_text in enumerate((projection.get("priority_paths") or [])[:2]):
            _add_triage_entry(
                entries,
                path_text=str(path_text),
                base_score=80 - (index * 3),
                route_id=projection_route_id,
                source="route_projection_priority",
            )
    _add_controller_service_pair_entries(
        entries,
        ownership_graph=ownership_graph,
        route_id=dominant_route_id,
        base_score=90.0,
    )
    graph_seed_paths = list(entries)[:6]
    for path_text in graph_seed_paths:
        related_paths = sorted(_ownership_related_paths(path_text, ownership_graph))
        for index, related_path in enumerate(related_paths[:3]):
            _add_triage_entry(
                entries,
                path_text=related_path,
                base_score=66 - (index * 3),
                route_id=dominant_route_id,
                source="graph_neighbor",
            )
    projection_route_ids = [
        str(projection.get("route_id") or "").strip()
        for projection in route_projections
        if str(projection.get("route_id") or "").strip()
    ]
    return entries, merged_command_suggestions, projection_route_ids


def _restrict_entries_to_exact_package_slice(
    entries: dict[str, dict[str, Any]],
    *,
    command_suggestions: list[dict[str, Any]],
    supporting_paths: set[str],
    ownership_graph: dict[str, Any] | None,
    response_mode: str,
) -> dict[str, dict[str, Any]]:
    if response_mode != "command_discovery":
        return entries
    exact_source_paths = _exact_package_source_paths(command_suggestions)
    exact_package_roots = [root for root in _exact_package_roots(command_suggestions) if root]
    if len(exact_source_paths) != 1 or not exact_package_roots:
        return entries
    filtered: dict[str, dict[str, Any]] = {}
    for path_text, entry in entries.items():
        if any(_path_matches_prefix(path_text, root) for root in exact_package_roots):
            filtered[path_text] = entry
            continue
        if path_text in supporting_paths:
            filtered[path_text] = entry
            continue
        if _has_direct_ownership_support(
            path_text,
            supporting_paths=supporting_paths,
            ownership_graph=ownership_graph,
        ):
            filtered[path_text] = entry
    return filtered if len(filtered) >= 3 else entries


def _score_triage_entries(
    entries: dict[str, dict[str, Any]],
    *,
    request_text: str | None,
    constraints: dict[str, Any],
    response_mode: str,
    command_suggestions: list[dict[str, Any]],
    do_not_scan_paths: list[str],
    excluded_families: list[str],
    ownership_graph: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    exact_source_paths = _exact_package_source_paths(command_suggestions)
    exact_package_roots = _exact_package_roots(command_suggestions)
    query_tokens = set(tokenize_text(request_text))
    supporting_paths = set(exact_source_paths)
    for entry in sorted(entries.values(), key=lambda item: (-float(item.get("base_score") or 0.0), str(item.get("path") or ""))):
        path_text = str(entry.get("path") or "").strip()
        if not path_text or _matched_excluded_family(path_text, excluded_families):
            continue
        if any(_path_matches_prefix(path_text, prefix) for prefix in do_not_scan_paths):
            continue
        supporting_paths.add(path_text)
        if len(supporting_paths) >= 8:
            break
    scored: list[dict[str, Any]] = []
    for entry in entries.values():
        path_text = str(entry.get("path") or "").strip()
        if not path_text:
            continue
        flags = _runtime_candidate_flags(
            path_text=path_text,
            why_text=entry.get("why"),
            do_not_scan_paths=do_not_scan_paths,
            exact_package_roots=exact_package_roots,
            exact_source_paths=exact_source_paths,
            excluded_families=excluded_families,
            ownership_graph=ownership_graph,
            supporting_paths=supporting_paths,
        )
        final_score = float(entry.get("base_score") or 0.0)
        path = Path(path_text)
        path_tokens = set(tokenize_text(path_text))
        if flags["exact_source"]:
            final_score += 32.0
        if flags["exact_manifest"]:
            final_score += 18.0
        if path_text == "package.json" and not (flags["exact_source"] or flags["exact_manifest"]):
            final_score -= 36.0
        if response_mode == "owner_focus" and path.name == "package.json" and not flags["exact_manifest"]:
            final_score -= 24.0
        if constraints.get("suppress_commands") and path.name == "package.json":
            final_score -= 28.0
        elif constraints.get("owner_files_only") and path.name == "package.json" and not flags["exact_source"]:
            final_score -= 12.0
        if _is_wrapper_like_path(path_text, ownership_graph):
            final_score -= 8.0
            if response_mode == "owner_focus" and {"entrypoint", "page", "route"}.intersection(query_tokens):
                final_score -= 20.0
        if (
            path.name.lower() in {"page.tsx", "page.jsx"}
            and "checkout" in query_tokens
            and "checkout" not in path_tokens
        ):
            final_score -= 26.0
        if response_mode == "owner_focus" and path.parts and path.parts[0] in {"scripts", "tools"}:
            if not query_tokens.intersection({"helper", "runner", "script", "tool", "tools"}):
                final_score -= 40.0
        if {"cta", "copy", "label"}.intersection(query_tokens) and "label" in path.name.lower():
            final_score += 18.0
        if {"entrypoint", "page", "route"}.intersection(query_tokens) and path.name.lower() in {"page.tsx", "page.jsx"}:
            final_score += 18.0
        readiness_query = {"contract", "failure", "health", "readiness", "ready", "triage"}.intersection(query_tokens)
        if readiness_query and "controller" in path.name.lower():
            final_score += 8.0
        if readiness_query and "service" in path.name.lower():
            final_score += 4.0
        if "controller_service_pair" in entry.get("sources", set()):
            final_score += 10.0
        if readiness_query and path_text.startswith("apps/server/test/"):
            final_score += 6.0
        if {"playwright", "spec", "verification"}.intersection(query_tokens) and (
            path.name.lower().endswith(".spec.ts") or path.name.lower().endswith(".spec.tsx")
        ):
            final_score += 6.0
            if response_mode == "command_discovery":
                final_score += 8.0
        if "script_reference" in entry.get("sources", set()) and _is_test_like_path(path_text):
            final_score += 12.0
        elif _is_test_like_path(path_text):
            final_score -= 12.0
        if readiness_query and _is_test_like_path(path_text) and not path_text.startswith("apps/server/test/"):
            final_score -= 18.0
        if readiness_query and path_text == "package.json":
            final_score -= 16.0
        if "graph_neighbor" in entry.get("sources", set()) and len(entry.get("sources", set())) == 1:
            final_score -= 8.0
        if flags["reason_distractor"]:
            final_score -= 24.0
        if flags["doc_or_config"] and not flags["exact_manifest"]:
            final_score -= 36.0
        if flags["conflict"] and not flags["exact_manifest"]:
            final_score -= 44.0
        if flags["excluded_without_support"]:
            final_score -= 64.0
        if flags["blocked"] and not (flags["exact_source"] or flags["exact_manifest"]):
            final_score -= 84.0
        scored.append(
            {
                **entry,
                "family": _path_family(path_text),
                "flags": flags,
                "final_score": final_score,
            }
        )
    scored.sort(key=lambda item: (-float(item.get("final_score") or 0.0), str(item.get("path") or "")))
    return scored


def _triage_response_mode(
    *,
    request_text: str | None,
    constraints: dict[str, Any],
    candidate_commands: list[str],
) -> str:
    if constraints.get("owner_files_only") or constraints.get("suppress_commands") or not candidate_commands:
        return "owner_focus"
    query_tokens = set(tokenize_text(request_text))
    if query_tokens.intersection(TRIAGE_COMMAND_DISCOVERY_HINTS):
        return "command_discovery"
    return "owner_focus"


def _select_triage_entries(
    scored_entries: list[dict[str, Any]],
    *,
    limit: int,
    required_paths: list[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_paths: set[str] = set()
    family_counts: dict[str, int] = {}
    has_code_owner_candidates = any(
        not entry["flags"]["doc_or_config"] and not entry["flags"]["blocked"] and not entry["flags"]["excluded_without_support"]
        for entry in scored_entries
    )
    deferred: list[dict[str, Any]] = []
    for entry in scored_entries:
        path_text = str(entry.get("path") or "").strip()
        if not path_text or path_text in selected_paths:
            continue
        flags = entry.get("flags") or {}
        counts_toward_family = not (
            flags.get("doc_or_config")
            or flags.get("exact_manifest")
            or _is_test_like_path(path_text)
        )
        if flags.get("excluded_without_support") or flags.get("conflict"):
            deferred.append(entry)
            continue
        if flags.get("blocked") and not (flags.get("exact_source") or flags.get("exact_manifest")):
            deferred.append(entry)
            continue
        if has_code_owner_candidates and flags.get("doc_or_config") and not flags.get("exact_manifest"):
            deferred.append(entry)
            continue
        family = str(entry.get("family") or "")
        if counts_toward_family and family and family_counts.get(family, 0) >= TRIAGE_MAX_FILES_PER_FAMILY:
            deferred.append(entry)
            continue
        selected.append(entry)
        selected_paths.add(path_text)
        if counts_toward_family and family:
            family_counts[family] = family_counts.get(family, 0) + 1
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for entry in deferred:
            path_text = str(entry.get("path") or "").strip()
            family = str(entry.get("family") or "")
            flags = entry.get("flags") or {}
            counts_toward_family = not (
                flags.get("doc_or_config")
                or flags.get("exact_manifest")
                or _is_test_like_path(path_text)
            )
            if not path_text or path_text in selected_paths:
                continue
            if counts_toward_family and family and family_counts.get(family, 0) >= TRIAGE_MAX_FILES_PER_FAMILY:
                continue
            selected.append(entry)
            selected_paths.add(path_text)
            if counts_toward_family and family:
                family_counts[family] = family_counts.get(family, 0) + 1
            if len(selected) >= limit:
                break
    required_lookup = {entry["path"]: entry for entry in scored_entries if isinstance(entry.get("path"), str)}
    for required_path in required_paths:
        if required_path in selected_paths or required_path not in required_lookup:
            continue
        replacement_index = None
        for index in range(len(selected) - 1, -1, -1):
            candidate = selected[index]
            if candidate["path"] not in required_paths:
                replacement_index = index
                break
        required_entry = required_lookup[required_path]
        if len(selected) < limit:
            selected.append(required_entry)
        elif replacement_index is not None:
            selected[replacement_index] = required_entry
        selected_paths = {str(entry.get("path") or "") for entry in selected}
    selected.sort(key=lambda item: (-float(item.get("final_score") or 0.0), str(item.get("path") or "")))
    return selected[:limit]


def _select_exact_command_minimal_entries(
    scored_entries: list[dict[str, Any]],
    *,
    limit: int,
    required_manifest_paths: list[str],
    ownership_graph: dict[str, Any] | None,
    request_text: str | None,
) -> list[dict[str, Any]]:
    if not required_manifest_paths:
        return []
    entry_lookup = {
        str(entry.get("path") or "").strip(): entry
        for entry in scored_entries
        if isinstance(entry.get("path"), str) and str(entry.get("path") or "").strip()
    }
    selected: list[dict[str, Any]] = []
    selected_paths: set[str] = set()
    prefer_test_evidence = bool(set(tokenize_text(request_text)).intersection(TRIAGE_TEST_EVIDENCE_HINTS))

    def _append(entry: dict[str, Any] | None) -> None:
        if not isinstance(entry, dict):
            return
        path_text = str(entry.get("path") or "").strip()
        if not path_text or path_text in selected_paths or len(selected) >= limit:
            return
        selected.append(entry)
        selected_paths.add(path_text)

    def _candidate_priority(entry: dict[str, Any], *, manifest_path: str, manifest_anchor_tokens: set[str]) -> tuple[int, int, float, str]:
        path_text = str(entry.get("path") or "").strip()
        flags = entry.get("flags") or {}
        is_test_like = _is_test_like_path(path_text)
        anchor_overlap = len(manifest_anchor_tokens.intersection(tokenize_text(path_text)))
        same_manifest = _package_manifest_for_path(path_text, ownership_graph) == manifest_path
        if prefer_test_evidence:
            if is_test_like and anchor_overlap:
                tier = 0
            elif same_manifest and is_test_like:
                tier = 1
            elif same_manifest and not flags.get("doc_or_config"):
                tier = 2
            elif is_test_like:
                tier = 3
            else:
                tier = 4
        else:
            if same_manifest and not flags.get("doc_or_config") and not is_test_like:
                tier = 0
            elif same_manifest and is_test_like:
                tier = 1
            elif is_test_like and anchor_overlap:
                tier = 2
            else:
                tier = 3
        return (tier, -anchor_overlap, -float(entry.get("final_score") or 0.0), path_text)

    manifests_found = 0
    for manifest_path in required_manifest_paths:
        manifest_entry = entry_lookup.get(manifest_path)
        if manifest_entry is None:
            continue
        manifests_found += 1
        _append(manifest_entry)
        manifest_anchor_tokens = {
            token
            for token in tokenize_text(manifest_path)
            if token not in {"app", "apps", "json", "package", "packages", "test", "tests"}
        }
        confirming_candidates: list[dict[str, Any]] = []
        for entry in scored_entries:
            path_text = str(entry.get("path") or "").strip()
            flags = entry.get("flags") or {}
            is_test_like = _is_test_like_path(path_text)
            anchor_overlap = bool(manifest_anchor_tokens.intersection(tokenize_text(path_text)))
            same_manifest = _package_manifest_for_path(path_text, ownership_graph) == manifest_path
            if (
                not path_text
                or path_text in selected_paths
                or path_text == manifest_path
                or flags.get("blocked")
                or flags.get("excluded_without_support")
                or flags.get("reason_distractor")
            ):
                continue
            if flags.get("doc_or_config") and not is_test_like:
                continue
            if flags.get("conflict") and not (prefer_test_evidence and is_test_like and anchor_overlap):
                continue
            if not same_manifest and not (prefer_test_evidence and is_test_like and anchor_overlap):
                continue
            confirming_candidates.append(entry)
        confirming_candidates.sort(
            key=lambda entry: _candidate_priority(
                entry,
                manifest_path=manifest_path,
                manifest_anchor_tokens=manifest_anchor_tokens,
            )
        )
        _append(confirming_candidates[0] if confirming_candidates else None)
    if manifests_found != len(required_manifest_paths) or not selected:
        return []
    return selected[:limit]


def _refine_owner_focus_entries(
    selected_entries: list[dict[str, Any]],
    *,
    scored_entries: list[dict[str, Any]],
    request_text: str | None,
    response_mode: str,
    ownership_graph: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if response_mode != "owner_focus":
        return selected_entries
    query_tokens = set(tokenize_text(request_text))
    if not selected_entries:
        return selected_entries
    selected = list(selected_entries)
    limit = len(selected_entries)

    def _selected_paths() -> set[str]:
        return {
            str(entry.get("path") or "").strip()
            for entry in selected
            if isinstance(entry.get("path"), str) and str(entry.get("path") or "").strip()
        }

    def _eligible_replacement(predicate: Any) -> dict[str, Any] | None:
        selected_paths = _selected_paths()
        for entry in scored_entries:
            path_text = str(entry.get("path") or "").strip()
            flags = entry.get("flags") or {}
            if (
                not path_text
                or path_text in selected_paths
                or flags.get("blocked")
                or flags.get("conflict")
                or flags.get("excluded_without_support")
                or flags.get("reason_distractor")
            ):
                continue
            if predicate(entry):
                return entry
        return None

    def _replace_at(index: int, replacement: dict[str, Any] | None) -> None:
        if not isinstance(replacement, dict):
            return
        selected[index] = replacement

    def _graph_signals(path_text: str) -> set[str]:
        return set(_ownership_graph_entry(ownership_graph, path_text).get("signals") or [])

    def _supporting_replacement_index(
        *,
        preferred_family: str,
        excluded_paths: set[str],
    ) -> int | None:
        for same_family_only in (True, False):
            replacement_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if (
                        str(selected[index].get("path") or "").strip()
                        and str(selected[index].get("path") or "").strip() not in excluded_paths
                        and (
                            not same_family_only
                            or _path_family(str(selected[index].get("path") or "").strip()) == preferred_family
                        )
                        and (
                            _is_test_like_path(str(selected[index].get("path") or "").strip())
                            or (selected[index].get("flags") or {}).get("doc_or_config")
                        )
                    )
                ),
                None,
            )
            if replacement_index is not None:
                return replacement_index
        return None

    if {"entrypoint", "page", "route"}.intersection(query_tokens):
        for index, entry in enumerate(list(selected)):
            path_text = str(entry.get("path") or "").strip()
            if not _is_wrapper_like_path(path_text, ownership_graph):
                continue
            path = Path(path_text)
            family = str(entry.get("family") or "")
            has_selected_page = any(
                str(candidate.get("family") or "") == family
                and Path(str(candidate.get("path") or "").strip()).name.lower() in {"page.tsx", "page.jsx"}
                for candidate in selected
            )
            replacement = _eligible_replacement(
                lambda candidate: str(candidate.get("family") or "") == family
                and Path(str(candidate.get("path") or "").strip()).name.lower() in {"page.tsx", "page.jsx"}
            )
            if replacement is None and has_selected_page:
                replacement = _eligible_replacement(
                    lambda candidate: not _is_wrapper_like_path(
                        str(candidate.get("path") or "").strip(),
                        ownership_graph,
                    )
                )
            if replacement is None and has_selected_page:
                selected[index] = {}
            else:
                _replace_at(index, replacement)
        if not any(
            Path(str(entry.get("path") or "").strip()).name.lower() in {"page.tsx", "page.jsx"}
            for entry in selected
        ):
            replacement = _eligible_replacement(
                lambda candidate: Path(str(candidate.get("path") or "").strip()).name.lower() in {"page.tsx", "page.jsx"}
            )
            if replacement is not None:
                replacement_index = next(
                    (
                        index
                        for index in range(len(selected) - 1, -1, -1)
                        if (
                            _is_test_like_path(str(selected[index].get("path") or "").strip())
                            or (selected[index].get("flags") or {}).get("doc_or_config")
                            or _is_wrapper_like_path(str(selected[index].get("path") or "").strip(), ownership_graph)
                        )
                    ),
                    None,
                )
                if replacement_index is None and len(selected) < limit:
                    selected.append(replacement)
                elif replacement_index is not None:
                    _replace_at(replacement_index, replacement)

    if {"cta", "copy", "label"}.intersection(query_tokens) and not any(
        "label" in Path(str(entry.get("path") or "").strip()).name.lower()
        for entry in selected
    ):
        replacement = _eligible_replacement(
            lambda candidate: "label" in Path(str(candidate.get("path") or "").strip()).name.lower()
        )
        if replacement is not None:
            replacement_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if Path(str(selected[index].get("path") or "").strip()).parts[:1] and Path(str(selected[index].get("path") or "").strip()).parts[0] in {"scripts", "tools"}
                ),
                None,
            )
            if replacement_index is None:
                replacement_index = next(
                    (
                        index
                        for index in range(len(selected) - 1, -1, -1)
                        if _is_wrapper_like_path(
                            str(selected[index].get("path") or "").strip(),
                            ownership_graph,
                        )
                    ),
                    None,
                )
            if replacement_index is not None:
                _replace_at(replacement_index, replacement)

    for index, entry in enumerate(list(selected)):
        path_text = str(entry.get("path") or "").strip()
        path = Path(path_text)
        if not path.parts or path.parts[0] not in {"scripts", "tools"}:
            continue
        replacement = _eligible_replacement(
            lambda candidate: Path(str(candidate.get("path") or "").strip()).parts[:1]
            and Path(str(candidate.get("path") or "").strip()).parts[0] not in {"scripts", "tools"}
        )
        _replace_at(index, replacement)

    readiness_backend_contract = {"backend", "contract", "failure", "health", "readiness", "ready"}.intersection(
        query_tokens
    )
    if ownership_graph and readiness_backend_contract:
        for controller_entry in list(selected):
            controller_path = str(controller_entry.get("path") or "").strip()
            if not controller_path:
                continue
            controller_signals = _graph_signals(controller_path)
            if "http-controller" not in controller_signals and "controller" not in Path(controller_path).stem.lower():
                continue
            paired_service_paths = _paired_service_owner_paths(controller_path, ownership_graph)
            if not paired_service_paths:
                continue
            if any(
                candidate_path in _selected_paths() and "service-owner" in _graph_signals(candidate_path)
                for candidate_path in paired_service_paths
            ):
                continue
            replacement = _eligible_replacement(
                lambda candidate, paired_service_paths=paired_service_paths: (
                    str(candidate.get("path") or "").strip() in paired_service_paths
                    and "service-owner" in _graph_signals(str(candidate.get("path") or "").strip())
                )
            )
            if replacement is None:
                continue
            service_path = str(replacement.get("path") or "").strip()
            service_family = _path_family(service_path)
            replacement_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if not str(selected[index].get("path") or "").strip()
                ),
                None,
            )
            if replacement_index is None:
                replacement_index = _supporting_replacement_index(
                    preferred_family=service_family,
                    excluded_paths={controller_path, service_path},
                )
            if replacement_index is None and len(selected) < limit:
                selected.append(replacement)
                continue
            if replacement_index is not None:
                _replace_at(replacement_index, replacement)

    for service_entry in list(selected):
        service_path = str(service_entry.get("path") or "").strip()
        if not service_path:
            continue
            service_signals = _graph_signals(service_path)
            if "service-owner" not in service_signals and "service" not in Path(service_path).stem.lower():
                continue
            paired_controller_paths = _paired_controller_owner_paths(service_path, ownership_graph)
            if not paired_controller_paths:
                continue
            if any(
                candidate_path in _selected_paths() and "http-controller" in _graph_signals(candidate_path)
                for candidate_path in paired_controller_paths
            ):
                continue
            replacement = _eligible_replacement(
                lambda candidate, paired_controller_paths=paired_controller_paths: (
                    str(candidate.get("path") or "").strip() in paired_controller_paths
                    and "http-controller" in _graph_signals(str(candidate.get("path") or "").strip())
                )
            )
            if replacement is None:
                continue
            controller_path = str(replacement.get("path") or "").strip()
            replacement_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if not str(selected[index].get("path") or "").strip()
                ),
                None,
            )
            if replacement_index is None:
                replacement_index = _supporting_replacement_index(
                    preferred_family=_path_family(controller_path),
                    excluded_paths={controller_path, service_path},
                )
            if replacement_index is None and len(selected) < limit:
                selected.append(replacement)
                continue
            if replacement_index is not None:
                _replace_at(replacement_index, replacement)

    if query_tokens.intersection(TRIAGE_TEST_EVIDENCE_HINTS):
        primary_anchor_tokens = {
            token
            for entry in selected
            for token in tokenize_text(str(entry.get("path") or "").strip())
            if token
            and token not in {"app", "apps", "package", "json", "packages", "src", "test", "tests"}
            and not _is_test_like_path(str(entry.get("path") or "").strip())
        }

        def _test_alignment(entry: dict[str, Any]) -> tuple[int, float, str]:
            path_text = str(entry.get("path") or "").strip()
            if not _is_test_like_path(path_text):
                return (-1, float("-inf"), path_text)
            path_tokens = set(tokenize_text(path_text))
            overlap = len((query_tokens | primary_anchor_tokens).intersection(path_tokens))
            if "playwright" in query_tokens and path_text.startswith("tests/"):
                overlap += 2
            return (overlap, float(entry.get("final_score") or 0.0), path_text)

        best_test_candidate = next(
            (
                entry
                for entry in sorted(scored_entries, key=_test_alignment, reverse=True)
                if _is_test_like_path(str(entry.get("path") or "").strip())
            ),
            None,
        )
        if best_test_candidate is not None:
            current_test_indices = [
                index
                for index, entry in enumerate(selected)
                if _is_test_like_path(str(entry.get("path") or "").strip())
            ]
            current_best_alignment = max(
                (_test_alignment(selected[index]) for index in current_test_indices),
                default=(-1, float("-inf"), ""),
            )
            if _test_alignment(best_test_candidate) > current_best_alignment:
                replacement_index = current_test_indices[-1] if current_test_indices else None
                if replacement_index is None:
                    replacement_index = _supporting_replacement_index(
                        preferred_family=_path_family(str(best_test_candidate.get("path") or "").strip()),
                        excluded_paths={str(best_test_candidate.get("path") or "").strip()},
                    )
                if replacement_index is None and len(selected) < limit:
                    selected.append(best_test_candidate)
                elif replacement_index is not None:
                    _replace_at(replacement_index, best_test_candidate)

    deduped: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for entry in selected:
        path_text = str(entry.get("path") or "").strip()
        if not path_text or path_text in seen_paths:
            continue
        deduped.append(entry)
        seen_paths.add(path_text)
        if len(deduped) >= limit:
            break
    return deduped


def _partition_triage_entries(
    selected_entries: list[dict[str, Any]],
    *,
    request_text: str | None,
    constraints: dict[str, Any],
    candidate_commands: list[str],
    ownership_graph: dict[str, Any] | None = None,
) -> tuple[list[str], list[str], str]:
    response_mode = _triage_response_mode(
        request_text=request_text,
        constraints=constraints,
        candidate_commands=candidate_commands,
    )
    query_tokens = set(tokenize_text(request_text))
    selected_paths = [
        str(entry.get("path") or "").strip()
        for entry in selected_entries
        if isinstance(entry.get("path"), str) and str(entry.get("path") or "").strip()
    ]
    non_wrapper_paths = {
        path_text
        for path_text in selected_paths
        if not _is_wrapper_like_path(path_text, ownership_graph)
    }
    primary_owner_files: list[str] = []
    supporting_evidence_files: list[str] = []
    for entry in selected_entries:
        path_text = str(entry.get("path") or "").strip()
        if not path_text:
            continue
        if (
            response_mode == "owner_focus"
            and not query_tokens.intersection({"shell", "wrapper", "reexport"})
            and _is_wrapper_like_path(path_text, ownership_graph)
        ):
            family = _path_family(path_text)
            same_family_non_wrapper = any(
                candidate != path_text
                and _path_family(candidate) == family
                and candidate in non_wrapper_paths
                for candidate in selected_paths
            )
            related_non_wrapper = bool(_ownership_direct_edges(path_text, ownership_graph).intersection(non_wrapper_paths))
            if same_family_non_wrapper and related_non_wrapper:
                continue
        flags = entry.get("flags") or {}
        if response_mode == "command_discovery":
            target = primary_owner_files if flags.get("exact_manifest") else supporting_evidence_files
        else:
            target = (
                supporting_evidence_files
                if (
                    flags.get("exact_manifest")
                    or flags.get("doc_or_config")
                    or _is_test_like_path(path_text)
                    or Path(path_text).name == "package.json"
                )
                else primary_owner_files
            )
        if path_text not in target:
            target.append(path_text)
    if not primary_owner_files and supporting_evidence_files:
        primary_owner_files.append(supporting_evidence_files.pop(0))
    return primary_owner_files, supporting_evidence_files, response_mode


def _prune_supporting_evidence_files(
    *,
    primary_owner_files: list[str],
    supporting_evidence_files: list[str],
    request_text: str | None,
    candidate_commands: list[str],
) -> list[str]:
    if candidate_commands:
        return supporting_evidence_files
    query_tokens = set(tokenize_text(request_text))
    allow_test_evidence = bool(query_tokens.intersection(TRIAGE_TEST_EVIDENCE_HINTS))
    keep_manifest = bool(query_tokens.intersection({"manifest", "manifests", "package.json"}))
    pruned: list[str] = []
    manifest_kept = False
    for path_text in supporting_evidence_files:
        normalized = str(path_text or "").strip()
        if not normalized:
            continue
        if not allow_test_evidence and _is_test_like_path(normalized):
            continue
        if not allow_test_evidence and _is_doc_or_config_path(normalized):
            continue
        if Path(normalized).name == "package.json" and primary_owner_files:
            if not keep_manifest or manifest_kept:
                continue
            manifest_kept = True
        if normalized not in pruned:
            pruned.append(normalized)
    return pruned


def _runtime_answer_readiness(
    *,
    selected_entries: list[dict[str, Any]],
    scored_entries: list[dict[str, Any]],
    retained_route_ids: list[str],
    route_candidates: list[dict[str, Any]],
    dominant_route_id: str | None,
    request_text: str | None,
    constraints: dict[str, Any],
    candidate_commands: list[str],
    required_manifest_paths: list[str],
) -> tuple[bool, str]:
    if not selected_entries:
        return False, "No bounded owner shortlist exists yet."
    selected_paths = {str(entry.get("path") or "") for entry in selected_entries}
    selected_route_ids = {
        route_id
        for entry in selected_entries
        for route_id in entry.get("route_ids", set())
        if isinstance(route_id, str) and route_id
    }
    missing_routes = [route_id for route_id in retained_route_ids if route_id and route_id not in selected_route_ids]
    if missing_routes:
        return False, f"The bounded shortlist still misses route family `{missing_routes[0]}`."
    dominant_confidence = 0.0
    dominant_score = 0.0
    query_tokens = set(tokenize_text(request_text))
    for candidate in route_candidates:
        if str(candidate.get("route_id") or "").strip() == str(dominant_route_id or "").strip():
            dominant_confidence = max(dominant_confidence, float(candidate.get("confidence") or 0.0))
            dominant_score = max(dominant_score, float(candidate.get("score") or 0.0))
    if not dominant_confidence and route_candidates:
        dominant_confidence = max(float(route_candidates[0].get("confidence") or 0.0), 0.0)
        dominant_score = max(float(route_candidates[0].get("score") or 0.0), 0.0)
    unresolved_competitor = next(
        (
            candidate
            for candidate in route_candidates
            if str(candidate.get("route_id") or "").strip()
            and str(candidate.get("route_id") or "").strip() not in retained_route_ids
            and (
                str(candidate.get("route_id") or "").strip() != "git"
                or bool(GIT_ROUTE_COMMAND_HINTS.intersection(query_tokens))
            )
            and float(candidate.get("confidence") or 0.0) > 0.0
            and float(candidate.get("confidence") or 0.0) >= max(dominant_confidence - TRIAGE_SECONDARY_ROUTE_CONFIDENCE_DELTA, 0.0)
            and float(candidate.get("score") or 0.0) >= max(dominant_score - TRIAGE_SECONDARY_ROUTE_SCORE_DELTA, ROUTE_SCORE_MIN)
        ),
        None,
    )
    if unresolved_competitor is not None:
        return False, f"A competing `{unresolved_competitor['route_id']}` route still scores close enough to justify one more retrieval step."
    if required_manifest_paths and not set(required_manifest_paths).intersection(selected_paths):
        return False, "The exact package manifest is not in the final owner set yet."
    weakest_selected_score = min(float(entry.get("final_score") or 0.0) for entry in selected_entries)
    unresolved_distractor = next(
        (
            entry
            for entry in scored_entries
            if str(entry.get("path") or "") not in selected_paths
            and float(entry.get("final_score") or 0.0) >= weakest_selected_score
            and (
                (entry.get("flags") or {}).get("blocked")
                or (entry.get("flags") or {}).get("conflict")
                or (entry.get("flags") or {}).get("excluded_without_support")
            )
        ),
        None,
    )
    if unresolved_distractor is not None:
        return False, f"`{unresolved_distractor['path']}` still competes too closely with the final owner set."
    if constraints.get("owner_files_only") or constraints.get("suppress_commands"):
        return True, "Owner-only triage already covers the retained route families without unresolved distractors."
    if candidate_commands:
        return True, "Exact package-level commands and their owning manifests are already in the bounded owner set."
    if len(retained_route_ids) > 1:
        return True, "Composite triage already covers the retained route families in one bounded packet."
    return True, "The bounded owner shortlist is already specific enough to answer without broader retrieval."


def _ready_confidence_reason(
    *,
    constraints: dict[str, Any],
    candidate_commands: list[str],
    retained_route_ids: list[str],
) -> str:
    if candidate_commands and not constraints.get("suppress_commands"):
        return "The bounded triage already covers the owning files and exact package-level commands."
    if len(retained_route_ids) > 1:
        return "The bounded triage already covers the retained route families in one answer-ready packet."
    if constraints.get("owner_files_only") or constraints.get("suppress_commands"):
        return "The bounded triage already covers the owning files without unresolved distractors."
    return "The bounded triage already covers the next owner slice without broader retrieval."


def _follow_up_policy(
    *,
    answer_ready: bool,
    retrieval_mode: str,
    selected_tools: list[str],
    next_read_paths: list[str],
) -> dict[str, Any]:
    if answer_ready:
        return {
            "answer_now": True,
            "allow_search_context_index": False,
            "allow_show_context_structure": False,
            "tool_budget": 0,
            "shell_read_budget": 0,
        }
    profile = retrieval_mode_profile(retrieval_mode)
    selected_tool_names = {str(tool).strip() for tool in selected_tools if str(tool).strip()}
    allow_search_context_index = "search_context_index" in selected_tool_names or bool(next_read_paths)
    allow_show_context_structure = "show_context_structure" in selected_tool_names and not allow_search_context_index
    tool_budget = int(allow_search_context_index) + int(allow_show_context_structure)
    if tool_budget == 0 and selected_tool_names:
        tool_budget = min(len(selected_tool_names), 1)
    shell_read_budget = min(
        int(profile.get("max_targeted_reads") or 0),
        len([path for path in next_read_paths if str(path or "").strip()]),
    )
    return {
        "answer_now": False,
        "allow_search_context_index": allow_search_context_index,
        "allow_show_context_structure": allow_show_context_structure,
        "tool_budget": tool_budget,
        "shell_read_budget": shell_read_budget,
    }


def _build_dependency_edges(
    *,
    primary_owner_files: list[str],
    supporting_evidence_files: list[str],
    ownership_graph: dict[str, Any] | None,
    workspace_root: Path | None = None,
) -> list[dict[str, str]]:
    if not ownership_graph:
        return []
    selected_paths = {
        str(path).strip()
        for path in [*primary_owner_files, *supporting_evidence_files]
        if isinstance(path, str) and str(path).strip()
    }
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    ordered_primary_files = sorted(
        primary_owner_files,
        key=lambda path_text: (
            0
            if Path(path_text).name.lower() in {"page.tsx", "page.jsx"}
            or "http-controller" in set(_ownership_graph_entry(ownership_graph, path_text).get("signals") or [])
            else 1
            if "service-owner" in set(_ownership_graph_entry(ownership_graph, path_text).get("signals") or [])
            else 2,
            path_text,
        ),
    )

    def _append(from_path: str | None, to_path: str | None) -> None:
        source = str(from_path or "").strip()
        target = str(to_path or "").strip()
        key = (source, target)
        if (
            not source
            or not target
            or source == target
            or key in seen_edges
            or len(edges) >= TRIAGE_MAX_DEPENDENCY_EDGES
        ):
            return
        edges.append({"from": source, "to": target})
        seen_edges.add(key)

    for source in ordered_primary_files:
        for target in ordered_primary_files:
            if source == target:
                continue
            if target in _paired_service_owner_paths(source, ownership_graph):
                _append(source, target)
            elif target in _paired_controller_owner_paths(source, ownership_graph):
                _append(target, source)
            if len(edges) >= TRIAGE_MAX_DEPENDENCY_EDGES:
                return edges

    for source in ordered_primary_files:
        for target in ordered_primary_files:
            if source == target:
                continue
            intermediary = _intermediate_wrapper_path(
                source,
                target,
                ownership_graph=ownership_graph,
                selected_paths=selected_paths,
                workspace_root=workspace_root,
            )
            if intermediary:
                _append(source, intermediary)
                _append(intermediary, target)
            elif target in _direct_forward_paths(
                source,
                ownership_graph=ownership_graph,
                workspace_root=workspace_root,
            ):
                _append(source, target)
            if len(edges) >= TRIAGE_MAX_DEPENDENCY_EDGES:
                return edges

    for support_path in supporting_evidence_files:
        manifest_attached = False
        for source in ordered_primary_files:
            if support_path == _package_manifest_for_path(source, ownership_graph):
                _append(source, support_path)
                manifest_attached = True
                break
        if manifest_attached:
            if len(edges) >= TRIAGE_MAX_DEPENDENCY_EDGES:
                return edges
            continue
        for source in ordered_primary_files:
            if support_path in _direct_forward_paths(
                source,
                ownership_graph=ownership_graph,
                workspace_root=workspace_root,
            ):
                _append(source, support_path)
                break
            if source in _ownership_direct_edges(support_path, ownership_graph):
                _append(support_path, source)
                break
        if len(edges) >= TRIAGE_MAX_DEPENDENCY_EDGES:
            return edges

    return edges


def _build_proof_assertions(
    *,
    primary_owner_files: list[str],
    supporting_evidence_files: list[str],
    dependency_edges: list[dict[str, str]],
    request_text: str | None,
    constraints: dict[str, Any],
    candidate_commands: list[str],
    ownership_graph: dict[str, Any] | None,
) -> list[str]:
    assertions: list[str] = []
    selected_paths = {
        str(path).strip()
        for path in [*primary_owner_files, *supporting_evidence_files]
        if isinstance(path, str) and str(path).strip()
    }

    def _append(text: str | None) -> None:
        normalized = str(text or "").strip()
        if not normalized or normalized in assertions or len(assertions) >= TRIAGE_MAX_PROOF_ASSERTIONS:
            return
        assertions.append(normalized)

    if candidate_commands:
        _append("Exact package-level commands are already anchored by the selected manifest owners.")

    if any(
        edge["to"] in _paired_service_owner_paths(edge["from"], ownership_graph)
        or edge["from"] in _paired_controller_owner_paths(edge["to"], ownership_graph)
        for edge in dependency_edges
    ):
        _append("The endpoint/controller and delegated service stay together because the ownership graph links them directly.")

    hidden_wrapper_paths = {
        path_text
        for edge in dependency_edges
        for path_text in (edge.get("from"), edge.get("to"))
        if isinstance(path_text, str)
        and path_text not in selected_paths
        and _is_wrapper_like_path(path_text, ownership_graph)
    }
    if hidden_wrapper_paths:
        _append("A thin wrapper or re-export layer connects retained owners, so that intermediary stays out of `candidate_files`.")

    if constraints.get("excluded_families_unless_imported"):
        _append("Excluded families stay out unless a direct import or dependency edge proves they are required.")

    if supporting_evidence_files and any(
        _is_test_like_path(path_text)
        or _is_doc_or_config_path(path_text)
        or Path(path_text).name == "package.json"
        for path_text in supporting_evidence_files
    ):
        _append("Specs, manifests, and config files stay in supporting evidence because the primary owner slice is already sufficient.")

    if not assertions and primary_owner_files:
        query_tokens = set(tokenize_text(request_text))
        if {"entrypoint", "page", "route"}.intersection(query_tokens):
            _append("The retained primary owner files already cover the route entrypoint and leaf ownership without broader retrieval.")
        else:
            _append("The retained primary owner files already cover the bounded owner slice without broader retrieval.")
    return assertions


def triage_repo_request(
    workspace: str | Path,
    request_text: str | None = None,
    route_id: str | None = None,
    limit: int | None = 4,
    force_refresh: bool = False,
    semantic_mode: str | None = "disabled",
) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    preflight_payload = show_runtime_preflight(
        resolved_workspace,
        request_text=request_text,
        route_id=route_id,
        limit=limit,
        force_refresh=force_refresh,
        semantic_mode=semantic_mode,
    )
    preflight = preflight_payload.get("preflight") or {}
    workspace_context = preflight_payload.get("workspace_context") or {}
    store_path_text = str(preflight_payload.get("context_store_path") or "").strip()
    store_path = Path(store_path_text) if store_path_text else None
    ownership_graph = (
        _load_ownership_graph(store_path=store_path, workspace_context=workspace_context)
        if store_path is not None
        else None
    )
    constraints = parse_runtime_triage_constraints(preflight_payload.get("effective_request_text") or request_text)
    dominant_route_id = str(preflight.get("owner_route_id") or preflight.get("route_id") or "").strip() or None
    retained_route_ids = _retained_triage_route_ids(
        request_text=preflight_payload.get("effective_request_text") or request_text,
        resolved_route=preflight_payload.get("resolved_route") if isinstance(preflight_payload.get("resolved_route"), dict) else None,
        route_candidates=[
            dict(item)
            for item in (preflight_payload.get("route_candidates") or [])
            if isinstance(item, dict)
        ],
        owner_route_id=dominant_route_id,
    )
    entries, merged_command_suggestions, projection_route_ids = _seed_triage_entries(
        preflight=preflight,
        retained_route_ids=retained_route_ids,
        workspace_context=workspace_context,
        store_path=store_path,
        ownership_graph=ownership_graph,
        include_script_references=(
            not constraints.get("suppress_commands")
            or bool(
                set(tokenize_text(preflight_payload.get("effective_request_text") or request_text)).intersection(
                    TRIAGE_TEST_EVIDENCE_HINTS
                )
            )
        ),
    )
    response_mode = _triage_response_mode(
        request_text=preflight_payload.get("effective_request_text") or request_text,
        constraints=constraints,
        candidate_commands=[
            str(item.get("command") or "").strip()
            for item in merged_command_suggestions
            if isinstance(item.get("command"), str) and str(item.get("command") or "").strip()
        ][:4],
    )
    if not constraints.get("suppress_commands"):
        triage_supporting_paths = {
            str(item.get("path") or "").strip()
            for item in (preflight.get("owner_candidates") or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        }
        entries = _restrict_entries_to_exact_package_slice(
            entries,
            command_suggestions=merged_command_suggestions,
            supporting_paths=triage_supporting_paths,
            ownership_graph=ownership_graph,
            response_mode=response_mode,
        )
    combined_retained_routes = list(dict.fromkeys([*retained_route_ids, *projection_route_ids]))[:TRIAGE_MAX_ROUTE_COUNT]
    do_not_scan_paths = list(
        dict.fromkeys(
            [
                str(path)
                for path in (preflight.get("do_not_scan_paths") or [])
                if isinstance(path, str) and path
            ]
            + _excluded_family_prefixes(list(constraints.get("excluded_families_unless_imported") or []))
        )
    )[:8]
    scoring_command_suggestions = [] if constraints.get("suppress_commands") else merged_command_suggestions
    scored_entries = _score_triage_entries(
        entries,
        request_text=preflight_payload.get("effective_request_text") or request_text,
        constraints=constraints,
        response_mode=response_mode,
        command_suggestions=scoring_command_suggestions,
        do_not_scan_paths=do_not_scan_paths,
        excluded_families=list(constraints.get("excluded_families_unless_imported") or []),
        ownership_graph=ownership_graph,
    )
    selection_limit = min(max(int(limit or 4), 1), TRIAGE_MAX_FILES)
    visible_commands = [] if constraints.get("suppress_commands") else [
        str(item.get("command") or "").strip()
        for item in merged_command_suggestions
        if isinstance(item.get("command"), str) and str(item.get("command") or "").strip()
    ][:4]
    response_mode = _triage_response_mode(
        request_text=preflight_payload.get("effective_request_text") or request_text,
        constraints=constraints,
        candidate_commands=visible_commands,
    )
    required_manifest_paths = (
        _exact_package_manifest_paths(scoring_command_suggestions, ownership_graph=ownership_graph)
        if not constraints.get("suppress_commands")
        else []
    )
    selected_entries = (
        _select_exact_command_minimal_entries(
            scored_entries,
            limit=selection_limit,
            required_manifest_paths=required_manifest_paths,
            ownership_graph=ownership_graph,
            request_text=preflight_payload.get("effective_request_text") or request_text,
        )
        if response_mode == "command_discovery"
        else []
    )
    if not selected_entries:
        selected_entries = _select_triage_entries(
            scored_entries,
            limit=selection_limit,
            required_paths=required_manifest_paths,
        )
    selected_entries = _refine_owner_focus_entries(
        selected_entries,
        scored_entries=scored_entries,
        request_text=preflight_payload.get("effective_request_text") or request_text,
        response_mode=response_mode,
        ownership_graph=ownership_graph,
    )
    primary_owner_files, supporting_evidence_files, _response_mode = _partition_triage_entries(
        selected_entries,
        request_text=preflight_payload.get("effective_request_text") or request_text,
        constraints=constraints,
        candidate_commands=visible_commands,
        ownership_graph=ownership_graph,
    )
    primary_owner_files = _stabilize_readiness_owner_order(
        primary_owner_files,
        request_text=preflight_payload.get("effective_request_text") or request_text,
        ownership_graph=ownership_graph,
    )
    supporting_evidence_files = _prune_supporting_evidence_files(
        primary_owner_files=primary_owner_files,
        supporting_evidence_files=supporting_evidence_files,
        request_text=preflight_payload.get("effective_request_text") or request_text,
        candidate_commands=visible_commands,
    )
    candidate_files = primary_owner_files[:selection_limit] or [
        str(entry.get("path") or "")
        for entry in selected_entries
        if str(entry.get("path") or "").strip()
    ][:selection_limit]
    ordered_triage_paths = [*primary_owner_files, *supporting_evidence_files][:selection_limit]
    role_lookup = {path: "primary" for path in primary_owner_files}
    role_lookup.update({path: "supporting" for path in supporting_evidence_files})
    owner_candidates = [
        {
            "path": entry.get("path"),
            "summary": entry.get("summary"),
            "score": round(float(entry.get("final_score") or 0.0), 2),
            "why": entry.get("why"),
            "role": role_lookup.get(str(entry.get("path") or "").strip(), "primary"),
        }
        for entry in selected_entries[: max(selection_limit, 4)]
    ]
    why_these_files = build_why_these_files_summary(
        owner_candidates=owner_candidates,
        command_suggestions=[] if constraints.get("suppress_commands") else merged_command_suggestions[:4],
        ownership_graph=ownership_graph,
        request_text=preflight_payload.get("effective_request_text") or request_text,
        route_id=dominant_route_id,
    )
    answer_ready, answer_ready_reason = _runtime_answer_readiness(
        selected_entries=selected_entries,
        scored_entries=scored_entries,
        retained_route_ids=combined_retained_routes,
        route_candidates=[
            dict(item)
            for item in (preflight_payload.get("route_candidates") or [])
            if isinstance(item, dict)
        ],
        dominant_route_id=dominant_route_id,
        request_text=preflight_payload.get("effective_request_text") or request_text,
        constraints=constraints,
        candidate_commands=visible_commands,
        required_manifest_paths=required_manifest_paths,
    )
    dependency_edges = _build_dependency_edges(
        primary_owner_files=primary_owner_files,
        supporting_evidence_files=supporting_evidence_files,
        ownership_graph=ownership_graph,
        workspace_root=resolved_workspace,
    )
    proof_assertions = _build_proof_assertions(
        primary_owner_files=primary_owner_files,
        supporting_evidence_files=supporting_evidence_files,
        dependency_edges=dependency_edges,
        request_text=preflight_payload.get("effective_request_text") or request_text,
        constraints=constraints,
        candidate_commands=visible_commands,
        ownership_graph=ownership_graph,
    )
    selected_tools = [] if answer_ready else list(preflight.get("selected_tools") or [])[:6]
    confidence_reason = (
        _ready_confidence_reason(
            constraints=constraints,
            candidate_commands=visible_commands,
            retained_route_ids=combined_retained_routes,
        )
        if answer_ready
        else preflight.get("confidence_reason")
    )
    follow_up_policy = _follow_up_policy(
        answer_ready=answer_ready,
        retrieval_mode=infer_retrieval_mode(
            str(preflight_payload.get("effective_request_text") or request_text or "repo triage")
        ),
        selected_tools=selected_tools,
        next_read_paths=ordered_triage_paths if answer_ready else (preflight.get("next_read_paths") or [])[:selection_limit],
    )
    why_fragments = [
        str((why_these_files or {}).get("summary") or "").strip().rstrip("."),
        str(answer_ready_reason or "").strip().rstrip("."),
        str(confidence_reason or "").strip().rstrip(".") if not answer_ready else "",
    ]
    why = ". ".join(fragment for fragment in why_fragments if fragment)
    if not why:
        why = "Use the ranked owner files and exact package-owned commands before broad shell scanning."
    why += "."
    payload = {
        "workspace_path": str(resolved_workspace),
        "cache_root": preflight_payload.get("cache_root"),
        "index_status": preflight_payload.get("index_status"),
        "index_refresh_reason": preflight_payload.get("index_refresh_reason"),
        "storage_backend": preflight_payload.get("storage_backend"),
        "storage_summary": preflight_payload.get("storage_summary"),
        "context_store_path": preflight_payload.get("context_store_path"),
        "retrieval": retrieval_policy_payload(
            "triage_repo_request",
            infer_retrieval_mode(
                str(preflight_payload.get("effective_request_text") or request_text or "repo triage")
            ),
        ),
        "semantic_mode": preflight_payload.get("semantic_mode"),
        "semantic_backend_status": preflight_payload.get("semantic_backend_status"),
        "workspace_context": preflight_payload.get("workspace_context") or {},
        "resolved_route": preflight_payload.get("resolved_route"),
        "route_resolution_status": preflight_payload.get("route_resolution_status"),
        "route_candidates": (preflight_payload.get("route_candidates") or [])[:3],
        "effective_request_text": preflight_payload.get("effective_request_text"),
        "request_text_source": preflight.get("request_text_source"),
        "repo_maturity": preflight.get("repo_maturity") or {},
        "candidate_files": candidate_files[:selection_limit],
        "primary_owner_files": primary_owner_files[:selection_limit],
        "supporting_evidence_files": supporting_evidence_files[:selection_limit],
        "candidate_commands": visible_commands[:4],
        "owner_candidates": owner_candidates[: max(selection_limit, 4)],
        "command_suggestions": merged_command_suggestions[:4],
        "why": why,
        "why_these_files": why_these_files,
        "confidence": preflight.get("confidence"),
        "confidence_reason": confidence_reason,
        "next_read_paths": ordered_triage_paths if answer_ready else (preflight.get("next_read_paths") or [])[:selection_limit],
        "do_not_scan_paths": do_not_scan_paths[:6],
        "exact_candidate_commands_only": visible_commands[:4],
        "stop_if_enough": answer_ready,
        "stop_if_enough_guidance": preflight.get("stop_if_enough_guidance"),
        "request_kind_hint": preflight.get("request_kind_hint"),
        "selected_tools": selected_tools,
        "cache_status": preflight.get("cache_status"),
        "proof_assertions": proof_assertions[:TRIAGE_MAX_PROOF_ASSERTIONS],
        "dependency_edges": dependency_edges[:TRIAGE_MAX_DEPENDENCY_EDGES],
        "follow_up_policy": follow_up_policy,
        "applied_constraints": {
            "owner_files_only": bool(constraints.get("owner_files_only")),
            "suppress_commands": bool(constraints.get("suppress_commands")),
            "excluded_families_unless_imported": list(constraints.get("excluded_families_unless_imported") or []),
        },
        "answer_ready_reason": answer_ready_reason,
        "manual_shell_scan_discouraged": True,
        "manual_shell_scan_reason": (
            "Use this triage output first; broad `rg --files`, `find`, tree scans, and generic manifest reads are fallback only."
            if not answer_ready
            else "This triage already resolved the minimal owner slice; answer directly from candidate_files and candidate_commands instead of broad scans or follow-up retrieval."
        ),
        "answer_ready": answer_ready,
        "additional_retrieval_discouraged": answer_ready,
        "additional_retrieval_reason": (
            answer_ready_reason
            if answer_ready
            else "Use `search_context_index` or `show_context_structure` only if the ranked shortlist still leaves the owner ambiguous."
        ),
    }
    _trim_triage_payload(payload)
    _surface_payload_stats("triage_repo_request", payload)
    _record_surface_benchmark(
        "triage_repo_request",
        payload,
        route_id=(preflight_payload.get("resolved_route") or {}).get("route_id"),
        retrieval_mode=(payload.get("retrieval") or {}).get("mode"),
        semantic_mode=preflight_payload.get("semantic_mode"),
        cache_status=preflight.get("cache_status"),
        selected_chunk_count=len(owner_candidates),
        selected_tool_count=len(payload.get("selected_tools") or []),
        refresh_reason=preflight_payload.get("index_refresh_reason"),
    )
    return payload


def show_context_structure(
    workspace: str | Path,
    query_text: str | None = None,
    route_id: str | None = None,
    module_path: str | None = None,
    limit: int | None = 8,
    semantic_mode: str | None = "disabled",
) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    retrieval_mode = infer_retrieval_mode(query_text or "show context structure")
    profile = retrieval_mode_profile(retrieval_mode)
    match_limit = _limit(limit, 8, profile["max_match_limit"])
    normalized_module_path = _normalize_module_path(resolved_workspace, module_path)
    refresh_result, cache_paths, workspace_context, usage, structure_index = load_workspace_context_bundle(
        resolved_workspace,
        query_text=query_text,
        route_id=route_id or "analysis",
        retrieval_mode=retrieval_mode,
        include_structure_index=True,
    )
    structure_index = structure_index or {}
    structure_modules = load_module_summaries(
        cache_paths["context_store"],
        limit=32,
        module_path=normalized_module_path,
    ) or list(structure_index.get("modules") or [])
    selected_modules = _select_modules_for_path(structure_modules, normalized_module_path)
    if not selected_modules and not normalized_module_path:
        selected_modules = structure_modules[:8]
    selected_module_ids = {item.get("module_id") for item in selected_modules if item.get("module_id")}
    selected_hotspots = list(structure_index.get("hotspots") or [])
    if normalized_module_path:
        selected_hotspots = [
            hotspot
            for hotspot in selected_hotspots
            if hotspot.get("module_id") in selected_module_ids or _path_within_module(hotspot.get("path"), normalized_module_path)
        ]

    matches: list[dict[str, Any]] = []
    route, route_candidates, route_status = _resolve_intent_candidates(None, route_id or "analysis", usage=usage)
    semantic_support = _semantic_support(
        cache_paths,
        semantic_mode=semantic_mode,
        analysis_explicit=True,
        query_text=query_text,
        limit=match_limit,
        module_path=normalized_module_path,
    )
    if query_text:
        search_bundle = _search_bundle_matches(
            query_text=query_text,
            route_id=route_id or "analysis",
            workspace_context=workspace_context,
            store_path=cache_paths["context_store"],
            usage=usage,
            match_limit=max(match_limit * 2, profile["max_match_limit"]),
            capability_limit=profile["max_selected_tool_limit"],
            module_path=normalized_module_path,
            chunk_kinds=["file", "symbol", "doc_section"],
        )
        route = search_bundle["route"]
        route_candidates = search_bundle["route_candidates"]
        route_status = search_bundle["route_status"]
        semantic_support = _semantic_support(
            cache_paths,
            semantic_mode=semantic_mode,
            analysis_explicit=True,
            query_text=query_text,
            limit=match_limit,
            module_path=normalized_module_path,
        )
        matches = _dedupe_matches_by_path(
            _merge_semantic_matches(
                list(search_bundle["matches"]),
                semantic_support["matches"],
                limit=match_limit,
            ),
            limit=match_limit,
        )
        if normalized_module_path:
            matches = [match for match in matches if _path_within_module(match.get("path"), normalized_module_path)]
        matches = matches[:match_limit]
        record_search_stats(
            cache_paths,
            query_text=query_text,
            match_count=search_bundle["match_count"],
            route_status=route_status,
            route_id=route["route_id"] if route else None,
        )
    elif normalized_module_path:
        matches = load_module_chunks(
            cache_paths["context_store"],
            module_path=normalized_module_path,
            chunk_kinds=["file", "symbol", "doc_section"],
            limit=match_limit,
        )

    module_payload_items = selected_modules if normalized_module_path else structure_modules[:8]
    payload = {
        "workspace_path": str(resolved_workspace),
        "cache_root": str(cache_paths["root"]),
        "index_status": refresh_result["status"],
        "index_refresh_reason": refresh_result.get("refresh_reason"),
        "storage_backend": refresh_result.get("storage_backend"),
        "storage_summary": refresh_result.get("storage_summary"),
        "context_store_path": refresh_result.get("context_store_path"),
        "query_text": query_text,
        "requested_route_id": route_id,
        "module_path": normalized_module_path,
        "resolved_route": route,
        "route_resolution_status": route_status,
        "route_candidates": route_candidates[:3],
        "retrieval": retrieval_policy_payload("show_context_structure", retrieval_mode),
        "semantic_mode": semantic_support["semantic_mode"],
        "semantic_backend_status": semantic_support["backend_status"],
        "semantic_summary": semantic_support["semantic_summary"],
        "summary": structure_index.get("summary") or {},
        "parser_backends": structure_index.get("parser_backends") or {},
        "incremental_indexing": structure_index.get("incremental_indexing") or {},
        "modules": [_serialize_module(module) for module in module_payload_items[:8]],
        "hotspots": [_serialize_hotspot(item) for item in selected_hotspots[:match_limit]],
        "matches": [_serialize_match(match) for match in matches] if matches else [],
    }
    _trim_structure_payload(payload)
    _surface_payload_stats("show_context_structure", payload)
    _record_surface_benchmark(
        "show_context_structure",
        payload,
        route_id=route["route_id"] if route else None,
        retrieval_mode=retrieval_mode,
        semantic_mode=semantic_support["semantic_mode"],
        selected_chunk_count=len(payload.get("matches") or []),
        selected_tool_count=0,
        refresh_reason=refresh_result.get("refresh_reason"),
    )
    return payload


def _evidence_payload(match: dict[str, Any], evidence_id: str) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "path": match.get("path"),
        "match_kind": match.get("chunk_kind"),
        "anchor_title": match.get("anchor_title"),
        "line_start": match.get("line_start"),
        "line_end": match.get("line_end"),
        "match_source": match.get("match_source", "symbolic"),
        "summary": match.get("summary"),
    }


def _audit_finding(
    finding_id: str,
    *,
    title: str,
    severity: str,
    summary: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "title": title,
        "severity": severity,
        "summary": summary,
        "evidence_refs": evidence_refs[:4],
    }


def _docs_file_truth(path: Path, patterns: list[str]) -> bool:
    if not path.exists():
        return False
    contents = path.read_text(encoding="utf-8", errors="ignore").lower()
    return all(pattern.lower() in contents for pattern in patterns)


def run_analysis_audit(
    workspace: str | Path,
    mode: str,
    query_text: str | None = None,
    module_path: str | None = None,
    limit: int | None = 8,
    semantic_mode: str | None = "auto",
) -> dict[str, Any]:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in ANALYSIS_AUDIT_MODES:
        raise ValueError(f"Unsupported analysis audit mode: {mode}")
    resolved_workspace = Path(workspace).expanduser().resolve()
    retrieval_mode = infer_retrieval_mode(query_text or f"{normalized_mode} analysis audit")
    profile = retrieval_mode_profile(retrieval_mode)
    match_limit = _limit(limit, 8, profile["max_match_limit"])
    normalized_module_path = _normalize_module_path(resolved_workspace, module_path)
    refresh_result, cache_paths, workspace_context, usage, structure_index = load_workspace_context_bundle(
        resolved_workspace,
        query_text=query_text or normalized_mode,
        route_id="analysis",
        retrieval_mode=retrieval_mode,
        include_structure_index=True,
    )
    structure_index = structure_index or {}
    modules = load_module_summaries(
        cache_paths["context_store"],
        limit=16,
        module_path=normalized_module_path,
        preferred_paths=[item.get("path") for item in (structure_index.get("hotspots") or [])[:8] if item.get("path")],
    ) or list(structure_index.get("modules") or [])[:16]
    route, route_candidates, route_status = _resolve_intent_candidates(query_text, "analysis", usage=usage)
    search_bundle = _search_bundle_matches(
        query_text=query_text or normalized_mode,
        route_id="analysis",
        workspace_context=workspace_context,
        store_path=cache_paths["context_store"],
        usage=usage,
        match_limit=match_limit,
        capability_limit=profile["max_selected_tool_limit"],
        module_path=normalized_module_path,
        chunk_kinds=["file", "symbol", "doc_section"],
    )
    semantic_support = _semantic_support(
        cache_paths,
        semantic_mode=semantic_mode,
        analysis_explicit=True,
        query_text=query_text or normalized_mode,
        limit=match_limit,
        module_path=normalized_module_path,
    )
    merged_matches = _dedupe_matches_by_path(
        _merge_semantic_matches(search_bundle["matches"], semantic_support["matches"], limit=match_limit),
        limit=match_limit,
    )
    if normalized_module_path:
        merged_matches = [match for match in merged_matches if _path_within_module(match.get("path"), normalized_module_path)]
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for index, match in enumerate(merged_matches[:6], start=1):
        evidence.append(_evidence_payload(match, f"ev-{index}"))

    if normalized_mode == "architecture":
        top_hotspots = list(structure_index.get("hotspots") or [])[:3]
        if top_hotspots:
            hotspot = top_hotspots[0]
            findings.append(
                _audit_finding(
                    "architecture-hotspot-cluster",
                    title="Cross-module hotspot cluster",
                    severity="warning",
                    summary=(
                        f"Structural indexing highlights `{hotspot.get('path') or hotspot.get('module_id')}` "
                        f"with score {int(hotspot.get('hotspot_score') or 0)} and labels "
                        f"{', '.join(hotspot.get('hotspot_labels') or []) or 'none'}."
                    ),
                    evidence_refs=[item["evidence_id"] for item in evidence[:2]],
                )
            )
        coupled_modules = [
            module
            for module in modules
            if int(module.get("local_fan_in") or 0) >= 1 and int(module.get("local_fan_out") or 0) >= 1
        ]
        if coupled_modules:
            module = coupled_modules[0]
            findings.append(
                _audit_finding(
                    "architecture-coupling-zone",
                    title="Coupling zone worth review",
                    severity="warning",
                    summary=(
                        f"Module `{module.get('path') or '.'}` has local fan-in {int(module.get('local_fan_in') or 0)} "
                        f"and fan-out {int(module.get('local_fan_out') or 0)}, which usually marks a boundary surface."
                    ),
                    evidence_refs=[item["evidence_id"] for item in evidence[1:3]],
                )
            )
        semantic_memory_refs = [match for match in semantic_support["matches"] if match.get("source_kind") in {"project_note", "generated_snapshot"}]
        if semantic_memory_refs:
            findings.append(
                _audit_finding(
                    "architecture-memory-signal",
                    title="Project memory points at a cross-cutting zone",
                    severity="info",
                    summary=(
                        "Pinned notes or generated snapshots align with the current analysis query, "
                        "so the architecture picture already has operator-facing memory attached to it."
                    ),
                    evidence_refs=[item["evidence_id"] for item in evidence[:1]],
                )
            )

    elif normalized_mode == "performance":
        incremental = structure_index.get("incremental_indexing") or {}
        if int((structure_index.get("summary") or {}).get("large_file_count") or 0) > 0:
            findings.append(
                _audit_finding(
                    "performance-large-files",
                    title="Large-file pressure is present",
                    severity="warning",
                    summary=(
                        f"The structural index is tracking {int((structure_index.get('summary') or {}).get('large_file_count') or 0)} "
                        "large files in bounded-read mode. That is a good guardrail, but it also signals likely broad-read pressure."
                    ),
                    evidence_refs=[item["evidence_id"] for item in evidence[:2]],
                )
            )
        if int(incremental.get("rebuilt_file_count") or 0) > 0 or int(incremental.get("bounded_read_count") or 0) > 0:
            findings.append(
                _audit_finding(
                    "performance-incremental-cost",
                    title="Incremental refresh cost is measurable",
                    severity="info",
                    summary=(
                        f"Latest refresh rebuilt {int(incremental.get('rebuilt_file_count') or 0)} files, "
                        f"reused {int(incremental.get('reused_file_count') or 0)} files, and bounded {int(incremental.get('bounded_read_count') or 0)} reads."
                    ),
                    evidence_refs=[item["evidence_id"] for item in evidence[1:3]],
                )
            )
        top_hotspot = next((item for item in (structure_index.get("hotspots") or []) if item.get("target_kind") == "file"), None)
        if top_hotspot:
            findings.append(
                _audit_finding(
                    "performance-hotspot-file",
                    title="One file dominates the hotspot ranking",
                    severity="warning",
                    summary=(
                        f"`{top_hotspot.get('path')}` is the top file hotspot with score {int(top_hotspot.get('hotspot_score') or 0)}. "
                        "That usually maps to broad-read cost, entrypoint pressure, or churn concentration."
                    ),
                    evidence_refs=[item["evidence_id"] for item in evidence[:1]],
                )
            )

    else:
        docs_candidates = [
            resolved_workspace / "README.md",
            resolved_workspace / "plugins" / "agentiux-dev" / "README.md",
        ]
        command_surface_candidates = [
            resolved_workspace / "plugins" / "agentiux-dev" / "references" / "command-surface.md",
            resolved_workspace / "references" / "command-surface.md",
        ]
        dashboard_candidates = [
            resolved_workspace / "plugins" / "agentiux-dev" / "references" / "dashboard.md",
            resolved_workspace / "references" / "dashboard.md",
        ]
        has_readme_truth = any(_docs_file_truth(path, ["analysis"]) for path in docs_candidates if path.exists())
        has_command_truth = any(
            _docs_file_truth(path, ["run analysis audit", "show context structure"]) for path in command_surface_candidates if path.exists()
        )
        has_dashboard_truth = any(_docs_file_truth(path, ["semantic_summary"]) for path in dashboard_candidates if path.exists())
        if not has_command_truth:
            findings.append(
                _audit_finding(
                    "docs-style-command-drift",
                    title="Command surface docs look stale",
                    severity="warning",
                    summary="The operator-facing command surface docs do not mention both `run_analysis_audit` and `show_context_structure`.",
                    evidence_refs=[item["evidence_id"] for item in evidence[:1]],
                )
            )
        if not has_dashboard_truth:
            findings.append(
                _audit_finding(
                    "docs-style-dashboard-drift",
                    title="Dashboard docs miss semantic summary truth",
                    severity="warning",
                    summary="Dashboard-facing documentation does not clearly mention compact semantic summary surfaces.",
                    evidence_refs=[item["evidence_id"] for item in evidence[1:2]],
                )
            )
        if not has_readme_truth:
            findings.append(
                _audit_finding(
                    "docs-style-readme-gap",
                    title="Top-level docs are sparse for analysis flows",
                    severity="info",
                    summary="The nearest README files do not clearly describe the analysis route or semantic audit behavior.",
                    evidence_refs=[item["evidence_id"] for item in evidence[:1]],
                )
            )

    if not findings:
        findings.append(
            _audit_finding(
                f"{normalized_mode}-no-gaps",
                title="No major issues detected",
                severity="info",
                summary="This audit mode did not find a deterministic warning from the current structural and semantic signals.",
                evidence_refs=[item["evidence_id"] for item in evidence[:2]],
            )
        )

    memory_snapshot_draft = {
        "schema_version": 1,
        "snapshot_id": short_hash(
            f"{resolved_workspace}:{normalized_mode}:{normalized_module_path or 'workspace'}:{query_text or ''}",
            length=16,
        ),
        "title": f"{normalized_mode.replace('_', ' ').title()} audit snapshot",
        "status": "draft",
        "source_audit_mode": normalized_mode,
        "source_query_text": query_text,
        "source_module_path": normalized_module_path,
        "confidence": round(min(0.45 + (0.1 * len(findings)) + (0.05 * len(semantic_support['matches'])), 0.95), 2),
        "expires_in_days": 14,
        "body_markdown": "\n".join(
            [
                f"# {normalized_mode.replace('_', ' ').title()} audit",
                "",
                *(f"- {finding['title']}: {finding['summary']}" for finding in findings[:6]),
            ]
        ),
        "provenance": {
            "workspace_path": str(resolved_workspace),
            "route_id": "analysis",
            "index_refresh_reason": refresh_result.get("refresh_reason"),
            "evidence_count": len(evidence),
            "semantic_match_count": len(semantic_support["matches"]),
        },
    }
    payload = {
        "workspace_path": str(resolved_workspace),
        "mode": normalized_mode,
        "query_text": query_text,
        "module_path": normalized_module_path,
        "resolved_route": route or search_bundle["route"],
        "route_resolution_status": route_status,
        "route_candidates": route_candidates[:3],
        "retrieval": retrieval_policy_payload("run_analysis_audit", retrieval_mode),
        "index_status": refresh_result["status"],
        "index_refresh_reason": refresh_result.get("refresh_reason"),
        "storage_backend": refresh_result.get("storage_backend"),
        "storage_summary": refresh_result.get("storage_summary"),
        "context_store_path": refresh_result.get("context_store_path"),
        "semantic_mode": semantic_support["semantic_mode"],
        "semantic_backend_status": semantic_support["backend_status"],
        "semantic_summary": semantic_support["semantic_summary"],
        "findings": findings,
        "evidence": evidence,
        "semantic_matches": [_serialize_match(_semantic_match_record(match)) for match in semantic_support["matches"]],
        "memory_snapshot_draft": memory_snapshot_draft,
        "recommended_follow_ups": [
            "Use `show_context_structure` to inspect the highlighted module or hotspot in compact form.",
            "Use `search_context_index` with `semanticMode=enabled` only if you want semantic shortlist expansion on the analysis route.",
            "Persist the draft snapshot only through workflow execution or closeout code paths after review.",
        ],
    }
    _trim_analysis_audit_payload(payload)
    _surface_payload_stats("run_analysis_audit", payload)
    _record_surface_benchmark(
        "run_analysis_audit",
        payload,
        route_id=(route or search_bundle["route"] or {}).get("route_id"),
        retrieval_mode=retrieval_mode,
        semantic_mode=semantic_support["semantic_mode"],
        selected_chunk_count=len(payload.get("evidence") or []),
        selected_tool_count=0,
        refresh_reason=refresh_result.get("refresh_reason"),
    )
    return payload
