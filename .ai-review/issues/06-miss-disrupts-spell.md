## Summary
`attack()` disrupts the target's spell even when the attack **misses**. In AD&D 2e a caster loses the spell only when they are hit or damaged before it goes off (PHB Ch. 9, "Spellcasting"), so casters currently lose spells to every swing aimed at them.

## Context
`declare_spell` adds a `"casting"` condition to the caster. `attack()` and `apply_combat_damage()` are supposed to strip it and report `spell_disrupted: true` when the caster takes damage before their segment.

Relevant code (pinned to 2965eb7):
- [`tools/combat.py#L866-L871`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L866-L871): in `attack()`, the disruption block runs **before** and independent of `if hit:`:
  ```python
  # Spell disruption on target
  spell_disrupted = False
  if "casting" in tgt["conditions"]:
      tgt["conditions"].remove("casting")
      spell_disrupted = True
  ```
- [`tools/combat.py#L879-L902`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L879-L902): the `if hit:` damage block.
- [`tools/combat.py#L934-L968`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L934-L968): `apply_combat_damage` disrupts unconditionally. That is fine, because it is only called when damage lands.

## Evidence
```python
T("start_combat")
T("add_combatant", name="Gob", hp=5, ac=-10, thac0=20, dmg="1d6")
T("declare_spell", character="Bob", spell_name="sleep")
T("roll_initiative")
r = T("attack", attacker="Gob", target="Bob")
# -> hit: False  spell_disrupted: True
```

## Proposed design
Move the disruption block inside the `if hit:` branch, after damage is computed, and disrupt only when `damage > 0`. A hit for 0 damage (only possible with negative modifiers) does not disrupt; a hit that deals damage does. Keep the result key `spell_disrupted` and the note text unchanged.

## Implementation steps
1. In `tools/combat.py` `attack()`, delete the block at L866–L871.
2. Directly after `tgt["hp"] = max(-10, tgt["hp"] - damage)` inside `if hit:`, add:
   ```python
   if damage > 0 and "casting" in tgt["conditions"]:
       tgt["conditions"].remove("casting")
       spell_disrupted = True
       notes.append(f"{tgt['name']} was casting — spell disrupted!")
   ```
   and initialise `spell_disrupted = False` before the `if hit:`.
3. Update the `attack` docstring with one line: "A caster's spell is disrupted only by a hit that deals damage."

## Acceptance criteria
- [ ] With `random` patched so the roll misses, `attack` on a `casting` target returns `spell_disrupted: False`, and the target still has `"casting"` in `conditions`.
- [ ] With a forced hit for ≥1 damage, the result is `spell_disrupted: True` and `"casting"` is removed.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
In `tests/test_combat_tools.py` (with `TempCampaign` from #1), patch `tools.combat.random.randint`. Cases: miss leaves casting; natural 1 leaves casting; hit with damage disrupts; `fleeing` auto-hit with damage disrupts.

## Out of scope
- Do not change `apply_combat_damage`.
- Do not change when `"casting"` is *cleared* at the end of the round. That is covered by the separate `declare_spell` issue.

## Risks and gotchas
- `resolve_attack` and `combat_round` pass the `spell_disrupted` key through. Do not rename it.

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: high | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
