from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from time import perf_counter, time_ns
from typing import Any

from agentiux_dev_lib import (
    PLUGIN_VERSION,
    _design_and_testability_summary,
    _current_task_record_from_paths,
    _current_workstream_record_from_paths,
    _git_output_or_empty,
    _workspace_state_from_paths,
    detect_workspace,
    now_iso,
    plugin_root,
    read_design_brief,
    read_design_handoff,
    slugify,
    state_root,
    workspace_paths,
    workspace_hash,
)
from agentiux_dev_context_structure import (
    MAX_DEPENDENCY_TARGETS,
    MAX_DOC_SECTION_CHUNKS_PER_FILE,
    MAX_SYMBOL_CHUNKS_PER_FILE,
    STRUCTURE_INDEX_SCHEMA_VERSION,
    aggregate_modules,
    apply_file_metadata_to_chunks,
    build_file_structure,
    default_parser_backends,
    hotspot_summary,
    resolve_local_dependency_targets,
    structure_index_payload,
    structure_summary,
    summarize_chunk_counts,
    top_hotspots,
)
from agentiux_dev_context_semantic import (
    load_semantic_manifest,
    refresh_semantic_index,
    semantic_summary_from_manifest,
)
from agentiux_dev_context_projection import (
    OWNERSHIP_GRAPH_SCHEMA_VERSION,
    ROUTE_SHORTLIST_SCHEMA_VERSION,
    build_ownership_graph,
    build_route_shortlist_projections,
)
from agentiux_dev_context_store import (
    QUERY_CACHE_CONTEXT_PACK_KIND,
    QUERY_CACHE_OWNERSHIP_GRAPH_KIND,
    QUERY_CACHE_ROUTE_SHORTLIST_KIND,
    QUERY_CACHE_RUNTIME_PREFLIGHT_KIND,
    QUERY_CACHE_TASK_RETRIEVAL_KIND,
    context_store_summary,
    load_chunks as load_context_store_chunks,
    load_modules as load_context_store_modules,
    list_query_cache_entries,
    replace_context_records,
    replace_query_cache_entries,
)
from agentiux_dev_retrieval import infer_retrieval_mode, retrieval_mode_profile, retrieval_policy_payload
from agentiux_dev_text import normalize_command_phrase, score_token_match, short_hash, tokenize_text
from agentiux_dev_verification import read_verification_recipes
from agentiux_dev_memory import list_generated_memory_snapshots, pinned_project_notes


CATALOG_FILENAMES = ("skills", "mcp_tools", "scripts", "references", "intent_routes")
CONTEXT_CACHE_SCHEMA_VERSION = 6
CONTEXT_INDEX_MANIFEST_SCHEMA_VERSION = 6
SEMANTIC_CACHE_ENTRY_SCHEMA_VERSION = 5
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
CONTEXT_INDEX_LARGE_FILE_BYTES = 64_000
CONTEXT_INDEX_MAX_FILES = 240
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
ROUTE_PROFILES: dict[str, dict[str, Any]] = {
    "analysis": {
        "priority_dirs": {"catalogs", "references", "scripts", "skills"},
        "priority_files": {"README.md", "plugin.json", "pyproject.toml"},
        "path_tokens": {"analysis", "chunk", "hotspot", "index", "incremental", "module", "section", "structural", "symbol"},
    },
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
        "priority_files": {
            ".mcp.json",
            "README.md",
            "plugin.json",
            "agentiux_dev_context_cache.py",
            "agentiux_dev_context_query.py",
            "agentiux_dev_e2e_support.py",
            "agentiux_dev_retrieval.py",
            "agentiux_dev_state.py",
        },
        "path_tokens": {
            "benchmark",
            "budget",
            "cache",
            "catalog",
            "context",
            "dashboard",
            "mcp",
            "payload",
            "performance",
            "plugin",
            "retrieval",
            "telemetry",
        },
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
PERSISTED_RUNTIME_CONTEXT_CONFIDENCE_MIN = 0.68


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return json.loads(json.dumps(default))
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{time_ns()}.tmp"
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def plugin_catalog_root() -> Path:
    return plugin_root() / "catalogs"


def _catalog_path(name: str) -> Path:
    if name not in CATALOG_FILENAMES:
        raise ValueError(f"Unknown catalog: {name}")
    return plugin_catalog_root() / f"{name}.json"


def load_catalog(name: str) -> dict[str, Any]:
    payload = load_json(_catalog_path(name), default={})
    if not payload or not isinstance(payload.get("entries"), list):
        raise ValueError(f"Catalog is missing or invalid: {_catalog_path(name)}")
    return payload


def all_capability_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in ("skills", "mcp_tools", "scripts", "references"):
        entries.extend(load_catalog(name)["entries"])
    return entries


def intent_routes() -> list[dict[str, Any]]:
    return load_catalog("intent_routes")["entries"]


def route_index() -> dict[str, dict[str, Any]]:
    return {route["route_id"]: route for route in intent_routes()}


def _catalog_digest() -> str:
    digest = hashlib.sha1()
    for name in CATALOG_FILENAMES:
        path = _catalog_path(name)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _capability_cache_root(workspace: str | Path) -> Path:
    workspace_path = Path(workspace).expanduser().resolve()
    return state_root() / "cache" / "context" / f"{slugify(workspace_path.name)}--{workspace_hash(workspace_path)}"


def context_cache_paths(workspace: str | Path) -> dict[str, Path]:
    root = _capability_cache_root(workspace)
    return {
        "root": root,
        "manifest": root / "index_manifest.json",
        "workspace_context": root / "workspace_context.json",
        "module_map": root / "module_map.json",
        "structure_index": root / "structure_index.json",
        "context_store": root / "context_store.sqlite",
        "chunk_summaries": root / "chunk_summaries.jsonl",
        "semantic_units": root / "semantic_units.jsonl",
        "semantic_index": root / "semantic_index.sqlite",
        "semantic_manifest": root / "semantic_manifest.json",
        "usage": root / "usage.json",
    }


def load_structure_index(paths: dict[str, Path]) -> dict[str, Any]:
    payload = load_json(paths["structure_index"], default={})
    return payload if isinstance(payload, dict) else {}


def _read_legacy_chunk_records(cache_paths: dict[str, Path]) -> list[dict[str, Any]]:
    return load_jsonl(cache_paths["chunk_summaries"])


def _materialize_context_store_from_legacy(
    cache_paths: dict[str, Path],
    *,
    structure_index: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    modules = (load_json(cache_paths["module_map"], default={"modules": []}) or {}).get("modules") or []
    files = list((structure_index or {}).get("files") or [])
    chunks = _read_legacy_chunk_records(cache_paths)
    if modules or files or chunks:
        replace_context_records(
            cache_paths["context_store"],
            modules=modules,
            files=files,
            chunks=chunks,
        )
        return modules, chunks, True
    return modules, chunks, False


def _load_context_store_records(
    cache_paths: dict[str, Path],
    *,
    structure_index: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    store_path = cache_paths["context_store"]
    modules = load_context_store_modules(store_path)
    chunks = load_context_store_chunks(store_path)
    if modules or chunks:
        return modules, chunks, "sqlite"
    legacy_modules, legacy_chunks, migrated = _materialize_context_store_from_legacy(
        cache_paths,
        structure_index=structure_index,
    )
    if migrated:
        return legacy_modules, legacy_chunks, "sqlite-migrated"
    return legacy_modules, legacy_chunks, "sqlite-empty"


def _prune_context_pack_cache_entries(
    cache_paths: dict[str, Path],
    chunks: list[dict[str, Any]],
    catalog_digest: str,
    *,
    full_reset_reason: str | None = None,
) -> tuple[int, str | None]:
    entries = list_query_cache_entries(cache_paths["context_store"], cache_kind=QUERY_CACHE_CONTEXT_PACK_KIND)
    if full_reset_reason:
        replace_query_cache_entries(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_CONTEXT_PACK_KIND,
            entries=[],
            limit=40,
        )
        return len(entries), full_reset_reason if entries else None
    chunk_hashes = {chunk["path"]: chunk["hash"] for chunk in chunks}
    retained: list[dict[str, Any]] = []
    pruned = 0
    prune_reason: str | None = None
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("catalog_digest") != catalog_digest:
            pruned += 1
            prune_reason = prune_reason or "catalog-digest"
            continue
        if int(payload.get("schema_version") or 0) < 8:
            pruned += 1
            prune_reason = prune_reason or "cache-schema"
            continue
        source_hashes = entry.get("source_hashes") or {}
        if source_hashes and any(chunk_hashes.get(path) != expected_hash for path, expected_hash in source_hashes.items()):
            pruned += 1
            prune_reason = prune_reason or "source-hash-drift"
            continue
        retained.append(entry)
    if pruned:
        replace_query_cache_entries(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_CONTEXT_PACK_KIND,
            entries=retained,
            limit=40,
        )
    return pruned, prune_reason


def _prune_runtime_preflight_cache_entries(
    cache_paths: dict[str, Path],
    chunks: list[dict[str, Any]],
    catalog_digest: str,
    *,
    full_reset_reason: str | None = None,
) -> tuple[int, str | None]:
    entries = list_query_cache_entries(cache_paths["context_store"], cache_kind=QUERY_CACHE_RUNTIME_PREFLIGHT_KIND)
    if full_reset_reason:
        replace_query_cache_entries(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_RUNTIME_PREFLIGHT_KIND,
            entries=[],
            limit=40,
        )
        return len(entries), full_reset_reason if entries else None
    chunk_hashes = {chunk["path"]: chunk["hash"] for chunk in chunks}
    retained: list[dict[str, Any]] = []
    pruned = 0
    prune_reason: str | None = None
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("catalog_digest") != catalog_digest:
            pruned += 1
            prune_reason = prune_reason or "catalog-digest"
            continue
        if int(payload.get("schema_version") or 0) < 2:
            pruned += 1
            prune_reason = prune_reason or "cache-schema"
            continue
        source_hashes = entry.get("source_hashes") or {}
        if source_hashes and any(chunk_hashes.get(path) != expected_hash for path, expected_hash in source_hashes.items()):
            pruned += 1
            prune_reason = prune_reason or "source-hash-drift"
            continue
        retained.append(entry)
    if pruned:
        replace_query_cache_entries(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_RUNTIME_PREFLIGHT_KIND,
            entries=retained,
            limit=40,
        )
    return pruned, prune_reason


def _prune_task_retrieval_cache_entries(
    cache_paths: dict[str, Path],
    chunks: list[dict[str, Any]],
    catalog_digest: str,
    *,
    full_reset_reason: str | None = None,
) -> tuple[int, str | None]:
    entries = list_query_cache_entries(cache_paths["context_store"], cache_kind=QUERY_CACHE_TASK_RETRIEVAL_KIND)
    if full_reset_reason:
        replace_query_cache_entries(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_TASK_RETRIEVAL_KIND,
            entries=[],
            limit=24,
        )
        return len(entries), full_reset_reason if entries else None
    chunk_hashes = {chunk["path"]: chunk["hash"] for chunk in chunks}
    retained: list[dict[str, Any]] = []
    pruned = 0
    prune_reason: str | None = None
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("catalog_digest") != catalog_digest:
            pruned += 1
            prune_reason = prune_reason or "catalog-digest"
            continue
        if int(payload.get("schema_version") or 0) < 1:
            pruned += 1
            prune_reason = prune_reason or "cache-schema"
            continue
        source_hashes = entry.get("source_hashes") or {}
        if source_hashes and any(chunk_hashes.get(path) != expected_hash for path, expected_hash in source_hashes.items()):
            pruned += 1
            prune_reason = prune_reason or "source-hash-drift"
            continue
        retained.append(entry)
    if pruned:
        replace_query_cache_entries(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_TASK_RETRIEVAL_KIND,
            entries=retained,
            limit=24,
        )
    return pruned, prune_reason


def _runtime_auxiliary_cache_current(
    cache_paths: dict[str, Path],
    *,
    workspace_fingerprint: str | None,
    catalog_digest: str,
) -> bool:
    if not workspace_fingerprint:
        return False
    ownership_entries = list_query_cache_entries(
        cache_paths["context_store"],
        cache_kind=QUERY_CACHE_OWNERSHIP_GRAPH_KIND,
    )
    if len(ownership_entries) != 1:
        return False
    ownership_entry = ownership_entries[0]
    ownership_payload = ownership_entry.get("payload") or {}
    if (
        ownership_entry.get("workspace_fingerprint") != workspace_fingerprint
        or ownership_entry.get("catalog_digest") != catalog_digest
        or int(ownership_payload.get("schema_version") or 0) < OWNERSHIP_GRAPH_SCHEMA_VERSION
    ):
        return False
    route_entries = list_query_cache_entries(
        cache_paths["context_store"],
        cache_kind=QUERY_CACHE_ROUTE_SHORTLIST_KIND,
    )
    route_index = {str(entry.get("cache_key") or ""): entry for entry in route_entries}
    for route_id in ROUTE_PROFILES:
        entry = route_index.get(route_id)
        if entry is None:
            return False
        payload = entry.get("payload") or {}
        if (
            entry.get("workspace_fingerprint") != workspace_fingerprint
            or entry.get("catalog_digest") != catalog_digest
            or int(payload.get("schema_version") or 0) < ROUTE_SHORTLIST_SCHEMA_VERSION
        ):
            return False
    return True


def _ensure_runtime_auxiliary_cache_entries(
    workspace: Path,
    *,
    cache_paths: dict[str, Path],
    workspace_context: dict[str, Any],
    catalog_digest: str,
    structure_index: dict[str, Any] | None = None,
    chunk_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    workspace_fingerprint = str(workspace_context.get("workspace_fingerprint") or "").strip()
    if _runtime_auxiliary_cache_current(
        cache_paths,
        workspace_fingerprint=workspace_fingerprint,
        catalog_digest=catalog_digest,
    ):
        ownership_entries = list_query_cache_entries(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_OWNERSHIP_GRAPH_KIND,
        )
        ownership_payload = (ownership_entries[0].get("payload") or {}) if ownership_entries else {}
        return {
            "status": "fresh",
            "route_projection_count": len(ROUTE_PROFILES),
            "ownership_graph_path_count": int(ownership_payload.get("path_count") or 0),
        }
    resolved_structure_index = structure_index or load_structure_index(cache_paths) or _empty_structure_index(workspace)
    resolved_file_records = [
        record for record in (resolved_structure_index.get("files") or []) if isinstance(record, dict) and record.get("path")
    ]
    if chunk_records is None:
        _modules, resolved_chunk_records, _backend = _load_context_store_records(
            cache_paths,
            structure_index=resolved_structure_index,
        )
    else:
        resolved_chunk_records = chunk_records
    ownership_graph = build_ownership_graph(file_records=resolved_file_records)
    created_at = now_iso()
    replace_query_cache_entries(
        cache_paths["context_store"],
        cache_kind=QUERY_CACHE_OWNERSHIP_GRAPH_KIND,
        entries=[
            {
                "cache_key": workspace_fingerprint or "current",
                "route_id": None,
                "workspace_fingerprint": workspace_fingerprint,
                "catalog_digest": catalog_digest,
                "semantic_mode": None,
                "created_at": created_at,
                "source_hashes": {},
                "payload": ownership_graph,
            }
        ],
        limit=1,
    )
    route_projections = build_route_shortlist_projections(
        workspace=workspace,
        chunk_records=resolved_chunk_records,
        file_records=resolved_file_records,
        route_profiles=ROUTE_PROFILES,
        workspace_context=workspace_context,
        ownership_graph=ownership_graph,
    )
    replace_query_cache_entries(
        cache_paths["context_store"],
        cache_kind=QUERY_CACHE_ROUTE_SHORTLIST_KIND,
        entries=[
            {
                "cache_key": str(projection.get("route_id") or ""),
                "route_id": projection.get("route_id"),
                "workspace_fingerprint": workspace_fingerprint,
                "catalog_digest": catalog_digest,
                "semantic_mode": None,
                "created_at": created_at,
                "source_hashes": projection.get("source_hashes") or {},
                "payload": projection,
            }
            for projection in route_projections
            if projection.get("route_id")
        ],
        limit=max(len(route_projections), 1),
    )
    return {
        "status": "rebuilt",
        "route_projection_count": len(route_projections),
        "ownership_graph_path_count": int(ownership_graph.get("path_count") or 0),
    }


def _empty_structure_index(workspace: Path) -> dict[str, Any]:
    return {
        "schema_version": STRUCTURE_INDEX_SCHEMA_VERSION,
        "workspace_path": str(workspace),
        "workspace_fingerprint": None,
        "generated_at": None,
        "parser_backends": {},
        "summary": {
            "module_count": 0,
            "file_count": 0,
            "indexed_file_count": 0,
            "chunk_counts": {},
            "large_file_count": 0,
            "file_hotspot_count": 0,
            "module_hotspot_count": 0,
        },
        "incremental_indexing": {},
        "modules": [],
        "files": [],
        "hotspots": [],
    }


def _git_dirty_digest(git_state: dict[str, Any]) -> str:
    changed_paths = sorted(entry["path"] for entry in git_state.get("changed_files", []))
    return short_hash("|".join(changed_paths), length=10)


def _path_tokens(path: Path) -> list[str]:
    return tokenize_text(" ".join(path.parts))


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


def _synthetic_mtime_ns(*values: Any) -> int:
    return int(hashlib.sha1("|".join(str(value or "") for value in values).encode("utf-8")).hexdigest()[:12], 16)


def _project_note_candidates(workspace: Path) -> list[dict[str, Any]]:
    from agentiux_dev_memory import get_project_note, list_project_notes

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
                "mtime_ns": _synthetic_mtime_ns(
                    note.get("updated_at"),
                    note.get("latest_revision_id"),
                    note.get("status"),
                    note.get("pin_state"),
                ),
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
    summary = normalize_command_phrase(
        f"Project memory note {note.get('title') or note.get('note_id')}. "
        f"Status: {note.get('status') or 'active'}. Pin: {note.get('pin_state') or 'normal'}. "
        f"Tags: {', '.join(note.get('tags') or []) or 'none'}."
    )
    return {
        "chunk_id": short_hash(f"{candidate['path']}:{file_hash}", length=16),
        "chunk_kind": "project_memory",
        "path": str(candidate["path"]),
        "module_id": None,
        "language": "markdown",
        "symbols": [note.get("title") or note.get("note_id")],
        "tags": tags,
        "summary": summary,
        "hash": file_hash,
        "dependencies": [],
        "dependency_targets": [],
        "route_hints": _infer_route_hints(str(candidate["path"]), tags, summary),
        "anchor_title": note.get("title") or note.get("note_id"),
        "anchor_kind": "project_note",
        "line_start": 1,
        "line_end": max(text.count("\n") + 1, 1),
        "section_level": None,
        "hotspot_labels": [],
        "source_kind": "project_memory",
        "note_id": note.get("note_id"),
        "note_status": note.get("status"),
        "pin_state": note.get("pin_state"),
    }


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


def _candidate_paths_digest(snapshot: list[dict[str, Any]]) -> str:
    return short_hash("|".join(item["path"] for item in snapshot), length=10)


def _workspace_state_paths(workspace: Path) -> dict[str, str]:
    workspace_dir = state_root() / "workspaces" / f"{slugify(workspace.name)}--{workspace_hash(workspace)}"
    return {
        "workspace_state": str(workspace_dir / "workspace.json"),
        "workstreams_index": str(workspace_dir / "workstreams" / "index.json"),
        "tasks_index": str(workspace_dir / "tasks" / "index.json"),
    }


def _safe_workspace_state(workspace: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    paths = _workspace_state_paths(workspace)
    if not Path(paths["workspace_state"]).exists():
        return None, None, None
    try:
        state = _workspace_state_from_paths(paths)
        if state is None:
            return None, None, None
        return (
            state,
            _current_workstream_record_from_paths(paths, workspace_state=state),
            _current_task_record_from_paths(paths, workspace_state=state),
        )
    except Exception:  # noqa: BLE001
        return None, None, None


def _safe_verification_context(
    workspace: Path,
    *,
    state: dict[str, Any] | None = None,
    workstream: dict[str, Any] | None = None,
) -> dict[str, Any]:
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


def _linked_worktree_count(workspace: Path) -> int:
    git_paths = _git_output_or_empty(workspace, ["git", "rev-parse", "--git-dir", "--git-common-dir"])
    lines = [line.strip() for line in git_paths.splitlines() if line.strip()]
    if len(lines) < 2:
        return 0
    git_dir = Path(lines[0])
    git_common_dir = Path(lines[1])
    if not git_dir.is_absolute():
        git_dir = (workspace / git_dir).resolve()
    else:
        git_dir = git_dir.resolve()
    if not git_common_dir.is_absolute():
        git_common_dir = (workspace / git_common_dir).resolve()
    else:
        git_common_dir = git_common_dir.resolve()
    if git_dir == git_common_dir:
        return 0
    worktrees_dir = git_common_dir / "worktrees"
    if not worktrees_dir.exists():
        return 0
    return sum(1 for candidate in worktrees_dir.iterdir() if candidate.is_dir())


def _safe_git_state(workspace: Path) -> dict[str, Any]:
    try:
        status_output = _git_output_or_empty(workspace, ["git", "status", "--porcelain", "--branch"])
        if not status_output:
            raise RuntimeError("git status returned no data")
        lines = status_output.splitlines()
        branch_status = lines[0][3:] if lines and lines[0].startswith("## ") else ""
        current_branch = None
        upstream_branch = None
        ahead_count = 0
        behind_count = 0
        if branch_status.startswith("No commits yet on "):
            current_branch = branch_status.removeprefix("No commits yet on ").strip() or None
        elif branch_status and not branch_status.startswith("HEAD"):
            branch_part, _, ahead_behind_part = branch_status.partition(" [")
            current_branch, _, upstream_branch = branch_part.partition("...")
            current_branch = current_branch or None
            upstream_branch = upstream_branch or None
            ahead_behind = ahead_behind_part.rstrip("]") if ahead_behind_part else ""
            for item in ahead_behind.split(", "):
                if item.startswith("ahead "):
                    ahead_count = int(item.removeprefix("ahead ").strip() or 0)
                elif item.startswith("behind "):
                    behind_count = int(item.removeprefix("behind ").strip() or 0)

        entries: list[dict[str, Any]] = []
        staged_files: list[str] = []
        unstaged_files: list[str] = []
        untracked_files: list[str] = []
        conflicted_files: list[str] = []
        for line in lines[1:]:
            if not line:
                continue
            status = line[:2]
            raw_path = line[3:] if len(line) > 3 else ""
            path = raw_path.split(" -> ", 1)[-1].strip()
            if status == "??":
                entry = {
                    "path": path,
                    "raw_status": status,
                    "staged_status": "?",
                    "unstaged_status": "?",
                    "staged": False,
                    "unstaged": False,
                    "untracked": True,
                    "conflicted": False,
                }
                entries.append(entry)
                untracked_files.append(path)
                continue
            staged_status = status[:1]
            unstaged_status = status[1:2]
            conflicted = status in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"} or "U" in status
            entry = {
                "path": path,
                "raw_status": status,
                "staged_status": staged_status,
                "unstaged_status": unstaged_status,
                "staged": staged_status not in {" ", "?"},
                "unstaged": unstaged_status not in {" ", "?"},
                "untracked": False,
                "conflicted": conflicted,
            }
            entries.append(entry)
            if entry["staged"]:
                staged_files.append(path)
            if entry["unstaged"]:
                unstaged_files.append(path)
            if conflicted:
                conflicted_files.append(path)
        head_commit = _git_output_or_empty(workspace, ["git", "rev-parse", "--short", "HEAD"]) or None
        return {
            "repo_root": str(workspace),
            "head_commit": head_commit,
            "current_branch": current_branch,
            "upstream_branch": upstream_branch,
            "ahead_count": ahead_count,
            "behind_count": behind_count,
            "dirty": bool(entries),
            "changed_files": entries,
            "staged_files": staged_files,
            "unstaged_files": unstaged_files,
            "untracked_files": untracked_files,
            "conflicted_files": conflicted_files,
            "summary_counts": {
                "changed_files": len(entries),
                "staged_files": len(staged_files),
                "unstaged_files": len(unstaged_files),
                "untracked_files": len(untracked_files),
                "conflicted_files": len(conflicted_files),
            },
            "worktree": {"linked_worktree_count": _linked_worktree_count(workspace)},
        }
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
            "worktree": {"linked_worktree_count": 0},
        }


def _package_managers(workspace: Path) -> list[str]:
    managers = [manager for filename, manager in PACKAGE_MANAGER_FILES.items() if (workspace / filename).exists()]
    if (workspace / "package.json").exists() and not managers:
        managers.append("npm")
    return sorted(dict.fromkeys(managers))


def _known_test_surfaces(workspace: Path) -> list[str]:
    package_manifest = load_json(workspace / "package.json", default={})
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
                "module_id": short_hash(relative, length=10),
                "path": relative,
                "kind": kind,
                "manifest_path": manifest_path,
                "tags": sorted(dict.fromkeys([kind, *tokenize_text(relative)])),
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
    query_tokens: list[str] | None = None,
    route_id: str | None = None,
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
    query_path_score, _ = score_token_match(query_tokens or [], _path_tokens(relative), 6)
    score += query_path_score
    if route_id:
        score += _route_profile_path_boost(relative, route_id)
    for index, recent_route_id in enumerate((recent_route_ids or [])[:2]):
        score += max(_route_profile_path_boost(relative, recent_route_id) - (index * 6), 0)
    return score


def _select_candidate_files(
    workspace: Path,
    changed_paths: list[str],
    recent_route_ids: list[str] | None = None,
    *,
    query_text: str | None = None,
    route_id: str | None = None,
    retrieval_mode: str = "orientation",
) -> list[Path]:
    query_tokens = tokenize_text(query_text)
    profile = retrieval_mode_profile(retrieval_mode)
    ranked = sorted(
        _iter_indexable_files(workspace),
        key=lambda item: (
            -_candidate_priority(
                workspace,
                item,
                set(changed_paths),
                recent_route_ids=recent_route_ids,
                query_tokens=query_tokens,
                route_id=route_id,
            ),
            str(item.relative_to(workspace)),
        ),
    )
    return ranked[: min(profile["candidate_file_limit"], CONTEXT_INDEX_MAX_FILES)]


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
    tags = [path.suffix.lower().lstrip("."), *path.parts[:3], *tokenize_text(path.stem), *tokenize_text(summary)]
    tags.extend(tokenize_text(" ".join(symbols[:4])))
    tags.extend(tokenize_text(" ".join(dependencies[:4])))
    cleaned = [tag for tag in tags if tag and tag not in {"", "."}]
    return sorted(dict.fromkeys(cleaned))


def _infer_route_hints(relative_path: str, tags: list[str], summary: str) -> list[str]:
    route_hints: list[str] = []
    content_tokens = set(tags + tokenize_text(summary))
    path_tokens = set(tokenize_text(relative_path))
    for route_id, profile in ROUTE_PROFILES.items():
        profile_tokens = set(profile.get("path_tokens", set()))
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
        return normalize_command_phrase(
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
        return normalize_command_phrase(f"{heading_match.group(1) if heading_match else path.stem}. {paragraph}")
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
        return normalize_command_phrase(f"{label} at {path.name}.{symbol_part}{dependency_part}")
    if path.suffix.lower() in {".json", ".toml", ".yaml", ".yml"}:
        keys = re.findall(r"^\s*\"?([A-Za-z0-9_.-]+)\"?\s*[:=]", text, flags=re.MULTILINE)
        return normalize_command_phrase(f"Config or manifest file at {path.name}. Keys: {', '.join(keys[:6]) or 'none'}.")
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), path.name)
    return normalize_command_phrase(f"{path.name}. {first_line}")


def _build_chunk_record(workspace: Path, path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    file_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
    relative = str(path.relative_to(workspace))
    symbols = _extract_symbols(path, text)
    dependencies = _extract_dependencies(path, text)
    summary = _summary_from_text(path, text, symbols, dependencies)
    tags = _file_tags(path.relative_to(workspace), summary, symbols, dependencies)
    return {
        "chunk_id": short_hash(f"{relative}:{file_hash}", length=16),
        "path": relative,
        "symbols": symbols,
        "tags": tags,
        "summary": summary,
        "hash": file_hash,
        "dependencies": dependencies,
        "route_hints": _infer_route_hints(relative, tags, summary),
    }


def _workspace_fingerprint(workspace: Path, chunks: list[dict[str, Any]]) -> str:
    chunk_digest = short_hash("|".join(f"{chunk['path']}:{chunk['hash']}" for chunk in chunks), length=10)
    return short_hash(f"{PLUGIN_VERSION}:{workspace}:{chunk_digest}", length=16)


def _workspace_context_payload(
    workspace: Path,
    detection: dict[str, Any],
    git_state: dict[str, Any],
    modules: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    structure_index: dict[str, Any],
    usage: dict[str, Any],
    catalog_digest: str,
    semantic_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state, workstream, task = _safe_workspace_state(workspace)
    verification = _safe_verification_context(workspace, state=state, workstream=workstream)
    current_workstream_id = (workstream or {}).get("workstream_id")
    brief_markdown = None
    brief_kind = "workstream"
    if state is not None:
        resolved_paths = workspace_paths(workspace, workstream_id=current_workstream_id)
        if state.get("workspace_mode") == "task" and (task or {}).get("task_id"):
            brief_kind = "task"
            brief_path = Path(resolved_paths["tasks_root"]) / str(task["task_id"]) / "task-brief.md"
            brief_markdown = brief_path.read_text() if brief_path.exists() else None
        elif current_workstream_id and resolved_paths.get("current_workstream_active_brief"):
            brief_path = Path(resolved_paths["current_workstream_active_brief"])
            brief_markdown = brief_path.read_text() if brief_path.exists() else None
    design_brief = read_design_brief(workspace, workstream_id=current_workstream_id) if current_workstream_id else {}
    design_handoff = read_design_handoff(workspace, workstream_id=current_workstream_id) if current_workstream_id else {}
    design_testability = _design_and_testability_summary(
        workspace,
        workstream_id=current_workstream_id,
        design_brief=design_brief,
        design_handoff=design_handoff,
        brief_markdown=brief_markdown,
        brief_kind=brief_kind,
    )
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
        "repo_maturity": detection.get("repo_maturity") or {},
        "detected_stacks": detection.get("detected_stacks", []),
        "selected_profiles": detection.get("selected_profiles", []),
        "package_managers": _package_managers(workspace),
        "top_level_modules": modules[:16],
        "current_workstream": {
            "workstream_id": workstream.get("workstream_id"),
            "title": workstream.get("title"),
            "kind": workstream.get("kind"),
            "status": workstream.get("status"),
        }
        if workstream
        else None,
        "current_task": {
            "task_id": task.get("task_id"),
            "title": task.get("title"),
            "status": task.get("status"),
        }
        if task
        else None,
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
        "design_summary": design_testability["design_summary"],
        "testability_summary": design_testability["testability_summary"],
        "structure_summary": structure_summary(structure_index),
        "hotspot_summary": hotspot_summary(structure_index),
        "semantic_summary": copy.deepcopy(semantic_summary or {}),
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


def _workspace_context_payload_from_existing(
    workspace: Path,
    *,
    detection: dict[str, Any],
    git_state: dict[str, Any],
    usage: dict[str, Any],
    catalog_digest: str,
    existing_context: dict[str, Any],
    semantic_summary: dict[str, Any],
) -> dict[str, Any]:
    state, workstream, task = _safe_workspace_state(workspace)
    verification = _safe_verification_context(workspace, state=state, workstream=workstream)
    current_workstream_id = (workstream or {}).get("workstream_id")
    brief_markdown = None
    brief_kind = "workstream"
    if state is not None:
        resolved_paths = workspace_paths(workspace, workstream_id=current_workstream_id)
        if state.get("workspace_mode") == "task" and (task or {}).get("task_id"):
            brief_kind = "task"
            brief_path = Path(resolved_paths["tasks_root"]) / str(task["task_id"]) / "task-brief.md"
            brief_markdown = brief_path.read_text() if brief_path.exists() else None
        elif current_workstream_id and resolved_paths.get("current_workstream_active_brief"):
            brief_path = Path(resolved_paths["current_workstream_active_brief"])
            brief_markdown = brief_path.read_text() if brief_path.exists() else None
    design_brief = read_design_brief(workspace, workstream_id=current_workstream_id) if current_workstream_id else {}
    design_handoff = read_design_handoff(workspace, workstream_id=current_workstream_id) if current_workstream_id else {}
    design_testability = _design_and_testability_summary(
        workspace,
        workstream_id=current_workstream_id,
        design_brief=design_brief,
        design_handoff=design_handoff,
        brief_markdown=brief_markdown,
        brief_kind=brief_kind,
    )
    changed_paths = [entry["path"] for entry in git_state.get("changed_files", [])][:24]
    top_level_modules = (existing_context.get("top_level_modules") or [])[:16]
    project_memory = copy.deepcopy(existing_context.get("project_memory") or {})
    return {
        "schema_version": CONTEXT_CACHE_SCHEMA_VERSION,
        "plugin_version": PLUGIN_VERSION,
        "catalog_digest": catalog_digest,
        "workspace_path": str(workspace),
        "workspace_label": detection.get("workspace_label"),
        "workspace_slug": detection.get("workspace_slug"),
        "workspace_hash": detection.get("workspace_hash"),
        "workspace_fingerprint": existing_context.get("workspace_fingerprint"),
        "generated_at": now_iso(),
        "state_initialized": state is not None,
        "repo_maturity": detection.get("repo_maturity") or {},
        "detected_stacks": detection.get("detected_stacks", []),
        "selected_profiles": detection.get("selected_profiles", []),
        "package_managers": _package_managers(workspace),
        "top_level_modules": top_level_modules,
        "current_workstream": {
            "workstream_id": workstream.get("workstream_id"),
            "title": workstream.get("title"),
            "kind": workstream.get("kind"),
            "status": workstream.get("status"),
        }
        if workstream
        else None,
        "current_task": {
            "task_id": task.get("task_id"),
            "title": task.get("title"),
            "status": task.get("status"),
        }
        if task
        else None,
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
        "design_summary": design_testability["design_summary"],
        "testability_summary": design_testability["testability_summary"],
        "structure_summary": copy.deepcopy(existing_context.get("structure_summary") or {}),
        "hotspot_summary": copy.deepcopy(existing_context.get("hotspot_summary") or {}),
        "semantic_summary": copy.deepcopy(semantic_summary or {}),
        "project_memory": project_memory,
        "last_used_routes": usage.get("recent_route_ids", [])[:6],
        "last_used_tools": usage.get("recent_tool_ids", [])[:12],
        "usage_stats": {
            "fresh_hit_count": usage.get("fresh_hit_count", 0),
            "refresh_count": usage.get("refresh_count", 0),
            "search_count": usage.get("search_count", 0),
            "context_pack_hit_count": usage.get("context_pack_hit_count", 0),
            "context_pack_miss_count": usage.get("context_pack_miss_count", 0),
        },
        "chunk_count": existing_context.get("chunk_count", 0),
        "module_count": existing_context.get("module_count", len(top_level_modules)),
    }


def _semantic_artifact_fingerprints(workspace: Path) -> dict[str, Any]:
    note_digest = hashlib.sha1()
    note_count = 0
    try:
        for note in pinned_project_notes(workspace):
            note_digest.update(str(note.get("note_id") or "").encode("utf-8"))
            note_digest.update(b"\0")
            note_digest.update(str(note.get("updated_at") or "").encode("utf-8"))
            note_digest.update(b"\0")
            note_digest.update(str(note.get("latest_revision_id") or "").encode("utf-8"))
            note_digest.update(b"\0")
            note_count += 1
    except FileNotFoundError:
        note_count = 0

    snapshot_digest = hashlib.sha1()
    snapshot_count = 0
    try:
        snapshot_payload = list_generated_memory_snapshots(workspace)
        items = snapshot_payload.get("items") or []
        snapshot_count = int((snapshot_payload.get("counts") or {}).get("active") or 0)
        for snapshot in items:
            snapshot_digest.update(str(snapshot.get("snapshot_id") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("title") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("status") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("confidence") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("source_audit_mode") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("source_query_text") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("source_module_path") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("updated_at") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("expires_at") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
            snapshot_digest.update(str(snapshot.get("preview") or "").encode("utf-8"))
            snapshot_digest.update(b"\0")
    except FileNotFoundError:
        snapshot_count = 0

    return {
        "note_fingerprint": note_digest.hexdigest(),
        "note_count": note_count,
        "snapshot_fingerprint": snapshot_digest.hexdigest(),
        "snapshot_count": snapshot_count,
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
        cache_paths["structure_index"],
        cache_paths["context_store"],
        cache_paths["semantic_units"],
        cache_paths["semantic_index"],
        cache_paths["semantic_manifest"],
    ]


def _load_manifest(paths: dict[str, Path]) -> dict[str, Any]:
    payload = load_json(paths["manifest"], default={})
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
    if manifest.get("dirty_digest") != _git_dirty_digest(git_state):
        reasons.append("dirty-digest")
    if manifest.get("candidate_paths_digest") != _candidate_paths_digest(candidate_snapshot):
        reasons.append("candidate-set")
    if manifest.get("indexed_file_snapshot") != candidate_snapshot:
        reasons.append("indexed-file-snapshot")
    for cache_file in _required_cache_files(context_cache_paths(workspace)):
        if not cache_file.exists():
            reasons.append(f"missing-cache:{cache_file.name}")
    return reasons


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
        if not isinstance(source_hashes, dict):
            pruned += 1
            continue
        if source_hashes and any(chunk_hashes.get(path) != expected_hash for path, expected_hash in source_hashes.items()):
            pruned += 1
            continue
        kept.append(entry)
    reason = "source-hash-drift" if pruned else None
    return kept, pruned, reason


def _usage_stats_payload() -> dict[str, Any]:
    return {
        "refresh_count": 0,
        "fresh_hit_count": 0,
        "search_count": 0,
        "context_pack_hit_count": 0,
        "context_pack_miss_count": 0,
        "last_refresh_candidate_file_count": 0,
        "last_refresh_rebuilt_file_count": 0,
        "last_refresh_reused_file_count": 0,
        "last_refresh_removed_file_count": 0,
        "last_refresh_rebuilt_chunk_count": 0,
        "last_refresh_reused_chunk_count": 0,
        "last_refresh_bounded_read_count": 0,
        "last_refresh_full_read_count": 0,
        "last_refresh_large_file_count": 0,
        "last_refresh_parser_backend_status": {},
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
        "last_high_confidence_request_text": None,
        "last_high_confidence_route_id": None,
        "last_high_confidence_confidence": 0.0,
        "last_high_confidence_recorded_at": None,
        "refresh_reason_counts": {},
        "route_resolution_counts": {},
    }


def _default_usage_payload() -> dict[str, Any]:
    payload = {
        "recent_route_ids": [],
        "recent_tool_ids": [],
        "updated_at": None,
    }
    payload.update(_usage_stats_payload())
    return payload


def load_usage(paths: dict[str, Path]) -> dict[str, Any]:
    payload = load_json(paths["usage"], default={})
    merged = _default_usage_payload()
    if isinstance(payload, dict):
        merged.update(payload)
    for field in ("refresh_reason_counts", "route_resolution_counts"):
        if not isinstance(merged.get(field), dict):
            merged[field] = {}
    if not isinstance(merged.get("last_refresh_parser_backend_status"), dict):
        merged["last_refresh_parser_backend_status"] = {}
    for field in ("recent_route_ids", "recent_tool_ids", "last_refresh_stale_reasons"):
        if not isinstance(merged.get(field), list):
            merged[field] = []
    for field in (
        "last_high_confidence_request_text",
        "last_high_confidence_route_id",
        "last_high_confidence_recorded_at",
    ):
        if merged.get(field) is not None and not isinstance(merged.get(field), str):
            merged[field] = None
    try:
        merged["last_high_confidence_confidence"] = round(float(merged.get("last_high_confidence_confidence") or 0.0), 2)
    except (TypeError, ValueError):
        merged["last_high_confidence_confidence"] = 0.0
    return merged


def _write_usage_payload(paths: dict[str, Path], usage: dict[str, Any]) -> dict[str, Any]:
    usage["updated_at"] = now_iso()
    write_json(paths["usage"], usage)
    return usage


def _bump_usage_counter(usage: dict[str, Any], field: str, key: str) -> None:
    counter = usage.setdefault(field, {})
    counter[key] = int(counter.get(key, 0)) + 1


def record_usage(paths: dict[str, Path], route_id: str | None, tool_ids: list[str]) -> dict[str, Any]:
    usage = load_usage(paths)
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
    rebuilt_file_count: int,
    reused_file_count: int,
    removed_file_count: int,
    rebuilt_chunk_count: int,
    reused_chunk_count: int,
    bounded_read_count: int,
    full_read_count: int,
    large_file_count: int,
    parser_backend_status: dict[str, Any],
) -> dict[str, Any]:
    usage = load_usage(paths)
    usage["refresh_count"] = int(usage.get("refresh_count", 0)) + 1
    if status == "fresh":
        usage["fresh_hit_count"] = int(usage.get("fresh_hit_count", 0)) + 1
    usage["last_refresh_status"] = status
    usage["last_refresh_reason"] = reason
    usage["last_refresh_stale_reasons"] = stale_reasons[:8]
    usage["last_refresh_candidate_file_count"] = candidate_file_count
    usage["last_refresh_rebuilt_file_count"] = rebuilt_file_count
    usage["last_refresh_reused_file_count"] = reused_file_count
    usage["last_refresh_removed_file_count"] = removed_file_count
    usage["last_refresh_rebuilt_chunk_count"] = rebuilt_chunk_count
    usage["last_refresh_reused_chunk_count"] = reused_chunk_count
    usage["last_refresh_bounded_read_count"] = bounded_read_count
    usage["last_refresh_full_read_count"] = full_read_count
    usage["last_refresh_large_file_count"] = large_file_count
    usage["last_refresh_parser_backend_status"] = parser_backend_status
    usage["last_refresh_duration_ms"] = duration_ms
    _bump_usage_counter(usage, "refresh_reason_counts", reason)
    return _write_usage_payload(paths, usage)


def record_search_stats(
    paths: dict[str, Path],
    *,
    query_text: str,
    match_count: int,
    route_status: str,
    route_id: str | None,
) -> dict[str, Any]:
    usage = load_usage(paths)
    usage["search_count"] = int(usage.get("search_count", 0)) + 1
    usage["last_search_match_count"] = match_count
    usage["last_search_query"] = query_text
    usage["last_search_route_status"] = route_status
    usage["last_search_route_id"] = route_id
    _bump_usage_counter(usage, "route_resolution_counts", route_status)
    return _write_usage_payload(paths, usage)


def record_context_pack_stats(
    paths: dict[str, Path],
    *,
    cache_status: str,
    route_status: str,
    route_id: str | None,
    selected_tool_count: int,
) -> dict[str, Any]:
    usage = load_usage(paths)
    if cache_status in {"hit", "task-hit"}:
        usage["context_pack_hit_count"] = int(usage.get("context_pack_hit_count", 0)) + 1
    elif cache_status == "miss":
        usage["context_pack_miss_count"] = int(usage.get("context_pack_miss_count", 0)) + 1
    usage["last_context_pack_status"] = cache_status
    usage["last_context_pack_route_status"] = route_status
    usage["last_context_pack_route_id"] = route_id
    usage["last_context_pack_selected_tool_count"] = selected_tool_count
    _bump_usage_counter(usage, "route_resolution_counts", route_status)
    return _write_usage_payload(paths, usage)


def record_high_confidence_runtime_context(
    paths: dict[str, Path],
    *,
    request_text: str | None,
    route_id: str | None,
    route_status: str,
    confidence: float | int | None,
) -> dict[str, Any]:
    normalized_request = " ".join(str(request_text or "").split())
    confidence_value = round(float(confidence or 0.0), 2)
    if (
        not normalized_request
        or not route_id
        or route_status != "exact"
        or confidence_value < PERSISTED_RUNTIME_CONTEXT_CONFIDENCE_MIN
    ):
        return load_usage(paths)
    usage = load_usage(paths)
    usage["last_high_confidence_request_text"] = normalized_request
    usage["last_high_confidence_route_id"] = route_id
    usage["last_high_confidence_confidence"] = confidence_value
    usage["last_high_confidence_recorded_at"] = now_iso()
    return _write_usage_payload(paths, usage)


def compact_workspace_context(workspace_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_fingerprint": workspace_context.get("workspace_fingerprint"),
        "catalog_digest": workspace_context.get("catalog_digest"),
        "workspace_label": workspace_context.get("workspace_label"),
        "state_initialized": workspace_context.get("state_initialized"),
        "repo_maturity": workspace_context.get("repo_maturity") or {},
        "detected_stacks": (workspace_context.get("detected_stacks") or [])[:8],
        "selected_profiles": (workspace_context.get("selected_profiles") or [])[:8],
        "package_managers": (workspace_context.get("package_managers") or [])[:4],
        "changed_paths": (workspace_context.get("changed_paths") or [])[:12],
        "current_workstream": workspace_context.get("current_workstream"),
        "current_task": workspace_context.get("current_task"),
        "known_test_surfaces": (workspace_context.get("known_test_surfaces") or [])[:6],
        "design_summary": workspace_context.get("design_summary") or {},
        "testability_summary": workspace_context.get("testability_summary") or {},
        "structure_summary": workspace_context.get("structure_summary") or {},
        "hotspot_summary": workspace_context.get("hotspot_summary") or {},
        "semantic_summary": workspace_context.get("semantic_summary") or {},
        "project_memory": workspace_context.get("project_memory") or {},
        "last_used_routes": (workspace_context.get("last_used_routes") or [])[:4],
        "last_used_tools": (workspace_context.get("last_used_tools") or [])[:8],
        "usage_stats": workspace_context.get("usage_stats") or {},
        "module_count": workspace_context.get("module_count"),
        "chunk_count": workspace_context.get("chunk_count"),
    }


def _chunk_records_by_path(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["path"])].append(record)
    return grouped


def _file_records_by_path(structure_index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(record["path"]): record
        for record in (structure_index.get("files") or [])
        if isinstance(record, dict) and record.get("path")
    }


def _module_id_for_candidate(workspace: Path, candidate: Path, modules: list[dict[str, Any]]) -> str | None:
    best_match: tuple[int, str] | None = None
    relative = candidate.relative_to(workspace)
    for module in modules:
        module_path = Path(str(module.get("path") or "."))
        if module_path == Path("."):
            score = 0
        elif relative == module_path or module_path in relative.parents:
            score = len(module_path.parts)
        else:
            continue
        module_id = module.get("module_id")
        if not module_id:
            continue
        if best_match is None or score > best_match[0]:
            best_match = (score, str(module_id))
    return best_match[1] if best_match else None


def _parser_backend_status_payload(parser_backends: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "status": details.get("status"),
            "available": details.get("available"),
            "source": details.get("source"),
            "reason": details.get("reason"),
        }
        for name, details in parser_backends.items()
        if isinstance(details, dict)
    }


def _incremental_indexing_payload(
    *,
    candidate_file_count: int,
    rebuilt_file_count: int,
    reused_file_count: int,
    removed_file_count: int,
    rebuilt_chunk_count: int,
    reused_chunk_count: int,
    bounded_read_count: int,
    full_read_count: int,
    large_file_count: int,
    refresh_reason: str,
    stale_reasons: list[str],
    parser_backend_status: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at": now_iso(),
        "candidate_file_count": candidate_file_count,
        "rebuilt_file_count": rebuilt_file_count,
        "reused_file_count": reused_file_count,
        "removed_file_count": removed_file_count,
        "rebuilt_chunk_count": rebuilt_chunk_count,
        "reused_chunk_count": reused_chunk_count,
        "bounded_read_count": bounded_read_count,
        "full_read_count": full_read_count,
        "large_file_count": large_file_count,
        "refresh_reason": refresh_reason,
        "stale_reasons": stale_reasons[:8],
        "parser_backend_status": parser_backend_status,
    }


def _refresh_semantic_context(
    workspace: Path,
    *,
    cache_paths: dict[str, Path],
    modules: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    structure_index: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    result = refresh_semantic_index(
        workspace,
        cache_paths=cache_paths,
        modules=modules,
        chunks=chunks,
        structure_index=structure_index,
        force=force,
    )
    manifest = load_semantic_manifest(cache_paths)
    return {
        **result,
        "semantic_summary": semantic_summary_from_manifest(manifest),
    }


def refresh_context_index(
    workspace: str | Path,
    force: bool = False,
    *,
    query_text: str | None = None,
    route_id: str | None = None,
    retrieval_mode: str | None = None,
) -> dict[str, Any]:
    started_at = perf_counter()
    resolved_workspace = Path(workspace).expanduser().resolve()
    cache_paths = context_cache_paths(resolved_workspace)
    git_state = _safe_git_state(resolved_workspace)
    usage = load_usage(cache_paths)
    resolved_mode = retrieval_mode or infer_retrieval_mode(query_text)
    candidate_files = _select_candidate_files(
        resolved_workspace,
        [entry["path"] for entry in git_state.get("changed_files", [])],
        recent_route_ids=usage.get("recent_route_ids", []),
        query_text=query_text,
        route_id=route_id,
        retrieval_mode=resolved_mode,
    )
    project_note_candidates = _project_note_candidates(resolved_workspace)
    candidate_snapshot = _indexed_file_snapshot(resolved_workspace, [*candidate_files, *project_note_candidates])
    candidate_count = len(candidate_snapshot)
    catalog_digest = _catalog_digest()
    workspace_context = load_json(cache_paths["workspace_context"], default={})
    store_summary = context_store_summary(cache_paths["context_store"])
    manifest = _load_manifest(cache_paths)
    semantic_manifest = load_semantic_manifest(cache_paths)
    semantic_summary = semantic_summary_from_manifest(semantic_manifest)
    semantic_artifact_state = _semantic_artifact_fingerprints(resolved_workspace)
    semantic_manifest_stale = (
        semantic_manifest.get("note_fingerprint") != semantic_artifact_state["note_fingerprint"]
        or semantic_manifest.get("snapshot_fingerprint") != semantic_artifact_state["snapshot_fingerprint"]
    )
    stale_reasons = _manifest_stale_reasons(
        manifest,
        workspace=resolved_workspace,
        git_state=git_state,
        candidate_snapshot=candidate_snapshot,
        catalog_digest=catalog_digest,
    )
    if force:
        stale_reasons = ["force-refresh", *stale_reasons]
    storage_backend_status = "sqlite" if any(store_summary[key] for key in ("module_count", "file_count", "chunk_count")) else "sqlite-empty"

    if not stale_reasons:
        semantic_result: dict[str, Any] | None = None
        if semantic_manifest_stale:
            existing_structure_index = load_structure_index(cache_paths) or _empty_structure_index(resolved_workspace)
            existing_modules, existing_chunk_records, _storage_backend = _load_context_store_records(
                cache_paths,
                structure_index=existing_structure_index,
            )
            modules = existing_modules or (load_json(cache_paths["module_map"], default={"modules": []}) or {}).get("modules", [])
            semantic_result = _refresh_semantic_context(
                resolved_workspace,
                cache_paths=cache_paths,
                modules=modules,
                chunks=existing_chunk_records,
                structure_index=existing_structure_index,
                force=False,
            )
            semantic_summary = semantic_result["semantic_summary"]
        if workspace_context.get("semantic_summary") != semantic_summary:
            workspace_context["semantic_summary"] = semantic_summary
            write_json(cache_paths["workspace_context"], workspace_context)
        auxiliary_cache = _ensure_runtime_auxiliary_cache_entries(
            resolved_workspace,
            cache_paths=cache_paths,
            workspace_context=workspace_context,
            catalog_digest=catalog_digest,
        )
        duration_ms = int((perf_counter() - started_at) * 1000)
        parser_status = copy.deepcopy(usage.get("last_refresh_parser_backend_status") or {})
        structure_summary_payload = copy.deepcopy(workspace_context.get("structure_summary") or {})
        hotspot_summary_payload = copy.deepcopy(workspace_context.get("hotspot_summary") or {})
        reused_file_count = int(structure_summary_payload.get("file_count") or 0)
        large_file_count = int(structure_summary_payload.get("large_file_count") or 0)
        module_count = int(workspace_context.get("module_count") or store_summary["module_count"] or 0)
        chunk_count = int(workspace_context.get("chunk_count") or store_summary["chunk_count"] or 0)
        _record_refresh_stats(
            cache_paths,
            status="fresh",
            duration_ms=duration_ms,
            reason="manifest-match",
            stale_reasons=[],
            candidate_file_count=candidate_count,
            rebuilt_file_count=0,
            reused_file_count=reused_file_count,
            removed_file_count=0,
            rebuilt_chunk_count=0,
            reused_chunk_count=chunk_count,
            bounded_read_count=0,
            full_read_count=0,
            large_file_count=large_file_count,
            parser_backend_status=parser_status,
        )
        return {
            "status": "fresh",
            "workspace_path": str(resolved_workspace),
            "retrieval": retrieval_policy_payload("refresh_context_index", resolved_mode),
            "cache_root": str(cache_paths["root"]),
            "workspace_context_path": str(cache_paths["workspace_context"]),
            "module_map_path": str(cache_paths["module_map"]),
            "structure_index_path": str(cache_paths["structure_index"]),
            "context_store_path": str(cache_paths["context_store"]),
            "storage_backend": "sqlite",
            "storage_backend_status": storage_backend_status,
            "storage_summary": store_summary,
            "semantic_units_path": str(cache_paths["semantic_units"]),
            "semantic_index_path": str(cache_paths["semantic_index"]),
            "semantic_manifest_path": str(cache_paths["semantic_manifest"]),
            "workspace_fingerprint": workspace_context.get("workspace_fingerprint"),
            "catalog_digest": catalog_digest,
            "candidate_file_count": candidate_count,
            "module_count": module_count,
            "chunk_count": chunk_count,
            "structure_summary": structure_summary_payload,
            "hotspot_summary": hotspot_summary_payload,
            "semantic_summary": semantic_summary,
            "rebuilt_file_count": 0,
            "reused_file_count": reused_file_count,
            "removed_file_count": 0,
            "rebuilt_chunk_count": 0,
            "reused_chunk_count": chunk_count,
            "bounded_read_count": 0,
            "full_read_count": 0,
            "large_file_count": large_file_count,
            "parser_backend_status": parser_status,
            "semantic_backend_status": semantic_summary.get("backend_status"),
            "semantic_refresh_status": (semantic_result or {}).get("status", "fresh"),
            "semantic_rebuilt_unit_count": (semantic_result or {}).get("rebuilt_unit_count", 0),
            "semantic_reused_unit_count": (semantic_result or {}).get("reused_unit_count", int(semantic_summary.get("unit_count") or 0)),
            "semantic_removed_unit_count": (semantic_result or {}).get("removed_unit_count", 0),
            "pruned_semantic_cache_entries": 0,
            "pruned_semantic_cache_reason": None,
            "runtime_projection_status": auxiliary_cache["status"],
            "route_projection_count": auxiliary_cache["route_projection_count"],
            "ownership_graph_path_count": auxiliary_cache["ownership_graph_path_count"],
            "refresh_reason": "manifest-match",
            "stale_reasons": [],
            "rebuilt_paths": [],
        }

    if not force and stale_reasons and not _requires_chunk_refresh(stale_reasons):
        detection = detect_workspace(resolved_workspace)
        parser_status = copy.deepcopy(usage.get("last_refresh_parser_backend_status") or {})
        semantic_result: dict[str, Any] | None = None
        if semantic_manifest_stale:
            existing_structure_index = load_structure_index(cache_paths) or _empty_structure_index(resolved_workspace)
            existing_modules, existing_chunk_records, _storage_backend = _load_context_store_records(
                cache_paths,
                structure_index=existing_structure_index,
            )
            modules = existing_modules or (load_json(cache_paths["module_map"], default={"modules": []}) or {}).get("modules", [])
            semantic_result = _refresh_semantic_context(
                resolved_workspace,
                cache_paths=cache_paths,
                modules=modules,
                chunks=existing_chunk_records,
                structure_index=existing_structure_index,
                force=False,
            )
            semantic_summary = semantic_result["semantic_summary"]
        workspace_context = _workspace_context_payload_from_existing(
            resolved_workspace,
            detection=detection,
            git_state=git_state,
            usage=usage,
            catalog_digest=catalog_digest,
            existing_context=workspace_context,
            semantic_summary=semantic_summary,
        )
        write_json(cache_paths["workspace_context"], workspace_context)
        write_json(
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
                "module_count": int(workspace_context.get("module_count") or store_summary["module_count"] or 0),
                "chunk_count": int(workspace_context.get("chunk_count") or store_summary["chunk_count"] or 0),
                "generated_at": now_iso(),
            },
        )
        duration_ms = int((perf_counter() - started_at) * 1000)
        auxiliary_cache = _ensure_runtime_auxiliary_cache_entries(
            resolved_workspace,
            cache_paths=cache_paths,
            workspace_context=workspace_context,
            catalog_digest=catalog_digest,
        )
        refresh_reason = stale_reasons[0]
        reused_file_count = int((workspace_context.get("structure_summary") or {}).get("file_count") or 0)
        large_file_count = int((workspace_context.get("structure_summary") or {}).get("large_file_count") or 0)
        chunk_count = int(workspace_context.get("chunk_count") or store_summary["chunk_count"] or 0)
        module_count = int(workspace_context.get("module_count") or store_summary["module_count"] or 0)
        _record_refresh_stats(
            cache_paths,
            status="context-refreshed",
            duration_ms=duration_ms,
            reason=refresh_reason,
            stale_reasons=stale_reasons,
            candidate_file_count=candidate_count,
            rebuilt_file_count=0,
            reused_file_count=reused_file_count,
            removed_file_count=0,
            rebuilt_chunk_count=0,
            reused_chunk_count=chunk_count,
            bounded_read_count=0,
            full_read_count=0,
            large_file_count=large_file_count,
            parser_backend_status=parser_status,
        )
        return {
            "status": "context-refreshed",
            "workspace_path": str(resolved_workspace),
            "retrieval": retrieval_policy_payload("refresh_context_index", resolved_mode),
            "cache_root": str(cache_paths["root"]),
            "workspace_context_path": str(cache_paths["workspace_context"]),
            "module_map_path": str(cache_paths["module_map"]),
            "structure_index_path": str(cache_paths["structure_index"]),
            "context_store_path": str(cache_paths["context_store"]),
            "storage_backend": "sqlite",
            "storage_backend_status": storage_backend_status,
            "storage_summary": store_summary,
            "semantic_units_path": str(cache_paths["semantic_units"]),
            "semantic_index_path": str(cache_paths["semantic_index"]),
            "semantic_manifest_path": str(cache_paths["semantic_manifest"]),
            "workspace_fingerprint": workspace_context["workspace_fingerprint"],
            "catalog_digest": catalog_digest,
            "candidate_file_count": candidate_count,
            "module_count": module_count,
            "chunk_count": chunk_count,
            "structure_summary": workspace_context.get("structure_summary") or {},
            "hotspot_summary": workspace_context.get("hotspot_summary") or {},
            "semantic_summary": semantic_summary,
            "rebuilt_file_count": 0,
            "reused_file_count": reused_file_count,
            "removed_file_count": 0,
            "rebuilt_chunk_count": 0,
            "reused_chunk_count": chunk_count,
            "bounded_read_count": 0,
            "full_read_count": 0,
            "large_file_count": large_file_count,
            "parser_backend_status": parser_status,
            "semantic_backend_status": semantic_summary.get("backend_status"),
            "semantic_refresh_status": (semantic_result or {}).get("status", "fresh"),
            "semantic_rebuilt_unit_count": (semantic_result or {}).get("rebuilt_unit_count", 0),
            "semantic_reused_unit_count": (semantic_result or {}).get("reused_unit_count", int(semantic_summary.get("unit_count") or 0)),
            "semantic_removed_unit_count": (semantic_result or {}).get("removed_unit_count", 0),
            "pruned_semantic_cache_entries": 0,
            "pruned_semantic_cache_reason": None,
            "runtime_projection_status": auxiliary_cache["status"],
            "route_projection_count": auxiliary_cache["route_projection_count"],
            "ownership_graph_path_count": auxiliary_cache["ownership_graph_path_count"],
            "refresh_reason": refresh_reason,
            "stale_reasons": stale_reasons[:8],
            "rebuilt_paths": [],
        }

    existing_structure_index = load_structure_index(cache_paths) or _empty_structure_index(resolved_workspace)
    existing_modules, existing_chunk_records, storage_backend = _load_context_store_records(
        cache_paths,
        structure_index=existing_structure_index,
    )
    existing_chunk_groups = _chunk_records_by_path(existing_chunk_records)
    existing_file_records = _file_records_by_path(existing_structure_index)
    detection = detect_workspace(resolved_workspace)
    modules = _discover_modules(resolved_workspace, detection)
    modules = _annotate_modules(resolved_workspace, modules, candidate_files)
    manifest_snapshot_map = {entry["path"]: (entry["size"], entry["mtime_ns"]) for entry in manifest.get("indexed_file_snapshot", [])}
    candidate_snapshot_map = {entry["path"]: (entry["size"], entry["mtime_ns"]) for entry in candidate_snapshot}
    changed_path_set = {entry["path"] for entry in git_state.get("changed_files", [])}
    chunk_records: list[dict[str, Any]] = []
    file_records: list[dict[str, Any]] = []
    rebuilt_paths: list[str] = []
    reused_paths: list[str] = []
    rebuilt_file_paths: list[str] = []
    reused_file_paths: list[str] = []
    rebuilt_chunk_count = 0
    reused_chunk_count = 0
    bounded_read_count = 0
    full_read_count = 0
    large_file_count = 0
    parser_backends = default_parser_backends(resolved_workspace, plugin_root())
    for candidate in candidate_files:
        relative = str(candidate.relative_to(resolved_workspace))
        existing = existing_chunk_groups.get(relative)
        existing_file = existing_file_records.get(relative)
        if not force and existing and existing_file and manifest_snapshot_map.get(relative) == candidate_snapshot_map.get(relative):
            chunk_records.extend(copy.deepcopy(existing))
            file_records.append(copy.deepcopy(existing_file))
            reused_paths.append(relative)
            reused_file_paths.append(relative)
            reused_chunk_count += len(existing)
            continue
        file_hash = hashlib.sha1(candidate.read_bytes()).hexdigest()
        structure = build_file_structure(
            resolved_workspace,
            candidate,
            file_hash=file_hash,
            module_id=_module_id_for_candidate(resolved_workspace, candidate, modules),
            parser_backends=parser_backends,
            large_file_threshold=CONTEXT_INDEX_LARGE_FILE_BYTES,
            recent_churn=relative in changed_path_set,
        )
        chunk_records.extend(structure["chunks"])
        file_records.append(structure["file_record"])
        rebuilt_paths.append(relative)
        rebuilt_file_paths.append(relative)
        rebuilt_chunk_count += len(structure["chunks"])
        bounded_read_count += structure["bounded_read_count"]
        full_read_count += structure["full_read_count"]
        large_file_count += structure["large_file_count"]
    for candidate in project_note_candidates:
        relative = str(candidate["path"])
        existing = existing_chunk_groups.get(relative)
        if not force and existing and manifest_snapshot_map.get(relative) == candidate_snapshot_map.get(relative):
            chunk_records.extend(copy.deepcopy(existing))
            reused_paths.append(relative)
            reused_chunk_count += len(existing)
            continue
        chunk_records.append(_build_project_note_chunk_record(candidate))
        rebuilt_paths.append(relative)
        rebuilt_chunk_count += 1

    file_records = resolve_local_dependency_targets(
        resolved_workspace,
        [record["path"] for record in file_records],
        file_records,
    )
    modules = aggregate_modules(modules, file_records)
    chunk_records = apply_file_metadata_to_chunks(chunk_records, file_records)
    chunk_records.sort(
        key=lambda item: (
            item["path"],
            item.get("chunk_kind") or "file",
            str(item.get("anchor_title") or ""),
            int(item.get("line_start") or 0),
        )
    )
    chunk_counts = summarize_chunk_counts(chunk_records)
    parser_status = _parser_backend_status_payload(parser_backends)
    removed_file_paths = sorted(set(existing_file_records).difference({record["path"] for record in file_records}))
    hotspots = top_hotspots(file_records, modules)
    incremental_indexing = _incremental_indexing_payload(
        candidate_file_count=candidate_count,
        rebuilt_file_count=len(rebuilt_file_paths),
        reused_file_count=len(reused_file_paths),
        removed_file_count=len(removed_file_paths),
        rebuilt_chunk_count=rebuilt_chunk_count,
        reused_chunk_count=reused_chunk_count,
        bounded_read_count=bounded_read_count,
        full_read_count=full_read_count,
        large_file_count=large_file_count,
        refresh_reason=stale_reasons[0] if stale_reasons else "refresh",
        stale_reasons=stale_reasons,
        parser_backend_status=parser_status,
    )
    workspace_fingerprint = _workspace_fingerprint(resolved_workspace, chunk_records)
    structure_index = structure_index_payload(
        workspace=resolved_workspace,
        workspace_fingerprint=workspace_fingerprint,
        parser_backends=parser_backends,
        modules=modules,
        files=file_records,
        chunk_counts=chunk_counts,
        hotspots=hotspots,
        incremental_indexing=incremental_indexing,
    )
    workspace_context = _workspace_context_payload(
        resolved_workspace,
        detection,
        git_state,
        modules,
        chunk_records,
        structure_index,
        usage,
        catalog_digest,
    )
    semantic_result = _refresh_semantic_context(
        resolved_workspace,
        cache_paths=cache_paths,
        modules=modules,
        chunks=chunk_records,
        structure_index=structure_index,
        force=force,
    )
    workspace_context["semantic_summary"] = semantic_result["semantic_summary"]

    previous_context = load_json(cache_paths["workspace_context"], default={})
    previous_catalog_digest = previous_context.get("catalog_digest")
    write_json(cache_paths["workspace_context"], workspace_context)
    write_json(
        cache_paths["module_map"],
        {
            "schema_version": CONTEXT_CACHE_SCHEMA_VERSION,
            "workspace_path": str(resolved_workspace),
            "workspace_fingerprint": workspace_context["workspace_fingerprint"],
            "modules": modules,
        },
    )
    write_json(cache_paths["structure_index"], structure_index)
    replace_context_records(
        cache_paths["context_store"],
        modules=modules,
        files=file_records,
        chunks=chunk_records,
    )
    auxiliary_cache = _ensure_runtime_auxiliary_cache_entries(
        resolved_workspace,
        cache_paths=cache_paths,
        workspace_context=workspace_context,
        catalog_digest=catalog_digest,
        structure_index=structure_index,
        chunk_records=chunk_records,
    )

    pruned_context_pack, pruned_context_pack_reason = _prune_context_pack_cache_entries(
        cache_paths,
        chunk_records,
        catalog_digest,
        full_reset_reason="catalog-digest" if previous_catalog_digest and previous_catalog_digest != catalog_digest else None,
    )
    pruned_runtime_preflight, pruned_runtime_preflight_reason = _prune_runtime_preflight_cache_entries(
        cache_paths,
        chunk_records,
        catalog_digest,
        full_reset_reason="catalog-digest" if previous_catalog_digest and previous_catalog_digest != catalog_digest else None,
    )
    pruned_task_retrieval, pruned_task_retrieval_reason = _prune_task_retrieval_cache_entries(
        cache_paths,
        chunk_records,
        catalog_digest,
        full_reset_reason="catalog-digest" if previous_catalog_digest and previous_catalog_digest != catalog_digest else None,
    )
    pruned = pruned_context_pack + pruned_runtime_preflight + pruned_task_retrieval
    pruned_reason = pruned_context_pack_reason or pruned_runtime_preflight_reason or pruned_task_retrieval_reason

    write_json(
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
        rebuilt_file_count=len(rebuilt_file_paths),
        reused_file_count=len(reused_file_paths),
        removed_file_count=len(removed_file_paths),
        rebuilt_chunk_count=rebuilt_chunk_count,
        reused_chunk_count=reused_chunk_count,
        bounded_read_count=bounded_read_count,
        full_read_count=full_read_count,
        large_file_count=large_file_count,
        parser_backend_status=parser_status,
    )

    return {
        "status": "refreshed",
        "workspace_path": str(resolved_workspace),
        "retrieval": retrieval_policy_payload("refresh_context_index", resolved_mode),
        "cache_root": str(cache_paths["root"]),
        "workspace_context_path": str(cache_paths["workspace_context"]),
        "module_map_path": str(cache_paths["module_map"]),
        "structure_index_path": str(cache_paths["structure_index"]),
        "context_store_path": str(cache_paths["context_store"]),
        "storage_backend": "sqlite",
        "storage_backend_status": storage_backend,
        "storage_summary": context_store_summary(cache_paths["context_store"]),
        "semantic_units_path": str(cache_paths["semantic_units"]),
        "semantic_index_path": str(cache_paths["semantic_index"]),
        "semantic_manifest_path": str(cache_paths["semantic_manifest"]),
        "workspace_fingerprint": workspace_context["workspace_fingerprint"],
        "catalog_digest": catalog_digest,
        "candidate_file_count": candidate_count,
        "module_count": len(modules),
        "chunk_count": len(chunk_records),
        "structure_summary": structure_summary(structure_index),
        "hotspot_summary": hotspot_summary(structure_index),
        "semantic_summary": semantic_result["semantic_summary"],
        "rebuilt_file_count": len(rebuilt_file_paths),
        "reused_file_count": len(reused_file_paths),
        "removed_file_count": len(removed_file_paths),
        "rebuilt_chunk_count": rebuilt_chunk_count,
        "reused_chunk_count": reused_chunk_count,
        "bounded_read_count": bounded_read_count,
        "full_read_count": full_read_count,
        "large_file_count": large_file_count,
        "parser_backend_status": parser_status,
        "semantic_backend_status": semantic_result.get("backend_status"),
        "semantic_refresh_status": semantic_result.get("status"),
        "semantic_rebuilt_unit_count": semantic_result.get("rebuilt_unit_count", 0),
        "semantic_reused_unit_count": semantic_result.get("reused_unit_count", 0),
        "semantic_removed_unit_count": semantic_result.get("removed_unit_count", 0),
        "pruned_semantic_cache_entries": pruned,
        "pruned_semantic_cache_reason": pruned_reason,
        "runtime_projection_status": auxiliary_cache["status"],
        "route_projection_count": auxiliary_cache["route_projection_count"],
        "ownership_graph_path_count": auxiliary_cache["ownership_graph_path_count"],
        "refresh_reason": refresh_reason,
        "stale_reasons": stale_reasons[:8],
        "rebuilt_paths": rebuilt_paths[:24],
}


def load_workspace_context_bundle(
    workspace: str | Path,
    *,
    query_text: str | None = None,
    route_id: str | None = None,
    retrieval_mode: str | None = None,
    include_structure_index: bool = False,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    refresh_result = refresh_context_index(
        workspace,
        force=False,
        query_text=query_text,
        route_id=route_id,
        retrieval_mode=retrieval_mode,
    )
    cache_paths = context_cache_paths(workspace)
    workspace_context = load_json(cache_paths["workspace_context"], default={})
    usage = load_usage(cache_paths)
    structure_index = (
        load_structure_index(cache_paths) or _empty_structure_index(Path(workspace).expanduser().resolve())
        if include_structure_index
        else None
    )
    return refresh_result, cache_paths, workspace_context, usage, structure_index
