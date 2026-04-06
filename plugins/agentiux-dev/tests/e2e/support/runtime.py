from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from typing import Any, Callable


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))


GroupLoader = Callable[["ExecutionContext"], Any]
ScenarioRunner = Callable[["ExecutionContext"], dict[str, Any] | None]


@dataclass(frozen=True)
class ScenarioDefinition:
    case_id: str
    fixture_id: str
    suite_ids: tuple[str, ...]
    tags: tuple[str, ...]
    run: ScenarioRunner
    required_env_roots: tuple[str, ...] = ("state",)
    cleanup_policy: str = "delete_temp_run_root"
    include_in_core_full_local: bool = True


@dataclass
class ExecutionContext:
    keep_run_root: bool = False
    run_root: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="agentiux-dev-wave1-")))
    _group_values: dict[str, Any] = field(default_factory=dict)
    _group_errors: dict[str, BaseException] = field(default_factory=dict)

    @property
    def plugin_root(self) -> Path:
        return PLUGIN_ROOT

    @property
    def scripts_root(self) -> Path:
        return SCRIPTS_ROOT

    def path(self, *parts: str) -> Path:
        target = self.run_root.joinpath(*parts)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def group(self, key: str, loader: GroupLoader) -> Any:
        if key in self._group_errors:
            raise self._group_errors[key]
        if key in self._group_values:
            return self._group_values[key]
        try:
            value = loader(self)
        except BaseException as exc:  # noqa: BLE001
            self._group_errors[key] = exc
            raise
        self._group_values[key] = value
        return value

    def cleanup(self) -> None:
        if self.keep_run_root:
            return
        shutil.rmtree(self.run_root, ignore_errors=True)


def failure_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exc(),
    }

