#!/usr/bin/env python3
"""Prometheus exporter for the local WSL qwen/llama-server stack."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable


DEFAULT_QWEN_URL = "http://127.0.0.1:8080"
DEFAULT_PIDFILE = "/tmp/qwen-8080.pid"
DEFAULT_LOG = str(Path.home() / ".local/state/qwen-8080.log")


def metric(name: str, value: float | int, help_text: str, labels: dict[str, str] | None = None) -> str:
    label_text = ""
    if labels:
        encoded = ",".join(f'{k}="{str(v).replace("\\", "\\\\").replace("\"", "\\\"")}"' for k, v in sorted(labels.items()))
        label_text = "{" + encoded + "}"
    return f"{name}{label_text} {value}\n"


def counter_metric(name: str, value: float | int, help_text: str, labels: dict[str, str] | None = None) -> str:
    label_text = ""
    if labels:
        encoded = ",".join(f'{k}="{str(v).replace("\\", "\\\\").replace("\"", "\\\"")}"' for k, v in sorted(labels.items()))
        label_text = "{" + encoded + "}"
    return f"{name}{label_text} {value}\n"


def get_json(url: str, timeout: float) -> tuple[int, object | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else None, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        return exc.code, parsed, body
    except Exception as exc:
        return 0, None, str(exc)


def read_pid(pidfile: str) -> int | None:
    try:
        text = Path(pidfile).read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except Exception:
        return None


def process_running(pid: int | None) -> bool:
    if pid is None:
        return False
    return Path(f"/proc/{pid}").exists()


def process_start_time_seconds(pid: int | None) -> float:
    if pid is None:
        return 0.0
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        start_ticks = int(stat.split()[21])
        clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        boot_time = 0
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                boot_time = int(line.split()[1])
                break
        return boot_time + (start_ticks / clock_ticks)
    except Exception:
        return 0.0


def meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            parts = raw.strip().split()
            if parts:
                values[key] = int(parts[0]) * 1024
    except Exception:
        pass
    return values


def loadavg() -> tuple[float, float, float]:
    try:
        one, five, fifteen, *_ = Path("/proc/loadavg").read_text(encoding="utf-8").split()
        return float(one), float(five), float(fifteen)
    except Exception:
        return 0.0, 0.0, 0.0


def disk_metrics(paths: Iterable[str]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        try:
            usage = shutil.disk_usage(path)
        except Exception:
            continue
        labels = {"path": path}
        lines.append(metric("qwen_disk_total_bytes", usage.total, "Filesystem size in bytes.", labels))
        lines.append(metric("qwen_disk_used_bytes", usage.used, "Filesystem used bytes.", labels))
        lines.append(metric("qwen_disk_free_bytes", usage.free, "Filesystem free bytes.", labels))
        pct = (usage.used / usage.total * 100.0) if usage.total else 0.0
        lines.append(metric("qwen_disk_used_percent", pct, "Filesystem used percent.", labels))
    return lines


def nvidia_smi_metrics() -> list[str]:
    query = [
        "name",
        "memory.total",
        "memory.used",
        "memory.free",
        "utilization.gpu",
        "utilization.memory",
        "temperature.gpu",
        "power.draw",
        "power.limit",
        "clocks.gr",
        "clocks.mem",
    ]
    cmd = [
        "nvidia-smi",
        f"--query-gpu={','.join(query)}",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=3)
    except Exception:
        return [metric("qwen_gpu_nvidia_smi_up", 0, "Whether nvidia-smi was readable.")]

    lines = [metric("qwen_gpu_nvidia_smi_up", 1, "Whether nvidia-smi was readable.")]
    reader = csv.reader(result.stdout.splitlines())
    for index, row in enumerate(reader):
        if len(row) < len(query):
            continue
        row = [cell.strip() for cell in row]
        labels = {"gpu": str(index), "name": row[0]}
        numeric = {
            "qwen_gpu_memory_total_bytes": float(row[1]) * 1024 * 1024,
            "qwen_gpu_memory_used_bytes": float(row[2]) * 1024 * 1024,
            "qwen_gpu_memory_free_bytes": float(row[3]) * 1024 * 1024,
            "qwen_gpu_utilization_percent": float(row[4]),
            "qwen_gpu_memory_utilization_percent": float(row[5]),
            "qwen_gpu_temperature_celsius": float(row[6]),
            "qwen_gpu_power_draw_watts": float(row[7]),
            "qwen_gpu_power_limit_watts": float(row[8]),
            "qwen_gpu_graphics_clock_mhz": float(row[9]),
            "qwen_gpu_memory_clock_mhz": float(row[10]),
        }
        for name, value in numeric.items():
            lines.append(metric(name, value, name.replace("_", " ").rstrip("."), labels))
        used = numeric["qwen_gpu_memory_used_bytes"]
        total = numeric["qwen_gpu_memory_total_bytes"]
        lines.append(metric("qwen_gpu_memory_used_percent", used / total * 100.0 if total else 0.0, "GPU memory used percent.", labels))
    return lines


def slot_metrics(slots: object) -> list[str]:
    if not isinstance(slots, list):
        return []
    lines = [metric("qwen_llama_slots_total", len(slots), "Number of llama-server slots.")]
    for idx, slot in enumerate(slots):
        labels = {"slot": str(slot.get("id", idx))} if isinstance(slot, dict) else {"slot": str(idx)}
        if not isinstance(slot, dict):
            continue
        for src, dst in [
            ("n_ctx", "qwen_llama_slot_context_total_tokens"),
            ("n_past", "qwen_llama_slot_context_used_tokens"),
            ("n_prompt_tokens", "qwen_llama_slot_prompt_tokens"),
            ("n_decoded", "qwen_llama_slot_decoded_tokens"),
        ]:
            value = slot.get(src)
            if isinstance(value, (int, float)):
                lines.append(metric(dst, value, dst.replace("_", " "), labels))
        n_ctx = slot.get("n_ctx")
        n_past = slot.get("n_past")
        if isinstance(n_ctx, (int, float)) and n_ctx:
            used = float(n_past or 0)
            lines.append(metric("qwen_llama_slot_context_used_percent", used / float(n_ctx) * 100.0, "Slot context used percent.", labels))
        state = str(slot.get("state", "unknown"))
        lines.append(metric("qwen_llama_slot_state", 1, "Slot state label.", {**labels, "state": state}))
    return lines


def log_metrics(log_path: str) -> list[str]:
    path = Path(log_path)
    if not path.exists():
        return [metric("qwen_log_present", 0, "Whether the qwen log file exists.")]
    lines = [metric("qwen_log_present", 1, "Whether the qwen log file exists.")]
    try:
        stat = path.stat()
        lines.append(metric("qwen_log_size_bytes", stat.st_size, "Qwen log file size."))
        lines.append(metric("qwen_log_modified_time_seconds", stat.st_mtime, "Qwen log modification timestamp."))
        tail = path.read_bytes()[-65536:].decode("utf-8", errors="replace").lower()
        lines.append(counter_metric("qwen_log_recent_error_lines_total", tail.count(" error") + tail.count(" e "), "Approximate recent error-line count in the log tail."))
        lines.append(counter_metric("qwen_log_recent_warning_lines_total", tail.count(" warning") + tail.count(" w "), "Approximate recent warning-line count in the log tail."))
    except Exception:
        pass
    return lines


def collect(args: argparse.Namespace) -> str:
    lines: list[str] = []
    now = time.time()
    lines.append(metric("qwen_exporter_up", 1, "Whether qwen_exporter is running."))
    lines.append(metric("qwen_exporter_scrape_time_seconds", now, "Exporter scrape Unix timestamp."))
    lines.append(metric("qwen_host_info", 1, "Host identity.", {"hostname": socket.gethostname()}))

    status, health_body, _ = get_json(f"{args.qwen_url}/health", args.timeout)
    health_up = 1 if status == 200 else 0
    loading = 1 if status == 503 and isinstance(health_body, dict) and "loading" in json.dumps(health_body).lower() else 0
    lines.append(metric("qwen_llama_health_up", health_up, "Whether llama-server health is OK."))
    lines.append(metric("qwen_llama_loading", loading, "Whether llama-server reports model loading."))
    lines.append(metric("qwen_llama_health_http_status", status, "HTTP status returned by /health."))

    status, slots_body, _ = get_json(f"{args.qwen_url}/slots", args.timeout)
    lines.append(metric("qwen_llama_slots_http_status", status, "HTTP status returned by /slots."))
    lines.extend(slot_metrics(slots_body))

    pid = read_pid(args.pidfile)
    running = process_running(pid)
    lines.append(metric("qwen_process_running", 1 if running else 0, "Whether the qwen PID is running."))
    lines.append(metric("qwen_process_pid", pid or 0, "Current qwen PID from pidfile."))
    start_time = process_start_time_seconds(pid)
    lines.append(metric("qwen_process_start_time_seconds", start_time, "Process start timestamp."))
    lines.append(metric("qwen_process_uptime_seconds", max(0.0, now - start_time) if start_time else 0, "Process uptime in seconds."))

    mem = meminfo()
    for key in ["MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached", "SwapTotal", "SwapFree"]:
        if key in mem:
            lines.append(metric(f"qwen_system_{key.lower()}_bytes", mem[key], f"/proc/meminfo {key}."))
    if mem.get("MemTotal"):
        used = mem["MemTotal"] - mem.get("MemAvailable", 0)
        lines.append(metric("qwen_system_memory_used_bytes", used, "Memory used based on MemTotal - MemAvailable."))
        lines.append(metric("qwen_system_memory_used_percent", used / mem["MemTotal"] * 100.0, "Memory used percent."))
    if mem.get("SwapTotal"):
        swap_used = mem["SwapTotal"] - mem.get("SwapFree", 0)
        lines.append(metric("qwen_system_swap_used_bytes", swap_used, "Swap used bytes."))
        lines.append(metric("qwen_system_swap_used_percent", swap_used / mem["SwapTotal"] * 100.0, "Swap used percent."))

    one, five, fifteen = loadavg()
    lines.append(metric("qwen_system_load1", one, "1 minute system load average."))
    lines.append(metric("qwen_system_load5", five, "5 minute system load average."))
    lines.append(metric("qwen_system_load15", fifteen, "15 minute system load average."))
    lines.extend(disk_metrics(args.disk_path))
    lines.extend(nvidia_smi_metrics())
    lines.extend(log_metrics(args.log))
    return "".join(lines)


class Handler(BaseHTTPRequestHandler):
    args: argparse.Namespace

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        body = collect(self.args).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("QWEN_EXPORTER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QWEN_EXPORTER_PORT", "9108")))
    parser.add_argument("--qwen-url", default=os.environ.get("QWEN_URL", DEFAULT_QWEN_URL))
    parser.add_argument("--pidfile", default=os.environ.get("QWEN_PIDFILE", DEFAULT_PIDFILE))
    parser.add_argument("--log", default=os.environ.get("QWEN_LOG", DEFAULT_LOG))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("QWEN_EXPORTER_TIMEOUT", "1.5")))
    parser.add_argument("--disk-path", action="append", default=["/", "/mnt/c"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    Handler.args = args
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"qwen_exporter listening on http://{args.host}:{args.port}/metrics", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
