from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from time import time_ns
from typing import Any

from agentiux_dev_lib import now_iso
from agentiux_dev_memory import list_generated_memory_snapshots, pinned_project_notes
from agentiux_dev_text import normalize_command_phrase, short_hash, tokenize_text


SEMANTIC_UNIT_SCHEMA_VERSION = 1
SEMANTIC_MANIFEST_SCHEMA_VERSION = 1
SEMANTIC_EMBEDDER_VERSION = "local-hash-v1"
SEMANTIC_VECTOR_DIM = 96
SEMANTIC_MODE_VALUES = {"disabled", "auto", "enabled"}
SEMANTIC_MODE_ALIASES = {
    "balanced": "auto",
    "default": "auto",
    "normal": "auto",
    "none": "disabled",
    "off": "disabled",
    "false": "disabled",
    "on": "enabled",
    "true": "enabled",
}
SEMANTIC_MODE_ARGUMENT_VALUES = tuple(sorted({*SEMANTIC_MODE_VALUES, *SEMANTIC_MODE_ALIASES}))
SEMANTIC_MATCH_LIMIT = 12
ANALYSIS_AUDIT_MODES = {"architecture", "performance", "docs_style"}
SEMANTIC_STATUS_ACTIVE = "active"
SEMANTIC_STATUS_READY = "ready"
SEMANTIC_SYNONYMS = {
    "analysis": ["architecture", "audit", "hotspot", "module", "symbol"],
    "architecture": ["analysis", "boundary", "coupling", "crosscutting", "module"],
    "boundary": ["architecture", "coupling"],
    "cache": ["incremental", "reuse", "rebuild"],
    "command": ["catalog", "docs", "surface"],
    "coupling": ["architecture", "boundary", "crosscutting"],
    "crosscutting": ["architecture", "memory", "semantic"],
    "docs": ["command", "documentation", "operator", "readme", "surface"],
    "documentation": ["docs", "readme"],
    "hotspot": ["analysis", "cost", "performance", "pressure", "risk"],
    "incremental": ["cache", "rebuild", "reuse"],
    "memory": ["crosscutting", "note", "snapshot"],
    "operator": ["docs", "truth"],
    "overfetch": ["broadread", "performance"],
    "performance": ["cost", "hotspot", "latency", "overfetch", "pressure"],
    "pressure": ["hotspot", "performance", "risk"],
    "readme": ["docs", "documentation"],
    "rebuild": ["cache", "incremental", "reuse"],
    "reuse": ["cache", "incremental", "rebuild"],
    "risk": ["hotspot", "pressure"],
    "semantic": ["crosscutting", "memory"],
    "snapshot": ["memory", "note"],
    "surface": ["command", "docs"],
    "truth": ["docs", "operator"],
}


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


def normalize_semantic_mode(value: str | None, *, default: str = "disabled") -> str:
    normalized = str(value or default).strip().lower() or default
    normalized = SEMANTIC_MODE_ALIASES.get(normalized, normalized)
    if normalized not in SEMANTIC_MODE_VALUES:
        raise ValueError(f"Unsupported semantic mode: {value}")
    return normalized


def semantic_mode_enabled(
    value: str | None,
    *,
    analysis_explicit: bool = False,
) -> tuple[str, bool]:
    normalized = normalize_semantic_mode(value)
    if normalized == "disabled":
        return normalized, False
    if normalized == "enabled":
        return normalized, True
    return normalized, bool(analysis_explicit)


def _semantic_summary(
    *,
    backend_status: str,
    unit_count: int,
    note_count: int,
    snapshot_count: int,
    last_refresh_status: str,
    rebuilt_unit_count: int,
    reused_unit_count: int,
    removed_unit_count: int,
) -> dict[str, Any]:
    return {
        "backend_status": backend_status,
        "unit_count": unit_count,
        "note_count": note_count,
        "snapshot_count": snapshot_count,
        "last_refresh_status": last_refresh_status,
        "rebuilt_unit_count": rebuilt_unit_count,
        "reused_unit_count": reused_unit_count,
        "removed_unit_count": removed_unit_count,
    }


def load_semantic_manifest(paths: dict[str, Path]) -> dict[str, Any]:
    payload = _load_json(paths["semantic_manifest"], default={})
    return payload if isinstance(payload, dict) else {}


def semantic_summary_from_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    manifest = manifest or {}
    summary = manifest.get("semantic_summary") or {}
    if summary:
        return {
            "backend_status": summary.get("backend_status", SEMANTIC_STATUS_ACTIVE),
            "unit_count": int(summary.get("unit_count") or 0),
            "note_count": int(summary.get("note_count") or 0),
            "snapshot_count": int(summary.get("snapshot_count") or 0),
            "last_refresh_status": summary.get("last_refresh_status") or "missing",
            "rebuilt_unit_count": int(summary.get("rebuilt_unit_count") or 0),
            "reused_unit_count": int(summary.get("reused_unit_count") or 0),
            "removed_unit_count": int(summary.get("removed_unit_count") or 0),
        }
    return _semantic_summary(
        backend_status=SEMANTIC_STATUS_ACTIVE,
        unit_count=0,
        note_count=0,
        snapshot_count=0,
        last_refresh_status="missing",
        rebuilt_unit_count=0,
        reused_unit_count=0,
        removed_unit_count=0,
    )


def _sqlite_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_vectors (
            unit_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            module_id TEXT,
            path TEXT,
            anchor_title TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_semantic_vectors_source_kind ON semantic_vectors(source_kind)")
    return connection


def _semantic_terms(text: str) -> list[str]:
    tokens = tokenize_text(normalize_command_phrase(text))
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        for synonym in SEMANTIC_SYNONYMS.get(token, []):
            if synonym not in expanded:
                expanded.append(synonym)
        if len(token) >= 5:
            for index in range(len(token) - 2):
                expanded.append(f"tri:{token[index:index + 3]}")
    return expanded


def _embed_text(text: str) -> list[float]:
    vector = [0.0 for _ in range(SEMANTIC_VECTOR_DIM)]
    for token in _semantic_terms(text):
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % SEMANTIC_VECTOR_DIM
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        weight = 1.0
        if token.startswith("tri:"):
            weight = 0.35
        elif token in SEMANTIC_SYNONYMS:
            weight = 1.25
        vector[index] += sign * weight
    norm = sum(value * value for value in vector) ** 0.5
    if norm <= 0:
        return vector
    return [round(value / norm, 6) for value in vector]


def _cosine_score(lhs: list[float], rhs: list[float]) -> float:
    return sum(left * right for left, right in zip(lhs, rhs))


def _json_vector(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def _preview_text(text: str, *, limit: int = 320) -> str:
    collapsed = " ".join(line.strip() for line in str(text or "").splitlines() if line.strip())
    return collapsed[:limit]


def _unit_fingerprint(parts: list[Any]) -> str:
    return hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()


def _unit_payload(
    *,
    unit_id: str,
    source_kind: str,
    summary_text: str,
    module_id: str | None,
    path: str | None,
    anchor_title: str | None,
    anchor_kind: str | None,
    line_start: int | None,
    line_end: int | None,
    source_paths: list[str],
    anchor_refs: list[dict[str, Any]],
    hotspot_labels: list[str] | None = None,
    dependency_targets: list[str] | None = None,
    note_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    fingerprint = _unit_fingerprint(
        [
            SEMANTIC_EMBEDDER_VERSION,
            unit_id,
            source_kind,
            summary_text,
            module_id,
            path,
            anchor_title,
            anchor_kind,
            line_start,
            line_end,
            ",".join(source_paths),
            json.dumps(anchor_refs, sort_keys=True),
            ",".join(hotspot_labels or []),
            ",".join(dependency_targets or []),
            note_id,
            snapshot_id,
        ]
    )
    return {
        "schema_version": SEMANTIC_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source_kind": source_kind,
        "summary_text": summary_text,
        "fingerprint": fingerprint,
        "module_id": module_id,
        "path": path,
        "anchor_title": anchor_title,
        "anchor_kind": anchor_kind,
        "line_start": line_start,
        "line_end": line_end,
        "hotspot_labels": list(hotspot_labels or []),
        "dependency_targets": list(dependency_targets or []),
        "note_id": note_id,
        "snapshot_id": snapshot_id,
        "provenance": {
            "source_paths": source_paths,
            "anchor_refs": anchor_refs,
            "module_ids": [module_id] if module_id else [],
            "source_kind": source_kind,
            "summary_text": summary_text,
            "fingerprint": fingerprint,
        },
    }


def _module_units(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for module in modules:
        module_id = module.get("module_id")
        if not module_id:
            continue
        summary_text = normalize_command_phrase(
            " ".join(
                part
                for part in [
                    f"Module {module.get('path') or '.'}.",
                    f"Kind {module.get('kind') or 'module'}.",
                    f"Languages {', '.join(sorted((module.get('language_counts') or {}).keys())) or 'unknown'}.",
                    f"Fan in {int(module.get('local_fan_in') or 0)}.",
                    f"Fan out {int(module.get('local_fan_out') or 0)}.",
                    f"Hotspot score {int(module.get('hotspot_score') or 0)}.",
                    f"Entrypoints {', '.join(module.get('entrypoint_hints') or []) or 'none'}.",
                ]
            )
        )
        units.append(
            _unit_payload(
                unit_id=f"module:{module_id}",
                source_kind="module_summary",
                summary_text=summary_text,
                module_id=str(module_id),
                path=str(module.get("path") or "."),
                anchor_title=str(module.get("path") or module_id),
                anchor_kind="module",
                line_start=None,
                line_end=None,
                source_paths=[str(module.get("path") or ".")],
                anchor_refs=[
                    {
                        "anchor_kind": "module",
                        "module_id": module_id,
                        "path": module.get("path"),
                    }
                ],
                hotspot_labels=module.get("hotspot_labels") or [],
            )
        )
    return units


def _chunk_units(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_kind = str(chunk.get("chunk_kind") or "")
        if chunk_kind not in {"symbol", "doc_section"}:
            continue
        unit_id = f"chunk:{chunk.get('chunk_id')}"
        summary_text = normalize_command_phrase(
            " ".join(
                part
                for part in [
                    str(chunk.get("summary") or ""),
                    f"Path {chunk.get('path')}.",
                    f"Anchor {chunk.get('anchor_title') or chunk.get('anchor_kind') or chunk_kind}.",
                    f"Language {chunk.get('language') or 'text'}.",
                ]
            )
        )
        units.append(
            _unit_payload(
                unit_id=unit_id,
                source_kind=f"{chunk_kind}_aggregate",
                summary_text=summary_text,
                module_id=chunk.get("module_id"),
                path=chunk.get("path"),
                anchor_title=chunk.get("anchor_title"),
                anchor_kind=chunk.get("anchor_kind"),
                line_start=chunk.get("line_start"),
                line_end=chunk.get("line_end"),
                source_paths=[str(chunk.get("path"))],
                anchor_refs=[
                    {
                        "chunk_id": chunk.get("chunk_id"),
                        "anchor_kind": chunk.get("anchor_kind"),
                        "anchor_title": chunk.get("anchor_title"),
                        "path": chunk.get("path"),
                        "line_start": chunk.get("line_start"),
                        "line_end": chunk.get("line_end"),
                    }
                ],
                hotspot_labels=chunk.get("hotspot_labels") or [],
                dependency_targets=chunk.get("dependency_targets") or [],
            )
        )
    return units


def _hotspot_units(hotspots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for hotspot in hotspots[:24]:
        target_kind = str(hotspot.get("target_kind") or "file")
        identity = hotspot.get("module_id") or hotspot.get("path") or short_hash(json.dumps(hotspot, sort_keys=True), length=8)
        summary_text = normalize_command_phrase(
            " ".join(
                part
                for part in [
                    f"{target_kind.title()} hotspot {hotspot.get('path') or hotspot.get('module_id') or 'unknown'}.",
                    f"Score {int(hotspot.get('hotspot_score') or 0)}.",
                    f"Fan in {int(hotspot.get('local_fan_in') or 0)}.",
                    f"Fan out {int(hotspot.get('local_fan_out') or 0)}.",
                    f"Labels {', '.join(hotspot.get('hotspot_labels') or []) or 'none'}.",
                ]
            )
        )
        units.append(
            _unit_payload(
                unit_id=f"hotspot:{target_kind}:{identity}",
                source_kind="hotspot_cluster",
                summary_text=summary_text,
                module_id=hotspot.get("module_id"),
                path=hotspot.get("path"),
                anchor_title=hotspot.get("path") or hotspot.get("module_id"),
                anchor_kind=target_kind,
                line_start=None,
                line_end=None,
                source_paths=[str(hotspot.get("path") or hotspot.get("module_id") or "")],
                anchor_refs=[
                    {
                        "target_kind": target_kind,
                        "module_id": hotspot.get("module_id"),
                        "path": hotspot.get("path"),
                    }
                ],
                hotspot_labels=hotspot.get("hotspot_labels") or [],
            )
        )
    return units


def _note_units(workspace: Path) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    try:
        notes = pinned_project_notes(workspace)
    except FileNotFoundError:
        return units
    for note in notes:
        body_preview = _preview_text(note.get("body_markdown") or "")
        summary_text = normalize_command_phrase(
            " ".join(
                part
                for part in [
                    f"Project note {note.get('title') or note.get('note_id')}.",
                    f"Tags {', '.join(note.get('tags') or []) or 'none'}.",
                    body_preview,
                ]
            )
        )
        units.append(
            _unit_payload(
                unit_id=f"note:{note.get('note_id')}",
                source_kind="project_note",
                summary_text=summary_text,
                module_id=None,
                path=f"external/project-memory/{note.get('note_id')}.json",
                anchor_title=note.get("title") or note.get("note_id"),
                anchor_kind="project_note",
                line_start=None,
                line_end=None,
                source_paths=[f"external/project-memory/{note.get('note_id')}.json"],
                anchor_refs=[
                    {
                        "note_id": note.get("note_id"),
                        "title": note.get("title"),
                    }
                ],
                note_id=note.get("note_id"),
            )
        )
    return units


def _snapshot_units(workspace: Path) -> tuple[list[dict[str, Any]], int]:
    units: list[dict[str, Any]] = []
    try:
        snapshot_payload = list_generated_memory_snapshots(workspace)
    except FileNotFoundError:
        return units, 0
    for snapshot in snapshot_payload.get("items", []):
        body_preview = _preview_text(snapshot.get("preview") or "")
        summary_text = normalize_command_phrase(
            " ".join(
                part
                for part in [
                    f"Generated snapshot {snapshot.get('title') or snapshot.get('snapshot_id')}.",
                    f"Mode {snapshot.get('source_audit_mode') or 'analysis'}.",
                    f"Scope {snapshot.get('source_module_path') or snapshot.get('source_query_text') or 'workspace'}.",
                    f"Confidence {snapshot.get('confidence') or 0}.",
                    body_preview,
                ]
            )
        )
        units.append(
            _unit_payload(
                unit_id=f"snapshot:{snapshot.get('snapshot_id')}",
                source_kind="generated_snapshot",
                summary_text=summary_text,
                module_id=None,
                path=f"external/generated-memory/{snapshot.get('snapshot_id')}.json",
                anchor_title=snapshot.get("title") or snapshot.get("snapshot_id"),
                anchor_kind="generated_snapshot",
                line_start=None,
                line_end=None,
                source_paths=[f"external/generated-memory/{snapshot.get('snapshot_id')}.json"],
                anchor_refs=[
                    {
                        "snapshot_id": snapshot.get("snapshot_id"),
                        "source_audit_mode": snapshot.get("source_audit_mode"),
                        "source_module_path": snapshot.get("source_module_path"),
                        "source_query_text": snapshot.get("source_query_text"),
                    }
                ],
                snapshot_id=snapshot.get("snapshot_id"),
            )
        )
    return units, int((snapshot_payload.get("counts") or {}).get("active") or 0)


def _note_fingerprint(workspace: Path) -> tuple[str, int]:
    try:
        notes = pinned_project_notes(workspace)
    except FileNotFoundError:
        return hashlib.sha1(b"").hexdigest(), 0
    digest = hashlib.sha1()
    for note in notes:
        digest.update(str(note.get("note_id") or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(note.get("updated_at") or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(note.get("latest_revision_id") or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest(), len(notes)


def _snapshot_fingerprint(workspace: Path) -> tuple[str, int]:
    try:
        snapshot_payload = list_generated_memory_snapshots(workspace)
    except FileNotFoundError:
        return hashlib.sha1(b"").hexdigest(), 0
    items = snapshot_payload.get("items") or []
    digest = hashlib.sha1()
    for snapshot in items:
        digest.update(str(snapshot.get("snapshot_id") or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(snapshot.get("updated_at") or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(snapshot.get("expires_at") or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest(), int((snapshot_payload.get("counts") or {}).get("active") or 0)


def refresh_semantic_index(
    workspace: str | Path,
    *,
    cache_paths: dict[str, Path],
    modules: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    structure_index: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    units_path = cache_paths["semantic_units"]
    sqlite_path = cache_paths["semantic_index"]
    manifest_path = cache_paths["semantic_manifest"]
    previous_manifest = load_semantic_manifest(cache_paths)
    previous_units = _load_jsonl(units_path)
    previous_units_by_id = {str(unit.get("unit_id")): unit for unit in previous_units if unit.get("unit_id")}
    current_units = []
    current_units.extend(_module_units(modules))
    current_units.extend(_chunk_units(chunks))
    current_units.extend(_hotspot_units(structure_index.get("hotspots") or []))
    current_units.extend(_note_units(resolved_workspace))
    snapshot_units, snapshot_count = _snapshot_units(resolved_workspace)
    current_units.extend(snapshot_units)
    current_units.sort(key=lambda item: (str(item.get("source_kind") or ""), str(item.get("path") or ""), str(item.get("unit_id") or "")))

    note_fingerprint, note_count = _note_fingerprint(resolved_workspace)
    snapshot_fingerprint, snapshot_count = _snapshot_fingerprint(resolved_workspace)
    structure_fingerprint = str(structure_index.get("workspace_fingerprint") or "")
    full_rebuild_reason = None
    if force:
        full_rebuild_reason = "force-refresh"
    elif previous_manifest.get("schema_version") != SEMANTIC_MANIFEST_SCHEMA_VERSION:
        full_rebuild_reason = "semantic-manifest-schema"
    elif previous_manifest.get("embedder_version") != SEMANTIC_EMBEDDER_VERSION:
        full_rebuild_reason = "embedder-version"
    elif not sqlite_path.exists():
        full_rebuild_reason = "missing-semantic-index"
    elif not units_path.exists():
        full_rebuild_reason = "missing-semantic-units"

    connection = _sqlite_connection(sqlite_path)
    try:
        if full_rebuild_reason:
            connection.execute("DELETE FROM semantic_vectors")

        existing_rows = {
            row[0]: {"fingerprint": row[1]}
            for row in connection.execute("SELECT unit_id, fingerprint FROM semantic_vectors")
        }
        current_ids = {str(unit["unit_id"]) for unit in current_units}
        removed_ids = sorted(set(previous_units_by_id).union(existing_rows).difference(current_ids))
        rebuilt_unit_count = 0
        reused_unit_count = 0
        for unit in current_units:
            unit_id = str(unit["unit_id"])
            previous_fingerprint = previous_units_by_id.get(unit_id, {}).get("fingerprint")
            sqlite_fingerprint = existing_rows.get(unit_id, {}).get("fingerprint")
            if not full_rebuild_reason and previous_fingerprint == unit["fingerprint"] and sqlite_fingerprint == unit["fingerprint"]:
                reused_unit_count += 1
                continue
            vector = _embed_text(
                " ".join(
                    [
                        str(unit.get("summary_text") or ""),
                        str(unit.get("path") or ""),
                        str(unit.get("anchor_title") or ""),
                        " ".join(unit.get("hotspot_labels") or []),
                    ]
                )
            )
            connection.execute(
                """
                INSERT INTO semantic_vectors(unit_id, fingerprint, vector_json, source_kind, module_id, path, anchor_title, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unit_id) DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    vector_json=excluded.vector_json,
                    source_kind=excluded.source_kind,
                    module_id=excluded.module_id,
                    path=excluded.path,
                    anchor_title=excluded.anchor_title,
                    updated_at=excluded.updated_at
                """,
                (
                    unit_id,
                    unit["fingerprint"],
                    _json_vector(vector),
                    unit.get("source_kind"),
                    unit.get("module_id"),
                    unit.get("path"),
                    unit.get("anchor_title"),
                    now_iso(),
                ),
            )
            rebuilt_unit_count += 1
        if removed_ids:
            connection.executemany("DELETE FROM semantic_vectors WHERE unit_id = ?", [(unit_id,) for unit_id in removed_ids])
        connection.commit()
    finally:
        connection.close()

    _write_jsonl(units_path, current_units)
    refresh_status = "refreshed" if (full_rebuild_reason or rebuilt_unit_count or removed_ids) else "fresh"
    summary = _semantic_summary(
        backend_status=SEMANTIC_STATUS_ACTIVE,
        unit_count=len(current_units),
        note_count=note_count,
        snapshot_count=snapshot_count,
        last_refresh_status=refresh_status,
        rebuilt_unit_count=rebuilt_unit_count,
        reused_unit_count=reused_unit_count,
        removed_unit_count=len(removed_ids),
    )
    manifest = {
        "schema_version": SEMANTIC_MANIFEST_SCHEMA_VERSION,
        "workspace_path": str(resolved_workspace),
        "generated_at": now_iso(),
        "embedder_version": SEMANTIC_EMBEDDER_VERSION,
        "structure_fingerprint": structure_fingerprint,
        "note_fingerprint": note_fingerprint,
        "snapshot_fingerprint": snapshot_fingerprint,
        "unit_count": len(current_units),
        "semantic_summary": summary,
        "last_refresh_reason": full_rebuild_reason or ("incremental" if rebuilt_unit_count or removed_ids else "manifest-match"),
    }
    _write_json(manifest_path, manifest)
    return {
        "status": refresh_status,
        "backend_status": SEMANTIC_STATUS_ACTIVE,
        "manifest_path": str(manifest_path),
        "semantic_units_path": str(units_path),
        "semantic_index_path": str(sqlite_path),
        "embedder_version": SEMANTIC_EMBEDDER_VERSION,
        "full_rebuild_reason": full_rebuild_reason,
        "rebuilt_unit_count": rebuilt_unit_count,
        "reused_unit_count": reused_unit_count,
        "removed_unit_count": len(removed_ids),
        "semantic_summary": summary,
    }


def search_semantic_units(
    cache_paths: dict[str, Path],
    *,
    query_text: str,
    limit: int = 8,
    module_path: str | None = None,
) -> dict[str, Any]:
    manifest = load_semantic_manifest(cache_paths)
    if semantic_summary_from_manifest(manifest).get("unit_count", 0) <= 0:
        return {
            "backend_status": SEMANTIC_STATUS_ACTIVE,
            "semantic_summary": semantic_summary_from_manifest(manifest),
            "matches": [],
        }
    query_vector = _embed_text(query_text)
    query_tokens = set(_semantic_terms(query_text))
    units = {str(unit.get("unit_id")): unit for unit in _load_jsonl(cache_paths["semantic_units"]) if unit.get("unit_id")}
    connection = _sqlite_connection(cache_paths["semantic_index"])
    try:
        rows = list(
            connection.execute(
                "SELECT unit_id, fingerprint, vector_json, source_kind, module_id, path, anchor_title FROM semantic_vectors"
            )
        )
    finally:
        connection.close()
    matches: list[dict[str, Any]] = []
    normalized_module_path = str(module_path or "").strip() or None
    for unit_id, fingerprint, vector_json, source_kind, module_id, path, anchor_title in rows:
        unit = units.get(str(unit_id))
        if not unit or unit.get("fingerprint") != fingerprint:
            continue
        if normalized_module_path and path:
            path_text = str(path)
            in_path_scope = path_text == normalized_module_path or path_text.startswith(f"{normalized_module_path}/")
            in_anchor_scope = any(
                str(anchor.get("source_module_path") or "") == normalized_module_path
                or str(anchor.get("path") or "").startswith(f"{normalized_module_path}/")
                for anchor in ((unit.get("provenance") or {}).get("anchor_refs") or [])
                if isinstance(anchor, dict)
            )
            if not in_path_scope and not in_anchor_scope:
                continue
        try:
            vector = json.loads(vector_json)
        except json.JSONDecodeError:
            continue
        cosine = _cosine_score(query_vector, vector)
        overlap = len(query_tokens.intersection(set(_semantic_terms(unit.get("summary_text") or ""))))
        score = round((cosine * 100) + (overlap * 4), 3)
        if score <= 0:
            continue
        matches.append(
            {
                **unit,
                "score": score,
                "match_source": "semantic_assisted",
                "why": {
                    "source_kind": source_kind,
                    "anchor_title": anchor_title,
                    "module_id": module_id,
                },
            }
        )
    matches.sort(key=lambda item: (-float(item.get("score") or 0), str(item.get("path") or ""), str(item.get("unit_id") or "")))
    return {
        "backend_status": SEMANTIC_STATUS_ACTIVE,
        "semantic_summary": semantic_summary_from_manifest(manifest),
        "matches": matches[: max(1, min(int(limit), SEMANTIC_MATCH_LIMIT))],
    }
