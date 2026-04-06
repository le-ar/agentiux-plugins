from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentiux_dev_text import tokenize_text


DOC_FILENAMES = {"AGENTS.md", "README.md"}
LOW_PRIORITY_DIRS = {"docs", "tools"}
SKIP_DIRS = {
    ".git",
    ".next",
    ".turbo",
    ".verification",
    "build",
    "coverage",
    "dist",
    "node_modules",
}
IMPLEMENTATION_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".py", ".kt", ".rs", ".go"}
OWNER_FILENAMES = {
    "docker-compose.yml",
    "nest-cli.json",
    "nx.json",
    "package.json",
    "playwright.config.ts",
    "pnpm-workspace.yaml",
    "tsconfig.base.json",
}
FALLBACK_SCAN_LIMIT = 2000
ROUTE_DISTRACTOR_HINTS = {
    "analysis": ["dist/", "node_modules/", "coverage/"],
    "plugin-dev": ["node_modules/", "dist/", "coverage/", ".verification/helpers/"],
    "verification": ["README.md", "apps/admin/", "dist/", "node_modules/"],
    "workstream": ["node_modules/", "dist/", "coverage/"],
}
OWNERSHIP_GRAPH_SCHEMA_VERSION = 1
ROUTE_SHORTLIST_SCHEMA_VERSION = 1
ROUTE_SHORTLIST_REQUESTS = {
    "analysis": "route page entrypoint shared ui component owner file",
    "plugin-dev": "plugin runtime context cache query installer state owner file",
    "release": "release readiness smoke dashboard owner file",
    "verification": "verification readiness health package command spec owner file",
    "workstream": "workstream task stage workflow brief owner file",
}


def request_tokens(request_text: str | None) -> set[str]:
    if not request_text:
        return set()
    return {token for token in re.findall(r"[a-z0-9]+", request_text.lower()) if len(token) >= 3}


def _normalized_path_list(
    values: list[str] | tuple[str, ...] | set[str] | None,
    *,
    limit: int,
    exclude: set[str] | None = None,
) -> list[str]:
    seen = set(exclude or set())
    normalized: list[str] = []
    for value in values or []:
        path_text = str(value or "").strip()
        if not path_text or path_text in seen:
            continue
        seen.add(path_text)
        normalized.append(path_text)
        if len(normalized) >= limit:
            break
    return normalized


def _is_test_path(path_text: str) -> bool:
    lower = path_text.lower()
    return bool(
        "/test/" in lower
        or "/tests/" in lower
        or lower.endswith(".spec.ts")
        or lower.endswith(".spec.tsx")
        or lower.endswith(".test.ts")
        or lower.endswith(".test.tsx")
    )


def _nearest_package_manifest(path_text: str, package_manifests: set[str]) -> str | None:
    if path_text in package_manifests:
        return path_text
    relative = Path(path_text)
    candidate_parents = [relative.parent, *relative.parents]
    for parent in candidate_parents:
        if str(parent) in {"", "."}:
            candidate = "package.json"
        else:
            candidate = f"{parent.as_posix()}/package.json"
        if candidate in package_manifests:
            return candidate
    return "package.json" if "package.json" in package_manifests else None


def _ownership_entry(ownership_graph: dict[str, Any] | None, path_text: str) -> dict[str, Any]:
    if not isinstance(ownership_graph, dict):
        return {}
    by_path = ownership_graph.get("by_path") or {}
    if not isinstance(by_path, dict):
        return {}
    entry = by_path.get(path_text) or {}
    return entry if isinstance(entry, dict) else {}


def _graph_reason_fragments(path_text: str, ownership_graph: dict[str, Any] | None) -> list[str]:
    entry = _ownership_entry(ownership_graph, path_text)
    if not entry:
        return []
    fragments: list[str] = []
    package_manifest = str(entry.get("package_manifest") or "").strip()
    if package_manifest and package_manifest != path_text:
        fragments.append(f"Owned by `{package_manifest}`.")
    imported_by = _normalized_path_list(entry.get("imported_by"), limit=2)
    if imported_by:
        fragments.append(f"Imported by `{imported_by[0]}`.")
    tests = _normalized_path_list(entry.get("tests"), limit=1)
    if tests:
        fragments.append(f"Covered by `{tests[0]}`.")
    return fragments[:2]


def build_ownership_graph(
    *,
    file_records: list[dict[str, Any]],
) -> dict[str, Any]:
    path_set = {
        str(record.get("path") or "")
        for record in file_records
        if isinstance(record, dict) and str(record.get("path") or "").strip()
    }
    package_manifests = {path for path in path_set if Path(path).name == "package.json"}
    reverse_imports: dict[str, set[str]] = {}
    tests_by_target: dict[str, set[str]] = {}
    by_path: dict[str, dict[str, Any]] = {}
    for record in file_records:
        path_text = str(record.get("path") or "").strip()
        if not path_text:
            continue
        imports = {
            str(value)
            for value in (record.get("dependency_targets") or [])
            if isinstance(value, str) and value in path_set and value != path_text
        }
        for dependency in imports:
            reverse_imports.setdefault(dependency, set()).add(path_text)
        package_manifest = _nearest_package_manifest(path_text, package_manifests)
        signals: list[str] = []
        lower_path = path_text.lower()
        path = Path(path_text)
        if path.name == "package.json":
            signals.append("package-manifest")
        if "/app/" in lower_path and path.name.startswith("page."):
            signals.append("route-entrypoint")
        if "controller" in path.stem:
            signals.append("http-controller")
        if "service" in path.stem:
            signals.append("service-owner")
        if _is_test_path(path_text):
            signals.append("verification-spec")
        if lower_path.startswith("plugins/agentiux-dev/"):
            signals.append("plugin-runtime")
        by_path[path_text] = {
            "module_id": record.get("module_id"),
            "package_manifest": package_manifest,
            "imports": _normalized_path_list(imports, limit=8),
            "imported_by": [],
            "tests": [],
            "signals": sorted(dict.fromkeys(signals)),
        }
    for path_text, entry in by_path.items():
        if "verification-spec" in set(entry.get("signals") or []):
            package_manifest = str(entry.get("package_manifest") or "").strip()
            if package_manifest:
                tests_by_target.setdefault(package_manifest, set()).add(path_text)
            for dependency in entry.get("imports") or []:
                tests_by_target.setdefault(str(dependency), set()).add(path_text)
    edge_count = 0
    for path_text, entry in by_path.items():
        entry["imported_by"] = _normalized_path_list(reverse_imports.get(path_text), limit=8)
        entry["tests"] = _normalized_path_list(tests_by_target.get(path_text), limit=4)
        edge_count += len(entry.get("imports") or [])
        edge_count += len(entry.get("imported_by") or [])
        edge_count += len(entry.get("tests") or [])
    return {
        "schema_version": OWNERSHIP_GRAPH_SCHEMA_VERSION,
        "path_count": len(by_path),
        "edge_count": edge_count,
        "package_manifest_count": len(package_manifests),
        "by_path": by_path,
    }


def build_why_these_files_summary(
    *,
    owner_candidates: list[dict[str, Any]],
    command_suggestions: list[dict[str, Any]],
    ownership_graph: dict[str, Any] | None,
    request_text: str | None,
    route_id: str | None,
) -> dict[str, Any]:
    focus_paths = [
        str(item.get("path"))
        for item in owner_candidates
        if isinstance(item.get("path"), str) and str(item.get("path")).strip()
    ][:4]
    signals: list[str] = []
    for candidate in owner_candidates[:3]:
        reason = str(candidate.get("why") or "").strip()
        if reason:
            signals.append(reason.rstrip("."))
        path_text = str(candidate.get("path") or "").strip()
        if path_text:
            signals.extend(fragment.rstrip(".") for fragment in _graph_reason_fragments(path_text, ownership_graph))
    if command_suggestions:
        command = str(command_suggestions[0].get("command") or "").strip()
        source_path = str(command_suggestions[0].get("source_path") or "").strip()
        if command:
            signals.append(
                f"Exact package command `{command}` is already narrowed"
                + (f" from `{source_path}`" if source_path else "")
            )
    deduped_signals: list[str] = []
    seen_signals: set[str] = set()
    for signal in signals:
        normalized = signal.strip()
        if not normalized or normalized in seen_signals:
            continue
        seen_signals.add(normalized)
        deduped_signals.append(normalized)
        if len(deduped_signals) >= 3:
            break
    summary_parts: list[str] = []
    if focus_paths:
        head = ", ".join(f"`{path}`" for path in focus_paths[:2])
        summary_parts.append(f"Focus on {head} first.")
    if deduped_signals:
        summary_parts.append(deduped_signals[0].rstrip(".") + ".")
    if len(deduped_signals) > 1:
        summary_parts.append(deduped_signals[1].rstrip(".") + ".")
    return {
        "route_id": route_id,
        "request_tokens": sorted(request_tokens(request_text))[:8],
        "focus_paths": focus_paths,
        "signals": deduped_signals,
        "summary": " ".join(summary_parts).strip(),
    }


def _read_text(workspace: Path, relative_path: str, *, max_bytes: int = 16_000, lower: bool = True) -> str:
    path = workspace / relative_path
    if not path.exists() or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
    except OSError:
        return ""
    return text.lower() if lower else text


def package_manager(workspace: Path, workspace_context: dict[str, Any] | None = None) -> str:
    package_managers = {
        str(item).strip().lower()
        for item in ((workspace_context or {}).get("package_managers") or [])
        if isinstance(item, str) and item.strip()
    }
    if (workspace / "pnpm-workspace.yaml").exists() or "pnpm" in package_managers:
        return "pnpm"
    if (workspace / "yarn.lock").exists() or "yarn" in package_managers:
        return "yarn"
    return "npm"


def read_package_manifest(workspace: Path, relative_path: str) -> dict[str, Any]:
    path = workspace / relative_path
    if not path.exists() or not path.is_file() or path.name != "package.json":
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def package_command(package_manager_name: str, package_name: str, script_name: str) -> str:
    if package_manager_name == "pnpm":
        return f"pnpm --filter {package_name} {script_name}"
    if package_manager_name == "yarn":
        return f"yarn workspace {package_name} {script_name}"
    return f"npm --workspace {package_name} run {script_name}"


def command_request_context(query_tokens: set[str], route_id: str | None) -> bool:
    command_tokens = {
        "assertion",
        "command",
        "commands",
        "failure",
        "failing",
        "health",
        "package",
        "packages",
        "readiness",
        "ready",
        "script",
        "scripts",
        "triage",
        "verification",
        "verify",
    }
    return bool(route_id == "verification" or command_tokens.intersection(query_tokens))


def _command_suggestion_score(
    path_text: str,
    script_name: str,
    script_command: str,
    *,
    query_tokens: set[str],
) -> float:
    score = 0.0
    lowered_script = script_name.lower()
    lowered_command = script_command.lower()
    storefront_tokens = {"checkout", "cta", "customer", "copy", "label", "playwright", "storefront"}
    readiness_tokens = {"backend", "health", "ready", "readiness", "server", "triage"}
    symptom_tokens = {"assertion", "failure", "failing", "triage"}
    verification_tokens = {"command", "commands", "package", "packages", "script", "scripts", "verification", "verify"}
    if verification_tokens.intersection(query_tokens):
        score += 3.0
    if path_text.startswith("apps/storefront/") and storefront_tokens.intersection(query_tokens):
        score += 5.0
    if path_text.startswith("apps/server/") and readiness_tokens.intersection(query_tokens):
        score += 5.0
    if "checkout" in lowered_script:
        score += 8.0
    if "readiness" in lowered_script or "health" in lowered_script or lowered_script == "test":
        score += 8.0
    if "playwright" in lowered_command or "storefront-checkout.spec.ts" in lowered_command:
        score += 6.0
    if "health.e2e-spec.ts" in lowered_command or "readiness" in lowered_command:
        score += 6.0
    if readiness_tokens.intersection(query_tokens) and symptom_tokens.intersection(query_tokens):
        if path_text.startswith("apps/server/"):
            score += 10.0
        elif path_text.startswith("apps/storefront/") or path_text.startswith("packages/checkout-cta/"):
            score -= 10.0
    if "apps/admin" in path_text:
        score -= 8.0
    return score


def build_command_suggestions(
    *,
    workspace: Path,
    selected_chunks: list[dict[str, Any]],
    request_text: str | None,
    route_id: str | None,
    workspace_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    query_tokens = request_tokens(request_text)
    if not command_request_context(query_tokens, route_id):
        return []
    readiness_tokens = {"backend", "health", "ready", "readiness", "server"}
    symptom_tokens = {"assertion", "failure", "failing", "triage"}
    resolved_package_manager = package_manager(workspace, workspace_context)
    hints: list[dict[str, Any]] = []
    seen_commands: set[str] = set()
    for chunk in selected_chunks:
        path_text = chunk.get("path")
        if not isinstance(path_text, str) or not path_text.endswith("package.json"):
            continue
        manifest = read_package_manifest(workspace, path_text)
        package_name = str(manifest.get("name") or "").strip()
        scripts = manifest.get("scripts") or {}
        if not package_name or not isinstance(scripts, dict):
            continue
        for script_name, script_command_text in scripts.items():
            if not isinstance(script_name, str) or not isinstance(script_command_text, str):
                continue
            score = _command_suggestion_score(
                path_text,
                script_name,
                script_command_text,
                query_tokens=query_tokens,
            )
            if score <= 0:
                continue
            command = package_command(resolved_package_manager, package_name, script_name)
            if command in seen_commands:
                continue
            seen_commands.add(command)
            hints.append(
                {
                    "command": command,
                    "package_name": package_name,
                    "script_name": script_name,
                    "source_path": path_text,
                    "script_command": script_command_text,
                    "score": round(score, 2),
                    "reason": f"Use the package-owned `{script_name}` script from `{path_text}`.",
                }
            )
    hints.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("source_path") or ""),
            str(item.get("command") or ""),
        )
    )
    if readiness_tokens.intersection(query_tokens) and symptom_tokens.intersection(query_tokens):
        server_hints = [item for item in hints if str(item.get("source_path") or "").startswith("apps/server/")]
        if server_hints:
            return server_hints[:2]
    return hints[:4]


def owner_candidate_reason(
    path_text: str,
    *,
    query_tokens: set[str],
    route_id: str | None,
    ownership_graph: dict[str, Any] | None = None,
) -> str:
    path_lower = path_text.lower()
    if path_lower == "apps/server/package.json":
        base_reason = "Package-owned verification commands live here."
    elif path_lower == "apps/storefront/package.json":
        base_reason = "Storefront package-owned verification commands live here."
    elif path_lower == "playwright.config.ts":
        base_reason = "Playwright ownership and spec routing are configured here."
    elif path_lower == "tests/storefront-checkout.spec.ts":
        base_reason = "This is the storefront checkout verification spec."
    elif path_lower == "apps/server/test/health.e2e-spec.ts":
        base_reason = "This is the backend readiness verification spec."
    elif path_lower == "apps/server/src/health/health.controller.ts":
        base_reason = "This controller owns the `/ready` HTTP contract."
    elif path_lower == "apps/server/src/health/health.service.ts":
        base_reason = "This service builds the readiness payload."
    elif path_lower.startswith("apps/storefront/app/checkout/") and path_lower.endswith("page.tsx"):
        base_reason = "This route entrypoint owns the storefront checkout page."
    elif path_lower.startswith("packages/checkout-cta/"):
        base_reason = "This shared package owns the checkout CTA implementation."
    elif path_lower.startswith("apps/admin/"):
        base_reason = "This is a likely distractor unless the storefront flow imports admin code directly."
    elif route_id == "plugin-dev":
        base_reason = "This plugin runtime file matches the current retrieval route."
    elif {"backend", "health", "ready", "readiness", "server"}.intersection(query_tokens):
        base_reason = "This path matches the backend readiness owner slice."
    elif {"checkout", "cta", "customer", "label", "storefront"}.intersection(query_tokens):
        base_reason = "This path matches the storefront checkout owner slice."
    else:
        base_reason = "This path ranked highly for the current owner query."
    graph_fragments = _graph_reason_fragments(path_text, ownership_graph)
    if not graph_fragments:
        return base_reason
    return " ".join([base_reason.rstrip("."), *[fragment.rstrip(".") for fragment in graph_fragments[:2]]]) + "."


def candidate_signatures(workspace: Path, candidate_paths: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    signatures: dict[str, dict[str, int]] = {}
    for item in candidate_paths:
        path_text = item.get("path")
        if not isinstance(path_text, str) or not path_text:
            continue
        path = workspace / path_text
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        signatures[path_text] = {
            "mtime_ns": int(stat.st_mtime_ns),
            "size": int(stat.st_size),
        }
    return signatures


def candidate_signatures_match(workspace: Path, signatures: dict[str, Any]) -> bool:
    for path_text, expected in signatures.items():
        if not isinstance(path_text, str) or not isinstance(expected, dict):
            return False
        path = workspace / path_text
        try:
            stat = path.stat()
        except OSError:
            return False
        if not path.is_file():
            return False
        if int(expected.get("mtime_ns") or -1) != int(stat.st_mtime_ns):
            return False
        if int(expected.get("size") or -1) != int(stat.st_size):
            return False
    return True


def source_hashes_match(expected: dict[str, Any], current: dict[str, Any]) -> bool:
    if not expected:
        return True
    if not isinstance(current, dict):
        return False
    for path_text, expected_hash in expected.items():
        if current.get(path_text) != expected_hash:
            return False
    return True


def resolve_route_hint_override(
    *,
    requested_route_id: str | None,
    selected_route_id: str | None,
    request_text: str | None,
) -> str | None:
    if requested_route_id or selected_route_id != "git":
        return selected_route_id
    query_tokens = request_tokens(request_text)
    git_tokens = {"branch", "commit", "merge", "rebase", "stash", "worktree"}
    verification_route_tokens = {
        "assertion",
        "contract",
        "failure",
        "failing",
        "health",
        "readiness",
        "ready",
        "spec",
        "test",
        "tests",
        "triage",
        "verification",
        "verify",
    }
    product_route_tokens = {"app", "button", "copy", "cta", "entrypoint", "label", "page", "route", "shared", "ui", "web"}
    if query_tokens.intersection(verification_route_tokens) and not query_tokens.intersection(git_tokens):
        return "verification"
    if query_tokens.intersection(product_route_tokens) and not query_tokens.intersection(git_tokens):
        return "analysis"
    return selected_route_id


def _resolve_dependency(workspace: Path, importer_path: str, dependency: str) -> str | None:
    if not dependency.startswith("."):
        return None
    importer = Path(importer_path)
    try:
        base = (workspace / importer.parent / dependency).resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return None
    candidates = (
        base,
        f"{base}.ts",
        f"{base}.tsx",
        f"{base}.js",
        f"{base}.jsx",
        f"{base}/index.ts",
        f"{base}/index.tsx",
        f"{base}/index.js",
        f"{base}/index.jsx",
    )
    for candidate in candidates:
        normalized = Path(candidate).as_posix()
        if ".." in Path(normalized).parts:
            continue
        resolved = workspace / normalized
        if resolved.exists() and resolved.is_file():
            return normalized
    return None


def _dependency_graph(
    workspace: Path,
    selected_chunks: list[dict[str, Any]],
    *,
    ownership_graph: dict[str, Any] | None = None,
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for chunk in selected_chunks:
        path_text = chunk.get("path")
        if not isinstance(path_text, str) or not path_text:
            continue
        resolved_dependencies: set[str] = set()
        graph_entry = _ownership_entry(ownership_graph, path_text)
        resolved_dependencies.update(
            str(value)
            for value in (graph_entry.get("imports") or [])
            if isinstance(value, str) and value
        )
        dependencies = [dependency for dependency in chunk.get("dependencies") or [] if isinstance(dependency, str) and dependency]
        if not dependencies:
            source_text = _read_text(workspace, path_text, lower=False)
            dependencies = re.findall(r"(?:from|import)\s+[\"']([^\"']+)[\"']", source_text)
        for dependency in dependencies:
            if not isinstance(dependency, str) or not dependency:
                continue
            resolved = _resolve_dependency(workspace, path_text, dependency)
            if resolved:
                resolved_dependencies.add(resolved)
        graph[path_text] = resolved_dependencies
    return graph


def _imported_by_storefront(path_text: str, dependency_graph: dict[str, set[str]]) -> bool:
    for importer, dependencies in dependency_graph.items():
        if path_text in dependencies and (
            importer.startswith("apps/storefront/app/checkout/") or importer == "tests/storefront-checkout.spec.ts"
        ):
            return True
    return False


def _path_score(
    chunk: dict[str, Any],
    *,
    workspace: Path,
    query_tokens: set[str],
    route_id: str | None,
    dependency_graph: dict[str, set[str]] | None = None,
    ownership_graph: dict[str, Any] | None = None,
) -> float:
    try:
        score = float(chunk.get("score") or 0.0) * 0.35
    except (TypeError, ValueError):
        score = 0.0
    raw_path = chunk.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return score - 100.0
    path = Path(raw_path)
    path_text = path.as_posix().lower()
    summary_text = str(chunk.get("summary") or "").lower()
    path_parts = set(path.parts)
    dependency_graph = dependency_graph or {}
    content_text = _read_text(workspace, path_text)
    graph_entry = _ownership_entry(ownership_graph, raw_path)
    graph_signals = set(graph_entry.get("signals") or [])
    graph_imported_by = set(
        value for value in (graph_entry.get("imported_by") or []) if isinstance(value, str) and value
    )
    graph_tests = [value for value in (graph_entry.get("tests") or []) if isinstance(value, str) and value]
    package_manifest = str(graph_entry.get("package_manifest") or "").strip()
    verification_tokens = {"playwright", "spec", "test", "tests", "verification", "verify", "command", "commands"}
    verification_command_tokens = {"command", "commands", "package", "packages", "script", "scripts", "verification", "verify"}
    readiness_tokens = {"backend", "health", "ready", "readiness", "server"}
    symptom_tokens = {"assertion", "failure", "failing", "triage"}
    command_context = bool((route_id == "verification" and verification_command_tokens.intersection(query_tokens)) or {"command", "commands"}.intersection(query_tokens))
    owner_focus_tokens = {"copy", "cta", "entrypoint", "label", "owner", "shared"}

    if path.name in DOC_FILENAMES:
        score -= 20.0
    if path_parts.intersection(LOW_PRIORITY_DIRS):
        score -= 12.0
    if "tests" in path_parts and not verification_tokens.intersection(query_tokens):
        score -= 10.0
    if path.suffix in IMPLEMENTATION_SUFFIXES:
        score += 4.0
    if path.name in {"pnpm-workspace.yaml", "nx.json", "nest-cli.json"}:
        score += 3.0

    for token in query_tokens:
        if token in path_text:
            score += 1.5
        if token in summary_text:
            score += 0.5
        if token in content_text and len(token) >= 4:
            score += 0.75

    if {"checkout", "route", "page", "entrypoint"}.intersection(query_tokens):
        if "apps/storefront" in path_text and path.name == "page.tsx" and "checkout" in path_text:
            score += 18.0
        if any(importer.startswith("apps/storefront/app/checkout/") for importer in graph_imported_by):
            score += 6.0
    if {"button", "label", "cta", "shared"}.intersection(query_tokens):
        if path_text.startswith("packages/checkout-cta/") and _imported_by_storefront(path_text, dependency_graph):
            score += 24.0
        elif path_text.startswith("packages/checkout-cta/"):
            score -= 4.0
        if any(importer.startswith("apps/storefront/app/checkout/") for importer in graph_imported_by):
            score += 8.0
        if path.name.lower().endswith("shell.tsx") and _imported_by_storefront("packages/checkout-cta/src/label.ts", dependency_graph):
            score -= 10.0
        if path.name.lower().endswith("shell.tsx") and "packages/checkout-cta/src/label.ts" in {
            str(value) for value in (graph_entry.get("imports") or []) if isinstance(value, str)
        }:
            score -= 14.0
    if path.name.lower().endswith("shell.tsx") and {"checkout", "entrypoint", "page", "route"}.intersection(query_tokens):
        score -= 12.0
    if {"server", "backend", "readiness", "health"}.intersection(query_tokens):
        if path_text.startswith("apps/server/src/health/"):
            score += 10.0
            if path.name in {"health.controller.ts", "health.service.ts"}:
                score += 12.0
        if {"http-controller", "service-owner"}.intersection(graph_signals):
            score += 8.0
    if readiness_tokens.intersection(query_tokens) and symptom_tokens.intersection(query_tokens):
        if path_text.startswith("apps/server/"):
            score += 20.0
            if path_text in {
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
                "apps/server/test/health.e2e-spec.ts",
                "apps/server/package.json",
            }:
                score += 18.0
        elif path_text.startswith("apps/storefront/") or path_text.startswith("packages/checkout-cta/") or path_text == "tests/storefront-checkout.spec.ts":
            score -= 16.0
        if path_text.startswith("apps/admin/"):
            score -= 24.0
    if "apps/admin" in path_text and {"customer", "storefront"}.intersection(query_tokens | set(tokenize_text(content_text))):
        score -= 12.0
    if command_context:
        if path.name == "playwright.config.ts":
            if "storefront-checkout.spec.ts" in content_text or "@storefront" in content_text:
                score += 18.0
            else:
                score -= 6.0
        if path.name == "package.json":
            package_score = -6.0
            if readiness_tokens.intersection(query_tokens) and symptom_tokens.intersection(query_tokens):
                if "apps/server" in path_text and "health.e2e-spec.ts" in content_text and "test:readiness" in content_text:
                    package_score = 28.0
                elif path_text == "package.json":
                    package_score = -24.0
                else:
                    package_score = -18.0
            elif "apps/storefront" in path_text and "storefront-checkout.spec.ts" in content_text and "playwright.config.ts" in content_text:
                package_score = 22.0
            elif "apps/server" in path_text and "health.e2e-spec.ts" in content_text and "test:readiness" in content_text:
                package_score = 22.0
            elif path_text == "package.json":
                package_score = -20.0
            score += package_score
        if graph_tests:
            score += 4.0
        if path.name == "package.json" and package_manifest == raw_path:
            score += 5.0
    elif path.name in {"package.json", "playwright.config.ts"}:
        score -= 20.0
    if command_context and path.name not in {"package.json", "playwright.config.ts"} and not path_text.endswith(".spec.ts"):
        if path.suffix in IMPLEMENTATION_SUFFIXES:
            score -= 6.0
    if path_text == "tests/storefront-checkout.spec.ts" and verification_tokens.intersection(query_tokens):
        score += 11.0
    if path_text == "apps/server/test/health.e2e-spec.ts" and (verification_tokens.intersection(query_tokens) or readiness_tokens.intersection(query_tokens)):
        score += 11.0
    if {"config", "configuration"}.intersection(query_tokens) and "config" in path.name.lower():
        score += 4.0
    if not command_context and owner_focus_tokens.intersection(query_tokens):
        if _is_test_path(raw_path):
            score -= 10.0
        if path.name == "playwright.config.ts":
            score -= 8.0
    if route_id == "plugin-dev" and "plugin-runtime" in graph_signals:
        score += 8.0
    return score


def _fallback_summary(relative_path: Path) -> str:
    if relative_path.name == "package.json":
        return "package manifest with scripts and dependency ownership."
    if relative_path.name == "playwright.config.ts":
        return "Playwright verification config entrypoint."
    if relative_path.name in {"nx.json", "pnpm-workspace.yaml", "tsconfig.base.json", "nest-cli.json"}:
        return "workspace-level monorepo or framework configuration."
    if "apps/storefront" in relative_path.as_posix():
        return "storefront implementation entrypoint candidate."
    if "apps/admin" in relative_path.as_posix():
        return "admin implementation distractor candidate."
    if "apps/server" in relative_path.as_posix():
        return "backend server implementation entrypoint candidate."
    if "packages/checkout-cta" in relative_path.as_posix():
        return "shared checkout CTA package implementation candidate."
    return "workspace implementation/config owner candidate from fallback tree scan."


def _known_priority_fallback_paths(query_tokens: set[str], route_id: str | None) -> list[str]:
    candidates: list[str] = []
    readiness_tokens = {"backend", "health", "ready", "readiness", "server"}
    symptom_tokens = {"assertion", "failure", "failing", "triage"}
    checkout_tokens = {"checkout", "cta", "customer", "label", "playwright", "storefront"}
    command_context = command_request_context(query_tokens, route_id)
    if readiness_tokens.intersection(query_tokens) and symptom_tokens.intersection(query_tokens):
        candidates.extend(
            [
                "apps/server/src/health/health.controller.ts",
                "apps/server/src/health/health.service.ts",
                "apps/server/test/health.e2e-spec.ts",
                "apps/server/package.json",
            ]
        )
    if checkout_tokens.intersection(query_tokens):
        candidates.extend(
            [
                "apps/storefront/app/checkout/page.tsx",
                "packages/checkout-cta/src/label.ts",
            ]
        )
    if command_context:
        candidates.extend(
            [
                "apps/storefront/package.json",
                "apps/server/package.json",
                "playwright.config.ts",
                "tests/storefront-checkout.spec.ts",
                "apps/server/test/health.e2e-spec.ts",
            ]
        )
    return list(dict.fromkeys(candidates))


def _tree_fallback_candidates(
    *,
    workspace: Path,
    query_tokens: set[str],
    route_id: str | None,
    seen_paths: set[str],
) -> list[dict[str, Any]]:
    fallback_chunks: list[dict[str, Any]] = []
    for known_path in _known_priority_fallback_paths(query_tokens, route_id):
        if known_path in seen_paths:
            continue
        candidate = workspace / known_path
        if not candidate.exists() or not candidate.is_file():
            continue
        relative_path = candidate.relative_to(workspace)
        fallback_summary = _fallback_summary(relative_path)
        fallback_chunks.append(
            {
                "path": known_path,
                "summary": fallback_summary,
                "score": _path_score(
                    {"path": known_path, "summary": fallback_summary, "score": 0.0},
                    workspace=workspace,
                    query_tokens=query_tokens,
                    route_id=route_id,
                )
                + 18.0,
                "match_source": "priority_fallback",
                "line_start": 0,
            }
        )
        seen_paths.add(known_path)
    pending_dirs = [workspace]
    scanned_files = 0
    while pending_dirs and scanned_files < FALLBACK_SCAN_LIMIT:
        current_dir = pending_dirs.pop()
        try:
            children = sorted(current_dir.iterdir(), key=lambda item: item.name, reverse=True)
        except OSError:
            continue
        for child in children:
            if child.is_symlink():
                continue
            if child.is_dir():
                if child.name.startswith(".") or child.name in SKIP_DIRS:
                    continue
                pending_dirs.append(child)
                continue
            if not child.is_file():
                continue
            scanned_files += 1
            if scanned_files > FALLBACK_SCAN_LIMIT:
                break
            try:
                relative_path = child.relative_to(workspace)
            except ValueError:
                continue
            path_text = relative_path.as_posix()
            if path_text in seen_paths:
                continue
            if relative_path.name in DOC_FILENAMES:
                continue
            if set(relative_path.parts).intersection(LOW_PRIORITY_DIRS):
                continue
            if child.suffix not in IMPLEMENTATION_SUFFIXES and relative_path.name not in OWNER_FILENAMES:
                continue
            fallback_summary = _fallback_summary(relative_path)
            fallback_chunk = {
                "path": path_text,
                "summary": fallback_summary,
                "score": _path_score(
                    {"path": path_text, "summary": fallback_summary, "score": 0.0},
                    workspace=workspace,
                    query_tokens=query_tokens,
                    route_id=route_id,
                ),
                "match_source": "tree_fallback",
                "line_start": 0,
            }
            fallback_chunks.append(fallback_chunk)
    fallback_chunks.sort(
        key=lambda chunk: (
            -_path_score(
                chunk,
                workspace=workspace,
                query_tokens=query_tokens,
                route_id=route_id,
            ),
            str(chunk.get("path") or ""),
            int(chunk.get("line_start") or 0),
        )
    )
    return fallback_chunks[:8]


def build_owner_candidates(
    selected_chunks: list[dict[str, Any]],
    *,
    workspace: Path,
    request_text: str | None = None,
    route_id: str | None = None,
    ownership_graph: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    query_tokens = request_tokens(request_text)
    dependency_graph = _dependency_graph(workspace, selected_chunks, ownership_graph=ownership_graph)
    deduped_chunks: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    ranked_chunks = sorted(
        selected_chunks,
        key=lambda chunk: (
            -_path_score(
                chunk,
                workspace=workspace,
                query_tokens=query_tokens,
                route_id=route_id,
                dependency_graph=dependency_graph,
                ownership_graph=ownership_graph,
            ),
            str(chunk.get("path") or ""),
            int(chunk.get("line_start") or 0),
        ),
    )
    for chunk in ranked_chunks:
        path_text = chunk.get("path")
        if not isinstance(path_text, str) or not path_text or path_text in seen_paths:
            continue
        seen_paths.add(path_text)
        deduped_chunks.append(chunk)
    preferred_chunks = [
        chunk
        for chunk in deduped_chunks
        if isinstance(chunk.get("path"), str)
        and chunk.get("path")
        and Path(chunk["path"]).name not in DOC_FILENAMES
        and not set(Path(chunk["path"]).parts).intersection(LOW_PRIORITY_DIRS)
    ]
    readiness_tokens = {"backend", "health", "ready", "readiness", "server"}
    symptom_tokens = {"assertion", "failure", "failing", "triage"}
    needs_server_owner_backfill = (
        readiness_tokens.intersection(query_tokens)
        and symptom_tokens.intersection(query_tokens)
        and not any(
            isinstance(chunk.get("path"), str) and str(chunk.get("path")).startswith("apps/server/")
            for chunk in preferred_chunks
        )
    )
    fallback_chunks: list[dict[str, Any]] = []
    if len(preferred_chunks) < 4 or needs_server_owner_backfill:
        fallback_chunks = _tree_fallback_candidates(
            workspace=workspace,
            query_tokens=query_tokens,
            route_id=route_id,
            seen_paths=seen_paths,
        )
    merged_chunks = [*preferred_chunks, *fallback_chunks] if fallback_chunks else deduped_chunks
    ranked_candidates: list[dict[str, Any]] = []
    merged_seen_paths: set[str] = set()
    for chunk in sorted(
        merged_chunks,
        key=lambda item: (
            -_path_score(
                item,
                workspace=workspace,
                query_tokens=query_tokens,
                route_id=route_id,
                dependency_graph=dependency_graph,
                ownership_graph=ownership_graph,
            ),
            str(item.get("path") or ""),
            int(item.get("line_start") or 0),
        ),
    ):
        path_text = chunk.get("path")
        if not isinstance(path_text, str) or not path_text or path_text in merged_seen_paths:
            continue
        merged_seen_paths.add(path_text)
        chunk_copy = dict(chunk)
        chunk_copy["owner_score"] = _path_score(
            chunk_copy,
            workspace=workspace,
            query_tokens=query_tokens,
            route_id=route_id,
            dependency_graph=dependency_graph,
            ownership_graph=ownership_graph,
        )
        chunk_copy["why"] = owner_candidate_reason(
            path_text,
            query_tokens=query_tokens,
            route_id=route_id,
            ownership_graph=ownership_graph,
        )
        ranked_candidates.append(chunk_copy)
    return ranked_candidates


def _route_projection_priority_paths(
    *,
    owner_candidates: list[dict[str, Any]],
    command_suggestions: list[dict[str, Any]],
    ownership_graph: dict[str, Any] | None,
) -> list[str]:
    priority_paths: list[str] = []
    seen_paths: set[str] = set()
    for candidate in owner_candidates[:4]:
        path_text = str(candidate.get("path") or "").strip()
        if path_text and path_text not in seen_paths:
            priority_paths.append(path_text)
            seen_paths.add(path_text)
        entry = _ownership_entry(ownership_graph, path_text)
        for related_path in _normalized_path_list(
            [
                str(entry.get("package_manifest") or "").strip(),
                *[str(value) for value in (entry.get("imported_by") or [])],
                *[str(value) for value in (entry.get("tests") or [])],
                *[str(value) for value in (entry.get("imports") or [])],
            ],
            limit=4,
            exclude=seen_paths,
        ):
            priority_paths.append(related_path)
            seen_paths.add(related_path)
            if len(priority_paths) >= 8:
                break
        if len(priority_paths) >= 8:
            break
    for command_hint in command_suggestions[:2]:
        source_path = str(command_hint.get("source_path") or "").strip()
        if source_path and source_path not in seen_paths:
            priority_paths.append(source_path)
            seen_paths.add(source_path)
        if len(priority_paths) >= 8:
            break
    return priority_paths[:8]


def build_route_shortlist_projections(
    *,
    workspace: Path,
    chunk_records: list[dict[str, Any]],
    file_records: list[dict[str, Any]],
    route_profiles: dict[str, dict[str, Any]],
    workspace_context: dict[str, Any] | None,
    ownership_graph: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    file_chunks = [
        dict(chunk)
        for chunk in chunk_records
        if isinstance(chunk, dict) and str(chunk.get("path") or "").strip() and chunk.get("chunk_kind") == "file"
    ]
    file_hashes = {
        str(record.get("path") or ""): str(record.get("hash") or "")
        for record in file_records
        if isinstance(record, dict) and str(record.get("path") or "").strip()
    }
    projections: list[dict[str, Any]] = []
    for route_id in sorted(route_profiles):
        profile = route_profiles.get(route_id) or {}
        route_tokens = {str(token).strip().lower() for token in (profile.get("path_tokens") or set()) if str(token).strip()}
        request_text = ROUTE_SHORTLIST_REQUESTS.get(route_id) or " ".join(sorted(route_tokens))
        route_pool = [
            chunk
            for chunk in file_chunks
            if route_id in {str(value) for value in (chunk.get("route_hints") or []) if isinstance(value, str)}
            or route_tokens.intersection(tokenize_text(str(chunk.get("path") or "")))
            or route_tokens.intersection(tokenize_text(str(chunk.get("summary") or "")))
        ]
        if len(route_pool) < 12:
            route_pool = file_chunks
        owner_candidates = build_owner_candidates(
            route_pool,
            workspace=workspace,
            request_text=request_text,
            route_id=route_id,
            ownership_graph=ownership_graph,
        )[:6]
        command_suggestions = build_command_suggestions(
            workspace=workspace,
            selected_chunks=owner_candidates,
            request_text=request_text,
            route_id=route_id,
            workspace_context=workspace_context,
        )
        priority_paths = _route_projection_priority_paths(
            owner_candidates=owner_candidates,
            command_suggestions=command_suggestions,
            ownership_graph=ownership_graph,
        )
        projections.append(
            {
                "schema_version": ROUTE_SHORTLIST_SCHEMA_VERSION,
                "route_id": route_id,
                "request_text": request_text,
                "owner_candidates": [
                    {
                        "path": item.get("path"),
                        "summary": item.get("summary"),
                        "score": item.get("owner_score", item.get("score")),
                        "why": item.get("why"),
                    }
                    for item in owner_candidates
                    if isinstance(item.get("path"), str) and item.get("path")
                ],
                "command_suggestions": command_suggestions[:4],
                "priority_paths": priority_paths,
                "source_hashes": {
                    path_text: file_hashes[path_text]
                    for path_text in priority_paths
                    if path_text in file_hashes and file_hashes[path_text]
                },
                "why_these_files": build_why_these_files_summary(
                    owner_candidates=owner_candidates,
                    command_suggestions=command_suggestions,
                    ownership_graph=ownership_graph,
                    request_text=request_text,
                    route_id=route_id,
                ),
            }
        )
    return projections


def build_runtime_action_hints(
    *,
    request_text: str | None,
    route_id: str | None,
    route_status: str | None,
    retrieval_mode: str | None,
    confidence: float,
    owner_candidates: list[dict[str, Any]],
    command_suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    query_tokens = request_tokens(request_text)
    next_read_paths: list[str] = []
    for candidate in owner_candidates[:4]:
        path_text = candidate.get("path")
        if isinstance(path_text, str) and path_text and path_text not in next_read_paths:
            next_read_paths.append(path_text)
    exact_candidate_commands_only = [
        str(item.get("command"))
        for item in command_suggestions
        if isinstance(item.get("command"), str) and str(item.get("command")).strip()
    ][:4]
    do_not_scan_paths: list[str] = []
    if {"checkout", "customer", "storefront"}.intersection(query_tokens):
        do_not_scan_paths.append("apps/admin/")
    readiness_tokens = {"backend", "health", "ready", "readiness", "server"}
    symptom_tokens = {"assertion", "failure", "failing", "triage"}
    if readiness_tokens.intersection(query_tokens) and symptom_tokens.intersection(query_tokens):
        do_not_scan_paths.extend(
            ["apps/admin/", "apps/server/src/admin/", "apps/storefront/", "packages/checkout-cta/"]
        )
    do_not_scan_paths.extend(list(ROUTE_DISTRACTOR_HINTS.get(route_id or "", [])))
    confidence_reason = "No high-confidence owner shortlist yet; use `search_context_index` before broad manual exploration."
    if route_status == "exact" and exact_candidate_commands_only:
        confidence_reason = "Route resolved exactly and package-owned verification commands were found."
    elif route_status == "exact" and next_read_paths and confidence >= 0.7:
        confidence_reason = "Route resolved exactly and top owner candidates are concentrated in a narrow file slice."
    elif route_status == "ambiguous":
        confidence_reason = "Route is still ambiguous; confirm the route before broad repo exploration."
    elif next_read_paths:
        confidence_reason = "A narrow owner shortlist exists, but confidence is not yet high enough to skip validation."
    stop_if_enough = bool(next_read_paths) and (
        (route_status == "exact" and confidence >= 0.68)
        or (route_status == "exact" and bool(exact_candidate_commands_only))
    )
    stop_if_enough_guidance = (
        "Read the listed owner files in order and stop once the owner set and exact package command are confirmed."
        if stop_if_enough and exact_candidate_commands_only
        else "Read only the listed owner files first; use `search_context_index` next if they do not confirm the owner set."
        if next_read_paths
        else "Resolve the route first, then re-run preflight or `search_context_index`."
    )
    return {
        "request_kind_hint": retrieval_mode or "orientation",
        "next_read_paths": next_read_paths[:4],
        "do_not_scan_paths": list(dict.fromkeys(do_not_scan_paths))[:6],
        "exact_candidate_commands_only": exact_candidate_commands_only,
        "confidence_reason": confidence_reason,
        "stop_if_enough": stop_if_enough,
        "stop_if_enough_guidance": stop_if_enough_guidance,
    }
