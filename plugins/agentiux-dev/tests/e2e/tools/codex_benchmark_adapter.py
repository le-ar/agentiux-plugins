from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from agentiux_dev_context import show_workspace_context_pack, triage_repo_request
from agentiux_dev_context_cache import context_cache_paths
from agentiux_dev_context_projection import (
    candidate_signatures,
    candidate_signatures_match,
    command_request_context,
    source_hashes_match,
)
from agentiux_dev_context_store import (
    QUERY_CACHE_BENCHMARK_PROJECTION_KIND,
    read_query_cache,
    upsert_query_cache_entry,
)
from agentiux_dev_lib import now_iso, plugin_info, python_launcher_tokens
from agentiux_dev_text import short_hash, tokenize_text


ROUTE_PLACEHOLDER = "<resolved-route>"
REQUEST_PLACEHOLDER = "<user-request>"


def _cache_key(
    request_text: str | None,
    *,
    catalog_digest: str,
    route_id: str | None,
    semantic_mode: str,
) -> str:
    normalized_request = " ".join(tokenize_text(request_text or "")) or "none"
    return short_hash(
        f"benchmark-bootstrap:{catalog_digest}:{route_id or 'none'}:{semantic_mode}:{normalized_request}",
        length=16,
    )


def _helper_command(*tokens: str) -> str:
    return " ".join(
        shlex.quote(token)
        for token in [*python_launcher_tokens(), str(Path(plugin_info()["current_root"]) / "scripts" / "agentiux_dev_state.py"), *tokens]
    )


def _bootstrap_candidates_from_triage(
    triage_payload: dict[str, Any],
    *,
    fallback_owner_candidates: list[dict[str, Any]],
    request_text: str | None,
) -> list[dict[str, Any]]:
    query_tokens = set(tokenize_text(request_text or ""))
    include_supporting_paths = bool(triage_payload.get("candidate_commands")) or bool(
        query_tokens.intersection({"playwright", "spec", "test", "tests", "verification", "verify"})
    ) or not bool(triage_payload.get("answer_ready"))
    primary_paths = [
        str(path).strip()
        for path in (triage_payload.get("primary_owner_files") or [])
        if isinstance(path, str) and str(path).strip()
    ]
    supporting_paths = [
        str(path).strip()
        for path in (triage_payload.get("supporting_evidence_files") or [])
        if isinstance(path, str) and str(path).strip()
    ]
    ordered_paths = [*primary_paths, *supporting_paths] if include_supporting_paths else primary_paths
    owner_lookup = {
        str(item.get("path") or "").strip(): dict(item)
        for item in (triage_payload.get("owner_candidates") or [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }
    candidate_paths: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for path_text in ordered_paths:
        if not path_text or path_text in seen_paths:
            continue
        item = dict(owner_lookup.get(path_text) or {})
        item["path"] = path_text
        item["role"] = "primary" if path_text in primary_paths else "supporting"
        if not item.get("summary"):
            item["summary"] = (
                "Primary owner evidence for the bounded triage packet."
                if item["role"] == "primary"
                else "Supporting evidence attached to the bounded triage packet."
            )
        candidate_paths.append(item)
        seen_paths.add(path_text)
    return candidate_paths[:6] or fallback_owner_candidates[:6]


def _bootstrap_command_hints_from_triage(triage_payload: dict[str, Any]) -> list[dict[str, Any]]:
    visible_commands = [
        str(command).strip()
        for command in (triage_payload.get("candidate_commands") or [])
        if isinstance(command, str) and str(command).strip()
    ]
    if not visible_commands:
        return []
    suggestions = [
        dict(item)
        for item in (triage_payload.get("command_suggestions") or [])
        if isinstance(item, dict) and isinstance(item.get("command"), str) and str(item.get("command") or "").strip()
    ]
    suggestion_lookup = {
        str(item.get("command") or "").strip(): item
        for item in suggestions
    }
    command_hints: list[dict[str, Any]] = []
    for command in visible_commands:
        hint = suggestion_lookup.get(command)
        if hint is not None:
            command_hints.append(hint)
            continue
        command_hints.append({"command": command})
    return command_hints[:4]


def _markdown(
    *,
    workspace: Path,
    request_text: str | None,
    route_id: str | None,
    route_status: str,
    owner_candidates: list[dict[str, Any]],
    command_hints: list[dict[str, Any]],
    why_summary: dict[str, Any] | None,
    confidence: float | int | None,
) -> str:
    helper_request = request_text or REQUEST_PLACEHOLDER
    helper_route = route_id or ROUTE_PLACEHOLDER
    query_tokens = set(tokenize_text(request_text or ""))
    candidate_lines = [
        f"- `{item['path']}` - {item.get('summary', 'workspace context match')}"
        for item in owner_candidates
        if isinstance(item.get("path"), str) and item.get("path")
    ]
    if not candidate_lines:
        candidate_lines = ["- Reload the warmed context pack, then inspect only the smallest matching file set."]
    lines = [
        "# AgentiUX Dev Benchmark Bootstrap",
        "",
        "This bootstrap is an evidence-only benchmark transport artifact. It is not part of the product runtime and must not be written into the repository.",
        "",
        f"- Workspace: `{workspace}`",
        f"- Request route: `{helper_route}` ({route_status})",
        f"- Context-pack confidence: `{float(confidence or 0):.2f}`",
        "- Do not include this generated bootstrap file in final candidate file lists; it is instruction transport only.",
        "- Start from the ranked owner-file candidates below before broad `rg`, `find`, or manual tree scans.",
        "- If the listed candidate files already answer the request, stop after reading them and return JSON instead of running broad shell exploration.",
        "- Never return the plugin helper commands below as final `candidate_commands`; they are only retrieval fallback inside the benchmark session.",
        "",
        "## Warm Context Candidates",
        "",
        *candidate_lines,
        "",
        "Prefer implementation/runtime files over docs, tests, generated bootstrap files, and UI wrappers when both mention the same concept.",
        "",
    ]
    summary_text = str((why_summary or {}).get("summary") or "").strip()
    if summary_text:
        lines.extend(
            [
                "## Why These Files",
                "",
                summary_text,
                "",
            ]
        )
    if command_hints:
        lines.extend(
            [
                "## Likely Package-Level Commands",
                "",
                "Return final `candidate_commands` only from this exact package-owned list when the task asks for verification or triage commands.",
                f"- Final `candidate_commands` should be a subset of these {len(command_hints)} commands.",
                "- Do not rewrite them into `cd <dir> && ...`, direct `playwright` or `tsx` invocations, or path-based variants such as `pnpm --filter ./apps/server ...`.",
                "- If one of these commands matches the task, prefer it over expanding the raw script body from `package.json`.",
                "",
                *[
                    f"- `{item['command']}` - from `{item['source_path']}` script `{item['script_name']}`"
                    for item in command_hints
                ],
                "",
            ]
        )
    elif command_request_context(query_tokens, route_id):
        lines.extend(
            [
                "## Package-Level Commands",
                "",
                "No exact package-owned verification command was derived from the warmed candidates.",
                "- Do not invent `candidate_commands` from guessed script names, `cd` wrappers, or raw tool invocations.",
                "- Inspect the ranked package manifests first and return `candidate_commands=[]` if no exact package-owned command is confirmed.",
                "",
            ]
        )
    lines.extend(
        [
            "## Fallback Plugin Retrieval Commands",
            "",
            "These are plugin retrieval commands only. They must never appear in final `candidate_commands`.",
            "",
            "1. Run front-door repo triage before any broad shell exploration:",
            "",
            "```bash",
            _helper_command(
                "triage-repo-request",
                "--workspace",
                str(workspace),
                "--route-id",
                helper_route,
                "--request-text",
                helper_request,
                "--semantic-mode",
                "disabled",
            ),
            "```",
            "",
            "2. Reload the warmed context pack if the candidate list still looks stale or contradictory:",
            "",
            "```bash",
            _helper_command(
                "show-workspace-context-pack",
                "--workspace",
                str(workspace),
                "--route-id",
                helper_route,
                "--request-text",
                helper_request,
                "--semantic-mode",
                "disabled",
            ),
            "```",
            "",
            "3. If you still need evidence, run indexed search before broad shell search:",
            "",
            "```bash",
            _helper_command(
                "search-context-index",
                "--workspace",
                str(workspace),
                "--route-id",
                helper_route,
                "--query-text",
                helper_request,
                "--semantic-mode",
                "disabled",
            ),
            "```",
            "",
            "4. Only if route or owner-path evidence is still ambiguous, inspect compact structure before manual reads:",
            "",
            "```bash",
            _helper_command(
                "show-context-structure",
                "--workspace",
                str(workspace),
                "--route-id",
                helper_route,
                "--query-text",
                helper_request,
                "--semantic-mode",
                "disabled",
            ),
            "```",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def build_codex_benchmark_bootstrap(
    workspace: str | Path,
    request_text: str | None = None,
    route_id: str | None = None,
    limit: int | None = 6,
    force_refresh: bool = False,
    semantic_mode: str | None = "disabled",
) -> dict[str, Any]:
    resolved_workspace = Path(workspace).expanduser().resolve()
    cache_paths = context_cache_paths(resolved_workspace)
    context_payload = show_workspace_context_pack(
        resolved_workspace,
        request_text=request_text,
        route_id=route_id,
        limit=limit,
        force_refresh=force_refresh,
        semantic_mode=semantic_mode,
    )
    context_pack = context_payload.get("context_pack") or {}
    initial_resolved_route = context_payload.get("resolved_route") or {}
    resolved_route = initial_resolved_route
    workspace_context = context_payload.get("workspace_context") or {}
    owner_route_id = context_pack.get("owner_route_id") or resolved_route.get("route_id") or route_id
    if context_pack.get("owner_route_override_applied") and owner_route_id:
        resolved_route = {**initial_resolved_route, "route_id": owner_route_id}
    cache_key = _cache_key(
        request_text,
        catalog_digest=str(workspace_context.get("catalog_digest") or ""),
        route_id=owner_route_id,
        semantic_mode=str(context_payload.get("semantic_mode") or "disabled"),
    )
    cached = read_query_cache(
        cache_paths["context_store"],
        cache_kind=QUERY_CACHE_BENCHMARK_PROJECTION_KIND,
        cache_key=cache_key,
    )
    if cached:
        cached_bootstrap = cached.get("payload") or {}
        if (
            int(cached_bootstrap.get("schema_version") or 0) == 2
            and cached.get("workspace_fingerprint") == workspace_context.get("workspace_fingerprint")
            and cached.get("catalog_digest") == workspace_context.get("catalog_digest")
            and cached.get("semantic_mode") == context_payload.get("semantic_mode")
            and cached_bootstrap.get("context_pack_query_fingerprint") == context_pack.get("query_fingerprint")
            and source_hashes_match(cached.get("source_hashes") or {}, context_pack.get("source_hashes") or {})
            and candidate_signatures_match(resolved_workspace, cached_bootstrap.get("candidate_signatures") or {})
        ):
            bootstrap_payload = cached_bootstrap.get("bootstrap") or {}
            cache_status = "hit"
        else:
            cached = None
    if not cached:
        fallback_owner_candidates = [
            item
            for item in context_pack.get("owner_candidates") or []
            if isinstance(item, dict) and isinstance(item.get("path"), str) and item.get("path")
        ]
        triage_payload = triage_repo_request(
            resolved_workspace,
            request_text=request_text,
            route_id=owner_route_id,
            limit=limit,
            force_refresh=force_refresh,
            semantic_mode=semantic_mode,
        )
        owner_candidates = _bootstrap_candidates_from_triage(
            triage_payload,
            fallback_owner_candidates=fallback_owner_candidates,
            request_text=request_text,
        )
        command_hints = _bootstrap_command_hints_from_triage(triage_payload)
        triage_route = triage_payload.get("resolved_route") or {}
        bootstrap_route_id = str(
            triage_payload.get("owner_route_id")
            or triage_route.get("route_id")
            or owner_route_id
            or ""
        ).strip() or None
        bootstrap_payload = {
            "schema_version": 2,
            "target_filename": "codex-bootstrap.md",
            "delivery_config_key": "model_instructions_file",
            "write_policy": "write outside the benchmark workspace clone and pass with `codex exec -c model_instructions_file=<path>`; the product runtime does not mutate the repository",
            "route_id": bootstrap_route_id,
            "candidate_paths": owner_candidates,
            "command_hints": command_hints,
            "commands": [
                _helper_command(
                    "triage-repo-request",
                    "--workspace",
                    str(resolved_workspace),
                    "--route-id",
                    bootstrap_route_id or ROUTE_PLACEHOLDER,
                    "--request-text",
                    request_text or REQUEST_PLACEHOLDER,
                    "--semantic-mode",
                    "disabled",
                ),
                _helper_command(
                    "show-workspace-context-pack",
                    "--workspace",
                    str(resolved_workspace),
                    "--route-id",
                    bootstrap_route_id or ROUTE_PLACEHOLDER,
                    "--request-text",
                    request_text or REQUEST_PLACEHOLDER,
                    "--semantic-mode",
                    "disabled",
                ),
                _helper_command(
                    "search-context-index",
                    "--workspace",
                    str(resolved_workspace),
                    "--route-id",
                    bootstrap_route_id or ROUTE_PLACEHOLDER,
                    "--query-text",
                    request_text or REQUEST_PLACEHOLDER,
                    "--semantic-mode",
                    "disabled",
                ),
                _helper_command(
                    "show-context-structure",
                    "--workspace",
                    str(resolved_workspace),
                    "--route-id",
                    bootstrap_route_id or ROUTE_PLACEHOLDER,
                    "--query-text",
                    request_text or REQUEST_PLACEHOLDER,
                    "--semantic-mode",
                    "disabled",
                ),
            ],
            "markdown": _markdown(
                workspace=resolved_workspace,
                request_text=request_text,
                route_id=bootstrap_route_id,
                route_status=str(
                    triage_payload.get("route_resolution_status")
                    or (
                        "owner-route-override"
                        if context_pack.get("owner_route_override_applied")
                        else context_payload.get("route_resolution_status", "unresolved")
                    )
                ),
                owner_candidates=owner_candidates,
                command_hints=command_hints,
                why_summary=triage_payload.get("why_these_files") or context_pack.get("why_these_files") or {},
                confidence=triage_payload.get("confidence") or context_pack.get("confidence"),
            ),
        }
        upsert_query_cache_entry(
            cache_paths["context_store"],
            cache_kind=QUERY_CACHE_BENCHMARK_PROJECTION_KIND,
            cache_key=cache_key,
            payload={
                "schema_version": 2,
                "context_pack_query_fingerprint": context_pack.get("query_fingerprint"),
                "candidate_signatures": candidate_signatures(resolved_workspace, owner_candidates),
                "bootstrap": bootstrap_payload,
            },
            route_id=owner_route_id,
            workspace_fingerprint=workspace_context.get("workspace_fingerprint"),
            catalog_digest=workspace_context.get("catalog_digest"),
            semantic_mode=context_payload.get("semantic_mode"),
            created_at=now_iso(),
            source_hashes=context_pack.get("source_hashes") or {},
            limit_per_kind=40,
        )
        cache_status = "miss"
    return {
        "workspace_path": str(resolved_workspace),
        "request_text": request_text,
        "initial_resolved_route": initial_resolved_route or None,
        "resolved_route": resolved_route or None,
        "route_resolution_status": context_payload.get("route_resolution_status"),
        "bootstrap_route_override_applied": bool(context_pack.get("owner_route_override_applied")),
        "bootstrap_route_override_from": (
            initial_resolved_route.get("route_id")
            if context_pack.get("owner_route_override_applied")
            else None
        ),
        "bootstrap_cache_status": cache_status,
        "bootstrap_store_path": str(cache_paths["context_store"]),
        "cache_status": context_payload.get("cache_status"),
        "cache_root": context_payload.get("cache_root"),
        "index_status": context_payload.get("index_status"),
        "index_refresh_reason": context_payload.get("index_refresh_reason"),
        "semantic_mode": context_payload.get("semantic_mode"),
        "semantic_backend_status": context_payload.get("semantic_backend_status"),
        "workspace_context": workspace_context,
        "bootstrap": bootstrap_payload,
        "context_pack": context_pack,
        "storage_backend": context_payload.get("storage_backend"),
        "storage_summary": context_payload.get("storage_summary"),
        "context_store_path": context_payload.get("context_store_path"),
    }
