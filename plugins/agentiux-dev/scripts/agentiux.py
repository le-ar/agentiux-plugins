#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("AGENTIUX_DEV_PLUGIN_ROOT", str(Path(__file__).resolve().parents[1]))

from agentiux_dev_gui import launch as launch_gui
from agentiux_dev_gui import status as gui_status
from agentiux_dev_gui import stop as stop_gui
from agentiux_dev_lib import list_workspaces, sanitize_identifier


class WorkspaceSelectionError(ValueError):
    pass


WEB_ACTIONS = {"launch", "start", "status", "stop", "url"}


def _workspace_entries() -> list[dict[str, Any]]:
    return list_workspaces().get("workspaces", [])


def _resolve_existing_path(selector: str, cwd: Path) -> Path | None:
    candidate = Path(selector).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        return resolved if resolved.exists() else None
    direct = (cwd / candidate).resolve()
    if direct.exists():
        return direct
    return None


def _workspace_path(entry: dict[str, Any]) -> Path:
    return Path(str(entry["workspace_path"])).expanduser().resolve()


def _matching_workspace_for_path(target: Path, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        workspace_path = _workspace_path(entry)
        try:
            target.relative_to(workspace_path)
        except ValueError:
            continue
        matches.append((len(workspace_path.parts), entry))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def _selector_identities(entry: dict[str, Any]) -> set[str]:
    workspace_path = _workspace_path(entry)
    values = {
        sanitize_identifier(entry.get("workspace_slug"), ""),
        sanitize_identifier(entry.get("workspace_name"), ""),
        sanitize_identifier(entry.get("workspace_label"), ""),
        sanitize_identifier(entry.get("workspace_hash"), ""),
        sanitize_identifier(workspace_path.name, ""),
        sanitize_identifier(str(workspace_path), ""),
    }
    return {value for value in values if value}


def _format_candidates(entries: list[dict[str, Any]]) -> str:
    lines = []
    for entry in entries[:8]:
        workspace_path = _workspace_path(entry)
        slug = entry.get("workspace_slug") or workspace_path.name
        label = entry.get("workspace_label") or workspace_path.name
        lines.append(f"- {slug}: {label} [{workspace_path}]")
    return "\n".join(lines)


def resolve_workspace_selector(selector: str | None, cwd: Path | None = None) -> str | None:
    entries = _workspace_entries()
    current_dir = (cwd or Path.cwd()).expanduser().resolve()
    if not entries:
        if selector:
            raise WorkspaceSelectionError("No initialized workspaces are registered yet.")
        return None

    if not selector:
        selected = _matching_workspace_for_path(current_dir, entries)
        return str(_workspace_path(selected)) if selected else None

    resolved_path = _resolve_existing_path(selector, current_dir)
    if resolved_path is not None:
        selected = _matching_workspace_for_path(resolved_path, entries)
        if selected is None:
            raise WorkspaceSelectionError(
                f"Workspace path exists but is not initialized in AgentiUX Dev state: {resolved_path}"
            )
        return str(_workspace_path(selected))

    normalized = sanitize_identifier(selector, "")
    if not normalized:
        return None

    exact_matches = [entry for entry in entries if normalized in _selector_identities(entry)]
    if len(exact_matches) == 1:
        return str(_workspace_path(exact_matches[0]))
    if len(exact_matches) > 1:
        raise WorkspaceSelectionError(
            f"Workspace selector `{selector}` is ambiguous.\n{_format_candidates(exact_matches)}"
        )

    prefix_matches = [
        entry for entry in entries if any(identity.startswith(normalized) for identity in _selector_identities(entry))
    ]
    if len(prefix_matches) == 1:
        return str(_workspace_path(prefix_matches[0]))
    if len(prefix_matches) > 1:
        raise WorkspaceSelectionError(
            f"Workspace selector `{selector}` matches multiple workspaces.\n{_format_candidates(prefix_matches)}"
        )

    fuzzy_matches = [
        entry
        for entry in entries
        if any(normalized in identity for identity in _selector_identities(entry))
    ]
    if len(fuzzy_matches) == 1:
        return str(_workspace_path(fuzzy_matches[0]))
    if len(fuzzy_matches) > 1:
        raise WorkspaceSelectionError(
            f"Workspace selector `{selector}` matches multiple workspaces.\n{_format_candidates(fuzzy_matches)}"
        )

    raise WorkspaceSelectionError(f"Unknown workspace selector `{selector}`.")


def _normalize_web_action(first: str | None, second: str | None) -> tuple[str, str | None]:
    normalized = sanitize_identifier(first, "")
    if normalized in WEB_ACTIONS:
        return normalized, second
    return "launch", first


def _emit_payload(payload: dict[str, Any], *, json_output: bool, url_only: bool = False) -> int:
    if json_output:
        print(json.dumps(payload, indent=2))
        return 0
    if url_only:
        url = payload.get("url")
        if not url:
            print("Dashboard is not running.", file=sys.stderr)
            return 1
        print(url)
        return 0

    status = payload.get("status", "unknown")
    print(f"status: {status}")
    if payload.get("url"):
        print(f"url: {payload['url']}")
    if payload.get("default_workspace"):
        print(f"workspace: {payload['default_workspace']}")
    if payload.get("pid"):
        print(f"pid: {payload['pid']}")
    elif payload.get("last_pid"):
        print(f"last_pid: {payload['last_pid']}")
    return 0 if status == "running" or payload.get("last_url") else 1


def run_web_command(args: argparse.Namespace) -> int:
    action, selector = _normalize_web_action(args.action_or_selector, args.selector)
    if action == "launch":
        workspace = resolve_workspace_selector(selector, Path.cwd())
        payload = launch_gui(args.host, args.port, workspace)
        return _emit_payload(payload, json_output=args.json_output)
    if action == "status":
        return _emit_payload(gui_status(), json_output=args.json_output)
    if action == "stop":
        return _emit_payload(stop_gui(), json_output=args.json_output)
    if action == "url":
        return _emit_payload(gui_status(), json_output=args.json_output, url_only=True)
    raise ValueError(f"Unsupported web action: {action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentiUX shell launcher")
    subparsers = parser.add_subparsers(dest="surface", required=True)

    for name in ("web", "gui"):
        command = subparsers.add_parser(name, help="Launch or manage the dashboard singleton")
        command.add_argument("action_or_selector", nargs="?")
        command.add_argument("selector", nargs="?")
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int)
        command.add_argument("--json", action="store_true", dest="json_output")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.surface in {"web", "gui"}:
            return run_web_command(args)
    except WorkspaceSelectionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
