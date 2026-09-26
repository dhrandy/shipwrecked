"""Shipwrecked story engine.

A tiny island, one castaway named Wade, and a brain that keeps his story
moving whether or not anyone is watching. State lives in SQLite so the
world survives restarts and the story advances while the page is closed.

Brain design borrowed from pocket-tank: a reflex layer (the slot tick),
a pluggable advisor that commits to GOALS with urgency (scripted rules by
default, same signature an LLM endpoint would use), and a progression
layer (chapters, milestones, memory) that lives through time away.
"""
from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timezone

DB_PATH = os.environ.get("SHIPWRECKED_DB", "/data/shipwrecked.db")
SIM_SPEED = float(os.environ.get("SIM_SPEED", "1"))  # 1 = real time
MAX_CATCHUP_SECONDS = 3 * 24 * 3600  # simulate at most 3 days back
EVENT_KEEP = 5000
MIN_GOAL_SLOTS = 3  # Wade commits to a goal for at least this many slots

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


# ---------------------------------------------------------------------------
# Calendar: phases, weather, moon, tide, holidays
# ---------------------------------------------------------------------------

def phase_of(ts: float) -> str:
    h = _local(ts).hour
    if 5 <= h < 8:
        return "dawn"
    if 8 <= h < 17:
        return "day"
    if 17 <= h < 20:
        return "dusk"
    return "night"


def moon_phase(ts: float) -> tuple[str, float]:
    synodic = 29.53058867
    known_new = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc).timestamp()
    age = ((ts - known_new) / 86400.0) % synodic
    illum = (1 - math.cos(2 * math.pi * age / synodic)) / 2
    names = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
             "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent"]
    idx = int((age / synodic) * 8 + 0.5) % 8
    return names[idx], round(illum, 2)


def tide_of(ts: float) -> str:
    cycle = ((ts / 60.0) % 745.0) / 745.0
    return "high" if math.sin(2 * math.pi * cycle) > 0 else "low"


def holidays_for(ts: float) -> list[str]:
    d = _local(ts)
    out = []
    fixed = {(1, 1): "newyear", (2, 14): "valentines", (3, 17): "stpatricks",
             (7, 4): "july4", (9, 19): "pirateday", (10, 31): "halloween",
             (12, 25): "christmas", (12, 31): "newyearseve"}
    if (d.month, d.day) in fixed:
        out.append(fixed[(d.month, d.day)])
    # Easter (Anonymous Gregorian algorithm)
    a, y = d.year % 19, d.year
    b, c = y // 100, y % 100
    dd, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - dd - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    em, ed = (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1
    if (d.month, d.day) == (em, ed):
        out.append("easter")
    return out


HOLIDAY_GAGS = {
    "newyear": "Wade hung a driftwood sign on Pal: 'HAPPY NEW YEAR'. The year is wrong. Nobody corrects him.",
    "valentines": "Wade carved a heart into a coconut, looked at it, and threw it in the sea. Snips the crab kept it.",
    "stpatricks": "Pal the palm looks extra green today. Wade pinched the crab anyway. The crab pinched back.",
    "july4": "Wade set off a 'firework' (a coconut in the campfire). It hissed, popped, and scared Captain Beak clean off the island.",
    "pirateday": "Wade spent the day saying 'arr' at the ocean and demanding the horizon hand over its booty.",
    "halloween": "A carved coconut lantern sits in the sand. Wade insists its face is 'based on a real guy from the ship'.",
    "christmas": "Pal the palm is strung with shells and one very shiny fishhook. Wade sang carols to the fish. The fish left.",
    "newyearseve": "Wade stayed up past midnight counting down to nobody. 'HAPPY NEW YEAR!' he yelled at a squid.",
    "easter": "Wade hid three eggs around the island. They were turtle eggs. Turbo was not amused.",
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, kind TEXT, activity TEXT, text TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""

DEFAULT_WORLD = {
    "seed": None,
    "slot": 0,
    "last_tick": None,
    "started_at": None,
    "island_day": 1,
    "cycle": 1,
    "chapter": "Landfall",
    "raft_progress": 0,      # 0-100
    "wood": 0,
    "coconuts": 3,
    "trinkets": [],
    "shelter": 0,
    "fire_lit": False,
    "needs": {"hunger": 40, "energy": 80, "mood": 60, "boredom": 30, "loneliness": 35},
    "relations": {"captain_beak": -20, "snips": 0, "turbo": 10},
    "memory": [],
    "thought": None,
    "activity": "stand",
    "activity_label": "standing on the beach",
    "activity_slot": 0,           # slot when current goal began
    "goal": {"id": "stand", "urgency": 3, "confidence": 1.0, "runner_up": None},
    "milestones": {},             # name -> {day, cycle, ts}
    "weather": {"state": "sunny", "until": 0},
    "cooldowns": {},
    "away": False,
    "flags": {},
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _load_world(conn) -> dict:
    row = conn.execute("SELECT value FROM kv WHERE key='world'").fetchone()
    if row:
        w = json.loads(row[0])
        for k, v in DEFAULT_WORLD.items():
            w.setdefault(k, v)
        return w
    w = json.loads(json.dumps(DEFAULT_WORLD))
    now = _now()
    w["seed"] = random.SystemRandom().randrange(1, 2**31)
    w["started_at"] = now
    w["last_tick"] = now
    w["activity_slot"] = 0
    return w


def _save_world(conn, world: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('world', ?)",
                 (json.dumps(world),))


def _log(conn, ts: float, kind: str, activity: str, text: str, data: dict | None = None) -> None:
    conn.execute("INSERT INTO events (ts, kind, activity, text, data) VALUES (?,?,?,?,?)",
                 (ts, kind, activity, text, json.dumps(data or {})))
    conn.execute(
        "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY ts DESC LIMIT ?)",
        (EVENT_KEEP,))


def get_events(since: float | None = None, limit: int = 200) -> list[dict]:
    conn = _connect()
    try:
        if since is None:
            rows = conn.execute(
                "SELECT ts, kind, activity, text, data FROM events ORDER BY ts DESC LIMIT ?",
                (limit,)).fetchall()
            rows.reverse()
        else:
            rows = conn.execute(
                "SELECT ts, kind, activity, text, data FROM events WHERE ts > ? ORDER BY ts ASC LIMIT ?",
                (since, limit)).fetchall()
        return [{"ts": r[0], "iso": _iso(r[0]), "kind": r[1], "activity": r[2],
                 "text": r[3], "data": json.loads(r[4])} for r in rows]
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# The advisor: Wade's brain
# ---------------------------------------------------------------------------
# Encodes the situation as one state line, scores every candidate goal
# against needs/weather/memory/personality, samples from the distribution
# (never flat argmax), and commits: goals have urgency, a minimum tenure,
# and visible hesitation when the top two run close.

TRAITS = {"stubborn": 0.7, "optimist": 0.8, "dramatic": 0.6, "curious": 0.9}

GOALS = {
    "fish": {
        "base": 1.0, "phases": ["dawn", "day", "dusk"],
        "label": "fishing off the rocks",
        "needs": {"hunger": -30, "boredom": -10, "energy": -8},
        "why": ["his stomach was making whale noises (hunger {hunger})",
                "dinner wasn't going to catch itself"],
        "thought": ["Feel that tug? No. That's the tide. Again.",
                    "One more cast. The big one's out there."],
    },
    "gather_wood": {
        "base": 0.7, "phases": ["dawn", "day"],
        "label": "dragging driftwood up the beach",
        "needs": {"energy": -14, "boredom": -6},
        "why": ["the raft won't build itself",
                "the tide dropped off fresh timber and Wade doesn't waste gifts"],
        "thought": ["This log is raft-shaped if you squint.", "Wood. Glorious wood."],
        "requires": lambda w: not w["away"] and w["raft_progress"] < 100,
    },
    "raft_work": {
        "base": 0.9, "phases": ["day", "dusk"],
        "label": "lashing logs onto the raft",
        "needs": {"energy": -16, "boredom": -12, "mood": +6},
        "why": ["daydreaming about open water put him in a building mood",
                "progress is progress, one knot at a time"],
        "thought": ["Knot, knot, knot. The Navy would weep.", "She'll float. Probably."],
        "requires": lambda w: w["wood"] >= 2 and w["raft_progress"] < 100 and not w["away"],
    },
    "nap": {
        "base": 0.6, "phases": ["day", "dusk"],
        "label": "napping in Pal's shade",
        "needs": {"energy": +25, "hunger": +6},
        "why": ["the sun was doing that thing where it makes eyelids heavy",
                "energy was running on fumes ({energy})"],
        "thought": ["Five minutes. That's all.", "...zzZZzz"],
    },
    "sleep": {
        "base": 2.0, "phases": ["night"],
        "label": "sleeping by the fire",
        "needs": {"energy": +40, "hunger": +8, "loneliness": +2, "boredom": -15},
        "why": ["even castaways keep office hours, and the office is closed",
                "the moon clocked in, so Wade clocked out"],
        "thought": ["...island... mine... zzZ", "...no more coconuts... zzZ"],
    },
    "jog": {
        "base": 0.5, "phases": ["dawn", "day", "dusk"],
        "label": "jogging laps around the island (all four seconds of one)",
        "needs": {"energy": -12, "boredom": -18, "mood": +8},
        "why": ["boredom hit {boredom} and the island isn't getting any bigger",
                "beach body season is eternal out here"],
        "thought": ["Lap twelve. Or forty. Lost count.", "Feel the burn. Hate the burn."],
    },
    "coconut": {
        "base": 0.8, "phases": ["dawn", "day", "dusk"],
        "label": "shaking Pal the palm for coconuts",
        "needs": {"hunger": -20, "energy": -6},
        "why": ["hunger was at {hunger} and Pal was holding out on him",
                "a coconut a day keeps the despair away"],
        "thought": ["Come on, Pal. Share.", "One bonk on the head builds character."],
    },
    "sandcastle": {
        "base": 0.4, "phases": ["dawn", "day", "dusk"],
        "label": "building an unnecessarily elaborate sandcastle",
        "needs": {"boredom": -22, "mood": +10, "energy": -6},
        "why": ["boredom demanded architecture",
                "if a ship passes, he wants it to see he's doing FINE, actually"],
        "thought": ["Moat. Every castle needs a moat.", "East tower is load-bearing. Trust me."],
    },
    "signal": {
        "base": 0.6, "phases": ["dawn", "day", "dusk"],
        "label": "scanning the horizon with the telescope",
        "needs": {"boredom": -8, "mood": -2, "loneliness": +2},
        "why": ["hope springs eternal, and so does squinting",
                "the horizon owed him an answer"],
        "thought": ["Anything? ...Anything? ...No.", "Is that a ship? It's a bird. It's always a bird."],
    },
    "cook": {
        "base": 0.7, "phases": ["dusk", "night", "dawn"],
        "label": "cooking something questionable over the fire",
        "needs": {"hunger": -35, "energy": -4, "mood": +4},
        "why": ["raw fish is a cry for help; cooked fish is dinner",
                "hunger hit {hunger} and the fire was right there"],
        "thought": ["Smells like... something.", "Gordon Ramsay could never."],
        "requires": lambda w: w["coconuts"] > 0 or w["flags"].get("has_fish"),
    },
    "write_bottle": {
        "base": 0.35, "phases": ["dawn", "day", "dusk"],
        "label": "scribbling a message for a bottle",
        "needs": {"loneliness": -18, "boredom": -8},
        "why": ["loneliness crept to {loneliness} and paper doesn't judge",
                "the sea mail service is slow but the rates are unbeatable"],
        "thought": ["Dear anyone. Hi.", "Day {day}. Still here. Send snacks."],
    },
    "stargaze": {
        "base": 1.0, "phases": ["night"],
        "label": "stargazing and naming constellations after snacks",
        "needs": {"boredom": -14, "loneliness": -6, "mood": +5},
        "why": ["the sky was showing off again",
                "there's the Big Cracker, and there, the Cheese Dipper"],
        "thought": ["That one's the Big Cracker.", "Stars don't charge rent."],
    },
    "bathe": {
        "base": 0.3, "phases": ["dawn", "day"],
        "label": "bathing in the lagoon, nervously",
        "needs": {"mood": +8, "energy": -4},
        "why": ["hygiene is a state of mind, and his mind said it was time",
                "even the fish were keeping their distance"],
        "thought": ["Cold. Cold cold cold.", "Nobody look. The fish count as nobody."],
    },
    "tend_fire": {
        "base": 0.6, "phases": ["dusk", "night"],
        "label": "feeding the campfire and arguing with the smoke",
        "needs": {"mood": +4, "boredom": -6},
        "why": ["a good fire is half the rent out here",
                "smoke follows beauty, and Wade was standing right there"],
        "thought": ["Smoke follows beauty. Ow. My eyes.", "Burn, you beautiful pile."],
    },
    "stand": {
        "base": 0.2, "phases": ["dawn", "day", "dusk", "night"],
        "label": "standing on the beach, hands on hips",
        "needs": {"boredom": +2},
        "why": ["somebody has to supervise the ocean"],
        "thought": ["Yep. Still an island.", "The horizon looks guilty today."],
    },
}

SPECIALS = {
    "seagull": {"chance": 0.05, "cooldown": 30, "phases": ["dawn", "day", "dusk"],
                "texts": ["Captain Beak the seagull dive-bombed Wade and made off with his hat. Again.",
                          "Captain Beak landed on Wade's head and refused to acknowledge the treaty.",
                          "Captain Beak stole Wade's lunch mid-bite. War has been re-declared."]},
    "shark": {"chance": 0.04, "cooldown": 40, "phases": ["dawn", "day", "dusk"],
              "texts": ["A shark fin did slow laps around the island. Wade did slow laps in the other direction.",
                        "The shark is back. Wade named it Mortgage, because it won't leave him alone."]},
    "ship": {"chance": 0.03, "cooldown": 60, "phases": ["dawn", "day", "dusk"],
             "texts": ["A ship crossed the horizon! Wade waved, screamed, and set fire to his 'HELP' sign. The ship did not turn.",
                       "Ship on the horizon! Wade's flag work was, in hindsight, illegible. The ship sailed on."]},
    "plane": {"chance": 0.02, "cooldown": 80, "phases": ["day", "dusk"],
              "texts": ["A plane droned overhead. Wade waved both arms and one coconut. The plane kept droning."]},
    "dolphin": {"chance": 0.04, "cooldown": 50, "phases": ["dawn", "day", "dusk"],
                "texts": ["A dolphin pod stopped by to click at him. Wade clicked back. Everyone pretended it was a conversation."]},
    "crab": {"chance": 0.05, "cooldown": 25, "phases": ["dawn", "day", "dusk"],
             "texts": ["Snips the crab relocated Wade's left shoe to an undisclosed location.",
                       "Snips the crab challenged Wade to a duel. Wade declined. Snips declared victory."]},
    "turtle": {"chance": 0.03, "cooldown": 70, "phases": ["dawn", "day"],
               "texts": ["Turbo the sea turtle surfaced to say hi. Turbo says hi by floating. It's enough."]},
    "bottle_found": {"chance": 0.02, "cooldown": 90, "phases": ["dawn", "day", "dusk"],
                     "texts": ["A bottle washed up! Inside: a coupon for a restaurant that closed in 1998, and hope. Mostly the coupon."]},
    "shooting_star": {"chance": 0.06, "cooldown": 12, "phases": ["night"],
                      "texts": ["A shooting star! Wade wished for a boat. Then, thinking bigger, a bigger island."]},
    "fireflies": {"chance": 0.05, "cooldown": 15, "phases": ["night"],
                  "texts": ["Fireflies drifted over the lagoon. Wade conducted them like an orchestra. They ignored the tempo."]},
    "whale": {"chance": 0.015, "cooldown": 120, "phases": ["dawn", "day", "dusk"],
              "texts": ["A whale breached way out past the reef. Wade applauded. The whale took no bow."]},
    "storm_rolls_in": {"chance": 0.02, "cooldown": 100, "phases": ["dawn", "day", "dusk", "night"],
                       "texts": ["The sky went the color of old nickels. Storm coming. Wade secured everything (moved it two feet inland)."]},
}

ARC = {
    1: ("Landfall", "day", "Wade stood on the beach, planted a stick, and declared the island 'open for business'. Business is poor."),
    3: ("Driftwood Dreams", "day", "Wade sketched raft plans in the sand. The tide reviewed them and requested changes."),
    4: ("Near Miss", "dusk", "A freighter passed close! Wade's signal fire was, tragically, also his dinner fire. Dinner won. The freighter didn't."),
    6: ("Halfway Hull", "day", "The raft is half built. Wade walked around it like a proud architect. Snips the crab moved in immediately."),
    7: ("Sky Bird", "day", "A plane! Wade spelled HELP with driftwood. He had only enough wood for HEL. Close enough, he figured."),
    9: ("Sea Trials", "dusk", "Raft complete. Wade pushed it into the shallows and sat on it for an hour, practicing looking casual."),
    10: ("The Voyage", "dawn", "At first light Wade pushed off. The island got smaller. Pal the palm waved. Probably the wind."),
    11: ("The Return", "dusk", "The current had opinions. Wade washed back ashore, saltier and quietly relieved. He hugged Pal. Pal dropped a coconut on him. Home."),
    12: ("Feast Day", "dusk", "Wade threw a one-man feast: roasted coconut, grilled fish, and a speech. The speech was for the fish. Cycle complete - tomorrow the dream starts again, slightly grander."),
}

MILESTONES = {
    "first_fish": "Caught his first fish",
    "first_bottle": "Sent his first message in a bottle",
    "first_castle": "Built his first sandcastle",
    "raft_started": "Began the raft",
    "raft_complete": "Finished the raft",
    "first_storm": "Weathered his first storm",
    "first_ship": "Waved at his first ship",
    "first_voyage": "Set sail for the first time",
    "first_return": "Came back to the island",
    "cycle_two": "Started the dream over",
}


def _rng(world: dict, salt: int = 0) -> random.Random:
    return random.Random(world["seed"] * 1000003 + world["slot"] * 97 + salt)


def _fmt(template: str, world: dict) -> str:
    n = world["needs"]
    return template.format(hunger=int(n["hunger"]), energy=int(n["energy"]),
                           mood=int(n["mood"]), boredom=int(n["boredom"]),
                           loneliness=int(n["loneliness"]), day=world["island_day"])


def _remember(world: dict, text: str) -> None:
    world["memory"].append({"day": world["island_day"], "cycle": world["cycle"], "text": text})
    world["memory"] = world["memory"][-40:]


def _clamp_needs(world: dict) -> None:
    for k, v in world["needs"].items():
        world["needs"][k] = max(0, min(100, v))


def encode_state_line(world: dict, phase: str) -> str:
    """The pocket-tank seam: the whole situation as one line of text.

    This is exactly what a real LLM advisor would read later, and what a
    tiny embedded client can parse without rendering anything."""
    n = world["needs"]
    w = world["weather"]["state"]
    return (
        f"day {world['island_day']} cycle {world['cycle']} phase {phase} "
        f"hunger {int(n['hunger'])} energy {int(n['energy'])} mood {int(n['mood'])} "
        f"bored {int(n['boredom'])} lonely {int(n['loneliness'])} "
        f"weather {w} tide {tide_of(world['last_tick'] or _now())} "
        f"wood {world['wood']} raft {world['raft_progress']} "
        f"coconuts {world['coconuts']} shelter {world['shelter']} "
        f"stubborn {int(TRAITS['stubborn']*10)} optimist {int(TRAITS['optimist']*10)} "
        f"curious {int(TRAITS['curious']*10)} last {world['activity']}"
    )


def advisor(world: dict, phase: str, rng: random.Random) -> dict:
    """Scripted advisor: same contract an LLM advisor would fill.

    Returns {id, urgency, confidence, runner_up}. Commits like pocket-tank:
    Wade sticks with his current goal for a minimum tenure unless something
    urgent overrides, and when the top two score close he hesitates."""
    n = world["needs"]
    scores = {}
    for name, g in GOALS.items():
        if phase not in g["phases"]:
            continue
        req = g.get("requires")
        if req and not req(world):
            continue
        w = g["base"]
        pull = 0.0
        for need, delta in g["needs"].items():
            if delta < 0:
                pull += (-delta) * (n.get(need, 0) / 100.0)
            elif delta > 0 and need == "energy":
                pull += delta * ((100 - n.get("energy", 0)) / 100.0)
        w *= (0.4 + pull / 40.0)
        wx = world["weather"]["state"]
        if wx in ("rain", "storm") and name in ("jog", "sandcastle", "bathe", "signal", "fish"):
            w *= 0.25
        if wx in ("rain", "storm") and name in ("cook", "tend_fire", "nap"):
            w *= 1.6
        if name == "sandcastle":
            w *= (0.5 + TRAITS["dramatic"])
        if name == "signal":
            w *= (0.5 + TRAITS["optimist"])
        if name == "raft_work":
            w *= (0.5 + TRAITS["stubborn"])
        recent_gull = any("Captain Beak" in m["text"] for m in world["memory"][-3:])
        if recent_gull and name == "nap":
            w *= 0.3
        if world["activity"] == name:
            w *= 1.25  # inertia: finishing what you started reads as intent
        scores[name] = w
    if not scores:
        return {"id": "stand", "urgency": 2, "confidence": 1.0, "runner_up": None}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top, second = ranked[0], ranked[1] if len(ranked) > 1 else (None, 0)
    confidence = 1.0 - (second[1] / top[1] if top[1] else 0.0)

    # sample from the distribution, pocket-tank style
    total = sum(scores.values())
    pick = rng.random() * total
    chosen = top[0]
    for name, w in ranked:
        pick -= w
        if pick <= 0:
            chosen = name
            break
    if chosen != top[0]:
        confidence = min(confidence, 0.6)

    # urgency from the driving need
    cur_goal = world["goal"].get("id", "stand")
    urgency = 3
    if chosen == "sleep":
        urgency = 9 if n["energy"] < 20 else 6
    elif chosen in ("fish", "coconut", "cook"):
        urgency = 9 if n["hunger"] > 75 else 6 if n["hunger"] > 50 else 4
    elif chosen == "nap":
        urgency = 7 if n["energy"] < 35 else 4
    elif chosen == "raft_work":
        urgency = 7 if world["raft_progress"] > 60 else 5
    elif chosen in ("jog", "sandcastle", "stargaze", "write_bottle"):
        urgency = 4
    elif chosen == "signal":
        urgency = 5 if n["loneliness"] > 60 else 3

    # commitment: keep the current goal through its tenure unless urgency overrides
    tenure = world["slot"] - world.get("activity_slot", 0)
    if (cur_goal in scores and tenure < MIN_GOAL_SLOTS
            and urgency < 8 and world["activity"] == cur_goal):
        return {"id": cur_goal, "urgency": world["goal"].get("urgency", 3),
                "confidence": 1.0, "runner_up": None, "kept": True}

    return {"id": chosen, "urgency": urgency, "confidence": round(confidence, 2),
            "runner_up": top[0] if chosen != top[0] else second[0]}


def _update_weather(world: dict, ts: float, rng: random.Random) -> None:
    if ts >= world["weather"].get("until", 0):
        prev = world["weather"].get("state", "sunny")
        if prev == "storm":
            nxt = rng.choice(["rain", "cloudy"])
        elif prev == "rain":
            nxt = rng.choice(["cloudy", "rain", "sunny"])
        elif prev == "cloudy":
            nxt = rng.choice(["sunny", "cloudy", "rain"])
        else:
            nxt = rng.choice(["sunny", "sunny", "sunny", "cloudy"])
        world["weather"] = {"state": nxt, "until": ts + rng.randint(2, 6) * 3600}


def _drift_needs(world: dict, phase: str) -> None:
    n = world["needs"]
    n["hunger"] += 4
    n["boredom"] += 2
    n["loneliness"] += 0.4
    if phase == "night" and world["activity"] != "sleep":
        n["energy"] -= 3
    else:
        n["energy"] -= 1.5
    if world["weather"]["state"] == "storm":
        n["mood"] -= 2
    _clamp_needs(world)


def _award(world: dict, ts: float, name: str) -> None:
    if name in world["milestones"]:
        return
    world["milestones"][name] = {"day": world["island_day"], "cycle": world["cycle"], "ts": ts}
    _log(conn_ctx, ts, "milestone", name,
         f"Milestone: {MILESTONES[name]} (day {world['island_day']}, cycle {world['cycle']}).",
         {"milestone": name})
    _remember(world, MILESTONES[name])


def _apply_goal(world: dict, ts: float, decision: dict, rng: random.Random) -> None:
    name = decision["id"]
    g = GOALS[name]
    new_goal = world["activity"] != name
    why = _fmt(rng.choice(g["why"]), world)
    world["thought"] = _fmt(rng.choice(g["thought"]), world)
    for need, delta in g["needs"].items():
        world["needs"][need] = world["needs"].get(need, 50) + delta
    _clamp_needs(world)
    if new_goal:
        world["activity_slot"] = world["slot"]
    world["activity"] = name
    world["activity_label"] = g["label"]
    world["goal"] = {k: decision[k] for k in ("id", "urgency", "confidence", "runner_up")}

    extra = ""
    if name == "fish":
        if rng.random() < 0.55:
            world["flags"]["has_fish"] = True
            extra = " He caught a fish with an attitude problem."
            _award(world, ts, "first_fish")
        else:
            junk = rng.choice(["an old boot", "a length of rope (kept it)",
                               "a license plate from 1974", "nothing, but with style"])
            if "kept" in junk or "plate" in junk:
                world["trinkets"].append(junk)
                world["trinkets"] = world["trinkets"][-12:]
            extra = f" He caught {junk}."
    elif name == "coconut":
        got = rng.randint(1, 3)
        world["coconuts"] += got
        extra = f" Pal surrendered {got} coconut{'s' if got > 1 else ''}."
    elif name == "gather_wood":
        got = rng.randint(2, 4)
        world["wood"] += got
        extra = f" {got} more logs for the pile (now {world['wood']})."
    elif name == "raft_work":
        use = min(world["wood"], rng.randint(2, 4))
        world["wood"] -= use
        was = world["raft_progress"]
        world["raft_progress"] = min(100, world["raft_progress"] + use * 3)
        extra = f" Raft at {world['raft_progress']}%."
        if was == 0:
            _award(world, ts, "raft_started")
        if world["raft_progress"] >= 100:
            _award(world, ts, "raft_complete")
    elif name == "cook":
        if world["flags"].get("has_fish"):
            world["flags"]["has_fish"] = False
            extra = " The fish went quietly."
        elif world["coconuts"] > 0:
            world["coconuts"] -= 1
            extra = " Roasted coconut. Again. Forever."
    elif name == "sleep":
        world["fire_lit"] = True
    elif name == "write_bottle":
        _award(world, ts, "first_bottle")
    elif name == "sandcastle":
        _award(world, ts, "first_castle")

    if not new_goal and not extra and rng.random() < 0.65:
        return  # continuing a goal quietly; the log only hears about changes and outcomes
    verb = "is now" if new_goal else "is still"
    urg = f" [urgency {decision['urgency']}]" if decision["urgency"] >= 7 else ""
    _log(conn_ctx, ts, "routine", name,
         f"Wade {verb} {g['label']} because {why}.{extra}{urg}",
         {"thought": world["thought"], "goal": world["goal"],
          "state_line": encode_state_line(world, phase_of(ts))})


def _fire_special(world: dict, ts: float, name: str, rng: random.Random) -> None:
    spec = SPECIALS[name]
    text = rng.choice(spec["texts"])
    world["cooldowns"][name] = world["slot"] + spec["cooldown"]
    _log(conn_ctx, ts, "event", name, text, {"overlay": name})
    _remember(world, text)
    n = world["needs"]
    if name == "seagull":
        world["relations"]["captain_beak"] = max(-100, world["relations"]["captain_beak"] - 5)
        n["mood"] -= 6
    if name in ("ship", "plane"):
        n["mood"] -= 8
        n["loneliness"] += 6
        if name == "ship":
            _award(world, ts, "first_ship")
    if name in ("dolphin", "turtle", "whale"):
        n["mood"] += 10
        n["loneliness"] = max(0, n["loneliness"] - 10)
    if name == "storm_rolls_in":
        world["weather"] = {"state": "storm", "until": ts + rng.randint(2, 5) * 3600}
        _award(world, ts, "first_storm")
    _clamp_needs(world)


def _fire_story_beat(world: dict, ts: float, day: int) -> None:
    title, phase, text = ARC[day]
    world["chapter"] = title
    data = {"chapter": title, "day": day, "cycle": world["cycle"]}
    if day == 9:
        world["raft_progress"] = 100
        _award(world, ts, "raft_complete")
    if day == 10:
        world["away"] = True
        world["activity"] = "voyage"
        world["activity_label"] = "somewhere out there on the raft"
        world["thought"] = None
        text += " Wade is at sea; the island keeps his fire warm."
        _award(world, ts, "first_voyage")
    if day == 11:
        world["away"] = False
        world["raft_progress"] = 0
        world["wood"] = 0
        text += " The raft is kindling now. The next one, Wade swears, gets a sail."
        _award(world, ts, "first_return")
    if day == 12:
        _award(world, ts, "cycle_two")
    _log(conn_ctx, ts, "story", "beat", f"[{title}] {text}", data)
    _remember(world, f"[{title}] {text}")


conn_ctx: sqlite3.Connection | None = None


def _run_slot(world: dict, ts: float) -> None:
    global conn_ctx
    world["slot"] += 1
    rng = _rng(world)
    phase = phase_of(ts)
    _update_weather(world, ts, rng)
    _drift_needs(world, phase)

    day_start = _local(ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if world.get("_day_marker") != day_start:
        world["_day_marker"] = day_start
        if world["slot"] > 1:
            world["island_day"] += 1
            if world["island_day"] > 12:
                world["island_day"] = 1
                world["chapter"] = ARC[1][0]
            for h in holidays_for(ts):
                if h in HOLIDAY_GAGS:
                    _log(conn_ctx, ts, "holiday", h, HOLIDAY_GAGS[h], {"holiday": h})
                    _remember(world, HOLIDAY_GAGS[h])

    day = world["island_day"]
    beat_key = f"beat_{world['cycle']}_{day}"
    if day in ARC and not world["flags"].get(beat_key):
        want_phase = ARC[day][1]
        if phase == want_phase or (phase == "night" and want_phase == "dusk"):
            world["flags"][beat_key] = True
            _fire_story_beat(world, ts, day)
            return

    if world["away"]:
        if rng.random() < 0.15:
            notes = ["The island is quiet. Captain Beak ate Wade's leftover coconut out of respect.",
                     "Snips the crab has expanded into Wade's shelter. Renovations are underway.",
                     "A bottle washed ashore. It was one of Wade's own. The sea returns to sender."]
            _log(conn_ctx, ts, "event", "island_quiet", rng.choice(notes), {})
        world["activity"] = "voyage"
        world["activity_label"] = "somewhere out there on the raft"
        return

    for name, spec in SPECIALS.items():
        if phase not in spec["phases"]:
            continue
        if world["slot"] < world["cooldowns"].get(name, 0):
            continue
        if rng.random() < spec["chance"]:
            _fire_special(world, ts, name, rng)
            return

    decision = advisor(world, phase, rng)
    # visible hesitation: top two ran close and the goal would change
    if (not decision.get("kept") and decision["confidence"] < 0.25
            and decision["id"] != world["activity"] and decision["urgency"] < 8):
        world["thought"] = "..."
        _log(conn_ctx, ts, "routine", "hesitate",
             f"Wade stood there a minute, torn between {GOALS[decision['id']]['label']} "
             f"and {GOALS.get(decision['runner_up'] or 'stand', GOALS['stand'])['label']}.",
             {"goal": decision})
        return  # he spends the slot dithering, like a person
    _apply_goal(world, ts, decision, rng)


def advance() -> dict:
    global conn_ctx
    with _lock:
        conn = _connect()
        conn_ctx = conn
        try:
            world = _load_world(conn)
            now = _now()
            last = world["last_tick"] or now
            gap = min((now - last) * SIM_SPEED, MAX_CATCHUP_SECONDS * SIM_SPEED)
            t = last
            slots = 0
            while gap > 0 and slots < 20000:
                dur = _rng(world, salt=5).uniform(4, 9) * 60
                step = min(dur, gap)
                t += step
                _run_slot(world, t)
                gap -= step
                slots += 1
            world["last_tick"] = t
            _save_world(conn, world)
            conn.commit()
            return world
        finally:
            conn_ctx = None
            conn.close()


def snapshot(world: dict) -> dict:
    ts = world["last_tick"] or _now()
    moon_name, moon_illum = moon_phase(ts)
    phase = phase_of(ts)
    return {
        "now": _iso(ts),
        "phase": phase,
        "weather": world["weather"]["state"],
        "moon": {"name": moon_name, "illum": moon_illum},
        "tide": tide_of(ts),
        "holidays": holidays_for(ts),
        "island_day": world["island_day"],
        "cycle": world["cycle"],
        "chapter": world["chapter"],
        "activity": world["activity"],
        "activity_label": world["activity_label"],
        "thought": world["thought"],
        "goal": world["goal"],
        "state_line": encode_state_line(world, phase),
        "away": world["away"],
        "world": {
            "raft_progress": world["raft_progress"],
            "wood": world["wood"],
            "coconuts": world["coconuts"],
            "shelter": world["shelter"],
            "fire_lit": world["fire_lit"] or world["activity"] in ("cook", "tend_fire", "sleep"),
            "trinkets": world["trinkets"],
        },
        "needs": {k: int(v) for k, v in world["needs"].items()},
        "relations": world["relations"],
        "milestones": [{"name": k, "label": MILESTONES[k], **v}
                       for k, v in world["milestones"].items()],
        "memory": world["memory"][-6:],
        "brain": "scripted",
    }


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
