#!/usr/bin/env python3
from __future__ import annotations

import copy
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


def _memory_paths(workspace: str | Path) -> dict[str, Any]:
    base_paths = _ensure_workspace_initialized(workspace)
    return {
        **base_paths,
        "root": Path(base_paths["memory_root"]),
        "notes_root": Path(base_paths["memory_notes_root"]),
        "index_path": Path(base_paths["memory_notes_index"]),
        "revisions_root": Path(base_paths["memory_revisions_root"]),
    }


def _ensure_memory_dirs(paths: dict[str, Any]) -> None:
    for key in ("root", "notes_root", "revisions_root"):
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


def _note_path(paths: dict[str, Any], note_id: str) -> Path:
    return Path(paths["notes_root"]) / f"{sanitize_identifier(note_id, 'note')}.json"


def _revision_dir(paths: dict[str, Any], note_id: str) -> Path:
    return Path(paths["revisions_root"]) / sanitize_identifier(note_id, "note")


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


def workspace_memory_summary(workspace: str | Path) -> dict[str, Any]:
    payload = list_project_notes(workspace)
    pinned = [item for item in payload.get("items", []) if item.get("status") == "active" and item.get("pin_state") == "pinned"]
    return {
        "note_count": payload["counts"]["total"],
        "active_note_count": payload["counts"]["active"],
        "archived_note_count": payload["counts"]["archived"],
        "pinned_note_count": payload["counts"]["pinned"],
        "pinned_notes": [
            {
                "note_id": item.get("note_id"),
                "title": item.get("title"),
                "tags": copy.deepcopy(item.get("tags") or []),
                "updated_at": item.get("updated_at"),
            }
            for item in pinned[:6]
        ],
    }


def workspace_memory_detail(workspace: str | Path) -> dict[str, Any]:
    payload = list_project_notes(workspace)
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
    }
