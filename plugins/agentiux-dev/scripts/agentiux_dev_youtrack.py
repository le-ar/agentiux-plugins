#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import html
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
    _load_workstreams_index,
    _normalize_task_payload,
    _persist_task_record,
    _save_workstreams_index,
    _workstream_record_by_id,
    _write_json,
    create_task,
    create_workstream,
    current_workstream,
    list_workstreams,
    now_iso,
    read_stage_register,
    sanitize_identifier,
    set_active_brief,
    slugify,
    switch_workstream,
    workspace_paths,
    write_stage_register,
)


YOUTRACK_SCHEMA_VERSION = 1
YOUTRACK_CONNECTION_SCHEMA_VERSION = 1
YOUTRACK_SEARCH_SCHEMA_VERSION = 1
YOUTRACK_PLAN_SCHEMA_VERSION = 1
YOUTRACK_ISSUE_LEDGER_SCHEMA_VERSION = 1
YOUTRACK_URL_CACHE_SCHEMA_VERSION = 1

DEFAULT_SEARCH_PAGE_SIZE = 25
DEFAULT_SHORTLIST_SIZE = 8
DEFAULT_STAGE_TARGET_MINUTES = 240
DEFAULT_STAGE_MAX_ISSUES = 4
DEFAULT_RESULT_SCAN_LIMIT = 1000
ISSUE_DEEP_CONTEXT_VERSION = 1
MAX_EXTERNAL_REFERENCES_PER_ISSUE = 4
MAX_EXTERNAL_REFERENCE_TEXT_BYTES = 12288
MAX_EXTERNAL_REFERENCE_SUMMARY_CHARS = 480
MAX_RELATED_ISSUES_PER_ISSUE = 6
MAX_RELATED_ISSUE_DEPTH = 2
MAX_TOTAL_RELATED_ISSUES = 12
MAX_COMMENT_LINK_SOURCES = 2
EXTERNAL_REFERENCE_TIMEOUT_SECONDS = 5
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)

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
ISSUE_COMMENT_FIELDS = "id,text,textPreview,deleted,created,updated,author(id,login,name)"
ISSUE_ACTIVITY_FIELDS = "id,timestamp,targetMember,author(id,login,name),category(id,name),field(name)"
ISSUE_ACTIVITY_CATEGORIES = [
    "IssueCreatedCategory",
    "CommentsCategory",
    "SummaryCategory",
    "DescriptionCategory",
    "CustomFieldCategory",
    "StateCategory",
    "LinksCategory",
    "WorkItemCategory",
]
ISSUE_COMMENTS_LIMIT = 20
ISSUE_ACTIVITY_LIMIT = 20
ISSUE_LINK_FIELDS = ",".join(
    [
        "direction",
        "linkType(name,sourceToTarget,targetToSource,localizedSourceToTarget,localizedTargetToSource)",
        "issues(id,idReadable,summary,resolved,updated,project(id,shortName,name))",
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
        "url_cache_dir": root / "url-cache",
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
        "url_cache_dir",
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


def _url_cache_path(paths: dict[str, Any], url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return Path(paths["url_cache_dir"]) / f"{key}.json"


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
        "issue_entity_id": issue.get("id"),
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


def _issue_context_runtime(runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = runtime if runtime is not None else {}
    payload.setdefault("issue_cache", {})
    payload.setdefault("url_cache", {})
    payload.setdefault("related_issue_count", 0)
    return payload


def _tracker_issue_key_from_url(connection: dict[str, Any], url: str) -> str | None:
    base = connection["base_url"].rstrip("/")
    if not url.startswith(f"{base}/issue/"):
        return None
    parsed = urllib.parse.urlparse(url)
    marker = "/issue/"
    if marker not in parsed.path:
        return None
    issue_key = parsed.path.split(marker, 1)[1].split("/", 1)[0].strip()
    return issue_key or None


def _read_url_cache(paths: dict[str, Any], url: str) -> dict[str, Any] | None:
    return _load_json(_url_cache_path(paths, url), default=None, strict=False)


def _write_url_cache(paths: dict[str, Any], url: str, payload: dict[str, Any]) -> dict[str, Any]:
    cached = copy.deepcopy(payload)
    cached["schema_version"] = YOUTRACK_URL_CACHE_SCHEMA_VERSION
    cached["updated_at"] = now_iso()
    _write_json(_url_cache_path(paths, url), cached)
    return cached


def _extract_urls_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    urls: list[str] = []
    for match in URL_PATTERN.findall(text):
        candidate = match.rstrip("),.;]>'\"")
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))
        urls.append(normalized)
    return list(dict.fromkeys(urls))


def _strip_html_preview(text: str) -> tuple[str | None, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else None
    stripped = re.sub(r"(?is)<script.*?</script>", " ", text)
    stripped = re.sub(r"(?is)<style.*?</style>", " ", stripped)
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    preview = html.unescape(re.sub(r"\s+", " ", stripped).strip())
    return title, preview


def _excerpt_text(text: str | None, limit: int = MAX_EXTERNAL_REFERENCE_SUMMARY_CHARS) -> str | None:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    shortened = normalized[:limit].rsplit(" ", 1)[0].strip()
    return (shortened or normalized[:limit]).rstrip(",;:") + "..."


def _text_like_content_type(content_type: str) -> bool:
    lowered = (content_type or "").split(";", 1)[0].strip().lower()
    return not lowered or lowered.startswith("text/") or "json" in lowered or "xml" in lowered


def _classify_external_reference(
    url: str,
    final_url: str,
    *,
    content_type: str,
    title: str | None,
    preview: str | None,
    raw_text: str,
) -> tuple[str, bool, str | None, list[str]]:
    lowered_path = urllib.parse.urlparse(final_url or url).path.lower()
    lowered_preview = f"{title or ''} {preview or ''}".lower()
    lowered_raw = raw_text.lower()
    signals: list[str] = []
    if not _text_like_content_type(content_type):
        return "binary_or_download", False, "Content is not text-like.", ["non_text_content_type"]
    if re.search(r"type\s*=\s*[\"']?password", lowered_raw):
        signals.append("password_field")
    if re.search(r"\b(sign in|log in|login|authenticate|authorization|forgot password|two-factor|verify code)\b", lowered_preview):
        signals.append("auth_terms")
    if re.search(r"\b(admin|administration|backoffice|control panel|manage users|permissions)\b", lowered_preview):
        signals.append("admin_terms")
    if re.search(r"/(login|signin|auth|admin|backoffice)(/|$)", lowered_path):
        signals.append("auth_or_admin_path")
    if lowered_raw.count("<form") >= 1 and len(preview or "") < 160:
        signals.append("form_shell")
    if ("password_field" in signals and ("auth_terms" in signals or "auth_or_admin_path" in signals)) or (
        "admin_terms" in signals and ("form_shell" in signals or "auth_or_admin_path" in signals)
    ):
        return "admin_or_auth_like", False, "Page looks like an authenticated or administrative surface.", signals
    return "openable_text", True, None, signals


def _fetch_external_reference(connection: dict[str, Any], url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,text/plain,application/json,application/xml;q=0.9,*/*;q=0.1",
            "User-Agent": "AgentiUX-Dev-YouTrack-LinkFetcher/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=EXTERNAL_REFERENCE_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_EXTERNAL_REFERENCE_TEXT_BYTES + 1)
            truncated = len(raw) > MAX_EXTERNAL_REFERENCE_TEXT_BYTES
            if truncated:
                raw = raw[:MAX_EXTERNAL_REFERENCE_TEXT_BYTES]
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
            status = getattr(response, "status", 200)
            if _text_like_content_type(content_type):
                charset = response.headers.get_content_charset() if hasattr(response.headers, "get_content_charset") else None
                decoded = raw.decode(charset or "utf-8", errors="ignore")
                if "<html" in decoded.lower():
                    title, preview = _strip_html_preview(decoded)
                else:
                    title = None
                    preview = decoded
                preview = _excerpt_text(preview)
                classification, openable, skip_reason, signals = _classify_external_reference(
                    url,
                    final_url,
                    content_type=content_type,
                    title=title,
                    preview=preview,
                    raw_text=decoded,
                )
            else:
                title = None
                preview = None
                classification, openable, skip_reason, signals = _classify_external_reference(
                    url,
                    final_url,
                    content_type=content_type,
                    title=None,
                    preview=None,
                    raw_text="",
                )
            return {
                "url": url,
                "final_url": final_url,
                "http_status": status,
                "content_type": content_type.split(";", 1)[0].strip().lower(),
                "classification": classification,
                "openable": openable,
                "skip_reason": skip_reason,
                "title": title,
                "summary": preview if openable else None,
                "signals": signals,
                "truncated": truncated,
                "tracker_issue_key": _tracker_issue_key_from_url(connection, final_url),
                "fetch_error": None,
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "url": url,
            "final_url": url,
            "http_status": None,
            "content_type": None,
            "classification": "fetch_error",
            "openable": False,
            "skip_reason": "Unable to fetch external reference.",
            "title": None,
            "summary": None,
            "signals": [],
            "truncated": False,
            "tracker_issue_key": _tracker_issue_key_from_url(connection, url),
            "fetch_error": str(exc),
        }


def _load_or_fetch_external_reference(
    paths: dict[str, Any],
    connection: dict[str, Any],
    url: str,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    state = _issue_context_runtime(runtime)
    if url in state["url_cache"]:
        cached = copy.deepcopy(state["url_cache"][url])
        cached["cache_status"] = "runtime"
        return cached
    cached = _read_url_cache(paths, url)
    if cached:
        state["url_cache"][url] = copy.deepcopy(cached)
        materialized = copy.deepcopy(cached)
        materialized["cache_status"] = "disk"
        return materialized
    fetched = _write_url_cache(paths, url, _fetch_external_reference(connection, url))
    state["url_cache"][url] = copy.deepcopy(fetched)
    materialized = copy.deepcopy(fetched)
    materialized["cache_status"] = "miss"
    return materialized


def _external_reference_sources(issue_payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    description = issue_payload.get("description")
    if description and _extract_urls_from_text(description):
        sources.append({"source": "description", "source_id": "description", "text": description})
    comment_sources = 0
    for comment in issue_payload.get("comments") or []:
        if comment_sources >= MAX_COMMENT_LINK_SOURCES:
            break
        text = comment.get("text") or comment.get("textPreview")
        if text and _extract_urls_from_text(text):
            sources.append({"source": "comment", "source_id": comment.get("id"), "text": text})
            comment_sources += 1
    return sources


def _external_reference_overview(references: list[dict[str, Any]], warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "link_count": len(references),
        "openable_count": sum(1 for item in references if item.get("openable")),
        "skipped_admin_count": sum(1 for item in references if item.get("classification") == "admin_or_auth_like"),
        "skipped_binary_count": sum(1 for item in references if item.get("classification") == "binary_or_download"),
        "error_count": sum(1 for item in references if item.get("classification") == "fetch_error"),
        "tracker_issue_reference_count": sum(1 for item in references if item.get("tracker_issue_key")),
        "warnings": list(dict.fromkeys(warnings or [])),
    }


def _fetch_issue_by_reference(
    connection: dict[str, Any],
    token: str,
    issue_reference: str,
    field_mapping: dict[str, list[str]],
) -> dict[str, Any] | None:
    raw_issue = None
    try:
        raw_issue = _request_json(
            connection,
            token,
            "GET",
            f"/api/issues/{urllib.parse.quote(issue_reference, safe='')}",
            params={"fields": ISSUE_FIELDS},
        )
    except Exception:
        matches = _request_json(
            connection,
            token,
            "GET",
            "/api/issues",
            params={
                "query": f"id: {issue_reference}",
                "$top": 1,
                "fields": ISSUE_FIELDS,
            },
        ) or []
        raw_issue = matches[0] if matches else None
    if not raw_issue:
        return None
    return _normalize_issue(connection, raw_issue, field_mapping)


def _issue_resource_id(issue_payload: dict[str, Any]) -> str | None:
    for key in ("issue_entity_id", "entity_id", "issue_id"):
        value = issue_payload.get(key)
        if value:
            return str(value)
    return None


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


def _attach_comments(connection: dict[str, Any], token: str, issue_payload: dict[str, Any]) -> dict[str, Any]:
    resource_id = _issue_resource_id(issue_payload)
    if not resource_id:
        return copy.deepcopy(issue_payload)
    items = _request_json(
        connection,
        token,
        "GET",
        f"/api/issues/{urllib.parse.quote(resource_id, safe='')}/comments",
        params={
            "$top": ISSUE_COMMENTS_LIMIT,
            "fields": ISSUE_COMMENT_FIELDS,
        },
    ) or []
    enriched = copy.deepcopy(issue_payload)
    enriched["comments"] = items
    return enriched


def _attach_recent_activity(connection: dict[str, Any], token: str, issue_payload: dict[str, Any]) -> dict[str, Any]:
    resource_id = _issue_resource_id(issue_payload)
    if not resource_id:
        return copy.deepcopy(issue_payload)
    page = _request_json(
        connection,
        token,
        "GET",
        f"/api/issues/{urllib.parse.quote(resource_id, safe='')}/activities",
        params={
            "$top": ISSUE_ACTIVITY_LIMIT,
            "reverse": "true",
            "categories": ISSUE_ACTIVITY_CATEGORIES,
            "fields": ISSUE_ACTIVITY_FIELDS,
        },
    ) or []
    enriched = copy.deepcopy(issue_payload)
    enriched["recent_activities"] = page
    enriched["recent_activity_page"] = {
        "returned_count": len(page),
        "limit": ISSUE_ACTIVITY_LIMIT,
    }
    return enriched


def _normalized_relation_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _relation_label(link_type: dict[str, Any], direction: str) -> str:
    if direction == "INWARD":
        return str(
            link_type.get("targetToSource")
            or link_type.get("sourceToTarget")
            or link_type.get("localizedTargetToSource")
            or link_type.get("sourceToTarget")
            or link_type.get("localizedSourceToTarget")
            or link_type.get("name")
            or "related"
        )
    return str(
        link_type.get("sourceToTarget")
        or link_type.get("targetToSource")
        or link_type.get("localizedSourceToTarget")
        or link_type.get("targetToSource")
        or link_type.get("localizedTargetToSource")
        or link_type.get("name")
        or "related"
    )


def _inverse_relation_label(link_type: dict[str, Any], direction: str) -> str:
    if direction == "INWARD":
        return str(
            link_type.get("sourceToTarget")
            or link_type.get("targetToSource")
            or link_type.get("localizedSourceToTarget")
            or link_type.get("targetToSource")
            or link_type.get("localizedTargetToSource")
            or link_type.get("name")
            or "related"
        )
    return str(
        link_type.get("targetToSource")
        or link_type.get("sourceToTarget")
        or link_type.get("localizedTargetToSource")
        or link_type.get("sourceToTarget")
        or link_type.get("localizedSourceToTarget")
        or link_type.get("name")
        or "related"
    )


def _classify_issue_link(link_type: dict[str, Any], relation_label: str) -> tuple[str, str]:
    relation_text = _normalized_relation_text(relation_label)
    combined = " | ".join(
        _normalized_relation_text(part)
        for part in (
            link_type.get("name"),
            link_type.get("sourceToTarget"),
            link_type.get("targetToSource"),
            relation_label,
        )
        if part
    )
    if "duplicate" in combined:
        if any(token in relation_text for token in ("duplicated by", "is duplicated by")):
            return "duplicate", "duplicated_by"
        return "duplicate", "duplicate_of"
    if any(token in combined for token in ("depend", "require", "block")):
        if any(
            token in relation_text
            for token in (
                "is required for",
                "required for",
                "blocks",
                "needed for",
            )
        ):
            return "dependency", "prerequisite_for"
        return "dependency", "blocked_by"
    if any(token in combined for token in ("subtask", "parent", "part of", "contains")):
        if any(
            token in relation_text
            for token in (
                "parent for",
                "has subtask",
                "contains",
                "includes",
            )
        ):
            return "hierarchy", "parent_of"
        return "hierarchy", "child_of"
    if "relate" in combined:
        return "related", "related_to"
    return "other", "other"


def _issue_link_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    role_order = {
        "blocked_by": 0,
        "prerequisite_for": 1,
        "duplicate_of": 2,
        "duplicated_by": 3,
        "child_of": 4,
        "parent_of": 5,
        "related_to": 6,
        "other": 7,
    }
    return (
        role_order.get(item.get("planning_role") or "other", 99),
        str(item.get("issue_key") or item.get("issue_id") or ""),
        str(item.get("relation_label") or ""),
    )


def _issue_link_summary(issue_links: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "linked_issue_count": len(issue_links),
        "open_linked_issue_count": sum(1 for item in issue_links if not item.get("resolved")),
        "blocking_link_count": sum(1 for item in issue_links if item.get("relation_kind") == "dependency"),
        "blocked_by_count": sum(1 for item in issue_links if item.get("planning_role") == "blocked_by"),
        "prerequisite_for_count": sum(1 for item in issue_links if item.get("planning_role") == "prerequisite_for"),
        "duplicate_count": sum(1 for item in issue_links if item.get("relation_kind") == "duplicate"),
        "duplicate_of_count": sum(1 for item in issue_links if item.get("planning_role") == "duplicate_of"),
        "duplicated_by_count": sum(1 for item in issue_links if item.get("planning_role") == "duplicated_by"),
        "hierarchy_count": sum(1 for item in issue_links if item.get("relation_kind") == "hierarchy"),
        "related_count": sum(1 for item in issue_links if item.get("relation_kind") == "related"),
    }


def _attach_issue_links(connection: dict[str, Any], token: str, issue_payload: dict[str, Any]) -> dict[str, Any]:
    resource_id = _issue_resource_id(issue_payload)
    if not resource_id:
        return copy.deepcopy(issue_payload)
    items = _request_json(
        connection,
        token,
        "GET",
        f"/api/issues/{urllib.parse.quote(resource_id, safe='')}/links",
        params={
            "fields": ISSUE_LINK_FIELDS,
        },
    ) or []
    normalized_links: list[dict[str, Any]] = []
    current_issue_id = str(issue_payload.get("issue_id") or "")
    current_entity_id = str(issue_payload.get("issue_entity_id") or "")
    for item in items:
        link_type = copy.deepcopy(item.get("linkType") or {})
        direction = str(item.get("direction") or "OUTWARD").upper()
        relation_label = _relation_label(link_type, direction)
        inverse_relation = _inverse_relation_label(link_type, direction)
        relation_kind, planning_role = _classify_issue_link(link_type, relation_label)
        for linked_issue in item.get("issues") or []:
            linked_issue_id = linked_issue.get("idReadable") or linked_issue.get("id")
            linked_entity_id = linked_issue.get("id")
            if not linked_issue_id:
                continue
            if str(linked_issue_id) == current_issue_id or str(linked_entity_id or "") == current_entity_id:
                continue
            normalized_links.append(
                {
                    "direction": direction,
                    "link_type_name": link_type.get("name"),
                    "relation_label": relation_label,
                    "inverse_relation_label": inverse_relation,
                    "relation_kind": relation_kind,
                    "planning_role": planning_role,
                    "issue_entity_id": linked_entity_id,
                    "issue_id": linked_issue_id,
                    "issue_key": linked_issue_id,
                    "issue_url": f"{connection['base_url'].rstrip('/')}/issue/{linked_issue_id}",
                    "summary": linked_issue.get("summary") or linked_issue_id,
                    "resolved": linked_issue.get("resolved"),
                    "updated": linked_issue.get("updated"),
                    "project": copy.deepcopy(linked_issue.get("project") or {}),
                }
            )
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in normalized_links:
        key = (
            str(item.get("issue_id") or ""),
            str(item.get("planning_role") or ""),
            str(item.get("relation_label") or ""),
        )
        deduped[key] = item
    issue_links = sorted(deduped.values(), key=_issue_link_sort_key)
    enriched = copy.deepcopy(issue_payload)
    enriched["issue_links"] = issue_links
    enriched["link_summary"] = _issue_link_summary(issue_links)
    return enriched


def _issue_context_complete(issue_payload: dict[str, Any]) -> bool:
    return (
        all(
            key in issue_payload
            for key in (
                "work_items",
                "comments",
                "recent_activities",
                "issue_links",
                "link_summary",
                "external_references",
                "external_reference_overview",
                "related_issue_summaries",
                "related_issue_overview",
            )
        )
        and issue_payload.get("deep_context_version") == ISSUE_DEEP_CONTEXT_VERSION
    )


def _ticket_overview(issue_payload: dict[str, Any], *, warnings: list[str] | None = None) -> dict[str, Any]:
    comments = issue_payload.get("comments") or []
    work_items = issue_payload.get("work_items") or []
    recent_activities = issue_payload.get("recent_activities") or []
    link_summary = copy.deepcopy(issue_payload.get("link_summary") or _issue_link_summary(issue_payload.get("issue_links") or []))
    external_reference_overview = copy.deepcopy(issue_payload.get("external_reference_overview") or {})
    related_issue_overview = copy.deepcopy(issue_payload.get("related_issue_overview") or {})
    aggregated_warnings = list(warnings or [])
    aggregated_warnings.extend(external_reference_overview.get("warnings") or [])
    aggregated_warnings.extend(related_issue_overview.get("warnings") or [])
    return {
        "has_description": bool(issue_payload.get("description")),
        "comment_count": len(comments),
        "work_item_count": len(work_items),
        "recent_activity_count": len(recent_activities),
        "linked_issue_count": link_summary.get("linked_issue_count", 0),
        "blocking_link_count": link_summary.get("blocking_link_count", 0),
        "duplicate_link_count": link_summary.get("duplicate_count", 0),
        "hierarchy_link_count": link_summary.get("hierarchy_count", 0),
        "external_reference_count": external_reference_overview.get("link_count", 0),
        "openable_external_reference_count": external_reference_overview.get("openable_count", 0),
        "related_issue_count": related_issue_overview.get("fetched_issue_count", 0),
        "related_issue_cycle_count": related_issue_overview.get("cycle_count", 0),
        "spent_minutes": issue_payload.get("youtrack_spent_minutes") or 0,
        "warnings": list(dict.fromkeys(aggregated_warnings)),
    }


def _related_issue_summary(
    link: dict[str, Any],
    payload: dict[str, Any] | None,
    *,
    depth: int,
    fetch_status: str,
    cycle_detected: bool = False,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    issue_payload = payload or {}
    return {
        "issue_entity_id": issue_payload.get("issue_entity_id") or link.get("issue_entity_id"),
        "issue_id": issue_payload.get("issue_id") or link.get("issue_id"),
        "issue_key": issue_payload.get("issue_key") or link.get("issue_key") or link.get("issue_id"),
        "issue_url": issue_payload.get("issue_url") or link.get("issue_url"),
        "summary": issue_payload.get("summary") or link.get("summary") or link.get("issue_id"),
        "resolved": issue_payload.get("resolved", link.get("resolved")),
        "depth": depth,
        "relation_kind": link.get("relation_kind"),
        "planning_role": link.get("planning_role"),
        "relation_label": link.get("relation_label"),
        "fetch_status": fetch_status,
        "cycle_detected": cycle_detected,
        "skip_reason": skip_reason,
        "description_excerpt": _excerpt_text(issue_payload.get("description"), limit=240),
        "ticket_overview": copy.deepcopy(issue_payload.get("ticket_overview") or {}),
        "external_reference_overview": copy.deepcopy(issue_payload.get("external_reference_overview") or {}),
        "related_issue_overview": copy.deepcopy(issue_payload.get("related_issue_overview") or {}),
    }


def _related_issue_overview(summaries: list[dict[str, Any]], warnings: list[str] | None = None) -> dict[str, Any]:
    relation_counts: dict[str, int] = {}
    for summary in summaries:
        relation = str(summary.get("planning_role") or summary.get("relation_kind") or "other")
        relation_counts[relation] = relation_counts.get(relation, 0) + 1
    return {
        "direct_issue_count": len(summaries),
        "fetched_issue_count": sum(1 for item in summaries if item.get("fetch_status") in {"fetched", "cached"}),
        "cycle_count": sum(1 for item in summaries if item.get("cycle_detected")),
        "failed_count": sum(1 for item in summaries if item.get("fetch_status") == "failed"),
        "relation_counts": relation_counts,
        "warnings": list(dict.fromkeys(warnings or [])),
    }


def _attach_external_references(
    connection: dict[str, Any],
    issue_payload: dict[str, Any],
    *,
    paths: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_urls: set[str] = set()
    for source in _external_reference_sources(issue_payload):
        for url in _extract_urls_from_text(source.get("text")):
            if url in seen_urls:
                continue
            if len(references) >= MAX_EXTERNAL_REFERENCES_PER_ISSUE:
                warnings.append("External reference limit reached for this issue.")
                break
            seen_urls.add(url)
            analysis = _load_or_fetch_external_reference(paths, connection, url, runtime)
            references.append(
                {
                    **analysis,
                    "source": source["source"],
                    "source_id": source.get("source_id"),
                }
            )
    enriched = copy.deepcopy(issue_payload)
    enriched["external_references"] = references
    enriched["external_reference_overview"] = _external_reference_overview(references, warnings=warnings)
    return enriched


def _attach_related_issue_context(
    connection: dict[str, Any],
    token: str,
    issue_payload: dict[str, Any],
    *,
    paths: dict[str, Any],
    field_mapping: dict[str, list[str]],
    runtime: dict[str, Any],
    depth: int,
    ancestry: tuple[str, ...],
) -> dict[str, Any]:
    state = _issue_context_runtime(runtime)
    issue_id = str(issue_payload.get("issue_id") or "")
    candidate_links = list(issue_payload.get("issue_links") or [])
    for reference in issue_payload.get("external_references") or []:
        tracker_issue_key = reference.get("tracker_issue_key")
        if not tracker_issue_key or any(item.get("issue_id") == tracker_issue_key for item in candidate_links):
            continue
        candidate_links.append(
            {
                "issue_entity_id": tracker_issue_key,
                "issue_id": tracker_issue_key,
                "issue_key": tracker_issue_key,
                "issue_url": reference.get("final_url") or reference.get("url"),
                "summary": tracker_issue_key,
                "resolved": None,
                "relation_kind": "text_reference",
                "planning_role": "mentioned_in_description",
                "relation_label": "mentioned in description",
            }
        )
    summaries: list[dict[str, Any]] = []
    warnings: list[str] = []
    if depth >= MAX_RELATED_ISSUE_DEPTH:
        warnings.append("Related issue depth limit reached.")
    else:
        for link in candidate_links[:MAX_RELATED_ISSUES_PER_ISSUE]:
            related_issue_id = str(link.get("issue_id") or "")
            related_reference = str(link.get("issue_entity_id") or related_issue_id)
            if not related_issue_id:
                continue
            if related_issue_id == issue_id or related_issue_id in ancestry:
                summaries.append(
                    _related_issue_summary(
                        link,
                        None,
                        depth=depth + 1,
                        fetch_status="cycle",
                        cycle_detected=True,
                        skip_reason="Cycle detected in related issue traversal.",
                    )
                )
                continue
            cache_key = (connection["connection_id"], related_issue_id)
            cached_payload = state["issue_cache"].get(cache_key)
            if cached_payload is not None and _issue_context_complete(cached_payload):
                summaries.append(_related_issue_summary(link, cached_payload, depth=depth + 1, fetch_status="cached"))
                continue
            if state["related_issue_count"] >= MAX_TOTAL_RELATED_ISSUES:
                warnings.append("Global related issue fetch limit reached.")
                summaries.append(
                    _related_issue_summary(
                        link,
                        None,
                        depth=depth + 1,
                        fetch_status="skipped",
                        skip_reason="Related issue fetch budget was exhausted.",
                    )
                )
                continue
            if cached_payload is not None:
                related_payload = _attach_ticket_context(
                    connection,
                    token,
                    cached_payload,
                    paths=paths,
                    field_mapping=field_mapping,
                    runtime=state,
                    depth=depth + 1,
                    ancestry=(*ancestry, issue_id),
                )
            else:
                related_payload = _fetch_issue_by_reference(connection, token, related_reference, field_mapping)
                if related_payload:
                    related_payload = _attach_ticket_context(
                        connection,
                        token,
                        related_payload,
                        paths=paths,
                        field_mapping=field_mapping,
                        runtime=state,
                        depth=depth + 1,
                        ancestry=(*ancestry, issue_id),
                    )
            if not related_payload:
                summaries.append(
                    _related_issue_summary(
                        link,
                        None,
                        depth=depth + 1,
                        fetch_status="failed",
                        skip_reason="Unable to resolve related issue payload.",
                    )
                )
                continue
            state["related_issue_count"] += 1
            state["issue_cache"][cache_key] = copy.deepcopy(related_payload)
            update_issue_snapshot(paths["workspace_path"], related_payload, connection_id=connection["connection_id"])
            summaries.append(_related_issue_summary(link, related_payload, depth=depth + 1, fetch_status="fetched"))
    enriched = copy.deepcopy(issue_payload)
    enriched["related_issue_summaries"] = summaries
    enriched["related_issue_overview"] = _related_issue_overview(summaries, warnings=warnings)
    return enriched


def _attach_ticket_context(
    connection: dict[str, Any],
    token: str,
    issue_payload: dict[str, Any],
    *,
    paths: dict[str, Any] | None = None,
    field_mapping: dict[str, list[str]] | None = None,
    runtime: dict[str, Any] | None = None,
    depth: int = 0,
    ancestry: tuple[str, ...] = (),
) -> dict[str, Any]:
    if _issue_context_complete(issue_payload) and issue_payload.get("ticket_overview"):
        return copy.deepcopy(issue_payload)
    enriched = _attach_spent_minutes(connection, token, issue_payload)
    warnings: list[str] = []
    try:
        enriched = _attach_comments(connection, token, enriched)
    except Exception as exc:
        warnings.append(f"comments: {exc}")
    try:
        enriched = _attach_recent_activity(connection, token, enriched)
    except Exception as exc:
        warnings.append(f"activities: {exc}")
    try:
        enriched = _attach_issue_links(connection, token, enriched)
    except Exception as exc:
        warnings.append(f"links: {exc}")
    if paths is not None:
        enriched = _attach_external_references(connection, enriched, paths=paths, runtime=_issue_context_runtime(runtime))
        if field_mapping is not None:
            enriched = _attach_related_issue_context(
                connection,
                token,
                enriched,
                paths=paths,
                field_mapping=field_mapping,
                runtime=_issue_context_runtime(runtime),
                depth=depth,
                ancestry=ancestry,
            )
    else:
        enriched["external_references"] = []
        enriched["external_reference_overview"] = _external_reference_overview([])
        enriched["related_issue_summaries"] = []
        enriched["related_issue_overview"] = _related_issue_overview([])
    if warnings:
        enriched["context_fetch_warnings"] = warnings
    enriched["deep_context_version"] = ISSUE_DEEP_CONTEXT_VERSION
    enriched["ticket_overview"] = _ticket_overview(enriched, warnings=warnings)
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


def _issue_ledger_targets(tasks_index: dict[str, Any], requested: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    if requested is not None:
        return list(dict.fromkeys((connection_id, issue_id) for connection_id, issue_id in requested if connection_id and issue_id))
    discovered: list[tuple[str, str]] = []
    for item in tasks_index.get("items", []):
        task = _normalize_task_payload(item)
        external_issue = task.get("external_issue") or {}
        connection_id = external_issue.get("connection_id")
        issue_id = external_issue.get("issue_id")
        if connection_id and issue_id:
            discovered.append((connection_id, issue_id))
    return list(dict.fromkeys(discovered))


def recompute_issue_ledgers(
    workspace: str | Path,
    *,
    targets: list[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    paths = _youtrack_paths(workspace)
    tasks_index = _load_tasks_index(workspace_paths(workspace))
    target_pairs = _issue_ledger_targets(tasks_index, targets)
    if not target_pairs:
        return {}
    target_set = set(target_pairs)
    state: dict[tuple[str, str], dict[str, Any]] = {}
    for connection_id, issue_id in target_pairs:
        existing = _load_json(_issue_ledger_path(paths, connection_id, issue_id), default={}, strict=False) or {}
        state[(connection_id, issue_id)] = {
            "existing": existing,
            "linked_task_ids": [],
            "time_entries": [],
            "codex_total_minutes": 0,
            "codex_estimate_minutes": existing.get("codex_estimate_minutes"),
            "latest_commit": None,
            "latest_task_timestamp": "",
            "latest_commit_timestamp": "",
        }
    for item in tasks_index.get("items", []):
        task = _normalize_task_payload(item)
        external_issue = task.get("external_issue") or {}
        target = (external_issue.get("connection_id"), external_issue.get("issue_id"))
        if target not in target_set:
            continue
        accumulator = state[target]
        accumulator["linked_task_ids"].append(task["task_id"])
        task_timestamp = str(task.get("updated_at") or task.get("created_at") or "")
        if task.get("codex_estimate_minutes") is not None and task_timestamp >= accumulator["latest_task_timestamp"]:
            accumulator["codex_estimate_minutes"] = task.get("codex_estimate_minutes")
            accumulator["latest_task_timestamp"] = task_timestamp
        latest_commit = task.get("latest_commit")
        latest_commit_timestamp = str((latest_commit or {}).get("recorded_at") or "")
        if latest_commit and latest_commit_timestamp >= accumulator["latest_commit_timestamp"]:
            accumulator["latest_commit"] = latest_commit
            accumulator["latest_commit_timestamp"] = latest_commit_timestamp
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
            accumulator["codex_total_minutes"] += normalized_entry["minutes"]
            accumulator["time_entries"].append(normalized_entry)
    ledgers: dict[tuple[str, str], dict[str, Any]] = {}
    for connection_id, issue_id in target_pairs:
        accumulator = state[(connection_id, issue_id)]
        existing = accumulator["existing"]
        time_entries = sorted(
            accumulator["time_entries"],
            key=lambda item: (item.get("started_at") or "", item.get("ended_at") or "", item.get("task_id") or ""),
        )
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
            "codex_estimate_minutes": (
                accumulator["codex_estimate_minutes"]
                if accumulator["codex_estimate_minutes"] is not None
                else existing.get("codex_estimate_minutes")
            ),
            "codex_total_minutes": accumulator["codex_total_minutes"],
            "time_entries": time_entries,
            "linked_task_ids": accumulator["linked_task_ids"],
            "latest_commit": accumulator["latest_commit"],
            "updated_at": now_iso(),
        }
        _write_json(_issue_ledger_path(paths, connection_id, issue_id), ledger)
        ledgers[(connection_id, issue_id)] = ledger
    return ledgers


def recompute_issue_ledger(workspace: str | Path, *, connection_id: str, issue_id: str) -> dict[str, Any]:
    return recompute_issue_ledgers(workspace, targets=[(connection_id, issue_id)])[(connection_id, issue_id)]


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


def _update_session_issue_payloads(session: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    by_issue_id = {item["issue_id"]: copy.deepcopy(item) for item in issues}
    shortlist = []
    for item in session.get("shortlist") or []:
        shortlist.append(copy.deepcopy(by_issue_id.get(item["issue_id"], item)))
    session["shortlist"] = shortlist
    shortlist_page = copy.deepcopy(session.get("shortlist_page") or {})
    page_items = []
    for item in shortlist_page.get("items") or []:
        page_items.append(copy.deepcopy(by_issue_id.get(item["issue_id"], item)))
    shortlist_page["items"] = page_items
    session["shortlist_page"] = shortlist_page
    return session


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
    runtime = _issue_context_runtime()
    for issue_payload in ranked_matches:
        runtime["issue_cache"][(resolved_connection_id, issue_payload["issue_id"])] = copy.deepcopy(issue_payload)
    safe_skip = max(skip, 0)
    safe_page_size = max(page_size, 1)
    safe_shortlist_size = max(shortlist_size, 1)
    page_items = ranked_matches[safe_skip : safe_skip + safe_page_size]
    shortlist: list[dict[str, Any]] = []
    shortlist_ids = {item["issue_id"] for item in page_items[:safe_shortlist_size]}
    for issue_payload in page_items:
        if issue_payload["issue_id"] in shortlist_ids:
            enriched = _attach_ticket_context(
                connection,
                secret["token"],
                issue_payload,
                paths=paths,
                field_mapping=field_catalog.get("field_mapping") or {},
                runtime=runtime,
            )
            shortlist.append(enriched)
            runtime["issue_cache"][(resolved_connection_id, issue_payload["issue_id"])] = copy.deepcopy(enriched)
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
        "selection_analysis": None,
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
        ledger = _issue_snapshot(paths, session["connection_id"], issue_id) or {}
        snapshot = copy.deepcopy(ledger.get("latest_snapshot") or {})
        payload = _merged_issue_snapshot(snapshot, payloads.get(issue_id) or {})
        if payload:
            payload["youtrack_estimate_minutes"] = ledger.get("youtrack_estimate_minutes", payload.get("youtrack_estimate_minutes"))
            payload["youtrack_spent_minutes"] = ledger.get("youtrack_spent_minutes", payload.get("youtrack_spent_minutes"))
            payload["codex_estimate_minutes"] = ledger.get("codex_estimate_minutes", payload.get("codex_estimate_minutes"))
            if "ticket_overview" not in payload:
                payload["ticket_overview"] = _ticket_overview(payload)
            selected.append(payload)
            continue
        raise FileNotFoundError(f"Issue `{issue_id}` is not available in the cached search snapshot for session `{session['session_id']}`.")
    return selected


def _unique_issue_ids(issue_links: list[dict[str, Any]], *, role: str | None = None, selected_only: bool | None = None) -> list[str]:
    values: list[str] = []
    for item in issue_links:
        if role and item.get("planning_role") != role:
            continue
        if selected_only is not None and bool(item.get("selected_in_plan")) is not selected_only:
            continue
        issue_id = item.get("issue_id")
        if issue_id:
            values.append(str(issue_id))
    return list(dict.fromkeys(values))


def _plan_link_analysis(issue: dict[str, Any]) -> dict[str, Any]:
    issue_links = issue.get("issue_links") or []
    open_external_blockers = [
        item for item in issue_links if item.get("planning_role") == "blocked_by" and not item.get("selected_in_plan") and not item.get("resolved")
    ]
    planner_notes: list[str] = []
    selected_blocked_by = _unique_issue_ids(issue_links, role="blocked_by", selected_only=True)
    external_blocked_by = [item["issue_id"] for item in open_external_blockers if item.get("issue_id")]
    selected_duplicates = _unique_issue_ids(issue_links, role="duplicate_of", selected_only=True)
    external_duplicates = _unique_issue_ids(issue_links, role="duplicate_of", selected_only=False)
    selected_children = _unique_issue_ids(issue_links, role="child_of", selected_only=True)
    selected_parents = _unique_issue_ids(issue_links, role="parent_of", selected_only=True)
    if selected_blocked_by:
        planner_notes.append(f"Depends on selected issues: {', '.join(selected_blocked_by)}.")
    if external_blocked_by:
        planner_notes.append(f"Depends on external issues outside this plan: {', '.join(external_blocked_by)}.")
    if selected_duplicates:
        planner_notes.append(f"Potential duplicate of selected issues: {', '.join(selected_duplicates)}.")
    elif external_duplicates:
        planner_notes.append(f"Potential duplicate of external issues: {', '.join(external_duplicates)}.")
    if selected_children:
        planner_notes.append(f"Has parent or container issues in this plan: {', '.join(selected_children)}.")
    if selected_parents:
        planner_notes.append(f"Has child issues in this plan: {', '.join(selected_parents)}.")
    return {
        "selected_linked_issue_ids": _unique_issue_ids(issue_links, selected_only=True),
        "external_linked_issue_ids": _unique_issue_ids(issue_links, selected_only=False),
        "selected_blocked_by_issue_ids": selected_blocked_by,
        "external_blocked_by_issue_ids": list(dict.fromkeys(external_blocked_by)),
        "selected_prerequisite_for_issue_ids": _unique_issue_ids(issue_links, role="prerequisite_for", selected_only=True),
        "external_prerequisite_for_issue_ids": _unique_issue_ids(issue_links, role="prerequisite_for", selected_only=False),
        "selected_duplicate_of_issue_ids": selected_duplicates,
        "external_duplicate_of_issue_ids": external_duplicates,
        "selected_duplicated_by_issue_ids": _unique_issue_ids(issue_links, role="duplicated_by", selected_only=True),
        "external_duplicated_by_issue_ids": _unique_issue_ids(issue_links, role="duplicated_by", selected_only=False),
        "selected_parent_issue_ids": selected_children,
        "selected_child_issue_ids": selected_parents,
        "selected_related_issue_ids": _unique_issue_ids(issue_links, role="related_to", selected_only=True),
        "external_related_issue_ids": _unique_issue_ids(issue_links, role="related_to", selected_only=False),
        "planner_notes": planner_notes,
    }


def _issue_execution_sort_key(issue: dict[str, Any]) -> tuple[int, int, int, int, str]:
    analysis = issue.get("plan_link_analysis") or {}
    has_external_blockers = 1 if analysis.get("external_blocked_by_issue_ids") else 0
    has_duplicate_warning = 1 if analysis.get("selected_duplicate_of_issue_ids") or analysis.get("external_duplicate_of_issue_ids") else 0
    return (
        has_external_blockers,
        has_duplicate_warning,
        -(issue.get("score") or 0),
        issue.get("codex_estimate_minutes") or 99999,
        str(issue.get("issue_key") or issue.get("issue_id") or ""),
    )


def _selection_link_analysis(selected_issues: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_issue_ids = {issue["issue_id"] for issue in selected_issues}
    annotated: list[dict[str, Any]] = []
    for issue in selected_issues:
        payload = copy.deepcopy(issue)
        annotated_links = []
        for link in payload.get("issue_links") or []:
            enriched_link = copy.deepcopy(link)
            enriched_link["selected_in_plan"] = enriched_link.get("issue_id") in selected_issue_ids
            annotated_links.append(enriched_link)
        payload["issue_links"] = sorted(annotated_links, key=_issue_link_sort_key)
        payload["plan_link_analysis"] = _plan_link_analysis(payload)
        payload["ticket_overview"] = _ticket_overview(payload, warnings=payload.get("context_fetch_warnings") or [])
        annotated.append(payload)
    base_sorted = sorted(annotated, key=_issue_execution_sort_key)
    issue_by_id = {issue["issue_id"]: issue for issue in base_sorted}
    base_order = {issue["issue_id"]: index for index, issue in enumerate(base_sorted)}
    adjacency: dict[str, set[str]] = {issue["issue_id"]: set() for issue in base_sorted}
    dependency_edges: list[dict[str, Any]] = []
    for issue in base_sorted:
        for blocker_id in issue["plan_link_analysis"].get("selected_blocked_by_issue_ids") or []:
            if blocker_id in adjacency and blocker_id != issue["issue_id"] and issue["issue_id"] not in adjacency[blocker_id]:
                adjacency[blocker_id].add(issue["issue_id"])
                dependency_edges.append(
                    {
                        "from_issue_id": blocker_id,
                        "to_issue_id": issue["issue_id"],
                        "reason": "blocked_by",
                    }
                )
        for dependent_id in issue["plan_link_analysis"].get("selected_prerequisite_for_issue_ids") or []:
            if dependent_id in adjacency and dependent_id != issue["issue_id"] and dependent_id not in adjacency[issue["issue_id"]]:
                adjacency[issue["issue_id"]].add(dependent_id)
                dependency_edges.append(
                    {
                        "from_issue_id": issue["issue_id"],
                        "to_issue_id": dependent_id,
                        "reason": "prerequisite_for",
                    }
                )
    incoming: dict[str, int] = {issue_id: 0 for issue_id in adjacency}
    for source_issue_id, targets in adjacency.items():
        for target_issue_id in targets:
            incoming[target_issue_id] += 1
    ready = sorted([issue_id for issue_id, count in incoming.items() if count == 0], key=lambda issue_id: base_order[issue_id])
    ordered_issue_ids: list[str] = []
    while ready:
        issue_id = ready.pop(0)
        ordered_issue_ids.append(issue_id)
        for target_issue_id in sorted(adjacency[issue_id], key=lambda candidate: base_order[candidate]):
            incoming[target_issue_id] -= 1
            if incoming[target_issue_id] == 0:
                ready.append(target_issue_id)
                ready.sort(key=lambda candidate: base_order[candidate])
    planning_warnings: list[str] = []
    if len(ordered_issue_ids) != len(base_sorted):
        remaining_issue_ids = [issue_id for issue_id in base_order if issue_id not in ordered_issue_ids]
        remaining_issue_ids.sort(key=lambda issue_id: base_order[issue_id])
        planning_warnings.append(f"Dependency cycle detected across selected issues: {', '.join(remaining_issue_ids)}.")
        ordered_issue_ids.extend(remaining_issue_ids)
    ordered_issues = [issue_by_id[issue_id] for issue_id in ordered_issue_ids]
    planning_warnings.extend(
        note
        for issue in ordered_issues
        for note in (issue.get("plan_link_analysis") or {}).get("planner_notes") or []
        if note
    )
    external_blockers = [
        {
            "issue_id": issue["issue_id"],
            "blocked_by_issue_ids": issue["plan_link_analysis"]["external_blocked_by_issue_ids"],
        }
        for issue in ordered_issues
        if issue.get("plan_link_analysis", {}).get("external_blocked_by_issue_ids")
    ]
    duplicate_candidates = [
        {
            "issue_id": issue["issue_id"],
            "duplicate_of_issue_ids": issue["plan_link_analysis"]["selected_duplicate_of_issue_ids"]
            or issue["plan_link_analysis"]["external_duplicate_of_issue_ids"],
        }
        for issue in ordered_issues
        if issue.get("plan_link_analysis", {}).get("selected_duplicate_of_issue_ids")
        or issue.get("plan_link_analysis", {}).get("external_duplicate_of_issue_ids")
    ]
    selection_analysis = {
        "ordered_issue_ids": ordered_issue_ids,
        "dependency_edges": dependency_edges,
        "external_blockers": external_blockers,
        "duplicate_candidates": duplicate_candidates,
        "planning_warnings": list(dict.fromkeys(planning_warnings)),
    }
    return ordered_issues, selection_analysis


def _proposal_from_issue(issue: dict[str, Any], stage_id: str) -> dict[str, Any]:
    issue_key = issue["issue_key"]
    task_id = sanitize_identifier(issue_key, issue_key.lower())
    return {
        "task_id": task_id,
        "title": f"{issue_key} {issue['summary']}",
        "objective": issue["summary"],
        "stage_id": stage_id,
        "issue_entity_id": issue.get("issue_entity_id"),
        "issue_id": issue["issue_id"],
        "issue_key": issue_key,
        "issue_url": issue["issue_url"],
        "summary": issue["summary"],
        "description": issue.get("description"),
        "youtrack_estimate_minutes": issue.get("youtrack_estimate_minutes"),
        "youtrack_spent_minutes": issue.get("youtrack_spent_minutes"),
        "codex_estimate_minutes": issue.get("codex_estimate_minutes"),
        "score": issue.get("score"),
        "work_items": copy.deepcopy(issue.get("work_items") or []),
        "comments": copy.deepcopy(issue.get("comments") or []),
        "recent_activities": copy.deepcopy(issue.get("recent_activities") or []),
        "recent_activity_page": copy.deepcopy(issue.get("recent_activity_page") or {}),
        "issue_links": copy.deepcopy(issue.get("issue_links") or []),
        "link_summary": copy.deepcopy(issue.get("link_summary") or _issue_link_summary(issue.get("issue_links") or [])),
        "external_references": copy.deepcopy(issue.get("external_references") or []),
        "external_reference_overview": copy.deepcopy(issue.get("external_reference_overview") or _external_reference_overview([])),
        "related_issue_summaries": copy.deepcopy(issue.get("related_issue_summaries") or []),
        "related_issue_overview": copy.deepcopy(issue.get("related_issue_overview") or _related_issue_overview([])),
        "plan_link_analysis": copy.deepcopy(issue.get("plan_link_analysis") or {}),
        "ticket_overview": copy.deepcopy(issue.get("ticket_overview") or _ticket_overview(issue)),
        "context_fetch_warnings": copy.deepcopy(issue.get("context_fetch_warnings") or []),
        "deep_context_version": issue.get("deep_context_version"),
        "branch_hint": f"task/{slugify(issue_key)}-{slugify(issue['summary'])[:32]}",
        "external_issue": {
            "connection_id": None,
            "issue_entity_id": issue.get("issue_entity_id"),
            "issue_id": issue["issue_id"],
            "issue_key": issue_key,
            "issue_url": issue["issue_url"],
            "summary": issue["summary"],
            "description": issue.get("description"),
            "field_snapshot": copy.deepcopy(issue.get("custom_fields") or {}),
            "youtrack_estimate_minutes": issue.get("youtrack_estimate_minutes"),
            "youtrack_spent_minutes": issue.get("youtrack_spent_minutes"),
            "work_items": copy.deepcopy(issue.get("work_items") or []),
            "comments": copy.deepcopy(issue.get("comments") or []),
            "recent_activities": copy.deepcopy(issue.get("recent_activities") or []),
            "recent_activity_page": copy.deepcopy(issue.get("recent_activity_page") or {}),
            "issue_links": copy.deepcopy(issue.get("issue_links") or []),
            "link_summary": copy.deepcopy(issue.get("link_summary") or _issue_link_summary(issue.get("issue_links") or [])),
            "external_references": copy.deepcopy(issue.get("external_references") or []),
            "external_reference_overview": copy.deepcopy(issue.get("external_reference_overview") or _external_reference_overview([])),
            "related_issue_summaries": copy.deepcopy(issue.get("related_issue_summaries") or []),
            "related_issue_overview": copy.deepcopy(issue.get("related_issue_overview") or _related_issue_overview([])),
            "plan_link_analysis": copy.deepcopy(issue.get("plan_link_analysis") or {}),
            "ticket_overview": copy.deepcopy(issue.get("ticket_overview") or _ticket_overview(issue)),
            "context_fetch_warnings": copy.deepcopy(issue.get("context_fetch_warnings") or []),
            "deep_context_version": issue.get("deep_context_version"),
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
        stage_planner_notes = list(
            dict.fromkeys(
                note
                for proposal in stage_proposals
                for note in (
                    f"{proposal['issue_key']}: {planner_note}"
                    for planner_note in (proposal.get("plan_link_analysis") or {}).get("planner_notes") or []
                )
            )
        )
        selected_dependency_issue_ids = list(
            dict.fromkeys(
                issue_id
                for proposal in stage_proposals
                for issue_id in (proposal.get("plan_link_analysis") or {}).get("selected_blocked_by_issue_ids") or []
            )
        )
        external_dependency_issue_ids = list(
            dict.fromkeys(
                issue_id
                for proposal in stage_proposals
                for issue_id in (proposal.get("plan_link_analysis") or {}).get("external_blocked_by_issue_ids") or []
            )
        )
        stages.append(
            {
                "id": stage_id,
                "title": stage_title,
                "objective": f"Resolve {objective}.",
                "canonical_execution_slices": [proposal["task_id"] for proposal in stage_proposals],
                "source_issue_ids": [proposal["issue_id"] for proposal in stage_proposals],
                "planner_notes": stage_planner_notes,
                "planning_signals": {
                    "selected_dependency_issue_ids": selected_dependency_issue_ids,
                    "external_dependency_issue_ids": external_dependency_issue_ids,
                },
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
    connection = _read_connection_record(paths, session["connection_id"])
    secret = _connection_secret(paths, session["connection_id"])
    selected = [_attach_ticket_context(connection, secret["token"], item) for item in selected]
    for issue_payload in selected:
        update_issue_snapshot(workspace, issue_payload, connection_id=session["connection_id"])
    selected, selection_analysis = _selection_link_analysis(selected)
    session = _update_session_issue_payloads(session, selected)
    session["selection_analysis"] = selection_analysis
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
        "selection_analysis": selection_analysis,
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


def _tasks_for_workstream(workspace: str | Path, workstream_id: str | None) -> list[dict[str, Any]]:
    if not workstream_id:
        return []
    return [
        item
        for item in (_load_tasks_index(workspace_paths(workspace)).get("items") or [])
        if item.get("linked_workstream_id") == workstream_id
    ]


def _existing_workstream_for_plan(workspace: str | Path, plan_id: str) -> str | None:
    for item in list_workstreams(workspace).get("items") or []:
        source_context = item.get("source_context") or {}
        if source_context.get("provider") == "youtrack" and source_context.get("plan_id") == plan_id:
            return item.get("workstream_id")
    return None


def _ensure_current_workstream_selected(workspace: str | Path, workstream_id: str | None) -> None:
    if not workstream_id:
        return
    try:
        current = current_workstream(workspace)
    except Exception:
        current = None
    if (current or {}).get("workstream_id") != workstream_id:
        switch_workstream(workspace, workstream_id)


def _plan_source_context(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "youtrack",
        "connection_id": plan["connection_id"],
        "search_session_id": plan["search_session_id"],
        "plan_id": plan["plan_id"],
    }


def _plan_scope_summary(plan: dict[str, Any]) -> str:
    return f"YouTrack plan derived from search session {plan['search_session_id']}"


def _plan_brief_markdown(plan: dict[str, Any]) -> str:
    brief_lines = [
        "# StageExecutionBrief",
        "",
        f"YouTrack plan: {plan['plan_id']}",
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
    return "\n".join(brief_lines)


def _refresh_youtrack_planner_notes(register: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    note = f"YouTrack plan {plan['plan_id']} applied from search session {plan['search_session_id']}."
    planner_notes = [
        item
        for item in (register.get("planner_notes") or [])
        if not str(item).startswith("YouTrack plan ")
    ]
    planner_notes.append(note)
    register["planner_notes"] = planner_notes
    return register


def _plan_stage_payloads_for_register(plan: dict[str, Any], register: dict[str, Any]) -> list[dict[str, Any]]:
    existing_stages = {
        stage.get("id"): stage
        for stage in (register.get("stages") or [])
        if stage.get("id")
    }
    merged_stages: list[dict[str, Any]] = []
    for stage in plan.get("stages") or []:
        payload = copy.deepcopy(stage)
        existing = existing_stages.get(payload.get("id")) or {}
        if existing.get("status") is not None:
            payload["status"] = existing.get("status")
        if existing.get("completed_at") is not None:
            payload["completed_at"] = existing.get("completed_at")
        merged_stages.append(payload)
    return merged_stages


def _reapply_plan_to_existing_workstream(
    workspace: str | Path,
    *,
    plan: dict[str, Any],
    workstream_id: str,
) -> dict[str, Any]:
    paths = _youtrack_paths(workspace)
    _ensure_current_workstream_selected(workspace, workstream_id)
    current = current_workstream(workspace)
    register = copy.deepcopy(current.get("register") or {})
    if any(stage.get("status") == "completed" for stage in (register.get("stages") or [])):
        raise RuntimeError(
            "Cannot reapply a new YouTrack plan onto a workstream with completed stages."
        )

    existing_tasks = _tasks_for_workstream(workspace, workstream_id)
    existing_task_ids = {item.get("task_id") for item in existing_tasks}
    expected_task_ids = [proposal["task_id"] for proposal in (plan.get("task_proposals") or [])]
    expected_task_id_set = set(expected_task_ids)
    unexpected_task_ids = sorted(task_id for task_id in existing_task_ids if task_id not in expected_task_id_set)
    if unexpected_task_ids:
        raise RuntimeError(
            "Current workstream task set contains issues that are not present in the selected YouTrack plan: "
            + ", ".join(unexpected_task_ids)
            + "."
        )

    workstreams_index = _load_workstreams_index(paths)
    workstream_record = _workstream_record_by_id(workstreams_index, workstream_id)
    workstream_record["title"] = plan["workstream_title"]
    workstream_record["scope_summary"] = _plan_scope_summary(plan)
    workstream_record["source_context"] = _plan_source_context(plan)
    workstream_record["updated_at"] = now_iso()
    _save_workstreams_index(paths, workstreams_index)

    register["stages"] = _plan_stage_payloads_for_register(plan, register)
    register["plan_status"] = "confirmed"
    register["scope_summary"] = _plan_scope_summary(plan)
    register["source_context"] = _plan_source_context(plan)
    register = _refresh_youtrack_planner_notes(register, plan)
    write_stage_register(workspace, register, confirmed_stage_plan_edit=True, workstream_id=workstream_id)

    task_by_id = {item["task_id"]: item for item in existing_tasks}
    refreshed_task_ids: list[str] = []
    for proposal in plan.get("task_proposals") or []:
        existing_task = task_by_id.get(proposal["task_id"])
        if existing_task:
            payload = _normalize_task_payload(existing_task)
            payload["title"] = proposal["title"]
            payload["objective"] = proposal["objective"]
            payload["branch_hint"] = proposal.get("branch_hint")
            payload["linked_workstream_id"] = workstream_id
            payload["stage_id"] = proposal.get("stage_id")
            payload["codex_estimate_minutes"] = proposal.get("codex_estimate_minutes")
            payload["external_issue"] = copy.deepcopy(proposal.get("external_issue"))
            _persist_task_record(workspace, payload)
        else:
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
                make_current=False,
                sync_issue_ledger=False,
            )
            task_by_id[proposal["task_id"]] = task_result["task"]
        proposal["created_task_id"] = proposal["task_id"]
        proposal["linked_workstream_id"] = workstream_id
        refreshed_task_ids.append(proposal["task_id"])
        update_issue_snapshot(
            workspace,
            proposal["external_issue"]
            | {
                "issue_entity_id": proposal.get("issue_entity_id"),
                "issue_id": proposal["issue_id"],
                "issue_key": proposal["issue_key"],
                "issue_url": proposal["issue_url"],
                "summary": proposal["summary"],
                "youtrack_estimate_minutes": proposal.get("youtrack_estimate_minutes"),
                "youtrack_spent_minutes": proposal.get("youtrack_spent_minutes"),
                "codex_estimate_minutes": proposal.get("codex_estimate_minutes"),
            },
            connection_id=plan["connection_id"],
        )

    recompute_issue_ledgers(
        workspace,
        targets=[
            (plan["connection_id"], proposal["issue_id"])
            for proposal in (plan.get("task_proposals") or [])
            if proposal.get("issue_id")
        ],
    )
    plan["status"] = "applied"
    plan["applied_workstream_id"] = workstream_id
    plan["created_task_ids"] = refreshed_task_ids
    plan["applied_at"] = plan.get("applied_at") or now_iso()
    plan["updated_at"] = now_iso()
    _write_json(_plan_draft_path(paths, plan["plan_id"]), plan)
    _ensure_current_workstream_selected(workspace, workstream_id)
    set_active_brief(workspace, _plan_brief_markdown(plan))
    return {
        "workspace_path": paths["workspace_path"],
        "plan": plan,
        "workstream": current_workstream(workspace),
        "tasks": _tasks_for_workstream(workspace, workstream_id),
    }


def apply_youtrack_workstream_plan(
    workspace: str | Path,
    *,
    plan_id: str | None = None,
    confirmed: bool = False,
    activate_first_task: bool = False,
    reuse_current_workstream: bool = False,
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
    if reuse_current_workstream:
        current = current_workstream(workspace)
        current_source = current.get("source_context") or {}
        if current_source.get("provider") != "youtrack":
            raise RuntimeError("Current workstream is not a YouTrack-backed workstream.")
        if plan.get("applied_workstream_id") and plan.get("applied_workstream_id") != current["workstream_id"]:
            raise RuntimeError(
                "This YouTrack plan is already linked to a different workstream and cannot be reused here."
            )
        return _reapply_plan_to_existing_workstream(
            workspace,
            plan=plan,
            workstream_id=current["workstream_id"],
        )
    existing_workstream_id = plan.get("applied_workstream_id") or _existing_workstream_for_plan(workspace, plan["plan_id"])
    if plan.get("status") == "applied" and existing_workstream_id:
        _ensure_current_workstream_selected(workspace, existing_workstream_id)
        if plan.get("applied_workstream_id") != existing_workstream_id:
            plan["applied_workstream_id"] = existing_workstream_id
            plan["updated_at"] = now_iso()
            _write_json(_plan_draft_path(paths, plan_id), plan)
        return {
            "workspace_path": paths["workspace_path"],
            "plan": plan,
            "workstream": current_workstream(workspace),
            "tasks": _tasks_for_workstream(workspace, existing_workstream_id),
        }
    if existing_workstream_id:
        expected_task_ids = [proposal["task_id"] for proposal in (plan.get("task_proposals") or [])]
        existing_tasks = _tasks_for_workstream(workspace, existing_workstream_id)
        existing_task_ids = {item.get("task_id") for item in existing_tasks}
        missing_task_ids = [task_id for task_id in expected_task_ids if task_id not in existing_task_ids]
        if missing_task_ids:
            raise RuntimeError(
                "YouTrack plan appears partially applied. "
                f"Existing workstream `{existing_workstream_id}` is missing tasks: {', '.join(missing_task_ids)}."
            )
        _ensure_current_workstream_selected(workspace, existing_workstream_id)
        for proposal in plan.get("task_proposals") or []:
            proposal["created_task_id"] = proposal["task_id"]
            proposal["linked_workstream_id"] = existing_workstream_id
        plan["status"] = "applied"
        plan["applied_workstream_id"] = existing_workstream_id
        plan["created_task_ids"] = expected_task_ids
        plan["applied_at"] = plan.get("applied_at") or now_iso()
        plan["updated_at"] = now_iso()
        _write_json(_plan_draft_path(paths, plan_id), plan)
        return {
            "workspace_path": paths["workspace_path"],
            "plan": plan,
            "workstream": current_workstream(workspace),
            "tasks": existing_tasks,
        }
    workstream_result = create_workstream(
        workspace,
        title=plan["workstream_title"],
        kind="feature",
        scope_summary=_plan_scope_summary(plan),
        source_context=_plan_source_context(plan),
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
    register["scope_summary"] = _plan_scope_summary(plan)
    register["source_context"] = _plan_source_context(plan)
    register = _refresh_youtrack_planner_notes(register, plan)
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
            sync_issue_ledger=False,
        )
        created_task_ids.append(task_result["created_task_id"])
        proposal["created_task_id"] = task_result["created_task_id"]
        proposal["linked_workstream_id"] = workstream_id
        update_issue_snapshot(workspace, proposal["external_issue"] | {
            "issue_entity_id": proposal.get("issue_entity_id"),
            "issue_id": proposal["issue_id"],
            "issue_key": proposal["issue_key"],
            "issue_url": proposal["issue_url"],
            "summary": proposal["summary"],
            "youtrack_estimate_minutes": proposal.get("youtrack_estimate_minutes"),
            "youtrack_spent_minutes": proposal.get("youtrack_spent_minutes"),
            "codex_estimate_minutes": proposal.get("codex_estimate_minutes"),
        }, connection_id=plan["connection_id"])
    recompute_issue_ledgers(
        workspace,
        targets=[
            (plan["connection_id"], proposal["issue_id"])
            for proposal in (plan.get("task_proposals") or [])
            if proposal.get("issue_id")
        ],
    )
    plan["status"] = "applied"
    plan["applied_workstream_id"] = workstream_id
    plan["created_task_ids"] = created_task_ids
    plan["applied_at"] = now_iso()
    plan["updated_at"] = now_iso()
    _write_json(_plan_draft_path(paths, plan_id), plan)
    set_active_brief(workspace, _plan_brief_markdown(plan))
    return {
        "workspace_path": paths["workspace_path"],
        "plan": plan,
        "workstream": current_workstream(workspace),
        "tasks": _tasks_for_workstream(workspace, workstream_id),
    }


def _dashboard_issue_text_excerpt(text: str | None, *, limit: int = 220) -> str | None:
    raw = str(text or "")
    if not raw.strip():
        return None
    without_urls = URL_PATTERN.sub(" ", raw)
    normalized = re.sub(r"\s+", " ", without_urls).strip(" \t\r\n-:;,")
    return _excerpt_text(normalized, limit=limit)


def _dashboard_reference_excerpt(reference: dict[str, Any]) -> str | None:
    summary = _excerpt_text(reference.get("summary"), limit=220)
    if not summary:
        return None
    title = _excerpt_text(reference.get("title"), limit=80)
    if title and summary.lower().startswith(title.lower()):
        return summary
    return _excerpt_text(f"{title}: {summary}" if title else summary, limit=220)


def _dashboard_issue_hover_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(snapshot or {})
    ticket_overview = payload.get("ticket_overview") or {}
    openable_references = [
        item for item in (payload.get("external_references") or []) if item.get("openable") and item.get("summary")
    ]
    related_issue_summaries = payload.get("related_issue_summaries") or []
    excerpt = None
    source = None

    description_excerpt = _dashboard_issue_text_excerpt(payload.get("description"))
    if description_excerpt and len(description_excerpt) >= 48:
        excerpt = description_excerpt
        source = "description"
    if excerpt is None and openable_references:
        excerpt = _dashboard_reference_excerpt(openable_references[0])
        source = "external_reference" if excerpt else None
    if excerpt is None and description_excerpt:
        excerpt = description_excerpt
        source = "description"
    if excerpt is None:
        for comment in payload.get("comments") or []:
            excerpt = _dashboard_issue_text_excerpt(comment.get("text") or comment.get("textPreview"))
            if excerpt:
                source = "comment"
                break
    if excerpt is None:
        for item in related_issue_summaries:
            related_excerpt = _dashboard_issue_text_excerpt(item.get("description_excerpt"))
            if related_excerpt:
                issue_key = item.get("issue_key")
                excerpt = _excerpt_text(f"{issue_key}: {related_excerpt}" if issue_key else related_excerpt, limit=220)
                source = "related_issue"
                break
    if excerpt is None:
        excerpt = _excerpt_text(payload.get("summary"), limit=220)
        source = "summary" if excerpt else None

    related_issue_keys: list[str] = []
    for item in related_issue_summaries:
        issue_key = str(item.get("issue_key") or "").strip()
        if issue_key and issue_key not in related_issue_keys:
            related_issue_keys.append(issue_key)

    return {
        "excerpt": excerpt,
        "source": source,
        "comment_count": ticket_overview.get("comment_count", len(payload.get("comments") or [])),
        "linked_issue_count": ticket_overview.get("linked_issue_count", len(payload.get("issue_links") or [])),
        "external_reference_count": ticket_overview.get("external_reference_count", len(payload.get("external_references") or [])),
        "openable_external_reference_count": ticket_overview.get("openable_external_reference_count", len(openable_references)),
        "related_issue_count": ticket_overview.get("related_issue_count", len(related_issue_summaries)),
        "warning_count": len(ticket_overview.get("warnings") or []),
        "reference_titles": [item.get("title") for item in openable_references if item.get("title")][:2],
        "related_issue_keys": related_issue_keys[:4],
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
        latest_snapshot = (ledger or {}).get("latest_snapshot") or {}
        hover_snapshot = _merged_issue_snapshot(external_issue, latest_snapshot)
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
                "hover_summary": _dashboard_issue_hover_summary(hover_snapshot),
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


def _dashboard_search_session_summary(search_session: dict[str, Any] | None) -> dict[str, Any] | None:
    if not search_session:
        return None
    shortlist_page = search_session.get("shortlist_page") or {}
    return {
        "session_id": search_session.get("session_id"),
        "resolved_query": search_session.get("resolved_query"),
        "result_count": search_session.get("result_count"),
        "result_count_exact": search_session.get("result_count_exact"),
        "shortlist_count": len(search_session.get("shortlist") or []),
        "selected_issue_count": len(search_session.get("selected_issue_ids") or []),
        "shortlist_page": {
            "skip": shortlist_page.get("skip"),
            "page_size": shortlist_page.get("page_size"),
            "returned": shortlist_page.get("returned"),
        },
        "updated_at": search_session.get("updated_at"),
    }


def _dashboard_plan_summary(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    return {
        "plan_id": plan.get("plan_id"),
        "status": plan.get("status"),
        "selected_issue_count": len(plan.get("selected_issue_ids") or []),
        "stage_count": len(plan.get("stages") or []),
        "task_proposal_count": len(plan.get("task_proposals") or []),
        "applied_workstream_id": plan.get("applied_workstream_id"),
        "updated_at": plan.get("updated_at"),
    }


def workspace_youtrack_dashboard_detail(workspace: str | Path) -> dict[str, Any]:
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
        "current_search_session": _dashboard_search_session_summary(search_session),
        "current_plan": _dashboard_plan_summary(plan),
        "current_workstream_issues": workstream_issue_cards(workspace, workstream_id=workstream_id),
    }
