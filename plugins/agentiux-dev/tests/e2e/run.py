#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from scenarios import build_registry  # noqa: E402
from support import ExecutionContext, failure_payload  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AgentiUX Dev executable E2E scenarios")
    parser.add_argument("--list", action="store_true", help="List available cases and suites")
    parser.add_argument("--case", action="append", default=[], help="Run one specific case id; may be repeated")
    parser.add_argument("--suite", action="append", default=[], help="Run one specific suite id; may be repeated")
    parser.add_argument(
        "--benchmark-task",
        action="append",
        default=[],
        help="Restrict Codex benchmark runs to one benchmark task id; may be repeated",
    )
    parser.add_argument("--json-report", help="Write a consolidated JSON report to this path")
    parser.add_argument("--keep-run-root", action="store_true", help="Keep the temp run root after execution")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failing case")
    return parser.parse_args()


def selected_case_ids(registry: dict[str, object], case_filters: list[str], suite_filters: list[str]) -> list[str]:
    if not case_filters and not suite_filters:
        suite_filters = ["core-full-local"]
    ordered = []
    for case_id, definition in registry.items():
        if case_filters and case_id not in case_filters:
            continue
        if suite_filters and not any(suite in definition.suite_ids for suite in suite_filters):
            continue
        ordered.append(case_id)
    return ordered


def main() -> int:
    args = parse_args()
    if args.benchmark_task:
        os.environ["AGENTIUX_DEV_BENCHMARK_TASKS"] = ",".join(dict.fromkeys(args.benchmark_task))
    registry = build_registry()
    if args.list:
        suite_index: dict[str, list[str]] = {}
        for case_id, definition in registry.items():
            for suite_id in definition.suite_ids:
                suite_index.setdefault(suite_id, []).append(case_id)
        payload = {
            "case_count": len(registry),
            "suite_count": len(suite_index),
            "suites": {suite_id: sorted(case_ids) for suite_id, case_ids in sorted(suite_index.items())},
            "cases": {
                case_id: {
                    "fixture_id": definition.fixture_id,
                    "suite_ids": list(definition.suite_ids),
                    "tags": list(definition.tags),
                    "cleanup_policy": definition.cleanup_policy,
                }
                for case_id, definition in sorted(registry.items())
            },
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    case_ids = selected_case_ids(registry, args.case, args.suite)
    if not case_ids:
        raise SystemExit("No cases matched the provided selection.")

    context = ExecutionContext(keep_run_root=args.keep_run_root)
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    results = []
    failed = False
    try:
        for case_id in case_ids:
            definition = registry[case_id]
            case_started = time.monotonic()
            try:
                summary = definition.run(context) or {}
                status = "passed"
                failure = None
            except BaseException as exc:  # noqa: BLE001
                status = "failed"
                failure = failure_payload(exc)
                summary = {}
                failed = True
            elapsed_ms = round((time.monotonic() - case_started) * 1000, 2)
            case_payload = {
                "case_id": case_id,
                "status": status,
                "elapsed_ms": elapsed_ms,
                "fixture_id": definition.fixture_id,
                "suite_ids": list(definition.suite_ids),
                "tags": list(definition.tags),
                "required_env_roots": list(definition.required_env_roots),
                "cleanup_policy": definition.cleanup_policy,
                "run_root": str(context.run_root),
                "summary": summary,
                "failure": failure,
            }
            results.append(case_payload)
            print(f"[{status}] {case_id} ({elapsed_ms} ms)")
            if failed and args.fail_fast:
                break
    finally:
        total_elapsed_ms = round((time.monotonic() - started_monotonic) * 1000, 2)
        report = {
            "status": "failed" if failed else "passed",
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "elapsed_ms": total_elapsed_ms,
            "run_root": str(context.run_root),
            "selected_case_ids": case_ids,
            "result_count": len(results),
            "passed_count": sum(1 for item in results if item["status"] == "passed"),
            "failed_count": sum(1 for item in results if item["status"] == "failed"),
            "results": results,
        }
        if args.json_report:
            report_path = Path(args.json_report).expanduser().resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "passed": report["passed_count"],
                    "failed": report["failed_count"],
                    "elapsed_ms": report["elapsed_ms"],
                    "run_root": report["run_root"],
                },
                sort_keys=True,
            )
        )
        context.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
