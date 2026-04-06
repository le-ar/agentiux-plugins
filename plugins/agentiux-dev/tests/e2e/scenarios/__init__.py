from __future__ import annotations

from dataclasses import replace

from . import codex_cli, dashboard, external_fixtures, foundation, integration, kernel, knowledge, self_host, verification_auth, workflow


def _augment_suite_aliases(registry: dict[str, object]) -> dict[str, object]:
    augmented = {}
    for case_id, definition in registry.items():
        suite_ids = list(definition.suite_ids)
        if "core-full-local" in suite_ids or "wave2-full-local" in suite_ids:
            suite_ids.append("catalog-implemented-local")
        augmented[case_id] = replace(definition, suite_ids=tuple(dict.fromkeys(suite_ids)))
    return augmented


def build_registry():
    registry = {}
    for module in (kernel, workflow, verification_auth, dashboard, integration, external_fixtures, foundation, knowledge, self_host, codex_cli):
        registry.update(module.register())
    return _augment_suite_aliases(registry)
