# Shipwrecked (beta)

**One guy. One island. A story that keeps going while you're away.**

Shipwrecked is a self-hosted web page starring Wade, a castaway on a tiny island
with one palm tree (named Pal). It's a riff on the classic "story-telling
screensaver" idea - day and night, weather, holidays, routines, and a slow
story arc - but the whole world runs on your server, so Wade's story advances
even when nobody has the page open. Come back after a day away and the page
catches you up on everything he got into.

All art is drawn procedurally in the browser. There are no image assets, no
external requests, and nothing here reuses anyone else's copyrighted work.

## What Wade does

- **Daily routines** - fishing, jogging, sandcastles, raft building, bottles
  to the sea, stargazing, napping in the shade of Pal the palm.
- **A brain, sort of** - Wade has needs (hunger, energy, mood, boredom,
  loneliness), a memory, opinions about the local wildlife, and a personality.
  Every few minutes he weighs his situation and *decides* what to do, commits
  to it for a while, hesitates when he's torn, and tells you why. It's
  scripted, not a real LLM - but it reads like a little mind at work.
- **Random events** - a nemesis seagull (Captain Beak), a shark named
  Mortgage, passing ships that never stop, dolphins, storms, shooting stars.
- **A 12-day story arc** - from Landfall to The Voyage to The Return, then
  the cycle starts over, slightly grander. The raft actually gets built.
- **The real sky** - day/night follow your server's timezone, the moon phase
  is the actual moon phase, and holidays (Halloween, Christmas, New Year,
  Talk Like a Pirate Day...) show up on the island.
- **Milestones** - his firsts (first fish, first bottle, raft complete) are
  detected from what actually happened and kept forever.

## Run it

```yaml
services:
  shipwrecked:
    build: https://github.com/dhrandy/shipwrecked.git#main
    image: shipwrecked:0.2.1
    container_name: shipwrecked
    restart: unless-stopped
    ports:
      - "8647:8647"
    environment:
      TZ: ${TZ:-America/New_York}
      SIM_SPEED: ${SIM_SPEED:-1}
    volumes:
      - shipwrecked-data:/data
volumes:
  shipwrecked-data:
```

Then open `http://your-server:8647`. Works on phones too.

No .env file is needed. The `${VAR:-default}` values work as-is; change them
in the compose file or in your Docker manager's environment settings.

| Setting | Default | What it does |
|---|---|---|
| `TZ` | `America/New_York` | Island timezone - day/night, holidays, and routines follow it |
| `SIM_SPEED` | `1` | Story speed multiplier (try `60` to watch a day fly by) |
| `SHIPWRECKED_DB` | `/data/shipwrecked.db` | Where the island's memory lives |

## API

Everything the page uses is plain JSON, so other clients (a kiosk, a tiny
e-ink display, an ESP32 gadget) can render Wade however they like:

- `GET /api/state` - full snapshot: phase, weather, moon, tide, Wade's
  activity, goal, thought, needs, world state, recent events
- `GET /api/state?since=<iso timestamp>` - also returns `catchup`: everything
  that happened since that time ("while you were away")
- `GET /api/log?limit=200` - the story log
- `GET /api/health` - `{"status":"ok"}`

The `state_line` field is the whole situation as one line of text
(`day 4 phase day hunger 62 ... raft 34 last fish`) - it's the seam where a
real LLM advisor could plug in later, and it's easy for a tiny client to parse.

## Development

```bash
pip install -r requirements.txt pytest
SHIPWRECKED_DB=/tmp/shipwrecked.db python app.py     # serves on :8647
SHIPWRECKED_DB=/tmp/shipwrecked.db pytest -q         # runs the test suite
```

## Notes

- Beta while it's still being tested. Versioning: `0.1.x` for fixes, `0.2.0`
  for the next feature batch.
- The story survives restarts and container rebuilds (it's in the volume).
  Delete the volume to strand Wade all over again.
- Nothing phones home; the page makes zero external requests.
