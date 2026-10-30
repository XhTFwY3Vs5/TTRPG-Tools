#!/usr/bin/env python3
"""Import creatures from the AD&D 2e Fandom wiki into global/monsters.db.

Scans https://adnd2e.fandom.com/wiki/Category:Creatures, parses each page's
``{{Creature}}`` infobox + description, and inserts any creature NOT already in
the monsters table (dedup by case-insensitive name). For each newly imported
creature it also downloads the wiki infobox image (if present) into
``global/monsters/<slug>.<ext>`` — the filename convention the dashboard's
``/monsters/portraits/<filename>`` route already discovers.

Upstream ships no builder for monsters.db, so this module also owns the schema:
when the DB (or its table) is missing it creates them before importing. Scraped
rows get ids from a dedicated high range (>= _ID_BASE) and a source footer in
the description, so they're easy to spot or re-import.

Usage:
    python3 tools/import_wiki_monsters.py [--dry-run] [--limit N]
        [--no-images] [--db PATH] [--img-dir PATH] [--verbose]
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
_DEFAULT_DB = _REPO_ROOT / "global" / "monsters.db"
_DEFAULT_IMG_DIR = _REPO_ROOT / "global" / "monsters"
_USER_AGENT = "ttrpg2-monster-importer/1.0 (campaign toolkit; local use)"
_SOURCE_TAG = "adnd2e.fandom.com"
_ID_BASE = 900_000
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# {{Creature}} infobox field -> monsters.db column.
_FIELD_MAP = {
    "frequency": "frequency",
    "numberappearing": "no_appearing",
    "armorclass": "armor_class",
    "movement": "move",
    "hitdice": "hit_dice",
    "thac0": "thac0",
    "treasure": "treasure_type",
    "noofattacks": "no_of_attacks",
    "damageattack": "damage_attack",
    "specialattack": "special_attacks",
    "specialdefenses": "special_defenses",
    "magicalresistance": "magic_resistance",
    "intelligence": "intelligence",
    "alignment": "alignment",
    "size": "size",
    "moral": "morale",
    "xp": "xp_value",
    "terrain": "climate_terrain",
}
# DB columns the wiki infobox doesn't provide.
_NULL_COLUMNS = ("pct_in_lair", "psionic_ability", "attack_defense_modes")
_ALL_COLUMNS = (
    "id", "name", "frequency", "no_appearing", "armor_class", "move",
    "hit_dice", "thac0", "pct_in_lair", "treasure_type", "no_of_attacks",
    "damage_attack", "special_attacks", "special_defenses", "magic_resistance",
    "intelligence", "alignment", "size", "psionic_ability",
    "attack_defense_modes", "description", "climate_terrain", "morale",
    "xp_value", "categories", "source",
)


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


def iter_category_members(category: str, sleep: float = 0.2):
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
            yield m["title"]
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        time.sleep(sleep)


def fetch_wikitext_batch(titles: list[str], sleep: float = 0.2) -> dict[str, str]:
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


def fetch_image_urls(filenames: list[str], sleep: float = 0.2) -> dict[str, str]:
    """Resolve {wiki image filename -> direct download URL} via imageinfo."""
    out: dict[str, str] = {}
    uniq = sorted({f for f in filenames if f})
    for i in range(0, len(uniq), 40):
        chunk = uniq[i:i + 40]
        data = _api_get({
            "action": "query", "prop": "imageinfo", "iiprop": "url",
            "titles": "|".join(f"File:{f}" for f in chunk),
        })
        query = data.get("query", {})
        norm = {n["to"]: n["from"] for n in query.get("normalized", [])}
        for page in query.get("pages", {}).values():
            title = page.get("title", "")           # e.g. "File:Aboleth 2e.png"
            ii = page.get("imageinfo")
            if not ii:
                continue
            orig = norm.get(title, title)
            fname = orig.split(":", 1)[1] if ":" in orig else orig
            out[fname] = ii[0]["url"]
        time.sleep(sleep)
    return out


def download_image(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if not data:
            return False
        dest.write_bytes(data)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Wikitext parsing (shared style with import_wiki_magic_items.py)
# --------------------------------------------------------------------------- #
def _split_top_level_pipes(s: str) -> list[str]:
    parts, dsq, dbr, buf, i = [], 0, 0, [], 0
    while i < len(s):
        two = s[i:i + 2]
        if two == "[[":
            dsq += 1; buf.append(two); i += 2; continue
        if two == "]]":
            dsq = max(0, dsq - 1); buf.append(two); i += 2; continue
        if two == "{{":
            dbr += 1; buf.append(two); i += 2; continue
        if two == "}}":
            dbr = max(0, dbr - 1); buf.append(two); i += 2; continue
        c = s[i]
        if c == "|" and dsq == 0 and dbr == 0:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def _find_template(wikitext: str, name: str) -> tuple[str | None, int]:
    m = re.search(r"\{\{\s*" + name + r"\b", wikitext, re.IGNORECASE)
    if not m:
        return None, 0
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
    t = re.sub(r"\{\{\s*br\s*\}\}", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"\[\[(?:[^\[\]|]*\|)?([^\[\]|]+)\]\]", r"\1", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"'''([^']+)'''", r"\1", t)
    t = re.sub(r"''([^']+)''", r"\1", t)
    t = re.sub(r"^=+\s*(.*?)\s*=+\s*$", r"\1", t, flags=re.MULTILINE)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def monster_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def split_metadata(description: str) -> tuple[str, list[str], str]:
    """Split a wiki-imported description into (body, categories, source).

    Imported descriptions carry a trailing metadata block: zero or more
    ``Category:<name>`` lines followed by a (possibly multi-line) ``Source: ...``
    footer ending in the wiki domain tag. This peels that block off the end:

    - ``source``     — text after ``Source:`` with newlines collapsed to spaces
                       ('' if no footer).
    - ``categories`` — de-duplicated category names with the ``Category:`` prefix
                       stripped, in document order ([] if none).
    - ``body``       — the description with the metadata block removed.

    Only the *trailing* contiguous run of category lines is treated as metadata,
    so stray ``Category:`` lines embedded mid-body are left untouched.
    """
    if not description:
        return (description or "", [], "")
    lines = description.split("\n")

    # 1. Pull off the Source: footer — the last 'Source:' line to end-of-text
    #    (the source string may wrap across several following lines).
    source = ""
    src_idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("Source:"):
            src_idx = i
    if src_idx is not None:
        parts = [lines[src_idx][len("Source:"):].strip()]
        parts += [l.strip() for l in lines[src_idx + 1:]]
        source = " ".join(p for p in parts if p).strip()
        lines = lines[:src_idx]

    # 2. Strip the trailing contiguous run of Category:/blank lines.
    cats_rev: list[str] = []
    while lines:
        s = lines[-1].strip()
        if not s:
            lines.pop()
            continue
        if s.startswith("Category:"):
            cats_rev.append(s[len("Category:"):].strip())
            lines.pop()
            continue
        break

    seen: set[str] = set()
    categories: list[str] = []
    for c in reversed(cats_rev):
        if c and c not in seen:
            seen.add(c)
            categories.append(c)

    body = "\n".join(lines).rstrip()
    return body, categories, source


def _parse_thac0(v: str):
    m = re.search(r"\d+", v or "")
    return int(m.group(0)) if m else None


def _variant_suffixes(fields: dict[str, str]) -> list[str]:
    """Field suffixes for one {{Creature}} block.

    Pages describing a single creature use bare keys ('armorclass'), but a page
    covering several variants numbers them — 'armorclass1'..'armorclass8', each
    with its own 'nameN'. Reading only bare keys is why the Monstrous Manual's
    multi-entry pages (orc, troll, kobold, ...) used to import with no stats.
    Returns [''] for a single-entry page so callers need no special case.
    """
    numbered: set[int] = set()
    has_bare = False
    for key in fields:
        m = re.fullmatch(r"(.+?)(\d+)", key)
        if m and m.group(1) in _FIELD_MAP:
            numbered.add(int(m.group(2)))
        elif key in _FIELD_MAP:
            has_bare = True
    suffixes = [str(n) for n in sorted(numbered)]
    if has_bare:
        suffixes.insert(0, "")
    return suffixes or [""]


def _row_from(fields: dict[str, str], suffix: str, title: str, wikitext: str,
              end: int) -> dict | None:
    """One monsters-row for a single variant (suffix '') or numbered variant."""

    def field(key: str) -> str:
        """Variant value, falling back to the unnumbered key."""
        if suffix:
            val = fields.get(f"{key}{suffix}")
            if val is not None:
                return val
        return fields.get(key, "")

    name = (clean_markup(field("name")) or title).strip().lower()
    if not name:
        return None
    # Skip overview/index pages ("X, general information") — not real stat blocks.
    if ("general information" in name or name.endswith(", general")
            or name.endswith(" (general)") or "overview" in name):
        return None

    row = {c: None for c in _ALL_COLUMNS}
    for wiki_key, col in _FIELD_MAP.items():
        val = clean_markup(field(wiki_key))
        if val:
            row[col] = _parse_thac0(val) if col == "thac0" else val

    raw_body = clean_markup(wikitext[end:] if end else wikitext)
    body, categories, _ = split_metadata(raw_body)
    src = clean_markup(field("source"))
    row["description"] = body or None
    row["categories"] = json.dumps(categories) if categories else None
    row["source"] = f"{src} ({_SOURCE_TAG})" if src else _SOURCE_TAG
    row["name"] = name

    img = field("image").strip()
    img = re.sub(r"\[\[(?:File|Image):", "", img, flags=re.IGNORECASE).strip("[]| ")
    img = img.split("|")[0].strip() if img else ""
    row["_image"] = img or None
    return row


# Stat fields as they appear in the transposed body tables ("Armor Class:" /
# "Hit Dice:" as rows, variants as columns). That is the wiki's older layout,
# used on pages whose {{Creature}} infobox is a bare stub — Zombie and
# Skeleton, for instance, carry every stat only in a table like this.
_BODY_STAT_FIELDS = {
    "armor class": "armor_class",
    "hit dice": "hit_dice",
    "thac0": "thac0",
    "no. of attacks": "no_of_attacks",
    "damage/attack": "damage_attack",
    "special attacks": "special_attacks",
    "special defenses": "special_defenses",
    "magic resistance": "magic_resistance",
    "intelligence": "intelligence",
    "alignment": "alignment",
    "size": "size",
    "morale": "morale",
    "xp value": "xp_value",
    "frequency": "frequency",
    "no. appearing": "no_appearing",
    "climate/terrain": "climate_terrain",
    "movement": "move",
    "treasure": "treasure_type",
    "pct. in lair": "pct_in_lair",
    "% in lair": "pct_in_lair",
    "psionic ability": "psionic_ability",
    "attack/defense modes": "attack_defense_modes",
}

# Column names that mean "the creature this page is about", rather than a
# named variant of it.
_DEFAULT_VARIANT_NAMES = ("", "common", "standard", "normal", "typical")


def _split_wiki_cells(line: str) -> list[str]:
    """Split a table row's cell text on || / !! outside links and templates."""
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


def _strip_cell_attributes(cell: str) -> str:
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


def parse_tables(wikitext: str) -> list[list[list[str]]]:
    """Every wikitable as rows of cleaned cells. Row breaks are '|-' only."""
    tables: list[list[list[str]]] = []
    rows: list[list[str]] | None = None
    cur: list[str] = []

    def flush():
        if any(c for c in cur):
            rows.append(list(cur))
        cur.clear()

    for raw in wikitext.splitlines():
        line = raw.strip()
        if line.startswith("{|"):
            rows, cur = [], []
        elif rows is None:
            continue
        elif line.startswith("|}"):
            flush()
            tables.append(rows)
            rows, cur = None, []
        elif line.startswith("|-"):
            flush()
        elif line.startswith("|+"):
            continue
        elif line.startswith("!") or line.startswith("|"):
            for cell in _split_wiki_cells(line[1:]):
                cur.append(clean_markup(_strip_cell_attributes(cell)))
    return tables


def _body_stat_rows(wikitext: str, end: int, title: str,
                    source: str) -> list[dict]:
    """Rows from a transposed stat table in the page body (see above)."""
    body = wikitext[end:] if end else wikitext
    for rows in parse_tables(body):
        labelled = []
        for row in rows:
            if row:
                label = row[0].strip().rstrip(":").strip().lower()
                if label in _BODY_STAT_FIELDS:
                    labelled.append((_BODY_STAT_FIELDS[label], row))
        if not labelled:
            continue
        n_cols = max(len(r) - 1 for _, r in labelled)
        if n_cols < 1:
            continue

        names = [""] * n_cols
        if rows and rows[0] and not rows[0][0].strip():
            for i, head in enumerate(rows[0][1:n_cols + 1]):
                names[i] = head

        out = []
        for i in range(n_cols):
            row = {c: None for c in _ALL_COLUMNS}
            for col, cells in labelled:
                if i + 1 < len(cells):
                    val = cells[i + 1].strip()
                    if val and val not in ("—", "–"):
                        row[col] = _parse_thac0(val) if col == "thac0" else val
            if not (row["armor_class"] or row["hit_dice"]):
                continue
            variant = clean_markup(names[i]).strip()
            name = title.lower() if variant.strip().lower() in _DEFAULT_VARIANT_NAMES \
                else f"{title.lower()}, {variant.lower()}"
            row["name"] = name
            row["source"] = f"{source} ({_SOURCE_TAG})" if source else _SOURCE_TAG
            out.append(row)
        if out:
            return out
    return []


def _source_rank(source: str) -> int:
    """Lower is better. Two pages can describe a creature under the same name —
    the core bestiary should win that race, not whichever page was fetched
    first (a Ravenloft 'Zombie' or a Mystara 'Troll' otherwise shadows the
    Monstrous Manual entry)."""
    s = (source or "").lower()
    if "monstrous manual" in s:
        return 0
    if "monstrous compendium" in s:
        return 2 if "annual" in s else 1
    return 3


def build_rows(title: str, wikitext: str) -> list[dict]:
    """Map a wiki page to monsters-row dicts (+ '_image' filenames).

    One row per creature the page describes. Empty when the page is a redirect
    or carries no {{Creature}} infobox.
    """
    if re.match(r"\s*#redirect", wikitext, re.IGNORECASE):
        return []
    inner, end = _find_template(wikitext, "Creature")
    if inner is None:
        return []
    fields = parse_infobox(inner)
    rows = []
    for suffix in _variant_suffixes(fields):
        row = _row_from(fields, suffix, title, wikitext, end)
        if row:
            rows.append(row)
    if not any(r["armor_class"] or r["hit_dice"] for r in rows):
        body_rows = _body_stat_rows(wikitext, end, title,
                                    clean_markup(fields.get("source", "")))
        if body_rows:
            return body_rows
    return rows


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
# The canonical monsters schema. Column names are the contract: lookup.py,
# combat.py, build_encounter_tables.py and dashboard_monsters.py all read these
# names, and _ALL_COLUMNS above is the same list in insert order.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS monsters (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT,
    frequency             TEXT,
    no_appearing          TEXT,
    armor_class           TEXT,
    move                  TEXT,
    hit_dice              TEXT,
    thac0                 INTEGER,
    pct_in_lair           TEXT,
    treasure_type         TEXT,
    no_of_attacks         TEXT,
    damage_attack         TEXT,
    special_attacks       TEXT,
    special_defenses      TEXT,
    magic_resistance      TEXT,
    intelligence          TEXT,
    alignment             TEXT,
    size                  TEXT,
    psionic_ability       TEXT,
    attack_defense_modes  TEXT,
    description           TEXT,
    climate_terrain       TEXT,
    morale                TEXT,
    xp_value              TEXT,
    categories            TEXT,
    source                TEXT
);
CREATE INDEX IF NOT EXISTS idx_monsters_name ON monsters(name COLLATE NOCASE);
"""


def table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def ensure_schema(conn) -> bool:
    """Create the monsters table when it is absent. True when it was created."""
    if table_exists(conn, "monsters"):
        return False
    conn.executescript(_SCHEMA)
    conn.commit()
    return True


def existing_names(conn) -> set[str]:
    return {r[0].strip().lower()
            for r in conn.execute("SELECT name FROM monsters WHERE name IS NOT NULL")}


def next_id(conn) -> int:
    cur = conn.execute("SELECT MAX(id) FROM monsters").fetchone()[0]
    return max((cur or 0) + 1, _ID_BASE)


def insert_row(conn, item_id: int, row: dict) -> None:
    cols = ", ".join(_ALL_COLUMNS)
    qs = ", ".join("?" for _ in _ALL_COLUMNS)
    vals = [item_id if c == "id" else row.get(c) for c in _ALL_COLUMNS]
    conn.execute(f"INSERT INTO monsters ({cols}) VALUES ({qs})", vals)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--db", default=str(_DEFAULT_DB))
    ap.add_argument("--img-dir", default=str(_DEFAULT_IMG_DIR))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    db_path, img_dir = Path(args.db), Path(args.img_dir)
    if not args.dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.dry_run and not db_path.exists():
        conn = sqlite3.connect(":memory:")   # a dry run writes nothing to disk
    else:
        conn = sqlite3.connect(str(db_path))
    if not args.dry_run and not args.no_images:
        img_dir.mkdir(parents=True, exist_ok=True)

    try:
        if ensure_schema(conn) and not args.dry_run:
            print(f"Initialised monsters schema in {db_path} (no builder ships "
                  f"upstream; columns taken from this module and its readers).")
        have = existing_names(conn)
        nid = next_id(conn)
        print(f"DB: {db_path} — {len(have)} creatures recorded; new ids from {nid}.")

        print("Listing Category:Creatures members…")
        titles = list(iter_category_members("Creatures"))
        if args.limit:
            titles = titles[:args.limit]
        print(f"{len(titles)} member pages to examine.")

        wikitexts: dict[str, str] = {}
        for i in range(0, len(titles), 40):
            wikitexts.update(fetch_wikitext_batch(titles[i:i + 40]))
            print(f"  fetched {min(i + 40, len(titles))}/{len(titles)} pages…",
                  end="\r", flush=True)
        print()

        # Build rows for creatures not already recorded. Rows are keyed by name:
        # the same creature appears on several pages (the Monstrous Manual
        # entry, a Ravenloft variant, a Mystara variant), and the best-sourced
        # one wins rather than whichever page happened to be fetched first.
        candidates: dict[str, dict] = {}
        skipped_existing = skipped_nodata = 0
        for title in titles:
            wt = wikitexts.get(title)
            rows = build_rows(title, wt) if wt else []
            if not rows:
                skipped_nodata += 1
                continue
            for row in rows:
                key = row["name"]
                if key in have:
                    skipped_existing += 1
                    continue
                prev = candidates.get(key)
                if prev is None:
                    candidates[key] = row
                else:
                    skipped_existing += 1
                    if _source_rank(row["source"]) < _source_rank(prev["source"]):
                        candidates[key] = row
        new_rows = list(candidates.values())

        print(f"{len(new_rows)} new creatures (skipped {skipped_existing} "
              f"already-recorded/duplicate, {skipped_nodata} pages with no "
              f"stat block).")

        # Resolve image URLs in bulk.
        url_map: dict[str, str] = {}
        if not args.no_images:
            wanted = [r["_image"] for r in new_rows if r.get("_image")]
            print(f"Resolving {len(set(filter(None, wanted)))} image URLs…")
            if not args.dry_run:
                url_map = fetch_image_urls(wanted)

        inserted = images = 0
        for row in new_rows:
            if args.dry_run:
                inserted += 1
                if args.verbose:
                    print(f"  + {row['name']}  (HD {row['hit_dice']}, "
                          f"img={'yes' if row.get('_image') else 'no'})")
                continue
            insert_row(conn, nid, row)
            nid += 1
            inserted += 1
            # Download image into <slug>.<ext>.
            img = row.get("_image")
            if img and not args.no_images:
                ext = Path(img).suffix.lower()
                if ext in _IMG_EXTS and img in url_map:
                    dest = img_dir / f"{monster_slug(row['name'])}{ext}"
                    if not dest.exists() and download_image(url_map[img], dest):
                        images += 1
            if args.verbose and inserted % 50 == 0:
                print(f"  …{inserted} inserted", end="\r", flush=True)

        if not args.dry_run:
            conn.commit()

        verb = "would insert" if args.dry_run else "inserted"
        print(f"\nDone. {verb} {inserted} creatures; downloaded {images} images.")
        if args.dry_run:
            print("(dry run — nothing written)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
