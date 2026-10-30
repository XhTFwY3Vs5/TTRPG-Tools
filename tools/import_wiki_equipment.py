#!/usr/bin/env python3
"""Import mundane weapons and armour from the AD&D 2e Fandom wiki into 2e.db.

Sources (both plain wikitables, one table each):
  * ``Weapon List (PHB)`` — Item / Cost / Weight / Size / Type / Speed Factor /
    Damage S-M / Damage L.
  * ``Armor List (PHB)``  — Item / Cost / Weight.

Why these choices:
  * The lists' own Type column is the 2e damage type (P/S/B), not melee vs
    ranged, so item_type is derived from the item name against the slugs the
    dashboard and item_lookup already understand (weapon_melee, weapon_ranged,
    weapon_ammo, armor).
  * The tables carry no ROF or range columns, so those stay NULL; likewise the
    wiki's armour page gives AC only for *combinations* of armour and shield,
    not per item, so armour AC is left NULL rather than guessed.
  * The DB (and the ``items`` table) is created when absent, so a fresh
    checkout has somewhere to import into.
  * Dedup is by name; re-runs are idempotent.

CAVEAT: ``tools/build_2e_db.py`` does ``DROP TABLE IF EXISTS items`` on a full
rebuild. That builder can no longer reach its upstream JSON, but if it is ever
revived, re-run this script (and import_wiki_magic_items.py) after it.

Usage:
    python3 tools/import_wiki_equipment.py [--dry-run] [--db PATH] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://adnd2e.fandom.com/api.php"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _REPO_ROOT / "global" / "2e.db"
_USER_AGENT = "ttrpg2-equipment-importer/1.0 (campaign toolkit; local use)"
_SOURCE_TAG = "adnd2e.fandom.com"

_ID_BASE = 900_000

WEAPON_PAGE = "Weapon List (PHB)"
ARMOR_PAGE = "Armor List (PHB)"

# Same DDL as build_2e_db.build_items.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL COLLATE NOCASE,
    item_type   TEXT,
    cost        TEXT,
    weight      TEXT,
    description TEXT,
    source      TEXT,
    rarity      INTEGER DEFAULT 0,
    ac          INTEGER,
    size        TEXT,
    weapon_type TEXT,
    speed       INTEGER,
    damage_sm   TEXT,
    damage_l    TEXT,
    rof         TEXT,
    range_s     TEXT,
    range_m     TEXT,
    range_l     TEXT,
    xp_value    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_items_name ON items(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_items_type ON items(item_type);
"""

_COLUMNS = (
    "id", "name", "item_type", "cost", "weight", "description", "source",
    "rarity", "ac", "size", "weapon_type", "speed", "damage_sm", "damage_l",
    "rof", "range_s", "range_m", "range_l", "xp_value",
)

# Name keywords that decide melee vs ranged vs ammunition.
_AMMO_KEYWORDS = ("arrow", "bolt", "quarrel", "bullet", "stone", "needle")
_RANGED_KEYWORDS = ("bow", "crossbow", "sling", "dart", "javelin", "arquebus",
                    "blowgun", "harpoon", "net", "atlatl")


# --------------------------------------------------------------------------- #
# MediaWiki API
# --------------------------------------------------------------------------- #
def _api_get(params: dict, retries: int = 4) -> dict:
    params = {**params, "format": "json", "maxlag": "5"}
    url = API + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"API failed after {retries} tries: {last}")


def fetch_wikitext(page: str) -> str:
    data = _api_get({"action": "parse", "page": page, "prop": "wikitext"})
    return data.get("parse", {}).get("wikitext", {}).get("*", "")


# --------------------------------------------------------------------------- #
# Wikitext / table parsing
# --------------------------------------------------------------------------- #
def clean_cell(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    t = re.sub(r"<sup>.*?</sup>", "", t, flags=re.DOTALL)   # footnote markers
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.DOTALL)
    t = re.sub(r"<ref[^>]*/>", "", t)
    t = re.sub(r"\{\{\s*br\s*\}\}", " ", t, flags=re.IGNORECASE)
    t = t.replace("&nbsp;", " ")
    t = re.sub(r"\[\[(?:[^\[\]|]*\|)?([^\[\]|]+)\]\]", r"\1", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"'''([^']+)'''", r"\1", t)
    t = re.sub(r"''([^']+)''", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _split_cells(line: str) -> list[str]:
    parts, links, braces, buf, i = [], 0, 0, [], 0
    while i < len(line):
        two = line[i:i + 2]
        if two == "[[":
            links += 1; buf.append(two); i += 2; continue
        if two == "]]":
            links = max(0, links - 1); buf.append(two); i += 2; continue
        if two == "{{":
            braces += 1; buf.append(two); i += 2; continue
        if two == "}}":
            braces = max(0, braces - 1); buf.append(two); i += 2; continue
        if two in ("||", "!!") and links == 0 and braces == 0:
            parts.append("".join(buf)); buf = []; i += 2; continue
        buf.append(line[i]); i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def _strip_attributes(cell: str) -> str:
    if "|" not in cell:
        return cell
    if not re.match(r"^\s*(?:style|colspan|rowspan|class|align|width|scope)\s*=",
                    cell, re.IGNORECASE):
        return cell
    links = braces = 0
    for i, ch in enumerate(cell):
        nxt = cell[i:i + 2]
        if nxt == "[[":
            links += 1
        elif nxt == "]]":
            links = max(0, links - 1)
        elif nxt == "{{":
            braces += 1
        elif nxt == "}}":
            braces = max(0, braces - 1)
        elif ch == "|" and links == 0 and braces == 0:
            return cell[i + 1:].strip()
    return cell


def parse_table(wikitext: str) -> list[list[str]]:
    """The page's first wikitable as rows of cleaned cells.

    Consecutive cell lines belong to one row; a new row starts only at '|-' —
    the wiki authors these tables one cell per line.
    """
    rows: list[list[str]] = []
    cur: list[str] | None = None
    in_table = False
    for raw in wikitext.splitlines():
        line = raw.strip()
        if line.startswith("{|"):
            in_table, cur = True, []
        elif not in_table:
            continue
        elif line.startswith("|}"):
            if cur:
                rows.append(cur)
            break
        elif line.startswith("|-"):
            if cur:
                rows.append(cur)
            cur = []
        elif line.startswith("|+"):
            continue
        elif line.startswith("!") or line.startswith("|"):
            for cell in _split_cells(line[1:]):
                cur.append(clean_cell(_strip_attributes(cell)))
    return [r for r in rows if any(c for c in r)]


def _col(header: list[str], *keywords: str) -> int | None:
    low = [c.lower() for c in header]
    for kw in keywords:
        for i, c in enumerate(low):
            if kw in c:
                return i
    return None


def _int_or_none(v) -> int | None:
    m = re.search(r"\d+", str(v or "").replace(",", ""))
    return int(m.group(0)) if m else None


def classify(item_type: str, name: str) -> str:
    """Melee vs ranged vs ammunition vs armour, from the name."""
    if item_type == "armor":
        return "armor"
    low = name.lower()
    if any(k in low for k in _AMMO_KEYWORDS):
        return "weapon_ammo"
    if any(k in low for k in _RANGED_KEYWORDS):
        return "weapon_ranged"
    return "weapon_melee"


def weapon_rows(wikitext: str) -> list[dict]:
    rows = parse_table(wikitext)
    if not rows:
        return []
    header = rows[0]
    i_name = _col(header, "item", "name")
    i_cost = _col(header, "cost")
    i_weight = _col(header, "weight")
    i_size = _col(header, "size")
    i_type = _col(header, "type")
    i_speed = _col(header, "speed")
    i_sm = _col(header, "damage s-m", "damage s")
    i_l = _col(header, "damage l")
    if i_name is None:
        return []

    out = []
    for row in rows[1:]:
        def cell(i):
            return row[i] if i is not None and i < len(row) else None

        name = cell(i_name)
        if not name or len(name) < 2:
            continue
        out.append({
            "name": name,
            "item_type": classify("weapon", name),
            "cost": cell(i_cost),
            "weight": cell(i_weight),
            "size": cell(i_size),
            "weapon_type": cell(i_type),
            "speed": _int_or_none(cell(i_speed)),
            "damage_sm": cell(i_sm),
            "damage_l": cell(i_l),
            "rarity": 0,
            "source": f"{WEAPON_PAGE} ({_SOURCE_TAG})",
        })
    return out


def armor_rows(wikitext: str) -> list[dict]:
    rows = parse_table(wikitext)
    if not rows:
        return []
    header = rows[0]
    i_name = _col(header, "item", "name")
    i_cost = _col(header, "cost")
    i_weight = _col(header, "weight")
    if i_name is None:
        return []

    out = []
    for row in rows[1:]:
        name = row[i_name] if i_name < len(row) else None
        if not name or len(name) < 2:
            continue
        out.append({
            "name": name,
            "item_type": "armor",
            "cost": row[i_cost] if i_cost is not None and i_cost < len(row) else None,
            "weight": row[i_weight] if i_weight is not None
                      and i_weight < len(row) else None,
            "rarity": 0,
            "source": f"{ARMOR_PAGE} ({_SOURCE_TAG})",
        })
    return out


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def ensure_schema(conn) -> bool:
    """Create the items table when absent. True when it was created."""
    if table_exists(conn, "items"):
        return False
    conn.executescript(_SCHEMA)
    conn.commit()
    return True


def existing_names(conn) -> set[str]:
    return {r[0].strip().lower()
            for r in conn.execute("SELECT name FROM items WHERE name IS NOT NULL")}


def next_id(conn) -> int:
    cur = conn.execute("SELECT MAX(id) FROM items").fetchone()[0]
    return max((cur or 0) + 1, _ID_BASE)


def insert_row(conn, item_id: int, row: dict) -> None:
    cols = ", ".join(_COLUMNS)
    qs = ", ".join("?" for _ in _COLUMNS)
    vals = [item_id if c == "id" else row.get(c) for c in _COLUMNS]
    conn.execute(f"INSERT INTO items ({cols}) VALUES ({qs})", vals)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(_DEFAULT_DB))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not args.dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.dry_run and not db_path.exists():
        conn = sqlite3.connect(":memory:")
    else:
        conn = sqlite3.connect(str(db_path))

    try:
        if ensure_schema(conn) and not args.dry_run:
            print(f"Initialised items schema in {db_path}.")
        have = existing_names(conn)
        nid = next_id(conn)
        print(f"DB: {db_path} — {len(have)} items recorded; new ids from {nid}.")

        print(f"Fetching {WEAPON_PAGE}…")
        weapons = weapon_rows(fetch_wikitext(WEAPON_PAGE))
        print(f"  {len(weapons)} weapon rows.")
        print(f"Fetching {ARMOR_PAGE}…")
        armor = armor_rows(fetch_wikitext(ARMOR_PAGE))
        print(f"  {len(armor)} armour rows.")

        inserted = skipped = 0
        seen: set[str] = set()
        for row in weapons + armor:
            key = row["name"].strip().lower()
            if key in have or key in seen:
                skipped += 1
                continue
            seen.add(key)
            if args.dry_run:
                inserted += 1
                if args.verbose:
                    print(f"  + would add [{row['item_type']}] {row['name']}")
                continue
            insert_row(conn, nid, row)
            nid += 1
            inserted += 1
            if args.verbose:
                print(f"  + added [{row['item_type']}] {row['name']}")

        if not args.dry_run:
            conn.commit()
        print(f"\nDone. {'Would insert' if args.dry_run else 'Inserted'} "
              f"{inserted} items; skipped {skipped} already-recorded.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
