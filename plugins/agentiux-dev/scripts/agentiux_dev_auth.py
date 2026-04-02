#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
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


AUTH_SCHEMA_VERSION = 1
AUTH_SCOPE_TYPES = {"workspace", "task", "external_issue"}
AUTH_ARTIFACT_TYPES = {"credentials", "url", "headers", "cookies", "storage_state"}


def _auth_paths(workspace: str | Path) -> dict[str, Any]:
    base_paths = _ensure_workspace_initialized(workspace)
    return {
        **base_paths,
        "root": Path(base_paths["auth_root"]),
        "profiles_dir": Path(base_paths["auth_profiles_dir"]),
        "secrets_dir": Path(base_paths["auth_secrets_dir"]),
        "index_path": Path(base_paths["auth_index"]),
    }


def _ensure_auth_dirs(paths: dict[str, Any]) -> None:
    for key in ("root", "profiles_dir", "secrets_dir"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(Path(paths["secrets_dir"]), 0o700)
    except OSError:
        pass


def _default_auth_index() -> dict[str, Any]:
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


def _profile_record_path(paths: dict[str, Any], profile_id: str) -> Path:
    return Path(paths["profiles_dir"]) / f"{sanitize_identifier(profile_id, 'profile')}.json"


def _secret_path(paths: dict[str, Any], profile_id: str) -> Path:
    return Path(paths["secrets_dir"]) / f"{sanitize_identifier(profile_id, 'profile')}.json"


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


def _normalize_resolver(resolver: Any, *, secret_payload: dict[str, Any]) -> dict[str, Any]:
    if resolver is None:
        if _coerce_static_artifact(secret_payload) is None:
            raise ValueError("resolver is required unless secret_payload can be coerced into a static auth artifact.")
        resolver = _default_static_resolver()
    payload = copy.deepcopy(resolver or {})
    if payload.get("kind") != "command_v1":
        raise ValueError("Only resolver.kind=`command_v1` is supported in v1.")
    argv = payload.get("argv")
    if not isinstance(argv, list) or not argv:
        raise ValueError("resolver.argv is required for command_v1 auth resolvers.")
    env = payload.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError("resolver.env must be an object when provided.")
    return {
        "kind": "command_v1",
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
    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "profile_id": profile_id,
        "label": label,
        "scope_type": scope_type,
        "scope_ref": scope_ref,
        "is_default": is_default,
        "resolver": _normalize_resolver(payload.get("resolver", existing.get("resolver")), secret_payload=secret_payload),
        "artifact_policy": _normalize_artifact_policy(payload.get("artifact_policy", existing.get("artifact_policy"))),
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


def _write_secret(path: Path, payload: dict[str, Any]) -> None:
    _write_json(path, payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _redacted_profile(metadata: dict[str, Any], *, paths: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = copy.deepcopy(metadata or {})
    payload["has_secret"] = _secret_path(paths, payload["profile_id"]).exists() if paths else None
    payload["resolver"] = {
        "kind": ((payload.get("resolver") or {}).get("kind")),
        "argv": list(((payload.get("resolver") or {}).get("argv") or [])),
        "cwd": ((payload.get("resolver") or {}).get("cwd")),
        "timeout_seconds": ((payload.get("resolver") or {}).get("timeout_seconds")),
    }
    return payload


def _load_profile_record(paths: dict[str, Any], profile_id: str) -> dict[str, Any]:
    record = _load_json(_profile_record_path(paths, profile_id), default={}, strict=True, purpose=f"auth profile `{profile_id}`") or {}
    if not record:
        raise FileNotFoundError(f"Unknown auth profile: {profile_id}")
    return record


def _load_secret_payload(paths: dict[str, Any], profile_id: str) -> dict[str, Any]:
    return _load_json(_secret_path(paths, profile_id), default={}, strict=False) or {}


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
    resolved_secret = provided_secret if provided_secret else existing_secret
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


def remove_auth_profile(workspace: str | Path, profile_id: str) -> dict[str, Any]:
    paths = _auth_paths(workspace)
    _ensure_auth_dirs(paths)
    resolved_id = sanitize_identifier(profile_id, "")
    if not resolved_id:
        raise ValueError("profile_id is required.")
    _profile_record_path(paths, resolved_id).unlink(missing_ok=True)
    _secret_path(paths, resolved_id).unlink(missing_ok=True)
    index = _load_auth_index(paths)
    index["items"] = [item for item in index.get("items", []) if item.get("profile_id") != resolved_id]
    _save_auth_index(paths, index)
    return {
        "workspace_path": paths["workspace_path"],
        "removed_profile_id": resolved_id,
        "profiles": show_auth_profiles(workspace),
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
    return {"artifact_type": artifact_type}


def _normalize_resolved_auth_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    artifact_type = str(payload.get("artifact_type") or "").strip().lower()
    if artifact_type not in AUTH_ARTIFACT_TYPES:
        raise ValueError(f"Unsupported auth artifact_type: {artifact_type}")
    result = {
        "schema_version": AUTH_SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "expires_at": payload.get("expires_at"),
        "payload": copy.deepcopy(payload.get("payload")),
        "summary": copy.deepcopy(payload.get("summary") or _artifact_summary(artifact_type, payload.get("payload"))),
    }
    if result["payload"] is None:
        raise ValueError("Resolved auth artifact requires payload.")
    return result


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
        }
        if case
        else None,
    }


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
    resolver = metadata.get("resolver") or {}
    cwd_value = resolver.get("cwd") or "."
    cwd = Path(workspace).expanduser().resolve() if cwd_value == "." else Path(cwd_value).expanduser().resolve()
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in (resolver.get("env") or {}).items()})
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
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Auth resolver returned invalid JSON: {exc}") from exc
    return _normalize_resolved_auth_artifact(payload)


def resolve_auth_profile_artifact(
    workspace: str | Path,
    *,
    profile_id: str | None = None,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
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
    secret_wrapper = _load_secret_payload(paths, metadata["profile_id"])
    secret_payload = copy.deepcopy(secret_wrapper.get("payload") or {})
    artifact = _execute_command_v1_resolver(
        workspace,
        metadata,
        secret_payload,
        task_id=task_id,
        external_issue=external_issue,
        case=case,
        workstream_id=workstream_id,
    )
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "profile": _redacted_profile(metadata, paths=paths),
        "artifact": artifact,
        "artifact_summary": copy.deepcopy(artifact.get("summary") or {}),
    }


def resolve_auth_profile(
    workspace: str | Path,
    *,
    profile_id: str | None = None,
    task_id: str | None = None,
    external_issue: Any = None,
    case: dict[str, Any] | None = None,
    workstream_id: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_auth_profile_artifact(
        workspace,
        profile_id=profile_id,
        task_id=task_id,
        external_issue=external_issue,
        case=case,
        workstream_id=workstream_id,
    )
    return {
        "workspace_path": resolved["workspace_path"],
        "profile": resolved["profile"],
        "artifact": {
            "artifact_type": resolved["artifact"]["artifact_type"],
            "expires_at": resolved["artifact"].get("expires_at"),
            "summary": copy.deepcopy(resolved["artifact_summary"]),
        },
    }


def workspace_auth_detail(workspace: str | Path) -> dict[str, Any]:
    payload = show_auth_profiles(workspace)
    default_profile = next((item for item in payload.get("items", []) if item.get("is_default")), None)
    summary = {
        "profile_count": payload["counts"]["total"],
        "workspace_profile_count": payload["counts"]["workspace"],
        "task_profile_count": payload["counts"]["task"],
        "external_issue_profile_count": payload["counts"]["external_issue"],
        "default_profile_id": default_profile.get("profile_id") if default_profile else None,
    }
    return {
        **payload,
        "default_profile_id": summary["default_profile_id"],
        "summary": summary,
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
