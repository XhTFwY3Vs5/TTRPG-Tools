## Summary
`declare_spell` permanently overwrites the caster's `weapon_speed` with the spell's casting time, and the `"casting"` condition is never cleared once the spell goes off. Every later round, the caster rolls initiative with that casting time even when swinging a weapon. Any hit in any later round also "disrupts" a spell that was finished long ago.

## Context
Per PHB initiative, a caster's initiative modifier is the casting time **for the round the spell is cast**. The tool's docstring agrees: "Must be called before roll_initiative() each round the spell is cast". The tracker has no notion of the round ending for the caster.

Relevant code (pinned to 2965eb7):
- [`tools/combat.py#L746-L778`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L746-L778): `declare_spell`. `caster["weapon_speed"] = ct_init` and `caster["conditions"].append("casting")`. The weapon speed is not saved anywhere.
- [`tools/combat.py#L716-L743`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L716-L743): `roll_initiative` uses `c["weapon_speed"]`.
- [`tools/combat.py#L1101-L1162`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L1101-L1162): `next_turn`. The new-round block (`if idx == 0:`) resets `attacks_this_round`, applies bleeding, and ticks effects. It never restores speed or clears `"casting"`.
- [`tools/combat.py#L256-L268`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L256-L268): `_apply_attack_block` sets `weapon_speed` from the selected weapon.

## Evidence
With `combat._spell_casting_time` stubbed to return `("1 round", 10)`:
```
declare_spell("Bob", "sleep"); roll_initiative(); next_turn() x3   # now round 2
Bob weapon_speed -> 10        (his dagger is speed 2)
roll_initiative() -> Bob: roll 9, wspeed 10, total 19
```
Without the stub (no DB), Bob's conditions are still `['casting']` in round 2.

## Proposed design
- `declare_spell` stores the pre-spell speed once: `caster.setdefault("base_weapon_speed", caster["weapon_speed"])`, then sets `weapon_speed = ct_init`. It records `caster["casting_round"] = _session["round"]`.
- In `next_turn`'s new-round block, for each combatant with `casting_round` set and less than the new round number: remove `"casting"` from `conditions`, restore `weapon_speed = base_weapon_speed`, and pop both keys. Append `"<name>'s spell resolves; initiative back to weapon speed N."` to the same report list as the expiry notes.
- `_apply_attack_block` (weapon switch) updates `base_weapon_speed` if present, rather than clobbering a declared casting time mid-round. If `casting_round` is set, write the block's speed to `base_weapon_speed` only.

Alternative: recompute weapon speed from the attack block at each new round. Rejected because enemies added with `add_combatant` have no attack blocks.

## Implementation steps
1. Edit `declare_spell` as described.
2. Edit the `if idx == 0:` block in `next_turn` to do the restore and clear.
3. Edit `_apply_attack_block` to respect `casting_round`.
4. Update the `declare_spell` docstring: "Applies to this round only; next_turn restores the weapon speed and clears 'casting' when the round ends."

## Acceptance criteria
- [ ] After `declare_spell` then `roll_initiative` then enough `next_turn` calls to reach round 2, the caster's `weapon_speed` equals the value before `declare_spell`, and `"casting"` is not in `conditions`.
- [ ] Within the declaring round, a damaging hit before the caster's turn still disrupts (unchanged behaviour).
- [ ] Declaring a spell two rounds in a row keeps the original weapon speed as `base_weapon_speed`, not the first spell's casting time.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_combat_tools.py` with `TempCampaign` (#1), stubbing `combat._spell_casting_time`. Cases: restore after the round; casting cleared; double declaration; weapon switch during the casting round; enemy casters added via `add_combatant`.

## Out of scope
- Do not change how casting time is parsed or looked up.
- Do not change the attack-disruption rule (separate issue).

## Risks and gotchas
- `next_turn` reaches the new-round block only when `idx` wraps to 0. The turn-pointer issue may change how rounds advance. Implement this against whatever "new round" hook exists after that lands, and keep the logic in a small helper `_start_new_round()` so both issues touch one place.
- `combat_session.json` round-trips these new keys. They are plain ints, so that is fine.

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: high | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
