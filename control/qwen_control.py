#!/usr/bin/env python3
"""Local web control for qwenctl/systemd qwen actions."""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_QWEN_URL = "http://127.0.0.1:8080"
DEFAULT_QWENCTL = str(Path.home() / "bin/qwenctl")
DEFAULT_TOKEN_FILE = str(Path.home() / ".local/state/qwen-control-token")
DEFAULT_LOG = str(Path.home() / ".local/state/qwen-control.log")

ACTION_LOCK = threading.Lock()
VALID_ACTIONS = {"start", "stop", "restart"}


def ensure_token(path: str) -> str:
    token_path = Path(path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if token_path.exists():
        return token_path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    token_path.write_text(token + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    return token


def append_log(path: str, message: str) -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def run_cmd(args: list[str], timeout: int = 60) -> dict[str, Any]:
    started = time.time()
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "cmd": args,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "duration_seconds": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": args,
            "returncode": 124,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": "command timed out",
            "duration_seconds": round(time.time() - started, 3),
        }


def get_health(qwen_url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{qwen_url}/health", timeout=1.5) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {"http_status": response.status, "ok": response.status == 200, "body": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"http_status": exc.code, "ok": False, "body": body}
    except Exception as exc:
        return {"http_status": 0, "ok": False, "body": str(exc)}


def pid_from_file(path: str) -> int | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except Exception:
        return None


def status(args: argparse.Namespace) -> dict[str, Any]:
    pid = pid_from_file(args.pidfile)
    return {
        "health": get_health(args.qwen_url),
        "pid": pid,
        "pid_running": bool(pid and Path(f"/proc/{pid}").exists()),
        "systemd_active": run_cmd(["systemctl", "--user", "is-active", "qwen"], timeout=5),
        "timestamp": time.time(),
    }


def run_action(action: str, args: argparse.Namespace) -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        return {"ok": False, "error": f"invalid action: {action}"}
    if not ACTION_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "another qwen action is already running"}
    try:
        append_log(args.log, f"action={action} start")
        commands: list[list[str]]
        if action == "start":
            commands = [["systemctl", "--user", "start", "qwen"]]
        elif action == "stop":
            commands = [
                ["systemctl", "--user", "stop", "qwen"],
                [args.qwenctl, "stop"],
            ]
        else:
            commands = [["systemctl", "--user", "restart", "qwen"]]

        results = [run_cmd(command, timeout=args.action_timeout) for command in commands]
        ok = all(result["returncode"] in (0, 3) for result in results)
        current = status(args)
        append_log(args.log, f"action={action} ok={ok} status={current['health']['http_status']}")
        return {"ok": ok, "action": action, "commands": results, "status": current}
    finally:
        ACTION_LOCK.release()


def page(token: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Qwen Control</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; background: #111827; color: #e5e7eb; }}
    main {{ max-width: 760px; margin: 0 auto; padding: 28px; }}
    h1 {{ font-size: 22px; margin: 0 0 18px; }}
    .status {{ border: 1px solid #374151; padding: 16px; border-radius: 8px; background: #0f172a; }}
    .row {{ display: flex; justify-content: space-between; gap: 18px; padding: 6px 0; border-bottom: 1px solid #1f2937; }}
    .row:last-child {{ border-bottom: 0; }}
    .actions {{ display: flex; gap: 10px; margin: 18px 0; flex-wrap: wrap; }}
    button {{ border: 1px solid #475569; border-radius: 7px; padding: 10px 14px; color: #f8fafc; background: #1f2937; cursor: pointer; font-weight: 650; }}
    button.start {{ background: #166534; }}
    button.stop {{ background: #991b1b; }}
    button.restart {{ background: #854d0e; }}
    button:disabled {{ opacity: 0.45; cursor: wait; }}
    pre {{ white-space: pre-wrap; background: #020617; border: 1px solid #334155; border-radius: 8px; padding: 12px; min-height: 120px; }}
    .muted {{ color: #94a3b8; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <h1>Qwen Control</h1>
    <section class="status" id="status">Loading...</section>
    <div class="actions">
      <button class="start" data-action="start">Start</button>
      <button class="stop" data-action="stop">Stop</button>
      <button class="restart" data-action="restart">Restart</button>
    </div>
    <p class="muted">Actions run only on localhost through systemd/qwenctl and are serialized.</p>
    <pre id="log"></pre>
  </main>
  <script>
    const token = "{html.escape(token)}";
    const statusEl = document.getElementById("status");
    const logEl = document.getElementById("log");
    const buttons = [...document.querySelectorAll("button[data-action]")];

    function setBusy(busy) {{
      buttons.forEach((button) => button.disabled = busy);
    }}

    function renderStatus(data) {{
      const health = data.health || {{}};
      statusEl.innerHTML = `
        <div class="row"><strong>Health</strong><span>${{health.ok ? "UP" : "DOWN / LOADING"}}</span></div>
        <div class="row"><strong>HTTP</strong><span>${{health.http_status ?? "n/a"}}</span></div>
        <div class="row"><strong>PID</strong><span>${{data.pid ?? "none"}}</span></div>
        <div class="row"><strong>PID running</strong><span>${{data.pid_running ? "yes" : "no"}}</span></div>
        <div class="row"><strong>systemd</strong><span>${{(data.systemd_active && data.systemd_active.stdout) || "unknown"}}</span></div>
      `;
    }}

    async function refresh() {{
      const response = await fetch("/api/status");
      renderStatus(await response.json());
    }}

    async function act(action) {{
      setBusy(true);
      logEl.textContent = `Running ${{action}}...`;
      try {{
        const response = await fetch(`/api/action/${{action}}`, {{
          method: "POST",
          headers: {{"X-Qwen-Control-Token": token}},
        }});
        const data = await response.json();
        logEl.textContent = JSON.stringify(data, null, 2);
        if (data.status) renderStatus(data.status);
      }} finally {{
        setBusy(false);
      }}
    }}

    buttons.forEach((button) => button.addEventListener("click", () => act(button.dataset.action)));
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    args: argparse.Namespace
    token: str

    def send_json(self, payload: dict[str, Any], code: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path == "/api/status":
            self.send_json(status(self.args))
            return
        if self.path in ("/", "/index.html"):
            body = page(self.token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.headers.get("X-Qwen-Control-Token") != self.token:
            self.send_json({"ok": False, "error": "forbidden"}, code=403)
            return
        prefix = "/api/action/"
        if not self.path.startswith(prefix):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        action = self.path[len(prefix):]
        self.send_json(run_action(action, self.args))

    def do_OPTIONS(self) -> None:
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("QWEN_CONTROL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QWEN_CONTROL_PORT", "9110")))
    parser.add_argument("--qwen-url", default=os.environ.get("QWEN_URL", DEFAULT_QWEN_URL))
    parser.add_argument("--qwenctl", default=os.environ.get("QWENCTL", DEFAULT_QWENCTL))
    parser.add_argument("--pidfile", default=os.environ.get("QWEN_PIDFILE", "/tmp/qwen-8080.pid"))
    parser.add_argument("--token-file", default=os.environ.get("QWEN_CONTROL_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    parser.add_argument("--log", default=os.environ.get("QWEN_CONTROL_LOG", DEFAULT_LOG))
    parser.add_argument("--action-timeout", type=int, default=int(os.environ.get("QWEN_CONTROL_ACTION_TIMEOUT", "120")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    Handler.args = args
    Handler.token = ensure_token(args.token_file)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"qwen_control listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
