# AI Stack Home Server Dashboard

Local Prometheus + Grafana for the WSL qwen stack.

![AI Stack Home Server Grafana dashboard](docs/assets/qwen-dashboard.png)

## Includes

- Grafana dashboard for qwen, GPU, RAM, disk, and load
- `qwen_exporter.py` for WSL and NVIDIA metrics on `127.0.0.1:9108`
- `qwen_control.py` for Start/Stop/Restart on `127.0.0.1:9110`
- Prometheus scrape and alert rules
- systemd user units for dashboard autostart

The exporter does not export prompts, responses, API keys, or request bodies.

## What It Shows

- qwen health, loading state, PID, uptime, and log hints
- llama-server metrics from `/metrics` and `/slots`
- RTX VRAM, GPU utilization, temperature, power, and clocks
- WSL RAM, swap, load average, and disk usage for `/` and `/mnt/c`

## Start

Install the user units once:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/qwen-dashboard.service ~/.config/systemd/user/
cp systemd/qwen-exporter.service ~/.config/systemd/user/
cp systemd/qwen-control.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

Start the dashboard stack:

```bash
systemctl --user enable --now qwen-dashboard qwen-exporter qwen-control
```

If you want qwen itself running too:

```bash
qwenctl restart
```

Open:

- Grafana: `http://127.0.0.1:3000/d/qwen-stack/ai-stack-home-server`
- Control UI: `http://127.0.0.1:9110`
- Prometheus: `http://127.0.0.1:9090`
- Exporter metrics: `http://127.0.0.1:9108/metrics`

Grafana login is `admin` / `admin` with anonymous viewer access on localhost.

## Autostart

```bash
systemctl --user disable qwen
```

This keeps Grafana, Prometheus, the exporter, and the control UI starting with WSL.
It leaves qwen inference disabled until you start it manually.

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
