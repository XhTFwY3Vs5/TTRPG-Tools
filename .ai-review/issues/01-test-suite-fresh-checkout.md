## Summary
Two unit tests fail on every fresh checkout because they silently depend on `global/2e.db`, a gitignored, locally built SQLite database. Most other issues from this review add tool-level tests, and those need a shared way to stand up a throwaway campaign and a tiny reference DB. This issue adds both.

## Context
`AGENTS.md` says to run `python3 -m unittest discover -s tests` before committing. The suite is all pure-function tests. None of them create a campaign, and none provide the reference database that `tools/party.py` reads.

Relevant code (pinned to 2965eb7):
- [`tools/party.py#L82-L113`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/party.py#L82-L113): `_con_hp_bonus`. Returns `0` whenever `global/2e.db` is missing (`if con is None or not _2E_DB.exists(): return 0`).
- [`tools/party.py#L31-L79`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/party.py#L31-L79): `_spell_level`. Returns `None` when the DB is missing, so `_normalize_memorized` leaves level `0`.
- [`tests/test_weapon_attacks.py#L172-L176`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tests/test_weapon_attacks.py#L172-L176): `test_bare_names_get_levels_resolved`. Expects levels `[1, 2]`.
- [`tests/test_weapon_attacks.py#L192-L198`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tests/test_weapon_attacks.py#L192-L198): `test_warrior_gets_the_parenthetical_value`. Expects `3`.
- [`_campaign.py#L8-L11`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/_campaign.py#L8-L11): `BASE_DIR`, `CAMPAIGNS_DIR`, `ACTIVE_FILE`, `_2E_DB` are module-level paths. `campaign_dir()` and `get_active_name()` read them at call time, so a test can patch them.
- `.gitignore` ignores `global/*.db`.

## Evidence
On a clean clone with `pip install -r requirements.txt` (Python 3.13):
```
$ python3 -m unittest discover -s tests
FAIL: test_warrior_gets_the_parenthetical_value ... AssertionError: 0 != 3
FAIL: test_bare_names_get_levels_resolved ... AssertionError: Lists differ: [0, 0] != [1, 2]
Ran 134 tests in 0.030s
FAILED (failures=2)
```
`ls global/` shows only `encounter_tables.json`, `house-rules` and `reference`. There is no `2e.db`.

## Proposed design
Add a test-only helper module, `tests/_fixtures.py`, with two pieces:

1. `make_2e_db(path: Path) -> Path`: creates a minimal SQLite DB using the **same column names production code queries**:
   - `spells(id INTEGER PRIMARY KEY, name TEXT COLLATE NOCASE, level INTEGER, caster TEXT, casting_time TEXT, casting_time_init INTEGER)`, seeded with at least: `("Cure Light Wounds", 1, "priest", "5", 5)`, `("Hold Person", 2, "priest", "5", 5)`, `("Hold Person", 3, "wizard", "3", 3)`, `("Sleep", 1, "wizard", "1", 1)`, `("Web", 2, "wizard", "2", 2)`.
   - `ability_scores(ability TEXT, score TEXT, data TEXT, sort_order INTEGER)`, seeded with constitution rows for `9` (`{"hp_adj": "0"}`) and `17` (`{"hp_adj": "+2(+3)"}`).
   - `classes(id INTEGER PRIMARY KEY, name TEXT)` and `class_levels(class_id INTEGER, level INTEGER, save_paralysis INTEGER, save_rsw INTEGER, save_petrify INTEGER, save_breath INTEGER, save_spell INTEGER)`, seeded with Fighter levels 1 and 9 (PHB Table 60: L1 = 14,16,15,17,17; L9 = 8,10,9,9,11).
2. `class TempCampaign`: a context manager (also usable from `setUp`/`tearDown`) that:
   - creates a `tempfile.TemporaryDirectory` holding `campaigns/<slug>/campaign.json`, built from a `characters` dict passed to the constructor, plus `global/2e.db` from `make_2e_db`;
   - patches with `unittest.mock.patch.object`: `_campaign.CAMPAIGNS_DIR`, `_campaign.ACTIVE_FILE`, `_campaign._2E_DB`, `tools.party._2E_DB`, `tools.combat._2E_DB`;
   - writes the slug to the patched `ACTIVE_FILE`, calls `_campaign._invalidate()`, sets `tools.combat._session = None`, and clears `tools.party._spell_level_cache` and `tools.combat._spell_ct_cache`, both on enter and on exit;
   - exposes `call(tool_name, **kwargs)`, which builds a `FastMCP("test")` once, registers `dice`, `party`, `combat`, `log`, `factions`, `campaign_mgmt`, `world` and `secrets`, and returns `asyncio.run(mcp._tool_manager.call_tool(name, kwargs))`. This is the same dispatch path `tools/composites.call_tool` uses.

Then point the two failing test classes at a DB built by `make_2e_db`.

Alternatives considered: `skipUnless(_2E_DB.exists())` hides the tests on exactly the machines (fresh checkouts, CI) where they matter. Committing a real `2e.db` puts scraped reference data into git, which `.gitignore` deliberately avoids.

## Implementation steps
1. Create `tests/_fixtures.py` with `make_2e_db` and `TempCampaign` as specified above. Add a module docstring that says how to use it.
2. In `tests/test_weapon_attacks.py`, in `SpellLevelTests` and `ConHpBonusTests`, add `setUp` and `tearDown` that build a DB with `make_2e_db` in a temp dir, `patch.object(party, "_2E_DB", path)`, and clear `party._spell_level_cache`.
3. Add `tests/test_fixtures.py` with one smoke test: `TempCampaign(characters={...one fighter...})`, then `call("party_status")` returns that character. Also assert that the real `campaigns/` directory's mtime did not change.

## Acceptance criteria
- [ ] On a checkout with no `global/2e.db`, `python3 -m unittest discover -s tests` passes with 0 failures.
- [ ] With a real `global/2e.db` present, the suite still passes (the fixture DB is used regardless).
- [ ] Running the suite creates or modifies nothing under the repo's `campaigns/` or `global/` (`git status --porcelain` is empty afterwards).

## Test plan
- Existing `SpellLevelTests` and `ConHpBonusTests` now run against the fixture DB: bare names resolve to 1 and 2, CON 17 gives 3 for a Fighter and 2 for a Thief, CON 9 gives 0, missing CON gives 0.
- New `tests/test_fixtures.py` smoke test for `TempCampaign`.
- Run: `python3 -m unittest discover -s tests`.

## Out of scope
- Do not change any production code under `tools/`, `_campaign.py` or `dashboard.py`.
- Do not add pytest or any new dependency. Stay on `unittest`.
- Do not commit any `.db` file.

## Risks and gotchas
- `_spell_level` and `_spell_casting_time` cache negative lookups in module-level dicts. Forgetting to clear them makes test order matter.
- `_campaign.load_campaign` caches by mtime. Always call `_campaign._invalidate()` after patching paths.
- `tools.party` and `tools.combat` hold their own `_2E_DB` constants and do not read `_campaign._2E_DB`. Patch each module separately.

## Dependencies
Blocked by: none

## Metadata
Type: tests | Severity: medium | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
