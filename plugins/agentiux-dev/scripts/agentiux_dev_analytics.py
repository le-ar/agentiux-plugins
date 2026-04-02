#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

from agentiux_dev_lib import _write_json, now_iso, state_root


ANALYTICS_SCHEMA_VERSION = 1
LEARNING_STATUS_VALUES = {"open", "resolved", "archived"}


def _workspace_path_value(workspace: str | Path | None) -> str | None:
    if workspace is None:
        return None
    return str(Path(workspace).expanduser().resolve())


def _workspace_hash_value(workspace: str | Path | None) -> str | None:
    if workspace is None:
        return None
    import hashlib

    return hashlib.sha1(str(Path(workspace).expanduser().resolve()).encode("utf-8")).hexdigest()[:10]


def _analytics_paths(workspace: str | Path | None = None) -> dict[str, Any]:
    resolved_workspace = _workspace_path_value(workspace)
    workspace_hash = _workspace_hash_value(workspace)
    root = state_root() / "analytics"
    learnings_dir = root / "learnings"
    events_root = root / "events"
    month_key = now_iso()[:7]
    return {
        "root": root,
        "index_path": root / "index.json",
        "events_root": events_root,
        "month_dir": events_root / month_key,
        "workspace_events_path": (events_root / month_key / f"{workspace_hash}.jsonl") if workspace_hash else None,
        "learnings_dir": learnings_dir,
        "workspace_path": resolved_workspace,
        "workspace_hash": workspace_hash,
    }


def _ensure_analytics_dirs(paths: dict[str, Any]) -> None:
    for key in ("root", "events_root", "month_dir", "learnings_dir"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)


def _default_analytics_index() -> dict[str, Any]:
    return {
        "schema_version": ANALYTICS_SCHEMA_VERSION,
        "event_counts": {
            "total": 0,
            "by_type": {},
            "by_workspace": {},
        },
        "learning_entries": [],
        "updated_at": now_iso(),
    }


def _load_index(paths: dict[str, Any]) -> dict[str, Any]:
    if not Path(paths["index_path"]).exists():
        return _default_analytics_index()
    try:
        with Path(paths["index_path"]).open() as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return _default_analytics_index()
    payload.setdefault("event_counts", {"total": 0, "by_type": {}, "by_workspace": {}})
    payload.setdefault("learning_entries", [])
    return payload


def _save_index(paths: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    payload["schema_version"] = ANALYTICS_SCHEMA_VERSION
    payload["updated_at"] = now_iso()
    _write_json(Path(paths["index_path"]), payload)
    return payload


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")) + "\n")


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if re.search(r"(password|secret|token|cookie|authorization|storage[_-]?state)", str(key), flags=re.IGNORECASE):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


def append_analytics_event(
    event_type: str,
    workspace: str | Path | None = None,
    *,
    source: str = "system",
    status: str | None = None,
    workstream_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    external_issue: Any = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = _analytics_paths(workspace)
    _ensure_analytics_dirs(paths)
    event = {
        "schema_version": ANALYTICS_SCHEMA_VERSION,
        "event_id": f"evt-{int(time.time() * 1000)}",
        "timestamp": now_iso(),
        "event_type": str(event_type).strip(),
        "source": source,
        "status": status,
        "workspace_path": paths["workspace_path"],
        "workspace_hash": paths["workspace_hash"],
        "workstream_id": workstream_id,
        "task_id": task_id,
        "run_id": run_id,
        "external_issue": copy.deepcopy(external_issue),
        "payload": _redact_payload(payload or {}),
    }
    if paths["workspace_events_path"] is not None:
        _append_jsonl(Path(paths["workspace_events_path"]), event)
    index = _load_index(paths)
    event_counts = index.setdefault("event_counts", {"total": 0, "by_type": {}, "by_workspace": {}})
    event_counts["total"] = int(event_counts.get("total") or 0) + 1
    event_counts.setdefault("by_type", {})
    event_counts["by_type"][event["event_type"]] = int(event_counts["by_type"].get(event["event_type"]) or 0) + 1
    if paths["workspace_hash"]:
        event_counts.setdefault("by_workspace", {})
        event_counts["by_workspace"][paths["workspace_hash"]] = int(event_counts["by_workspace"].get(paths["workspace_hash"]) or 0) + 1
    _save_index(paths, index)
    return event


def _entry_path(paths: dict[str, Any], entry_id: str) -> Path:
    return Path(paths["learnings_dir"]) / f"{entry_id}.json"


def _normalize_learning_entry(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = copy.deepcopy(existing or {})
    entry_id = str(payload.get("entry_id") or existing.get("entry_id") or f"learning-{int(time.time() * 1000)}").strip().lower()
    status = str(payload.get("status") or existing.get("status") or "open").strip().lower()
    if status not in LEARNING_STATUS_VALUES:
        raise ValueError(f"Unsupported learning entry status: {status}")
    workspace_path = _workspace_path_value(payload.get("workspace_path") or existing.get("workspace_path"))
    return {
        "schema_version": ANALYTICS_SCHEMA_VERSION,
        "entry_id": entry_id,
        "kind": str(payload.get("kind") or existing.get("kind") or "general").strip() or "general",
        "status": status,
        "workspace_path": workspace_path,
        "workspace_hash": _workspace_hash_value(workspace_path) if workspace_path else None,
        "workstream_id": payload.get("workstream_id", existing.get("workstream_id")),
        "task_id": payload.get("task_id", existing.get("task_id")),
        "run_id": payload.get("run_id", existing.get("run_id")),
        "external_issue": copy.deepcopy(payload.get("external_issue", existing.get("external_issue"))),
        "symptom": str(payload.get("symptom") or existing.get("symptom") or ""),
        "root_cause": str(payload.get("root_cause") or existing.get("root_cause") or ""),
        "missing_signal": str(payload.get("missing_signal") or existing.get("missing_signal") or ""),
        "fix_applied": str(payload.get("fix_applied") or existing.get("fix_applied") or ""),
        "prevention": str(payload.get("prevention") or existing.get("prevention") or ""),
        "source": str(payload.get("source") or existing.get("source") or "chat"),
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }


def _entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": entry.get("entry_id"),
        "kind": entry.get("kind"),
        "status": entry.get("status"),
        "workspace_path": entry.get("workspace_path"),
        "workspace_hash": entry.get("workspace_hash"),
        "workstream_id": entry.get("workstream_id"),
        "task_id": entry.get("task_id"),
        "run_id": entry.get("run_id"),
        "external_issue": copy.deepcopy(entry.get("external_issue")),
        "symptom": entry.get("symptom"),
        "source": entry.get("source"),
        "updated_at": entry.get("updated_at"),
        "created_at": entry.get("created_at"),
    }


def _load_learning_entry(paths: dict[str, Any], entry_id: str) -> dict[str, Any]:
    path = _entry_path(paths, entry_id)
    if not path.exists():
        raise FileNotFoundError(f"Unknown learning entry: {entry_id}")
    with path.open() as handle:
        return json.load(handle)


def get_learning_entry(entry_id: str, workspace: str | Path | None = None) -> dict[str, Any]:
    paths = _analytics_paths(workspace)
    _ensure_analytics_dirs(paths)
    return _load_learning_entry(paths, entry_id)


def _sync_learning_index(paths: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    index = _load_index(paths)
    items = [item for item in index.get("learning_entries", []) if item.get("entry_id") != entry["entry_id"]]
    items.append(_entry_summary(entry))
    items.sort(key=lambda item: (item.get("status") != "open", item.get("updated_at") or "", item.get("entry_id")), reverse=False)
    index["learning_entries"] = items
    return _save_index(paths, index)


def write_learning_entry(workspace: str | Path | None, entry: dict[str, Any]) -> dict[str, Any]:
    paths = _analytics_paths(workspace or entry.get("workspace_path"))
    _ensure_analytics_dirs(paths)
    normalized = _normalize_learning_entry({**copy.deepcopy(entry), "workspace_path": paths["workspace_path"] or entry.get("workspace_path")})
    _write_json(_entry_path(paths, normalized["entry_id"]), normalized)
    _sync_learning_index(paths, normalized)
    append_analytics_event(
        "learning_entry_created",
        normalized.get("workspace_path"),
        source=normalized.get("source") or "chat",
        status=normalized.get("status"),
        workstream_id=normalized.get("workstream_id"),
        task_id=normalized.get("task_id"),
        run_id=normalized.get("run_id"),
        external_issue=normalized.get("external_issue"),
        payload={"entry_id": normalized["entry_id"], "kind": normalized.get("kind")},
    )
    return {
        "entry": normalized,
        "entries": list_learning_entries(workspace=normalized.get("workspace_path")),
    }


def update_learning_entry(workspace: str | Path | None, entry_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    paths = _analytics_paths(workspace)
    _ensure_analytics_dirs(paths)
    existing = _load_learning_entry(paths, entry_id)
    normalized = _normalize_learning_entry({**copy.deepcopy(existing), **copy.deepcopy(updates)}, existing=existing)
    _write_json(_entry_path(paths, normalized["entry_id"]), normalized)
    _sync_learning_index(paths, normalized)
    event_type = "learning_entry_updated"
    if existing.get("status") != normalized.get("status") and normalized.get("status") in {"resolved", "archived"}:
        event_type = "learning_entry_closed"
    append_analytics_event(
        event_type,
        normalized.get("workspace_path"),
        source=normalized.get("source") or "chat",
        status=normalized.get("status"),
        workstream_id=normalized.get("workstream_id"),
        task_id=normalized.get("task_id"),
        run_id=normalized.get("run_id"),
        external_issue=normalized.get("external_issue"),
        payload={"entry_id": normalized["entry_id"], "kind": normalized.get("kind")},
    )
    return {
        "entry": normalized,
        "entries": list_learning_entries(workspace=normalized.get("workspace_path")),
    }


def list_learning_entries(workspace: str | Path | None = None, status: str | None = None, limit: int | None = 50) -> dict[str, Any]:
    paths = _analytics_paths(workspace)
    _ensure_analytics_dirs(paths)
    items = []
    for item in _load_index(paths).get("learning_entries", []):
        if workspace and item.get("workspace_path") != paths["workspace_path"]:
            continue
        if status and item.get("status") != status:
            continue
        items.append(copy.deepcopy(item))
    items.sort(key=lambda item: (item.get("status") != "open", item.get("updated_at") or ""), reverse=False)
    if limit is not None:
        items = items[: max(1, min(int(limit), 200))]
    return {
        "workspace_path": paths["workspace_path"],
        "items": items,
        "counts": {
            "total": len(items),
            "open": sum(1 for item in items if item.get("status") == "open"),
            "resolved": sum(1 for item in items if item.get("status") == "resolved"),
            "archived": sum(1 for item in items if item.get("status") == "archived"),
        },
    }


def get_analytics_snapshot(workspace: str | Path | None = None) -> dict[str, Any]:
    paths = _analytics_paths(workspace)
    _ensure_analytics_dirs(paths)
    index = _load_index(paths)
    entries_payload = list_learning_entries(workspace=workspace, limit=12 if workspace else 20)
    workspace_event_count = 0
    if paths["workspace_hash"]:
        workspace_event_count = int((index.get("event_counts") or {}).get("by_workspace", {}).get(paths["workspace_hash"]) or 0)
    return {
        "workspace_path": paths["workspace_path"],
        "event_counts": {
            **copy.deepcopy(index.get("event_counts") or {}),
            "workspace_total": workspace_event_count,
        },
        "learning_counts": copy.deepcopy(entries_payload.get("counts") or {}),
        "recent_learning_entries": copy.deepcopy(entries_payload.get("items") or []),
        "updated_at": index.get("updated_at"),
    }


def workspace_analytics_summary(workspace: str | Path) -> dict[str, Any]:
    snapshot = get_analytics_snapshot(workspace)
    return {
        "event_count": int((snapshot.get("event_counts") or {}).get("workspace_total") or 0),
        "learning_entry_count": int((snapshot.get("learning_counts") or {}).get("total") or 0),
        "open_learning_entry_count": int((snapshot.get("learning_counts") or {}).get("open") or 0),
        "resolved_learning_entry_count": int((snapshot.get("learning_counts") or {}).get("resolved") or 0),
    }


def workspace_analytics_detail(workspace: str | Path) -> dict[str, Any]:
    snapshot = get_analytics_snapshot(workspace)
    return {
        **snapshot,
        "learning_entries": list_learning_entries(workspace=workspace, limit=50),
    }
