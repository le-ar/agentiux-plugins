#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentiux_dev_lib import (
    PLUGIN_NAME,
    current_host_os,
    marketplace_path,
    now_iso,
    python_launcher_tokens,
)


def source_plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_install_root() -> Path:
    return (Path.home() / "plugins" / PLUGIN_NAME).resolve()


def launcher_name(host_os: str | None = None) -> str:
    return "agentiux.cmd" if (host_os or current_host_os()) == "windows" else "agentiux"


def install_bin_root(destination: Path) -> Path:
    return destination / "bin"


def codex_cache_install_root(codex_home: Path, *, provider_name: str = "local-plugins") -> Path:
    return (codex_home / "plugins" / "cache" / provider_name / PLUGIN_NAME / "local").resolve()


def codex_tmp_plugin_install_root(codex_home: Path) -> Path:
    return (codex_home / ".tmp" / "plugins" / "plugins" / PLUGIN_NAME).resolve()


def codex_tmp_marketplace_path(codex_home: Path) -> Path:
    return (codex_home / ".tmp" / "plugins" / ".agents" / "plugins" / "marketplace.json").resolve()


def _path_entries(path_env: str | None = None) -> list[Path]:
    entries: list[Path] = []
    for raw_entry in (path_env or os.environ.get("PATH", "")).split(os.pathsep):
        if not raw_entry:
            continue
        candidate = Path(raw_entry).expanduser()
        if candidate not in entries:
            entries.append(candidate)
    return entries


def _is_writable_directory(path: Path) -> bool:
    return path.exists() and path.is_dir() and os.access(path, os.W_OK)


def _is_ephemeral_directory(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    ephemeral_roots = [
        home / ".codex" / "tmp",
        Path("/tmp"),
        Path("/var/folders"),
    ]
    return any(resolved == root or root in resolved.parents for root in ephemeral_roots)


def discover_global_bin_dir(path_env: str | None = None) -> Path | None:
    home = Path.home().resolve()
    for entry in _path_entries(path_env):
        resolved_entry = entry.resolve()
        try:
            resolved_entry.relative_to(home)
        except ValueError:
            continue
        if _is_ephemeral_directory(resolved_entry):
            continue
        if _is_writable_directory(resolved_entry):
            return resolved_entry
    return None


def _launcher_script(script_path: Path, plugin_root: Path, host_os: str | None = None) -> str:
    resolved_host = host_os or current_host_os()
    launcher_tokens = python_launcher_tokens(resolved_host)
    if resolved_host == "windows":
        command = subprocess.list2cmdline([*launcher_tokens, str(script_path.resolve())])
        return (
            "@echo off\r\n"
            f'set "AGENTIUX_DEV_PLUGIN_ROOT={plugin_root.resolve()}"\r\n'
            f"{command} %*\r\n"
        )
    command = shlex.join([*launcher_tokens, str(script_path.resolve())])
    return (
        "#!/bin/sh\n"
        f"export AGENTIUX_DEV_PLUGIN_ROOT={shlex.quote(str(plugin_root.resolve()))}\n"
        f'exec {command} "$@"\n'
    )


def write_launcher(target_path: Path, script_path: Path, plugin_root: Path, host_os: str | None = None) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(_launcher_script(script_path, plugin_root, host_os=host_os))
    if (host_os or current_host_os()) != "windows":
        target_path.chmod(0o755)
    return target_path.resolve()


def install_launchers(destination: Path, bin_dir: Path | None = None, *, install_global_command: bool = True) -> dict[str, Any]:
    host_os = current_host_os()
    script_path = destination / "scripts" / "agentiux.py"
    local_launcher_path = write_launcher(
        install_bin_root(destination) / launcher_name(host_os),
        script_path,
        destination,
        host_os=host_os,
    )

    global_launcher_path: Path | None = None
    global_command_status = "skipped"
    global_command_reason = "Global command installation was disabled."
    selected_bin_dir: Path | None = None

    if install_global_command:
        selected_bin_dir = (bin_dir or discover_global_bin_dir())
        if selected_bin_dir is None:
            global_command_status = "not_installed"
            global_command_reason = "No writable user PATH directory was detected. Re-run with --bin-dir <dir-in-PATH>."
        else:
            selected_bin_dir.mkdir(parents=True, exist_ok=True)
            global_launcher_path = write_launcher(
                selected_bin_dir / launcher_name(host_os),
                script_path,
                destination,
                host_os=host_os,
            )
            global_command_status = "installed"
            global_command_reason = None

    return {
        "installed_launcher_path": str(local_launcher_path),
        "global_command_name": "agentiux.cmd" if host_os == "windows" else "agentiux",
        "global_command_bin_dir": str(selected_bin_dir.resolve()) if selected_bin_dir else None,
        "global_launcher_path": str(global_launcher_path) if global_launcher_path else None,
        "global_command_status": global_command_status,
        "global_command_reason": global_command_reason,
    }


def _copy_plugin(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            ".DS_Store",
            "__pycache__",
            "*.pyc",
            "*.pyo",
            ".pytest_cache",
            "node_modules",
            ".git",
        ),
    )


def _write_install_metadata(destination: Path, source: Path, marketplace: Path) -> None:
    payload = {
        "plugin_name": PLUGIN_NAME,
        "source_root": str(source.resolve()),
        "install_root": str(destination.resolve()),
        "marketplace_path": str(marketplace.resolve()),
        "installed_at": now_iso(),
    }
    (destination / "install-metadata.json").write_text(json.dumps(payload, indent=2) + "\n")


def _write_installed_mcp(destination: Path) -> None:
    launcher = python_launcher_tokens()
    payload = {
        "mcpServers": {
            "agentiux-dev-state": {
                "type": "stdio",
                "command": launcher[0],
                "args": launcher[1:] + [str((destination / "scripts" / "agentiux_dev_mcp.py").resolve())],
                "env": {
                    "AGENTIUX_DEV_PLUGIN_ROOT": str(destination.resolve()),
                },
            }
        }
    }
    (destination / ".mcp.json").write_text(json.dumps(payload, indent=2) + "\n")


def _load_plugin_manifest(source: Path) -> dict[str, Any]:
    manifest_path = source / ".codex-plugin" / "plugin.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _marketplace_plugin_entry(source: Path) -> dict[str, Any]:
    manifest = _load_plugin_manifest(source)
    interface_payload = dict(manifest.get("interface") or {})
    entry = {
        "name": PLUGIN_NAME,
        "source": {
            "source": "local",
            "path": f"./plugins/{PLUGIN_NAME}",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": str(interface_payload.get("category") or "Coding"),
    }
    description = str(manifest.get("description") or "").strip()
    if description:
        entry["description"] = description
    version = str(manifest.get("version") or "").strip()
    if version:
        entry["version"] = version
    keywords = [
        str(item).strip()
        for item in (manifest.get("keywords") or [])
        if isinstance(item, str) and item.strip()
    ]
    if keywords:
        entry["keywords"] = keywords
    interface: dict[str, Any] = {}
    for field in ("displayName", "shortDescription", "longDescription", "brandColor"):
        value = str(interface_payload.get(field) or "").strip()
        if value:
            interface[field] = value
    default_prompt = [
        str(item).strip()
        for item in (interface_payload.get("defaultPrompt") or [])
        if isinstance(item, str) and item.strip()
    ]
    if default_prompt:
        interface["defaultPrompt"] = default_prompt
    capabilities = [
        str(item).strip()
        for item in (interface_payload.get("capabilities") or [])
        if isinstance(item, str) and item.strip()
    ]
    if capabilities:
        interface["capabilities"] = capabilities
    if interface:
        entry["interface"] = interface
    return entry


def _load_marketplace(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "name": "local-plugins",
            "interface": {
                "displayName": "Local Plugins",
            },
            "plugins": [],
        }
    return json.loads(path.read_text())


def _normalize_marketplace_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    interface = dict(normalized.get("interface") or {})
    current_name = str(normalized.get("name") or "").strip()
    current_display_name = str(interface.get("displayName") or "").strip()
    if not current_name or current_name.endswith("-local"):
        normalized["name"] = "local-plugins"
    if not current_display_name or current_display_name.endswith(" Local Plugins"):
        interface["displayName"] = "Local Plugins"
    normalized["interface"] = interface
    return normalized


def _update_marketplace(path: Path, source: Path) -> dict[str, Any]:
    payload = _normalize_marketplace_metadata(_load_marketplace(path))
    entry = _marketplace_plugin_entry(source)
    plugins = [plugin for plugin in payload.get("plugins", []) if plugin.get("name") != PLUGIN_NAME]
    plugins.append(entry)
    payload["plugins"] = plugins
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def install_plugin(
    source: Path,
    destination: Path,
    marketplace: Path,
    *,
    bin_dir: Path | None = None,
    install_global_command: bool = True,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _copy_plugin(source, destination)
    _write_install_metadata(destination, source, marketplace)
    _write_installed_mcp(destination)
    launcher_payload = install_launchers(destination, bin_dir=bin_dir, install_global_command=install_global_command)
    marketplace_payload = _update_marketplace(marketplace, source)
    return {
        "plugin_name": PLUGIN_NAME,
        "source_root": str(source.resolve()),
        "install_root": str(destination.resolve()),
        "marketplace_path": str(marketplace.resolve()),
        "installed_at": now_iso(),
        "marketplace_plugin_count": len(marketplace_payload.get("plugins", [])),
        **launcher_payload,
    }


def install_plugin_into_codex_home(
    source: Path,
    codex_home: Path,
    *,
    provider_name: str = "local-plugins",
) -> dict[str, Any]:
    resolved_codex_home = codex_home.expanduser().resolve()
    cache_root = codex_cache_install_root(resolved_codex_home, provider_name=provider_name)
    stage_root = codex_tmp_plugin_install_root(resolved_codex_home)
    marketplace = codex_tmp_marketplace_path(resolved_codex_home)

    stage_result = install_plugin(
        source,
        stage_root,
        marketplace,
        install_global_command=False,
    )
    _copy_plugin(source, cache_root)
    _write_install_metadata(cache_root, source, marketplace)
    _write_installed_mcp(cache_root)
    return {
        **stage_result,
        "install_root": str(cache_root),
        "cache_install_root": str(cache_root),
        "stage_install_root": str(stage_root),
        "marketplace_path": str(marketplace),
        "provider_name": provider_name,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or sync AgentiUX Dev into the home-local plugin directory")
    parser.add_argument("--source-plugin-root", default=str(source_plugin_root()))
    parser.add_argument("--install-root", default=str(default_install_root()))
    parser.add_argument("--marketplace-path", default=str(marketplace_path()))
    parser.add_argument("--bin-dir", help="Install the global `agentiux` launcher into this directory.")
    parser.add_argument("--skip-global-command", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = install_plugin(
        Path(args.source_plugin_root).expanduser().resolve(),
        Path(args.install_root).expanduser().resolve(),
        Path(args.marketplace_path).expanduser().resolve(),
        bin_dir=Path(args.bin_dir).expanduser().resolve() if args.bin_dir else None,
        install_global_command=not args.skip_global_command,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
