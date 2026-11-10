## Summary
Record every `saving_throw` and `skill_check` in the campaign event log, so the audit and `pre_turn_check` can tell a resolved save or check from a narrated one. Today these two tools, the ones Hard Constraints #1 and #3 lean on most, leave no trace.

## Context
The audit (`tools/audit.py`) verifies the procedure from `events.jsonl`: reactions, surprise, morale and encounter checks all log events (`_c.append_event`). `saving_throw` and `skill_check` do not, so the audit cannot check die-first resolution for the most common non-combat rolls, and the `/log` chronicle never shows them.

Relevant code (pinned to 2965eb7):
- [`tools/dice.py#L459-L496`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L459-L496): `saving_throw`, no `append_event`.
- [`tools/dice.py#L578-L611`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L578-L611): `skill_check`, no `append_event`.
- [`tools/dice.py#L236-L262`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L236-L262): `morale_check` as the pattern to copy (`type`, a few fields, `notes`).
- [`_campaign.py#L308-L319`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/_campaign.py#L308-L319): `append_event`.

## Evidence
`grep -n append_event tools/dice.py` shows calls in `reaction`, `morale_check`, `check_encounter`, `determine_encounter` and `surprise_check`, but none in `saving_throw` or `skill_check`.

## Proposed design
At the end of each tool, after rolling and before returning, append:
- `{"type": "saving_throw", "slug": <char key>, "name": <label>, "save": <save name>, "roll": .., "target": .., "success": .., "notes": "<Label> save vs <save>: <roll> vs <target> — success|failure"}`
- `{"type": "skill_check", "slug": <char key>, "name": <label>, "skill": <matched key>, "roll": .., "target": .., "success": .., "notes": ...}`

Only successful rolls are logged; error returns (unknown character or skill) are not. Include `modifier` and `total` fields if the saving-throw issue (#ISSUE10) has landed.

This issue only produces the events. New audit rules that consume them are out of scope.

Also make sure the chronicle (`/log` in the dashboard) does not render these events as noise. Check `dashboard.py`'s event-type rendering and add both types to whatever list collapses mechanical events, if one exists.

## Implementation steps
1. `tools/dice.py` `saving_throw`: add the `_c.append_event(cfg, {...})` call.
2. `tools/dice.py` `skill_check`: same.
3. Grep `dashboard.py` and `tools/lore.py` for how event `type`s are filtered (for example `morale_check`), and add the two new types in the same places.

## Acceptance criteria
- [ ] After `saving_throw("Ann", "spell")`, the last line of `events.jsonl` has `type == "saving_throw"`, `slug == "ann"`, and the same `roll` as the tool's return value.
- [ ] Same for `skill_check`.
- [ ] Error paths append nothing.
- [ ] `scout()` produces one `skill_check` event per rolled check.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_dice_tools.py` with `TempCampaign` (#1). Read `events.jsonl` after each call. Cases: success, failure, error (no event), `scout` multi-check.

## Out of scope
- New audit rules. File them separately once these events exist.
- Logging `roll()` (ad-hoc dice).

## Risks and gotchas
- `scout` calls `skill_check` through `_call_tool`, so it inherits the logging. Do not log twice.
- Edits the same functions as #ISSUE10. Land that first.

## Dependencies
Blocked by: #1, #ISSUE10

## Metadata
Type: feature | Severity: medium | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
