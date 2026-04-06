from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path
from typing import Any


CONTEXT_STORE_SCHEMA_VERSION = 2
QUERY_CACHE_CONTEXT_PACK_KIND = "context_pack"
QUERY_CACHE_RUNTIME_PREFLIGHT_KIND = "runtime_preflight"
QUERY_CACHE_BENCHMARK_PROJECTION_KIND = "benchmark_projection"
QUERY_CACHE_ROUTE_SHORTLIST_KIND = "route_shortlist"
QUERY_CACHE_OWNERSHIP_GRAPH_KIND = "ownership_graph"
QUERY_CACHE_TASK_RETRIEVAL_KIND = "task_retrieval"


@contextmanager
def _connect(store_path: Path):
    store_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(store_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        _initialize_schema(connection)
        yield connection
    finally:
        connection.close()


def _existing_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
    }


def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _existing_columns(connection, table)
    for name, definition in columns.items():
        if name in existing:
            continue
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")  # noqa: S608


def _initialize_schema(connection: sqlite3.Connection) -> None:
    chunk_columns = connection.execute("PRAGMA table_info(chunks)").fetchall()
    if chunk_columns:
        primary_keys = [row["name"] for row in chunk_columns if int(row["pk"] or 0) > 0]
        if primary_keys != ["chunk_id"]:
            connection.execute("DROP TABLE chunks")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS modules (
            module_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            kind TEXT,
            hotspot_score REAL,
            local_fan_in INTEGER,
            local_fan_out INTEGER,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            module_id TEXT,
            hash TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            module_id TEXT,
            chunk_kind TEXT,
            source_kind TEXT,
            anchor_title TEXT,
            line_start INTEGER,
            hash TEXT,
            summary_text TEXT,
            symbols_text TEXT,
            tags_text TEXT,
            route_hints_text TEXT,
            dependency_targets_text TEXT,
            search_text TEXT,
            note_status TEXT,
            pin_state TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS query_cache (
            cache_kind TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            route_id TEXT,
            workspace_fingerprint TEXT,
            catalog_digest TEXT,
            semantic_mode TEXT,
            created_at TEXT,
            source_hashes_json TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (cache_kind, cache_key)
        );
        """
    )
    _ensure_columns(
        connection,
        "modules",
        {
            "kind": "TEXT",
            "hotspot_score": "REAL",
            "local_fan_in": "INTEGER",
            "local_fan_out": "INTEGER",
        },
    )
    _ensure_columns(
        connection,
        "files",
        {
            "module_id": "TEXT",
            "hash": "TEXT",
        },
    )
    _ensure_columns(
        connection,
        "chunks",
        {
            "module_id": "TEXT",
            "source_kind": "TEXT",
            "anchor_title": "TEXT",
            "summary_text": "TEXT",
            "symbols_text": "TEXT",
            "tags_text": "TEXT",
            "route_hints_text": "TEXT",
            "dependency_targets_text": "TEXT",
            "search_text": "TEXT",
            "note_status": "TEXT",
            "pin_state": "TEXT",
        },
    )
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_modules_path_hotspot
        ON modules(path, hotspot_score DESC);

        CREATE INDEX IF NOT EXISTS idx_files_module
        ON files(module_id, path);

        CREATE INDEX IF NOT EXISTS idx_chunks_path_kind_line
        ON chunks(path, chunk_kind, line_start, chunk_id);

        CREATE INDEX IF NOT EXISTS idx_chunks_module
        ON chunks(module_id, path, line_start);

        CREATE INDEX IF NOT EXISTS idx_chunks_source_pin
        ON chunks(source_kind, pin_state, note_status, path);

        CREATE INDEX IF NOT EXISTS idx_query_cache_kind_created
        ON query_cache(cache_kind, created_at);
        """
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
        ("schema_version", str(CONTEXT_STORE_SCHEMA_VERSION)),
    )
    connection.commit()


def _encode(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _decode(payload: str | bytes | None, default: Any) -> Any:
    if payload in {None, ""}:
        return default
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return default


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, dict):
        return _normalized_text(" ".join(str(key) for key in value))
    if isinstance(value, (list, tuple, set)):
        return " ".join(part for item in value if (part := _normalized_text(item)))
    return str(value).strip().lower()


def _search_blob(*values: Any) -> str:
    return " ".join(part for value in values if (part := _normalized_text(value)))


def replace_context_records(
    store_path: Path,
    *,
    modules: list[dict[str, Any]],
    files: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> None:
    with _connect(store_path) as connection:
        connection.execute("DELETE FROM modules")
        connection.execute("DELETE FROM files")
        connection.execute("DELETE FROM chunks")
        if modules:
            connection.executemany(
                """
                INSERT INTO modules(module_id, path, kind, hotspot_score, local_fan_in, local_fan_out, payload_json)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(module.get("module_id") or module.get("path") or ""),
                        str(module.get("path") or ""),
                        str(module.get("kind") or ""),
                        float(module.get("hotspot_score") or 0.0),
                        int(module.get("local_fan_in") or 0),
                        int(module.get("local_fan_out") or 0),
                        _encode(module),
                    )
                    for module in modules
                ],
            )
        if files:
            connection.executemany(
                """
                INSERT INTO files(path, module_id, hash, payload_json)
                VALUES(?, ?, ?, ?)
                """,
                [
                    (
                        str(record.get("path") or ""),
                        str(record.get("module_id") or ""),
                        str(record.get("hash") or ""),
                        _encode(record),
                    )
                    for record in files
                ],
            )
        if chunks:
            connection.executemany(
                """
                INSERT INTO chunks(
                    chunk_id,
                    path,
                    module_id,
                    chunk_kind,
                    source_kind,
                    anchor_title,
                    line_start,
                    hash,
                    summary_text,
                    symbols_text,
                    tags_text,
                    route_hints_text,
                    dependency_targets_text,
                    search_text,
                    note_status,
                    pin_state,
                    payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(
                            chunk.get("chunk_id")
                            or f"{chunk.get('path') or ''}:{chunk.get('chunk_kind') or ''}:{int(chunk.get('line_start') or 0)}"
                        ),
                        str(chunk.get("path") or ""),
                        str(chunk.get("module_id") or ""),
                        str(chunk.get("chunk_kind") or ""),
                        str(chunk.get("source_kind") or ""),
                        str(chunk.get("anchor_title") or ""),
                        int(chunk.get("line_start") or 0),
                        str(chunk.get("hash") or ""),
                        _normalized_text(chunk.get("summary")),
                        _normalized_text(chunk.get("symbols")),
                        _normalized_text(chunk.get("tags")),
                        _normalized_text(chunk.get("route_hints")),
                        _normalized_text(chunk.get("dependency_targets")),
                        _search_blob(
                            chunk.get("path"),
                            chunk.get("summary"),
                            chunk.get("symbols"),
                            chunk.get("tags"),
                            chunk.get("route_hints"),
                            chunk.get("dependency_targets"),
                            chunk.get("anchor_title"),
                            chunk.get("module_id"),
                            chunk.get("source_kind"),
                            chunk.get("language"),
                        ),
                        str(chunk.get("note_status") or ""),
                        str(chunk.get("pin_state") or ""),
                        _encode(chunk),
                    )
                    for chunk in chunks
                ],
            )
        connection.commit()


def _load_payloads(store_path: Path, *, table: str, order_by: str) -> list[dict[str, Any]]:
    if not store_path.exists():
        return []
    with _connect(store_path) as connection:
        rows = connection.execute(
            f"SELECT payload_json FROM {table} ORDER BY {order_by}"  # noqa: S608
        ).fetchall()
    return [payload for row in rows if isinstance((payload := _decode(row["payload_json"], {})), dict)]


def _placeholder_list(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


def load_modules(store_path: Path) -> list[dict[str, Any]]:
    return _load_payloads(store_path, table="modules", order_by="path, module_id")


def load_files(store_path: Path) -> list[dict[str, Any]]:
    return _load_payloads(store_path, table="files", order_by="path")


def load_chunks(store_path: Path) -> list[dict[str, Any]]:
    return _load_payloads(store_path, table="chunks", order_by="path, chunk_kind, line_start, chunk_id")


def _decode_payload_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [payload for row in rows if isinstance((payload := _decode(row["payload_json"], {})), dict)]


def load_pinned_project_memory_chunks(store_path: Path, *, limit: int = 12) -> list[dict[str, Any]]:
    if not store_path.exists() or limit <= 0:
        return []
    with _connect(store_path) as connection:
        rows = connection.execute(
            """
            SELECT payload_json
            FROM chunks
            WHERE source_kind = 'project_memory'
              AND pin_state = 'pinned'
              AND note_status = 'active'
            ORDER BY path, line_start, chunk_id
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return _decode_payload_rows(rows)


def load_source_hashes(store_path: Path, paths: list[str]) -> dict[str, str]:
    normalized_paths = [str(path) for path in paths if str(path).strip()]
    if not store_path.exists() or not normalized_paths:
        return {}
    placeholders = _placeholder_list(normalized_paths)
    hashes: dict[str, str] = {}
    with _connect(store_path) as connection:
        file_rows = connection.execute(
            f"SELECT path, hash FROM files WHERE path IN ({placeholders})",  # noqa: S608
            normalized_paths,
        ).fetchall()
        for row in file_rows:
            path = str(row["path"] or "")
            file_hash = str(row["hash"] or "")
            if path and file_hash:
                hashes[path] = file_hash
        missing_paths = [path for path in normalized_paths if path not in hashes]
        if missing_paths:
            missing_placeholders = _placeholder_list(missing_paths)
            chunk_rows = connection.execute(
                f"""
                SELECT path, MAX(hash) AS hash
                FROM chunks
                WHERE path IN ({missing_placeholders})
                GROUP BY path
                """,  # noqa: S608
                missing_paths,
            ).fetchall()
            for row in chunk_rows:
                path = str(row["path"] or "")
                chunk_hash = str(row["hash"] or "")
                if path and chunk_hash:
                    hashes[path] = chunk_hash
    return {path: hashes[path] for path in normalized_paths if path in hashes}


def _module_scope_predicate(module_path: str | None) -> tuple[str, list[str]]:
    normalized = str(module_path or "").strip()
    if not normalized or normalized == ".":
        return "", []
    return "WHERE path = ? OR path LIKE ?", [normalized, f"{normalized}/%"]


def load_module_summaries(
    store_path: Path,
    *,
    limit: int = 8,
    module_path: str | None = None,
    preferred_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not store_path.exists() or limit <= 0:
        return []
    score_terms = ["COALESCE(hotspot_score, 0.0)"]
    params: list[Any] = []
    if module_path and str(module_path).strip() and str(module_path).strip() != ".":
        normalized_module_path = str(module_path).strip()
        score_terms.append("CASE WHEN path = ? THEN 60.0 WHEN ? LIKE path || '/%' THEN 40.0 WHEN path LIKE ? || '/%' THEN 20.0 ELSE 0.0 END")
        params.extend([normalized_module_path, normalized_module_path, normalized_module_path])
    for preferred_path in list(dict.fromkeys(preferred_paths or []))[:16]:
        normalized_path = str(preferred_path).strip()
        if not normalized_path:
            continue
        score_terms.append("CASE WHEN ? = path OR ? LIKE path || '/%' THEN 12.0 ELSE 0.0 END")
        params.extend([normalized_path, normalized_path])
    score_expr = " + ".join(score_terms) if score_terms else "0.0"
    scope_sql, scope_params = _module_scope_predicate(module_path)
    with _connect(store_path) as connection:
        rows = connection.execute(
            f"""
            SELECT payload_json
            FROM (
                SELECT payload_json, path, ({score_expr}) AS shortlist_score, hotspot_score
                FROM modules
                {scope_sql}
            )
            ORDER BY shortlist_score DESC, hotspot_score DESC, LENGTH(path) DESC, path ASC
            LIMIT ?
            """,  # noqa: S608
            [*params, *scope_params, int(limit)],
        ).fetchall()
    return _decode_payload_rows(rows)


def load_module_chunks(
    store_path: Path,
    *,
    module_path: str,
    chunk_kinds: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    normalized_module_path = str(module_path or "").strip()
    if not store_path.exists() or not normalized_module_path or limit <= 0:
        return []
    where_clauses = ["(path = ? OR path LIKE ?)"]
    params: list[Any] = [normalized_module_path, f"{normalized_module_path}/%"]
    if chunk_kinds:
        normalized_kinds = [str(kind) for kind in chunk_kinds if str(kind).strip()]
        if normalized_kinds:
            where_clauses.append(f"chunk_kind IN ({_placeholder_list(normalized_kinds)})")
            params.extend(normalized_kinds)
    order_sql = """
        CASE chunk_kind
            WHEN 'file' THEN 0
            WHEN 'symbol' THEN 1
            WHEN 'doc_section' THEN 2
            ELSE 3
        END,
        path ASC,
        line_start ASC,
        chunk_id ASC
    """
    with _connect(store_path) as connection:
        rows = connection.execute(
            f"""
            SELECT payload_json
            FROM chunks
            WHERE {' AND '.join(where_clauses)}
            ORDER BY {order_sql}
            LIMIT ?
            """,  # noqa: S608
            [*params, int(limit)],
        ).fetchall()
    return _decode_payload_rows(rows)


def search_chunks(
    store_path: Path,
    *,
    query_tokens: list[str],
    route_id: str | None = None,
    changed_paths: list[str] | None = None,
    module_path: str | None = None,
    chunk_kinds: list[str] | None = None,
    limit: int = 96,
) -> list[dict[str, Any]]:
    normalized_tokens = list(dict.fromkeys(token.strip().lower() for token in query_tokens if token.strip()))[:12]
    if not store_path.exists() or limit <= 0:
        return []
    where_clauses: list[str] = []
    where_params: list[Any] = []
    normalized_module_path = str(module_path or "").strip()
    if normalized_module_path and normalized_module_path != ".":
        where_clauses.append("(path = ? OR path LIKE ?)")
        where_params.extend([normalized_module_path, f"{normalized_module_path}/%"])
    normalized_chunk_kinds = [str(kind) for kind in (chunk_kinds or []) if str(kind).strip()]
    if normalized_chunk_kinds:
        where_clauses.append(f"chunk_kind IN ({_placeholder_list(normalized_chunk_kinds)})")
        where_params.extend(normalized_chunk_kinds)

    score_terms: list[str] = []
    score_params: list[Any] = []
    if route_id:
        normalized_route_id = str(route_id).strip().lower()
        if normalized_route_id:
            score_terms.append("CASE WHEN route_hints_text LIKE ? THEN 12.0 ELSE 0.0 END")
            score_params.append(f"%{normalized_route_id}%")
    for token in normalized_tokens:
        like_value = f"%{token}%"
        score_terms.extend(
            [
                "CASE WHEN path LIKE ? THEN 10.0 ELSE 0.0 END",
                "CASE WHEN tags_text LIKE ? THEN 8.0 ELSE 0.0 END",
                "CASE WHEN route_hints_text LIKE ? THEN 6.0 ELSE 0.0 END",
                "CASE WHEN symbols_text LIKE ? THEN 4.0 ELSE 0.0 END",
                "CASE WHEN summary_text LIKE ? THEN 4.0 ELSE 0.0 END",
                "CASE WHEN dependency_targets_text LIKE ? THEN 2.0 ELSE 0.0 END",
                "CASE WHEN search_text LIKE ? THEN 1.0 ELSE 0.0 END",
            ]
        )
        score_params.extend([like_value, like_value, like_value, like_value, like_value, like_value, like_value])
    normalized_changed_paths = [str(path) for path in (changed_paths or []) if str(path).strip()][:24]
    if normalized_changed_paths:
        score_terms.append(f"CASE WHEN path IN ({_placeholder_list(normalized_changed_paths)}) THEN 6.0 ELSE 0.0 END")
        score_params.extend(normalized_changed_paths)
    score_expr = " + ".join(score_terms) if score_terms else "0.0"
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    with _connect(store_path) as connection:
        rows = connection.execute(
            f"""
            SELECT payload_json
            FROM (
                SELECT
                    payload_json,
                    path,
                    line_start,
                    ({score_expr}) AS shortlist_score,
                    CASE
                        WHEN source_kind = 'project_memory' AND pin_state = 'pinned' AND note_status = 'active'
                        THEN 1
                        ELSE 0
                    END AS pinned_memory
                FROM chunks
                {where_sql}
            )
            WHERE shortlist_score > 0 OR pinned_memory = 1
            ORDER BY pinned_memory DESC, shortlist_score DESC, path ASC, line_start ASC
            LIMIT ?
            """,  # noqa: S608
            [*score_params, *where_params, int(limit)],
        ).fetchall()
    return _decode_payload_rows(rows)


def read_query_cache(store_path: Path, *, cache_kind: str, cache_key: str) -> dict[str, Any] | None:
    if not store_path.exists():
        return None
    with _connect(store_path) as connection:
        row = connection.execute(
            """
            SELECT cache_kind, cache_key, route_id, workspace_fingerprint, catalog_digest,
                   semantic_mode, created_at, source_hashes_json, payload_json
            FROM query_cache
            WHERE cache_kind = ? AND cache_key = ?
            """,
            (cache_kind, cache_key),
        ).fetchone()
    if row is None:
        return None
    payload = _decode(row["payload_json"], {})
    if not isinstance(payload, dict):
        return None
    return {
        "cache_kind": row["cache_kind"],
        "cache_key": row["cache_key"],
        "route_id": row["route_id"],
        "workspace_fingerprint": row["workspace_fingerprint"],
        "catalog_digest": row["catalog_digest"],
        "semantic_mode": row["semantic_mode"],
        "created_at": row["created_at"],
        "source_hashes": _decode(row["source_hashes_json"], {}),
        "payload": payload,
    }


def list_query_cache_entries(store_path: Path, *, cache_kind: str) -> list[dict[str, Any]]:
    if not store_path.exists():
        return []
    with _connect(store_path) as connection:
        rows = connection.execute(
            """
            SELECT cache_kind, cache_key, route_id, workspace_fingerprint, catalog_digest,
                   semantic_mode, created_at, source_hashes_json, payload_json
            FROM query_cache
            WHERE cache_kind = ?
            ORDER BY created_at
            """,
            (cache_kind,),
        ).fetchall()
    entries: list[dict[str, Any]] = []
    for row in rows:
        payload = _decode(row["payload_json"], {})
        if not isinstance(payload, dict):
            continue
        entries.append(
            {
                "cache_kind": row["cache_kind"],
                "cache_key": row["cache_key"],
                "route_id": row["route_id"],
                "workspace_fingerprint": row["workspace_fingerprint"],
                "catalog_digest": row["catalog_digest"],
                "semantic_mode": row["semantic_mode"],
                "created_at": row["created_at"],
                "source_hashes": _decode(row["source_hashes_json"], {}),
                "payload": payload,
            }
        )
    return entries


def replace_query_cache_entries(
    store_path: Path,
    *,
    cache_kind: str,
    entries: list[dict[str, Any]],
    limit: int | None = None,
) -> None:
    retained = entries[-limit:] if limit is not None and limit > 0 else entries
    with _connect(store_path) as connection:
        connection.execute("DELETE FROM query_cache WHERE cache_kind = ?", (cache_kind,))
        if retained:
            connection.executemany(
                """
                INSERT INTO query_cache(
                    cache_kind,
                    cache_key,
                    route_id,
                    workspace_fingerprint,
                    catalog_digest,
                    semantic_mode,
                    created_at,
                    source_hashes_json,
                    payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        cache_kind,
                        str(entry.get("cache_key") or ""),
                        entry.get("route_id"),
                        entry.get("workspace_fingerprint"),
                        entry.get("catalog_digest"),
                        entry.get("semantic_mode"),
                        entry.get("created_at"),
                        _encode(entry.get("source_hashes") or {}),
                        _encode(entry.get("payload") or {}),
                    )
                    for entry in retained
                ],
            )
        connection.commit()


def upsert_query_cache_entry(
    store_path: Path,
    *,
    cache_kind: str,
    cache_key: str,
    payload: dict[str, Any],
    route_id: str | None,
    workspace_fingerprint: str | None,
    catalog_digest: str | None,
    semantic_mode: str | None,
    created_at: str | None,
    source_hashes: dict[str, Any] | None = None,
    limit_per_kind: int | None = None,
) -> None:
    with _connect(store_path) as connection:
        connection.execute(
            """
            INSERT INTO query_cache(
                cache_kind,
                cache_key,
                route_id,
                workspace_fingerprint,
                catalog_digest,
                semantic_mode,
                created_at,
                source_hashes_json,
                payload_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_kind, cache_key) DO UPDATE SET
                route_id = excluded.route_id,
                workspace_fingerprint = excluded.workspace_fingerprint,
                catalog_digest = excluded.catalog_digest,
                semantic_mode = excluded.semantic_mode,
                created_at = excluded.created_at,
                source_hashes_json = excluded.source_hashes_json,
                payload_json = excluded.payload_json
            """,
            (
                cache_kind,
                cache_key,
                route_id,
                workspace_fingerprint,
                catalog_digest,
                semantic_mode,
                created_at,
                _encode(source_hashes or {}),
                _encode(payload),
            ),
        )
        if limit_per_kind is not None and limit_per_kind > 0:
            connection.execute(
                """
                DELETE FROM query_cache
                WHERE cache_kind = ?
                  AND cache_key NOT IN (
                      SELECT cache_key
                      FROM query_cache
                      WHERE cache_kind = ?
                      ORDER BY created_at DESC, cache_key DESC
                      LIMIT ?
                  )
                """,
                (cache_kind, cache_kind, int(limit_per_kind)),
            )
        connection.commit()


def context_store_summary(store_path: Path) -> dict[str, Any]:
    summary = {
        "backend": "sqlite",
        "path": str(store_path),
        "schema_version": CONTEXT_STORE_SCHEMA_VERSION,
        "module_count": 0,
        "file_count": 0,
        "chunk_count": 0,
        "query_cache_entry_count": 0,
    }
    if not store_path.exists():
        return summary
    with _connect(store_path) as connection:
        summary["module_count"] = int(connection.execute("SELECT COUNT(*) FROM modules").fetchone()[0])
        summary["file_count"] = int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0])
        summary["chunk_count"] = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        summary["query_cache_entry_count"] = int(connection.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0])
    return summary
