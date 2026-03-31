from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLUGIN_NAME = "agentiux-dev"
PLUGIN_VERSION = "0.8.0"
STATE_SCHEMA_VERSION = 7
SUPPORTED_HOSTS = ("windows", "linux", "macos")

STAGE_REGISTER_STORAGE = {
    "filename": "stage-register.yaml",
    "content_encoding": "json_text",
    "yaml_compatibility": "yaml_1_2",
    "machine_owned": True,
}

DEFAULT_WORKSTREAM_ID = "default"
NO_CURRENT_WORKSTREAM_MESSAGE = "No current workstream selected."
PLACEHOLDER_WORKSTREAM_TITLES = {"default", "default workstream", "active workstream"}
PLACEHOLDER_SCOPE_SUMMARIES = {
    "Primary product workstream.",
    "Migrated legacy workspace state.",
}
MIRROR_REGISTER_FIELDS = {
    "is_mirror",
    "mirror_of_workstream_id",
    "read_only_derived",
}

STAGE_STATUS_VALUES = {
    "planned",
    "in_progress",
    "blocked",
    "awaiting_user",
    "ready_for_closeout",
    "completed",
}

PLAN_STATUS_VALUES = {
    "needs_user_confirmation",
    "confirmed",
}

WORKSTREAM_STATUS_VALUES = {
    "planned",
    "active",
    "blocked",
    "on_hold",
    "completed",
    "archived",
}

TASK_STATUS_VALUES = {
    "planned",
    "active",
    "blocked",
    "awaiting_user",
    "completed",
    "cancelled",
}

BRIEF_PLACEHOLDER = """# StageExecutionBrief

No active execution brief is recorded yet.
"""

TASK_BRIEF_PLACEHOLDER = """# TaskBrief

No active task brief is recorded yet.
"""

TASK_TIME_TRACKING_SCHEMA_VERSION = 2


class StateFileError(ValueError):
    pass

DISCOVERY_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}

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
    "develop",
    "continue work",
    "ship",
    "\u0441\u0434\u0435\u043b\u0430\u0439",
    "\u0440\u0435\u0430\u043b\u0438\u0437\u0443\u0439",
    "\u0434\u043e\u0431\u0430\u0432\u044c",
    "\u0438\u0441\u043f\u0440\u0430\u0432\u044c",
    "\u043f\u043e\u0447\u0438\u043d\u0438",
    "\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0430\u0439",
]

GREENFIELD_HINTS = [
    "from scratch",
    "greenfield",
    "new project",
    "new app",
    "create project",
    "create app",
    "bootstrap",
    "starter",
    "scaffold",
    "\u0441 \u043d\u0443\u043b\u044f",
    "\u043d\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u0435\u043a\u0442",
    "\u0441\u043e\u0437\u0434\u0430\u0439 \u043f\u0440\u043e\u0435\u043a\u0442",
    "\u0441\u0442\u0430\u0440\u0442\u0435\u0440",
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
    "\u0444\u0438\u0447",
    "\u0431\u043e\u043b\u044c\u0448",
    "\u0430\u0440\u0445\u0438\u0442\u0435\u043a\u0442",
    "\u043c\u0438\u0433\u0440\u0430\u0446",
    "\u044d\u043a\u0440\u0430\u043d",
    "\u043c\u043e\u0434\u0443\u043b",
    "\u0432\u0435\u0442\u043a",
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
    "\u043f\u043e\u0444\u0438\u043a\u0441",
    "\u0438\u0441\u043f\u0440\u0430\u0432",
    "\u043f\u043e\u043f\u0440\u0430\u0432",
    "\u043e\u0442\u0441\u0442\u0443\u043f",
    "\u043a\u043d\u043e\u043f\u043a",
    "\u0442\u0435\u043a\u0441\u0442",
    "\u043e\u043f\u0435\u0447\u0430\u0442",
]

AUDIT_HINTS = [
    "audit repository",
    "upgrade plan",
    "audit",
    "review repository",
    "assess repository",
    "onboard repository",
    "\u0430\u0443\u0434\u0438\u0442",
    "upgrade plan",
]

COMMIT_HINTS = [
    "commit",
    "git commit",
    "commit changes",
    "commit this",
    "\u0437\u0430\u043a\u043e\u043c\u0438\u0442",
    "\u043a\u043e\u043c\u043c\u0438\u0442",
]

STARTER_PRESET_HINTS = {
    "next-web": [
        "next",
        "web",
        "website",
        "landing",
        "tailwind",
        "react app",
        "\u0432\u0435\u0431",
        "\u0441\u0430\u0439\u0442",
    ],
    "expo-mobile": [
        "expo",
        "mobile",
        "ios",
        "android",
        "react native",
        "nativewind",
        "\u043c\u043e\u0431\u0438\u043b",
        "\u0430\u043d\u0434\u0440\u043e\u0438\u0434",
        "\u0430\u0439\u043e\u0441",
    ],
    "nestjs-api": [
        "nestjs",
        "api",
        "backend",
        "service",
        "node api",
        "\u0431\u044d\u043a",
        "\u0430\u043f\u0438",
    ],
    "rust-service": [
        "rust",
        "worker",
        "daemon",
        "service",
        "\u0440\u0430\u0441\u0442",
        "\u0441\u0435\u0440\u0432\u0438\u0441",
    ],
    "nx-fullstack": [
        "nx",
        "monorepo",
        "fullstack",
        "workspace",
        "\u043c\u043e\u043d\u043e\u0440\u0435\u043f",
        "\u0444\u0443\u043b\u043b\u0441\u0442\u0435\u043a",
    ],
}

# Localized aliases stay runtime-only and use ASCII escape sequences so tracked source remains English-only.
COMMAND_ALIAS_TABLE = {
    "initialize workspace": [
        "initialize workspace",
        "\u0438\u043d\u0438\u0446\u0438\u0430\u043b\u0438\u0437\u0438\u0440\u0443\u0439 workspace",
    ],
    "reset workspace state": [
        "reset workspace state",
        "\u0441\u0431\u0440\u043e\u0441\u044c workspace state",
    ],
    "preview repair workspace state": [
        "preview repair workspace state",
        "\u043f\u043e\u043a\u0430\u0436\u0438 repair workspace state",
    ],
    "repair workspace state": [
        "repair workspace state",
        "\u043f\u043e\u0447\u0438\u043d\u0438 workspace state",
    ],
    "show state paths": [
        "show state paths",
        "\u043f\u043e\u043a\u0430\u0436\u0438 state paths",
    ],
    "show stages": [
        "show stages",
        "\u043f\u043e\u043a\u0430\u0436\u0438 \u0441\u0442\u0430\u0434\u0438\u0438",
    ],
    "show active brief": [
        "show active brief",
        "\u043f\u043e\u043a\u0430\u0436\u0438 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0439 brief",
    ],
    "propose stage plan changes": [
        "propose stage plan changes",
        "\u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0438 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f stage plan",
    ],
    "apply stage plan changes": [
        "apply stage plan changes",
        "\u043f\u0440\u0438\u043c\u0435\u043d\u0438 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f stage plan",
    ],
    "continue work": [
        "continue work",
        "\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0430\u0439 \u0440\u0430\u0431\u043e\u0442\u0443",
    ],
    "close current stage": [
        "close current stage",
        "\u0437\u0430\u043a\u0440\u043e\u0439 \u0442\u0435\u043a\u0443\u0449\u0443\u044e \u0441\u0442\u0430\u0434\u0438\u044e",
    ],
    "launch gui": [
        "launch gui",
        "\u0437\u0430\u043f\u0443\u0441\u0442\u0438 gui",
    ],
    "stop gui": [
        "stop gui",
        "\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u0438 gui",
    ],
    "show gui url": [
        "show gui url",
        "\u043f\u043e\u043a\u0430\u0436\u0438 gui url",
    ],
    "run verification case": [
        "run verification case",
        "\u0437\u0430\u043f\u0443\u0441\u0442\u0438 verification case",
    ],
    "run verification suite": [
        "run verification suite",
        "\u0437\u0430\u043f\u0443\u0441\u0442\u0438 verification suite",
    ],
    "show verification log": [
        "show verification log",
        "\u043f\u043e\u043a\u0430\u0436\u0438 verification log",
    ],
    "show verification recipes": [
        "show verification recipes",
        "\u043f\u043e\u043a\u0430\u0436\u0438 verification recipes",
    ],
    "show verification helper catalog": [
        "show verification helper catalog",
        "\u043f\u043e\u043a\u0430\u0436\u0438 verification helper catalog",
    ],
    "audit verification coverage": [
        "audit verification coverage",
        "\u043f\u0440\u043e\u0432\u0435\u0440\u044c verification coverage",
    ],
    "sync verification helpers": [
        "sync verification helpers",
        "\u0441\u0438\u043d\u0445\u0440\u043e\u043d\u0438\u0437\u0438\u0440\u0443\u0439 verification helpers",
    ],
    "resolve verification": [
        "resolve verification",
        "\u043f\u043e\u043a\u0430\u0436\u0438 verification plan",
    ],
    "show capability catalog": [
        "show capability catalog",
        "\u043f\u043e\u043a\u0430\u0436\u0438 capability catalog",
    ],
    "show intent route": [
        "show intent route",
        "\u043f\u043e\u043a\u0430\u0436\u0438 intent route",
    ],
    "show workspace context pack": [
        "show workspace context pack",
        "\u043f\u043e\u043a\u0430\u0436\u0438 workspace context pack",
    ],
    "search context index": [
        "search context index",
        "\u043f\u043e\u0438\u0449\u0438 context index",
    ],
    "refresh context index": [
        "refresh context index",
        "\u043e\u0431\u043d\u043e\u0432\u0438 context index",
    ],
    "approve verification baseline": [
        "approve verification baseline",
        "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438 verification baseline",
    ],
    "update verification baseline": [
        "update verification baseline",
        "\u043e\u0431\u043d\u043e\u0432\u0438 verification baseline",
    ],
    "show host support": [
        "show host support",
        "\u043f\u043e\u043a\u0430\u0436\u0438 host support",
    ],
    "show host setup plan": [
        "show host setup plan",
        "\u043f\u043e\u043a\u0430\u0436\u0438 host setup plan",
    ],
    "install host requirements": [
        "install host requirements",
        "\u0443\u0441\u0442\u0430\u043d\u043e\u0432\u0438 host requirements",
    ],
    "repair host requirements": [
        "repair host requirements",
        "\u043f\u043e\u0447\u0438\u043d\u0438 host requirements",
    ],
    "create workstream": [
        "create workstream",
        "\u0441\u043e\u0437\u0434\u0430\u0439 workstream",
    ],
    "list workstreams": [
        "list workstreams",
        "\u043f\u043e\u043a\u0430\u0436\u0438 workstreams",
    ],
    "switch workstream": [
        "switch workstream",
        "\u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438 workstream",
    ],
    "show current workstream": [
        "show current workstream",
        "\u043f\u043e\u043a\u0430\u0436\u0438 current workstream",
    ],
    "close current workstream": [
        "close current workstream",
        "\u0437\u0430\u043a\u0440\u043e\u0439 current workstream",
    ],
    "create task": [
        "create task",
        "\u0441\u043e\u0437\u0434\u0430\u0439 task",
    ],
    "switch task": [
        "switch task",
        "\u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438 task",
    ],
    "list tasks": [
        "list tasks",
        "\u043f\u043e\u043a\u0430\u0436\u0438 tasks",
    ],
    "show current task": [
        "show current task",
        "\u043f\u043e\u043a\u0430\u0436\u0438 current task",
    ],
    "close current task": [
        "close current task",
        "\u0437\u0430\u043a\u0440\u043e\u0439 current task",
    ],
    "show youtrack connections": [
        "show youtrack connections",
        "\u043f\u043e\u043a\u0430\u0436\u0438 youtrack connections",
    ],
    "connect youtrack": [
        "connect youtrack",
        "\u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0438 youtrack",
    ],
    "update youtrack connection": [
        "update youtrack connection",
        "\u043e\u0431\u043d\u043e\u0432\u0438 youtrack connection",
    ],
    "remove youtrack connection": [
        "remove youtrack connection",
        "\u0443\u0434\u0430\u043b\u0438 youtrack connection",
    ],
    "search youtrack issues": [
        "search youtrack issues",
        "\u043d\u0430\u0439\u0434\u0438 youtrack issues",
    ],
    "show youtrack issue queue": [
        "show youtrack issue queue",
        "\u043f\u043e\u043a\u0430\u0436\u0438 youtrack issue queue",
    ],
    "propose youtrack workstream plan": [
        "propose youtrack workstream plan",
        "\u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0438 youtrack workstream plan",
    ],
    "apply youtrack workstream plan": [
        "apply youtrack workstream plan",
        "\u043f\u0440\u0438\u043c\u0435\u043d\u0438 youtrack workstream plan",
    ],
    "audit repository": [
        "audit repository",
        "\u0430\u0443\u0434\u0438\u0442 repo",
    ],
    "show upgrade plan": [
        "show upgrade plan",
        "\u043f\u043e\u043a\u0430\u0436\u0438 upgrade plan",
    ],
    "apply upgrade plan": [
        "apply upgrade plan",
        "\u043f\u0440\u0438\u043c\u0435\u043d\u0438 upgrade plan",
    ],
    "create starter": [
        "create starter",
        "\u0441\u043e\u0437\u0434\u0430\u0439 starter",
    ],
    "show starter presets": [
        "show starter presets",
        "\u043f\u043e\u043a\u0430\u0436\u0438 starter presets",
    ],
    "suggest branch name": [
        "suggest branch name",
        "\u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0438 branch name",
    ],
    "suggest commit message": [
        "suggest commit message",
        "\u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0438 commit message",
    ],
    "suggest pr title": [
        "suggest pr title",
        "\u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0438 pr title",
    ],
    "suggest pr body": [
        "suggest pr body",
        "\u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0438 pr body",
    ],
    "show git workflow advice": [
        "show git workflow advice",
        "\u043f\u043e\u043a\u0430\u0436\u0438 git workflow advice",
    ],
    "show git state": [
        "show git state",
        "\u043f\u043e\u043a\u0430\u0436\u0438 git state",
    ],
    "plan git change": [
        "plan git change",
        "\u0441\u043f\u043b\u0430\u043d\u0438\u0440\u0443\u0439 git change",
    ],
    "create git branch": [
        "create git branch",
        "\u0441\u043e\u0437\u0434\u0430\u0439 git branch",
    ],
    "stage git files": [
        "stage git files",
        "\u0434\u043e\u0431\u0430\u0432\u044c git files \u0432 stage",
    ],
    "create git commit": [
        "create git commit",
        "\u0441\u043e\u0437\u0434\u0430\u0439 git commit",
    ],
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

GREENFIELD_HINTS = [
    "from scratch",
    "new project",
    "new repo",
    "new repository",
    "new app",
    "start a project",
    "bootstrap",
    "scaffold",
    "starter",
    "boilerplate",
    "greenfield",
    "\u0441 \u043d\u0443\u043b\u044f",
    "\u043d\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u0435\u043a\u0442",
    "\u0441\u043e\u0437\u0434\u0430\u0439 \u043f\u0440\u043e\u0435\u043a\u0442",
    "\u0441\u043e\u0437\u0434\u0430\u0439 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435",
]

LARGE_REQUEST_HINTS = [
    "feature",
    "epic",
    "module",
    "flow",
    "integration",
    "architecture",
    "refactor",
    "migration",
    "dashboard",
    "payment",
    "auth",
    "admin panel",
    "multi-step",
    "\u0444\u0438\u0447",
    "\u044d\u043f\u0438\u043a",
    "\u043c\u043e\u0434\u0443\u043b",
    "\u0438\u043d\u0442\u0435\u0433\u0440\u0430\u0446",
    "\u0430\u0440\u0445\u0438\u0442\u0435\u043a\u0442",
    "\u0440\u0435\u0444\u0430\u043a\u0442",
    "\u043c\u0438\u0433\u0440\u0430\u0446",
]

SMALL_REQUEST_HINTS = [
    "fix",
    "bug",
    "rename",
    "spacing",
    "padding",
    "margin",
    "text",
    "copy",
    "button",
    "color",
    "one screen",
    "small",
    "minor",
    "hotfix",
    "\u0438\u0441\u043f\u0440\u0430\u0432",
    "\u043f\u043e\u043f\u0440\u0430\u0432",
    "\u043f\u0435\u0440\u0435\u0438\u043c\u0435\u043d",
    "\u0442\u0435\u043a\u0441\u0442",
    "\u043e\u0442\u0441\u0442\u0443\u043f",
    "\u043a\u043d\u043e\u043f\u043a",
]

PROD_READY_BACKLOG = [
    {
        "title": "Deepen platform verification adapters",
        "objective": "Strengthen the real adapter integrations for Playwright, Detox, Android, and iOS so deterministic verification goes beyond generic command orchestration.",
        "scope": ["verification", "playwright", "detox", "android", "ios"],
    },
    {
        "title": "Harden repository audit and remediation",
        "objective": "Improve existing-repository audit heuristics and make upgrade plans more stack-aware and remediation-specific.",
        "scope": ["audit", "upgrade-plan", "existing-repos"],
    },
    {
        "title": "Add concurrency-safe state writes",
        "objective": "Protect external workspace state against concurrent Codex sessions with locking or equivalent atomic coordination.",
        "scope": ["state", "locking", "concurrency"],
    },
    {
        "title": "Strengthen schema migration discipline",
        "objective": "Expand workspace schema migration coverage and recovery so future upgrades remain predictable.",
        "scope": ["migration", "state-schema"],
    },
    {
        "title": "Polish GUI and operator UX",
        "objective": "Improve dashboard filtering, live status visibility, and drill-down behavior for workstreams, tasks, and verification runs.",
        "scope": ["gui", "dashboard", "ux"],
    },
    {
        "title": "Validate public distribution flow",
        "objective": "Run and stabilize remote CI and public release discipline so the plugin is reliable outside one local machine.",
        "scope": ["ci", "github-actions", "release"],
    },
]


def plugin_root() -> Path:
    override = os.getenv("AGENTIUX_DEV_PLUGIN_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def install_root() -> Path:
    override = os.getenv("AGENTIUX_DEV_INSTALL_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "plugins" / PLUGIN_NAME).resolve()


def marketplace_path() -> Path:
    override = os.getenv("AGENTIUX_DEV_MARKETPLACE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".agents" / "plugins" / "marketplace.json").resolve()


def state_root() -> Path:
    override = os.getenv("AGENTIUX_DEV_STATE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".agentiux" / PLUGIN_NAME).resolve()


def runtime_root() -> Path:
    return state_root() / "runtime"


def gui_runtime_path() -> Path:
    return runtime_root() / "dashboard.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_host_os() -> str:
    if sys.platform.startswith("darwin"):
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def python_launcher_tokens(host_os: str | None = None) -> list[str]:
    resolved_host = host_os or current_host_os()
    if resolved_host == "windows":
        for candidate in (["py", "-3"], ["python"], [sys.executable]):
            if candidate == [sys.executable] or shutil.which(candidate[0]):
                return candidate
        return ["python"]
    for candidate in (["python3"], ["python"], [sys.executable]):
        if candidate == [sys.executable] or shutil.which(candidate[0]):
            return candidate
    return ["python3"]


def python_launcher_string(host_os: str | None = None) -> str:
    return " ".join(python_launcher_tokens(host_os))


def python_script_command(script_path: str | Path, script_args: list[str] | None = None, host_os: str | None = None) -> list[str]:
    return [*python_launcher_tokens(host_os), str(Path(script_path).expanduser().resolve()), *(script_args or [])]


def process_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_status(pid: int | None, host_os: str | None = None) -> dict[str, Any]:
    resolved_host = host_os or current_host_os()
    running = process_running(pid)
    return {
        "pid": pid if isinstance(pid, int) and pid > 0 else None,
        "host_os": resolved_host,
        "running": running,
        "status": "running" if running else "stopped",
    }


def terminate_process(pid: int | None, host_os: str | None = None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    resolved_host = host_os or current_host_os()
    if resolved_host == "windows":
        result = subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def stop_process(pid: int | None, host_os: str | None = None) -> dict[str, Any]:
    resolved_host = host_os or current_host_os()
    stopped = terminate_process(pid, host_os=resolved_host)
    return {
        "pid": pid if isinstance(pid, int) and pid > 0 else None,
        "host_os": resolved_host,
        "stopped": stopped,
        "status": "stopped" if stopped else ("running" if process_running(pid) else "not_running"),
    }


def start_logged_process(
    command: list[str] | str,
    stdout_path: Path,
    stderr_path: Path,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    shell: bool = False,
    start_new_session: bool = False,
) -> subprocess.Popen[str]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("a") as stdout_handle, stderr_path.open("a") as stderr_handle:
        return subprocess.Popen(  # noqa: S602,S603
            command,
            cwd=str(Path(cwd).expanduser().resolve()) if cwd else None,
            env=env,
            shell=shell,
            stdout=stdout_handle,
            stderr=stderr_handle,
            stdin=subprocess.DEVNULL,
            text=True,
            start_new_session=start_new_session,
        )


def start_logged_python_process(
    script_path: str | Path,
    stdout_path: Path,
    stderr_path: Path,
    *,
    script_args: list[str] | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    start_new_session: bool = False,
    host_os: str | None = None,
) -> subprocess.Popen[str]:
    return start_logged_process(
        python_script_command(script_path, script_args=script_args, host_os=host_os),
        stdout_path,
        stderr_path,
        cwd=cwd,
        env=env,
        shell=False,
        start_new_session=start_new_session,
    )


def spawn_logged_process(
    command: list[str] | str,
    stdout_path: Path,
    stderr_path: Path,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    shell: bool = False,
    start_new_session: bool = False,
) -> subprocess.Popen[str]:
    return start_logged_process(
        command,
        stdout_path,
        stderr_path,
        cwd=cwd,
        env=env,
        shell=shell,
        start_new_session=start_new_session,
    )


def _tool_override_env(command: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", command).strip("_").upper()
    return f"AGENTIUX_DEV_TOOL_OVERRIDE_{normalized}"


def _tool_override_value(command: str) -> str | None:
    return os.getenv(_tool_override_env(command))


def _tool_command_path(command: str) -> str | None:
    override = _tool_override_value(command)
    if override is None:
        return shutil.which(command)
    normalized = override.strip().lower()
    if normalized in {"0", "false", "missing", "unavailable"}:
        return None
    if normalized in {"1", "true", "available"}:
        return shutil.which(command)
    return str(Path(override).expanduser())


def _tool_available(command: str) -> bool:
    override = _tool_override_value(command)
    if override is not None:
        normalized = override.strip().lower()
        if normalized in {"0", "false", "missing", "unavailable"}:
            return False
        if normalized in {"1", "true", "available"}:
            return True
        resolved_override = Path(override).expanduser()
        return resolved_override.exists() and os.access(resolved_override, os.X_OK)
    return shutil.which(command) is not None


def host_capabilities(host_os: str | None = None) -> dict[str, Any]:
    resolved_host = host_os or current_host_os()
    core_supported = resolved_host in SUPPORTED_HOSTS
    return {
        "core_runtime": {
            "supported": core_supported,
            "reason": None if core_supported else f"Unsupported host OS: {resolved_host}",
        },
        "mcp": {"supported": core_supported, "reason": None},
        "gui": {"supported": core_supported, "reason": None},
        "git_agent": {"supported": core_supported, "reason": None},
        "starter_planning": {"supported": core_supported, "reason": None},
        "ios_runtime": {
            "supported": resolved_host == "macos",
            "reason": None if resolved_host == "macos" else "iOS tooling is available only on macOS hosts.",
        },
    }


def _toolchain_capabilities(detected_stacks: set[str], host_os: str | None = None) -> dict[str, Any]:
    resolved_host = host_os or current_host_os()
    node_available = _tool_available("node")
    npx_available = _tool_available("npx")
    cargo_available = _tool_available("cargo")
    docker_available = _tool_available("docker")
    adb_available = _tool_available("adb")
    xcodebuild_available = resolved_host == "macos" and _tool_available("xcodebuild")
    python_available = bool(python_launcher_tokens(resolved_host))
    return {
        "python": {
            "supported": True,
            "available": python_available,
            "reason": None if python_available else "Python launcher could not be resolved on this host.",
        },
        "web_verification": {
            "supported": True,
            "available": node_available,
            "reason": None if node_available else "Node.js is required for web verification and starter execution.",
        },
        "mobile_verification_android": {
            "supported": True,
            "available": node_available and adb_available,
            "reason": None if node_available and adb_available else "Node.js and adb are required for Android mobile verification.",
        },
        "mobile_verification_ios": {
            "supported": resolved_host == "macos",
            "available": resolved_host == "macos" and node_available and xcodebuild_available,
            "reason": None
            if resolved_host == "macos" and node_available and xcodebuild_available
            else "iOS verification requires macOS, Node.js, and Xcode command-line tools.",
        },
        "backend_starters": {
            "supported": True,
            "available": node_available or cargo_available,
            "reason": None if node_available or cargo_available else "Backend starters require Node.js or Cargo, depending on the preset.",
        },
        "docker": {
            "supported": True,
            "available": docker_available,
            "reason": None if docker_available else "Docker is not available on this host.",
        },
        "android_tooling": {
            "supported": True,
            "available": adb_available,
            "reason": None if adb_available else "Android verification requires adb on the host.",
        },
    }


def _support_warnings(detected_stacks: set[str], host_os: str, toolchain: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if "ios" in detected_stacks and host_os != "macos":
        warnings.append("This workspace includes iOS surfaces, but iOS execution is available only on macOS.")
    if any(stack in detected_stacks for stack in {"react", "nextjs", "react-native", "expo", "nestjs"}) and not toolchain["web_verification"]["available"]:
        warnings.append("Node.js tooling is missing, so some starters or verification flows will remain planning-only.")
    if any(stack in detected_stacks for stack in {"postgres", "mongodb", "redis", "nats", "docker-compose"}) and not toolchain["docker"]["available"]:
        warnings.append("Docker is not available on this host, so local infra boot paths cannot run here.")
    return warnings


HOST_SETUP_STATUS_SCHEMA_VERSION = 1
HOST_SETUP_REQUIREMENT_ORDER = (
    "web_verification",
    "mobile_verification_android",
    "android_tooling",
    "mobile_verification_ios",
    "docker",
    "backend_starters",
)
HOST_SETUP_REQUIREMENT_METADATA: dict[str, dict[str, Any]] = {
    "web_verification": {
        "title": "Web verification toolchain",
        "tools": ["node"],
        "relevant_stacks": {"react", "nextjs", "react-native", "expo", "nestjs"},
    },
    "mobile_verification_android": {
        "title": "Android mobile verification toolchain",
        "tools": ["node", "adb"],
        "relevant_stacks": {"android", "expo", "react-native"},
    },
    "android_tooling": {
        "title": "Android platform tooling",
        "tools": ["adb"],
        "relevant_stacks": {"android", "expo", "react-native"},
    },
    "mobile_verification_ios": {
        "title": "iOS mobile verification toolchain",
        "tools": ["node", "xcode_cli"],
        "relevant_stacks": {"expo", "ios", "react-native"},
    },
    "docker": {
        "title": "Local infra runtime",
        "tools": ["docker"],
        "relevant_stacks": {"docker-compose", "mongodb", "nats", "postgres", "redis"},
    },
    "backend_starters": {
        "title": "Backend starter toolchain",
        "tools": ["node"],
        "relevant_stacks": {"nestjs"},
    },
}
HOST_SETUP_TOOL_METADATA: dict[str, dict[str, Any]] = {
    "node": {
        "title": "Node.js runtime",
        "check_command": "node",
        "host_recipes": {
            "macos": {
                "mode": "automatic",
                "installer_id": "brew",
                "commands": [["brew", "install", "node"]],
            },
            "linux": {
                "mode": "automatic",
                "installer_id": "apt-get",
                "commands": [["__SUDO__", "apt-get", "update"], ["__SUDO__", "apt-get", "install", "-y", "nodejs", "npm"]],
            },
            "windows": {
                "mode": "automatic",
                "installer_options": [
                    {
                        "installer_id": "winget",
                        "commands": [
                            [
                                "winget",
                                "install",
                                "--exact",
                                "--id",
                                "OpenJS.NodeJS.LTS",
                                "--accept-source-agreements",
                                "--accept-package-agreements",
                                "--disable-interactivity",
                            ]
                        ],
                    },
                    {
                        "installer_id": "choco",
                        "commands": [["choco", "upgrade", "nodejs-lts", "-y"]],
                    },
                ],
                "manual_instructions": "Install Node.js LTS with winget, Chocolatey, or the official installer if no supported package manager is available.",
            },
        },
    },
    "adb": {
        "title": "Android platform tools (adb)",
        "check_command": "adb",
        "host_recipes": {
            "macos": {
                "mode": "automatic",
                "installer_id": "brew",
                "commands": [["brew", "install", "android-platform-tools"]],
            },
            "linux": {
                "mode": "automatic",
                "installer_id": "apt-get",
                "commands": [["__SUDO__", "apt-get", "update"], ["__SUDO__", "apt-get", "install", "-y", "adb"]],
            },
            "windows": {
                "mode": "automatic",
                "installer_options": [
                    {
                        "installer_id": "choco",
                        "commands": [["choco", "upgrade", "adb", "-y"]],
                    },
                    {
                        "installer_id": "winget",
                        "commands": [
                            [
                                "winget",
                                "install",
                                "--exact",
                                "--id",
                                "Google.PlatformTools",
                                "--accept-source-agreements",
                                "--accept-package-agreements",
                                "--disable-interactivity",
                            ]
                        ],
                    },
                ],
                "manual_instructions": "Install Android platform tools and ensure `adb` is on PATH if no supported package manager is available.",
            },
        },
    },
    "xcode_cli": {
        "title": "Xcode command-line tools",
        "check_command": "xcodebuild",
        "host_recipes": {
            "macos": {
                "mode": "manual",
                "manual_instructions": "Run `xcode-select --install` and complete the macOS installer prompt.",
            },
            "linux": {
                "mode": "unsupported",
                "manual_instructions": "iOS verification is available only on macOS hosts.",
            },
            "windows": {
                "mode": "unsupported",
                "manual_instructions": "iOS verification is available only on macOS hosts.",
            },
        },
    },
    "docker": {
        "title": "Docker runtime",
        "check_command": "docker",
        "host_recipes": {
            "macos": {
                "mode": "manual",
                "manual_instructions": "Install Docker Desktop and start it before running local infra flows.",
            },
            "linux": {
                "mode": "manual",
                "manual_instructions": "Install Docker Engine for your distribution, then ensure the daemon is running and your user can access the socket.",
            },
            "windows": {
                "mode": "manual",
                "manual_instructions": "Install Docker Desktop and start it before running local infra flows.",
            },
        },
    },
}
HOST_SETUP_INSTALLER_METADATA = {
    "brew": {
        "title": "Homebrew",
        "command": "brew",
        "supported_hosts": {"macos"},
    },
    "apt-get": {
        "title": "APT",
        "command": "apt-get",
        "supported_hosts": {"linux"},
    },
    "winget": {
        "title": "WinGet",
        "command": "winget",
        "supported_hosts": {"windows"},
    },
    "choco": {
        "title": "Chocolatey",
        "command": "choco",
        "supported_hosts": {"windows"},
    },
}


def _host_setup_installer_available(installer_id: str, host_os: str) -> tuple[bool, str | None]:
    metadata = HOST_SETUP_INSTALLER_METADATA.get(installer_id)
    if not metadata:
        return False, f"Unknown installer: {installer_id}"
    supported_hosts = metadata.get("supported_hosts")
    if supported_hosts and host_os not in supported_hosts:
        return False, f"{metadata['title']} automation is not supported on {host_os}."
    command = metadata["command"]
    if installer_id == "apt-get":
        apt_available = _tool_available("apt-get")
        sudo_available = (hasattr(os, "geteuid") and os.geteuid() == 0) or _tool_available("sudo")
        if not apt_available:
            return False, "apt-get is not available on this host."
        if not sudo_available:
            return False, "apt-get install requires root privileges or sudo."
        return True, None
    available = _tool_available(command)
    if available:
        return True, None
    return False, f"{metadata['title']} is not available on this host."


def _host_setup_requirements_for_workspace(detected_stacks: set[str], toolchain: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    for requirement_id in HOST_SETUP_REQUIREMENT_ORDER:
        metadata = HOST_SETUP_REQUIREMENT_METADATA[requirement_id]
        capability = toolchain.get(requirement_id, {})
        if not capability.get("supported", True):
            continue
        if detected_stacks.intersection(metadata["relevant_stacks"]):
            selected.append(requirement_id)
    if selected:
        return selected
    return [
        requirement_id
        for requirement_id in HOST_SETUP_REQUIREMENT_ORDER
        if toolchain.get(requirement_id, {}).get("supported", True) and not toolchain.get(requirement_id, {}).get("available", True)
    ]


def _normalize_host_setup_requirements(requirement_ids: list[str] | None, detected_stacks: set[str], toolchain: dict[str, Any]) -> list[str]:
    if not requirement_ids:
        return _host_setup_requirements_for_workspace(detected_stacks, toolchain)
    normalized: list[str] = []
    for requirement_id in requirement_ids:
        if requirement_id not in HOST_SETUP_REQUIREMENT_METADATA:
            raise ValueError(f"Unsupported host requirement id: {requirement_id}")
        if requirement_id not in normalized:
            normalized.append(requirement_id)
    return normalized


def _render_host_setup_command(command: list[str]) -> str:
    return " ".join(command)


def _host_setup_recipe_for_tool(tool_id: str, host_os: str) -> dict[str, Any]:
    tool = HOST_SETUP_TOOL_METADATA[tool_id]
    recipe = copy.deepcopy(tool["host_recipes"].get(host_os) or {"mode": "unsupported", "manual_instructions": f"No host setup recipe exists for `{tool['title']}` on {host_os}."})
    recipe["tool_id"] = tool_id
    recipe["tool_title"] = tool["title"]
    recipe["check_command"] = tool["check_command"]
    installer_options = recipe.pop("installer_options", None)
    if recipe.get("mode") == "automatic" and installer_options:
        recipe["available_installers"] = []
        chosen_option = None
        for option in installer_options:
            installer_id = option["installer_id"]
            available, reason = _host_setup_installer_available(installer_id, host_os)
            recipe["available_installers"].append(
                {
                    "installer_id": installer_id,
                    "title": HOST_SETUP_INSTALLER_METADATA[installer_id]["title"],
                    "available": available,
                    "reason": reason,
                }
            )
            if chosen_option is None and available:
                chosen_option = option
        if chosen_option is not None:
            recipe["installer_id"] = chosen_option["installer_id"]
            recipe["commands"] = chosen_option.get("commands", [])
            recipe["installer_available"] = True
            recipe["installer_reason"] = None
        else:
            recipe["installer_id"] = None
            recipe["commands"] = []
            recipe["installer_available"] = False
            failure_reasons = [entry["reason"] for entry in recipe["available_installers"] if entry.get("reason")]
            recipe["installer_reason"] = "; ".join(dict.fromkeys(failure_reasons)) or "No supported installer is available on this host."
    elif recipe.get("mode") == "automatic":
        installer_available, reason = _host_setup_installer_available(recipe["installer_id"], host_os)
        recipe["installer_available"] = installer_available
        recipe["installer_reason"] = reason
    return recipe


def _resolve_host_setup_commands(commands: list[list[str]]) -> list[list[str]]:
    resolved_commands: list[list[str]] = []
    for command in commands:
        resolved: list[str] = []
        for token in command:
            if token == "__SUDO__":
                if os.geteuid() == 0:
                    continue
                sudo_path = _tool_command_path("sudo")
                if not sudo_path:
                    raise RuntimeError("sudo is required for this host setup step but is not available.")
                resolved.append(sudo_path)
                continue
            if token in {"brew", "apt-get", "winget", "choco"}:
                command_path = _tool_command_path(token)
                if not command_path:
                    raise RuntimeError(f"{token} is required for this host setup step but is not available.")
                resolved.append(command_path)
                continue
            resolved.append(token)
        resolved_commands.append(resolved)
    return resolved_commands


def _build_host_setup_plan(workspace: str | Path, requirement_ids: list[str] | None = None) -> dict[str, Any]:
    state = read_workspace_state(workspace)
    detection = detect_workspace(workspace)
    host_os = detection["host_os"]
    detected_stacks = set(detection["detected_stacks"])
    toolchain = detection["toolchain_capabilities"]
    selected_requirements = _normalize_host_setup_requirements(requirement_ids, detected_stacks, toolchain)
    requirement_summaries = []
    tools_to_install: dict[str, dict[str, Any]] = {}
    missing_requirements: list[str] = []
    for requirement_id in selected_requirements:
        capability = toolchain.get(requirement_id, {"supported": True, "available": True, "reason": None})
        metadata = HOST_SETUP_REQUIREMENT_METADATA[requirement_id]
        summary = {
            "requirement_id": requirement_id,
            "title": metadata["title"],
            "supported": capability.get("supported", True),
            "available": capability.get("available", True),
            "reason": capability.get("reason"),
            "tools": metadata["tools"],
        }
        requirement_summaries.append(summary)
        if summary["supported"] and not summary["available"]:
            missing_requirements.append(requirement_id)
        for tool_id in metadata["tools"]:
            tool = HOST_SETUP_TOOL_METADATA[tool_id]
            if _tool_available(tool["check_command"]):
                continue
            entry = tools_to_install.setdefault(
                tool_id,
                {
                    "tool_id": tool_id,
                    "title": tool["title"],
                    "requirement_ids": [],
                },
            )
            if requirement_id not in entry["requirement_ids"]:
                entry["requirement_ids"].append(requirement_id)

    steps: list[dict[str, Any]] = []
    automatic_steps = 0
    manual_steps = 0
    for tool_id in sorted(tools_to_install):
        tool_entry = tools_to_install[tool_id]
        recipe = _host_setup_recipe_for_tool(tool_id, host_os)
        step = {
            "step_id": f"install-{tool_id}",
            "tool_id": tool_id,
            "title": recipe["tool_title"],
            "requirement_ids": sorted(tool_entry["requirement_ids"]),
            "mode": recipe["mode"],
            "installer_id": recipe.get("installer_id"),
            "installer_title": HOST_SETUP_INSTALLER_METADATA.get(recipe.get("installer_id"), {}).get("title"),
            "installer_available": bool(recipe.get("installer_available", False)),
            "available_installers": recipe.get("available_installers", []),
            "requires_confirmation": recipe["mode"] == "automatic",
            "commands": [],
            "command_preview": [],
            "manual_instructions": recipe.get("manual_instructions"),
            "status": "manual_required" if recipe["mode"] == "manual" else ("unsupported" if recipe["mode"] == "unsupported" else "planned"),
            "reason": recipe.get("installer_reason"),
        }
        if recipe["mode"] == "automatic":
            if recipe.get("installer_available"):
                resolved_commands = _resolve_host_setup_commands(recipe.get("commands", []))
                step["commands"] = resolved_commands
                step["command_preview"] = [_render_host_setup_command(command) for command in resolved_commands]
                automatic_steps += 1
            else:
                step["status"] = "manual_required"
                step["manual_instructions"] = step["reason"] or step["manual_instructions"] or "A supported installer is required on this host."
                manual_steps += 1
        else:
            manual_steps += 1
        steps.append(step)

    if not missing_requirements:
        status = "ready"
    elif automatic_steps:
        status = "needs_confirmation"
    elif manual_steps:
        status = "manual_action_required"
    else:
        status = "unsupported"

    installers = []
    for installer_id, metadata in HOST_SETUP_INSTALLER_METADATA.items():
        available, reason = _host_setup_installer_available(installer_id, host_os)
        installers.append(
            {
                "installer_id": installer_id,
                "title": metadata["title"],
                "available": available,
                "reason": reason,
            }
        )

    return {
        "workspace_path": state["workspace_path"],
        "workspace_label": state.get("workspace_label"),
        "host_os": host_os,
        "status": status,
        "requires_confirmation": bool(automatic_steps),
        "selected_requirements": selected_requirements,
        "requirement_summaries": requirement_summaries,
        "missing_requirements": missing_requirements,
        "automatic_step_count": automatic_steps,
        "manual_step_count": manual_steps,
        "supported_installers": installers,
        "steps": steps,
    }


def _local_dev_policy(detected_stacks: set[str], selected_profiles: list[str]) -> dict[str, Any]:
    infra_signals = {"postgres", "mongodb", "redis", "nats"}
    has_supporting_services = bool(detected_stacks.intersection(infra_signals))
    if "plugin-platform" in selected_profiles and not has_supporting_services and "local-infra" not in selected_profiles:
        return {
            "infra_mode": "not_applicable",
            "orchestration": None,
            "app_runtime_mode": "native",
            "reason": "This workspace is plugin-only and does not declare local supporting services.",
        }
    if has_supporting_services or "local-infra" in selected_profiles:
        return {
            "infra_mode": "docker_required" if has_supporting_services else "docker_optional",
            "orchestration": "docker_compose",
            "app_runtime_mode": "native_allowed_when_more_reliable",
            "reason": "Supporting services should run in Docker for local development when the workspace uses local data or broker dependencies.",
        }
    return {
        "infra_mode": "not_applicable",
        "orchestration": None,
        "app_runtime_mode": "native",
            "reason": "No local supporting services were detected for this workspace.",
        }


def default_planning_policy() -> dict[str, Any]:
    return {
        "planner_mode": "dynamic",
        "template_library_mode": "advisory",
        "stage_count": "workspace_specific",
        "explicit_stage_plan_required": True,
        "workflow_advice_auto_create": "point_task_only",
        "starter_fragments": "optional",
        "verification_fragments": "optional",
        "docs_hints": "optional",
        "design_scaffolds": "optional",
        "completed_stage_immutability": True,
        "unfinished_stage_edits_require_confirmation": True,
    }


def _derived_plan_status(stages: list[dict[str, Any]]) -> str:
    return "confirmed" if stages else "needs_user_confirmation"


def _base_stage_register(
    detection: dict[str, Any],
    workstream_record: dict[str, Any],
    *,
    planner_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    templates = _load_templates()
    normalized_record = _normalize_workstream_record(detection, workstream_record)
    docs_hint_payload = _resolve_docs_hints(detection["selected_profiles"], templates)
    resolved_planner_context = planner_context or _planner_context(detection, normalized_record)
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "workspace_path": detection["workspace_path"],
        "workspace_label": detection["workspace_label"],
        "workspace_slug": detection["workspace_slug"],
        "host_os": detection["host_os"],
        "host_platform": detection["host_os"],
        "host_capabilities": detection["host_capabilities"],
        "toolchain_capabilities": detection["toolchain_capabilities"],
        "support_warnings": detection["support_warnings"],
        "selected_profiles": detection["selected_profiles"],
        "detected_stacks": detection["detected_stacks"],
        "workstream_id": normalized_record["workstream_id"],
        "workstream_title": normalized_record["title"],
        "workstream_kind": normalized_record["kind"],
        "workstream_status": normalized_record["status"],
        "branch_hint": normalized_record.get("branch_hint"),
        "scope_summary": normalized_record.get("scope_summary"),
        "plan_status": "needs_user_confirmation",
        "current_stage": None,
        "last_completed_stage": None,
        "stage_status": None,
        "current_slice": None,
        "remaining_slices": [],
        "slice_status": None,
        "active_goal": None,
        "next_task": None,
        "blockers": [],
        "required_doc_updates": [
            "update in-repo docs when runtime behavior, architecture, local-dev commands, or verification contracts change",
            "update external stage-register.yaml and the active brief during closeout",
        ],
        "verification_requirements": [
            "use deterministic verification first",
            "keep transient artifacts under the external artifact root only",
            "keep canonical visual baselines in project-owned test assets or snapshots",
        ],
        "verification_selectors": copy.deepcopy(normalized_record.get("verification_selectors", [])),
        "verification_policy": {
            **normalized_record.get("verification_policy", {}),
        },
        "slice_verification_summary": None,
        "last_verification_summary": None,
        "open_decisions": ["A confirmed stage plan is required before stage execution or closeout."],
        "planner_context": resolved_planner_context,
        "planner_notes": [],
        "docs_hints": docs_hint_payload["docs_hints"],
        "docs_hint_resolution": docs_hint_payload["docs_hint_resolution"],
        "template_policy": default_planning_policy(),
        "stages": [],
    }


STAGE_SHELL_LIBRARY: dict[str, dict[str, Any]] = {}


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", normalized) or "workspace"


def humanize_identifier(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    normalized = re.sub(r"[-_]+", " ", text).strip()
    normalized = re.sub(r"\s{2,}", " ", normalized)
    return normalized.title() if normalized else fallback


def sanitize_identifier(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized or fallback


def _looks_placeholder_scope_summary(summary: str | None) -> bool:
    return (summary or "").strip() in PLACEHOLDER_SCOPE_SUMMARIES


def workspace_hash(workspace_path: Path) -> str:
    return hashlib.sha1(str(workspace_path).encode("utf-8")).hexdigest()[:10]


def normalize_command_phrase(phrase: str) -> str:
    lowered = (phrase or "").strip().lower()
    return re.sub(r"\s+", " ", lowered)


def command_aliases() -> dict[str, list[str]]:
    return {
        canonical: [normalize_command_phrase(alias) for alias in aliases]
        for canonical, aliases in COMMAND_ALIAS_TABLE.items()
    }


def resolve_command_phrase(phrase: str) -> str | None:
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


def _matched_keywords(normalized_text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in normalized_text]


def _request_has_execution_intent(normalized_text: str) -> bool:
    return bool(_matched_keywords(normalized_text, EXECUTION_INTENT_HINTS))


def _suggested_title_from_request(request_text: str | None, fallback: str) -> str:
    text = re.sub(r"\s+", " ", (request_text or "").strip())
    if not text:
        return fallback
    text = re.split(r"[.!?\n]", text, maxsplit=1)[0].strip()
    text = re.sub(r"^[\-\*\d\.\)\s]+", "", text)
    text = re.sub(
        r"^(please|could you|can you|let'?s|lets|need to|do|make|build|implement|fix|update|create|add|start|help me)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    words = text.split()
    if len(words) > 10:
        text = " ".join(words[:10]).rstrip(",;:")
    if len(text) > 72:
        truncated = text[:72].rsplit(" ", 1)[0].rstrip(",;:")
        if len(truncated) >= 24:
            text = truncated
        else:
            text = text[:69].rstrip() + "..."
    return text[:1].upper() + text[1:] if text else fallback


def _suggested_objective_from_request(request_text: str | None) -> str:
    title = _suggested_title_from_request(request_text, "Targeted repository change")
    return title.rstrip(".")


def _workspace_initialized(paths: dict[str, str]) -> bool:
    workspace_state_path = Path(paths["workspace_state"])
    compatibility_register = Path(paths["stage_register"])
    if workspace_state_path.exists():
        return True
    return compatibility_register.exists()


def _recommend_starter_preset(request_text: str | None) -> dict[str, Any] | None:
    normalized = normalize_command_phrase(request_text or "")
    if not normalized:
        return None
    scored: list[tuple[int, str]] = []
    for preset_id, keywords in STARTER_PRESET_HINTS.items():
        score = len(_matched_keywords(normalized, keywords))
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


def _analyze_request_text(request_text: str | None) -> dict[str, Any]:
    normalized = normalize_command_phrase(request_text or "")
    recognized_command = resolve_command_phrase(normalized) if normalized else None
    greenfield_matches = _matched_keywords(normalized, GREENFIELD_HINTS)
    large_matches = _matched_keywords(normalized, LARGE_WORK_HINTS)
    small_matches = _matched_keywords(normalized, SMALL_TASK_HINTS)
    audit_matches = _matched_keywords(normalized, AUDIT_HINTS)
    commit_matches = _matched_keywords(normalized, COMMIT_HINTS)
    execution_intent = _request_has_execution_intent(normalized)
    word_count = len(normalized.split())

    request_kind = "neutral"
    recommended_mode = None
    reason = "No stage-aware routing recommendation is required yet."

    if greenfield_matches:
        request_kind = "greenfield"
        recommended_mode = "workstream"
        reason = "Greenfield product work is better tracked through a dedicated workstream."
    elif commit_matches:
        request_kind = "commit"
        reason = "Commit requests should inspect repo commit history or commit rules before generating a message."
    elif audit_matches:
        request_kind = "repository_audit"
        reason = "Repository audits should stay read-only until an upgrade plan is explicitly approved."
    elif large_matches or (execution_intent and word_count >= 18 and len(small_matches) <= 1):
        request_kind = "large_feature"
        recommended_mode = "workstream"
        reason = "Large or multi-slice implementation work should use a named workstream."
    elif small_matches or (execution_intent and word_count <= 14):
        request_kind = "point_task"
        recommended_mode = "task"
        reason = "Narrow corrections and point fixes are cheaper to track as lightweight tasks."

    return {
        "request_text": request_text or "",
        "normalized_request": normalized,
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
        },
    }


def _state_file_error(path: Path, exc: Exception, purpose: str | None = None) -> StateFileError:
    label = purpose or "JSON file"
    return StateFileError(f"Unable to read {label} at {path}: {exc}")


def _load_json(path: Path, default: Any | None = None, *, strict: bool = False, purpose: str | None = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        with path.open() as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        if strict:
            raise _state_file_error(path, exc, purpose) from exc
        return copy.deepcopy(default)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    with temp_path.open("w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temp_path, path)


def _read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text()


def _strip_mirror_markers_from_markdown(markdown: str | None) -> str:
    text = (markdown or "").replace("\r\n", "\n")
    filtered_lines = [
        line
        for line in text.splitlines()
        if not re.match(r"^\s*<!--\s*(derived-mirror|mirror-of-workstream|read-only-derived):", line)
    ]
    return "\n".join(filtered_lines).strip()


def _canonical_brief_markdown(markdown: str | None) -> str:
    stripped = _strip_mirror_markers_from_markdown(markdown)
    return stripped or BRIEF_PLACEHOLDER.strip()


def _strip_mirror_fields(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(payload)
    for field in MIRROR_REGISTER_FIELDS:
        cleaned.pop(field, None)
    return cleaned


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.rstrip() + "\n")


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for candidate in path.rglob("*") if candidate.is_file())


def _json_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(candidate for candidate in path.glob("*.json") if candidate.is_file())


def _load_install_metadata() -> dict[str, Any]:
    return _load_json(plugin_root() / "install-metadata.json", default={}) or {}


def plugin_info() -> dict[str, Any]:
    metadata = _load_install_metadata()
    current_root = plugin_root()
    return {
        "name": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "current_root": str(current_root),
        "source_root": str(Path(metadata.get("source_root", current_root)).expanduser().resolve()),
        "install_root": str(Path(metadata.get("install_root", install_root())).expanduser().resolve()),
        "marketplace_path": str(Path(metadata.get("marketplace_path", marketplace_path())).expanduser().resolve()),
        "installed_copy": bool(metadata),
        "installed_at": metadata.get("installed_at"),
    }


def language_policy() -> dict[str, Any]:
    return {
        "reply_behavior": "reply in the user's language unless the user asks to switch",
        "tracked_source_language": "english_only",
        "localized_aliases_runtime_only": True,
    }


def _workspace_root_path(workspace: str | Path) -> Path:
    workspace_path = Path(workspace).expanduser().resolve()
    slug = slugify(workspace_path.name)
    digest = workspace_hash(workspace_path)
    return state_root() / "workspaces" / f"{slug}--{digest}"


def _current_workspace_state_payload(workspace_dir: Path) -> dict[str, Any]:
    return _normalize_workspace_state_payload(_load_json(workspace_dir / "workspace.json", default={}) or {})


def _sanitize_nullable_identifier(value: Any) -> str | None:
    if value is None:
        return None
    sanitized = sanitize_identifier(value, "")
    return sanitized or None


def _normalize_workspace_state_payload(workspace_state: dict[str, Any] | None) -> dict[str, Any]:
    normalized = copy.deepcopy(workspace_state or {})
    normalized["current_workstream_id"] = _sanitize_nullable_identifier(normalized.get("current_workstream_id"))
    normalized["current_task_id"] = _sanitize_nullable_identifier(normalized.get("current_task_id"))
    workspace_mode = normalized.get("workspace_mode")
    if workspace_mode not in {"workspace", "task", "workstream"}:
        if normalized["current_task_id"]:
            workspace_mode = "task"
        elif normalized["current_workstream_id"]:
            workspace_mode = "workstream"
        else:
            workspace_mode = "workspace"
    normalized["workspace_mode"] = workspace_mode
    if normalized.get("state_repair_status") is None:
        normalized["state_repair_status"] = {}
    return normalized


def _no_current_workstream_error() -> FileNotFoundError:
    return FileNotFoundError(NO_CURRENT_WORKSTREAM_MESSAGE)


def _workspace_state_storage_payload(workspace_state: dict[str, Any]) -> dict[str, Any]:
    persisted: dict[str, Any] = {}
    for key in (
        "schema_version",
        "workspace_path",
        "workspace_label",
        "workspace_slug",
        "workspace_hash",
        "init_status",
        "initialized_at",
        "current_workstream_id",
        "current_task_id",
        "workspace_mode",
        "host_os",
        "host_capabilities",
        "toolchain_capabilities",
        "detected_stacks",
        "selected_profiles",
        "plugin_platform",
        "local_dev_policy",
        "planning_policy",
        "support_warnings",
        "state_repair_status",
        "updated_at",
    ):
        if key in workspace_state:
            persisted[key] = copy.deepcopy(workspace_state[key])
    return _normalize_workspace_state_payload(persisted)


def workspace_paths(workspace: str | Path, workstream_id: str | None = None, task_id: str | None = None) -> dict[str, str]:
    workspace_path = Path(workspace).expanduser().resolve()
    slug = slugify(workspace_path.name)
    digest = workspace_hash(workspace_path)
    workspace_dir = _workspace_root_path(workspace_path)
    existing_state = _current_workspace_state_payload(workspace_dir)
    raw_workstream_id = workstream_id if workstream_id is not None else existing_state.get("current_workstream_id")
    resolved_workstream_id = _sanitize_nullable_identifier(raw_workstream_id)
    raw_current_task_id = task_id if task_id is not None else existing_state.get("current_task_id")
    resolved_task_id = sanitize_identifier(raw_current_task_id, "") if raw_current_task_id else None
    workstreams_root = workspace_dir / "workstreams"
    current_workstream_root = workstreams_root / resolved_workstream_id if resolved_workstream_id else None
    tasks_root = workspace_dir / "tasks"
    current_task_root = tasks_root / resolved_task_id if resolved_task_id else None
    audits_root = workspace_dir / "audits"
    upgrade_root = workspace_dir / "upgrade-plans"
    migrations_root = workspace_dir / "migrations"
    integrations_root = workspace_dir / "integrations"
    youtrack_root = integrations_root / "youtrack"
    starter_runs_root = state_root() / "starter-runs"
    return {
        "workspace_path": str(workspace_path),
        "workspace_slug": slug,
        "workspace_hash": digest,
        "state_root": str(state_root()),
        "runtime_root": str(runtime_root()),
        "gui_runtime": str(gui_runtime_path()),
        "workspace_root": str(workspace_dir),
        "registry": str(state_root() / "registry.json"),
        "workspace_state": str(workspace_dir / "workspace.json"),
        "stage_register": str(workspace_dir / STAGE_REGISTER_STORAGE["filename"]),
        "active_brief": str(workspace_dir / "active-stage-brief.md"),
        "artifacts_dir": str(current_workstream_root / "artifacts") if current_workstream_root else "",
        "workstreams_root": str(workstreams_root),
        "workstreams_index": str(workstreams_root / "index.json"),
        "current_workstream_id": resolved_workstream_id or "",
        "current_workstream_root": str(current_workstream_root) if current_workstream_root else "",
        "current_workstream_stage_register": str(current_workstream_root / STAGE_REGISTER_STORAGE["filename"]) if current_workstream_root else "",
        "current_workstream_stages_dir": str(current_workstream_root / "stages") if current_workstream_root else "",
        "current_workstream_active_brief": str(current_workstream_root / "active-stage-brief.md") if current_workstream_root else "",
        "design_dir": str(current_workstream_root / "design") if current_workstream_root else "",
        "design_brief": str(current_workstream_root / "design" / "brief.json") if current_workstream_root else "",
        "design_boards_dir": str(current_workstream_root / "design" / "boards") if current_workstream_root else "",
        "reference_board": str(current_workstream_root / "design" / "current-board.json") if current_workstream_root else "",
        "design_handoffs_dir": str(current_workstream_root / "design" / "handoffs") if current_workstream_root else "",
        "design_handoff": str(current_workstream_root / "design" / "current-handoff.json") if current_workstream_root else "",
        "design_cache_dir": str(current_workstream_root / "design" / "cache") if current_workstream_root else "",
        "verification_dir": str(current_workstream_root / "verification") if current_workstream_root else "",
        "verification_recipes": str(current_workstream_root / "verification" / "recipes.json") if current_workstream_root else "",
        "verification_runs_dir": str(current_workstream_root / "verification" / "runs") if current_workstream_root else "",
        "verification_baselines_dir": str(current_workstream_root / "verification" / "baselines") if current_workstream_root else "",
        "tasks_root": str(tasks_root),
        "tasks_index": str(tasks_root / "index.json"),
        "current_task_id": resolved_task_id or "",
        "current_task_root": str(current_task_root) if current_task_root else "",
        "current_task_record": str(current_task_root / "task.json") if current_task_root else "",
        "current_task_brief": str(current_task_root / "task-brief.md") if current_task_root else "",
        "current_task_verification_summary": str(current_task_root / "verification-summary.json") if current_task_root else "",
        "audits_root": str(audits_root),
        "current_audit": str(audits_root / "current.json"),
        "upgrade_plans_root": str(upgrade_root),
        "current_upgrade_plan": str(upgrade_root / "current.json"),
        "migrations_root": str(migrations_root),
        "integrations_root": str(integrations_root),
        "youtrack_root": str(youtrack_root),
        "youtrack_connections_dir": str(youtrack_root / "connections"),
        "youtrack_secrets_dir": str(youtrack_root / "secrets"),
        "youtrack_field_catalogs_dir": str(youtrack_root / "field-catalogs"),
        "youtrack_searches_dir": str(youtrack_root / "searches"),
        "youtrack_plans_dir": str(youtrack_root / "plans"),
        "youtrack_issues_dir": str(youtrack_root / "issues"),
        "starter_runs_root": str(starter_runs_root),
    }


def _workstream_paths(workspace: str | Path, workstream_id: str) -> dict[str, str]:
    return workspace_paths(workspace, workstream_id=workstream_id)


def _task_paths(workspace: str | Path, task_id: str) -> dict[str, str]:
    return workspace_paths(workspace, task_id=task_id)


def _package_manifest(workspace_path: Path) -> tuple[dict[str, Any], set[str]]:
    package_path = workspace_path / "package.json"
    if not package_path.exists():
        return {}, set()
    payload = _load_json(package_path, default={}) or {}
    packages = set()
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        section = payload.get(field, {})
        if isinstance(section, dict):
            packages.update(section.keys())
    return payload, packages


def _compose_files(workspace_path: Path) -> list[Path]:
    candidates = [
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "docker-compose.dev.yml",
        "docker-compose.dev.yaml",
        "docker-stack.yml",
        "docker-stack.yaml",
        "stack.yml",
        "stack.yaml",
    ]
    return [workspace_path / candidate for candidate in candidates if (workspace_path / candidate).exists()]


def _safe_rglob(workspace_path: Path, pattern: str) -> list[Path]:
    matches: list[Path] = []
    for root, dirnames, filenames in os.walk(workspace_path):
        dirnames[:] = sorted(dirname for dirname in dirnames if dirname not in DISCOVERY_EXCLUDED_DIRS)
        for filename in sorted(filenames):
            if Path(filename).match(pattern):
                matches.append((Path(root) / filename).resolve())
    return matches


def _relative_path(workspace_path: Path, candidate: Path) -> str:
    relative = candidate.resolve().relative_to(workspace_path.resolve())
    return "." if str(relative) == "" else str(relative)


def _plugin_platform_detection(workspace_path: Path) -> dict[str, Any]:
    python_files = _safe_rglob(workspace_path, "*.py")
    plugin_manifests = [candidate for candidate in _safe_rglob(workspace_path, "plugin.json") if ".codex-plugin" in candidate.parts]
    mcp_configs = _safe_rglob(workspace_path, ".mcp.json")
    dashboard_indexes = [
        candidate for candidate in _safe_rglob(workspace_path, "index.html") if candidate.parent.name == "dashboard"
    ]

    plugin_roots: set[Path] = set()
    for manifest_path in plugin_manifests:
        plugin_roots.add(manifest_path.parent.parent.resolve())
    for mcp_path in mcp_configs:
        candidate_root = mcp_path.parent.resolve()
        if (candidate_root / ".codex-plugin" / "plugin.json").exists() or (candidate_root / "scripts").exists():
            plugin_roots.add(candidate_root)
    for dashboard_index in dashboard_indexes:
        candidate_root = dashboard_index.parent.parent.resolve()
        if (candidate_root / "scripts").exists():
            plugin_roots.add(candidate_root)

    ordered_plugin_roots = sorted(plugin_roots, key=lambda path: (len(_relative_path(workspace_path, path)), _relative_path(workspace_path, path)))
    plugin_python_files = [
        candidate
        for candidate in python_files
        if any(root == candidate.parent or root in candidate.parents for root in ordered_plugin_roots)
    ]
    primary_plugin_root = _relative_path(workspace_path, ordered_plugin_roots[0]) if ordered_plugin_roots else None
    release_readiness_command = None
    host_os = current_host_os()
    if primary_plugin_root:
        script_root = workspace_path if primary_plugin_root == "." else workspace_path / primary_plugin_root
        script_path = script_root / "scripts" / "release_readiness.py"
        if script_path.exists():
            relative_script = "scripts/release_readiness.py" if primary_plugin_root == "." else f"{primary_plugin_root}/scripts/release_readiness.py"
            release_readiness_command = f"{python_launcher_string(host_os)} {relative_script}"

    detected_features = []
    if plugin_python_files:
        detected_features.append("python")
    if plugin_manifests:
        detected_features.append("codex-plugin")
    if mcp_configs:
        detected_features.append("mcp-server")
    if dashboard_indexes:
        detected_features.append("local-dashboard")

    return {
        "enabled": bool(ordered_plugin_roots or detected_features),
        "detected_features": sorted(detected_features),
        "plugin_roots": [_relative_path(workspace_path, path) for path in ordered_plugin_roots],
        "primary_plugin_root": primary_plugin_root,
        "python_entrypoints": sorted(_relative_path(workspace_path, path) for path in plugin_python_files[:12]),
        "codex_plugin_manifests": sorted(_relative_path(workspace_path, path) for path in plugin_manifests),
        "mcp_configs": sorted(_relative_path(workspace_path, path) for path in mcp_configs),
        "dashboard_roots": sorted(_relative_path(workspace_path, path.parent) for path in dashboard_indexes),
        "release_readiness_command": release_readiness_command,
    }


def _available_upgrade_playbooks(selected_profiles: list[str]) -> list[str]:
    playbooks = ["workstream-task-readiness", "docs-sync"]
    if "local-infra" in selected_profiles or "backend-platform" in selected_profiles:
        playbooks.append("dockerize-local-infra")
    if "deterministic-verification" in selected_profiles:
        playbooks.append("deterministic-verification")
    if any(profile in selected_profiles for profile in {"web-platform", "mobile-platform"}):
        playbooks.append("design-hooks")
    if "plugin-platform" in selected_profiles:
        playbooks.append("plugin-platform-hardening")
    return playbooks


def detect_workspace(workspace: str | Path) -> dict[str, Any]:
    workspace_path = Path(workspace).expanduser().resolve()
    host_os = current_host_os()
    manifest, packages = _package_manifest(workspace_path)
    compose_files = _compose_files(workspace_path)
    compose_text = "\n".join(path.read_text(errors="ignore") for path in compose_files).lower()
    plugin_platform = _plugin_platform_detection(workspace_path)
    python_files = _safe_rglob(workspace_path, "*.py")
    techs: set[str] = set()
    signals: list[str] = []

    def mark(condition: bool, stack: str, signal: str) -> None:
        if condition:
            techs.add(stack)
            signals.append(signal)

    tsconfig_exists = any((workspace_path / name).exists() for name in ("tsconfig.json", "tsconfig.base.json"))
    tailwind_exists = any(
        (workspace_path / name).exists()
        for name in ("tailwind.config.js", "tailwind.config.cjs", "tailwind.config.ts")
    )
    app_json_exists = any(
        (workspace_path / name).exists()
        for name in ("app.json", "app.config.ts", "app.config.js", "app.config.json")
    )
    nest_config_exists = any((workspace_path / name).exists() for name in ("nest-cli.json", ".nestcli.json"))
    gradle_exists = any((workspace_path / name).exists() for name in ("build.gradle", "settings.gradle", "settings.gradle.kts"))
    gradle_exists = gradle_exists or (workspace_path / "android").exists()
    ios_exists = (workspace_path / "ios").exists() or (workspace_path / "Podfile").exists() or any(workspace_path.glob("*.xcodeproj"))
    docker_exists = (workspace_path / "Dockerfile").exists() or bool(compose_files)
    swarm_exists = any("stack" in path.name for path in compose_files) or ("deploy:" in compose_text and "replicas:" in compose_text)
    python_exists = any((workspace_path / name).exists() for name in ("pyproject.toml", "requirements.txt", "setup.py")) or bool(python_files)

    mark("react" in packages or "react-dom" in packages, "react", "package.json:react")
    mark("next" in packages, "nextjs", "package.json:next")
    mark("@nestjs/core" in packages or nest_config_exists, "nestjs", "nestjs-config")
    mark("typescript" in packages or tsconfig_exists, "typescript", "typescript-signal")
    mark(
        "react-native" in packages or ((workspace_path / "android").exists() and (workspace_path / "ios").exists()),
        "react-native",
        "react-native-signal",
    )
    mark("expo" in packages or app_json_exists, "expo", "expo-config")
    mark("nativewind" in packages, "nativewind", "package.json:nativewind")
    mark("tailwindcss" in packages or tailwind_exists, "tailwind", "tailwind-config")
    mark((workspace_path / "Cargo.toml").exists(), "rust", "Cargo.toml")
    mark(python_exists, "python", "python-signal")
    mark((workspace_path / "nx.json").exists() or "nx" in packages or "@nx/workspace" in packages, "nx", "nx-signal")
    mark(docker_exists, "docker", "dockerfile-or-compose")
    mark(bool(compose_files), "docker-compose", "compose-files")
    mark(swarm_exists, "docker-swarm", "swarm-signal")
    mark("postgres" in compose_text or "pg" in packages or "postgresql" in compose_text, "postgres", "postgres-signal")
    mark("mongo" in compose_text or "mongodb" in packages or "mongoose" in packages, "mongodb", "mongodb-signal")
    mark("redis" in compose_text or "redis" in packages or "ioredis" in packages, "redis", "redis-signal")
    mark("nats" in compose_text or "nats" in packages, "nats", "nats-signal")
    mark(gradle_exists, "android", "android-signal")
    mark(ios_exists, "ios", "ios-signal")
    mark(bool(plugin_platform["codex_plugin_manifests"]), "codex-plugin", "codex-plugin-manifest")
    mark(bool(plugin_platform["mcp_configs"]), "mcp-server", "mcp-config")
    mark(bool(plugin_platform["dashboard_roots"]), "local-dashboard", "dashboard-assets")

    selected_profiles = ["workspace-kernel", "docs-sync", "deterministic-verification"]
    if any(stack in techs for stack in {"react", "nextjs", "tailwind", "typescript"}):
        selected_profiles.append("web-platform")
    if any(stack in techs for stack in {"react-native", "expo", "nativewind", "android", "ios"}):
        selected_profiles.append("mobile-platform")
    if any(stack in techs for stack in {"nestjs", "rust", "postgres", "mongodb", "redis", "nats"}):
        selected_profiles.extend(["backend-platform", "local-infra"])
    if any(stack in techs for stack in {"docker", "docker-compose", "docker-swarm"}):
        selected_profiles.append("local-infra")
    if "nx" in techs:
        selected_profiles.append("monorepo-platform")
    if any(stack in techs for stack in {"codex-plugin", "mcp-server", "local-dashboard"}) or plugin_platform.get("enabled"):
        selected_profiles.append("plugin-platform")

    selected_profiles = sorted(set(selected_profiles))
    paths = workspace_paths(workspace_path)
    workspace_label = manifest.get("name") or workspace_path.name
    local_dev_policy = _local_dev_policy(techs, selected_profiles)
    toolchain_capabilities = _toolchain_capabilities(techs, host_os=host_os)
    capability_matrix = host_capabilities(host_os)
    return {
        "workspace_path": str(workspace_path),
        "workspace_name": workspace_path.name,
        "workspace_label": workspace_label,
        "workspace_slug": paths["workspace_slug"],
        "workspace_hash": paths["workspace_hash"],
        "host_os": host_os,
        "host_platform": host_os,
        "host_capabilities": capability_matrix,
        "toolchain_capabilities": toolchain_capabilities,
        "support_warnings": _support_warnings(techs, host_os, toolchain_capabilities),
        "detected_stacks": sorted(techs),
        "selected_profiles": selected_profiles,
        "plugin_platform": plugin_platform,
        "signals": sorted(set(signals)),
        "command_surface": CANONICAL_COMMAND_SURFACE,
        "command_alias_policy": language_policy(),
        "local_dev_policy": local_dev_policy,
        "docs_policy": {
            "codex_state_inside_repo": False,
            "project_docs_sync_required": True,
            "stage_state_external_canonical": True,
            "design_state_external_canonical": True,
            "reply_in_user_language": True,
        },
        "planning_policy": default_planning_policy(),
        "paths": paths,
        "package_manager": manifest.get("packageManager"),
        "available_starters": list_starter_presets()["presets"],
        "available_upgrade_playbooks": _available_upgrade_playbooks(selected_profiles),
    }


def workflow_advice(workspace: str | Path, request_text: str | None = None, auto_create: bool = False) -> dict[str, Any]:
    detection = detect_workspace(workspace)
    paths = detection["paths"]
    initialized = _workspace_initialized(paths)
    analysis = _analyze_request_text(request_text)
    starter = _recommend_starter_preset(request_text) if analysis["request_kind"] == "greenfield" else None
    current_workstream_payload = None
    current_task_payload = None
    workspace_state_payload = None
    track_recommendation: dict[str, Any] | None = None
    applied_action: dict[str, Any] | None = None

    if initialized:
        workspace_state_payload = read_workspace_state(workspace)
        if workspace_state_payload.get("current_workstream_id"):
            current_workstream_payload = current_workstream(workspace)
        current_task_payload = current_task(workspace)

    if analysis["recommended_mode"] == "workstream":
        should_create = current_workstream_payload is None
        track_recommendation = {
            "recommended_mode": "workstream",
            "should_propose": True,
            "should_create": should_create,
            "requires_confirmation": True,
            "reason": analysis["reason"],
            "title": _suggested_title_from_request(request_text, "New Workstream"),
            "scope_summary": _suggested_objective_from_request(request_text),
            "reuse_current_workstream_id": current_workstream_payload.get("workstream_id") if current_workstream_payload and not should_create else None,
        }
    elif analysis["recommended_mode"] == "task":
        should_create = current_task_payload is None or current_task_payload.get("status") not in {"planned", "active", "blocked", "awaiting_user"}
        track_recommendation = {
            "recommended_mode": "task",
            "should_propose": True,
            "should_create": should_create,
            "requires_confirmation": True,
            "reason": analysis["reason"],
            "title": _suggested_title_from_request(request_text, "Targeted Task"),
            "objective": _suggested_objective_from_request(request_text),
            "linked_workstream_id": current_workstream_payload.get("workstream_id") if current_workstream_payload else None,
            "reuse_current_task_id": current_task_payload.get("task_id") if current_task_payload and not should_create else None,
        }
        if initialized and analysis["request_kind"] == "point_task":
            if should_create:
                created_task = create_task(
                    workspace,
                    title=track_recommendation["title"],
                    objective=track_recommendation["objective"],
                    linked_workstream_id=track_recommendation.get("linked_workstream_id"),
                )
                current_task_payload = created_task["task"]
                workspace_state_payload = read_workspace_state(workspace)
                applied_action = {
                    "action": "create_task",
                    "mode": "task",
                    "reason": analysis["reason"],
                    "task_id": created_task["created_task_id"],
                    "linked_workstream_id": track_recommendation.get("linked_workstream_id"),
                }
            elif current_task_payload is not None:
                applied_action = {
                    "action": "reuse_current_task",
                    "mode": "task",
                    "reason": analysis["reason"],
                    "task_id": current_task_payload["task_id"],
                    "linked_workstream_id": current_task_payload.get("linked_workstream_id"),
                }
            track_recommendation["auto_applied"] = applied_action is not None
            track_recommendation["requires_confirmation"] = False

    next_actions: list[str] = []
    initialization_advice: dict[str, Any] | None = None
    if not initialized:
        preview = preview_workspace_init(workspace)
        initialization_advice = {
            "should_propose": True,
            "requires_confirmation": True,
            "reason": "This repository is not initialized in external AgentiUX Dev state yet.",
            "preview": preview,
        }
        next_actions.append("Propose workspace initialization before any stateful work starts.")
    if starter:
        next_actions.append(f"Propose starter preset `{starter['recommended_preset_id']}` and wait for confirmation before bootstrapping the new project.")
    if track_recommendation and initialized:
        if track_recommendation.get("auto_applied") and applied_action:
            next_actions.append(f"Continue in task mode using `{applied_action['task_id']}` for this focused request.")
        else:
            next_actions.append(f"Propose {track_recommendation['recommended_mode']} mode for this request and wait for explicit confirmation before writing state.")
    if analysis["request_kind"] == "commit":
        next_actions.append("Inspect existing commit history or commitlint-style rules before writing a commit message.")

    return {
        "workspace_path": detection["workspace_path"],
        "workspace_label": detection["workspace_label"],
        "workspace_initialized": initialized,
        "request_analysis": analysis,
        "initialization_advice": initialization_advice,
        "starter_recommendation": starter,
        "track_recommendation": track_recommendation,
        "workspace_state": workspace_state_payload,
        "current_workstream": current_workstream_payload,
        "current_task": current_task_payload,
        "language_policy": language_policy(),
        "applied_action": applied_action,
        "requires_confirmation": bool(initialization_advice or starter or (track_recommendation and not track_recommendation.get("auto_applied"))),
        "auto_create_supported": bool(initialized and analysis["request_kind"] == "point_task"),
        "next_actions": next_actions,
    }


CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?(!)?: .+"
)
MIN_COMMIT_STYLE_HISTORY = 3
FALLBACK_BRANCH_PREFIX = "task"
FALLBACK_WORKSTREAM_BRANCH_PREFIX = "feature"
PROTECTED_BRANCHES = {"main", "master", "develop", "trunk"}


def _git_output(repo_root: str | Path, argv: list[str]) -> str:
    result = subprocess.run(
        argv,
        cwd=str(Path(repo_root).expanduser().resolve()),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Command failed: {' '.join(argv)}")
    return result.stdout.rstrip("\n")


def _git_repo_exists(repo_root: str | Path) -> bool:
    return (Path(repo_root).expanduser().resolve() / ".git").exists()


def _commit_config_candidates(repo_root: Path) -> list[Path]:
    patterns = [
        ".commitlintrc",
        ".commitlintrc.json",
        ".commitlintrc.yml",
        ".commitlintrc.yaml",
        "commitlint.config.js",
        "commitlint.config.cjs",
        "commitlint.config.mjs",
        ".czrc",
    ]
    return [repo_root / candidate for candidate in patterns if (repo_root / candidate).exists()]


def _git_output_or_empty(repo_root: str | Path, argv: list[str]) -> str:
    try:
        return _git_output(repo_root, argv)
    except Exception:  # noqa: BLE001
        return ""


def _recent_git_branches(repo_root: str | Path, limit: int = 30) -> list[str]:
    output = _git_output_or_empty(repo_root, ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"])
    branches = [line.strip() for line in output.splitlines() if line.strip()]
    return branches[:limit]


def _infer_branch_prefix(branches: list[str]) -> tuple[str, str]:
    prefixes: dict[str, int] = {}
    for branch in branches:
        if "/" not in branch:
            continue
        prefix = branch.split("/", 1)[0]
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
    if prefixes:
        prefix = sorted(prefixes.items(), key=lambda item: (-item[1], item[0]))[0][0]
        return prefix, "history"
    return FALLBACK_BRANCH_PREFIX, "fallback"


def _git_message_bodies(repo_root: str | Path, limit: int) -> list[str]:
    if not _git_repo_exists(repo_root):
        return []
    output = _git_output_or_empty(repo_root, ["git", "log", f"-n{limit}", "--format=%B%x1e"])
    return [entry.strip() for entry in output.split("\x1e") if entry.strip()]


def _detect_issue_tokens(messages: list[str]) -> list[str]:
    issue_re = re.compile(r"\b[A-Z]{2,}-\d+\b")
    detected: list[str] = []
    for message in messages:
        for match in issue_re.findall(message):
            if match not in detected:
                detected.append(match)
    return detected[:5]


def _detect_trailers(bodies: list[str]) -> list[str]:
    trailers: list[str] = []
    for body in bodies:
        for line in body.splitlines()[1:]:
            if re.match(r"^[A-Za-z-]+: .+", line.strip()):
                trailer_name = line.split(":", 1)[0].strip()
                if trailer_name not in trailers:
                    trailers.append(trailer_name)
    return trailers


def detect_commit_style(repo_root: str | Path, limit: int = 30) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    config_files = _commit_config_candidates(root)
    config_rules: list[str] = []
    style_from_config = None
    for candidate in config_files:
        text = candidate.read_text(encoding="utf-8", errors="ignore").lower()
        if "conventional" in text or "type-enum" in text:
            style_from_config = "conventional"
            config_rules.append(f"{candidate.name}:conventional")

    messages: list[str] = []
    git_error = None
    branch_names: list[str] = []
    if _git_repo_exists(root):
        try:
            output = _git_output(root, ["git", "log", f"-n{limit}", "--format=%s"])
            messages = [line.strip() for line in output.splitlines() if line.strip()]
            branch_names = _recent_git_branches(root, limit=limit)
        except Exception as exc:  # noqa: BLE001
            git_error = str(exc)

    conventional_count = sum(1 for message in messages if CONVENTIONAL_COMMIT_RE.match(message))
    scoped_count = sum(1 for message in messages if re.match(r"^[a-z]+\(.*\): .+", message))
    uppercase_subjects = sum(1 for message in messages if message[:1].isupper())
    bodies = _git_message_bodies(root, limit)
    branch_prefix, branch_prefix_source = _infer_branch_prefix(branch_names)
    issue_tokens = _detect_issue_tokens(messages)
    trailers = _detect_trailers(bodies)

    history_sufficient = len(messages) >= MIN_COMMIT_STYLE_HISTORY
    style = style_from_config or "plain-imperative"
    confidence = 0.3 if not messages else 0.45
    source = "fallback"
    if style_from_config:
        source = "config"
        confidence = 0.9
    elif messages and history_sufficient:
        if conventional_count / len(messages) >= 0.6:
            style = "conventional"
            source = "history"
            confidence = 0.8
        else:
            style = "plain-imperative"
            source = "history"
            confidence = 0.65
    elif messages:
        style = "conventional" if conventional_count == len(messages) else "plain-imperative"
        source = "limited-history"
        confidence = 0.45

    subject_case = "sentence" if uppercase_subjects >= max(len(messages) // 2, 1) else "lower"
    return {
        "repo_root": str(root),
        "is_git_repo": _git_repo_exists(root),
        "has_commits": bool(messages),
        "style": style,
        "source": source,
        "confidence": round(confidence, 2),
        "uses_scope": bool(messages and scoped_count / len(messages) >= 0.4) or style_from_config == "conventional",
        "subject_case": subject_case,
        "examples": messages[:5],
        "config_files": [str(path) for path in config_files],
        "config_rules": config_rules,
        "history_count": len(messages),
        "history_sufficient": history_sufficient,
        "history_threshold": MIN_COMMIT_STYLE_HISTORY,
        "branch_examples": branch_names[:5],
        "preferred_branch_prefix": branch_prefix,
        "branch_prefix_source": branch_prefix_source,
        "issue_tokens": issue_tokens,
        "issue_token_pattern": "PROJECT-123" if issue_tokens else None,
        "uses_commit_body": bool([body for body in bodies if "\n" in body.strip()]),
        "uses_trailers": bool(trailers),
        "trailer_examples": trailers[:5],
        "preferred_pr_title_style": "match_commit_subject",
        "safety_rules": [
            "Do not amend, rebase, or rewrite history without explicit user approval.",
            "Prefer one logical commit per task unless the repository convention clearly suggests a split.",
            "Prefer repository consistency over generic Git style advice.",
        ],
        "git_error": git_error,
    }


def _infer_commit_type(summary: str, files: list[str] | None = None) -> str:
    normalized = normalize_command_phrase(summary)
    file_text = " ".join(files or []).lower()
    if any(keyword in normalized or keyword in file_text for keyword in ("readme", "docs", "documentation")):
        return "docs"
    if any(keyword in normalized or keyword in file_text for keyword in ("test", "spec", "verification", "smoke")):
        return "test"
    if any(keyword in normalized for keyword in ("fix", "bug", "repair", "hotfix", "\u0438\u0441\u043f\u0440\u0430\u0432", "\u043f\u043e\u0447\u0438\u043d")):
        return "fix"
    if any(keyword in normalized or keyword in file_text for keyword in ("ci", "workflow", "github actions")):
        return "ci"
    if any(keyword in normalized for keyword in ("refactor", "cleanup", "simplify", "restructure")):
        return "refactor"
    if any(keyword in normalized for keyword in ("release", "build", "install", "dependency")):
        return "build"
    return "feat"


def _infer_commit_scope(files: list[str] | None, uses_scope: bool) -> str | None:
    if not uses_scope or not files:
        return None
    top_levels: list[str] = []
    for file_path in files:
        parts = Path(file_path).parts
        if not parts:
            continue
        if "dashboard" in parts:
            top_levels.append("dashboard")
        elif "scripts" in parts:
            top_levels.append("scripts")
        elif "skills" in parts:
            top_levels.append("skills")
        elif len(parts) > 1 and parts[0] == "plugins":
            top_levels.append(parts[-2] if len(parts) > 2 else parts[-1])
        else:
            top_levels.append(parts[0])
    unique = sorted(set(item for item in top_levels if item))
    if len(unique) == 1:
        return unique[0]
    return None


def _git_workflow_advice(repo_root: str | Path) -> dict[str, Any]:
    style = detect_commit_style(repo_root)
    recommended_commit_style = style["style"] if style["source"] in {"config", "history"} else "conventional"
    branch_prefix = style.get("preferred_branch_prefix") or FALLBACK_BRANCH_PREFIX
    branch_pattern = f"{branch_prefix}/<slug>"
    root = Path(repo_root).expanduser().resolve()
    worktree_state = list_git_worktrees(root)
    safety_rules = list(dict.fromkeys(style["safety_rules"] + ["Do not force-push or rewrite shared history without explicit approval."]))
    return {
        "repo_root": str(root),
        "resolution_order": [
            "repo_config",
            "repo_history",
            "conventional_commits_fallback",
            "plain_imperative_fallback",
        ],
        "inspection": style,
        "branch_policy": {
            "recommended_prefix": branch_prefix,
            "prefix_source": style.get("branch_prefix_source"),
            "pattern": branch_pattern,
            "workstream_prefix_override": FALLBACK_WORKSTREAM_BRANCH_PREFIX if branch_prefix == FALLBACK_BRANCH_PREFIX else branch_prefix,
            "protected_branches": sorted(PROTECTED_BRANCHES),
        },
        "commit_policy": {
            "recommended_style": recommended_commit_style,
            "detected_style": style["style"],
            "detected_style_source": style["source"],
            "uses_scope": bool(style["uses_scope"] or recommended_commit_style == "conventional"),
            "subject_case": style["subject_case"],
            "commit_body_expected": style["uses_commit_body"],
            "fallback_order": ["conventional", "plain-imperative"],
        },
        "ticket_prefix_policy": {
            "examples": style["issue_tokens"],
            "pattern": style["issue_token_pattern"],
            "usage": "follow_repo_history" if style["issue_tokens"] else "optional",
        },
        "trailer_policy": {
            "uses_trailers": style["uses_trailers"],
            "trailer_examples": style["trailer_examples"],
            "signoff_required": "Signed-off-by" in style["trailer_examples"],
        },
        "pull_request_policy": {
            "title_style": style["preferred_pr_title_style"],
            "body_sections": ["Summary", "Changed Areas", "Verification", "Notes"],
            "draft_for_long_running_workstreams": True,
        },
        "worktree_policy": {
            "recommended_for_parallel_tasks": True,
            "recommended_for_long_running_workstreams": True,
            "current_worktree_count": worktree_state["worktree_count"],
            "current_checkout_is_linked_worktree": worktree_state["is_linked_worktree"],
            "path_pattern": str(root.parent / f"{root.name}-<slug>"),
            "branch_isolation": "prefer_one_branch_per_worktree",
        },
        "safety_rules": safety_rules,
    }


def suggest_commit_message(repo_root: str | Path, summary: str, files: list[str] | None = None) -> dict[str, Any]:
    advice = _git_workflow_advice(repo_root)
    inspection = advice["inspection"]
    context = _git_workspace_context(repo_root)
    compact_summary = _suggested_objective_from_request(summary)
    commit_type = _infer_commit_type(summary, files=files)
    scope = _infer_commit_scope(files, uses_scope=advice["commit_policy"]["uses_scope"])

    if advice["commit_policy"]["recommended_style"] == "conventional":
        subject = compact_summary.rstrip(".")
        subject = subject[:1].lower() + subject[1:] if subject else "update project state"
        prefix = commit_type
        if scope:
            prefix = f"{prefix}({scope})"
        message = f"{prefix}: {subject}"
    else:
        verb_map = {
            "docs": "Document",
            "test": "Add",
            "fix": "Fix",
            "ci": "Update",
            "refactor": "Refactor",
            "build": "Update",
            "feat": "Add",
        }
        verb = verb_map.get(commit_type, "Update")
        tail = compact_summary[:1].lower() + compact_summary[1:] if compact_summary else "project state"
        message = f"{verb} {tail}".rstrip(".")
    issue_key = _required_issue_prefix(context)
    if issue_key and not message.startswith(f"{issue_key} "):
        message = f"{issue_key} {message}"

    return {
        "repo_root": str(Path(repo_root).expanduser().resolve()),
        "inspection": inspection,
        "advice": advice,
        "summary": summary,
        "files": files or [],
        "workspace_context": context,
        "required_issue_prefix": issue_key,
        "commit_prefix_required": bool(issue_key),
        "suggested_message": message,
    }


def suggest_branch_name(repo_root: str | Path, summary: str, mode: str = "task") -> dict[str, Any]:
    advice = _git_workflow_advice(repo_root)
    prefix = advice["branch_policy"]["recommended_prefix"]
    slug = slugify(_suggested_title_from_request(summary, "change"))
    if mode == "workstream" and prefix == FALLBACK_BRANCH_PREFIX:
        prefix = advice["branch_policy"]["workstream_prefix_override"]
    return {
        "repo_root": str(Path(repo_root).expanduser().resolve()),
        "mode": mode,
        "advice": advice,
        "summary": summary,
        "suggested_branch_name": f"{prefix}/{slug}",
    }


def suggest_pr_title(repo_root: str | Path, summary: str, files: list[str] | None = None) -> dict[str, Any]:
    commit_suggestion = suggest_commit_message(repo_root, summary, files=files)
    title = commit_suggestion["suggested_message"]
    if commit_suggestion["inspection"]["style"] != "conventional" and title:
        title = title[:1].upper() + title[1:]
    return {
        "repo_root": str(Path(repo_root).expanduser().resolve()),
        "summary": summary,
        "files": files or [],
        "suggested_pr_title": title,
        "inspection": commit_suggestion["inspection"],
        "advice": commit_suggestion["advice"],
    }


def suggest_pr_body(repo_root: str | Path, summary: str, files: list[str] | None = None) -> dict[str, Any]:
    advice = _git_workflow_advice(repo_root)
    bullet_files = "\n".join(f"- {path}" for path in (files or [])) or "- No file list supplied."
    suggested_body = textwrap.dedent(
        f"""\
        ## Summary

        {summary.strip() or "Update the repository."}

        ## Changed Areas

        {bullet_files}

        ## Verification

        - Add or summarize the deterministic checks that were run for this change.

        ## Notes

        - Keep the PR aligned with repository conventions and avoid history rewrites without approval.
        - Branch policy: `{advice['branch_policy']['pattern']}`
        """
    ).strip()
    return {
        "repo_root": str(Path(repo_root).expanduser().resolve()),
        "inspection": advice["inspection"],
        "advice": advice,
        "summary": summary,
        "files": files or [],
        "suggested_pr_body": suggested_body,
    }


def show_git_workflow_advice(repo_root: str | Path) -> dict[str, Any]:
    return _git_workflow_advice(repo_root)


def _current_git_root(repo_root: str | Path) -> Path:
    return Path(_git_output(repo_root, ["git", "rev-parse", "--show-toplevel"])).expanduser().resolve()


def _resolve_worktree_path(repo_root: str | Path, path: str | Path) -> Path:
    root = _current_git_root(repo_root)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (root.parent / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return candidate
    raise ValueError("Linked worktree path must be outside the repository root.")


def _suggested_worktree_path(repo_root: str | Path, summary: str) -> str:
    root = _current_git_root(repo_root)
    slug = slugify(_suggested_title_from_request(summary, root.name))
    return str(root.parent / f"{root.name}-{slug}")


def list_git_worktrees(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    if not _git_repo_exists(root):
        raise FileNotFoundError(f"Not a git repository: {root}")
    current_root = _current_git_root(root)
    git_dir_value = _git_output_or_empty(root, ["git", "rev-parse", "--git-dir"]) or None
    git_common_dir_value = _git_output_or_empty(root, ["git", "rev-parse", "--git-common-dir"]) or git_dir_value
    git_dir = str((current_root / git_dir_value).resolve()) if git_dir_value and not Path(git_dir_value).is_absolute() else git_dir_value
    git_common_dir = (
        str((current_root / git_common_dir_value).resolve())
        if git_common_dir_value and not Path(git_common_dir_value).is_absolute()
        else git_common_dir_value
    )
    output = _git_output_or_empty(root, ["git", "worktree", "list", "--porcelain"])
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    def flush() -> None:
        nonlocal current
        if not current:
            return
        path_value = current.get("path")
        if not path_value:
            current = {}
            return
        resolved = Path(path_value).expanduser().resolve()
        entries.append(
            {
                "path": str(resolved),
                "head": current.get("head"),
                "branch": current.get("branch"),
                "branch_ref": current.get("branch_ref"),
                "detached": bool(current.get("detached")),
                "bare": bool(current.get("bare")),
                "locked": current.get("locked"),
                "prunable": current.get("prunable"),
                "is_current": resolved == current_root,
            }
        )
        current = {}

    for line in output.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            flush()
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch_ref"] = value
            current["branch"] = value.rsplit("/", 1)[-1]
        elif key == "detached":
            current["detached"] = True
        elif key == "bare":
            current["bare"] = True
        elif key == "locked":
            current["locked"] = value or True
        elif key == "prunable":
            current["prunable"] = value or True
    flush()

    return {
        "repo_root": str(current_root),
        "current_worktree_path": str(current_root),
        "git_dir": git_dir,
        "git_common_dir": git_common_dir,
        "is_linked_worktree": bool(git_dir and git_common_dir and git_dir != git_common_dir),
        "worktree_count": len(entries),
        "linked_worktree_count": max(len(entries) - 1, 0),
        "worktrees": entries,
    }


def _git_branch_exists(repo_root: str | Path, branch_name: str) -> bool:
    if not branch_name:
        return False
    output = _git_output_or_empty(repo_root, ["git", "branch", "--list", branch_name])
    return bool(output.strip())


def _git_status_entries(repo_root: str | Path) -> list[dict[str, Any]]:
    if not _git_repo_exists(repo_root):
        return []
    output = _git_output_or_empty(repo_root, ["git", "status", "--porcelain"])
    entries: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line:
            continue
        status = line[:2]
        raw_path = line[3:] if len(line) > 3 else ""
        path = raw_path.split(" -> ", 1)[-1].strip()
        if status == "??":
            entries.append(
                {
                    "path": path,
                    "raw_status": status,
                    "staged_status": "?",
                    "unstaged_status": "?",
                    "staged": False,
                    "unstaged": False,
                    "untracked": True,
                    "conflicted": False,
                }
            )
            continue
        staged_status = status[:1]
        unstaged_status = status[1:2]
        conflicted = status in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"} or "U" in status
        entries.append(
            {
                "path": path,
                "raw_status": status,
                "staged_status": staged_status,
                "unstaged_status": unstaged_status,
                "staged": staged_status not in {" ", "?"},
                "unstaged": unstaged_status not in {" ", "?"},
                "untracked": False,
                "conflicted": conflicted,
            }
        )
    return entries


def _normalize_git_file_args(repo_root: str | Path, files: list[str]) -> list[str]:
    root = Path(repo_root).expanduser().resolve()
    normalized: list[str] = []
    for candidate in files:
        path = Path(candidate).expanduser()
        if path.is_absolute():
            try:
                normalized.append(str(path.resolve().relative_to(root)))
            except ValueError:
                normalized.append(str(path.resolve()))
        else:
            normalized.append(str(path))
    return normalized


def inspect_git_state(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    if not _git_repo_exists(root):
        raise FileNotFoundError(f"Not a git repository: {root}")
    advice = _git_workflow_advice(root)
    worktree_state = list_git_worktrees(root)
    current_branch = _git_output_or_empty(root, ["git", "rev-parse", "--abbrev-ref", "HEAD"]) or None
    detached_head = current_branch == "HEAD"
    upstream_branch = _git_output_or_empty(root, ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]) or None
    ahead_count = 0
    behind_count = 0
    if upstream_branch:
        counts = _git_output_or_empty(root, ["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream_branch}"])
        if counts:
            ahead_text, behind_text = counts.split()
            ahead_count = int(ahead_text)
            behind_count = int(behind_text)
    head_commit = _git_output_or_empty(root, ["git", "rev-parse", "--short", "HEAD"]) or None
    entries = _git_status_entries(root)
    staged_files = [entry["path"] for entry in entries if entry["staged"]]
    unstaged_files = [entry["path"] for entry in entries if entry["unstaged"]]
    untracked_files = [entry["path"] for entry in entries if entry["untracked"]]
    conflicted_files = [entry["path"] for entry in entries if entry["conflicted"]]
    warnings = list(advice["safety_rules"])
    if detached_head:
        warnings.append("HEAD is detached, so create or switch to a branch before committing.")
    if conflicted_files:
        warnings.append("Resolve conflicted files before staging or committing changes.")
    if behind_count > 0 and upstream_branch:
        warnings.append(f"Current branch is behind `{upstream_branch}` by {behind_count} commit(s).")
    if ahead_count > 0 and upstream_branch:
        warnings.append(f"Current branch is ahead of `{upstream_branch}` by {ahead_count} commit(s).")
    if not entries:
        warnings.append("Working tree is clean.")
    if worktree_state["linked_worktree_count"] > 0:
        warnings.append(f"Repository currently has {worktree_state['worktree_count']} linked worktrees.")
    return {
        "repo_root": str(root),
        "head_commit": head_commit,
        "current_branch": None if detached_head else current_branch,
        "detached_head": detached_head,
        "upstream_branch": upstream_branch,
        "ahead_count": ahead_count,
        "behind_count": behind_count,
        "changed_files": entries,
        "staged_files": staged_files,
        "unstaged_files": unstaged_files,
        "untracked_files": untracked_files,
        "conflicted_files": conflicted_files,
        "dirty": bool(entries),
        "summary_counts": {
            "changed_files": len(entries),
            "staged_files": len(staged_files),
            "unstaged_files": len(unstaged_files),
            "untracked_files": len(untracked_files),
            "conflicted_files": len(conflicted_files),
        },
        "worktree": worktree_state,
        "workflow_advice": advice,
        "safety_warnings": list(dict.fromkeys(warnings)),
    }


def _git_workspace_context(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    paths = workspace_paths(root)
    if not _workspace_initialized(paths):
        return {
            "workspace_initialized": False,
            "context_type": None,
            "summary": None,
            "task_id": None,
            "workstream_id": None,
            "issue_key": None,
        }
    state = read_workspace_state(root)
    task = current_task(root)
    workstream = current_workstream(root) if state.get("current_workstream_id") else None
    if task:
        summary = task.get("objective") or task.get("title")
        external_issue = task.get("external_issue") or {}
        return {
            "workspace_initialized": True,
            "context_type": "task",
            "summary": summary,
            "task_id": task.get("task_id"),
            "workstream_id": task.get("linked_workstream_id"),
            "issue_key": external_issue.get("issue_key") or external_issue.get("issue_id"),
        }
    if workstream:
        summary = workstream.get("scope_summary") or workstream.get("title")
        return {
            "workspace_initialized": True,
            "context_type": "workstream",
            "summary": summary,
            "task_id": None,
            "workstream_id": workstream.get("workstream_id"),
            "issue_key": None,
        }
    return {
        "workspace_initialized": True,
        "context_type": None,
        "summary": None,
        "task_id": None,
        "workstream_id": state.get("current_workstream_id"),
        "issue_key": None,
    }


def _required_issue_prefix(context: dict[str, Any] | None) -> str | None:
    issue_key = (context or {}).get("issue_key")
    return str(issue_key).strip() if issue_key else None


def _record_task_commit(workspace: str | Path, task_id: str, commit_hash: str, message: str) -> None:
    try:
        task = read_task(workspace, task_id=task_id)
    except FileNotFoundError:
        return
    task["latest_commit"] = {
        "commit_hash": commit_hash,
        "message": message,
        "recorded_at": now_iso(),
    }
    persisted = _persist_task_record(workspace, task)
    _sync_linked_issue_ledger(workspace, persisted)


def plan_git_change(repo_root: str | Path, summary: str | None = None, files: list[str] | None = None) -> dict[str, Any]:
    state = inspect_git_state(repo_root)
    context = _git_workspace_context(repo_root)
    resolved_summary = (summary or context.get("summary") or "Update repository state").strip()
    resolved_files = files or state["staged_files"] or [entry["path"] for entry in state["changed_files"]]
    mode = "workstream" if context.get("context_type") == "workstream" else "task"
    branch_suggestion = suggest_branch_name(repo_root, resolved_summary, mode=mode)
    current_branch = state.get("current_branch")
    suggested_branch_name = branch_suggestion["suggested_branch_name"]
    suggested_worktree_path = _suggested_worktree_path(repo_root, resolved_summary)
    branch_action = "reuse_current_branch"
    if state.get("detached_head") or not current_branch or current_branch in PROTECTED_BRANCHES:
        branch_action = "create_and_switch"
    elif current_branch == suggested_branch_name:
        branch_action = "keep_current_branch"
    worktree_action = "current_checkout_ok"
    if mode == "workstream" and not state["dirty"] and current_branch in PROTECTED_BRANCHES:
        worktree_action = "create_linked_worktree"
    elif mode == "workstream":
        worktree_action = "consider_linked_worktree"
    commit_suggestion = suggest_commit_message(repo_root, resolved_summary, files=resolved_files)
    pr_title = suggest_pr_title(repo_root, resolved_summary, files=resolved_files)
    pr_body = suggest_pr_body(repo_root, resolved_summary, files=resolved_files)
    required_issue_prefix = _required_issue_prefix(context)
    confirmations = []
    if worktree_action == "create_linked_worktree":
        confirmations.append(f"Confirm creating linked worktree `{suggested_worktree_path}` for `{suggested_branch_name}`.")
    if branch_action == "create_and_switch":
        confirmations.append(f"Confirm creating and switching to `{suggested_branch_name}` before editing git state.")
    if resolved_files:
        confirmations.append("Confirm the staged file set before running git add.")
    confirmations.append("Confirm commit creation before running git commit.")
    return {
        "repo_root": state["repo_root"],
        "git_state": state,
        "workspace_context": context,
        "resolved_summary": resolved_summary,
        "recommended_staged_files": resolved_files,
        "branch_action": branch_action,
        "worktree_action": worktree_action,
        "suggested_worktree_path": suggested_worktree_path,
        "suggested_branch_name": suggested_branch_name,
        "suggested_commit_message": commit_suggestion["suggested_message"],
        "required_issue_prefix": required_issue_prefix,
        "commit_prefix_required": bool(required_issue_prefix),
        "suggested_pr_title": pr_title["suggested_pr_title"],
        "suggested_pr_body": pr_body["suggested_pr_body"],
        "required_confirmations": confirmations,
        "advice": state["workflow_advice"],
    }


def create_git_worktree(repo_root: str | Path, path: str | Path, branch_name: str, start_point: str = "HEAD") -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    if not _git_repo_exists(root):
        raise FileNotFoundError(f"Not a git repository: {root}")
    if _git_branch_exists(root, branch_name):
        raise ValueError(f"Branch already exists: {branch_name}")
    destination = _resolve_worktree_path(root, path)
    argv = ["git", "worktree", "add", "-b", branch_name, str(destination), start_point or "HEAD"]
    _git_output(root, argv)
    return {
        "repo_root": str(_current_git_root(root)),
        "worktree_path": str(destination),
        "branch_name": branch_name,
        "start_point": start_point or "HEAD",
        "git_state": inspect_git_state(destination),
        "worktree_state": list_git_worktrees(root),
    }


def create_git_branch(repo_root: str | Path, branch_name: str) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    if not _git_repo_exists(root):
        raise FileNotFoundError(f"Not a git repository: {root}")
    current_branch = _git_output_or_empty(root, ["git", "rev-parse", "--abbrev-ref", "HEAD"]) or None
    if current_branch == branch_name:
        status = "already_current"
    elif _git_branch_exists(root, branch_name):
        _git_output(root, ["git", "switch", branch_name])
        status = "switched_existing"
    else:
        _git_output(root, ["git", "switch", "-c", branch_name])
        status = "created"
    return {
        "repo_root": str(root),
        "branch_name": branch_name,
        "status": status,
        "git_state": inspect_git_state(root),
    }


def stage_git_files(repo_root: str | Path, files: list[str]) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    if not _git_repo_exists(root):
        raise FileNotFoundError(f"Not a git repository: {root}")
    if not files:
        raise ValueError("stage_git_files requires at least one file path.")
    normalized_files = _normalize_git_file_args(root, files)
    _git_output(root, ["git", "add", "--", *normalized_files])
    return {
        "repo_root": str(root),
        "staged_files_requested": normalized_files,
        "git_state": inspect_git_state(root),
    }


def create_git_commit(repo_root: str | Path, message: str, body: str | None = None) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    if not _git_repo_exists(root):
        raise FileNotFoundError(f"Not a git repository: {root}")
    state = inspect_git_state(root)
    context = _git_workspace_context(root)
    if not state["staged_files"]:
        raise ValueError("No staged changes are available for commit creation.")
    issue_key = _required_issue_prefix(context)
    if issue_key and not re.match(rf"^{re.escape(issue_key)}\b", message):
        raise ValueError(f"Commit message must start with the linked issue id: {issue_key}")
    argv = ["git", "commit", "-m", message]
    if body:
        argv.extend(["-m", body])
    _git_output(root, argv)
    commit_hash = _git_output(root, ["git", "rev-parse", "--short", "HEAD"])
    if context.get("task_id"):
        _record_task_commit(root, context["task_id"], commit_hash, message)
    return {
        "repo_root": str(root),
        "commit_hash": commit_hash,
        "message": message,
        "body": body,
        "workspace_context": context,
        "required_issue_prefix": issue_key,
        "git_state": inspect_git_state(root),
    }


def _default_registry() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "plugin": plugin_info(),
        "updated_at": now_iso(),
        "workspaces": {},
    }


def _registry_path() -> Path:
    return state_root() / "registry.json"


def _load_registry() -> dict[str, Any]:
    return _load_json(_registry_path(), default=_default_registry()) or _default_registry()


def _workspace_key(detection: dict[str, Any]) -> str:
    return f"{detection['workspace_path']}::{detection['workspace_hash']}"


def _load_templates() -> dict[str, Any]:
    template_path = plugin_root() / "templates" / "profile-packs.json"
    payload = _load_json(template_path, default=None, strict=True, purpose="template library")
    if not payload:
        raise FileNotFoundError(f"Missing template file: {template_path}")
    for section in (
        "stageModules",
        "starterFragments",
        "verificationFragments",
        "docsHints",
        "designHandoffScaffolds",
        "profilePacks",
    ):
        section_payload = payload.get(section) or {}
        if not isinstance(section_payload, dict):
            raise ValueError(f"Template library section `{section}` must be an object.")
        payload[section] = section_payload
    return payload


def _profile_notes(selected_profiles: list[str], templates: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for profile in selected_profiles:
        pack = templates["profilePacks"].get(profile, {})
        notes.extend(pack.get("notes", []))
    deduped: list[str] = []
    for note in notes:
        if note not in deduped:
            deduped.append(note)
    return deduped


def _template_stage_modules(templates: dict[str, Any]) -> dict[str, Any]:
    return templates["stageModules"]


def _template_optional_section(templates: dict[str, Any], section: str) -> dict[str, Any]:
    payload = templates.get(section) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Template library section `{section}` must be an object.")
    return payload


def _merge_fragment_value(current: Any, incoming: Any) -> Any:
    if isinstance(current, dict) and isinstance(incoming, dict):
        merged = copy.deepcopy(current)
        for key, value in incoming.items():
            if key in merged:
                merged[key] = _merge_fragment_value(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    if isinstance(current, list) and isinstance(incoming, list):
        merged = copy.deepcopy(current)
        for item in incoming:
            if item not in merged:
                merged.append(copy.deepcopy(item))
        return merged
    return copy.deepcopy(incoming)


def _merge_named_items(current: list[dict[str, Any]], incoming: list[dict[str, Any]], *, id_field: str = "id") -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [copy.deepcopy(item) for item in current]
    item_positions = {
        item.get(id_field): index
        for index, item in enumerate(merged)
        if isinstance(item, dict) and item.get(id_field)
    }
    for item in incoming:
        item_id = item.get(id_field)
        if item_id and item_id in item_positions:
            merged[item_positions[item_id]] = _merge_fragment_value(merged[item_positions[item_id]], item)
        else:
            if item_id:
                item_positions[item_id] = len(merged)
            merged.append(copy.deepcopy(item))
    return merged


def _template_resolution(source_module_ids: list[str], note: str) -> dict[str, Any]:
    return _template_resolution_with_origin(source_module_ids, note)


def _template_resolution_with_origin(source_module_ids: list[str], note: str, *, origin: str | None = None) -> dict[str, Any]:
    resolved_origin = origin or ("template" if source_module_ids else "custom")
    if resolved_origin not in {"custom", "template", "mixed"}:
        raise ValueError(f"Unsupported template resolution origin: {resolved_origin}")
    return {
        "origin": resolved_origin,
        "source_module_ids": source_module_ids,
        "planner_notes": [note] if note else [],
    }


def _matches_fragment_values(expected: list[str] | None, actual: str | None) -> bool:
    if not expected:
        return True
    if not actual:
        return False
    return actual in expected


def _fragment_matches(
    fragment: dict[str, Any],
    *,
    selected_profiles: list[str] | None = None,
    detected_stacks: list[str] | None = None,
    preset_id: str | None = None,
    verification_profile: str | None = None,
    design_platform: str | None = None,
) -> bool:
    profiles = fragment.get("profiles") or []
    if profiles and not set(profiles).intersection(selected_profiles or []):
        return False
    required_stacks = fragment.get("detectedStacks") or []
    if required_stacks and not set(required_stacks).intersection(detected_stacks or []):
        return False
    if not _matches_fragment_values(fragment.get("presetIds") or [], preset_id):
        return False
    if not _matches_fragment_values(fragment.get("verificationProfiles") or [], verification_profile):
        return False
    if not _matches_fragment_values(fragment.get("platforms") or [], design_platform):
        return False
    return True


def _materialize_verification_fragment_payload(
    payload: dict[str, Any],
    workspace: str | Path,
    detection: dict[str, Any],
) -> dict[str, Any]:
    materialized = copy.deepcopy(payload)
    plugin_platform = detection.get("plugin_platform") or {}
    primary_root = plugin_platform.get("primary_plugin_root")
    if primary_root:
        readiness_script = Path(workspace).expanduser().resolve() / primary_root / "scripts" / "release_readiness.py"
    else:
        readiness_script = None
    for case in materialized.get("cases", []):
        command_template = case.pop("command_template", None)
        if not command_template:
            continue
        if command_template.get("kind") != "release_readiness":
            raise ValueError(f"Unsupported verification command template: {command_template}")
        if readiness_script is None or not readiness_script.exists():
            raise FileNotFoundError(f"Release readiness script is not available for verification fragments: {workspace}")
        case["argv"] = python_script_command(
            readiness_script,
            [
                command_template["check"],
                "--repo-root",
                str(Path(workspace).expanduser().resolve()),
            ],
            host_os=detection.get("host_os"),
        )
    return materialized


def _resolve_starter_fragments(preset: dict[str, Any], templates: dict[str, Any]) -> dict[str, Any]:
    library = _template_optional_section(templates, "starterFragments")
    applied_ids: list[str] = []
    resolved_post_setup = copy.deepcopy(preset.get("post_setup") or {})
    for fragment_id, fragment in library.items():
        if not _fragment_matches(fragment, preset_id=preset.get("preset_id")):
            continue
        applied_ids.append(fragment_id)
        resolved_post_setup = _merge_fragment_value(resolved_post_setup, fragment.get("post_setup") or {})
    return {
        "post_setup": resolved_post_setup,
        "starter_post_setup_resolution": _template_resolution_with_origin(
            applied_ids,
            "Starter post-setup was resolved from the template library." if applied_ids else "Starter post-setup used preset defaults only.",
            origin="template" if applied_ids else "custom",
        ),
    }


def _resolve_verification_fragments(
    workspace: str | Path,
    detection: dict[str, Any],
    templates: dict[str, Any] | None = None,
    *,
    verification_profile: str | None = None,
) -> dict[str, Any]:
    loaded_templates = templates or _load_templates()
    library = _template_optional_section(loaded_templates, "verificationFragments")
    applied_ids: list[str] = []
    payload = {
        "cases": [],
        "suites": [],
    }
    for fragment_id, fragment in library.items():
        if not _fragment_matches(
            fragment,
            selected_profiles=detection.get("selected_profiles"),
            detected_stacks=detection.get("detected_stacks"),
            verification_profile=verification_profile,
        ):
            continue
        applied_ids.append(fragment_id)
        fragment_payload = _materialize_verification_fragment_payload(
            fragment.get("verification") or {},
            workspace,
            detection,
        )
        payload = _merge_fragment_value(payload, {key: value for key, value in fragment_payload.items() if key not in {"cases", "suites"}})
        payload["cases"] = _merge_named_items(payload.get("cases", []), fragment_payload.get("cases", []))
        payload["suites"] = _merge_named_items(payload.get("suites", []), fragment_payload.get("suites", []))
    return {
        "verification": payload,
        "verification_fragment_resolution": _template_resolution_with_origin(
            applied_ids,
            "Verification fragments were resolved from the template library." if applied_ids else "No verification fragments matched the current context.",
            origin="template" if applied_ids else "custom",
        ),
    }


def _resolve_docs_hints(selected_profiles: list[str], templates: dict[str, Any]) -> dict[str, Any]:
    hints_library = _template_optional_section(templates, "docsHints")
    applied_ids: list[str] = []
    hints: list[str] = []
    for hint_id, hint_payload in hints_library.items():
        if not _fragment_matches(hint_payload, selected_profiles=selected_profiles):
            continue
        applied_ids.append(hint_id)
        hints.extend(hint_payload.get("hints") or [])
    deduped_hints: list[str] = []
    for hint in hints:
        if hint not in deduped_hints:
            deduped_hints.append(hint)
    return {
        "docs_hints": deduped_hints,
        "docs_hint_resolution": _template_resolution_with_origin(
            applied_ids,
            "Docs hints were resolved from the optional template library." if applied_ids else "No docs hints matched the selected profiles.",
            origin="template" if applied_ids else "custom",
        ),
    }


def _resolve_design_handoff_scaffold(design_platform: str | None, templates: dict[str, Any]) -> dict[str, Any]:
    library = _template_optional_section(templates, "designHandoffScaffolds")
    applied_ids: list[str] = []
    scaffold: dict[str, Any] = {}
    for scaffold_id, scaffold_payload in library.items():
        if not _fragment_matches(scaffold_payload, design_platform=design_platform):
            continue
        applied_ids.append(scaffold_id)
        scaffold = _merge_fragment_value(scaffold, scaffold_payload.get("handoff") or {})
    return {
        "handoff": scaffold,
        "design_handoff_scaffold_resolution": _template_resolution_with_origin(
            applied_ids,
            "Design handoff scaffold was resolved from the template library." if applied_ids else "No design handoff scaffold matched the requested platform.",
            origin="template" if applied_ids else "custom",
        ),
    }


def _should_compact_stage_plan(detection: dict[str, Any], workstream_record: dict[str, Any]) -> bool:
    if workstream_record.get("kind") in {"fix", "task", "hotfix", "small-change"}:
        return True
    summary = normalize_command_phrase(
        " ".join(
            value
            for value in [
                workstream_record.get("title"),
                workstream_record.get("scope_summary"),
            ]
            if value
        )
    )
    return bool(summary and _matched_keywords(summary, SMALL_TASK_HINTS)) and detection.get("local_dev_policy", {}).get("infra_mode") == "not_applicable"


def _planner_audit_gap_count(detection: dict[str, Any]) -> int:
    audit_path = Path(workspace_paths(detection["workspace_path"])["current_audit"])
    audit = _load_json(audit_path, default={}, strict=False)
    if not audit or audit.get("workspace_path") != detection["workspace_path"]:
        return 0
    return len(audit.get("gaps", []))


def _planner_context(detection: dict[str, Any], workstream_record: dict[str, Any], *, setup_drift: bool = False) -> dict[str, Any]:
    compact = _should_compact_stage_plan(detection, workstream_record)
    verification_policy = workstream_record.get("verification_policy") or {}
    workstream_kind = workstream_record.get("kind") or "feature"
    greenfield = workstream_kind == "greenfield"
    return {
        "workstream_id": workstream_record.get("workstream_id"),
        "workstream_kind": workstream_kind,
        "request_size": "focused" if compact else "expanded",
        "greenfield": greenfield,
        "existing_repo": not greenfield,
        "setup_drift": setup_drift,
        "audit_gap_count": _planner_audit_gap_count(detection),
        "selected_profiles": detection.get("selected_profiles", []),
        "needs_plugin_runtime": "plugin-platform" in detection.get("selected_profiles", []),
        "needs_local_dev_infra": detection.get("local_dev_policy", {}).get("infra_mode") != "not_applicable",
        "verification_default_mode": verification_policy.get("default_mode", "targeted"),
        "verification_closeout_mode": verification_policy.get("closeout_default_mode", "targeted"),
        "requires_workspace_baseline": greenfield or setup_drift,
    }


def _planned_stage_entry(
    shell_id: str,
    origin: str,
    source_module_ids: list[str],
    note: str,
    *,
    verification_selectors: dict[str, Any] | None = None,
    verification_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "shell_id": shell_id,
        "origin": origin,
        "source_module_ids": source_module_ids,
        "planner_notes": [note],
        "verification_selectors": copy.deepcopy(verification_selectors or {}),
        "verification_policy": copy.deepcopy(verification_policy or {}),
    }


def _planner_stage_shells(
    detection: dict[str, Any],
    workstream_record: dict[str, Any],
    *,
    planner_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return []


def _materialize_stage_entry(
    planned_stage: dict[str, Any],
    rule_bundles: dict[str, Any],
    *,
    order: int,
    profile_notes: list[str],
    docs_hints: list[str],
) -> dict[str, Any]:
    if planned_stage.get("stage_definition"):
        stage_definition = copy.deepcopy(planned_stage["stage_definition"])
    else:
        stage_definition = copy.deepcopy(STAGE_SHELL_LIBRARY[planned_stage["shell_id"]])
    stage_definition["order"] = order
    stage_definition["status"] = "planned"
    stage_definition["completed_at"] = None
    stage_definition["profile_notes"] = profile_notes
    stage_definition["docs_hints"] = docs_hints
    stage_definition["origin"] = planned_stage["origin"]
    stage_definition["source_module_ids"] = planned_stage["source_module_ids"]
    stage_definition["planner_notes"] = planned_stage["planner_notes"]
    stage_definition["verification_selectors"] = copy.deepcopy(planned_stage.get("verification_selectors") or stage_definition.get("verification_selectors") or {})
    stage_definition["verification_policy"] = copy.deepcopy(planned_stage.get("verification_policy") or stage_definition.get("verification_policy") or {})
    stage_definition["checklists"] = {"entry": [], "closeout": []}
    stage_definition["docs_sync_obligations"] = []
    stage_definition["verification_hooks"] = []
    stage_definition["closeout_rules"] = []
    stage_definition["guidance_hints"] = []
    for bundle_id in planned_stage["source_module_ids"]:
        bundle = copy.deepcopy(rule_bundles.get(bundle_id) or {})
        if not bundle:
            continue
        stage_definition["checklists"]["entry"] = _merge_fragment_value(stage_definition["checklists"]["entry"], bundle.get("checklists", {}).get("entry", []))
        stage_definition["checklists"]["closeout"] = _merge_fragment_value(stage_definition["checklists"]["closeout"], bundle.get("checklists", {}).get("closeout", []))
        stage_definition["docs_sync_obligations"] = _merge_fragment_value(stage_definition["docs_sync_obligations"], bundle.get("docs_sync_obligations", []))
        stage_definition["verification_hooks"] = _merge_fragment_value(stage_definition["verification_hooks"], bundle.get("verification_hooks", []))
        stage_definition["closeout_rules"] = _merge_fragment_value(stage_definition["closeout_rules"], bundle.get("closeout_rules", []))
        stage_definition["guidance_hints"] = _merge_fragment_value(stage_definition["guidance_hints"], bundle.get("guidance_hints", []))
        if bundle.get("verification_policy"):
            stage_definition["verification_policy"] = _merge_fragment_value(stage_definition["verification_policy"], bundle["verification_policy"])
    stage_definition["resolved_guidance"] = {
        "profile_notes": profile_notes,
        "docs_hints": docs_hints,
        "guidance_hints": stage_definition["guidance_hints"],
    }
    return stage_definition


def _default_design_brief(workspace_path: str, paths: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workspace_path": workspace_path,
        "status": "not_started",
        "platform": None,
        "surface": None,
        "audience": None,
        "constraints": [],
        "style_goals": [],
        "exclusions": [],
        "notes": [],
        "reference_board_path": paths["reference_board"],
        "design_handoff_path": paths["design_handoff"],
        "updated_at": now_iso(),
    }


def _default_reference_board(workspace_path: str, paths: dict[str, str], board_id: str = "current") -> dict[str, Any]:
    board_key = sanitize_identifier(board_id, "current")
    return {
        "schema_version": 1,
        "workspace_path": workspace_path,
        "board_id": board_key,
        "title": board_key,
        "platform": None,
        "iteration_count": 0,
        "search_notes": [],
        "candidates": [],
        "selected_candidate_ids": [],
        "rejected_candidate_ids": [],
        "cache_dir": paths["design_cache_dir"],
        "updated_at": now_iso(),
    }


def _default_design_handoff(workspace_path: str, handoff_id: str = "current") -> dict[str, Any]:
    handoff_key = sanitize_identifier(handoff_id, "current")
    return {
        "schema_version": 1,
        "workspace_path": workspace_path,
        "handoff_id": handoff_key,
        "status": "not_started",
        "platform": None,
        "layout_system": [],
        "component_inventory": [],
        "motion_rules": [],
        "typography": {},
        "color_system": {},
        "accessibility_constraints": [],
        "copy_tone": [],
        "platform_deltas": [],
        "verification_hooks": [],
        "updated_at": now_iso(),
    }


def _default_task_record(
    workspace_path: str,
    task_id: str,
    title: str,
    objective: str,
    scope: list[str] | None = None,
    verification_target: str | None = None,
    verification_selectors: dict[str, Any] | None = None,
    verification_mode_default: str | None = None,
    branch_hint: str | None = None,
    linked_workstream_id: str | None = None,
    stage_id: str | None = None,
    external_issue: dict[str, Any] | None = None,
    codex_estimate_minutes: int | None = None,
) -> dict[str, Any]:
    selectors = verification_selectors or {}
    if verification_target and not selectors:
        selectors = {"explicit_targets": [verification_target]}
    return {
        "schema_version": 2,
        "workspace_path": workspace_path,
        "task_id": task_id,
        "title": title,
        "status": "planned",
        "branch_hint": branch_hint,
        "linked_workstream_id": linked_workstream_id,
        "stage_id": stage_id,
        "objective": objective,
        "scope": scope or [],
        "verification_target": verification_target,
        "verification_selectors": selectors,
        "verification_mode_default": verification_mode_default or "targeted",
        "codex_estimate_minutes": int(codex_estimate_minutes) if codex_estimate_minutes is not None else None,
        "external_issue": copy.deepcopy(external_issue) if external_issue else None,
        "time_tracking": {
            "schema_version": TASK_TIME_TRACKING_SCHEMA_VERSION,
            "active_session_started_at": None,
            "active_session_id": None,
            "local_total_minutes": 0,
            "entries": [],
            "updated_at": now_iso(),
        },
        "latest_commit": None,
        "docs_sync_required": True,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "closed_at": None,
    }


def _default_task_index() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "current_task_id": None,
        "items": [],
        "updated_at": now_iso(),
    }


def _default_workstream_record(
    detection: dict[str, Any],
    workstream_id: str,
    title: str,
    kind: str,
    branch_hint: str | None,
    scope_summary: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workspace_path": detection["workspace_path"],
        "workspace_label": detection["workspace_label"],
        "workstream_id": workstream_id,
        "title": title,
        "kind": kind,
        "status": "active",
        "visibility": "normal",
        "branch_hint": branch_hint,
        "scope_summary": scope_summary,
        "plan_status": "needs_user_confirmation",
        "current_stage": None,
        "current_slice": None,
        "last_completed_stage": None,
        "verification_selectors": [],
        "verification_policy": {
            "default_mode": "targeted",
            "full_suite_requires_explicit_request": True,
            "changed_file_heuristics": "advisory_only",
            "closeout_default_mode": "targeted",
        },
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "closed_at": None,
    }


def _normalize_workstream_record(
    detection: dict[str, Any],
    record: dict[str, Any],
    existing_register: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = copy.deepcopy(record or {})
    existing_register = existing_register or {}
    workstream_id = sanitize_identifier(normalized.get("workstream_id"), "workstream")
    title = normalized.get("title")
    normalized_title = normalize_command_phrase(title) if title else None
    if normalized_title in PLACEHOLDER_WORKSTREAM_TITLES or not title:
        title = workstream_id
    kind = normalized.get("kind") or existing_register.get("workstream_kind") or "feature"
    if kind == "default":
        kind = "feature"
    scope_summary = normalized.get("scope_summary")
    if _looks_placeholder_scope_summary(scope_summary):
        scope_summary = existing_register.get("scope_summary")
    branch_hint = normalized.get("branch_hint") or existing_register.get("branch_hint")
    if not branch_hint:
        branch_hint = f"{FALLBACK_WORKSTREAM_BRANCH_PREFIX}/{workstream_id}"
    verification_policy = copy.deepcopy(normalized.get("verification_policy") or existing_register.get("verification_policy") or {})
    verification_policy.setdefault("default_mode", "targeted")
    verification_policy.setdefault("full_suite_requires_explicit_request", True)
    verification_policy.setdefault("changed_file_heuristics", "advisory_only")
    verification_policy.setdefault("closeout_default_mode", "targeted")
    verification_selectors = copy.deepcopy(normalized.get("verification_selectors") or existing_register.get("verification_selectors") or [])
    normalized.update(
        {
            "schema_version": normalized.get("schema_version", 1),
            "workspace_path": detection["workspace_path"],
            "workspace_label": detection["workspace_label"],
            "workstream_id": workstream_id,
            "title": title,
            "kind": kind,
            "status": normalized.get("status") or existing_register.get("workstream_status") or "active",
            "visibility": normalized.get("visibility") or "normal",
            "branch_hint": branch_hint,
            "scope_summary": scope_summary,
            "plan_status": normalized.get("plan_status") or existing_register.get("plan_status") or "needs_user_confirmation",
            "verification_policy": verification_policy,
            "verification_selectors": verification_selectors,
            "updated_at": now_iso(),
        }
    )
    return normalized


def _default_workstream_index(current_workstream_id: str | None = None, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "current_workstream_id": current_workstream_id,
        "items": copy.deepcopy(items or []),
        "updated_at": now_iso(),
    }


def _default_audit_payload(workspace_path: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "audit_id": sanitize_identifier(f"audit-{int(time.time())}", "audit"),
        "workspace_path": workspace_path,
        "generated_at": now_iso(),
        "initialized": False,
        "gaps": [],
        "recommended_playbooks": [],
        "notes": [],
    }


def _default_upgrade_plan(workspace_path: str, audit_id: str | None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "plan_id": sanitize_identifier(f"upgrade-{int(time.time())}", "upgrade"),
        "workspace_path": workspace_path,
        "audit_id": audit_id,
        "generated_at": now_iso(),
        "status": "draft",
        "items": [],
        "created_workstream_id": None,
        "created_task_ids": [],
    }


def _default_verification_summary() -> dict[str, Any]:
    return {
        "status": "not_started",
        "run_id": None,
        "summary": None,
        "updated_at": now_iso(),
    }


def _starter_runs_root() -> Path:
    return state_root() / "starter-runs"


def _starter_run_path(run_id: str) -> Path:
    return _starter_runs_root() / run_id / "run.json"


def _starter_logs(run_id: str) -> tuple[Path, Path]:
    run_root = _starter_run_path(run_id).parent
    return run_root / "stdout.log", run_root / "stderr.log"


def _ensure_state_dirs(paths: dict[str, str]) -> None:
    for key in (
        "state_root",
        "workspace_root",
        "workstreams_root",
        "tasks_root",
        "audits_root",
        "upgrade_plans_root",
        "migrations_root",
        "integrations_root",
        "youtrack_root",
        "youtrack_connections_dir",
        "youtrack_secrets_dir",
        "youtrack_field_catalogs_dir",
        "youtrack_searches_dir",
        "youtrack_plans_dir",
        "youtrack_issues_dir",
        "starter_runs_root",
    ):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)


def _workstream_record_by_id(index: dict[str, Any], workstream_id: str) -> dict[str, Any]:
    for record in index.get("items", []):
        if record.get("workstream_id") == workstream_id:
            return record
    raise FileNotFoundError(f"Unknown workstream: {workstream_id}")


def _task_record_by_id(index: dict[str, Any], task_id: str) -> dict[str, Any]:
    for record in index.get("items", []):
        if record.get("task_id") == task_id:
            return record
    raise FileNotFoundError(f"Unknown task: {task_id}")


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _session_minutes_between(started_at: str | None, ended_at: str | None) -> int:
    started = _parse_iso_timestamp(started_at)
    ended = _parse_iso_timestamp(ended_at)
    if not started or not ended:
        return 0
    seconds = max((ended - started).total_seconds(), 0)
    if seconds <= 0:
        return 0
    return max(1, int((seconds + 59) // 60))


def _default_task_time_tracking() -> dict[str, Any]:
    return {
        "schema_version": TASK_TIME_TRACKING_SCHEMA_VERSION,
        "active_session_started_at": None,
        "active_session_id": None,
        "local_total_minutes": 0,
        "entries": [],
        "updated_at": now_iso(),
    }


def _normalize_task_time_tracking(tracking: dict[str, Any] | None) -> dict[str, Any]:
    normalized = copy.deepcopy(tracking or {})
    normalized["schema_version"] = normalized.get("schema_version", TASK_TIME_TRACKING_SCHEMA_VERSION)
    normalized["active_session_started_at"] = normalized.get("active_session_started_at")
    normalized["active_session_id"] = normalized.get("active_session_id")
    normalized["entries"] = copy.deepcopy(normalized.get("entries") or [])
    normalized["local_total_minutes"] = int(normalized.get("local_total_minutes") or 0)
    for entry in normalized["entries"]:
        if entry.get("minutes") is not None:
            entry["minutes"] = int(entry.get("minutes") or 0)
        entry["session_id"] = entry.get("session_id")
    if not normalized["local_total_minutes"] and normalized["entries"]:
        normalized["local_total_minutes"] = sum(int(entry.get("minutes") or 0) for entry in normalized["entries"])
    normalized["updated_at"] = normalized.get("updated_at") or now_iso()
    return normalized


def _normalize_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(payload or {})
    normalized["status"] = normalized.get("status") or "planned"
    normalized["stage_id"] = normalized.get("stage_id")
    normalized["codex_estimate_minutes"] = (
        int(normalized.get("codex_estimate_minutes"))
        if normalized.get("codex_estimate_minutes") not in {None, ""}
        else None
    )
    normalized["external_issue"] = copy.deepcopy(normalized.get("external_issue")) if normalized.get("external_issue") else None
    normalized["time_tracking"] = _normalize_task_time_tracking(normalized.get("time_tracking"))
    normalized["latest_commit"] = copy.deepcopy(normalized.get("latest_commit")) if normalized.get("latest_commit") else None
    return normalized


def _activate_task_payload(task_payload: dict[str, Any], *, started_at: str | None = None) -> dict[str, Any]:
    payload = _normalize_task_payload(task_payload)
    payload["status"] = "active"
    tracking = payload["time_tracking"]
    tracking["active_session_started_at"] = tracking.get("active_session_started_at") or started_at or now_iso()
    tracking["active_session_id"] = tracking.get("active_session_id") or uuid.uuid4().hex
    tracking["updated_at"] = now_iso()
    payload["updated_at"] = now_iso()
    return payload


def _pause_task_payload(task_payload: dict[str, Any], *, ended_at: str | None = None, next_status: str | None = None) -> dict[str, Any]:
    payload = _normalize_task_payload(task_payload)
    tracking = payload["time_tracking"]
    stopped_at = ended_at or now_iso()
    started_at = tracking.get("active_session_started_at")
    if started_at:
        entry = {
            "started_at": started_at,
            "ended_at": stopped_at,
            "minutes": _session_minutes_between(started_at, stopped_at),
            "session_id": tracking.get("active_session_id") or uuid.uuid4().hex,
            "updated_at": stopped_at,
        }
        tracking["entries"].append(entry)
        tracking["active_session_started_at"] = None
        tracking["active_session_id"] = None
        tracking["local_total_minutes"] = sum(int(item.get("minutes") or 0) for item in tracking["entries"])
    else:
        tracking["active_session_id"] = None
    tracking["updated_at"] = stopped_at
    payload["status"] = next_status or ("planned" if payload.get("status") == "active" else payload.get("status") or "planned")
    payload["updated_at"] = stopped_at
    return payload


def _persist_task_record(workspace: str | Path, task_payload: dict[str, Any]) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    payload = _normalize_task_payload(task_payload)
    task_id = sanitize_identifier(payload.get("task_id"), "")
    if not task_id:
        raise ValueError("Task payload is missing a task_id.")
    payload["task_id"] = task_id
    payload["updated_at"] = now_iso()
    task_paths = _task_paths(workspace, task_id)
    _write_json(Path(task_paths["current_task_record"]), payload)
    index = _load_tasks_index(paths)
    replaced = False
    for index_item, item in enumerate(index.get("items", [])):
        if item.get("task_id") == task_id:
            index["items"][index_item] = copy.deepcopy(payload)
            replaced = True
            break
    if not replaced:
        index.setdefault("items", []).append(copy.deepcopy(payload))
    _save_tasks_index(paths, index)
    return payload


def _sync_linked_issue_ledger(workspace: str | Path, task_payload: dict[str, Any]) -> None:
    external_issue = (task_payload or {}).get("external_issue") or {}
    connection_id = external_issue.get("connection_id")
    issue_id = external_issue.get("issue_id")
    if not connection_id or not issue_id:
        return
    try:
        from agentiux_dev_youtrack import recompute_issue_ledger

        recompute_issue_ledger(workspace, connection_id=connection_id, issue_id=issue_id)
    except Exception:
        return


def _deactivate_current_task(workspace: str | Path, *, next_status: str = "planned") -> dict[str, Any] | None:
    active_task = current_task(workspace)
    if not active_task:
        return None
    updated = _pause_task_payload(active_task, next_status=next_status)
    persisted = _persist_task_record(workspace, updated)
    _sync_linked_issue_ledger(workspace, persisted)
    return persisted


def _load_workstreams_index(paths: dict[str, str], *, strict: bool = True) -> dict[str, Any]:
    payload = _load_json(
        Path(paths["workstreams_index"]),
        default=_default_workstream_index(),
        strict=strict,
        purpose="workstreams index",
    ) or _default_workstream_index()
    payload["current_workstream_id"] = _sanitize_nullable_identifier(payload.get("current_workstream_id"))
    payload["items"] = copy.deepcopy(payload.get("items") or [])
    return payload


def _load_tasks_index(paths: dict[str, str], *, strict: bool = True) -> dict[str, Any]:
    payload = _load_json(
        Path(paths["tasks_index"]),
        default=_default_task_index(),
        strict=strict,
        purpose="tasks index",
    ) or _default_task_index()
    payload["items"] = [_normalize_task_payload(item) for item in payload.get("items", [])]
    return payload


def _save_workstreams_index(paths: dict[str, str], index: dict[str, Any]) -> dict[str, Any]:
    index["current_workstream_id"] = _sanitize_nullable_identifier(index.get("current_workstream_id"))
    index["updated_at"] = now_iso()
    _write_json(Path(paths["workstreams_index"]), index)
    return index


def _save_tasks_index(paths: dict[str, str], index: dict[str, Any]) -> dict[str, Any]:
    index["updated_at"] = now_iso()
    _write_json(Path(paths["tasks_index"]), index)
    return index


def _write_registry_entry(workspace: str | Path, workspace_state: dict[str, Any]) -> None:
    detection = detect_workspace(workspace)
    paths = detection["paths"]
    registry = _load_registry()
    key = _workspace_key(detection)
    registry["plugin"] = plugin_info()
    registry["workspaces"][key] = {
        "workspace_path": detection["workspace_path"],
        "workspace_label": workspace_state["workspace_label"],
        "workspace_slug": detection["workspace_slug"],
        "workspace_hash": detection["workspace_hash"],
        "workspace_root": paths["workspace_root"],
        "workspace_state": paths["workspace_state"],
        "stage_register": paths["stage_register"],
        "active_brief": paths["active_brief"],
        "current_workstream_id": workspace_state.get("current_workstream_id"),
        "current_task_id": workspace_state.get("current_task_id"),
        "workstreams_index": paths["workstreams_index"],
        "tasks_index": paths["tasks_index"],
        "current_audit": paths["current_audit"],
        "current_upgrade_plan": paths["current_upgrade_plan"],
        "starter_runs_root": paths["starter_runs_root"],
        "workspace_mode": workspace_state.get("workspace_mode"),
        "initialized_at": workspace_state["initialized_at"],
        "updated_at": now_iso(),
    }
    registry["updated_at"] = now_iso()
    _write_json(_registry_path(), registry)


def preview_reset_workspace_state(workspace: str | Path) -> dict[str, Any]:
    detection = detect_workspace(workspace)
    paths = detection["paths"]
    registry = _load_registry()
    key = _workspace_key(detection)
    workspace_root = Path(paths["workspace_root"])
    return {
        "workspace_path": detection["workspace_path"],
        "workspace_label": detection["workspace_label"],
        "paths": paths,
        "registry_key": key,
        "workspace_root_exists": workspace_root.exists(),
        "registry_entry_exists": key in registry.get("workspaces", {}),
        "will_remove": [
            str(workspace_root),
            f"{paths['registry']}::{key}",
        ],
        "must_confirm_before_write": True,
    }


def reset_workspace_state(workspace: str | Path) -> dict[str, Any]:
    preview = preview_reset_workspace_state(workspace)
    workspace_root = Path(preview["paths"]["workspace_root"])
    removed_workspace_root = workspace_root.exists()
    if removed_workspace_root:
        shutil.rmtree(workspace_root)
    registry = _load_registry()
    removed_registry_entry = registry.get("workspaces", {}).pop(preview["registry_key"], None) is not None
    registry["updated_at"] = now_iso()
    _write_json(_registry_path(), registry)
    return {
        **preview,
        "removed_workspace_root": removed_workspace_root,
        "removed_registry_entry": removed_registry_entry,
    }


def preview_workspace_init(workspace: str | Path) -> dict[str, Any]:
    detection = detect_workspace(workspace)
    paths = detection["paths"]
    workspace_root_exists = Path(paths["workspace_root"]).exists()
    workspace_state_exists = Path(paths["workspace_state"]).exists()
    return {
        "workspace_path": detection["workspace_path"],
        "workspace_label": detection["workspace_label"],
        "host_os": detection["host_os"],
        "detected_stacks": detection["detected_stacks"],
        "selected_profiles": detection["selected_profiles"],
        "plugin_platform": detection["plugin_platform"],
        "local_dev_policy": detection["local_dev_policy"],
        "planning_policy": detection["planning_policy"],
        "support_warnings": detection["support_warnings"],
        "paths": paths,
        "already_initialized": workspace_root_exists or workspace_state_exists,
        "will_create": [
            paths["registry"],
            paths["workspace_root"],
            paths["workspace_state"],
            paths["workstreams_root"],
            paths["workstreams_index"],
            paths["tasks_root"],
            paths["tasks_index"],
            paths["audits_root"],
            paths["upgrade_plans_root"],
            paths["migrations_root"],
        ],
        "must_confirm_before_write": True,
    }


def build_stage_register(
    detection: dict[str, Any],
    workstream_record: dict[str, Any],
    *,
    planner_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_record = _normalize_workstream_record(detection, workstream_record)
    register = _base_stage_register(detection, normalized_record, planner_context=planner_context)
    return _decorate_stage_register(detection["workspace_path"], register, normalized_record["workstream_id"])


def _remove_stage_docs(paths: dict[str, str]) -> None:
    stages_dir = Path(paths["current_workstream_stages_dir"])
    if not stages_dir.exists():
        return
    for path in stages_dir.glob("*.md"):
        path.unlink()
    try:
        stages_dir.rmdir()
    except OSError:
        pass


def _prune_workstream_artifacts(workspace: str | Path, keep_workstream_ids: list[str]) -> None:
    paths = workspace_paths(workspace)
    workstreams_root = Path(paths["workstreams_root"])
    keep = set(keep_workstream_ids)
    if workstreams_root.exists():
        for candidate in workstreams_root.iterdir():
            if not candidate.is_dir():
                continue
            if candidate.name not in keep:
                shutil.rmtree(candidate)
    for workstream_id in keep:
        _remove_stage_docs(_workstream_paths(workspace, workstream_id))


def _load_workstream_register(workspace: str | Path, workstream_id: str) -> dict[str, Any]:
    paths = _workstream_paths(workspace, workstream_id)
    payload = _load_json(
        Path(paths["current_workstream_stage_register"]),
        default={},
        strict=True,
        purpose=f"workstream stage register `{workstream_id}`",
    ) or {}
    return _canonicalize_register_state(_strip_mirror_fields(payload))


def _load_workstream_brief(workspace: str | Path, workstream_id: str) -> str:
    paths = _workstream_paths(workspace, workstream_id)
    return _canonical_brief_markdown(_read_text(Path(paths["current_workstream_active_brief"]), default=BRIEF_PLACEHOLDER))


def _sanitize_workstream_documents(workspace: str | Path, workstream_id: str) -> tuple[dict[str, Any], str]:
    paths = _workstream_paths(workspace, workstream_id)
    register_path = Path(paths["current_workstream_stage_register"])
    brief_path = Path(paths["current_workstream_active_brief"])
    register = _canonicalize_register_state(
        _strip_mirror_fields(
        _load_json(
            register_path,
            default={},
            strict=True,
            purpose=f"workstream stage register `{workstream_id}`",
        )
        or {}
        )
    )
    brief_markdown = _canonical_brief_markdown(_read_text(brief_path, default=BRIEF_PLACEHOLDER))
    _write_json(register_path, register)
    _write_text(brief_path, brief_markdown)
    _remove_stage_docs(paths)
    return register, brief_markdown


def _write_default_workstream_documents(workspace: str | Path, workstream_record: dict[str, Any], register: dict[str, Any]) -> None:
    paths = _workstream_paths(workspace, workstream_record["workstream_id"])
    for key in (
        "current_workstream_root",
        "artifacts_dir",
        "design_dir",
        "design_boards_dir",
        "design_handoffs_dir",
        "design_cache_dir",
        "verification_dir",
        "verification_runs_dir",
        "verification_baselines_dir",
    ):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    canonical_register = _strip_mirror_fields(register)
    canonical_brief = _canonical_brief_markdown(BRIEF_PLACEHOLDER)
    _write_json(Path(paths["current_workstream_stage_register"]), canonical_register)
    _write_text(Path(paths["current_workstream_active_brief"]), canonical_brief)
    brief = _default_design_brief(paths["workspace_path"], paths)
    board = _default_reference_board(paths["workspace_path"], paths)
    handoff = _default_design_handoff(paths["workspace_path"])
    _write_json(Path(paths["design_brief"]), brief)
    _write_json(Path(paths["reference_board"]), board)
    _write_json(Path(paths["design_boards_dir"]) / "current.json", board)
    _write_json(Path(paths["design_handoff"]), handoff)
    _write_json(Path(paths["design_handoffs_dir"]) / "current.json", handoff)
    _remove_stage_docs(paths)


def _remove_root_mirrors(workspace: str | Path) -> None:
    root_paths = workspace_paths(workspace)
    for candidate in (root_paths["stage_register"], root_paths["active_brief"]):
        path = Path(candidate)
        if path.exists():
            path.unlink()


def _mirror_current_workstream(workspace: str | Path, workstream_id: str | None) -> None:
    if not workstream_id:
        _remove_root_mirrors(workspace)
        return
    root_paths = workspace_paths(workspace)
    workstream_paths = _workstream_paths(workspace, workstream_id)
    stage_register_path = Path(workstream_paths["current_workstream_stage_register"])
    active_brief_path = Path(workstream_paths["current_workstream_active_brief"])
    if not stage_register_path.exists() and not active_brief_path.exists():
        _remove_root_mirrors(workspace)
        return
    if stage_register_path.exists():
        payload, canonical_brief = _sanitize_workstream_documents(workspace, workstream_id)
        payload["is_mirror"] = True
        payload["mirror_of_workstream_id"] = workstream_id
        payload["read_only_derived"] = True
        _write_json(Path(root_paths["stage_register"]), payload)
    elif active_brief_path.exists():
        canonical_brief = _canonical_brief_markdown(_read_text(active_brief_path, default=BRIEF_PLACEHOLDER))
    else:
        canonical_brief = BRIEF_PLACEHOLDER.strip()
    if active_brief_path.exists():
        mirrored = textwrap.dedent(
            f"""\
            <!-- derived-mirror: true -->
            <!-- mirror-of-workstream: {workstream_id} -->
            <!-- read-only-derived: true -->

            {canonical_brief}
            """
        ).strip() + "\n"
        _write_text(Path(root_paths["active_brief"]), mirrored)
    else:
        active_brief_root = Path(root_paths["active_brief"])
        if active_brief_root.exists():
            active_brief_root.unlink()


def _current_workstream_id(paths: dict[str, str]) -> str | None:
    state = _load_json(Path(paths["workspace_state"]), default={}) or {}
    return _sanitize_nullable_identifier(state.get("current_workstream_id"))


def _current_task_id(paths: dict[str, str]) -> str | None:
    state = _load_json(Path(paths["workspace_state"]), default={}) or {}
    value = state.get("current_task_id")
    return sanitize_identifier(value, "") if value else None


def _workstream_ids(paths: dict[str, str]) -> list[str]:
    index = _load_workstreams_index(paths)
    ids = [_sanitize_nullable_identifier(item.get("workstream_id")) for item in index.get("items", [])]
    return [workstream_id for workstream_id in dict.fromkeys(ids) if workstream_id]


def _recent_audit_files(paths: dict[str, str]) -> list[Path]:
    audits_dir = Path(paths["audits_root"])
    return sorted((candidate for candidate in audits_dir.glob("audit-*.json") if candidate.is_file()), reverse=True)


def _recent_upgrade_files(paths: dict[str, str]) -> list[Path]:
    upgrade_dir = Path(paths["upgrade_plans_root"])
    return sorted((candidate for candidate in upgrade_dir.glob("upgrade-*.json") if candidate.is_file()), reverse=True)


def _migrate_legacy_workspace(workspace: str | Path) -> None:
    paths = workspace_paths(workspace)
    workspace_state_path = Path(paths["workspace_state"])
    legacy_stage_register = Path(paths["stage_register"])
    if not workspace_state_path.exists() and not legacy_stage_register.exists():
        return

    state = _normalize_workspace_state_payload(_load_json(workspace_state_path, default={}) or {})
    if state.get("schema_version", 0) >= STATE_SCHEMA_VERSION and Path(paths["workstreams_index"]).exists():
        for workstream_id in _workstream_ids(paths):
            _remove_stage_docs(_workstream_paths(workspace, workstream_id))
        _mirror_current_workstream(workspace, state.get("current_workstream_id"))
        return

    detection = detect_workspace(workspace)
    _ensure_state_dirs(paths)
    for key in ("tasks_root", "audits_root", "upgrade_plans_root", "migrations_root"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)

    legacy_register = _strip_mirror_fields(_load_json(legacy_stage_register, default={}) or {}) if legacy_stage_register.exists() else {}
    legacy_brief = Path(paths["active_brief"])
    raw_workstream_id = _sanitize_nullable_identifier(state.get("current_workstream_id")) or _sanitize_nullable_identifier(legacy_register.get("workstream_id"))
    workstream_title = legacy_register.get("workstream_title") or state.get("workspace_label") or detection["workspace_label"]
    placeholder_default = (
        raw_workstream_id in {None, DEFAULT_WORKSTREAM_ID}
        and normalize_command_phrase(workstream_title) in PLACEHOLDER_WORKSTREAM_TITLES
        and not legacy_register.get("last_completed_stage")
        and not any(stage.get("status") == "completed" for stage in legacy_register.get("stages", []))
    )

    migrated_workstreams: list[dict[str, Any]] = []
    source_workstream_schema_versions: dict[str, Any] = {}
    current_workstream_id: str | None = None
    if legacy_register and not placeholder_default:
        migrated_workstream_id = raw_workstream_id if raw_workstream_id and raw_workstream_id != DEFAULT_WORKSTREAM_ID else sanitize_identifier(workstream_title, "legacy-workstream")
        workstream_record = _default_workstream_record(
            detection,
            migrated_workstream_id,
            title=legacy_register.get("workstream_title") or humanize_identifier(migrated_workstream_id, "Legacy Workstream"),
            kind=legacy_register.get("workstream_kind") or "feature",
            branch_hint=legacy_register.get("branch_hint"),
            scope_summary=legacy_register.get("scope_summary"),
        )
        migrated_register = build_stage_register(detection, workstream_record)
        migrated_register.update({key: value for key, value in legacy_register.items() if key != "schema_version"})
        migrated_register["schema_version"] = STATE_SCHEMA_VERSION
        migrated_register["workstream_id"] = migrated_workstream_id
        migrated_register["workstream_title"] = workstream_record["title"]
        migrated_register["workstream_kind"] = workstream_record["kind"]
        migrated_register["workstream_status"] = legacy_register.get("workstream_status") or workstream_record["status"]
        _write_default_workstream_documents(workspace, workstream_record, migrated_register)
        if legacy_brief.exists():
            _write_text(
                Path(_workstream_paths(workspace, migrated_workstream_id)["current_workstream_active_brief"]),
                _canonical_brief_markdown(legacy_brief.read_text()),
            )
        decorated_register = _canonicalize_register_state(_strip_mirror_fields(migrated_register))
        workstream_record.update(
            {
                "current_stage": decorated_register.get("current_stage"),
                "current_slice": decorated_register.get("current_slice"),
                "last_completed_stage": decorated_register.get("last_completed_stage"),
                "status": decorated_register.get("workstream_status") or workstream_record["status"],
            }
        )
        migrated_workstreams.append(workstream_record)
        source_workstream_schema_versions[migrated_workstream_id] = legacy_register.get("schema_version")
        current_workstream_id = migrated_workstream_id

    _write_json(Path(paths["workstreams_index"]), _default_workstream_index(current_workstream_id, migrated_workstreams))
    if not Path(paths["tasks_index"]).exists():
        _write_json(Path(paths["tasks_index"]), _default_task_index())

    workspace_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "workspace_path": detection["workspace_path"],
        "workspace_label": state.get("workspace_label") or detection["workspace_label"],
        "workspace_slug": detection["workspace_slug"],
        "workspace_hash": detection["workspace_hash"],
        "host_os": detection["host_os"],
        "host_capabilities": detection["host_capabilities"],
        "toolchain_capabilities": detection["toolchain_capabilities"],
        "selected_profiles": detection["selected_profiles"],
        "local_dev_policy": detection["local_dev_policy"],
        "init_status": "initialized",
        "initialized_at": state.get("initialized_at") or now_iso(),
        "current_workstream_id": current_workstream_id,
        "current_task_id": state.get("current_task_id"),
        "workspace_mode": "task" if state.get("current_task_id") else ("workstream" if current_workstream_id else "workspace"),
        "state_repair_status": {
            "last_checked_at": now_iso(),
            "last_repaired_at": now_iso(),
            "status": "repaired",
            "source_schema_version": state.get("schema_version", 0) or 0,
            "source_workstream_schema_versions": source_workstream_schema_versions,
            "target_schema_version": STATE_SCHEMA_VERSION,
            "last_successful_schema_version": STATE_SCHEMA_VERSION,
            "applied_actions": [{"action": "migrate_legacy_workspace"}],
        },
    }
    _write_json(workspace_state_path, _workspace_state_storage_payload(_decorate_workspace_state(workspace, workspace_state)))
    migration_note = {
        "schema_version": 1,
        "migrated_at": now_iso(),
        "workspace_path": detection["workspace_path"],
        "from_schema_version": state.get("schema_version", 0) or 0,
        "to_schema_version": STATE_SCHEMA_VERSION,
        "migrated_workstream_id": current_workstream_id,
    }
    _write_json(Path(paths["migrations_root"]) / f"migration-{int(time.time())}.json", migration_note)
    _mirror_current_workstream(workspace, current_workstream_id)
    legacy_default_root = Path(paths["workstreams_root"]) / DEFAULT_WORKSTREAM_ID
    if current_workstream_id != DEFAULT_WORKSTREAM_ID and legacy_default_root.exists():
        shutil.rmtree(legacy_default_root)


def _ensure_workspace_initialized(workspace: str | Path) -> dict[str, str]:
    paths = workspace_paths(workspace)
    if not Path(paths["workspace_state"]).exists() and not Path(paths["stage_register"]).exists():
        raise FileNotFoundError(f"Workspace is not initialized in AgentiUX Dev state: {paths['workspace_root']}")
    _migrate_legacy_workspace(workspace)
    paths = workspace_paths(workspace)
    if not Path(paths["workspace_state"]).exists():
        raise FileNotFoundError(f"Workspace is not initialized in AgentiUX Dev state: {paths['workspace_root']}")
    if not Path(paths["workstreams_index"]).exists():
        _write_json(Path(paths["workstreams_index"]), _default_workstream_index())
    if not Path(paths["tasks_index"]).exists():
        _write_json(Path(paths["tasks_index"]), _default_task_index())
    return paths


def _collect_verification_runs(paths: dict[str, str]) -> list[dict[str, Any]]:
    runs = []
    for run_path in Path(paths["workstreams_root"]).glob("*/verification/runs/*/run.json"):
        payload = _load_json(run_path, default={}, strict=False) or {}
        if payload:
            runs.append(payload)
    return runs


def _workspace_counts(paths: dict[str, str]) -> dict[str, Any]:
    workstreams_index = _load_workstreams_index(paths)
    tasks_index = _load_tasks_index(paths)
    runs = _collect_verification_runs(paths)
    total_stages = 0
    completed_stages = 0
    blocked_stages = 0
    artifact_files = 0
    reference_boards = 0
    design_handoffs = 0
    cache_files = 0

    for workstream_id in _workstream_ids(paths):
        workstream_paths = _workstream_paths(paths["workspace_path"], workstream_id)
        register = _strip_mirror_fields(
            _load_json(
                Path(workstream_paths["current_workstream_stage_register"]),
                default={},
                strict=True,
                purpose=f"workstream stage register `{workstream_id}`",
            )
            or {}
        )
        if register:
            stages = register.get("stages", [])
            total_stages += len(stages)
            completed_stages += sum(1 for stage in stages if stage.get("status") == "completed")
            blocked_stages += sum(1 for stage in stages if stage.get("status") == "blocked")
        artifact_files += _count_files(Path(workstream_paths["artifacts_dir"]))
        reference_boards += len(_json_files(Path(workstream_paths["design_boards_dir"])))
        design_handoffs += len(_json_files(Path(workstream_paths["design_handoffs_dir"])))
        cache_files += _count_files(Path(workstream_paths["design_cache_dir"]))

    active_runs = [run for run in runs if run.get("status") in {"queued", "running"}]
    failed_runs = [run for run in runs if run.get("status") == "failed"]
    passed_runs = [run for run in runs if run.get("status") == "passed"]

    visible_workstreams = [item for item in workstreams_index.get("items", []) if item.get("visibility") != "system-hidden"]
    return {
        "workstreams": len(visible_workstreams),
        "hidden_workstreams": len(workstreams_index.get("items", [])) - len(visible_workstreams),
        "tasks": len(tasks_index.get("items", [])),
        "active_tasks": sum(1 for item in tasks_index.get("items", []) if item.get("status") == "active"),
        "total_stages": total_stages,
        "completed_stages": completed_stages,
        "blocked_stages": blocked_stages,
        "pending_stages": max(total_stages - completed_stages, 0),
        "artifact_files": artifact_files,
        "reference_boards": reference_boards,
        "design_handoffs": design_handoffs,
        "cache_files": cache_files,
        "verification_runs": len(runs),
        "active_verification_runs": len(active_runs),
        "failed_verification_runs": len(failed_runs),
        "passed_verification_runs": len(passed_runs),
        "starter_runs": len(list(_starter_runs_root().glob("*/run.json"))),
        "audit_reports": len(_recent_audit_files(paths)),
    }


def _decorate_stage_register(workspace: str | Path, register: dict[str, Any], workstream_id: str) -> dict[str, Any]:
    register = _strip_mirror_fields(register)
    paths = _workstream_paths(workspace, workstream_id)
    register["storage_format"] = STAGE_REGISTER_STORAGE
    register["command_surface"] = {
        "canonical": CANONICAL_COMMAND_SURFACE,
        "localized_aliases_runtime_only": True,
    }
    register["language_policy"] = language_policy()
    register["template_policy"] = register.get("template_policy") or default_planning_policy()
    register["design_state"] = {
        "brief_path": paths["design_brief"],
        "current_board_path": paths["reference_board"],
        "current_handoff_path": paths["design_handoff"],
        "boards_dir": paths["design_boards_dir"],
        "handoffs_dir": paths["design_handoffs_dir"],
        "cache_dir": paths["design_cache_dir"],
    }
    register["verification_state"] = {
        "recipes_path": paths["verification_recipes"],
        "runs_dir": paths["verification_runs_dir"],
        "baselines_dir": paths["verification_baselines_dir"],
    }
    register["dashboard_counts"] = _workspace_counts(workspace_paths(workspace))
    register["updated_at"] = now_iso()
    return register


def _decorate_workspace_state(workspace: str | Path, state: dict[str, Any]) -> dict[str, Any]:
    paths = workspace_paths(workspace)
    decorated = _normalize_workspace_state_payload(state)
    decorated["workspace_path"] = decorated.get("workspace_path") or paths["workspace_path"]
    decorated["workspace_label"] = decorated.get("workspace_label") or Path(paths["workspace_path"]).name
    decorated["workspace_slug"] = decorated.get("workspace_slug") or paths["workspace_slug"]
    decorated["workspace_hash"] = decorated.get("workspace_hash") or paths["workspace_hash"]
    decorated["state_repair_status"] = decorated.get("state_repair_status") or {
        "last_checked_at": now_iso(),
        "last_repaired_at": None,
        "status": "not_repaired",
        "source_schema_version": decorated.get("schema_version", 0) or 0,
        "source_workstream_schema_versions": {},
        "target_schema_version": STATE_SCHEMA_VERSION,
        "last_successful_schema_version": None,
        "applied_actions": [],
    }
    decorated["state_repair_status"]["source_schema_version"] = decorated["state_repair_status"].get("source_schema_version", decorated.get("schema_version", 0) or 0)
    decorated["state_repair_status"]["source_workstream_schema_versions"] = decorated["state_repair_status"].get("source_workstream_schema_versions") or {}
    decorated["state_repair_status"]["target_schema_version"] = decorated["state_repair_status"].get("target_schema_version", STATE_SCHEMA_VERSION)
    decorated["state_repair_status"]["last_successful_schema_version"] = decorated["state_repair_status"].get("last_successful_schema_version")
    decorated["state_repair_status"]["applied_actions"] = decorated["state_repair_status"].get("applied_actions") or []
    decorated["updated_at"] = now_iso()
    return decorated


def _persist_workspace_state(workspace: str | Path, workspace_state: dict[str, Any]) -> dict[str, Any]:
    decorated = _decorate_workspace_state(workspace, workspace_state)
    _write_json(Path(workspace_paths(workspace)["workspace_state"]), _workspace_state_storage_payload(decorated))
    _write_registry_entry(workspace, decorated)
    return decorated


def init_workspace(workspace: str | Path, force: bool = False) -> dict[str, Any]:
    detection = detect_workspace(workspace)
    preview = preview_workspace_init(workspace)
    paths = detection["paths"]
    workspace_root = Path(paths["workspace_root"])
    if preview["already_initialized"]:
        if force:
            raise ValueError(f"Workspace already initialized: {workspace_root}. Use reset workspace state first.")
        raise ValueError(f"Workspace already initialized: {workspace_root}")

    _ensure_state_dirs(paths)
    for key in ("tasks_root", "audits_root", "upgrade_plans_root", "migrations_root"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    _save_workstreams_index(paths, _default_workstream_index())
    _save_tasks_index(paths, _default_task_index())

    workspace_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "workspace_path": detection["workspace_path"],
        "workspace_label": detection["workspace_label"],
        "workspace_slug": detection["workspace_slug"],
        "workspace_hash": detection["workspace_hash"],
        "host_os": detection["host_os"],
        "host_capabilities": detection["host_capabilities"],
        "toolchain_capabilities": detection["toolchain_capabilities"],
        "detected_stacks": detection["detected_stacks"],
        "selected_profiles": detection["selected_profiles"],
        "plugin_platform": detection["plugin_platform"],
        "local_dev_policy": detection["local_dev_policy"],
        "planning_policy": detection["planning_policy"],
        "support_warnings": detection["support_warnings"],
        "init_status": "initialized",
        "initialized_at": now_iso(),
        "current_workstream_id": None,
        "current_task_id": None,
        "workspace_mode": "workspace",
        "state_repair_status": {
            "last_checked_at": now_iso(),
            "last_repaired_at": None,
            "status": "clean",
            "source_schema_version": STATE_SCHEMA_VERSION,
            "source_workstream_schema_versions": {},
            "target_schema_version": STATE_SCHEMA_VERSION,
            "last_successful_schema_version": STATE_SCHEMA_VERSION,
            "applied_actions": [],
        },
    }
    persisted_state = _persist_workspace_state(workspace, workspace_state)
    _remove_root_mirrors(workspace)
    return {
        "workspace_state": persisted_state,
        "workstreams": list_workstreams(workspace),
        "tasks": list_tasks(workspace),
        "paths": workspace_paths(workspace),
    }


def migrate_workspace_state(workspace: str | Path) -> dict[str, Any]:
    _migrate_legacy_workspace(workspace)
    return repair_workspace_state(workspace)


def _merge_replanned_register(old_register: dict[str, Any], new_register: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(new_register)
    completed_prefix: list[dict[str, Any]] = []
    old_stages = old_register.get("stages", [])
    for stage in old_stages:
        if stage.get("status") != "completed":
            break
        completed_prefix.append(copy.deepcopy(stage))
    completed_ids = {stage["id"] for stage in completed_prefix}
    old_remaining_map = {
        stage["id"]: stage
        for stage in old_stages
        if stage.get("id") not in completed_ids
    }
    merged_stages: list[dict[str, Any]] = completed_prefix[:]
    for stage in merged.get("stages", []):
        if stage["id"] in completed_ids:
            continue
        previous_stage = old_remaining_map.get(stage["id"])
        stage_copy = copy.deepcopy(stage)
        if previous_stage:
            stage_copy["status"] = previous_stage.get("status", stage_copy["status"])
            stage_copy["completed_at"] = previous_stage.get("completed_at")
        merged_stages.append(stage_copy)
    merged["stages"] = merged_stages
    merged_stage_map = {stage["id"]: stage for stage in merged_stages}
    current_stage = old_register.get("current_stage")
    current_stage_changed = current_stage not in merged_stage_map or merged_stage_map[current_stage].get("status") == "completed"
    if not current_stage_changed and current_stage in merged_stage_map:
        merged["current_stage"] = current_stage
        merged["stage_status"] = old_register.get("stage_status", merged.get("stage_status"))
        stage_slices = merged_stage_map[current_stage].get("canonical_execution_slices", [])
        current_slice = old_register.get("current_slice")
        merged["current_slice"] = current_slice if current_slice in stage_slices else (stage_slices[0] if stage_slices else None)
        merged["remaining_slices"] = [slice_id for slice_id in stage_slices if slice_id != merged.get("current_slice")]
        merged["slice_status"] = old_register.get("slice_status", merged.get("slice_status"))
        merged["active_goal"] = old_register.get("active_goal", merged.get("active_goal"))
        merged["next_task"] = old_register.get("next_task", merged.get("next_task"))
    else:
        replacement_stage = next((stage for stage in merged_stages if stage.get("status") != "completed"), None)
        if replacement_stage is None and merged_stages:
            replacement_stage = merged_stages[-1]
        if replacement_stage is not None:
            merged["current_stage"] = replacement_stage["id"]
            stage_slices = replacement_stage.get("canonical_execution_slices", [])
            merged["current_slice"] = stage_slices[0] if stage_slices else None
            merged["remaining_slices"] = stage_slices[1:] if stage_slices else []
            merged["stage_status"] = replacement_stage.get("status", "planned")
            merged["slice_status"] = "completed" if replacement_stage.get("status") == "completed" else "planned"
            merged["active_goal"] = replacement_stage.get("objective")
            merged["next_task"] = "Review the current stage in stage-register.yaml and the workspace sources of truth before implementation starts."
        else:
            merged["current_stage"] = None
            merged["current_slice"] = None
            merged["remaining_slices"] = []
            merged["stage_status"] = None
            merged["slice_status"] = None
            merged["active_goal"] = None
            merged["next_task"] = None
    merged["last_completed_stage"] = next((stage["id"] for stage in reversed(merged_stages) if stage.get("status") == "completed"), old_register.get("last_completed_stage"))
    merged["blockers"] = old_register.get("blockers", [])
    merged["required_doc_updates"] = old_register.get("required_doc_updates", merged.get("required_doc_updates", []))
    merged["verification_requirements"] = old_register.get("verification_requirements", merged.get("verification_requirements", []))
    merged["slice_verification_summary"] = old_register.get("slice_verification_summary")
    merged["last_verification_summary"] = old_register.get("last_verification_summary")
    merged["open_decisions"] = old_register.get("open_decisions", merged.get("open_decisions", []))
    merged["verification_selectors"] = old_register.get("verification_selectors", merged.get("verification_selectors", []))
    merged["verification_policy"] = old_register.get("verification_policy", merged.get("verification_policy", {}))
    return merged


def _workstream_is_placeholder(record: dict[str, Any], register: dict[str, Any]) -> bool:
    if record.get("workstream_id") != DEFAULT_WORKSTREAM_ID:
        return False
    if record.get("branch_hint"):
        return False
    if any(stage.get("status") != "planned" for stage in register.get("stages", [])):
        return False
    if register.get("last_completed_stage"):
        return False
    return True


def _repair_plan_for_workstream(workspace: str | Path, detection: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    workstream_id = sanitize_identifier(record.get("workstream_id"), "workstream")
    register_path = Path(_workstream_paths(workspace, workstream_id)["current_workstream_stage_register"])
    existing_register = _load_workstream_register(workspace, workstream_id) if register_path.exists() else {}
    raw_brief = _read_text(Path(_workstream_paths(workspace, workstream_id)["current_workstream_active_brief"]), default=BRIEF_PLACEHOLDER)
    canonical_brief = _canonical_brief_markdown(raw_brief)
    normalized_record = _normalize_workstream_record(detection, record, existing_register)
    planner_context = existing_register.get("planner_context") or _planner_context(detection, normalized_record)
    repaired_register = _base_stage_register(detection, normalized_record, planner_context=planner_context)
    repaired_register.update(copy.deepcopy(existing_register))
    repaired_register.update(
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "workspace_path": detection["workspace_path"],
            "workspace_label": detection["workspace_label"],
            "workspace_slug": detection["workspace_slug"],
            "host_os": detection["host_os"],
            "host_platform": detection["host_os"],
            "host_capabilities": detection["host_capabilities"],
            "toolchain_capabilities": detection["toolchain_capabilities"],
            "support_warnings": detection["support_warnings"],
            "selected_profiles": detection["selected_profiles"],
            "detected_stacks": detection["detected_stacks"],
            "workstream_id": normalized_record["workstream_id"],
            "workstream_title": normalized_record["title"],
            "workstream_kind": normalized_record["kind"],
            "workstream_status": normalized_record["status"],
            "branch_hint": normalized_record.get("branch_hint"),
            "scope_summary": normalized_record.get("scope_summary"),
            "verification_selectors": copy.deepcopy(existing_register.get("verification_selectors") or normalized_record.get("verification_selectors") or []),
            "verification_policy": copy.deepcopy(existing_register.get("verification_policy") or normalized_record.get("verification_policy") or {}),
            "planner_context": planner_context,
            "template_policy": default_planning_policy(),
        }
    )
    repaired_register = _decorate_stage_register(workspace, _canonicalize_register_state(repaired_register), workstream_id)
    existing_stage_ids = {entry["id"] for entry in existing_register.get("stages", [])}
    repaired_stage_ids = {entry["id"] for entry in repaired_register.get("stages", [])}
    normalized_fields = [
        field
        for field in ("title", "kind", "scope_summary", "branch_hint")
        if (record.get(field) or normalized_record.get(field)) != normalized_record.get(field) or record.get(field) != normalized_record.get(field)
    ]
    mirror_cleanup = raw_brief.strip() != canonical_brief.strip() or any(field in existing_register for field in MIRROR_REGISTER_FIELDS)
    return {
        "workstream_id": workstream_id,
        "record_before": copy.deepcopy(record),
        "record_after": normalized_record,
        "existing_register": existing_register,
        "repaired_register": repaired_register,
        "canonical_brief": canonical_brief,
        "removed_stage_ids": sorted(existing_stage_ids.difference(repaired_stage_ids)),
        "added_stage_ids": sorted(repaired_stage_ids.difference(existing_stage_ids)),
        "current_stage_before": existing_register.get("current_stage"),
        "current_stage_after": repaired_register.get("current_stage"),
        "normalized_fields": [field for field in normalized_fields if record.get(field) != normalized_record.get(field)],
        "mirror_cleanup_required": mirror_cleanup,
        "planner_context": planner_context,
        "source_schema_version": existing_register.get("schema_version"),
    }


def _repair_plan(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    detection = detect_workspace(workspace)
    raw_current_state = _load_json(Path(paths["workspace_state"]), default={}, strict=True, purpose="workspace state") or {}
    current_state = _decorate_workspace_state(workspace, copy.deepcopy(raw_current_state))
    index = _load_workstreams_index(paths)
    workstream_plans = [_repair_plan_for_workstream(workspace, detection, item) for item in index.get("items", [])]
    current_workstream_id = _sanitize_nullable_identifier(
        raw_current_state.get("current_workstream_id") or current_state.get("current_workstream_id") or index.get("current_workstream_id")
    )
    available_ids = {plan["workstream_id"] for plan in workstream_plans}
    if current_workstream_id not in available_ids and workstream_plans:
        current_workstream_id = workstream_plans[0]["workstream_id"]
    normalized_items: list[dict[str, Any]] = []
    applied_actions: list[dict[str, Any]] = []
    preview_workstreams: list[dict[str, Any]] = []
    for plan in workstream_plans:
        item = copy.deepcopy(plan["record_after"])
        item["plan_status"] = plan["repaired_register"].get("plan_status")
        item["current_stage"] = plan["repaired_register"].get("current_stage")
        item["current_slice"] = plan["repaired_register"].get("current_slice")
        item["last_completed_stage"] = plan["repaired_register"].get("last_completed_stage")
        item["verification_selectors"] = plan["repaired_register"].get("verification_selectors", [])
        item["verification_policy"] = plan["repaired_register"].get("verification_policy", {})
        item["updated_at"] = now_iso()
        normalized_items.append(item)
        if plan["normalized_fields"]:
            applied_actions.append(
                {
                    "action": "normalize_workstream_record",
                    "workstream_id": plan["workstream_id"],
                    "fields": plan["normalized_fields"],
                }
            )
        if plan["removed_stage_ids"] or plan["added_stage_ids"]:
            applied_actions.append(
                {
                    "action": "replan_workstream",
                    "workstream_id": plan["workstream_id"],
                    "removed_stage_ids": plan["removed_stage_ids"],
                    "added_stage_ids": plan["added_stage_ids"],
                }
            )
        if plan["mirror_cleanup_required"]:
            applied_actions.append(
                {
                    "action": "strip_canonical_mirror_markers",
                    "workstream_id": plan["workstream_id"],
                }
            )
        preview_workstreams.append(
            {
                "workstream_id": plan["workstream_id"],
                "title_before": plan["record_before"].get("title"),
                "title_after": plan["record_after"].get("title"),
                "kind_before": plan["record_before"].get("kind"),
                "kind_after": plan["record_after"].get("kind"),
                "scope_summary_before": plan["record_before"].get("scope_summary"),
                "scope_summary_after": plan["record_after"].get("scope_summary"),
                "plan_status_before": plan["existing_register"].get("plan_status"),
                "plan_status_after": plan["repaired_register"].get("plan_status"),
                "removed_stage_ids": plan["removed_stage_ids"],
                "added_stage_ids": plan["added_stage_ids"],
                "current_stage_before": plan["current_stage_before"],
                "current_stage_after": plan["current_stage_after"],
                "mirror_cleanup_required": plan["mirror_cleanup_required"],
                "normalized_fields": plan["normalized_fields"],
                "infra_mode_after": detection["local_dev_policy"]["infra_mode"],
                "planner_context": plan["planner_context"],
            }
        )
    if raw_current_state.get("docker_policy") is not None:
        applied_actions.append({"action": "remove_legacy_docker_policy"})
    source_workstream_schema_versions = {
        plan["workstream_id"]: plan["source_schema_version"]
        for plan in workstream_plans
    }
    state_repair_status = {
        "last_checked_at": now_iso(),
        "last_repaired_at": None,
        "status": "repair_required" if applied_actions or raw_current_state.get("schema_version", 0) != STATE_SCHEMA_VERSION else "clean",
        "source_schema_version": raw_current_state.get("schema_version", 0) or 0,
        "source_workstream_schema_versions": source_workstream_schema_versions,
        "target_schema_version": STATE_SCHEMA_VERSION,
        "last_successful_schema_version": current_state.get("state_repair_status", {}).get("last_successful_schema_version"),
        "applied_actions": applied_actions,
    }
    return {
        "paths": paths,
        "detection": detection,
        "current_state": current_state,
        "raw_current_state": raw_current_state,
        "current_workstream_id": current_workstream_id,
        "normalized_items": normalized_items,
        "workstream_plans": workstream_plans,
        "applied_actions": applied_actions,
        "preview_workstreams": preview_workstreams,
        "state_repair_status": state_repair_status,
    }


def preview_repair_workspace_state(workspace: str | Path) -> dict[str, Any]:
    plan = _repair_plan(workspace)
    return {
        "workspace_path": plan["detection"]["workspace_path"],
        "workspace_label": plan["detection"]["workspace_label"],
        "paths": plan["paths"],
        "current_workstream_id": plan["current_workstream_id"],
        "changes": {
            "host_os": plan["detection"]["host_os"],
            "local_dev_policy": plan["detection"]["local_dev_policy"],
            "support_warnings": plan["detection"]["support_warnings"],
            "remove_legacy_docker_policy": any(action["action"] == "remove_legacy_docker_policy" for action in plan["applied_actions"]),
            "state_repair_status": plan["state_repair_status"],
            "repair_actions": plan["applied_actions"],
            "workstreams": plan["preview_workstreams"],
        },
    }


def repair_workspace_state(workspace: str | Path) -> dict[str, Any]:
    plan = _repair_plan(workspace)
    paths = plan["paths"]
    detection = plan["detection"]
    current_state = plan["current_state"]
    index = _load_workstreams_index(paths)
    for workstream_plan in plan["workstream_plans"]:
        workstream_paths = _workstream_paths(workspace, workstream_plan["workstream_id"])
        write_stage_register(
            workspace,
            _strip_mirror_fields(workstream_plan["repaired_register"]),
            confirmed_stage_plan_edit=True,
            workstream_id=workstream_plan["workstream_id"],
        )
        _write_text(Path(workstream_paths["current_workstream_active_brief"]), workstream_plan["canonical_brief"])
    index["items"] = plan["normalized_items"]
    index["current_workstream_id"] = plan["current_workstream_id"]
    _save_workstreams_index(paths, index)
    _prune_workstream_artifacts(workspace, [item["workstream_id"] for item in index.get("items", []) if item.get("workstream_id")])

    tasks_index = _load_tasks_index(paths)
    for task in tasks_index.get("items", []):
        task_paths = _task_paths(workspace, task["task_id"])
        if not Path(task_paths["current_task_brief"]).exists():
            _write_text(Path(task_paths["current_task_brief"]), TASK_BRIEF_PLACEHOLDER)
        if not Path(task_paths["current_task_verification_summary"]).exists():
            _write_json(Path(task_paths["current_task_verification_summary"]), _default_verification_summary())
        task["verification_selectors"] = task.get("verification_selectors") or (
            {"explicit_targets": [task["verification_target"]]} if task.get("verification_target") else {}
        )
        task["verification_mode_default"] = task.get("verification_mode_default") or "targeted"
        _write_json(Path(task_paths["current_task_record"]), task)
    _save_tasks_index(paths, tasks_index)

    current_state.update(
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "host_os": detection["host_os"],
            "host_capabilities": detection["host_capabilities"],
            "toolchain_capabilities": detection["toolchain_capabilities"],
            "detected_stacks": detection["detected_stacks"],
            "selected_profiles": detection["selected_profiles"],
            "plugin_platform": detection["plugin_platform"],
            "local_dev_policy": detection["local_dev_policy"],
            "planning_policy": detection["planning_policy"],
            "support_warnings": detection["support_warnings"],
            "current_workstream_id": plan["current_workstream_id"],
            "workspace_mode": "task" if current_state.get("current_task_id") else ("workstream" if plan["current_workstream_id"] else "workspace"),
            "state_repair_status": {
                "last_checked_at": now_iso(),
                "last_repaired_at": now_iso(),
                "status": "repaired",
                "source_schema_version": plan["state_repair_status"]["source_schema_version"],
                "source_workstream_schema_versions": plan["state_repair_status"]["source_workstream_schema_versions"],
                "target_schema_version": STATE_SCHEMA_VERSION,
                "last_successful_schema_version": STATE_SCHEMA_VERSION,
                "applied_actions": plan["applied_actions"],
            },
        }
    )
    current_state.pop("docker_policy", None)
    _persist_workspace_state(workspace, current_state)
    repair_note = {
        "schema_version": 1,
        "repaired_at": now_iso(),
        "workspace_path": detection["workspace_path"],
        "from_schema_version": plan["state_repair_status"]["source_schema_version"],
        "source_workstream_schema_versions": plan["state_repair_status"]["source_workstream_schema_versions"],
        "to_schema_version": STATE_SCHEMA_VERSION,
        "applied_actions": plan["applied_actions"],
    }
    _write_json(Path(paths["migrations_root"]) / f"repair-{int(time.time())}.json", repair_note)
    _mirror_current_workstream(workspace, current_state["current_workstream_id"])
    result = {
        "workspace_state": read_workspace_state(workspace),
        "workstreams": list_workstreams(workspace),
        "tasks": list_tasks(workspace),
        "preview": preview_repair_workspace_state(workspace),
    }
    if current_state.get("current_workstream_id"):
        result["stage_register"] = read_stage_register(workspace)
    return result


def read_workspace_state(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    return _normalize_workspace_state_payload(
        _load_json(Path(paths["workspace_state"]), default={}, strict=True, purpose="workspace state") or {}
    )


def _resolve_target_workstream_id(workspace: str | Path, workstream_id: str | None = None) -> str:
    paths = _ensure_workspace_initialized(workspace)
    target_id = _sanitize_nullable_identifier(workstream_id) if workstream_id is not None else _current_workstream_id(paths)
    if not target_id:
        raise _no_current_workstream_error()
    return target_id


def read_stage_register(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    path = Path(paths["current_workstream_stage_register"])
    if not path.exists():
        raise FileNotFoundError(f"Stage register does not exist: {path}")
    return _decorate_stage_register(
        workspace,
        _canonicalize_register_state(
            _strip_mirror_fields(_load_json(path, default={}, strict=True, purpose=f"workstream stage register `{target_id}`") or {})
        ),
        target_id,
    )


def _current_task_record(workspace: str | Path) -> dict[str, Any] | None:
    paths = _ensure_workspace_initialized(workspace)
    task_id = _current_task_id(paths)
    if not task_id:
        return None
    return read_task(workspace, task_id=task_id)


def list_stages(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    register = read_stage_register(workspace, workstream_id=workstream_id)
    return {
        "workspace_path": register["workspace_path"],
        "workspace_label": register.get("workspace_label"),
        "workstream_id": register.get("workstream_id"),
        "workstream_title": register.get("workstream_title"),
        "plan_status": register.get("plan_status"),
        "current_stage": register["current_stage"],
        "last_completed_stage": register["last_completed_stage"],
        "dashboard_counts": register.get("dashboard_counts"),
        "stages": [
            {
                "id": stage["id"],
                "title": stage["title"],
                "status": stage["status"],
                "completed_at": stage["completed_at"],
            }
            for stage in register["stages"]
        ],
    }


def get_state_paths(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    return {
        "workspace_path": paths["workspace_path"],
        "paths": paths,
    }


def _host_setup_runtime_paths() -> dict[str, Path]:
    root = runtime_root() / "host-setup"
    return {
        "root": root,
        "status_json": root / "status.json",
        "history_jsonl": root / "history.jsonl",
    }


def _read_host_setup_runtime_status() -> dict[str, Any]:
    return _load_json(_host_setup_runtime_paths()["status_json"], default={}) or {}


def _write_host_setup_runtime_status(payload: dict[str, Any]) -> None:
    paths = _host_setup_runtime_paths()
    _write_json(paths["status_json"], payload)


def _append_host_setup_runtime_history(payload: dict[str, Any]) -> None:
    paths = _host_setup_runtime_paths()
    paths["history_jsonl"].parent.mkdir(parents=True, exist_ok=True)
    with paths["history_jsonl"].open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _record_host_setup_operation(record: dict[str, Any]) -> None:
    status_payload = {
        "schema_version": HOST_SETUP_STATUS_SCHEMA_VERSION,
        "updated_at": now_iso(),
        "last_operation": record,
    }
    _write_host_setup_runtime_status(status_payload)
    _append_host_setup_runtime_history(record)


def _last_host_setup_operation_for_workspace(workspace: str | Path) -> dict[str, Any] | None:
    last_operation = _read_host_setup_runtime_status().get("last_operation")
    if not isinstance(last_operation, dict):
        return None
    try:
        if Path(last_operation.get("workspace_path", "")).expanduser().resolve() != Path(workspace).expanduser().resolve():
            return None
    except OSError:
        return None
    return last_operation


def show_host_setup_plan(workspace: str | Path, requirement_ids: list[str] | None = None) -> dict[str, Any]:
    plan = _build_host_setup_plan(workspace, requirement_ids=requirement_ids)
    return {
        **plan,
        "host_setup_status": _last_host_setup_operation_for_workspace(workspace),
    }


def _execute_host_setup_plan(workspace: str | Path, *, requirement_ids: list[str] | None, operation: str, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError(f"{operation.replace('_', ' ').title()} requires explicit confirmation.")
    plan = _build_host_setup_plan(workspace, requirement_ids=requirement_ids)
    before_support = show_host_support(workspace)
    step_results: list[dict[str, Any]] = []
    any_failures = False
    any_auto_steps = False
    for step in plan["steps"]:
        if step["mode"] != "automatic" or not step["commands"]:
            step_results.append(
                {
                    **step,
                    "status": step["status"],
                    "command_results": [],
                }
            )
            continue
        any_auto_steps = True
        command_results = []
        step_status = "completed"
        for command in step["commands"]:
            started_at = now_iso()
            result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
            command_results.append(
                {
                    "command": command,
                    "command_preview": _render_host_setup_command(command),
                    "started_at": started_at,
                    "completed_at": now_iso(),
                    "returncode": result.returncode,
                    "stdout_tail": result.stdout.strip().splitlines()[-20:],
                    "stderr_tail": result.stderr.strip().splitlines()[-20:],
                }
            )
            if result.returncode != 0:
                step_status = "failed"
                any_failures = True
                break
        step_results.append(
            {
                **step,
                "status": step_status,
                "command_results": command_results,
            }
        )

    repair_workspace_state(workspace)
    after_support = show_host_support(workspace)
    unresolved_requirements = [
        requirement_id
        for requirement_id in plan["missing_requirements"]
        if not after_support["toolchain_capabilities"].get(requirement_id, {}).get("available", True)
    ]
    if not plan["missing_requirements"]:
        status = "no_action_needed"
    elif any_failures and unresolved_requirements:
        status = "failed"
    elif unresolved_requirements and any_auto_steps:
        status = "partial"
    elif unresolved_requirements:
        status = "manual_action_required"
    else:
        status = "completed"

    result = {
        "workspace_path": plan["workspace_path"],
        "workspace_label": plan.get("workspace_label"),
        "host_os": plan["host_os"],
        "operation": operation,
        "confirmed": True,
        "status": status,
        "selected_requirements": plan["selected_requirements"],
        "missing_requirements_before": plan["missing_requirements"],
        "unresolved_requirements_after": unresolved_requirements,
        "before_support": before_support,
        "after_support": after_support,
        "steps": step_results,
    }
    _record_host_setup_operation(
        {
            "operation": operation,
            "workspace_path": plan["workspace_path"],
            "workspace_label": plan.get("workspace_label"),
            "host_os": plan["host_os"],
            "status": status,
            "selected_requirements": plan["selected_requirements"],
            "missing_requirements_before": plan["missing_requirements"],
            "unresolved_requirements_after": unresolved_requirements,
            "updated_at": now_iso(),
        }
    )
    return result


def install_host_requirements(workspace: str | Path, requirement_ids: list[str] | None = None, confirmed: bool = False) -> dict[str, Any]:
    return _execute_host_setup_plan(
        workspace,
        requirement_ids=requirement_ids,
        operation="install_host_requirements",
        confirmed=confirmed,
    )


def repair_host_requirements(workspace: str | Path, requirement_ids: list[str] | None = None, confirmed: bool = False) -> dict[str, Any]:
    return _execute_host_setup_plan(
        workspace,
        requirement_ids=requirement_ids,
        operation="repair_host_requirements",
        confirmed=confirmed,
    )


def show_host_support(workspace: str | Path) -> dict[str, Any]:
    state = read_workspace_state(workspace)
    detection = detect_workspace(workspace)
    host_setup_plan = _build_host_setup_plan(workspace)
    host_setup_status = _last_host_setup_operation_for_workspace(workspace)
    return {
        "workspace_path": state["workspace_path"],
        "workspace_label": state.get("workspace_label"),
        "host_os": detection.get("host_os") or state.get("host_os"),
        "host_capabilities": state.get("host_capabilities", {}),
        "toolchain_capabilities": detection.get("toolchain_capabilities", state.get("toolchain_capabilities", {})),
        "support_warnings": detection.get("support_warnings", []),
        "host_setup": {
            "status": host_setup_plan["status"],
            "requires_confirmation": host_setup_plan["requires_confirmation"],
            "selected_requirements": host_setup_plan["selected_requirements"],
            "missing_requirements": host_setup_plan["missing_requirements"],
            "automatic_step_count": host_setup_plan["automatic_step_count"],
            "manual_step_count": host_setup_plan["manual_step_count"],
            "last_operation": host_setup_status,
        },
    }


def get_active_brief(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    workspace_state = read_workspace_state(workspace)
    if workspace_state.get("workspace_mode") == "task" and workspace_state.get("current_task_id"):
        task = read_task(workspace, task_id=workspace_state["current_task_id"])
        task_paths = _task_paths(workspace, task["task_id"])
        return {
            "workspace_path": paths["workspace_path"],
            "mode": "task",
            "task_id": task["task_id"],
            "active_brief_path": task_paths["current_task_brief"],
            "markdown": _read_text(Path(task_paths["current_task_brief"]), default=TASK_BRIEF_PLACEHOLDER),
        }
    workstream_id = workspace_state.get("current_workstream_id")
    if not workstream_id:
        raise _no_current_workstream_error()
    workstream_paths = _workstream_paths(workspace, workstream_id)
    return {
        "workspace_path": paths["workspace_path"],
        "mode": "workstream",
        "workstream_id": workstream_id,
        "active_brief_path": workstream_paths["current_workstream_active_brief"],
        "markdown": _canonical_brief_markdown(_read_text(Path(workstream_paths["current_workstream_active_brief"]), default=BRIEF_PLACEHOLDER)),
    }


def set_active_brief(workspace: str | Path, markdown: str) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    state = read_workspace_state(workspace)
    if state.get("workspace_mode") == "task" and state.get("current_task_id"):
        task_paths = _task_paths(workspace, state["current_task_id"])
        _write_text(Path(task_paths["current_task_brief"]), markdown or TASK_BRIEF_PLACEHOLDER)
        return get_active_brief(workspace)
    workstream_id = state.get("current_workstream_id")
    if not workstream_id:
        raise _no_current_workstream_error()
    workstream_paths = _workstream_paths(workspace, workstream_id)
    _write_text(Path(workstream_paths["current_workstream_active_brief"]), _canonical_brief_markdown(markdown or BRIEF_PLACEHOLDER))
    _mirror_current_workstream(workspace, workstream_id)
    return get_active_brief(workspace)


def _stage_definition_changed(old_stage: dict[str, Any], new_stage: dict[str, Any]) -> bool:
    ignored = {"status", "completed_at"}
    old_filtered = {key: value for key, value in old_stage.items() if key not in ignored}
    new_filtered = {key: value for key, value in new_stage.items() if key not in ignored}
    return old_filtered != new_filtered


def _canonicalize_stage_entries(register: dict[str, Any]) -> list[dict[str, Any]]:
    docs_hints = copy.deepcopy(register.get("docs_hints") or [])
    canonical: list[dict[str, Any]] = []
    for index, stage in enumerate(register.get("stages") or []):
        payload = copy.deepcopy(stage or {})
        payload["order"] = index
        payload.setdefault("status", "planned")
        payload.setdefault("completed_at", None)
        payload.setdefault("origin", "custom")
        payload.setdefault("source_module_ids", [])
        payload.setdefault("planner_notes", [])
        payload.setdefault("verification_selectors", {})
        payload.setdefault("verification_policy", {})
        payload.setdefault("profile_notes", [])
        payload.setdefault("docs_hints", docs_hints)
        payload.setdefault("checklists", {"entry": [], "closeout": []})
        payload.setdefault("docs_sync_obligations", [])
        payload.setdefault("verification_hooks", [])
        payload.setdefault("closeout_rules", [])
        payload.setdefault("guidance_hints", [])
        payload.setdefault(
            "resolved_guidance",
            {
                "profile_notes": payload.get("profile_notes", []),
                "docs_hints": payload.get("docs_hints", []),
                "guidance_hints": payload.get("guidance_hints", []),
            },
        )
        canonical.append(payload)
    return canonical


def _canonicalize_register_state(register: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(register or {})
    payload["stages"] = _canonicalize_stage_entries(payload)
    payload["plan_status"] = payload.get("plan_status") or _derived_plan_status(payload["stages"])

    if not payload["stages"]:
        payload["plan_status"] = "needs_user_confirmation"
        payload["current_stage"] = None
        payload["last_completed_stage"] = None
        payload["stage_status"] = None
        payload["current_slice"] = None
        payload["remaining_slices"] = []
        payload["slice_status"] = None
        payload["active_goal"] = None
        payload["next_task"] = None
        return payload

    stage_map = {stage["id"]: stage for stage in payload["stages"]}
    current_stage = stage_map.get(payload.get("current_stage"))
    if current_stage is None:
        current_stage = next((stage for stage in payload["stages"] if stage.get("status") != "completed"), payload["stages"][0])
        payload["current_stage"] = current_stage["id"]

    stage_slices = current_stage.get("canonical_execution_slices") or []
    current_slice = payload.get("current_slice")
    if stage_slices:
        if current_slice not in stage_slices:
            current_slice = stage_slices[0]
        payload["current_slice"] = current_slice
        payload["remaining_slices"] = [slice_id for slice_id in stage_slices if slice_id != current_slice]
    else:
        payload["current_slice"] = None
        payload["remaining_slices"] = []

    if payload.get("stage_status") is None:
        payload["stage_status"] = current_stage.get("status") or "planned"
    if payload.get("slice_status") is None:
        payload["slice_status"] = "planned" if payload.get("current_slice") else None
    if payload.get("active_goal") is None:
        payload["active_goal"] = current_stage.get("objective")
    if payload.get("last_completed_stage") is None:
        payload["last_completed_stage"] = next(
            (stage["id"] for stage in reversed(payload["stages"]) if stage.get("status") == "completed"),
            None,
        )
    return payload


def _validate_register(register: dict[str, Any]) -> None:
    required = {
        "workspace_path",
        "plan_status",
        "current_stage",
        "stage_status",
        "current_slice",
        "remaining_slices",
        "next_task",
        "stages",
        "workstream_id",
    }
    missing = required.difference(register.keys())
    if missing:
        raise ValueError(f"Stage register is missing required keys: {sorted(missing)}")
    if register["plan_status"] not in PLAN_STATUS_VALUES:
        raise ValueError(f"Invalid plan_status: {register['plan_status']}")
    if register["stage_status"] is not None and register["stage_status"] not in STAGE_STATUS_VALUES:
        raise ValueError(f"Invalid stage_status: {register['stage_status']}")
    if not isinstance(register["stages"], list):
        raise ValueError("Stage register must contain a stages list.")
    stage_ids: set[str] = set()
    for stage in register["stages"]:
        stage_id = stage.get("id")
        if not stage_id:
            raise ValueError("Every stage must define an id.")
        if stage_id in stage_ids:
            raise ValueError(f"Stage ids must be unique: {stage_id}")
        stage_ids.add(stage_id)
        if not stage.get("title"):
            raise ValueError(f"Stage `{stage_id}` is missing a title.")
        if stage["status"] not in STAGE_STATUS_VALUES:
            raise ValueError(f"Invalid stage status for {stage['id']}: {stage['status']}")
    if register["current_stage"] is not None and register["current_stage"] not in stage_ids:
        raise ValueError(f"Current stage does not exist in stage list: {register['current_stage']}")
    if not register["stages"] and register["plan_status"] != "needs_user_confirmation":
        raise ValueError("Empty stage registers must remain in needs_user_confirmation status.")


def _update_workstream_record_from_register(workspace: str | Path, register: dict[str, Any]) -> None:
    paths = _ensure_workspace_initialized(workspace)
    index = _load_workstreams_index(paths)
    target = _workstream_record_by_id(index, register["workstream_id"])
    target["current_stage"] = register.get("current_stage")
    target["current_slice"] = register.get("current_slice")
    target["last_completed_stage"] = register.get("last_completed_stage")
    target["plan_status"] = register.get("plan_status") or _derived_plan_status(register.get("stages", []))
    target["status"] = register.get("workstream_status") or target.get("status") or "active"
    target["updated_at"] = now_iso()
    _save_workstreams_index(paths, index)


def write_stage_register(
    workspace: str | Path,
    register: dict[str, Any],
    confirmed_stage_plan_edit: bool = False,
    workstream_id: str | None = None,
) -> dict[str, Any]:
    register = _canonicalize_register_state(register)
    if register.get("stages") and confirmed_stage_plan_edit:
        register["plan_status"] = "confirmed"
    _validate_register(register)
    target_id = _resolve_target_workstream_id(workspace, workstream_id or register.get("workstream_id"))
    old_register = read_stage_register(workspace, workstream_id=target_id)
    old_stages = {stage["id"]: stage for stage in old_register["stages"]}
    new_stages = {stage["id"]: stage for stage in register["stages"]}

    definition_changed = False
    for stage_id, old_stage in old_stages.items():
        if stage_id not in new_stages:
            if old_stage["status"] == "completed":
                raise ValueError(f"Completed stage cannot be removed: {stage_id}")
            definition_changed = True
            continue
        new_stage = new_stages[stage_id]
        if old_stage["status"] == "completed" and old_stage != new_stage:
            raise ValueError(f"Completed stage cannot be modified: {stage_id}")
        if _stage_definition_changed(old_stage, new_stage):
            definition_changed = True
    for stage_id in new_stages:
        if stage_id not in old_stages:
            definition_changed = True

    if definition_changed and not confirmed_stage_plan_edit:
        raise ValueError("Stage definition changes require explicit confirmation.")

    decorated = _decorate_stage_register(workspace, register, target_id)
    workstream_paths = _workstream_paths(workspace, target_id)
    canonical_register = _strip_mirror_fields(decorated)
    _write_json(Path(workstream_paths["current_workstream_stage_register"]), canonical_register)
    _remove_stage_docs(workstream_paths)
    _update_workstream_record_from_register(workspace, decorated)
    if read_workspace_state(workspace).get("current_workstream_id") == target_id:
        _mirror_current_workstream(workspace, target_id)
        _persist_workspace_state(workspace, read_workspace_state(workspace))
    return read_stage_register(workspace, workstream_id=target_id)


def read_design_brief(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    path = Path(paths["design_brief"])
    return _load_json(path, default=_default_design_brief(paths["workspace_path"], paths))


def write_design_brief(workspace: str | Path, brief: dict[str, Any], workstream_id: str | None = None) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    payload = _default_design_brief(paths["workspace_path"], paths)
    payload.update(brief or {})
    payload["workspace_path"] = paths["workspace_path"]
    payload["reference_board_path"] = paths["reference_board"]
    payload["design_handoff_path"] = paths["design_handoff"]
    payload["updated_at"] = now_iso()
    _write_json(Path(paths["design_brief"]), payload)
    return payload


def cache_reference_preview(workspace: str | Path, source_path: str, candidate_id: str | None = None, workstream_id: str | None = None) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Reference preview source does not exist: {source}")
    cache_id = sanitize_identifier(candidate_id or source.stem, "reference")
    ext = source.suffix or ".bin"
    destination = Path(paths["design_cache_dir"]) / f"{cache_id}{ext}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "workspace_path": paths["workspace_path"],
        "workstream_id": target_id,
        "source_path": str(source),
        "cached_preview_path": str(destination),
    }


def _hydrate_reference_candidates(workspace: str | Path, board: dict[str, Any], workstream_id: str | None = None) -> dict[str, Any]:
    candidates = []
    for candidate in board.get("candidates", []):
        hydrated = dict(candidate)
        source_path = hydrated.get("cached_preview_source_path")
        if source_path:
            cached = cache_reference_preview(workspace, source_path, hydrated.get("id"), workstream_id=workstream_id)
            hydrated["cached_preview_path"] = cached["cached_preview_path"]
            hydrated.pop("cached_preview_source_path", None)
        candidates.append(hydrated)
    board["candidates"] = candidates
    return board


def list_reference_boards(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    boards = []
    for path in _json_files(Path(paths["design_boards_dir"])):
        payload = _load_json(path, default={}) or {}
        boards.append(
            {
                "board_id": payload.get("board_id", path.stem),
                "title": payload.get("title"),
                "platform": payload.get("platform"),
                "updated_at": payload.get("updated_at"),
                "path": str(path),
                "candidate_count": len(payload.get("candidates", [])),
            }
        )
    return {
        "workspace_path": paths["workspace_path"],
        "workstream_id": target_id,
        "boards": boards,
    }


def read_reference_board(workspace: str | Path, board_id: str = "current", workstream_id: str | None = None) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    board_key = sanitize_identifier(board_id, "current")
    path = Path(paths["reference_board"]) if board_key == "current" else Path(paths["design_boards_dir"]) / f"{board_key}.json"
    return _load_json(path, default=_default_reference_board(paths["workspace_path"], paths, board_key))


def write_reference_board(
    workspace: str | Path,
    board: dict[str, Any],
    board_id: str = "current",
    make_current: bool = True,
    workstream_id: str | None = None,
) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    board_key = sanitize_identifier(board_id or board.get("board_id"), "current")
    payload = _default_reference_board(paths["workspace_path"], paths, board_key)
    payload.update(board or {})
    payload["workspace_path"] = paths["workspace_path"]
    payload["board_id"] = board_key
    payload["updated_at"] = now_iso()
    payload = _hydrate_reference_candidates(workspace, payload, workstream_id=target_id)
    board_path = Path(paths["design_boards_dir"]) / f"{board_key}.json"
    _write_json(board_path, payload)
    if make_current or board_key == "current":
        _write_json(Path(paths["reference_board"]), payload)
    return payload


def list_design_handoffs(workspace: str | Path, workstream_id: str | None = None) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    handoffs = []
    for path in _json_files(Path(paths["design_handoffs_dir"])):
        payload = _load_json(path, default={}) or {}
        handoffs.append(
            {
                "handoff_id": payload.get("handoff_id", path.stem),
                "platform": payload.get("platform"),
                "status": payload.get("status"),
                "updated_at": payload.get("updated_at"),
                "path": str(path),
                "verification_hook_count": len(payload.get("verification_hooks", [])),
            }
        )
    return {
        "workspace_path": paths["workspace_path"],
        "workstream_id": target_id,
        "handoffs": handoffs,
    }


def read_design_handoff(workspace: str | Path, handoff_id: str = "current", workstream_id: str | None = None) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    handoff_key = sanitize_identifier(handoff_id, "current")
    path = Path(paths["design_handoff"]) if handoff_key == "current" else Path(paths["design_handoffs_dir"]) / f"{handoff_key}.json"
    return _load_json(path, default=_default_design_handoff(paths["workspace_path"], handoff_key))


def write_design_handoff(
    workspace: str | Path,
    handoff: dict[str, Any],
    handoff_id: str = "current",
    make_current: bool = True,
    workstream_id: str | None = None,
) -> dict[str, Any]:
    target_id = _resolve_target_workstream_id(workspace, workstream_id)
    paths = _workstream_paths(workspace, target_id)
    handoff_key = sanitize_identifier(handoff_id or handoff.get("handoff_id"), "current")
    payload = _default_design_handoff(paths["workspace_path"], handoff_key)
    payload.update(handoff or {})
    payload["workspace_path"] = paths["workspace_path"]
    payload["handoff_id"] = handoff_key
    payload["updated_at"] = now_iso()
    handoff_path = Path(paths["design_handoffs_dir"]) / f"{handoff_key}.json"
    _write_json(handoff_path, payload)
    if make_current or handoff_key == "current":
        _write_json(Path(paths["design_handoff"]), payload)
    return payload


def read_gui_runtime() -> dict[str, Any]:
    return _load_json(gui_runtime_path(), default={"status": "stopped", "runtime_path": str(gui_runtime_path())}) or {
        "status": "stopped",
        "runtime_path": str(gui_runtime_path()),
    }


def list_workstreams(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    index = _load_workstreams_index(paths)
    items = []
    for item in index.get("items", []):
        if item.get("visibility") == "system-hidden":
            continue
        register = _load_workstream_register(workspace, item["workstream_id"])
        items.append(
            {
                **item,
                "current_stage": register.get("current_stage"),
                "current_slice": register.get("current_slice"),
                "last_completed_stage": register.get("last_completed_stage"),
                "plan_status": register.get("plan_status"),
                "stage_status": register.get("stage_status"),
                "path": _workstream_paths(workspace, item["workstream_id"])["current_workstream_root"],
            }
        )
    return {
        "workspace_path": paths["workspace_path"],
        "current_workstream_id": index.get("current_workstream_id"),
        "items": items,
    }


def create_workstream(
    workspace: str | Path,
    title: str,
    kind: str = "feature",
    branch_hint: str | None = None,
    scope_summary: str | None = None,
    workstream_id: str | None = None,
    source_context: dict[str, Any] | None = None,
    make_current: bool = True,
) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    previous_task = _deactivate_current_task(workspace, next_status="planned")
    detection = detect_workspace(workspace)
    index = _load_workstreams_index(paths)
    target_id = sanitize_identifier(workstream_id or title, f"workstream-{uuid.uuid4().hex[:6]}")
    if any(item.get("workstream_id") == target_id for item in index.get("items", [])):
        raise ValueError(f"Workstream already exists: {target_id}")
    record = _default_workstream_record(detection, target_id, title=title, kind=kind, branch_hint=branch_hint, scope_summary=scope_summary)
    if source_context:
        record["source_context"] = copy.deepcopy(source_context)
    register = build_stage_register(detection, record)
    _write_default_workstream_documents(workspace, record, register)
    index["items"].append(record)
    if make_current:
        index["current_workstream_id"] = target_id
    _save_workstreams_index(paths, index)
    state = read_workspace_state(workspace)
    if make_current:
        state["current_workstream_id"] = target_id
        state["workspace_mode"] = "workstream"
        state["current_task_id"] = None
    _persist_workspace_state(workspace, state)
    if make_current:
        _mirror_current_workstream(workspace, target_id)
    return {
        "workspace_path": paths["workspace_path"],
        "created_workstream_id": target_id,
        "workstreams": list_workstreams(workspace),
        "previous_task": previous_task,
        "current_workstream": current_workstream(workspace) if make_current else (current_workstream(workspace) if state.get("current_workstream_id") else None),
    }


def switch_workstream(workspace: str | Path, workstream_id: str) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    _deactivate_current_task(workspace, next_status="planned")
    index = _load_workstreams_index(paths)
    _workstream_record_by_id(index, workstream_id)
    index["current_workstream_id"] = workstream_id
    _save_workstreams_index(paths, index)
    state = read_workspace_state(workspace)
    state["current_workstream_id"] = workstream_id
    state["workspace_mode"] = "workstream"
    state["current_task_id"] = None
    _persist_workspace_state(workspace, state)
    _mirror_current_workstream(workspace, workstream_id)
    return current_workstream(workspace)


def current_workstream(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    index = _load_workstreams_index(paths)
    workstream_id = _sanitize_nullable_identifier(index.get("current_workstream_id"))
    if not workstream_id:
        raise _no_current_workstream_error()
    record = _workstream_record_by_id(index, workstream_id)
    register = read_stage_register(workspace, workstream_id=workstream_id)
    return {
        **record,
        "register": register,
        "paths": _workstream_paths(workspace, workstream_id),
    }


def close_current_workstream(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    index = _load_workstreams_index(paths)
    workstream_id = _sanitize_nullable_identifier(index.get("current_workstream_id"))
    if not workstream_id:
        raise _no_current_workstream_error()
    record = _workstream_record_by_id(index, workstream_id)
    register = read_stage_register(workspace, workstream_id=workstream_id)
    all_completed = bool(register.get("stages")) and all(stage.get("status") == "completed" for stage in register.get("stages", []))
    record["status"] = "completed" if all_completed and register.get("plan_status") == "confirmed" else "archived"
    record["closed_at"] = now_iso()
    record["updated_at"] = now_iso()
    register["workstream_status"] = record["status"]
    write_stage_register(workspace, register, confirmed_stage_plan_edit=False, workstream_id=workstream_id)
    _save_workstreams_index(paths, index)
    return current_workstream(workspace)


def list_tasks(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    index = _load_tasks_index(paths)
    return {
        "workspace_path": paths["workspace_path"],
        "current_task_id": index.get("current_task_id"),
        "items": index.get("items", []),
    }


def switch_task(workspace: str | Path, task_id: str) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    index = _load_tasks_index(paths)
    target_id = sanitize_identifier(task_id, "")
    if not target_id:
        raise ValueError("Task id is required.")
    target = _task_record_by_id(index, target_id)
    current_id = sanitize_identifier(index.get("current_task_id"), "") if index.get("current_task_id") else ""
    if current_id and current_id != target_id:
        _deactivate_current_task(workspace, next_status="planned")
        index = _load_tasks_index(paths)
    activated = _activate_task_payload(target)
    _persist_task_record(workspace, activated)
    index = _load_tasks_index(paths)
    index["current_task_id"] = target_id
    _save_tasks_index(paths, index)
    state = read_workspace_state(workspace)
    state["current_task_id"] = target_id
    state["workspace_mode"] = "task"
    _persist_workspace_state(workspace, state)
    _sync_linked_issue_ledger(workspace, activated)
    return read_task(workspace, task_id=target_id)


def create_task(
    workspace: str | Path,
    title: str,
    objective: str,
    scope: list[str] | None = None,
    verification_target: str | None = None,
    verification_selectors: dict[str, Any] | None = None,
    verification_mode_default: str | None = None,
    branch_hint: str | None = None,
    linked_workstream_id: str | None = None,
    stage_id: str | None = None,
    external_issue: dict[str, Any] | None = None,
    codex_estimate_minutes: int | None = None,
    task_id: str | None = None,
    make_current: bool = True,
    sync_issue_ledger: bool = True,
) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    previous_task = _deactivate_current_task(workspace, next_status="planned") if make_current else None
    index = _load_tasks_index(paths)
    target_id = sanitize_identifier(task_id or title, f"task-{uuid.uuid4().hex[:6]}")
    if any(item.get("task_id") == target_id for item in index.get("items", [])):
        raise ValueError(f"Task already exists: {target_id}")
    task_paths = _task_paths(workspace, target_id)
    Path(task_paths["current_task_root"]).mkdir(parents=True, exist_ok=True)
    payload = _default_task_record(
        paths["workspace_path"],
        target_id,
        title=title,
        objective=objective,
        scope=scope,
        verification_target=verification_target,
        verification_selectors=verification_selectors,
        verification_mode_default=verification_mode_default,
        branch_hint=branch_hint,
        linked_workstream_id=linked_workstream_id,
        stage_id=stage_id,
        external_issue=external_issue,
        codex_estimate_minutes=codex_estimate_minutes,
    )
    if make_current:
        payload = _activate_task_payload(payload)
    _write_json(Path(task_paths["current_task_record"]), payload)
    _write_text(Path(task_paths["current_task_brief"]), TASK_BRIEF_PLACEHOLDER)
    _write_json(Path(task_paths["current_task_verification_summary"]), _default_verification_summary())
    index["items"].append(payload)
    if make_current:
        index["current_task_id"] = target_id
    _save_tasks_index(paths, index)
    state = read_workspace_state(workspace)
    if make_current:
        state["current_task_id"] = target_id
        state["workspace_mode"] = "task"
    _persist_workspace_state(workspace, state)
    if sync_issue_ledger:
        _sync_linked_issue_ledger(workspace, payload)
    return {
        "workspace_path": paths["workspace_path"],
        "created_task_id": target_id,
        "previous_task": previous_task,
        "task": read_task(workspace, task_id=target_id),
        "tasks": list_tasks(workspace),
    }


def read_task(workspace: str | Path, task_id: str | None = None) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    index = _load_tasks_index(paths)
    resolved_id = sanitize_identifier(task_id or index.get("current_task_id"), "")
    if not resolved_id:
        raise FileNotFoundError("No current task is selected.")
    task_paths = _task_paths(workspace, resolved_id)
    payload = _normalize_task_payload(
        _load_json(
        Path(task_paths["current_task_record"]),
        default={},
        strict=True,
        purpose=f"task record `{resolved_id}`",
    )
        or {}
    )
    if not payload:
        raise FileNotFoundError(f"Task does not exist: {resolved_id}")
    payload["task_brief_path"] = task_paths["current_task_brief"]
    payload["brief_path"] = task_paths["current_task_brief"]
    payload["verification_summary_path"] = task_paths["current_task_verification_summary"]
    payload["brief_markdown"] = _read_text(Path(task_paths["current_task_brief"]), default=TASK_BRIEF_PLACEHOLDER)
    payload["verification_summary"] = _load_json(
        Path(task_paths["current_task_verification_summary"]),
        default=_default_verification_summary(),
        strict=False,
    )
    payload["verification_selectors"] = payload.get("verification_selectors") or (
        {"explicit_targets": [payload["verification_target"]]} if payload.get("verification_target") else {}
    )
    payload["verification_mode_default"] = payload.get("verification_mode_default") or "targeted"
    tracking = _normalize_task_time_tracking(payload.get("time_tracking"))
    running_minutes = _session_minutes_between(tracking.get("active_session_started_at"), now_iso()) if tracking.get("active_session_started_at") else 0
    payload["time_tracking"] = {
        **tracking,
        "running_minutes": running_minutes,
        "local_total_minutes_including_running": tracking.get("local_total_minutes", 0) + running_minutes,
    }
    issue_ledger = None
    if payload.get("external_issue"):
        try:
            from agentiux_dev_youtrack import read_issue_ledger

            issue_ledger = read_issue_ledger(
                workspace,
                connection_id=payload["external_issue"]["connection_id"],
                issue_id=payload["external_issue"]["issue_id"],
            )
        except Exception:
            issue_ledger = None
    payload["issue_ledger"] = issue_ledger
    payload["time_summary"] = {
        "local_task_minutes": tracking.get("local_total_minutes", 0),
        "local_task_minutes_including_running": payload["time_tracking"]["local_total_minutes_including_running"],
        "aggregate_issue_minutes": (issue_ledger or {}).get("codex_total_minutes"),
        "youtrack_estimate_minutes": (issue_ledger or {}).get("youtrack_estimate_minutes"),
        "youtrack_spent_minutes": (issue_ledger or {}).get("youtrack_spent_minutes"),
        "codex_estimate_minutes": payload.get("codex_estimate_minutes") if payload.get("codex_estimate_minutes") is not None else (issue_ledger or {}).get("codex_estimate_minutes"),
    }
    return payload


def close_task(workspace: str | Path, task_id: str | None = None, verification_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    index = _load_tasks_index(paths)
    resolved_id = sanitize_identifier(task_id or index.get("current_task_id"), "")
    if not resolved_id:
        raise FileNotFoundError("No current task is selected.")
    record = _pause_task_payload(_task_record_by_id(index, resolved_id), next_status="completed")
    record["closed_at"] = now_iso()
    record["updated_at"] = now_iso()
    task_paths = _task_paths(workspace, resolved_id)
    payload = _default_verification_summary()
    payload.update(verification_summary or {})
    payload["status"] = payload.get("status") or "completed"
    payload["updated_at"] = now_iso()
    _write_json(Path(task_paths["current_task_verification_summary"]), payload)
    _persist_task_record(workspace, record)
    index = _load_tasks_index(paths)
    if index.get("current_task_id") == resolved_id:
        index["current_task_id"] = None
    _save_tasks_index(paths, index)
    state = read_workspace_state(workspace)
    if state.get("current_task_id") == resolved_id:
        state["current_task_id"] = None
        state["workspace_mode"] = "workstream" if state.get("current_workstream_id") else "workspace"
    _persist_workspace_state(workspace, state)
    _sync_linked_issue_ledger(workspace, record)
    return read_task(workspace, task_id=resolved_id)


def current_task(workspace: str | Path) -> dict[str, Any] | None:
    paths = _ensure_workspace_initialized(workspace)
    current_id = _load_tasks_index(paths).get("current_task_id")
    if not current_id:
        return None
    return read_task(workspace, task_id=current_id)


def _write_audit(paths: dict[str, str], audit: dict[str, Any]) -> dict[str, Any]:
    audit["generated_at"] = now_iso()
    _write_json(Path(paths["audits_root"]) / f"{audit['audit_id']}.json", audit)
    _write_json(Path(paths["current_audit"]), audit)
    return audit


def _write_upgrade_plan(paths: dict[str, str], plan: dict[str, Any]) -> dict[str, Any]:
    plan["updated_at"] = now_iso()
    _write_json(Path(paths["upgrade_plans_root"]) / f"{plan['plan_id']}.json", plan)
    _write_json(Path(paths["current_upgrade_plan"]), plan)
    return plan


def audit_repository(workspace: str | Path) -> dict[str, Any]:
    detection = detect_workspace(workspace)
    paths = detection["paths"]
    initialized = Path(paths["workspace_state"]).exists() or Path(paths["stage_register"]).exists()
    gaps: list[dict[str, Any]] = []

    compose_files = _compose_files(Path(detection["workspace_path"]))
    if detection.get("local_dev_policy", {}).get("infra_mode") in {"docker_required", "docker_optional"} and not compose_files:
        gaps.append(
            {
                "gap_id": "missing-dockerized-local-infra",
                "severity": "high",
                "category": "infra",
                "title": "Missing Dockerized local infra baseline",
                "recommendation": "Add Docker Compose for databases, caches, and brokers used in local development.",
            }
        )

    verification_recipes_path = Path(paths["verification_recipes"])
    recipes = _load_json(verification_recipes_path, default={}) if verification_recipes_path.exists() else {}
    if not recipes or not recipes.get("cases"):
        gaps.append(
            {
                "gap_id": "missing-deterministic-verification",
                "severity": "high",
                "category": "verification",
                "title": "Missing deterministic verification recipes",
                "recommendation": "Define at least one named case and one suite for the changed surface.",
            }
        )

    if not (Path(detection["workspace_path"]) / "README.md").exists():
        gaps.append(
            {
                "gap_id": "missing-project-docs",
                "severity": "medium",
                "category": "docs",
                "title": "Missing root project README",
                "recommendation": "Add project documentation covering local development and architecture truth.",
            }
        )

    handoff = {}
    if initialized:
        try:
            handoff = read_design_handoff(workspace)
        except Exception:  # noqa: BLE001
            handoff = {}
    if any(profile in detection["selected_profiles"] for profile in {"web-platform", "mobile-platform"}) and not handoff.get("verification_hooks"):
        gaps.append(
            {
                "gap_id": "missing-design-verification-hooks",
                "severity": "medium",
                "category": "design",
                "title": "Missing design verification hooks",
                "recommendation": "Persist stable routes, screens, masks, and target states in the design handoff.",
            }
        )

    if initialized:
        try:
            ws_paths = _ensure_workspace_initialized(workspace)
            if not Path(ws_paths["workstreams_index"]).exists() or not Path(ws_paths["tasks_index"]).exists():
                gaps.append(
                    {
                        "gap_id": "missing-workstream-task-readiness",
                        "severity": "medium",
                        "category": "workflow",
                        "title": "Workspace is missing workstream or task indexes",
                        "recommendation": "Migrate workspace state to workstreams and tasks.",
                    }
                )
        except Exception:  # noqa: BLE001
            gaps.append(
                {
                    "gap_id": "workspace-state-not-ready",
                    "severity": "medium",
                    "category": "workflow",
                    "title": "Workspace state is not fully initialized",
                    "recommendation": "Initialize the workspace and migrate it to the current schema.",
                }
            )
    else:
        gaps.append(
            {
                "gap_id": "workspace-not-initialized",
                "severity": "high",
                "category": "workflow",
                "title": "Workspace has not been initialized in AgentiUX Dev",
                "recommendation": "Initialize workspace state before continuing with staged workflow.",
            }
        )

    audit = _default_audit_payload(detection["workspace_path"])
    audit["initialized"] = initialized
    audit["detected_stacks"] = detection["detected_stacks"]
    audit["selected_profiles"] = detection["selected_profiles"]
    audit["gaps"] = gaps
    audit["recommended_playbooks"] = detection["available_upgrade_playbooks"]
    audit["notes"] = [
        "Audit is read-only for repo code.",
        "Apply upgrade plan only after explicit confirmation.",
    ]
    if initialized:
        _write_audit(_ensure_workspace_initialized(workspace), audit)
    return audit


def read_current_audit(workspace: str | Path) -> dict[str, Any] | None:
    try:
        paths = _ensure_workspace_initialized(workspace)
    except FileNotFoundError:
        return None
    path = Path(paths["current_audit"])
    if not path.exists():
        return None
    return _load_json(path, default=None, strict=True, purpose="current audit")


def show_upgrade_plan(workspace: str | Path) -> dict[str, Any]:
    try:
        paths = _ensure_workspace_initialized(workspace)
    except FileNotFoundError:
        paths = workspace_paths(workspace)
    audit = read_current_audit(workspace) or audit_repository(workspace)
    plan = _default_upgrade_plan(audit["workspace_path"], audit.get("audit_id"))
    items = []
    for gap in audit.get("gaps", []):
        items.append(
            {
                "gap_id": gap["gap_id"],
                "title": gap["title"],
                "mode": "task" if gap["category"] in {"docs", "design", "verification"} else "workstream",
                "recommended_title": gap["title"],
                "recommendation": gap["recommendation"],
            }
        )
    plan["items"] = items
    plan["status"] = "draft"
    if Path(paths["workspace_state"]).exists():
        _write_upgrade_plan(paths, plan)
    return plan


def read_upgrade_plan(workspace: str | Path) -> dict[str, Any] | None:
    try:
        paths = _ensure_workspace_initialized(workspace)
    except FileNotFoundError:
        return None
    path = Path(paths["current_upgrade_plan"])
    if not path.exists():
        return None
    return _load_json(path, default=None, strict=True, purpose="current upgrade plan")


def apply_upgrade_plan(workspace: str | Path, confirmed: bool = False) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("Upgrade plan application requires explicit confirmation.")
    paths = _ensure_workspace_initialized(workspace)
    plan = read_upgrade_plan(workspace) or show_upgrade_plan(workspace)
    if not plan.get("items"):
        return plan
    created_workstream_ids: list[str] = []
    created_tasks: list[str] = []
    for item in plan.get("items", []):
        if item.get("mode") == "workstream":
            workstream_result = create_workstream(
                workspace,
                title=item["recommended_title"],
                kind="upgrade",
                scope_summary=item.get("recommendation"),
                make_current=False,
            )
            created_workstream_ids.append(workstream_result["created_workstream_id"])
        else:
            task_result = create_task(
                workspace,
                title=item["recommended_title"],
                objective=item["recommendation"],
                make_current=False,
            )
            created_tasks.append(task_result["created_task_id"])
    plan["created_workstream_ids"] = created_workstream_ids
    plan["created_workstream_id"] = created_workstream_ids[0] if len(created_workstream_ids) == 1 else None
    plan["created_task_ids"] = created_tasks
    plan["status"] = "applied"
    _write_upgrade_plan(paths, plan)
    return plan


def list_starter_presets() -> dict[str, Any]:
    templates = _load_templates()
    presets = []
    for preset in STARTER_PRESETS.values():
        resolved = copy.deepcopy(preset)
        fragment_payload = _resolve_starter_fragments(resolved, templates)
        resolved["post_setup"] = fragment_payload["post_setup"]
        resolved["starter_post_setup_resolution"] = fragment_payload["starter_post_setup_resolution"]
        presets.append(resolved)
    return {
        "presets": presets,
    }


def _starter_run_record(preset: dict[str, Any], destination_root: Path, project_name: str) -> dict[str, Any]:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    stdout_log, stderr_log = _starter_logs(run_id)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "preset_id": preset["preset_id"],
        "display_name": preset["display_name"],
        "destination_root": str(destination_root),
        "project_name": project_name,
        "project_root": str((destination_root / project_name).resolve()),
        "status": "queued",
        "created_at": now_iso(),
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        "workspace_initialized": False,
        "summary": None,
    }


def _write_starter_run(run: dict[str, Any]) -> dict[str, Any]:
    path = _starter_run_path(run["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, run)
    return run


def _fill_bootstrap_placeholders(argv: list[str], destination_root: Path, project_name: str) -> list[str]:
    project_root = destination_root / project_name
    return [
        part.replace("__DESTINATION_ROOT__", str(destination_root)).replace("__PROJECT_NAME__", project_name).replace("__PROJECT_ROOT__", str(project_root))
        for part in argv
    ]


def _run_bootstrap_command(run: dict[str, Any], argv: list[str], cwd: Path) -> None:
    stdout_log = Path(run["stdout_log_path"])
    stderr_log = Path(run["stderr_log_path"])
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    with stdout_log.open("a") as stdout_handle, stderr_log.open("a") as stderr_handle:
        stdout_handle.write(f"\n=== START {' '.join(argv)} ===\n")
        stderr_handle.write(f"\n=== START {' '.join(argv)} ===\n")
        process = subprocess.run(argv, cwd=str(cwd), stdout=stdout_handle, stderr=stderr_handle, text=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"Starter bootstrap failed: {' '.join(argv)}")


def _ensure_starter_preflight(preset: dict[str, Any]) -> None:
    missing = [command for command in preset.get("required_commands", []) if shutil.which(command) is None]
    if missing:
        raise RuntimeError(f"Starter preflight failed. Missing commands: {', '.join(missing)}")


def _append_project_readme(project_root: Path, preset: dict[str, Any]) -> None:
    readme = project_root / "README.md"
    addition = textwrap.dedent(
        f"""\

        ## Local Development

        This project was initialized with the `{preset['preset_id']}` starter.

        - Keep supporting services in Docker for local development when the stack uses databases, caches, or brokers.
        - Keep deterministic verification recipes in external AgentiUX Dev state.
        - Update project docs when local-dev commands, architecture, or verification contracts change.
        """
    )
    if readme.exists():
        content = readme.read_text()
        if addition.strip() not in content:
            readme.write_text(content.rstrip() + "\n" + addition)
    else:
        readme.write_text(f"# {project_root.name}\n{addition}")


def _ensure_docker_compose(project_root: Path, preset: dict[str, Any]) -> None:
    if not preset.get("post_setup", {}).get("docker_compose"):
        return
    compose_path = project_root / "docker-compose.yml"
    if compose_path.exists():
        return
    compose_path.write_text(
        "services:\n"
        "  postgres:\n"
        "    image: postgres:16\n"
        "    ports:\n"
        "      - \"5432:5432\"\n"
        "  redis:\n"
        "    image: redis:7\n"
        "    ports:\n"
        "      - \"6379:6379\"\n"
    )


def _starter_verification_payload(project_root: Path, preset: dict[str, Any]) -> dict[str, Any]:
    profile = preset.get("post_setup", {}).get("verification_profile")
    detection = detect_workspace(project_root)
    templates = _load_templates()
    resolved = _resolve_verification_fragments(project_root, detection, templates, verification_profile=profile)
    payload = {
        "schema_version": 2,
        "baseline_policy": {
            "canonical_baselines": "project_owned",
            "transient_artifacts": "external_state_only",
        },
        "verification_fragment_resolution": resolved["verification_fragment_resolution"],
    }
    payload = _merge_fragment_value(payload, resolved["verification"])
    return payload


def _seed_starter_design(project_root: Path, preset: dict[str, Any]) -> None:
    platform = preset.get("post_setup", {}).get("design_platform")
    if not platform:
        return
    templates = _load_templates()
    scaffold_payload = _resolve_design_handoff_scaffold(platform, templates)
    write_design_brief(
        project_root,
        {
            "status": "briefed",
            "platform": platform,
            "surface": "starter-home",
            "style_goals": ["production-ready", "clear system"],
            "constraints": ["Keep deterministic verification hooks stable."],
        },
    )
    write_design_handoff(
        project_root,
        _merge_fragment_value(
            {
                "status": "ready",
                "platform": platform,
                "verification_hooks": [
                    "route:/",
                    "screen:home",
                    "mask:dynamic-zones",
                ],
                "design_handoff_scaffold_resolution": scaffold_payload["design_handoff_scaffold_resolution"],
            },
            scaffold_payload["handoff"],
        ),
    )


def create_starter(
    preset_id: str,
    destination_root: str | Path,
    project_name: str,
    force: bool = False,
) -> dict[str, Any]:
    preset = next((item for item in list_starter_presets()["presets"] if item["preset_id"] == preset_id), None)
    if not preset:
        raise ValueError(f"Unknown starter preset: {preset_id}")
    destination = Path(destination_root).expanduser().resolve()
    project_root = destination / project_name
    if project_root.exists() and not force:
        raise ValueError(f"Starter destination already exists: {project_root}")
    destination.mkdir(parents=True, exist_ok=True)
    run = _starter_run_record(preset, destination, project_name)
    _write_starter_run(run)
    try:
        _ensure_starter_preflight(preset)
        run["status"] = "running"
        _write_starter_run(run)
        for command in preset.get("bootstrap_commands", []):
            argv = _fill_bootstrap_placeholders(command["argv"], destination, project_name)
            cwd = Path(command["cwd"].replace("__DESTINATION_ROOT__", str(destination)).replace("__PROJECT_ROOT__", str(project_root)))
            _run_bootstrap_command(run, argv, cwd)
        run["status"] = "passed"
        run["completed_at"] = now_iso()
        run["summary"] = {
            "project_root": str(project_root),
            "preset_id": preset_id,
            "starter_post_setup_resolution": preset.get("starter_post_setup_resolution"),
            "recommended_next_steps": [
                "Review the generated project before initializing AgentiUX Dev workspace state.",
                "Choose the workstream or task structure with the user before persisting stage plans or design state.",
            ],
        }
    except Exception as exc:  # noqa: BLE001
        run["status"] = "failed"
        run["completed_at"] = now_iso()
        run["summary"] = {"error": str(exc)}
        _write_starter_run(run)
        raise
    _write_starter_run(run)
    return run


def list_starter_runs(limit: int | None = 10) -> dict[str, Any]:
    runs = []
    for run_path in sorted(_starter_runs_root().glob("*/run.json"), reverse=True):
        payload = _load_json(run_path, default={}, strict=False) or {}
        if payload:
            runs.append(payload)
    recent = runs[:limit] if limit is not None else runs
    return {
        "run_count": len(runs),
        "runs": recent,
    }


def _empty_verification_runs_payload(workspace: str | Path) -> dict[str, Any]:
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": None,
        "run_count": 0,
        "runs": [],
        "recent_runs": [],
        "active_run": None,
        "latest_run": None,
        "latest_completed_run": None,
    }


def _empty_recent_verification_events_payload(workspace: str | Path) -> dict[str, Any]:
    return {
        "workspace_path": str(Path(workspace).expanduser().resolve()),
        "workstream_id": None,
        "run_id": None,
        "events": [],
    }


def workspace_summary(workspace: str | Path) -> dict[str, Any]:
    from agentiux_dev_verification import list_verification_runs, recent_verification_events, resolve_verification_selection
    from agentiux_dev_youtrack import workspace_youtrack_summary

    paths = _ensure_workspace_initialized(workspace)
    workspace_state = read_workspace_state(workspace)
    counts = _workspace_counts(paths)
    task = current_task(workspace)
    has_workstream = bool(workspace_state.get("current_workstream_id"))
    workstream = current_workstream(workspace) if has_workstream else None
    verification_runs = list_verification_runs(workspace, limit=5) if has_workstream else _empty_verification_runs_payload(workspace)
    active_run = verification_runs["active_run"]
    latest_run = verification_runs["latest_run"]
    latest_completed_run = verification_runs["latest_completed_run"]
    recent_events = recent_verification_events(workspace, limit=5) if has_workstream else _empty_recent_verification_events_payload(workspace)
    register = workstream["register"] if workstream else None
    brief = get_active_brief(workspace) if (task or workstream) else {"markdown": ""}
    board = read_reference_board(workspace, workstream_id=workspace_state["current_workstream_id"]) if workstream else None
    handoff = read_design_handoff(workspace, workstream_id=workspace_state["current_workstream_id"]) if workstream else None
    verification_selection = resolve_verification_selection(workspace) if (task or workstream) else None
    return {
        "workspace_path": paths["workspace_path"],
        "workspace_label": workspace_state.get("workspace_label"),
        "workspace_slug": workspace_state.get("workspace_slug"),
        "host_os": workspace_state.get("host_os"),
        "support_warnings": workspace_state.get("support_warnings", []),
        "detected_stacks": workspace_state.get("detected_stacks", []),
        "selected_profiles": workspace_state.get("selected_profiles", []),
        "plugin_platform": workspace_state.get("plugin_platform", {"enabled": False}),
        "local_dev_policy": workspace_state.get("local_dev_policy"),
        "planning_policy": workspace_state.get("planning_policy"),
        "state_repair_status": workspace_state.get("state_repair_status"),
        "workspace_mode": workspace_state.get("workspace_mode"),
        "current_workstream_id": workspace_state.get("current_workstream_id"),
        "current_task_id": workspace_state.get("current_task_id"),
        "plan_status": register.get("plan_status") if register else None,
        "current_stage": register.get("current_stage") if register else None,
        "stage_status": register.get("stage_status") if register else None,
        "last_completed_stage": register.get("last_completed_stage") if register else None,
        "current_slice": register.get("current_slice") if register else None,
        "next_task": task.get("objective") if task else (register.get("next_task") if register else None),
        "blockers": register.get("blockers", []) if register else [],
        "updated_at": workspace_state.get("updated_at"),
        "summary_counts": counts,
        "active_brief_preview": brief["markdown"].strip().splitlines()[:4],
        "design": {
            "brief_status": read_design_brief(workspace).get("status") if workstream else None,
            "current_board_title": board.get("title") if board else None,
            "current_board_candidates": len(board.get("candidates", [])) if board else 0,
            "current_handoff_status": handoff.get("status") if handoff else None,
            "verification_hooks": len(handoff.get("verification_hooks", [])) if handoff else 0,
        },
        "workstream": {
            "workstream_id": workstream.get("workstream_id") if workstream else None,
            "title": workstream.get("title") if workstream else None,
            "kind": workstream.get("kind") if workstream else None,
            "branch_hint": workstream.get("branch_hint") if workstream else None,
            "status": workstream.get("status") if workstream else None,
        },
        "task": {
            "task_id": task.get("task_id") if task else None,
            "title": task.get("title") if task else None,
            "status": task.get("status") if task else None,
            "linked_workstream_id": task.get("linked_workstream_id") if task else None,
            "stage_id": task.get("stage_id") if task else None,
            "external_issue": copy.deepcopy(task.get("external_issue")) if task else None,
            "latest_commit": copy.deepcopy(task.get("latest_commit")) if task else None,
        },
        "youtrack": workspace_youtrack_summary(workspace),
        "verification": {
            "active_run_id": active_run.get("run_id") if active_run else None,
            "active_run_status": active_run.get("status") if active_run else None,
            "active_run_health": active_run.get("health") if active_run else None,
            "latest_run_id": latest_run.get("run_id") if latest_run else None,
            "latest_run_status": latest_run.get("status") if latest_run else None,
            "latest_completed_run_id": latest_completed_run.get("run_id") if latest_completed_run else None,
            "recent_events": recent_events.get("events", []),
            "selection": verification_selection,
        },
    }


def list_workspaces() -> dict[str, Any]:
    registry = _load_registry()
    workspace_states = sorted((state_root() / "workspaces").glob("*/workspace.json"))
    workspaces = []
    for state_file in workspace_states:
        payload = _load_json(state_file, default={}) or {}
        workspace_path = payload.get("workspace_path")
        if not workspace_path:
            continue
        try:
            workspaces.append(workspace_summary(workspace_path))
        except FileNotFoundError:
            continue
    workspaces.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {
        "plugin": plugin_info(),
        "registry_updated_at": registry.get("updated_at"),
        "workspace_count": len(workspaces),
        "workspaces": workspaces,
    }


def plugin_stats() -> dict[str, Any]:
    workspaces = list_workspaces()["workspaces"]
    return _plugin_stats_from_workspaces(workspaces)


def _plugin_stats_from_workspaces(workspaces: list[dict[str, Any]]) -> dict[str, Any]:
    blocked = sum(1 for workspace in workspaces if workspace["stage_status"] == "blocked")
    ready = sum(1 for workspace in workspaces if workspace["stage_status"] == "ready_for_closeout")
    plugin_platform_workspaces = sum(1 for workspace in workspaces if workspace.get("plugin_platform", {}).get("enabled"))
    gui_runtime = read_gui_runtime()
    return {
        "plugin": plugin_info(),
        "workspace_count": len(workspaces),
        "blocked_workspaces": blocked,
        "ready_for_closeout_workspaces": ready,
        "artifact_files": sum(workspace["summary_counts"]["artifact_files"] for workspace in workspaces),
        "reference_boards": sum(workspace["summary_counts"]["reference_boards"] for workspace in workspaces),
        "design_handoffs": sum(workspace["summary_counts"]["design_handoffs"] for workspace in workspaces),
        "active_verification_runs": sum(workspace["summary_counts"]["active_verification_runs"] for workspace in workspaces),
        "failed_verification_runs": sum(workspace["summary_counts"]["failed_verification_runs"] for workspace in workspaces),
        "plugin_platform_workspaces": plugin_platform_workspaces,
        "starter_runs": list_starter_runs(limit=None)["run_count"],
        "gui_status": gui_runtime.get("status", "stopped"),
        "generated_at": now_iso(),
    }


def _dashboard_workspace_summary(workspace: str | Path) -> dict[str, Any]:
    paths = _ensure_workspace_initialized(workspace)
    workspace_state = read_workspace_state(workspace)
    counts = _workspace_counts(paths)
    register = None
    workstream_id = workspace_state.get("current_workstream_id")
    if workstream_id:
        try:
            register = read_stage_register(workspace, workstream_id=workstream_id)
        except Exception:  # noqa: BLE001
            register = None
    return {
        "workspace_path": paths["workspace_path"],
        "workspace_label": workspace_state.get("workspace_label"),
        "workspace_slug": workspace_state.get("workspace_slug"),
        "workspace_mode": workspace_state.get("workspace_mode"),
        "current_workstream_id": workspace_state.get("current_workstream_id"),
        "current_task_id": workspace_state.get("current_task_id"),
        "updated_at": workspace_state.get("updated_at"),
        "plan_status": register.get("plan_status") if register else None,
        "current_stage": register.get("current_stage") if register else None,
        "stage_status": register.get("stage_status") if register else None,
        "summary_counts": counts,
        "plugin_platform": workspace_state.get("plugin_platform", {"enabled": False}),
    }


def list_dashboard_workspaces() -> dict[str, Any]:
    registry = _load_registry()
    workspace_states = sorted((state_root() / "workspaces").glob("*/workspace.json"))
    workspaces = []
    for state_file in workspace_states:
        payload = _load_json(state_file, default={}) or {}
        workspace_path = payload.get("workspace_path")
        if not workspace_path:
            continue
        try:
            workspaces.append(_dashboard_workspace_summary(workspace_path))
        except FileNotFoundError:
            continue
    workspaces.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {
        "plugin": plugin_info(),
        "registry_updated_at": registry.get("updated_at"),
        "workspace_count": len(workspaces),
        "workspaces": workspaces,
    }


def _dashboard_task_card(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not task:
        return None
    return {
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "linked_workstream_id": task.get("linked_workstream_id"),
        "stage_id": task.get("stage_id"),
    }


def _dashboard_task_list(workspace: str | Path) -> dict[str, Any]:
    tasks = list_tasks(workspace)
    return {
        "workspace_path": tasks["workspace_path"],
        "current_task_id": tasks.get("current_task_id"),
        "items": [card for card in (_dashboard_task_card(item) for item in tasks.get("items", [])) if card],
    }


def _dashboard_starter_runs(workspace: str | Path) -> list[dict[str, Any]]:
    workspace_path = str(Path(workspace).expanduser().resolve())
    return [
        {
            "run_id": run.get("run_id"),
            "preset_id": run.get("preset_id"),
            "status": run.get("status"),
            "project_root": run.get("project_root"),
        }
        for run in list_starter_runs(limit=20)["runs"]
        if run.get("workspace_initialized") and run.get("project_root") == workspace_path
    ]


def dashboard_overview_snapshot() -> dict[str, Any]:
    overview = list_dashboard_workspaces()
    return {
        "schema_version": 2,
        "generated_at": now_iso(),
        "plugin": plugin_info(),
        "stats": _plugin_stats_from_workspaces(overview["workspaces"]),
        "gui": read_gui_runtime(),
        "overview": overview,
        "starter_runs": list_starter_runs(limit=10),
        "workspace_detail": None,
    }


def read_workspace_dashboard_detail(workspace: str | Path | None) -> dict[str, Any] | None:
    from agentiux_dev_verification import (
        list_verification_runs,
        read_verification_log_tail,
        read_verification_recipes,
        recent_verification_events,
        resolve_verification_selection,
    )
    from agentiux_dev_youtrack import workspace_youtrack_dashboard_detail

    if not workspace:
        return None
    paths = _ensure_workspace_initialized(workspace)
    workspace_state = read_workspace_state(workspace)
    has_workstream = bool(workspace_state.get("current_workstream_id"))
    current_task_payload = current_task(workspace)
    verification_runs = list_verification_runs(workspace, limit=10) if has_workstream else _empty_verification_runs_payload(workspace)
    current_log_run = verification_runs["active_run"] or verification_runs["latest_run"]
    return {
        "summary": workspace_summary(workspace),
        "paths": get_state_paths(workspace)["paths"],
        "workspace_state": workspace_state,
        "workstreams": list_workstreams(workspace),
        "tasks": _dashboard_task_list(workspace),
        "current_task": _dashboard_task_card(current_task_payload),
        "stage_register": read_stage_register(workspace) if has_workstream else None,
        "design_brief": read_design_brief(workspace) if has_workstream else None,
        "current_reference_board": read_reference_board(workspace) if has_workstream else None,
        "current_design_handoff": read_design_handoff(workspace) if has_workstream else None,
        "verification_recipes": read_verification_recipes(workspace) if has_workstream else None,
        "verification_selection": resolve_verification_selection(workspace) if (has_workstream or current_task_payload) else None,
        "verification_runs": verification_runs,
        "latest_verification_run": verification_runs["latest_run"],
        "latest_completed_verification_run": verification_runs["latest_completed_run"],
        "recent_verification_events": recent_verification_events(workspace, limit=12) if has_workstream else _empty_recent_verification_events_payload(workspace),
        "active_verification_stdout": read_verification_log_tail(workspace, current_log_run["run_id"], "stdout", 20) if current_log_run else None,
        "active_verification_stderr": read_verification_log_tail(workspace, current_log_run["run_id"], "stderr", 20) if current_log_run else None,
        "active_verification_logcat": read_verification_log_tail(workspace, current_log_run["run_id"], "logcat", 20) if current_log_run else None,
        "current_audit": read_current_audit(workspace),
        "current_upgrade_plan": read_upgrade_plan(workspace),
        "youtrack": workspace_youtrack_dashboard_detail(workspace),
        "recent_starter_runs": _dashboard_starter_runs(workspace),
    }


def dashboard_snapshot(workspace: str | Path | None = None) -> dict[str, Any]:
    overview = list_workspaces()
    selected_workspace = None
    if workspace:
        selected_workspace = str(Path(workspace).expanduser().resolve())
    elif overview["workspaces"]:
        selected_workspace = overview["workspaces"][0]["workspace_path"]

    detail = read_workspace_detail(selected_workspace) if selected_workspace else None
    return {
        "schema_version": 2,
        "generated_at": now_iso(),
        "plugin": plugin_info(),
        "stats": plugin_stats(),
        "gui": read_gui_runtime(),
        "overview": overview,
        "starter_runs": list_starter_runs(limit=10),
        "workspace_detail": detail,
    }


def read_workspace_detail(workspace: str | Path | None) -> dict[str, Any] | None:
    from agentiux_dev_verification import (
        audit_verification_coverage,
        list_verification_runs,
        read_verification_log_tail,
        read_verification_recipes,
        recent_verification_events,
        resolve_verification_selection,
    )
    from agentiux_dev_youtrack import workspace_youtrack_detail

    if not workspace:
        return None
    paths = _ensure_workspace_initialized(workspace)
    workspace_state = read_workspace_state(workspace)
    has_workstream = bool(workspace_state.get("current_workstream_id"))
    current_task_payload = current_task(workspace)
    verification_runs = list_verification_runs(workspace, limit=10) if has_workstream else _empty_verification_runs_payload(workspace)
    current_log_run = verification_runs["active_run"] or verification_runs["latest_run"]
    return {
        "summary": workspace_summary(workspace),
        "paths": get_state_paths(workspace)["paths"],
        "workspace_state": workspace_state,
        "workstreams": list_workstreams(workspace),
        "current_workstream": current_workstream(workspace) if has_workstream else None,
        "tasks": list_tasks(workspace),
        "current_task": current_task_payload,
        "stage_register": read_stage_register(workspace) if has_workstream else None,
        "active_brief": get_active_brief(workspace) if (has_workstream or current_task_payload) else None,
        "design_brief": read_design_brief(workspace) if has_workstream else None,
        "reference_boards": list_reference_boards(workspace) if has_workstream else {"workspace_path": paths["workspace_path"], "items": []},
        "current_reference_board": read_reference_board(workspace) if has_workstream else None,
        "design_handoffs": list_design_handoffs(workspace) if has_workstream else {"workspace_path": paths["workspace_path"], "items": []},
        "current_design_handoff": read_design_handoff(workspace) if has_workstream else None,
        "verification_recipes": read_verification_recipes(workspace) if has_workstream else None,
        "verification_selection": resolve_verification_selection(workspace) if (has_workstream or current_task_payload) else None,
        "verification_runs": verification_runs,
        "active_verification_run": verification_runs["active_run"],
        "latest_verification_run": verification_runs["latest_run"],
        "latest_completed_verification_run": verification_runs["latest_completed_run"],
        "recent_verification_events": recent_verification_events(workspace, limit=12) if has_workstream else _empty_recent_verification_events_payload(workspace),
        "active_verification_stdout": read_verification_log_tail(workspace, current_log_run["run_id"], "stdout", 20) if current_log_run else None,
        "active_verification_stderr": read_verification_log_tail(workspace, current_log_run["run_id"], "stderr", 20) if current_log_run else None,
        "active_verification_logcat": read_verification_log_tail(workspace, current_log_run["run_id"], "logcat", 20) if current_log_run else None,
        "verification_coverage_audit": audit_verification_coverage(workspace),
        "current_audit": read_current_audit(workspace),
        "current_upgrade_plan": read_upgrade_plan(workspace),
        "youtrack": workspace_youtrack_detail(workspace),
        "recent_starter_runs": [run for run in list_starter_runs(limit=20)["runs"] if run.get("workspace_initialized") and run.get("project_root") == str(Path(workspace).expanduser().resolve())],
        "gui_runtime_path": paths["gui_runtime"],
    }


def text_result(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
