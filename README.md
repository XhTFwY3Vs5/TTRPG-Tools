# TTRPG-Tools

An MCP-backed campaign toolkit for running **AD&D 2nd Edition** games with an AI
Dungeon Master. It gives the DM a large library of deterministic tools — dice,
combat, saving throws, NPC reactions, treasure, weather, travel, rules lookup —
plus a Flask dashboard and two map renderers, so the mechanics stay honest and
the world stays consistent across sessions.

The design principle throughout: the AI narrates, but the **tools decide**. Every
check, attack, reaction, and table result comes from a real roll against a real
rules database.

---

## What's inside

| Layer | What it does |
|---|---|
| **MCP server** (`server.py`) | Registers ~40 tool modules with Claude Code (or any MCP client). This is the core. |
| **Dashboard** (`dashboard.py`) | Flask web UI: party status, combat, session log, character index, locations, maps, reference browsers. |
| **World maps** (`tools/world_map.py`) | A Structurizr-style DSL for overland geography, compiled to per-view GeoJSON and rendered with Leaflet. |
| **Tactical maps** (`tools/combat_map.py`) | Hands a `.dmap` DSL to the external **dungml** service for SVG battle maps with fog of war. |
| **Reference DBs** (`global/`, `settings/`) | Monster stats, 2e rules, items, and a structured Greyhawk wiki snapshot, all queryable. |

---

## Tool modules

`server.py` imports each module in `tools/` and calls its `register(mcp)`. A new
module must be added to **both** the import line and the register list, or it
silently won't exist.

```
dice          party         combat        combat_map    dungml_play
world         world_map     lore          log           lookup
images        campaign_mgmt secrets       rules         survival
factions      weather       travel        rumors        hirelings
quests        audit         holdings      greyhawk      wordlist
pre_check     composites
```

Highlights:

- **`composites.py`** — the pipeline tools that keep a turn fast: `setup_combat`,
  `resolve_attack`, `combat_round`, `scout`, `council`, `batch`. These run a
  mandated sequence server-side in one round trip.
- **`combat.py`** — the full combat tracker: initiative, attack resolution,
  conditions, morale, spell slots, active effects, turning undead.
- **`rules.py` / `lookup.py`** — FTS5 search over PHB/DMG/MM and lookups for
  monsters, spells, classes, items, abilities, proficiencies.
- **`greyhawk.py`** — structured queries against the Greyhawk wiki snapshot.
- **`world_map.py`** — DSL parser, style cascade, GeoJSON compiler, distance
  routing (direct + via-roads, surface-weighted).

---

## Requirements

- Python 3.10+
- The packages in `requirements.txt`:

  ```
  mcp
  flask
  python-dotenv
  replicate
  openai
  ```

- **Optional:** `dungml` (a separate repo) running as `dmap-server` for tactical
  maps and fog of war. Play is never blocked on it — the map tools degrade to a
  no-op when it isn't running.

---

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Register the MCP server with your client. `.mcp.json` ships a template — edit the
paths for your machine:

```json
{
  "mcpServers": {
    "ttrpg": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["/path/to/server.py"],
      "cwd": "/path/to/repo"
    }
  }
}
```

Some tools that generate images or narration call Replicate/OpenAI and read
credentials from a `.env` file (gitignored). The game runs without them.

---

## Running

Three processes, all local. Only the first is required to play.

| Process | Start it with | Serves |
|---|---|---|
| **MCP server** | launched by your MCP client (`python3 server.py`) | every DM tool |
| **Dashboard** | `python3 dashboard.py [--port 5000]` | `/party`, `/combat`, `/log`, `/maps`, reference browsers |
| **dmap-server** | `uv run dmap-server` (from the `dungml` repo) | tactical map rendering + fog of war |

Dashboard routes:

- `/` — scene image gallery
- `/party` — live party status
- `/combat` — tactical map + combat tracker
- `/log` — session chronicle
- `/characters` — NPC index with portraits
- `/locations` — two-level location browser
- `/maps` — world-map index (per-slug Leaflet views)
- `/sheets/<slug>` — character sheet
- `/abilities`, `/proficiencies`, `/turning`, `/reference` — rules browsers

---

## Tests

```bash
python3 -m unittest discover -s tests
```

Run before committing. Existing suites cover the composites, DM session
bookkeeping, the dungml play integration, the session primer, and weapon attacks.

---

## Repo layout

```
server.py            MCP entry point — imports + registers tool modules
dashboard.py         Flask web app
dashboard_items.py   dashboard helpers for item rendering
dashboard_monsters.py dashboard helpers for monster rendering
_campaign.py         shared campaign-path / state helpers
names.py             NPC name generation
treasure.py          treasure tables
statusline.py        Claude Code status line (live campaign state)
workspace.dsl        sample world-map DSL

tools/               ~40 MCP tool modules (see above)
tests/               unittest suites
static/              dashboard JS/CSS (incl. Leaflet map renderer)
settings/greyhawk/   structured Greyhawk wiki snapshot (SQLite)
global/              reference DBs + encounter tables + house rules
campaigns/           per-campaign data (created at runtime)
```

### Campaign data

Each campaign lives under `campaigns/<slug>/` and holds `campaign.json`
(party + settings), `state.json` (time, weather, HP), `events.json`, character
and location files, `secrets.json` (DM-only notes), and `maps/`. Generated
images, TTS audio, and compiled reference DBs are gitignored — they're
regenerable.

---

## Documentation

`AGENTS.md` is the DM's operating manual: the hard procedural constraints
(die-first resolution, no fallback mechanics, rolled NPC consensus, the fourth
wall), the combat pipeline, the end-of-scene checklist, and the full tool
reference. Read it before changing DM behaviour.

`WORLD-MAP-PLAN.md` documents the world-map DSL design. `TODO.md` tracks pending
work.
