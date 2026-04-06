#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


SEMANTIC_REPORT_SCHEMA_VERSION = 2


def _fail(message: str) -> None:
    raise SystemExit(message)


def _root_bounds_for_runner(runner: str) -> dict[str, float]:
    if runner == "playwright-visual":
        width, height = 1280.0, 800.0
    elif runner == "detox-visual":
        width, height = 390.0, 844.0
    elif runner == "android-compose-screenshot":
        width, height = 411.0, 891.0
    else:
        width, height = 1024.0, 768.0
    return {
        "left": 0.0,
        "top": 0.0,
        "right": width,
        "bottom": height,
        "width": width,
        "height": height,
    }


def _target_bounds(root_bounds: dict[str, float], *, index: int) -> dict[str, float]:
    left = 24.0 + float(index * 12)
    top = 32.0 + float(index * 18)
    width = max(220.0, root_bounds["width"] - 48.0 - float(index * 8))
    height = 64.0 + float(index * 6)
    return {
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
        "width": width,
        "height": height,
    }


def _style_tokens_for_runner(runner: str) -> dict[str, object]:
    base = {
        "display": "flex",
        "visibility": "visible",
        "opacity": 1,
        "font_size": 16,
    }
    if runner == "playwright-visual":
        return {
            **base,
            "color": "#111827",
            "background_color": "#ffffff",
            "padding_inline": 24,
            "padding_block": 16,
        }
    return {
        **base,
        "color": "#111827",
        "background_color": "#f8fafc",
        "padding_horizontal": 24,
        "padding_vertical": 16,
    }


def _extra_checks_for_runner(runner: str) -> list[str]:
    if runner in {"detox-visual", "android-compose-screenshot", "ios-simulator-capture", "playwright-visual"}:
        return ["overflow_clipping", "computed_styles", "occlusion"]
    return []


def _check_payload(
    check_id: str,
    *,
    artifact_name: str,
    helper_root: Path,
    runner: str,
    case_id: str,
    target_id: str,
    target_bounds: dict[str, float],
    root_bounds: dict[str, float],
    style_tokens: dict[str, object],
) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "case_id": case_id,
        "helper_root": helper_root.as_posix(),
        "target_id": target_id,
    }
    if check_id == "layout_relations":
        diagnostics["layout"] = {
            "bounds": target_bounds,
            "root_bounds": root_bounds,
        }
    elif check_id == "overflow_clipping":
        diagnostics.update(
            {
                "clipped": False,
                "bounds": target_bounds,
                "root_bounds": root_bounds,
                "clipping": {
                    "clipped": False,
                    "bounds": target_bounds,
                    "root_bounds": root_bounds,
                },
            }
        )
    elif check_id == "computed_styles":
        diagnostics["style_tokens"] = style_tokens
    elif check_id == "occlusion":
        diagnostics["metadata"] = {"occluded": False}
    elif check_id == "text_overflow":
        diagnostics["truncated"] = False
    elif check_id == "accessibility_state":
        diagnostics["state"] = {"enabled": True}
    return {
        "check_id": check_id,
        "status": "passed",
        "runner": runner,
        "diagnostics": diagnostics,
        "artifact_paths": [artifact_name],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--helper-file", action="append", default=[])
    parser.add_argument("--artifact-name", required=True)
    args = parser.parse_args()

    required_env = [
        "VERIFICATION_RUN_ID",
        "VERIFICATION_CASE_ID",
        "VERIFICATION_ARTIFACT_DIR",
        "VERIFICATION_HELPER_ROOT",
        "VERIFICATION_HELPER_VERSION",
        "VERIFICATION_SEMANTIC_SPEC_PATH",
        "VERIFICATION_SEMANTIC_REPORT_PATH",
    ]
    env = {name: os.environ.get(name) for name in required_env}
    missing = [name for name, value in env.items() if not value]
    if missing:
        _fail("Missing verification env vars: " + ", ".join(missing))

    repo_root = Path(args.repo_root).resolve()
    helper_root = Path(env["VERIFICATION_HELPER_ROOT"]).resolve()
    expected_helper_root = (repo_root / ".verification" / "helpers").resolve()
    if helper_root != expected_helper_root:
        _fail(f"Expected helper root {expected_helper_root}, got {helper_root}")
    if ".verification/helpers" not in helper_root.as_posix():
        _fail(f"Helper root should be neutral project path, got {helper_root}")
    if (repo_root / ".agentiux").exists():
        _fail("Legacy .agentiux helper root should not exist")

    relative_helper_root = helper_root.relative_to(repo_root).as_posix().lower()
    if "agentiux" in relative_helper_root or "codex" in relative_helper_root:
        _fail(f"Project helper path should not contain branding: {relative_helper_root}")

    for relative in args.helper_file:
        candidate = helper_root / relative
        if not candidate.exists():
            _fail(f"Expected helper file is missing: {candidate}")

    for candidate in helper_root.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in {".js", ".json", ".kt"}:
            continue
        text = candidate.read_text(encoding="utf-8", errors="ignore").lower()
        if "agentiux" in text or "codex" in text:
            _fail(f"Branded string leaked into materialized helper file: {candidate}")

    spec_path = Path(env["VERIFICATION_SEMANTIC_SPEC_PATH"]).resolve()
    report_path = Path(env["VERIFICATION_SEMANTIC_REPORT_PATH"]).resolve()
    artifact_dir = Path(env["VERIFICATION_ARTIFACT_DIR"]).resolve()
    if artifact_dir not in report_path.parents:
        _fail(f"Semantic report should live under artifact dir: {report_path}")

    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if payload.get("runner") != args.runner:
        _fail(f"Spec runner mismatch: {payload.get('runner')} != {args.runner}")
    if payload.get("helper_bundle_version") != env["VERIFICATION_HELPER_VERSION"]:
        _fail("Spec helper bundle version does not match env")
    if Path(payload.get("helper_root") or "").resolve() != helper_root:
        _fail("Spec helper root does not match env helper root")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_dir / args.artifact_name
    artifact_file.write_text(f"{args.runner}:{env['VERIFICATION_CASE_ID']}\n", encoding="utf-8")

    root_bounds = _root_bounds_for_runner(args.runner)
    style_tokens = _style_tokens_for_runner(args.runner)
    case_id = str(env["VERIFICATION_CASE_ID"])
    target_entries = []
    extra_checks = _extra_checks_for_runner(args.runner)
    check_totals = {
        "passed": 0,
        "failed": 0,
        "warning": 0,
        "skipped": 0,
        "not_applicable": 0,
        "unknown": 0,
    }
    for index, target in enumerate(payload.get("targets") or []):
        target_id = str(target.get("target_id") or f"target-{index + 1}")
        bounds = _target_bounds(root_bounds, index=index)
        check_ids = []
        for check_id in [*(payload.get("required_checks") or []), *extra_checks]:
            normalized = str(check_id or "").strip()
            if normalized and normalized not in check_ids:
                check_ids.append(normalized)
        checks = [
            _check_payload(
                check_id,
                artifact_name=artifact_file.name,
                helper_root=helper_root,
                runner=args.runner,
                case_id=case_id,
                target_id=target_id,
                target_bounds=bounds,
                root_bounds=root_bounds,
                style_tokens=style_tokens,
            )
            for check_id in check_ids
        ]
        for check in checks:
            status = str(check.get("status") or "unknown").lower()
            check_totals[status if status in check_totals else "unknown"] += 1
        target_entries.append(
            {
                "target_id": target_id,
                "status": "passed",
                "diagnostics": {
                    "locator_kind": ((target.get("locator") or {}).get("kind")),
                    "auto_scan": bool(payload.get("auto_scan", False)),
                    "bounds": bounds,
                    "root_bounds": root_bounds,
                    "style_tokens": style_tokens,
                },
                "artifact_paths": [artifact_file.name],
                "checks": checks,
            }
        )

    report = {
        "schema_version": SEMANTIC_REPORT_SCHEMA_VERSION,
        "helper_bundle_version": env["VERIFICATION_HELPER_VERSION"],
        "runner": args.runner,
        "case_id": case_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_path": str(report_path),
        "targets": target_entries,
        "summary": {
            "status": "passed",
            "message": f"{args.runner} semantic contract runner completed",
            "required_checks": list(payload.get("required_checks") or []),
            "check_counts": check_totals,
            "target_count": len(target_entries),
            "failed_checks": [],
            "optional_failed_checks": [],
            "reachability_path_count": len(payload.get("reachability_paths") or []),
            "limitation_entry_count": len(payload.get("limitation_entries") or []),
        },
        "reachability_paths": [],
        "limitation_entries": list(payload.get("limitation_entries") or []),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "report_path": str(report_path), "artifact_path": str(artifact_file)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
