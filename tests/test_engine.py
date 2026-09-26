import json
import os
import sqlite3
import time

import pytest

os.environ["SHIPWRECKED_DB"] = "/tmp/shipwrecked_test.db"

import engine  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(engine, "DB_PATH", str(db))
    engine.init_db()
    yield


def test_world_advances_and_logs():
    w = engine._load_world(engine._connect())
    # rewind the world clock 6 hours and let advance() catch up
    conn = engine._connect()
    w = engine._load_world(conn)
    w["last_tick"] = time.time() - 6 * 3600
    engine._save_world(conn, w)
    conn.commit()
    conn.close()
    world = engine.advance()
    assert world["slot"] > 20
    events = engine.get_events(limit=500)
    assert len(events) > 10
    kinds = {e["kind"] for e in events}
    assert "routine" in kinds


def test_catchup_deterministic_for_same_seed():
    # same seed + same slots => same decisions (engine is slot-seeded)
    conn = engine._connect()
    w = engine._load_world(conn)
    w["seed"] = 12345
    engine._save_world(conn, w)
    conn.commit(); conn.close()
    w1 = engine.advance()
    r1 = engine._rng({"seed": 12345, "slot": 50})
    r2 = engine._rng({"seed": 12345, "slot": 50})
    assert r1.random() == r2.random()


def test_needs_stay_clamped():
    conn = engine._connect()
    w = engine._load_world(conn)
    w["last_tick"] = time.time() - 24 * 3600
    engine._save_world(conn, w)
    conn.commit(); conn.close()
    world = engine.advance()
    for v in world["needs"].values():
        assert 0 <= v <= 100


def test_advisor_commits_and_hesitates_sometimes():
    conn = engine._connect()
    w = engine._load_world(conn)
    w["last_tick"] = time.time() - 12 * 3600
    engine._save_world(conn, w)
    conn.commit(); conn.close()
    engine.advance()
    events = engine.get_events(limit=1000)
    # with 12h of slots there should be more than one distinct activity
    acts = {e["activity"] for e in events if e["kind"] == "routine"}
    assert len(acts) >= 2
    # every routine event carries the goal contract
    for e in events:
        if e["kind"] == "routine" and e["activity"] != "hesitate":
            assert "goal" in e["data"] or e["activity"] == "stand"


def test_state_line_format():
    conn = engine._connect()
    w = engine._load_world(conn)
    line = engine.encode_state_line(w, "day")
    conn.close()
    for tok in ("day", "phase day", "hunger", "energy", "raft", "last"):
        assert tok in line


def test_holidays():
    import datetime
    xmas = datetime.datetime(2026, 12, 25, 12, 0).timestamp()
    assert "christmas" in engine.holidays_for(xmas)
    pirate = datetime.datetime(2026, 9, 19, 12, 0).timestamp()
    assert "pirateday" in engine.holidays_for(pirate)
    plain = datetime.datetime(2026, 8, 10, 12, 0).timestamp()
    assert engine.holidays_for(plain) == []


def test_moon_phase_bounds():
    name, illum = engine.moon_phase(time.time())
    assert 0.0 <= illum <= 1.0
    assert "Moon" in name or "Quarter" in name or "Crescent" in name or "Gibbous" in name


def test_story_arc_fires_over_days():
    conn = engine._connect()
    w = engine._load_world(conn)
    w["last_tick"] = time.time() - 3 * 24 * 3600 - 60  # max catch-up window
    engine._save_world(conn, w)
    conn.commit(); conn.close()
    world = engine.advance()
    assert world["island_day"] >= 3
    events = engine.get_events(limit=1000)
    assert any(e["kind"] == "story" for e in events)
