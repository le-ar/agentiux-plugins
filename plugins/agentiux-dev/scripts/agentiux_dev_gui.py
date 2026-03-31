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

from agentiux_dev_lib import (
    dashboard_overview_snapshot,
    get_state_paths,
    gui_runtime_path,
    now_iso,
    plugin_info,
    process_status,
    read_workspace_dashboard_detail,
    read_gui_runtime,
    state_root,
    start_logged_python_process,
    stop_process,
)
from agentiux_dev_verification import audit_verification_coverage
from agentiux_dev_youtrack import (
    connect_youtrack,
    list_youtrack_connections,
    remove_youtrack_connection,
    test_youtrack_connection,
    update_youtrack_connection,
)


def dashboard_root() -> Path:
    return Path(__file__).resolve().parents[1] / "dashboard"


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


def _wait_for_health(url: str, timeout_seconds: float = 5.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError(f"GUI did not become ready at {url}")


def launch(host: str, port: int | None, workspace: str | None) -> dict[str, Any]:
    with _runtime_lock():
        current = _status_unlocked()
        if current.get("status") == "running":
            if workspace and current.get("default_workspace") != workspace:
                updated = dict(current)
                updated["default_workspace"] = workspace
                updated["updated_at"] = now_iso()
                _write_runtime(updated)
                return updated
            return current

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

        _wait_for_health(url)
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
        )
        _write_runtime(payload)
        return payload


def stop() -> dict[str, Any]:
    with _runtime_lock():
        payload = _status_unlocked()
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
        )
        _write_runtime(stopped)
        return stopped


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

            if parsed.path == "/health":
                self._send_json({"ok": True, "generated_at": now_iso()})
                return
            if parsed.path == "/api/dashboard":
                self._send_json(dashboard_overview_snapshot())
                return
            if parsed.path == "/api/workspace-detail":
                self._send_json(read_workspace_dashboard_detail(workspace))
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

    subparsers.add_parser("stop")
    subparsers.add_parser("status")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "serve":
        return serve(args.host, args.port, args.workspace)
    if args.command == "launch":
        print(json.dumps(launch(args.host, args.port, args.workspace), indent=2))
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
