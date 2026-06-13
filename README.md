# AI Stack Home Server Dashboard

Local Prometheus + Grafana for the WSL qwen stack.

![AI Stack Home Server Grafana dashboard](docs/assets/qwen-dashboard.png)

## Overview

- Grafana for qwen, GPU, RAM, disk, and load
- `qwen_exporter.py` on `127.0.0.1:9108`
- `qwen_control.py` on `127.0.0.1:9110`
- Prometheus scrape and alert rules
- systemd user units for WSL autostart

The exporter stays read-only and does not export prompts, responses, API keys, or request bodies.

## Setup

```bash
mkdir -p ~/.config/systemd/user
cp systemd/qwen-dashboard.service ~/.config/systemd/user/
cp systemd/qwen-exporter.service ~/.config/systemd/user/
cp systemd/qwen-control.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now qwen-dashboard qwen-exporter qwen-control
systemctl --user disable qwen
```

If you want qwen inference running too:

```bash
qwenctl restart
```

Open:

- Grafana: `http://127.0.0.1:3000/d/qwen-stack/ai-stack-home-server`
- Control UI: `http://127.0.0.1:9110`
- Prometheus: `http://127.0.0.1:9090`
- Exporter metrics: `http://127.0.0.1:9108/metrics`

Grafana login is `admin` / `admin` with anonymous viewer access on localhost.

## Qwen Control

The dashboard includes a `Qwen Control` tile that opens the local control UI.
It binds to localhost, uses a per-machine CSRF token, and logs actions to `~/.local/state/qwen-control.log`.

## Validation

```bash
uv run python -m py_compile exporter/qwen_exporter.py control/qwen_control.py
uv run python exporter/qwen_exporter.py --port 19108
uv run python control/qwen_control.py --port 19110
docker compose config
```

If Docker cannot access the daemon from WSL, start Docker Desktop or fix access to `/var/run/docker.sock`, then rerun `docker compose up -d`.
