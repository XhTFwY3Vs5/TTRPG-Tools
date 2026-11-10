## Summary
`resolve_attack` and `combat_round` advance the turn pointer from whoever is *current*, not from whoever just acted. When the declared actions don't cover every combatant in initiative order (the normal case, because the DM resolves the party's actions and then the enemies'), the pointer lands on someone who already acted, enemies get skipped, and the round counter falls behind. Bleeding, effect expiry, and 3/2 attack entitlements all key off that round counter.

## Context
The tracker keeps `_session["current_idx"]` into the initiative-sorted `combatants` list. `next_turn()` moves the pointer to the next combatant with HP > 0. When it wraps to index 0, it starts a new round: `round += 1`, resets `attacks_this_round`, applies bleeding, and decrements effects. `attack()` never checks or moves the pointer, so the DM can resolve attacks in any order. `resolve_attack` then calls `next_turn()` once the attacker's attacks are spent, and that advances the *current* combatant, who may be someone else.

Relevant code (pinned to 2965eb7):
- [`tools/combat.py#L1101-L1162`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L1101-L1162): `next_turn`.
- [`tools/combat.py#L1419-L1455`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L1419-L1455): `resolve_attack`. `if advance:` and no attacks remaining leads to `result["next"] = next_turn()`.
- [`tools/combat.py#L1457-L1543`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L1457-L1543): `combat_round` sorts actions by initiative and calls `resolve_attack` for each.
- [`tools/combat.py#L295-L313`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L295-L313): `_attacks_allowed_in_round` / `_attacks_remaining_this_round`. Entitlement depends on `round`.

## Evidence
Initiative order `['Ann', 'Gob', 'Bob']`, round 1, current Ann:
```python
T("combat_round", actions=[{"attacker": "Ann", "target": "Gob"},
                           {"attacker": "Bob", "target": "Gob"}])
# status after: round 1, current 'Bob'
```
After Ann, `next_turn` moves to Gob. After Bob, `next_turn` moves from Gob to **Bob**, who has already acted. Gob's turn was consumed without Gob acting, and the round never advanced. If the DM then resolves Gob with `resolve_attack`, the pointer moves to Ann *and* round 2 starts, so every party member's bleeding and every effect ticks at a point that depends on call order rather than on the round actually ending.

## Proposed design
Track who has acted this round, and make advancing skip them:
- New per-combatant field `acted_round: int | None`.
- `_mark_acted(c)` sets `c["acted_round"] = _session["round"]`.
- `_start_new_round()`: extract the existing `if idx == 0:` body into this helper. It increments the round, resets `attacks_this_round`, applies bleeding, and ticks effects, and returns the report list.
- `next_turn()` (the public tool): mark the current combatant as acted, then call `_advance()`.
- `_advance()`: scan forward from `current_idx + 1` through the list for the first combatant with `hp > 0` and `acted_round != round`. If none remains, call `_start_new_round()` and pick the first combatant with `hp > 0` from index 0. Return the same result shape as today (`round`, `current`, `init`, `bleeding`).
- `resolve_attack(advance=True)`: when the attacker has no attacks remaining, `_mark_acted(attacker)`. Then, **only if the current combatant has now acted** (`current.acted_round == round`), call `_advance()`. Otherwise leave the pointer where it is, because the current combatant is still owed a turn.
- `combat_round`: add `"awaiting"` to its output, listing the names with `hp > 0` and `acted_round != round`, in initiative order, so the DM sees who still has to act this round.

Alternative: force `current_idx` to the attacker before advancing. That silently drops every combatant between the pointer and the attacker, which is the same bug in a different place.

## Implementation steps
1. In `tools/combat.py`, add `_mark_acted`, `_start_new_round` and `_advance` as module-level helpers. They use the global `_session`.
2. Reimplement `next_turn` using them. Keep the docstring and return shape.
3. Update `resolve_attack` as described. Keep the `"next"` key: it holds the `_advance()` result when the pointer moved, or a string saying whose turn it still is when it did not.
4. Add `awaiting` to `combat_round`'s output.
5. In `start_combat` and `add_combatant`, initialise `"acted_round": None`.

## Acceptance criteria
- [ ] Repro above: after the PCs-only `combat_round`, `status.current == "Gob"`, `status.round == 1`, and `awaiting == ["Gob"]`.
- [ ] Then `resolve_attack("Gob", "Ann")` returns `next.round == 2`, and `current` is the first living combatant in initiative order.
- [ ] Calling `next_turn()` N times with no attacks behaves as before: it visits each living combatant once per round, in order.
- [ ] A 3/2 attacker keeps the turn after their first swing in round 2, as today (`tests/test_weapon_attacks.py` keeps passing).
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_combat_tools.py` with `TempCampaign` (#1). Set initiative deterministically by writing `init` values and sorting, or by patching `random.randint`. Cases: the repro; full-round resolution in order; out-of-order resolution; a combatant dropping mid-round; all combatants down (error unchanged); bleeding applied exactly once per round.

## Out of scope
- Do not enforce that only the current combatant may attack. The DM still resolves in any order.
- Do not change initiative rolling, the morale trigger, or damage.

## Risks and gotchas
- This rewrites the same `next_turn` new-round block that the `declare_spell` issue extends. Whichever lands second should put its logic in `_start_new_round`.
- `combat_session.json` files from before this change have no `acted_round`. Treat a missing value as `None`.
- `tools/dm_session.py` and the dashboard read `current` from the persisted state. Keep `current_idx` semantics: it still indexes `combatants`.

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: high | Confidence: verified | Effort: M | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
