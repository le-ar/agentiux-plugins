from __future__ import annotations

from pathlib import Path
from typing import Any

from agentiux_dev_lib import now_iso, plugin_info
from agentiux_dev_retrieval import (
    SURFACE_PAYLOAD_CEILINGS,
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
    load_jsonl,
    load_workspace_context_bundle,
    plugin_catalog_root,
    record_context_pack_stats,
    record_search_stats,
    record_usage,
    refresh_context_index,
    route_index,
    write_jsonl,
)
from agentiux_dev_context_semantic import (
    ANALYSIS_AUDIT_MODES,
    load_semantic_manifest,
    search_semantic_units,
    semantic_mode_enabled,
    semantic_summary_from_manifest,
)


ROUTE_SCORE_MIN = 8
ROUTE_SCORE_AMBIGUOUS_DELTA = 3
TOKEN_SYNONYMS = {
    "analysis": ["hotspot", "incremental", "module", "section", "structural", "symbol"],
    "a11y": ["accessibility", "semantic"],
    "accessibility": ["a11y", "semantic"],
    "baseline": ["snapshot", "verification", "visual"],
    "branch": ["checkout", "worktree"],
    "catalog": ["index", "mcp", "tools"],
    "checkout": ["branch", "worktree"],
    "cockpit": ["dashboard"],
    "commit": ["git", "message"],
    "context": ["catalog", "workspace"],
    "dashboard": ["cockpit", "gui", "release"],
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
    "plugin": ["catalog", "dashboard", "mcp", "self", "host"],
    "pr": ["pull", "request"],
    "pull": ["pr"],
    "release": ["dashboard", "readiness", "ship", "smoke"],
    "semantic": ["a11y", "helper", "verification", "visual"],
    "section": ["analysis", "doc", "module", "structural"],
    "ship": ["readiness", "release", "smoke"],
    "smoke": ["readiness", "release", "verification"],
    "structural": ["analysis", "hotspot", "module", "symbol"],
    "symbol": ["analysis", "module", "section", "structural"],
    "task": ["stage", "workstream", "workspace"],
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
        "surface": "show_workspace_context_pack",
        "description": "Load the current workspace context pack for the request if repo context is needed.",
        "tools": ["show_workspace_context_pack"],
    },
    {
        "step": 5,
        "surface": "search_context_index",
        "description": "Search the global context index for relevant chunks instead of broad manual scans.",
        "tools": ["search_context_index"],
    },
    {
        "step": 6,
        "surface": "show_context_structure",
        "description": "Inspect compact structural module, symbol, doc-section, hotspot, and incremental index summaries.",
        "tools": ["show_context_structure"],
    },
    {
        "step": 7,
        "surface": "run_analysis_audit",
        "description": "Run a read-only architecture, performance, or docs-style audit with optional semantic shortlist expansion.",
        "tools": ["run_analysis_audit"],
    },
    {
        "step": 8,
        "surface": "targeted_file_reads",
        "description": "Open only the specific files referenced by the selected route and search hits.",
        "tools": [],
    },
    {
        "step": 9,
        "surface": "manual_exploration",
        "description": "Use broad rg/manual exploration only if the earlier layers are insufficient.",
        "tools": [],
    },
]


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
    return payload


def _trim_context_pack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = SURFACE_PAYLOAD_CEILINGS["show_workspace_context_pack"]
    context_pack = payload.get("context_pack") or {}
    while payload_size_bytes(payload) > ceiling and context_pack.get("selected_tools"):
        context_pack["selected_tools"] = context_pack["selected_tools"][:-1]
    while payload_size_bytes(payload) > ceiling and context_pack.get("selected_chunks"):
        context_pack["selected_chunks"] = context_pack["selected_chunks"][:-1]
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
        confidence = min(score / 24, 1.0)
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
    return {
        "plugin": plugin_info(),
        "catalog_root": str(plugin_catalog_root()),
        "filter": {
            "kind": kind,
            "route_id": route_id,
            "query_text": query_text,
            "limit": _limit(limit, 20, 100),
        },
        "catalog_counts": {
            "skills": len([entry for entry in all_entries if entry["kind"] == "skill"]),
            "mcp_tools": len([entry for entry in all_entries if entry["kind"] == "mcp_tool"]),
            "scripts": len([entry for entry in all_entries if entry["kind"] == "script"]),
            "references": len([entry for entry in all_entries if entry["kind"] == "reference"]),
        },
        "total_matches": len(scored_entries),
        "entries": selected,
    }


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


def _search_bundle_matches(
    *,
    query_text: str,
    route_id: str | None,
    workspace_context: dict[str, Any],
    modules: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    usage: dict[str, Any],
    match_limit: int,
    capability_limit: int,
) -> dict[str, Any]:
    route, route_candidates, route_status = _resolve_intent_candidates(query_text, route_id, usage=usage)
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
    scored_chunks = []
    for chunk in chunks:
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
    capability_catalog = show_capability_catalog(
        route_id=route["route_id"] if route else None,
        query_text=query_text,
        limit=capability_limit,
    )
    return {
        "route": route,
        "route_candidates": route_candidates[:3],
        "route_status": route_status,
        "matches": scored_chunks[:match_limit],
        "match_count": len(scored_chunks),
        "recommended_capabilities": capability_catalog["entries"][:capability_limit],
        "modules": modules[:8],
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
    refresh_result, cache_paths, workspace_context, modules, chunks, usage, _structure_index = load_workspace_context_bundle(
        workspace,
        query_text=query_text,
        route_id=route_id,
        retrieval_mode=retrieval_mode,
    )
    search_bundle = _search_bundle_matches(
        query_text=query_text,
        route_id=route_id,
        workspace_context=workspace_context,
        modules=modules,
        chunks=chunks,
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
    merged_matches = _merge_semantic_matches(
        search_bundle["matches"],
        semantic_support["matches"],
        limit=match_limit,
    )
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
    return payload


def _semantic_cache_key(
    query_text: str,
    catalog_digest: str,
    route_id: str | None,
    semantic_mode: str,
    *,
    semantic_enabled: bool,
) -> str:
    return short_hash(
        f"{catalog_digest}:{route_id or 'none'}:{semantic_mode}:{int(semantic_enabled)}:{' '.join(tokenize_text(query_text))}",
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


def show_workspace_context_pack(
    workspace: str | Path,
    request_text: str | None = None,
    route_id: str | None = None,
    limit: int | None = 6,
    force_refresh: bool = False,
    semantic_mode: str | None = "disabled",
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
    refresh_result, cache_paths, workspace_context, modules, chunks, usage, _structure_index = load_workspace_context_bundle(
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
        )
        semantic_cache = load_jsonl(cache_paths["semantic_cache"])
        chunk_hashes = {chunk["path"]: chunk["hash"] for chunk in chunks}
        cached = next(
            (
                entry
                for entry in semantic_cache
                if entry.get("query_fingerprint") == cache_key
                and entry.get("schema_version", 1) == 5
                and entry.get("catalog_digest") == workspace_context.get("catalog_digest")
                and entry.get("semantic_mode") == semantic_support["semantic_mode"]
                and bool(entry.get("semantic_enabled")) == bool(semantic_support["enabled"])
                and (
                    semantic_fingerprint is None
                    or entry.get("semantic_fingerprint") == semantic_fingerprint
                )
                and isinstance(entry.get("source_hashes"), dict)
                and (
                    not entry.get("source_hashes")
                    or all(chunk_hashes.get(path) == expected_hash for path, expected_hash in entry["source_hashes"].items())
                )
            ),
            None,
        )
        if cached:
            record_context_pack_stats(
                cache_paths,
                cache_status="hit",
                route_status=route_status,
                route_id=route["route_id"] if route else None,
                selected_tool_count=len(cached.get("selected_tools", [])),
            )
            payload = {
                "workspace_path": str(resolved_workspace),
                "cache_root": str(cache_paths["root"]),
                "cache_status": "hit",
                "index_status": refresh_result["status"],
                "index_refresh_reason": refresh_result.get("refresh_reason"),
                "retrieval": retrieval_policy_payload("show_workspace_context_pack", retrieval_mode),
                "semantic_mode": semantic_support["semantic_mode"],
                "semantic_backend_status": semantic_support["backend_status"],
                "workspace_context": compact_context,
                "resolved_route": route,
                "route_resolution_status": route_status,
                "route_candidates": route_candidates[:3],
                "context_pack": cached,
            }
            _trim_context_pack_payload(payload)
            _surface_payload_stats("show_workspace_context_pack", payload)
            return payload

        search_bundle = _search_bundle_matches(
            query_text=request_text,
            route_id=route["route_id"] if route else None,
            workspace_context=workspace_context,
            modules=modules,
            chunks=chunks,
            usage=usage,
            match_limit=chunk_limit,
            capability_limit=profile["max_selected_tool_limit"],
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
        selected_chunks = _pinned_project_memory_selected_chunks(chunks)
        selected_chunks.extend(
            [
                {
                    "chunk_id": match["chunk_id"],
                    "path": match["path"],
                    "summary": match["summary"],
                    "score": match["score"],
                    "match_source": "symbolic",
                }
                for match in search_bundle["matches"]
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
        source_hashes = {
            match["path"]: next(chunk["hash"] for chunk in chunks if chunk["path"] == match["path"])
            for match in selected_chunks
            if any(chunk["path"] == match["path"] for chunk in chunks)
        }
        confidence = round(min(sum(match["score"] for match in selected_chunks[:3]) / 36, 1.0), 2)
        context_pack = {
            "schema_version": 5,
            "query_fingerprint": cache_key,
            "workspace_fingerprint": workspace_context.get("workspace_fingerprint"),
            "catalog_digest": workspace_context.get("catalog_digest"),
            "route_id": route["route_id"] if route else None,
            "semantic_mode": semantic_support["semantic_mode"],
            "semantic_enabled": bool(semantic_support["enabled"]),
            "semantic_fingerprint": semantic_fingerprint,
            "selected_chunks": selected_chunks,
            "selected_tools": selected_tools,
            "confidence": confidence,
            "source_hashes": source_hashes,
            "created_at": now_iso(),
        }
        semantic_cache = [entry for entry in semantic_cache if entry.get("query_fingerprint") != cache_key]
        semantic_cache.append(context_pack)
        semantic_cache.sort(key=lambda item: item.get("created_at", ""))
        write_jsonl(cache_paths["semantic_cache"], semantic_cache[-40:])
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
        _surface_payload_stats("show_workspace_context_pack", payload)
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
        "retrieval": retrieval_policy_payload("show_workspace_context_pack", retrieval_mode),
        "semantic_mode": semantic_support["semantic_mode"],
        "semantic_backend_status": semantic_support["backend_status"],
        "workspace_context": compact_context,
        "resolved_route": route,
        "route_resolution_status": route_status,
        "route_candidates": route_candidates[:3],
        "modules": modules[:8],
        "chunk_count": len(chunks),
        "pinned_project_memory": _pinned_project_memory_selected_chunks(chunks)[: profile["max_selected_chunk_limit"]],
        "retrieval_ladder": RETRIEVAL_LADDER,
    }
    _surface_payload_stats("show_workspace_context_pack", payload)
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
    refresh_result, cache_paths, workspace_context, modules, chunks, usage, structure_index = load_workspace_context_bundle(
        resolved_workspace,
        query_text=query_text,
        route_id=route_id or "analysis",
        retrieval_mode=retrieval_mode,
    )
    structure_modules = list(structure_index.get("modules") or modules)
    selected_modules = _select_modules_for_path(structure_modules, normalized_module_path)
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
            modules=structure_modules,
            chunks=chunks,
            usage=usage,
            match_limit=max(match_limit * 2, profile["max_match_limit"]),
            capability_limit=profile["max_selected_tool_limit"],
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
        matches = _merge_semantic_matches(
            list(search_bundle["matches"]),
            semantic_support["matches"],
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
        filtered_chunks = [
            chunk
            for chunk in chunks
            if _path_within_module(chunk.get("path"), normalized_module_path)
            and chunk.get("chunk_kind") in {"file", "symbol", "doc_section"}
        ]
        filtered_chunks.sort(
            key=lambda item: (
                item.get("chunk_kind") != "file",
                str(item.get("path") or ""),
                int(item.get("line_start") or 0),
                str(item.get("anchor_title") or ""),
            )
        )
        matches = filtered_chunks[:match_limit]

    module_payload_items = selected_modules if normalized_module_path else structure_modules[:8]
    payload = {
        "workspace_path": str(resolved_workspace),
        "cache_root": str(cache_paths["root"]),
        "index_status": refresh_result["status"],
        "index_refresh_reason": refresh_result.get("refresh_reason"),
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
    refresh_result, cache_paths, workspace_context, modules, chunks, usage, structure_index = load_workspace_context_bundle(
        resolved_workspace,
        query_text=query_text or normalized_mode,
        route_id="analysis",
        retrieval_mode=retrieval_mode,
    )
    route, route_candidates, route_status = _resolve_intent_candidates(query_text, "analysis", usage=usage)
    search_bundle = _search_bundle_matches(
        query_text=query_text or normalized_mode,
        route_id="analysis",
        workspace_context=workspace_context,
        modules=modules,
        chunks=chunks,
        usage=usage,
        match_limit=match_limit,
        capability_limit=profile["max_selected_tool_limit"],
    )
    semantic_support = _semantic_support(
        cache_paths,
        semantic_mode=semantic_mode,
        analysis_explicit=True,
        query_text=query_text or normalized_mode,
        limit=match_limit,
        module_path=normalized_module_path,
    )
    merged_matches = _merge_semantic_matches(search_bundle["matches"], semantic_support["matches"], limit=match_limit)
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
    return payload
