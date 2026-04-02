from __future__ import annotations

import copy
import json
from typing import Any

from agentiux_dev_request_intent import has_execution_intent
from agentiux_dev_text import match_keywords, normalize_command_phrase


SURFACE_PAYLOAD_CEILINGS = {
    "workflow_advice": 12 * 1024,
    "show_intent_route": 12 * 1024,
    "search_context_index": 24 * 1024,
    "show_workspace_context_pack": 32 * 1024,
}

RETRIEVAL_MODE_PROFILES = {
    "orientation": {
        "mode": "orientation",
        "max_targeted_reads": 3,
        "initial_read_budget_kb": 12,
        "candidate_file_limit": 96,
        "max_match_limit": 6,
        "max_selected_chunk_limit": 8,
        "max_selected_tool_limit": 8,
        "heavy_path_conditions": [
            "cheap path could not resolve a route or next file",
            "route-specific context was insufficient for the next targeted read",
        ],
    },
    "audit": {
        "mode": "audit",
        "max_targeted_reads": 5,
        "initial_read_budget_kb": 20,
        "candidate_file_limit": 160,
        "max_match_limit": 8,
        "max_selected_chunk_limit": 10,
        "max_selected_tool_limit": 10,
        "heavy_path_conditions": [
            "a hotspot or payload/latency drift needs deeper inspection",
            "the user explicitly requested a repository audit or broad analysis",
        ],
    },
    "fix": {
        "mode": "fix",
        "max_targeted_reads": 4,
        "initial_read_budget_kb": 16,
        "candidate_file_limit": 128,
        "max_match_limit": 6,
        "max_selected_chunk_limit": 8,
        "max_selected_tool_limit": 8,
        "heavy_path_conditions": [
            "the bug or regression boundary could not be localized cheaply",
            "the nearest targeted context is insufficient to patch safely",
        ],
    },
    "execution": {
        "mode": "execution",
        "max_targeted_reads": 6,
        "initial_read_budget_kb": 24,
        "candidate_file_limit": 176,
        "max_match_limit": 10,
        "max_selected_chunk_limit": 12,
        "max_selected_tool_limit": 10,
        "heavy_path_conditions": [
            "an active slice is known but the implementation boundary still needs deeper reads",
            "cheap path cannot safely continue the current execution slice",
        ],
    },
}

FIX_RETRIEVAL_HINTS = [
    "fix",
    "bug",
    "patch",
    "hotfix",
    "regression",
    "spacing",
    "padding",
    "rename",
    "warning",
]

AUDIT_RETRIEVAL_HINTS = [
    "audit",
    "review",
    "assess",
    "analyze",
    "latency",
    "payload",
    "performance",
    "over-fetch",
    "serialization",
    "hotspot",
]


def payload_size_bytes(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def retrieval_mode_profile(mode: str | None) -> dict[str, Any]:
    return copy.deepcopy(RETRIEVAL_MODE_PROFILES.get(mode or "", RETRIEVAL_MODE_PROFILES["orientation"]))


def infer_retrieval_mode(request_text: str | None, *, execution_intent: bool | None = None) -> str:
    normalized = normalize_command_phrase(request_text)
    if not normalized:
        return "orientation"
    if execution_intent is None:
        execution_intent = has_execution_intent(normalized)
    if match_keywords(normalized, FIX_RETRIEVAL_HINTS):
        return "fix"
    if execution_intent:
        return "execution"
    if match_keywords(normalized, AUDIT_RETRIEVAL_HINTS):
        return "audit"
    return "orientation"


def retrieval_policy_payload(surface_name: str, mode: str) -> dict[str, Any]:
    profile = retrieval_mode_profile(mode)
    return {
        "surface": surface_name,
        "mode": profile["mode"],
        "max_targeted_reads": profile["max_targeted_reads"],
        "initial_read_budget_kb": profile["initial_read_budget_kb"],
        "candidate_file_limit": profile["candidate_file_limit"],
        "max_match_limit": profile["max_match_limit"],
        "max_selected_chunk_limit": profile["max_selected_chunk_limit"],
        "max_selected_tool_limit": profile["max_selected_tool_limit"],
        "heavy_path_conditions": profile["heavy_path_conditions"][:],
    }
