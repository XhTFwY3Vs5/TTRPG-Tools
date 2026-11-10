## Summary
The combat tracker keeps its session in a module-level global that is never tied to a campaign. After `switch_campaign`, the old campaign's fight is still live. `end_combat` then discards that party's HP, because their keys don't exist in the new campaign, writes a `combat` event into the wrong campaign's `events.jsonl`, and leaves the old campaign's `combat_session.json` on disk to resurrect later.

## Context
`tools/combat.py` keeps `_session` in memory and mirrors it to `campaigns/<active>/combat_session.json` after every mutation, so a fight survives MCP-server restarts. `_session_file()` resolves the *currently active* campaign at call time. `_ensure_session()` returns the in-memory `_session` whenever it is non-`None`, without checking which campaign it belongs to.

Relevant code (pinned to 2965eb7):
- [`tools/combat.py#L10`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L10): `_session: dict | None = None`.
- [`tools/combat.py#L28-L50`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L28-L50): `_session_file` and `_ensure_session`.
- [`tools/combat.py#L1207-L1252`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/combat.py#L1207-L1252): `end_combat`. Saves HP only for `_key`s present in the *active* campaign's state, appends the event to the active campaign, and deletes the active campaign's session files.
- [`tools/campaign_mgmt.py#L215-L245`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/campaign_mgmt.py#L215-L245): `switch_campaign` calls `_c.set_active` and does not touch combat.
- [`_campaign.py#L117-L119`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/_campaign.py#L117-L119): `set_active`.
- The dashboard can also switch campaigns (`POST /campaigns/<slug>/switch`) from a different process, so the MCP server cannot rely on being told about a switch.

## Evidence
Repro against a scratch copy, with campaign `zz` (PCs Ann and Bob) and campaign `greyhawk`:
```python
_c.set_active("zz")
T("start_combat"); T("add_combatant", name="Gob", hp=5, ac=10, thac0=20, dmg="1d6")
T("switch_campaign", name="greyhawk")   # {'active': 'greyhawk', ...}
T("combat_status")   # combatants: ['Ann', 'Bob', 'Gob']  <- zz's fight, under greyhawk
T("end_combat")      # {'saved': {}, 'rounds': 1}          <- zz HP never saved
# greyhawk/events.jsonl gains: {"type": "combat", "outcome": "ended", "party_hp": {}, ...}
# campaigns/zz/combat_session.json is still on disk
```

## Proposed design
Stamp the session with its owning campaign, and treat a mismatch as "no session in memory":
- `start_combat` sets `_session["campaign"] = cfg["_name"]`.
- `_ensure_session()` reads the active name first (`_c.get_active_name()`, in a try/except that returns `None` on failure). If `_session` is in memory and `_session.get("campaign")` differs from the active name, set `_session = None` and fall through to loading the active campaign's file. A loaded file whose `campaign` field is present and different is ignored. A missing field means a legacy file; accept it.
- `_persist_session()` writes to `campaigns/<_session["campaign"]>/combat_session.json` when the field is set, not to the active campaign.
- `end_combat()` loads the session's own campaign (`_c.load_campaign(_session["campaign"])`) for the state write, the event, and the file cleanup.

Alternative considered: clearing `combat._session` inside `switch_campaign`. That misses switches made by the dashboard process, and a later `end_combat` would still target the wrong campaign.

## Implementation steps
1. `tools/combat.py` `start_combat` (L555): add `"campaign": cfg["_name"]` to the new `_session` dict.
2. Change `_session_file()` to `_session_file(name: str | None = None)` and use `_c.campaign_dir(name)` when `name` is given.
3. Rewrite `_ensure_session()` as described in the design.
4. `_persist_session()`: pass `_session.get("campaign")` to `_session_file`. Call `save_combat_state` with `_c.load_campaign(_session.get("campaign"))` when the field is set.
5. `end_combat()`: use `cfg = _c.load_campaign(_session.get("campaign")) if _session.get("campaign") else _c.load_campaign()`, and clear files via `_session_file(that name)`.

## Acceptance criteria
- [ ] Repro above: after `switch_campaign("greyhawk")`, `combat_status()` returns `{"error": "No active combat session..."}`.
- [ ] Switching back to `zz` restores the fight from `campaigns/zz/combat_session.json`, and `end_combat()` then writes Ann's and Bob's HP into `campaigns/zz/state.json` and the event into `campaigns/zz/events.jsonl`.
- [ ] A legacy `combat_session.json` with no `campaign` field still loads.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
Add `tests/test_combat_tools.py` (shared with the other combat issues) using `TempCampaign` from #1, extended to hold two campaign dirs, or with a second `campaign.json` written by hand. Cases: the repro; switching back resumes; `end_combat` writes to the owning campaign; legacy-file compatibility.

## Out of scope
- Do not change `switch_campaign`, the dashboard, or the `combat_state.json` dashboard format beyond writing it to the owning campaign.
- Do not change attack or initiative logic.

## Risks and gotchas
- `tools.party.apply_heal` calls `combat.get_session()` to sync HP. After this change it correctly gets `None` for another campaign's fight.
- `_c.load_campaign(name)` with an explicit name bypasses the cache (by design), which is fine here.
- The other combat issues (spell disruption, declare_spell, turn pointer) edit the same file. Rebase carefully.

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: high | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
