#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentiux_dev_e2e_support import run_external_fixture_suite, timestamp_slug, write_json_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run external-repository e2e coverage for AgentiUX Dev")
    parser.add_argument("--source-plugin-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--e2e-root", default="/tmp/agentiux-dev-e2e")
    parser.add_argument("--run-slug", default=timestamp_slug())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_plugin_root = Path(args.source_plugin_root).expanduser().resolve()
    e2e_root = Path(args.e2e_root).expanduser().resolve()
    run_root = e2e_root / args.run_slug
    if run_root.exists():
        raise SystemExit(f"E2E run root already exists: {run_root}")
    run_root.mkdir(parents=True, exist_ok=True)
    payload = run_external_fixture_suite(source_plugin_root, run_root)
    summary_path = run_root / "e2e-summary.json"
    write_json_file(summary_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
