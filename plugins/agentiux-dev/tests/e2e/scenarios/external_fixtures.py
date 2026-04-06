from __future__ import annotations

from typing import Any

from support.runtime import ExecutionContext, ScenarioDefinition

from agentiux_dev_e2e_support import REPO_FIXTURES, run_external_fixture_suite


GROUP_KEY = "external-fixtures"


FIXTURE_CASE_MAP = {
    "fullstack-workspace": "external-fixture-playwright-reset",
    "mobile-detox-app": "external-fixture-detox-native-audit",
    "android-compose-lab": "external-fixture-compose-native-audit",
}


def _external_group(context: ExecutionContext) -> dict[str, Any]:
    payload = context.group(
        f"{GROUP_KEY}/suite",
        lambda _: run_external_fixture_suite(context.plugin_root, context.path("external-fixtures-suite")),
    )
    results_by_fixture = {item["fixture_id"]: item for item in payload["results"]}
    return {"payload": payload, "results_by_fixture": results_by_fixture}


def _case_playwright(context: ExecutionContext) -> dict[str, Any]:
    group = _external_group(context)
    payload = group["payload"]
    result = group["results_by_fixture"]["fullstack-workspace"]
    assert payload["status"] == "passed"
    assert result["verification_status"] == "passed"
    assert result["semantic_status"] == "passed"
    assert result["reset_removed_context_cache_root"] is True
    return result


def _case_detox(context: ExecutionContext) -> dict[str, Any]:
    group = _external_group(context)
    result = group["results_by_fixture"]["mobile-detox-app"]
    assert result["verification_status"] == "passed"
    assert result["native_layout_status"] == "passed"
    return result


def _case_compose(context: ExecutionContext) -> dict[str, Any]:
    group = _external_group(context)
    result = group["results_by_fixture"]["android-compose-lab"]
    assert result["verification_status"] == "passed"
    assert result["native_layout_status"] == "passed"
    return result


def register() -> dict[str, ScenarioDefinition]:
    return {
        "external-fixture-playwright-reset": ScenarioDefinition(
            case_id="external-fixture-playwright-reset",
            fixture_id="fullstack-workspace",
            suite_ids=("external-fixtures", "core-full-local"),
            tags=("external", "playwright", "reset"),
            run=_case_playwright,
            required_env_roots=("state", "install", "marketplace"),
        ),
        "external-fixture-detox-native-audit": ScenarioDefinition(
            case_id="external-fixture-detox-native-audit",
            fixture_id="mobile-detox-app",
            suite_ids=("external-fixtures", "core-full-local"),
            tags=("external", "detox", "native-layout"),
            run=_case_detox,
            required_env_roots=("state", "install", "marketplace"),
        ),
        "external-fixture-compose-native-audit": ScenarioDefinition(
            case_id="external-fixture-compose-native-audit",
            fixture_id="android-compose-lab",
            suite_ids=("external-fixtures", "core-full-local"),
            tags=("external", "compose", "native-layout"),
            run=_case_compose,
            required_env_roots=("state", "install", "marketplace"),
        ),
    }

