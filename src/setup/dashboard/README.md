# Dashboard

LAN-accessible, **read-only** progress monitor for the running enrichment pipeline.

## What it does

- Watches `logs/enrich_run/master_*.log` for the current tool / batch progress.
- Reads `v_peptide_enrichment_coverage` from PG for per-tool completion.
- Lists the enrich / run.sh / 9-microservice processes with CPU/RSS/GPU-mem.
- Shows CPU / memory / GPU load + top CPU processes.
- Streams the last 30 lines of master log.

## What it does NOT do

- No write operations (no INSERT/UPDATE/DELETE, no `tee`, no POST).
- No kill / pause / restart / skip controls.
- No auth, no history, no alerts.

## Run

```bash
pip install -r requirements.txt  # or --break-system-packages on Debian

# Local only
python3 -m src.setup.dashboard

# LAN (default port 8088)
python3 -m src.setup.dashboard --host 0.0.0.0

# Also poll the 9 microservice /health endpoints (default OFF)
python3 -m src.setup.dashboard --host 0.0.0.0 --probe
```

Open `http://<server-ip>:8088/`.

## Firewall (do this)

```bash
# Only allow LAN subnet
sudo ufw allow from 192.168.1.0/24 to any port 8088
```

**Do not expose to the public internet.** No auth, no rate limit.
