from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from time import perf_counter, time_ns
from typing import Any

from agentiux_dev_lib import (
    PLUGIN_VERSION,
    current_task,
    current_workstream,
    detect_workspace,
    inspect_git_state,
    now_iso,
    plugin_info,
    plugin_root,
    read_workspace_state,
    state_root,
    workspace_paths,
)
from agentiux_dev_verification import read_verification_recipes


CATALOG_FILENAMES = ("skills", "mcp_tools", "scripts", "references", "intent_routes")
CONTEXT_CACHE_SCHEMA_VERSION = 3
CONTEXT_INDEX_MANIFEST_SCHEMA_VERSION = 3
CONTEXT_ROUTE_PROFILE_VERSION = 1
SEMANTIC_CACHE_ENTRY_SCHEMA_VERSION = 3
CONTEXT_INDEX_EXCLUDED_DIRS = {
    ".agentiux",
    ".git",
    ".gradle",
    ".idea",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    ".verification",
    ".yarn",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
}
CONTEXT_INDEX_SUFFIXES = {
    ".cjs",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".mjs",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
CONTEXT_INDEX_FILENAMES = {
    ".mcp.json",
    "Cargo.toml",
    "Dockerfile",
    "LICENSE",
    "Podfile",
    "README",
    "README.md",
    "app.json",
    "package.json",
    "plugin.json",
    "pyproject.toml",
    "settings.gradle",
    "settings.gradle.kts",
    "tsconfig.json",
}
CONTEXT_INDEX_PRIORITY_FILENAMES = {
    ".mcp.json",
    "Cargo.toml",
    "README.md",
    "app.json",
    "package.json",
    "plugin.json",
    "pyproject.toml",
    "tsconfig.json",
}
CONTEXT_INDEX_PRIORITY_DIRS = {
    "android",
    "app",
    "apps",
    "libs",
    "packages",
    "plugins",
    "references",
    "scripts",
    "skills",
    "src",
}
CONTEXT_INDEX_MAX_FILE_BYTES = 160_000
CONTEXT_INDEX_MAX_FILES = 240
ROUTE_SCORE_MIN = 8
ROUTE_SCORE_AMBIGUOUS_DELTA = 3
CAPABILITY_SCORE_MIN = 5
MODULE_PARENT_DIRS = ("apps", "packages", "libs", "services", "plugins", "crates")
PACKAGE_MANAGER_FILES = {
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lockb": "bun",
    "Cargo.lock": "cargo",
    "poetry.lock": "poetry",
    "uv.lock": "uv",
    "requirements.txt": "pip",
}
TOKEN_SYNONYMS = {
    "a11y": ["accessibility", "semantic"],
    "accessibility": ["a11y", "semantic"],
    "baseline": ["snapshot", "verification", "visual"],
    "branch": ["checkout", "worktree"],
    "catalog": ["index", "manifest", "mcp", "tools"],
    "checkout": ["branch", "worktree"],
    "commit": ["git", "message"],
    "dashboard": ["gui", "release", "snapshot"],
    "design": ["brief", "handoff", "ui", "ux", "visual"],
    "git": ["branch", "commit", "worktree"],
    "gui": ["dashboard"],
    "handoff": ["brief", "design"],
    "helper": ["bundle", "semantic", "verification"],
    "index": ["catalog", "context"],
    "mcp": ["catalog", "plugin", "tool", "tools"],
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
ROUTE_PROFILES: dict[str, dict[str, Any]] = {
    "design": {
        "priority_dirs": {"design", "references", "src"},
        "priority_files": {"README.md"},
        "path_tokens": {"brief", "design", "handoff", "reference", "ui", "ux", "visual"},
    },
    "git": {
        "priority_dirs": {"scripts", "skills"},
        "priority_files": {"README.md"},
        "path_tokens": {"branch", "commit", "git", "pr", "worktree"},
    },
    "plugin-dev": {
        "priority_dirs": {"catalogs", "references", "scripts", "skills"},
        "priority_files": {".mcp.json", "README.md", "plugin.json"},
        "path_tokens": {"catalog", "dashboard", "mcp", "plugin", "release"},
    },
    "release": {
        "priority_dirs": {"references", "scripts", "skills"},
        "priority_files": {"README.md"},
        "path_tokens": {"dashboard", "readiness", "release", "smoke"},
    },
    "verification": {
        "priority_dirs": {"bundles", "references", "scripts"},
        "priority_files": {"README.md"},
        "path_tokens": {"baseline", "helper", "semantic", "verification", "visual"},
    },
    "workstream": {
        "priority_dirs": {"references", "scripts", "skills"},
        "priority_files": {"README.md"},
        "path_tokens": {"stage", "task", "workflow", "workstream", "workspace"},
    },
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


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _tokenize(value: str | None) -> list[str]:
    normalized = _normalize_text(value).lower()
    if not normalized:
        return []
    return sorted({token for token in re.split(r"[^a-z0-9]+", normalized) if len(token) >= 2})


def _short_hash(value: str, length: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return json.loads(json.dumps(default))
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{time_ns()}.tmp"
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def _plugin_catalog_root() -> Path:
    return plugin_root() / "catalogs"


def _catalog_path(name: str) -> Path:
    if name not in CATALOG_FILENAMES:
        raise ValueError(f"Unknown catalog: {name}")
    return _plugin_catalog_root() / f"{name}.json"


def _load_catalog(name: str) -> dict[str, Any]:
    payload = _load_json(_catalog_path(name), default={})
    if not payload or not isinstance(payload.get("entries"), list):
        raise ValueError(f"Catalog is missing or invalid: {_catalog_path(name)}")
    return payload


def _all_capability_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in ("skills", "mcp_tools", "scripts", "references"):
        entries.extend(_load_catalog(name)["entries"])
    return entries


def _intent_routes() -> list[dict[str, Any]]:
    return _load_catalog("intent_routes")["entries"]


def _capability_cache_root(workspace: str | Path) -> Path:
    paths = workspace_paths(workspace)
    return state_root() / "cache" / "context" / f"{paths['workspace_slug']}--{paths['workspace_hash']}"


def _context_cache_paths(workspace: str | Path) -> dict[str, Path]:
    root = _capability_cache_root(workspace)
    return {
        "root": root,
        "manifest": root / "index_manifest.json",
        "workspace_context": root / "workspace_context.json",
        "module_map": root / "module_map.json",
        "chunk_summaries": root / "chunk_summaries.jsonl",
        "semantic_cache": root / "semantic_cache.jsonl",
        "usage": root / "usage.json",
    }


def _route_index() -> dict[str, dict[str, Any]]:
    return {route["route_id"]: route for route in _intent_routes()}


def _catalog_digest() -> str:
    digest = hashlib.sha1()
    for name in CATALOG_FILENAMES:
        path = _catalog_path(name)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _expanded_token_set(values: list[str]) -> set[str]:
    expanded: set[str] = set()
    for value in values:
        normalized = value.strip().lower()
        if not normalized:
            continue
        expanded.add(normalized)
        for synonym in TOKEN_SYNONYMS.get(normalized, []):
            expanded.add(synonym)
    return expanded


def _score_token_match(query_tokens: list[str], candidate_tokens: list[str], weight: int) -> tuple[int, list[str]]:
    query_set = _expanded_token_set(query_tokens)
    candidate_set = _expanded_token_set(candidate_tokens)
    if not query_set or not candidate_set:
        return 0, []
    matches = sorted(query_set.intersection(candidate_set))
    return len(matches) * weight, matches


def _git_dirty_digest(git_state: dict[str, Any]) -> str:
    changed_paths = sorted(entry["path"] for entry in git_state.get("changed_files", []))
    return _short_hash("|".join(changed_paths), length=10)


def _indexed_file_snapshot(workspace: Path, files: list[Any]) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for file_path in files:
        if isinstance(file_path, Path):
            stat = file_path.stat()
            snapshot.append(
                {
                    "path": str(file_path.relative_to(workspace)),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
            continue
        if isinstance(file_path, dict):
            snapshot.append(
                {
                    "path": str(file_path["path"]),
                    "size": int(file_path["size"]),
                    "mtime_ns": int(file_path["mtime_ns"]),
                }
            )
    return snapshot


def _synthetic_mtime_ns(*values: Any) -> int:
    return int(hashlib.sha1("|".join(str(value or "") for value in values).encode("utf-8")).hexdigest()[:12], 16)


def _project_note_context_text(note: dict[str, Any]) -> str:
    tags = ", ".join(note.get("tags") or []) or "none"
    return "\n".join(
        [
            f"# {note.get('title') or note.get('note_id')}",
            "",
            f"Note ID: {note.get('note_id')}",
            f"Status: {note.get('status')}",
            f"Pin: {note.get('pin_state')}",
            f"Source: {note.get('source')}",
            f"Tags: {tags}",
            "",
            str(note.get("body_markdown") or "").strip(),
        ]
    ).strip()


def _project_note_candidates(workspace: Path) -> list[dict[str, Any]]:
    from agentiux_dev_memory import list_project_notes, get_project_note

    try:
        note_items = list_project_notes(workspace).get("items", [])
    except FileNotFoundError:
        return []
    candidates: list[dict[str, Any]] = []
    for item in note_items:
        note = get_project_note(workspace, item["note_id"])
        text = _project_note_context_text(note)
        candidates.append(
            {
                "path": f"external/project-memory/{note['note_id']}.md",
                "size": len(text.encode("utf-8")),
                "mtime_ns": _synthetic_mtime_ns(note.get("updated_at"), note.get("latest_revision_id"), note.get("status"), note.get("pin_state")),
                "text": text,
                "note": note,
            }
        )
    candidates.sort(key=lambda item: item["path"])
    return candidates


def _build_project_note_chunk_record(candidate: dict[str, Any]) -> dict[str, Any]:
    note = copy.deepcopy(candidate.get("note") or {})
    text = str(candidate.get("text") or "")
    file_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
    tags = [
        "project-memory",
        f"note-status:{note.get('status') or 'active'}",
        f"pin-state:{note.get('pin_state') or 'normal'}",
        *[str(tag) for tag in (note.get("tags") or [])],
    ]
    if note.get("pin_state") == "pinned":
        tags.append("pinned")
    if note.get("status") == "archived":
        tags.append("archived")
    summary = _normalize_text(
        f"Project memory note {note.get('title') or note.get('note_id')}. "
        f"Status: {note.get('status') or 'active'}. Pin: {note.get('pin_state') or 'normal'}. "
        f"Tags: {', '.join(note.get('tags') or []) or 'none'}."
    )
    return {
        "chunk_id": _short_hash(f"{candidate['path']}:{file_hash}", length=16),
        "path": str(candidate["path"]),
        "symbols": [note.get("title") or note.get("note_id")],
        "tags": tags,
        "summary": summary,
        "hash": file_hash,
        "dependencies": [],
        "route_hints": _infer_route_hints(str(candidate["path"]), tags, summary),
        "source_kind": "project_memory",
        "note_id": note.get("note_id"),
        "note_status": note.get("status"),
        "pin_state": note.get("pin_state"),
    }


def _candidate_paths_digest(snapshot: list[dict[str, Any]]) -> str:
    return _short_hash("|".join(item["path"] for item in snapshot), length=10)


def _usage_stats_payload() -> dict[str, Any]:
    return {
        "refresh_count": 0,
        "fresh_hit_count": 0,
        "search_count": 0,
        "context_pack_hit_count": 0,
        "context_pack_miss_count": 0,
        "last_refresh_candidate_file_count": 0,
        "last_refresh_rebuilt_chunk_count": 0,
        "last_refresh_reused_chunk_count": 0,
        "last_refresh_duration_ms": 0,
        "last_refresh_status": None,
        "last_refresh_reason": None,
        "last_refresh_stale_reasons": [],
        "last_search_match_count": 0,
        "last_search_query": None,
        "last_search_route_status": None,
        "last_search_route_id": None,
        "last_context_pack_status": None,
        "last_context_pack_route_status": None,
        "last_context_pack_route_id": None,
        "last_context_pack_selected_tool_count": 0,
        "refresh_reason_counts": {},
        "route_resolution_counts": {},
    }


def _score_tokens(query_tokens: list[str], candidate_tokens: list[str], weight: int) -> int:
    if not query_tokens or not candidate_tokens:
        return 0
    return len(set(query_tokens).intersection(candidate_tokens)) * weight


def _limit(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(value, maximum))


def _catalog_entry_score(
    entry: dict[str, Any],
    query_tokens: list[str],
    route_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    score = 0
    why: dict[str, Any] = {}
    title_score, title_matches = _score_token_match(query_tokens, _tokenize(entry.get("title")), 5)
    if title_matches:
        why["matched_title"] = title_matches
    score += title_score
    tag_score, tag_matches = _score_token_match(query_tokens, entry.get("tags", []), 6)
    if tag_matches:
        why["matched_tags"] = tag_matches
    score += tag_score
    summary_score, summary_matches = _score_token_match(query_tokens, _tokenize(entry.get("summary")), 3)
    if summary_matches:
        why["matched_summary"] = summary_matches
    score += summary_score
    path_score, path_matches = _score_token_match(query_tokens, _tokenize(entry.get("path")), 4)
    if path_matches:
        why["matched_path"] = path_matches
    score += path_score
    trigger_score, trigger_matches = _score_token_match(query_tokens, entry.get("triggers", []), 5)
    if trigger_matches:
        why["matched_triggers"] = trigger_matches
    score += trigger_score
    if route_id and route_id in entry.get("related_routes", []):
        score += 6
        why["route_match"] = route_id
    return score, why


def _default_usage_payload() -> dict[str, Any]:
    payload = {
        "recent_route_ids": [],
        "recent_tool_ids": [],
        "updated_at": None,
    }
    payload.update(_usage_stats_payload())
    return payload


def _load_usage(paths: dict[str, Path]) -> dict[str, Any]:
    payload = _load_json(paths["usage"], default={})
    merged = _default_usage_payload()
    if isinstance(payload, dict):
        merged.update(payload)
    for field in ("refresh_reason_counts", "route_resolution_counts"):
        if not isinstance(merged.get(field), dict):
            merged[field] = {}
    for field in ("recent_route_ids", "recent_tool_ids", "last_refresh_stale_reasons"):
        if not isinstance(merged.get(field), list):
            merged[field] = []
    return merged


def _write_usage_payload(paths: dict[str, Path], usage: dict[str, Any]) -> dict[str, Any]:
    usage["updated_at"] = now_iso()
    _write_json(paths["usage"], usage)
    return usage


def _bump_usage_counter(usage: dict[str, Any], field: str, key: str) -> None:
    counter = usage.setdefault(field, {})
    counter[key] = int(counter.get(key, 0)) + 1


def _record_usage(paths: dict[str, Path], route_id: str | None, tool_ids: list[str]) -> dict[str, Any]:
    usage = _load_usage(paths)
    if route_id:
        usage["recent_route_ids"] = [route_id, *[item for item in usage.get("recent_route_ids", []) if item != route_id]][:6]
    for tool_id in tool_ids:
        usage["recent_tool_ids"] = [tool_id, *[item for item in usage.get("recent_tool_ids", []) if item != tool_id]][:12]
    return _write_usage_payload(paths, usage)


def _record_refresh_stats(
    paths: dict[str, Path],
    *,
    status: str,
    duration_ms: int,
    reason: str,
    stale_reasons: list[str],
    candidate_file_count: int,
    rebuilt_chunk_count: int,
    reused_chunk_count: int,
) -> dict[str, Any]:
    usage = _load_usage(paths)
    usage["refresh_count"] = int(usage.get("refresh_count", 0)) + 1
    if status == "fresh":
        usage["fresh_hit_count"] = int(usage.get("fresh_hit_count", 0)) + 1
    usage["last_refresh_status"] = status
    usage["last_refresh_reason"] = reason
    usage["last_refresh_stale_reasons"] = stale_reasons[:8]
    usage["last_refresh_candidate_file_count"] = candidate_file_count
    usage["last_refresh_rebuilt_chunk_count"] = rebuilt_chunk_count
    usage["last_refresh_reused_chunk_count"] = reused_chunk_count
    usage["last_refresh_duration_ms"] = duration_ms
    _bump_usage_counter(usage, "refresh_reason_counts", reason)
    return _write_usage_payload(paths, usage)


def _record_search_stats(
    paths: dict[str, Path],
    *,
    query_text: str,
    match_count: int,
    route_status: str,
    route_id: str | None,
) -> dict[str, Any]:
    usage = _load_usage(paths)
    usage["search_count"] = int(usage.get("search_count", 0)) + 1
    usage["last_search_match_count"] = match_count
    usage["last_search_query"] = query_text
    usage["last_search_route_status"] = route_status
    usage["last_search_route_id"] = route_id
    _bump_usage_counter(usage, "route_resolution_counts", route_status)
    return _write_usage_payload(paths, usage)


def _record_context_pack_stats(
    paths: dict[str, Path],
    *,
    cache_status: str,
    route_status: str,
    route_id: str | None,
    selected_tool_count: int,
) -> dict[str, Any]:
    usage = _load_usage(paths)
    if cache_status == "hit":
        usage["context_pack_hit_count"] = int(usage.get("context_pack_hit_count", 0)) + 1
    elif cache_status == "miss":
        usage["context_pack_miss_count"] = int(usage.get("context_pack_miss_count", 0)) + 1
    usage["last_context_pack_status"] = cache_status
    usage["last_context_pack_route_status"] = route_status
    usage["last_context_pack_route_id"] = route_id
    usage["last_context_pack_selected_tool_count"] = selected_tool_count
    _bump_usage_counter(usage, "route_resolution_counts", route_status)
    return _write_usage_payload(paths, usage)


def _resolve_intent_candidates(
    request_text: str | None,
    route_id: str | None,
    usage: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    routes = _intent_routes()
    if route_id:
        exact = next((route for route in routes if route["route_id"] == route_id), None)
        if exact is None:
            raise ValueError(f"Unknown route id: {route_id}")
        return exact, [dict(exact, score=100, confidence=1.0, why={"explicit_route_id": route_id})], "exact"

    query_tokens = _tokenize(request_text)
    if not query_tokens:
        ordered = [dict(route, score=0, confidence=0.0) for route in routes]
        return None, ordered, "unresolved"

    recent_routes = set((usage or {}).get("recent_route_ids", []))
    recent_tools = set((usage or {}).get("recent_tool_ids", []))
    scored: list[dict[str, Any]] = []
    for route in routes:
        score = 0
        why: dict[str, Any] = {}
        tag_score, tag_matches = _score_token_match(query_tokens, route.get("tags", []), 6)
        if tag_matches:
            why["matched_tags"] = tag_matches
        score += tag_score
        trigger_score, trigger_matches = _score_token_match(query_tokens, route.get("triggers", []), 7)
        if trigger_matches:
            why["matched_triggers"] = trigger_matches
        score += trigger_score
        title_score, title_matches = _score_token_match(query_tokens, _tokenize(route.get("title")), 4)
        if title_matches:
            why["matched_title"] = title_matches
        score += title_score
        summary_score, summary_matches = _score_token_match(query_tokens, _tokenize(route.get("summary")), 3)
        if summary_matches:
            why["matched_summary"] = summary_matches
        score += summary_score
        if route["route_id"] in recent_routes:
            score += 2
            why["recent_route"] = route["route_id"]
        if recent_tools.intersection(route.get("recommended_tools", [])):
            score += 2
            why["recent_tools"] = sorted(recent_tools.intersection(route.get("recommended_tools", [])))[:4]
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
    return {
        "plugin": plugin_info(),
        "catalog_root": str(_plugin_catalog_root()),
        "request_text": request_text,
        "requested_route_id": route_id,
        "resolved_route": resolved,
        "resolution_status": resolution_status,
        "requires_confirmation": resolution_status == "ambiguous",
        "route_candidates": candidates[:3],
        "retrieval_ladder": RETRIEVAL_LADDER,
    }


def show_capability_catalog(
    kind: str | None = None,
    route_id: str | None = None,
    query_text: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    if kind and kind not in {"skill", "mcp_tool", "script", "reference"}:
        raise ValueError(f"Unsupported capability kind: {kind}")
    entries = _all_capability_entries()
    if kind:
        entries = [entry for entry in entries if entry["kind"] == kind]
    if route_id:
        route_map = _route_index()
        if route_id not in route_map:
            raise ValueError(f"Unknown route id: {route_id}")
        entries = [entry for entry in entries if route_id in entry.get("related_routes", [])]

    query_tokens = _tokenize(query_text)
    scored_entries = []
    for entry in entries:
        score, why = _catalog_entry_score(entry, query_tokens, route_id=route_id)
        scored_entries.append(dict(entry, score=score, why=why))
    if query_tokens:
        scored_entries = [entry for entry in scored_entries if entry["score"] > 0]
        scored_entries.sort(key=lambda item: (-item["score"], item["kind"], item["title"]))
    else:
        scored_entries.sort(key=lambda item: (item["kind"], item["title"]))
    selected = scored_entries[: _limit(limit, 20, 100)]
    return {
        "plugin": plugin_info(),
        "catalog_root": str(_plugin_catalog_root()),
        "filter": {
            "kind": kind,
            "route_id": route_id,
            "query_text": query_text,
            "limit": _limit(limit, 20, 100),
        },
        "catalog_counts": {
            name: _load_catalog(name)["entry_count"]
            for name in ("skills", "mcp_tools", "scripts", "references")
        },
        "total_matches": len(scored_entries),
        "entries": selected,
    }


def _safe_workspace_state(workspace: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    paths = workspace_paths(workspace)
    if not Path(paths["workspace_state"]).exists():
        return None, None, None
    try:
        return read_workspace_state(workspace), current_workstream(workspace), current_task(workspace)
    except Exception:  # noqa: BLE001
        return None, None, None


def _safe_verification_context(workspace: Path) -> dict[str, Any]:
    state, workstream, _ = _safe_workspace_state(workspace)
    if state is None:
        return {"cases": [], "suites": [], "surface_types": []}
    try:
        recipes = read_verification_recipes(workspace, workstream_id=workstream.get("workstream_id") if workstream else None) or {}
    except Exception:  # noqa: BLE001
        return {"cases": [], "suites": [], "surface_types": []}
    cases = recipes.get("cases", []) or []
    suites = recipes.get("suites", []) or []
    return {
        "cases": [case.get("id") for case in cases[:12] if case.get("id")],
        "suites": [suite.get("id") for suite in suites[:12] if suite.get("id")],
        "surface_types": sorted({case.get("surface_type") for case in cases if case.get("surface_type")}),
    }


def _safe_git_state(workspace: Path) -> dict[str, Any]:
    try:
        return inspect_git_state(workspace)
    except Exception:  # noqa: BLE001
        return {
            "repo_root": str(workspace),
            "head_commit": None,
            "current_branch": None,
            "dirty": False,
            "changed_files": [],
            "summary_counts": {
                "changed_files": 0,
                "staged_files": 0,
                "unstaged_files": 0,
                "untracked_files": 0,
                "conflicted_files": 0,
            },
            "worktree": {
                "linked_worktree_count": 0,
            },
        }


def _package_managers(workspace: Path) -> list[str]:
    managers = [manager for filename, manager in PACKAGE_MANAGER_FILES.items() if (workspace / filename).exists()]
    if (workspace / "package.json").exists() and not managers:
        managers.append("npm")
    return sorted(dict.fromkeys(managers))


def _known_test_surfaces(workspace: Path) -> list[str]:
    package_manifest = _load_json(workspace / "package.json", default={})
    package_names = set()
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(package_manifest.get(field), dict):
            package_names.update(package_manifest[field].keys())
    surfaces: list[str] = []
    if "@playwright/test" in package_names or any((workspace / name).exists() for name in ("playwright.config.ts", "playwright.config.js", "playwright.config.mjs")):
        surfaces.append("playwright")
    if "detox" in package_names or any((workspace / name).exists() for name in (".detoxrc.js", ".detoxrc.cjs", "detox.config.js")):
        surfaces.append("detox")
    if any((workspace / name).exists() for name in ("pytest.ini", "pyproject.toml")):
        surfaces.append("python-tests")
    if (workspace / "Cargo.toml").exists():
        surfaces.append("cargo-test")
    if (workspace / "android").exists():
        surfaces.append("android-compose")
    if "vitest" in package_names:
        surfaces.append("vitest")
    if "jest" in package_names:
        surfaces.append("jest")
    return sorted(dict.fromkeys(surfaces))


def _discover_modules(workspace: Path, detection: dict[str, Any]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    def add_module(path: Path, kind: str) -> None:
        if not path.exists() or not path.is_dir():
            return
        relative = "." if path == workspace else str(path.relative_to(workspace))
        if relative in seen_paths:
            return
        manifest_path = None
        for candidate_name in ("package.json", "pyproject.toml", "Cargo.toml", ".codex-plugin/plugin.json", "build.gradle", "build.gradle.kts"):
            candidate = path / candidate_name
            if candidate.exists():
                manifest_path = str(candidate.relative_to(workspace))
                break
        modules.append(
            {
                "module_id": _short_hash(relative, length=10),
                "path": relative,
                "kind": kind,
                "manifest_path": manifest_path,
                "tags": sorted(dict.fromkeys([kind, *_tokenize(relative)])),
            }
        )
        seen_paths.add(relative)

    for parent_name in MODULE_PARENT_DIRS:
        parent = workspace / parent_name
        if not parent.exists() or not parent.is_dir():
            continue
        for child in sorted(parent.iterdir(), key=lambda item: item.name):
            if child.is_dir() and child.name not in CONTEXT_INDEX_EXCLUDED_DIRS:
                add_module(child, parent_name.rstrip("s") if parent_name != "apps" else "app")

    for plugin_rel in (detection.get("plugin_platform") or {}).get("plugin_roots", []):
        if plugin_rel:
            add_module(workspace / plugin_rel, "plugin")

    if not modules:
        add_module(workspace, "root")
    return modules


def _iter_indexable_files(workspace: Path) -> list[Path]:
    candidates: list[Path] = []
    for root, dirnames, filenames in os.walk(workspace):
        relative_root = Path(root).relative_to(workspace)
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if dirname not in CONTEXT_INDEX_EXCLUDED_DIRS and (relative_root / dirname) != Path(".git")
        )
        for filename in sorted(filenames):
            path = Path(root) / filename
            if any(part in CONTEXT_INDEX_EXCLUDED_DIRS for part in path.relative_to(workspace).parts):
                continue
            if filename not in CONTEXT_INDEX_FILENAMES and path.suffix.lower() not in CONTEXT_INDEX_SUFFIXES:
                continue
            try:
                if path.stat().st_size > CONTEXT_INDEX_MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            candidates.append(path)
    return candidates


def _path_tokens(path: Path) -> list[str]:
    return _tokenize(" ".join(path.parts))


def _route_profile_path_boost(path: Path, route_id: str) -> int:
    profile = ROUTE_PROFILES.get(route_id)
    if not profile:
        return 0
    score = 0
    relative_tokens = set(_path_tokens(path))
    if path.name in profile.get("priority_files", set()):
        score += 18
    if any(part in profile.get("priority_dirs", set()) for part in path.parts):
        score += 15
    score += len(relative_tokens.intersection(set(profile.get("path_tokens", set())))) * 4
    return score


def _candidate_priority(
    workspace: Path,
    path: Path,
    changed_paths: set[str],
    recent_route_ids: list[str] | None = None,
) -> int:
    relative = Path(path).relative_to(workspace)
    parts = relative.parts
    score = 0
    relative_string = str(relative)
    if relative_string in changed_paths:
        score += 120
    if len(parts) == 1:
        score += 100
    if path.name in CONTEXT_INDEX_PRIORITY_FILENAMES:
        score += 70
    if any(part in CONTEXT_INDEX_PRIORITY_DIRS for part in parts):
        score += 50
    if path.name.lower() == "readme.md":
        score += 40
    if "src" in parts or "app" in parts:
        score += 20
    if path.suffix.lower() in {".py", ".md", ".json", ".ts", ".tsx"}:
        score += 10
    for index, route_id in enumerate((recent_route_ids or [])[:2]):
        score += max(_route_profile_path_boost(relative, route_id) - (index * 6), 0)
    return score


def _select_candidate_files(workspace: Path, changed_paths: list[str], recent_route_ids: list[str] | None = None) -> list[Path]:
    ranked = sorted(
        _iter_indexable_files(workspace),
        key=lambda item: (
            -_candidate_priority(workspace, item, set(changed_paths), recent_route_ids=recent_route_ids),
            str(item.relative_to(workspace)),
        ),
    )
    return ranked[:CONTEXT_INDEX_MAX_FILES]


def _extract_symbols(path: Path, text: str) -> list[str]:
    suffix = path.suffix.lower()
    patterns = []
    if suffix == ".py":
        patterns = [r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"]
    elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
        patterns = [
            r"^\s*export\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*export\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^\s*export\s+const\s+([A-Za-z_][A-Za-z0-9_]*)",
        ]
    elif suffix in {".kt", ".kts"}:
        patterns = [r"^\s*fun\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*(?:data\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)"]
    elif suffix == ".rs":
        patterns = [r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)"]
    elif suffix == ".md":
        patterns = [r"^#+\s+(.+)$"]
    symbols: list[str] = []
    for pattern in patterns:
        symbols.extend(re.findall(pattern, text, flags=re.MULTILINE))
    cleaned = []
    for item in symbols:
        normalized = item.strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned[:12]


def _extract_dependencies(path: Path, text: str) -> list[str]:
    suffix = path.suffix.lower()
    dependencies: list[str] = []
    if path.name == "package.json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {}
        for field in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(payload.get(field), dict):
                for package_name in sorted(payload[field]):
                    if package_name not in dependencies:
                        dependencies.append(package_name)
    elif suffix == ".py":
        dependencies.extend(re.findall(r"^\s*(?:from|import)\s+([A-Za-z0-9_\.]+)", text, flags=re.MULTILINE))
    elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
        dependencies.extend(re.findall(r"from\s+[\"']([^\"']+)[\"']", text))
    elif suffix in {".kt", ".kts"}:
        dependencies.extend(re.findall(r"^\s*import\s+([A-Za-z0-9_\.]+)", text, flags=re.MULTILINE))
    elif suffix == ".rs":
        dependencies.extend(re.findall(r"^\s*use\s+([^;]+);", text, flags=re.MULTILINE))
    elif suffix in {".yml", ".yaml"}:
        dependencies.extend(re.findall(r"^\s*-\s*([A-Za-z0-9_./:-]+)", text, flags=re.MULTILINE))
    cleaned = []
    for dependency in dependencies:
        normalized = dependency.strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned[:12]


def _file_tags(path: Path, summary: str, symbols: list[str], dependencies: list[str]) -> list[str]:
    tags = [path.suffix.lower().lstrip("."), *path.parts[:3], *_tokenize(path.stem), *_tokenize(summary)]
    tags.extend(_tokenize(" ".join(symbols[:4])))
    tags.extend(_tokenize(" ".join(dependencies[:4])))
    cleaned = [tag for tag in tags if tag and tag not in {"", "."}]
    return sorted(dict.fromkeys(cleaned))


def _infer_route_hints(relative_path: str, tags: list[str], summary: str) -> list[str]:
    route_hints: list[str] = []
    content_tokens = set(_expanded_token_set(tags + _tokenize(summary)))
    path_tokens = set(_expanded_token_set(_tokenize(relative_path)))
    for route_id, profile in ROUTE_PROFILES.items():
        profile_tokens = set(_expanded_token_set(list(profile.get("path_tokens", set()))))
        if route_id in content_tokens or profile_tokens.intersection(content_tokens) or profile_tokens.intersection(path_tokens):
            route_hints.append(route_id)
    return sorted(dict.fromkeys(route_hints))


def _summary_from_text(path: Path, text: str, symbols: list[str], dependencies: list[str]) -> str:
    if path.name == "package.json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {}
        package_name = payload.get("name") or path.parent.name
        scripts = sorted((payload.get("scripts") or {}).keys())[:4]
        deps = dependencies[:4]
        return _normalize_text(
            f"Package manifest for {package_name}. Scripts: {', '.join(scripts) or 'none'}. Dependencies: {', '.join(deps) or 'none'}."
        )
    if path.suffix.lower() == ".md":
        heading_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        paragraph = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("- ") and not stripped.startswith("```"):
                paragraph = stripped
                break
        return _normalize_text(f"{heading_match.group(1) if heading_match else path.stem}. {paragraph}")
    if path.suffix.lower() in {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".kt", ".kts", ".rs"}:
        label = {
            ".py": "Python module",
            ".js": "JavaScript module",
            ".jsx": "React module",
            ".mjs": "JavaScript module",
            ".cjs": "JavaScript module",
            ".ts": "TypeScript module",
            ".tsx": "React TypeScript module",
            ".kt": "Kotlin source file",
            ".kts": "Kotlin build script",
            ".rs": "Rust module",
        }[path.suffix.lower()]
        symbol_part = f" Symbols: {', '.join(symbols[:4])}." if symbols else ""
        dependency_part = f" Imports: {', '.join(dependencies[:4])}." if dependencies else ""
        return _normalize_text(f"{label} at {path.name}.{symbol_part}{dependency_part}")
    if path.suffix.lower() in {".json", ".toml", ".yaml", ".yml"}:
        keys = re.findall(r"^\s*\"?([A-Za-z0-9_.-]+)\"?\s*[:=]", text, flags=re.MULTILINE)
        return _normalize_text(f"Config or manifest file at {path.name}. Keys: {', '.join(keys[:6]) or 'none'}.")
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), path.name)
    return _normalize_text(f"{path.name}. {first_line}")


def _build_chunk_record(workspace: Path, path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    file_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
    relative = str(path.relative_to(workspace))
    symbols = _extract_symbols(path, text)
    dependencies = _extract_dependencies(path, text)
    summary = _summary_from_text(path, text, symbols, dependencies)
    tags = _file_tags(path.relative_to(workspace), summary, symbols, dependencies)
    return {
        "chunk_id": _short_hash(f"{relative}:{file_hash}", length=16),
        "path": relative,
        "symbols": symbols,
        "tags": tags,
        "summary": summary,
        "hash": file_hash,
        "dependencies": dependencies,
        "route_hints": _infer_route_hints(relative, tags, summary),
    }


def _workspace_fingerprint(workspace: Path, chunks: list[dict[str, Any]]) -> str:
    chunk_digest = _short_hash("|".join(f"{chunk['path']}:{chunk['hash']}" for chunk in chunks), length=10)
    return _short_hash(f"{PLUGIN_VERSION}:{workspace}:{chunk_digest}", length=16)


def _workspace_context_payload(
    workspace: Path,
    detection: dict[str, Any],
    git_state: dict[str, Any],
    modules: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    usage: dict[str, Any],
    catalog_digest: str,
) -> dict[str, Any]:
    state, workstream, task = _safe_workspace_state(workspace)
    verification = _safe_verification_context(workspace)
    changed_paths = [entry["path"] for entry in git_state.get("changed_files", [])][:24]
    project_memory_chunks = [chunk for chunk in chunks if chunk.get("source_kind") == "project_memory"]
    pinned_project_memory_chunks = [
        chunk
        for chunk in project_memory_chunks
        if chunk.get("pin_state") == "pinned" and chunk.get("note_status") == "active"
    ]
    return {
        "schema_version": CONTEXT_CACHE_SCHEMA_VERSION,
        "plugin_version": PLUGIN_VERSION,
        "catalog_digest": catalog_digest,
        "workspace_path": str(workspace),
        "workspace_label": detection.get("workspace_label"),
        "workspace_slug": detection.get("workspace_slug"),
        "workspace_hash": detection.get("workspace_hash"),
        "workspace_fingerprint": _workspace_fingerprint(workspace, chunks),
        "generated_at": now_iso(),
        "state_initialized": state is not None,
        "detected_stacks": detection.get("detected_stacks", []),
        "selected_profiles": detection.get("selected_profiles", []),
        "package_managers": _package_managers(workspace),
        "top_level_modules": modules[:16],
        "current_workstream": {
            "workstream_id": workstream.get("workstream_id"),
            "title": workstream.get("title"),
            "kind": workstream.get("kind"),
            "status": workstream.get("status"),
        } if workstream else None,
        "current_task": {
            "task_id": task.get("task_id"),
            "title": task.get("title"),
            "status": task.get("status"),
        } if task else None,
        "changed_paths": changed_paths,
        "git_summary": {
            "current_branch": git_state.get("current_branch"),
            "head_commit": git_state.get("head_commit"),
            "dirty": git_state.get("dirty"),
            "summary_counts": git_state.get("summary_counts", {}),
            "linked_worktree_count": (git_state.get("worktree") or {}).get("linked_worktree_count", 0),
        },
        "known_test_surfaces": _known_test_surfaces(workspace),
        "known_verification_surfaces": verification,
        "project_memory": {
            "note_count": len(project_memory_chunks),
            "pinned_note_count": len(pinned_project_memory_chunks),
            "pinned_notes": [
                {
                    "note_id": chunk.get("note_id"),
                    "path": chunk.get("path"),
                    "summary": chunk.get("summary"),
                }
                for chunk in pinned_project_memory_chunks[:8]
            ],
        },
        "last_used_routes": usage.get("recent_route_ids", [])[:6],
        "last_used_tools": usage.get("recent_tool_ids", [])[:12],
        "usage_stats": {
            "fresh_hit_count": usage.get("fresh_hit_count", 0),
            "refresh_count": usage.get("refresh_count", 0),
            "search_count": usage.get("search_count", 0),
            "context_pack_hit_count": usage.get("context_pack_hit_count", 0),
            "context_pack_miss_count": usage.get("context_pack_miss_count", 0),
        },
        "chunk_count": len(chunks),
        "module_count": len(modules),
    }


def _annotate_modules(workspace: Path, modules: list[dict[str, Any]], candidate_files: list[Path]) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for module in modules:
        module_path = workspace if module["path"] == "." else workspace / module["path"]
        hints = [
            str(path.relative_to(workspace))
            for path in candidate_files
            if path == module_path or module_path in path.parents
        ][:6]
        annotated.append({**module, "entrypoint_hints": hints})
    return annotated


def _required_cache_files(cache_paths: dict[str, Path]) -> list[Path]:
    return [
        cache_paths["workspace_context"],
        cache_paths["module_map"],
        cache_paths["chunk_summaries"],
        cache_paths["semantic_cache"],
    ]


def _load_manifest(paths: dict[str, Path]) -> dict[str, Any]:
    payload = _load_json(paths["manifest"], default={})
    return payload if isinstance(payload, dict) else {}


def _manifest_stale_reasons(
    manifest: dict[str, Any],
    *,
    workspace: Path,
    git_state: dict[str, Any],
    candidate_snapshot: list[dict[str, Any]],
    catalog_digest: str,
) -> list[str]:
    reasons: list[str] = []
    if not manifest:
        return ["missing-manifest"]
    if manifest.get("schema_version") != CONTEXT_INDEX_MANIFEST_SCHEMA_VERSION:
        reasons.append("manifest-schema")
    if manifest.get("plugin_version") != PLUGIN_VERSION:
        reasons.append("plugin-version")
    if manifest.get("catalog_digest") != catalog_digest:
        reasons.append("catalog-digest")
    if manifest.get("workspace_path") != str(workspace):
        reasons.append("workspace-path")
    if manifest.get("git_head_commit") != git_state.get("head_commit"):
        reasons.append("git-head")
    dirty_digest = _git_dirty_digest(git_state)
    if manifest.get("dirty_digest") != dirty_digest:
        reasons.append("dirty-digest")
    if manifest.get("candidate_paths_digest") != _candidate_paths_digest(candidate_snapshot):
        reasons.append("candidate-set")
    if manifest.get("indexed_file_snapshot") != candidate_snapshot:
        reasons.append("indexed-file-snapshot")
    for cache_file in _required_cache_files(_context_cache_paths(workspace)):
        if not cache_file.exists():
            reasons.append(f"missing-cache:{cache_file.name}")
    return reasons


def _context_only_stale_reasons(stale_reasons: list[str]) -> list[str]:
    return [reason for reason in stale_reasons if reason in {"git-head", "dirty-digest"}]


def _requires_chunk_refresh(stale_reasons: list[str]) -> bool:
    return any(reason not in {"git-head", "dirty-digest"} for reason in stale_reasons)


def _prune_semantic_cache_entries(
    entries: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    catalog_digest: str,
    *,
    full_reset_reason: str | None = None,
) -> tuple[list[dict[str, Any]], int, str | None]:
    if full_reset_reason:
        return [], len(entries), full_reset_reason if entries else None
    chunk_hashes = {chunk["path"]: chunk["hash"] for chunk in chunks}
    kept: list[dict[str, Any]] = []
    pruned = 0
    for entry in entries:
        if entry.get("schema_version", 1) != SEMANTIC_CACHE_ENTRY_SCHEMA_VERSION:
            pruned += 1
            continue
        if entry.get("catalog_digest") != catalog_digest:
            pruned += 1
            continue
        source_hashes = entry.get("source_hashes")
        if not isinstance(source_hashes, dict) or not source_hashes:
            pruned += 1
            continue
        if any(chunk_hashes.get(path) != expected_hash for path, expected_hash in source_hashes.items()):
            pruned += 1
            continue
        kept.append(entry)
    reason = "source-hash-drift" if pruned else None
    return kept, pruned, reason


def refresh_context_index(workspace: str | Path, force: bool = False) -> dict[str, Any]:
    started_at = perf_counter()
    resolved_workspace = Path(workspace).expanduser().resolve()
    cache_paths = _context_cache_paths(resolved_workspace)
    git_state = _safe_git_state(resolved_workspace)
    usage = _load_usage(cache_paths)
    candidate_files = _select_candidate_files(
        resolved_workspace,
        [entry["path"] for entry in git_state.get("changed_files", [])],
        recent_route_ids=usage.get("recent_route_ids", []),
    )
    project_note_candidates = _project_note_candidates(resolved_workspace)
    candidate_snapshot = _indexed_file_snapshot(resolved_workspace, [*candidate_files, *project_note_candidates])
    candidate_count = len(candidate_snapshot)
    catalog_digest = _catalog_digest()
    manifest = _load_manifest(cache_paths)
    stale_reasons = _manifest_stale_reasons(
        manifest,
        workspace=resolved_workspace,
        git_state=git_state,
        candidate_snapshot=candidate_snapshot,
        catalog_digest=catalog_digest,
    )
    if force:
        stale_reasons = ["force-refresh", *stale_reasons]
    if not stale_reasons:
        workspace_context = _load_json(cache_paths["workspace_context"], default={})
        module_map = _load_json(cache_paths["module_map"], default={"modules": []})
        chunks = _load_jsonl(cache_paths["chunk_summaries"])
        duration_ms = int((perf_counter() - started_at) * 1000)
        _record_refresh_stats(
            cache_paths,
            status="fresh",
            duration_ms=duration_ms,
            reason="manifest-match",
            stale_reasons=[],
            candidate_file_count=candidate_count,
            rebuilt_chunk_count=0,
            reused_chunk_count=len(chunks),
        )
        return {
            "status": "fresh",
            "workspace_path": str(resolved_workspace),
            "cache_root": str(cache_paths["root"]),
            "workspace_context_path": str(cache_paths["workspace_context"]),
            "module_map_path": str(cache_paths["module_map"]),
            "chunk_summaries_path": str(cache_paths["chunk_summaries"]),
            "semantic_cache_path": str(cache_paths["semantic_cache"]),
            "workspace_fingerprint": workspace_context.get("workspace_fingerprint"),
            "catalog_digest": catalog_digest,
            "candidate_file_count": candidate_count,
            "module_count": len(module_map.get("modules", [])),
            "chunk_count": len(chunks),
            "rebuilt_chunk_count": 0,
            "reused_chunk_count": len(chunks),
            "pruned_semantic_cache_entries": 0,
            "pruned_semantic_cache_reason": None,
            "refresh_reason": "manifest-match",
            "stale_reasons": [],
            "rebuilt_paths": [],
        }

    if not force and stale_reasons and not _requires_chunk_refresh(stale_reasons):
        detection = detect_workspace(resolved_workspace)
        chunks = _load_jsonl(cache_paths["chunk_summaries"])
        module_map = _load_json(cache_paths["module_map"], default={"modules": []})
        modules = module_map.get("modules", [])
        workspace_context = _workspace_context_payload(
            resolved_workspace,
            detection,
            git_state,
            modules,
            chunks,
            usage,
            catalog_digest,
        )
        previous_context = _load_json(cache_paths["workspace_context"], default={})
        previous_catalog_digest = previous_context.get("catalog_digest")
        semantic_cache_entries = _load_jsonl(cache_paths["semantic_cache"])
        semantic_cache_entries, pruned, pruned_reason = _prune_semantic_cache_entries(
            semantic_cache_entries,
            chunks,
            catalog_digest,
            full_reset_reason="catalog-digest" if previous_catalog_digest and previous_catalog_digest != catalog_digest else None,
        )
        _write_json(cache_paths["workspace_context"], workspace_context)
        _write_json(
            cache_paths["manifest"],
            {
                "schema_version": CONTEXT_INDEX_MANIFEST_SCHEMA_VERSION,
                "plugin_version": PLUGIN_VERSION,
                "catalog_digest": catalog_digest,
                "workspace_path": str(resolved_workspace),
                "git_head_commit": git_state.get("head_commit"),
                "dirty_digest": _git_dirty_digest(git_state),
                "candidate_paths_digest": _candidate_paths_digest(candidate_snapshot),
                "indexed_file_snapshot": candidate_snapshot,
                "workspace_fingerprint": workspace_context["workspace_fingerprint"],
                "candidate_file_count": candidate_count,
                "module_count": len(modules),
                "chunk_count": len(chunks),
                "generated_at": now_iso(),
            },
        )
        _write_jsonl(cache_paths["semantic_cache"], semantic_cache_entries)
        duration_ms = int((perf_counter() - started_at) * 1000)
        refresh_reason = stale_reasons[0]
        _record_refresh_stats(
            cache_paths,
            status="context-refreshed",
            duration_ms=duration_ms,
            reason=refresh_reason,
            stale_reasons=stale_reasons,
            candidate_file_count=candidate_count,
            rebuilt_chunk_count=0,
            reused_chunk_count=len(chunks),
        )
        return {
            "status": "context-refreshed",
            "workspace_path": str(resolved_workspace),
            "cache_root": str(cache_paths["root"]),
            "workspace_context_path": str(cache_paths["workspace_context"]),
            "module_map_path": str(cache_paths["module_map"]),
            "chunk_summaries_path": str(cache_paths["chunk_summaries"]),
            "semantic_cache_path": str(cache_paths["semantic_cache"]),
            "workspace_fingerprint": workspace_context["workspace_fingerprint"],
            "catalog_digest": catalog_digest,
            "candidate_file_count": candidate_count,
            "module_count": len(modules),
            "chunk_count": len(chunks),
            "rebuilt_chunk_count": 0,
            "reused_chunk_count": len(chunks),
            "pruned_semantic_cache_entries": pruned,
            "pruned_semantic_cache_reason": pruned_reason,
            "refresh_reason": refresh_reason,
            "stale_reasons": stale_reasons[:8],
            "rebuilt_paths": [],
        }

    detection = detect_workspace(resolved_workspace)
    modules = _discover_modules(resolved_workspace, detection)
    modules = _annotate_modules(resolved_workspace, modules, candidate_files)
    existing_chunks = {entry["path"]: entry for entry in _load_jsonl(cache_paths["chunk_summaries"])}
    manifest_snapshot_map = {entry["path"]: (entry["size"], entry["mtime_ns"]) for entry in manifest.get("indexed_file_snapshot", [])}
    candidate_snapshot_map = {entry["path"]: (entry["size"], entry["mtime_ns"]) for entry in candidate_snapshot}
    chunk_records: list[dict[str, Any]] = []
    rebuilt_paths: list[str] = []
    reused_paths: list[str] = []
    for candidate in candidate_files:
        relative = str(candidate.relative_to(resolved_workspace))
        existing = existing_chunks.get(relative)
        if (
            not force
            and existing
            and manifest_snapshot_map.get(relative) == candidate_snapshot_map.get(relative)
        ):
            chunk_records.append(existing)
            reused_paths.append(relative)
            continue
        chunk_records.append(_build_chunk_record(resolved_workspace, candidate))
        rebuilt_paths.append(relative)
    for candidate in project_note_candidates:
        relative = str(candidate["path"])
        existing = existing_chunks.get(relative)
        if (
            not force
            and existing
            and manifest_snapshot_map.get(relative) == candidate_snapshot_map.get(relative)
        ):
            chunk_records.append(existing)
            reused_paths.append(relative)
            continue
        chunk_records.append(_build_project_note_chunk_record(candidate))
        rebuilt_paths.append(relative)

    chunk_records.sort(key=lambda item: item["path"])
    workspace_context = _workspace_context_payload(
        resolved_workspace,
        detection,
        git_state,
        modules,
        chunk_records,
        usage,
        catalog_digest,
    )

    previous_context = _load_json(cache_paths["workspace_context"], default={})
    previous_catalog_digest = previous_context.get("catalog_digest")
    _write_json(cache_paths["workspace_context"], workspace_context)
    _write_json(
        cache_paths["module_map"],
        {
            "schema_version": CONTEXT_CACHE_SCHEMA_VERSION,
            "workspace_path": str(resolved_workspace),
            "workspace_fingerprint": workspace_context["workspace_fingerprint"],
            "modules": modules,
        },
    )
    _write_jsonl(cache_paths["chunk_summaries"], chunk_records)

    semantic_cache_entries = _load_jsonl(cache_paths["semantic_cache"])
    semantic_cache_entries, pruned, pruned_reason = _prune_semantic_cache_entries(
        semantic_cache_entries,
        chunk_records,
        catalog_digest,
        full_reset_reason="catalog-digest" if previous_catalog_digest and previous_catalog_digest != catalog_digest else None,
    )
    _write_jsonl(cache_paths["semantic_cache"], semantic_cache_entries)

    _write_json(
        cache_paths["manifest"],
        {
            "schema_version": CONTEXT_INDEX_MANIFEST_SCHEMA_VERSION,
            "plugin_version": PLUGIN_VERSION,
            "catalog_digest": catalog_digest,
            "workspace_path": str(resolved_workspace),
            "git_head_commit": git_state.get("head_commit"),
            "dirty_digest": _git_dirty_digest(git_state),
            "candidate_paths_digest": _candidate_paths_digest(candidate_snapshot),
            "indexed_file_snapshot": candidate_snapshot,
            "workspace_fingerprint": workspace_context["workspace_fingerprint"],
            "candidate_file_count": candidate_count,
            "module_count": len(modules),
            "chunk_count": len(chunk_records),
            "generated_at": now_iso(),
        },
    )
    duration_ms = int((perf_counter() - started_at) * 1000)
    refresh_reason = stale_reasons[0] if stale_reasons else "refresh"
    _record_refresh_stats(
        cache_paths,
        status="refreshed",
        duration_ms=duration_ms,
        reason=refresh_reason,
        stale_reasons=stale_reasons,
        candidate_file_count=candidate_count,
        rebuilt_chunk_count=len(rebuilt_paths),
        reused_chunk_count=len(reused_paths),
    )

    return {
        "status": "refreshed",
        "workspace_path": str(resolved_workspace),
        "cache_root": str(cache_paths["root"]),
        "workspace_context_path": str(cache_paths["workspace_context"]),
        "module_map_path": str(cache_paths["module_map"]),
        "chunk_summaries_path": str(cache_paths["chunk_summaries"]),
        "semantic_cache_path": str(cache_paths["semantic_cache"]),
        "workspace_fingerprint": workspace_context["workspace_fingerprint"],
        "catalog_digest": catalog_digest,
        "candidate_file_count": candidate_count,
        "module_count": len(modules),
        "chunk_count": len(chunk_records),
        "rebuilt_chunk_count": len(rebuilt_paths),
        "reused_chunk_count": len(reused_paths),
        "pruned_semantic_cache_entries": pruned,
        "pruned_semantic_cache_reason": pruned_reason,
        "refresh_reason": refresh_reason,
        "stale_reasons": stale_reasons[:8],
        "rebuilt_paths": rebuilt_paths[:24],
    }


def _load_workspace_context_bundle(
    workspace: str | Path,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    refresh_result = refresh_context_index(workspace, force=False)
    cache_paths = _context_cache_paths(workspace)
    workspace_context = _load_json(cache_paths["workspace_context"], default={})
    module_map = _load_json(cache_paths["module_map"], default={"modules": []})
    chunks = _load_jsonl(cache_paths["chunk_summaries"])
    usage = _load_usage(cache_paths)
    return refresh_result, cache_paths, workspace_context, module_map.get("modules", []), chunks, usage


def _chunk_score(
    chunk: dict[str, Any],
    query_tokens: list[str],
    route: dict[str, Any] | None,
    workspace_context: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    score = 0
    why: dict[str, Any] = {}
    tag_score, tag_matches = _score_token_match(query_tokens, chunk.get("tags", []), 6)
    if tag_matches:
        why["matched_tags"] = tag_matches
    score += tag_score
    summary_score, summary_matches = _score_token_match(query_tokens, _tokenize(chunk.get("summary")), 4)
    if summary_matches:
        why["matched_summary"] = summary_matches
    score += summary_score
    path_score, path_matches = _score_token_match(query_tokens, _tokenize(chunk.get("path")), 5)
    if path_matches:
        why["matched_path"] = path_matches
    score += path_score
    symbol_score, symbol_matches = _score_token_match(query_tokens, _tokenize(" ".join(chunk.get("symbols", []))), 5)
    if symbol_matches:
        why["matched_symbols"] = symbol_matches
    score += symbol_score
    if route:
        route_tokens = list(route.get("tags", [])) + list(route.get("triggers", []))
        route_score, route_matches = _score_token_match(query_tokens, route_tokens, 1)
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
        profile_path_tokens = sorted(set(_path_tokens(relative_path)).intersection(set(profile.get("path_tokens", set()))))
        if profile_path_tokens:
            score += len(profile_path_tokens) * 3
            why["profile_path_tokens"] = profile_path_tokens
    if chunk.get("path") in set(workspace_context.get("changed_paths", [])):
        score += 4
        why["changed_path"] = True
    if chunk.get("source_kind") == "project_memory" and chunk.get("pin_state") == "pinned" and chunk.get("note_status") == "active":
        score += 6
        why["pinned_project_memory"] = True
    current_tokens = _tokenize(
        " ".join(
            value
            for value in [
                (workspace_context.get("current_workstream") or {}).get("title", ""),
                (workspace_context.get("current_task") or {}).get("title", ""),
            ]
        )
    )
    current_score, current_matches = _score_token_match(query_tokens, current_tokens, 2)
    if current_matches:
        why["matched_current_scope"] = current_matches
    score += current_score
    return score, why


def search_context_index(
    workspace: str | Path,
    query_text: str,
    route_id: str | None = None,
    limit: int | None = 8,
) -> dict[str, Any]:
    if not _normalize_text(query_text):
        raise ValueError("query_text is required")
    refresh_result, cache_paths, workspace_context, modules, chunks, usage = _load_workspace_context_bundle(workspace)
    route, route_candidates, route_status = _resolve_intent_candidates(query_text, route_id, usage=usage)
    query_tokens = _tokenize(query_text)

    scored_chunks = []
    for chunk in chunks:
        score, why = _chunk_score(chunk, query_tokens, route, workspace_context)
        if score <= 0:
            continue
        scored_chunks.append(
            {
                **chunk,
                "score": score,
                "why": why,
            }
        )
    scored_chunks.sort(key=lambda item: (-item["score"], item["path"]))

    capability_catalog = show_capability_catalog(route_id=route["route_id"] if route else None, query_text=query_text, limit=12)
    _record_search_stats(
        cache_paths,
        query_text=query_text,
        match_count=len(scored_chunks),
        route_status=route_status,
        route_id=route["route_id"] if route else None,
    )
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "cache_root": str(cache_paths["root"]),
        "index_status": refresh_result["status"],
        "index_refresh_reason": refresh_result.get("refresh_reason"),
        "query_text": query_text,
        "resolved_route": route,
        "route_resolution_status": route_status,
        "requires_route_confirmation": route_status == "ambiguous",
        "route_candidates": route_candidates[:3],
        "workspace_context": {
            "workspace_fingerprint": workspace_context.get("workspace_fingerprint"),
            "catalog_digest": workspace_context.get("catalog_digest"),
            "detected_stacks": workspace_context.get("detected_stacks", []),
            "selected_profiles": workspace_context.get("selected_profiles", []),
            "changed_paths": workspace_context.get("changed_paths", []),
            "current_workstream": workspace_context.get("current_workstream"),
            "current_task": workspace_context.get("current_task"),
            "project_memory": workspace_context.get("project_memory"),
        },
        "modules": modules[:12],
        "matches": scored_chunks[: _limit(limit, 8, 40)],
        "recommended_capabilities": capability_catalog["entries"][:12],
    }


def _semantic_cache_key(query_text: str, catalog_digest: str, route_id: str | None) -> str:
    return _short_hash(f"{catalog_digest}:{route_id or 'none'}:{_normalize_text(query_text).lower()}", length=16)


def _pinned_project_memory_selected_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [
        {
            "chunk_id": chunk["chunk_id"],
            "path": chunk["path"],
            "summary": chunk["summary"],
            "score": 999,
        }
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
    if force_refresh:
        refresh_context_index(resolved_workspace, force=True)
    refresh_result, cache_paths, workspace_context, modules, chunks, usage = _load_workspace_context_bundle(resolved_workspace)
    route, route_candidates, route_status = _resolve_intent_candidates(request_text, route_id, usage=usage)

    if request_text:
        cache_key = _semantic_cache_key(
            request_text,
            workspace_context.get("catalog_digest", ""),
            route["route_id"] if route else None,
        )
        semantic_cache = _load_jsonl(cache_paths["semantic_cache"])
        chunk_hashes = {chunk["path"]: chunk["hash"] for chunk in chunks}
        cached = next(
            (
                entry
                for entry in semantic_cache
                if entry.get("query_fingerprint") == cache_key
                and entry.get("schema_version", 1) == SEMANTIC_CACHE_ENTRY_SCHEMA_VERSION
                and entry.get("catalog_digest") == workspace_context.get("catalog_digest")
                and isinstance(entry.get("source_hashes"), dict)
                and entry.get("source_hashes")
                and all(chunk_hashes.get(path) == expected_hash for path, expected_hash in entry["source_hashes"].items())
            ),
            None,
        )
        if cached:
            _record_context_pack_stats(
                cache_paths,
                cache_status="hit",
                route_status=route_status,
                route_id=route["route_id"] if route else None,
                selected_tool_count=len(cached.get("selected_tools", [])),
            )
            return {
                "workspace_path": str(resolved_workspace),
                "cache_root": str(cache_paths["root"]),
                "cache_status": "hit",
                "index_status": refresh_result["status"],
                "index_refresh_reason": refresh_result.get("refresh_reason"),
                "workspace_context": workspace_context,
                "resolved_route": route,
                "route_resolution_status": route_status,
                "route_candidates": route_candidates[:3],
                "context_pack": cached,
            }

        search = search_context_index(resolved_workspace, request_text, route_id=route["route_id"] if route else None, limit=limit)
        selected_chunks = _pinned_project_memory_selected_chunks(chunks)
        selected_chunks.extend(
            [
                {
                    "chunk_id": match["chunk_id"],
                    "path": match["path"],
                    "summary": match["summary"],
                    "score": match["score"],
                }
                for match in search["matches"]
            ]
        )
        deduped_chunks: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for item in selected_chunks:
            if item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            deduped_chunks.append(item)
        selected_chunks = deduped_chunks[: _limit(limit, 6, 40) + 12]
        selected_tools = []
        if route:
            selected_tools.extend(route.get("recommended_tools", []))
        selected_tools.extend(
            entry["tool_name"]
            for entry in search["recommended_capabilities"]
            if entry["kind"] == "mcp_tool"
        )
        selected_tools = list(dict.fromkeys(selected_tools))[:12]
        source_hashes = {
            match["path"]: next(chunk["hash"] for chunk in chunks if chunk["path"] == match["path"])
            for match in selected_chunks
        }
        confidence = round(min(sum(match["score"] for match in selected_chunks[:3]) / 36, 1.0), 2)
        context_pack = {
            "schema_version": SEMANTIC_CACHE_ENTRY_SCHEMA_VERSION,
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
        _write_jsonl(cache_paths["semantic_cache"], semantic_cache[-40:])
        _record_usage(cache_paths, route["route_id"] if route else None, selected_tools)
        _record_context_pack_stats(
            cache_paths,
            cache_status="miss",
            route_status=route_status,
            route_id=route["route_id"] if route else None,
            selected_tool_count=len(selected_tools),
        )
        return {
            "workspace_path": str(resolved_workspace),
            "cache_root": str(cache_paths["root"]),
            "cache_status": "miss",
            "index_status": refresh_result["status"],
            "index_refresh_reason": refresh_result.get("refresh_reason"),
            "workspace_context": workspace_context,
            "resolved_route": route,
            "route_resolution_status": route_status,
            "route_candidates": route_candidates[:3],
            "context_pack": context_pack,
        }

    _record_context_pack_stats(
        cache_paths,
        cache_status="workspace_only",
        route_status=route_status,
        route_id=route["route_id"] if route else None,
        selected_tool_count=0,
    )
    return {
        "workspace_path": str(resolved_workspace),
        "cache_root": str(cache_paths["root"]),
        "cache_status": "workspace_only",
        "index_status": refresh_result["status"],
        "index_refresh_reason": refresh_result.get("refresh_reason"),
        "workspace_context": workspace_context,
        "resolved_route": route,
        "route_resolution_status": route_status,
        "route_candidates": route_candidates[:3],
        "modules": modules[:16],
        "chunk_count": len(chunks),
        "pinned_project_memory": _pinned_project_memory_selected_chunks(chunks),
        "retrieval_ladder": RETRIEVAL_LADDER,
    }
