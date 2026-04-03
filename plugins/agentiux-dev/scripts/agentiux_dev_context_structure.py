from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentiux_dev_text import normalize_command_phrase, short_hash, tokenize_text


STRUCTURE_INDEX_SCHEMA_VERSION = 1
MAX_SYMBOL_CHUNKS_PER_FILE = 12
MAX_DOC_SECTION_CHUNKS_PER_FILE = 8
MAX_DEPENDENCY_TARGETS = 16

LANGUAGE_BY_SUFFIX = {
    ".cjs": "javascript",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".md": "markdown",
    ".mjs": "javascript",
    ".py": "python",
    ".rs": "rust",
    ".sh": "shell",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
}

JS_TS_SUFFIXES = {".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"}
SCRIPT_LANGUAGE_NAMES = {
    "javascript": "JavaScript",
    "json": "JSON",
    "kotlin": "Kotlin",
    "markdown": "Markdown",
    "python": "Python",
    "rust": "Rust",
    "shell": "Shell",
    "text": "Text",
    "toml": "TOML",
    "typescript": "TypeScript",
    "yaml": "YAML",
}


def language_for_path(path: Path) -> str:
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "text")


def default_parser_backends(workspace: Path, plugin_root_path: Path | None = None) -> dict[str, Any]:
    node_path = shutil.which("node")
    typescript_backend = _resolve_typescript_backend(workspace, plugin_root_path, node_path=node_path)
    return {
        "python_ast": {
            "available": True,
            "status": "active",
            "backend": "built_in",
            "reason": None,
        },
        "markdown_sections": {
            "available": True,
            "status": "active",
            "backend": "built_in",
            "reason": None,
        },
        "typescript_compiler": typescript_backend,
    }


def structure_index_payload(
    *,
    workspace: Path,
    workspace_fingerprint: str,
    parser_backends: dict[str, Any],
    modules: list[dict[str, Any]],
    files: list[dict[str, Any]],
    chunk_counts: dict[str, int],
    hotspots: list[dict[str, Any]],
    incremental_indexing: dict[str, Any],
) -> dict[str, Any]:
    large_file_count = sum(1 for item in files if item.get("large_file"))
    file_hotspots = [item for item in hotspots if item.get("target_kind") == "file"]
    module_hotspots = [item for item in hotspots if item.get("target_kind") == "module"]
    return {
        "schema_version": STRUCTURE_INDEX_SCHEMA_VERSION,
        "workspace_path": str(workspace),
        "workspace_fingerprint": workspace_fingerprint,
        "generated_at": incremental_indexing.get("generated_at"),
        "parser_backends": parser_backends,
        "summary": {
            "module_count": len(modules),
            "file_count": len(files),
            "indexed_file_count": len(files),
            "chunk_counts": chunk_counts,
            "large_file_count": large_file_count,
            "file_hotspot_count": len(file_hotspots),
            "module_hotspot_count": len(module_hotspots),
        },
        "incremental_indexing": incremental_indexing,
        "modules": modules,
        "files": files,
        "hotspots": hotspots,
    }


def build_file_structure(
    workspace: Path,
    path: Path,
    *,
    file_hash: str,
    module_id: str | None,
    parser_backends: dict[str, Any],
    large_file_threshold: int,
    recent_churn: bool = False,
) -> dict[str, Any]:
    relative = str(path.relative_to(workspace))
    relative_path = Path(relative)
    size_bytes = path.stat().st_size
    large_file = size_bytes > large_file_threshold
    read_result = _read_source_text(path, large_file_threshold=large_file_threshold, force_bounded=large_file)
    text = read_result["text"]
    line_count = max(text.count("\n") + 1, 1)
    language = language_for_path(path)
    route_hints = ["analysis"]

    parser_backend = "heuristic"
    symbol_entries: list[dict[str, Any]] = []
    doc_sections: list[dict[str, Any]] = []
    dependencies: list[str] = []

    if language == "python" and not read_result["truncated"]:
        try:
            parsed = _extract_python_ast(text)
            parser_backend = "python_ast"
            symbol_entries = parsed["symbols"]
            dependencies = parsed["dependencies"]
        except SyntaxError:
            parsed = _extract_heuristic_structure(path, text)
            symbol_entries = parsed["symbols"]
            dependencies = parsed["dependencies"]
    elif language == "markdown":
        parsed = _extract_markdown_sections(text, fallback_title=path.stem)
        parser_backend = "markdown_sections"
        doc_sections = parsed["sections"]
    elif language in {"javascript", "typescript"} and not large_file:
        parsed = _extract_typescript_backend(path, workspace, parser_backends)
        if parsed is not None:
            parser_backend = parsed.get("backend", "typescript_compiler")
            symbol_entries = parsed.get("symbols", [])
            dependencies = parsed.get("dependencies", [])
        else:
            parsed = _extract_heuristic_structure(path, text)
            symbol_entries = parsed["symbols"]
            dependencies = parsed["dependencies"]
    else:
        parsed = _extract_heuristic_structure(path, text)
        symbol_entries = parsed["symbols"]
        dependencies = parsed["dependencies"]
        if language == "markdown":
            doc_sections = _extract_markdown_sections(text, fallback_title=path.stem)["sections"]

    symbol_entries = symbol_entries[:MAX_SYMBOL_CHUNKS_PER_FILE]
    doc_sections = doc_sections[:MAX_DOC_SECTION_CHUNKS_PER_FILE]
    dependencies = _dedupe_strings(dependencies)[:MAX_DEPENDENCY_TARGETS]

    file_summary = _file_summary(
        path,
        language=language,
        parser_backend=parser_backend,
        symbol_entries=symbol_entries,
        doc_sections=doc_sections,
        dependencies=dependencies,
        large_file=large_file,
        truncated=read_result["truncated"],
    )
    base_tags = _file_tags(relative_path, file_summary, symbol_entries, dependencies, chunk_kind="file")
    line_end = line_count if not read_result["truncated"] else max(line_count, 1)
    file_chunk = {
        "chunk_id": short_hash(f"{relative}:{file_hash}:file", length=16),
        "chunk_kind": "file",
        "path": relative,
        "hash": file_hash,
        "module_id": module_id,
        "language": language,
        "symbols": [entry["title"] for entry in symbol_entries[:4]],
        "tags": base_tags,
        "summary": file_summary,
        "dependencies": dependencies[:],
        "dependency_targets": [],
        "route_hints": sorted(dict.fromkeys(route_hints + _route_hints_for_content(relative, base_tags, file_summary))),
        "anchor_title": path.name,
        "anchor_kind": "file",
        "line_start": 1,
        "line_end": line_end,
        "section_level": None,
        "hotspot_labels": [],
        "source_kind": "repo_file",
    }
    chunks = [file_chunk]

    for entry in symbol_entries:
        summary = normalize_command_phrase(
            f"{SCRIPT_LANGUAGE_NAMES.get(language, language.title())} {entry['kind']} {entry['title']} in {path.name}."
        )
        chunks.append(
            {
                "chunk_id": short_hash(
                    f"{relative}:{file_hash}:symbol:{entry['kind']}:{entry['title']}:{entry['line_start']}",
                    length=16,
                ),
                "chunk_kind": "symbol",
                "path": relative,
                "hash": file_hash,
                "module_id": module_id,
                "language": language,
                "symbols": [entry["title"]],
                "tags": _file_tags(relative_path, summary, [entry], dependencies, chunk_kind="symbol"),
                "summary": summary,
                "dependencies": dependencies[:],
                "dependency_targets": [],
                "route_hints": ["analysis"],
                "anchor_title": entry["title"],
                "anchor_kind": entry["kind"],
                "line_start": entry["line_start"],
                "line_end": entry["line_end"],
                "section_level": None,
                "hotspot_labels": [],
                "source_kind": "repo_file",
            }
        )

    for section in doc_sections:
        summary = normalize_command_phrase(f"Markdown section {section['title']} in {path.name}.")
        chunks.append(
            {
                "chunk_id": short_hash(
                    f"{relative}:{file_hash}:doc:{section['title']}:{section['line_start']}:{section['line_end']}",
                    length=16,
                ),
                "chunk_kind": "doc_section",
                "path": relative,
                "hash": file_hash,
                "module_id": module_id,
                "language": language,
                "symbols": [section["title"]],
                "tags": _file_tags(
                    relative_path,
                    summary,
                    [{"title": section["title"], "kind": "section"}],
                    [],
                    chunk_kind="doc_section",
                ),
                "summary": summary,
                "dependencies": [],
                "dependency_targets": [],
                "route_hints": ["analysis"],
                "anchor_title": section["title"],
                "anchor_kind": "section",
                "line_start": section["line_start"],
                "line_end": section["line_end"],
                "section_level": section["level"],
                "hotspot_labels": [],
                "source_kind": "repo_file",
            }
        )

    file_record = {
        "path": relative,
        "module_id": module_id,
        "language": language,
        "file_size_bytes": size_bytes,
        "indexed_bytes": read_result["indexed_bytes"],
        "read_mode": "bounded" if read_result["bounded"] else "full",
        "large_file": large_file,
        "parser_backend": parser_backend,
        "summary": file_summary,
        "symbol_count": len(symbol_entries),
        "doc_section_count": len(doc_sections),
        "dependency_count": len(dependencies),
        "dependencies": dependencies[:],
        "dependency_targets": [],
        "local_fan_in": 0,
        "local_fan_out": 0,
        "hotspot_score": 0,
        "hotspot_labels": [],
        "entrypoint_score": _entrypoint_score(relative),
        "recent_churn": recent_churn,
        "line_count": line_count,
    }
    return {
        "file_record": file_record,
        "chunks": chunks,
        "bounded_read_count": 1 if read_result["bounded"] else 0,
        "full_read_count": 0 if read_result["bounded"] else 1,
        "large_file_count": 1 if large_file else 0,
    }


def summarize_chunk_counts(chunks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "file": 0,
        "symbol": 0,
        "doc_section": 0,
        "project_memory": 0,
    }
    for chunk in chunks:
        chunk_kind = str(chunk.get("chunk_kind") or "file")
        if chunk_kind not in counts:
            counts[chunk_kind] = 0
        counts[chunk_kind] += 1
    return counts


def top_hotspots(files: list[dict[str, Any]], modules: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    hotspots: list[dict[str, Any]] = []
    for file_record in files:
        if int(file_record.get("hotspot_score") or 0) <= 0:
            continue
        hotspots.append(
            {
                "target_kind": "file",
                "path": file_record["path"],
                "module_id": file_record.get("module_id"),
                "language": file_record.get("language"),
                "hotspot_score": file_record.get("hotspot_score", 0),
                "hotspot_labels": file_record.get("hotspot_labels", []),
                "local_fan_in": file_record.get("local_fan_in", 0),
                "local_fan_out": file_record.get("local_fan_out", 0),
                "large_file": file_record.get("large_file", False),
            }
        )
    for module in modules:
        if int(module.get("hotspot_score") or 0) <= 0:
            continue
        hotspots.append(
            {
                "target_kind": "module",
                "path": module.get("path"),
                "module_id": module.get("module_id"),
                "hotspot_score": module.get("hotspot_score", 0),
                "hotspot_labels": module.get("hotspot_labels", []),
                "local_fan_in": module.get("local_fan_in", 0),
                "local_fan_out": module.get("local_fan_out", 0),
                "large_file_count": module.get("large_file_count", 0),
            }
        )
    hotspots.sort(
        key=lambda item: (
            -int(item.get("hotspot_score") or 0),
            item.get("target_kind") != "module",
            str(item.get("path") or ""),
        )
    )
    return hotspots[:limit]


def resolve_local_dependency_targets(
    workspace: Path,
    candidate_paths: list[str],
    file_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    path_lookup = _candidate_path_lookup(candidate_paths)
    fan_in: dict[str, int] = {item["path"]: 0 for item in file_records}
    for file_record in file_records:
        resolved = _resolve_dependency_targets(workspace, file_record["path"], file_record.get("dependencies") or [], path_lookup)
        file_record["dependency_targets"] = resolved
        file_record["local_fan_out"] = len(resolved)
        for target in resolved:
            fan_in[target] = fan_in.get(target, 0) + 1
    for file_record in file_records:
        file_record["local_fan_in"] = fan_in.get(file_record["path"], 0)
        file_record["hotspot_score"], file_record["hotspot_labels"] = _hotspot_score(file_record)
    return file_records


def aggregate_modules(modules: list[dict[str, Any]], file_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    file_records_by_module: dict[str, list[dict[str, Any]]] = {}
    for record in file_records:
        module_id = record.get("module_id")
        if module_id:
            file_records_by_module.setdefault(module_id, []).append(record)
    annotated: list[dict[str, Any]] = []
    for module in modules:
        module_files = file_records_by_module.get(module["module_id"], [])
        language_counts: dict[str, int] = {}
        hotspot_labels: list[str] = []
        for record in module_files:
            language = str(record.get("language") or "text")
            language_counts[language] = language_counts.get(language, 0) + 1
            for label in record.get("hotspot_labels", []):
                if label not in hotspot_labels:
                    hotspot_labels.append(label)
        annotated.append(
            {
                **module,
                "file_count": len(module_files),
                "indexed_file_count": len(module_files),
                "language_counts": language_counts,
                "local_fan_in": sum(int(record.get("local_fan_in") or 0) for record in module_files),
                "local_fan_out": sum(int(record.get("local_fan_out") or 0) for record in module_files),
                "hotspot_score": max((int(record.get("hotspot_score") or 0) for record in module_files), default=0),
                "hotspot_labels": hotspot_labels[:8],
                "large_file_count": sum(1 for record in module_files if record.get("large_file")),
            }
        )
    return annotated


def apply_file_metadata_to_chunks(
    chunks: list[dict[str, Any]],
    file_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    file_map = {record["path"]: record for record in file_records}
    hydrated: list[dict[str, Any]] = []
    for chunk in chunks:
        file_record = file_map.get(chunk["path"])
        if not file_record:
            hydrated.append(chunk)
            continue
        hydrated.append(
            {
                **chunk,
                "module_id": file_record.get("module_id"),
                "language": file_record.get("language"),
                "dependency_targets": file_record.get("dependency_targets", []),
                "hotspot_labels": file_record.get("hotspot_labels", []),
            }
        )
    return hydrated


def structure_summary(structure_index: dict[str, Any]) -> dict[str, Any]:
    summary = structure_index.get("summary") or {}
    incremental = structure_index.get("incremental_indexing") or {}
    parser_backends = structure_index.get("parser_backends") or {}
    return {
        "module_count": summary.get("module_count", 0),
        "file_count": summary.get("file_count", 0),
        "indexed_file_count": summary.get("indexed_file_count", 0),
        "chunk_counts": summary.get("chunk_counts", {}),
        "large_file_count": summary.get("large_file_count", 0),
        "parser_backend_status": {
            name: details.get("status")
            for name, details in parser_backends.items()
            if isinstance(details, dict)
        },
        "rebuilt_file_count": incremental.get("rebuilt_file_count", 0),
        "reused_file_count": incremental.get("reused_file_count", 0),
        "removed_file_count": incremental.get("removed_file_count", 0),
    }


def hotspot_summary(structure_index: dict[str, Any]) -> dict[str, Any]:
    summary = structure_index.get("summary") or {}
    hotspots = structure_index.get("hotspots") or []
    return {
        "hotspot_count": len(hotspots),
        "file_hotspot_count": summary.get("file_hotspot_count", 0),
        "module_hotspot_count": summary.get("module_hotspot_count", 0),
        "top_hotspot_labels": _top_hotspot_labels(hotspots),
    }


def _resolve_typescript_backend(
    workspace: Path,
    plugin_root_path: Path | None,
    *,
    node_path: str | None,
) -> dict[str, Any]:
    if not node_path:
        return {
            "available": False,
            "status": "unavailable",
            "backend": "node",
            "node_path": None,
            "module_path": None,
            "source": None,
            "reason": "node_not_found",
        }
    for source, root in (("workspace", workspace), ("plugin", plugin_root_path)):
        if root is None:
            continue
        package_json = root / "node_modules" / "typescript" / "package.json"
        if not package_json.exists():
            continue
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        main_field = payload.get("main") or "lib/typescript.js"
        module_path = (package_json.parent / main_field).resolve()
        if module_path.exists():
            return {
                "available": True,
                "status": "available",
                "backend": "typescript_compiler",
                "node_path": node_path,
                "module_path": str(module_path),
                "source": source,
                "reason": None,
            }
    return {
        "available": False,
        "status": "unavailable",
        "backend": "typescript_compiler",
        "node_path": node_path,
        "module_path": None,
        "source": None,
        "reason": "typescript_not_found",
    }


def _read_source_text(path: Path, *, large_file_threshold: int, force_bounded: bool) -> dict[str, Any]:
    if not force_bounded:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return {
            "text": text,
            "bounded": False,
            "truncated": False,
            "indexed_bytes": len(text.encode("utf-8")),
        }
    with path.open("rb") as handle:
        raw = handle.read(large_file_threshold)
    text = raw.decode("utf-8", errors="ignore")
    return {
        "text": text,
        "bounded": True,
        "truncated": path.stat().st_size > len(raw),
        "indexed_bytes": len(raw),
    }


def _extract_python_ast(text: str) -> dict[str, Any]:
    tree = ast.parse(text)
    symbols: list[dict[str, Any]] = []
    dependencies: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                {
                    "title": node.name,
                    "kind": "function",
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                }
            )
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                {
                    "title": node.name,
                    "kind": "class",
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                }
            )
        elif isinstance(node, ast.Import):
            dependencies.extend(alias.name for alias in node.names if alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                dependencies.append(node.module)
    return {
        "symbols": symbols,
        "dependencies": dependencies,
    }


def _extract_markdown_sections(text: str, *, fallback_title: str) -> dict[str, Any]:
    headings: list[dict[str, Any]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if not match:
            continue
        headings.append(
            {
                "title": match.group(2).strip(),
                "level": len(match.group(1)),
                "line_start": line_number,
            }
        )
    if not headings:
        return {
            "sections": [
                {
                    "title": fallback_title,
                    "level": 1,
                    "line_start": 1,
                    "line_end": max(len(lines), 1),
                }
            ]
        }
    sections: list[dict[str, Any]] = []
    for index, heading in enumerate(headings):
        next_heading = headings[index + 1] if index + 1 < len(headings) else None
        sections.append(
            {
                "title": heading["title"],
                "level": heading["level"],
                "line_start": heading["line_start"],
                "line_end": (next_heading["line_start"] - 1) if next_heading else max(len(lines), heading["line_start"]),
            }
        )
    return {"sections": sections}


def _extract_typescript_backend(path: Path, workspace: Path, parser_backends: dict[str, Any]) -> dict[str, Any] | None:
    backend = parser_backends.get("typescript_compiler") or {}
    if not backend.get("available") or not backend.get("node_path") or not backend.get("module_path"):
        return None
    helper_path = Path(__file__).with_name("ts_structure_helper.js")
    result = subprocess.run(
        [
            str(backend["node_path"]),
            str(helper_path),
            "--file",
            str(path),
            "--workspace",
            str(workspace),
            "--typescript-module",
            str(backend["module_path"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if payload.get("status") != "ok":
        return None
    return {
        "backend": str(payload.get("backend") or "typescript_compiler"),
        "symbols": payload.get("symbols", []),
        "dependencies": payload.get("dependencies", []),
    }


def _extract_heuristic_structure(path: Path, text: str) -> dict[str, Any]:
    suffix = path.suffix.lower()
    symbol_patterns: list[tuple[str, str]] = []
    if suffix == ".py":
        symbol_patterns = [
            (r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", "function"),
            (r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", "class"),
        ]
    elif suffix in JS_TS_SUFFIXES:
        symbol_patterns = [
            (r"^\s*export\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", "function"),
            (r"^\s*(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", "function"),
            (r"^\s*export\s+class\s+([A-Za-z_][A-Za-z0-9_]*)", "class"),
            (r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", "class"),
            (r"^\s*export\s+const\s+([A-Za-z_][A-Za-z0-9_]*)", "constant"),
        ]
    elif suffix in {".kt", ".kts"}:
        symbol_patterns = [
            (r"^\s*fun\s+([A-Za-z_][A-Za-z0-9_]*)", "function"),
            (r"^\s*(?:data\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)", "class"),
        ]
    elif suffix == ".rs":
        symbol_patterns = [
            (r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)", "function"),
            (r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)", "type"),
        ]
    symbols: list[dict[str, Any]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        for pattern, kind in symbol_patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            title = match.group(1).strip()
            if title and not any(item["title"] == title for item in symbols):
                symbols.append(
                    {
                        "title": title,
                        "kind": kind,
                        "line_start": line_number,
                        "line_end": line_number,
                    }
                )
            break
    return {
        "symbols": symbols,
        "dependencies": _extract_dependencies(path, text),
    }


def _extract_dependencies(path: Path, text: str) -> list[str]:
    suffix = path.suffix.lower()
    dependencies: list[str] = []
    if suffix == ".py":
        dependencies.extend(re.findall(r"^\s*(?:from|import)\s+([A-Za-z0-9_\.]+)", text, flags=re.MULTILINE))
    elif suffix in JS_TS_SUFFIXES:
        dependencies.extend(re.findall(r"from\s+[\"']([^\"']+)[\"']", text))
        dependencies.extend(re.findall(r"require\([\"']([^\"']+)[\"']\)", text))
    elif suffix in {".kt", ".kts"}:
        dependencies.extend(re.findall(r"^\s*import\s+([A-Za-z0-9_\.]+)", text, flags=re.MULTILINE))
    elif suffix == ".rs":
        dependencies.extend(re.findall(r"^\s*use\s+([^;]+);", text, flags=re.MULTILINE))
    elif path.name == "package.json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {}
        for field in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(payload.get(field), dict):
                dependencies.extend(sorted(payload[field]))
    return dependencies


def _file_summary(
    path: Path,
    *,
    language: str,
    parser_backend: str,
    symbol_entries: list[dict[str, Any]],
    doc_sections: list[dict[str, Any]],
    dependencies: list[str],
    large_file: bool,
    truncated: bool,
) -> str:
    language_label = SCRIPT_LANGUAGE_NAMES.get(language, language.title())
    summary_bits = [f"{language_label} source at {path.name}."]
    if parser_backend != "heuristic":
        summary_bits.append(f"Structural backend: {parser_backend}.")
    if symbol_entries:
        summary_bits.append(f"Anchors: {', '.join(entry['title'] for entry in symbol_entries[:4])}.")
    if doc_sections:
        summary_bits.append(f"Sections: {', '.join(section['title'] for section in doc_sections[:4])}.")
    if dependencies:
        summary_bits.append(f"Dependencies: {', '.join(dependencies[:4])}.")
    if large_file:
        summary_bits.append("Large-file structural mode is active.")
    if truncated:
        summary_bits.append("Bounded extraction truncated the source body.")
    return normalize_command_phrase(" ".join(summary_bits))


def _file_tags(
    path: Path,
    summary: str,
    symbol_entries: list[dict[str, Any]],
    dependencies: list[str],
    *,
    chunk_kind: str,
) -> list[str]:
    tags = [chunk_kind, language_for_path(path), path.suffix.lower().lstrip(".")]
    tags.extend(path.parts[:4])
    tags.extend(tokenize_text(path.stem))
    tags.extend(tokenize_text(summary))
    tags.extend(tokenize_text(" ".join(entry["title"] for entry in symbol_entries[:4])))
    tags.extend(tokenize_text(" ".join(dependencies[:4])))
    return sorted(dict.fromkeys(tag for tag in tags if tag))


def _route_hints_for_content(relative_path: str, tags: list[str], summary: str) -> list[str]:
    hints: list[str] = []
    token_set = set(tags + tokenize_text(relative_path) + tokenize_text(summary))
    if token_set.intersection({"analysis", "chunk", "doc", "hotspot", "incremental", "index", "large", "module", "section", "structural", "symbol"}):
        hints.append("analysis")
    if token_set.intersection({"brief", "design", "handoff", "reference", "ux"}):
        hints.append("design")
    if token_set.intersection({"dashboard", "mcp", "plugin", "release"}):
        hints.append("plugin-dev")
    if token_set.intersection({"stage", "task", "workflow", "workspace"}):
        hints.append("workstream")
    if token_set.intersection({"baseline", "semantic", "verification", "visual"}):
        hints.append("verification")
    return sorted(dict.fromkeys(hints))


def _entrypoint_score(relative_path: str) -> int:
    path = Path(relative_path)
    score = 0
    if path.name in {"README.md", "main.py", "app.js", "app.ts", "index.js", "index.ts", "index.tsx"}:
        score += 4
    if "scripts" in path.parts:
        score += 3
    if "references" in path.parts:
        score += 2
    if path.name == "plugin.json":
        score += 5
    return score


def _candidate_path_lookup(candidate_paths: list[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for candidate in candidate_paths:
        candidate_path = Path(candidate)
        tokens = {
            candidate,
            candidate_path.with_suffix("").as_posix(),
            candidate_path.stem,
        }
        if candidate_path.name == "__init__.py":
            tokens.add(candidate_path.parent.as_posix())
        dotted = candidate_path.with_suffix("").as_posix().replace("/", ".")
        tokens.add(dotted)
        for token in tokens:
            lookup.setdefault(token, candidate)
    return lookup


def _resolve_dependency_targets(workspace: Path, relative_path: str, dependencies: list[str], path_lookup: dict[str, str]) -> list[str]:
    current_path = workspace / relative_path
    resolved: list[str] = []
    for dependency in dependencies:
        normalized = dependency.strip()
        if not normalized:
            continue
        target = None
        if normalized.startswith("."):
            target = _resolve_relative_dependency(current_path, normalized, path_lookup)
        else:
            target = path_lookup.get(normalized) or path_lookup.get(normalized.replace("::", ".")) or path_lookup.get(normalized.replace("::", "/"))
        if target and target not in resolved and target != relative_path:
            resolved.append(target)
    return resolved[:MAX_DEPENDENCY_TARGETS]


def _resolve_relative_dependency(current_path: Path, dependency: str, path_lookup: dict[str, str]) -> str | None:
    base = current_path.parent
    for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".kt", ".kts", ".rs", ".md"):
        candidate = (base / dependency).resolve()
        if suffix:
            candidate = candidate.with_suffix(suffix)
        token = candidate.as_posix()
        for lookup_key, relative_path in path_lookup.items():
            if lookup_key.endswith(token):
                return relative_path
    relative_candidate = (base / dependency).as_posix().lstrip("./")
    return path_lookup.get(relative_candidate) or path_lookup.get(relative_candidate.replace("/", "."))


def _hotspot_score(file_record: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    labels: list[str] = []
    if file_record.get("large_file"):
        score += 4
        labels.append("large-file")
    if int(file_record.get("symbol_count") or 0) >= 8:
        score += 2
        labels.append("dense-symbols")
    if int(file_record.get("doc_section_count") or 0) >= 6:
        score += 2
        labels.append("section-heavy")
    if int(file_record.get("local_fan_out") or 0) >= 4:
        score += 2
        labels.append("dependency-fan-out")
    if int(file_record.get("local_fan_in") or 0) >= 4:
        score += 2
        labels.append("dependency-fan-in")
    if int(file_record.get("entrypoint_score") or 0) >= 4:
        score += 1
        labels.append("entrypoint-likely")
    if file_record.get("recent_churn"):
        score += 1
        labels.append("recent-churn")
    return score, labels


def _top_hotspot_labels(hotspots: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for hotspot in hotspots:
        for label in hotspot.get("hotspot_labels", []):
            counts[label] = counts.get(label, 0) + 1
    return [item[0] for item in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:6]]


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped
