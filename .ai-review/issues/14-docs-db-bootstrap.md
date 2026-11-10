## Summary
A fresh checkout has none of the reference databases that most tools read (`global/2e.db`, `global/monsters.db`, `global/rules.db`), and the README never says how to build them. `tools/build_2e_db.py`, the most obvious-looking builder, fetches from an upstream that now returns 404, and running it after the wiki importers would `DROP` their tables. New users get silently degraded tools (spell levels 0, generic saves, empty monster lookups) with no hint why.

## Context
`.gitignore` excludes `global/*.db` ("regeneratable"). The importers' docstrings document their usage individually. Nothing ties them into an order.

Relevant code (pinned to 2965eb7):
- [`README.md#L76-L101`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/README.md#L76-L101): Setup covers only the venv and `.mcp.json`.
- [`tools/build_2e_db.py#L1-L24`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/build_2e_db.py#L1-L24): fetches `raw.githubusercontent.com/brandonm4/2ERPGDB/...`, which is gone per commit 58ea4f2's message, and does `DROP TABLE IF EXISTS spells`.
- [`tools/import_wiki_spells.py#L1-L40`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/import_wiki_spells.py#L1-L40): writes `spells` into 2e.db and warns about `build_2e_db.py`.
- `tools/import_wiki_classes.py` (`classes`, `class_levels`), `tools/import_wiki_equipment.py` and `tools/import_wiki_magic_items.py` (`items`), `tools/build_phb_ref.py` (`ability_scores`, `proficiencies`, `turning_undead`; no inputs), `tools/import_wiki_monsters.py` (`global/monsters.db`), `tools/build_rules_db.py` (`global/rules.db` from `global/reference/{phb,dmg,monstermanual}.txt`, gitignored and supplied by the user), `tools/build_encounter_tables.py` (reads monsters.db; output already committed).
- [`tools/lookup.py#L13-L20`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/lookup.py#L13-L20): `_MONSTERS_DB_CANDIDATES` also looks in `../ttrpg/global/monsters.db`, a sibling checkout path that is undocumented.

## Evidence
`ls global/` on a fresh clone shows `encounter_tables.json  house-rules  reference`. `monster_lookup` returns its "not found" path, `memorize_spells` reports `unresolved_levels` for every spell, and `rules_lookup` has no DB.

## Proposed design
Add a "Reference data" section to the README, after Setup, with:
1. A table: DB → tables → built by → needs network? → needs user-supplied files?
2. The exact bootstrap order, as copy-pasteable commands:
   ```bash
   python3 tools/build_phb_ref.py
   python3 tools/import_wiki_classes.py
   python3 tools/import_wiki_spells.py
   python3 tools/import_wiki_equipment.py
   python3 tools/import_wiki_magic_items.py
   python3 tools/import_wiki_monsters.py --no-images
   # optional, needs your own copies of the books as text:
   python3 tools/build_rules_db.py
   ```
   Before writing these, the implementer must open each script and confirm its exact flags and that the order is safe. Check whether any script `DROP`s a table another one writes. Adjust the order if needed.
3. A warning box: do not run `tools/build_2e_db.py`; its upstream is gone and it drops the wiki-imported `spells`.
4. A "what degrades without it" line per DB.

Also add a module-level `DEPRECATED` note at the top of `tools/build_2e_db.py`'s docstring that points to the README section. Do not delete the script.

## Implementation steps
1. Read each builder's docstring and `argparse` setup, and confirm the order and flags.
2. If possible, run the bootstrap in a scratch checkout and record the wall time. Network to `adnd2e.fandom.com` is required.
3. Write the README section, and add a one-line pointer in AGENTS.md "Running the System".
4. Add the deprecation note to `tools/build_2e_db.py`.

## Acceptance criteria
- [ ] Following only the README on a fresh clone produces `global/2e.db` and `global/monsters.db`, and `monster_lookup("orc")` returns stats.
- [ ] The README states which steps need network and which need user-supplied text files.
- [ ] `python3 -m unittest discover -s tests` passes (docs-only change, apart from a docstring).

## Test plan
Manual: run the documented commands in a scratch clone and confirm the DBs exist and contain rows (`sqlite3 global/2e.db "select count(*) from spells"`).

## Out of scope
- Do not change any importer's behaviour. Do not commit any DB.
- Do not remove the `../ttrpg` candidate path (mention it only).

## Risks and gotchas
- The wiki import can take a long time and hits a third-party site. Mention rate and courtesy (the scripts may already sleep between requests; check).

## Dependencies
Blocked by: none

## Metadata
Type: docs | Severity: medium | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
