#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from agentiux_dev_lib import (
    _ensure_workspace_initialized,
    _load_json,
    _load_tasks_index,
    _normalize_task_payload,
    _write_json,
    create_task,
    create_workstream,
    current_workstream,
    now_iso,
    read_stage_register,
    sanitize_identifier,
    set_active_brief,
    slugify,
    workspace_paths,
    write_stage_register,
)


YOUTRACK_SCHEMA_VERSION = 1
YOUTRACK_CONNECTION_SCHEMA_VERSION = 1
YOUTRACK_SEARCH_SCHEMA_VERSION = 1
YOUTRACK_PLAN_SCHEMA_VERSION = 1
YOUTRACK_ISSUE_LEDGER_SCHEMA_VERSION = 1

DEFAULT_SEARCH_PAGE_SIZE = 25
DEFAULT_SHORTLIST_SIZE = 8
DEFAULT_STAGE_TARGET_MINUTES = 240
DEFAULT_STAGE_MAX_ISSUES = 4
DEFAULT_RESULT_SCAN_LIMIT = 1000

ISSUE_FIELDS = ",".join(
    [
        "id",
        "idReadable",
        "summary",
        "description",
        "updated",
        "created",
        "resolved",
        "project(id,shortName,name)",
        "customFields(name,value(name,fullName,localizedName,presentation,text,minutes,id),projectCustomField(field(name)))",
    ]
)


def _youtrack_paths(workspace: str | Path) -> dict[str, Any]:
    base_paths = _ensure_workspace_initialized(workspace)
    root = Path(base_paths["youtrack_root"])
    return {
        **base_paths,
        "root": root,
        "connections_dir": Path(base_paths["youtrack_connections_dir"]),
        "connections_index": Path(base_paths["youtrack_connections_dir"]) / "index.json",
        "current_connection": Path(base_paths["youtrack_connections_dir"]) / "current.json",
        "secrets_dir": Path(base_paths["youtrack_secrets_dir"]),
        "field_catalogs_dir": Path(base_paths["youtrack_field_catalogs_dir"]),
        "searches_dir": Path(base_paths["youtrack_searches_dir"]),
        "current_search": Path(base_paths["youtrack_searches_dir"]) / "current.json",
        "plans_dir": Path(base_paths["youtrack_plans_dir"]),
        "current_plan": Path(base_paths["youtrack_plans_dir"]) / "current.json",
        "issues_dir": Path(base_paths["youtrack_issues_dir"]),
    }


def _ensure_youtrack_dirs(paths: dict[str, Any]) -> None:
    for key in (
        "root",
        "connections_dir",
        "secrets_dir",
        "field_catalogs_dir",
        "searches_dir",
        "plans_dir",
        "issues_dir",
    ):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(Path(paths["secrets_dir"]), 0o700)
    except OSError:
        return


def _connection_record_path(paths: dict[str, Any], connection_id: str) -> Path:
    return Path(paths["connections_dir"]) / f"{sanitize_identifier(connection_id, 'connection')}.json"


def _secret_path(paths: dict[str, Any], connection_id: str) -> Path:
    return Path(paths["secrets_dir"]) / f"{sanitize_identifier(connection_id, 'connection')}.json"


def _field_catalog_path(paths: dict[str, Any], connection_id: str) -> Path:
    return Path(paths["field_catalogs_dir"]) / f"{sanitize_identifier(connection_id, 'connection')}.json"


def _search_session_path(paths: dict[str, Any], session_id: str) -> Path:
    return Path(paths["searches_dir"]) / f"{sanitize_identifier(session_id, 'search')}.json"


def _plan_draft_path(paths: dict[str, Any], plan_id: str) -> Path:
    return Path(paths["plans_dir"]) / f"{sanitize_identifier(plan_id, 'plan')}.json"


def _issue_ledger_path(paths: dict[str, Any], connection_id: str, issue_id: str) -> Path:
    key = sanitize_identifier(f"{connection_id}-{issue_id}", "issue")
    return Path(paths["issues_dir"]) / f"{key}.json"


def _default_connections_index() -> dict[str, Any]:
    return {
        "schema_version": YOUTRACK_SCHEMA_VERSION,
        "default_connection_id": None,
        "items": [],
        "updated_at": now_iso(),
    }


def _load_connections_index(paths: dict[str, Any]) -> dict[str, Any]:
    payload = _load_json(Path(paths["connections_index"]), default=_default_connections_index(), strict=False) or _default_connections_index()
    payload["items"] = copy.deepcopy(payload.get("items") or [])
    return payload


def _save_connections_index(paths: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    payload["updated_at"] = now_iso()
    _write_json(Path(paths["connections_index"]), payload)
    return payload


def _upsert_connection_index_item(paths: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    index = _load_connections_index(paths)
    replaced = False
    for idx, item in enumerate(index.get("items", [])):
        if item.get("connection_id") == record.get("connection_id"):
            index["items"][idx] = copy.deepcopy(record)
            replaced = True
            break
    if not replaced:
        index.setdefault("items", []).append(copy.deepcopy(record))
    _save_connections_index(paths, index)
    return index


def _write_secret(path: Path, payload: dict[str, Any]) -> None:
    _write_json(path, payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def _new_entity_id(prefix: str) -> str:
    return sanitize_identifier(f"{prefix}-{uuid.uuid4().hex[:10]}", prefix)


def _normalize_project_scope(project_scope: str | list[str] | None) -> list[str]:
    if project_scope is None:
        return []
    if isinstance(project_scope, str):
        values = [part.strip() for part in re.split(r"[,\n]+", project_scope) if part.strip()]
        return values
    return [str(item).strip() for item in project_scope if str(item).strip()]


def _normalize_base_url(base_url: str) -> str:
    raw = (base_url or "").strip()
    if not raw:
        raise ValueError("YouTrack base_url is required.")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid YouTrack base_url: {base_url}")
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _default_connection_record(connection_id: str, label: str, base_url: str, project_scope: list[str]) -> dict[str, Any]:
    return {
        "schema_version": YOUTRACK_CONNECTION_SCHEMA_VERSION,
        "connection_id": connection_id,
        "label": label,
        "base_url": _normalize_base_url(base_url),
        "auth_mode": "permanent_token",
        "default": False,
        "status": "configured",
        "last_tested_at": None,
        "last_error": None,
        "field_mapping": {},
        "project_scope": project_scope,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def _normalize_connection_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(record or {})
    payload["schema_version"] = payload.get("schema_version", YOUTRACK_CONNECTION_SCHEMA_VERSION)
    payload["connection_id"] = sanitize_identifier(payload.get("connection_id"), "connection")
    payload["label"] = payload.get("label") or payload["connection_id"]
    payload["base_url"] = _normalize_base_url(payload.get("base_url") or "")
    payload["auth_mode"] = "permanent_token"
    payload["default"] = bool(payload.get("default"))
    payload["status"] = payload.get("status") or "configured"
    payload["project_scope"] = _normalize_project_scope(payload.get("project_scope"))
    payload["field_mapping"] = copy.deepcopy(payload.get("field_mapping") or {})
    payload["created_at"] = payload.get("created_at") or now_iso()
    payload["updated_at"] = now_iso()
    return payload


def _write_connection_record(paths: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_connection_record(record)
    _write_json(_connection_record_path(paths, normalized["connection_id"]), normalized)
    return normalized


def _read_connection_record(paths: dict[str, Any], connection_id: str) -> dict[str, Any]:
    payload = _load_json(_connection_record_path(paths, connection_id), default={}, strict=True, purpose=f"YouTrack connection `{connection_id}`") or {}
    if not payload:
        raise FileNotFoundError(f"Unknown YouTrack connection: {connection_id}")
    return _normalize_connection_record(payload)


def _redact_connection(record: dict[str, Any], *, include_paths: bool = False, paths: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = copy.deepcopy(record)
    payload.pop("token", None)
    payload["has_secret"] = _secret_path(paths, payload["connection_id"]).exists() if include_paths and paths else None
    if include_paths and paths:
        payload["connection_path"] = str(_connection_record_path(paths, payload["connection_id"]))
        payload["field_catalog_path"] = str(_field_catalog_path(paths, payload["connection_id"]))
    return payload


def _connection_secret(paths: dict[str, Any], connection_id: str) -> dict[str, Any]:
    payload = _load_json(_secret_path(paths, connection_id), default={}, strict=True, purpose=f"YouTrack secret `{connection_id}`") or {}
    if not payload.get("token"):
        raise FileNotFoundError(f"Missing token for YouTrack connection: {connection_id}")
    return payload


def _apply_project_scope(query: str, project_scope: list[str]) -> str:
    trimmed = (query or "").strip()
    if not project_scope:
        return trimmed
    lowered = trimmed.lower()
    if "project:" in lowered:
        return trimmed
    if len(project_scope) == 1:
        prefix = f"project: {project_scope[0]}"
    else:
        joined = ", ".join(project_scope)
        prefix = f"project: {joined}"
    return f"{prefix} {trimmed}".strip()


def _request_json(
    connection: dict[str, Any],
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None}, doseq=True)
    url = f"{connection['base_url'].rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"YouTrack API request failed ({exc.code}) for {path}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Unable to reach YouTrack at {connection['base_url']}: {exc.reason}") from exc


def _flatten_custom_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_flatten_custom_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    for key in ("minutes", "presentation", "name", "localizedName", "fullName", "text", "id"):
        if value.get(key) not in {None, ""}:
            return value[key]
    return copy.deepcopy(value)


def _custom_field_map(issue: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for custom_field in issue.get("customFields") or []:
        field_name = (
            custom_field.get("name")
            or ((custom_field.get("projectCustomField") or {}).get("field") or {}).get("name")
            or "custom-field"
        )
        mapping[field_name] = _flatten_custom_value(custom_field.get("value"))
    return mapping


def _duration_to_minutes(value: Any) -> int | None:
    if isinstance(value, dict) and isinstance(value.get("minutes"), int):
        return int(value["minutes"])
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.lower()
        total = 0
        for amount, unit in re.findall(r"(\d+)\s*([dhm])", text):
            count = int(amount)
            if unit == "d":
                total += count * 8 * 60
            elif unit == "h":
                total += count * 60
            else:
                total += count
        if total:
            return total
        digits = re.findall(r"\d+", text)
        if digits:
            return int(digits[0])
    return None


def _has_signal(value: Any) -> bool:
    return value not in (None, "", [], {})


def _text_numeric(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        digits = re.findall(r"-?\d+", value)
        if digits:
            return int(digits[0])
    return None


def _infer_field_mapping(fields: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapping = {
        "priority": [],
        "severity": [],
        "estimate": [],
        "state": [],
        "assignee": [],
        "risk": [],
        "dependency": [],
    }
    for field in fields:
        name = str(field.get("name") or "").lower()
        if "priority" in name:
            mapping["priority"].append(field["name"])
        if "severity" in name:
            mapping["severity"].append(field["name"])
        if any(token in name for token in ("estimate", "estimation", "story point", "storypoint")):
            mapping["estimate"].append(field["name"])
        if any(token in name for token in ("state", "status")):
            mapping["state"].append(field["name"])
        if any(token in name for token in ("assignee", "owner")):
            mapping["assignee"].append(field["name"])
        if any(token in name for token in ("risk", "impact")):
            mapping["risk"].append(field["name"])
        if any(token in name for token in ("dependency", "depends", "blocker")):
            mapping["dependency"].append(field["name"])
    return mapping


def _field_catalog_from_connection(connection: dict[str, Any], token: str) -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    project_error = None
    field_error = None
    try:
        projects = _request_json(connection, token, "GET", "/api/admin/projects", params={"fields": "id,shortName,name"}) or []
    except RuntimeError as exc:
        project_error = str(exc)
    try:
        fields = _request_json(connection, token, "GET", "/api/admin/customFieldSettings/customFields", params={"fields": "id,name"}) or []
    except RuntimeError as exc:
        field_error = str(exc)
    return {
        "schema_version": YOUTRACK_SCHEMA_VERSION,
        "connection_id": connection["connection_id"],
        "projects": projects,
        "fields": fields,
        "field_mapping": _infer_field_mapping(fields),
        "project_error": project_error,
        "field_error": field_error,
        "updated_at": now_iso(),
    }


def _severity_weight(value: Any) -> int:
    text = str(value or "").lower()
    if any(token in text for token in ("blocker", "critical", "showstopper")):
        return 5
    if any(token in text for token in ("major", "high", "severe")):
        return 4
    if any(token in text for token in ("normal", "medium")):
        return 3
    if any(token in text for token in ("minor", "low")):
        return 2
    return 1


def _risk_weight(value: Any) -> int:
    text = str(value or "").lower()
    if any(token in text for token in ("critical", "high", "severe")):
        return 4
    if any(token in text for token in ("medium", "moderate")):
        return 2
    if any(token in text for token in ("low", "minor")):
        return 1
    numeric = _text_numeric(value)
    return max(min(int(numeric or 0), 4), 0)


def _extract_issue_metrics(custom_fields: dict[str, Any], field_mapping: dict[str, list[str]]) -> dict[str, Any]:
    priority_value = None
    severity_value = None
    risk_value = None
    estimate_minutes = None
    state_value = None
    dependency_flag = False
    for field_name in field_mapping.get("priority", []):
        if field_name in custom_fields and priority_value is None:
            priority_value = _text_numeric(custom_fields[field_name])
    for field_name in field_mapping.get("severity", []):
        if field_name in custom_fields and severity_value is None:
            severity_value = custom_fields[field_name]
    for field_name in field_mapping.get("risk", []):
        if field_name in custom_fields and risk_value is None:
            risk_value = custom_fields[field_name]
    for field_name in field_mapping.get("estimate", []):
        if field_name in custom_fields and estimate_minutes is None:
            estimate_minutes = _duration_to_minutes(custom_fields[field_name])
    for field_name in field_mapping.get("state", []):
        if field_name in custom_fields and state_value is None:
            state_value = custom_fields[field_name]
    for field_name in field_mapping.get("dependency", []):
        value = custom_fields.get(field_name)
        if _has_signal(value):
            dependency_flag = True
            break
    return {
        "priority_value": priority_value or 0,
        "severity_value": severity_value,
        "severity_weight": _severity_weight(severity_value),
        "risk_value": risk_value,
        "risk_weight": _risk_weight(risk_value),
        "estimate_minutes": estimate_minutes,
        "state_value": state_value,
        "dependency_flag": dependency_flag,
    }


def _codex_estimate_minutes(issue: dict[str, Any]) -> int:
    estimate = issue.get("youtrack_estimate_minutes")
    summary = str(issue.get("summary") or "").lower()
    multiplier = 1.0
    if any(token in summary for token in ("refactor", "migrate", "architecture", "rewrite")):
        multiplier = 1.25
    elif any(token in summary for token in ("copy", "text", "spacing", "typo", "button", "small")):
        multiplier = 0.8
    if estimate:
        adjusted = max(15, int(round(estimate * multiplier / 15.0)) * 15)
        return adjusted
    base = 120
    if any(token in summary for token in ("critical", "outage", "broken")):
        base = 90
    if any(token in summary for token in ("small", "typo", "copy", "text", "padding")):
        base = 30
    return base


def _score_issue(issue: dict[str, Any]) -> dict[str, Any]:
    priority_score = int(issue.get("priority_value") or 0) * 8
    severity_score = int(issue.get("severity_weight") or 1) * 10
    risk_score = int(issue.get("risk_weight") or 0) * 6
    estimate_minutes = issue.get("youtrack_estimate_minutes")
    quick_win_bonus = 8 if estimate_minutes and estimate_minutes <= 60 else 0
    estimate_penalty = min(int((estimate_minutes or 0) / 30), 12)
    unresolved_bonus = 4 if not issue.get("resolved") else -2
    dependency_penalty = 6 if issue.get("dependency_flag") else 0
    score = priority_score + severity_score + risk_score + quick_win_bonus + unresolved_bonus - estimate_penalty - dependency_penalty
    return {
        "score": score,
        "breakdown": {
            "priority_score": priority_score,
            "severity_score": severity_score,
            "risk_score": risk_score,
            "quick_win_bonus": quick_win_bonus,
            "estimate_penalty": estimate_penalty,
            "unresolved_bonus": unresolved_bonus,
            "dependency_penalty": dependency_penalty,
        },
    }


def _normalize_issue(connection: dict[str, Any], issue: dict[str, Any], field_mapping: dict[str, list[str]]) -> dict[str, Any]:
    custom_fields = _custom_field_map(issue)
    metrics = _extract_issue_metrics(custom_fields, field_mapping)
    normalized = {
        "issue_id": issue.get("idReadable") or issue.get("id"),
        "issue_key": issue.get("idReadable") or issue.get("id"),
        "issue_url": f"{connection['base_url'].rstrip('/')}/issue/{issue.get('idReadable') or issue.get('id')}",
        "summary": issue.get("summary") or "Untitled issue",
        "description": issue.get("description"),
        "project": copy.deepcopy(issue.get("project") or {}),
        "created": issue.get("created"),
        "updated": issue.get("updated"),
        "resolved": issue.get("resolved"),
        "custom_fields": custom_fields,
        "priority_value": metrics["priority_value"],
        "severity_value": metrics["severity_value"],
        "severity_weight": metrics["severity_weight"],
        "risk_value": metrics["risk_value"],
        "risk_weight": metrics["risk_weight"],
        "dependency_flag": metrics["dependency_flag"],
        "state_value": metrics["state_value"],
        "youtrack_estimate_minutes": metrics["estimate_minutes"],
        "youtrack_spent_minutes": None,
        "codex_estimate_minutes": None,
        "score": 0,
        "score_breakdown": {},
    }
    normalized["codex_estimate_minutes"] = _codex_estimate_minutes(normalized)
    score = _score_issue(normalized)
    normalized["score"] = score["score"]
    normalized["score_breakdown"] = score["breakdown"]
    return normalized


def _attach_spent_minutes(connection: dict[str, Any], token: str, issue_payload: dict[str, Any]) -> dict[str, Any]:
    issue_key = issue_payload["issue_key"]
    items = _request_json(
        connection,
        token,
        "GET",
        "/api/workItems",
        params={
            "query": f"issue id: {issue_key}",
            "$top": 100,
            "fields": "id,date,duration(minutes,presentation),text",
        },
    ) or []
    total_minutes = 0
    for item in items:
        total_minutes += _duration_to_minutes(item.get("duration")) or 0
    enriched = copy.deepcopy(issue_payload)
    enriched["youtrack_spent_minutes"] = total_minutes
    enriched["work_items"] = items
    return enriched


def _structured_filters_from_query(query: str, project_scope: list[str]) -> dict[str, Any]:
    text = (query or "").strip()
    return {
        "raw": text,
        "project_scope": list(project_scope),
        "mentions_assignee": "assignee:" in text.lower() or "for:" in text.lower(),
        "mentions_priority": "priority:" in text.lower(),
        "mentions_severity": "severity:" in text.lower(),
        "mentions_state": "state:" in text.lower(),
    }


def _read_field_catalog(paths: dict[str, Any], connection_id: str) -> dict[str, Any]:
    return _load_json(_field_catalog_path(paths, connection_id), default={"field_mapping": {}, "fields": [], "projects": []}, strict=False) or {
        "field_mapping": {},
        "fields": [],
        "projects": [],
    }


def _write_field_catalog(paths: dict[str, Any], connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    catalog = copy.deepcopy(payload)
    catalog["updated_at"] = now_iso()
    _write_json(_field_catalog_path(paths, connection_id), catalog)
    return catalog


def list_youtrack_connections(workspace: str | Path) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    _ensure_youtrack_dirs(paths)
    index = _load_connections_index(paths)
    items = []
    for item in index.get("items", []):
        try:
            record = _read_connection_record(paths, item["connection_id"])
        except FileNotFoundError:
            continue
        items.append(_redact_connection(record, include_paths=True, paths=paths))
    return {
        "workspace_path": paths["workspace_path"],
        "default_connection_id": index.get("default_connection_id"),
        "items": sorted(items, key=lambda item: (not bool(item.get("default")), item["label"].lower())),
        "updated_at": index.get("updated_at"),
    }


show_youtrack_connections = list_youtrack_connections


def _persist_default_connection(paths: dict[str, Any], connection_id: str | None) -> None:
    _write_json(Path(paths["current_connection"]), {"connection_id": connection_id, "updated_at": now_iso()})


def _set_default_flag(paths: dict[str, Any], connection_id: str | None) -> None:
    index = _load_connections_index(paths)
    index["default_connection_id"] = connection_id
    rewritten_items = []
    for item in index.get("items", []):
        try:
            record = _read_connection_record(paths, item["connection_id"])
        except FileNotFoundError:
            continue
        record["default"] = record.get("connection_id") == connection_id
        rewritten_items.append(_write_connection_record(paths, record))
    index["items"] = rewritten_items
    _save_connections_index(paths, index)
    _persist_default_connection(paths, connection_id)


def _resolve_connection_id(paths: dict[str, Any], connection_id: str | None) -> str:
    if connection_id:
        return sanitize_identifier(connection_id, "")
    index = _load_connections_index(paths)
    default_connection_id = index.get("default_connection_id")
    if default_connection_id:
        return sanitize_identifier(default_connection_id, "")
    if len(index.get("items", [])) == 1:
        return sanitize_identifier(index["items"][0]["connection_id"], "")
    raise ValueError("YouTrack connection_id is required because no default connection is configured.")


def connect_youtrack(
    workspace: str | Path,
    *,
    base_url: str,
    token: str,
    label: str | None = None,
    connection_id: str | None = None,
    project_scope: str | list[str] | None = None,
    default: bool = False,
    test_connection: bool = True,
) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    _ensure_youtrack_dirs(paths)
    normalized_base_url = _normalize_base_url(base_url)
    connection_key = sanitize_identifier(connection_id or label or urllib.parse.urlparse(normalized_base_url).netloc, "youtrack")
    record = _default_connection_record(connection_key, label or connection_key, normalized_base_url, _normalize_project_scope(project_scope))
    _write_connection_record(paths, record)
    _write_secret(
        _secret_path(paths, connection_key),
        {
            "schema_version": YOUTRACK_SCHEMA_VERSION,
            "connection_id": connection_key,
            "token": token,
            "updated_at": now_iso(),
        },
    )
    index = _upsert_connection_index_item(paths, record)
    if default or len(index.get("items", [])) == 1:
        index["default_connection_id"] = connection_key
        record["default"] = True
    _save_connections_index(paths, _upsert_connection_index_item(paths, record) | {"default_connection_id": index.get("default_connection_id")})
    if record["default"]:
        _set_default_flag(paths, connection_key)
    if test_connection:
        tested = test_youtrack_connection(workspace, connection_key)
        return {
            "workspace_path": paths["workspace_path"],
            "created_connection_id": connection_key,
            "connection": tested["connection"],
            "connections": list_youtrack_connections(workspace),
            "field_catalog": tested["field_catalog"],
        }
    return {
        "workspace_path": paths["workspace_path"],
        "created_connection_id": connection_key,
        "connection": _redact_connection(record, include_paths=True, paths=paths),
        "connections": list_youtrack_connections(workspace),
    }


def update_youtrack_connection(
    workspace: str | Path,
    connection_id: str,
    *,
    label: str | None = None,
    base_url: str | None = None,
    token: str | None = None,
    project_scope: str | list[str] | None = None,
    default: bool | None = None,
    test_connection: bool = True,
) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    record = _read_connection_record(paths, connection_id)
    if label is not None:
        record["label"] = label
    if base_url is not None:
        record["base_url"] = _normalize_base_url(base_url)
    if project_scope is not None:
        record["project_scope"] = _normalize_project_scope(project_scope)
    if default is not None:
        record["default"] = bool(default)
    record = _write_connection_record(paths, record)
    if token is not None:
        _write_secret(
            _secret_path(paths, connection_id),
            {
                "schema_version": YOUTRACK_SCHEMA_VERSION,
                "connection_id": connection_id,
                "token": token,
                "updated_at": now_iso(),
            },
        )
    _upsert_connection_index_item(paths, record)
    if record.get("default"):
        _set_default_flag(paths, connection_id)
    elif _load_connections_index(paths).get("default_connection_id") == connection_id:
        _set_default_flag(paths, None)
    if test_connection:
        tested = test_youtrack_connection(workspace, connection_id)
        return {
            "workspace_path": paths["workspace_path"],
            "updated_connection_id": connection_id,
            "connection": tested["connection"],
            "connections": list_youtrack_connections(workspace),
            "field_catalog": tested["field_catalog"],
        }
    return {
        "workspace_path": paths["workspace_path"],
        "updated_connection_id": connection_id,
        "connection": _redact_connection(record, include_paths=True, paths=paths),
        "connections": list_youtrack_connections(workspace),
    }


def remove_youtrack_connection(workspace: str | Path, connection_id: str) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    normalized_id = sanitize_identifier(connection_id, "")
    index = _load_connections_index(paths)
    index["items"] = [item for item in index.get("items", []) if item.get("connection_id") != normalized_id]
    if index.get("default_connection_id") == normalized_id:
        index["default_connection_id"] = index["items"][0]["connection_id"] if index["items"] else None
    _save_connections_index(paths, index)
    for file_path in (
        _connection_record_path(paths, normalized_id),
        _secret_path(paths, normalized_id),
        _field_catalog_path(paths, normalized_id),
    ):
        if file_path.exists():
            file_path.unlink()
    _persist_default_connection(paths, index.get("default_connection_id"))
    if index.get("default_connection_id"):
        _set_default_flag(paths, index["default_connection_id"])
    return {
        "workspace_path": paths["workspace_path"],
        "removed_connection_id": normalized_id,
        "connections": list_youtrack_connections(workspace),
    }


def test_youtrack_connection(workspace: str | Path, connection_id: str) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    record = _read_connection_record(paths, connection_id)
    secret = _connection_secret(paths, connection_id)
    try:
        me = _request_json(record, secret["token"], "GET", "/api/users/me", params={"fields": "id,login,name"})
        field_catalog = _field_catalog_from_connection(record, secret["token"])
        _write_field_catalog(paths, connection_id, field_catalog)
        record["status"] = "connected"
        record["field_mapping"] = copy.deepcopy(field_catalog.get("field_mapping") or {})
        record["last_error"] = None
    except RuntimeError as exc:
        me = None
        field_catalog = _read_field_catalog(paths, connection_id)
        record["status"] = "invalid"
        record["last_error"] = str(exc)
    record["last_tested_at"] = now_iso()
    record = _write_connection_record(paths, record)
    _upsert_connection_index_item(paths, record)
    return {
        "workspace_path": paths["workspace_path"],
        "connection": _redact_connection(record, include_paths=True, paths=paths),
        "field_catalog": field_catalog,
        "account": me,
    }


def _save_current_pointer(path: Path, key: str, value: str | None) -> None:
    _write_json(path, {key: value, "updated_at": now_iso()})


def _read_search_session(paths: dict[str, Any], session_id: str) -> dict[str, Any]:
    payload = _load_json(_search_session_path(paths, session_id), default={}, strict=True, purpose=f"YouTrack search session `{session_id}`") or {}
    if not payload:
        raise FileNotFoundError(f"Unknown YouTrack search session: {session_id}")
    return payload


def _read_plan_draft(paths: dict[str, Any], plan_id: str) -> dict[str, Any]:
    payload = _load_json(_plan_draft_path(paths, plan_id), default={}, strict=True, purpose=f"YouTrack plan draft `{plan_id}`") or {}
    if not payload:
        raise FileNotFoundError(f"Unknown YouTrack plan draft: {plan_id}")
    return payload


def _maybe_read_search_session(paths: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    try:
        return _read_search_session(paths, session_id)
    except FileNotFoundError:
        return None


def _maybe_read_plan_draft(paths: dict[str, Any], plan_id: str | None) -> dict[str, Any] | None:
    if not plan_id:
        return None
    try:
        return _read_plan_draft(paths, plan_id)
    except FileNotFoundError:
        return None


def _issue_snapshot(paths: dict[str, Any], connection_id: str, issue_id: str) -> dict[str, Any] | None:
    return _load_json(_issue_ledger_path(paths, connection_id, issue_id), default=None, strict=False)


def _merged_issue_snapshot(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy(existing or {})
    for key, value in (incoming or {}).items():
        if _has_signal(value) or key not in snapshot:
            snapshot[key] = copy.deepcopy(value)
    if not snapshot.get("custom_fields") and snapshot.get("field_snapshot"):
        snapshot["custom_fields"] = copy.deepcopy(snapshot["field_snapshot"])
    if not snapshot.get("field_snapshot") and snapshot.get("custom_fields"):
        snapshot["field_snapshot"] = copy.deepcopy(snapshot["custom_fields"])
    return snapshot


def update_issue_snapshot(workspace: str | Path, issue_payload: dict[str, Any], *, connection_id: str) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    issue_id = issue_payload["issue_id"]
    path = _issue_ledger_path(paths, connection_id, issue_id)
    existing = _load_json(path, default={}, strict=False) or {}
    latest_snapshot = _merged_issue_snapshot(existing.get("latest_snapshot") or {}, issue_payload)
    youtrack_estimate_minutes = issue_payload.get("youtrack_estimate_minutes")
    if youtrack_estimate_minutes is None:
        youtrack_estimate_minutes = existing.get("youtrack_estimate_minutes")
    youtrack_spent_minutes = issue_payload.get("youtrack_spent_minutes")
    if youtrack_spent_minutes is None:
        youtrack_spent_minutes = existing.get("youtrack_spent_minutes")
    codex_estimate_minutes = issue_payload.get("codex_estimate_minutes")
    if codex_estimate_minutes is None:
        codex_estimate_minutes = existing.get("codex_estimate_minutes")
    ledger = {
        "schema_version": YOUTRACK_ISSUE_LEDGER_SCHEMA_VERSION,
        "connection_id": connection_id,
        "issue_id": issue_id,
        "issue_key": latest_snapshot.get("issue_key") or existing.get("issue_key"),
        "issue_url": latest_snapshot.get("issue_url") or existing.get("issue_url"),
        "latest_snapshot": latest_snapshot,
        "youtrack_estimate_minutes": youtrack_estimate_minutes,
        "youtrack_spent_minutes": youtrack_spent_minutes,
        "codex_estimate_minutes": codex_estimate_minutes,
        "codex_total_minutes": existing.get("codex_total_minutes", 0),
        "time_entries": copy.deepcopy(existing.get("time_entries") or []),
        "linked_task_ids": copy.deepcopy(existing.get("linked_task_ids") or []),
        "latest_commit": copy.deepcopy(existing.get("latest_commit")),
        "updated_at": now_iso(),
    }
    _write_json(path, ledger)
    return ledger


def read_issue_ledger(workspace: str | Path, *, connection_id: str, issue_id: str) -> dict[str, Any] | None:
    paths = _youtrack_paths(workspace)
    return _load_json(_issue_ledger_path(paths, connection_id, issue_id), default=None, strict=False)


def recompute_issue_ledger(workspace: str | Path, *, connection_id: str, issue_id: str) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    existing = _load_json(_issue_ledger_path(paths, connection_id, issue_id), default={}, strict=False) or {}
    tasks_index = _load_tasks_index(workspace_paths(workspace))
    linked_tasks = []
    time_entries = []
    codex_total_minutes = 0
    codex_estimate_minutes = None
    latest_commit = None
    latest_task_timestamp = ""
    latest_commit_timestamp = ""
    for item in tasks_index.get("items", []):
        task = _normalize_task_payload(item)
        external_issue = task.get("external_issue") or {}
        if external_issue.get("connection_id") != connection_id or external_issue.get("issue_id") != issue_id:
            continue
        linked_tasks.append(task["task_id"])
        task_timestamp = str(task.get("updated_at") or task.get("created_at") or "")
        if task.get("codex_estimate_minutes") is not None and task_timestamp >= latest_task_timestamp:
            codex_estimate_minutes = task.get("codex_estimate_minutes")
            latest_task_timestamp = task_timestamp
        if task.get("latest_commit") and str(task["latest_commit"].get("recorded_at") or "") >= latest_commit_timestamp:
            latest_commit = task["latest_commit"]
            latest_commit_timestamp = str(task["latest_commit"].get("recorded_at") or "")
        tracking = task.get("time_tracking") or {}
        for entry in tracking.get("entries", []):
            normalized_entry = {
                "started_at": entry.get("started_at"),
                "ended_at": entry.get("ended_at"),
                "minutes": int(entry.get("minutes") or 0),
                "session_id": entry.get("session_id"),
                "workstream_id": task.get("linked_workstream_id"),
                "task_id": task.get("task_id"),
                "stage_id": task.get("stage_id"),
            }
            codex_total_minutes += normalized_entry["minutes"]
            time_entries.append(normalized_entry)
    time_entries.sort(key=lambda item: (item.get("started_at") or "", item.get("ended_at") or "", item.get("task_id") or ""))
    latest_snapshot = copy.deepcopy(existing.get("latest_snapshot") or {})
    ledger = {
        "schema_version": YOUTRACK_ISSUE_LEDGER_SCHEMA_VERSION,
        "connection_id": connection_id,
        "issue_id": issue_id,
        "issue_key": existing.get("issue_key") or latest_snapshot.get("issue_key") or issue_id,
        "issue_url": existing.get("issue_url") or latest_snapshot.get("issue_url"),
        "latest_snapshot": latest_snapshot,
        "youtrack_estimate_minutes": existing.get("youtrack_estimate_minutes") or latest_snapshot.get("youtrack_estimate_minutes"),
        "youtrack_spent_minutes": existing.get("youtrack_spent_minutes") or latest_snapshot.get("youtrack_spent_minutes"),
        "codex_estimate_minutes": codex_estimate_minutes if codex_estimate_minutes is not None else existing.get("codex_estimate_minutes"),
        "codex_total_minutes": codex_total_minutes,
        "time_entries": time_entries,
        "linked_task_ids": linked_tasks,
        "latest_commit": latest_commit,
        "updated_at": now_iso(),
    }
    _write_json(_issue_ledger_path(paths, connection_id, issue_id), ledger)
    return ledger


def _fetch_matching_issues(
    connection: dict[str, Any],
    token: str,
    resolved_query: str,
    *,
    batch_size: int = 100,
    max_items: int = DEFAULT_RESULT_SCAN_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    items: list[dict[str, Any]] = []
    skip = 0
    exact = True
    while len(items) < max_items:
        top = min(batch_size, max_items - len(items))
        batch = _request_json(
            connection,
            token,
            "GET",
            "/api/issues",
            params={
                "query": resolved_query,
                "$top": top,
                "$skip": skip,
                "fields": ISSUE_FIELDS,
            },
        ) or []
        items.extend(batch)
        if len(batch) < top:
            return items, exact
        skip += len(batch)
    probe = _request_json(
        connection,
        token,
        "GET",
        "/api/issues",
        params={
            "query": resolved_query,
            "$top": 1,
            "$skip": skip,
            "fields": "id",
        },
    ) or []
    if probe:
        exact = False
    return items, exact


def _session_issue_payloads(session: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for item in session.get("shortlist") or []:
        payloads[item["issue_id"]] = copy.deepcopy(item)
    for item in ((session.get("shortlist_page") or {}).get("items") or []):
        payloads[item["issue_id"]] = copy.deepcopy(item)
    return payloads


def search_youtrack_issues(
    workspace: str | Path,
    *,
    query_text: str,
    connection_id: str | None = None,
    page_size: int = DEFAULT_SEARCH_PAGE_SIZE,
    skip: int = 0,
    shortlist_size: int = DEFAULT_SHORTLIST_SIZE,
) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    resolved_connection_id = _resolve_connection_id(paths, connection_id)
    connection = _read_connection_record(paths, resolved_connection_id)
    secret = _connection_secret(paths, resolved_connection_id)
    field_catalog = _read_field_catalog(paths, resolved_connection_id)
    if not field_catalog.get("field_mapping"):
        field_catalog = _field_catalog_from_connection(connection, secret["token"])
        _write_field_catalog(paths, resolved_connection_id, field_catalog)
    resolved_query = _apply_project_scope(query_text, connection.get("project_scope") or [])
    issue_rows, result_count_exact = _fetch_matching_issues(connection, secret["token"], resolved_query)
    ranked_matches = [_normalize_issue(connection, item, field_catalog.get("field_mapping") or {}) for item in issue_rows]
    ranked_matches.sort(key=lambda item: (-item["score"], item.get("youtrack_estimate_minutes") or 99999, item["issue_key"]))
    for issue_payload in ranked_matches:
        update_issue_snapshot(workspace, issue_payload, connection_id=resolved_connection_id)
    safe_skip = max(skip, 0)
    safe_page_size = max(page_size, 1)
    safe_shortlist_size = max(shortlist_size, 1)
    page_items = ranked_matches[safe_skip : safe_skip + safe_page_size]
    shortlist: list[dict[str, Any]] = []
    shortlist_ids = {item["issue_id"] for item in page_items[:safe_shortlist_size]}
    for issue_payload in page_items:
        if issue_payload["issue_id"] in shortlist_ids:
            enriched = _attach_spent_minutes(connection, secret["token"], issue_payload)
            shortlist.append(enriched)
            update_issue_snapshot(workspace, enriched, connection_id=resolved_connection_id)
    page_payloads = []
    shortlist_by_id = {item["issue_id"]: item for item in shortlist}
    for issue_payload in page_items:
        page_payloads.append(copy.deepcopy(shortlist_by_id.get(issue_payload["issue_id"], issue_payload)))
    has_more = safe_skip + safe_page_size < len(ranked_matches) or not result_count_exact
    session_id = _new_entity_id("yt-search")
    session = {
        "schema_version": YOUTRACK_SEARCH_SCHEMA_VERSION,
        "session_id": session_id,
        "connection_id": resolved_connection_id,
        "raw_query": query_text,
        "resolved_query": resolved_query,
        "structured_filters": _structured_filters_from_query(query_text, connection.get("project_scope") or []),
        "result_count": len(ranked_matches),
        "result_count_exact": result_count_exact,
        "page_cursor": {
            "skip": safe_skip,
            "page_size": safe_page_size,
            "has_more": has_more,
            "next_skip": safe_skip + safe_page_size if has_more else None,
        },
        "shortlist_page": {
            "skip": safe_skip,
            "page_size": safe_page_size,
            "returned_count": len(page_payloads),
            "issue_ids": [item["issue_id"] for item in page_payloads],
            "items": page_payloads,
        },
        "shortlist": shortlist,
        "selected_issue_ids": [],
        "rejected_issue_ids": [],
        "scoring_metadata": {
            "field_mapping": field_catalog.get("field_mapping") or {},
            "strategy": "priority-severity-risk-estimate heuristic",
            "ranked_result_count": len(ranked_matches),
        },
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "selection_updated_at": None,
    }
    _write_json(_search_session_path(paths, session_id), session)
    _save_current_pointer(Path(paths["current_search"]), "session_id", session_id)
    return {
        "workspace_path": paths["workspace_path"],
        "connection": _redact_connection(connection, include_paths=True, paths=paths),
        "search_session": session,
    }


def show_youtrack_issue_queue(workspace: str | Path, *, search_session_id: str | None = None) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    if not search_session_id:
        current = _load_json(Path(paths["current_search"]), default={}, strict=False) or {}
        search_session_id = current.get("session_id")
    if not search_session_id:
        raise FileNotFoundError("No YouTrack search session is available for this workspace.")
    session = _read_search_session(paths, search_session_id)
    return {
        "workspace_path": paths["workspace_path"],
        "connection": _redact_connection(_read_connection_record(paths, session["connection_id"]), include_paths=True, paths=paths),
        "search_session": session,
    }


def _selected_issue_payloads(paths: dict[str, Any], session: dict[str, Any], selected_issue_ids: list[str]) -> list[dict[str, Any]]:
    payloads = _session_issue_payloads(session)
    selected: list[dict[str, Any]] = []
    for issue_id in selected_issue_ids:
        if issue_id in payloads:
            selected.append(copy.deepcopy(payloads[issue_id]))
            continue
        ledger = _issue_snapshot(paths, session["connection_id"], issue_id) or {}
        snapshot = copy.deepcopy((ledger.get("latest_snapshot") or {}))
        if snapshot:
            snapshot["youtrack_estimate_minutes"] = ledger.get("youtrack_estimate_minutes", snapshot.get("youtrack_estimate_minutes"))
            snapshot["youtrack_spent_minutes"] = ledger.get("youtrack_spent_minutes", snapshot.get("youtrack_spent_minutes"))
            snapshot["codex_estimate_minutes"] = ledger.get("codex_estimate_minutes", snapshot.get("codex_estimate_minutes"))
            selected.append(snapshot)
            continue
        raise FileNotFoundError(f"Issue `{issue_id}` is not available in the cached search snapshot for session `{session['session_id']}`.")
    return selected

def _proposal_from_issue(issue: dict[str, Any], stage_id: str) -> dict[str, Any]:
    issue_key = issue["issue_key"]
    task_id = sanitize_identifier(issue_key, issue_key.lower())
    return {
        "task_id": task_id,
        "title": f"{issue_key} {issue['summary']}",
        "objective": issue["summary"],
        "stage_id": stage_id,
        "issue_id": issue["issue_id"],
        "issue_key": issue_key,
        "issue_url": issue["issue_url"],
        "summary": issue["summary"],
        "youtrack_estimate_minutes": issue.get("youtrack_estimate_minutes"),
        "youtrack_spent_minutes": issue.get("youtrack_spent_minutes"),
        "codex_estimate_minutes": issue.get("codex_estimate_minutes"),
        "score": issue.get("score"),
        "branch_hint": f"task/{slugify(issue_key)}-{slugify(issue['summary'])[:32]}",
        "external_issue": {
            "connection_id": None,
            "issue_id": issue["issue_id"],
            "issue_key": issue_key,
            "issue_url": issue["issue_url"],
            "summary": issue["summary"],
            "field_snapshot": copy.deepcopy(issue.get("custom_fields") or {}),
            "youtrack_estimate_minutes": issue.get("youtrack_estimate_minutes"),
            "youtrack_spent_minutes": issue.get("youtrack_spent_minutes"),
        },
    }


def _auto_select_issue_ids(session: dict[str, Any]) -> list[str]:
    shortlist = session.get("shortlist") or []
    return [item["issue_id"] for item in shortlist[: min(5, len(shortlist))]]


def _build_stage_batches(selected_issues: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stages: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    current_batch: list[dict[str, Any]] = []
    current_minutes = 0

    def flush() -> None:
        nonlocal current_batch, current_minutes
        if not current_batch:
            return
        stage_number = len(stages) + 1
        stage_id = f"stage-{stage_number}"
        stage_title = "Quick wins" if all((item.get("codex_estimate_minutes") or 0) <= 60 for item in current_batch) else f"Batch {stage_number}"
        stage_proposals = [_proposal_from_issue(item, stage_id) for item in current_batch]
        objective = ", ".join(proposal["issue_key"] for proposal in stage_proposals)
        stages.append(
            {
                "id": stage_id,
                "title": stage_title,
                "objective": f"Resolve {objective}.",
                "canonical_execution_slices": [proposal["task_id"] for proposal in stage_proposals],
                "source_issue_ids": [proposal["issue_id"] for proposal in stage_proposals],
            }
        )
        proposals.extend(stage_proposals)
        current_batch = []
        current_minutes = 0

    for issue in selected_issues:
        estimate = issue.get("codex_estimate_minutes") or 60
        if current_batch and (len(current_batch) >= DEFAULT_STAGE_MAX_ISSUES or current_minutes + estimate > DEFAULT_STAGE_TARGET_MINUTES):
            flush()
        current_batch.append(issue)
        current_minutes += estimate
    flush()
    return stages, proposals


def propose_youtrack_workstream_plan(
    workspace: str | Path,
    *,
    search_session_id: str | None = None,
    selected_issue_ids: list[str] | None = None,
    rejected_issue_ids: list[str] | None = None,
    workstream_title: str | None = None,
) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    if not search_session_id:
        current = _load_json(Path(paths["current_search"]), default={}, strict=False) or {}
        search_session_id = current.get("session_id")
    if not search_session_id:
        raise FileNotFoundError("No YouTrack search session is available for planning.")
    session = _read_search_session(paths, search_session_id)
    session["selected_issue_ids"] = list(dict.fromkeys(selected_issue_ids or session.get("selected_issue_ids") or _auto_select_issue_ids(session)))
    session["rejected_issue_ids"] = list(dict.fromkeys(rejected_issue_ids or session.get("rejected_issue_ids") or []))
    if not session["selected_issue_ids"]:
        raise ValueError("propose_youtrack_workstream_plan requires at least one selected issue.")
    selected = _selected_issue_payloads(paths, session, session["selected_issue_ids"])
    selected.sort(key=lambda item: (-item["score"], item.get("codex_estimate_minutes") or 99999, item["issue_key"]))
    stages, proposals = _build_stage_batches(selected)
    for proposal in proposals:
        proposal["external_issue"]["connection_id"] = session["connection_id"]
    title = workstream_title or f"YouTrack {session['resolved_query'][:60]}".strip()
    plan_id = _new_entity_id("yt-plan")
    plan = {
        "schema_version": YOUTRACK_PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "search_session_id": search_session_id,
        "connection_id": session["connection_id"],
        "workstream_title": title,
        "selected_issue_ids": session["selected_issue_ids"],
        "rejected_issue_ids": session["rejected_issue_ids"],
        "stages": stages,
        "task_proposals": proposals,
        "codex_estimates": {
            "total_minutes": sum(item.get("codex_estimate_minutes") or 0 for item in proposals),
            "issue_count": len(proposals),
        },
        "status": "needs_user_confirmation",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    session["selection_updated_at"] = now_iso()
    session["updated_at"] = now_iso()
    _write_json(_search_session_path(paths, search_session_id), session)
    _write_json(_plan_draft_path(paths, plan_id), plan)
    _save_current_pointer(Path(paths["current_plan"]), "plan_id", plan_id)
    return {
        "workspace_path": paths["workspace_path"],
        "search_session": session,
        "plan": plan,
    }


def apply_youtrack_workstream_plan(
    workspace: str | Path,
    *,
    plan_id: str | None = None,
    confirmed: bool = False,
    activate_first_task: bool = False,
) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("apply_youtrack_workstream_plan requires confirmed=True.")
    paths = _youtrack_paths(workspace)
    if not plan_id:
        current = _load_json(Path(paths["current_plan"]), default={}, strict=False) or {}
        plan_id = current.get("plan_id")
    if not plan_id:
        raise FileNotFoundError("No YouTrack plan draft is available for apply.")
    plan = _read_plan_draft(paths, plan_id)
    if plan.get("status") == "applied":
        return {
            "workspace_path": paths["workspace_path"],
            "plan": plan,
            "workstream": current_workstream(workspace),
            "tasks": [item for item in (_load_tasks_index(workspace_paths(workspace)).get("items") or []) if item.get("linked_workstream_id") == plan.get("applied_workstream_id")],
        }
    workstream_result = create_workstream(
        workspace,
        title=plan["workstream_title"],
        kind="feature",
        scope_summary=f"YouTrack plan derived from search session {plan['search_session_id']}",
        source_context={
            "provider": "youtrack",
            "connection_id": plan["connection_id"],
            "search_session_id": plan["search_session_id"],
            "plan_id": plan["plan_id"],
        },
        make_current=True,
    )
    workstream_id = workstream_result["created_workstream_id"]
    register = read_stage_register(workspace, workstream_id=workstream_id)
    register["stages"] = copy.deepcopy(plan.get("stages") or [])
    if register["stages"]:
        register["current_stage"] = register["stages"][0]["id"]
        register["current_slice"] = register["stages"][0]["canonical_execution_slices"][0]
        register["remaining_slices"] = register["stages"][0]["canonical_execution_slices"][1:]
        register["stage_status"] = "planned"
    register["plan_status"] = "confirmed"
    register["source_context"] = {
        "provider": "youtrack",
        "connection_id": plan["connection_id"],
        "search_session_id": plan["search_session_id"],
        "plan_id": plan["plan_id"],
    }
    register.setdefault("planner_notes", []).append(f"YouTrack plan {plan_id} applied from search session {plan['search_session_id']}.")
    write_stage_register(workspace, register, confirmed_stage_plan_edit=True, workstream_id=workstream_id)
    created_task_ids: list[str] = []
    for index, proposal in enumerate(plan.get("task_proposals") or []):
        task_result = create_task(
            workspace,
            title=proposal["title"],
            objective=proposal["objective"],
            branch_hint=proposal.get("branch_hint"),
            linked_workstream_id=workstream_id,
            stage_id=proposal.get("stage_id"),
            external_issue=proposal.get("external_issue"),
            codex_estimate_minutes=proposal.get("codex_estimate_minutes"),
            task_id=proposal["task_id"],
            make_current=activate_first_task and index == 0,
        )
        created_task_ids.append(task_result["created_task_id"])
        proposal["created_task_id"] = task_result["created_task_id"]
        proposal["linked_workstream_id"] = workstream_id
        update_issue_snapshot(workspace, proposal["external_issue"] | {
            "issue_id": proposal["issue_id"],
            "issue_key": proposal["issue_key"],
            "issue_url": proposal["issue_url"],
            "summary": proposal["summary"],
            "youtrack_estimate_minutes": proposal.get("youtrack_estimate_minutes"),
            "youtrack_spent_minutes": proposal.get("youtrack_spent_minutes"),
            "codex_estimate_minutes": proposal.get("codex_estimate_minutes"),
        }, connection_id=plan["connection_id"])
        recompute_issue_ledger(workspace, connection_id=plan["connection_id"], issue_id=proposal["issue_id"])
    plan["status"] = "applied"
    plan["applied_workstream_id"] = workstream_id
    plan["created_task_ids"] = created_task_ids
    plan["applied_at"] = now_iso()
    plan["updated_at"] = now_iso()
    _write_json(_plan_draft_path(paths, plan_id), plan)
    brief_lines = [
        "# StageExecutionBrief",
        "",
        f"YouTrack plan: {plan_id}",
        f"Search session: {plan['search_session_id']}",
        f"Workstream: {plan['workstream_title']}",
        "",
        "Selected issues:",
        *[
            f"- {proposal['issue_key']}: {proposal['summary']} "
            f"(YT est {proposal.get('youtrack_estimate_minutes') or 'n/a'}m, "
            f"YT spent {proposal.get('youtrack_spent_minutes') or 0}m, "
            f"Codex est {proposal.get('codex_estimate_minutes') or 'n/a'}m)"
            for proposal in plan.get("task_proposals") or []
        ],
        "",
        "Acceptance notes:",
        "- Keep each issue isolated to its own task and commit.",
        "- Every linked commit subject must start with the YouTrack issue id.",
        "- Ask the user to validate behavior before closeout when project-specific access tokens or production-only checks are needed.",
    ]
    set_active_brief(workspace, "\n".join(brief_lines))
    return {
        "workspace_path": paths["workspace_path"],
        "plan": plan,
        "workstream": current_workstream(workspace),
        "tasks": [item for item in (_load_tasks_index(workspace_paths(workspace)).get("items") or []) if item.get("linked_workstream_id") == workstream_id],
    }


def workstream_issue_cards(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    tasks_index = _load_tasks_index(workspace_paths(workspace))
    items = []
    for item in tasks_index.get("items", []):
        task = _normalize_task_payload(item)
        external_issue = task.get("external_issue") or {}
        if not external_issue:
            continue
        if workstream_id and task.get("linked_workstream_id") != workstream_id:
            continue
        ledger = read_issue_ledger(workspace, connection_id=external_issue["connection_id"], issue_id=external_issue["issue_id"])
        items.append(
            {
                "task_id": task.get("task_id"),
                "stage_id": task.get("stage_id"),
                "task_status": task.get("status"),
                "issue_id": external_issue.get("issue_id"),
                "issue_key": external_issue.get("issue_key"),
                "issue_url": external_issue.get("issue_url"),
                "title": external_issue.get("summary") or task.get("title"),
                "user_estimate_minutes": (ledger or {}).get("youtrack_estimate_minutes") or external_issue.get("youtrack_estimate_minutes"),
                "codex_estimate_minutes": (ledger or {}).get("codex_estimate_minutes") or task.get("codex_estimate_minutes"),
                "youtrack_spent_minutes": (ledger or {}).get("youtrack_spent_minutes") or external_issue.get("youtrack_spent_minutes"),
                "codex_spent_minutes": (ledger or {}).get("codex_total_minutes", 0),
                "latest_commit": (ledger or {}).get("latest_commit") or task.get("latest_commit"),
            }
        )
    return {"items": items}


def workspace_youtrack_summary(workspace: str | Path) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    connections = list_youtrack_connections(workspace)
    current_search = _load_json(Path(paths["current_search"]), default={}, strict=False) or {}
    current_plan = _load_json(Path(paths["current_plan"]), default={}, strict=False) or {}
    current_search_session = _maybe_read_search_session(paths, current_search.get("session_id"))
    current_plan_draft = _maybe_read_plan_draft(paths, current_plan.get("plan_id"))
    workstream_id = None
    try:
        workstream_id = current_workstream(workspace).get("workstream_id")
    except Exception:
        workstream_id = None
    issues = workstream_issue_cards(workspace, workstream_id=workstream_id)["items"] if workstream_id else []
    return {
        "connection_count": len(connections["items"]),
        "default_connection_id": connections.get("default_connection_id"),
        "last_search_session_id": (current_search_session or {}).get("session_id"),
        "active_plan_id": (current_plan_draft or {}).get("plan_id"),
        "current_workstream_issue_count": len(issues),
        "current_workstream_issues": issues[:6],
    }


def workspace_youtrack_detail(workspace: str | Path) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    connections = list_youtrack_connections(workspace)
    current_search = _load_json(Path(paths["current_search"]), default={}, strict=False) or {}
    current_plan = _load_json(Path(paths["current_plan"]), default={}, strict=False) or {}
    search_session = _maybe_read_search_session(paths, current_search.get("session_id"))
    plan = _maybe_read_plan_draft(paths, current_plan.get("plan_id"))
    workstream_id = None
    try:
        workstream_id = current_workstream(workspace).get("workstream_id")
    except Exception:
        workstream_id = None
    return {
        "connections": connections,
        "current_search_session": search_session,
        "current_plan": plan,
        "current_workstream_issues": workstream_issue_cards(workspace, workstream_id=workstream_id),
    }
