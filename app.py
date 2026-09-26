"""Shipwrecked - a tiny island story that keeps going while you're away.

A love letter to the story-telling screensavers of the early 90s:
one castaway, one palm tree, and a server-side brain that never sleeps.
"""
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory

import engine

VERSION = "0.2.3"

app = Flask(__name__, static_folder="static", static_url_path="/static")

engine.init_db()


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "version": VERSION})


@app.get("/api/state")
def state():
    """Current world snapshot + everything that happened since `since`.

    `since` is an ISO timestamp or unix float; the page passes the visitor's
    last visit so they get the "while you were away" catch-up.
    """
    since = request.args.get("since", type=str)
    since_ts = None
    if since:
        try:
            since_ts = float(since)
        except ValueError:
            try:
                since_ts = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
            except ValueError:
                since_ts = None
    world = engine.advance()
    snap = engine.snapshot(world)
    snap["version"] = VERSION
    snap["catchup"] = engine.get_events(since=since_ts, limit=250) if since_ts else []
    snap["recent"] = engine.get_events(limit=40)
    return jsonify(snap)


@app.get("/api/log")
def log():
    limit = min(request.args.get("limit", 200, type=int), 500)
    world = engine.advance()
    engine.snapshot(world)  # ensure advance happened; snapshot unused here
    return jsonify({"events": engine.get_events(limit=limit)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8647"))
    app.run(host="0.0.0.0", port=port)
