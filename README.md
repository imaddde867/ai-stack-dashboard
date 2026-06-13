# AI Stack Home Server Dashboard

Local Prometheus + Grafana dashboard for the WSL qwen/llama-server stack.

## What It Watches

- qwen health, loading state, PID, uptime, log size, recent warning/error hints
- llama-server native Prometheus metrics from `http://127.0.0.1:8080/metrics`
- slot/context telemetry from `http://127.0.0.1:8080/slots`
- RTX GPU VRAM, utilization, temperature, power, clocks from `nvidia-smi`
- WSL RAM, swap, load average, and disk usage for `/` and `/mnt/c`

The exporter does not export prompts, responses, API keys, or request bodies.

## Files

- `exporter/qwen_exporter.py` exposes local WSL/GPU/qwen metrics on `127.0.0.1:9108`.
- `control/qwen_control.py` exposes a local Qwen control UI on `127.0.0.1:9110`.
- `prometheus/prometheus.yml` scrapes llama-server and the exporter.
- `prometheus/alerts.yml` defines local alert rules.
- `grafana/dashboards/qwen-stack.json` is provisioned automatically.
- `systemd/qwen-exporter.service` is an optional user service.
- `systemd/qwen-control.service` is an optional user service for Start/Stop/Restart.
- `systemd/qwen-dashboard.service` starts the Prometheus/Grafana containers at WSL login.

## Start

Start or restart qwen once so the new `--metrics` flag is active:

```bash
qwenctl restart
```

Start the exporter:

```bash
cd ~/projects/ai-stack-dashboard
uv run python exporter/qwen_exporter.py
```

In another shell, start Prometheus and Grafana:

```bash
cd ~/projects/ai-stack-dashboard
docker compose up -d
```

Open:

- Grafana: `http://127.0.0.1:3000/d/qwen-stack/ai-stack-home-server`
- Qwen Control: `http://127.0.0.1:9110`
- Prometheus: `http://127.0.0.1:9090`
- Exporter metrics: `http://127.0.0.1:9108/metrics`

Grafana login is `admin` / `admin`, with anonymous viewer access enabled on localhost.

## Autostart Dashboard

```bash
mkdir -p ~/.config/systemd/user
cp systemd/qwen-exporter.service ~/.config/systemd/user/
cp systemd/qwen-control.service ~/.config/systemd/user/
cp systemd/qwen-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now qwen-dashboard qwen-exporter qwen-control
systemctl --user disable qwen
```

This starts only Grafana, Prometheus, the metrics exporter, and the control UI when your WSL user session starts. It does not start the qwen inference service.

## Qwen Control

The dashboard has a `Qwen Control` panel that opens `http://127.0.0.1:9110`.

The control UI runs Start, Stop, and Restart through `systemctl --user qwen` and `qwenctl`.
It binds only to localhost and requires a per-machine CSRF token stored in:

```bash
~/.local/state/qwen-control-token
```

Action logs are written to:

```bash
~/.local/state/qwen-control.log
```

## Validation

```bash
uv run python -m py_compile exporter/qwen_exporter.py
uv run python -m py_compile control/qwen_control.py
uv run python exporter/qwen_exporter.py --port 19108
curl -sf http://127.0.0.1:19108/metrics | head
uv run python control/qwen_control.py --port 19110
curl -sf http://127.0.0.1:19110/api/status
docker compose config
```

If Docker cannot access the daemon from WSL, start Docker Desktop or fix access to `/var/run/docker.sock`, then rerun `docker compose up -d`.

## Notes

- Prometheus and Grafana use `network_mode: host` so containers can scrape WSL-local `127.0.0.1:8080`.
- CPU temperature is not included because WSL usually does not expose reliable motherboard sensor data.
- Per-client token attribution is not included. Add a local reverse proxy later if you want request-by-request tokens, latency, caller labels, or prompt-cache attribution.
- The control UI is intentionally separate from the metrics exporter so Prometheus scraping remains read-only.
