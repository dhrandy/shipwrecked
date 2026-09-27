# Shipwrecked (beta)

**One guy. One island. A story that keeps going while you're away.**

Shipwrecked is a self-hosted web page starring Wade, a castaway on a little
island with one palm tree (named Pal). It's a riff on the classic "story-telling
screensaver" idea - day and night, weather, holidays, routines, and a slow
story arc - but the whole world runs on your server, so Wade's story advances
even when nobody has the page open. Come back after a day away and the page
catches you up on everything he got into.

The scene is built from real modeled glTF assets (loaded with Three.js
GLTFLoader): Wade is the KayKit Adventurers Barbarian (CC0, by Kay Lousberg -
kaylousberg.com), and the island props come from Quaternius (CC0 - palm trees,
rocks, chest, coins, barrel, anchor, bottle, wood, shark, bird) and the Google
Poly archive (CC-BY 3.0 - campfire, seashell, crab by Poly by Google; log raft
by Adam Marc Williams). The island, ocean shader, sky, and small beach props
are procedural. There are no tracking pixels, no
external requests, and nothing here reuses anyone else's copyrighted work.

## What Wade does

- **Daily routines** - fishing off the rocks, jogging, sandcastles, raft
  building, bottles to the sea, stargazing, napping in the shade of Pal the
  palm. His gear lives in the camp chest; the lid stands open while he's
  using something.
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

In 0.6.0 the scene moved to real modeled assets: Wade is a KayKit character
with a full animation rig - he idles, walks, jogs, sits to fish, lies down to
sleep, picks things up, cheers at the horizon - and the island is dressed with
Quaternius and Google Poly models (palms, rocks, chest, campfire, raft, shark
and more). The ocean got traveling breakers that roll toward the beach, an
animated wash, and a live contact line where the sea meets the sand. Still
zero external requests: every asset is vendored in the image.

## Run it

```yaml
services:
  shipwrecked:
    image: ghcr.io/dhrandy/shipwrecked:0.6.0
    container_name: shipwrecked
    restart: unless-stopped
    ports:
      - "8647:8647"
    environment:
      TZ: ${TZ}
      SIM_SPEED: ${SIM_SPEED}
      SHIPWRECKED_DB: ${SHIPWRECKED_DB}
    volumes:
      - shipwrecked-data:/data
volumes:
  shipwrecked-data:
```

Create a `.env` file next to the compose file. Docker Compose reads it
automatically. Copy this example and change the values for your setup:

```dotenv
TZ=America/New_York
SIM_SPEED=1
SHIPWRECKED_DB=/data/shipwrecked.db
```

Any tool that accepts a compose file works: paste the block into a new stack
in Portainer, Dockhand, CasaOS, Synology Container Manager, or similar, and
set the same variables in its Environment tab instead of a `.env` file. To
build from source instead of pulling the image, swap the `image:` line for
`build: https://github.com/dhrandy/shipwrecked.git#main`.

Then open `http://your-server:8647`. Works on phones too.

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

- Beta while it's still being tested. Fixes bump the patch (`0.6.x`), feature
  batches bump the minor (`0.x.0`). Images carry both the version tag and
  `latest`. A version bump touches four places: `app.py` VERSION, the
  compose image tag, `.github/workflows/docker.yml` tags, and this README.
- The story survives restarts and container rebuilds (it's in the volume).
  Delete the volume to strand Wade all over again.
- Nothing phones home; the page makes zero external requests.
