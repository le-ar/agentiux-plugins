#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from agentiux_dev_lib import (
    _ensure_workspace_initialized,
    _load_json,
    _write_json,
    now_iso,
    sanitize_identifier,
)


MEMORY_SCHEMA_VERSION = 1
NOTE_STATUS_VALUES = {"active", "archived"}
NOTE_PIN_VALUES = {"normal", "pinned"}
NOTE_SOURCE_VALUES = {"chat", "web", "system"}
GENERATED_MEMORY_SNAPSHOT_SCHEMA_VERSION = 1
GENERATED_SNAPSHOT_STATUS_VALUES = {"active", "archived", "expired"}
GENERATED_SNAPSHOT_TTL_DAYS = 14
GENERATED_SNAPSHOT_ACTIVE_CAP = 40


def _memory_paths(workspace: str | Path) -> dict[str, Any]:
    base_paths = _ensure_workspace_initialized(workspace)
    return {
        **base_paths,
        "root": Path(base_paths["memory_root"]),
        "notes_root": Path(base_paths["memory_notes_root"]),
        "index_path": Path(base_paths["memory_notes_index"]),
        "revisions_root": Path(base_paths["memory_revisions_root"]),
        "generated_snapshots_root": Path(base_paths["memory_generated_snapshots_root"]),
        "generated_snapshots_index": Path(base_paths["memory_generated_snapshots_index"]),
    }


def _ensure_memory_dirs(paths: dict[str, Any]) -> None:
    for key in ("root", "notes_root", "revisions_root", "generated_snapshots_root"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)


def _default_notes_index() -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "items": [],
        "updated_at": now_iso(),
    }


def _load_notes_index(paths: dict[str, Any]) -> dict[str, Any]:
    payload = _load_json(Path(paths["index_path"]), default=_default_notes_index(), strict=False) or _default_notes_index()
    payload["items"] = copy.deepcopy(payload.get("items") or [])
    return payload


def _save_notes_index(paths: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    payload["schema_version"] = MEMORY_SCHEMA_VERSION
    payload["updated_at"] = now_iso()
    _write_json(Path(paths["index_path"]), payload)
    return payload


def _default_generated_snapshot_index() -> dict[str, Any]:
    return {
        "schema_version": GENERATED_MEMORY_SNAPSHOT_SCHEMA_VERSION,
        "items": [],
        "updated_at": now_iso(),
    }


def _load_generated_snapshot_index(paths: dict[str, Any]) -> dict[str, Any]:
    payload = _load_json(
        Path(paths["generated_snapshots_index"]),
        default=_default_generated_snapshot_index(),
        strict=False,
    ) or _default_generated_snapshot_index()
    payload["items"] = copy.deepcopy(payload.get("items") or [])
    return payload


def _save_generated_snapshot_index(paths: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    payload["schema_version"] = GENERATED_MEMORY_SNAPSHOT_SCHEMA_VERSION
    payload["updated_at"] = now_iso()
    _write_json(Path(paths["generated_snapshots_index"]), payload)
    return payload


def _note_path(paths: dict[str, Any], note_id: str) -> Path:
    return Path(paths["notes_root"]) / f"{sanitize_identifier(note_id, 'note')}.json"


def _revision_dir(paths: dict[str, Any], note_id: str) -> Path:
    return Path(paths["revisions_root"]) / sanitize_identifier(note_id, "note")


def _generated_snapshot_path(paths: dict[str, Any], snapshot_id: str) -> Path:
    return Path(paths["generated_snapshots_root"]) / f"{sanitize_identifier(snapshot_id, 'snapshot')}.json"


def _write_note_revision(paths: dict[str, Any], note: dict[str, Any]) -> str:
    revision_id = sanitize_identifier(f"{note['note_id']}-{int(time.time() * 1000)}", f"{note['note_id']}-revision")
    target_dir = _revision_dir(paths, note["note_id"])
    target_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        target_dir / f"{revision_id}.json",
        {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "revision_id": revision_id,
            "note_id": note["note_id"],
            "captured_at": now_iso(),
            "note": copy.deepcopy(note),
        },
    )
    return revision_id


def _normalize_tags(tags: Any) -> list[str]:
    normalized: list[str] = []
    for item in tags or []:
        value = str(item or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _normalize_note(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = copy.deepcopy(existing or {})
    note_id = sanitize_identifier(payload.get("note_id") or existing.get("note_id") or payload.get("title"), "note")
    title = str(payload.get("title") or existing.get("title") or note_id).strip() or note_id
    body_markdown = str(payload.get("body_markdown") if payload.get("body_markdown") is not None else existing.get("body_markdown") or "")
    status = str(payload.get("status") or existing.get("status") or "active").strip().lower()
    if status not in NOTE_STATUS_VALUES:
        raise ValueError(f"Unsupported note status: {status}")
    pin_state = str(payload.get("pin_state") or existing.get("pin_state") or "normal").strip().lower()
    if pin_state not in NOTE_PIN_VALUES:
        raise ValueError(f"Unsupported note pin_state: {pin_state}")
    source = str(payload.get("source") or existing.get("source") or "chat").strip().lower()
    if source not in NOTE_SOURCE_VALUES:
        raise ValueError(f"Unsupported note source: {source}")
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "note_id": note_id,
        "title": title,
        "body_markdown": body_markdown,
        "tags": _normalize_tags(payload.get("tags", existing.get("tags", []))),
        "status": status,
        "pin_state": pin_state,
        "source": source,
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "latest_revision_id": existing.get("latest_revision_id"),
    }


def _parse_iso_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _default_expires_at(created_at: str | None = None, *, days: int = GENERATED_SNAPSHOT_TTL_DAYS) -> str:
    base = _parse_iso_datetime(created_at) or dt.datetime.now(dt.timezone.utc)
    return (base + dt.timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_snapshot_tags(tags: Any) -> list[str]:
    normalized: list[str] = []
    for item in tags or []:
        value = str(item or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _snapshot_preview_lines(body_markdown: str, *, limit: int = 3) -> list[str]:
    lines = []
    for raw_line in str(body_markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _snapshot_preview(body_markdown: str) -> str:
    return " ".join(_snapshot_preview_lines(body_markdown))[:320]


def _snapshot_is_expired(snapshot: dict[str, Any], *, now: dt.datetime | None = None) -> bool:
    status = str(snapshot.get("status") or "active").strip().lower()
    if status == "archived":
        return False
    expires_at = _parse_iso_datetime(snapshot.get("expires_at"))
    if not expires_at:
        return False
    now_value = now or dt.datetime.now(dt.timezone.utc)
    return expires_at <= now_value


def _normalize_generated_snapshot(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = copy.deepcopy(existing or {})
    snapshot_id = sanitize_identifier(
        payload.get("snapshot_id") or existing.get("snapshot_id") or payload.get("title"),
        "snapshot",
    )
    title = str(payload.get("title") or existing.get("title") or snapshot_id).strip() or snapshot_id
    body_markdown = str(
        payload.get("body_markdown")
        if payload.get("body_markdown") is not None
        else existing.get("body_markdown") or ""
    )
    requested_status = str(payload.get("status") or existing.get("status") or "active").strip().lower()
    if requested_status not in GENERATED_SNAPSHOT_STATUS_VALUES:
        raise ValueError(f"Unsupported generated snapshot status: {requested_status}")
    created_at = existing.get("created_at") or now_iso()
    expires_at = str(payload.get("expires_at") or existing.get("expires_at") or _default_expires_at(created_at)).strip()
    normalized = {
        "schema_version": GENERATED_MEMORY_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "title": title,
        "body_markdown": body_markdown,
        "tags": _normalize_snapshot_tags(payload.get("tags", existing.get("tags", []))),
        "status": requested_status,
        "confidence": max(0.0, min(float(payload.get("confidence", existing.get("confidence", 0.5)) or 0.5), 1.0)),
        "source_audit_mode": str(
            payload.get("source_audit_mode") or existing.get("source_audit_mode") or "analysis"
        ).strip(),
        "source_query_text": str(
            payload.get("source_query_text")
            if payload.get("source_query_text") is not None
            else existing.get("source_query_text") or ""
        ).strip()
        or None,
        "source_module_path": str(
            payload.get("source_module_path")
            if payload.get("source_module_path") is not None
            else existing.get("source_module_path") or ""
        ).strip()
        or None,
        "provenance": copy.deepcopy(payload.get("provenance") or existing.get("provenance") or {}),
        "created_at": created_at,
        "updated_at": now_iso(),
        "expires_at": expires_at,
    }
    if normalized["status"] != "archived" and _snapshot_is_expired(normalized):
        normalized["status"] = "expired"
    return normalized


def _generated_snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "title": snapshot.get("title"),
        "status": snapshot.get("status"),
        "confidence": snapshot.get("confidence"),
        "source_audit_mode": snapshot.get("source_audit_mode"),
        "source_query_text": snapshot.get("source_query_text"),
        "source_module_path": snapshot.get("source_module_path"),
        "tags": copy.deepcopy(snapshot.get("tags") or []),
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "expires_at": snapshot.get("expires_at"),
        "preview": _snapshot_preview(snapshot.get("body_markdown") or ""),
    }


def _load_generated_snapshot(paths: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
    payload = _load_json(
        _generated_snapshot_path(paths, snapshot_id),
        default={},
        strict=True,
        purpose=f"generated memory snapshot `{snapshot_id}`",
    ) or {}
    if not payload:
        raise FileNotFoundError(f"Unknown generated memory snapshot: {snapshot_id}")
    return payload


def _persist_generated_snapshot_housekeeping(paths: dict[str, Any]) -> dict[str, Any]:
    _ensure_memory_dirs(paths)
    index = _load_generated_snapshot_index(paths)
    changed = False
    now_value = dt.datetime.now(dt.timezone.utc)
    items: list[dict[str, Any]] = []
    for item in index.get("items", []):
        summary = copy.deepcopy(item)
        if summary.get("status") != "archived" and _snapshot_is_expired(summary, now=now_value):
            summary["status"] = "expired"
            changed = True
            snapshot_path = _generated_snapshot_path(paths, summary["snapshot_id"])
            if snapshot_path.exists():
                snapshot_payload = _load_generated_snapshot(paths, summary["snapshot_id"])
                snapshot_payload["status"] = "expired"
                snapshot_payload["updated_at"] = now_iso()
                _write_json(snapshot_path, snapshot_payload)
        items.append(summary)
    active_items = [item for item in items if item.get("status") == "active"]
    if len(active_items) > GENERATED_SNAPSHOT_ACTIVE_CAP:
        active_items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
        allowed = {item["snapshot_id"] for item in active_items[:GENERATED_SNAPSHOT_ACTIVE_CAP]}
        for item in items:
            if item.get("status") == "active" and item.get("snapshot_id") not in allowed:
                item["status"] = "archived"
                changed = True
                snapshot_path = _generated_snapshot_path(paths, item["snapshot_id"])
                if snapshot_path.exists():
                    snapshot_payload = _load_generated_snapshot(paths, item["snapshot_id"])
                    snapshot_payload["status"] = "archived"
                    snapshot_payload["updated_at"] = now_iso()
                    _write_json(snapshot_path, snapshot_payload)
    if changed:
        items.sort(
            key=lambda item: (
                item.get("status") != "active",
                item.get("status") == "archived",
                -(1 if item.get("updated_at") else 0),
                item.get("title") or item.get("snapshot_id"),
            )
        )
        index["items"] = items
        _save_generated_snapshot_index(paths, index)
    return index


def _note_summary(note: dict[str, Any]) -> dict[str, Any]:
    body = str(note.get("body_markdown") or "")
    preview = body.strip().splitlines()[:3]
    return {
        "note_id": note.get("note_id"),
        "title": note.get("title"),
        "tags": copy.deepcopy(note.get("tags") or []),
        "status": note.get("status"),
        "pin_state": note.get("pin_state"),
        "source": note.get("source"),
        "updated_at": note.get("updated_at"),
        "created_at": note.get("created_at"),
        "latest_revision_id": note.get("latest_revision_id"),
        "preview": " ".join(line.strip() for line in preview if line.strip())[:240],
    }


def _load_note(paths: dict[str, Any], note_id: str) -> dict[str, Any]:
    payload = _load_json(_note_path(paths, note_id), default={}, strict=True, purpose=f"project note `{note_id}`") or {}
    if not payload:
        raise FileNotFoundError(f"Unknown project note: {note_id}")
    return payload


def write_project_note(workspace: str | Path, note: dict[str, Any]) -> dict[str, Any]:
    paths = _memory_paths(workspace)
    _ensure_memory_dirs(paths)
    incoming = copy.deepcopy(note or {})
    note_id = sanitize_identifier(incoming.get("note_id"), "")
    existing = _load_note(paths, note_id) if note_id and _note_path(paths, note_id).exists() else None
    normalized = _normalize_note(incoming, existing=existing)
    normalized["latest_revision_id"] = _write_note_revision(paths, normalized)
    _write_json(_note_path(paths, normalized["note_id"]), normalized)
    index = _load_notes_index(paths)
    items = [item for item in index.get("items", []) if item.get("note_id") != normalized["note_id"]]
    items.append(_note_summary(normalized))
    items.sort(key=lambda item: (item.get("status") != "active", item.get("pin_state") != "pinned", item.get("title") or item.get("note_id")))
    index["items"] = items
    _save_notes_index(paths, index)
    return {
        "workspace_path": paths["workspace_path"],
        "note": get_project_note(workspace, normalized["note_id"]),
        "notes": list_project_notes(workspace),
    }


def get_project_note(workspace: str | Path, note_id: str) -> dict[str, Any]:
    paths = _memory_paths(workspace)
    _ensure_memory_dirs(paths)
    note = _load_note(paths, note_id)
    revisions = sorted(_revision_dir(paths, note["note_id"]).glob("*.json"), reverse=True)
    return {
        **note,
        "workspace_path": paths["workspace_path"],
        "revision_dir": str(_revision_dir(paths, note["note_id"])),
        "revision_count": len(revisions),
    }


def list_project_notes(workspace: str | Path, status: str | None = None) -> dict[str, Any]:
    paths = _memory_paths(workspace)
    _ensure_memory_dirs(paths)
    items = []
    for item in _load_notes_index(paths).get("items", []):
        if status and item.get("status") != status:
            continue
        items.append(copy.deepcopy(item))
    items.sort(key=lambda item: (item.get("status") != "active", item.get("pin_state") != "pinned", -(1 if item.get("updated_at") else 0), item.get("title") or item.get("note_id")))
    return {
        "workspace_path": paths["workspace_path"],
        "items": items,
        "counts": {
            "total": len(items),
            "active": sum(1 for item in items if item.get("status") == "active"),
            "archived": sum(1 for item in items if item.get("status") == "archived"),
            "pinned": sum(1 for item in items if item.get("pin_state") == "pinned"),
        },
    }


def archive_project_note(workspace: str | Path, note_id: str) -> dict[str, Any]:
    note = get_project_note(workspace, note_id)
    return write_project_note(
        workspace,
        {
            **note,
            "status": "archived",
            "pin_state": "normal" if note.get("pin_state") == "pinned" else note.get("pin_state"),
        },
    )


def _search_score(note: dict[str, Any], query_tokens: list[str]) -> int:
    haystacks = [
        str(note.get("title") or "").lower(),
        str(note.get("body_markdown") or "").lower(),
        " ".join(note.get("tags") or []).lower(),
    ]
    score = 0
    for token in query_tokens:
        for haystack in haystacks:
            if token in haystack:
                score += 3 if haystack == haystacks[0] else 1
    if note.get("pin_state") == "pinned" and note.get("status") == "active":
        score += 2
    return score


def search_project_notes(workspace: str | Path, query_text: str, limit: int = 8) -> dict[str, Any]:
    query_tokens = [token for token in str(query_text or "").lower().split() if token]
    if not query_tokens:
        raise ValueError("query_text is required.")
    paths = _memory_paths(workspace)
    _ensure_memory_dirs(paths)
    matches = []
    for item in list_project_notes(workspace)["items"]:
        note = _load_note(paths, item["note_id"])
        score = _search_score(note, query_tokens)
        if score <= 0:
            continue
        matches.append(
            {
                **_note_summary(note),
                "score": score,
            }
        )
    matches.sort(key=lambda item: (-item["score"], item.get("title") or item.get("note_id")))
    return {
        "workspace_path": paths["workspace_path"],
        "query_text": query_text,
        "matches": matches[: max(1, min(int(limit), 40))],
    }


def pinned_project_notes(workspace: str | Path) -> list[dict[str, Any]]:
    paths = _memory_paths(workspace)
    _ensure_memory_dirs(paths)
    items = []
    for item in list_project_notes(workspace)["items"]:
        if item.get("status") != "active" or item.get("pin_state") != "pinned":
            continue
        items.append(_load_note(paths, item["note_id"]))
    items.sort(key=lambda item: item.get("title") or item.get("note_id"))
    return items


def persist_generated_memory_snapshot(workspace: str | Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    paths = _memory_paths(workspace)
    _ensure_memory_dirs(paths)
    incoming = copy.deepcopy(snapshot or {})
    snapshot_id = sanitize_identifier(incoming.get("snapshot_id"), "")
    existing = (
        _load_generated_snapshot(paths, snapshot_id)
        if snapshot_id and _generated_snapshot_path(paths, snapshot_id).exists()
        else None
    )
    normalized = _normalize_generated_snapshot(incoming, existing=existing)
    _write_json(_generated_snapshot_path(paths, normalized["snapshot_id"]), normalized)
    index = _persist_generated_snapshot_housekeeping(paths)
    items = [item for item in index.get("items", []) if item.get("snapshot_id") != normalized["snapshot_id"]]
    items.append(_generated_snapshot_summary(normalized))
    items.sort(
        key=lambda item: (
            item.get("status") != "active",
            item.get("status") == "archived",
            -(1 if item.get("updated_at") else 0),
            item.get("title") or item.get("snapshot_id"),
        )
    )
    index["items"] = items
    _save_generated_snapshot_index(paths, index)
    _persist_generated_snapshot_housekeeping(paths)
    current_snapshot = _load_generated_snapshot(paths, normalized["snapshot_id"])
    return {
        "workspace_path": paths["workspace_path"],
        "snapshot": copy.deepcopy(current_snapshot),
        "generated_snapshots": list_generated_memory_snapshots(workspace),
    }


def list_generated_memory_snapshots(
    workspace: str | Path,
    *,
    include_expired: bool = False,
    include_archived: bool = False,
) -> dict[str, Any]:
    paths = _memory_paths(workspace)
    _ensure_memory_dirs(paths)
    index = _persist_generated_snapshot_housekeeping(paths)
    items: list[dict[str, Any]] = []
    for item in index.get("items", []):
        status = item.get("status")
        if status == "archived" and not include_archived:
            continue
        if status == "expired" and not include_expired:
            continue
        items.append(copy.deepcopy(item))
    return {
        "workspace_path": paths["workspace_path"],
        "items": items,
        "counts": {
            "total": len(index.get("items", [])),
            "active": sum(1 for item in index.get("items", []) if item.get("status") == "active"),
            "expired": sum(1 for item in index.get("items", []) if item.get("status") == "expired"),
            "archived": sum(1 for item in index.get("items", []) if item.get("status") == "archived"),
        },
    }


def workspace_memory_summary(workspace: str | Path) -> dict[str, Any]:
    payload = list_project_notes(workspace)
    snapshot_payload = list_generated_memory_snapshots(workspace)
    pinned = [item for item in payload.get("items", []) if item.get("status") == "active" and item.get("pin_state") == "pinned"]
    return {
        "note_count": payload["counts"]["total"],
        "active_note_count": payload["counts"]["active"],
        "archived_note_count": payload["counts"]["archived"],
        "pinned_note_count": payload["counts"]["pinned"],
        "generated_snapshot_count": snapshot_payload["counts"]["total"],
        "active_generated_snapshot_count": snapshot_payload["counts"]["active"],
        "expired_generated_snapshot_count": snapshot_payload["counts"]["expired"],
        "pinned_notes": [
            {
                "note_id": item.get("note_id"),
                "title": item.get("title"),
                "tags": copy.deepcopy(item.get("tags") or []),
                "updated_at": item.get("updated_at"),
            }
            for item in pinned[:6]
        ],
        "generated_snapshots": copy.deepcopy(snapshot_payload.get("items") or [])[:6],
    }


def workspace_memory_detail(workspace: str | Path) -> dict[str, Any]:
    payload = list_project_notes(workspace)
    snapshot_payload = list_generated_memory_snapshots(workspace, include_expired=True, include_archived=True)
    return {
        **payload,
        "pinned_notes": [
            {
                **copy.deepcopy(item),
                "body_markdown": _load_note(_memory_paths(workspace), item["note_id"]).get("body_markdown"),
            }
            for item in payload.get("items", [])
            if item.get("status") == "active" and item.get("pin_state") == "pinned"
        ],
        "generated_snapshots": copy.deepcopy(snapshot_payload.get("items") or []),
        "generated_snapshot_counts": copy.deepcopy(snapshot_payload.get("counts") or {}),
    }
