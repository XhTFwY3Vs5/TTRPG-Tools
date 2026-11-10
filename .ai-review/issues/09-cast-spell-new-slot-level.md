## Summary
After a caster gains a new spell level (their `spell_slots` in `campaign.json` grows), `memorize_spells` accepts spells of the new level, but `cast_spell` refuses them with "No level N slots remaining" until the next `rest()`. The per-slot state that `cast_spell` reads has no key for the new level, and it defaults to 0. `use_spell_slot` handles the same case correctly by defaulting to the maximum.

## Context
`campaign.json` holds each caster's maximum slots (`characters.<key>.spell_slots`, e.g. `{"1": 2, "2": 1}`). `state.json` holds remaining slots (`characters.<key>.spell_slots`). `rest()` overwrites the state copy with the config copy. Between rests, a level-up changes only the config.

Relevant code (pinned to 2965eb7):
- [`tools/party.py#L395-L456`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/party.py#L395-L456): `cast_spell`. `slot_state = cstate.setdefault("spell_slots", dict(slot_cfg))` copies the config only when the state has **no** slot dict at all. Then `remaining = slot_state.get(lvl_key, 0)` at L435.
- [`tools/party.py#L490-L535`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/party.py#L490-L533): `use_spell_slot`. `current = slot_state.get(lvl_key, max_slots)`, the correct default.
- [`tools/party.py#L335-L386`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/party.py#L323-L374): `memorize_spells` validates against `slot_cfg` (the config), so it accepts the new level.
- `party_status`, `character_status` and `spell_status` all display `slot_state.get(lvl, max_count)`, so the UI shows the slot as available.

## Evidence
```python
T("rest", character="Bob")                        # state spell_slots = {"1": 1}
# campaign.json: Bob.spell_slots -> {"1": 2, "2": 1}  (level-up)
T("memorize_spells", character="Bob", spells=[{"name": "Web", "level": 2}])
# -> {'memorized': ['Web'], 'by_level': {'2': ['Web']}}
T("cast_spell", character="Bob", spell_name="Web")
# -> {'error': "No level 2 slots remaining — cannot cast 'Web'."}
```

## Proposed design
Make `cast_spell` default a missing level key to the configured maximum, the same rule `use_spell_slot` and the status tools use:
```python
remaining = slot_state.get(lvl_key, int(slot_cfg.get(lvl_key, 0)))
```
Keep the error when the configured maximum is also 0. That case is a caster with no slots at that level. Change its message to say so: `f"{label} has no level {spell_level} spell slots."`. Today the message is the misleading "no slots remaining".

## Implementation steps
1. In `tools/party.py` `cast_spell`, replace the `remaining = slot_state.get(lvl_key, 0)` line as above.
2. Split the `remaining <= 0` error. If `int(slot_cfg.get(lvl_key, 0)) == 0`, return the "has no level N spell slots" message; otherwise keep the existing "No level N slots remaining" message.

## Acceptance criteria
- [ ] The repro above returns a successful cast with `slots_remaining["2"] == 0`.
- [ ] Casting a second level-2 spell afterwards returns "No level 2 slots remaining".
- [ ] A caster with no level-3 slots who memorises a level-3 spell gets "has no level 3 spell slots". (`memorize_spells` should already block this; test `cast_spell` directly by writing state.)
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_party_tools.py` with `TempCampaign` (#1). Cases: the level-up repro; the normal decrement; exhausted slots; a level with no slots configured.

## Out of scope
- Do not change `rest`, `memorize_spells` or `use_spell_slot`.
- Do not migrate existing `state.json` files.

## Risks and gotchas
- Slot keys are strings in JSON (`"1"`). `slot_cfg` may contain ints if a caller passed them through `add_character`. Coerce with `str(spell_level)` (already done) and `int(...)` on the value.

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: medium | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
