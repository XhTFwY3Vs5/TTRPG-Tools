#!/usr/bin/env python3
"""Import class progression from the AD&D 2e Fandom wiki into ``global/2e.db``.

Each class page carries the three tables this needs: the level progression
(Level / XP / Hit Dice / THAC0 / Attacks, plus one column per spell level for
casters), the saving-throw table (level ranges x five save categories), and a
sidebar giving the prime requisite.

Why these choices:
  * Columns are identified by *header keyword*, not position — the wiki's
    saving-throw tables disagree on column order between pages, and the
    progression tables carry extra columns (proficiency slots, thief skills).
  * Saving throws are stored per level, expanded from the table's level ranges
    ("4-6"), because class_lookup() asks for a single level.
  * ``spell_slots`` is {"1": 2, "2": 1} — spell level -> slots, zeroes omitted.
  * Import is idempotent per class: re-running replaces that class's rows.

CAVEAT: ``tools/build_2e_db.py`` does ``DROP TABLE IF EXISTS classes`` on a full
rebuild. That builder can no longer reach its upstream JSON, but if it is ever
revived, re-run this script after it.

Usage:
    python3 tools/import_wiki_classes.py [--dry-run] [--db PATH] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://adnd2e.fandom.com/api.php"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _REPO_ROOT / "global" / "2e.db"
_USER_AGENT = "ttrpg2-class-importer/1.0 (campaign toolkit; local use)"
_SOURCE_TAG = "adnd2e.fandom.com"

# The core PHB classes. Anything absent from the wiki is skipped rather than
# guessed at. Kits and later-edition classes are deliberately out of scope.
CORE_CLASSES = (
    "Fighter", "Paladin", "Ranger", "Mage", "Illusionist",
    "Cleric", "Druid", "Thief", "Bard",
)
MAX_LEVEL = 20

# Specialist wizards share the mage's XP/HD/THAC0/saving-throw progression in
# 2e; only their spell lists differ. The wiki's Illusionist page carries no
# tables of its own, so borrow the mage's and say so in the description.
PROGRESSION_ALIASES = {"Illusionist": "Mage"}

# Same DDL as build_2e_db.build_classes.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL COLLATE NOCASE,
    description      TEXT,
    hit_die          TEXT,
    prime_requisite  TEXT,
    special_abilities TEXT
);
CREATE INDEX IF NOT EXISTS idx_classes_name ON classes(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS class_levels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id        INTEGER NOT NULL,
    level           INTEGER NOT NULL,
    xp_required     INTEGER,
    hit_dice        TEXT,
    attacks         TEXT,
    thac0           INTEGER,
    save_paralysis  INTEGER,
    save_rsw        INTEGER,
    save_petrify    INTEGER,
    save_breath     INTEGER,
    save_spell      INTEGER,
    spell_slots     TEXT,
    turn_undead     TEXT,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);
CREATE INDEX IF NOT EXISTS idx_class_levels ON class_levels(class_id, level);
"""


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


def fetch_wikitexts(titles: list[str]) -> dict[str, str]:
    """Resolve {title -> wikitext} in one batched request (these are few pages)."""
    data = _api_get({
        "action": "query", "prop": "revisions", "rvprop": "content",
        "rvslots": "main", "titles": "|".join(titles),
    })
    out: dict[str, str] = {}
    query = data.get("query", {})
    norm = {n["to"]: n["from"] for n in query.get("normalized", [])}
    for page in query.get("pages", {}).values():
        title = page.get("title", "")
        revs = page.get("revisions")
        if revs and "missing" not in page:
            out[norm.get(title, title)] = \
                revs[0].get("slots", {}).get("main", {}).get("*", "")
    time.sleep(0.2)
    return out


# --------------------------------------------------------------------------- #
# Wikitext / table parsing
# --------------------------------------------------------------------------- #
def clean_markup(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.DOTALL)
    t = re.sub(r"<ref[^>]*/>", "", t)
    t = re.sub(r"\{\{\s*br\s*\}\}", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"<br\s*/?>", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\[\[(?:[^\[\]|]*\|)?([^\[\]|]+)\]\]", r"\1", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"'''([^']+)'''", r"\1", t)
    t = re.sub(r"''([^']+)''", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _split_cells(line: str) -> list[str]:
    """Split a table row's cell text on || / !! that sit outside links/templates."""
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
    """Drop 'style="..." |' / 'colspan="3" |' prefixes from a cell.

    Only split on a pipe that is outside [[ ]] and {{ }} and that appears in an
    attribute-looking prefix, so '[[File:x.png|thumb]]' is left intact.
    """
    if "|" not in cell:
        return cell
    if not re.match(r"^\s*(?:style|colspan|rowspan|class|align|width|scope)\s*=", cell,
                    re.IGNORECASE):
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


def parse_tables(wikitext: str) -> list[list[list[str]]]:
    """Every wikitable as a list of rows, each a list of cleaned cell strings.

    A new row starts only at '|-' — consecutive cell lines ('!' or '|') belong
    to the *same* row, because the wiki authors these tables one cell per line
    as often as they use the inline '||' / '!!' form. Getting that wrong splits
    a header across rows and every column lookup misses.
    """
    tables: list[list[list[str]]] = []
    rows: list[list[str]] | None = None
    cur: list[str] = []

    def _flush_row():
        if cur:
            rows.append(list(cur))
            cur.clear()

    for raw in wikitext.splitlines():
        line = raw.strip()
        if line.startswith("{|"):
            rows, cur = [], []
        elif rows is None:
            continue
        elif line.startswith("|}"):
            _flush_row()
            tables.append(rows)
            rows, cur = None, []
        elif line.startswith("|-"):
            _flush_row()
        elif line.startswith("|+"):
            continue
        elif line.startswith("!") or line.startswith("|"):
            for cell in _split_cells(line[1:]):
                cur.append(clean_markup(_strip_attributes(cell)))
    return tables


def _find_header_row(rows: list[list[str]], *keywords: str) -> int | None:
    """Index of the first row whose cells mention every keyword group."""
    for i, row in enumerate(rows):
        low = [c.lower() for c in row]
        if all(any(k in c for c in low) for k in keywords):
            return i
    return None


def _col(row: list[str], *keywords: str) -> int | None:
    low = [c.lower() for c in row]
    for kw in keywords:
        for i, c in enumerate(low):
            if kw in c:
                return i
    return None


def _to_int(v) -> int | None:
    m = re.search(r"-?\d+", str(v or "").replace(",", ""))
    return int(m.group(0)) if m else None


def _progression_header_index(rows: list[list[str]]) -> int | None:
    """Index of the level/XP header row.

    Requires a cell that is *exactly* Level: the saving-throw table's
    "Experience Level" header also contains both 'level' and 'xp', so a plain
    substring test would happily parse the save table as a progression table.
    """
    for i, row in enumerate(rows):
        low = [c.strip().lower() for c in row]
        if not any(c in ("level", "lvl") for c in low):
            continue
        if any("paraly" in c for c in low):
            continue
        if any("xp" in c or "experience" in c for c in low):
            return i
    return None


def parse_progression(wikitext: str) -> dict:
    """Level rows from the level/XP table, keyed by level."""
    for rows in parse_tables(wikitext):
        h = _progression_header_index(rows)
        if h is None:
            continue
        header = rows[h]
        idx_level = _col(header, "level")
        idx_xp = _col(header, "xp", "experience")
        idx_hd = _col(header, "hit dice", "dice", "die")
        idx_thac0 = _col(header, "thac0")
        idx_attacks = _col(header, "attacks")
        # Spell-slot columns are headed by a bare number (1, 2, ... 7*).
        slot_cols = {int(m.group(1)): i for i, c in enumerate(header)
                     if (m := re.fullmatch(r"(\d+)[*\u2020]?", c.strip()))}

        levels: dict[int, dict] = {}
        for row in rows[h + 1:]:
            if idx_level is None or idx_level >= len(row):
                continue
            level = _to_int(row[idx_level])
            if level is None or not 1 <= level <= MAX_LEVEL:
                continue
            entry = {
                "xp_required": _to_int(row[idx_xp]) if idx_xp is not None
                               and idx_xp < len(row) else None,
                "hit_dice": row[idx_hd] if idx_hd is not None
                            and idx_hd < len(row) else None,
                "attacks": (re.sub(r"\s*rounds?$", "", row[idx_attacks], flags=re.I)
                            if idx_attacks is not None and idx_attacks < len(row)
                            else None),
                "thac0": _to_int(row[idx_thac0]) if idx_thac0 is not None
                         and idx_thac0 < len(row) else None,
            }
            slots = {}
            for spell_level, ci in slot_cols.items():
                if ci < len(row):
                    n = _to_int(row[ci]) if re.search(r"\d", row[ci]) else 0
                    if n:
                        slots[str(spell_level)] = n
            entry["spell_slots"] = json.dumps(slots) if slots else None
            levels[level] = entry
        if levels:
            return {"levels": levels,
                    "hit_die": _hit_die_from_header(header)}
    return {}


def _hit_die_from_header(header: list[str]) -> str | None:
    for c in header:
        m = re.search(r"\(?\s*d\s*(\d+)\s*\)?", c, re.IGNORECASE)
        if m and ("hit" in c.lower() or "dice" in c.lower()):
            return f"d{m.group(1)}"
    return None


def parse_saves(wikitext: str) -> dict[int, dict]:
    """{level -> five save targets}, expanded from the table's level ranges."""
    keys = {
        "paraly": "save_paralysis",
        "rod": "save_rsw",
        "petrif": "save_petrify",
        "polymorph": "save_petrify",
        "breath": "save_breath",
        "spell": "save_spell",
    }
    for rows in parse_tables(wikitext):
        h = _find_header_row(rows, "paraly")
        if h is None:
            continue
        header = rows[h]
        colmap = {}
        for i, c in enumerate(header):
            low = c.lower()
            for kw, field in keys.items():
                if kw in low and field not in colmap.values():
                    colmap[i] = field
                    break
        if len(set(colmap.values())) < 5:
            continue

        out: dict[int, dict] = {}
        ranges: list[tuple[int, int, dict]] = []
        for row in rows[h + 1:]:
            nums = re.findall(r"\d+", row[0]) if row else []
            if not nums:
                continue
            lo = int(nums[0])
            if lo < 1:                       # a stray "0" row is not a level band
                continue
            hi = int(nums[1]) if len(nums) > 1 else (MAX_LEVEL if "+" in row[0]
                                                     else lo)
            vals = {field: _to_int(row[i]) for i, field in colmap.items()
                    if i < len(row)}
            if all(v is None for v in vals.values()):
                continue
            ranges.append((lo, hi, vals))
        for lo, hi, vals in ranges:
            for lvl in range(lo, min(hi, MAX_LEVEL) + 1):
                out[lvl] = dict(vals)
        # Anything above the last band keeps the last band's numbers.
        if ranges:
            lo, hi, vals = ranges[-1]
            for lvl in range(hi + 1, MAX_LEVEL + 1):
                out[lvl] = dict(vals)
        if out:
            return out
    return {}


def parse_prime_requisite(wikitext: str) -> list[str]:
    m = re.search(r"Prime Requisites?:\s*(?:\|\||\||\n)\s*([^|\n}]+)", wikitext,
                  re.IGNORECASE)
    if not m:
        return []
    val = clean_markup(m.group(1)).strip(" |")
    if not val or val.lower() in ("none", "n/a", "\u2014", "-"):
        return []
    return [p.strip() for p in re.split(r",| and ", val) if p.strip()]


def parse_description(wikitext: str) -> str | None:
    """The first prose paragraph — the blurb before the first table."""
    body = wikitext.split("{|", 1)[0]
    body = re.sub(r"\{\{[^{}]*\}\}", "", body)
    for para in re.split(r"\n\s*\n", body):
        text = clean_markup(para)
        if len(text) > 80 and ":" not in text[:20]:
            return text[:1200]
    return None


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def ensure_schema(conn) -> bool:
    """Create the classes/class_levels tables when absent. True if created."""
    if table_exists(conn, "classes") and table_exists(conn, "class_levels"):
        return False
    conn.executescript(_SCHEMA)
    conn.commit()
    return True


def store_class(conn, name: str, description, hit_die, prime_req, progression,
                saves) -> tuple[int, int]:
    """Replace any existing rows for this class. Returns (class_id, levels)."""
    row = conn.execute("SELECT id FROM classes WHERE name = ? COLLATE NOCASE",
                       (name,)).fetchone()
    if row:
        class_id = row[0]
        conn.execute("DELETE FROM class_levels WHERE class_id = ?", (class_id,))
        conn.execute(
            "UPDATE classes SET description=?, hit_die=?, prime_requisite=?,"
            " special_abilities=? WHERE id=?",
            (description, hit_die, json.dumps(prime_req) if prime_req else None,
             json.dumps([]), class_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO classes (name, description, hit_die, prime_requisite,"
            " special_abilities) VALUES (?,?,?,?,?)",
            (name, description, hit_die,
             json.dumps(prime_req) if prime_req else None, json.dumps([])),
        )
        class_id = cur.lastrowid

    levels = progression.get("levels", {})
    for level in sorted(levels):
        entry = levels[level]
        save = saves.get(level, {})
        conn.execute(
            "INSERT INTO class_levels (class_id, level, xp_required, hit_dice,"
            " attacks, thac0, save_paralysis, save_rsw, save_petrify,"
            " save_breath, save_spell, spell_slots, turn_undead)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (class_id, level, entry.get("xp_required"), entry.get("hit_dice"),
             entry.get("attacks"), entry.get("thac0"),
             save.get("save_paralysis"), save.get("save_rsw"),
             save.get("save_petrify"), save.get("save_breath"),
             save.get("save_spell"), entry.get("spell_slots"), None),
        )
    return class_id, len(levels)


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
            print(f"Initialised classes schema in {db_path}.")
        # Some classes put the progression on a "<Class> (PHB)" sub-page; the
        # bare page is then just a sidebar and a pointer to it.
        variants = list(CORE_CLASSES) + [f"{n} (PHB)" for n in CORE_CLASSES]
        print(f"Fetching {len(variants)} class pages…")
        wikitexts = fetch_wikitexts(variants)

        stored = 0
        for name in CORE_CLASSES:
            wt = None
            for title in (name, f"{name} (PHB)"):
                candidate = wikitexts.get(title)
                if candidate and parse_progression(candidate):
                    wt = candidate
                    break
            aliased = None
            if wt is None and name in PROGRESSION_ALIASES:
                aliased = PROGRESSION_ALIASES[name]
                wt = wikitexts.get(aliased) or wikitexts.get(f"{aliased} (PHB)")
            if wt is None:
                print(f"  · {name}: no level table found — skipped")
                continue
            progression = parse_progression(wt)
            saves = parse_saves(wt)
            prime = parse_prime_requisite(wt)
            hit_die = progression.get("hit_die")
            desc = parse_description(wt)
            if aliased:
                desc = (f"{name} uses the {aliased} progression tables. "
                        f"{desc or ''}").strip()
            levels = len(progression["levels"])
            if args.dry_run:
                print(f"  + would store {name}: {levels} levels, "
                      f"{len(saves)} save rows, die {hit_die}, prime {prime}")
                stored += 1
                continue
            _, n = store_class(conn, name, desc, hit_die, prime, progression, saves)
            print(f"  + {name}: {n} levels, {len(saves)} save rows, die {hit_die}")
            stored += 1

        if not args.dry_run:
            conn.commit()
        print(f"\nDone. {'Would store' if args.dry_run else 'Stored'} {stored} "
              f"classes.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
