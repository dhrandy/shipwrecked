import os
import time

import pytest

os.environ["SHIPWRECKED_DB"] = "/tmp/shipwrecked_app_test.db"

import engine  # noqa: E402
import app as appmod  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "DB_PATH", str(tmp_path / "a.db"))
    engine.init_db()
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"SHIPWRECKED" in r.data


def test_state_shape(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    s = r.get_json()
    for key in ("phase", "weather", "moon", "tide", "island_day", "cycle",
                "chapter", "activity", "activity_label", "goal", "state_line",
                "needs", "world", "recent", "catchup", "milestones"):
        assert key in s, key
    assert s["phase"] in ("dawn", "day", "dusk", "night")
    assert s["brain"] == "scripted"


def test_state_since_returns_catchup(client):
    client.get("/api/state")
    r = client.get(f"/api/state?since={time.time() - 3600}")
    s = r.get_json()
    assert isinstance(s["catchup"], list)


def test_no_external_requests_in_page(client):
    html = client.get("/").data.decode()
    assert "http://" not in html.replace("http://your-server", "")
    assert "https://" not in html
    assert "cdn" not in html.lower()


def test_version_matches_docs():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert appmod.VERSION in (root / "README.md").read_text()
    assert f"shipwrecked:{appmod.VERSION}" in (root / "compose.yaml").read_text()
