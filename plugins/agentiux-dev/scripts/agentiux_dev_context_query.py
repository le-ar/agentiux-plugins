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


ROUTE_SCORE_MIN = 8
ROUTE_SCORE_AMBIGUOUS_DELTA = 3
TOKEN_SYNONYMS = {
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
    "index": ["catalog", "context"],
    "mcp": ["catalog", "plugin", "tool", "tools"],
    "memory": ["note", "project"],
    "plugin": ["catalog", "dashboard", "mcp", "self", "host"],
    "pr": ["pull", "request"],
    "pull": ["pr"],
    "release": ["dashboard", "readiness", "ship", "smoke"],
    "semantic": ["a11y", "helper", "verification", "visual"],
    "ship": ["readiness", "release", "smoke"],
    "smoke": ["readiness", "release", "verification"],
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
        "surface": "targeted_file_reads",
        "description": "Open only the specific files referenced by the selected route and search hits.",
        "tools": [],
    },
    {
        "step": 7,
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
) -> dict[str, Any]:
    if not tokenize_text(query_text):
        raise ValueError("query_text is required")
    retrieval_mode = infer_retrieval_mode(query_text)
    profile = retrieval_mode_profile(retrieval_mode)
    match_limit = _limit(limit, 8, profile["max_match_limit"])
    refresh_result, cache_paths, workspace_context, modules, chunks, usage = load_workspace_context_bundle(
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
        "route_candidates": search_bundle["route_candidates"],
        "workspace_context": compact_workspace_context(workspace_context),
        "modules": search_bundle["modules"],
        "matches": search_bundle["matches"],
        "recommended_capabilities": search_bundle["recommended_capabilities"],
    }
    _trim_search_payload(payload)
    _surface_payload_stats("search_context_index", payload)
    return payload


def _semantic_cache_key(query_text: str, catalog_digest: str, route_id: str | None) -> str:
    return short_hash(f"{catalog_digest}:{route_id or 'none'}:{' '.join(tokenize_text(query_text))}", length=16)


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
    refresh_result, cache_paths, workspace_context, modules, chunks, usage = load_workspace_context_bundle(
        resolved_workspace,
        query_text=request_text,
        route_id=route_id,
        retrieval_mode=retrieval_mode,
    )
    route, route_candidates, route_status = _resolve_intent_candidates(request_text, route_id, usage=usage)
    compact_context = compact_workspace_context(workspace_context)

    if request_text:
        cache_key = _semantic_cache_key(request_text, workspace_context.get("catalog_digest", ""), route["route_id"] if route else None)
        semantic_cache = load_jsonl(cache_paths["semantic_cache"])
        chunk_hashes = {chunk["path"]: chunk["hash"] for chunk in chunks}
        cached = next(
            (
                entry
                for entry in semantic_cache
                if entry.get("query_fingerprint") == cache_key
                and entry.get("schema_version", 1) == 3
                and entry.get("catalog_digest") == workspace_context.get("catalog_digest")
                and isinstance(entry.get("source_hashes"), dict)
                and entry.get("source_hashes")
                and all(chunk_hashes.get(path) == expected_hash for path, expected_hash in entry["source_hashes"].items())
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
                {"chunk_id": match["chunk_id"], "path": match["path"], "summary": match["summary"], "score": match["score"]}
                for match in search_bundle["matches"]
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
        }
        confidence = round(min(sum(match["score"] for match in selected_chunks[:3]) / 36, 1.0), 2)
        context_pack = {
            "schema_version": 3,
            "query_fingerprint": cache_key,
            "workspace_fingerprint": workspace_context.get("workspace_fingerprint"),
            "catalog_digest": workspace_context.get("catalog_digest"),
            "route_id": route["route_id"] if route else None,
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
