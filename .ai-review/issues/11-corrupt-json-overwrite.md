## Summary
Several JSON stores treat a parse failure as "empty" and then write that empty value back on the next mutation, silently wiping the file. For `secrets.json`, the DM-only memory, one bad byte (a hand edit, a merge conflict, a truncated copy) means the next `dm_note` permanently deletes every recorded secret.

## Context
`_campaign.atomic_write_text` protects files against torn writes. The load-then-save paths below still discard content they could not parse.

Relevant code (pinned to 2965eb7):
- [`tools/secrets.py#L22-L29`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/secrets.py#L22-L29): `_load` returns `[]` on `JSONDecodeError`. `dm_note` ([`#L42-L72`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/secrets.py#L42-L72)) and `dm_secret_update` then `_save` the result.
- [`tools/images.py#L43-L52`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/images.py#L43-L52): `_update_index` resets the records to `[]` on decode error, then appends one entry and writes.
- [`_campaign.py#L244-L280`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/_campaign.py#L244-L280): `load_character_meta` returns `{}` on error, and `set_character_meta` writes the merged result.

## Evidence
```python
p = cfg["_dir"] / "secrets.json"
p.write_text('[{"id": 1, "text": "the innkeeper serves the cult"}')   # missing ]
dm_note("second secret")
json.loads(p.read_text())   # -> [{"id": 1, "text": "second secret", ...}]  original secret gone
```
(Follows directly from `_load` → `[]` → `next_id = 1` → `_save(recs)`.)

## Proposed design
Add one helper to `_campaign.py`:
```python
class CorruptStoreError(RuntimeError):
    pass

def load_json_store(path: Path, default):
    """Return parsed JSON, `default` if the file is absent.
    On a parse error: copy the file to <name>.corrupt-<YYYYmmddTHHMMSS>
    and raise CorruptStoreError naming both paths."""
```
Use it on the **write** paths: `secrets._load` when called from `dm_note` and `dm_secret_update`, `images._update_index`, and `_campaign.set_character_meta`. The tool then returns `{"error": "<path> is not valid JSON; a copy was saved to <backup>. Fix or remove it, then retry."}` instead of writing. Read-only paths (`dm_secrets`, `load_character_meta` for the dashboard facets) keep the current forgiving behaviour, so the dashboard still renders.

Alternative: auto-repair or merge. There is no safe generic repair. Refusing to write and keeping a backup is the conservative choice.

## Implementation steps
1. Add `CorruptStoreError` and `load_json_store` to `_campaign.py`.
2. In `tools/secrets.py`, add `_load_for_write(cfg)` that calls `load_json_store(_secrets_path(cfg), [])`. Use it in `dm_note` and `dm_secret_update`, catching `CorruptStoreError` and returning `{"error": str(e)}`.
3. In `tools/images.py` `_update_index`, use `load_json_store(index_file, [])` and let the error propagate to the image tool, which returns it as an error dict. Check the callers of `_update_index` and wrap them.
4. In `_campaign.set_character_meta`, use `load_json_store(character_meta_path(cfg), {})`. Callers (`world.set_character_location`, `introduce_npc`) catch the error and return `{"error": ...}`.

## Acceptance criteria
- [ ] With a truncated `secrets.json`, `dm_note("x")` returns an `error`, the file is byte-identical afterwards, and a `secrets.json.corrupt-*` copy exists.
- [ ] With a valid or missing `secrets.json`, `dm_note` behaves exactly as before.
- [ ] Same two checks for `images/index.json` via `_update_index` and for `_character_meta.json` via `set_character_meta`.
- [ ] `dm_secrets()` on a corrupt file still returns `{"count": 0, "secrets": []}` (read path unchanged).
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_json_stores.py` with `TempCampaign` (#1): the cases above, plus `load_json_store` unit tests (absent file, valid file, invalid file creating a backup, a backup name collision within the same second getting a numeric suffix).

## Out of scope
- `state.json`, `campaign.json` and `events.jsonl` already raise or skip bad lines. Leave them alone.
- No schema validation.

## Risks and gotchas
- The dashboard imports `_campaign`. Do not make `load_character_meta` raise, because the `/characters` page would 500.
- Backups must go in the same directory as the original, so campaign export picks them up.

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: medium | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
