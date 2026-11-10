## Summary
`saving_throw` uses one fixed set of generic save targets (16/18/17/20/19) whenever a sheet has no `saves` entry. It ignores the class and level, even though `_campaign.base_saves_for(cls, level)` exists for exactly this, and it offers no way to apply a modifier. Separately, `skill_check` matches skill names literally, so `scout()`'s default checks (`"move silently"`, …) never match the snake_case keys (`move_silently`) that sheets and the dashboard use.

## Context
These two tools resolve most non-combat uncertainty. Hard Constraint #1 requires the DM to call them before narrating. When the target is wrong or the call errors, the DM either gets an unfair roll or falls back to narrating, which is exactly what Hard Constraint #3 forbids.

Relevant code (pinned to 2965eb7):
- [`tools/dice.py#L459-L496`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L459-L496): `saving_throw`. `target = by_type.get(..., [16, 18, 17, 20, 19][idx])` falls back to fixed defaults. It has no `modifier` parameter and does not read combat effects.
- [`_campaign.py#L40-L61`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/_campaign.py#L40-L61): `base_saves_for(cls, level)` reads class saves from `global/2e.db`. Its only caller is `add_character` ([`tools/campaign_mgmt.py#L337`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/campaign_mgmt.py#L337)).
- [`tools/combat.py#L1038-L1075`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L1028-L1063): `apply_effect` stores `save` modifiers, and its docstring admits "saving_throw() does not consult effects".
- [`tools/dice.py#L578-L611`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L578-L604): `skill_check`. `if skill_lower in k.lower()`, a literal substring match.
- [`tools/dice.py#L619-L650`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L619-L650): `scout`, which defaults to `["move silently", "hide in shadows", "detect noise"]`.
- [`dashboard.py#L7272-L7283`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/dashboard.py#L7272-L7283) and [`_campaign.py#L467-L473`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/_campaign.py#L460-L465): the renderers call `.replace('_', ' ')` on skill keys, so snake_case keys are the expected format.

## Evidence
Level-9 Fighter Ann with no `saves` on her sheet and `skills: {"move_silently": 40, "hide_in_shadows": 30}`:
```
skill_check("Ann", "move silently")
-> {'error': "Skill 'move silently' not found for Ann. Available: ['move_silently', 'hide_in_shadows']"}
saving_throw("Ann", "spell")
-> {'save': 'Spell/Magic', 'roll': 12, 'target': 19, ...}   # PHB Table 60: L9 warrior vs spell is 11
```

## Proposed design
**saving_throw(character, save_type, modifier: int = 0)**
- Target resolution order: the sheet's `saves` for that type, then `_c.base_saves_for(char["cls"], char["level"])`, then the existing fixed defaults. Report which one was used as `"target_source": "sheet" | "class_table" | "default"`.
- `modifier` is added to the d20 (2e convention: bonuses add to the roll). The total is the sum of `modifier` and any `save` values from active combat effects on that character. Find these through `tools.combat.get_session()`, matching the combatant whose `_key` equals the character key. Return `roll`, `modifier`, `effect_modifier`, `total` and `success = total >= target`.
- Do **not** add automatic success on a natural 20 or failure on a natural 1. 2e has no such rule for saving throws.

**skill_check matching**
- Normalise both the needle and the keys with `_norm = lambda s: re.sub(r"[\s_\-]+", " ", s.lower()).strip()`. Try an exact normalised match first, then substring.
- Add `modifier: int = 0` (percentage points, added to the target, clamped to 1..99). Return `target` (the base) and `effective_target`.

## Implementation steps
1. `tools/dice.py` `saving_throw`: add the `modifier` parameter, the resolution chain, and the effect lookup (wrap the `tools.combat` import and lookup in `try/except`, so a missing session means 0). Update the docstring.
2. `tools/dice.py` `skill_check`: add `_norm`, exact-then-substring matching, and the `modifier` parameter.
3. `tools/party.py` `character_status` (L204–L249): use the same chain (sheet, then `base_saves_for`, then defaults) for its `saves` output, so the status tools and `saving_throw` agree.
4. `tools/combat.py` `apply_effect` docstring: change the `save:` line to say `saving_throw` applies it automatically.

## Acceptance criteria
- [ ] With the fixture DB from #1 (Fighter L9 spell save 11), `saving_throw("Ann", "spell")` returns `target == 11` and `target_source == "class_table"`.
- [ ] `saving_throw("Ann", "spell", modifier=2)` with `random.randint` patched to 9 gives `total == 11` and `success`.
- [ ] With an active effect `apply_effect("Ann", "prayer", 3, save=1)`, `effect_modifier == 1`.
- [ ] `skill_check("Ann", "move silently")` and `skill_check("Ann", "Move-Silently")` both resolve to `move_silently`.
- [ ] `scout("Ann")` returns rolled results for the two skills Ann has, plus an error entry for `detect noise`.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_dice_tools.py` with `TempCampaign` (#1). Patch `tools.dice.random.randint` for determinism. Cases: sheet saves win over the class table; the class table when the sheet is empty; the default when class is unknown; the modifier; the effect modifier; no combat session; skill normalisation (spaces, underscores, hyphens, case); the skill modifier clamp; ambiguous substring (first exact match wins).

## Out of scope
- No ability-score magical-defence adjustments (WIS vs charm and so on). The DM passes those through `modifier`.
- Do not log new events here. That is a separate feature issue.

## Risks and gotchas
- `base_saves_for` needs `level`. Sheets without `level` must fall through to the defaults, not raise.
- Multi-class strings like "Fighter/Mage" won't match `classes.name`. Falling through to the defaults is acceptable; note it in the docstring.
- Importing `tools.combat` from `tools.dice` at module import time creates a cycle (`combat` imports `composites`, which is fine, but keep the import inside the function).

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: medium | Confidence: verified | Effort: M | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
