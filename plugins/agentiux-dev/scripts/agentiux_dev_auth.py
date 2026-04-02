#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentiux_dev_lib import (
    _ensure_workspace_initialized,
    _load_json,
    _write_json,
    now_iso,
    plugin_root,
    python_launcher_tokens,
    sanitize_identifier,
)


AUTH_SCHEMA_VERSION = 2
AUTH_SCOPE_TYPES = {"workspace", "task", "external_issue"}
AUTH_ARTIFACT_TYPES = {"credentials", "url", "headers", "cookies", "storage_state", "token_bundle"}
AUTH_REQUEST_MODES = {"read_only", "mutating"}
AUTH_RESOLUTION_REASONS = {"initial", "refresh", "reuse", "manual_seed"}
AUTH_SESSION_SOURCE_KINDS = {"manual", "resolver"}
AUTH_SESSION_STATUS_VALUES = {"active", "expired", "invalidated"}
SESSION_EXPIRING_WINDOW = timedelta(hours=24)
CACHEABLE_ARTIFACT_TYPES = {"credentials", "headers", "cookies", "storage_state", "token_bundle"}
_UNSET = object()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_iso8601(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _max_request_mode(mode_a: str, mode_b: str) -> str:
    if "mutating" in {mode_a, mode_b}:
        return "mutating"
    return "read_only"


def _normalize_string_list(values: Any) -> list[str]:
    items = values if isinstance(values, list) else ([values] if values is not None else [])
    normalized: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_session_binding(value: Any) -> dict[str, Any] | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, str):
        refs = _normalize_string_list([value])
        return {"primary_ref": refs[0] if refs else None, "refs": refs, "label": None} if refs else None
    if isinstance(value, list):
        refs = _normalize_string_list(value)
        return {"primary_ref": refs[0] if refs else None, "refs": refs, "label": None} if refs else None
    if not isinstance(value, dict):
        raise ValueError("session_binding must be a string, array, object, or null.")
    refs = _normalize_string_list(value.get("refs"))
    primary_ref = str(value.get("primary_ref") or "").strip() or None
    if primary_ref and primary_ref not in refs:
        refs = [primary_ref, *refs]
    label_value = value.get("label")
    label = str(label_value).strip() if label_value not in (None, "") else None
    if not refs and not label:
        return None
    return {
        "primary_ref": primary_ref or (refs[0] if refs else None),
        "refs": refs,
        "label": label,
    }


def _session_binding_refs(value: Any) -> set[str]:
    binding = _normalize_session_binding(value)
    if not binding:
        return set()
    return set(binding.get("refs") or [])


def _session_binding_matches(session_binding: Any, requested_binding: Any) -> bool:
    session_refs = _session_binding_refs(session_binding)
    requested_refs = _session_binding_refs(requested_binding)
    if not session_refs and not requested_refs:
        return True
    if not session_refs or not requested_refs:
        return False
    return bool(session_refs & requested_refs)


def _normalize_request_mode(value: Any, *, default: str = "read_only") -> str:
    candidate = str(value or default).strip().lower()
    if candidate not in AUTH_REQUEST_MODES:
        raise ValueError(f"Unsupported auth request_mode: {candidate}")
    return candidate


def _normalize_resolution_reason(value: Any, *, default: str = "initial") -> str:
    candidate = str(value or default).strip().lower()
    if candidate not in AUTH_RESOLUTION_REASONS:
        raise ValueError(f"Unsupported auth resolution_reason: {candidate}")
    return candidate


def _request_mode_allows(granted_mode: str, requested_mode: str) -> bool:
    granted = _normalize_request_mode(granted_mode)
    requested = _normalize_request_mode(requested_mode)
    if requested == "read_only":
        return granted in {"read_only", "mutating"}
    return granted == "mutating"


def _allowed_modes_allow(allowed_modes: list[str], requested_mode: str) -> bool:
    modes = [_normalize_request_mode(mode) for mode in allowed_modes] if allowed_modes else ["read_only"]
    return any(_request_mode_allows(mode, requested_mode) for mode in modes)


def _tags_allowed(allowed_tags: list[str], requested_tags: list[str]) -> bool:
    return set(requested_tags).issubset(set(allowed_tags))


def _auth_paths(workspace: str | Path) -> dict[str, Any]:
    base_paths = _ensure_workspace_initialized(workspace)
    auth_root = Path(base_paths["auth_root"])
    sessions_dir = Path(base_paths.get("auth_sessions_dir") or (auth_root / "sessions"))
    session_secrets_dir = Path(base_paths.get("auth_session_secrets_dir") or (auth_root / "session-secrets"))
    session_revisions_dir = Path(base_paths.get("auth_session_revisions_dir") or (auth_root / "session-revisions"))
    sessions_index = Path(base_paths.get("auth_sessions_index") or (sessions_dir / "index.json"))
    return {
        **base_paths,
        "root": auth_root,
        "profiles_dir": Path(base_paths["auth_profiles_dir"]),
        "secrets_dir": Path(base_paths["auth_secrets_dir"]),
        "index_path": Path(base_paths["auth_index"]),
        "sessions_dir": sessions_dir,
        "session_secrets_dir": session_secrets_dir,
        "session_revisions_dir": session_revisions_dir,
        "sessions_index_path": sessions_index,
    }


def _ensure_auth_dirs(paths: dict[str, Any]) -> None:
    for key in ("root", "profiles_dir", "secrets_dir", "sessions_dir", "session_secrets_dir", "session_revisions_dir"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(Path(paths["secrets_dir"]), 0o700)
    except OSError:
        pass
    try:
        os.chmod(Path(paths["session_secrets_dir"]), 0o700)
    except OSError:
        pass


def _default_auth_index() -> dict[str, Any]:
    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "items": [],
        "updated_at": now_iso(),
    }


def _default_auth_sessions_index() -> dict[str, Any]:
    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "items": [],
        "updated_at": now_iso(),
    }


def _load_auth_index(paths: dict[str, Any]) -> dict[str, Any]:
    payload = _load_json(Path(paths["index_path"]), default=_default_auth_index(), strict=False) or _default_auth_index()
    payload["items"] = copy.deepcopy(payload.get("items") or [])
    return payload


def _save_auth_index(paths: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    payload["schema_version"] = AUTH_SCHEMA_VERSION
    payload["updated_at"] = now_iso()
    _write_json(Path(paths["index_path"]), payload)
    return payload


def _load_sessions_index(paths: dict[str, Any]) -> dict[str, Any]:
    payload = _load_json(Path(paths["sessions_index_path"]), default=_default_auth_sessions_index(), strict=False) or _default_auth_sessions_index()
    payload["items"] = copy.deepcopy(payload.get("items") or [])
    return payload


def _save_sessions_index(paths: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    payload["schema_version"] = AUTH_SCHEMA_VERSION
    payload["updated_at"] = now_iso()
    _write_json(Path(paths["sessions_index_path"]), payload)
    return payload


def _profile_record_path(paths: dict[str, Any], profile_id: str) -> Path:
    return Path(paths["profiles_dir"]) / f"{sanitize_identifier(profile_id, 'profile')}.json"


def _secret_path(paths: dict[str, Any], profile_id: str) -> Path:
    return Path(paths["secrets_dir"]) / f"{sanitize_identifier(profile_id, 'profile')}.json"


def _session_record_path(paths: dict[str, Any], session_id: str) -> Path:
    return Path(paths["sessions_dir"]) / f"{sanitize_identifier(session_id, 'session')}.json"


def _session_secret_path(paths: dict[str, Any], session_id: str) -> Path:
    return Path(paths["session_secrets_dir"]) / f"{sanitize_identifier(session_id, 'session')}.json"


def _session_revision_dir(paths: dict[str, Any], session_id: str) -> Path:
    return Path(paths["session_revisions_dir"]) / sanitize_identifier(session_id, "session")


def _session_revision_path(paths: dict[str, Any], session_id: str, revision_id: str) -> Path:
    return _session_revision_dir(paths, session_id) / f"{sanitize_identifier(revision_id, 'revision')}.json"


def _next_session_id(profile_id: str) -> str:
    return sanitize_identifier(f"{profile_id}-{int(time.time() * 1000)}", "session")


def _next_revision_id() -> str:
    return sanitize_identifier(f"revision-{int(time.time() * 1000)}", "revision")


def _default_static_resolver() -> dict[str, Any]:
    script_path = plugin_root() / "scripts" / "agentiux_dev_auth.py"
    return {
        "kind": "command_v1",
        "argv": [*python_launcher_tokens(), str(script_path), "emit-static-artifact"],
        "cwd": ".",
        "env": {},
        "timeout_seconds": 10,
    }


def _normalize_scope_ref(scope_type: str, scope_ref: Any) -> str | None:
    if scope_type == "workspace":
        return None
    text = str(scope_ref or "").strip()
    if not text:
        raise ValueError(f"scope_ref is required for auth scope `{scope_type}`.")
    return text


def _normalize_usage_policy(payload: Any, *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = copy.deepcopy(existing or {})
    policy = copy.deepcopy(payload or existing or {})
    default_request_mode = _normalize_request_mode(
        policy.get("default_request_mode", existing.get("default_request_mode") or "read_only")
    )
    allowed_request_modes = _normalize_string_list(
        policy.get("allowed_request_modes", existing.get("allowed_request_modes") or [default_request_mode])
    )
    if not allowed_request_modes:
        allowed_request_modes = [default_request_mode]
    normalized_modes = []
    for item in allowed_request_modes:
        mode = _normalize_request_mode(item)
        if mode not in normalized_modes:
            normalized_modes.append(mode)
    if default_request_mode not in normalized_modes:
        normalized_modes.append(default_request_mode)
    notes_value = policy.get("notes", existing.get("notes"))
    notes = str(notes_value).strip() if notes_value not in (None, "") else None
    return {
        "default_request_mode": default_request_mode,
        "allowed_request_modes": normalized_modes,
        "allowed_surface_modes": _normalize_string_list(policy.get("allowed_surface_modes", existing.get("allowed_surface_modes"))),
        "action_tags": _normalize_string_list(policy.get("action_tags", existing.get("action_tags"))),
        "allow_session_persistence": bool(policy.get("allow_session_persistence", existing.get("allow_session_persistence", True))),
        "allow_session_refresh": bool(policy.get("allow_session_refresh", existing.get("allow_session_refresh", True))),
        "notes": notes,
    }


def _normalize_resolver(resolver: Any, *, secret_payload: dict[str, Any]) -> dict[str, Any]:
    if resolver is None:
        if _coerce_static_artifact(secret_payload) is None:
            raise ValueError("resolver is required unless secret_payload can be coerced into a static auth artifact.")
        resolver = _default_static_resolver()
    payload = copy.deepcopy(resolver or {})
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in {"command_v1", "command_v2"}:
        raise ValueError("resolver.kind must be `command_v1` or `command_v2`.")
    argv = payload.get("argv")
    if not isinstance(argv, list) or not argv:
        raise ValueError(f"resolver.argv is required for {kind} auth resolvers.")
    env = payload.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError("resolver.env must be an object when provided.")
    return {
        "kind": kind,
        "argv": [str(item) for item in argv],
        "cwd": str(payload.get("cwd") or "."),
        "env": {str(key): str(value) for key, value in env.items()},
        "timeout_seconds": max(1, int(payload.get("timeout_seconds") or 30)),
    }


def _normalize_artifact_policy(payload: Any) -> dict[str, Any]:
    policy = copy.deepcopy(payload or {})
    return {
        "delete_after_use": bool(policy.get("delete_after_use", True)),
        "persist_redacted_summary": bool(policy.get("persist_redacted_summary", True)),
        "max_ttl_seconds": int(policy["max_ttl_seconds"]) if policy.get("max_ttl_seconds") is not None else None,
    }


def _normalize_profile_metadata(
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    secret_payload: dict[str, Any],
) -> dict[str, Any]:
    existing = copy.deepcopy(existing or {})
    profile_id = sanitize_identifier(payload.get("profile_id") or existing.get("profile_id") or payload.get("label"), "profile")
    label = str(payload.get("label") or existing.get("label") or profile_id).strip() or profile_id
    scope_type = str(payload.get("scope_type") or existing.get("scope_type") or "workspace").strip().lower()
    if scope_type not in AUTH_SCOPE_TYPES:
        raise ValueError(f"Unsupported auth scope_type: {scope_type}")
    scope_ref = _normalize_scope_ref(scope_type, payload.get("scope_ref", existing.get("scope_ref")))
    is_default = bool(payload.get("is_default", existing.get("is_default", False)))
    if scope_type != "workspace" and is_default:
        raise ValueError("Only workspace-scoped auth profiles can be marked as default.")
    notes_value = payload.get("notes", existing.get("notes"))
    notes = str(notes_value).strip() if notes_value not in (None, "") else None
    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "profile_id": profile_id,
        "label": label,
        "scope_type": scope_type,
        "scope_ref": scope_ref,
        "is_default": is_default,
        "resolver": _normalize_resolver(payload.get("resolver", existing.get("resolver")), secret_payload=secret_payload),
        "artifact_policy": _normalize_artifact_policy(payload.get("artifact_policy", existing.get("artifact_policy"))),
        "usage_policy": _normalize_usage_policy(payload.get("usage_policy"), existing=existing.get("usage_policy")),
        "notes": notes,
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }


def _coerce_static_artifact(secret_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    payload = copy.deepcopy(secret_payload or {})
    if not payload:
        return None
    if payload.get("artifact_type"):
        return payload
    lowered_keys = {str(key).lower(): value for key, value in payload.items()}
    if "access_token" in lowered_keys or "refresh_token" in lowered_keys:
        token_payload = {
            "access_token": lowered_keys.get("access_token"),
            "refresh_token": lowered_keys.get("refresh_token"),
            "token_type": lowered_keys.get("token_type"),
            "access_expires_at": lowered_keys.get("access_expires_at") or payload.get("expires_at"),
            "refresh_expires_at": lowered_keys.get("refresh_expires_at"),
            "base_url": lowered_keys.get("base_url"),
            "subject_ref": lowered_keys.get("subject_ref"),
            "headers": copy.deepcopy(lowered_keys.get("headers")) if isinstance(lowered_keys.get("headers"), dict) else None,
        }
        token_payload = {key: value for key, value in token_payload.items() if value is not None}
        return {
            "artifact_type": "token_bundle",
            "expires_at": token_payload.get("access_expires_at"),
            "payload": token_payload,
        }
    if ("login" in lowered_keys or "username" in lowered_keys or "email" in lowered_keys) and "password" in lowered_keys:
        return {
            "artifact_type": "credentials",
            "payload": copy.deepcopy(payload),
        }
    if "url" in lowered_keys:
        return {
            "artifact_type": "url",
            "payload": {"url": lowered_keys["url"]},
        }
    if "headers" in lowered_keys and isinstance(lowered_keys["headers"], dict):
        return {
            "artifact_type": "headers",
            "payload": copy.deepcopy(lowered_keys["headers"]),
        }
    if "cookies" in lowered_keys and isinstance(lowered_keys["cookies"], list):
        return {
            "artifact_type": "cookies",
            "payload": copy.deepcopy(lowered_keys["cookies"]),
        }
    if "storage_state" in lowered_keys:
        return {
            "artifact_type": "storage_state",
            "payload": copy.deepcopy(lowered_keys["storage_state"]),
        }
    return None


def _coerce_session_artifact(payload: Any, *, explicit_artifact_type: str | None = None) -> dict[str, Any]:
    candidate = copy.deepcopy(payload or {})
    if explicit_artifact_type:
        if isinstance(candidate, dict) and "payload" in candidate and candidate.get("artifact_type") == explicit_artifact_type:
            return _normalize_resolved_auth_artifact(candidate)
        return _normalize_resolved_auth_artifact(
            {
                "artifact_type": explicit_artifact_type,
                "expires_at": candidate.get("expires_at") if isinstance(candidate, dict) else None,
                "payload": candidate.get("payload") if isinstance(candidate, dict) and "payload" in candidate else candidate,
                "summary": candidate.get("summary") if isinstance(candidate, dict) else None,
            }
        )
    if isinstance(candidate, dict) and candidate.get("artifact_type"):
        return _normalize_resolved_auth_artifact(candidate)
    coerced = _coerce_static_artifact(candidate if isinstance(candidate, dict) else {})
    if coerced is None:
        raise ValueError("Session secret payload could not be coerced into a supported auth artifact.")
    return _normalize_resolved_auth_artifact(coerced)


def _write_secret(path: Path, payload: dict[str, Any]) -> None:
    _write_json(path, payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _effective_session_status(metadata: dict[str, Any]) -> str:
    status = str(metadata.get("status") or "active").strip().lower()
    if status not in AUTH_SESSION_STATUS_VALUES:
        status = "active"
    if status == "invalidated":
        return "invalidated"
    access_expires_at = _parse_iso8601(metadata.get("access_expires_at"))
    if access_expires_at and access_expires_at <= _utc_now():
        return "expired"
    return status


def _expires_state(expires_at: Any) -> str:
    parsed = _parse_iso8601(expires_at)
    if parsed is None:
        return "unknown"
    now = _utc_now()
    if parsed <= now:
        return "expired"
    if parsed <= now + SESSION_EXPIRING_WINDOW:
        return "expiring"
    return "active"


def _redacted_profile(metadata: dict[str, Any], *, paths: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = copy.deepcopy(metadata or {})
    payload["usage_policy"] = _normalize_usage_policy(payload.get("usage_policy"))
    payload["has_secret"] = _secret_path(paths, payload["profile_id"]).exists() if paths else None
    payload["resolver"] = {
        "kind": ((payload.get("resolver") or {}).get("kind")),
        "argv": list(((payload.get("resolver") or {}).get("argv") or [])),
        "cwd": ((payload.get("resolver") or {}).get("cwd")),
        "timeout_seconds": ((payload.get("resolver") or {}).get("timeout_seconds")),
    }
    return payload


def _redacted_session(metadata: dict[str, Any], *, paths: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = copy.deepcopy(metadata or {})
    payload["status"] = _effective_session_status(payload)
    payload["session_binding"] = _normalize_session_binding(payload.get("session_binding"))
    payload["has_secret"] = _session_secret_path(paths, payload["session_id"]).exists() if paths else None
    payload["expires_state"] = _expires_state(payload.get("access_expires_at"))
    payload["refresh_expires_state"] = _expires_state(payload.get("refresh_expires_at"))
    payload["refreshable"] = payload["status"] != "invalidated" and payload["refresh_expires_state"] != "expired"
    return payload


def _load_profile_record(paths: dict[str, Any], profile_id: str) -> dict[str, Any]:
    record = _load_json(_profile_record_path(paths, profile_id), default={}, strict=True, purpose=f"auth profile `{profile_id}`") or {}
    if not record:
        raise FileNotFoundError(f"Unknown auth profile: {profile_id}")
    if int(record.get("schema_version") or 0) < AUTH_SCHEMA_VERSION:
        record["schema_version"] = AUTH_SCHEMA_VERSION
        record["usage_policy"] = _normalize_usage_policy(record.get("usage_policy"))
    return record


def _load_secret_payload(paths: dict[str, Any], profile_id: str) -> dict[str, Any]:
    return _load_json(_secret_path(paths, profile_id), default={}, strict=False) or {}


def _load_session_record(paths: dict[str, Any], session_id: str) -> dict[str, Any]:
    record = _load_json(_session_record_path(paths, session_id), default={}, strict=True, purpose=f"auth session `{session_id}`") or {}
    if not record:
        raise FileNotFoundError(f"Unknown auth session: {session_id}")
    record["schema_version"] = AUTH_SCHEMA_VERSION
    record["status"] = _effective_session_status(record)
    record["action_tags"] = _normalize_string_list(record.get("action_tags"))
    record["request_mode"] = _normalize_request_mode(record.get("request_mode"), default="read_only")
    record["session_binding"] = _normalize_session_binding(record.get("session_binding"))
    return record


def _load_session_secret(paths: dict[str, Any], session_id: str) -> dict[str, Any]:
    return _load_json(_session_secret_path(paths, session_id), default={}, strict=False) or {}


def _session_sort_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("last_used_at") or ""),
        str(item.get("last_resolved_at") or ""),
        str(item.get("updated_at") or ""),
        str(item.get("created_at") or ""),
    )


def _artifact_summary(artifact_type: str, payload: Any) -> dict[str, Any]:
    if artifact_type == "credentials":
        values = payload if isinstance(payload, dict) else {}
        return {
            "artifact_type": artifact_type,
            "login": values.get("login") or values.get("username") or values.get("email"),
            "fields": sorted(values.keys()),
        }
    if artifact_type == "url":
        url = payload.get("url") if isinstance(payload, dict) else str(payload or "")
        return {"artifact_type": artifact_type, "url": url}
    if artifact_type == "headers":
        values = payload if isinstance(payload, dict) else {}
        return {"artifact_type": artifact_type, "header_names": sorted(values.keys())}
    if artifact_type == "cookies":
        values = payload if isinstance(payload, list) else []
        return {"artifact_type": artifact_type, "cookie_count": len(values)}
    if artifact_type == "storage_state":
        values = payload if isinstance(payload, dict) else {}
        return {"artifact_type": artifact_type, "keys": sorted(values.keys())}
    if artifact_type == "token_bundle":
        values = payload if isinstance(payload, dict) else {}
        headers = values.get("headers") if isinstance(values.get("headers"), dict) else {}
        return {
            "artifact_type": artifact_type,
            "token_type": values.get("token_type"),
            "has_access_token": bool(values.get("access_token")),
            "has_refresh_token": bool(values.get("refresh_token")),
            "access_expires_at": values.get("access_expires_at"),
            "refresh_expires_at": values.get("refresh_expires_at"),
            "base_url": values.get("base_url"),
            "subject_ref": values.get("subject_ref"),
            "header_names": sorted(headers.keys()),
        }
    return {"artifact_type": artifact_type}


def _normalize_resolved_auth_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    artifact_type = str(payload.get("artifact_type") or "").strip().lower()
    if artifact_type not in AUTH_ARTIFACT_TYPES:
        raise ValueError(f"Unsupported auth artifact_type: {artifact_type}")
    result = {
        "schema_version": AUTH_SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "expires_at": payload.get("expires_at") or ((payload.get("payload") or {}).get("access_expires_at") if artifact_type == "token_bundle" and isinstance(payload.get("payload"), dict) else None),
        "payload": copy.deepcopy(payload.get("payload")),
        "summary": copy.deepcopy(payload.get("summary") or _artifact_summary(artifact_type, payload.get("payload"))),
    }
    if result["payload"] is None:
        raise ValueError("Resolved auth artifact requires payload.")
    return result


def _issue_scope_ref(external_issue: Any) -> str | None:
    if external_issue is None:
        return None
    if isinstance(external_issue, str):
        text = external_issue.strip()
        return text or None
    if isinstance(external_issue, dict):
        for key in ("issue_key", "idReadable", "id", "key"):
            value = external_issue.get(key)
            if value:
                return str(value).strip()
    return None


def _matches_scope(item: dict[str, Any], scope_type: str, scope_ref: str | None) -> bool:
    if item.get("scope_type") != scope_type:
        return False
    if scope_type == "workspace":
        return bool(item.get("is_default"))
    if scope_ref is None:
        return False
    return str(item.get("scope_ref") or "").strip().lower() == scope_ref.strip().lower()


def _select_auth_profile_metadata(
    workspace: str | Path,
    *,
    profile_id: str | None = None,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    profiles = show_auth_profiles(workspace).get("items", [])
    explicit_profile_id = sanitize_identifier(profile_id or ((case or {}).get("auth_profile_ref")), "")
    if explicit_profile_id:
        for item in profiles:
            if item.get("profile_id") == explicit_profile_id:
                return _load_profile_record(_auth_paths(workspace), explicit_profile_id)
        raise FileNotFoundError(f"Unknown auth profile: {explicit_profile_id}")
    candidates: list[dict[str, Any]]
    if task_id:
        candidates = [item for item in profiles if _matches_scope(item, "task", task_id)]
        if len(candidates) > 1:
            raise ValueError(f"Multiple task-scoped auth profiles match task `{task_id}`.")
        if candidates:
            return _load_profile_record(_auth_paths(workspace), candidates[0]["profile_id"])
    issue_ref = _issue_scope_ref(external_issue)
    if issue_ref:
        candidates = [item for item in profiles if _matches_scope(item, "external_issue", issue_ref)]
        if len(candidates) > 1:
            raise ValueError(f"Multiple external_issue-scoped auth profiles match `{issue_ref}`.")
        if candidates:
            return _load_profile_record(_auth_paths(workspace), candidates[0]["profile_id"])
    candidates = [item for item in profiles if _matches_scope(item, "workspace", None)]
    if len(candidates) > 1:
        raise ValueError("Multiple workspace-default auth profiles are configured.")
    if candidates:
        return _load_profile_record(_auth_paths(workspace), candidates[0]["profile_id"])
    return None


def _resolver_context(
    workspace: str | Path,
    *,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
) -> dict[str, Any]:
    resolved_workspace = str(Path(workspace).expanduser().resolve())
    return {
        "workspace": {
            "workspace_path": resolved_workspace,
            "workstream_id": workstream_id,
        },
        "task": {"task_id": task_id} if task_id else None,
        "external_issue": {"scope_ref": _issue_scope_ref(external_issue)} if _issue_scope_ref(external_issue) else None,
        "case": {
            "id": (case or {}).get("id"),
            "title": (case or {}).get("title"),
            "runner": (case or {}).get("runner"),
            "surface_type": (case or {}).get("surface_type"),
            "auth_profile_ref": (case or {}).get("auth_profile_ref"),
            "auth_request_mode": (case or {}).get("auth_request_mode"),
            "auth_action_tags": copy.deepcopy((case or {}).get("auth_action_tags") or []),
            "auth_session_binding": copy.deepcopy((case or {}).get("auth_session_binding")),
            "auth_context": copy.deepcopy((case or {}).get("auth_context")),
        }
        if case
        else None,
    }


def _command_v2_input(
    workspace: str | Path,
    metadata: dict[str, Any],
    secret_payload: dict[str, Any],
    *,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
    request_mode: str,
    action_tags: list[str],
    session_binding: Any = None,
    resolution_reason: str,
    context_overrides: Any = None,
    cached_session: dict[str, Any] | None = None,
    cached_session_secret: dict[str, Any] | None = None,
    surface_mode: str | None = None,
) -> dict[str, Any]:
    context = _resolver_context(
        workspace,
        task_id=task_id,
        external_issue=external_issue,
        case=case,
        workstream_id=workstream_id,
    )
    context["request"] = {
        "request_mode": request_mode,
        "action_tags": copy.deepcopy(action_tags),
        "session_binding": copy.deepcopy(_normalize_session_binding(session_binding)),
        "resolution_reason": resolution_reason,
        "surface_mode": surface_mode,
    }
    cached_secret_payload = None
    cached_secret_record = None
    if isinstance(cached_session_secret, dict):
        cached_secret_record = copy.deepcopy(cached_session_secret)
        cached_secret_payload = copy.deepcopy(cached_session_secret.get("payload"))
    elif cached_session_secret is not None:
        cached_secret_payload = copy.deepcopy(cached_session_secret)
    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "profile": copy.deepcopy(metadata),
        "secret_payload": copy.deepcopy(secret_payload),
        "context": context,
        "request_mode": request_mode,
        "action_tags": copy.deepcopy(action_tags),
        "session_binding": copy.deepcopy(_normalize_session_binding(session_binding)),
        "resolution_reason": resolution_reason,
        "context_overrides": copy.deepcopy(context_overrides),
        "cached_session": copy.deepcopy(cached_session),
        "cached_session_secret_payload": cached_secret_payload,
        "cached_session_secret_record": cached_secret_record,
    }


def _run_resolver_command(resolver: dict[str, Any], workspace: str | Path, input_payload: dict[str, Any]) -> dict[str, Any]:
    cwd_value = resolver.get("cwd") or "."
    cwd = Path(workspace).expanduser().resolve() if cwd_value == "." else Path(cwd_value).expanduser().resolve()
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in (resolver.get("env") or {}).items()})
    completed = subprocess.run(  # noqa: S603
        list((resolver.get("argv") or [])),
        cwd=str(cwd),
        env=env,
        input=json.dumps(input_payload),
        text=True,
        capture_output=True,
        timeout=float(resolver.get("timeout_seconds") or 30),
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"Resolver exited with code {completed.returncode}."
        raise RuntimeError(detail)
    if not completed.stdout.strip():
        raise RuntimeError("Auth resolver returned empty stdout.")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Auth resolver returned invalid JSON: {exc}") from exc


def _execute_command_v1_resolver(
    workspace: str | Path,
    metadata: dict[str, Any],
    secret_payload: dict[str, Any],
    *,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
) -> dict[str, Any]:
    input_payload = {
        "schema_version": AUTH_SCHEMA_VERSION,
        "profile": {
            "profile_id": metadata.get("profile_id"),
            "label": metadata.get("label"),
            "scope_type": metadata.get("scope_type"),
            "scope_ref": metadata.get("scope_ref"),
            "artifact_policy": copy.deepcopy(metadata.get("artifact_policy") or {}),
        },
        "secret_payload": copy.deepcopy(secret_payload),
        "context": _resolver_context(
            workspace,
            task_id=task_id,
            external_issue=external_issue,
            case=case,
            workstream_id=workstream_id,
        ),
    }
    payload = _run_resolver_command(metadata.get("resolver") or {}, workspace, input_payload)
    return _normalize_resolved_auth_artifact(payload)


def _normalize_command_v2_output(payload: dict[str, Any]) -> dict[str, Any]:
    artifact = _normalize_resolved_auth_artifact(payload.get("artifact") or {})
    persistence = copy.deepcopy(payload.get("session_persistence") or {})
    persistence["persist"] = bool(persistence.get("persist", True))
    if persistence.get("request_mode") is not None:
        persistence["request_mode"] = _normalize_request_mode(persistence.get("request_mode"))
    if persistence.get("action_tags") is not None:
        persistence["action_tags"] = _normalize_string_list(persistence.get("action_tags"))
    if "session_binding" in persistence:
        persistence["session_binding"] = _normalize_session_binding(persistence.get("session_binding"))
    if persistence.get("source_kind") is not None:
        source_kind = str(persistence.get("source_kind") or "").strip().lower()
        if source_kind not in AUTH_SESSION_SOURCE_KINDS:
            raise ValueError(f"Unsupported auth session source_kind: {source_kind}")
        persistence["source_kind"] = source_kind
    return {
        "artifact": artifact,
        "session_persistence": persistence,
        "session_summary": copy.deepcopy(payload.get("session_summary") or artifact.get("summary") or {}),
    }


def _execute_command_v2_resolver(
    workspace: str | Path,
    metadata: dict[str, Any],
    secret_payload: dict[str, Any],
    *,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
    request_mode: str,
    action_tags: list[str],
    session_binding: Any = None,
    resolution_reason: str,
    context_overrides: Any = None,
    cached_session: dict[str, Any] | None = None,
    cached_session_secret: dict[str, Any] | None = None,
    surface_mode: str | None = None,
) -> dict[str, Any]:
    payload = _run_resolver_command(
        metadata.get("resolver") or {},
        workspace,
        _command_v2_input(
            workspace,
            metadata,
            secret_payload,
            task_id=task_id,
            external_issue=external_issue,
            case=case,
            workstream_id=workstream_id,
            request_mode=request_mode,
            action_tags=action_tags,
            session_binding=session_binding,
            resolution_reason=resolution_reason,
            context_overrides=context_overrides,
            cached_session=cached_session,
            cached_session_secret=cached_session_secret,
            surface_mode=surface_mode,
        ),
    )
    return _normalize_command_v2_output(payload)


def _append_auth_event(
    event_type: str,
    workspace: str | Path,
    *,
    source: str,
    status: str | None = None,
    task_id: str | None = None,
    workstream_id: str | None = None,
    external_issue: Any = None,
    payload: dict[str, Any] | None = None,
) -> None:
    from agentiux_dev_analytics import append_analytics_event

    append_analytics_event(
        event_type,
        workspace,
        source=source,
        status=status,
        task_id=task_id,
        workstream_id=workstream_id,
        external_issue=copy.deepcopy(external_issue),
        payload=copy.deepcopy(payload or {}),
    )


def _profile_usage_policy(metadata: dict[str, Any]) -> dict[str, Any]:
    return _normalize_usage_policy((metadata or {}).get("usage_policy"))


def _default_request_mode_for_profile(metadata: dict[str, Any], request_mode: str | None) -> str:
    policy = _profile_usage_policy(metadata)
    return _normalize_request_mode(request_mode or policy.get("default_request_mode") or "read_only")


def _enforce_profile_policy(
    metadata: dict[str, Any],
    *,
    request_mode: str,
    action_tags: list[str],
    workspace: str | Path,
    source: str,
    task_id: str | None,
    workstream_id: str | None,
    external_issue: Any,
) -> None:
    policy = _profile_usage_policy(metadata)
    if not _allowed_modes_allow(policy.get("allowed_request_modes") or [], request_mode):
        _append_auth_event(
            "auth_policy_rejected",
            workspace,
            source=source,
            status="failed",
            task_id=task_id,
            workstream_id=workstream_id,
            external_issue=external_issue,
            payload={
                "profile_id": metadata.get("profile_id"),
                "reason": "request_mode_not_allowed",
                "request_mode": request_mode,
                "allowed_request_modes": copy.deepcopy(policy.get("allowed_request_modes") or []),
            },
        )
        raise PermissionError(f"Auth request_mode `{request_mode}` is not allowed for profile `{metadata.get('profile_id')}`.")
    if not _tags_allowed(policy.get("action_tags") or [], action_tags):
        _append_auth_event(
            "auth_policy_rejected",
            workspace,
            source=source,
            status="failed",
            task_id=task_id,
            workstream_id=workstream_id,
            external_issue=external_issue,
            payload={
                "profile_id": metadata.get("profile_id"),
                "reason": "action_tags_not_allowed",
                "requested_action_tags": copy.deepcopy(action_tags),
                "allowed_action_tags": copy.deepcopy(policy.get("action_tags") or []),
            },
        )
        raise PermissionError(f"Requested auth action_tags are not allowed for profile `{metadata.get('profile_id')}`.")


def _session_policy_allows(session: dict[str, Any], *, request_mode: str, action_tags: list[str]) -> bool:
    if session.get("status") == "invalidated":
        return False
    if not _request_mode_allows(session.get("request_mode") or "read_only", request_mode):
        return False
    return _tags_allowed(session.get("action_tags") or [], action_tags)


def _session_scope_snapshot(
    profile: dict[str, Any],
    *,
    workspace: str | Path,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
    session_binding: Any = None,
) -> dict[str, Any]:
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": workstream_id,
        "task_id": task_id,
        "external_issue_ref": _issue_scope_ref(external_issue),
        "case_id": (case or {}).get("id"),
        "session_binding": copy.deepcopy(_normalize_session_binding(session_binding)),
        "profile_scope": {
            "scope_type": profile.get("scope_type"),
            "scope_ref": profile.get("scope_ref"),
            "is_default": bool(profile.get("is_default")),
        },
    }


def _session_summary_payload(
    profile: dict[str, Any],
    artifact: dict[str, Any],
    *,
    request_mode: str,
    action_tags: list[str],
    source_kind: str,
    summary_override: Any = None,
) -> dict[str, Any]:
    override = copy.deepcopy(summary_override) if isinstance(summary_override, dict) else {}
    return {
        "profile_id": profile.get("profile_id"),
        "label": profile.get("label"),
        "source_kind": source_kind,
        "request_mode": request_mode,
        "action_tags": copy.deepcopy(action_tags),
        "artifact": copy.deepcopy(override or artifact.get("summary") or {}),
    }


def _session_summary_artifact(summary: Any) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    artifact = summary.get("artifact")
    if isinstance(artifact, dict) and any(
        key in summary for key in ("profile_id", "label", "source_kind", "request_mode", "action_tags")
    ):
        return copy.deepcopy(artifact)
    return copy.deepcopy(summary)


def _artifact_refresh_expires_at(artifact: dict[str, Any], secret_payload: Any) -> Any:
    if artifact.get("artifact_type") == "token_bundle":
        payload = artifact.get("payload") if isinstance(artifact.get("payload"), dict) else {}
        return payload.get("refresh_expires_at")
    if isinstance(secret_payload, dict):
        return secret_payload.get("refresh_expires_at")
    return None


def _write_session_revision(paths: dict[str, Any], metadata: dict[str, Any], secret_wrapper: dict[str, Any]) -> str:
    revision_id = _next_revision_id()
    revision_dir = _session_revision_dir(paths, metadata["session_id"])
    revision_dir.mkdir(parents=True, exist_ok=True)
    _write_secret(
        _session_revision_path(paths, metadata["session_id"], revision_id),
        {
            "schema_version": AUTH_SCHEMA_VERSION,
            "revision_id": revision_id,
            "recorded_at": now_iso(),
            "session": copy.deepcopy(metadata),
            "secret": copy.deepcopy(secret_wrapper),
        },
    )
    return revision_id


def _sync_session_index_entry(paths: dict[str, Any], metadata: dict[str, Any]) -> None:
    index = _load_sessions_index(paths)
    items = [item for item in index.get("items", []) if item.get("session_id") != metadata.get("session_id")]
    items.append(_redacted_session(metadata, paths=paths))
    items.sort(key=lambda item: (_session_sort_key(item), item.get("session_id")), reverse=True)
    index["items"] = items
    _save_sessions_index(paths, index)


def _persist_session_record(
    paths: dict[str, Any],
    profile: dict[str, Any],
    artifact: dict[str, Any],
    *,
    secret_payload: Any = None,
    session_id: str | None = None,
    existing: dict[str, Any] | None = None,
    source_kind: str,
    request_mode: str,
    action_tags: list[str],
    summary: Any = None,
    access_expires_at: Any = None,
    refresh_expires_at: Any = None,
    status: str | None = None,
    scope_snapshot: dict[str, Any] | None = None,
    session_binding: Any = _UNSET,
    mark_resolved: bool = False,
    mark_used: bool = False,
) -> dict[str, Any]:
    existing = copy.deepcopy(existing or {})
    resolved_id = sanitize_identifier(session_id or existing.get("session_id") or _next_session_id(profile.get("profile_id") or "profile"), "session")
    now_value = now_iso()
    resolved_secret = secret_payload if secret_payload is not None else copy.deepcopy(artifact.get("payload"))
    resolved_source_kind = str(source_kind or existing.get("source_kind") or "resolver").strip().lower()
    if resolved_source_kind not in AUTH_SESSION_SOURCE_KINDS:
        raise ValueError(f"Unsupported auth session source_kind: {resolved_source_kind}")
    resolved_status = str(status or existing.get("status") or "active").strip().lower()
    if resolved_status not in AUTH_SESSION_STATUS_VALUES:
        raise ValueError(f"Unsupported auth session status: {resolved_status}")
    summary_payload = _session_summary_payload(
        profile,
        artifact,
        request_mode=request_mode,
        action_tags=action_tags,
        source_kind=resolved_source_kind,
        summary_override=summary,
    )
    metadata = {
        "schema_version": AUTH_SCHEMA_VERSION,
        "session_id": resolved_id,
        "profile_id": profile.get("profile_id"),
        "workspace_path": str(Path(paths["workspace_path"]).expanduser().resolve()),
        "scope_snapshot": copy.deepcopy(scope_snapshot or existing.get("scope_snapshot") or {}),
        "session_binding": (
            copy.deepcopy(existing.get("session_binding"))
            if session_binding is _UNSET
            else copy.deepcopy(_normalize_session_binding(session_binding))
        ),
        "source_kind": resolved_source_kind,
        "status": resolved_status,
        "artifact_type": artifact.get("artifact_type"),
        "request_mode": _normalize_request_mode(request_mode, default="read_only"),
        "action_tags": _normalize_string_list(action_tags),
        "access_expires_at": access_expires_at if access_expires_at is not None else (artifact.get("expires_at") or existing.get("access_expires_at")),
        "refresh_expires_at": refresh_expires_at if refresh_expires_at is not None else (_artifact_refresh_expires_at(artifact, resolved_secret) or existing.get("refresh_expires_at")),
        "last_resolved_at": now_value if mark_resolved or not existing else existing.get("last_resolved_at"),
        "last_used_at": now_value if mark_used else existing.get("last_used_at"),
        "created_at": existing.get("created_at") or now_value,
        "updated_at": now_value,
        "latest_revision_id": existing.get("latest_revision_id"),
        "summary": summary_payload,
    }
    if isinstance(metadata.get("scope_snapshot"), dict):
        metadata["scope_snapshot"]["session_binding"] = copy.deepcopy(metadata.get("session_binding"))
    secret_wrapper = {
        "schema_version": AUTH_SCHEMA_VERSION,
        "session_id": resolved_id,
        "updated_at": now_value,
        "artifact_type": artifact.get("artifact_type"),
        "expires_at": metadata.get("access_expires_at"),
        "payload": copy.deepcopy(resolved_secret),
    }
    revision_id = _write_session_revision(paths, metadata, secret_wrapper)
    metadata["latest_revision_id"] = revision_id
    _write_json(_session_record_path(paths, resolved_id), metadata)
    _write_secret(_session_secret_path(paths, resolved_id), secret_wrapper)
    _sync_session_index_entry(paths, metadata)
    return metadata


def _touch_session_usage(paths: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    secret_wrapper = _load_session_secret(paths, metadata["session_id"])
    return _persist_session_record(
        paths,
        _load_profile_record(paths, metadata["profile_id"]),
        _build_artifact_from_session(metadata, secret_wrapper),
        secret_payload=secret_wrapper.get("payload"),
        existing=metadata,
        session_id=metadata["session_id"],
        source_kind=metadata.get("source_kind") or "resolver",
        request_mode=metadata.get("request_mode") or "read_only",
        action_tags=metadata.get("action_tags") or [],
        summary=_session_summary_artifact(metadata.get("summary")),
        access_expires_at=metadata.get("access_expires_at"),
        refresh_expires_at=metadata.get("refresh_expires_at"),
        status=metadata.get("status") or "active",
        scope_snapshot=metadata.get("scope_snapshot") or {},
        session_binding=metadata.get("session_binding"),
        mark_resolved=False,
        mark_used=True,
    )


def _build_artifact_from_session(metadata: dict[str, Any], secret_wrapper: dict[str, Any]) -> dict[str, Any]:
    artifact = {
        "artifact_type": metadata.get("artifact_type"),
        "expires_at": metadata.get("access_expires_at") or secret_wrapper.get("expires_at"),
        "payload": copy.deepcopy(secret_wrapper.get("payload")),
        "summary": copy.deepcopy((metadata.get("summary") or {}).get("artifact") or _artifact_summary(metadata.get("artifact_type"), secret_wrapper.get("payload"))),
    }
    return _normalize_resolved_auth_artifact(artifact)


def _session_refresh_candidate(
    paths: dict[str, Any],
    metadata: dict[str, Any],
    *,
    profile: dict[str, Any],
    request_mode: str,
    action_tags: list[str],
    session_binding: Any = None,
) -> bool:
    if (profile.get("resolver") or {}).get("kind") != "command_v2":
        return False
    if not _profile_usage_policy(profile).get("allow_session_refresh", True):
        return False
    if metadata.get("status") == "invalidated":
        return False
    if not _session_policy_allows(metadata, request_mode=request_mode, action_tags=action_tags):
        return False
    if not _session_binding_matches(metadata.get("session_binding"), session_binding):
        return False
    refresh_expires_at = _parse_iso8601(metadata.get("refresh_expires_at"))
    if refresh_expires_at and refresh_expires_at <= _utc_now():
        return False
    secret_wrapper = _load_session_secret(paths, metadata["session_id"])
    return bool(secret_wrapper.get("payload"))


def _compatible_session_candidates(
    paths: dict[str, Any],
    profile: dict[str, Any],
    *,
    request_mode: str,
    action_tags: list[str],
    session_binding: Any = None,
) -> list[dict[str, Any]]:
    items = []
    for item in list_auth_sessions(paths["workspace_path"], profile_id=profile["profile_id"]).get("items", []):
        if item.get("profile_id") != profile["profile_id"]:
            continue
        if not _session_policy_allows(item, request_mode=request_mode, action_tags=action_tags):
            continue
        if not _session_binding_matches(item.get("session_binding"), session_binding):
            continue
        items.append(item)
    items.sort(key=lambda item: (_session_sort_key(item), item.get("session_id")), reverse=True)
    return items


def _persist_resolved_session(
    workspace: str | Path,
    profile: dict[str, Any],
    artifact: dict[str, Any],
    *,
    request_mode: str,
    action_tags: list[str],
    source_kind: str,
    session_persistence: dict[str, Any] | None = None,
    session_summary: Any = None,
    existing_session: dict[str, Any] | None = None,
    scope_snapshot: dict[str, Any],
    session_binding: Any = None,
) -> dict[str, Any] | None:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    profile_policy = _profile_usage_policy(profile)
    persistence = copy.deepcopy(session_persistence or {})
    should_persist = persistence.get("persist")
    if should_persist is None:
        should_persist = artifact.get("artifact_type") in CACHEABLE_ARTIFACT_TYPES
    if artifact.get("artifact_type") == "url" and not persistence.get("persist"):
        should_persist = False
    if not profile_policy.get("allow_session_persistence", True):
        should_persist = False
    if not should_persist:
        return None
    secret_payload = persistence.get("secret_payload")
    access_expires_at = persistence.get("access_expires_at", artifact.get("expires_at"))
    refresh_expires_at = persistence.get("refresh_expires_at", _artifact_refresh_expires_at(artifact, secret_payload or artifact.get("payload")))
    request_mode_value = persistence.get("request_mode") or request_mode
    action_tag_values = persistence.get("action_tags") or action_tags
    status_value = persistence.get("status") or "active"
    binding_value = (
        persistence.get("session_binding")
        if "session_binding" in persistence
        else (existing_session.get("session_binding") if existing_session else _normalize_session_binding(session_binding))
    )
    resolved_source_kind = (
        persistence.get("source_kind")
        or (existing_session.get("source_kind") if existing_session else None)
        or source_kind
    )
    metadata = _persist_session_record(
        paths,
        profile,
        artifact,
        secret_payload=secret_payload if secret_payload is not None else artifact.get("payload"),
        session_id=existing_session.get("session_id") if existing_session else None,
        existing=existing_session,
        source_kind=resolved_source_kind,
        request_mode=request_mode_value,
        action_tags=action_tag_values,
        summary=session_summary,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        status=status_value,
        scope_snapshot=scope_snapshot,
        session_binding=binding_value,
        mark_resolved=True,
        mark_used=True,
    )
    event_type = "auth_session_refreshed" if existing_session else "auth_session_created"
    _append_auth_event(
        event_type,
        workspace,
        source="auth",
        status="passed",
        payload={
            "profile_id": profile.get("profile_id"),
            "session_id": metadata.get("session_id"),
            "request_mode": metadata.get("request_mode"),
            "artifact_type": metadata.get("artifact_type"),
            "action_tags": copy.deepcopy(metadata.get("action_tags") or []),
        },
    )
    return _redacted_session(metadata, paths=paths)


def _policy_mismatch_items(profiles: list[dict[str, Any]], sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profiles_by_id = {item.get("profile_id"): item for item in profiles}
    mismatches: list[dict[str, Any]] = []
    for session in sessions:
        profile = profiles_by_id.get(session.get("profile_id"))
        if not profile:
            mismatches.append(
                {
                    "kind": "missing_profile",
                    "profile_id": session.get("profile_id"),
                    "session_id": session.get("session_id"),
                    "message": "Session references a missing auth profile.",
                }
            )
            continue
        policy = _profile_usage_policy(profile)
        if not _allowed_modes_allow(policy.get("allowed_request_modes") or [], session.get("request_mode") or "read_only"):
            mismatches.append(
                {
                    "kind": "request_mode",
                    "profile_id": session.get("profile_id"),
                    "session_id": session.get("session_id"),
                    "message": "Session request_mode exceeds the profile policy.",
                }
            )
        if not _tags_allowed(policy.get("action_tags") or [], session.get("action_tags") or []):
            mismatches.append(
                {
                    "kind": "action_tags",
                    "profile_id": session.get("profile_id"),
                    "session_id": session.get("session_id"),
                    "message": "Session action_tags exceed the profile policy.",
                }
            )
        if not policy.get("allow_session_persistence", True) and session.get("status") != "invalidated":
            mismatches.append(
                {
                    "kind": "persistence_disabled",
                    "profile_id": session.get("profile_id"),
                    "session_id": session.get("session_id"),
                    "message": "Cached session exists while profile persistence is disabled.",
                }
            )
    return mismatches


def write_auth_profile(workspace: str | Path, profile: dict[str, Any], secret_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    incoming = copy.deepcopy(profile or {})
    provided_secret = copy.deepcopy(secret_payload if secret_payload is not None else incoming.pop("secret_payload", incoming.pop("secret", {})) or {})
    existing = None
    existing_id = sanitize_identifier(incoming.get("profile_id"), "")
    if existing_id and _profile_record_path(paths, existing_id).exists():
        existing = _load_profile_record(paths, existing_id)
    existing_secret = _load_secret_payload(paths, existing_id) if existing_id and existing else {}
    resolved_secret = provided_secret if provided_secret else existing_secret.get("payload") or {}
    metadata = _normalize_profile_metadata(incoming, existing=existing, secret_payload=resolved_secret)
    if metadata["is_default"]:
        index = _load_auth_index(paths)
        for item in index.get("items", []):
            if item.get("scope_type") == "workspace" and item.get("profile_id") != metadata["profile_id"]:
                item["is_default"] = False
                existing_record_path = _profile_record_path(paths, item["profile_id"])
                existing_record = _load_json(existing_record_path, default={}, strict=False) or {}
                if existing_record:
                    existing_record["is_default"] = False
                    existing_record["updated_at"] = now_iso()
                    _write_json(existing_record_path, existing_record)
        _save_auth_index(paths, index)
    _write_json(_profile_record_path(paths, metadata["profile_id"]), metadata)
    if resolved_secret:
        _write_secret(
            _secret_path(paths, metadata["profile_id"]),
            {
                "schema_version": AUTH_SCHEMA_VERSION,
                "profile_id": metadata["profile_id"],
                "updated_at": now_iso(),
                "payload": resolved_secret,
            },
        )
    index = _load_auth_index(paths)
    index_items = [item for item in index.get("items", []) if item.get("profile_id") != metadata["profile_id"]]
    index_items.append(_redacted_profile(metadata, paths=paths))
    index_items.sort(key=lambda item: (item.get("scope_type") != "workspace", item.get("label") or item.get("profile_id")))
    index["items"] = index_items
    _save_auth_index(paths, index)
    return {
        "workspace_path": paths["workspace_path"],
        "profile": _redacted_profile(metadata, paths=paths),
        "profiles": show_auth_profiles(workspace),
    }


def _remove_auth_session_internal(paths: dict[str, Any], session_id: str) -> None:
    _session_record_path(paths, session_id).unlink(missing_ok=True)
    _session_secret_path(paths, session_id).unlink(missing_ok=True)
    shutil.rmtree(_session_revision_dir(paths, session_id), ignore_errors=True)
    index = _load_sessions_index(paths)
    index["items"] = [item for item in index.get("items", []) if item.get("session_id") != session_id]
    _save_sessions_index(paths, index)


def remove_auth_profile(workspace: str | Path, profile_id: str) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    resolved_id = sanitize_identifier(profile_id, "")
    if not resolved_id:
        raise ValueError("profile_id is required.")
    removed_session_ids = []
    for session in list_auth_sessions(workspace, profile_id=resolved_id).get("items", []):
        removed_session_ids.append(session["session_id"])
        _remove_auth_session_internal(paths, session["session_id"])
    _profile_record_path(paths, resolved_id).unlink(missing_ok=True)
    _secret_path(paths, resolved_id).unlink(missing_ok=True)
    index = _load_auth_index(paths)
    index["items"] = [item for item in index.get("items", []) if item.get("profile_id") != resolved_id]
    _save_auth_index(paths, index)
    return {
        "workspace_path": paths["workspace_path"],
        "removed_profile_id": resolved_id,
        "removed_session_ids": removed_session_ids,
        "profiles": show_auth_profiles(workspace),
        "sessions": list_auth_sessions(workspace),
    }


def show_auth_profiles(workspace: str | Path) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    index = _load_auth_index(paths)
    items = []
    for item in index.get("items", []):
        profile_id = sanitize_identifier(item.get("profile_id"), "")
        if not profile_id:
            continue
        try:
            items.append(_redacted_profile(_load_profile_record(paths, profile_id), paths=paths))
        except FileNotFoundError:
            continue
    items.sort(key=lambda item: (item.get("scope_type") != "workspace", not item.get("is_default"), item.get("label") or item.get("profile_id")))
    return {
        "workspace_path": paths["workspace_path"],
        "items": items,
        "counts": {
            "total": len(items),
            "workspace": sum(1 for item in items if item.get("scope_type") == "workspace"),
            "task": sum(1 for item in items if item.get("scope_type") == "task"),
            "external_issue": sum(1 for item in items if item.get("scope_type") == "external_issue"),
            "defaults": sum(1 for item in items if item.get("is_default")),
        },
        "updated_at": index.get("updated_at"),
    }


def list_auth_sessions(workspace: str | Path, *, profile_id: str | None = None) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    index = _load_sessions_index(paths)
    target_profile_id = sanitize_identifier(profile_id, "") if profile_id else None
    items = []
    for item in index.get("items", []):
        session_id = sanitize_identifier(item.get("session_id"), "")
        if not session_id:
            continue
        try:
            record = _load_session_record(paths, session_id)
        except FileNotFoundError:
            continue
        if target_profile_id and record.get("profile_id") != target_profile_id:
            continue
        items.append(_redacted_session(record, paths=paths))
    items.sort(key=lambda item: (_session_sort_key(item), item.get("session_id")), reverse=True)
    return {
        "workspace_path": paths["workspace_path"],
        "profile_id": target_profile_id,
        "items": items,
        "counts": {
            "total": len(items),
            "active": sum(1 for item in items if item.get("status") == "active"),
            "expired": sum(1 for item in items if item.get("status") == "expired"),
            "invalidated": sum(1 for item in items if item.get("status") == "invalidated"),
            "read_only": sum(1 for item in items if item.get("request_mode") == "read_only"),
            "mutating": sum(1 for item in items if item.get("request_mode") == "mutating"),
            "expiring": sum(1 for item in items if item.get("expires_state") == "expiring"),
        },
        "updated_at": index.get("updated_at"),
    }


def get_auth_session(workspace: str | Path, session_id: str) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    record = _load_session_record(paths, session_id)
    profile = _load_profile_record(paths, record["profile_id"])
    revision_dir = _session_revision_dir(paths, record["session_id"])
    revisions = sorted(revision_dir.glob("*.json"), reverse=True) if revision_dir.exists() else []
    return {
        "workspace_path": paths["workspace_path"],
        "session": _redacted_session(record, paths=paths),
        "profile": _redacted_profile(profile, paths=paths),
        "revision_count": len(revisions),
        "revision_ids": [path.stem for path in revisions[:10]],
    }


def write_auth_session(workspace: str | Path, session: dict[str, Any], secret_payload: Any | None = None) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    incoming = copy.deepcopy(session or {})
    profile_id = sanitize_identifier(incoming.get("profile_id"), "")
    if not profile_id:
        raise ValueError("profile_id is required for auth sessions.")
    profile = _load_profile_record(paths, profile_id)
    existing_id = sanitize_identifier(incoming.get("session_id"), "")
    existing = _load_session_record(paths, existing_id) if existing_id and _session_record_path(paths, existing_id).exists() else None
    existing_secret = _load_session_secret(paths, existing_id) if existing else {}
    raw_secret = secret_payload
    if raw_secret is None:
        raw_secret = incoming.pop("secret_payload", incoming.pop("secret", None))
    if raw_secret is None and isinstance(incoming.get("artifact"), dict):
        raw_secret = incoming.pop("artifact")
    if raw_secret is None and existing_secret.get("payload") is not None:
        raw_secret = existing_secret.get("payload")
    artifact = _coerce_session_artifact(raw_secret, explicit_artifact_type=incoming.get("artifact_type"))
    request_mode = _normalize_request_mode(
        incoming.get("request_mode") or (existing.get("request_mode") if existing else None) or "read_only"
    )
    action_tags = _normalize_string_list(incoming.get("action_tags", existing.get("action_tags") if existing else []))
    session_binding = (
        _normalize_session_binding(incoming.get("session_binding"))
        if "session_binding" in incoming
        else _normalize_session_binding(existing.get("session_binding") if existing else None)
    )
    metadata = _persist_session_record(
        paths,
        profile,
        artifact,
        secret_payload=copy.deepcopy(artifact.get("payload")),
        session_id=existing_id or incoming.get("session_id"),
        existing=existing,
        source_kind=str(incoming.get("source_kind") or (existing.get("source_kind") if existing else "manual")).strip().lower() or "manual",
        request_mode=request_mode,
        action_tags=action_tags,
        summary=_session_summary_artifact(incoming.get("summary") or (existing.get("summary") if existing else None)),
        access_expires_at=incoming.get("access_expires_at") or artifact.get("expires_at") or (existing.get("access_expires_at") if existing else None),
        refresh_expires_at=incoming.get("refresh_expires_at") or _artifact_refresh_expires_at(artifact, artifact.get("payload")) or (existing.get("refresh_expires_at") if existing else None),
        status=incoming.get("status") or (existing.get("status") if existing else "active"),
        scope_snapshot=copy.deepcopy(
            incoming.get("scope_snapshot")
            or (
                existing.get("scope_snapshot")
                if existing
                else _session_scope_snapshot(
                    profile,
                    workspace=paths["workspace_path"],
                    session_binding=session_binding,
                )
            )
        ),
        session_binding=session_binding,
        mark_resolved=True,
        mark_used=False,
    )
    if not existing:
        _append_auth_event(
            "auth_session_created",
            workspace,
            source="auth",
            status="passed",
            payload={
                "profile_id": profile_id,
                "session_id": metadata.get("session_id"),
                "request_mode": metadata.get("request_mode"),
                "artifact_type": metadata.get("artifact_type"),
            },
        )
    return {
        "workspace_path": paths["workspace_path"],
        "session": _redacted_session(metadata, paths=paths),
        "sessions": list_auth_sessions(workspace, profile_id=profile_id),
    }


def invalidate_auth_session(workspace: str | Path, session_id: str) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    metadata = _load_session_record(paths, session_id)
    profile = _load_profile_record(paths, metadata["profile_id"])
    secret_wrapper = _load_session_secret(paths, metadata["session_id"])
    updated = _persist_session_record(
        paths,
        profile,
        _build_artifact_from_session(metadata, secret_wrapper),
        secret_payload=secret_wrapper.get("payload"),
        session_id=metadata["session_id"],
        existing=metadata,
        source_kind=metadata.get("source_kind") or "resolver",
        request_mode=metadata.get("request_mode") or "read_only",
        action_tags=metadata.get("action_tags") or [],
        summary=_session_summary_artifact(metadata.get("summary")),
        access_expires_at=metadata.get("access_expires_at"),
        refresh_expires_at=metadata.get("refresh_expires_at"),
        status="invalidated",
        scope_snapshot=metadata.get("scope_snapshot") or {},
        session_binding=metadata.get("session_binding"),
        mark_resolved=False,
        mark_used=False,
    )
    _append_auth_event(
        "auth_session_invalidated",
        workspace,
        source="auth",
        status="passed",
        payload={"profile_id": updated.get("profile_id"), "session_id": updated.get("session_id")},
    )
    return {
        "workspace_path": paths["workspace_path"],
        "session": _redacted_session(updated, paths=paths),
        "sessions": list_auth_sessions(workspace, profile_id=updated["profile_id"]),
    }


def remove_auth_session(workspace: str | Path, session_id: str) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    resolved_id = sanitize_identifier(session_id, "")
    if not resolved_id:
        raise ValueError("session_id is required.")
    profile_id = None
    if _session_record_path(paths, resolved_id).exists():
        profile_id = (_load_session_record(paths, resolved_id) or {}).get("profile_id")
    _remove_auth_session_internal(paths, resolved_id)
    return {
        "workspace_path": paths["workspace_path"],
        "removed_session_id": resolved_id,
        "sessions": list_auth_sessions(workspace, profile_id=profile_id),
    }


def resolve_auth_profile_artifact(
    workspace: str | Path,
    *,
    profile_id: str | None = None,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
    request_mode: str | None = None,
    action_tags: list[str] | None = None,
    session_binding: Any = None,
    context_overrides: Any = None,
    prefer_cached: bool = True,
    force_refresh: bool = False,
    surface_mode: str = "resolver_only",
) -> dict[str, Any]:
    metadata = _select_auth_profile_metadata(
        workspace,
        profile_id=profile_id,
        task_id=task_id,
        external_issue=external_issue,
        case=case,
    )
    if metadata is None:
        raise FileNotFoundError("No matching auth profile could be resolved.")
    paths = _auth_paths(workspace)
    requested_mode = _default_request_mode_for_profile(metadata, request_mode or ((case or {}).get("auth_request_mode")))
    requested_action_tags = _normalize_string_list(action_tags if action_tags is not None else (case or {}).get("auth_action_tags"))
    requested_session_binding = _normalize_session_binding(
        session_binding if session_binding is not None else (case or {}).get("auth_session_binding")
    )
    _enforce_profile_policy(
        metadata,
        request_mode=requested_mode,
        action_tags=requested_action_tags,
        workspace=workspace,
        source=surface_mode,
        task_id=task_id,
        workstream_id=workstream_id,
        external_issue=external_issue,
    )
    scope_snapshot = _session_scope_snapshot(
        metadata,
        workspace=workspace,
        task_id=task_id,
        external_issue=external_issue,
        case=case,
        workstream_id=workstream_id,
        session_binding=requested_session_binding,
    )
    secret_wrapper = _load_secret_payload(paths, metadata["profile_id"])
    profile_secret_payload = copy.deepcopy(secret_wrapper.get("payload") or {})

    candidates = _compatible_session_candidates(
        paths,
        metadata,
        request_mode=requested_mode,
        action_tags=requested_action_tags,
        session_binding=requested_session_binding,
    )
    if prefer_cached and not force_refresh:
        for candidate in candidates:
            if candidate.get("status") != "active":
                continue
            secret_payload_wrapper = _load_session_secret(paths, candidate["session_id"])
            if not secret_payload_wrapper.get("payload"):
                continue
            updated_session = _touch_session_usage(paths, candidate)
            artifact = _build_artifact_from_session(updated_session, secret_payload_wrapper)
            _append_auth_event(
                "auth_session_reused",
                workspace,
                source=surface_mode,
                status="passed",
                task_id=task_id,
                workstream_id=workstream_id,
                external_issue=external_issue,
                payload={
                    "profile_id": metadata.get("profile_id"),
                    "session_id": updated_session.get("session_id"),
                    "artifact_type": artifact.get("artifact_type"),
                    "request_mode": requested_mode,
                    "action_tags": copy.deepcopy(requested_action_tags),
                },
            )
            return {
                "workspace_path": str(Path(workspace).expanduser().resolve()),
                "profile": _redacted_profile(metadata, paths=paths),
                "session": _redacted_session(updated_session, paths=paths),
                "artifact": artifact,
                "artifact_summary": copy.deepcopy(artifact.get("summary") or {}),
                "resolution_reason": "reuse",
                "request_mode": requested_mode,
                "action_tags": requested_action_tags,
                "session_binding": copy.deepcopy(requested_session_binding),
            }

    refresh_candidate = next(
        (
            candidate
            for candidate in candidates
            if _session_refresh_candidate(
                paths,
                candidate,
                profile=metadata,
                request_mode=requested_mode,
                action_tags=requested_action_tags,
                session_binding=requested_session_binding,
            )
        ),
        None,
    )
    if refresh_candidate is not None:
        try:
            output = _execute_command_v2_resolver(
                workspace,
                metadata,
                profile_secret_payload,
                task_id=task_id,
                external_issue=external_issue,
                case=case,
                workstream_id=workstream_id,
                request_mode=requested_mode,
                action_tags=requested_action_tags,
                session_binding=requested_session_binding,
                resolution_reason="manual_seed" if refresh_candidate.get("source_kind") == "manual" else "refresh",
                context_overrides=context_overrides,
                cached_session=_redacted_session(refresh_candidate, paths=paths),
                cached_session_secret=_load_session_secret(paths, refresh_candidate["session_id"]),
                surface_mode=surface_mode,
            )
        except Exception as exc:  # noqa: BLE001
            invalidate_auth_session(workspace, refresh_candidate["session_id"])
            _append_auth_event(
                "auth_resolver_failed",
                workspace,
                source=surface_mode,
                status="failed",
                task_id=task_id,
                workstream_id=workstream_id,
                external_issue=external_issue,
                payload={
                    "profile_id": metadata.get("profile_id"),
                    "session_id": refresh_candidate.get("session_id"),
                    "reason": "refresh_failed",
                    "error": str(exc),
                },
            )
            raise
        persisted_session = _persist_resolved_session(
            workspace,
            metadata,
            output["artifact"],
            request_mode=requested_mode,
            action_tags=requested_action_tags,
            source_kind="resolver",
            session_persistence=output.get("session_persistence"),
            session_summary=output.get("session_summary"),
            existing_session=refresh_candidate,
            scope_snapshot=scope_snapshot,
            session_binding=requested_session_binding,
        )
        return {
            "workspace_path": str(Path(workspace).expanduser().resolve()),
            "profile": _redacted_profile(metadata, paths=paths),
            "session": persisted_session or _redacted_session(refresh_candidate, paths=paths),
            "artifact": output["artifact"],
            "artifact_summary": copy.deepcopy(output["artifact"].get("summary") or {}),
            "resolution_reason": "manual_seed" if refresh_candidate.get("source_kind") == "manual" else "refresh",
            "request_mode": requested_mode,
            "action_tags": requested_action_tags,
            "session_binding": copy.deepcopy(requested_session_binding),
        }

    try:
        if (metadata.get("resolver") or {}).get("kind") == "command_v2":
            output = _execute_command_v2_resolver(
                workspace,
                metadata,
                profile_secret_payload,
                task_id=task_id,
                external_issue=external_issue,
                case=case,
                workstream_id=workstream_id,
                request_mode=requested_mode,
                action_tags=requested_action_tags,
                session_binding=requested_session_binding,
                resolution_reason="initial",
                context_overrides=context_overrides,
                cached_session=None,
                cached_session_secret=None,
                surface_mode=surface_mode,
            )
            artifact = output["artifact"]
            persisted_session = _persist_resolved_session(
                workspace,
                metadata,
                artifact,
                request_mode=requested_mode,
                action_tags=requested_action_tags,
                source_kind="resolver",
                session_persistence=output.get("session_persistence"),
                session_summary=output.get("session_summary"),
                existing_session=None,
                scope_snapshot=scope_snapshot,
                session_binding=requested_session_binding,
            )
        else:
            artifact = _execute_command_v1_resolver(
                workspace,
                metadata,
                profile_secret_payload,
                task_id=task_id,
                external_issue=external_issue,
                case=case,
                workstream_id=workstream_id,
            )
            persisted_session = _persist_resolved_session(
                workspace,
                metadata,
                artifact,
                request_mode=requested_mode,
                action_tags=requested_action_tags,
                source_kind="resolver",
                session_persistence=None,
                session_summary=artifact.get("summary"),
                existing_session=None,
                scope_snapshot=scope_snapshot,
                session_binding=requested_session_binding,
            )
    except Exception as exc:  # noqa: BLE001
        _append_auth_event(
            "auth_resolver_failed",
            workspace,
            source=surface_mode,
            status="failed",
            task_id=task_id,
            workstream_id=workstream_id,
            external_issue=external_issue,
            payload={
                "profile_id": metadata.get("profile_id"),
                "reason": "resolution_failed",
                "error": str(exc),
            },
        )
        raise
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "profile": _redacted_profile(metadata, paths=paths),
        "session": persisted_session,
        "artifact": artifact,
        "artifact_summary": copy.deepcopy(artifact.get("summary") or {}),
        "resolution_reason": "initial",
        "request_mode": requested_mode,
        "action_tags": requested_action_tags,
        "session_binding": copy.deepcopy(requested_session_binding),
    }


def resolve_auth_profile(
    workspace: str | Path,
    *,
    profile_id: str | None = None,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
    request_mode: str | None = None,
    action_tags: list[str] | None = None,
    session_binding: Any = None,
    context_overrides: Any = None,
    prefer_cached: bool = True,
    force_refresh: bool = False,
    surface_mode: str = "resolver_only",
) -> dict[str, Any]:
    resolved = resolve_auth_profile_artifact(
        workspace,
        profile_id=profile_id,
        task_id=task_id,
        external_issue=external_issue,
        case=case,
        workstream_id=workstream_id,
        request_mode=request_mode,
        action_tags=action_tags,
        session_binding=session_binding,
        context_overrides=context_overrides,
        prefer_cached=prefer_cached,
        force_refresh=force_refresh,
        surface_mode=surface_mode,
    )
    return {
        "workspace_path": resolved["workspace_path"],
        "profile": resolved["profile"],
        "session": copy.deepcopy(resolved.get("session")),
        "resolution_reason": resolved.get("resolution_reason"),
        "request_mode": resolved.get("request_mode"),
        "action_tags": copy.deepcopy(resolved.get("action_tags") or []),
        "session_binding": copy.deepcopy(resolved.get("session_binding")),
        "artifact": {
            "artifact_type": resolved["artifact"]["artifact_type"],
            "expires_at": resolved["artifact"].get("expires_at"),
            "summary": copy.deepcopy(resolved["artifact_summary"]),
        },
    }


def workspace_auth_detail(workspace: str | Path) -> dict[str, Any]:
    profiles = show_auth_profiles(workspace)
    sessions = list_auth_sessions(workspace)
    default_profile = next((item for item in profiles.get("items", []) if item.get("is_default")), None)
    attention_items = _policy_mismatch_items(profiles.get("items", []), sessions.get("items", []))
    summary = {
        "profile_count": profiles["counts"]["total"],
        "workspace_profile_count": profiles["counts"]["workspace"],
        "task_profile_count": profiles["counts"]["task"],
        "external_issue_profile_count": profiles["counts"]["external_issue"],
        "default_profile_id": default_profile.get("profile_id") if default_profile else None,
        "session_count": sessions["counts"]["total"],
        "active_session_count": sessions["counts"]["active"],
        "read_only_session_count": sessions["counts"]["read_only"],
        "mutating_session_count": sessions["counts"]["mutating"],
        "expiring_session_count": sessions["counts"]["expiring"],
        "expired_session_count": sessions["counts"]["expired"],
        "invalidated_session_count": sessions["counts"]["invalidated"],
        "policy_mismatch_count": len(attention_items),
    }
    return {
        "workspace_path": profiles["workspace_path"],
        "items": copy.deepcopy(profiles.get("items") or []),
        "counts": copy.deepcopy(profiles.get("counts") or {}),
        "updated_at": max(str(profiles.get("updated_at") or ""), str(sessions.get("updated_at") or "")) or None,
        "default_profile_id": summary["default_profile_id"],
        "summary": summary,
        "profiles": profiles,
        "sessions": sessions,
        "attention_items": attention_items,
    }


def workspace_auth_summary(workspace: str | Path) -> dict[str, Any]:
    detail = workspace_auth_detail(workspace)
    return copy.deepcopy(detail.get("summary") or {})


def _emit_static_artifact_from_stdin() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    artifact = _coerce_static_artifact((payload.get("secret_payload") or {}))
    if artifact is None:
        raise ValueError("secret_payload cannot be coerced into a static auth artifact.")
    print(json.dumps(_normalize_resolved_auth_artifact(artifact)))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AgentiUX Dev auth helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("emit-static-artifact")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "emit-static-artifact":
        return _emit_static_artifact_from_stdin()
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
