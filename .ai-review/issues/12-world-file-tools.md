## Summary
`create_area` and `create_location` silently overwrite an existing file, which loses everything `update_location` accumulated if the DM re-"creates" a place. None of the file-backed character and location tools validate `slug` or `area`, so a value like `../../campaign` reads or writes outside the campaign's `characters/` or `locations/` directory. These arguments come from an LLM that reads untrusted module text and player input.

## Context
Characters and locations are markdown files under `campaigns/<slug>/<data_dir>/characters/` and `.../locations/[<area>/]`. AGENTS.md tells the DM to "always create a location file before first describing a place". In long campaigns the DM often does not remember that one already exists.

Relevant code (pinned to 2965eb7):
- [`tools/world.py#L588-L612`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/world.py#L588-L612): `create_area`, an unconditional `area_file.write_text(...)`.
- [`tools/world.py#L615-L644`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/world.py#L615-L644): `create_location`, an unconditional `loc_file.write_text(...)`.
- [`tools/world.py#L647-L663`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/world.py#L647-L663): `update_location`. [`#L666-L680`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/world.py#L666-L680): `get_location`.
- [`tools/world.py#L494-L552`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/tools/world.py#L494-L552): `get_character` / `update_character`, using `cfg["_data_dir"] / "characters" / f"{slug}.md"` with no validation.
- Compare the dashboard, which validates slugs with `^[a-z0-9][a-z0-9\-_]*$` ([`dashboard.py#L2702-L2704`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/dashboard.py#L2702-L2704)).

## Evidence
Reading the code: `create_location("inn", "The Inn", "x")` called twice replaces the first file's content with `# The Inn\n\nx`. `update_character("../../../README", "...")` writes `<repo>/README.md`, because `Path / "characters" / "../../../README.md"` is not normalised or checked.

## Proposed design
1. Add `_safe_name(value: str, field: str) -> str` in `tools/world.py`. It raises `ValueError(f"invalid {field} {value!r}")` when the value is empty, contains `/`, `\\` or NUL, equals `.` or `..`, or starts with `.`. It accepts existing styles (lowercase, digits, `-`, `_`, and uppercase for legacy files). After joining, assert that `path.resolve()` is inside the expected base directory (`base.resolve() in path.resolve().parents`).
2. Use it for every `slug` and `area` argument in `create_area`, `create_location`, `update_location`, `get_location`, `list_locations(area)`, `get_character`, `update_character` and `set_character_location`. Each tool catches `ValueError` and returns `{"error": ...}`.
3. `create_area` and `create_location` gain `overwrite: bool = False`. If the target exists and `overwrite` is false, return `{"error": "location '<slug>' already exists — use update_location to change it, or pass overwrite=True", "existing_path": ...}` and write nothing. `create_area` still `mkdir`s the subdirectory idempotently.

Alternative: a strict lowercase regex like the dashboard's. Rejected, because existing campaigns may have files with other characters, and a strict regex would make them unreachable.

## Implementation steps
1. Add `_safe_name` and `_inside(base, path)` to `tools/world.py`.
2. Apply them to the eight tools listed.
3. Add the `overwrite` parameter to `create_area` and `create_location`, and update their docstrings.
4. In AGENTS.md's Locations section, add one sentence: "`create_location` refuses to overwrite; use `update_location` for an existing place."

## Acceptance criteria
- [ ] Calling `create_location("inn", ...)` twice: the second call returns an `error`, and the file keeps the first content.
- [ ] `create_location("inn", ..., overwrite=True)` replaces it.
- [ ] `update_character("../x", "c")`, `get_location("a/b")` and `create_area("..", ...)` each return an `error` and touch no file.
- [ ] An existing slug like `Old_Mill` still works with `get_location`.
- [ ] `python3 -m unittest discover -s tests` passes.

## Test plan
`tests/test_world_tools.py` with `TempCampaign` (#1), registering `world`. Cases: the overwrite guard (area and location), `overwrite=True`, traversal attempts on each tool, backslash, a leading dot, an empty slug, legacy mixed-case slugs.

## Out of scope
- `introduce_npc` and `quick_npc` generate their own slugs. Leave them.
- Do not change file formats or the event types logged.

## Risks and gotchas
- `cfg["_data_dir"]` may differ from `cfg["_dir"]` (the `data_dir` key). Validate against the directory actually used.
- Some prompts in AGENTS.md may say "create" where an update is intended. The new error message tells the DM what to do instead.

## Dependencies
Blocked by: #1

## Metadata
Type: bug | Severity: medium | Confidence: verified | Effort: M | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10
