## Summary
World clocks only move when `tick_world` or `travel` is called. `advance_calendar`, `advance_time` rolling past midnight, and multi-day `rest` never tick them, and `rest` does not even advance the calendar. AGENTS.md tells the DM the opposite ("clocks tick in `advance_calendar`"). The result is faction clocks that drift away from the calendar, which breaks the "world has its own momentum" design the manual leans on. Fixing it needs a decision about the time model.

## Context
Faction clocks (`state.json` `faction_clocks[]`) store a mutable `days_remaining` that each ticker decrements. Several tools move the calendar, and each either ticks clocks or doesn't, independently:

| Tool | Moves `current_day` | Ticks clocks |
|---|---|---|
| `advance_calendar(days)` | yes | **no** |
| `advance_time(minutes)` past midnight | yes | **no** |
| `rest(days)` | **no** | **no** |
| `travel(...)` | yes, per leg | yes, per leg |
| `tick_world(days)` | **no** | yes |

Relevant code (pinned to 2965eb7):
- [`tools/log.py#L84-L98`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/log.py#L84-L98): `advance_calendar`. [`#L100-L167`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/log.py#L100-L167): `advance_time`.
- [`tools/factions.py#L242-L277`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/factions.py#L242-L277): `tick_world`. Its docstring says "Does NOT advance the calendar".
- [`tools/travel.py#L148-L156`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/travel.py#L148-L156): the clock tick inside `travel`.
- [`tools/party.py#L540-L689`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/party.py#L540-L689): `rest`, which never touches `current_day`.
- [`AGENTS.md#L190`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/AGENTS.md#L190): "At session start … call `tick_world(days_since_last_session)`". [`#L325`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/AGENTS.md#L325): "clocks tick in `advance_calendar`".

## Evidence
`advance_calendar(9)` leaves every `faction_clocks[].days_remaining` unchanged (the function only writes `current_day`). `rest(days=9, bed_rest=True)` heals 27 hp but leaves `current_day` and the clocks unchanged. Meanwhile AGENTS.md L325 tells the DM that clocks advance there.

## Options
**A. Derive clocks from the calendar (recommended).** Store `due_day = started_day + days` on each clock, and compute `days_remaining = due_day - current_day` wherever it is read (`list_clocks`, `tick_world`, `audit`, `session_primer`, the dashboard). Calendar movers need no clock code at all. `tick_world()` becomes a report of what is complete or urgent since a stored `last_reported_day`, and its `days` argument is deprecated and ignored with a warning. `rest(days)` advances `current_day` by `days`.
- Pros: clocks can never drift or double-count; one source of truth; simplest mental model for the DM.
- Cons: touches about 6 readers; needs a one-time migration (`due_day = current_day + days_remaining` for existing clocks); changes `tick_world`'s contract, so AGENTS.md L190 must be rewritten.

**B. Make every calendar mover tick.** Factor out `factions._tick(state, days) -> (completed, urgent)`, and call it from `advance_calendar`, the `advance_time` rollover, and (if it is made to advance the calendar) `rest`. Keep `tick_world` for manual use.
- Pros: smaller change; keeps the current data shape.
- Cons: the session-start `tick_world(days_since_last_session)` instruction then double-counts any days the DM already advanced; ordering bugs remain possible whenever a new time-moving tool is added.

**C. Change only the documentation.** Fix AGENTS.md to say clocks move only via `tick_world` and `travel`, and that `rest` and `advance_calendar` must be followed by `tick_world(days)`.
- Pros: no code risk.
- Cons: keeps the footgun; relies on the DM remembering two calls every time; the audit's "ignored urgent clock" check keeps reading stale numbers.

Recommendation: **A**. The repo's own stated design is that "if 30 days passed in-game, 30 days passed for the cult too", and a derived value enforces that by construction. After a decision, split implementation into (1) the data model, migration and readers, and (2) `rest` advancing the calendar plus the AGENTS.md updates.

## Acceptance criteria
- [ ] A human picks an option, and comments it on this issue.
- [ ] Follow-up implementation issues are filed with the `agent-ready` label.

## Test plan
Decided per option. For A: `advance_calendar(5)` reduces every clock's reported `days_remaining` by 5; `tick_world()` reports completion exactly once; migrating an existing `state.json` keeps the remaining days.

## Out of scope
- Changing what clocks *do* on completion (they remain text-only).

## Risks and gotchas
- `travel` currently both advances the calendar and ticks. Under A, its tick code must be removed, or clocks will double-count.
- The dashboard reads `faction_clocks` directly (grep `faction_clocks` in `dashboard.py`) and must use the derived value.

## Dependencies
Blocked by: none

## Metadata
Type: bug | Severity: medium | Confidence: verified | Effort: M | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
