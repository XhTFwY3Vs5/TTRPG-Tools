## Summary
Three small correctness nits in the party and reaction tools, bundled into one PR: `update_coin` lets the party's purse go negative; negative dispositions and reputations round toward -∞, so they bite harder than the equivalent positive values help; and `award_xp`'s level-up hint stops at level 4 and covers only some classes.

## Context
All three are pure bookkeeping helpers the DM calls many times per session.

Relevant code (pinned to 2965eb7):
- [`tools/party.py#L700-L720`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/party.py#L700-L720): `update_coin`. `coin[denom] = coin.get(denom, 0) + amount` has no floor.
- [`tools/dice.py#L181`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L181) `disposition_applied = disp // 20`, and [`#L190`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L190) `rep // 25`. The same expression is echoed at [`tools/world.py#L490`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/world.py#L490).
- [`tools/party.py#L138-L160`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/party.py#L138-L160): `_XP_THRESHOLDS` hard-codes levels 2–4 for 11 class keywords. `_level_up_hint` returns `""` above 4th level.

## Evidence
- `update_coin("-50gp")` with 0 gp returns `{'coin': {'gp': -50, ...}}` (run against a scratch campaign).
- Python floor division: `-1 // 20 == -1` but `1 // 20 == 0`, and `-21 // 20 == -2` but `21 // 20 == 1`. A "Wary" NPC at -5 gets -1 to reactions, while a "Cordial" one at +19 gets nothing.
- `_level_up_hint({"cls": "Fighter"}, 7000, 9000)` returns `""`, though 8,000 XP is level 4, and `(15000, 17000)`, which crosses 16,000 (level 5), also returns `""`.

## Proposed design
1. **Coin.** If `coin.get(denom, 0) + amount < 0`, return `{"error": f"Not enough {denom}: have {have}, need {-amount}. Convert coin first or adjust the amount."}` and do not save. Do not convert between denominations automatically; that is a DM choice.
2. **Rounding.** Use truncation toward zero, `int(disp / 20)` and `int(rep / 25)`, in `dice.reaction` and in `world.set_disposition`'s `modifier_applied_to_reaction`. Then ±19 gives 0 and ±20 gives ±1, symmetric.
3. **Level-up hint.** When `global/2e.db` is present, read `class_levels` for the character's class (`SELECT level, xp FROM class_levels cl JOIN classes c ON c.id = cl.class_id WHERE lower(c.name) = lower(?) ORDER BY level`; confirm the XP column name in `tools/import_wiki_classes.py` first), and report the highest level whose threshold was crossed. Fall back to the existing table when the DB or class is missing.

## Implementation steps
1. `tools/party.py` `update_coin`: add the floor check.
2. `tools/dice.py` `reaction`: change both divisions. `tools/world.py` `set_disposition`: change the reported modifier the same way.
3. `tools/party.py`: add `_xp_table(cls) -> list[tuple[int, int]] | None`, reading 2e.db with an `lru_cache`, and use it in `_level_up_hint` before the hard-coded fallback.
4. Update the `reaction` docstring ("adds disposition/20, truncated toward zero").

## Acceptance criteria
- [ ] `update_coin("-50gp")` with 10 gp returns an error and leaves gp at 10. `update_coin("-10gp")` leaves 0.
- [ ] `reaction(npc=...)` with disposition -5 applies modifier 0, with -20 applies -1, and with +20 applies +1.
- [ ] With the fixture DB from #1 extended with Fighter XP rows, `_level_up_hint({"cls": "Fighter", "label": "A"}, 15000, 17000)` mentions level 5.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_party_tools.py` and `tests/test_dice_tools.py` with `TempCampaign`. Cases: coin overdraw per denomination, exact zero, positive deltas unchanged; rounding at -21, -20, -19, -1, 0, 1, 19, 20, 21; level hint via DB, via fallback, unknown class, a multi-level jump.

## Out of scope
- No automatic coin conversion. No change to the reaction table itself.

## Risks and gotchas
- Add the XP column to the fixture DB in `tests/_fixtures.py` (#1) rather than creating a second fixture.

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: low | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
