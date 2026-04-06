from __future__ import annotations

import re
from typing import Any

from agentiux_dev_text import match_keywords, normalize_command_phrase


CANONICAL_COMMAND_SURFACE = [
    "initialize workspace",
    "preview reset workspace state",
    "reset workspace state",
    "preview repair workspace state",
    "repair workspace state",
    "show state paths",
    "show stages",
    "show active brief",
    "propose stage plan changes",
    "apply stage plan changes",
    "continue work",
    "close current stage",
    "launch gui",
    "stop gui",
    "show gui url",
    "run verification case",
    "run verification suite",
    "show verification log",
    "show verification recipes",
    "show verification helper catalog",
    "audit verification coverage",
    "sync verification helpers",
    "resolve verification",
    "show capability catalog",
    "show intent route",
    "show workspace context pack",
    "search context index",
    "show context structure",
    "run analysis audit",
    "refresh context index",
    "approve verification baseline",
    "update verification baseline",
    "show host support",
    "show host setup plan",
    "install host requirements",
    "repair host requirements",
    "create workstream",
    "list workstreams",
    "switch workstream",
    "show current workstream",
    "close current workstream",
    "create task",
    "switch task",
    "list tasks",
    "show current task",
    "close current task",
    "show auth profiles",
    "write auth profile",
    "remove auth profile",
    "resolve auth profile",
    "list auth sessions",
    "get auth session",
    "write auth session",
    "invalidate auth session",
    "remove auth session",
    "list project notes",
    "get project note",
    "write project note",
    "archive project note",
    "search project notes",
    "get analytics snapshot",
    "list learning entries",
    "write learning entry",
    "update learning entry",
    "show youtrack connections",
    "connect youtrack",
    "update youtrack connection",
    "remove youtrack connection",
    "search youtrack issues",
    "show youtrack issue queue",
    "propose youtrack workstream plan",
    "apply youtrack workstream plan",
    "audit repository",
    "show upgrade plan",
    "apply upgrade plan",
    "create starter",
    "show starter presets",
    "suggest branch name",
    "suggest commit message",
    "suggest pr title",
    "suggest pr body",
    "show git workflow advice",
    "show git state",
    "plan git change",
    "create git branch",
    "stage git files",
    "create git commit",
]

EXECUTION_INTENT_HINTS = [
    "implement",
    "build",
    "create",
    "add",
    "fix",
    "update",
    "refactor",
    "continue work",
    "ship",
]

GREENFIELD_HINTS = [
    "from scratch",
    "greenfield",
    "new project",
    "new repo",
    "new repository",
    "new app",
    "create project",
    "create app",
    "bootstrap",
    "starter",
    "scaffold",
    "boilerplate",
]

LARGE_WORK_HINTS = [
    "feature",
    "epic",
    "flow",
    "screen",
    "module",
    "system",
    "architecture",
    "migration",
    "checkout",
    "authentication",
    "onboarding",
    "dashboard",
    "fullstack",
    "multi-step",
    "parallel",
    "integration",
    "admin panel",
]

SMALL_TASK_HINTS = [
    "fix",
    "bug",
    "typo",
    "spacing",
    "padding",
    "margin",
    "rename",
    "adjust",
    "tweak",
    "patch",
    "button",
    "copy",
    "text",
    "lint",
    "warning",
    "color",
    "minor",
    "small",
    "hotfix",
]

NEGATIVE_LARGE_WORK_HINTS = [
    "typo",
    "spacing",
    "padding",
    "margin",
    "rename",
    "copy",
    "lint",
    "warning",
    "button",
    "minor",
    "small",
    "hotfix",
]

NEGATIVE_SMALL_TASK_HINTS = [
    "from scratch",
    "greenfield",
    "new project",
    "new app",
    "migration",
    "architecture",
    "system",
    "platform",
    "multi-step",
    "across web and backend",
    "across frontend and backend",
    "fullstack",
]

TRIAGE_OWNER_ONLY_HINTS = [
    "owner file only",
    "owner files only",
    "owner-only",
    "return owner files only",
    "smallest owner file",
    "smallest owner files",
    "smallest set of files",
]

TRIAGE_OWNER_ROUTING_HINTS = [
    "owner file set",
    "owner files",
    "owner set",
    "route file",
    "route files",
    "entrypoint",
    "entry point",
    "shared package file",
    "shared package files",
]

TRIAGE_SUPPRESS_COMMAND_HINTS = [
    "candidate_commands should be empty",
    "candidate commands should be empty",
    "candidate commands empty",
    "do not include commands",
    "no commands",
    "without commands",
]

TRIAGE_COMMAND_REQUEST_HINTS = [
    "package-level command",
    "package-level commands",
    "package level command",
    "package level commands",
    "minimal package-level command",
    "minimal package-level commands",
    "verification command",
    "verification commands",
    "command to inspect next",
    "commands you would use",
]

TRIAGE_EXCLUDED_FAMILY_ALIASES = {
    "admin": ["admin", "admin checkout", "admin-console"],
    "storefront": ["storefront", "customer storefront"],
    "server": ["server", "backend", "readiness backend"],
    "docs": ["docs", "documentation", "readme"],
    "tests": ["tests", "test", "spec", "specs"],
}

AUDIT_HINTS = [
    "audit repository",
    "upgrade plan",
    "audit",
    "review repository",
    "assess repository",
    "onboard repository",
]

COMMIT_HINTS = [
    "commit",
    "git commit",
    "commit changes",
    "commit this",
]

STARTER_PRESET_HINTS = {
    "next-web": ["next", "web", "website", "landing", "tailwind", "react app"],
    "expo-mobile": ["expo", "mobile", "ios", "android", "react native", "nativewind"],
    "nestjs-api": ["nestjs", "api", "backend", "service", "node api"],
    "rust-service": ["rust", "worker", "daemon", "service"],
    "nx-fullstack": ["nx", "monorepo", "fullstack", "workspace"],
}

STARTER_PRESETS: dict[str, dict[str, Any]] = {
    "next-web": {
        "preset_id": "next-web",
        "display_name": "Next.js Web Starter",
        "description": "Next.js, TypeScript, Tailwind, Docker Compose local infra hooks, and Playwright-ready verification setup.",
        "kind": "web",
        "required_commands": ["npx"],
        "bootstrap_commands": [
            {
                "argv": [
                    "npx",
                    "create-next-app@latest",
                    "__PROJECT_NAME__",
                    "--ts",
                    "--tailwind",
                    "--eslint",
                    "--app",
                    "--src-dir",
                    "--use-npm",
                    "--yes",
                ],
                "cwd": "__DESTINATION_ROOT__",
            }
        ],
        "post_setup": {
            "docker_compose": False,
            "verification_profile": "web",
            "design_platform": "web",
        },
    },
    "expo-mobile": {
        "preset_id": "expo-mobile",
        "display_name": "Expo Mobile Starter",
        "description": "Expo, React Native, TypeScript, Nativewind, and deterministic mobile verification hooks.",
        "kind": "mobile",
        "required_commands": ["npx"],
        "bootstrap_commands": [
            {
                "argv": [
                    "npx",
                    "create-expo-app@latest",
                    "__PROJECT_NAME__",
                    "--template",
                    "blank-typescript",
                ],
                "cwd": "__DESTINATION_ROOT__",
            }
        ],
        "post_setup": {
            "docker_compose": False,
            "verification_profile": "mobile",
            "design_platform": "expo",
        },
    },
    "nestjs-api": {
        "preset_id": "nestjs-api",
        "display_name": "NestJS API Starter",
        "description": "NestJS API with Docker Compose local infra notes and backend verification hooks.",
        "kind": "backend",
        "required_commands": ["npx"],
        "bootstrap_commands": [
            {
                "argv": [
                    "npx",
                    "@nestjs/cli",
                    "new",
                    "__PROJECT_NAME__",
                    "--package-manager",
                    "npm",
                    "--skip-git",
                ],
                "cwd": "__DESTINATION_ROOT__",
            }
        ],
        "post_setup": {
            "docker_compose": True,
            "verification_profile": "backend",
            "design_platform": None,
        },
    },
    "rust-service": {
        "preset_id": "rust-service",
        "display_name": "Rust Service Starter",
        "description": "Rust service with Docker Compose local infra notes and service smoke verification hooks.",
        "kind": "service",
        "required_commands": ["cargo"],
        "bootstrap_commands": [
            {
                "argv": ["cargo", "new", "__PROJECT_NAME__", "--bin"],
                "cwd": "__DESTINATION_ROOT__",
            }
        ],
        "post_setup": {
            "docker_compose": True,
            "verification_profile": "backend",
            "design_platform": None,
        },
    },
    "nx-fullstack": {
        "preset_id": "nx-fullstack",
        "display_name": "Nx Fullstack Starter",
        "description": "Nx monorepo with Next.js web, NestJS API, shared libs, and monorepo-aware verification.",
        "kind": "monorepo",
        "required_commands": ["npx"],
        "bootstrap_commands": [
            {
                "argv": [
                    "npx",
                    "create-nx-workspace@latest",
                    "__PROJECT_NAME__",
                    "--preset=apps",
                    "--appName=web",
                    "--style=tailwind",
                    "--bundler=next",
                    "--routing",
                    "--interactive=false",
                    "--packageManager=npm",
                ],
                "cwd": "__DESTINATION_ROOT__",
            },
            {
                "argv": [
                    "npx",
                    "nx",
                    "g",
                    "@nx/nest:app",
                    "api",
                    "--frontendProject=web",
                ],
                "cwd": "__PROJECT_ROOT__",
            },
            {
                "argv": [
                    "npx",
                    "nx",
                    "g",
                    "@nx/js:lib",
                    "shared",
                    "--bundler=none",
                    "--unitTestRunner=none",
                ],
                "cwd": "__PROJECT_ROOT__",
            },
        ],
        "post_setup": {
            "docker_compose": True,
            "verification_profile": "monorepo",
            "design_platform": "web",
        },
    },
}

COMMAND_ALIAS_TABLE = {command: [command] for command in CANONICAL_COMMAND_SURFACE}
COMMAND_ALIAS_TABLE.update(
    {
        "initialize workspace": ["initialize workspace", "init workspace"],
        "show active brief": ["show active brief", "active brief"],
        "show capability catalog": ["show capability catalog", "capability catalog"],
        "show intent route": ["show intent route", "intent route"],
        "show workspace context pack": ["show workspace context pack", "workspace context pack", "context pack"],
        "search context index": ["search context index", "context index search", "search index"],
        "show context structure": ["show context structure", "context structure", "structure view"],
        "run analysis audit": ["run analysis audit", "analysis audit", "audit analysis"],
        "refresh context index": ["refresh context index", "rebuild context index"],
        "show starter presets": ["show starter presets", "starter presets"],
        "show git workflow advice": ["show git workflow advice", "git workflow advice"],
        "audit repository": ["audit repository", "repo audit"],
        "show upgrade plan": ["show upgrade plan", "upgrade plan"],
    }
)


def command_aliases() -> dict[str, list[str]]:
    return {
        canonical: [normalize_command_phrase(alias) for alias in aliases]
        for canonical, aliases in COMMAND_ALIAS_TABLE.items()
    }


def resolve_command_phrase(phrase: str | None) -> str | None:
    normalized = normalize_command_phrase(phrase)
    if not normalized:
        return None
    aliases = command_aliases()
    for canonical, variants in aliases.items():
        if normalized in variants:
            return canonical
    for canonical, variants in aliases.items():
        if any(normalized.startswith(f"{variant} ") or f" {variant} " in f" {normalized} " for variant in variants):
            return canonical
    return None


def has_execution_intent(normalized_text: str) -> bool:
    return bool(match_keywords(normalized_text, EXECUTION_INTENT_HINTS))


def recommend_starter_preset(request_text: str | None) -> dict[str, Any] | None:
    normalized = normalize_command_phrase(request_text)
    if not normalized:
        return None
    scored: list[tuple[int, str]] = []
    for preset_id, keywords in STARTER_PRESET_HINTS.items():
        score = len(match_keywords(normalized, keywords))
        if score:
            scored.append((score, preset_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return None
    recommended = scored[0][1]
    alternatives = [preset_id for _, preset_id in scored[1:4]]
    return {
        "recommended_preset_id": recommended,
        "recommended_preset": STARTER_PRESETS[recommended],
        "alternative_preset_ids": alternatives,
        "alternatives": [STARTER_PRESETS[preset_id] for preset_id in alternatives],
        "reason": "Request text matches the preset signals for this starter.",
    }


def analyze_request_text(
    request_text: str | None,
    *,
    canonical_request_text: str | None = None,
) -> dict[str, Any]:
    analysis_text = canonical_request_text or request_text
    normalized = normalize_command_phrase(analysis_text)
    original_normalized = normalize_command_phrase(request_text)
    recognized_command = resolve_command_phrase(normalized) if normalized else None
    greenfield_matches = match_keywords(normalized, GREENFIELD_HINTS)
    large_matches = match_keywords(normalized, LARGE_WORK_HINTS)
    small_matches = match_keywords(normalized, SMALL_TASK_HINTS)
    audit_matches = match_keywords(normalized, AUDIT_HINTS)
    commit_matches = match_keywords(normalized, COMMIT_HINTS)
    negative_large_matches = match_keywords(normalized, NEGATIVE_LARGE_WORK_HINTS)
    negative_small_matches = match_keywords(normalized, NEGATIVE_SMALL_TASK_HINTS)
    execution_intent = has_execution_intent(normalized)
    word_count = len(normalized.split())

    request_kind = "neutral"
    recommended_mode = None
    reason = "No stage-aware routing recommendation is required yet."
    fallback_reason = reason

    greenfield_score = len(greenfield_matches) * 6
    commit_score = len(commit_matches) * 6
    audit_score = len(audit_matches) * 5
    large_feature_score = (
        len(large_matches) * 3
        + (2 if execution_intent else 0)
        + (2 if word_count >= 18 else 0)
        - len(negative_large_matches) * 2
        - len(small_matches)
    )
    point_task_score = (
        len(small_matches) * 3
        + (2 if execution_intent else 0)
        + (2 if word_count <= 14 else 0)
        - len(negative_small_matches) * 2
        - len(large_matches)
    )

    if greenfield_score > 0:
        request_kind = "greenfield"
        recommended_mode = "workstream"
        reason = "Greenfield product work is better tracked through a dedicated workstream."
    elif commit_score > 0:
        request_kind = "commit"
        reason = "Commit requests should inspect repo commit history or commit rules before generating a message."
    elif audit_score > 0:
        request_kind = "repository_audit"
        reason = "Repository audits should stay read-only until an upgrade plan is explicitly approved."
    elif large_feature_score >= max(point_task_score + 2, 4):
        request_kind = "large_feature"
        recommended_mode = "workstream"
        reason = "Large or multi-slice implementation work should use a named workstream."
    elif point_task_score >= max(large_feature_score + 2, 4):
        request_kind = "point_task"
        recommended_mode = "task"
        reason = "Narrow corrections and point fixes are cheaper to track as lightweight tasks."
    elif execution_intent:
        if word_count <= 14:
            request_kind = "point_task"
            recommended_mode = "task"
            fallback_reason = "Execution intent with a short request defaults to the lighter-weight task flow."
        elif word_count >= 18:
            request_kind = "large_feature"
            recommended_mode = "workstream"
            fallback_reason = "Execution intent with a broader request defaults to workstream mode when heuristics are inconclusive."
        reason = fallback_reason

    return {
        "request_text": request_text or "",
        "canonical_request_text": canonical_request_text or "",
        "analysis_text": analysis_text or "",
        "analysis_source": "canonical_request_text" if canonical_request_text else "request_text",
        "normalized_request": normalized,
        "original_normalized_request": original_normalized,
        "recognized_command": recognized_command,
        "execution_intent": execution_intent,
        "word_count": word_count,
        "request_kind": request_kind,
        "recommended_mode": recommended_mode,
        "reason": reason,
        "matched_keywords": {
            "greenfield": greenfield_matches,
            "large_work": large_matches,
            "small_task": small_matches,
            "audit": audit_matches,
            "commit": commit_matches,
            "negative_large_work": negative_large_matches,
            "negative_small_task": negative_small_matches,
        },
    }


def parse_runtime_triage_constraints(request_text: str | None) -> dict[str, Any]:
    normalized = normalize_command_phrase(request_text)
    explicit_owner_only = bool(match_keywords(normalized, TRIAGE_OWNER_ONLY_HINTS))
    owner_file_routing = bool(match_keywords(normalized, TRIAGE_OWNER_ROUTING_HINTS))
    explicit_command_request = bool(match_keywords(normalized, TRIAGE_COMMAND_REQUEST_HINTS))
    owner_files_only = explicit_owner_only or (owner_file_routing and not explicit_command_request)
    suppress_commands = bool(match_keywords(normalized, TRIAGE_SUPPRESS_COMMAND_HINTS)) or bool(
        re.search(r"candidate[_ ]commands?\s*(?:should be|=)?\s*(?:empty|\[\s*\])", normalized)
    ) or ((explicit_owner_only or owner_file_routing) and not explicit_command_request)
    excluded_families_unless_imported: list[str] = []
    for family, aliases in TRIAGE_EXCLUDED_FAMILY_ALIASES.items():
        for alias in aliases:
            pattern = (
                r"(?:exclude|ignore|skip|avoid)\s+"
                + re.escape(alias)
                + r"(?:\s+[a-z0-9/_-]+){0,2}\s+unless\b[^.]{0,120}\bimport(?:ed|s?)\b"
            )
            if re.search(pattern, normalized):
                excluded_families_unless_imported.append(family)
                break
    return {
        "owner_files_only": owner_files_only,
        "suppress_commands": suppress_commands,
        "excluded_families_unless_imported": excluded_families_unless_imported,
        "normalized_request": normalized,
    }
