"""app.py — Flask app + argparse entry point.

Routes (all GET, no exceptions):
  GET /              → HTML
  GET /api/state     → JSON
  GET /api/health    → JSON
"""
from __future__ import annotations

import argparse
import sys

from flask import Flask, jsonify, render_template

from .state import StateCache


def create_app(log_dir: str, probe: bool = False):
    """Factory for tests and main(). Sets up the StateCache and wires routes."""
    cache = StateCache(log_dir=log_dir, dashboard_root="/tmp", probe=probe)
    cache.start()

    app = Flask(__name__)
    app.config["STATE_CACHE"] = cache

    @app.route("/")
    def index():
        return render_template("index.html", initial_state=cache.snapshot())

    @app.route("/api/state")
    def api_state():
        return jsonify(cache.snapshot())

    @app.route("/api/health")
    def api_health():
        return jsonify({"ok": True, "last_tick": cache.last_tick_time()})

    return app, cache


def main() -> None:
    p = argparse.ArgumentParser(description="iGEM dashboard (read-only)")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8088, help="bind port (default 8088)")
    p.add_argument("--log-dir", default="/home/lenovo/Projects/iGEM-platform/logs/enrich_run")
    p.add_argument("--probe", action="store_true", help="probe microservice /health endpoints each tick")
    args = p.parse_args()

    app, _cache = create_app(log_dir=args.log_dir, probe=args.probe)
    print(f"[dashboard] http://{args.host}:{args.port}/  log_dir={args.log_dir} probe={args.probe}", file=sys.stderr)
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
