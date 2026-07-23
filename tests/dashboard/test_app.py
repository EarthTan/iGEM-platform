import json

import pytest

from src.setup.dashboard.app import create_app


@pytest.fixture
def client(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    app, _cache = create_app(log_dir=str(log_dir), probe=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        # make tests fast by replacing slow collectors
        _cache._db.read_coverage = lambda: []
        _cache._db.read_db_stats = lambda: {"active_connections": 0, "active_queries": 0, "db_size": "0 MB"}
        _cache._proc.snapshot = lambda: {"enrich_py": [], "master_sh": [], "services": {}}
        _cache._sysmon.snapshot = lambda: {"cpu": {}, "memory": {}, "gpu": [], "top_procs": []}
        # trigger at least one tick
        _cache._refresh()
        yield c
        _cache.stop()


def test_index_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"<!DOCTYPE html>" in r.data or b"<html" in r.data


def test_api_state_returns_json(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "generated_at" in data
    assert "current_run" in data
    assert "tools_overview" in data
    assert "system" in data


def test_api_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["ok"] is True


def test_post_to_api_state_returns_405(client):
    r = client.post("/api/state")
    assert r.status_code == 405


def test_post_to_api_health_returns_405(client):
    r = client.post("/api/health")
    assert r.status_code == 405


def test_unknown_route_returns_404(client):
    r = client.get("/api/control")
    assert r.status_code == 404


def test_probe_query_param_enables_probe(client):
    cache = client.application.config["STATE_CACHE"]
    # start with probe=False
    assert cache._proc.probe is False
    # probe=1 enables
    client.get("/api/state?probe=1")
    assert cache._proc.probe is True
    # probe=0 disables
    client.get("/api/state?probe=0")
    assert cache._proc.probe is False
