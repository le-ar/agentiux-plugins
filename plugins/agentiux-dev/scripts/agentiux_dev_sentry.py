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
    _write_json,
    current_task,
    now_iso,
    read_task,
    sanitize_identifier,
)


SENTRY_SCHEMA_VERSION = 1
SENTRY_CONNECTION_SCHEMA_VERSION = 1
SENTRY_PROJECT_CATALOG_SCHEMA_VERSION = 1
SENTRY_TOPOLOGY_SCHEMA_VERSION = 1
SENTRY_SEARCH_SCHEMA_VERSION = 1
SENTRY_ISSUE_SNAPSHOT_SCHEMA_VERSION = 1

DEFAULT_SEARCH_LIMIT = 8
DEFAULT_SHORTLIST_SIZE = 3
DEFAULT_STATS_PERIOD = "14d"
DEFAULT_EVENT_SAMPLE_LIMIT = 3
REQUEST_TIMEOUT_SECONDS = 8

SENTRY_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
STRUCTURED_QUERY_PATTERN = re.compile(r"(^|\s)[a-z_.-]+:", re.IGNORECASE)
SURFACE_RULES = {
    "backend/api": {
        "label": "Backend API",
        "tokens": {"api", "backend", "server", "gateway", "service"},
        "exclude": {"worker", "queue", "job", "cron", "admin", "ios", "android", "mobile"},
    },
    "backend/workers": {
        "label": "Backend Workers",
        "tokens": {"worker", "workers", "queue", "job", "jobs", "cron", "consumer"},
        "exclude": {"web", "admin", "ios", "android", "mobile"},
    },
    "frontend/web": {
        "label": "Frontend Web",
        "tokens": {"web", "frontend", "front", "site", "storefront", "landing", "checkout"},
        "exclude": {"admin", "ios", "android", "mobile", "worker"},
    },
    "frontend/admin": {
        "label": "Frontend Admin",
        "tokens": {"admin", "backoffice", "console", "dashboard", "ops"},
        "exclude": {"ios", "android", "mobile", "worker"},
    },
    "mobile/android": {
        "label": "Mobile Android",
        "tokens": {"android", "playstore", "pixel", "samsung"},
        "exclude": {"ios", "web", "admin", "backend"},
    },
    "mobile/ios": {
        "label": "Mobile iOS",
        "tokens": {"ios", "iphone", "ipad", "appstore"},
        "exclude": {"android", "web", "admin", "backend"},
    },
    "mobile/shared": {
        "label": "Mobile Shared",
        "tokens": {"mobile", "react", "native", "react-native", "expo", "app"},
        "exclude": {"android", "ios", "web", "admin", "worker"},
    },
}
TAG_KEYS_OF_INTEREST = [
    "environment",
    "release",
    "dist",
    "transaction",
    "level",
    "device",
    "device.family",
    "device.class",
    "device.model",
    "browser",
    "browser.name",
    "browser.version",
    "os",
    "os.name",
    "os.version",
    "runtime",
    "runtime.name",
]

MESSAGE_LINE_PATTERN = re.compile(r"^\s*Message:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
SYMPTOM_PATTERNS = [
    re.compile(r"(Network request failed)", re.IGNORECASE),
    re.compile(r"(Cannot load an empty url)", re.IGNORECASE),
    re.compile(r"(Cannot read property [^\n\r]+)", re.IGNORECASE),
]


def _sentry_paths(workspace: str | Path) -> dict[str, Any]:
    base_paths = _ensure_workspace_initialized(workspace)
    root = Path(base_paths["sentry_root"])
    return {
        **base_paths,
        "root": root,
        "connections_dir": Path(base_paths["sentry_connections_dir"]),
        "connections_index": Path(base_paths["sentry_connections_dir"]) / "index.json",
        "current_connection": Path(base_paths["sentry_connections_dir"]) / "current.json",
        "secrets_dir": Path(base_paths["sentry_secrets_dir"]),
        "project_catalogs_dir": Path(base_paths["sentry_project_catalogs_dir"]),
        "topologies_dir": Path(base_paths["sentry_topologies_dir"]),
        "searches_dir": Path(base_paths["sentry_searches_dir"]),
        "current_search": Path(base_paths["sentry_searches_dir"]) / "current.json",
        "issues_dir": Path(base_paths["sentry_issues_dir"]),
    }


def _ensure_sentry_dirs(paths: dict[str, Any]) -> None:
    for key in (
        "root",
        "connections_dir",
        "secrets_dir",
        "project_catalogs_dir",
        "topologies_dir",
        "searches_dir",
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


def _project_catalog_path(paths: dict[str, Any], connection_id: str) -> Path:
    return Path(paths["project_catalogs_dir"]) / f"{sanitize_identifier(connection_id, 'connection')}.json"


def _topology_path(paths: dict[str, Any], connection_id: str) -> Path:
    return Path(paths["topologies_dir"]) / f"{sanitize_identifier(connection_id, 'connection')}.json"


def _search_session_path(paths: dict[str, Any], session_id: str) -> Path:
    return Path(paths["searches_dir"]) / f"{sanitize_identifier(session_id, 'search')}.json"


def _issue_snapshot_path(paths: dict[str, Any], connection_id: str, issue_id: str) -> Path:
    issue_key = sanitize_identifier(f"{connection_id}-{issue_id}", "issue")
    return Path(paths["issues_dir"]) / f"{issue_key}.json"


def _default_connections_index() -> dict[str, Any]:
    return {
        "schema_version": SENTRY_SCHEMA_VERSION,
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


def _save_current_pointer(path: Path, field: str, value: str | None) -> None:
    _write_json(path, {field: value, "updated_at": now_iso()})


def _persist_default_connection(paths: dict[str, Any], connection_id: str | None) -> None:
    _save_current_pointer(Path(paths["current_connection"]), "connection_id", connection_id)


def _set_default_flag(paths: dict[str, Any], connection_id: str | None) -> dict[str, Any]:
    resolved_default_id = sanitize_identifier(connection_id, "connection") if connection_id else None
    index = _load_connections_index(paths)
    index["default_connection_id"] = resolved_default_id
    rewritten_items: list[dict[str, Any]] = []
    for item in index.get("items", []):
        record = _maybe_read_connection_record(paths, item.get("connection_id"))
        if not record:
            continue
        record["default"] = record.get("connection_id") == resolved_default_id
        record = _write_connection_record(paths, record)
        rewritten_items.append(
            {
                "connection_id": record["connection_id"],
                "label": record["label"],
                "base_url": record["base_url"],
                "organization_slug": record["organization_slug"],
                "default": record["default"],
                "status": record["status"],
                "last_tested_at": record.get("last_tested_at"),
                "updated_at": record.get("updated_at"),
            }
        )
    index["items"] = rewritten_items
    _save_connections_index(paths, index)
    _persist_default_connection(paths, resolved_default_id)
    return index


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


def _remove_connection_index_item(paths: dict[str, Any], connection_id: str) -> dict[str, Any]:
    normalized_connection_id = sanitize_identifier(connection_id, "connection")
    index = _load_connections_index(paths)
    index["items"] = [item for item in index.get("items", []) if item.get("connection_id") != normalized_connection_id]
    if index.get("default_connection_id") == normalized_connection_id:
        index["default_connection_id"] = index["items"][0]["connection_id"] if index["items"] else None
    _save_connections_index(paths, index)
    _save_current_pointer(Path(paths["current_connection"]), "connection_id", index.get("default_connection_id"))
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
        return [part.strip() for part in re.split(r"[,\n]+", project_scope) if part.strip()]
    return [str(item).strip() for item in project_scope if str(item).strip()]


def _normalize_base_url(base_url: str) -> str:
    raw = (base_url or "").strip()
    if not raw:
        raise ValueError("Sentry base_url is required.")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid Sentry base_url: {base_url}")
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _default_connection_record(
    connection_id: str,
    label: str,
    base_url: str,
    organization_slug: str,
    project_scope: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SENTRY_CONNECTION_SCHEMA_VERSION,
        "connection_id": connection_id,
        "label": label,
        "base_url": _normalize_base_url(base_url),
        "organization_slug": sanitize_identifier(organization_slug, "organization"),
        "auth_mode": "bearer_token",
        "default": False,
        "status": "configured",
        "last_tested_at": None,
        "last_error": None,
        "project_scope": project_scope,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def _normalize_connection_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(record or {})
    payload["schema_version"] = payload.get("schema_version", SENTRY_CONNECTION_SCHEMA_VERSION)
    payload["connection_id"] = sanitize_identifier(payload.get("connection_id"), "connection")
    payload["label"] = payload.get("label") or payload["connection_id"]
    payload["base_url"] = _normalize_base_url(payload.get("base_url") or "")
    payload["organization_slug"] = sanitize_identifier(payload.get("organization_slug"), "organization")
    payload["auth_mode"] = "bearer_token"
    payload["default"] = bool(payload.get("default"))
    payload["status"] = payload.get("status") or "configured"
    payload["project_scope"] = _normalize_project_scope(payload.get("project_scope"))
    payload["created_at"] = payload.get("created_at") or now_iso()
    payload["updated_at"] = now_iso()
    return payload


def _write_connection_record(paths: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_connection_record(record)
    _write_json(_connection_record_path(paths, normalized["connection_id"]), normalized)
    return normalized


def _read_connection_record(paths: dict[str, Any], connection_id: str) -> dict[str, Any]:
    payload = _load_json(
        _connection_record_path(paths, connection_id),
        default={},
        strict=True,
        purpose=f"Sentry connection `{connection_id}`",
    ) or {}
    if not payload:
        raise FileNotFoundError(f"Sentry connection `{connection_id}` was not found.")
    return _normalize_connection_record(payload)


def _maybe_read_connection_record(paths: dict[str, Any], connection_id: str | None) -> dict[str, Any] | None:
    if not connection_id:
        return None
    path = _connection_record_path(paths, connection_id)
    if not path.exists():
        return None
    return _normalize_connection_record(_load_json(path, default={}, strict=False) or {})


def _connection_secret(paths: dict[str, Any], connection_id: str) -> dict[str, Any]:
    payload = _load_json(
        _secret_path(paths, connection_id),
        default={},
        strict=True,
        purpose=f"Sentry token `{connection_id}`",
    ) or {}
    token = str(payload.get("token") or "").strip()
    if not token:
        raise ValueError(f"Sentry connection `{connection_id}` has no token.")
    return {"token": token}


def _read_project_catalog(paths: dict[str, Any], connection_id: str) -> dict[str, Any]:
    return copy.deepcopy(_load_json(_project_catalog_path(paths, connection_id), default={}, strict=False) or {})


def _read_topology(paths: dict[str, Any], connection_id: str) -> dict[str, Any]:
    return copy.deepcopy(_load_json(_topology_path(paths, connection_id), default={}, strict=False) or {})


def _redact_connection(
    connection: dict[str, Any],
    *,
    include_paths: bool = False,
    paths: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = copy.deepcopy(connection or {})
    payload["has_token"] = _secret_path(paths or _sentry_paths(payload.get("workspace_path") or "."), payload["connection_id"]).exists() if (include_paths and paths) else True
    payload.pop("auth_mode", None)
    payload.pop("workspace_path", None)
    if include_paths and paths:
        catalog = _read_project_catalog(paths, payload["connection_id"])
        topology = _read_topology(paths, payload["connection_id"])
        payload["paths"] = {
            "record_path": str(_connection_record_path(paths, payload["connection_id"])),
            "secret_path": str(_secret_path(paths, payload["connection_id"])),
            "project_catalog_path": str(_project_catalog_path(paths, payload["connection_id"])),
            "topology_path": str(_topology_path(paths, payload["connection_id"])),
        }
        payload["project_catalog_summary"] = {
            "project_count": len(catalog.get("items") or []),
            "updated_at": catalog.get("updated_at"),
            "platforms": sorted(
                {
                    str(item.get("platform") or "unknown")
                    for item in (catalog.get("items") or [])
                    if item.get("platform") or "unknown"
                }
            ),
        }
        payload["topology_summary"] = {
            "mapped_surface_count": len([item for item in (topology.get("surfaces") or []) if item.get("project_slugs")]),
            "mapped_project_count": len(topology.get("mapped_project_slugs") or []),
            "unmapped_project_count": len(topology.get("unmapped_projects") or []),
            "updated_at": topology.get("updated_at"),
        }
    return payload


def _request_json(
    connection: dict[str, Any],
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Any:
    base_url = connection["base_url"].rstrip("/")
    query = ""
    if params:
        encoded: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                encoded.extend((str(key), str(item)) for item in value)
            else:
                encoded.append((str(key), str(value)))
        if encoded:
            query = "?" + urllib.parse.urlencode(encoded, doseq=True)
    request = urllib.request.Request(
        f"{base_url}{path}{query}",
        method=method.upper(),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _safe_request_json(
    connection: dict[str, Any],
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Any | None:
    try:
        return _request_json(connection, token, method, path, params=params, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in {400, 403, 404}:
            return None
        raise


def _discover_organization_slug(base_url: str, token: str, organization_slug: str | None = None) -> str:
    if organization_slug:
        return sanitize_identifier(organization_slug, "organization")
    connection = {"base_url": _normalize_base_url(base_url)}
    organizations = _request_json(connection, token, "GET", "/api/0/organizations/") or []
    if len(organizations) == 1:
        discovered = organizations[0].get("slug") or organizations[0].get("id")
        return sanitize_identifier(discovered, "organization")
    if not organizations:
        raise ValueError("No Sentry organizations are visible for the provided token.")
    visible = [str(item.get("slug") or item.get("id") or "").strip() for item in organizations]
    raise ValueError(
        "Multiple Sentry organizations are available for this token. Re-run with an explicit organization slug. "
        f"Visible organizations: {', '.join(item for item in visible if item)}"
    )


def _normalize_project(item: dict[str, Any]) -> dict[str, Any]:
    slug = str(item.get("slug") or item.get("name") or item.get("id") or "").strip()
    project_id = str(item.get("id") or slug).strip()
    platform = str(item.get("platform") or "").strip() or None
    return {
        "project_id": project_id,
        "slug": sanitize_identifier(slug, "project"),
        "name": item.get("name") or slug,
        "platform": platform,
        "team": copy.deepcopy(item.get("team") or {}),
        "color": item.get("color"),
        "status": item.get("status") or "active",
        "date_created": item.get("dateCreated") or item.get("date_created"),
    }


def _project_catalog_from_connection(connection: dict[str, Any], token: str) -> dict[str, Any]:
    organization_slug = connection["organization_slug"]
    rows = _request_json(connection, token, "GET", f"/api/0/organizations/{organization_slug}/projects/") or []
    items = [_normalize_project(item) for item in rows]
    items.sort(key=lambda item: (item["slug"], item["project_id"]))
    return {
        "schema_version": SENTRY_PROJECT_CATALOG_SCHEMA_VERSION,
        "connection_id": connection["connection_id"],
        "organization_slug": organization_slug,
        "items": items,
        "updated_at": now_iso(),
    }


def _write_project_catalog(paths: dict[str, Any], connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _write_json(_project_catalog_path(paths, connection_id), payload)
    return payload


def _normalize_match_tokens(value: str | None) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(value or "").lower()) if token}


def _project_surface_matches(project: dict[str, Any], surface_id: str) -> tuple[int, list[str]]:
    rule = SURFACE_RULES[surface_id]
    text_tokens = _normalize_match_tokens(
        " ".join(
            str(part or "")
            for part in (
                project.get("slug"),
                project.get("name"),
                project.get("platform"),
            )
        )
    )
    matched = sorted(token for token in rule["tokens"] if token in text_tokens)
    excluded = sorted(token for token in rule["exclude"] if token in text_tokens)
    score = len(matched) * 5
    if project.get("platform"):
        platform_token = str(project["platform"]).lower()
        if surface_id.endswith("/android") and "android" in platform_token:
            score += 8
        if surface_id.endswith("/ios") and "cocoa" in platform_token:
            score += 8
        if surface_id == "mobile/shared" and any(token in platform_token for token in ("react-native", "native", "expo")):
            score += 8
        if surface_id.startswith("frontend/") and any(token in platform_token for token in ("javascript", "browser", "node")):
            score += 2
        if surface_id.startswith("backend/") and any(token in platform_token for token in ("python", "node", "java", "go", "rust")):
            score += 2
    if excluded:
        score -= len(excluded) * 4
    return score, matched


def _auto_discover_topology(
    workspace: str | Path,
    connection: dict[str, Any],
    project_catalog: dict[str, Any],
) -> dict[str, Any]:
    mapped_slugs: list[str] = []
    surfaces: list[dict[str, Any]] = []
    projects = project_catalog.get("items") or []
    for surface_id, rule in SURFACE_RULES.items():
        matched_projects: list[dict[str, Any]] = []
        reasons: list[str] = []
        for project in projects:
            score, matched_tokens = _project_surface_matches(project, surface_id)
            if score <= 0:
                continue
            matched_projects.append(
                {
                    "project_id": project["project_id"],
                    "slug": project["slug"],
                    "name": project["name"],
                    "platform": project.get("platform"),
                    "match_score": score,
                }
            )
            if matched_tokens:
                reasons.append(f"{project['slug']}: matched {', '.join(matched_tokens)}")
        matched_projects.sort(key=lambda item: (-int(item["match_score"]), item["slug"]))
        project_slugs = [item["slug"] for item in matched_projects]
        mapped_slugs.extend(project_slugs)
        surfaces.append(
            {
                "surface_id": surface_id,
                "label": rule["label"],
                "project_slugs": project_slugs,
                "project_ids": [item["project_id"] for item in matched_projects],
                "confidence": "heuristic" if project_slugs else "empty",
                "reasons": reasons[:6],
            }
        )
    mapped_slug_set = set(mapped_slugs)
    unresolved = [
        {
            "project_id": item["project_id"],
            "slug": item["slug"],
            "name": item["name"],
            "platform": item.get("platform"),
        }
        for item in projects
        if item["slug"] not in mapped_slug_set
    ]
    payload = {
        "schema_version": SENTRY_TOPOLOGY_SCHEMA_VERSION,
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "connection_id": connection["connection_id"],
        "organization_slug": connection["organization_slug"],
        "strategy": "heuristic_project_catalog_v1",
        "surfaces": surfaces,
        "mapped_project_slugs": sorted(mapped_slug_set),
        "unmapped_projects": unresolved,
        "generated_at": now_iso(),
        "updated_at": now_iso(),
    }
    return payload


def _write_topology(paths: dict[str, Any], connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _write_json(_topology_path(paths, connection_id), payload)
    return payload


def _ensure_topology(paths: dict[str, Any], connection: dict[str, Any], token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    project_catalog = _read_project_catalog(paths, connection["connection_id"])
    if not project_catalog.get("items"):
        project_catalog = _write_project_catalog(paths, connection["connection_id"], _project_catalog_from_connection(connection, token))
    topology = _read_topology(paths, connection["connection_id"])
    if not topology.get("surfaces"):
        topology = _write_topology(
            paths,
            connection["connection_id"],
            _auto_discover_topology(paths["workspace_path"], connection, project_catalog),
        )
    return project_catalog, topology


def _resolve_connection_id(paths: dict[str, Any], connection_id: str | None) -> str:
    if connection_id:
        return sanitize_identifier(connection_id, "connection")
    current = _load_json(Path(paths["current_connection"]), default={}, strict=False) or {}
    if current.get("connection_id"):
        return sanitize_identifier(current["connection_id"], "connection")
    index = _load_connections_index(paths)
    default_connection = index.get("default_connection_id")
    if default_connection:
        return sanitize_identifier(default_connection, "connection")
    items = index.get("items") or []
    if items:
        return sanitize_identifier(items[0]["connection_id"], "connection")
    raise FileNotFoundError("No Sentry connection is configured for this workspace.")


def _contains_structured_query(query_text: str) -> bool:
    return bool(STRUCTURED_QUERY_PATTERN.search(query_text or ""))


def _tag_dict(event_payload: dict[str, Any]) -> dict[str, Any]:
    tags = event_payload.get("tags") or []
    if isinstance(tags, dict):
        return copy.deepcopy(tags)
    result: dict[str, Any] = {}
    for item in tags:
        if isinstance(item, dict):
            key = item.get("key")
            value = item.get("value")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            key, value = item[0], item[1]
        else:
            continue
        if key:
            result[str(key)] = value
    return result


def _event_context_summary(event_payload: dict[str, Any]) -> dict[str, Any]:
    entries = copy.deepcopy(event_payload.get("entries") or [])
    contexts = copy.deepcopy(event_payload.get("contexts") or {})
    stacktraces: list[dict[str, Any]] = []
    breadcrumbs: list[dict[str, Any]] = []
    request_entry: dict[str, Any] | None = None
    for entry in entries:
        entry_type = entry.get("type")
        data = copy.deepcopy(entry.get("data") or {})
        if entry_type == "exception":
            for exception in data.get("values") or []:
                stacktraces.append(
                    {
                        "type": exception.get("type"),
                        "value": exception.get("value"),
                        "mechanism": copy.deepcopy(exception.get("mechanism") or {}),
                        "stacktrace": copy.deepcopy((exception.get("stacktrace") or {}).get("frames") or []),
                    }
                )
        elif entry_type == "breadcrumbs":
            breadcrumbs = copy.deepcopy(data.get("values") or [])
        elif entry_type == "request":
            request_entry = data
    return {
        "event_id": event_payload.get("id") or event_payload.get("eventID"),
        "title": event_payload.get("title"),
        "message": event_payload.get("message"),
        "platform": event_payload.get("platform"),
        "date_created": event_payload.get("dateCreated"),
        "date_received": event_payload.get("dateReceived"),
        "level": event_payload.get("level"),
        "user": copy.deepcopy(event_payload.get("user") or {}),
        "tags": _tag_dict(event_payload),
        "contexts": contexts,
        "request": request_entry,
        "sdk": copy.deepcopy(event_payload.get("sdk") or {}),
        "dist": event_payload.get("dist"),
        "group_id": event_payload.get("groupID"),
        "entries": entries,
        "stacktraces": stacktraces,
        "breadcrumbs": breadcrumbs,
    }


def _issue_tag_values(connection: dict[str, Any], token: str, issue_id: str, tag_key: str) -> list[dict[str, Any]]:
    payload = _safe_request_json(
        connection,
        token,
        "GET",
        f"/api/0/organizations/{connection['organization_slug']}/issues/{urllib.parse.quote(issue_id, safe='')}/tags/{urllib.parse.quote(tag_key, safe='')}/values/",
    )
    return copy.deepcopy(payload or [])


def _normalize_issue_row(row: dict[str, Any], *, candidate_project_slugs: set[str] | None = None, query_tokens: set[str] | None = None) -> dict[str, Any]:
    metadata = copy.deepcopy(row.get("metadata") or {})
    project = copy.deepcopy(row.get("project") or {})
    title = row.get("title") or metadata.get("value") or row.get("culprit") or row.get("shortId") or "Untitled issue"
    project_slug = str(project.get("slug") or project.get("name") or "")
    match_tokens = _normalize_match_tokens(" ".join([str(title), str(metadata.get("type") or ""), str(metadata.get("value") or ""), str(row.get("culprit") or "")]))
    text_score = len((query_tokens or set()) & match_tokens)
    project_score = 5 if candidate_project_slugs and project_slug in candidate_project_slugs else 0
    count_score = min(int(row.get("count") or 0), 1000) / 100.0
    return {
        "issue_id": str(row.get("id") or ""),
        "short_id": row.get("shortId"),
        "title": title,
        "culprit": row.get("culprit"),
        "level": row.get("level"),
        "status": row.get("status"),
        "substatus": row.get("substatus"),
        "count": int(row.get("count") or 0),
        "user_count": int(row.get("userCount") or 0),
        "first_seen": row.get("firstSeen"),
        "last_seen": row.get("lastSeen"),
        "project": project,
        "metadata": metadata,
        "permalink": row.get("permalink"),
        "is_bookmarked": bool(row.get("isBookmarked")),
        "is_subscribed": bool(row.get("isSubscribed")),
        "match_score": round(project_score + text_score + count_score, 2),
    }


def _fetch_project_issues(
    connection: dict[str, Any],
    token: str,
    project_slug: str,
    *,
    query_text: str,
    environment: str | None = None,
    stats_period: str = DEFAULT_STATS_PERIOD,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "limit": max(limit, 1),
        "statsPeriod": stats_period,
    }
    resolved_query = (query_text or "").strip()
    if resolved_query:
        params["query"] = resolved_query
    if environment:
        params["environment"] = environment
    payload = _request_json(
        connection,
        token,
        "GET",
        f"/api/0/projects/{connection['organization_slug']}/{urllib.parse.quote(project_slug, safe='')}/issues/",
        params=params,
    ) or []
    return copy.deepcopy(payload)


def _collect_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        urls.extend(SENTRY_URL_PATTERN.findall(value))
        return urls
    if isinstance(value, dict):
        for item in value.values():
            urls.extend(_collect_urls(item))
        return urls
    if isinstance(value, list):
        for item in value:
            urls.extend(_collect_urls(item))
    return urls


def _parse_sentry_reference(url: str) -> dict[str, Any] | None:
    parsed = urllib.parse.urlparse(url)
    match = re.search(r"/organizations/([^/]+)/issues/([^/?#]+)(?:/events/([^/?#]+))?", parsed.path)
    if not match:
        return None
    return {
        "url": url,
        "organization_slug": sanitize_identifier(match.group(1), "organization"),
        "issue_id": match.group(2),
        "event_id": match.group(3),
    }


def _external_issue_text(external_issue: dict[str, Any] | None) -> str:
    if not isinstance(external_issue, dict):
        return ""
    candidates = [
        external_issue.get("summary"),
        external_issue.get("title"),
        external_issue.get("description"),
        external_issue.get("body_markdown"),
        external_issue.get("preview"),
    ]
    if isinstance(external_issue.get("comments"), list):
        candidates.extend(item.get("text") or item.get("textPreview") for item in external_issue["comments"] if isinstance(item, dict))
    return "\n".join(str(item).strip() for item in candidates if str(item or "").strip())


def _normalize_issue_query_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:\n\r\t")
    return text


def _extract_issue_symptom(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    message_match = MESSAGE_LINE_PATTERN.search(text)
    if message_match:
        text = message_match.group(1).strip()
    for pattern in SYMPTOM_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    if message_match:
        return _normalize_issue_query_text(text)
    return ""


def _preferred_external_issue_query(external_issue: dict[str, Any] | None) -> str:
    if not isinstance(external_issue, dict):
        return ""
    rich_candidates = (
        external_issue.get("summary"),
        external_issue.get("title"),
        external_issue.get("description"),
        external_issue.get("body_markdown"),
        external_issue.get("preview"),
    )
    for candidate in rich_candidates:
        symptom = _extract_issue_symptom(candidate)
        if symptom:
            return symptom
    if isinstance(external_issue.get("comments"), list):
        for item in external_issue["comments"]:
            if not isinstance(item, dict):
                continue
            symptom = _extract_issue_symptom(item.get("text") or item.get("textPreview"))
            if symptom:
                return symptom
    for candidate in rich_candidates:
        normalized = _normalize_issue_query_text(candidate)
        if normalized:
            return normalized
    if isinstance(external_issue.get("comments"), list):
        for item in external_issue["comments"]:
            if not isinstance(item, dict):
                continue
            candidate = _normalize_issue_query_text(item.get("text") or item.get("textPreview"))
            if candidate:
                return candidate
    return ""


def _candidate_surface_ids(query_text: str, external_issue: dict[str, Any] | None = None) -> list[str]:
    tokens = _normalize_match_tokens(" ".join([query_text or "", _external_issue_text(external_issue)]))
    matched: list[str] = []
    for surface_id, rule in SURFACE_RULES.items():
        if tokens & set(rule["tokens"]):
            matched.append(surface_id)
    if not matched:
        return list(SURFACE_RULES)
    ordered = []
    for preferred in ("mobile/android", "mobile/ios", "frontend/admin", "frontend/web", "backend/api", "backend/workers", "mobile/shared"):
        if preferred in matched:
            ordered.append(preferred)
    for surface_id in matched:
        if surface_id not in ordered:
            ordered.append(surface_id)
    return ordered


def _candidate_project_slugs(
    connection: dict[str, Any],
    topology: dict[str, Any],
    *,
    query_text: str,
    external_issue: dict[str, Any] | None = None,
    project_slugs: list[str] | None = None,
) -> list[str]:
    if project_slugs:
        return list(dict.fromkeys(sanitize_identifier(item, "project") for item in project_slugs if item))
    selected: list[str] = []
    for surface_id in _candidate_surface_ids(query_text, external_issue):
        for item in topology.get("surfaces") or []:
            if item.get("surface_id") != surface_id:
                continue
            selected.extend(item.get("project_slugs") or [])
    if connection.get("project_scope"):
        allowed = {sanitize_identifier(item, "project") for item in (connection.get("project_scope") or [])}
        selected = [item for item in selected if item in allowed]
    if selected:
        return list(dict.fromkeys(selected))
    catalog = _read_project_catalog(_sentry_paths(connection.get("workspace_path") or "."), connection["connection_id"])
    fallback = [item["slug"] for item in (catalog.get("items") or [])]
    if connection.get("project_scope"):
        allowed = {sanitize_identifier(item, "project") for item in (connection.get("project_scope") or [])}
        fallback = [item for item in fallback if item in allowed]
    return list(dict.fromkeys(fallback))


def _read_task_external_issue(workspace: str | Path, task_id: str | None) -> dict[str, Any] | None:
    if task_id:
        task = read_task(workspace, task_id)
        return copy.deepcopy(task.get("external_issue") or {})
    task = current_task(workspace)
    if not task:
        return None
    return copy.deepcopy(task.get("external_issue") or {})


def _event_id(item: dict[str, Any]) -> str | None:
    for key in ("id", "eventID", "eventId"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _collect_issue_packet(
    paths: dict[str, Any],
    connection: dict[str, Any],
    token: str,
    issue_id: str,
    *,
    stats_period: str = DEFAULT_STATS_PERIOD,
    event_sample_limit: int = DEFAULT_EVENT_SAMPLE_LIMIT,
    preferred_event_id: str | None = None,
) -> dict[str, Any]:
    organization_slug = connection["organization_slug"]
    issue = _request_json(connection, token, "GET", f"/api/0/organizations/{organization_slug}/issues/{urllib.parse.quote(issue_id, safe='')}/")
    latest_event = _safe_request_json(
        connection,
        token,
        "GET",
        f"/api/0/organizations/{organization_slug}/issues/{urllib.parse.quote(issue_id, safe='')}/events/latest/",
    )
    recommended_event = _safe_request_json(
        connection,
        token,
        "GET",
        f"/api/0/organizations/{organization_slug}/issues/{urllib.parse.quote(issue_id, safe='')}/events/recommended/",
    )
    recent_event_rows = _safe_request_json(
        connection,
        token,
        "GET",
        f"/api/0/organizations/{organization_slug}/issues/{urllib.parse.quote(issue_id, safe='')}/events/",
        params={"statsPeriod": stats_period},
    ) or []
    detailed_events: list[dict[str, Any]] = []
    ordered_event_ids: list[str] = []
    if preferred_event_id:
        ordered_event_ids.append(preferred_event_id)
    for event_payload in (recommended_event, latest_event):
        event_id = _event_id(event_payload or {})
        if event_id:
            ordered_event_ids.append(event_id)
    for row in recent_event_rows:
        event_id = _event_id(row or {})
        if event_id:
            ordered_event_ids.append(event_id)
    deduped_event_ids = list(dict.fromkeys(item for item in ordered_event_ids if item))[: max(event_sample_limit, 1) + 2]
    for event_id in deduped_event_ids:
        event_payload = _safe_request_json(
            connection,
            token,
            "GET",
            f"/api/0/organizations/{organization_slug}/issues/{urllib.parse.quote(issue_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}/",
        )
        if event_payload:
            detailed_events.append(event_payload)
    tag_breakdowns = {
        key: _issue_tag_values(connection, token, issue_id, key)
        for key in TAG_KEYS_OF_INTEREST
    }
    representative_event = preferred_event_id and next((item for item in detailed_events if _event_id(item) == preferred_event_id), None)
    if not representative_event:
        representative_event = recommended_event or latest_event or (detailed_events[0] if detailed_events else None)
    packet = {
        "schema_version": SENTRY_ISSUE_SNAPSHOT_SCHEMA_VERSION,
        "connection_id": connection["connection_id"],
        "organization_slug": organization_slug,
        "issue_id": str(issue.get("id") or issue_id),
        "issue": copy.deepcopy(issue),
        "representative_event": _event_context_summary(representative_event or {}),
        "recommended_event": _event_context_summary(recommended_event or {}),
        "latest_event": _event_context_summary(latest_event or {}),
        "recent_events": [_event_context_summary(item) for item in detailed_events[: max(event_sample_limit, 1)]],
        "recent_event_rows": copy.deepcopy(recent_event_rows[: max(event_sample_limit, 1)]),
        "tag_breakdowns": tag_breakdowns,
        "collected_at": now_iso(),
        "stats_period": stats_period,
    }
    _write_json(_issue_snapshot_path(paths, connection["connection_id"], str(issue.get("id") or issue_id)), packet)
    return packet


def list_sentry_connections(workspace: str | Path) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    index = _load_connections_index(paths)
    default_connection_id = sanitize_identifier(index.get("default_connection_id"), "connection") if index.get("default_connection_id") else None
    items = []
    for item in index.get("items", []):
        record = _maybe_read_connection_record(paths, item.get("connection_id"))
        if not record:
            continue
        record["default"] = record.get("connection_id") == default_connection_id
        items.append(_redact_connection(record, include_paths=True, paths=paths))
    items.sort(key=lambda item: (not bool(item.get("default")), item.get("label") or item.get("connection_id")))
    return {
        "workspace_path": paths["workspace_path"],
        "default_connection_id": default_connection_id,
        "items": items,
        "counts": {
            "total": len(items),
            "connected": sum(1 for item in items if item.get("status") == "connected"),
            "configured": sum(1 for item in items if item.get("status") in {"configured", "connected"}),
        },
    }


def connect_sentry(
    workspace: str | Path,
    *,
    base_url: str,
    token: str,
    label: str | None = None,
    connection_id: str | None = None,
    organization_slug: str | None = None,
    project_scope: str | list[str] | None = None,
    default: bool = False,
    test_connection: bool = True,
) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    normalized_base_url = _normalize_base_url(base_url)
    resolved_org = _discover_organization_slug(normalized_base_url, token, organization_slug)
    resolved_connection_id = sanitize_identifier(connection_id or label or resolved_org or "sentry", "connection")
    record = _default_connection_record(
        resolved_connection_id,
        label or resolved_connection_id,
        normalized_base_url,
        resolved_org,
        _normalize_project_scope(project_scope),
    )
    if test_connection:
        project_catalog = _project_catalog_from_connection(record, token)
        record["status"] = "connected"
        record["last_tested_at"] = now_iso()
        record["last_error"] = None
    else:
        project_catalog = {
            "schema_version": SENTRY_PROJECT_CATALOG_SCHEMA_VERSION,
            "connection_id": resolved_connection_id,
            "organization_slug": resolved_org,
            "items": [],
            "updated_at": None,
        }
    _write_secret(_secret_path(paths, resolved_connection_id), {"token": token})
    if record.get("default") or default:
        record["default"] = True
    written = _write_connection_record(paths, record)
    _write_project_catalog(paths, resolved_connection_id, project_catalog)
    topology = _write_topology(paths, resolved_connection_id, _auto_discover_topology(workspace, written, project_catalog))
    index_record = {
        "connection_id": written["connection_id"],
        "label": written["label"],
        "base_url": written["base_url"],
        "organization_slug": written["organization_slug"],
        "default": bool(default),
        "status": written["status"],
        "last_tested_at": written.get("last_tested_at"),
        "updated_at": written.get("updated_at"),
    }
    index = _upsert_connection_index_item(paths, index_record)
    if default or not index.get("default_connection_id"):
        index = _set_default_flag(paths, written["connection_id"])
        written = _read_connection_record(paths, written["connection_id"])
    else:
        _persist_default_connection(paths, index.get("default_connection_id"))
    return {
        "workspace_path": paths["workspace_path"],
        "created_connection_id": written["connection_id"],
        "connection": _redact_connection(written, include_paths=True, paths=paths),
        "project_catalog": project_catalog,
        "topology": topology,
    }


def update_sentry_connection(
    workspace: str | Path,
    connection_id: str,
    *,
    base_url: str | None = None,
    token: str | None = None,
    label: str | None = None,
    organization_slug: str | None = None,
    project_scope: str | list[str] | None = None,
    default: bool | None = None,
    test_connection: bool = True,
) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    existing = _read_connection_record(paths, connection_id)
    current_secret = _connection_secret(paths, existing["connection_id"])
    resolved_base_url = _normalize_base_url(base_url or existing["base_url"])
    resolved_token = str(token or current_secret["token"]).strip()
    resolved_org = _discover_organization_slug(resolved_base_url, resolved_token, organization_slug or existing["organization_slug"])
    record = {
        **existing,
        "label": label or existing["label"],
        "base_url": resolved_base_url,
        "organization_slug": resolved_org,
        "project_scope": _normalize_project_scope(project_scope) if project_scope is not None else existing.get("project_scope") or [],
        "default": existing.get("default") if default is None else bool(default),
    }
    if test_connection:
        project_catalog = _project_catalog_from_connection(record, resolved_token)
        record["status"] = "connected"
        record["last_tested_at"] = now_iso()
        record["last_error"] = None
    else:
        project_catalog = _read_project_catalog(paths, existing["connection_id"]) or {
            "schema_version": SENTRY_PROJECT_CATALOG_SCHEMA_VERSION,
            "connection_id": existing["connection_id"],
            "organization_slug": resolved_org,
            "items": [],
            "updated_at": None,
        }
    _write_secret(_secret_path(paths, existing["connection_id"]), {"token": resolved_token})
    written = _write_connection_record(paths, record)
    _write_project_catalog(paths, written["connection_id"], project_catalog)
    topology = _write_topology(paths, written["connection_id"], _auto_discover_topology(workspace, written, project_catalog))
    index = _upsert_connection_index_item(
        paths,
        {
            "connection_id": written["connection_id"],
            "label": written["label"],
            "base_url": written["base_url"],
            "organization_slug": written["organization_slug"],
            "default": written["default"],
            "status": written["status"],
            "last_tested_at": written.get("last_tested_at"),
            "updated_at": written.get("updated_at"),
        },
    )
    if written.get("default"):
        index = _set_default_flag(paths, written["connection_id"])
        written = _read_connection_record(paths, written["connection_id"])
    elif index.get("default_connection_id") == written["connection_id"]:
        fallback_default_id = index.get("items", [{}])[0].get("connection_id") if index.get("items") else None
        if fallback_default_id == written["connection_id"]:
            remaining = [item.get("connection_id") for item in index.get("items", []) if item.get("connection_id") != written["connection_id"]]
            fallback_default_id = remaining[0] if remaining else None
        index = _set_default_flag(paths, fallback_default_id)
        written = _read_connection_record(paths, written["connection_id"])
    else:
        _persist_default_connection(paths, index.get("default_connection_id"))
    return {
        "workspace_path": paths["workspace_path"],
        "updated_connection_id": written["connection_id"],
        "connection": _redact_connection(written, include_paths=True, paths=paths),
        "project_catalog": project_catalog,
        "topology": topology,
    }


def remove_sentry_connection(workspace: str | Path, connection_id: str) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    resolved_connection_id = sanitize_identifier(connection_id, "connection")
    existing = _read_connection_record(paths, resolved_connection_id)
    for path in (
        _connection_record_path(paths, resolved_connection_id),
        _secret_path(paths, resolved_connection_id),
        _project_catalog_path(paths, resolved_connection_id),
        _topology_path(paths, resolved_connection_id),
    ):
        if path.exists():
            path.unlink()
    for search_path in Path(paths["searches_dir"]).glob("*.json"):
        if search_path.name == "current.json":
            continue
        payload = _load_json(search_path, default={}, strict=False) or {}
        if payload.get("connection_id") == resolved_connection_id:
            search_path.unlink()
    index = _remove_connection_index_item(paths, resolved_connection_id)
    if index.get("default_connection_id"):
        _set_default_flag(paths, index["default_connection_id"])
    else:
        _persist_default_connection(paths, None)
    return {
        "workspace_path": paths["workspace_path"],
        "removed_connection_id": resolved_connection_id,
        "connection": _redact_connection(existing, include_paths=True, paths=paths),
    }


def test_sentry_connection(workspace: str | Path, connection_id: str) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    connection = _read_connection_record(paths, connection_id)
    secret = _connection_secret(paths, connection["connection_id"])
    project_catalog = _project_catalog_from_connection(connection, secret["token"])
    connection["status"] = "connected"
    connection["last_tested_at"] = now_iso()
    connection["last_error"] = None
    written = _write_connection_record(paths, connection)
    _write_project_catalog(paths, written["connection_id"], project_catalog)
    topology = _write_topology(paths, written["connection_id"], _auto_discover_topology(workspace, written, project_catalog))
    index = _upsert_connection_index_item(
        paths,
        {
            "connection_id": written["connection_id"],
            "label": written["label"],
            "base_url": written["base_url"],
            "organization_slug": written["organization_slug"],
            "default": written.get("default", False),
            "status": written["status"],
            "last_tested_at": written.get("last_tested_at"),
            "updated_at": written.get("updated_at"),
        },
    )
    if not index.get("default_connection_id"):
        index = _set_default_flag(paths, written["connection_id"])
        written = _read_connection_record(paths, written["connection_id"])
    else:
        _persist_default_connection(paths, index.get("default_connection_id"))
    return {
        "workspace_path": paths["workspace_path"],
        "connection": _redact_connection(written, include_paths=True, paths=paths),
        "project_catalog": project_catalog,
        "topology": topology,
    }


def search_sentry_issues(
    workspace: str | Path,
    *,
    query_text: str,
    connection_id: str | None = None,
    external_issue: dict[str, Any] | None = None,
    task_id: str | None = None,
    project_slugs: list[str] | None = None,
    environment: str | None = None,
    stats_period: str = DEFAULT_STATS_PERIOD,
    limit: int = DEFAULT_SEARCH_LIMIT,
    shortlist_size: int = DEFAULT_SHORTLIST_SIZE,
) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    task_external_issue = external_issue or _read_task_external_issue(workspace, task_id)
    resolved_connection_id = _resolve_connection_id(paths, connection_id)
    connection = _read_connection_record(paths, resolved_connection_id)
    connection["workspace_path"] = paths["workspace_path"]
    secret = _connection_secret(paths, resolved_connection_id)
    project_catalog, topology = _ensure_topology(paths, connection, secret["token"])
    candidate_project_slugs = _candidate_project_slugs(
        connection,
        topology,
        query_text=query_text,
        external_issue=task_external_issue,
        project_slugs=project_slugs,
    )
    if not candidate_project_slugs:
        candidate_project_slugs = [item["slug"] for item in (project_catalog.get("items") or [])]
    query_tokens = _normalize_match_tokens(query_text)
    ranked: list[dict[str, Any]] = []
    seen_issue_ids: set[str] = set()
    for project_slug in candidate_project_slugs:
        rows = _fetch_project_issues(
            connection,
            secret["token"],
            project_slug,
            query_text=query_text,
            environment=environment,
            stats_period=stats_period,
            limit=limit,
        )
        for row in rows:
            normalized = _normalize_issue_row(
                row,
                candidate_project_slugs=set(candidate_project_slugs),
                query_tokens=query_tokens,
            )
            issue_id = normalized["issue_id"]
            if issue_id in seen_issue_ids:
                continue
            seen_issue_ids.add(issue_id)
            ranked.append(normalized)
    ranked.sort(key=lambda item: (-float(item.get("match_score") or 0), -(item.get("count") or 0), item.get("title") or ""))
    shortlist = ranked[: max(shortlist_size, 1)]
    session_id = _new_entity_id("sentry-search")
    session = {
        "schema_version": SENTRY_SEARCH_SCHEMA_VERSION,
        "session_id": session_id,
        "connection_id": resolved_connection_id,
        "organization_slug": connection["organization_slug"],
        "raw_query": query_text,
        "candidate_project_slugs": candidate_project_slugs,
        "environment": environment,
        "stats_period": stats_period,
        "result_count": len(ranked),
        "shortlist": shortlist,
        "items": ranked[: max(limit, 1)],
        "topology_summary": {
            "mapped_surface_count": len([item for item in (topology.get("surfaces") or []) if item.get("project_slugs")]),
            "mapped_project_count": len(topology.get("mapped_project_slugs") or []),
        },
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    _write_json(_search_session_path(paths, session_id), session)
    _save_current_pointer(Path(paths["current_search"]), "session_id", session_id)
    return {
        "workspace_path": paths["workspace_path"],
        "connection": _redact_connection(connection, include_paths=True, paths=paths),
        "topology": topology,
        "search_session": session,
        "project_catalog": project_catalog,
    }


def show_sentry_issue_queue(workspace: str | Path, *, search_session_id: str | None = None) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    if not search_session_id:
        current = _load_json(Path(paths["current_search"]), default={}, strict=False) or {}
        search_session_id = current.get("session_id")
    if not search_session_id:
        raise FileNotFoundError("No Sentry search session is available for this workspace.")
    session = _load_json(
        _search_session_path(paths, search_session_id),
        default={},
        strict=True,
        purpose=f"Sentry search session `{search_session_id}`",
    ) or {}
    connection = _read_connection_record(paths, session["connection_id"])
    topology = _read_topology(paths, session["connection_id"])
    return {
        "workspace_path": paths["workspace_path"],
        "connection": _redact_connection(connection, include_paths=True, paths=paths),
        "topology": topology,
        "search_session": session,
    }


def collect_sentry_context(
    workspace: str | Path,
    *,
    query_text: str | None = None,
    connection_id: str | None = None,
    external_issue: dict[str, Any] | None = None,
    task_id: str | None = None,
    project_slugs: list[str] | None = None,
    environment: str | None = None,
    stats_period: str = DEFAULT_STATS_PERIOD,
    limit: int = DEFAULT_SEARCH_LIMIT,
    shortlist_size: int = DEFAULT_SHORTLIST_SIZE,
) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    resolved_external_issue = external_issue or _read_task_external_issue(workspace, task_id)
    direct_refs = [_parse_sentry_reference(url) for url in _collect_urls(resolved_external_issue)]
    direct_refs = [item for item in direct_refs if item]
    resolved_connection_id = _resolve_connection_id(paths, connection_id)
    connection = _read_connection_record(paths, resolved_connection_id)
    connection["workspace_path"] = paths["workspace_path"]
    secret = _connection_secret(paths, resolved_connection_id)
    project_catalog, topology = _ensure_topology(paths, connection, secret["token"])
    selected_packet: dict[str, Any] | None = None
    selected_issue_id: str | None = None
    search_payload: dict[str, Any] | None = None
    if direct_refs:
        for ref in direct_refs:
            if ref["organization_slug"] != connection["organization_slug"]:
                continue
            try:
                selected_issue_id = str(ref["issue_id"])
                selected_packet = _collect_issue_packet(
                    paths,
                    connection,
                    secret["token"],
                    selected_issue_id,
                    stats_period=stats_period,
                    preferred_event_id=ref.get("event_id"),
                )
                break
            except Exception:
                selected_issue_id = None
                selected_packet = None
                continue
    if not selected_packet:
        effective_query = (
            (query_text or "").strip()
            or _preferred_external_issue_query(resolved_external_issue)
            or _external_issue_text(resolved_external_issue)
        )
        if not effective_query:
            raise ValueError("Sentry context collection needs a query_text, a linked task issue, or a direct Sentry URL in the external issue payload.")
        search_payload = search_sentry_issues(
            workspace,
            query_text=effective_query,
            connection_id=resolved_connection_id,
            external_issue=resolved_external_issue,
            task_id=task_id,
            project_slugs=project_slugs,
            environment=environment,
            stats_period=stats_period,
            limit=limit,
            shortlist_size=shortlist_size,
        )
        shortlist = (search_payload.get("search_session") or {}).get("shortlist") or []
        if not shortlist:
            return {
                "workspace_path": paths["workspace_path"],
                "connection": _redact_connection(connection, include_paths=True, paths=paths),
                "topology": topology,
                "search_session": search_payload.get("search_session"),
                "match_status": "no_matches",
                "diagnostic_packet": None,
            }
        selected_issue_id = shortlist[0]["issue_id"]
        selected_packet = _collect_issue_packet(
            paths,
            connection,
            secret["token"],
            selected_issue_id,
            stats_period=stats_period,
        )
    return {
        "workspace_path": paths["workspace_path"],
        "connection": _redact_connection(connection, include_paths=True, paths=paths),
        "topology": topology,
        "project_catalog": project_catalog,
        "search_session": (search_payload or {}).get("search_session"),
        "matched_issue_id": selected_issue_id,
        "match_status": "matched" if selected_packet else "no_matches",
        "diagnostic_packet": selected_packet,
    }


def workspace_sentry_summary(workspace: str | Path) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    connections = list_sentry_connections(workspace)
    current_search = _load_json(Path(paths["current_search"]), default={}, strict=False) or {}
    current_connection_id = _resolve_connection_id(paths, None) if connections.get("items") else None
    topology = _read_topology(paths, current_connection_id) if current_connection_id else {}
    search_session = (
        _load_json(_search_session_path(paths, current_search.get("session_id")), default={}, strict=False)
        if current_search.get("session_id")
        else {}
    ) or {}
    return {
        "connection_count": len(connections.get("items") or []),
        "default_connection_id": connections.get("default_connection_id"),
        "current_connection_id": current_connection_id,
        "mapped_surface_count": len([item for item in (topology.get("surfaces") or []) if item.get("project_slugs")]),
        "mapped_project_count": len(topology.get("mapped_project_slugs") or []),
        "unmapped_project_count": len(topology.get("unmapped_projects") or []),
        "last_search_session_id": search_session.get("session_id"),
        "last_search_result_count": search_session.get("result_count"),
    }


def workspace_sentry_detail(workspace: str | Path) -> dict[str, Any]:
    paths = _sentry_paths(workspace)
    _ensure_sentry_dirs(paths)
    connections = list_sentry_connections(workspace)
    current_search = _load_json(Path(paths["current_search"]), default={}, strict=False) or {}
    current_connection_id = _resolve_connection_id(paths, None) if connections.get("items") else None
    current_topology = _read_topology(paths, current_connection_id) if current_connection_id else {}
    current_project_catalog = _read_project_catalog(paths, current_connection_id) if current_connection_id else {}
    current_search_session = (
        _load_json(_search_session_path(paths, current_search.get("session_id")), default={}, strict=False)
        if current_search.get("session_id")
        else {}
    ) or None
    return {
        "connections": connections,
        "current_connection_id": current_connection_id,
        "current_project_catalog": current_project_catalog,
        "current_topology": current_topology,
        "current_search_session": current_search_session,
    }
