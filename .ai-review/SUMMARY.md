# AI review — XhTFwY3Vs5/TTRPG-Tools — 2026-11-10
Reviewed at 2965eb7e513f77f793112528619717e9d7d3a4fa.

## Purpose
An MCP server plus Flask dashboard that gives an AI Dungeon Master deterministic AD&D 2e tools (dice, combat, saves, reactions, treasure, travel, clocks, lore) so that rules and world state, not the narration, decide outcomes. The users are a DM, or a solo player driving Claude as the DM, running long campaigns locally.

## Stack
Python 3.10+ (checked on 3.13). FastMCP (`mcp<2`), Flask, SQLite reference DBs (gitignored, built by `tools/import_wiki_*`), JSON/JSONL campaign state, an external `dungml` HTTP service for maps, and `claude -p` subprocesses for `/play`. Runs locally only; no deploy.

## Build and test
- `pip install -r requirements.txt`: OK.
- `python3 -m unittest discover -s tests`: 134 tests, **2 failures** (they depend on a missing `global/2e.db`).
- There is no CI, no linter config and no type-check setup.

## Findings
| id | title | type | sev | conf | effort | blocked by |
|---|---|---|---|---|---|---|
| 01 | Make test suite pass on fresh checkout + shared fixtures | tests | medium | verified | S | — |
| 02 | Add GitHub Actions CI | tests | medium | verified | S | 01 |
| 03 | Dashboard: reject cross-origin / DNS-rebinding requests | security | high | verified | M | 01 |
| 04 | Remove unsafe tar extract fallback | security | medium | verified | S | 01 |
| 05 | Combat session leaks across campaign switch | bug | high | verified | S | 01 |
| 06 | Missed attack disrupts spell | bug | high | verified | S | 01 |
| 07 | declare_spell casting time / 'casting' persist past the round | bug | high | verified | S | 01 |
| 08 | resolve_attack/combat_round turn pointer desync | bug | high | verified | M | 01 |
| 09 | cast_spell refuses newly gained slot levels | bug | medium | verified | S | 01 |
| 10 | saving_throw ignores class/level, no modifiers; skill_check name matching | bug | medium | verified | M | 01 |
| 11 | Corrupt JSON stores (secrets etc.) overwritten on next write | bug | medium | verified | S | 01 |
| 12 | create_* overwrite files; slugs unvalidated (path traversal) | bug | medium | verified | M | 01 |
| 13 | World clocks drift from calendar (needs-decision) | bug | medium | verified | M | — |
| 14 | Document reference DB bootstrap | docs | medium | verified | S | — |
| 15 | Cleanup: negative coin, disposition rounding, level-up hint | bug | low | verified | S | 01 |
| 16 | Log saving_throw / skill_check events | feature | medium | verified | S | 01, 10 |
| 17 | travel: apply weather pace + log encounter checks | feature | medium | verified | M | 01 |
| 18 | generate_store tool (TODO.md) | feature | medium | verified | M | 01 |

## Implementation order
01, then 02, 03, 05, 06, 07, 08, 04, 09, 10, 11, 12, 15, 14, then 16, 17, 18. Issue 13 waits on a human decision.

## Considered but not filed
- `tools/dm_session.py` fallback path passes the player message as a positional argv. A message starting with `-` may be parsed as a CLI flag. This path is only used when the resident process fails; how the CLI parses it is unverified.
- `/play` agent runs with unrestricted `Bash`/`Write` (`_ALLOWED_TOOLS`). That is a product decision; issue 03 mitigates it.
- Werkzeug debugger is on by default for loopback binds. Low risk after the Host check in issue 03.
- `apply_damage` does not sync the live combat tracker, and `apply_combat_heal` revives the dead (HP ≤ -10). Both are minor and documented workflows avoid them.
- `roll_initiative` keys totals by name, so duplicate names collide. `setup_combat` numbers duplicates, so this only hits hand-added duplicates.
- `lookup.py` searches a sibling `../ttrpg` path for monsters.db. It is an oddity, mentioned in 14.
- Dashboard (`dashboard.py`, 11k lines) was reviewed for routes and security only, not line by line.
- The reaction table direction vs DMG Table 59 is speculative, so it was dropped.
