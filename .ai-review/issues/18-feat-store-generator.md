## Summary
Add a `generate_store` MCP tool that stocks a shop from the item database by category and rarity, with a price modifier. This is the first item in `TODO.md`. Today the DM invents shop inventories and prices freehand, which is exactly what the "never invent amounts" rule forbids for treasure.

## Context
`global/2e.db` `items` already holds names, types, costs and a `rarity` scale (0 = common, 10 = uncommon, 30 = rare, 50 = very rare, 65 = legendary, 100 = artifact), and `item_update` exists so the DM can set rarity "when stocking stores/treasure" (AGENTS.md). Nothing consumes it for shops.

Relevant code (pinned to 2965eb7):
- [`TODO.md`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/TODO.md): "Add store generator MCP (with price modifier & category/rarity specification - e.g. weapons, cheap, common)".
- [`tools/import_wiki_magic_items.py#L291-L314`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/import_wiki_magic_items.py#L291-L314): the `items` schema.
- [`tools/lookup.py#L438-L492`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/lookup.py#L438-L492): `item_lookup`, which shows the `item_type` values and `_find_db(_2E_DB_CANDIDATES)`.
- [`server.py#L18-L51`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/server.py#L18-L51): the module import and register list. A new module must be added to **both**.

## Evidence
`grep -rn "store\|shop" tools/*.py` finds no store or shop tool. The TODO item is open.

## Proposed design
New module `tools/stores.py`, registered in `server.py`:

```python
@mcp.tool()
def generate_store(
    category: str = "general",     # general | weapons | armor | magic | provisions | any
    max_rarity: int = 0,           # inclusive, items.rarity scale
    count: int = 12,               # distinct items to stock (1..50)
    price_modifier: float = 1.0,   # 0.8 = cheap, 1.5 = gouging; clamp 0.1..10
    store_slug: str = "",          # persist stock under state["stores"][slug]
    restock: bool = False,         # with store_slug: re-roll even if stock exists
) -> dict
```

- Category to `item_type` mapping, as a module-level dict. `weapons` gives `weapon_melee`, `weapon_ranged` and `weapon_ammo`; `armor` gives `armor`; `provisions` gives `provisions`; `general` gives `misc_equipment`, `provisions` and `clothing`; `magic` gives `item_type` values for scroll, potion, ring, rod, staff, wand, and anything starting with `magic_item_` or `misc_magic_`; `any` applies no filter. Unknown categories return an error listing the valid ones.
- Selection: `SELECT id, name, item_type, cost, weight, rarity FROM items WHERE <type filter> AND rarity <= ?`, then `random.sample(rows, min(count, len(rows)))`.
- Price: `_parse_cost("1,000 gp") -> (1000, "gp")`. It accepts `pp`, `gp`, `ep`, `sp` and `cp`, commas and decimals. Convert to cp (pp = 500, gp = 100, ep = 50, sp = 10, cp = 1), multiply by `price_modifier`, round to the nearest cp with a minimum of 1, and render in the largest whole denomination (`"12 gp 5 sp"`). Rows whose cost will not parse keep `"price": None` and `"list_price": <raw>`, and are reported in `unpriced`. Never invent a number.
- Output: `{"category", "max_rarity", "price_modifier", "items": [{"id", "name", "item_type", "list_price", "price", "price_cp", "weight", "rarity"}], "unpriced": [...], "note": "<n> candidates matched"}`.
- With `store_slug`, save `{"category", "max_rarity", "price_modifier", "items", "stocked_day"}` to `state["stores"][slug]` (via `_c.save_state`). A later call with the same slug and no `restock` returns the saved stock unchanged, so repeat visits see the same shelves. Append an event `{"type": "store_stocked", "slug": slug, "notes": "<n> items, <category>, rarity ≤ <r>"}`.
- No 2e.db: return `{"error": "2e.db not found — see README 'Reference data'"}`.

Alternatives: extending `item_lookup` would mix lookup and a stateful shop. A separate module keeps `lookup.py` read-only.

## Implementation steps
1. Create `tools/stores.py` with `_CATEGORIES`, `_parse_cost`, `_format_cp` and `register(mcp)` defining `generate_store`.
2. Add `stores` to both the import line and the `register` calls in `server.py`.
3. Add a short "Stores" subsection to AGENTS.md (under Survival Resources or Reference Lookups): call `generate_store` before describing a shop's goods, and use `store_slug` for shops the party may revisit.
4. Remove the item from `TODO.md`.

## Acceptance criteria
- [ ] `_parse_cost` handles `"15 gp"`, `"1,000 gp"`, `"5 sp"`, `"2 cp"` and `"1/2 gp"` (either parsed to 50 cp or reported as unparseable; choose one and test it), and returns `None` for `"Varies"` and `""`.
- [ ] With the fixture DB from #1 extended with about 10 items, `generate_store("weapons", count=3)` returns 3 weapon items, each priced, and `price_modifier=2.0` doubles `price_cp`.
- [ ] `max_rarity=0` never returns an item with `rarity > 0`.
- [ ] `store_slug="smithy"` called twice returns identical items. With `restock=True` it re-rolls, and `state.json` holds `stores.smithy`.
- [ ] `python3 -m unittest discover -s tests` passes, and `python3 -c "import server"` imports cleanly.

## Test plan
`tests/test_stores.py` with `TempCampaign` (#1). Seed `items` rows covering each category, a few rarities, and an unparseable cost. Patch `random.sample` for determinism. Cases: the criteria above; unknown category; count larger than candidates; clamp of `price_modifier`.

## Out of scope
- Haggling, selling to the store, or encumbrance.
- Changing item rarities or the importers.

## Risks and gotchas
- Cost strings in the wiki import may include prose (`"5 gp per day"`). Parse only a leading number and denomination, and treat anything else as unparseable.
- `item_type` values for magic items vary. Inspect `SELECT DISTINCT item_type FROM items` on a real DB and document the final mapping in the module docstring.

## Dependencies
Blocked by: #1

## Metadata
Type: feature | Severity: medium | Confidence: verified | Effort: M | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
