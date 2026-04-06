#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import mimetypes
import os
import re
import socket
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from agentiux_dev_analytics import get_analytics_snapshot, get_learning_entry, list_learning_entries, update_learning_entry, write_learning_entry
from agentiux_dev_auth import (
    get_auth_session,
    invalidate_auth_session,
    list_auth_sessions,
    remove_auth_profile,
    remove_auth_session,
    resolve_auth_profile,
    show_auth_profiles,
    write_auth_profile,
    write_auth_session,
)
from agentiux_dev_lib import (
    dashboard_bootstrap_snapshot,
    dashboard_overview_snapshot,
    get_state_paths,
    gui_runtime_path,
    now_iso,
    plugin_info,
    process_status,
    read_workspace_dashboard_detail,
    read_workspace_dashboard_panel_snapshot,
    read_gui_runtime,
    state_root,
    start_logged_python_process,
    stop_process,
)
from agentiux_dev_memory import archive_project_note, get_project_note, list_project_notes, search_project_notes, write_project_note
from agentiux_dev_verification import audit_verification_coverage
from agentiux_dev_youtrack import (
    connect_youtrack,
    list_youtrack_connections,
    remove_youtrack_connection,
    test_youtrack_connection,
    update_youtrack_connection,
)

WATCHED_DASHBOARD_SUFFIXES = {".py", ".js", ".css", ".html"}


def dashboard_root() -> Path:
    return Path(__file__).resolve().parents[1] / "dashboard"


def _script_root() -> Path:
    return Path(__file__).resolve().parent


def _write_runtime(payload: dict[str, Any]) -> None:
    path = gui_runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _lock_path() -> Path:
    return gui_runtime_path().with_suffix(".lock")


@contextmanager
def _runtime_lock() -> Any:
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        if os.name == "nt":
            if lock_path.stat().st_size == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _process_running(pid: int | None) -> bool:
    return process_status(pid).get("running", False)


def _runtime_payload(status: str, **kwargs: Any) -> dict[str, Any]:
    payload = {
        "status": status,
        "runtime_path": str(gui_runtime_path()),
        "updated_at": now_iso(),
        "plugin": plugin_info(),
    }
    payload.update(kwargs)
    return payload


def _dashboard_source_state() -> dict[str, Any]:
    current_root = Path(str(plugin_info()["current_root"])).resolve()
    latest_path = None
    latest_mtime_ns = 0
    file_count = 0
    for root in (dashboard_root(), _script_root()):
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_file() or candidate.suffix.lower() not in WATCHED_DASHBOARD_SUFFIXES:
                continue
            file_count += 1
            try:
                mtime_ns = candidate.stat().st_mtime_ns
            except OSError:
                continue
            if mtime_ns >= latest_mtime_ns:
                latest_mtime_ns = mtime_ns
                try:
                    latest_path = str(candidate.resolve().relative_to(current_root))
                except ValueError:
                    latest_path = candidate.name
    signature = f"{latest_mtime_ns}:{file_count}:{latest_path or 'none'}"
    return {
        "current_root": str(current_root),
        "latest_path": latest_path,
        "latest_mtime_ns": latest_mtime_ns,
        "file_count": file_count,
        "signature": signature,
    }


def _runtime_restart_reasons(
    current: dict[str, Any],
    *,
    desired_host: str,
    desired_port: int | None,
    expected_source_state: dict[str, Any],
    force_restart: bool,
) -> list[str]:
    reasons: list[str] = []
    current_plugin_root = str(((current.get("plugin") or {}).get("current_root")) or "")
    if current_plugin_root != expected_source_state["current_root"]:
        reasons.append("plugin_root_changed")
    current_source_state = current.get("source_state") or {}
    if current_source_state.get("signature") != expected_source_state["signature"]:
        reasons.append("source_changed")
    if desired_host and current.get("host") and current.get("host") != desired_host:
        reasons.append("host_changed")
    if desired_port is not None and current.get("port") and current.get("port") != desired_port:
        reasons.append("port_changed")
    if force_restart:
        reasons.append("forced")
    return list(dict.fromkeys(reasons))


def _status_unlocked() -> dict[str, Any]:
    payload = read_gui_runtime()
    pid = payload.get("pid")
    running = _process_running(pid if isinstance(pid, int) else None)
    if running:
        payload["status"] = "running"
        return payload
    if payload.get("status") == "running":
        stopped = _runtime_payload(
            "stopped",
            last_url=payload.get("url"),
            last_pid=payload.get("pid"),
            log_path=payload.get("log_path"),
            error_log_path=payload.get("error_log_path"),
        )
        _write_runtime(stopped)
        return stopped
    return payload


def status() -> dict[str, Any]:
    with _runtime_lock():
        return _status_unlocked()


def _find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _tail_log(path: str | Path | None, *, lines: int = 20) -> str:
    if not path:
        return ""
    try:
        log_path = Path(path)
        if not log_path.exists():
            return ""
        return "\n".join(log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:])
    except Exception:  # noqa: BLE001
        return ""


def _wait_for_health(
    url: str,
    timeout_seconds: float = 20.0,
    *,
    process: Any | None = None,
    log_path: str | Path | None = None,
    error_log_path: str | Path | None = None,
) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if process is not None and callable(getattr(process, "poll", None)) and process.poll() is not None:
                break
            time.sleep(0.15)
    details: list[str] = []
    if process is not None and callable(getattr(process, "poll", None)) and process.poll() is not None:
        details.append(f"process exited with code {process.returncode}")
    if last_error is not None:
        details.append(f"last health error: {last_error}")
    stdout_tail = _tail_log(log_path)
    stderr_tail = _tail_log(error_log_path)
    if stdout_tail:
        details.append(f"stdout tail:\n{stdout_tail}")
    if stderr_tail:
        details.append(f"stderr tail:\n{stderr_tail}")
    message = f"GUI did not become ready at {url}"
    if details:
        message += "\n" + "\n".join(details)
    raise RuntimeError(message)


def _stop_unlocked(payload: dict[str, Any]) -> dict[str, Any]:
    pid = payload.get("pid")
    if payload.get("status") != "running" or not isinstance(pid, int):
        return payload
    stop_payload = stop_process(pid)
    time.sleep(0.2)
    stopped = _runtime_payload(
        "stopped",
        last_pid=pid,
        last_url=payload.get("url"),
        log_path=payload.get("log_path"),
        error_log_path=payload.get("error_log_path"),
        stopped_at=now_iso(),
        process_stop=stop_payload,
        plugin=payload.get("plugin") or plugin_info(),
        source_state=payload.get("source_state"),
    )
    _write_runtime(stopped)
    return stopped


def launch(host: str, port: int | None, workspace: str | None, *, force_restart: bool = False) -> dict[str, Any]:
    with _runtime_lock():
        current = _status_unlocked()
        expected_source_state = _dashboard_source_state()
        if current.get("status") == "running":
            restart_reasons = _runtime_restart_reasons(
                current,
                desired_host=host,
                desired_port=port,
                expected_source_state=expected_source_state,
                force_restart=force_restart,
            )
            if not restart_reasons:
                updated = dict(current)
                if workspace and current.get("default_workspace") != workspace:
                    updated["default_workspace"] = workspace
                    updated["updated_at"] = now_iso()
                updated["launch_action"] = "reused"
                updated["restart_reasons"] = []
                _write_runtime(updated)
                return updated
            current = _stop_unlocked(current)
            host = current.get("host") or host
            if port is None:
                port = current.get("port")
            workspace = workspace or current.get("default_workspace")
            launch_action = "restarted"
        else:
            restart_reasons = []
            launch_action = "started"

        runtime_dir = gui_runtime_path().parent
        runtime_dir.mkdir(parents=True, exist_ok=True)
        chosen_port = port or _find_free_port(host)
        url = f"http://{host}:{chosen_port}"
        log_path = runtime_dir / "dashboard.log"
        error_log_path = runtime_dir / "dashboard.err.log"

        env = os.environ.copy()
        env.setdefault("AGENTIUX_DEV_PLUGIN_ROOT", str(Path(__file__).resolve().parents[1]))
        script_args = [
            "serve",
            "--host",
            host,
            "--port",
            str(chosen_port),
        ]
        if workspace:
            script_args.extend(["--workspace", workspace])
        process = start_logged_python_process(
            Path(__file__).resolve(),
            log_path,
            error_log_path,
            script_args=script_args,
            env=env,
            start_new_session=True,
        )

        _wait_for_health(
            url,
            process=process,
            log_path=log_path,
            error_log_path=error_log_path,
        )
        payload = _runtime_payload(
            "running",
            pid=process.pid,
            host=host,
            port=chosen_port,
            url=url,
            default_workspace=workspace,
            log_path=str(log_path),
            error_log_path=str(error_log_path),
            started_at=now_iso(),
            source_state=expected_source_state,
            launch_action=launch_action,
            restart_reasons=restart_reasons,
        )
        _write_runtime(payload)
        return payload


def stop() -> dict[str, Any]:
    with _runtime_lock():
        payload = _status_unlocked()
        return _stop_unlocked(payload)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AgentiUXDevDashboard/0.7.0"

    def _runtime_default_workspace(self) -> str | None:
        payload = read_gui_runtime()
        workspace = payload.get("default_workspace")
        return workspace if isinstance(workspace, str) and workspace else None

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _resolve_workspace(self, parsed: urllib.parse.ParseResult, body: dict[str, Any] | None = None) -> str | None:
        query = urllib.parse.parse_qs(parsed.query)
        body_workspace = (body or {}).get("workspacePath")
        return body_workspace or query.get("workspace", [None])[0] or self._runtime_default_workspace()

    def _send_file(self, path: Path) -> None:
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            workspace = query.get("workspace", [None])[0] or self._runtime_default_workspace()
            panel = query.get("panel", ["now"])[0]
            force_overview = query.get("forceOverview", ["0"])[0] in {"1", "true", "yes"}

            if parsed.path == "/health":
                self._send_json({"ok": True, "generated_at": now_iso()})
                return
            if parsed.path == "/api/dashboard":
                self._send_json(dashboard_overview_snapshot())
                return
            if parsed.path == "/api/dashboard-bootstrap":
                self._send_json(
                    dashboard_bootstrap_snapshot(
                        workspace,
                        panel=panel,
                        force_overview=force_overview,
                    )
                )
                return
            if parsed.path in {"/api/workspace-cockpit", "/api/workspace-detail"}:
                self._send_json(read_workspace_dashboard_detail(workspace))
                return
            if parsed.path == "/api/workspace-panel":
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                self._send_json(read_workspace_dashboard_panel_snapshot(workspace, panel=panel))
                return
            if parsed.path == "/api/verification-coverage":
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                self._send_json(audit_verification_coverage(workspace))
                return
            if parsed.path == "/api/state-paths":
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                self._send_json(get_state_paths(workspace))
                return
            if parsed.path == "/api/youtrack/connections":
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                self._send_json(list_youtrack_connections(workspace))
                return
            if parsed.path == "/api/auth/profiles":
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                self._send_json(show_auth_profiles(workspace))
                return
            if parsed.path == "/api/auth/sessions":
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                profile_id = query.get("profileId", [None])[0] or query.get("profile_id", [None])[0]
                self._send_json(list_auth_sessions(workspace, profile_id=profile_id))
                return
            session_match = re.match(r"^/api/auth/sessions/([^/]+)$", parsed.path)
            if session_match:
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                self._send_json(get_auth_session(workspace, urllib.parse.unquote(session_match.group(1))))
                return
            if parsed.path == "/api/project-notes":
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                note_status = query.get("status", [None])[0]
                self._send_json(list_project_notes(workspace, status=note_status))
                return
            if parsed.path == "/api/project-notes/search":
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                query_text = query.get("query", [None])[0] or query.get("queryText", [None])[0]
                if not query_text:
                    self._send_json({"error": "query parameter is required"}, status=400)
                    return
                limit = int(query.get("limit", ["8"])[0])
                self._send_json(search_project_notes(workspace, query_text, limit=limit))
                return
            note_match = re.match(r"^/api/project-notes/([^/]+)$", parsed.path)
            if note_match:
                if not workspace:
                    self._send_json({"error": "workspace query parameter is required"}, status=400)
                    return
                self._send_json(get_project_note(workspace, urllib.parse.unquote(note_match.group(1))))
                return
            if parsed.path == "/api/analytics":
                self._send_json(get_analytics_snapshot(workspace))
                return
            if parsed.path == "/api/learnings":
                status = query.get("status", [None])[0]
                limit = int(query.get("limit", ["50"])[0])
                self._send_json(list_learning_entries(workspace=workspace, status=status, limit=limit))
                return
            learning_match = re.match(r"^/api/learnings/([^/]+)$", parsed.path)
            if learning_match:
                self._send_json(get_learning_entry(urllib.parse.unquote(learning_match.group(1)), workspace=workspace))
                return

            dashboard_dir = dashboard_root()
            relative_path = parsed.path.lstrip("/") or "index.html"
            candidate = (dashboard_dir / relative_path).resolve()
            if candidate.is_file() and str(candidate).startswith(str(dashboard_dir.resolve())):
                self._send_file(candidate)
                return

            self._send_file(dashboard_dir / "index.html")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_json(
                {
                    "error": "Dashboard request failed",
                    "detail": str(exc),
                    "path": self.path,
                },
                status=500,
            )

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlparse(self.path)
            body = self._read_json_body()
            workspace = self._resolve_workspace(parsed, body)
            if not workspace:
                self._send_json({"error": "workspacePath is required"}, status=400)
                return
            if parsed.path == "/api/youtrack/connections":
                self._send_json(
                    connect_youtrack(
                        workspace,
                        base_url=body["baseUrl"],
                        token=body["token"],
                        label=body.get("label"),
                        connection_id=body.get("connectionId"),
                        project_scope=body.get("projectScope"),
                        default=body.get("default", False),
                        test_connection=body.get("testConnection", True),
                    )
                )
                return
            match = re.match(r"^/api/youtrack/connections/([^/]+)/test$", parsed.path)
            if match:
                self._send_json(test_youtrack_connection(workspace, urllib.parse.unquote(match.group(1))))
                return
            if parsed.path == "/api/auth/profiles":
                self._send_json(
                    write_auth_profile(
                        workspace,
                        body["profile"],
                        secret_payload=body.get("secretPayload"),
                    )
                )
                return
            if parsed.path == "/api/auth/profiles/resolve":
                self._send_json(
                    resolve_auth_profile(
                        workspace,
                        profile_id=body.get("profileId"),
                        task_id=body.get("taskId"),
                        external_issue=body.get("externalIssue"),
                        case=body.get("case"),
                        workstream_id=body.get("workstreamId"),
                        request_mode=body.get("requestMode"),
                        action_tags=body.get("actionTags"),
                        session_binding=body.get("sessionBinding"),
                        context_overrides=body.get("contextOverrides"),
                        prefer_cached=body.get("preferCached", True),
                        force_refresh=body.get("forceRefresh", False),
                        surface_mode="dashboard",
                    )
                )
                return
            if parsed.path == "/api/auth/sessions":
                self._send_json(
                    write_auth_session(
                        workspace,
                        body["session"],
                        secret_payload=body.get("secretPayload"),
                    )
                )
                return
            match = re.match(r"^/api/auth/sessions/([^/]+)/invalidate$", parsed.path)
            if match:
                self._send_json(invalidate_auth_session(workspace, urllib.parse.unquote(match.group(1))))
                return
            if parsed.path == "/api/project-notes":
                self._send_json(write_project_note(workspace, body["note"]))
                return
            match = re.match(r"^/api/project-notes/([^/]+)/archive$", parsed.path)
            if match:
                self._send_json(archive_project_note(workspace, urllib.parse.unquote(match.group(1))))
                return
            if parsed.path == "/api/learnings":
                self._send_json(write_learning_entry(workspace, body["entry"]))
                return
            self._send_json({"error": f"Unsupported POST path: {parsed.path}"}, status=404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_json({"error": "Dashboard request failed", "detail": str(exc), "path": self.path}, status=500)

    def do_PATCH(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlparse(self.path)
            body = self._read_json_body()
            workspace = self._resolve_workspace(parsed, body)
            if not workspace:
                self._send_json({"error": "workspacePath is required"}, status=400)
                return
            if parsed.path != "/api/youtrack/connections":
                if parsed.path == "/api/auth/profiles":
                    self._send_json(
                        write_auth_profile(
                            workspace,
                            body["profile"],
                            secret_payload=body.get("secretPayload"),
                        )
                    )
                    return
                if parsed.path == "/api/auth/sessions":
                    self._send_json(
                        write_auth_session(
                            workspace,
                            body["session"],
                            secret_payload=body.get("secretPayload"),
                        )
                    )
                    return
                match = re.match(r"^/api/project-notes/([^/]+)$", parsed.path)
                if match:
                    note_id = urllib.parse.unquote(match.group(1))
                    note_payload = body.get("note") if isinstance(body.get("note"), dict) else body
                    self._send_json(write_project_note(workspace, {**note_payload, "note_id": note_id}))
                    return
                match = re.match(r"^/api/learnings/([^/]+)$", parsed.path)
                if match:
                    entry_id = urllib.parse.unquote(match.group(1))
                    updates = body.get("updates") if isinstance(body.get("updates"), dict) else body
                    self._send_json(update_learning_entry(workspace, entry_id, updates))
                    return
                self._send_json({"error": f"Unsupported PATCH path: {parsed.path}"}, status=404)
                return
            self._send_json(
                update_youtrack_connection(
                    workspace,
                    body["connectionId"],
                    base_url=body.get("baseUrl"),
                    token=body.get("token"),
                    label=body.get("label"),
                    project_scope=body.get("projectScope"),
                    default=body.get("default"),
                    test_connection=body.get("testConnection", True),
                )
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_json({"error": "Dashboard request failed", "detail": str(exc), "path": self.path}, status=500)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlparse(self.path)
            body = self._read_json_body()
            workspace = self._resolve_workspace(parsed, body)
            if not workspace:
                self._send_json({"error": "workspacePath is required"}, status=400)
                return
            if parsed.path != "/api/youtrack/connections":
                if parsed.path == "/api/auth/profiles":
                    self._send_json(remove_auth_profile(workspace, body["profileId"]))
                    return
                match = re.match(r"^/api/auth/sessions/([^/]+)$", parsed.path)
                if match:
                    self._send_json(remove_auth_session(workspace, urllib.parse.unquote(match.group(1))))
                    return
                self._send_json({"error": f"Unsupported DELETE path: {parsed.path}"}, status=404)
                return
            self._send_json(remove_youtrack_connection(workspace, body["connectionId"]))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_json({"error": "Dashboard request failed", "detail": str(exc), "path": self.path}, status=500)


def serve(host: str, port: int, workspace: str | None) -> int:
    runtime = read_gui_runtime()
    payload = _runtime_payload(
        "running",
        pid=os.getpid(),
        host=host,
        port=port,
        url=f"http://{host}:{port}",
        default_workspace=workspace or runtime.get("default_workspace"),
        started_at=runtime.get("started_at") or now_iso(),
        log_path=runtime.get("log_path"),
        error_log_path=runtime.get("error_log_path"),
        source_state=_dashboard_source_state(),
    )
    _write_runtime(payload)
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AgentiUX Dev local dashboard launcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument("--workspace")

    launch_parser = subparsers.add_parser("launch")
    launch_parser.add_argument("--host", default="127.0.0.1")
    launch_parser.add_argument("--port", type=int)
    launch_parser.add_argument("--workspace")
    launch_parser.add_argument("--restart", action="store_true")

    subparsers.add_parser("stop")
    subparsers.add_parser("status")
    restart_parser = subparsers.add_parser("restart")
    restart_parser.add_argument("--host", default="127.0.0.1")
    restart_parser.add_argument("--port", type=int)
    restart_parser.add_argument("--workspace")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "serve":
        return serve(args.host, args.port, args.workspace)
    if args.command == "launch":
        print(json.dumps(launch(args.host, args.port, args.workspace, force_restart=args.restart), indent=2))
        return 0
    if args.command == "restart":
        print(json.dumps(launch(args.host, args.port, args.workspace, force_restart=True), indent=2))
        return 0
    if args.command == "stop":
        print(json.dumps(stop(), indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(status(), indent=2))
        return 0
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
