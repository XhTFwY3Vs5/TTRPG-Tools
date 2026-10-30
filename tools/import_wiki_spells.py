#!/usr/bin/env python3
"""Import spells from the AD&D 2e Fandom wiki into ``global/2e.db``.

Walks the spell categories via the MediaWiki API, parses each page's
``{{Infobox Spells}}`` block plus its description prose, and inserts any spell
NOT already recorded in the ``spells`` table (dedup is by name + caster, so the
priest and wizard versions of "Hold Person" coexist).

Why these choices:
  * MediaWiki API (not HTML scraping) — stable, paginated, batchable.
  * ``caster`` is normalised to 'priest' / 'wizard' because party.py matches it
    with a plain ``=`` against the caster kind it derives from the class name;
    'Priest' or 'Cleric' would silently fail to resolve spell levels.
  * ``school`` carries the sphere for priest spells (Healing, Charm, ...) and
    the school of magic for wizard spells — the mechanically relevant category
    on each side, since the table has only one column for it.
  * Scraped rows get IDs from a dedicated high range (>= _ID_BASE) and a
    distinctive ``source`` so they're easy to spot, delete, or re-import.
  * The DB (and the ``spells`` table) is created when absent, so a fresh
    checkout has somewhere to import into.

CAVEAT: ``tools/build_2e_db.py`` does ``DROP TABLE IF EXISTS spells`` on a full
rebuild, which would remove these scraped rows. That builder can no longer
reach its upstream JSON, but if it is ever revived, re-run this script after it.

Usage:
    python3 tools/import_wiki_spells.py [--dry-run] [--limit N]
                                        [--db PATH] [--verbose]

    --dry-run   parse and report, write nothing
    --limit N   only process the first N members per category (smoke test)
    --verbose   print every spell as it's inserted/skipped
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
_USER_AGENT = "ttrpg2-spell-importer/1.0 (campaign toolkit; local use)"
_SOURCE_TAG = "adnd2e.fandom.com"

# Scraped rows sit well clear of the 2ERPGDB source ids (~1000-2200).
_ID_BASE = 900_000

# Category:Spells is the superset the wiki files spells under; the two typed
# categories catch anything only listed there. Titles are deduped across all of
# them, and the infobox ``class`` field decides priest vs wizard.
CATEGORIES = ("Spells", "Priest Spells", "Wizard Spells")

# Same DDL as build_2e_db.build_spells — kept in sync by hand because that
# builder's upstream is gone and this importer now owns the table in practice.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS spells (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL COLLATE NOCASE,
    level            INTEGER,
    school           TEXT,
    caster           TEXT,
    verbal           INTEGER,
    somatic          INTEGER,
    material         INTEGER,
    materials        TEXT,
    range            TEXT,
    aoe              TEXT,
    casting_time     TEXT,
    casting_time_init INTEGER,
    duration         TEXT,
    save             TEXT,
    damage           TEXT,
    description      TEXT,
    source           TEXT,
    page             INTEGER,
    reversible       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_spells_name ON spells(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_spells_caster_level ON spells(caster, level);
"""

# Infobox field (lowercased) -> spells column.
_FIELD_MAP = {
    "level": "level",
    "range": "range",
    "aoe": "aoe",
    "castingtime": "casting_time",
    "duration": "duration",
    "save": "save",
    "materials": "materials",
    "damage": "damage",
}


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


def iter_category_members(category: str, sleep: float = 0.2) -> list[str]:
    titles: list[str] = []
    cont = None
    while True:
        params = {
            "action": "query", "list": "categorymembers",
            "cmtitle": f"Category:{category}", "cmlimit": "500", "cmtype": "page",
        }
        if cont:
            params["cmcontinue"] = cont
        data = _api_get(params)
        for m in data.get("query", {}).get("categorymembers", []):
            titles.append(m["title"])
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            return titles
        time.sleep(sleep)


def fetch_wikitext_batch(titles: list[str], sleep: float = 0.2) -> dict[str, str]:
    """Resolve {page title -> wikitext} for up to 40 titles at a time."""
    out: dict[str, str] = {}
    for i in range(0, len(titles), 40):
        data = _api_get({
            "action": "query", "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "titles": "|".join(titles[i:i + 40]),
        })
        query = data.get("query", {})
        norm = {n["to"]: n["from"] for n in query.get("normalized", [])}
        for page in query.get("pages", {}).values():
            title = page.get("title", "")
            revs = page.get("revisions")
            if revs:
                out[norm.get(title, title)] = \
                    revs[0].get("slots", {}).get("main", {}).get("*", "")
        time.sleep(sleep)
    return out


# --------------------------------------------------------------------------- #
# Wikitext parsing (same style as import_wiki_monsters / import_wiki_magic_items)
# --------------------------------------------------------------------------- #
def _split_top_level_pipes(s: str) -> list[str]:
    parts, links, braces, buf, i = [], 0, 0, [], 0
    while i < len(s):
        two = s[i:i + 2]
        if two == "[[":
            links += 1; buf.append(two); i += 2; continue
        if two == "]]":
            links = max(0, links - 1); buf.append(two); i += 2; continue
        if two == "{{":
            braces += 1; buf.append(two); i += 2; continue
        if two == "}}":
            braces = max(0, braces - 1); buf.append(two); i += 2; continue
        c = s[i]
        if c == "|" and links == 0 and braces == 0:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def _find_template(wikitext: str, names: tuple[str, ...]) -> tuple[str | None, int]:
    """Return (inner text, end offset) of the first matching template."""
    for name in names:
        m = re.search(r"\{\{\s*" + name + r"\b", wikitext, re.IGNORECASE)
        if not m:
            continue
        start, depth, i = m.start(), 0, m.start()
        while i < len(wikitext):
            two = wikitext[i:i + 2]
            if two == "{{":
                depth += 1; i += 2
            elif two == "}}":
                depth -= 1; i += 2
                if depth == 0:
                    return wikitext[start + 2:i - 2], i
            else:
                i += 1
    return None, 0


def parse_infobox(inner: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for seg in _split_top_level_pipes(inner)[1:]:
        if "=" in seg:
            k, _, v = seg.partition("=")
            fields[k.strip().lower()] = v.strip()
    return fields


def clean_markup(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.DOTALL)
    t = re.sub(r"<ref[^>]*/>", "", t)
    t = re.sub(r"\{\{\s*br\s*\}\}|\{\{\s*Br\s*\}\}", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\[\[(?:[^\[\]|]*\|)?([^\[\]|]+)\]\]", r"\1", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"'''([^']+)'''", r"\1", t)
    t = re.sub(r"''([^']+)''", r"\1", t)
    t = re.sub(r"^=+\s*(.*?)\s*=+\s*$", r"\1", t, flags=re.MULTILINE)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def strip_footer(text: str) -> str:
    """Drop the trailing Category:/Source: metadata block the wiki appends."""
    lines = text.split("\n")
    while lines:
        s = lines[-1].strip()
        if not s or s.startswith("Category:") or s.startswith("Source:"):
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()


def clean_field(text: str) -> str:
    """Infobox values are single-line: a {{br}} there separates list members
    ("Alteration{{br}}Alchemy"), not paragraphs, so it becomes a comma."""
    t = re.sub(r"\{\{\s*br\s*\}\}", ", ", text or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", clean_markup(t)).strip(" ,")


def _int_or_none(v) -> int | None:
    m = re.search(r"\d+", str(v or ""))
    return int(m.group(0)) if m else None


def _flag(v) -> int:
    """Infobox 1/0/'yes' booleans. Anything falsy-looking is 0."""
    s = str(v or "").strip().lower()
    return 0 if s in ("", "0", "no", "false", "none", "nil") else 1


def parse_casting_time_init(ct) -> int:
    """Casting time -> initiative segment (1-9), 10 for a full round or longer.

    Same convention as build_2e_db.py: a spell with no parseable segment value
    costs the whole round.
    """
    if not ct:
        return 10
    s = str(ct).strip().lower()
    try:
        return min(int(float(s)), 10)
    except ValueError:
        pass
    if any(x in s for x in ["rd", "turn", "hour", "hr", "min", "segment"]):
        return 10
    m = re.match(r"^(\d+)", s)
    return min(int(m.group(1)), 10) if m else 10


def caster_kind(infobox_class: str, title: str, category_hint: str) -> str:
    """Normalise to 'priest'/'wizard' — the two values party.py matches on."""
    low = f"{infobox_class} {title} {category_hint}".lower()
    if "wizard" in low or "mage" in low or "illusionist" in low:
        return "wizard"
    if "priest" in low or "cleric" in low or "druid" in low:
        return "priest"
    return ""


def build_row(title: str, wikitext: str, category_hint: str) -> dict | None:
    """Map a wiki page to a spells-row dict, or None to skip it."""
    if re.match(r"\s*#redirect", wikitext, re.IGNORECASE):
        return None
    inner, end = _find_template(wikitext, ("Infobox Spells", "Infobox Spell"))
    if inner is None:
        return None
    fields = parse_infobox(inner)

    level = _int_or_none(fields.get("level"))
    if level is None:
        return None                      # not a spell entry (index/list page)

    caster = caster_kind(fields.get("class", ""), title, category_hint)
    if not caster:
        return None

    name = clean_field(fields.get("name", "")) or title
    name = re.sub(r"\s*\(reversible\)\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\((?:priest|wizard) spell\)\s*$", "", name,
                  flags=re.IGNORECASE)
    name = name.strip(" ,")
    if not name:
        return None

    row = {c: None for c in (
        "name", "level", "school", "caster", "verbal", "somatic", "material",
        "materials", "range", "aoe", "casting_time", "casting_time_init",
        "duration", "save", "damage", "description", "source", "page", "reversible",
    )}
    for key, col in _FIELD_MAP.items():
        if fields.get(key):
            val = clean_field(fields[key])
            row[col] = _int_or_none(val) if col == "level" else (val or None)

    # The sphere is the operative category for priest spells; wizards get their
    # school of magic. Either may be absent.
    sphere = clean_field(fields.get("sphere", ""))
    school = clean_field(fields.get("school", ""))
    row["school"] = (sphere or school) if caster == "priest" else (school or sphere)
    row["school"] = row["school"] or None

    row["caster"] = caster
    row["verbal"] = _flag(fields.get("verbal"))
    row["somatic"] = _flag(fields.get("somatic"))
    row["material"] = _flag(fields.get("material"))
    row["casting_time"] = clean_field(fields.get("castingtime", "")) or None
    row["casting_time_init"] = parse_casting_time_init(row["casting_time"])
    row["reversible"] = 1 if re.search(r"reversible", name, re.IGNORECASE) else _flag(
        fields.get("reversible"))

    raw_source = clean_field(fields.get("source", ""))
    row["source"] = f"{raw_source} ({_SOURCE_TAG})" if raw_source else _SOURCE_TAG
    page = re.search(r"\bpage\s*(\d+)", raw_source, re.IGNORECASE)
    row["page"] = int(page.group(1)) if page else None

    body = clean_markup(wikitext[end:] if end else wikitext)
    row["description"] = strip_footer(body) or None
    row["name"] = name
    return row


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def ensure_schema(conn) -> bool:
    """Create the spells table when absent. True when it was created."""
    if table_exists(conn, "spells"):
        return False
    conn.executescript(_SCHEMA)
    conn.commit()
    return True


def existing_keys(conn) -> set[tuple[str, str]]:
    return {(r[0].strip().lower(), (r[1] or "").lower())
            for r in conn.execute("SELECT name, caster FROM spells")}


def next_id(conn) -> int:
    cur = conn.execute("SELECT MAX(id) FROM spells").fetchone()[0]
    return max((cur or 0) + 1, _ID_BASE)


_COLUMNS = (
    "id", "name", "level", "school", "caster", "verbal", "somatic", "material",
    "materials", "range", "aoe", "casting_time", "casting_time_init",
    "duration", "save", "damage", "description", "source", "page", "reversible",
)


def insert_row(conn, spell_id: int, row: dict) -> None:
    cols = ", ".join(_COLUMNS)
    qs = ", ".join("?" for _ in _COLUMNS)
    vals = [spell_id if c == "id" else row.get(c) for c in _COLUMNS]
    conn.execute(f"INSERT INTO spells ({cols}) VALUES ({qs})", vals)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--db", default=str(_DEFAULT_DB))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not args.dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.dry_run and not db_path.exists():
        conn = sqlite3.connect(":memory:")   # a dry run writes nothing to disk
    else:
        conn = sqlite3.connect(str(db_path))

    try:
        if ensure_schema(conn) and not args.dry_run:
            print(f"Initialised spells schema in {db_path}.")
        have = existing_keys(conn)
        nid = next_id(conn)
        print(f"DB: {db_path} — {len(have)} spells recorded; new ids from {nid}.")

        # Union of the spell categories, titles deduped, each remembering which
        # category listed it (used as a caster hint when the infobox is vague).
        hints: dict[str, str] = {}
        for category in CATEGORIES:
            print(f"Listing Category:{category} members…")
            members = iter_category_members(category)
            if args.limit:
                members = members[:args.limit]
            for t in members:
                hints.setdefault(t, category)
            print(f"  {len(members)} pages.")
        titles = list(hints)
        print(f"{len(titles)} unique spell pages to examine.")

        wikitexts: dict[str, str] = {}
        for i in range(0, len(titles), 40):
            wikitexts.update(fetch_wikitext_batch(titles[i:i + 40]))
            print(f"  fetched {min(i + 40, len(titles))}/{len(titles)} pages…",
                  end="\r", flush=True)
        print()

        inserted = skipped_existing = skipped_nodata = 0
        seen: set[tuple[str, str]] = set()
        for title in titles:
            wt = wikitexts.get(title)
            row = build_row(title, wt, hints.get(title, "")) if wt else None
            if row is None:
                skipped_nodata += 1
                continue
            key = (row["name"].strip().lower(), row["caster"])
            if key in have or key in seen:
                skipped_existing += 1
                continue
            seen.add(key)
            if args.dry_run:
                inserted += 1
                if args.verbose:
                    print(f"  + would add {row['name']} [{row['caster']} "
                          f"L{row['level']}/{row['school']}]")
                continue
            insert_row(conn, nid, row)
            nid += 1
            inserted += 1
            if args.verbose:
                print(f"  + added {row['name']} [{row['caster']} "
                      f"L{row['level']}/{row['school']}]")

        if not args.dry_run:
            conn.commit()

        verb = "would insert" if args.dry_run else "inserted"
        print(f"\nDone. {verb} {inserted} spells; skipped {skipped_existing} "
              f"already-recorded, {skipped_nodata} redirects/no-data.")
        if args.dry_run:
            print("(dry run — nothing written)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
