## Summary
Make `travel` apply the weather pace multiplier it already computes. Today a blizzard (pace ×0.0) costs a day but the party still "arrives" on schedule, because a trip given as `distance_miles` is converted to a fixed number of days up front and weather never changes it. The docstring promises "reports days extended by bad weather". Also log each leg's encounter check as an `encounter_check` event, the way `check_encounter` does, so the audit's cadence check counts travel.

## Context
`travel` is the overland composite: weather, then encounter, then rations, then calendar, then clocks, per day. Users are DMs moving the party between towns. Weather that does not slow travel removes a resource-attrition lever the manual relies on.

Relevant code (pinned to 2965eb7):
- [`tools/travel.py#L83-L183`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/travel.py#L83-L183): `travel`. L114–L116: `days` is computed once from `distance_miles`. L137: `pace_mult = _weather_pace(w_label)` is only *reported*, in `leg["pace_multiplier"]`.
- [`tools/travel.py#L14-L33`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/travel.py#L14-L33): `_WEATHER_PACE` and `_weather_pace`.
- [`tools/travel.py#L36-L49`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/travel.py#L36-L49): `_check_encounter`, which appends no event.
- [`tools/dice.py#L266-L305`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/dice.py#L264-L310): `check_encounter`, which appends `{"type": "encounter_check", ...}`.

## Evidence
Reading the code: `for leg_n in range(1, int(days) + 1)` runs a fixed count, and nothing reads `pace_mult` except the `leg` dict. The docstring step 2 says "Applies weather pace modifier; reports days extended by bad weather".

## Proposed design
- When `distance_miles > 0`, loop day by day until the distance is covered: `miles_today = base_pace_mpd * pace_mult * (1.5 if forced_march else 1)` (apply forced march exactly once; today it is applied to both `pace` and `pace_mult`). Stop once `covered >= distance_miles`. Add a safety cap of `max_days = max(3 * planned_days, planned_days + 10)`; if hit, stop and return `"stalled": true` with the remaining miles.
- When `days` is given explicitly, run exactly that many days (the DM said how long) and report `miles_covered` per leg and in total.
- Each leg gains `"miles": round(miles_today, 1)`. The result gains `"planned_days"`, `"total_days"`, `"days_lost_to_weather" = total_days - planned_days`, `"miles_covered"` and `"miles_remaining"` (only when a distance was given).
- `_check_encounter` appends `{"type": "encounter_check", "terrain": ..., "triggered": ..., "notes": "travel day N: d{die} → {roll}/{thresh}"}` with `_c.append_event`.

## Implementation steps
1. `tools/travel.py`: rewrite the loop in `travel` as described, keeping the per-day order (weather, encounter, rations, calendar, clocks) and the `_c.save_state` per leg.
2. Add the event append to `_check_encounter` (pass the leg number in).
3. Update the docstring to describe the distance and days modes and the new fields.

## Acceptance criteria
- [ ] With `_weather._weighted_pick` patched to return blizzard on day 1 and clear afterwards, `travel("X", distance_miles=24, base_pace_mpd=24)` returns `total_days == 2`, `planned_days == 1` and `days_lost_to_weather == 1`, and `current_day` advanced by 2.
- [ ] `travel("X", days=3)` always runs 3 legs and reports `miles_covered`.
- [ ] Permanent blizzard stops at the cap with `stalled: true`.
- [ ] Each leg appends one `encounter_check` event.
- [ ] `forced_march=True` multiplies pace by 1.5 exactly once.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_travel.py` with `TempCampaign` (#1). Patch `tools.travel._weather._weighted_pick` and `tools.travel.random.randint`. Cases: listed above, plus rations consumed per actual day.

## Out of scope
- Hunger HP loss. The tool keeps reporting `ration_hungry`, and the DM applies the consequence.
- Changing `_WEATHER_PACE` values or the weather tables.
- How clocks are ticked (see the clocks decision issue).

## Risks and gotchas
- If the clocks decision (#ISSUE13) chooses option A, this loop's clock tick is removed there. Keep the tick code in one clearly separated block so either change merges cleanly.

## Dependencies
Blocked by: #1

## Metadata
Type: feature | Severity: medium | Confidence: verified | Effort: M | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
