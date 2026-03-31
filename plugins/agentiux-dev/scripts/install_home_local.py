#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from agentiux_dev_lib import PLUGIN_NAME, marketplace_path, now_iso, python_launcher_tokens


def source_plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_install_root() -> Path:
    return (Path.home() / "plugins" / PLUGIN_NAME).resolve()


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


def _update_marketplace(path: Path) -> dict[str, Any]:
    payload = _normalize_marketplace_metadata(_load_marketplace(path))
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
        "category": "Coding",
    }
    plugins = [plugin for plugin in payload.get("plugins", []) if plugin.get("name") != PLUGIN_NAME]
    plugins.append(entry)
    payload["plugins"] = plugins
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def install_plugin(source: Path, destination: Path, marketplace: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _copy_plugin(source, destination)
    _write_install_metadata(destination, source, marketplace)
    _write_installed_mcp(destination)
    marketplace_payload = _update_marketplace(marketplace)
    return {
        "plugin_name": PLUGIN_NAME,
        "source_root": str(source.resolve()),
        "install_root": str(destination.resolve()),
        "marketplace_path": str(marketplace.resolve()),
        "installed_at": now_iso(),
        "marketplace_plugin_count": len(marketplace_payload.get("plugins", [])),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or sync AgentiUX Dev into the home-local plugin directory")
    parser.add_argument("--source-plugin-root", default=str(source_plugin_root()))
    parser.add_argument("--install-root", default=str(default_install_root()))
    parser.add_argument("--marketplace-path", default=str(marketplace_path()))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = install_plugin(
        Path(args.source_plugin_root).expanduser().resolve(),
        Path(args.install_root).expanduser().resolve(),
        Path(args.marketplace_path).expanduser().resolve(),
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
